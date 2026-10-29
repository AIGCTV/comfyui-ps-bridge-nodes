"""Keep error messages, redact credentials, and never serialize input dumps."""
from __future__ import annotations

import re
import logging

VALIDATION_MESSAGES = {
    "required_input_missing": "Required input is missing",
    "return_type_mismatch": "Return type mismatch",
    "value_not_in_list": "Value not in list",
    "value_smaller_than_min": "Value smaller than min",
    "value_bigger_than_max": "Value bigger than max",
    "invalid_input_type": "Invalid input type",
    "exception_during_validation": "Exception during validation",
    "exception_during_inner_validation": "Exception during inner validation",
}



def safe_name(value, limit):
    return value if isinstance(value, str) and 0 < len(value) <= limit and all(c.isalnum() or c in "_.:- " for c in value) else None


def message_text(value):
    if not isinstance(value, str):
        return ""
    text = re.split(r"\n\s*(?:Traceback\b|File\s+[\"']|at\s+\S+\s*\()", value, maxsplit=1)[0]
    text = re.sub(r"https?://[^\s<>\"']+", lambda m: re.sub(r"^(https?://)[^/]*@", r"\1[redacted]@", re.split(r"[?#]", m[0])[0]), text)
    text = re.sub(r"\bBearer\s+[^\s,;\"']+", "Bearer [redacted]", text, flags=re.I)
    text = re.sub(r"([\"']?\b(?:api[ _-]?key|runningHubWalletKey|userApiKey|clientToken|attachToken|refreshToken|accessToken|token|authorization)[\"']?\s*[:=]\s*)(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s,;|}]+)", r"\1[redacted]", text, flags=re.I)
    text = re.sub(r"data:image/[^\s\"']+", "[image data removed]", text)[:8192].strip()
    return "" if re.fullmatch(r"(?:token|api[_ -]?key|authorization)\s*[:=]\s*\[redacted\]", text, re.I) else text


def rejection_diagnostic(body, prompt):
    nodes = []
    source = body.get("node_errors", {}) if isinstance(body, dict) else {}
    if not isinstance(source, dict):
        return nodes
    for node_id, node in list(source.items())[:64]:
        target = prompt.get(node_id)
        if not safe_name(node_id, 256) or not isinstance(node, dict) or not isinstance(target, dict):
            continue
        errors = []
        raw_errors = node.get("errors", [])
        for error in (raw_errors if isinstance(raw_errors, list) else [])[:16]:
            if not isinstance(error, dict):
                continue
            code = safe_name(error.get("type"), 128) or "validation_failed"
            item = {"code": code, "message": message_text(error.get("message")) or VALIDATION_MESSAGES.get(code, "The service did not return an error message.")}
            extra = error.get("extra_info", {})
            field = safe_name(extra.get("input_name"), 128) if isinstance(extra, dict) else None
            if field:
                item["inputName"] = field
            errors.append(item)
        if errors:
            nodes.append({"nodeId": node_id, "nodeType": safe_name(target.get("class_type"), 256) or "UnknownNode", "errors": errors})
    return nodes


def rejection_message(nodes):
    return "\n".join(dict.fromkeys(error["message"] for node in nodes[:16] for error in node["errors"][:4]))[:4096] or "ComfyUI rejected the task without an error message."


def execution_failure(messages, prompt=None, result_error=None):
    for event in reversed(messages):
        if not isinstance(event, (tuple, list)) or len(event) != 2 or event[0] != "execution_error" or not isinstance(event[1], dict):
            continue
        detail = event[1]
        raw_id = detail.get("node_id")
        node_id = safe_name(str(raw_id), 256) if isinstance(raw_id, (str, int)) and not isinstance(raw_id, bool) else None
        target = (prompt or {}).get(node_id)
        node_type = safe_name(target.get("class_type"), 256) if isinstance(target, dict) else None
        message = message_text(detail.get("exception_message")) or "The service did not return an error message."
        if node_type == "VP_SendToPS" and result_error and result_error.get("nodeId") == node_id:
            message = result_error["message"]  # Locally generated, persisted save-stage diagnostic.
        error = {"code": "EXECUTION_FAILED", "message": message, "retryable": False}
        if node_id and isinstance(target, dict):
            error["nodeId"] = node_id
        return error
    return None


def operation_error(operation, exc, *, code="INTERNAL_ERROR", **identity):
    # OSError.strerror keeps the cause without exposing a user filename.
    cause = message_text(exc.strerror if isinstance(exc, OSError) else str(exc))
    error = {"code": code, "message": f"{operation}: {cause}" if cause else f"{operation} failed.", "retryable": False}
    if safe_name(identity.get("nodeId"), 256):
        error["nodeId"] = identity["nodeId"]
    return error


def record_result_error(runs, run_id, error):
    try:
        runs.update(run_id, resultError=error)
    except Exception as exc:
        # Saving the diagnostic can fail on the same filesystem. Preserve the
        # original save exception and emit only already-safe diagnostic facts.
        logging.getLogger(__name__).error("%s; diagnostic persistence failed: %s", error["message"], type(exc).__name__)
