from __future__ import annotations

import asyncio
import json
import logging
import uuid

from aiohttp import WSMsgType, web

from .manager import BridgeClient, manager
from .paths import PS_IMAGES_DIR, WORKFLOWS_DIR, ensure_data_dirs
from .protocol import BRIDGE_MAX_MESSAGE_BYTES, BRIDGE_PROTOCOL_VERSION, BRIDGE_SUPPORTED_ENCODINGS
from .security import (
    auth_token_matches,
    bearer_token,
    bridge_auth_token,
    is_local_or_private_ip,
    is_loopback_ip,
    lan_access_enabled,
    resolve_inside,
    validate_workflow_id,
)


logger = logging.getLogger(__name__)
_ROUTES_REGISTERED = False


def _remote_ip(request: web.Request) -> str:
    peername = request.transport.get_extra_info("peername") if request.transport else None
    return peername[0] if peername else request.remote or ""


def _base_url(request: web.Request) -> str:
    scheme = request.scheme or "http"
    host = request.host or "127.0.0.1:8188"
    return f"{scheme}://{host}"


def _network_access_error(request: web.Request) -> web.Response | None:
    ip = _remote_ip(request)
    if is_loopback_ip(ip):
        return None
    if not is_local_or_private_ip(ip):
        return web.Response(status=403, text="PS Bridge rejects public-network clients")
    if not lan_access_enabled():
        return web.Response(
            status=403,
            text="PS Bridge LAN access is disabled; set PS_BRIDGE_ALLOW_LAN=1 to enable it",
        )
    if not bridge_auth_token():
        return web.Response(
            status=503,
            text="PS Bridge LAN access requires PS_BRIDGE_AUTH_TOKEN",
        )
    return None


def _authorize_http(request: web.Request) -> web.Response | None:
    error = _network_access_error(request)
    if error is not None or is_loopback_ip(_remote_ip(request)):
        return error
    candidate = bearer_token(request.headers.get("Authorization"))
    if not auth_token_matches(candidate):
        return web.Response(
            status=401,
            text="PS Bridge LAN authentication failed",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return None


async def _authenticate_websocket(ws: web.WebSocketResponse) -> bool:
    try:
        message = await asyncio.wait_for(ws.receive(), timeout=5)
    except asyncio.TimeoutError:
        await ws.close(code=1008, message=b"PS Bridge authentication timed out")
        return False

    candidate = ""
    if message.type == WSMsgType.TEXT:
        try:
            payload = json.loads(message.data)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict) and payload.get("type") == "authenticate":
            data = payload.get("data")
            if isinstance(data, dict):
                candidate = str(data.get("token") or "")
            else:
                candidate = str(payload.get("token") or "")

    if not auth_token_matches(candidate):
        await ws.close(code=1008, message=b"PS Bridge authentication failed")
        return False

    await ws.send_json({
        "type": "authenticated",
        "data": {"protocol_version": BRIDGE_PROTOCOL_VERSION},
    })
    return True


def _workflow_files() -> list[dict[str, str]]:
    ensure_data_dirs()
    files = []
    for path in sorted(WORKFLOWS_DIR.glob("*.json")):
        if path.name == "manifest.json":
            continue
        files.append({"feature_id": path.stem, "filename": path.name})
    return files


def register_routes() -> None:
    global _ROUTES_REGISTERED
    if _ROUTES_REGISTERED:
        return
    try:
        from server import PromptServer

        routes = PromptServer.instance.routes
    except Exception as exc:
        logger.warning("PS Bridge routes were not registered: %s", exc)
        return

    ensure_data_dirs()

    @routes.get("/ps-bridge/health")
    async def health(request: web.Request) -> web.Response:
        forbidden = _authorize_http(request)
        if forbidden:
            return forbidden
        return web.json_response({"ok": True, "bridge": manager.snapshot()})

    @routes.get("/ps-bridge/workflows")
    async def workflows(request: web.Request) -> web.Response:
        forbidden = _authorize_http(request)
        if forbidden:
            return forbidden
        manifest_path = WORKFLOWS_DIR / "manifest.json"
        manifest = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                manifest = {"error": "manifest.json is invalid"}
        return web.json_response({"workflows": _workflow_files(), "manifest": manifest})

    @routes.get("/ps-bridge/workflows/{feature_id:.+}")
    async def workflow(request: web.Request) -> web.Response:
        forbidden = _authorize_http(request)
        if forbidden:
            return forbidden
        try:
            feature_id = validate_workflow_id(request.match_info["feature_id"])
            path = resolve_inside(WORKFLOWS_DIR, f"{feature_id}.json")
        except ValueError as exc:
            return web.Response(status=400, text=str(exc))
        if not path.exists():
            return web.Response(status=404, text="Workflow not found")
        return web.FileResponse(path)

    @routes.get("/ps-bridge/inputs/{filename:.+}")
    async def ps_input(request: web.Request) -> web.Response:
        forbidden = _authorize_http(request)
        if forbidden:
            return forbidden
        try:
            path = resolve_inside(PS_IMAGES_DIR, request.match_info["filename"])
        except ValueError as exc:
            return web.Response(status=400, text=str(exc))
        if not path.exists():
            return web.Response(status=404, text="Input image not found")
        return web.FileResponse(path)

    @routes.get("/ps-bridge/ws")
    async def websocket(request: web.Request) -> web.WebSocketResponse:
        forbidden = _network_access_error(request)
        if forbidden:
            if forbidden.status == 503:
                raise web.HTTPServiceUnavailable(text=forbidden.text)
            raise web.HTTPForbidden(text=forbidden.text)

        role = request.query.get("role", "")
        if role not in {"ps", "comfy"}:
            raise web.HTTPBadRequest(text="role must be ps or comfy")
        encoding = (request.query.get("encoding") or "json").strip().lower()
        if role == "comfy":
            encoding = "json"
        elif encoding not in BRIDGE_SUPPORTED_ENCODINGS:
            raise web.HTTPBadRequest(text="encoding must be json or msgpack")
        client_id = request.query.get("client_id") or f"{role}-{uuid.uuid4().hex}"
        ws = web.WebSocketResponse(max_msg_size=BRIDGE_MAX_MESSAGE_BYTES)
        await ws.prepare(request)
        if not is_loopback_ip(_remote_ip(request)) and not await _authenticate_websocket(ws):
            return ws
        client = BridgeClient(
            ws=ws,
            role=role,
            client_id=client_id,
            ip=_remote_ip(request),
            base_url=_base_url(request),
            encoding=encoding,
        )
        await manager.add_client(client)

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await manager.handle_message(client_id, msg.data)
                elif msg.type == WSMsgType.BINARY:
                    await manager.handle_message(client_id, msg.data)
                elif msg.type == WSMsgType.ERROR:
                    logger.warning("PS Bridge websocket error: %s", ws.exception())
                    break
        finally:
            await manager.remove_client(client_id)
        return ws

    _ROUTES_REGISTERED = True
