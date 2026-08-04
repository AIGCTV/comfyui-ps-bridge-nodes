import unittest
import base64
import json
import sys
from tempfile import TemporaryDirectory
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ps_bridge.storage as storage
from ps_bridge.security import safe_png_filename, validate_workflow_id
from ps_bridge.storage import (
    adv_request_from_payload,
    image_from_payload,
    image_from_raw_rgba,
    image_slot_id,
    is_api_prompt_workflow,
    mask_image_from_payload,
    merge_slots,
    normalize_slots_for_payload,
)


class SecurityTests(unittest.TestCase):
    def test_workflow_id_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            validate_workflow_id("../bad")

    def test_safe_png_filename_is_deterministic(self):
        self.assertEqual(safe_png_filename("IMAGE_1"), safe_png_filename("IMAGE_1"))
        self.assertTrue(safe_png_filename("IMAGE_1").endswith(".png"))


class ImageConversionTests(unittest.TestCase):
    def _encoded_image(self, mode="RGB", image_format="PNG"):
        image = Image.new(mode, (2, 1), (255, 0, 0, 255) if mode == "RGBA" else (255, 0, 0))
        buffer = BytesIO()
        image.save(buffer, format=image_format)
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    def test_rgba_bytes_to_image(self):
        image = image_from_raw_rgba(2, 1, bytes([255, 0, 0, 255, 0, 0, 255, 128]))
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.size, (2, 1))
        self.assertEqual(image.getpixel((1, 0)), (0, 0, 255, 128))

    def test_jpeg_base64_to_image(self):
        image = image_from_payload({"width": 2, "height": 1, "jpeg": self._encoded_image(image_format="JPEG")})
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.size, (2, 1))

    def test_data_url_png_to_image(self):
        encoded = self._encoded_image(mode="RGBA", image_format="PNG")
        image = image_from_payload({"image": f"data:image/png;base64,{encoded}"})
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.size, (2, 1))
        self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 255))

    def test_png_mask_from_generic_mask_key(self):
        encoded = self._encoded_image(mode="RGB", image_format="PNG")
        mask = mask_image_from_payload({"mask": f"data:image/png;base64,{encoded}"}, 2, 1, full_default=False)
        self.assertEqual(mask.mode, "L")
        self.assertEqual(mask.size, (2, 1))

    def test_mask_source_bounds_paste(self):
        mask = mask_image_from_payload(
            {
                "mask": bytes([255, 128]),
                "source_bounds": {"left": 1, "top": 1, "right": 3, "bottom": 2},
            },
            4,
            3,
            full_default=False,
        )
        self.assertEqual(mask.getpixel((0, 0)), 0)
        self.assertEqual(mask.getpixel((1, 1)), 255)
        self.assertEqual(mask.getpixel((2, 1)), 128)


class BridgeStateTests(unittest.TestCase):
    def test_image_slot_id_accepts_current_ids_and_labels(self):
        self.assertEqual(image_slot_id("IMAGE_1"), "IMAGE_1")
        self.assertEqual(image_slot_id("Image 2"), "IMAGE_2")
        self.assertEqual(image_slot_id("IMAGE_6"), "IMAGE_6")

    def test_normalize_slots_for_payload_keeps_explicit_protocol_slots(self):
        slots = normalize_slots_for_payload(
            {
                "slots": {
                    "prompt": {"prompt": "a cat"},
                    "seed": {"seed": 123},
                    "float": {"strength": 0.65},
                    "int": {"batch_count": 3},
                },
            },
            {},
        )
        self.assertEqual(slots["prompt"], {"prompt": "a cat"})
        self.assertEqual(slots["seed"], {"seed": 123})
        self.assertEqual(slots["float"], {"strength": 0.65})
        self.assertEqual(slots["int"], {"batch_count": 3})

    def test_merge_slots_update_keeps_unspecified_values(self):
        slots = merge_slots(
            {
                "prompt": {"prompt": "old"},
                "seed": {"seed": 11},
                "int": {"batch_count": 1},
                "float": {"strength": 0.5},
            },
            {
                "prompt": {"prompt": "new"},
                "float": {"strength": 0.8},
            },
        )
        self.assertEqual(slots["prompt"], {"prompt": "new"})
        self.assertEqual(slots["seed"], {"seed": 11})
        self.assertEqual(slots["int"], {"batch_count": 1})
        self.assertEqual(slots["float"], {"strength": 0.8})

    def test_is_api_prompt_workflow_rejects_graph_workflows(self):
        self.assertTrue(is_api_prompt_workflow({"1": {"class_type": "Adv_Request", "inputs": {}}}))
        self.assertFalse(is_api_prompt_workflow({"nodes": [{"type": "Adv_Request"}]}))

    def test_execution_mode_ignores_non_execution_mode_alias(self):
        self.assertEqual(storage.execution_mode_for_payload({"mode": "inpaint"}, "roundtrip"), "auto")
        self.assertEqual(storage.execution_mode_for_payload({"mode": "current_graph"}, "roundtrip"), "current_graph")
        with self.assertRaises(ValueError):
            storage.execution_mode_for_payload({"execution_mode": "inpaint"}, "roundtrip")


