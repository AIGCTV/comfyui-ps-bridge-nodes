"""Contract 3: the only supported public bridge protocol."""
from __future__ import annotations
from .json_codec import digest

BRIDGE_PROTOCOL_VERSION = 2
BRIDGE_CONTRACT_VERSION = 3
BRIDGE_MANIFEST_VERSION = 1
BRIDGE_PARAMETER_SYNC_VERSION = 2
BRIDGE_TEST_MODE_VERSION = 3
BRIDGE_PACKAGE_VERSION = "0.3.2"
BRIDGE_MAX_IMAGES = 6
BRIDGE_MAX_BATCH_COUNT = 4
BRIDGE_MAX_SEED = 9007199254740991
BRIDGE_MAX_MESSAGE_BYTES = 2 * 1024 * 1024
BRIDGE_MAX_ASSET_BYTES = 64 * 1024 * 1024
BRIDGE_MAX_PIXELS = 64 * 1024 * 1024
BRIDGE_MAX_RESULTS = 64
BRIDGE_SUPPORTED_ENCODINGS = ("json",)
PROVIDERS = ("comfy_bridge", "local_comfy_api")
SLOTS = ("main", "ref1", "ref2", "ref3", "ref4", "ref5")
NODE_CATEGORY = "🔷PS Vplugins"
PARAMETER_NODES = ("VP_Seed", "VP_Slider", "VP_Prompt", "VP_Batch")
RUNTIME_NODES = ("VP_Image", "VP_SendToPS")
RETIRED_NODES = {"VP_Strings", "VP_Mask", "VP_RunInfo", "VP_Boolean", "VP_Enum"}
LEGACY_NODES = {"Adv_Request", "Adv_SendToPS", "Seed (PS Plugin)", "Float (PS Plugin)",
                "Strings (PS Plugin)", "Images (PS Plugin)", "ComfyUIToPhotoshop",
                "Photoshop", "PhotoshopPlugin", "🔹SendTo Photoshop Plugin"} | RETIRED_NODES

def _node(inputs, outputs, singleton=False):
    return {"inputs": inputs, "outputs": [
        {"index": i, "name": name, "type": kind} for i, (name, kind) in enumerate(outputs)
    ], "singleton": singleton}

NODE_CATALOG = {
    "VP_Seed": _node({"param_id": "STRING", "label": "STRING", "value": "INT", "mode": "COMBO"}, [("seed", "INT")], True),
    "VP_Slider": _node({"value": "FLOAT", "min": "FLOAT", "max": "FLOAT", "step": "FLOAT", "param_id": "STRING", "label": "STRING", "source_json": "STRING"}, [("value", "FLOAT")], True),
    "VP_Prompt": _node({"text": "STRING", "param_id": "STRING", "label": "STRING", "source_json": "STRING"}, [("text", "STRING")], True),
    "VP_Batch": _node({"value": "INT"}, [("count", "INT")], True),
    "VP_Image": _node({"slot": "COMBO", "required": "BOOLEAN", "file_name": "STRING", "selection_file": "STRING", "request_id": "STRING"},
                          [("RGB", "IMAGE"), ("ALPHA", "MASK"), ("MASK", "MASK"), ("width", "INT"), ("height", "INT")]),
    "VP_SendToPS": _node({"images": "IMAGE", "alpha": "MASK", "result_id": "STRING", "filename_prefix": "STRING", "request_id": "STRING"}, [("RGB", "IMAGE"), ("ALPHA", "MASK")]),
}

def contract_capabilities():
    return {"protocolVersion": 2, "contractVersion": 3, "manifestVersion": 1,
            "parameterSyncVersion": 2, "testModeVersion": BRIDGE_TEST_MODE_VERSION, "bridgePackageVersion": BRIDGE_PACKAGE_VERSION,
            "nodeCatalog": NODE_CATALOG, "nodeCatalogSha256": digest(NODE_CATALOG),
            "limits": {"maxImages": 6, "maxReferences": 5, "count": {"min": 1, "max": 4},
                       "maxSeed": BRIDGE_MAX_SEED, "maxResults": BRIDGE_MAX_RESULTS,
                       "maxMessageBytes": BRIDGE_MAX_MESSAGE_BYTES, "maxAssetBytes": BRIDGE_MAX_ASSET_BYTES,
                       "maxPixels": BRIDGE_MAX_PIXELS, "testHeartbeatSeconds": 90,
                       "sessionLifetime": "serverEpoch", "runRetention": "untilExplicitRemoval"},
            "capabilities": {"apiOnly": True, "immutableRuns": True, "singleSubmitOwner": True,
                             "featureSessions": True, "testSessions": True, "legacyWorkflows": False}}
