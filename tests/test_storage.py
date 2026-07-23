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
    constrain_float_slot_value,
    constrain_int_slot_value,
    image_from_payload,
    image_from_raw_rgba,
    image_slot_id,
    is_api_prompt_workflow,
    mask_image_from_payload,
    merge_slots,
    normalize_slots_for_payload,
    vplugins_request_from_payload,
    workflow_slot_ids_for_feature,
)


class SecurityTests(unittest.TestCase):
    def test_workflow_id_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            validate_workflow_id("../bad")

    def test_safe_png_filename_is_deterministic(self):
        self.assertEqual(safe_png_filename("MAIN"), safe_png_filename("MAIN"))
        self.assertTrue(safe_png_filename("MAIN").endswith(".png"))


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


class SlotTests(unittest.TestCase):
    def test_image_slot_id_accepts_labels_and_legacy_main(self):
        self.assertEqual(image_slot_id("Image 2"), "IMAGE_2")
        self.assertEqual(image_slot_id("MAIN"), "IMAGE_1")
        self.assertEqual(image_slot_id("6"), "IMAGE_6")

    def test_image_slot_id_maps_reference_aliases_after_main(self):
        self.assertEqual(image_slot_id("REF_1"), "IMAGE_2")
        self.assertEqual(image_slot_id("REF_2"), "IMAGE_3")
        self.assertEqual(image_slot_id("reference 3"), "IMAGE_4")

    def test_numeric_constraints_match_frontend_rules(self):
        self.assertEqual(constrain_float_slot_value("0.26", 0, 0, 1, 0.1), 0.3)
        self.assertEqual(constrain_float_slot_value("bad", 0.25, 1, 0, 0.1), 0.3)
        self.assertEqual(constrain_int_slot_value("7.6", 0, 0, 20, 5), 10)
        self.assertEqual(constrain_int_slot_value("bad", 12, 20, 0, 5), 10)
        self.assertEqual(constrain_int_slot_value(1024, 30, -1000000, 1000000, 1), 1024)
        self.assertEqual(constrain_float_slot_value(7.5, 0.5, -100000.0, 100000.0, 0.01), 7.5)

    def test_workflow_slot_ids_extracts_api_prompt_slots(self):
        workflow = {
            "1": {
                "class_type": "PSBridgePrompt",
                "inputs": {"slot_id": "positive", "fallback": ""},
            },
            "2": {
                "class_type": "PSBridgeSeed",
                "inputs": {"slot_id": "MAIN", "fallback": 42},
            },
            "3": {
                "class_type": "PSBridgeInt",
                "inputs": {"slot_id": "steps", "fallback": 20},
            },
        }
        with TemporaryDirectory() as temp_dir, patch.object(storage, "WORKFLOWS_DIR", Path(temp_dir)):
            (Path(temp_dir) / "TEST.json").write_text(
                json.dumps(workflow),
                encoding="utf-8",
            )
            slots = workflow_slot_ids_for_feature("TEST")
        self.assertEqual(slots["prompt"], ["positive"])
        self.assertEqual(slots["seed"], ["MAIN"])
        self.assertEqual(slots["int"], ["steps"])

    def test_normalize_slots_for_payload_maps_single_shorthand_slot(self):
        slots = normalize_slots_for_payload(
            {"prompt": "a cat", "seed": 42, "int": 3},
            {
                "prompt": ["positive"],
                "seed": ["MAIN"],
                "float": [],
                "int": ["steps"],
                "boolean": [],
            },
        )
        self.assertEqual(slots["prompt"], {"positive": "a cat"})
        self.assertEqual(slots["seed"], {"MAIN": 42})
        self.assertEqual(slots["int"], {"steps": 3})

    def test_normalize_slots_for_payload_does_not_guess_multiple_slots(self):
        slots = normalize_slots_for_payload({"prompt": "a cat"}, {"prompt": ["positive", "negative"]})
        self.assertEqual(slots["prompt"], {})

    def test_normalize_slots_for_payload_keeps_explicit_multiple_int_slots(self):
        slots = normalize_slots_for_payload(
            {
                "slots": {
                    "seed": {"main": 123},
                    "int": {"width": 768, "height": 512, "batchCount": 3},
                },
            },
            {
                "seed": ["MAIN"],
                "int": ["width", "height", "batch_count"],
            },
        )
        self.assertEqual(slots["seed"], {"MAIN": 123})
        self.assertEqual(slots["int"]["width"], 768)
        self.assertEqual(slots["int"]["height"], 512)
        self.assertEqual(slots["int"]["batch_count"], 3)

    def test_normalize_slots_for_payload_maps_settings_and_params_by_slot_id(self):
        slots = normalize_slots_for_payload(
            {
                "seed": 99,
                "settings": {"width": 1024, "height": 768, "batchCount": 2, "denoise": 0.65},
                "params": {"cfg": 7.5},
            },
            {
                "seed": ["MAIN"],
                "float": ["denoise", "cfg"],
                "int": ["width", "height", "batch_count"],
            },
        )
        self.assertEqual(slots["seed"], {"MAIN": 99})
        self.assertEqual(slots["int"]["width"], 1024)
        self.assertEqual(slots["int"]["height"], 768)
        self.assertEqual(slots["int"]["batch_count"], 2)
        self.assertEqual(slots["float"]["denoise"], 0.65)
        self.assertEqual(slots["float"]["cfg"], 7.5)

    def test_normalize_slots_for_payload_prefers_explicit_slots(self):
        slots = normalize_slots_for_payload(
            {
                "slots": {"int": {"batchCount": 2, "width": 512}, "seed": {"MAIN": 111}},
                "seed": 222,
                "int": {"batch_count": 3, "width": 768},
                "settings": {"width": 1024},
            },
            {
                "seed": ["MAIN"],
                "int": ["batch_count", "width"],
            },
        )
        self.assertEqual(slots["seed"], {"MAIN": 111})
        self.assertEqual(slots["int"]["batch_count"], 2)
        self.assertEqual(slots["int"]["width"], 512)

    def test_merge_slots_update_keeps_unspecified_values(self):
        slots = merge_slots(
            {
                "prompt": {"positive": "old"},
                "seed": {"MAIN": 11},
                "int": {"width": 512, "height": 768, "batch_count": 1},
                "float": {"cfg": 7.5},
                "boolean": {"enabled": True},
            },
            {
                "prompt": {"positive": "new"},
                "int": {"width": 1024},
            },
        )
        self.assertEqual(slots["prompt"], {"positive": "new"})
        self.assertEqual(slots["seed"], {"MAIN": 11})
        self.assertEqual(slots["int"], {"width": 1024, "height": 768, "batch_count": 1})
        self.assertEqual(slots["float"], {"cfg": 7.5})
        self.assertEqual(slots["boolean"], {"enabled": True})

    def test_is_api_prompt_workflow_rejects_graph_workflows(self):
        self.assertTrue(is_api_prompt_workflow({"1": {"class_type": "PSBridgePrompt", "inputs": {}}}))
        self.assertFalse(is_api_prompt_workflow({"nodes": [{"type": "PSBridgePrompt"}]}))

    def test_execution_mode_ignores_non_execution_mode_alias(self):
        self.assertEqual(storage.execution_mode_for_payload({"mode": "inpaint"}, "roundtrip"), "auto")
        self.assertEqual(storage.execution_mode_for_payload({"mode": "current_graph"}, "roundtrip"), "current_graph")
        with self.assertRaises(ValueError):
            storage.execution_mode_for_payload({"execution_mode": "inpaint"}, "roundtrip")


