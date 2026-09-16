"""Single submission, history reconciliation and ownership-safe cancellation."""
from __future__ import annotations
import asyncio
import logging
import time
from collections import OrderedDict
from aiohttp import ClientSession, ClientTimeout
from .errors import BridgeError, require
from .json_codec import digest, execution_prompt
from .requests import TERMINAL

def execution_failure(messages):
    """Keep the node diagnostic, without copying tensors, prompt inputs or traceback."""
    for event in reversed(messages):
        if not isinstance(event, (tuple, list)) or len(event) != 2 or event[0] != "execution_error":
            continue
        detail = event[1]
        if not isinstance(detail, dict):
            continue
        message = detail.get("exception_message")
        if not isinstance(message, str) or not message.strip():
            continue
        error = {"code": "EXECUTION_FAILED", "message": message.strip()[:4096], "retryable": False}
        node_id = detail.get("node_id")
        node_id = str(node_id) if isinstance(node_id, (str, int)) and not isinstance(node_id, bool) else ""
        if node_id and len(node_id) <= 256 and all(ord(c) >= 32 for c in node_id):
            error["nodeId"] = node_id
            node_type = detail.get("node_type")
            label = f" ({node_type[:128]})" if isinstance(node_type, str) and node_type else ""
            error["message"] = f"Node {node_id}{label}: {error['message']}"
        return error
    return None

class ComfyAdapter:
    def __init__(self, server):
        self.server = server

    def native_url(self, path):
        # Use the actual listening port and a fixed loopback host, never HTTP Host input.
        host = "[::1]" if ":" in getattr(self.server, "address", "") else "127.0.0.1"
        port = int(self.server.port)
        return f"http://{host}:{port}{path}"

    async def submit(self, prompt, extra_data, native_client_id=None):
        body = {"prompt": prompt, "extra_data": extra_data}
        if native_client_id:
            body["client_id"] = native_client_id
        async with ClientSession(timeout=ClientTimeout(total=120, connect=10)) as session:
            async with session.post(self.native_url("/prompt"), json=body) as response:
                if response.status == 400:
                    from .pipeline import rejection_diagnostic
                    from .json_codec import loads
                    data = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        if len(data) + len(chunk) > 2 * 1024 * 1024:
                            break
                        data.extend(chunk)
                    try:
                        diagnostic = rejection_diagnostic(loads(bytes(data)), prompt)
                    except (BridgeError, ValueError):
                        diagnostic = []
                    exc = BridgeError("PROMPT_REJECTED", "ComfyUI rejected the prepared prompt; inspect node validation", status=422)
                    exc.node_errors = diagnostic
                    raise exc
                if response.status >= 400:
                    raise BridgeError("SUBMISSION_UNKNOWN", "ComfyUI submission response was not conclusive")
                result = await response.json()
        require(isinstance(result, dict) and isinstance(result.get("prompt_id"), str),
                "SUBMISSION_UNKNOWN", "ComfyUI did not return a prompt ID")
        return result["prompt_id"]

    def queue(self):
        return self.server.prompt_queue.get_current_queue()

    def history(self, prompt_id=None):
        return self.server.prompt_queue.get_history(prompt_id=prompt_id) if prompt_id else self.server.prompt_queue.get_history()

    def cancel_queued(self, prompt_id):
        return self.server.prompt_queue.delete_queue_item(lambda item: str(item[1]) == prompt_id)

    def interrupt_owned(self, prompt_id):
        # Lock queue ownership while setting the interrupt flag. No unrelated prompt is interrupted.
        queue = self.server.prompt_queue
        with queue.mutex:
            running, _ = queue.get_current_queue()
            require(len(running) == 1 and str(running[0][1]) == prompt_id, "CANCEL_NOT_OWNER", "The server is not executing this run")
            from nodes import interrupt_processing
            interrupt_processing()

