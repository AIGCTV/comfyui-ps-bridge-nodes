"""Pipeline v1 sidecar journal and run-isolated native WebSocket observers.

The run store lock is also the subscription boundary. Core snapshots never gain
extension fields. Slow subscribers reconnect to durable state instead of growing
an unbounded in-memory event history.
"""
from __future__ import annotations
import asyncio
import copy
import time
import threading
import uuid
from pathlib import Path
from aiohttp import ClientSession, ClientTimeout, WSMsgType
from jsonschema import Draft202012Validator
from .errors import BridgeError, require
from .json_codec import loads, canonical_bytes, byte_digest, digest
from .parameter_sync import validate_scope
from .protocol import BRIDGE_MAX_MESSAGE_BYTES
from .requests import TERMINAL
from .storage import atomic_json, atomic_bytes, read_json
from .diagnostics import trace

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "docs/contracts/bridge-v3/pipeline-v1.schema.json"
SCHEMA_BYTES = SCHEMA_PATH.read_bytes()
SCHEMA = loads(SCHEMA_BYTES)
UI_LIMIT = 16 * 1024 * 1024
INLINE_UI_LIMIT = 256 * 1024


def validate_pipeline(name, value):
    schema = {**SCHEMA, "$ref": f"#/$defs/{name}"}
    require(Draft202012Validator(schema).is_valid(value), "MESSAGE_INVALID", "Invalid pipeline " + name)
    canonical_bytes(value)
    return value


def safe_name(value, limit):
    return value if isinstance(value, str) and 0 < len(value) <= limit and all(c.isalnum() or c in "_.:- " for c in value) else None


def rejection_diagnostic(body, prompt):
    """Never retain validator text, received values, credentials or input config."""
    nodes = []
    source = body.get("node_errors", {}) if isinstance(body, dict) else {}
    if isinstance(source, dict):
        for node_id, node in list(source.items())[:64]:
            if not safe_name(node_id, 256) or not isinstance(node, dict):
                continue
            errors = []
            for error in node.get("errors", [])[:16]:
                if not isinstance(error, dict):
                    continue
                item = {"code": safe_name(error.get("type"), 128) or "validation_failed", "message": "Node input validation failed"}
                extra = error.get("extra_info", {})
                field = safe_name(extra.get("input_name"), 128) if isinstance(extra, dict) else None
                if field:
                    item["inputName"] = field
                errors.append(item)
            if errors:
                nodes.append({"nodeId": node_id, "nodeType": safe_name(prompt.get(node_id, {}).get("class_type"), 256) or "UnknownNode", "errors": errors})
    return nodes


class Subscription:
    def __init__(self, loop, owner, scope, run_id):
        self.loop, self.owner, self.scope, self.run_id = loop, owner, scope, run_id
        self.queue = asyncio.Queue(maxsize=8)
        self.bytes = 0
        self.closed = False
        self.lock = threading.Lock()
        self.pending = []
        self.scheduled = False

    def put(self, event):
        size = len(canonical_bytes(event))
        with self.lock:
            if self.closed:
                return
            if self.queue.qsize() + len(self.pending) >= 8 or self.bytes + size > 4 * 1024 * 1024:
                self.closed = True
                self.pending.clear()
            else:
                self.bytes += size
                self.pending.append((event, size))
            if self.scheduled:
                return
            self.scheduled = True
        self.loop.call_soon_threadsafe(self._drain)

    def _drain(self):
        with self.lock:
            self.scheduled = False
            if self.closed:
                while not self.queue.empty():
                    self.queue.get_nowait()
                self.queue.put_nowait(None)
                self.bytes = 0
                return
            for item in self.pending:
                self.queue.put_nowait(item)
            self.pending.clear()

    async def next(self):
        item = await self.queue.get()
        if item is None:
            return None
        event, size = item
        with self.lock:
            self.bytes -= size
        return event


