from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image, ImageOps
import folder_paths
from comfy_api.latest import io, ui

from . import storage
from .manager import manager
from .protocol import BRIDGE_MAX_IMAGES
from .security import safe_component


CATEGORY = "PS Bridge"
SEED_MAX = 0xFFFFFFFFFFFFFFFF
ADV_REQUEST_MAX_IMAGES = BRIDGE_MAX_IMAGES
ADV_REQUEST_MAX_BATCH_COUNT = 4
INLINE_RENDER_IMAGE_ENV = "PS_BRIDGE_RENDER_INLINE_IMAGE"


def _inline_render_image_enabled() -> bool:
    return os.getenv(INLINE_RENDER_IMAGE_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


def _first_present(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if source.get(key) is not None:
            return source[key]
    return None


def _coerce_int(value, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return fallback


def _coerce_float(value, fallback: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else fallback
    except (TypeError, ValueError):
        return fallback


def _resize_alpha(alpha: torch.Tensor, image: torch.Tensor) -> torch.Tensor:
    if alpha.dim() == 2:
        alpha = alpha.unsqueeze(0)
    if alpha.dim() == 4 and alpha.shape[-1] == 1:
        alpha = alpha.squeeze(-1)
    batch = image.shape[0]
    if alpha.shape[0] != batch:
        alpha = alpha[:1].repeat(batch, 1, 1)
    if alpha.shape[1:3] != image.shape[1:3]:
        alpha = F.interpolate(alpha.unsqueeze(1), size=image.shape[1:3], mode="bilinear", align_corners=False).squeeze(1)
    return torch.clamp(alpha, 0.0, 1.0)


def _tensor_to_rgba_images(image: torch.Tensor, alpha: torch.Tensor | None) -> list[Image.Image]:
    image = torch.clamp(image.detach().cpu(), 0.0, 1.0)
    if image.dim() != 4:
        raise ValueError("image must be a ComfyUI IMAGE tensor [B,H,W,C]")
    if image.shape[-1] >= 4 and alpha is None:
        alpha = image[..., 3]
    if alpha is None:
        alpha = torch.ones(image.shape[0], image.shape[1], image.shape[2], dtype=image.dtype)
    else:
        alpha = _resize_alpha(alpha.detach().cpu(), image)

    pil_images = []
    for index in range(image.shape[0]):
        rgb = image[index, :, :, :3].numpy()
        a = alpha[index].numpy()
        rgba = np.concatenate([rgb, a[:, :, None]], axis=2)
        pil_images.append(Image.fromarray(np.clip(rgba * 255.0, 0, 255).astype(np.uint8), "RGBA"))
    return pil_images


def _alpha_bounds(image: Image.Image) -> dict[str, int]:
    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    width, height = image.size
    if bbox is None:
        return {"left": 0, "top": 0, "right": width, "bottom": height}
    return {"left": bbox[0], "top": bbox[1], "right": bbox[2], "bottom": bbox[3]}


def _image_bounds(image: Image.Image) -> dict[str, int]:
    width, height = image.size
    return {"left": 0, "top": 0, "right": width, "bottom": height}


def _clean_text(value: str | None) -> str:
    return str(value or "").strip()


def _input_file_path(filename: str) -> Path:
    text = _clean_text(filename)
    if not text:
        raise ValueError("Input filename is empty")

    path = Path(folder_paths.get_annotated_filepath(text)).resolve()
    input_dir = Path(folder_paths.get_input_directory()).resolve()
    if path != input_dir and input_dir not in path.parents:
        raise ValueError("Adv_Request only loads files from the ComfyUI input directory")
    if not path.exists():
        raise FileNotFoundError(f"Input image not found: {text}")
    return path


def _input_file_fingerprint(filename: str | None) -> str:
    text = _clean_text(filename)
    if not text:
        return "empty"
    try:
        path = _input_file_path(text)
        stat = path.stat()
    except Exception as exc:
        return f"error:{type(exc).__name__}:{text}"
    return f"{path}:{stat.st_mtime_ns}:{stat.st_size}"


def _load_input_image(filename: str) -> storage.ImageLoadResult:
    path = _input_file_path(filename)
    with Image.open(path) as image:
        return storage.pil_to_tensors(image.copy())


def _mask_to_tensor(mask: Image.Image, width: int, height: int, image: torch.Tensor) -> torch.Tensor:
    mask = ImageOps.exif_transpose(mask).convert("L")
    if mask.size != (width, height):
        mask = mask.resize((width, height), Image.Resampling.NEAREST)
    tensor = torch.from_numpy(np.asarray(mask).astype(np.float32) / 255.0).unsqueeze(0)
    return _resize_alpha(tensor, image)


def _load_input_mask(filename: str, width: int, height: int, image: torch.Tensor) -> torch.Tensor:
    path = _input_file_path(filename)
    with Image.open(path) as mask:
        return _mask_to_tensor(mask.copy(), width, height, image)


def _full_mask(image: torch.Tensor) -> torch.Tensor:
    return torch.ones((image.shape[0], image.shape[1], image.shape[2]), dtype=image.dtype, device=image.device)


def _blank_image_like(image: torch.Tensor) -> torch.Tensor:
    return torch.zeros_like(image)


def _bridge_selection_available(slot_id: str = "IMAGE_1") -> bool:
    state = storage.load_state()
    canonical_slot_id = storage.image_slot_id(slot_id)
    selection = None
    selections = state.get("selections")
    if isinstance(selections, dict):
        selection = selections.get(canonical_slot_id)
    if not isinstance(selection, dict) and canonical_slot_id == "IMAGE_1":
        selection = state.get("selection")
    if not isinstance(selection, dict):
        return False
    filename = selection.get("filename")
    if not filename:
        return False
    return (storage.PS_IMAGES_DIR / filename).exists()


def _clamp_image_count(value: Any) -> int:
    return max(1, min(_coerce_int(value, 1), ADV_REQUEST_MAX_IMAGES))


def _clamp_batch_count(value: Any) -> int:
    return max(1, min(_coerce_int(value, 1), ADV_REQUEST_MAX_BATCH_COUNT))


def _parse_params_json(value: str | None) -> dict[str, Any]:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"raw_params_json": text}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _canonical_params_json(
    params_json: str,
    *,
    image_count: int,
    resolution: str,
    strength: float,
    batch_count: int,
    seed: int,
) -> str:
    params = _parse_params_json(params_json)
    params.update(
        {
            "image_count": image_count,
            "resolution": str(resolution or ""),
            "strength": float(strength),
            "batch_count": int(batch_count),
            "batchCount": int(batch_count),
            "seed": int(seed),
        }
    )
    return json.dumps(params, ensure_ascii=False, separators=(",", ":"))


def _request_json(
    *,
    image_count: int,
    prompt: str,
    resolution: str,
    strength: float,
    batch_count: int,
    seed: int,
    params_json: str,
) -> str:
    return json.dumps(
        {
            "image_count": image_count,
            "prompt": str(prompt or ""),
            "resolution": str(resolution or ""),
            "strength": float(strength),
            "batch_count": int(batch_count),
            "seed": int(seed),
            "params_json": params_json,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _image_source(
    *,
    index: int,
    connected_image: torch.Tensor | None,
    image_file: str,
    enabled: bool,
    fallback: torch.Tensor | None = None,
) -> storage.ImageLoadResult:
    if not enabled:
        if fallback is not None:
            return storage.ImageLoadResult(
                _blank_image_like(fallback),
                _full_mask(fallback),
                _full_mask(fallback),
                int(fallback.shape[2]),
                int(fallback.shape[1]),
            )
        placeholder = torch.zeros((1, 512, 512, 3), dtype=torch.float32)
        return storage.ImageLoadResult(placeholder, _full_mask(placeholder), _full_mask(placeholder), 512, 512)
    if connected_image is not None:
        height = int(connected_image.shape[1])
        width = int(connected_image.shape[2])
        return storage.ImageLoadResult(connected_image, _full_mask(connected_image), _full_mask(connected_image), width, height)
    if enabled and _clean_text(image_file):
        return _load_input_image(image_file)
    if enabled:
        return storage.load_bridge_image(f"IMAGE_{index}", 512, 512)


class AdvRequest(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        extra_image_outputs = [
            io.Image.Output(f"image_{index}", display_name=f"IMAGE_{index}")
            for index in range(2, ADV_REQUEST_MAX_IMAGES + 1)
        ]
        return io.Schema(
            node_id="Adv_Request",
            display_name="PS Bridge Send To ComfyUI",
            category=CATEGORY,
            description="Official PS plugin request entry node for mapped local ComfyUI and RunningHub workflows.",
            inputs=[
                io.Int.Input("image_count", default=1, min=1, max=ADV_REQUEST_MAX_IMAGES, step=1, socketless=True),
                io.String.Input("prompt", default="", multiline=True, dynamic_prompts=True, socketless=True),
                io.String.Input("resolution", default="1k", socketless=True),
                io.Float.Input("strength", default=0.65, min=0.0, max=1.0, step=0.01, socketless=True),
                io.Int.Input("batch_count", default=1, min=1, max=ADV_REQUEST_MAX_BATCH_COUNT, step=1, socketless=True),
                io.Int.Input(
                    "seed",
                    default=42,
                    min=0,
                    max=SEED_MAX,
                    step=1,
                    control_after_generate=True,
                    socketless=True,
                ),
            ],
            outputs=[
                io.Image.Output("image_1", display_name="IMAGE_1"),
                *extra_image_outputs,
                io.Mask.Output("mask", display_name="MASK"),
                io.String.Output("prompt", display_name="PROMPT"),
                io.String.Output("resolution", display_name="RESOLUTION"),
                io.Float.Output("strength", display_name="STRENGTH"),
                io.Int.Output("batch_count", display_name="BATCH_COUNT"),
                io.Int.Output("seed", display_name="SEED"),
                io.Int.Output("width", display_name="WIDTH"),
                io.Int.Output("height", display_name="HEIGHT"),
                io.String.Output("params_json", display_name="PARAMS_JSON"),
                io.String.Output("request_json", display_name="REQUEST_JSON"),
            ],
        )

    @classmethod
    def execute(
        cls,
        image_count: int = 1,
        prompt: str = "",
        resolution: str = "1k",
        strength: float = 0.65,
        batch_count: int = 1,
        seed: int = 42,
        params_json: str = "{}",
        image_1_file: str = "",
        image_2_file: str = "",
        image_3_file: str = "",
        image_4_file: str = "",
        image_5_file: str = "",
        image_6_file: str = "",
        mask_image_file: str = "",
    ) -> io.NodeOutput:
        if not _clean_text(params_json) or params_json == "{}":
            state_request = storage.load_state().get("adv_request")
            if isinstance(state_request, dict):
                params_json = str(state_request.get("params_json") or params_json)
        count = _clamp_image_count(image_count)
        image_files = [image_1_file, image_2_file, image_3_file, image_4_file, image_5_file, image_6_file]

        loaded_images: list[storage.ImageLoadResult] = []
        first_image: torch.Tensor | None = None
        for index in range(1, ADV_REQUEST_MAX_IMAGES + 1):
            loaded = _image_source(
                index=index,
                connected_image=None,
                image_file=image_files[index - 1],
                enabled=index <= count,
                fallback=first_image,
            )
            if first_image is None:
                first_image = loaded.image
            loaded_images.append(loaded)

        base = loaded_images[0]
        image = base.image
        width = int(base.width)
        height = int(base.height)

        if _clean_text(mask_image_file):
            output_mask = _load_input_mask(mask_image_file, width, height, image)
        elif _bridge_selection_available():
            output_mask = _resize_alpha(storage.load_bridge_image("IMAGE_1", width, height).selection, image)
        else:
            output_mask = _full_mask(image)

        batch = _clamp_batch_count(batch_count)
        seed_value = max(0, min(_coerce_int(seed, 42), SEED_MAX))
        strength_value = max(0.0, min(1.0, _coerce_float(strength, 0.65)))
        merged_params_json = _canonical_params_json(
            params_json,
            image_count=count,
            resolution=resolution,
            strength=strength_value,
            batch_count=batch,
            seed=seed_value,
        )
        request_json = _request_json(
            image_count=count,
            prompt=prompt,
            resolution=resolution,
            strength=strength_value,
            batch_count=batch,
            seed=seed_value,
            params_json=merged_params_json,
        )

        return io.NodeOutput(
            loaded_images[0].image,
            *(loaded.image for loaded in loaded_images[1:]),
            output_mask,
            str(prompt or ""),
            str(resolution or ""),
            strength_value,
            batch,
            seed_value,
            width,
            height,
            merged_params_json,
            request_json,
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs) -> tuple:
        count = _clamp_image_count(kwargs.get("image_count", 1))
        file_fingerprints = tuple(_input_file_fingerprint(kwargs.get(f"image_{index}_file")) for index in range(1, count + 1))
        mask_file = kwargs.get("mask_image_file")
        uses_bridge = any(not _clean_text(kwargs.get(f"image_{index}_file")) for index in range(1, count + 1))
        if not _clean_text(mask_file):
            uses_bridge = True
        return (
            count,
            str(kwargs.get("prompt") or ""),
            str(kwargs.get("resolution") or ""),
            max(0.0, min(1.0, _coerce_float(kwargs.get("strength"), 0.65))),
            _clamp_batch_count(kwargs.get("batch_count", 1)),
            _coerce_int(kwargs.get("seed"), 42),
            str(kwargs.get("params_json") or "{}"),
            file_fingerprints,
            _input_file_fingerprint(mask_file),
            storage.latest_fingerprint() if uses_bridge else "",
        )


class AdvSendToPS(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="Adv_SendToPS",
            display_name="PS Bridge Send To Photoshop",
            category=CATEGORY,
            description="Terminal node for official Adv Request templates. Connect the final generated IMAGE here to return it to Photoshop.",
            is_output_node=True,
            not_idempotent=True,
            inputs=[
                io.Image.Input("image"),
                io.Mask.Input("alpha", optional=True, tooltip="Optional opacity alpha mask, 0 transparent and 1 opaque."),
                io.String.Input("filename_prefix", default="PSBridge", advanced=True, optional=True),
            ],
            outputs=[],
            hidden=[io.Hidden.prompt, io.Hidden.extra_pnginfo],
        )

    @classmethod
    def execute(
        cls,
        image: torch.Tensor,
        alpha: torch.Tensor | None = None,
        filename_prefix: str = "PSBridge",
        request_id: str = "",
    ) -> io.NodeOutput:
        return _send_images_to_ps(image, alpha, filename_prefix, request_id)

    @classmethod
    def fingerprint_inputs(cls, **kwargs) -> float:
        return float("NaN")


def _send_images_to_ps(
    image: torch.Tensor,
    alpha: torch.Tensor | None,
    filename_prefix: str,
    request_id: str,
) -> io.NodeOutput:
    temp_dir = Path(folder_paths.get_temp_directory())
    temp_dir.mkdir(parents=True, exist_ok=True)
    latest_state = storage.load_state()
    request_id = str(request_id or latest_state.get("request_id") or "")
    prefix = safe_component(filename_prefix or "PSBridge", "PSBridge")
    request = safe_component(request_id or f"render-{int(time.time() * 1000)}", "render")
    pil_images = _tensor_to_rgba_images(image, alpha)
    saved = []
    render_payload = {
        "request_id": request_id,
        "transport": "file_ref",
        "images": [],
    }
    latest_images = latest_state.get("images") or {}
    latest_main = latest_images.get("IMAGE_1") or latest_images.get(storage.DEFAULT_SLOT_ID)
    placement_metadata = {}
    if isinstance(latest_main, dict):
        placement_bounds = _first_present(latest_main, "placement_bounds", "placementBounds", "bounds")
        target_document_id = _first_present(
            latest_main,
            "targetDocumentId",
            "targetDocumentID",
            "target_document_id",
            "documentID",
            "documentId",
        )
        document = _first_present(latest_main, "document", "document_info", "documentInfo")
        if placement_bounds is not None:
            placement_metadata["placement_bounds"] = placement_bounds
        if target_document_id is not None:
            placement_metadata["targetDocumentId"] = target_document_id
        if document is not None:
            placement_metadata["document"] = document

    for index, pil_image in enumerate(pil_images):
        filename = f"{prefix}_{request}_{index:03d}.png"
        path = temp_dir / filename
        pil_image.save(path, compress_level=0)
        saved.append(ui.SavedResult(filename=filename, subfolder="", type=io.FolderType.temp))
        image_payload = {
            "request_id": request_id,
            "transport": "file_ref",
            "filename": filename,
            "subfolder": "",
            "type": "temp",
            "url": f"/view?filename={quote(filename, safe='')}&subfolder=&type=temp",
            "format": "png",
            "mime_type": "image/png",
            "width": pil_image.width,
            "height": pil_image.height,
            "source_bounds": _image_bounds(pil_image),
            "alpha_bounds": _alpha_bounds(pil_image),
        }
        if _inline_render_image_enabled():
            image_payload["image"] = path.read_bytes()
        image_payload.update(placement_metadata)
        render_payload["images"].append(image_payload)

    manager.send_render_result_from_thread(render_payload)
    return io.NodeOutput(ui=ui.SavedImages(saved))
