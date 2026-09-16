"""Validate and compile immutable contract-3 workflow definitions."""
from __future__ import annotations
import copy
import posixpath
from .errors import BridgeError, require
from .json_codec import loads, digest, byte_digest, canonical_bytes
from .parameters import configured_fields, normalize, validate_source, identifier
from .protocol import NODE_CATALOG, PARAMETER_NODES, RUNTIME_NODES, PROVIDERS, SLOTS, LEGACY_NODES, BRIDGE_MAX_RESULTS
from .schemas import validate_shape

ENV_FIELDS = {"comfyCoreVersion", "comfyCoreCommit", "comfyFrontendVersion", "bridgePackageVersion",
              "vpluginsVersion", "pythonVersion", "photoshopVersion", "uxpHostVersion"}

def api_prompt(raw):
    value = loads(raw) if isinstance(raw, (str, bytes)) else copy.deepcopy(raw)
    require(isinstance(value, dict) and value, "MANIFEST_INVALID", "Expected an API node object")
    if isinstance(value.get("nodes"), list):
        if any(n.get("type") in LEGACY_NODES for n in value["nodes"] if isinstance(n, dict)):
            raise BridgeError("LEGACY_WORKFLOW_UNSUPPORTED", "Recreate this workflow with VP nodes")
        raise BridgeError("MANIFEST_INVALID", "UI workflow JSON must be exported through ComfyUI graphToPrompt")
    require(all(isinstance(k, str) and isinstance(n, dict) and isinstance(n.get("class_type"), str)
                and isinstance(n.get("inputs"), dict) for k, n in value.items()),
            "MANIFEST_INVALID", "Expected node IDs mapped to class_type and inputs")
    for node_id, node in value.items():
        identifier(node_id, "nodeId")
        require(node["class_type"] not in LEGACY_NODES,
                "LEGACY_WORKFLOW_UNSUPPORTED", "Recreate this workflow with VP nodes", field=node_id)
    validate_shape("apiPrompt", value, code="MANIFEST_INVALID")
    return value

