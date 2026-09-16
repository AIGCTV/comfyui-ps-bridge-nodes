"""Authenticated connection dispatch; all state is owned by scoped services."""
from __future__ import annotations
import asyncio
import copy
import json
import logging
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from .errors import BridgeError, require
from .json_codec import loads, digest
from .protocol import contract_capabilities, BRIDGE_MAX_MESSAGE_BYTES
from .identity import ClientIdentities
from .parameter_sync import validate_scope
from .schemas import validate_shape
from .manager_notifications import ManagerNotifications
from .diagnostics import trace
from .manager_state import (notification_view, active_scopes, editor_current,
                            control_transaction, disconnected)

logger = logging.getLogger(__name__)

@dataclass
class BridgeClient:
    client_id: str
    role: str
    ws: object
    control_gate: object = field(default_factory=asyncio.Lock)
    retired: bool = False

class BridgeManager:
    def __init__(self, service):
        self.service = service
        self.identities = ClientIdentities(service.root / "clients.json")
        self.clients = {}
        self.loop = None
        self.pending_captures = {}
        self.last_run_event = {}
        self.previews = {}
        self.editor_pipeline_latest = {}
        self.editor_delivery = {}
        self.editor_delivery_lock = threading.Lock()
        self.mode_subscribers = set()
        self.presence_task = None
        self.last_mode_revision = -1
        self.notifications = ManagerNotifications(self._consume_notification, self._recover_notifications)
        service.test_mode.on_change = self.mode_changed
        service.sessions.on_commit = self.committed
        service.preparation.on_prepared = self.preview_ready
        if service.runner:
            service.runner.notify = self.run_changed
        if getattr(service, "pipeline", None):
            service.pipeline.on_event = self.pipeline_changed

    def envelope(self, kind, payload, *, reply_to=None, session=None, token=None):
        result = {"type": kind, "protocolVersion": 2, "contractVersion": 3, "messageId": uuid.uuid4().hex,
                  "sourceClientId": "bridge-server", "payload": payload}
        if reply_to is not None:
            result["replyTo"] = reply_to
        if session is not None:
            result.update(sessionId=session["sessionId"], serverEpoch=self.service.sessions.epoch, attachToken=token)
        return result

    async def send(self, client, kind, payload, **kwargs):
        if not client.ws.closed:
            message = self.envelope(kind, payload, **kwargs)
            validate_shape("serverMessage", message)
            await asyncio.wait_for(client.ws.send_json(message), 5)

    def committed(self, session, event):
        self.notifications.post("committed", {"scope": session["scope"], "event": event})

    def mode_changed(self, state):
        self.notifications.post("mode", state)

    def run_changed(self, run):
        self.notifications.post("run", run)

    def pipeline_changed(self, event):
        self.notifications.post("pipeline", event)

    def preview_ready(self, run):
        self.notifications.post("preview", {"scope": run["scope"], "runId": run["runId"],
            "parameterRevision": run["parameterRevision"],
            "nodes": [{"nodeId": b["nodeId"], "slot": b["slot"]} for b in run["manifest"]["images"]]})

    async def flush_notifications(self):
        self.loop = asyncio.get_running_loop()
        await self.notifications.flush()
        await asyncio.sleep(0)

    async def _consume_notification(self, kind, payload):
        handler = {"committed": self._committed, "mode": self._mode_changed,
                   "run": self._run_changed, "pipeline": self._pipeline_changed, "preview": self._preview_ready}[kind]
        await handler(payload)

    async def _view(self, scope, run_id=None, **kwargs):
        return await self.service.work.finish(notification_view, self.service, scope, run_id, **kwargs)

    async def _recover_notifications(self):
        await self._mode_changed(await self.service.work.finish(self.service.test_mode.snapshot))
        for scope in await self.service.work.finish(active_scopes, self.service):
            view = await self._view(scope, parameters=True)
            session = view["session"]
            if not session:
                continue
            for token, attachment in session["attachments"].items():
                client = self.clients.get(attachment["clientId"])
                if client:
                    await self.send(client, "state.snapshot", view["parameters"], session=session, token=token)
            latest = view["latest"]
            if latest:
                run = await self.service.work.finish(lambda: self.service.runs.public(self.service.runs.get(latest["runId"])))
                await self._run_changed(run)
                await self._preview_ready({"scope": scope, "runId": run["runId"], "parameterRevision": run["parameterRevision"],
                    "nodes": [{"nodeId": b["nodeId"], "slot": b["slot"]} for b in run["manifest"]["images"]]})

    async def _committed(self, payload):
        view = await self._view(payload["scope"])
        session, event = view["session"], payload["event"]
        if not session:
            return
        for token, attach in list(session["attachments"].items()):
            client = self.clients.get(attach["clientId"])
            if client:
                await self.send(client, "parameter.committed", event["payload"],
                                reply_to=event.get("replyTo"), session=session, token=token)

    async def _mode_changed(self, state):
        if state["modeRevision"] < self.last_mode_revision:
            return
        self.last_mode_revision = state["modeRevision"]
        target = state["target"]
        for entry in self.pending_captures.values():
            if (state["modeRevision"] >= entry["modeRevision"] and
                    (state["status"] != "ready" or not target or entry["scope"] != target["scope"]) and not entry["future"].done()):
                entry["future"].set_exception(BridgeError("DRAFT_CHANGED", "Test target changed during capture"))
        for client_id in list(self.mode_subscribers):
            client = self.clients.get(client_id)
            if client:
                await self.send(client, "test.mode.state", state)

    async def monitor_presence(self):
        try:
            while self.clients:
                await asyncio.sleep(2)
                await self.service.work.finish(self.service.test_mode.expire)
        except asyncio.CancelledError:
            pass

    async def _run_changed(self, run):
        fingerprint = digest(run)
        if self.last_run_event.get(run["runId"]) == fingerprint:
            return
        self.last_run_event[run["runId"]] = fingerprint
        if len(self.last_run_event) > 512:
            self.last_run_event.pop(next(iter(self.last_run_event)))
        client = self.clients.get(run["controllerClientId"])
        if client:
            await self.send(client, "run.status", run)
        # API submissions have no browser ComfyUI client_id. Deliver completed
        # previews through the existing attachment, never a global executed event.
        if run["scope"]["domain"] != "test" or run["status"] != "succeeded":
            return
        view = await self._view(run["scope"], run["runId"])
        if view["observed"]:
            return
        mode = view["mode"]
        if not mode["enabled"] or not mode["target"] or mode["target"]["scope"] != run["scope"]:
            return
        session = view["session"]
        if session and session["manifest"]["definitionSha256"] == run["definitionSha256"]:
            for token, attachment in list(session["attachments"].items()):
                editor = self.clients.get(attachment["clientId"])
                if editor and attachment["role"] == "editor" and editor.client_id == run["scope"]["ownerClientId"]:
                    await self.send(editor, "run.status", run, session=session, token=token)

    async def send_editor_pipeline(self, client, event, session, token):
        """Do not extend or reformat the frozen contract-3 serverMessage schema."""
        if client.ws.closed or not await self.service.work.finish(editor_current, self.service,
                client.client_id, session, token, event["runId"]):
            return
        latest = self.editor_pipeline_latest.get(digest(event["scope"]))
        if latest and (event["runOrder"] < latest["runOrder"] or event["runId"] != latest["runId"]):
            return
        if event["kind"] == "snapshot":
            event = {**event, "editorExecution": self.service.execution.snapshot(event["runId"])}
        message = self.envelope("editor.pipeline", event, session=session, token=token)
        raw = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        require(len(raw.encode("utf-8")) <= BRIDGE_MAX_MESSAGE_BYTES,
                "MESSAGE_TOO_LARGE", "Editor pipeline message exceeds size limit", status=413)
        await asyncio.wait_for(client.ws.send_json(message, dumps=lambda _: raw), 5)
        if event["kind"] in {"node.output", "snapshot"}:
            trace("editor.output.sent", runId=event["runId"], promptId=event.get("promptId"), sequence=event["sequence"])

    async def _pipeline_changed(self, event):
        view = await self._view(event["scope"], event["runId"])
        if not view["observed"] or not view["latest"] or view["latest"]["runId"] != event["runId"]:
            return
        scope = event["scope"]
        scope_key = digest(scope)
        latest = self.editor_pipeline_latest.get(scope_key)
        if latest and event["runOrder"] < latest["runOrder"]:
            return
        preview = self.previews.get(scope_key)
        if latest and preview and preview["runId"] != event["runId"] and event["runOrder"] <= latest["runOrder"]:
            return
        # Retain identities only; full UI outputs remain in bounded pipeline storage.
        self.editor_pipeline_latest[scope_key] = {"runId": event["runId"], "runOrder": event["runOrder"]}
        if len(self.editor_pipeline_latest) > 128:
            self.editor_pipeline_latest.pop(next(iter(self.editor_pipeline_latest)))
        session = view["session"]
        if not session:
            return
        if scope["domain"] == "test":
            mode = view["mode"]
            if not mode["enabled"] or not mode["target"] or mode["target"]["scope"] != scope:
                return
        for token, attachment in list(session["attachments"].items()):
            editor = self.clients.get(attachment["clientId"])
            if editor and attachment["role"] == "editor" and (scope["domain"] != "test" or editor.client_id == scope["ownerClientId"]):
                self.queue_editor_pipeline(editor, event, session, token)

    def queue_editor_pipeline(self, client, event, session, token):
        if not self.loop or self.loop.is_closed():
            return
        if event["kind"] in {"node.output", "snapshot"}:
            trace("editor.output.queued", runId=event["runId"], promptId=event.get("promptId"), sequence=event["sequence"])
        size = len(json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        with self.editor_delivery_lock:
            delivery = self.editor_delivery.setdefault(client.client_id,
                {"pending": deque(), "bytes": 0, "scheduled": False, "task": None})
            if len(delivery["pending"]) >= 64 or delivery["bytes"] + size > 4 * 1024 * 1024:
                delivery["pending"].clear()
                delivery["bytes"] = 0
                # The snapshot is read when the drain reaches it, so all omitted
                # UI outputs (including cache hits) are represented durably.
                item = (None, session, token, 0)
            else:
                item = (copy.deepcopy(event), session, token, size)
            delivery["pending"].append(item)
            delivery["bytes"] += item[3]
            if delivery["scheduled"]:
                return
            delivery["scheduled"] = True
        def start():
            if self.editor_delivery.get(client.client_id) is delivery:
                delivery["task"] = asyncio.create_task(self.drain_editor_pipeline(client, delivery))
        self.loop.call_soon_threadsafe(start)

    async def drain_editor_pipeline(self, client, delivery):
        while True:
            with self.editor_delivery_lock:
                if self.editor_delivery.get(client.client_id) is not delivery or not delivery["pending"]:
                    delivery["scheduled"] = False
                    delivery["task"] = None
                    return
                event, session, token, size = delivery["pending"].popleft()
                delivery["bytes"] -= size
            try:
                if event is None:
                    await self.restore_editor_pipeline(client, session, token)
                else:
                    await self.send_editor_pipeline(client, event, session, token)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Editor pipeline delivery failed")

    async def restore_editor_pipeline(self, client, session, token):
        pipeline = getattr(self.service, "pipeline", None)
        current = await self.service.work.finish(pipeline.latest, session["scope"]) if pipeline else None
        if not pipeline:
            return
        if current is None:
            return
        if not await self.service.work.finish(pipeline.has_observer, current["runId"]):
            return
        event = await self.service.work.finish(pipeline.snapshot, current["runId"], session["controller"], session["scope"])
        if token in session["attachments"]:
            self.editor_pipeline_latest[digest(session["scope"])] = {"runId": event["runId"], "runOrder": event["runOrder"]}
            await self.send_editor_pipeline(client, event, session, token)

    async def _preview_ready(self, payload):
        """Send resource identity only to editors attached to this exact scope."""
        view = await self._view(payload["scope"])
        if not view["latest"] or view["latest"]["runId"] != payload["runId"]:
            return
        self.previews[digest(payload["scope"])] = payload
        if len(self.previews) > 128:
            self.previews.pop(next(iter(self.previews)))
        session = view["session"]
        if session:
            for token, attachment in list(session["attachments"].items()):
                client = self.clients.get(attachment["clientId"])
                if client and attachment["role"] == "editor":
                    await self.send_preview(client, payload, session, token)

    async def send_preview(self, client, payload, session, token):
        # Preparation and image loading can finish out of order. Check the latest
        # prepared identity at send time as well as on the authenticated HTTP read.
        if (not await self.service.work.finish(editor_current, self.service, client.client_id, session, token, payload["runId"]) or
                self.previews.get(digest(payload["scope"]), {}).get("runId") != payload["runId"]):
            return
        await self.restore_editor_pipeline(client, session, token)
        if self.previews.get(digest(payload["scope"]), {}).get("runId") == payload["runId"]:
            await self.send(client, "media.preview", payload, session=session, token=token)

    async def add(self, client):
        self.loop = asyncio.get_running_loop()
        self.service.execution.bind_transport(self.send_editor_execution)
        self.notifications.bind()
        existing = self.clients.get(client.client_id)
        require(existing is None, "CONTROLLER_IN_USE", "This client already has a live control connection")
        self.clients[client.client_id] = client
        if self.presence_task is None or self.presence_task.done():
            self.presence_task = asyncio.create_task(self.monitor_presence())

    async def remove(self, client):
        if self.clients.get(client.client_id) is client:
            self.clients.pop(client.client_id)
            client.retired = True
            with self.editor_delivery_lock:
                delivery = self.editor_delivery.pop(client.client_id, None)
            if delivery and delivery["task"]:
                delivery["task"].cancel()
            self.mode_subscribers.discard(client.client_id)
            async with client.control_gate:
                await self.service.work.finish(disconnected, self.service, client.client_id)
            if not self.clients and self.presence_task:
                self.presence_task.cancel()
            for entry in self.pending_captures.values():
                if entry["owner"] == client.client_id and not entry["future"].done():
                    entry["future"].set_exception(BridgeError("TEST_TARGET_UNAVAILABLE", "Test editor disconnected"))

    async def close(self):
        await self.service.execution.close()
        if self.presence_task:
            self.presence_task.cancel()
            await asyncio.gather(self.presence_task, return_exceptions=True)
        tasks = tuple(d["task"] for d in self.editor_delivery.values() if d["task"])
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.notifications.close()

    async def send_editor_execution(self, payload):
        """Live delivery never waits for journal workers or the output queue."""
        # Never block the asyncio thread on a transaction holding the session
        # lock while it persists a business change. Recheck the attachment before
        # every send; a cached lease alone is not authorization after test OFF.
        while not self.service.sessions.lock.acquire(blocking=False):
            await asyncio.sleep(.005)
        try:
            session = self.service.sessions.by_scope.get(digest(payload["scope"]))
            with self.service.execution.lock:
                state = self.service.execution.runs.get(payload["runId"])
                if not session or not state or state["controller"] != session["controller"] or session["manifest"]["definitionSha256"] != payload["definitionSha256"]:
                    return
            deliveries = []
            for token, attachment in session["attachments"].items():
                client = self.clients.get(attachment["clientId"])
                if not client or client.retired or client.ws.closed or client.role != "editor" or attachment["role"] != "editor":
                    continue
                if payload["scope"]["domain"] == "test" and client.client_id != payload["scope"]["ownerClientId"]:
                    continue
                message = self.envelope("editor.execution", payload, session=session, token=token)
                self.service.sessions.authorize(client.client_id, message)
                deliveries.append((client, message))
        finally:
            self.service.sessions.lock.release()
        for client, message in deliveries:
            require(len(json.dumps(message, ensure_ascii=False).encode("utf-8")) <= BRIDGE_MAX_MESSAGE_BYTES,
                    "MESSAGE_TOO_LARGE", "Execution feedback exceeds control budget")
            await asyncio.wait_for(client.ws.send_json(message), 5)

    async def handle(self, client, raw):
        envelope = loads(raw)
        require(isinstance(envelope, dict) and envelope.get("protocolVersion") == 2 and envelope.get("contractVersion") == 3,
                "CONTRACT_UNSUPPORTED", "Expected protocol 2 / contract 3")
        require(envelope.get("sourceClientId") == client.client_id and self.clients.get(client.client_id) is client,
                "FORBIDDEN", "Transport client identity mismatch", status=403)
        validate_shape("clientMessage", envelope)
        require(isinstance(envelope.get("messageId"), str) and envelope["messageId"] and isinstance(envelope.get("payload"), dict),
                "MESSAGE_INVALID", "Envelope requires messageId and payload")
        kind, payload = envelope.get("type"), envelope["payload"]
        self.loop = asyncio.get_running_loop()
        self.notifications.bind()
        entry = None
        if kind == "test.mode.subscribe":
            require(client.role in {"editor", "controller"}, "FORBIDDEN", "Test discovery requires an editor or controller", status=403)
            self.mode_subscribers.add(client.client_id)
        elif kind == "test.snapshot":
            entry = self.pending_captures.get(payload.get("captureRequestId"))
            require(entry is not None and entry["owner"] == client.client_id, "DRAFT_CHANGED", "Unknown capture reply")
        try:
            async with client.control_gate:
                require(not client.retired, "SESSION_EXPIRED", "Transport was closed")
                operation = asyncio.create_task(self.service.work.run(control_transaction, self.service,
                    client.client_id, client.role, envelope))
                try:
                    response_kind, result, session = await asyncio.shield(operation)
                except asyncio.CancelledError:
                    await operation
                    raise
        except BridgeError as exc:
            if entry and not entry["future"].done():
                entry["future"].set_exception(exc)
            raise
        if entry and not entry["future"].done():
            entry["future"].set_result(result)
        if kind == "test.capture":
            owner = self.clients.get(session["scope"]["ownerClientId"])
            require(owner is not None, "TEST_TARGET_UNAVAILABLE", "Test editor is offline")
            request = result
            capture_id = request["captureRequestId"]
            future = asyncio.get_running_loop().create_future()
            self.pending_captures[capture_id] = {"owner": owner.client_id, "scope": session["scope"],
                                               "modeRevision": session["modeRevision"], "future": future}
            try:
                await self.service.work.run(self.service.test_mode.check_scope, client.client_id, client.role, session["scope"])
                editor_attach = next((t for t, a in session["attachments"].items()
                                      if a["clientId"] == owner.client_id and a["role"] == "editor"), None)
                require(editor_attach is not None, "TEST_TARGET_UNAVAILABLE", "Test graph must attach before capture")
                await self.send(owner, "test.capture", request, session=session, token=editor_attach)
                result = await asyncio.wait_for(future, timeout=15)
                response_kind = "test.snapshot"
            except asyncio.TimeoutError as exc:
                raise BridgeError("TEST_TARGET_UNAVAILABLE", "Test editor capture timed out") from exc
            finally:
                self.pending_captures.pop(capture_id, None)
                if not future.done():
                    future.cancel()
                elif not future.cancelled():
                    future.exception()
        await self.send(client, response_kind, result, reply_to=envelope["messageId"], session=session,
                        token=envelope.get("attachToken") if session else None)
        if kind == "session.attach" and client.role == "editor":
            snapshot = result["snapshot"]
            preview = self.previews.get(digest(snapshot["scope"]))
            attached_session = (await self._view(snapshot["scope"]))["session"]
            if not attached_session:
                return
            if preview:
                await self.send_preview(client, preview, attached_session, result["attachToken"])
            else:
                await self.restore_editor_pipeline(client, attached_session, result["attachToken"])
