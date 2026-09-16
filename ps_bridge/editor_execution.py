"""Common native-event adapter, independent of the Pipeline journal.

Lock order: session -> execution. Never acquire the run store from this module's
hot path. Frozen registrations are supplied by the already-authorized observer.
"""
from __future__ import annotations

import copy
import struct
import threading
import time
import uuid
from .errors import BridgeError, require
from .json_codec import canonical_bytes, loads
from .manager_notifications import ManagerNotifications

NATIVE_EVENTS = frozenset({"execution_start", "executing", "execution_cached", "progress",
    "progress_state", "progress_text", "execution_success", "execution_error", "execution_interrupted"})
TERMINALS = frozenset({"execution_success", "execution_error", "execution_interrupted"})
COALESCIBLE = frozenset({"progress", "progress_state", "progress_text"})
CONTROL_LIMIT = 2 * 1024 * 1024
PREVIEW_LIMIT = 8 * 1024 * 1024


class EditorExecution:
    def __init__(self, service):
        self.service = service
        self.lock = threading.RLock()
        self.runs = {}
        self.previews = {}
        self.deliver = None
        self.notifications = ManagerNotifications(self._deliver, self._recover)

    def bind_transport(self, deliver):
        self.deliver = deliver
        self.notifications.bind()

    def track(self, run):
        """Called only with a frozen run validated by Pipeline, never browser data."""
        with self.lock:
            state = self.runs.get(run["runId"])
            if state is None:
                # Bounded recovery cache; active executions are never evicted.
                if len(self.runs) >= 32:
                    old = next((key for key, value in self.runs.items() if value["terminal"]), None)
                    if old is None:
                        return False
                    self.runs.pop(old)
                    self._drop_previews(old)
                registration = {"prompt": copy.deepcopy(run["prompt"]),
                    "workflow": copy.deepcopy(run.get("extra_data", {}).get("extra_pnginfo", {}).get("workflow")),
                    "createdAt": run["createdAt"]}
                if len(canonical_bytes(registration)) > CONTROL_LIMIT - 65536:
                    return False
                state = self.runs[run["runId"]] = {"runId": run["runId"], "scope": copy.deepcopy(run["scope"]),
                    "controller": run["controllerClientId"], "definitionSha256": run["definitionSha256"],
                    "promptId": None, "registration": registration, "events": {}, "terminal": False,
                    "previewSequence": 0, "sequence": 0, "node": None, "displayNode": None, "observation": "connected"}
            if run["promptId"] and not state["promptId"]:
                state["promptId"] = run["promptId"]
                self._emit(state, "registered", state["registration"])
            if run["status"] in {"succeeded", "failed", "cancelled"} and state["promptId"] and not state["terminal"]:
                kind = {"succeeded": "execution_success", "failed": "execution_error", "cancelled": "execution_interrupted"}[run["status"]]
                self.native(run["runId"], kind, {"prompt_id": state["promptId"]})
            elif run["status"] in {"succeeded", "failed", "cancelled"} and not state["promptId"]:
                state["terminal"] = True
            return True

    def accepts(self, run_id, data):
        with self.lock:
            state = self.runs.get(run_id)
            return bool(state and not state["terminal"] and state["promptId"] and isinstance(data, dict)
                        and data.get("prompt_id") == state["promptId"])

    def native(self, run_id, kind, data):
        if kind not in NATIVE_EVENTS or not self.accepts(run_id, data):
            return False
        # Execution errors may contain arbitrary inputs, credentials and paths.
        if kind == "execution_error":
            data = {key: copy.deepcopy(data[key]) for key in ("prompt_id", "node_id", "node_type", "executed") if key in data}
            data.update(exception_type="BridgeExecutionError", exception_message="ComfyUI execution failed; inspect the server log", traceback=[])
        else:
            data = copy.deepcopy(data)
        if len(canonical_bytes(data)) > CONTROL_LIMIT - 65536:
            return False
        with self.lock:
            state = self.runs[run_id]
            if kind == "execution_start":
                state["events"].clear()
            if kind == "executing":
                state["node"] = data.get("node")
                state["displayNode"] = data.get("display_node") or data.get("node")
                for key in COALESCIBLE:
                    state["events"].pop(key, None)
            state["events"][kind] = data
            if kind in TERMINALS:
                state["terminal"] = True
                state["node"] = None
                self._drop_previews(run_id)
                state.pop("preview", None)
            self._emit(state, "native", {"type": kind, "data": data})
            return True

    def observation(self, run_id, status):
        with self.lock:
            state = self.runs.get(run_id)
            if state and not state["terminal"]:
                state["observation"] = status
                self._emit(state, "observation", {"status": status})

    def binary(self, run_id, raw, *, text_metadata=False):
        """ComfyUI big-endian type 1 / type 4, with strict identity and budgets."""
        if len(raw) < 8 or len(raw) > PREVIEW_LIMIT + 65536:
            return
        event_type, header = struct.unpack_from(">II", raw)
        with self.lock:
            state = self.runs.get(run_id)
            if not state or state["terminal"] or not state["promptId"]:
                return
            metadata = {}
            if event_type == 3:
                if len(raw) > 65536:
                    return
                try:
                    offset = 4
                    if text_metadata:
                        size = struct.unpack_from(">I", raw, offset)[0]
                        offset += 4
                        prompt_id = raw[offset:offset + size].decode("utf-8")
                        offset += size
                        if prompt_id != state["promptId"]:
                            return
                    size = struct.unpack_from(">I", raw, offset)[0]
                    offset += 4
                    if not 0 < size <= 256 or offset + size > len(raw):
                        return
                    node = raw[offset:offset + size].decode("utf-8")
                    text = raw[offset + size:].decode("utf-8")
                    if not text_metadata and node != str(state["node"]):
                        return
                    self.native(run_id, "progress_text", {"prompt_id": state["promptId"], "nodeId": node, "text": text})
                except (UnicodeError, struct.error):
                    pass
                return
            if event_type == 4:
                if header > 65536 or 8 + header >= len(raw):
                    return
                try:
                    metadata = loads(raw[8:8 + header])
                except (ValueError, BridgeError):
                    return
                if not isinstance(metadata, dict) or metadata.get("prompt_id") != state["promptId"] or not metadata.get("node_id"):
                    return
                mime, data = metadata.get("image_type"), raw[8 + header:]
                node = str(metadata["node_id"])
                display = str(metadata.get("display_node_id") or node)
            elif event_type == 1 and state["node"] is not None:
                mime, data = ("image/png" if header == 2 else "image/jpeg"), raw[8:]
                node, display = str(state["node"]), str(state["displayNode"])
            else:
                return
            if mime not in {"image/jpeg", "image/png"} or not 0 < len(data) <= PREVIEW_LIMIT:
                return
            # Two immutable frames per run, 32 MiB globally; HTTP responses own
            # their bytes after lookup so eviction never mutates an in-flight frame.
            own = [key for key, value in self.previews.items() if value["runId"] == run_id]
            for key in own[:-1]:
                self.previews.pop(key)
            while self.previews and sum(len(v["bytes"]) for v in self.previews.values()) + len(data) > 32 * 1024 * 1024:
                self.previews.pop(next(iter(self.previews)))
            state["previewSequence"] += 1
            descriptor = {"previewId": uuid.uuid4().hex, "runId": run_id, "promptId": state["promptId"],
                "nodeId": node, "displayNodeId": display, "previewSequence": state["previewSequence"],
                "mime": mime, "byteSize": len(data)}
            for source, target in (("parent_node_id", "parentNodeId"), ("real_node_id", "realNodeId")):
                if metadata.get(source) is not None:
                    descriptor[target] = str(metadata[source])
            self.previews[descriptor["previewId"]] = {"runId": run_id, "bytes": data, "mime": mime, "expires": time.monotonic() + 30}
            state["preview"] = descriptor
            self._emit(state, "preview", descriptor)

    def _drop_previews(self, run_id):
        for key in [key for key, value in self.previews.items() if value["runId"] == run_id]:
            self.previews.pop(key)

    def _event(self, state, kind, data):
        return {"version": 1, "serverEpoch": self.service.sessions.epoch, "scope": state["scope"], "runId": state["runId"],
                "promptId": state["promptId"], "definitionSha256": state["definitionSha256"],
                "liveSequence": state["sequence"], "kind": kind, "data": copy.deepcopy(data), "receivedAt": time.time() * 1000}

    def snapshot(self, run_id):
        with self.lock:
            state = self.runs.get(run_id)
            if not state or not state["promptId"]:
                return None
            return self._event(state, "snapshot", {"registration": state["registration"], "events": state["events"],
                "observation": state["observation"], "preview": state.get("preview")})

    def _emit(self, state, kind, data):
        if state["promptId"]:
            state["sequence"] += 1
            self.notifications.post("event", self._event(state, kind, data))

    async def _deliver(self, _kind, event):
        if self.deliver:
            await self.deliver(event)

    async def _recover(self):
        # Reuse the same bounded handoff as the existing manager. Overflow is an
        # actual sequence gap; an authoritative snapshot explicitly closes it.
        with self.lock:
            snapshots = [self.snapshot(run_id) for run_id in self.runs]
        for event in snapshots:
            if event:
                await self._deliver("event", event)

    def preview(self, run_id, preview_id):
        with self.lock:
            preview = self.previews.get(preview_id)
            require(preview and preview["runId"] == run_id and preview["expires"] > time.monotonic(),
                    "RESULT_NOT_FOUND", "Sampling preview expired", status=404)
            return preview["bytes"], preview["mime"]

    async def flush(self):
        await self.notifications.flush()

    async def close(self):
        await self.notifications.close()
        with self.lock:
            self.previews.clear()
