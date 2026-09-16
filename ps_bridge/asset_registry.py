"""Freeze validated PNGs and their capture identity under opaque asset IDs."""
from __future__ import annotations
import copy
import io
import struct
import threading
import uuid
from collections import OrderedDict
from pathlib import Path
from PIL import Image
from .errors import BridgeError, require
from .json_codec import byte_digest, digest
from .parameters import identifier, number
from .protocol import BRIDGE_MAX_ASSET_BYTES, BRIDGE_MAX_PIXELS, SLOTS
from .security import resolve_inside
from .storage import atomic_bytes, atomic_json, read_json
from .schemas import validate_shape

def bounds(value, field):
    require(isinstance(value, dict) and set(value) == {"left", "top", "right", "bottom"},
            "ASSET_INVALID", "Expected a rectangle", field=field)
    v = {k: number(n) for k, n in value.items()}
    require(v["right"] > v["left"] and v["bottom"] > v["top"], "ASSET_INVALID", "Rectangle must have positive size", field=field)

def _inspect_png(data, *, preview=False):
    require(isinstance(data, bytes) and 0 < len(data) <= BRIDGE_MAX_ASSET_BYTES, "ASSET_INVALID", "PNG exceeds byte limit", status=413)
    require(data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 33, "ASSET_INVALID", "Expected a complete PNG")
    width, height, bit_depth, color_type = struct.unpack(">IIBB", data[16:26])
    require(0 < width * height <= BRIDGE_MAX_PIXELS, "ASSET_INVALID", "PNG exceeds pixel limit", status=413)
    require(bit_depth == 8 and color_type in (0, 2, 6), "UNSUPPORTED_PIXEL_FORMAT", "Only 8-bit grayscale/RGB/RGBA PNG is supported")
    try:
        with Image.open(io.BytesIO(data)) as image:
            require(image.format == "PNG" and not getattr(image, "is_animated", False), "ASSET_INVALID", "Expected a single PNG frame")
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            components = {"L": 1, "RGB": 3, "RGBA": 4}.get(image.mode)
            require(components is not None, "UNSUPPORTED_PIXEL_FORMAT", "Unsupported PNG mode")
            extrema = image.getextrema() if image.mode == "L" else None
            thumbnail = None
            if preview:
                # Reuse the boundary validation decode. Encoding is deferred until
                # an authorized editor actually requests this derived resource.
                image.thumbnail((512, 512))
                thumbnail = image.copy()
    except (OSError, ValueError, SyntaxError) as exc:
        if isinstance(exc, BridgeError):
            raise
        raise BridgeError("ASSET_INVALID", "PNG decoding failed") from exc
    return ({"sha256": byte_digest(data), "byteLength": len(data), "width": width, "height": height,
             "components": components, "mime": "image/png"}, extrema, thumbnail)


def validate_png(data):
    return _inspect_png(data)[0]

