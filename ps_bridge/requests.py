"""Persistent run snapshots, submit claims and result/receipt journals."""
from __future__ import annotations
import copy
import threading
import time
import uuid
from pathlib import Path
from .errors import require
from .json_codec import digest
from .parameters import identifier
from .storage import atomic_json, read_json
from .schemas import validate_shape

TERMINAL = {"succeeded", "failed", "cancelled"}
TRANSITIONS = {
    "prepared": {"submitting", "cancelled"},
    "submitting": {"queued", "running", "submission_unknown", "failed", "cancel_requested"},
    "submission_unknown": {"queued", "running", "failed", "cancel_requested"},
    "queued": {"running", "succeeded", "failed", "cancel_requested", "cancelled"},
    "running": {"succeeded", "failed", "cancel_requested", "cancelled"},
    "cancel_requested": {"cancelled", "succeeded", "failed", "submission_unknown", "queued", "running"},
}

class RunStore:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.RLock()
        self.on_change = lambda run, previous: None
        self.active_ids = set()
        # An interrupted submission is never treated as permission to enqueue again.
        for path in self.root.glob("*.json"):
            run = read_json(path)
            if run["status"] == "submitting":
                run["status"] = "submission_unknown"
                atomic_json(path, run)
            if run["status"] not in TERMINAL:
                self.active_ids.add(run["runId"])

    def _path(self, run_id):
        identifier(run_id, "runId")
        return self.root / f"{digest(run_id)}.json"

    def _read(self, run_id):
        run = read_json(self._path(run_id))
        require(run is not None, "RUN_NOT_FOUND", "Unknown run", status=404)
        require(run["runId"] == run_id, "ID_REUSE", "Run ID digest collision")
        return run

    def get(self, run_id, owner=None):
        with self.lock:
            run = self._read(run_id)
            if owner is not None:
                require(run["controllerClientId"] == owner, "FORBIDDEN", "Run belongs to another client", status=403)
            return copy.deepcopy(run)

    def public(self, run):
        return {k: copy.deepcopy(v) for k, v in run.items() if k not in {"claimToken", "requestDigest", "cancelMessages"}}

    def existing(self, run_id, owner, request_digest):
        with self.lock:
            if not self._path(run_id).exists():
                return None
            run = self.get(run_id, owner)
            require(run["requestDigest"] == request_digest, "ID_REUSE", "runId was used with another prepare request")
            return self.public(run)

    def create(self, snapshot, request_digest):
        with self.lock:
            path = self._path(snapshot["runId"])
            require(not path.exists(), "ID_REUSE", "runId already exists")
            run = {**copy.deepcopy(snapshot), "requestDigest": request_digest, "status": "prepared", "promptId": None,
                   "results": [], "receipts": {}, "createdAt": time.time(), "updatedAt": time.time()}
            atomic_json(path, run)
            self.active_ids.add(run["runId"])
            self.on_change(self.public(run), None)
            return self.public(run)

    def update(self, run_id, *, status=None, **changes):
        with self.lock:
            run = self._read(run_id)
            previous = self.public(run)
            original = copy.deepcopy(run)
            if status is not None and status != run["status"]:
                require(status in TRANSITIONS.get(run["status"], set()), "RUN_STATE_INVALID", "Run state cannot move backwards")
                run["status"] = status
            require(set(changes) <= {"promptId", "results", "receipts", "actualCount", "error", "resultError", "claimToken", "cancelMessages"},
                    "RUN_STATE_INVALID", "Frozen fields cannot change")
            for key in ("claimToken", "promptId"):
                require(key not in changes or run.get(key) is None or run[key] == changes[key],
                        "ID_REUSE", "An established submission identity cannot change")
            if "results" in changes:
                require(all(item in changes["results"] for item in run["results"]),
                        "ID_REUSE", "Recorded results cannot be removed or rebound")
            run.update(copy.deepcopy(changes))
            if run == original:
                return previous
            run["updatedAt"] = time.time()
            atomic_json(self._path(run_id), run)
            if run["status"] in TERMINAL:
                self.active_ids.discard(run_id)
            self.on_change(self.public(run), previous)
            return self.public(run)

    def claim(self, run_id, owner, submit_owner):
        with self.lock:
            run = self.get(run_id, owner)
            require(run["submitOwner"] == submit_owner, "FORBIDDEN", "Wrong submit owner", status=403)
            if "claimToken" not in run:
                require(run["status"] == "prepared", "RUN_STATE_INVALID", "Run cannot be claimed")
                token = uuid.uuid4().hex
                self.update(run_id, status="submitting", claimToken=token)
                run = self._read(run_id)
                first = True
            else:
                first = False
            return {"claimToken": run["claimToken"], "status": run["status"], "prompt": run["prompt"],
                    "extra_data": run["extra_data"], "preparedPromptSha256": run["preparedPromptSha256"], "firstClaim": first}

    def submitted(self, run_id, owner, token, prompt_id=None):
        with self.lock:
            run = self.get(run_id, owner)
            require(run.get("claimToken") == token and token, "FORBIDDEN", "Invalid submit claim", status=403)
            if prompt_id is not None:
                identifier(prompt_id, "promptId")
                require(run["promptId"] in (None, prompt_id), "ID_REUSE", "Run is already associated with another prompt")
                if run["promptId"] == prompt_id:
                    return self.public(run)
                return self.update(run_id, status="queued" if run["status"] in {"submitting", "submission_unknown"} else None, promptId=prompt_id)
            if run["status"] in {"submitting", "submission_unknown"}:
                return self.update(run_id, status="submission_unknown")
            return self.public(run)

    def associate_execution(self, run_id, prompt_id):
        with self.lock:
            run = self._read(run_id)
            require(run.get("claimToken") and run["status"] not in TERMINAL,
                    "RUN_STATE_INVALID", "Run has no active submission claim")
            require(run["promptId"] in (None, prompt_id), "ID_REUSE", "Execution belongs to another prompt")
            return self.update(run_id, promptId=prompt_id, status="running" if run["status"] != "cancel_requested" else None)

    def record_results(self, run_id, prompt_id, node_id, result_id, items):
        with self.lock:
            run = self._read(run_id)
            require(run["promptId"] == prompt_id and run["status"] not in TERMINAL, "BINDING_INVALID", "Result does not belong to the active prompt")
            binding = next((r for r in run["manifest"]["results"] if r["nodeId"] == node_id and r["resultId"] == result_id), None)
            require(binding is not None, "BINDING_INVALID", "Result node is not declared")
            by_id = {(r["resultId"], r["batchIndex"]): r for r in run["results"]}
            for item in items:
                validate_shape("result", item, code="BINDING_INVALID")
                require((item["runId"], item["promptId"], item["nodeId"], item["resultId"])
                        == (run_id, prompt_id, node_id, result_id), "BINDING_INVALID", "Result identity differs from its journal target")
                key = (result_id, item["batchIndex"])
                require(key not in by_id or by_id[key] == item, "ID_REUSE", "Result identity was reused")
                by_id[key] = copy.deepcopy(item)
            require(len(by_id) <= run["manifest"]["limits"]["maxResults"], "MEDIA_OVERFLOW", "Result count exceeds the function limit", status=413)
            order = {r["resultId"]: r["order"] for r in run["manifest"]["results"]}
            results = sorted(by_id.values(), key=lambda r: (order[r["resultId"]], r["batchIndex"]))
            return self.update(run_id, results=results, actualCount=len(results))

    def finish(self, run_id, *, success, interrupted=False, execution_error=None):
        with self.lock:
            run = self._read(run_id)
            if run["status"] in TERMINAL:
                return self.public(run)
            code = None
            if not success:
                code = "EXECUTION_INTERRUPTED" if interrupted else "EXECUTION_FAILED"
            elif not run["results"]:
                code = "NO_RESULTS"
            elif any(r["required"] and not any(i["resultId"] == r["resultId"] for i in run["results"])
                     for r in run["manifest"]["results"]):
                code = "MISSING_REQUIRED_RESULT"
            status = "cancelled" if interrupted else "failed" if code else "succeeded"
            error = {"code": code, "message": code, "retryable": False} if code else None
            if code == "EXECUTION_FAILED":
                error = execution_error or {"code": code, "message": "ComfyUI execution failed without a node diagnostic", "retryable": False}
                validate_shape("error", error, code="BINDING_INVALID")
            return self.update(run_id, status=status, error=error,
                               actualCount=len(run["results"]))

    def receipt(self, run_id, owner, kind, payload):
        require(kind in {"received", "placed"}, "BINDING_INVALID", "Unknown receipt kind")
        validate_shape(kind, payload, code="BINDING_INVALID")
        require(payload["runId"] == run_id, "BINDING_INVALID", "Receipt run identity differs")
        with self.lock:
            run = self.get(run_id, owner)
            key = (payload.get("resultId"), payload.get("batchIndex"))
            require(any((r["resultId"], r["batchIndex"]) == key for r in run["results"]), "BINDING_INVALID", "Unknown result receipt")
            if kind == "placed":
                require(payload.get("targetDocumentId") == run["media"]["runtime"]["documentId"],
                        "BINDING_INVALID", "Placement receipt belongs to another document")
            receipts = run["receipts"]
            identity = digest([kind, *key])
            receipt = {k: payload[k] for k in ("resultId", "batchIndex", "targetDocumentId") if k in payload}
            require(identity not in receipts or receipts[identity] == receipt, "ID_REUSE", "Receipt identity reused")
            if identity in receipts:
                return self.public(run)
            receipts[identity] = receipt
            return self.update(run_id, receipts=receipts)

    def active(self):
        with self.lock:
            return [self._read(run_id) for run_id in tuple(self.active_ids)]
