from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import re
from pathlib import Path


# Workflow ids are file stems, so allow common exported suffixes like " (1)".
_WORKFLOW_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. ()-]{0,127}$")
_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9_. -]+")
_TRUE_VALUES = {"1", "true", "yes", "on"}

ALLOW_LAN_ENV = "PS_BRIDGE_ALLOW_LAN"
AUTH_TOKEN_ENV = "PS_BRIDGE_AUTH_TOKEN"


def validate_workflow_id(value: str) -> str:
    value = (value or "").strip()
    if not _WORKFLOW_ID_RE.fullmatch(value):
        raise ValueError("Invalid workflow id")
    if ".." in value or "/" in value or "\\" in value:
        raise ValueError("Invalid workflow id")
    return value


def safe_component(value: str, fallback: str = "item") -> str:
    original = str(value or fallback)
    slug = _SAFE_COMPONENT_RE.sub("_", original).strip(" ._")
    if not slug:
        slug = fallback
    digest = hashlib.sha1(original.encode("utf-8")).hexdigest()[:8]
    return f"{slug[:64]}-{digest}"


def safe_png_filename(value: str, fallback: str = "item") -> str:
    return f"{safe_component(value, fallback)}.png"


def resolve_inside(base: Path, child_name: str, *, allow_subdirs: bool = False) -> Path:
    if not isinstance(child_name, str) or not child_name or "\\" in child_name or ":" in child_name:
        raise ValueError("Invalid filename")
    parts = child_name.split("/")
    if any(part in {"", ".", ".."} for part in parts) or (len(parts) > 1 and not allow_subdirs):
        raise ValueError("Invalid filename")
    target = (base / child_name).resolve()
    base_resolved = base.resolve()
    if base_resolved not in target.parents and target != base_resolved:
        raise ValueError("Path escapes base directory")
    return target


def is_local_or_private_ip(value: str | None) -> bool:
    if not value:
        return False
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def is_loopback_ip(value: str | None) -> bool:
    if not value:
        return False
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def lan_access_enabled() -> bool:
    return os.getenv(ALLOW_LAN_ENV, "0").strip().lower() in _TRUE_VALUES


def bridge_auth_token() -> str:
    return os.getenv(AUTH_TOKEN_ENV, "").strip()


def lan_access_ready() -> bool:
    return lan_access_enabled() and bool(bridge_auth_token())


def auth_token_matches(candidate: str | None) -> bool:
    expected = bridge_auth_token()
    if not expected or not candidate:
        return False
    return hmac.compare_digest(expected, candidate)


def bearer_token(value: str | None) -> str:
    if not value:
        return ""
    scheme, separator, token = value.strip().partition(" ")
    if not separator or scheme.lower() != "bearer":
        return ""
    return token.strip()