class AssetRegistry:
    def __init__(self, root, input_dir):
        self.root, self.input_dir = Path(root), Path(input_dir)
        self.lock = threading.RLock()
        # These caches own derived memory only; eviction never removes durable
        # assets still referenced by an active run or cold recovery.
        self.validation_cache = OrderedDict()
        self.validation_cache_limit = 128
        self.preview_cache = OrderedDict()
        self.preview_cache_limit = 32
        self.preview_cache_budget = 32 * 1024 * 1024
        self.preview_cache_bytes = 0

    def _signature(self, asset_id):
        try:
            return tuple((s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                         for s in (self.path(asset_id).stat(), (self.root / f"{asset_id}.json").stat()))
        except OSError as exc:
            raise BridgeError("ASSET_INVALID", "Frozen asset is unavailable") from exc

    def _remember(self, owner, asset, signature, extrema=None):
        asset_id = asset["assetId"]
        self.validation_cache[asset_id] = (signature, owner, copy.deepcopy(asset), extrema)
        self.validation_cache.move_to_end(asset_id)
        while len(self.validation_cache) > self.validation_cache_limit:
            self.validation_cache.popitem(last=False)

    def _remember_preview(self, asset_id, signature, preview):
        previous = self.preview_cache.pop(asset_id, None)
        if previous:
            self.preview_cache_bytes -= previous[2]
        size = len(preview) if isinstance(preview, bytes) else preview.width * preview.height * len(preview.getbands())
        if size > self.preview_cache_budget:
            return
        self.preview_cache[asset_id] = (signature, preview, size)
        self.preview_cache_bytes += size
        while len(self.preview_cache) > self.preview_cache_limit or self.preview_cache_bytes > self.preview_cache_budget:
            _, evicted = self.preview_cache.popitem(last=False)
            self.preview_cache_bytes -= evicted[2]

    def register(self, owner, metadata, data):
        require(isinstance(metadata, dict), "ASSET_INVALID", "Missing capture metadata")
        required = {"sha256", "byteLength", "width", "height", "components", "captureId", "role", "sourceBounds", "placementBounds", "documentId"}
        require(required <= set(metadata) and set(metadata) <= required | {"mime", "componentSize", "syntheticPolicy"},
                "ASSET_INVALID", "Invalid capture metadata fields")
        identifier(metadata["captureId"], "captureId")
        require(type(metadata["documentId"]) in (str, int) and str(metadata["documentId"]), "ASSET_INVALID", "Missing documentId")
        require(metadata["role"] in (*SLOTS, "selection"), "ASSET_INVALID", "Invalid media role")
        require(metadata.get("componentSize", 8) == 8, "UNSUPPORTED_PIXEL_FORMAT", "componentSize must be 8")
        validate_shape("assetMetadata", metadata, code="ASSET_INVALID")
        bounds(metadata["sourceBounds"], "sourceBounds"); bounds(metadata["placementBounds"], "placementBounds")
        actual, extrema, thumbnail = _inspect_png(data, preview=metadata["role"] != "selection")
        for key in ("sha256", "byteLength", "width", "height", "components"):
            require(type(metadata[key]) is type(actual[key]) and metadata[key] == actual[key], "ASSET_INVALID", f"PNG {key} mismatch", field=key)
        require(metadata.get("mime", "image/png") == "image/png", "ASSET_INVALID", "Invalid MIME")
        if "syntheticPolicy" in metadata:
            require(metadata["role"] == "selection" and metadata["syntheticPolicy"] in ("full", "empty"),
                    "ASSET_INVALID", "Only selection may carry a synthetic policy")
            require(extrema == ((255, 255) if metadata["syntheticPolicy"] == "full" else (0, 0)),
                    "ASSET_INVALID", "Synthetic mask pixels disagree with its policy")
        with self.lock:
            # Same bytes + same capture may reuse a record; another capture never loses its identity.
            key = digest({"owner": owner, "metadata": metadata})
            existing = read_json(self.root / f"capture-{key}.json")
            if existing:
                return self.get(owner, existing["assetId"])
            asset_id = uuid.uuid4().hex
            record = {**copy.deepcopy(metadata), **actual, "assetId": asset_id}
            atomic_bytes(self.root / f"{asset_id}.png", data)
            atomic_json(self.root / f"{asset_id}.json", {"owner": owner, "asset": record})
            atomic_json(self.root / f"capture-{key}.json", {"assetId": asset_id})
            signature = self._signature(asset_id)
            self._remember(owner, record, signature, extrema)
            if thumbnail is not None:
                self._remember_preview(asset_id, signature, thumbnail)
            return record

    def register_input(self, owner, file, metadata):
        require(isinstance(file, dict) and set(file) == {"name", "subfolder", "type"} and file["type"] == "input",
                "ASSET_INVALID", "Expected a ComfyUI input file reference")
        require(isinstance(file["subfolder"], str) and isinstance(file["name"], str) and "/" not in file["name"],
                "ASSET_INVALID", "Invalid input file reference")
        relative = f"{file['subfolder']}/{file['name']}" if file["subfolder"] else file["name"]
        try:
            path = resolve_inside(self.input_dir, relative, allow_subdirs=True)
            require(path.is_file() and path.stat().st_size <= BRIDGE_MAX_ASSET_BYTES, "ASSET_INVALID", "Input file missing or too large")
            with path.open("rb") as stream:
                data = stream.read(BRIDGE_MAX_ASSET_BYTES + 1)
        except (ValueError, OSError) as exc:
            if isinstance(exc, BridgeError):
                raise
            raise BridgeError("ASSET_INVALID", "Input reference is outside the controlled directory or unavailable") from exc
        return self.register(owner, metadata, data)

    def get(self, owner, asset_id):
        require(isinstance(asset_id, str) and len(asset_id) == 32 and all(c in "0123456789abcdef" for c in asset_id),
                "ASSET_INVALID", "Invalid asset ID")
        with self.lock:
            cached = self.validation_cache.get(asset_id)
            if cached:
                require(cached[1] == owner, "FORBIDDEN", "Asset belongs to another client", status=403)
                signature = self._signature(asset_id)
                if signature == cached[0]:
                    self.validation_cache.move_to_end(asset_id)
                    return copy.deepcopy(cached[2])
            record = read_json(self.root / f"{asset_id}.json")
            require(record is not None, "ASSET_NOT_FOUND", "Asset does not exist", status=404)
            require(record["owner"] == owner, "FORBIDDEN", "Asset belongs to another client", status=403)
            signature = self._signature(asset_id)
            require(signature[0][2] <= BRIDGE_MAX_ASSET_BYTES, "ASSET_INVALID", "Frozen asset exceeds byte limit", status=413)
            if cached:
                require(cached[2] == record["asset"], "ASSET_INVALID", "Frozen asset metadata changed on disk")
            with self.path(asset_id).open("rb") as stream:
                data = stream.read(BRIDGE_MAX_ASSET_BYTES + 1)
            actual, extrema, thumbnail = _inspect_png(data, preview=record["asset"]["role"] != "selection")
            require(all(record["asset"].get(k) == value for k, value in actual.items()),
                    "ASSET_INVALID", "Frozen asset changed on disk")
            require(self._signature(asset_id) == signature, "ASSET_INVALID", "Frozen asset changed during validation")
            self._remember(owner, record["asset"], signature, extrema)
            if thumbnail is not None:
                self._remember_preview(asset_id, signature, thumbnail)
            return copy.deepcopy(record["asset"])

    def preview(self, owner, asset_id):
        with self.lock:
            self.get(owner, asset_id)
            signature = self._signature(asset_id)
            cached = self.preview_cache.get(asset_id)
            if cached is None or cached[0] != signature:
                with Image.open(self.path(asset_id)) as image:
                    image.thumbnail((512, 512))
                    thumbnail = image.copy()
                require(self._signature(asset_id) == signature, "ASSET_INVALID", "Frozen asset changed during preview")
                self._remember_preview(asset_id, signature, thumbnail)
                cached = (signature, thumbnail, 0)
            preview = cached[1]
            if not isinstance(preview, bytes):
                buffer = io.BytesIO()
                preview.save(buffer, format="PNG", compress_level=1)
                preview = buffer.getvalue()
                self._remember_preview(asset_id, signature, preview)
            if asset_id in self.preview_cache:
                self.preview_cache.move_to_end(asset_id)
            return preview

    def path(self, asset_id):
        require(isinstance(asset_id, str) and len(asset_id) == 32 and all(c in "0123456789abcdef" for c in asset_id),
                "ASSET_INVALID", "Invalid asset ID")
        return self.root / f"{asset_id}.png"

    def freeze(self, owner, media, manifest):
        require(isinstance(media, dict) and set(media) == {"main", "references", "selection", "capture"},
                "ASSET_INVALID", "Expected RunMedia main/references/selection/capture")
        refs = media["references"]
        require(isinstance(refs, list), "ASSET_INVALID", "references must be an array")
        require(len(refs) <= 5, "MEDIA_OVERFLOW", "At most five reference images are supported", status=413)
        validate_shape("runMedia", media, code="ASSET_INVALID")
        limit = manifest["limits"]
        require(len(refs) + 1 <= limit["maxImages"] and len(refs) <= limit["maxReferences"],
                "UNMAPPED_MEDIA", "This function does not consume the supplied references")
        main = self.get(owner, media["main"])
        require(main["role"] == "main", "ASSET_INVALID", "Main asset has the wrong role")
        images = [main] + [self.get(owner, asset) for asset in refs]
        require([a["role"] for a in images] == list(SLOTS[:len(images)]), "ASSET_INVALID", "Reference roles must be ordered without gaps")
        supplied = {a["role"]: a for a in images}
        declared = {a["slot"] for a in manifest["images"]}
        require(all(slot in declared for slot in supplied if slot != "main"), "UNMAPPED_MEDIA", "Undeclared reference image")
        for binding in manifest["images"]:
            require(not binding["required"] or binding["slot"] in supplied, "MISSING_MEDIA", "Required image slot is missing", field=binding["slot"])
        capture = copy.deepcopy(media["capture"])
        require(isinstance(capture, dict) and {"captureId", "documentId", "sourceBounds", "placementBounds", "selectionState"} <= set(capture),
                "ASSET_INVALID", "Incomplete capture identity")
        require(set(capture) <= {"captureId", "documentId", "sourceBounds", "placementBounds", "selectionState",
                                "syntheticPolicy", "documentWidth", "documentHeight"}, "ASSET_INVALID", "Unexpected capture field")
        require(all(capture[k] == main[k] for k in ("captureId", "documentId", "sourceBounds", "placementBounds")),
                "ASSET_INVALID", "Capture metadata differs from main image")
        require(capture["selectionState"] in ("none", "present"), "ASSET_INVALID", "Invalid selection state")
        for k in ("documentWidth", "documentHeight"):
            if k in capture:
                require(type(capture[k]) is int and capture[k] > 0, "ASSET_INVALID", "Invalid document dimensions")
        selection = self.get(owner, media["selection"]) if media["selection"] is not None else None
        real = capture["selectionState"] == "present"
        if selection:
            require(selection["role"] == "selection" and selection["components"] == 1 and
                    all(selection[k] == main[k] for k in ("width", "height", "captureId", "documentId", "sourceBounds", "placementBounds")),
                    "ASSET_INVALID", "Selection must align with the same main capture")
            if real:
                require("syntheticPolicy" not in selection and "syntheticPolicy" not in capture, "ASSET_INVALID", "Synthetic mask cannot be a real selection")
            else:
                policy = selection.get("syntheticPolicy")
                require(policy in ("full", "empty") and capture.get("syntheticPolicy") == policy,
                        "ASSET_INVALID", "Synthetic selection source must be explicit")
                with self.lock:
                    self.get(owner, selection["assetId"])
                    extrema = self.validation_cache[selection["assetId"]][3]
                    require(extrema == ((255, 255) if policy == "full" else (0, 0)),
                            "ASSET_INVALID", "Synthetic mask pixels disagree with its policy")
        else:
            require(not real and "syntheticPolicy" not in capture, "MISSING_MEDIA", "Real selection asset is missing")
        policy = manifest["selection"]["missingPolicy"]
        require(real or policy != "error", "MISSING_MEDIA", "This function requires a real selection")
        # For no selection the node applies the function's policy, even if the adapter supplied a synthetic PNG.
        return {"images": images, "selection": selection, "capture": capture,
                "missingPolicy": policy, "runtime": {"width": main["width"], "height": main["height"],
                    "imageCount": len(images), "hasSelection": real, "documentId": main["documentId"],
                    "sourceBounds": main["sourceBounds"], "placementBounds": main["placementBounds"]}}