class AdvRequestTests(unittest.TestCase):
    def test_adv_request_uses_current_request_object(self):
        request = adv_request_from_payload(
            {
                "adv_request": {
                    "image_count": 3,
                    "prompt": "a cat",
                    "resolution": "2k",
                    "strength": 0.65,
                    "batch_count": 2,
                    "seed": 1379,
                    "params_json": {"model": "example"},
                },
            }
        )
        self.assertEqual(request["image_count"], 3)
        self.assertEqual(request["prompt"], "a cat")
        self.assertEqual(request["resolution"], "2k")
        self.assertEqual(request["strength"], 0.65)
        self.assertEqual(request["batch_count"], 2)
        self.assertEqual(request["seed"], 1379)
        self.assertEqual(json.loads(request["params_json"]), {"model": "example"})

    def test_adv_request_defaults_are_current_node_defaults(self):
        request = adv_request_from_payload({})
        self.assertEqual(request["image_count"], 1)
        self.assertEqual(request["prompt"], "")
        self.assertEqual(request["resolution"], "1k")
        self.assertEqual(request["strength"], 0.65)
        self.assertEqual(request["batch_count"], 1)
        self.assertEqual(request["seed"], 42)
        self.assertEqual(request["params_json"], "{}")

    def test_adv_request_clamps_current_numeric_fields(self):
        high = adv_request_from_payload(
            {"adv_request": {"image_count": 99, "batch_count": 54, "seed": -1, "strength": 8}}
        )
        low = adv_request_from_payload({"adv_request": {"image_count": 0, "batch_count": 0, "strength": -2}})
        self.assertEqual(high["image_count"], 6)
        self.assertEqual(high["batch_count"], 4)
        self.assertEqual(high["seed"], 0)
        self.assertEqual(high["strength"], 1.0)
        self.assertEqual(low["image_count"], 1)
        self.assertEqual(low["batch_count"], 1)
        self.assertEqual(low["strength"], 0.0)

    def test_adv_request_rejects_infinity_and_preserves_uint64_seed(self):
        infinite = adv_request_from_payload(
            {"adv_request": {"image_count": float("inf"), "batch_count": float("inf"), "seed": float("inf")}}
        )
        precise = adv_request_from_payload({"adv_request": {"seed": 9007199254740993}})
        maximum = adv_request_from_payload({"adv_request": {"seed": 0xFFFFFFFFFFFFFFFF}})
        oversized = adv_request_from_payload({"adv_request": {"seed": 0x10000000000000000}})

        self.assertEqual(infinite["image_count"], 1)
        self.assertEqual(infinite["batch_count"], 1)
        self.assertEqual(infinite["seed"], 42)
        self.assertEqual(precise["seed"], 9007199254740993)
        self.assertEqual(maximum["seed"], 0xFFFFFFFFFFFFFFFF)
        self.assertEqual(oversized["seed"], 0xFFFFFFFFFFFFFFFF)