class VpluginsRequestTests(unittest.TestCase):
    def test_vplugins_request_uses_prompt_and_run_params(self):
        request = vplugins_request_from_payload(
            {
                "prompt": "a cat",
                "settings": {"resolution": "1k"},
                "params": {"strength": 0.65},
                "batchCount": 2,
            }
        )
        self.assertEqual(request["main_image"], "")
        self.assertEqual(request["mask_image"], "")
        self.assertEqual(request["prompt"], "a cat")
        self.assertEqual(json.loads(request["params_json"]), {"resolution": "1k", "strength": 0.65, "batchCount": 2})

    def test_vplugins_request_falls_back_to_legacy_slots(self):
        request = vplugins_request_from_payload(
            {
                "slots": {
                    "prompt": {"positive": "a city"},
                    "seed": {"MAIN": 123},
                    "float": {"strength": 0.5},
                    "int": {"batch_count": 1},
                }
            }
        )
        self.assertEqual(request["prompt"], "a city")
        self.assertEqual(
            json.loads(request["params_json"]),
            {"seed": {"MAIN": 123}, "float": {"strength": 0.5}, "int": {"batch_count": 1}},
        )


class AdvRequestTests(unittest.TestCase):
    def test_adv_request_uses_prompt_and_run_params(self):
        request = adv_request_from_payload(
            {
                "prompt": "a cat",
                "image_count": 3,
                "settings": {"resolution": "1k"},
                "params": {"strength": 0.65},
                "batchCount": 2,
                "seed": 1379,
            }
        )
        self.assertEqual(request["image_count"], 3)
        self.assertEqual(request["prompt"], "a cat")
        self.assertEqual(request["resolution"], "1k")
        self.assertEqual(request["strength"], 0.65)
        self.assertEqual(request["batch_count"], 2)
        self.assertEqual(request["seed"], 1379)
        self.assertEqual(json.loads(request["params_json"]), {"resolution": "1k", "strength": 0.65, "batchCount": 2, "seed": 1379})

    def test_adv_request_falls_back_to_legacy_slots(self):
        request = adv_request_from_payload(
            {
                "slots": {
                    "prompt": {"positive": "a city"},
                    "seed": {"MAIN": 123},
                    "float": {"strength": 0.5},
                    "int": {"batch_count": 4},
                }
            }
        )
        self.assertEqual(request["image_count"], 1)
        self.assertEqual(request["prompt"], "a city")
        self.assertEqual(request["strength"], 0.5)
        self.assertEqual(request["batch_count"], 4)
        self.assertEqual(request["seed"], 123)

    def test_adv_request_clamps_batch_count(self):
        self.assertEqual(adv_request_from_payload({"batchCount": 54})["batch_count"], 4)
        self.assertEqual(adv_request_from_payload({"batchCount": 0})["batch_count"], 1)


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
            json.dumps({"1": {"class_type": "PSBridgePrompt", "inputs": {"slot_id": "positive"}}}),
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
                            "MAIN": {
                                "width": 2,
                                "height": 1,
                                "png": self._encoded_image(),
                                "sourceBounds": {"left": 10, "top": 20, "right": 12, "bottom": 21},
                                "placementBounds": {"left": 100, "top": 200, "right": 102, "bottom": 201},
                                "document": {"id": 7, "name": "A.psd"},
                                "documentId": 7,
                            },
                            "REF_1": {
                                "width": 2,
                                "height": 1,
                                "png": self._encoded_image((0, 255, 0, 255)),
                            },
                        },
                        "selection": {"width": 2, "height": 1, "mask": bytes([0, 255])},
                        "selections": {
                            "REF_1": {"width": 2, "height": 1, "mask": bytes([255, 0])},
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

    def test_load_bridge_image_only_falls_back_to_legacy_main_for_image_1(self):
        temp_dir, patches, images_dir, _latest_path = self._patched_storage()
        with temp_dir:
            with patches[0], patches[1], patches[2], patches[3]:
                main_filename = safe_png_filename("MAIN")
                Image.new("RGBA", (1, 1), (255, 0, 0, 255)).save(images_dir / main_filename)
                storage.save_state(
                    {
                        "images": {
                            "MAIN": {
                                "filename": main_filename,
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
