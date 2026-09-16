"""Full-canvas PNG output, journaled before ComfyUI success."""
from __future__ import annotations
import io as buffers
import math
import uuid
from pathlib import Path
from urllib.parse import quote
import numpy as np
import torch
from PIL import Image, PngImagePlugin
import folder_paths
from comfy_api.latest import io, ui
from .errors import require
from .json_codec import byte_digest, canonical_bytes, execution_prompt
from .protocol import BRIDGE_MAX_RESULTS, BRIDGE_MAX_PIXELS, BRIDGE_MAX_ASSET_BYTES, NODE_CATEGORY
from .security import safe_component
from .storage import atomic_bytes
from .media_nodes import run_context, HIDDEN
from .parameter_nodes import text
from .diagnostics import trace

def validate_image_batch(images, alpha):
    require(isinstance(images, torch.Tensor) and images.ndim == 4 and images.shape[-1] in (3, 4),
            "PARAMETER_INVALID", "IMAGE must have shape [B,H,W,3/4]")
    batch, height, width, _ = images.shape
    require(0 < batch <= BRIDGE_MAX_RESULTS and 0 < height * width <= BRIDGE_MAX_PIXELS,
            "MEDIA_OVERFLOW", "Output exceeds image or batch limits", status=413)
    def unit_range(frame, message):
        # One reduction checks finiteness and range without even allocating a
        # frame-sized boolean tensor. Only two scalar values cross devices.
        minimum, maximum = torch.aminmax(frame)
        low, high = minimum.item(), maximum.item()
        require(math.isfinite(low) and math.isfinite(high), "PARAMETER_INVALID", message)
        return low >= 0 and high <= 1

    # Validate every frame before creating durable files. Valid tensors can be
    # returned as views; only out-of-range values need a clamped output copy.
    rgb_in_range, implicit_alpha_in_range = True, True
    for frame in images:
        if not unit_range(frame, "Output image contains non-finite values"):
            rgb_in_range = unit_range(frame[..., :3], "Output image contains non-finite values") and rgb_in_range
            if frame.shape[-1] == 4:
                implicit_alpha_in_range = unit_range(frame[..., 3], "Output image contains non-finite values") and implicit_alpha_in_range
    alpha_in_range = implicit_alpha_in_range
    if alpha is not None:
        require(isinstance(alpha, torch.Tensor) and alpha.ndim == 3 and tuple(alpha.shape[1:]) == (height, width)
                and alpha.shape[0] in (1, batch), "ALPHA_SHAPE_MISMATCH", "Alpha must match dimensions and have batch 1 or image batch")
        alpha_in_range = True
        for frame in alpha:
            alpha_in_range = unit_range(frame, "Alpha contains non-finite values") and alpha_in_range
    return rgb_in_range, alpha_in_range


def rgba_images(images, alpha, *, validated=False):
    if not validated:
        validate_image_batch(images, alpha)
    for i in range(images.shape[0]):
        array = images[i].detach().cpu().clamp(0, 1).numpy()
        pixels = (array[:, :, :3] * 255.0).round().astype(np.uint8)
        result = Image.fromarray(pixels, "RGB")
        opacity = (alpha[0 if alpha.shape[0] == 1 else i].detach().cpu().clamp(0, 1).numpy()
                   if alpha is not None else array[:, :, 3] if array.shape[-1] == 4 else None)
        if opacity is not None:
            result.putalpha(Image.fromarray((opacity * 255.0).round().astype(np.uint8), "L"))
        del array, pixels, opacity
        yield result


class _PngLimitExceeded(Exception):
    pass


class _LimitedPngBuffer(buffers.BytesIO):
    def write(self, data):
        if self.tell() + len(data) > BRIDGE_MAX_ASSET_BYTES:
            raise _PngLimitExceeded
        return super().write(data)


def encode_png(image, pnginfo=None):
    # Both passes are lossless, with the same dimensions, channels and metadata.
    # A large fast encoding gets one stronger compression attempt; never resize
    # or discard alpha to fit the transport's per-result byte limit.
    for level in (1, 9):
        with _LimitedPngBuffer() as buffer:
            try:
                image.save(buffer, format="PNG", pnginfo=pnginfo, compress_level=level)
            except _PngLimitExceeded:
                continue
            return buffer.getvalue()
    require(False, "RESULT_TOO_LARGE", "PNG result exceeds the 64 MiB byte limit after lossless compression", status=413)

