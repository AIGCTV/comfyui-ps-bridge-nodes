"""One transaction for parameter overrides, random Seed and immutable API."""
from __future__ import annotations
import copy
import secrets
from .errors import require
from .json_codec import digest
from .manifest import compile_prompt
from .protocol import BRIDGE_MAX_SEED
from .schemas import validate_shape

class RunPreparation:
    def __init__(self, sessions, assets, runs, *, random_seed=None):
        self.sessions, self.assets, self.runs = sessions, assets, runs
        self.random_seed = random_seed or (lambda: secrets.randbelow(BRIDGE_MAX_SEED + 1))
        self.on_prepared = lambda run: None

    def prepare(self, owner, payload):
        resources = payload.get("resources") if isinstance(payload, dict) else None
        refs = resources.get("references") if isinstance(resources, dict) else None
        require(not isinstance(refs, list) or len(refs) <= 5, "MEDIA_OVERFLOW",
                "At most five reference images are supported", status=413)
        validate_shape("prepare", payload, code="BINDING_INVALID")
        require(isinstance(payload, dict), "BINDING_INVALID", "Expected a prepare object")
        required = {"runId", "controllerClientId", "sessionId", "serverEpoch", "attachToken", "scope", "expectedRevision", "submitOwner", "resources"}
        require(required <= set(payload) and set(payload) <= required | {"overrides", "captureRequestId"}, "BINDING_INVALID", "Invalid prepare fields")
        require(payload["controllerClientId"] == owner, "FORBIDDEN", "Controller identity mismatch", status=403)
        # Tokens rotate on reconnect; they are never part of semantic idempotency or persisted snapshots.
        fingerprint = digest({k: v for k, v in payload.items() if k != "attachToken"})
        with self.sessions.lock, self.runs.lock:
            existing = self.runs.existing(payload["runId"], owner, fingerprint)
            if existing is not None:
                return existing
            session, _ = self.sessions.authorize(owner, payload, controller=True)
            expected = payload["expectedRevision"]
            require(type(expected) is int and expected == session["revision"], "REVISION_CONFLICT", "Run revision is stale",
                    currentRevision=session["revision"], snapshot=self.sessions.snapshot(session))
            scope = session["scope"]
            require(payload["submitOwner"] == ("bridge" if scope["providerId"] == "comfy_bridge" else "rust"),
                    "BINDING_INVALID", "Submit owner must match the provider")
            manifest = session["manifest"]
            media = self.assets.freeze(owner, payload["resources"], manifest)
            template, ui = session["prompt"], None
            if scope["domain"] == "test":
                capture = self.sessions.targets.frozen_capture(scope, expected, payload.get("captureRequestId"))
                template, ui = capture["prompt"], capture["ui"]
            overrides = payload.get("overrides", {})
            require(isinstance(overrides, dict) and set(overrides) <= {"canonical", "params", "values", "seedModes"},
                    "PARAMETER_INVALID", "Invalid overrides")
            changes = copy.deepcopy(overrides.get("values", {}))
            require(isinstance(changes, dict), "PARAMETER_INVALID", "Override values must be an object")
            sources = {(p["source"]["kind"], p["source"]["key"]): p for p in manifest["parameters"]}
            for field, source_kind in (("canonical", "canonical"), ("params", "parameter")):
                values = overrides.get(field, {})
                require(isinstance(values, dict), "PARAMETER_INVALID", "Source overrides must be objects")
                for key, value in values.items():
                    parameter = sources.get((source_kind, key))
                    require(parameter is not None, "PARAMETER_INVALID", "This function has no binding for the override", field=key)
                    param_id = parameter["paramId"]
                    require(param_id not in changes or changes[param_id] == value, "DUPLICATE_PARAMETER_SOURCE", "Conflicting overrides for the same parameter")
                    changes[param_id] = value
            values, modes = self.sessions.normalize_changes(session, changes, overrides.get("seedModes", {}))
            seeds = {}
            for param_id, mode in modes.items():
                seeds[param_id] = self.random_seed() if mode == "random" else values[param_id]
                values[param_id] = seeds[param_id]
            # Validate a custom random provider as well; the execution copy never randomizes again.
            values, modes = self.sessions.normalize_changes(session, values, modes)
            canonical = {p["source"]["key"]: values[p["paramId"]] for p in manifest["parameters"] if p["source"]["kind"] == "canonical"}
            canonical["params"] = {p["source"]["key"]: values[p["paramId"]] for p in manifest["parameters"] if p["source"]["kind"] == "parameter"}
            requested = canonical.get("count", 1)
            require(manifest["limits"]["count"]["min"] <= requested <= manifest["limits"]["count"]["max"],
                    "PARAMETER_INVALID", "Requested output count exceeds function limits")
            changed = values != session["values"] or modes != session["seedModes"]
            revision = session["revision"] + int(changed)
            prompt = compile_prompt(manifest, template, values, seeds, payload["runId"])
            extra = {"extra_pnginfo": {"ps_bridge_v3": {"contractVersion": 3, "runId": payload["runId"], "scope": scope,
                                                      "revision": revision, "definitionSha256": manifest["definitionSha256"]}}}
            if ui is not None:
                extra["extra_pnginfo"]["workflow"] = ui
            params_json = {"formatVersion": 1, "canonical": canonical, "parameterValues": values, "runtime": media["runtime"]}
            identity = {"runId": payload["runId"], "scope": scope, "revision": revision, "definitionSha256": manifest["definitionSha256"],
                        "apiSha256": manifest["apiSha256"]}
            request_json = {**params_json, "identity": identity, "actualSeeds": seeds,
                            "resources": {k: v for k, v in media.items() if k != "runtime"}}
            snapshot = {**identity, "parameterRevision": revision, "controllerClientId": owner,
                        "submitOwner": payload["submitOwner"], "manifest": manifest, "media": media, "values": values,
                        "seedModes": modes, "actualSeeds": seeds, "canonical": canonical,
                        "prompt": prompt, "extra_data": extra, "preparedPromptSha256": digest({"prompt": prompt, "extra_data": extra}),
                        "requestedCount": requested, "actualCount": 0, "paramsJson": params_json, "requestJson": request_json}
            # Persist before acknowledging a parameter commit; an I/O failure leaves the session untouched.
            run = self.runs.create(snapshot, fingerprint)
            if changed:
                self.sessions.commit_values(session, values, modes)
            self.on_prepared(run)
            return run
