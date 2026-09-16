"""Authoritative in-memory parameter sessions, scoped to a server epoch."""
from __future__ import annotations
import copy
import threading
import uuid
from .errors import BridgeError, require
from .json_codec import digest
from .parameters import normalize, identifier
from .manifest import definition_digest, api_prompt, validate_ui
from .protocol import PROVIDERS
from .schemas import validate_shape

FEATURE_FIELDS = {"domain", "providerId", "featureId", "workflowId", "workflowVersion", "definitionSha256"}
TEST_FIELDS = {"domain", "providerId", "workflowId", "graphId", "draftRevision", "ownerClientId"}

def validate_scope(scope):
    validate_shape("scope", scope, code="BINDING_INVALID")
    require(isinstance(scope, dict) and scope.get("domain") in {"feature", "test"}, "BINDING_INVALID", "Invalid scope domain")
    require(set(scope) == (FEATURE_FIELDS if scope["domain"] == "feature" else TEST_FIELDS),
            "BINDING_INVALID", "Scope must include the complete identity")
    require(scope["providerId"] in PROVIDERS, "CONTRACT_UNSUPPORTED", "Unknown provider")
    for key, value in scope.items():
        if key == "draftRevision":
            require(type(value) is int and value >= 0, "DRAFT_CHANGED", "Invalid draft revision")
        else:
            identifier(value, key)
    return digest(scope)

