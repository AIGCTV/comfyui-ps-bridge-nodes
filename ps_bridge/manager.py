from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from aiohttp import WSMsgType, web

from . import __version__
from . import backend_runner, storage
from .protocol import (
    BRIDGE_MAX_IMAGES,
    BRIDGE_MAX_MESSAGE_BYTES,
    BRIDGE_PROTOCOL_VERSION,
    BRIDGE_SUPPORTED_ENCODINGS,
)
from .security import lan_access_enabled, lan_access_ready

try:
    import msgpack
except ImportError:  # pragma: no cover - exercised in ComfyUI environments without requirements installed
    msgpack = None


logger = logging.getLogger(__name__)
INLINE_RENDER_IMAGE_ENV = "PS_BRIDGE_RENDER_INLINE_IMAGE"
INLINE_RENDER_IMAGE_FIELDS = ("image", "png", "base64", "image_png")


def inline_render_image_enabled() -> bool:
    return os.getenv(INLINE_RENDER_IMAGE_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


def strip_inline_images_for_file_ref(payload: Any) -> Any:
    if inline_render_image_enabled() or not isinstance(payload, dict):
        return payload
    images = payload.get("images")
    if images is None and isinstance(payload.get("data"), dict):
        images = payload["data"].get("images")
    if not isinstance(images, list):
        return payload
    for image in images:
        if isinstance(image, dict):
            for field in INLINE_RENDER_IMAGE_FIELDS:
                image.pop(field, None)
    return payload


@dataclass
class BridgeClient:
    ws: web.WebSocketResponse
    role: str
    client_id: str
    ip: str
    base_url: str = ""
    encoding: str = "json"
    capabilities: dict[str, Any] = field(default_factory=dict)


class BridgeManager:
    def __init__(self) -> None:
        self.clients: dict[str, BridgeClient] = {}

    def role_clients(self, role: str, capability: str | None = None) -> list[str]:
        clients = [client_id for client_id, client in self.clients.items() if client.role == role]
        if capability:
            clients = [
                client_id
                for client_id in clients
                if bool(self.clients[client_id].capabilities.get(capability))
            ]
        return clients

    def primary_role_client(self, role: str, capability: str | None = None) -> str | None:
        clients = self.role_clients(role, capability)
        return clients[-1] if clients else None

    def workflow_requires_api_prompt_client(self, feature_id: Any) -> bool:
        try:
            return storage.is_api_prompt_workflow(storage.migrated_workflow_for_feature(feature_id))
        except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError):
            return False

    def primary_comfy_client_for_feature(self, feature_id: Any) -> str | None:
        if self.workflow_requires_api_prompt_client(feature_id):
            return self.primary_role_client("comfy", "api_prompt_workflows")
        return self.primary_role_client("comfy")

    def primary_current_graph_client(self) -> str | None:
        return self.primary_role_client("comfy", "current_graph_workflows")

    def _current_graph_test_client_score(self, client_id: str) -> tuple[int, int, int, float]:
        capabilities = self.clients[client_id].capabilities
        focused = 1 if bool(capabilities.get("window_focused")) else 0
        visible = 1 if bool(capabilities.get("page_visible", True)) else 0
        active = 1 if focused and visible else 0
        try:
            last_active_at = float(capabilities.get("last_active_at") or 0)
        except (TypeError, ValueError):
            last_active_at = 0.0
        return (active, focused, visible, last_active_at)

    def primary_current_graph_test_client(self) -> str | None:
        clients = [
            client_id
            for client_id in self.role_clients("comfy", "current_graph_workflows")
            if bool(self.clients[client_id].capabilities.get("current_graph_test_mode"))
        ]
        if not clients:
            return None
        return max(clients, key=self._current_graph_test_client_score)

    def update_comfy_client_capabilities(self, client: BridgeClient, payload: dict[str, Any]) -> None:
        for key in (
            "graph_id",
            "current_graph_test_mode",
            "page_visible",
            "window_focused",
            "last_active_at",
        ):
            if key in payload:
                client.capabilities[key] = payload[key]

    def snapshot(self) -> dict[str, Any]:
        state = storage.load_state()
        return {
            "protocol_version": BRIDGE_PROTOCOL_VERSION,
            "node_version": __version__,
            "max_images": BRIDGE_MAX_IMAGES,
            "max_message_bytes": BRIDGE_MAX_MESSAGE_BYTES,
            "supported_encodings": list(BRIDGE_SUPPORTED_ENCODINGS),
            "network": {
                "default_scope": "loopback",
                "lan_enabled": lan_access_enabled(),
                "lan_ready": lan_access_ready(),
                "lan_auth": "shared_token",
            },
            "clients": {
                "ps": len(self.role_clients("ps")),
                "comfy": len(self.role_clients("comfy")),
                "comfy_api_prompt": len(self.role_clients("comfy", "api_prompt_workflows")),
            },
            "request_id": state.get("request_id"),
            "feature_id": state.get("feature_id"),
            "image_count": state.get("image_count", 0),
            "multi_image": bool(state.get("multi_image")),
            "msgpack_available": msgpack is not None,
        }

    def _state_message(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "request_id": state["request_id"],
            "feature_id": state["feature_id"],
            "execution_mode": state.get("execution_mode", storage.EXECUTION_MODE_AUTO),
            "slots": state["slots"],
            "images": state["images"],
            "image_count": state.get("image_count", 0),
            "multi_image": state.get("multi_image", False),
            "canvas": state.get("canvas"),
            "selection": state["selection"],
            "selections": state.get("selections", {}),
            "adv_request": state.get("adv_request"),
            "vplugins_request": state.get("vplugins_request"),
            "updated_at": state.get("updated_at"),
        }

    async def _send_error(self, client: BridgeClient, payload: dict[str, Any], message: str) -> None:
        await self.send_to(client.client_id, "error", {
            "request_id": str(payload.get("request_id") or ""),
            "feature_id": str(payload.get("feature_id") or payload.get("featureId") or ""),
            "message": message,
        })

    async def _queue_backend_workflow(self, client: BridgeClient, state: dict[str, Any]) -> None:
        try:
            result = await backend_runner.queue_api_workflow_for_state(
                feature_id=state["feature_id"],
                state=state,
                base_url=client.base_url,
            )
        except (FileNotFoundError, ValueError, backend_runner.BackendRunnerError, OSError, json.JSONDecodeError) as exc:
            await self.send_to(client.client_id, "error", {
                "request_id": state.get("request_id", ""),
                "feature_id": state.get("feature_id", ""),
                "execution_mode": storage.EXECUTION_MODE_API_WORKFLOW,
                "message": str(exc),
            })
            return
        await self.send_to(client.client_id, "run_status", result)

    def _json_safe(self, value: Any) -> Any:
        if isinstance(value, bytes):
            return base64.b64encode(value).decode("ascii")
        if isinstance(value, bytearray):
            return base64.b64encode(bytes(value)).decode("ascii")
        if isinstance(value, memoryview):
            return base64.b64encode(value.tobytes()).decode("ascii")
        if isinstance(value, dict):
            return {str(key): self._json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._json_safe(item) for item in value]
        if isinstance(value, tuple):
            return [self._json_safe(item) for item in value]
        return value

    async def add_client(self, client: BridgeClient) -> None:
        self.clients[client.client_id] = client
        await self.send_to(client.client_id, "hello", self.snapshot())
        peer_role = "comfy" if client.role == "ps" else "ps"
        await self.broadcast(peer_role, "peer_connected", {"role": client.role, "client_id": client.client_id})

    async def remove_client(self, client_id: str) -> None:
        client = self.clients.pop(client_id, None)
        if client is not None:
            peer_role = "comfy" if client.role == "ps" else "ps"
            await self.broadcast(peer_role, "peer_disconnected", {"role": client.role, "client_id": client_id})

    def decode_message(self, role: str, data: str | bytes) -> dict[str, Any]:
        if isinstance(data, bytes):
            if msgpack is None:
                raise RuntimeError("msgpack is required for binary Photoshop messages")
            unpacked = msgpack.unpackb(data, raw=False)
            if not isinstance(unpacked, dict):
                raise ValueError("Message must decode to an object")
            return unpacked
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            raise ValueError("Message must be a JSON object")
        return parsed

    async def handle_message(self, client_id: str, data: str | bytes) -> None:
        client = self.clients[client_id]
        message = self.decode_message(client.role, data)
        msg_type = message.get("type")
        if not msg_type and len(message) == 1:
            msg_type = next(iter(message))
            message = {"type": msg_type, "data": message[msg_type]}

        if client.role == "ps":
            await self.handle_ps_message(client, msg_type, message)
        else:
            await self.handle_comfy_message(client, msg_type, message)

    async def handle_ps_message(self, client: BridgeClient, msg_type: str, message: dict[str, Any]) -> None:
        payload = message.get("data") if isinstance(message.get("data"), dict) else message
        if msg_type == "run_workflow":
            raw_feature_id = payload.get("feature_id") or payload.get("featureId") or "roundtrip"
            try:
                feature_id = storage.validate_workflow_id(raw_feature_id)
                execution_mode = storage.execution_mode_for_payload(payload, feature_id)
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                await self._send_error(client, payload, str(exc))
                return

            comfy_test_client_id = self.primary_current_graph_test_client()
            if comfy_test_client_id is not None:
                try:
                    state = storage.ingest_run_payload(payload, require_workflow=False)
                except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
                    await self._send_error(client, payload, str(exc))
                    return
                state["execution_mode"] = storage.EXECUTION_MODE_CURRENT_GRAPH
                await self.send_to(comfy_test_client_id, "run_workflow", self._state_message(state))
                await self.send_to(client.client_id, "run_status", {
                    "request_id": state["request_id"],
                    "feature_id": state["feature_id"],
                    "execution_mode": state["execution_mode"],
                    "status": "queued_in_comfy",
                })
                return

            if execution_mode == storage.EXECUTION_MODE_CURRENT_GRAPH:
                comfy_client_id = self.primary_current_graph_client()
                if comfy_client_id is None:
                    await self._send_error(client, payload, "No ComfyUI frontend is connected with current graph workflow support.")
                    return
                try:
                    state = storage.ingest_run_payload(payload, require_workflow=False)
                except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
                    await self._send_error(client, payload, str(exc))
                    return
                await self.send_to(comfy_client_id, "run_workflow", self._state_message(state))
                await self.send_to(client.client_id, "run_status", {
                    "request_id": state["request_id"],
                    "feature_id": state["feature_id"],
                    "execution_mode": execution_mode,
                    "status": "queued_in_comfy",
                })
                return

            if execution_mode == storage.EXECUTION_MODE_API_WORKFLOW:
                try:
                    state = storage.ingest_run_payload(payload)
                except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
                    await self._send_error(client, payload, str(exc))
                    return
                await self._queue_backend_workflow(client, state)
                return

            comfy_client_id = self.primary_role_client("comfy")
            if comfy_client_id is None:
                try:
                    state = storage.ingest_run_payload(payload)
                except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
                    await self._send_error(client, payload, str(exc))
                    return
                await self._queue_backend_workflow(client, state)
                return

            try:
                state = storage.ingest_run_payload(payload, require_workflow=False)
            except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
                await self._send_error(client, payload, str(exc))
                return
            await self.send_to(comfy_client_id, "run_workflow", self._state_message(state))
            await self.send_to(client.client_id, "run_status", {
                "request_id": state["request_id"],
                "feature_id": state["feature_id"],
                "execution_mode": execution_mode,
                "status": "queued_in_comfy",
            })
            return

        if msg_type == "slots_update":
            state = storage.load_state()
            feature_id = (
                payload.get("feature_id")
                or payload.get("featureId")
                or state.get("feature_id")
            )
            try:
                workflow_slots = storage.workflow_slot_ids_for_feature(feature_id)
            except (FileNotFoundError, ValueError):
                workflow_slots = None
            slots = storage.normalize_slots_for_payload(payload, workflow_slots or {})
            state["slots"] = storage.merge_slots(state.get("slots"), slots)
            storage.save_state(state)
            comfy_client_id = self.primary_role_client("comfy")
            if comfy_client_id is not None:
                await self.send_to(comfy_client_id, "slots_update", {"slots": slots, "payload": payload})
            return

        if msg_type == "ping":
            await self.send_to(client.client_id, "pong", self.snapshot())
            return

        await self.broadcast("comfy", msg_type or "message", message)

    async def handle_comfy_message(self, client: BridgeClient, msg_type: str, message: dict[str, Any]) -> None:
        if msg_type == "client_capabilities":
            capabilities = message.get("data") if isinstance(message.get("data"), dict) else message
            client.capabilities = {str(key): value for key, value in capabilities.items()}
            return
        if msg_type == "slots_snapshot":
            snapshot = message.get("data") if isinstance(message.get("data"), dict) else message
            if isinstance(snapshot, dict):
                self.update_comfy_client_capabilities(client, snapshot)
            await self.broadcast("ps", "slots_snapshot", snapshot)
            return
        if msg_type in {"run_status", "progress", "render_result", "error"}:
            await self.broadcast("ps", msg_type, message.get("data") or message)
            return
        if msg_type == "ping":
            await self.send_to(client.client_id, "pong", self.snapshot())
            return
        await self.broadcast("ps", msg_type or "message", message)

    async def broadcast(self, role: str, msg_type: str, payload: Any) -> None:
        for client_id in self.role_clients(role):
            await self.send_to(client_id, msg_type, payload)

    async def send_to(self, client_id: str, msg_type: str, payload: Any) -> None:
        client = self.clients.get(client_id)
        if client is None or client.ws.closed:
            return
        if client.role == "ps" and msg_type == "render_result":
            payload = strip_inline_images_for_file_ref(payload)
        if client.role == "ps":
            if client.encoding == "msgpack" and msgpack is not None:
                await client.ws.send_bytes(msgpack.packb({"type": msg_type, "data": payload}, use_bin_type=True))
            else:
                await client.ws.send_str(json.dumps(self._json_safe({"type": msg_type, "data": payload}), default=str))
        else:
            await client.ws.send_str(json.dumps({"type": msg_type, "data": payload}, default=str))

    def send_render_result_from_thread(self, payload: dict[str, Any]) -> None:
        try:
            from server import PromptServer

            loop = PromptServer.instance.loop
        except Exception as exc:
            logger.warning("Unable to access PromptServer loop for PS render result: %s", exc)
            return
        asyncio.run_coroutine_threadsafe(self.broadcast("ps", "render_result", payload), loop)


manager = BridgeManager()
