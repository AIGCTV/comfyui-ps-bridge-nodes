"""Blocking service transactions. Never access event-loop-owned manager state here."""
import copy

from .errors import BridgeError, require
from .json_codec import digest
from .diagnostics import trace


def session_view(session):
    if session is None:
        return None
    return copy.deepcopy({key: session[key] for key in
        ("sessionId", "scope", "controller", "attachments")}) | {
        "manifest": {"definitionSha256": session["manifest"]["definitionSha256"]}}


def notification_view(service, scope, run_id=None, *, parameters=False):
    # The only nested state-lock order: sessions -> runs. Producers never call us.
    with service.sessions.lock:
        mode = service.test_mode.snapshot()
        session = service.sessions.by_scope.get(digest(scope))
        view = {"session": session_view(session), "mode": mode,
                "latest": service.pipeline.latest(scope), "observed": False}
        if run_id:
            view["observed"] = service.pipeline.has_observer(run_id)
        if parameters and session:
            view["parameters"] = service.sessions.snapshot(session)
        return view


def active_scopes(service):
    with service.sessions.lock:
        return [copy.deepcopy(s["scope"]) for s in service.sessions.by_id.values() if s["attachments"]]


def editor_current(service, client_id, session, token, run_id):
    with service.sessions.lock:
        current = service.sessions.by_id.get(session["sessionId"])
        if not current or current["scope"] != session["scope"]:
            return False
        attachment = current["attachments"].get(token)
        if not attachment or attachment["clientId"] != client_id or attachment["role"] != "editor":
            return False
        latest = service.pipeline.latest(current["scope"])
        return latest is not None and latest["runId"] == run_id


def disconnected(service, client_id):
    with service.sessions.lock:
        service.sessions.disconnected(client_id)
        service.test_mode.disconnected(client_id)


def control_transaction(service, client_id, role, envelope):
    """Return an immutable reply/attachment. Capture waiting and Future completion stay on the loop."""
    kind, payload = envelope["type"], envelope["payload"]
    sessions = service.sessions
    if kind in {"result.received", "result.placed"}:
        require(role == "controller", "FORBIDDEN", "Only controllers may acknowledge PS results", status=403)
        run = service.runs.receipt(payload.get("runId"), client_id, kind.split(".")[1], payload)
        trace("ps." + kind.split(".")[1] + ".receipt", runId=run["runId"], promptId=run["promptId"],
              resultId=payload.get("resultId"), batchIndex=payload.get("batchIndex"))
        return kind, run, None
    with sessions.lock:
        session = None
        response = kind
        if kind == "session.attach":
            result = None
            if payload["scope"]["domain"] == "test":
                if role == "editor":
                    result = service.test_mode.resume_editor(client_id, payload)
                service.test_mode.check_scope(client_id, role, payload["scope"])
            if result is None:
                result = sessions.attach(client_id, role, payload)
            if payload["scope"]["domain"] == "test":
                service.test_mode.attached(client_id, role, payload["scope"])
            response = "session.attached"
        elif kind == "test.mode.subscribe":
            require(role in {"editor", "controller"}, "FORBIDDEN", "Test discovery requires an editor or controller", status=403)
            response, result = "test.mode.state", service.test_mode.snapshot()
        elif kind in {"test.mode.set", "test.mode.stop", "test.target", "test.heartbeat", "test.invalidate"}:
            result = service.test_mode.dispatch(client_id, role, kind, payload, envelope["messageId"])
            response = "test.mode.state"
        else:
            session, attachment = sessions.authorize(client_id, envelope)
            if kind == "parameter.patch":
                result, response = sessions.patch(client_id, envelope), "parameter.committed"
            elif kind == "state.get":
                result, response = sessions.snapshot(session), "state.snapshot"
            elif kind == "session.detach":
                attach_role = attachment["role"]
                result = sessions.detach(client_id, envelope)
                if session["scope"]["domain"] == "test":
                    service.test_mode.detached(client_id, attach_role)
            elif kind in {"parameter.applied", "parameter.durable"}:
                result = sessions.acknowledge(client_id, envelope, durable=kind == "parameter.durable")
                if kind == "parameter.applied" and session["scope"]["domain"] == "test":
                    service.test_mode.applied(client_id, session, attachment)
            elif kind == "test.capture":
                sessions.authorize(client_id, envelope, controller=True)
                require(session["scope"]["domain"] == "test", "BINDING_INVALID", "Capture requires test scope")
                service.test_mode.check_scope(client_id, role, session["scope"])
                result = service.targets.begin_capture(session["scope"], session["revision"])
            elif kind == "test.snapshot":
                require(role == "editor", "FORBIDDEN", "Only graph owners may return a capture", status=403)
                if payload.get("error"):
                    raise BridgeError("DRAFT_CHANGED", "The editor changed during capture")
                result = service.targets.accept_capture(client_id, payload, session)
            else:
                raise BridgeError("MESSAGE_INVALID", "Unknown contract-3 message type")
        frozen_session = session_view(session)
        if kind == "test.capture":
            frozen_session["modeRevision"] = service.test_mode.snapshot()["modeRevision"]
        return response, copy.deepcopy(result), frozen_session