class SessionRegistry:
    def __init__(self, definitions, targets):
        self.definitions, self.targets = definitions, targets
        self.epoch = uuid.uuid4().hex
        self.lock = threading.RLock()
        self.by_scope, self.by_id = {}, {}
        self.on_commit = lambda session, result: None

    def snapshot(self, session):
        return {"sessionId": session["sessionId"], "serverEpoch": self.epoch,
                "scope": copy.deepcopy(session["scope"]), "revision": session["revision"],
                "values": copy.deepcopy(session["values"]), "seedModes": copy.deepcopy(session["seedModes"]),
                "definitionSha256": session["manifest"]["definitionSha256"],
                "appliedRevisions": {a["clientId"]: a["appliedRevision"] for a in session["attachments"].values()},
                "durableRevision": session["durableRevision"],
                "editorConnected": any(a["role"] == "editor" for a in session["attachments"].values()),
                "manifest": copy.deepcopy(session["manifest"])}

    def attach(self, client_id, role, payload):
        scope = payload.get("scope")
        key = validate_scope(scope)
        require(role in {"controller", "editor", "observer"}, "FORBIDDEN", "Invalid authenticated role", status=403)
        with self.lock:
            session = self.by_scope.get(key)
            if session is None:
                if scope["domain"] == "feature":
                    require(role == "controller", "SESSION_EXPIRED", "The controller must create the feature session first")
                    definition = self.definitions.get(scope)
                else:
                    definition = self.targets.definition(scope)
                    require(role == "controller" or client_id == scope["ownerClientId"],
                            "FORBIDDEN", "Only the owner or controller may create this test session", status=403)
                manifest = definition["manifest"]
                values = {p["paramId"]: p["default"] for p in manifest["parameters"]}
                modes = {p["paramId"]: p["defaultMode"] for p in manifest["parameters"] if p.get("semantic") == "seed"}
                bootstrap = payload.get("bootstrap", {})
                require(isinstance(bootstrap, dict) and set(bootstrap) <= {"values", "seedModes"}, "PARAMETER_INVALID", "Invalid bootstrap")
                if scope["domain"] == "test":
                    require(not bootstrap, "BINDING_INVALID", "Test values come from the exported draft")
                session = {"sessionId": uuid.uuid4().hex, "scope": copy.deepcopy(scope), "manifest": manifest,
                           "prompt": definition["prompt"], "values": values, "seedModes": modes,
                           "revision": 0, "durableRevision": None, "controller": None, "attachments": {}, "messages": {}}
                session["values"], session["seedModes"] = self.normalize_changes(session, bootstrap.get("values", {}), bootstrap.get("seedModes", {}))
                self.by_scope[key] = session
                self.by_id[session["sessionId"]] = session
            if role == "controller":
                require(session["controller"] in (None, client_id), "CONTROLLER_IN_USE", "This scope already has a controller")
                session["controller"] = client_id
            elif role == "editor":
                graph = payload.get("graph")
                require(isinstance(graph, dict) and {"graphId", "draftRevision", "api", "ui"} <= set(graph),
                        "BINDING_INVALID", "Editor attach requires a formally exported graph")
                identifier(graph["graphId"], "graphId")
                require(type(graph["draftRevision"]) is int and graph["draftRevision"] >= 0, "DRAFT_CHANGED", "Invalid draft revision")
                validate_ui(graph["ui"])
                exported = api_prompt(graph["api"])
                require(definition_digest(session["manifest"], exported) == session["manifest"]["definitionSha256"],
                        "WORKFLOW_VERSION_MISMATCH", "Graph definition differs from this session")
                if scope["domain"] == "test":
                    require(client_id == scope["ownerClientId"] and graph["graphId"] == scope["graphId"]
                            and graph["draftRevision"] == scope["draftRevision"], "DRAFT_CHANGED", "Test graph identity changed")
                    self.targets.definition(scope)
                editors = [(t, a) for t, a in session["attachments"].items() if a["role"] == "editor" and a["clientId"] != client_id]
                require(not editors or payload.get("takeover") is True, "CONTROLLER_IN_USE", "Another editable graph owns this session")
                for token, _ in editors:
                    session["attachments"].pop(token)
            # A reconnect rotates authority; every late message using the old token fails.
            for token, attach in list(session["attachments"].items()):
                if attach["clientId"] == client_id:
                    session["attachments"].pop(token)
            token = uuid.uuid4().hex
            session["attachments"][token] = {"clientId": client_id, "role": role, "appliedRevision": None,
                                            "graph": copy.deepcopy(payload.get("graph", {}))}
            return {"attachToken": token, "snapshot": self.snapshot(session)}

    def authorize(self, client_id, envelope, *, write=False, controller=False):
        require(envelope.get("serverEpoch") == self.epoch, "SESSION_EXPIRED", "Server epoch changed")
        session = self.by_id.get(envelope.get("sessionId"))
        require(session is not None, "SESSION_EXPIRED", "Session no longer exists")
        payload = envelope.get("payload", envelope)
        require(payload.get("scope") == session["scope"], "BINDING_INVALID", "Message scope does not match session")
        attach = session["attachments"].get(envelope.get("attachToken"))
        require(attach is not None and attach["clientId"] == client_id, "SESSION_EXPIRED", "Attachment token expired")
        if write:
            require(attach["role"] in {"controller", "editor"}, "FORBIDDEN", "This attachment is read-only", status=403)
        if controller:
            require(attach["role"] == "controller" and session["controller"] == client_id, "FORBIDDEN", "Only the controller may perform this operation", status=403)
        if session["scope"]["domain"] == "test":
            self.targets.definition(session["scope"])
        return session, attach

    def normalize_changes(self, session, changes, modes):
        require(isinstance(changes, dict) and isinstance(modes, dict), "PARAMETER_INVALID", "Changes must be objects")
        schemas = {p["paramId"]: p for p in session["manifest"]["parameters"]}
        require(changes.keys() <= schemas.keys(), "PARAMETER_INVALID", "Unknown parameter ID")
        values, seed_modes = copy.deepcopy(session["values"]), copy.deepcopy(session["seedModes"])
        for key, value in changes.items():
            try:
                values[key] = normalize(schemas[key], value)
            except BridgeError as exc:
                exc.error["field"] = key
                raise
        for key, value in modes.items():
            require(key in seed_modes and value in ("fixed", "random"), "PARAMETER_INVALID", "Invalid Seed strategy", field=key)
            seed_modes[key] = value
        return values, seed_modes

    def commit_values(self, session, values, modes, reply_to=None, *, force=False):
        if not force and values == session["values"] and modes == session["seedModes"]:
            return None
        session["values"], session["seedModes"] = copy.deepcopy(values), copy.deepcopy(modes)
        session["revision"] += 1
        result = {"scope": copy.deepcopy(session["scope"]), "revision": session["revision"],
                  "values": copy.deepcopy(values), "seedModes": copy.deepcopy(modes)}
        self.on_commit(session, {"type": "parameter.committed", "replyTo": reply_to, "payload": result})
        return result

    def patch(self, client_id, envelope):
        with self.lock:
            session, _ = self.authorize(client_id, envelope, write=True)
            message_id = identifier(envelope.get("messageId"), "messageId")
            payload = envelope["payload"]
            key = (client_id, message_id)
            fingerprint = digest(payload)
            if key in session["messages"]:
                cached = session["messages"][key]
                require(cached["digest"] == fingerprint, "ID_REUSE", "messageId was reused with another patch")
                if "error" in cached:
                    raise BridgeError(**copy.deepcopy(cached["error"]), status=cached["status"])
                return copy.deepcopy(cached["result"])
            try:
                require(set(payload) <= {"scope", "baseRevision", "changes", "seedModes"}, "PARAMETER_INVALID", "Unexpected patch field")
                require(type(payload.get("baseRevision")) is int and payload["baseRevision"] == session["revision"],
                        "REVISION_CONFLICT", "Parameter revision changed", currentRevision=session["revision"], snapshot=self.snapshot(session))
                changes = payload.get("changes", [])
                require(isinstance(changes, list) and all(isinstance(v, dict) and set(v) == {"paramId", "value"} for v in changes),
                        "PARAMETER_INVALID", "changes must contain paramId/value pairs")
                require(all(isinstance(v["paramId"], str) for v in changes), "PARAMETER_INVALID", "Invalid parameter ID")
                require(len({v["paramId"] for v in changes}) == len(changes), "PARAMETER_INVALID", "Repeated parameter in patch")
                modes = payload.get("seedModes", {})
                require(changes or modes, "PARAMETER_INVALID", "Patch is empty")
                values, seed_modes = self.normalize_changes(session, {v["paramId"]: v["value"] for v in changes}, modes)
                result = self.commit_values(session, values, seed_modes, message_id, force=True)
            except BridgeError as exc:
                session["messages"][key] = {"digest": fingerprint, "error": copy.deepcopy(exc.error), "status": exc.status}
                raise
            session["messages"][key] = {"digest": fingerprint, "result": copy.deepcopy(result)}
            return result

    def acknowledge(self, client_id, envelope, *, durable=False):
        with self.lock:
            session, attach = self.authorize(client_id, envelope, controller=durable)
            key = "durableRevision" if durable else "appliedRevision"
            revision = envelope["payload"].get(key)
            require(type(revision) is int and 0 <= revision <= session["revision"], "REVISION_CONFLICT", "Invalid acknowledgment revision")
            previous = session["durableRevision"] if durable else attach["appliedRevision"]
            require(previous is None or revision >= previous, "REVISION_CONFLICT", "Acknowledgment cannot move backwards")
            if durable:
                session["durableRevision"] = revision
            else:
                attach["appliedRevision"] = revision
            return self.snapshot(session)

    def detach(self, client_id, envelope):
        with self.lock:
            session, _ = self.authorize(client_id, envelope)
            session["attachments"].pop(envelope["attachToken"])
            return self.snapshot(session)

    def disconnected(self, client_id):
        with self.lock:
            for session in self.by_id.values():
                for token, attach in list(session["attachments"].items()):
                    if attach["clientId"] == client_id:
                        session["attachments"].pop(token)
                if session["scope"]["domain"] == "test" and session["controller"] == client_id:
                    session["controller"] = None
            self.targets.disconnected(client_id)