def ui_bytes(ui):
    """Preserve supplied UTF-8 bytes; object exports use the declared bundle format."""
    if isinstance(ui, bytes):
        return ui
    if isinstance(ui, str):
        return ui.encode("utf-8")
    import json
    canonical_bytes(ui)
    return (json.dumps(ui, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")

def validate_ui(ui):
    require(isinstance(ui, dict) and isinstance(ui.get("nodes"), list), "MANIFEST_INVALID", "Expected UI workflow nodes")
    counts = {}
    # Walk exported subgraphs as well as the main canvas.
    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get("type"), str) and "id" in value:
                kind = value["type"]
                require(kind not in LEGACY_NODES,
                        "LEGACY_WORKFLOW_UNSUPPORTED", "Legacy node in UI source")
                counts[kind] = counts.get(kind, 0) + 1
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(ui)
    for kind in PARAMETER_NODES:
        require(counts.get(kind, 0) <= 1, "DUPLICATE_PARAMETER_NODE", f"Only one {kind} is allowed")
    slots = []
    def images(value):
        if isinstance(value, dict):
            if value.get("type") == "VP_Image" and "id" in value:
                widgets = value.get("widgets_values", [])
                named = value.get("widgets_values_named", {})
                slot = named.get("slot") if isinstance(named, dict) else None
                if slot is None and isinstance(widgets, list) and widgets:
                    slot = widgets[0]
                require(isinstance(slot, str) and slot in SLOTS, "BINDING_INVALID", "Invalid UI image name")
                slots.append(slot)
            for item in value.values():
                images(item)
        elif isinstance(value, list):
            for item in value:
                images(item)
    images(ui)
    require(len(slots) == len(set(slots)), "DUPLICATE_IMAGE_SLOT", "Each image name may occur only once")

def validate_node_uniqueness(prompt):
    counts, slots = {}, set()
    for node in prompt.values():
        kind = node.get("class_type")
        counts[kind] = counts.get(kind, 0) + 1
        require(kind not in LEGACY_NODES, "LEGACY_WORKFLOW_UNSUPPORTED", "Recreate retired bridge nodes")
        if kind == "VP_Image":
            slot = node["inputs"].get("slot")
            require(slot in SLOTS and slot not in slots, "DUPLICATE_IMAGE_SLOT", "Each image name may occur only once")
            slots.add(slot)
    for kind in PARAMETER_NODES:
        require(counts.get(kind, 0) <= 1, "DUPLICATE_PARAMETER_NODE", f"Only one {kind} is allowed")

def definition_object(manifest, prompt):
    definition = {k: copy.deepcopy(v) for k, v in manifest.items()
                  if k not in {"definitionSha256", "apiSha256", "uiFile", "uiSha256", "apiFile", "name"}}
    api = copy.deepcopy(prompt)
    for node in api.values():
        node.pop("_meta", None)
    for param in manifest["parameters"]:
        target = param["target"]
        api[target["nodeId"]]["inputs"][target["inputName"]] = param["default"]
        if "modeTarget" in param:
            target = param["modeTarget"]
            api[target["nodeId"]]["inputs"][target["inputName"]] = param["defaultMode"]
    for target in manifest["runtimeTargets"]:
        api[target["nodeId"]]["inputs"][target["inputName"]] = ""
    return {"definition": definition, "apiDefinition": api}

def definition_digest(manifest, prompt):
    try:
        return digest(definition_object(manifest, prompt))
    except (KeyError, TypeError) as exc:
        raise BridgeError("BINDING_INVALID", "Definition contains a missing injection target") from exc

def _file_name(value):
    require(isinstance(value, str) and value and not value.startswith("/") and "\\" not in value and ":" not in value
            and all(p not in {"", ".", ".."} for p in value.split("/")),
            "MANIFEST_INVALID", "Package filenames must be relative paths")
    return posixpath.normpath(value)

def installed_catalog():
    """Read actual registered ComfyUI schemas; no network or model loading."""
    import nodes
    catalog = {}
    for name, cls in nodes.NODE_CLASS_MAPPINGS.items():
        inputs = cls.INPUT_TYPES()
        catalog[name] = {"input": inputs, "output": list(cls.RETURN_TYPES)}
    return catalog

def _bridge_node_info(kind):
    item = NODE_CATALOG[kind]
    optional = {"alpha"} if kind == "VP_SendToPS" else set()
    return {n: (t, {}) for n, t in item["inputs"].items()}, set(item["inputs"]) - optional, [o["type"] for o in item["outputs"]]

def _link(value):
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and type(value[1]) is int

def _compatible(output, expected):
    if output == "*" or expected == "*":
        return True
    return output == expected or output in str(expected).split(",")

def validate(manifest, raw_api, *, installed=None, ui=None, published=True, profile=None):
    # Profiles differ in publication evidence, not in ownership of ComfyUI's graph.
    # Bridge validates its bindings; ComfyUI validates ordinary node execution.
    profile = profile or ("publication" if published else "draft")
    require(profile in {"draft", "runtime", "publication"}, "MANIFEST_INVALID", "Unknown validation profile")
    published, executable = profile == "publication", profile != "draft"
    require(isinstance(manifest, dict), "MANIFEST_INVALID", "manifest must be an object")
    canonical_bytes(manifest)
    required = {"manifestVersion", "contractVersion", "featureId", "workflowId", "workflowVersion", "name", "providerId",
                "apiFile", "apiSha256", "definitionSha256", "parameters", "images", "selection", "results", "runtimeTargets",
                "limits", "dependencies"}
    require(required <= set(manifest) and not set(manifest) - required - {"uiFile", "uiSha256"},
            "MANIFEST_INVALID", "Manifest fields do not match contract 3")
    require(manifest["manifestVersion"] == 1 and manifest["contractVersion"] == 3,
            "CONTRACT_UNSUPPORTED", "Expected manifest 1 / contract 3")
    validate_shape("publishedManifest" if published else "runtimeManifest" if executable else "manifest", manifest, code="MANIFEST_INVALID")
    for key in ("featureId", "workflowId", "workflowVersion", "name"):
        identifier(manifest[key], key)
    require(manifest["providerId"] in PROVIDERS, "MANIFEST_INVALID", "Unknown local provider")
    _file_name(manifest["apiFile"])
    require(("uiFile" in manifest) == ("uiSha256" in manifest), "MANIFEST_INVALID", "UI file and digest must be paired")
    if "uiFile" in manifest:
        _file_name(manifest["uiFile"])
        require(isinstance(manifest["uiSha256"], str) and len(manifest["uiSha256"]) == 64,
                "MANIFEST_INVALID", "Invalid UI digest")
    raw = raw_api.encode("utf-8") if isinstance(raw_api, str) else raw_api
    require(isinstance(raw, bytes) and byte_digest(raw) == manifest["apiSha256"],
            "MANIFEST_INVALID", "API byte digest mismatch", field="apiSha256")
    prompt = api_prompt(raw)
    if ui is not None:
        raw_ui = ui_bytes(ui)
        validate_ui(loads(raw_ui))
        if "uiSha256" in manifest:
            require(byte_digest(raw_ui) == manifest["uiSha256"], "MANIFEST_INVALID", "UI byte digest mismatch", field="uiSha256")
    for key in ("parameters", "images", "results", "runtimeTargets"):
        require(isinstance(manifest[key], list), "MANIFEST_INVALID", f"{key} must be an array")
    info, counts = {}, {}
    for node_id, node in prompt.items():
        kind = node["class_type"]
        counts[kind] = counts.get(kind, 0) + 1
        if kind in NODE_CATALOG:
            info[node_id] = _bridge_node_info(kind)
    validate_node_uniqueness(prompt)
    for kind in PARAMETER_NODES:
        require(counts.get(kind, 0) <= 1, "DUPLICATE_PARAMETER_NODE", f"Only one {kind} is allowed")
    for node_id, node in prompt.items():
        if node_id not in info:
            continue
        accepted, required_inputs, outputs = info[node_id]
        require(required_inputs <= node["inputs"].keys(), "BINDING_INVALID", "Missing node input", field=node_id)
        require(node["inputs"].keys() <= accepted.keys(), "BINDING_INVALID", "Unknown node input", field=node_id)
        for name, value in node["inputs"].items():
            spec = accepted[name]
            expected, options = spec[0], spec[1] if len(spec) > 1 else {}
            if _link(value):
                source, index = value
                require(source in prompt and index >= 0, "BINDING_INVALID", "Invalid output link", field=f"{node_id}.{name}")
                # ComfyUI owns dynamic and third-party output schemas.
                if source in info:
                    require(index < len(info[source][2]), "BINDING_INVALID", "Invalid output link", field=f"{node_id}.{name}")
                    require(_compatible(info[source][2][index], expected), "BINDING_INVALID", "Linked types are incompatible", field=f"{node_id}.{name}")
                require(not (node["class_type"] in PARAMETER_NODES or
                             node["class_type"] in RUNTIME_NODES and expected not in {"IMAGE", "MASK"}),
                        "BINDING_INVALID", "Editable scalar inputs must be literals", field=f"{node_id}.{name}")
            elif isinstance(expected, str) and expected in {"IMAGE", "MASK", "MODEL", "CLIP", "VAE", "LATENT"}:
                raise BridgeError("BINDING_INVALID", "This input requires an output link", field=f"{node_id}.{name}")
            elif isinstance(expected, list):
                require(value in expected, "BINDING_INVALID", "Invalid combo value", field=f"{node_id}.{name}")
            elif isinstance(expected, str) and expected in {"INT", "FLOAT", "STRING", "BOOLEAN"}:
                valid = (type(value) in (int, float) and (expected != "INT" or value == int(value))) if expected in {"INT", "FLOAT"} else type(value) is (str if expected == "STRING" else bool)
                require(valid, "BINDING_INVALID", "Invalid literal input type", field=f"{node_id}.{name}")
                if type(value) in (int, float):
                    require(options.get("min", value) <= value <= options.get("max", value), "BINDING_INVALID", "Input outside node bounds")

    result_nodes, result_ids, orders = set(), set(), set()
    require(manifest["results"], "MANIFEST_INVALID", "At least one VP_SendToPS result is required")
    for result in manifest["results"]:
        require(isinstance(result, dict) and set(result) == {"resultId", "nodeId", "required", "order"}, "MANIFEST_INVALID", "Invalid result declaration")
        node_id = result["nodeId"]
        require(node_id in prompt and prompt[node_id]["class_type"] == "VP_SendToPS", "BINDING_INVALID", "Result must target VP_SendToPS")
        identifier(result["resultId"], "resultId")
        require(node_id not in result_nodes and result["resultId"] not in result_ids and type(result["order"]) is int
                and result["order"] >= 0 and result["order"] not in orders and type(result["required"]) is bool,
                "MANIFEST_INVALID", "Duplicate or invalid result declaration")
        require(prompt[node_id]["inputs"]["result_id"] == result["resultId"], "BINDING_INVALID", "Result ID differs from node")
        result_nodes.add(node_id); result_ids.add(result["resultId"]); orders.add(result["order"])
    require(result_nodes == {k for k, n in prompt.items() if n["class_type"] == "VP_SendToPS"},
            "BINDING_INVALID", "Every output side effect must be declared")
    expected_params = {}
    for node_id, node in prompt.items():
        configured = configured_fields(node["class_type"], node["inputs"])
        for name, index, schema in configured:
            expected_params[(node_id, name)] = schema
    seen_targets, param_ids, sources = set(), set(), set()
    for parameter in manifest["parameters"]:
        require(isinstance(parameter, dict) and isinstance(parameter.get("target"), dict)
                and set(parameter["target"]) == {"nodeId", "inputName"}, "BINDING_INVALID", "Invalid parameter target")
        target = parameter["target"]
        key = (target["nodeId"], target["inputName"])
        require(key in expected_params and key not in seen_targets, "BINDING_INVALID", "Unknown or reused parameter target")
        schema = expected_params[key]
        allowed = set(schema) | {"source", "target", "modeTarget"}
        require(set(parameter) <= allowed, "MANIFEST_INVALID", "Unexpected parameter field")
        require(all(parameter.get(k) == v for k, v in schema.items()), "BINDING_INVALID", "Parameter differs from configured node slot")
        require(prompt[key[0]]["inputs"][key[1]] == parameter["default"], "BINDING_INVALID", "Default must already be normalized")
        param_id = identifier(parameter.get("paramId"), "paramId")
        require(param_id not in param_ids, "BINDING_INVALID", "Duplicate paramId")
        validate_source(parameter)
        source = (parameter["source"]["kind"], parameter["source"]["key"])
        require(source not in sources, "DUPLICATE_PARAMETER_SOURCE", "Parameter source is already bound")
        if parameter.get("semantic") == "seed":
            require(parameter.get("modeTarget") == {"nodeId": key[0], "inputName": "mode"}, "BINDING_INVALID", "Seed mode target must be on the same node")
        else:
            require("modeTarget" not in parameter, "BINDING_INVALID", "Only Seed may bind mode")
        normalize(parameter, parameter["default"])
        seen_targets.add(key); param_ids.add(param_id); sources.add(source)
    require(seen_targets == set(expected_params), "BINDING_INVALID", "Every active slot must have a parameter")

    image_nodes, image_slots = set(), set()
    for image in manifest["images"]:
        require(isinstance(image, dict) and set(image) == {"slot", "nodeId", "required"}, "MANIFEST_INVALID", "Invalid image binding")
        node_id, slot = image["nodeId"], image["slot"]
        require(node_id in prompt and prompt[node_id]["class_type"] == "VP_Image" and node_id not in image_nodes,
                "BINDING_INVALID", "Image must bind a unique VP_Image")
        require(slot in SLOTS and slot not in image_slots and type(image["required"]) is bool and (slot != "main" or image["required"]),
                "MANIFEST_INVALID", "Invalid image slot or required flag")
        inputs = prompt[node_id]["inputs"]
        require(inputs["slot"] == slot and inputs["required"] == image["required"], "BINDING_INVALID", "Image binding differs from node")
        image_nodes.add(node_id); image_slots.add(slot)
    require(image_nodes == {k for k, n in prompt.items() if n["class_type"] == "VP_Image"}, "BINDING_INVALID", "Undeclared image node")
    refs = sorted(int(slot[3:]) for slot in image_slots if slot.startswith("ref"))
    require(refs == list(range(1, len(refs) + 1)), "MANIFEST_INVALID", "Reference slots must be contiguous")
    selection = manifest["selection"]
    require(isinstance(selection, dict) and set(selection) == {"nodeId", "missingPolicy"} and selection["missingPolicy"] in {"full", "empty", "error"},
            "MANIFEST_INVALID", "Invalid selection policy")
    masks = {k for k, n in prompt.items() if n["class_type"] == "VP_Image" and n["inputs"]["slot"] == "main"}
    require(masks == ({selection["nodeId"]} if selection["nodeId"] is not None else set()), "BINDING_INVALID", "Mask binding mismatch")
    require(selection["missingPolicy"] == "full", "BINDING_INVALID", "Six-node images use optional selection with full-image fallback")
    expected_runtime = {(node_id, name): ("runId" if name == "request_id" else "empty")
                        for node_id, node in prompt.items() if node["class_type"] in RUNTIME_NODES
                        for name in ("request_id", "file_name", "selection_file") if name in NODE_CATALOG[node["class_type"]]["inputs"]}
    runtime = {}
    for target in manifest["runtimeTargets"]:
        require(isinstance(target, dict) and set(target) == {"nodeId", "inputName", "source"}, "BINDING_INVALID", "Invalid runtime target")
        key = (target["nodeId"], target["inputName"])
        require(key in expected_runtime and key not in runtime and expected_runtime[key] == target["source"], "BINDING_INVALID", "Illegal runtime injection")
        require(prompt[key[0]]["inputs"].get(key[1]) == "", "BINDING_INVALID", "Published template contains transient input")
        runtime[key] = target["source"]
    require(runtime == expected_runtime, "BINDING_INVALID", "Runtime targets must cover every request_id/file_name")

    limits = manifest["limits"]
    require(isinstance(limits, dict) and set(limits) == {"maxImages", "maxReferences", "count", "maxResults"}
            and all(type(limits[k]) is int for k in ("maxImages", "maxReferences", "maxResults")),
            "MANIFEST_INVALID", "Invalid resource limits")
    require(1 <= limits["maxImages"] <= 6 and 0 <= limits["maxReferences"] <= 5
            and limits["maxReferences"] + 1 <= limits["maxImages"]
            and max(1, len(image_slots | {"main"})) <= limits["maxImages"] and len(refs) <= limits["maxReferences"]
            and len(result_nodes) <= limits["maxResults"] <= BRIDGE_MAX_RESULTS, "MANIFEST_INVALID", "Inconsistent resource limits")
    count = limits["count"]
    require(isinstance(count, dict) and set(count) == {"min", "max"} and all(type(v) is int for v in count.values())
            and 1 <= count["min"] <= count["max"] <= 4, "MANIFEST_INVALID", "Invalid count limits")
    for p in manifest["parameters"]:
        if p["source"] == {"kind": "canonical", "key": "count"}:
            require(count["min"] <= p["min"] <= p["max"] <= count["max"], "MANIFEST_INVALID", "Count limits disagree")
    deps = manifest["dependencies"]
    require(isinstance(deps, dict) and {"classTypes", "nodePackages", "verifiedEnvironments"} <= set(deps),
            "MANIFEST_INVALID", "Missing dependency declarations")
    require(isinstance(deps["classTypes"], list) and set(deps["classTypes"]) == set(counts)
            and len(set(deps["classTypes"])) == len(deps["classTypes"]), "MANIFEST_INVALID", "Dependency classTypes mismatch")
    require(isinstance(deps["nodePackages"], list) and any(p.get("id") == "comfyui-ps-bridge-nodes"
            and p.get("contractVersion") == 3 for p in deps["nodePackages"] if isinstance(p, dict)),
            "MANIFEST_INVALID", "Missing bridge contract dependency")
    envs = deps["verifiedEnvironments"]
    require(isinstance(envs, list) and (not published or envs), "MANIFEST_INVALID", "A verified environment is required for publication")
    for env in envs:
        require(isinstance(env, dict) and ENV_FIELDS <= set(env) and all(isinstance(env[k], str) and env[k] for k in ENV_FIELDS)
                and isinstance(env.get("testIds"), list) and env["testIds"], "MANIFEST_INVALID", "Incomplete verified environment")
    require(definition_digest(manifest, prompt) == manifest["definitionSha256"], "WORKFLOW_VERSION_MISMATCH", "Definition digest mismatch")
    return copy.deepcopy(prompt)

def compile_prompt(manifest, template, values, actual_seeds, run_id):
    prompt = copy.deepcopy(template)
    for parameter in manifest["parameters"]:
        target = parameter["target"]
        value = normalize(parameter, values[parameter["paramId"]])
        prompt[target["nodeId"]]["inputs"][target["inputName"]] = value
        if parameter.get("semantic") == "seed":
            prompt[target["nodeId"]]["inputs"][target["inputName"]] = actual_seeds[parameter["paramId"]]
            prompt[parameter["modeTarget"]["nodeId"]]["inputs"]["mode"] = "fixed"
    for target in manifest["runtimeTargets"]:
        prompt[target["nodeId"]]["inputs"][target["inputName"]] = run_id if target["source"] == "runId" else ""
    return prompt

def candidate(raw_api, metadata, *, installed=None, ui=None):
    """Build a reviewable manifest from real API inputs, never widget array positions."""
    prompt = api_prompt(raw_api)
    validate_node_uniqueness(prompt)
    for node_id, node in prompt.items():
        kind = node["class_type"]
        if kind in NODE_CATALOG:
            declared = set(NODE_CATALOG[kind]["inputs"])
            required = declared - ({"alpha"} if kind == "VP_SendToPS" else set())
            require(required <= set(node["inputs"]) <= declared, "BINDING_INVALID",
                    "Inputs do not match the six-node format; recreate retired layouts", field=node_id)
    require(isinstance(metadata, dict), "MANIFEST_INVALID", "Export metadata is required")
    if ui is not None:
        ui = loads(ui_bytes(ui))
        validate_ui(ui)
    params, images, results, runtime = [], [], [], []
    selection = {"nodeId": None, "missingPolicy": "full"}
    for node_id, node in prompt.items():
        kind, inputs = node["class_type"], node["inputs"]
        for name, _, schema in configured_fields(kind, inputs):
            source = schema.get("source", {"kind": "canonical" if kind == "VP_Seed" else "parameter",
                                           "key": "seed" if kind == "VP_Seed" else schema["paramId"]})
            param = {**schema, "source": source, "target": {"nodeId": node_id, "inputName": name}}
            if kind == "VP_Seed":
                param["modeTarget"] = {"nodeId": node_id, "inputName": "mode"}
            params.append(param)
        if kind == "VP_Image":
            images.append({"nodeId": node_id, "slot": inputs["slot"], "required": inputs["required"]})
            if inputs["slot"] == "main":
                require(selection["nodeId"] is None, "DUPLICATE_IMAGE_SLOT", "Only one main image is supported")
                selection = {"nodeId": node_id, "missingPolicy": "full"}
        elif kind == "VP_SendToPS":
            results.append({"nodeId": node_id, "resultId": inputs["result_id"], "order": len(results), "required": True})
        if kind in RUNTIME_NODES:
            for name in ("request_id", "file_name", "selection_file"):
                if name in NODE_CATALOG[kind]["inputs"]:
                    runtime.append({"nodeId": node_id, "inputName": name, "source": "runId" if name == "request_id" else "empty"})
                    inputs[name] = ""
    # Export a clean execution template: local test filenames are explicit transient fields.
    import json
    raw = (json.dumps(prompt, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    references = {image["slot"] for image in images if image["slot"] != "main"}
    count = next((p for p in params if p["source"] == {"kind": "canonical", "key": "count"}), None)
    manifest = {"manifestVersion": 1, "contractVersion": 3,
                **{k: metadata[k] for k in ("featureId", "workflowId", "workflowVersion", "name", "providerId")},
                "apiFile": "workflow.api.json", "apiSha256": byte_digest(raw), "parameters": params, "images": images,
                "selection": selection, "results": results, "runtimeTargets": runtime,
                "limits": {"maxImages": 1 + len(references), "maxReferences": len(references),
                           "count": {"min": count["min"], "max": count["max"]} if count else {"min": 1, "max": 1},
                           "maxResults": metadata.get("maxResults", max(1, len(results)) * (count["max"] if count else 1))},
                "dependencies": {"classTypes": list(dict.fromkeys(n["class_type"] for n in prompt.values())),
                                 "nodePackages": [{"id": "comfyui-ps-bridge-nodes", "contractVersion": 3}],
                                 "verifiedEnvironments": metadata.get("verifiedEnvironments", [])}}
    if ui is not None:
        ui_raw = (json.dumps(ui, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        manifest.update(uiFile="workflow.ui.json", uiSha256=byte_digest(ui_raw))
    manifest["definitionSha256"] = definition_digest(manifest, prompt)
    validate(manifest, raw, installed=installed, ui=ui, published=False)
    return {"manifest": manifest, "api": raw.decode("utf-8"), "ui": ui}
