"""Durable local workflow definitions, addressed by their complete scope."""
from __future__ import annotations
import copy
import os
import threading
import uuid
from pathlib import Path
from .errors import require
from .json_codec import loads, canonical_bytes, digest
from . import manifest as contracts

LOCK = threading.RLock()

def read_json(path, default=None):
    return loads(Path(path).read_bytes()) if Path(path).exists() else copy.deepcopy(default)

def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temp.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)

def atomic_json(path, data):
    atomic_bytes(path, canonical_bytes(data))

class DefinitionStore:
    def __init__(self, root, *, installed=None):
        self.root = Path(root)
        self.installed = installed
        self.lock = LOCK

    def register(self, manifest, raw_api, *, ui=None):
        contracts.validate(manifest, raw_api, installed=self.installed, ui=ui, profile="runtime")
        key = [manifest[k] for k in ("providerId", "featureId", "workflowId", "workflowVersion")]
        definition_key = digest(key + [manifest["definitionSha256"]])
        record = {"manifest": copy.deepcopy(manifest), "api": raw_api.decode("utf-8") if isinstance(raw_api, bytes) else raw_api}
        if ui is not None:
            record["ui"] = contracts.ui_bytes(ui).decode("utf-8")
        with self.lock:
            index_path = self.root / "index.json"
            index = read_json(index_path, {"definitions": {}, "current": {}})
            definitions = index.setdefault("definitions", {})
            current = index.setdefault("current", {})
            feature_key = digest(key[:2])
            content = digest(record)
            path = self.root / f"{content}.json"
            if not path.exists():
                atomic_json(path, record)
            # UI, display names and JSON formatting may change without changing
            # execution identity. Existing sessions and runs own frozen copies.
            if definitions.get(definition_key) != content or current.get(feature_key) != definition_key:
                definitions[definition_key] = content
                current[feature_key] = definition_key
                # Preserve old indexes so pre-upgrade scopes remain addressable.
                atomic_json(index_path, index)
        return {"definitionSha256": manifest["definitionSha256"], "workflowVersion": manifest["workflowVersion"]}

    def get(self, scope):
        key = [scope[k] for k in ("providerId", "featureId", "workflowId", "workflowVersion")]
        with self.lock:
            index = read_json(self.root / "index.json", {})
            content = index.get("definitions", {}).get(digest(key + [scope["definitionSha256"]]))
            if content is None:
                content = index.get("versions", {}).get(digest(key))
            require(content is not None, "WORKFLOW_NOT_FOUND", "Register the workflow definition first", status=404)
            record = read_json(self.root / f"{content}.json")
            require(all(record["manifest"][field] == scope[field] for field in
                        ("providerId", "featureId", "workflowId", "workflowVersion", "definitionSha256")),
                    "WORKFLOW_VERSION_MISMATCH", "Scope does not match registered definition")
            return {"manifest": record["manifest"], "prompt": contracts.api_prompt(record["api"])}

    def list_current(self):
        with self.lock:
            index = read_json(self.root / "index.json", {})
            definitions, versions = index.get("definitions", {}), index.get("versions", {})
            return [read_json(self.root / f"{definitions.get(key) or versions.get(key)}.json")["manifest"]
                    for key in index.get("current", {}).values()]
