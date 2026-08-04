import importlib.util
import inspect
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "_ps_bridge_nodes_contract_test"


class _Field:
    def __init__(self, kind, direction, name=None, **options):
        self.kind = kind
        self.direction = direction
        self.name = name
        for key, value in options.items():
            setattr(self, key, value)


class _DataType:
    def __init__(self, kind):
        self.kind = kind

    def Input(self, name, **options):
        return _Field(self.kind, "input", name, **options)

    def Output(self, name=None, **options):
        return _Field(self.kind, "output", name, **options)


class _Schema:
    def __init__(self, *, node_id, inputs, outputs, **options):
        self.node_id = node_id
        self.inputs = inputs
        self.outputs = outputs
        for key, value in options.items():
            setattr(self, key, value)


class _NodeOutput:
    def __init__(self, *values, ui=None):
        self.values = values
        self.ui = ui


def _install_runtime_stubs():
    folder_paths = types.ModuleType("folder_paths")
    folder_paths.get_temp_directory = lambda: "."
    folder_paths.get_input_directory = lambda: "."
    folder_paths.get_annotated_filepath = lambda filename: filename
    sys.modules["folder_paths"] = folder_paths

    comfy_api = types.ModuleType("comfy_api")
    latest = types.ModuleType("comfy_api.latest")

    class IO:
        ComfyNode = object
        Schema = _Schema
        NodeOutput = _NodeOutput
        FolderType = types.SimpleNamespace(temp="temp")
        Hidden = types.SimpleNamespace(prompt="prompt", extra_pnginfo="extra_pnginfo")
        Image = _DataType("IMAGE")
        Mask = _DataType("MASK")
        String = _DataType("STRING")
        Float = _DataType("FLOAT")
        Int = _DataType("INT")

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


def _load_package():
    _install_runtime_stubs()
    spec = importlib.util.spec_from_file_location(
        PACKAGE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load PS Bridge node package for contract tests")
    package = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE_NAME] = package
    spec.loader.exec_module(package)
    return package


PACKAGE = _load_package()


class NodeRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_extension_registers_exactly_the_two_current_nodes(self):
        extension = await PACKAGE.comfy_entrypoint()
        node_classes = await extension.get_node_list()
        schemas = [node_class.define_schema() for node_class in node_classes]

        self.assertEqual(node_classes, [PACKAGE.AdvRequest, PACKAGE.AdvSendToPS])
        self.assertEqual([schema.node_id for schema in schemas], ["Adv_Request", "Adv_SendToPS"])
        self.assertEqual(
            [schema.display_name for schema in schemas],
            ["PS Bridge Send To ComfyUI", "PS Bridge Send To Photoshop"],
        )


class AdvRequestContractTests(unittest.TestCase):
    def test_non_finite_strength_falls_back_to_current_default(self):
        nodes_module = sys.modules[PACKAGE.AdvRequest.__module__]

        self.assertEqual(nodes_module._coerce_float(float("nan"), 0.65), 0.65)
        self.assertEqual(nodes_module._coerce_float(float("inf"), 0.65), 0.65)
        self.assertEqual(nodes_module._coerce_int(float("inf"), 42), 42)

    def test_schema_has_only_current_parameters_and_defaults(self):
        schema = PACKAGE.AdvRequest.define_schema()
        inputs = {field.name: field for field in schema.inputs}

        self.assertEqual(
            list(inputs),
            ["image_count", "prompt", "resolution", "strength", "batch_count", "seed"],
        )
        self.assertEqual(
            {name: field.default for name, field in inputs.items()},
            {
                "image_count": 1,
                "prompt": "",
                "resolution": "1k",
                "strength": 0.65,
                "batch_count": 1,
                "seed": 42,
            },
        )
        self.assertEqual((inputs["image_count"].min, inputs["image_count"].max), (1, 6))
        self.assertEqual((inputs["strength"].min, inputs["strength"].max), (0.0, 1.0))
        self.assertEqual((inputs["batch_count"].min, inputs["batch_count"].max), (1, 4))
        self.assertTrue(all(field.socketless for field in inputs.values()))
        self.assertTrue(inputs["seed"].control_after_generate)

    def test_schema_output_order_matches_current_workflow_contract(self):
        schema = PACKAGE.AdvRequest.define_schema()

        self.assertEqual(
            [field.name for field in schema.outputs],
            [
                "image_1",
                "image_2",
                "image_3",
                "image_4",
                "image_5",
                "image_6",
                "mask",
                "prompt",
                "resolution",
                "strength",
                "batch_count",
                "seed",
                "width",
                "height",
                "params_json",
                "request_json",
            ],
        )

    def test_execute_signature_has_no_removed_send_flag(self):
        parameters = inspect.signature(PACKAGE.AdvRequest.execute).parameters

        self.assertNotIn("send_to_ps", parameters)
        self.assertEqual(
            list(parameters)[:6],
            ["image_count", "prompt", "resolution", "strength", "batch_count", "seed"],
        )


class AdvSendToPSContractTests(unittest.TestCase):
    def test_send_node_is_the_current_output_node(self):
        schema = PACKAGE.AdvSendToPS.define_schema()

        self.assertEqual(schema.node_id, "Adv_SendToPS")
        self.assertEqual(schema.display_name, "PS Bridge Send To Photoshop")
        self.assertEqual([field.name for field in schema.inputs], ["image", "alpha", "filename_prefix"])
        self.assertEqual(schema.outputs, [])
        self.assertTrue(schema.is_output_node)


if __name__ == "__main__":
    unittest.main()
