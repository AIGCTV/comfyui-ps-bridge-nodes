"""Opt-in output timing, without image data, credentials or journal writes."""
import json
import logging
import os
import time

_logger = logging.getLogger(__name__)
_enabled = os.environ.get("PS_BRIDGE_TRACE") == "1"
_fields = frozenset({"runId", "promptId", "nodeId", "sequence", "resultId", "batchIndex", "count"})


def trace(stage, **identity):
    if not _enabled:
        return
    # Diagnostics must never alter execution or receipt handling, including when
    # a user-configured logging handler fails. No arbitrary payload is retained.
    try:
        record = {"stage": stage, "atMs": time.time_ns() / 1_000_000}
        record.update({key: value for key, value in identity.items()
                       if key in _fields and (type(value) in (int, float)
                       or isinstance(value, str) and len(value) <= 256)})
        _logger.info("PS_BRIDGE_TRACE %s", json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        pass