class BackendRunner:
    def __init__(self, runs, adapter, *, notify=None):
        self.runs, self.adapter = runs, adapter
        self.notify = notify or (lambda run: None)
        self.submits = {}
        self.monitor = None
        self.service = None
        self.unknown_history_checked = OrderedDict()

    async def blocking(self, function, *args, **kwargs):
        if self.service:
            return await self.service.work.run(function, *args, **kwargs)
        return function(*args, **kwargs)

    async def submit(self, run_id, owner):
        native_id = None
        if self.service and hasattr(self.adapter, "native_url"):
            run = await self.blocking(self.runs.get, run_id, owner)
            observer = await self.service.pipeline.observe(run_id, owner, run["scope"])
            native_id = observer["nativeClientId"]
        claim = await self.blocking(self.runs.claim, run_id, owner, "bridge")
        if not claim["firstClaim"]:
            return await self.blocking(self.reconcile, run_id)
        try:
            if native_id:
                prompt_id = await self.adapter.submit(claim["prompt"], claim["extra_data"], native_id)
            else:
                prompt_id = await self.adapter.submit(claim["prompt"], claim["extra_data"])
            await self.blocking(self.runs.submitted, run_id, owner, claim["claimToken"], prompt_id)
        except BridgeError as exc:
            if exc.code == "PROMPT_REJECTED":
                if self.service:
                    await self.blocking(self.service.pipeline.submission_rejected, run_id, owner,
                                        {"version": 1, "runId": run_id, "claimToken": claim["claimToken"], "httpStatus": 400,
                                         "error": exc.error, "nodeErrors": getattr(exc, "node_errors", [])})
                else:
                    await self.blocking(self.runs.update, run_id, status="failed", error=exc.error)
            else:
                await self.blocking(self.runs.submitted, run_id, owner, claim["claimToken"])
        except (Exception, asyncio.CancelledError):
            await self.blocking(self.runs.submitted, run_id, owner, claim["claimToken"])
            # Cancellation of the HTTP request does not cancel the accepted generation.
        return await self.blocking(self.reconcile, run_id)

    def history_for(self, prompt_id):
        if hasattr(self.adapter, "native_url"):
            return self.adapter.history(prompt_id)
        # Test adapters and legacy integrations keep their original interface.
        return self.adapter.history()

    def _matches(self, run, item):
        if not isinstance(item, (tuple, list)) or len(item) < 4:
            return False
        meta = item[3].get("extra_pnginfo", {}).get("ps_bridge_v3", {})
        return (meta == run["extra_data"]["extra_pnginfo"]["ps_bridge_v3"]
                and digest(execution_prompt(item[2])) == digest(run["prompt"]))

    def reconcile(self, run_id):
        with self.runs.lock:
            run = self.runs.get(run_id)
            if run["status"] in TERMINAL or run["status"] == "prepared":
                return self.runs.public(run)
            running, pending = self.adapter.queue()
            if run["promptId"]:
                history = self.history_for(run["promptId"])
            elif time.monotonic() - self.unknown_history_checked.get(run_id, float("-inf")) >= 15:
                # Only an unknown submission needs metadata lookup; never scan
                # the whole history once per second for every active run.
                history = self.adapter.history()
                self.unknown_history_checked[run_id] = time.monotonic()
                self.unknown_history_checked.move_to_end(run_id)
                if len(self.unknown_history_checked) > 128:
                    self.unknown_history_checked.popitem(last=False)
            else:
                history = {}
            matches = [(str(item[1]), state) for state, items in (("running", running), ("queued", pending))
                       for item in items if self._matches(run, item)]
            matches += [(str(pid), "history") for pid, entry in history.items() if self._matches(run, entry.get("prompt"))]
            ids = {pid for pid, _ in matches}
            require(len(ids) <= 1, "ID_REUSE", "More than one prompt claims this run; automatic recovery stopped")
            if not matches:
                # Empty queue/history is not proof the original /prompt was not received.
                return self.runs.public(run)
            prompt_id, state = next((v for v in matches if v[1] == "history"), matches[0])
            require(run["promptId"] in (None, prompt_id), "ID_REUSE", "Recovered prompt conflicts with recorded identity")
            if run["promptId"] is None:
                self.runs.submitted(run_id, run["controllerClientId"], run["claimToken"], prompt_id)
                run = self.runs.get(run_id)
            if state == "running" and run["status"] != "cancel_requested":
                self.runs.update(run_id, status="running")
            elif state == "history":
                entry = history[prompt_id]
                if self.service and self.service.pipeline.has_observer(run_id):
                    self.service.pipeline.recover_ui(run_id, entry)
                status = entry.get("status", {})
                if status.get("completed") or status.get("status_str") == "error":
                    messages = status.get("messages", [])
                    interrupted = any(m[0] == "execution_interrupted" for m in messages)
                    success = status.get("status_str") == "success" and any(m[0] == "execution_success" for m in messages)
                    # Results are journaled by VP_SendToPS before ComfyUI completes. Never recover another node's files.
                    self.runs.finish(run_id, success=success, interrupted=interrupted,
                                     execution_error=execution_failure(messages))
            result = self.runs.public(self.runs.get(run_id))
            self.notify(result)
            return result

    def cancel(self, run_id, owner, message_id):
        from .parameters import identifier
        identifier(message_id, "cancelMessageId")
        with self.runs.lock:
            run = self.runs.get(run_id, owner)
            previous = run.get("cancelMessages", {})
            if message_id in previous:
                return {"status": previous[message_id], "run": self.runs.public(run)}
            if run["status"] in TERMINAL:
                result = "already_finished"
            elif run["status"] == "prepared":
                self.runs.update(run_id, status="cancelled")
                result = "cancelled"
            else:
                self.reconcile(run_id)
                run = self.runs.get(run_id, owner)
                if run["status"] in TERMINAL:
                    result = "already_finished"
                else:
                    pid = run["promptId"]
                    require(pid is not None, "CANCEL_NOT_OWNER", "Resolve the submission before cancelling")
                    running, pending = self.adapter.queue()
                    if any(str(item[1]) == pid and self._matches(run, item) for item in pending):
                        removed = self.adapter.cancel_queued(pid)
                        if removed:
                            self.runs.update(run_id, status="cancelled")
                            result = "cancelled"
                        else:
                            raise BridgeError("CANCEL_NOT_OWNER", "Queue ownership changed; query status before retrying", retryable=True)
                    elif len(running) == 1 and str(running[0][1]) == pid and self._matches(run, running[0]):
                        self.adapter.interrupt_owned(pid)
                        self.runs.update(run_id, status="cancel_requested")
                        result = "cancel_requested"
                    else:
                        raise BridgeError("CANCEL_NOT_OWNER", "Cannot confirm this run owns the current server task")
            previous[message_id] = result
            self.runs.update(run_id, cancelMessages=previous)
            return {"status": result, "run": self.runs.public(self.runs.get(run_id))}

    async def watch(self):
        while True:
            try:
                active = await self.blocking(self.runs.active)
            except Exception as exc:
                # A full bounded work queue is temporary. Keep the recovery
                # monitor alive so cold/legacy runs resume when capacity returns.
                logging.getLogger(__name__).warning("Run enumeration failed: %s", type(exc).__name__)
                await asyncio.sleep(1)
                continue
            for run in active:
                if run["status"] == "prepared":
                    continue
                try:
                    if self.service:
                        record = self.service.pipeline.observers.get(run["runId"])
                        if record and not record["task"].done() and record["ready"].is_set():
                            continue
                    await self.blocking(self.reconcile, run["runId"])
                except Exception as exc:
                    # A failed observation grants no retry/submit authority and never rewinds state.
                    logging.getLogger(__name__).warning("Run observation failed: %s", type(exc).__name__)
            await asyncio.sleep(1)
