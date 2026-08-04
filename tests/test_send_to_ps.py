import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _install_runtime_stubs() -> None:
    if "folder_paths" not in sys.modules:
        folder_paths = types.ModuleType("folder_paths")
        folder_paths.get_temp_directory = lambda: "."
        folder_paths.get_input_directory = lambda: "."
        folder_paths.get_annotated_filepath = lambda filename: filename
        sys.modules["folder_paths"] = folder_paths

    if "comfy_api.latest" not in sys.modules:
        comfy_api = types.ModuleType("comfy_api")
        latest = types.ModuleType("comfy_api.latest")

        class NodeOutput:
            def __init__(self, *values, ui=None):
                self.values = values
                self.ui = ui

        class IO:
            ComfyNode = object
            FolderType = types.SimpleNamespace(temp="temp")

        IO.NodeOutput = NodeOutput

        class SavedResult:
            def __init__(self, filename, subfolder, type):
                self.filename = filename
                self.subfolder = subfolder
                self.type = type

        class SavedImages:
            def __init__(self, images):
                self.images = images

        latest.ComfyExtension = object
        latest.io = IO
        latest.ui = types.SimpleNamespace(SavedResult=SavedResult, SavedImages=SavedImages)
        comfy_api.latest = latest
        sys.modules["comfy_api"] = comfy_api
        sys.modules["comfy_api.latest"] = latest

    if importlib.util.find_spec("aiohttp") is None and "aiohttp" not in sys.modules:
        aiohttp = types.ModuleType("aiohttp")
        aiohttp.WSMsgType = types.SimpleNamespace(TEXT="TEXT", BINARY="BINARY", ERROR="ERROR")
        aiohttp.web = types.SimpleNamespace(WebSocketResponse=object)
        sys.modules["aiohttp"] = aiohttp


try:
    import torch
except ImportError:  # pragma: no cover - depends on local ComfyUI dev environment
    torch = None


_install_runtime_stubs()

from ps_bridge import manager as manager_module

if torch is not None:
    from ps_bridge import nodes
else:  # pragma: no cover
    nodes = None


class ManagerRenderResultTests(unittest.TestCase):
    def test_strip_inline_images_for_file_ref_removes_large_fields_by_default(self):
        payload = {
            "images": [
                {
                    "filename": "result.png",
                    "image": b"png",
                    "png": "base64",
                    "base64": "base64",
                    "image_png": "base64",
                }
            ]
        }

        with patch.dict(os.environ, {"PS_BRIDGE_RENDER_INLINE_IMAGE": "0"}, clear=False):
            stripped = manager_module.strip_inline_images_for_file_ref(payload)

        image = stripped["images"][0]
        self.assertEqual(image["filename"], "result.png")
        for field in ("image", "png", "base64", "image_png"):
            self.assertNotIn(field, image)

    def test_strip_inline_images_for_file_ref_keeps_optional_inline_data_when_enabled(self):
        payload = {"images": [{"filename": "result.png", "image": b"png"}]}

        with patch.dict(os.environ, {"PS_BRIDGE_RENDER_INLINE_IMAGE": "1"}, clear=False):
            kept = manager_module.strip_inline_images_for_file_ref(payload)

        self.assertIn("image", kept["images"][0])


@unittest.skipIf(torch is None, "torch is required for Adv_SendToPS protocol tests")
class SendToPSProtocolTests(unittest.TestCase):
    def _run_send_to_ps(self, pil_images, state, temp_dir, inline=False):
        captured = {}

        def capture(payload):
            captured["payload"] = payload

        env_value = "1" if inline else "0"
        with patch.dict(os.environ, {"PS_BRIDGE_RENDER_INLINE_IMAGE": env_value}, clear=False):
            with patch.object(nodes.folder_paths, "get_temp_directory", return_value=str(temp_dir)):
                with patch.object(nodes.storage, "load_state", return_value=state):
                    with patch.object(nodes.manager, "send_render_result_from_thread", side_effect=capture):
                        with patch.object(nodes, "_tensor_to_rgba_images", return_value=pil_images):
                            nodes._send_images_to_ps(object(), None, "PSBridge", "")
        return captured["payload"]

    def test_send_to_ps_defaults_to_file_ref_payload_without_inline_image(self):
        image = Image.new("RGBA", (3, 2), (255, 255, 255, 255))
        state = {
            "request_id": "task-001",
            "images": {
                "IMAGE_1": {
                    "placementBounds": {"left": 10, "top": 20, "right": 13, "bottom": 22},
                    "documentId": 123,
                    "document": {"id": 123, "name": "design.psd"},
                }
            },
        }

        with TemporaryDirectory() as root:
            payload = self._run_send_to_ps([image], state, Path(root))

        self.assertEqual(payload["request_id"], "task-001")
        self.assertEqual(payload["transport"], "file_ref")
        self.assertEqual(len(payload["images"]), 1)
        result = payload["images"][0]
        self.assertEqual(result["request_id"], "task-001")
        self.assertEqual(result["transport"], "file_ref")
        self.assertEqual(result["subfolder"], "")
        self.assertEqual(result["type"], "temp")
        self.assertTrue(result["filename"].endswith(".png"))
        self.assertIn("/view?filename=", result["url"])
        self.assertIn("&subfolder=&type=temp", result["url"])
        self.assertEqual(result["mime_type"], "image/png")
        self.assertEqual(result["width"], 3)
        self.assertEqual(result["height"], 2)
        self.assertEqual(result["source_bounds"], {"left": 0, "top": 0, "right": 3, "bottom": 2})
        self.assertEqual(result["placement_bounds"], {"left": 10, "top": 20, "right": 13, "bottom": 22})
        self.assertEqual(result["targetDocumentId"], 123)
        self.assertEqual(result["document"], {"id": 123, "name": "design.psd"})
        for field in ("image", "png", "base64", "image_png"):
            self.assertNotIn(field, result)

    def test_send_to_ps_keeps_request_id_on_each_image_for_batches(self):
        images = [Image.new("RGBA", (2, 2), (255, 255, 255, 255)) for _ in range(4)]
        state = {"request_id": "task-batch", "images": {}}

        with TemporaryDirectory() as root:
            payload = self._run_send_to_ps(images, state, Path(root))

        self.assertEqual(len(payload["images"]), 4)
        self.assertTrue(all(result["request_id"] == "task-batch" for result in payload["images"]))
        self.assertTrue(all(result["type"] == "temp" for result in payload["images"]))

    def test_send_to_ps_optional_inline_mode_can_return_image_bytes(self):
        image = Image.new("RGBA", (1, 1), (255, 255, 255, 255))
        state = {"request_id": "task-inline", "images": {}}

        with TemporaryDirectory() as root:
            payload = self._run_send_to_ps([image], state, Path(root), inline=True)

        result = payload["images"][0]
        self.assertEqual(result["transport"], "file_ref")
        self.assertIsInstance(result["image"], bytes)


if __name__ == "__main__":
    unittest.main()