class Pipeline:
    def __init__(self, service):
        self.service, self.runs = service, service.runs
        self.root = service.root / "pipeline"
        self.epoch = service.sessions.epoch
        self.subscriptions = set()
        self.observers = {}
        self.observer_lock = asyncio.Lock()
        self.on_event = lambda event: None
        self.runs.on_change = self.changed

    def capabilities(self):
        adapter = self.service.runner.adapter if self.service.runner else None
        require(adapter is not None and hasattr(adapter, "native_url"), "NODE_UNAVAILABLE", "Native observation is unavailable", status=503)
        return {"version": 1, "serverEpoch": self.epoch, "schemaSha256": byte_digest(SCHEMA_BYTES),
                "capabilities": ["observer", "snapshot", "events", "submission-rejected"]}

    def _path(self, run_id):
        return self.root / (digest(run_id) + ".json")

    def _state(self, run, *, creating=False):
        state = read_json(self._path(run["runId"]))
        if state is None:
            state = {"runId": run["runId"], "nodeOutputs": {}, "sequence": 0}
            scope_path = self.root / "scopes" / (digest(run["scope"]) + ".json")
            current = read_json(scope_path) or {}
            # Retain preparation order across process restarts; opening an old
            # snapshot must never turn it into the newest editor run.
            order = max(1, int(run["createdAt"] * 1_000_000))
            if creating or not current:
                order = max(order, current.get("runOrder", 0) + 1)
                atomic_json(scope_path, {"runId": run["runId"], "runOrder": order, "createdAt": run["createdAt"]})
            elif current.get("runId") == run["runId"]:
                order = current["runOrder"]
            else:
                # A pre-extension run has no sidecar. Its wall clock may be
                # ahead of a newer run after a clock correction; recovery is
                # never authority to replace an established latest pointer.
                order = max(1, min(order, current["runOrder"] - 1))
            state["runOrder"] = order
        if state.get("serverEpoch") != self.epoch:
            state.update(serverEpoch=self.epoch, sequence=0,
                         publicDigest=digest(run))
            atomic_json(self._path(run["runId"]), state)
        return state

    def authorize(self, run_id, owner, scope):
        validate_scope(scope)
        run = self.runs.get(run_id, owner)
        require(run["scope"] == scope, "FORBIDDEN", "Run belongs to a different scope", status=403)
        return run

    def latest(self, scope):
        validate_scope(scope)
        with self.runs.lock:
            current = read_json(self.root / "scopes" / (digest(scope) + ".json"))
            return {k: current[k] for k in ("runId", "runOrder")} if current else None

    def _event(self, run, state, kind, payload=None):
        payload = copy.deepcopy(payload or {})
        if kind in {"snapshot", "input.ready", "result.ready", "execution.started", "execution.succeeded", "execution.failed", "execution.interrupted"}:
            payload["run"] = run
        if kind == "snapshot":
            payload["nodeOutputs"] = state.get("nodeOutputs", {})
            payload["execution"] = state.get("execution", {})
            if state.get("submissionRejection"):
                payload["submissionRejection"] = state["submissionRejection"]
            if state.get("observationError"):
                payload["observationError"] = state["observationError"]
        ui = payload.get("nodeOutputs")
        if ui and len(canonical_bytes(ui)) > INLINE_UI_LIMIT:
            raw = canonical_bytes(ui)
            resource_id = byte_digest(raw)
            self._retain_resource(run["runId"], resource_id, raw)
            payload.pop("nodeOutputs")
            payload["nodeOutputsResource"] = {"url": "/ps-bridge/v3/editor/pipeline/resources/" + resource_id,
                                                "byteSize": len(raw), "sha256": resource_id}
        event = {"version": 1, "serverEpoch": self.epoch, "scope": run["scope"], "runId": run["runId"],
                 "promptId": run["promptId"], "runOrder": state["runOrder"], "sequence": state["sequence"], "kind": kind, "payload": payload}
        require(len(canonical_bytes(event)) <= BRIDGE_MAX_MESSAGE_BYTES, "MESSAGE_TOO_LARGE", "Pipeline snapshot exceeds the control message limit", status=413)
        return event

    def _publish(self, event, run):
        for subscription in tuple(self.subscriptions):
            if subscription.owner == run["controllerClientId"] and subscription.scope == run["scope"] and subscription.run_id in (None, run["runId"]):
                subscription.put(event)
        self.on_event(copy.deepcopy(event))

    def changed(self, run, previous):
        with self.runs.lock:
            if run["runId"] in self.service.execution.runs:
                self.service.execution.track(run)
            state = self._state(run, creating=previous is None)
            state["sequence"] += 1
            state["publicDigest"] = digest(run)
            atomic_json(self._path(run["runId"]), state)
            kind = "snapshot"
            if previous is None:
                kind = "input.ready"
            elif run["status"] != previous["status"]:
                kind = {"running": "execution.started", "succeeded": "execution.succeeded", "failed": "execution.failed", "cancelled": "execution.interrupted"}.get(run["status"], kind)
            elif run["results"] != previous["results"]:
                kind = "result.ready"
            self._publish(self._event(run, state, kind), run)
            if self.service.runner and not state.get("controllerSubscribed"):
                self.service.runner.notify(run)

    def snapshot(self, run_id, owner, scope):
        with self.runs.lock:
            run = self.runs.public(self.authorize(run_id, owner, scope))
            state = self._state(run)
            if (run["status"] in TERMINAL and run["promptId"] and state.get("nativeClientId")
                    and not state.get("historyUiRecovered") and self.service.runner):
                entry = self.service.runner.history_for(run["promptId"]).get(run["promptId"])
                if entry and self.service.runner._matches(run, entry.get("prompt")):
                    self.recover_ui(run_id, entry)
                    state = self._state(run)
            # Covers a recovered write interrupted between the core and sidecar journals.
            if state.get("publicDigest") != digest(run):
                state["sequence"] += 1
                state["publicDigest"] = digest(run)
                atomic_json(self._path(run_id), state)
            return self._event(run, state, "snapshot")

    def subscribe(self, loop, owner, scope, run_id):
        with self.runs.lock:
            require(len(self.subscriptions) < 32, "BUSY", "Too many pipeline subscriptions", retryable=True, status=503)
            if run_id is None:
                validate_scope(scope)
                current = read_json(self.root / "scopes" / (digest(scope) + ".json"))
                require(current is not None, "RUN_NOT_FOUND", "Scope has no prepared run", status=404)
                initial = self.snapshot(current["runId"], owner, scope)
            else:
                initial = self.snapshot(run_id, owner, scope)
            run = initial["payload"]["run"]
            state = self._state(run)
            if not state.get("controllerSubscribed"):
                state["controllerSubscribed"] = True
                atomic_json(self._path(run["runId"]), state)
            subscription = Subscription(loop, owner, copy.deepcopy(scope), run_id)
            subscription.put(initial)
            self.subscriptions.add(subscription)
            return subscription

    def unsubscribe(self, subscription):
        with self.runs.lock:
            self.subscriptions.discard(subscription)
            subscription.closed = True

    def resource(self, run_id, resource_id):
        require(isinstance(resource_id, str) and len(resource_id) == 64 and all(c in "0123456789abcdef" for c in resource_id), "BINDING_INVALID", "Invalid UI resource")
        with self.runs.lock:
            root = self.root / "resources" / digest(run_id)
            entries = read_json(root / "index.json") or []
            entry = next((e for e in entries if e["id"] == resource_id), None)
            require(entry and entry["expiresAt"] > time.time(), "RESULT_NOT_FOUND", "UI resource expired; request a fresh snapshot", status=404)
            raw = (root / (str(entry["slot"]) + ".json")).read_bytes()
            require(len(raw) <= UI_LIMIT and byte_digest(raw) == resource_id, "BINDING_INVALID", "UI resource integrity failed")
            return raw

    def _retain_resource(self, run_id, resource_id, raw):
        root = self.root / "resources" / digest(run_id)
        entries = read_json(root / "index.json") or []
        now = time.time()
        entry = next((e for e in entries if e["id"] == resource_id), None)
        if entry:
            entry["expiresAt"] = now + 600
        else:
            # Fixed slots are reused only after every issued URL for that slot
            # has expired. No cleanup deletes run assets or active references.
            expired = next((e for e in entries if e["expiresAt"] <= now), None)
            slot = expired["slot"] if expired else len(entries)
            retained = [e for e in entries if e is not expired]
            require(slot < 128 and sum(e["size"] for e in retained) + len(raw) <= 64 * 1024 * 1024,
                    "BUSY", "Node UI resource budget is full", retryable=True, status=503)
            atomic_bytes(root / (str(slot) + ".json"), raw)
            entries = retained + [{"id": resource_id, "size": len(raw), "slot": slot, "expiresAt": now + 600}]
        atomic_json(root / "index.json", entries)

    def has_observer(self, run_id):
        with self.runs.lock:
            state = read_json(self._path(run_id))
            return bool(state and state.get("nativeClientId"))

    def observer_identity(self, run_id, owner, scope):
        with self.runs.lock:
            run = self.authorize(run_id, owner, scope)
            self.service.execution.track(run)
            state = self._state(self.runs.public(run))
            if not state.get("nativeClientId"):
                state["nativeClientId"] = "ps-pipeline-" + uuid.uuid4().hex
                atomic_json(self._path(run_id), state)
            self._publish(self._event(self.runs.public(run), state, "snapshot"), run)
            return state["nativeClientId"]

    def recover_ui(self, run_id, entry):
        with self.runs.lock:
            run = self.runs.public(self.runs.get(run_id))
            state = self._state(run)
            outputs = copy.deepcopy(state["nodeOutputs"])
            meta = entry.get("meta", {})
            for node, output in entry.get("outputs", {}).items():
                outputs[str(node)] = {"output": output, "display_node": str(meta.get(node, {}).get("display_node", node))}
            if outputs == state["nodeOutputs"]:
                if not state.get("historyUiRecovered"):
                    state["historyUiRecovered"] = True
                    atomic_json(self._path(run_id), state)
                return
            require(len(canonical_bytes(outputs)) <= UI_LIMIT, "MESSAGE_TOO_LARGE", "Recovered node UI exceeds budget")
            state["nodeOutputs"] = outputs
            state["historyUiRecovered"] = True
            state["sequence"] += 1
            atomic_json(self._path(run_id), state)
            self._publish(self._event(run, state, "snapshot"), run)

    async def observe(self, run_id, owner, scope):
        self.capabilities()
        native_id = await self.service.work.run(self.observer_identity, run_id, owner, scope)
        async with self.observer_lock:
            record = self.observers.get(run_id)
            if record is None or record["task"].done():
                self.observers = {key: value for key, value in self.observers.items() if not value["task"].done()}
                require(len(self.observers) < 32, "BUSY", "Too many native observers", retryable=True, status=503)
                record = {"ready": asyncio.Event()}
                record["task"] = asyncio.create_task(self._observe(run_id, native_id, record["ready"]))
                self.observers[run_id] = record
        try:
            await asyncio.wait_for(record["ready"].wait(), 8)
        except asyncio.TimeoutError as exc:
            raise BridgeError("NODE_UNAVAILABLE", "Native observer did not become ready", retryable=True, status=503) from exc
        return {"version": 1, "serverEpoch": self.epoch, "runId": run_id, "nativeClientId": native_id, "ready": True}

    async def _observe(self, run_id, native_id, ready):
        from .native_execution import NativeExecutionPump
        adapter = self.service.runner.adapter
        deadline = time.monotonic() + 120
        pump = NativeExecutionPump(self, run_id)
        try:
            async with ClientSession(timeout=ClientTimeout(total=None, connect=5)) as session:
                while True:
                    try:
                        async with session.ws_connect(adapter.native_url("/ws"), params={"clientId": native_id}, heartbeat=20, max_msg_size=UI_LIMIT) as ws:
                            # Wait for ComfyUI's status handshake: HTTP upgrade alone
                            # does not prove the sid was registered in its socket map.
                            first = await asyncio.wait_for(ws.receive(), 5)
                            require(first.type == WSMsgType.TEXT, "NODE_UNAVAILABLE", "Native observer handshake failed")
                            handshake = loads(first.data)
                            require(handshake.get("type") == "status" and handshake.get("data", {}).get("sid") == native_id,
                                    "NODE_UNAVAILABLE", "Native observer identity mismatch")
                            await ws.send_json({"type": "feature_flags", "data": {"supports_preview_metadata": True}})
                            self.service.execution.observation(run_id, "connected")
                            text_metadata = False
                            ready.set()
                            while True:
                                try:
                                    message = await asyncio.wait_for(ws.receive(), 15)
                                except asyncio.TimeoutError:
                                    run = await self._native_work(self.service.runner.reconcile, run_id)
                                    if run["status"] in TERMINAL or (run["status"] == "prepared" and time.monotonic() > deadline):
                                        return
                                    continue
                                if message.type == WSMsgType.TEXT:
                                    event = loads(message.data)
                                    kind = event.get("type")
                                    if kind == "executed" and isinstance(event.get("data"), dict):
                                        data = event["data"]
                                        trace("observer.output.received", runId=run_id, promptId=data.get("prompt_id"), nodeId=data.get("node"))
                                    if kind == "feature_flags":
                                        text_metadata = event.get("data", {}).get("supports_progress_text_metadata") is True
                                    accepted = await pump.feed(kind, event.get("data"))
                                    if accepted and kind in {"execution_success", "execution_error", "execution_interrupted"}:
                                        await pump.close()
                                        run = await self._native_work(self.runs.get, run_id)
                                        if run["status"] in TERMINAL:
                                            return
                                elif message.type == WSMsgType.BINARY:
                                    self.service.execution.binary(run_id, message.data, text_metadata=text_metadata)
                                elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                                    break
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        self.service.execution.observation(run_id, "recovering")
                        await self._native_work(self.observation_error, run_id, getattr(exc, "code", type(exc).__name__))
                    ready.clear()
                    self.service.execution.observation(run_id, "recovering")
                    run = await self._native_work(self.service.runner.reconcile, run_id)
                    if run["status"] in TERMINAL or (run["status"] == "prepared" and time.monotonic() > deadline):
                        return
                    await asyncio.sleep(0.5)
        finally:
            ready.clear()
            await pump.close()

    async def _native_work(self, function, *args):
        # At most one pending message per bounded observer is retained here.
        # Backpressure keeps the already-received event intact; HTTP callers
        # still receive BUSY rather than entering another unbounded queue.
        while True:
            try:
                return await self.service.work.run(function, *args)
            except BridgeError as exc:
                if exc.code != "BUSY":
                    raise
                await asyncio.sleep(0.05)

    def observation_error(self, run_id, reason):
        with self.runs.lock:
            run = self.runs.public(self.runs.get(run_id))
            state = self._state(run)
            if state.get("observationError") == reason:
                return
            state["observationError"] = reason
            state["sequence"] += 1
            atomic_json(self._path(run_id), state)
            self._publish(self._event(run, state, "snapshot"), run)

    def native_event(self, run_id, kind, data, *, publish_live=True):
        mapped = {"execution_start": "execution.started", "executing": "node.executing", "execution_cached": "execution.cached",
                  "executed": "node.output", "progress": "progress"}
        if kind not in {*mapped, "progress_state", "progress_text", "execution_success", "execution_error", "execution_interrupted"} or not isinstance(data, dict):
            return
        prompt_id = data.get("prompt_id")
        if not isinstance(prompt_id, str):
            return
        with self.runs.lock:
            run = self.runs.get(run_id)
            if run["status"] in TERMINAL:
                return
            if run["promptId"] is None:
                running, pending = self.service.runner.adapter.queue()
                candidates = [*running, *pending]
                if not any(str(item[1]) == prompt_id and self.service.runner._matches(run, item) for item in candidates):
                    candidates += [entry.get("prompt") for entry in self.service.runner.history_for(prompt_id).values()]
                require(any(isinstance(item, (list, tuple)) and str(item[1]) == prompt_id and self.service.runner._matches(run, item) for item in candidates),
                        "BINDING_INVALID", "Native event does not match the prepared execution")
                self.runs.associate_execution(run_id, prompt_id)
            elif run["promptId"] != prompt_id:
                return
            elif kind == "execution_start":
                self.runs.associate_execution(run_id, prompt_id)
            run = self.runs.public(self.runs.get(run_id))
            self.service.execution.track(run)
            if publish_live:
                self.service.execution.native(run_id, kind, data)
            if kind in {"progress_state", "progress_text"}:
                return  # Editor-only extensions never enter Pipeline v1's enum.
            state = self._state(run)
            if kind in {"execution_success", "execution_error", "execution_interrupted"}:
                from .backend_runner import execution_failure
                self.runs.finish(run_id, success=kind == "execution_success", interrupted=kind == "execution_interrupted",
                                 execution_error=execution_failure([[kind, data]]))
                return
            payload = {}
            if kind == "executed":
                node = str(data.get("node", ""))
                require(node and len(node) <= 256, "BINDING_INVALID", "Invalid native UI node")
                output = {"output": data.get("output"), "display_node": str(data.get("display_node", node))}
                outputs = state["nodeOutputs"]
                if outputs.get(node) == output:
                    return
                outputs[node] = output
                require(len(canonical_bytes(outputs)) <= UI_LIMIT, "MESSAGE_TOO_LARGE", "Node UI exceeds the retained UI budget", status=413)
                payload["nodeOutputs"] = {node: output}
            else:
                for key in ("node", "display_node", "nodes", "value", "max"):
                    if key in data:
                        payload[key] = data[key]
                if kind == "execution_start":
                    return  # associate_execution already persisted and emitted it.
                execution = state.setdefault("execution", {})
                state_key = {"executing": "executing", "execution_cached": "cached", "progress": "progress"}[kind]
                if execution.get(state_key) == payload:
                    return
                execution[state_key] = payload
            state["sequence"] += 1
            atomic_json(self._path(run_id), state)
            if kind == "executed":
                trace("pipeline.output.persisted", runId=run_id, promptId=prompt_id, nodeId=node, sequence=state["sequence"])
            self._publish(self._event(run, state, mapped[kind], payload), run)

    def native_message(self, run_id, raw):
        event = loads(raw)
        self.native_event(run_id, event.get("type"), event.get("data"))
        return event.get("type")

    def submission_rejected(self, run_id, owner, payload):
        validate_pipeline("submissionRejected", payload)
        require(payload["runId"] == run_id, "BINDING_INVALID", "Rejected run differs from URL")
        with self.runs.lock:
            run = self.runs.get(run_id, owner)
            require(run.get("claimToken") == payload["claimToken"], "FORBIDDEN", "Invalid submission claim", status=403)
            require(run["promptId"] is None and run["status"] in {"submitting", "submission_unknown", "failed"},
                    "RUN_STATE_INVALID", "Execution evidence cannot be overwritten by a rejection")
            state = self._state(self.runs.public(run))
            # Rebuild diagnostics even though B already redacts, so callers cannot
            # persist arbitrary error messages through this endpoint.
            native = {"node_errors": {n["nodeId"]: {"errors": [{"type": e["code"], "extra_info": {"input_name": e.get("inputName")}} for e in n["errors"]]} for n in payload["nodeErrors"]}}
            rejection = {"httpStatus": 400, "nodeErrors": rejection_diagnostic(native, run["prompt"])}
            if state.get("submissionRejection") == rejection and run["status"] == "failed":
                return self.runs.public(run)
            require(run["status"] != "failed" or state.get("submissionRejection") == rejection, "ID_REUSE", "Conflicting submission rejection")
            state["submissionRejection"] = rejection
            state["sequence"] += 1
            atomic_json(self._path(run_id), state)
            self._publish(self._event(self.runs.public(run), state, "snapshot"), run)
            nodes = rejection["nodeErrors"]
            message = "; ".join("Node " + n["nodeId"] + " (" + n["nodeType"] + "): input validation failed" for n in nodes[:16]) or "ComfyUI rejected the prepared prompt; inspect node validation"
            return self.runs.update(run_id, status="failed", error={"code": "PROMPT_REJECTED", "message": message[:4096], "retryable": False})

    async def close(self):
        tasks = [record["task"] for record in self.observers.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