class IngestRunPayloadTests(unittest.TestCase):
    def _encoded_image(self, color=(255, 0, 0, 255)):
        image = Image.new("RGBA", (2, 1), color)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    def _patched_storage(self):
        temp_dir = TemporaryDirectory()
        root = Path(temp_dir.name)
        images_dir = root / "images"
        workflows_dir = root / "workflows"
        latest_path = root / "latest.json"

        def ensure_dirs():
            images_dir.mkdir(parents=True, exist_ok=True)
            workflows_dir.mkdir(parents=True, exist_ok=True)

        ensure_dirs()
        (workflows_dir / "roundtrip.json").write_text(
            json.dumps(
                {
                    "1": {"class_type": "Adv_Request", "inputs": {}},
                    "2": {"class_type": "Adv_SendToPS", "inputs": {}},
                }
            ),
            encoding="utf-8",
        )
        patches = [
            patch.object(storage, "PS_IMAGES_DIR", images_dir),
            patch.object(storage, "WORKFLOWS_DIR", workflows_dir),
            patch.object(storage, "LATEST_STATE_PATH", latest_path),
            patch.object(storage, "ensure_data_dirs", ensure_dirs),
        ]
        return temp_dir, patches, images_dir, latest_path

    def test_ingest_canonicalizes_image_slots_and_keeps_metadata_and_selection(self):
        temp_dir, patches, _images_dir, latest_path = self._patched_storage()
        with temp_dir:
            with patches[0], patches[1], patches[2], patches[3]:
                state = storage.ingest_run_payload(
                    {
                        "feature_id": "roundtrip",
                        "request_id": "task-1",
                        "images": {
                            "IMAGE_1": {
                                "width": 2,
                                "height": 1,
                                "png": self._encoded_image(),
                                "sourceBounds": {"left": 10, "top": 20, "right": 12, "bottom": 21},
                                "placementBounds": {"left": 100, "top": 200, "right": 102, "bottom": 201},
                                "document": {"id": 7, "name": "A.psd"},
                                "documentId": 7,
                            },
                            "IMAGE_2": {
                                "width": 2,
                                "height": 1,
                                "png": self._encoded_image((0, 255, 0, 255)),
                            },
                        },
                        "selection": {"width": 2, "height": 1, "mask": bytes([0, 255])},
                        "selections": {
                            "IMAGE_2": {"width": 2, "height": 1, "mask": bytes([255, 0])},
                        },
                    }
                )

                self.assertEqual(set(state["images"]), {"IMAGE_1", "IMAGE_2"})
                self.assertEqual(state["images"]["IMAGE_1"]["filename"], "IMAGE_1.png")
                self.assertEqual(state["images"]["IMAGE_1"]["source_bounds"], {"left": 10, "top": 20, "right": 12, "bottom": 21})
                self.assertEqual(state["images"]["IMAGE_1"]["placement_bounds"], {"left": 100, "top": 200, "right": 102, "bottom": 201})
                self.assertEqual(state["images"]["IMAGE_1"]["document"], {"id": 7, "name": "A.psd"})
                self.assertEqual(state["images"]["IMAGE_1"]["targetDocumentId"], 7)
                self.assertEqual(state["selection"]["slot_id"], "IMAGE_1")
                self.assertEqual(set(state["selections"]), {"IMAGE_1", "IMAGE_2"})

                persisted = json.loads(latest_path.read_text(encoding="utf-8"))
                self.assertEqual(persisted["selection"]["slot_id"], "IMAGE_1")
                self.assertEqual(set(persisted["selections"]), {"IMAGE_1", "IMAGE_2"})

    def test_ingest_uses_fixed_slot_files_and_clears_stale_reference_slots(self):
        temp_dir, patches, images_dir, _latest_path = self._patched_storage()
        with temp_dir:
            with patches[0], patches[1], patches[2], patches[3]:
                storage.ingest_run_payload(
                    {
                        "feature_id": "roundtrip",
                        "images": {
                            "IMAGE_1": {"width": 2, "height": 1, "png": self._encoded_image((255, 0, 0, 255))},
                            "IMAGE_2": {"width": 2, "height": 1, "png": self._encoded_image((0, 255, 0, 255))},
                        },
                    }
                )
                self.assertEqual(Image.open(images_dir / "IMAGE_2.png").getpixel((0, 0)), (0, 255, 0, 255))

                state = storage.ingest_run_payload(
                    {
                        "feature_id": "roundtrip",
                        "images": {
                            "IMAGE_1": {"width": 2, "height": 1, "png": self._encoded_image((0, 0, 255, 255))},
                        },
                    }
                )

                self.assertEqual(set(state["images"]), {"IMAGE_1"})
                self.assertEqual(state["images"]["IMAGE_1"]["filename"], "IMAGE_1.png")
                self.assertEqual(Image.open(images_dir / "IMAGE_1.png").getpixel((0, 0)), (0, 0, 255, 255))
                self.assertEqual(Image.open(images_dir / "IMAGE_2.png").getpixel((0, 0)), (0, 0, 0, 255))

    def test_load_bridge_image_reads_only_the_requested_current_slot(self):
        temp_dir, patches, images_dir, _latest_path = self._patched_storage()
        with temp_dir:
            with patches[0], patches[1], patches[2], patches[3]:
                image_1_filename = "IMAGE_1.png"
                Image.new("RGBA", (1, 1), (255, 0, 0, 255)).save(images_dir / image_1_filename)
                storage.save_state(
                    {
                        "images": {
                            "IMAGE_1": {
                                "filename": image_1_filename,
                                "width": 1,
                                "height": 1,
                                "mode": "RGBA",
                            }
                        },
                        "selection": None,
                    }
                )

                def fake_pil_to_tensors(image, selection=None):
                    return {"pixel": image.getpixel((0, 0)), "size": image.size}

                with patch.object(storage, "pil_to_tensors", fake_pil_to_tensors):
                    image_1 = storage.load_bridge_image("IMAGE_1", 1, 1)
                    image_2 = storage.load_bridge_image("IMAGE_2", 1, 1)

                self.assertEqual(image_1["pixel"], (255, 0, 0, 255))
                self.assertEqual(image_2["pixel"], (0, 0, 0, 255))


if __name__ == "__main__":
    unittest.main()
