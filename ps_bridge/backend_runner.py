from __future__ import annotations

import copy
from typing import Any

from aiohttp import ClientSession

from . import storage


SEND_NODE_CLASS = "Adv_SendToPS"
ADV_REQUEST_CLASS = "Adv_Request"


class BackendRunnerError(RuntimeError):
    pass


def _api_prompt_entries(prompt: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (node_id, node)
        for node_id, node in prompt.items()
        if isinstance(node, dict) and isinstance(node.get("class_type"), str)
    ]


def _apply_request_id_to_send_node(prompt: dict[str, Any], request_id: str) -> None:
    for _node_id, node in _api_prompt_entries(prompt):
        if node.get("class_type") == SEND_NODE_CLASS:
            node.setdefault("inputs", {})["request_id"] = request_id


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
        for index in range(1, storage.ADV_REQUEST_MAX_IMAGES + 1):
            inputs[f"image_{index}_file"] = ""
        inputs["mask_image_file"] = ""


def _has_send_node(prompt: dict[str, Any]) -> bool:
    return any(node.get("class_type") == SEND_NODE_CLASS for _node_id, node in _api_prompt_entries(prompt))


def patched_api_prompt_for_state(workflow: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    if not storage.is_api_prompt_workflow(workflow):
        raise BackendRunnerError("Backend API workflow execution requires an API prompt workflow JSON.")

    prompt = copy.deepcopy(workflow)
    request_id = str(state.get("request_id") or "")
    _apply_request_id_to_send_node(prompt, request_id)
    _apply_adv_request_to_api_prompt(prompt, state)

    if not _has_send_node(prompt):
        raise BackendRunnerError("Workflow is missing Adv_SendToPS, so generated images cannot be returned to Photoshop.")
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
    workflow = storage.workflow_for_feature(feature_id)
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
