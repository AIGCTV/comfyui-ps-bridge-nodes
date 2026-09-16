"""Stable, media-free wire errors for bridge contract 3."""
from __future__ import annotations

CONFLICTS = {"CONTRACT_UNSUPPORTED", "LEGACY_WORKFLOW_UNSUPPORTED", "WORKFLOW_VERSION_MISMATCH",
             "CONTROLLER_IN_USE", "REVISION_CONFLICT", "SESSION_EXPIRED", "DRAFT_CHANGED",
             "ID_REUSE", "SUBMISSION_UNKNOWN", "CANCEL_NOT_OWNER", "TEST_TARGET_UNAVAILABLE"}

class BridgeError(ValueError):
    def __init__(self, code, message, *, field=None, retryable=False, status=None, **details):
        self.code = code
        self.status = status or (409 if code in CONFLICTS else 422)
        self.error = {"code": code, "message": message, "retryable": retryable, **details}
        if field is not None:
            self.error["field"] = field
        super().__init__(f"{code}: {message}")

def require(condition, code, message, **details):
    if not condition:
        raise BridgeError(code, message, **details)
