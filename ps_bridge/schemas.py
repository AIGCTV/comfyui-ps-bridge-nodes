"""Shared offline JSON Schema validation; never resolves remote references."""
from __future__ import annotations
from functools import lru_cache
from pathlib import Path
from jsonschema import Draft202012Validator
from .errors import BridgeError
from .json_codec import loads, canonical_bytes

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "docs" / "contracts" / "bridge-v3" / "bridge.schema.json"

@lru_cache(maxsize=1)
def document():
    schema = loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema

@lru_cache(maxsize=None)
def validator(name):
    schema = document()
    if name not in schema["$defs"]:
        raise ValueError(f"Unknown bridge schema: {name}")
    return Draft202012Validator({"$schema": schema["$schema"], "$defs": schema["$defs"],
                                 "$ref": f"#/$defs/{name}"})

def validate_shape(name, value, *, code="MESSAGE_INVALID"):
    # JSON Schema alone cannot reject duplicate source keys or unsafe JSON numbers.
    # Wire bytes must first pass json_codec.loads; objects must pass JCS validation.
    canonical_bytes(value)
    error = next(validator(name).iter_errors(value), None)
    if error is not None:
        # Do not include jsonschema's message: it interpolates the user's value,
        # which may be a credential or an erroneously supplied media payload.
        path = ".".join(str(p) for p in error.absolute_path)
        raise BridgeError(code, f"Invalid {name} structure", field=path or name)
    return value
