from __future__ import annotations

import base64
import binascii
import json
import math
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import torch
from PIL import Image, ImageOps

from .paths import LATEST_STATE_PATH, PS_IMAGES_DIR, WORKFLOWS_DIR, ensure_data_dirs
from .protocol import BRIDGE_MAX_IMAGES
from .security import safe_png_filename, validate_workflow_id


DEFAULT_SLOT_ID = "MAIN"
IMAGE_SLOT_LABELS = [f"Image {index}" for index in range(1, BRIDGE_MAX_IMAGES + 1)]
IMAGE_SLOT_IDS = tuple(f"IMAGE_{index}" for index in range(1, len(IMAGE_SLOT_LABELS) + 1))
SELECTION_FILENAME = "selection.png"
PLACEHOLDER_SIZE = (64, 64)
ENCODED_IMAGE_KEYS = ("png", "image_png", "jpg", "jpeg", "image_jpg", "image_jpeg", "webp", "image_webp")
RAW_IMAGE_KEYS = ("rgba", "rgb", "imageData", "data", "image")
SLOT_GROUPS = ("prompt", "seed", "float", "int", "boolean")
REQUEST_PARAM_OBJECT_KEYS = ("options", "settings", "params")
REQUEST_TOP_LEVEL_PARAM_KEYS = (
    "resolution",
    "batchCount",
    "batch_count",
    "strength",
    "width",
    "height",
    "steps",
    "cfg",
    "denoise",
    "seed",
)
ADV_REQUEST_MAX_IMAGES = BRIDGE_MAX_IMAGES
ADV_REQUEST_MAX_BATCH_COUNT = 4
SEED_MAX = 0xFFFFFFFFFFFFFFFF
EXECUTION_MODE_AUTO = "auto"
EXECUTION_MODE_CURRENT_GRAPH = "current_graph"
EXECUTION_MODE_API_WORKFLOW = "api_workflow"
EXECUTION_MODES = {EXECUTION_MODE_AUTO, EXECUTION_MODE_CURRENT_GRAPH, EXECUTION_MODE_API_WORKFLOW}


@dataclass(frozen=True)
class ImageLoadResult:
    image: torch.Tensor
    selection: torch.Tensor
    alpha: torch.Tensor
    width: int
    height: int


def _json_default(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes__": len(value)}
    return str(value)


def _coerce_bytes(value: Any) -> bytes | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, list):
        return bytes(value)
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("data:") and "," in text:
            text = text.split(",", 1)[1]
        try:
            return base64.b64decode(text)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Invalid base64 image data") from exc
    if hasattr(value, "data"):
        return _coerce_bytes(value.data)
    return None


def _payload_value(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def _prompt_from_payload(payload: dict[str, Any]) -> str:
    prompt = payload.get("prompt")
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, dict):
        for key in ("prompt", "positive", "main", "text"):
            value = prompt.get(key)
            if value is not None:
                return str(value)
        if len(prompt) == 1:
            return str(next(iter(prompt.values())))
    elif prompt is not None:
        return str(prompt)

    slots = payload.get("slots")
    prompt_slots = slots.get("prompt") if isinstance(slots, dict) else None
    if isinstance(prompt_slots, dict):
        for key in ("prompt", "positive", "main", "text"):
            value = prompt_slots.get(key)
            if value is not None:
                return str(value)
        if len(prompt_slots) == 1:
            return str(next(iter(prompt_slots.values())))
    return ""


def _non_empty_slot_params(slots: Any) -> dict[str, Any]:
    if not isinstance(slots, dict):
        return {}
    params: dict[str, Any] = {}
    for group in ("seed", "float", "int", "boolean"):
        values = slots.get(group)
        if isinstance(values, dict) and values:
            params[group] = values
    return params


def _params_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for key in REQUEST_PARAM_OBJECT_KEYS:
        value = payload.get(key)
        if isinstance(value, dict):
            params.update({str(item_key): item_value for item_key, item_value in value.items()})
    for key in REQUEST_TOP_LEVEL_PARAM_KEYS:
        if key in payload and payload[key] is not None:
            params[key] = payload[key]
    for group, values in _non_empty_slot_params(payload.get("slots")).items():
        existing = params.get(group)
        params[group] = {
            **(existing if isinstance(existing, dict) else {}),
            **values,
        }
    return params


