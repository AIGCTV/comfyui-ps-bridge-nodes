"""Strict UTF-8 JSON and RFC 8785, shared by storage and wire boundaries."""
from __future__ import annotations
import hashlib
import json
import math
import rfc8785
from .errors import BridgeError

MAX_SAFE_INTEGER = 9007199254740991

def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result

def validate_json(value):
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")
    elif value is None or isinstance(value, bool):
        pass
    elif isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise ValueError("Integer outside the JavaScript safe range")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Non-finite number")
        if value.is_integer() and abs(value) > MAX_SAFE_INTEGER:
            raise ValueError("Integer outside the JavaScript safe range")
    elif isinstance(value, list):
        for item in value:
            validate_json(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            validate_json(key)
            validate_json(item)
    else:
        raise ValueError("Not a JSON value")
    return value

def loads(raw):
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="strict")
        return validate_json(json.loads(raw, object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite number"))))
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise BridgeError("JSON_INVALID", "Expected strict UTF-8 JSON without duplicate keys or unsafe numbers") from exc

def canonical_bytes(value):
    try:
        validate_json(value)
        return rfc8785.dumps(value)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise BridgeError("JSON_INVALID", "Value cannot be canonicalized with RFC 8785") from exc

def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()

def byte_digest(value):
    return hashlib.sha256(value).hexdigest()

def execution_prompt(value):
    """Remove only ComfyUI's derived cache field from a live execution prompt.

    Core mutates each node with is_changed (which can contain NaN). It is not
    part of the submitted API or the frozen definition. Inputs remain strict.
    """
    import copy
    result = copy.deepcopy(value)
    if isinstance(result, dict):
        for node in result.values():
            if isinstance(node, dict):
                node.pop("is_changed", None)
    return result