class VPSendToPS(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="VP_SendToPS", display_name="Send to PS", category=NODE_CATEGORY,
            is_output_node=True, not_idempotent=True,
            inputs=[io.Image.Input("images", display_name="RGB"), io.Mask.Input("alpha", display_name="ALPHA", optional=True), text("result_id", "result", advanced=True),
                    text("filename_prefix", "VPlugins", advanced=True), text("request_id", advanced=True)],
            outputs=[io.Image.Output("RGB"), io.Mask.Output("ALPHA")], hidden=HIDDEN)

    @classmethod
    def execute(cls, images, alpha=None, result_id="result", filename_prefix="VPlugins", request_id=""):
        from .parameter_nodes import check_graph
        check_graph(cls)
        # Validate before touching the result journal or allocating any files.
        rgb_in_range, opacity_in_range = validate_image_batch(images, alpha)
        frames = rgba_images(images, alpha, validated=True)
        rgb = images[..., :3]
        if not rgb_in_range:
            rgb = rgb.clamp(0, 1)
        opacity = (alpha if alpha is not None else images[..., 3] if images.shape[-1] == 4
                   else torch.ones(images.shape[:3], dtype=images.dtype, device=images.device))
        if not opacity_in_range:
            opacity = opacity.clamp(0, 1)
        if opacity.shape[0] == 1 and images.shape[0] != 1:
            opacity = opacity.expand(images.shape[0], -1, -1)
        run, service, context = None, None, None
        node_id = str(getattr(getattr(cls, "hidden", None), "unique_id", "") or "standalone")
        if request_id:
            service, run, context = run_context(cls, request_id)
            require(any(r["nodeId"] == node_id and r["resultId"] == result_id for r in run["manifest"]["results"]),
                    "BINDING_INVALID", "Output identity not declared")
            require(len(run["results"]) + images.shape[0] <= run["manifest"]["limits"]["maxResults"],
                    "MEDIA_OVERFLOW", "Output exceeds function result limit", status=413)
        # Business results use durable output, so ComfyUI temp cleanup cannot erase recoverable files.
        output = Path(folder_paths.get_output_directory() if request_id else folder_paths.get_temp_directory())
        folder_type = "output" if request_id else "temp"
        output.mkdir(parents=True, exist_ok=True)
        prefix = safe_component(filename_prefix, "VPlugins")
        saved = []
        pnginfo = PngImagePlugin.PngInfo()
        hidden = getattr(cls, "hidden", None)
        for key, value in (("prompt", execution_prompt(getattr(hidden, "prompt", None))),
                           ("workflow", (getattr(hidden, "extra_pnginfo", None) or {}).get("workflow"))):
            if value is not None:
                pnginfo.add_text(key, canonical_bytes(value).decode("utf-8"))
        image = None
        try:
            for index, image in enumerate(frames):
                filename = f"{prefix}_{uuid.uuid4().hex}_{index:03d}.png"
                data = encode_png(image, pnginfo)
                atomic_bytes(output / filename, data)
                ref = {"filename": filename, "subfolder": "", "type": folder_type}
                saved.append(ui.SavedResult(filename=filename, subfolder="", type=io.FolderType(folder_type)))
                if run:
                    # Comfy list expansion invokes a node repeatedly; allocate stable per-result indices under the run lock below.
                    record = {"resultId": result_id, "nodeId": node_id, "runId": request_id,
                        "promptId": context.prompt_id, "batchIndex": index, "mime": "image/png",
                        "width": image.width, "height": image.height, "sha256": byte_digest(data), "byteLength": len(data),
                        "file": ref, "url": f"/view?filename={quote(filename)}&subfolder=&type={folder_type}",
                        "targetDocumentId": run["media"]["runtime"]["documentId"], "placementBounds": run["media"]["runtime"]["placementBounds"],
                        "sourceBounds": run["media"]["runtime"]["sourceBounds"],
                        "captureWidth": run["media"]["runtime"]["width"], "captureHeight": run["media"]["runtime"]["height"]}
                    with service.runs.lock:
                        current = service.runs.get(request_id)
                        record["batchIndex"] = sum(r["resultId"] == result_id for r in current["results"])
                        service.result_assets.register(output / filename, record)
                        # A frame becomes ready only after its atomic PNG write
                        # and journal commit. Later frames cannot hold it back.
                        service.runs.record_results(request_id, context.prompt_id, node_id, result_id, [record])
                image.close()
        except OSError:
            if run:
                service.runs.update(request_id, resultError={"code": "RESULT_SAVE_FAILED", "message": "Failed to persist a PNG result", "retryable": False})
            raise
        finally:
            if image is not None:
                image.close()
            frames.close()
        result = io.NodeOutput(rgb, opacity, ui=ui.SavedImages(saved))
        if run:
            trace("node.ui_ready", runId=request_id, promptId=context.prompt_id, nodeId=node_id, count=len(saved))
        return result

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return float("nan")