def _single_value(value: Any) -> Any:
    if isinstance(value, dict) and len(value) == 1:
        return next(iter(value.values()))
    return value


def _param_value(params: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in params:
            return _single_value(params[key])
    for group in ("seed", "float", "int", "boolean"):
        values = params.get(group)
        if not isinstance(values, dict):
            continue
        for key in keys:
            if key in values:
                return values[key]
    return None


def _int_value(value: Any, fallback: int) -> int:
    if isinstance(value, int):
        return value
    try:
        text = str(value).strip()
        try:
            return int(text, 10)
        except ValueError:
            number = float(text)
            return math.floor(number + 0.5) if math.isfinite(number) else fallback
    except (TypeError, ValueError, OverflowError):
        return fallback


def _clamp_adv_batch_count(value: Any) -> int:
    return max(1, min(_int_value(value, 1), ADV_REQUEST_MAX_BATCH_COUNT))


def adv_request_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    request = payload.get("adv_request") if isinstance(payload.get("adv_request"), dict) else {}
    params = _params_from_payload(payload)
    images = payload.get("images")
    image_count_fallback = len(images) if isinstance(images, dict) and images else 1
    image_count = max(
        1,
        min(
            _int_value(request.get("image_count", payload.get("image_count", _param_value(params, "image_count"))), image_count_fallback),
            ADV_REQUEST_MAX_IMAGES,
        ),
    )
    batch_count = _clamp_adv_batch_count(
        request.get("batch_count", request.get("batchCount", payload.get("batch_count", payload.get("batchCount", _param_value(params, "batch_count", "batchCount"))))),
    )
    seed = _int_value(request.get("seed", payload.get("seed", _param_value(params, "seed", "MAIN"))), 42)
    strength = max(
        0.0,
        min(1.0, _finite_float(request.get("strength", payload.get("strength", _param_value(params, "strength"))), 0.65)),
    )
    params_json = request.get("params_json", request.get("paramsJson"))
    if params_json is None:
        params_json = json.dumps(params, ensure_ascii=False, separators=(",", ":"), default=_json_default)
    elif not isinstance(params_json, str):
        params_json = json.dumps(params_json, ensure_ascii=False, separators=(",", ":"), default=_json_default)
    return {
        "image_count": image_count,
        "prompt": str(request.get("prompt", _prompt_from_payload(payload)) or ""),
        "resolution": str(request.get("resolution", payload.get("resolution", _param_value(params, "resolution") or "1k")) or "1k"),
        "strength": strength,
        "batch_count": batch_count,
        "seed": max(0, min(seed, SEED_MAX)),
        "params_json": str(params_json or "{}"),
    }


def _looks_like_image_data_url(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith("data:image/")


def _looks_like_encoded_image(data: bytes) -> bool:
    return (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith(b"\xff\xd8\xff")
        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
    )


def _image_from_encoded_bytes(value: Any) -> Image.Image:
    data = _coerce_bytes(value)
    if data is None:
        raise ValueError("Missing encoded image data")
    return ImageOps.exif_transpose(Image.open(BytesIO(data))).convert("RGBA")


def image_from_raw_rgba(width: int, height: int, raw: Any) -> Image.Image:
    data = _coerce_bytes(raw)
    if data is None:
        raise ValueError("Missing RGBA image data")
    expected = width * height * 4
    if len(data) != expected:
        raise ValueError(f"Invalid RGBA byte length: expected {expected}, got {len(data)}")
    return Image.frombytes("RGBA", (width, height), data)


def image_from_payload(payload: dict[str, Any]) -> Image.Image:
    width = max(1, int(payload.get("width", 0)))
    height = max(1, int(payload.get("height", 0)))

    encoded_image = _payload_value(payload, *ENCODED_IMAGE_KEYS)
    if encoded_image is not None:
        return _image_from_encoded_bytes(encoded_image)

    image_format = str(payload.get("format") or payload.get("mime") or payload.get("mime_type") or "").lower()
    generic_image = _payload_value(payload, "image")
    if generic_image is not None:
        image_data = _coerce_bytes(generic_image)
        if image_data is not None and (
            _looks_like_image_data_url(generic_image)
            or image_format in {"png", "jpg", "jpeg", "webp", "image/png", "image/jpeg", "image/jpg", "image/webp"}
            or _looks_like_encoded_image(image_data)
        ):
            return _image_from_encoded_bytes(image_data)

    if payload.get("rgba") is not None:
        return image_from_raw_rgba(width, height, payload.get("rgba"))

    rgb_data = _coerce_bytes(_payload_value(payload, "rgb", "imageData", "data", "image"))
    if rgb_data is None:
        raise ValueError("Missing image data")

    if len(rgb_data) == width * height * 4:
        return Image.frombytes("RGBA", (width, height), rgb_data)
    if len(rgb_data) == width * height * 3:
        return Image.frombytes("RGB", (width, height), rgb_data).convert("RGBA")
    raise ValueError("Invalid image byte length")


def _bounds_size(bounds: dict[str, Any]) -> tuple[int, int, int, int] | None:
    try:
        left = int(round(float(bounds.get("left", 0))))
        top = int(round(float(bounds.get("top", 0))))
        right = int(round(float(bounds.get("right", 0))))
        bottom = int(round(float(bounds.get("bottom", 0))))
    except (TypeError, ValueError):
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def mask_image_from_payload(payload: dict[str, Any] | None, width: int, height: int, *, full_default: bool) -> Image.Image:
    fill = 255 if full_default else 0
    if not payload:
        return Image.new("L", (width, height), fill)

    encoded_mask = _payload_value(payload, "png", "mask_png", "image_png")
    if encoded_mask is not None:
        img = ImageOps.exif_transpose(Image.open(BytesIO(_coerce_bytes(encoded_mask)))).convert("L")
        return img.resize((width, height), Image.Resampling.LANCZOS) if img.size != (width, height) else img

    mask_value = _payload_value(payload, "mask", "maskData", "data")
    mask_data = _coerce_bytes(mask_value)
    if mask_data is None:
        return Image.new("L", (width, height), fill)

    if _looks_like_image_data_url(mask_value) or _looks_like_encoded_image(mask_data):
        img = ImageOps.exif_transpose(Image.open(BytesIO(mask_data))).convert("L")
        return img.resize((width, height), Image.Resampling.LANCZOS) if img.size != (width, height) else img

    if len(mask_data) == width * height:
        return Image.frombytes("L", (width, height), mask_data)

    if len(mask_data) == width * height * 4:
        array = np.frombuffer(mask_data, dtype=np.uint8).reshape((height, width, 4))
        return Image.fromarray(array[:, :, 0], "L")

    if len(mask_data) == width * height * 3:
        array = np.frombuffer(mask_data, dtype=np.uint8).reshape((height, width, 3))
        return Image.fromarray(array[:, :, 0], "L")

    bounds = _bounds_size(payload.get("source_bounds") or payload.get("sourceBounds") or {})
    if bounds:
        left, top, right, bottom = bounds
        source_width = right - left
        source_height = bottom - top
        if len(mask_data) == source_width * source_height:
            canvas = Image.new("L", (width, height), 0)
            mask = Image.frombytes("L", (source_width, source_height), mask_data)
            canvas.paste(mask, (left, top))
            return canvas

    return Image.new("L", (width, height), fill)


def pil_to_tensors(image: Image.Image, selection: Image.Image | None = None) -> ImageLoadResult:
    image = ImageOps.exif_transpose(image).convert("RGBA")
    width, height = image.size
    rgba = np.asarray(image).astype(np.float32) / 255.0
    rgb_tensor = torch.from_numpy(rgba[:, :, :3]).unsqueeze(0)
    alpha_tensor = torch.from_numpy(rgba[:, :, 3]).unsqueeze(0)

    if selection is None:
        selection = Image.new("L", (width, height), 255)
    else:
        selection = selection.convert("L")
        if selection.size != (width, height):
            selection = selection.resize((width, height), Image.Resampling.NEAREST)
    selection_tensor = torch.from_numpy(np.asarray(selection).astype(np.float32) / 255.0).unsqueeze(0)
    return ImageLoadResult(rgb_tensor, selection_tensor, alpha_tensor, width, height)


def _state_file_payload(state: dict[str, Any]) -> str:
    return json.dumps(state, ensure_ascii=False, indent=2, default=_json_default)


def load_state() -> dict[str, Any]:
    ensure_data_dirs()
    if not LATEST_STATE_PATH.exists():
        return {}
    try:
        with LATEST_STATE_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict[str, Any]) -> None:
    ensure_data_dirs()
    tmp_path = LATEST_STATE_PATH.with_suffix(".tmp")
    tmp_path.write_text(_state_file_payload(state), encoding="utf-8")
    tmp_path.replace(LATEST_STATE_PATH)


def latest_fingerprint() -> str:
    if not LATEST_STATE_PATH.exists():
        return "missing"
    stat = LATEST_STATE_PATH.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _slot_groups() -> dict[str, dict[str, Any]]:
    return {group: {} for group in SLOT_GROUPS}


def _finite_float(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = fallback
    if not math.isfinite(number):
        return fallback
    return number


def image_slot_id(value: Any) -> str:
    text = str(value or "").strip()
    normalized = "_".join(text.upper().split())
    normalized_key = "".join(char for char in text.upper() if char.isalnum())
    if normalized_key in {"", DEFAULT_SLOT_ID, "MAINDOC", "MAINDOCUMENT"}:
        return "IMAGE_1"
    for prefix in ("REF", "REFERENCE"):
        if normalized_key.startswith(prefix):
            suffix = normalized_key[len(prefix):]
            if suffix.isdigit():
                index = int(suffix) + 1
                if 1 <= index <= len(IMAGE_SLOT_LABELS):
                    return f"IMAGE_{index}"
    for index in range(1, len(IMAGE_SLOT_LABELS) + 1):
        if normalized in {str(index), f"IMAGE{index}", f"IMAGE_{index}"} or normalized_key in {str(index), f"IMAGE{index}"}:
            return f"IMAGE_{index}"
    for index, label in enumerate(IMAGE_SLOT_LABELS, start=1):
        if text == label:
            return f"IMAGE_{index}"
    for char in text:
        if char in "123456":
            return f"IMAGE_{char}"
    return "IMAGE_1"


def is_api_prompt_workflow(workflow: dict[str, Any]) -> bool:
    if not isinstance(workflow, dict):
        return False
    if isinstance(workflow.get("nodes"), list):
        return False
    return any(
        isinstance(node, dict) and "class_type" in node and "inputs" in node
        for node in workflow.values()
    )


def workflow_for_feature(feature_id: str) -> dict[str, Any]:
    fid = validate_workflow_id(feature_id)
    path = WORKFLOWS_DIR / f"{fid}.json"
    if not path.exists():
        raise FileNotFoundError(f"Workflow not found: {fid}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_execution_mode(value: Any, fallback: str = EXECUTION_MODE_AUTO, *, strict: bool = True) -> str:
    if value is None or value == "":
        return fallback
    text = str(value).strip().lower().replace("-", "_")
    aliases = {
        "current": EXECUTION_MODE_CURRENT_GRAPH,
        "currentgraph": EXECUTION_MODE_CURRENT_GRAPH,
        "current_graph": EXECUTION_MODE_CURRENT_GRAPH,
        "graph": EXECUTION_MODE_CURRENT_GRAPH,
        "api": EXECUTION_MODE_API_WORKFLOW,
        "api_prompt": EXECUTION_MODE_API_WORKFLOW,
        "api_prompt_workflow": EXECUTION_MODE_API_WORKFLOW,
        "api_workflow": EXECUTION_MODE_API_WORKFLOW,
        "backend": EXECUTION_MODE_API_WORKFLOW,
        "auto": EXECUTION_MODE_AUTO,
    }
    normalized = aliases.get(text)
    if normalized in EXECUTION_MODES:
        return normalized
    if not strict:
        return fallback
    raise ValueError(f"Invalid execution_mode: {value}")


def _workflow_manifest() -> dict[str, Any]:
    manifest_path = WORKFLOWS_DIR / "manifest.json"
    if not manifest_path.exists():
        return {}
    with manifest_path.open("r", encoding="utf-8") as f:
        manifest = json.load(f)
    return manifest if isinstance(manifest, dict) else {}


def manifest_execution_mode_for_feature(feature_id: str) -> str | None:
    fid = validate_workflow_id(feature_id)
    manifest = _workflow_manifest()
    candidates: list[Any] = []
    for container_key in ("workflows", "features"):
        container = manifest.get(container_key)
        if isinstance(container, dict):
            candidates.append(container.get(fid))
    candidates.append(manifest.get(fid))

    for candidate in candidates:
        if isinstance(candidate, dict):
            value = candidate.get("execution_mode", candidate.get("mode"))
        else:
            value = candidate
        if value is not None:
            return normalize_execution_mode(value)
    return None


def execution_mode_for_payload(payload: dict[str, Any], feature_id: str) -> str:
    explicit = payload.get("execution_mode")
    if explicit is not None:
        return normalize_execution_mode(explicit)
    if payload.get("mode") is not None:
        mode_alias = normalize_execution_mode(payload.get("mode"), "", strict=False)
        if mode_alias:
            return mode_alias
    manifest_mode = manifest_execution_mode_for_feature(feature_id)
    return manifest_mode or EXECUTION_MODE_AUTO


def _slot_key(value: Any) -> str:
    return "".join(char for char in str(value).lower() if char.isalnum())


def normalize_slots_for_payload(payload: dict[str, Any], workflow_slots: dict[str, list[str]]) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return _slot_groups()
    existing_slots = payload.get("slots")
    result = _slot_groups()
    sources = [payload]
    for key in ("settings", "params"):
        if isinstance(payload.get(key), dict):
            sources.append(payload[key])

    def slot_id_for(group: str, key: Any) -> str:
        text = str(key)
        ids = workflow_slots.get(group, [])
        if text in ids:
            return text
        matches = [slot_id for slot_id in ids if _slot_key(slot_id) == _slot_key(text)]
        return matches[0] if len(matches) == 1 else text

    def put(group: str, key: Any, value: Any) -> None:
        if value is not None:
            result[group].setdefault(slot_id_for(group, key), value)

    # 1. Explicit slots, then typed groups like {"int": {"width": 1024}}.
    group_sources = ([existing_slots] if isinstance(existing_slots, dict) else []) + sources
    for group in SLOT_GROUPS:
        for source in group_sources:
            value = source.get(group)
            if isinstance(value, dict):
                for key, item in value.items():
                    put(group, key, item)
            elif value is not None and len(workflow_slots.get(group, [])) == 1:
                put(group, workflow_slots[group][0], value)

    # 2. Direct values whose keys match an explicitly supplied protocol slot ID.
    for source in sources:
        for group in SLOT_GROUPS:
            for slot_id in workflow_slots.get(group, []):
                if slot_id in result[group]:
                    continue
                keys = [
                    key
                    for key in source
                    if isinstance(key, str) and key not in SLOT_GROUPS and _slot_key(key) == _slot_key(slot_id)
                ]
                if len(keys) == 1 and source[keys[0]] is not None:
                    result[group][slot_id] = source[keys[0]]
    return result


def merge_slots(base: Any, updates: Any) -> dict[str, dict[str, Any]]:
    result = normalize_slots(base)
    incoming = normalize_slots(updates)
    for group in SLOT_GROUPS:
        result[group].update(incoming[group])
    return result


def _metadata_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    source_bounds = _payload_value(payload, "source_bounds", "sourceBounds", "bounds")
    placement_bounds = _payload_value(payload, "placement_bounds", "placementBounds", "bounds")
    document = _payload_value(payload, "document", "document_info", "documentInfo")
    target_document_id = _payload_value(
        payload,
        "targetDocumentId",
        "targetDocumentID",
        "target_document_id",
        "documentID",
        "documentId",
    )
    if source_bounds is not None:
        metadata["source_bounds"] = source_bounds
    if placement_bounds is not None:
        metadata["placement_bounds"] = placement_bounds
    if document is not None:
        metadata["document"] = document
    if target_document_id is not None:
        metadata["targetDocumentId"] = target_document_id
    return metadata


def _selection_filename(slot_id: str) -> str:
    return SELECTION_FILENAME if slot_id == "IMAGE_1" else f"selection_{slot_id}.png"


def _image_filename(slot_id: str) -> str:
    canonical_slot_id = image_slot_id(slot_id)
    if canonical_slot_id in IMAGE_SLOT_IDS:
        return f"{canonical_slot_id}.png"
    return safe_png_filename(canonical_slot_id, DEFAULT_SLOT_ID)


def _placeholder_image(size: tuple[int, int] = PLACEHOLDER_SIZE) -> Image.Image:
    width = max(1, int(size[0]))
    height = max(1, int(size[1]))
    return Image.new("RGBA", (width, height), (0, 0, 0, 255))


def _placeholder_selection(size: tuple[int, int] = PLACEHOLDER_SIZE) -> Image.Image:
    width = max(1, int(size[0]))
    height = max(1, int(size[1]))
    return Image.new("L", (width, height), 255)


def _save_png_atomic(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.stem}.{time.time_ns()}.tmp{path.suffix}")
    image.save(tmp_path)
    tmp_path.replace(path)


def _ensure_fixed_slot_placeholders() -> None:
    for slot_id in IMAGE_SLOT_IDS:
        path = PS_IMAGES_DIR / _image_filename(slot_id)
        if not path.exists():
            _save_png_atomic(_placeholder_image(), path)
    selection_path = PS_IMAGES_DIR / SELECTION_FILENAME
    if not selection_path.exists():
        _save_png_atomic(_placeholder_selection(), selection_path)


def _reset_missing_fixed_slots(active_slot_ids: set[str], size: tuple[int, int] | None) -> None:
    placeholder_size = size or PLACEHOLDER_SIZE
    for slot_id in IMAGE_SLOT_IDS:
        if slot_id not in active_slot_ids:
            _save_png_atomic(_placeholder_image(placeholder_size), PS_IMAGES_DIR / _image_filename(slot_id))


def _save_selection_payload(
    slot_id: str,
    selection_payload: dict[str, Any] | None,
    image_sizes: dict[str, tuple[int, int]],
    fallback_size: tuple[int, int],
) -> dict[str, Any]:
    width, height = image_sizes.get(slot_id, fallback_size)
    if isinstance(selection_payload, dict):
        width = int(selection_payload.get("width") or width)
        height = int(selection_payload.get("height") or height)
    selection = mask_image_from_payload(selection_payload if isinstance(selection_payload, dict) else None, width, height, full_default=True)
    selection_filename = _selection_filename(slot_id)
    _save_png_atomic(selection, PS_IMAGES_DIR / selection_filename)
    selection_meta = {
        "filename": selection_filename,
        "width": width,
        "height": height,
        "slot_id": slot_id,
    }
    if isinstance(selection_payload, dict):
        selection_meta.update(_metadata_from_payload(selection_payload))
    return selection_meta


def _image_meta_for_slot(images: Any, slot_id: str) -> dict[str, Any] | None:
    if not isinstance(images, dict):
        return None
    canonical_slot_id = image_slot_id(slot_id)
    meta = images.get(canonical_slot_id)
    if not isinstance(meta, dict) and canonical_slot_id == "IMAGE_1":
        meta = images.get(DEFAULT_SLOT_ID)
    return meta if isinstance(meta, dict) else None


def _selection_meta_for_slot(state: dict[str, Any], slot_id: str) -> dict[str, Any] | None:
    canonical_slot_id = image_slot_id(slot_id)
    selections = state.get("selections")
    if isinstance(selections, dict):
        meta = selections.get(canonical_slot_id)
        if isinstance(meta, dict):
            return meta
    if canonical_slot_id == "IMAGE_1":
        meta = state.get("selection")
        if isinstance(meta, dict):
            return meta
    return None


def ingest_run_payload(
    message: dict[str, Any],
    *,
    require_workflow: bool = True,
    workflow_slots: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    ensure_data_dirs()
    _ensure_fixed_slot_placeholders()
    feature_id = validate_workflow_id(message.get("feature_id") or message.get("featureId") or "roundtrip")
    request_id = str(message.get("request_id") or f"ps-{int(time.time() * 1000)}")
    images_payload = message.get("images") or {}
    if not isinstance(images_payload, dict):
        raise ValueError("images must be an object keyed by slot id")

    images_meta: dict[str, Any] = {}
    image_sizes: dict[str, tuple[int, int]] = {}
    first_size: tuple[int, int] | None = None
    for slot_id, image_payload in images_payload.items():
        if not isinstance(image_payload, dict):
            continue
        canonical_slot_id = image_slot_id(slot_id)
        image = image_from_payload(image_payload)
        width, height = image.size
        first_size = first_size or (width, height)
        image_sizes[canonical_slot_id] = (width, height)
        filename = _image_filename(canonical_slot_id)
        path = PS_IMAGES_DIR / filename
        _save_png_atomic(image, path)
        images_meta[canonical_slot_id] = {
            "filename": filename,
            "width": width,
            "height": height,
            "mode": "RGBA",
        }
        images_meta[canonical_slot_id].update(_metadata_from_payload(image_payload))

    image_count = len(images_meta)
    _reset_missing_fixed_slots(set(images_meta), first_size)
    selection_meta = None
    selections_meta: dict[str, Any] = {}
    if first_size:
        selections_payload = message.get("selections")
        if isinstance(selections_payload, dict):
            for slot_id, selection_payload in selections_payload.items():
                if not isinstance(selection_payload, dict):
                    continue
                canonical_slot_id = image_slot_id(slot_id)
                selections_meta[canonical_slot_id] = _save_selection_payload(canonical_slot_id, selection_payload, image_sizes, first_size)
        if "IMAGE_1" not in selections_meta:
            sel_payload = message.get("selection")
            selections_meta["IMAGE_1"] = _save_selection_payload(
                "IMAGE_1",
                sel_payload if isinstance(sel_payload, dict) else None,
                image_sizes,
                first_size,
            )
        selection_meta = selections_meta.get("IMAGE_1")

    if require_workflow:
        workflow_for_feature(feature_id)
    workflow_slots = workflow_slots or {group: [] for group in SLOT_GROUPS}

    state = {
        "request_id": request_id,
        "feature_id": feature_id,
        "execution_mode": execution_mode_for_payload(message, feature_id),
        "images": images_meta,
        "image_count": image_count,
        "multi_image": image_count > 1,
        "canvas": {
            "width": first_size[0],
            "height": first_size[1],
        } if first_size else None,
        "selection": selection_meta,
        "selections": selections_meta,
        "slots": normalize_slots_for_payload(message, workflow_slots),
        "adv_request": adv_request_from_payload(message),
        "updated_at": time.time(),
    }
    save_state(state)
    return state


def normalize_slots(value: Any) -> dict[str, dict[str, Any]]:
    groups = _slot_groups()
    if not isinstance(value, dict):
        return groups
    for group in SLOT_GROUPS:
        incoming = value.get(group) or {}
        if isinstance(incoming, dict):
            groups[group] = {str(k): v for k, v in incoming.items()}
    return groups


def load_bridge_image(slot_id: str, fallback_width: int, fallback_height: int) -> ImageLoadResult:
    ensure_data_dirs()
    _ensure_fixed_slot_placeholders()
    state = load_state()
    images = state.get("images") or {}
    canonical_slot_id = image_slot_id(slot_id)
    meta = _image_meta_for_slot(images, canonical_slot_id)
    width = max(1, int(fallback_width or 512))
    height = max(1, int(fallback_height or 512))

    if isinstance(meta, dict):
        filename = meta.get("filename")
        if filename:
            path = PS_IMAGES_DIR / filename
            if path.exists():
                image = Image.open(path)
                width, height = image.size
                selection = None
                selection_meta = _selection_meta_for_slot(state, canonical_slot_id)
                if isinstance(selection_meta, dict) and selection_meta.get("filename"):
                    selection_path = PS_IMAGES_DIR / selection_meta["filename"]
                    if selection_path.exists():
                        selection = Image.open(selection_path)
                return pil_to_tensors(image, selection)

    placeholder = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    selection = Image.new("L", (width, height), 255)
    return pil_to_tensors(placeholder, selection)
