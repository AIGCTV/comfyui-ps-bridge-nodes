"""Request-scoped images, selection, and read-only runtime metadata."""
from __future__ import annotations
import numpy as np
import torch
from PIL import Image, ImageOps
import folder_paths
from comfy_api.latest import io
from comfy_execution.graph_utils import ExecutionBlocker
from .errors import require, BridgeError
from .json_codec import byte_digest, digest, canonical_bytes, execution_prompt
from .parameters import identifier
from .protocol import SLOTS, BRIDGE_MAX_PIXELS
from .security import resolve_inside
from .service import get_service
from .parameter_nodes import text

def input_path(file_name, *, field="file_name"):
    try:
        path = resolve_inside(__import__("pathlib").Path(folder_paths.get_input_directory()), file_name, allow_subdirs=True)
        require(path.is_file(), "MISSING_MEDIA", f"{field}: input file does not exist: {file_name}", field=field)
        return path
    except ValueError as exc:
        if isinstance(exc, BridgeError):
            raise
        raise BridgeError("ASSET_INVALID", "File test path must be inside ComfyUI input") from exc

def run_context(cls, request_id):
    service = get_service()
    run = service.runs.get(request_id)
    hidden = getattr(cls, "hidden", None)
    meta = (getattr(hidden, "extra_pnginfo", None) or {}).get("ps_bridge_v3")
    prompt = execution_prompt(getattr(hidden, "prompt", None))
    require(meta == run["extra_data"]["extra_pnginfo"]["ps_bridge_v3"] and digest(prompt) == digest(run["prompt"]),
            "BINDING_INVALID", "Execution metadata or API differs from the frozen run")
    from comfy_execution.utils import get_executing_context
    context = get_executing_context()
    prompt_id = getattr(context, "prompt_id", None)
    require(isinstance(prompt_id, str) and prompt_id, "BINDING_INVALID", "Execution has no ComfyUI prompt identity")
    service.runs.associate_execution(request_id, prompt_id)
    return service, run, context

def load_image(path, *, local=False):
    with Image.open(path) as image:
        require(image.width * image.height <= BRIDGE_MAX_PIXELS and not getattr(image, "is_animated", False),
                "ASSET_INVALID", "Unsupported image size or animation")
        modes = ("RGB", "RGBA", "L", "LA", "P") if local else ("RGB", "RGBA", "L")
        require(image.mode in modes, "UNSUPPORTED_PIXEL_FORMAT", "Expected an 8-bit color or grayscale image")
        # PNG RGB16 can be down-converted by Pillow before mode inspection.
        if local and image.format == "PNG":
            with open(path, "rb") as header_file:
                header = header_file.read(29)
            require(len(header) >= 29 and header[24] <= 8, "UNSUPPORTED_PIXEL_FORMAT", "High-bit-depth PNG is unsupported")
        if local:
            image = ImageOps.exif_transpose(image)
        # Materialize only the tensors returned by this node. A full float RGBA
        # intermediate followed by RGB/alpha copies doubles peak pixel memory.
        rgb_pixels = np.asarray(image.convert("RGB"), dtype=np.float32)
        rgb_pixels /= 255.0
        if "A" in image.getbands() or "transparency" in image.info:
            channel = image.getchannel("A") if "A" in image.getbands() else image.convert("RGBA").getchannel("A")
            opacity_pixels = np.asarray(channel, dtype=np.float32)
            opacity_pixels /= 255.0
        else:
            opacity_pixels = np.ones((image.height, image.width), dtype=np.float32)
    rgb = torch.from_numpy(rgb_pixels).unsqueeze(0)
    opacity = torch.from_numpy(opacity_pixels).unsqueeze(0)
    return rgb, opacity

HIDDEN = [io.Hidden.prompt, io.Hidden.extra_pnginfo, io.Hidden.unique_id]

