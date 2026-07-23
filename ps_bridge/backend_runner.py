from __future__ import annotations

import copy
from typing import Any

from aiohttp import ClientSession

from . import storage


SEND_NODE_CLASSES = {"Adv_SendToPS", "PSBridgeSendToPS"}
VPLUGINS_REQUEST_CLASS = "VpluginsRequest"
ADV_REQUEST_CLASS = "Adv_Request"


class BackendRunnerError(RuntimeError):
    pass


def _api_prompt_entries(prompt: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (node_id, node)
        for node_id, node in prompt.items()
        if isinstance(node, dict) and isinstance(node.get("class_type"), str)
    ]


def _sync_api_prompt_numeric_node(node: dict[str, Any]) -> None:
    class_type = node.get("class_type")
    inputs = node.setdefault("inputs", {})
    if class_type == "PSBridgeFloat":
        inputs["fallback"] = storage.constrain_float_slot_value(
            inputs.get("fallback"),
            inputs.get("fallback"),
            inputs.get("min_value"),
            inputs.get("max_value"),
            inputs.get("step"),
        )
    elif class_type == "PSBridgeInt":
        inputs["fallback"] = storage.constrain_int_slot_value(
            inputs.get("fallback"),
            inputs.get("fallback"),
            inputs.get("min_value"),
            inputs.get("max_value"),
            inputs.get("step"),
        )


def _apply_slots_to_api_prompt(prompt: dict[str, Any], slots: dict[str, Any], request_id: str) -> None:
    for _node_id, node in _api_prompt_entries(prompt):
        class_type = node.get("class_type")
        inputs = node.setdefault("inputs", {})
        if class_type in SEND_NODE_CLASSES:
            inputs["request_id"] = request_id
            continue
        group = storage.SLOT_NODE_GROUPS.get(str(class_type))
        if not group:
            continue
        slot_id = str(inputs.get("slot_id") or "")
        if slot_id and slot_id in (slots.get(group) or {}):
            inputs["fallback"] = slots[group][slot_id]
        _sync_api_prompt_numeric_node(node)


def _apply_adv_request_to_api_prompt(prompt: dict[str, Any], state: dict[str, Any]) -> None:
    request = state.get("adv_request")
    if not isinstance(request, dict):
        request = {}
    for _node_id, node in _api_prompt_entries(prompt):
        if node.get("class_type") != ADV_REQUEST_CLASS:
            continue
        inputs = node.setdefault("inputs", {})
        inputs["image_count"] = request.get("image_count", 1)
        inputs["prompt"] = request.get("prompt", "")
        inputs["resolution"] = request.get("resolution", "1k")
        inputs["strength"] = request.get("strength", 0.65)
        inputs["batch_count"] = request.get("batch_count", 1)
        inputs["seed"] = request.get("seed", 42)
        inputs["params_json"] = request.get("params_json", "{}")
        inputs.pop("send_to_ps", None)
        inputs.pop("sendToPs", None)
        for index in range(1, storage.ADV_REQUEST_MAX_IMAGES + 1):
            inputs[f"image_{index}_file"] = ""
        inputs["mask_image_file"] = ""


def _apply_vplugins_request_to_api_prompt(prompt: dict[str, Any], state: dict[str, Any]) -> None:
    request = state.get("vplugins_request")
    if not isinstance(request, dict):
        request = {}
    for _node_id, node in _api_prompt_entries(prompt):
        if node.get("class_type") != VPLUGINS_REQUEST_CLASS:
            continue
        inputs = node.setdefault("inputs", {})
        inputs["main_image"] = request.get("main_image", "")
        inputs["mask_image"] = request.get("mask_image", "")
        inputs["prompt"] = request.get("prompt", "")
        inputs["params_json"] = request.get("params_json", "{}")


def _has_send_node(prompt: dict[str, Any]) -> bool:
    return any(node.get("class_type") in SEND_NODE_CLASSES for _node_id, node in _api_prompt_entries(prompt))


def patched_api_prompt_for_state(workflow: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    if not storage.is_api_prompt_workflow(workflow):
        raise BackendRunnerError("Backend API workflow execution requires an API prompt workflow JSON.")

    prompt = copy.deepcopy(workflow)
    request_id = str(state.get("request_id") or "")
    _apply_slots_to_api_prompt(prompt, state.get("slots") or {}, request_id)
    _apply_adv_request_to_api_prompt(prompt, state)
    _apply_vplugins_request_to_api_prompt(prompt, state)

    if not _has_send_node(prompt):
        raise BackendRunnerError("Workflow is missing Adv_SendToPS or PSBridgeSendToPS, so generated images cannot be returned to Photoshop.")
    return prompt


async def queue_api_workflow_for_state(
    *,
    feature_id: str,
    state: dict[str, Any],
    base_url: str,
    client_id: str = "ps-bridge-backend",
) -> dict[str, Any]:
    if not base_url:
        raise BackendRunnerError("Unable to submit backend workflow: missing ComfyUI base URL.")
    workflow = storage.migrated_workflow_for_feature(feature_id)
    prompt = patched_api_prompt_for_state(workflow, state)
    body = {
        "client_id": client_id,
        "prompt": prompt,
        "extra_data": {"extra_pnginfo": {"workflow": prompt}},
    }
    url = f"{base_url.rstrip('/')}/prompt"
    async with ClientSession() as session:
        async with session.post(url, json=body) as response:
            if response.status >= 400:
                try:
                    details = await response.json()
                except Exception:
                    details = await response.text()
                raise BackendRunnerError(f"ComfyUI /prompt failed ({response.status}): {details}")
            try:
                result = await response.json()
            except Exception as exc:
                raise BackendRunnerError("ComfyUI /prompt returned invalid JSON") from exc
    return {
        "request_id": state.get("request_id"),
        "feature_id": feature_id,
        "execution_mode": storage.EXECUTION_MODE_API_WORKFLOW,
        "status": "queued_backend",
        "result": result,
    }
