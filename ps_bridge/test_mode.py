"""Explicit editor-owned drafts with atomic, revision-checked capture."""
from __future__ import annotations
import copy
import time
import uuid
from .errors import require
from .json_codec import digest
from .manifest import validate, definition_digest, api_prompt
from .parameters import identifier

class TestTargetRegistry:
    def __init__(self, *, installed=None, clock=time.monotonic, heartbeat_timeout=90):
        self.installed, self.clock, self.timeout = installed, clock, heartbeat_timeout
        self.targets, self.captures = {}, {}

    def target(self, owner, payload):
        scope = payload.get("scope", {})
        from .parameter_sync import validate_scope
        key = validate_scope(scope)
        require(scope["domain"] == "test" and scope["ownerClientId"] == owner, "FORBIDDEN", "Test target must be owned by this editor", status=403)
        manifest = payload.get("manifest")
        prompt = validate(manifest, payload.get("api"), installed=self.installed, ui=payload.get("ui"), published=False)
        require(manifest["providerId"] == scope["providerId"] and manifest["workflowId"] == scope["workflowId"],
                "BINDING_INVALID", "Draft manifest identity differs from test scope")
        fingerprint = digest({"manifest": manifest, "api": prompt, "ui": payload["ui"]})
        prior = self.targets.get(key)
        require(prior is None or prior["fingerprint"] == fingerprint, "DRAFT_CHANGED", "Use a new draftRevision for an edited definition")
        # Changing this graph's draft invalidates all prior targets and outstanding captures.
        for old_key, target in self.targets.items():
            if target["scope"]["ownerClientId"] == owner and target["scope"]["graphId"] == scope["graphId"] and old_key != key:
                target["online"] = False
        self.targets[key] = {"scope": copy.deepcopy(scope), "manifest": copy.deepcopy(manifest), "prompt": prompt,
                             "ui": copy.deepcopy(payload["ui"]), "fingerprint": fingerprint,
                             "lastSeen": self.clock(), "online": True}
        return {"scope": scope, "status": "ready", "definitionSha256": manifest["definitionSha256"]}

    def definition(self, scope):
        target = self.targets.get(digest(scope))
        require(target is not None and target["online"] and self.clock() - target["lastSeen"] <= self.timeout,
                "TEST_TARGET_UNAVAILABLE", "The selected test graph is offline or changed")
        return {"manifest": copy.deepcopy(target["manifest"]), "prompt": copy.deepcopy(target["prompt"])}

    def heartbeat(self, owner, scope):
        require(scope.get("ownerClientId") == owner, "FORBIDDEN", "Not the graph owner", status=403)
        self.definition(scope)
        self.targets[digest(scope)]["lastSeen"] = self.clock()
        return {"scope": scope, "status": "ready"}

    def disconnected(self, owner):
        for target in self.targets.values():
            if target["scope"]["ownerClientId"] == owner:
                target["online"] = False

    def invalidate(self, owner, scope):
        require(scope.get("ownerClientId") == owner, "FORBIDDEN", "Not the graph owner", status=403)
        target = self.targets.get(digest(scope))
        if target:
            target["online"] = False

    def begin_capture(self, scope, revision):
        self.definition(scope)
        capture_id = uuid.uuid4().hex
        self.captures[capture_id] = {"scope": copy.deepcopy(scope), "revision": revision, "started": self.clock(), "snapshot": None}
        return {"captureRequestId": capture_id, "scope": scope, "parameterRevision": revision}

    def accept_capture(self, owner, payload, session):
        capture_id = identifier(payload.get("captureRequestId"), "captureRequestId")
        capture = self.captures.get(capture_id)
        require(capture is not None and self.clock() - capture["started"] <= 15, "TEST_TARGET_UNAVAILABLE", "Capture request expired")
        scope = capture["scope"]
        require(owner == scope["ownerClientId"] and payload.get("scope") == scope, "DRAFT_CHANGED", "Capture graph changed")
        self.definition(scope)
        require(payload.get("parameterRevision") == capture["revision"] == session["revision"],
                "DRAFT_CHANGED", "Parameters changed during capture")
        from .manifest import validate_ui
        validate_ui(payload.get("ui"))
        prompt = api_prompt(payload.get("api"))
        require(definition_digest(session["manifest"], prompt) == session["manifest"]["definitionSha256"],
                "DRAFT_CHANGED", "Definition changed during capture")
        for parameter in session["manifest"]["parameters"]:
            target = parameter["target"]
            require(prompt[target["nodeId"]]["inputs"][target["inputName"]] == session["values"][parameter["paramId"]],
                    "DRAFT_CHANGED", "Exported widget values differ from committed values")
            if "modeTarget" in parameter:
                require(prompt[target["nodeId"]]["inputs"]["mode"] == session["seedModes"][parameter["paramId"]],
                        "DRAFT_CHANGED", "Exported Seed strategy differs from committed strategy")
        capture["snapshot"] = {"prompt": prompt, "ui": copy.deepcopy(payload["ui"])}
        return {"captureRequestId": capture_id, "status": "captured"}

    def frozen_capture(self, scope, revision, capture_id):
        self.definition(scope)
        capture = self.captures.get(capture_id)
        require(capture is not None and capture["scope"] == scope and capture["revision"] == revision and capture["snapshot"] is not None,
                "DRAFT_CHANGED", "A matching atomic editor capture is required")
        require(self.clock() - capture["started"] <= 15, "TEST_TARGET_UNAVAILABLE", "Test capture expired")
        return copy.deepcopy(capture["snapshot"])