class VPImage(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        from .protocol import NODE_CATEGORY
        return io.Schema(node_id="VP_Image", display_name="PS Images", category=NODE_CATEGORY,
            inputs=[io.Combo.Input("slot", display_name="Image Name", options=list(SLOTS), default="main", socketless=True),
                    io.Boolean.Input("required", default=True, socketless=True, advanced=True),
                    text("file_name", advanced=True), text("selection_file", advanced=True), text("request_id", advanced=True)],
            outputs=[io.Image.Output("RGB"), io.Mask.Output("ALPHA"), io.Mask.Output("MASK"),
                     io.Int.Output("width"), io.Int.Output("height")], hidden=HIDDEN, has_intermediate_output=True)

    @classmethod
    def execute(cls, slot="main", required=True, file_name="", selection_file="", request_id=""):
        from comfy_api.latest import ui
        from .parameter_nodes import check_graph
        check_graph(cls)
        require(slot in SLOTS and type(required) is bool and (slot != "main" or required), "PARAMETER_INVALID", "Invalid image slot")
        require(slot == "main" or not selection_file, "PARAMETER_INVALID", "Reference images do not have a selection")
        selection_path = None
        if request_id:
            service, run, _ = run_context(cls, request_id)
            node_id = str(cls.hidden.unique_id)
            require(any(b == {"slot": slot, "nodeId": node_id, "required": required} for b in run["manifest"]["images"]),
                    "BINDING_INVALID", "Image node does not match the frozen resource binding")
            asset = next((a for a in run["media"]["images"] if a["role"] == slot), None)
            if asset is None:
                require(not required, "MISSING_MEDIA", "Required image is absent")
                return io.NodeOutput(ExecutionBlocker(None), ExecutionBlocker(None), ExecutionBlocker(None), 0, 0,
                                     ui={"ps_preview": [], "has_selection": [False]})
            require(service.assets.get(run["controllerClientId"], asset["assetId"]) == asset,
                    "BINDING_INVALID", "Image asset differs from the frozen run")
            path = service.assets.path(asset["assetId"])
            if slot == "main" and run["media"]["runtime"]["hasSelection"]:
                selection = run["media"]["selection"]
                require(service.assets.get(run["controllerClientId"], selection["assetId"]) == selection,
                        "BINDING_INVALID", "Selection asset differs from the frozen run")
                selection_path = service.assets.path(selection["assetId"])
        else:
            if not file_name and not required:
                return io.NodeOutput(ExecutionBlocker(None), ExecutionBlocker(None), ExecutionBlocker(None), 0, 0,
                                     ui={"ps_preview": [], "ps_preview_file": [file_name], "has_selection": [False]})
            require(bool(file_name), "MISSING_MEDIA", "Select an input file for standalone testing")
            path = input_path(file_name)
            selection_path = input_path(selection_file, field="selection_file") if slot == "main" and selection_file else None
        image, opacity = load_image(path, local=not request_id)
        height, width = image.shape[1:3]
        mask = torch.ones((1, height, width), dtype=torch.float32)
        if selection_path:
            with Image.open(selection_path) as selected:
                require(selected.mode == "L" and selected.size == (width, height), "ASSET_INVALID",
                        f"Selection must be 8-bit grayscale matching main dimensions (image={width}x{height}, "
                        f"selection={selected.width}x{selected.height}, mode={selected.mode})")
                selection_pixels = np.asarray(selected, dtype=np.float32)
                selection_pixels /= 255.0
                mask = torch.from_numpy(selection_pixels).unsqueeze(0)
        # Business thumbnails are delivered only by the scoped authenticated endpoint,
        # never by ComfyUI's globally addressable /view temporary preview.
        preview = None
        if not request_id:
            saved = ui.PreviewImage(torch.cat((image, opacity.unsqueeze(-1)), dim=-1), cls=cls)
            # A namespaced UI key keeps the standard output store from drawing a
            # second preview below our checkerboard. Still use the core PNG helper.
            preview = {"ps_preview": saved.as_dict()["images"], "ps_preview_file": [file_name]}
        return io.NodeOutput(image, opacity, mask, int(width), int(height), ui=preview)

    @classmethod
    def fingerprint_inputs(cls, request_id="", file_name="", selection_file="", slot="main", **kwargs):
        if request_id:
            run = get_service().runs.get(request_id)
            return digest({"runId": request_id, "slot": slot, "media": run["media"]})
        return digest({"image": byte_digest(input_path(file_name).read_bytes()) if file_name else "missing",
                       "selection": byte_digest(input_path(selection_file, field="selection_file").read_bytes()) if slot == "main" and selection_file else None})
