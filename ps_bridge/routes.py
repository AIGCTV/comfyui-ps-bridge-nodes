"""Contract-3 REST and WebSocket endpoints with shared authorization."""
from __future__ import annotations
import asyncio
import logging
from urllib.parse import urlsplit
from aiohttp import web, WSMsgType
from .errors import BridgeError, require
from .json_codec import loads, canonical_bytes
from .manager import BridgeManager, BridgeClient
from .protocol import BRIDGE_MAX_MESSAGE_BYTES, BRIDGE_MAX_ASSET_BYTES, contract_capabilities
from .security import (is_loopback_ip, is_local_or_private_ip, lan_access_enabled,
                       bridge_auth_token, bearer_token, auth_token_matches, resolve_inside)
from .service import get_service
from .manifest import candidate
from .schemas import validate_shape

logger = logging.getLogger(__name__)
_ROUTES_REGISTERED = False

def _remote_ip(request):
    peer = request.transport.get_extra_info("peername") if request.transport else None
    return peer[0] if peer else request.remote or ""

def _network_access_error(request):
    ip = _remote_ip(request)
    if not is_loopback_ip(ip):
        if not is_local_or_private_ip(ip) or not lan_access_enabled() or not bridge_auth_token():
            return web.json_response({"ok": False, "error": {"code": "FORBIDDEN", "message": "LAN access requires explicit configuration and authentication", "retryable": False}}, status=403)
    origin = request.headers.get("Origin")
    if origin:
        parsed = urlsplit(origin)
        if parsed.netloc != request.host or parsed.scheme != request.scheme:
            return web.json_response({"ok": False, "error": {"code": "FORBIDDEN", "message": "Cross-origin bridge requests are forbidden", "retryable": False}}, status=403)
    return None

def _authorize_http(request):
    error = _network_access_error(request)
    if error is not None:
        return error
    # A configured token is enforced on loopback as well as LAN.
    if bridge_auth_token() and not auth_token_matches(bearer_token(request.headers.get("Authorization"))):
        return web.json_response({"ok": False, "error": {"code": "UNAUTHENTICATED", "message": "Bridge authentication required", "retryable": False}}, status=401)
    return None

async def read_body(request):
    data = bytearray()
    async for chunk in request.content.iter_chunked(65536):
        data.extend(chunk)
        require(len(data) <= BRIDGE_MAX_MESSAGE_BYTES, "MESSAGE_TOO_LARGE", "JSON control message exceeds size limit", status=413)
    return loads(bytes(data))

def create_routes(service):
    routes = web.RouteTableDef()
    manager = BridgeManager(service)
    service.manager = manager
    uploads = 0
    def identity(request, role=None):
        client_id = request.headers.get("X-PS-Bridge-Client-Id")
        actual = manager.identities.authenticate(client_id, request.headers.get("X-PS-Bridge-Client-Token"), role)
        return client_id, actual
    def route(method, path, *, auth=True, client=True, role=None, media=False):
        def decorate(handler):
            async def wrapped(request):
                nonlocal uploads
                uploading = False
                try:
                    if auth:
                        forbidden = _authorize_http(request)
                        if forbidden is not None:
                            return forbidden
                    owner = (await service.work.run(identity, request, role))[0] if client else None
                    if media:
                        require(uploads < 2, "BUSY", "Image upload queue is full", retryable=True, status=503)
                        uploads += 1
                        uploading = True
                    result = await handler(request, owner)
                    if isinstance(result, web.StreamResponse):
                        return result
                    return web.json_response({"ok": True, "data": result}, dumps=lambda value: canonical_bytes(value).decode("utf-8"))
                except BridgeError as exc:
                    return web.json_response({"ok": False, "error": exc.error}, status=exc.status)
                except (KeyError, TypeError, ValueError, OverflowError):
                    return web.json_response({"ok": False, "error": {"code": "MESSAGE_INVALID", "message": "Invalid request structure", "retryable": False}}, status=422)
                except Exception:
                    logger.exception("Bridge request failed")
                    return web.json_response({"ok": False, "error": {"code": "INTERNAL_ERROR", "message": "Bridge operation failed; inspect server logs", "retryable": False}}, status=500)
                finally:
                    if uploading:
                        uploads -= 1
            routes.route(method, "/ps-bridge/v3" + path)(wrapped)
            return wrapped
        return decorate

    @route("GET", "/health", client=False)
    async def health(request, owner):
        return {**contract_capabilities(), "serverEpoch": service.sessions.epoch}

    @route("POST", "/clients", client=False)
    async def enroll(request, owner):
        payload = await read_body(request)
        validate_shape("enrollment", payload)
        return await service.work.run(manager.identities.enroll, payload.get("clientId"), payload.get("role"), payload.get("clientToken"))

    @route("POST", "/workflows/inspect")
    async def inspect_workflow(request, owner):
        require((await service.work.run(identity, request))[1] in {"editor", "controller"}, "FORBIDDEN", "Only editors or controllers may inspect workflows", status=403)
        payload = await read_body(request)
        return await service.work.run(candidate, payload.get("api"), payload.get("metadata"), installed=service.definitions.installed, ui=payload.get("ui"))

    @route("POST", "/workflows/register", role="controller")
    async def register(request, owner):
        payload = await read_body(request)
        return await service.work.run(service.definitions.register, payload.get("manifest"), payload.get("api"), ui=payload.get("ui"))

    @route("GET", "/workflows")
    async def workflows(request, owner):
        return await service.work.run(service.definitions.list_current)

    @route("POST", "/assets", role="controller", media=True)
    async def asset_upload(request, owner):
        reader = await request.multipart()
        metadata, data = None, None
        async for part in reader:
            require(part.name in ("metadata", "file") and (metadata is None if part.name == "metadata" else data is None),
                    "ASSET_INVALID", "Expected one metadata part and one PNG file")
            limit = BRIDGE_MAX_MESSAGE_BYTES if part.name == "metadata" else BRIDGE_MAX_ASSET_BYTES
            content = bytearray()
            while chunk := await part.read_chunk(65536):
                content.extend(chunk)
                require(len(content) <= limit, "ASSET_INVALID", "Upload exceeds size limit", status=413)
            if part.name == "metadata":
                metadata = loads(bytes(content))
            else:
                data = bytes(content)
        require(metadata is not None and data is not None, "ASSET_INVALID", "Incomplete multipart upload")
        return await service.work.run(service.assets.register, owner, metadata, data)

    @route("POST", "/assets/register-input", role="controller")
    async def register_input(request, owner):
        payload = await read_body(request)
        return await service.work.run(service.assets.register_input, owner, payload.get("file"), payload.get("metadata"))

    @route("POST", "/runs/prepare", role="controller")
    async def prepare(request, owner):
        return await service.work.run(service.preparation.prepare, owner, await read_body(request))

    @route("POST", "/editor/preview", role="editor")
    async def editor_preview(request, owner):
        from .json_codec import digest
        envelope = await read_body(request)
        validate_shape("editorPreviewRequest", envelope)
        def resolve_preview():
            with service.sessions.lock:
                session, attachment = service.sessions.authorize(owner, envelope)
                require(attachment["role"] == "editor", "FORBIDDEN", "Preview requires an attached editor", status=403)
                payload = envelope["payload"]
                run = service.runs.get(payload["runId"])
                require(run["scope"] == session["scope"] and run["controllerClientId"] == session["controller"],
                        "FORBIDDEN", "Preview belongs to a different scope", status=403)
                current = service.pipeline.latest(session["scope"])
                require(current is not None and current["runId"] == run["runId"], "DRAFT_CHANGED", "Preview was superseded")
                binding = next((b for b in run["manifest"]["images"] if b["nodeId"] == payload["nodeId"]), None)
                require(binding is not None, "BINDING_INVALID", "Unknown preview node")
                asset = next((a for a in run["media"]["images"] if a["role"] == binding["slot"]), None)
                require(asset is not None, "MISSING_MEDIA", "Optional image is absent", status=404)
                return run, asset
        run, asset = await service.work.run(resolve_preview)
        png = await service.work.run(service.assets.preview, run["controllerClientId"], asset["assetId"])
        return web.Response(body=png, content_type="image/png",
                            headers={"Cache-Control": "private, no-store", "ETag": '"' + asset["sha256"] + '"'})

    @route("GET", "/runs/{runId}", role="controller")
    async def status(request, owner):
        run_id = request.match_info["runId"]
        await service.work.run(service.runs.get, run_id, owner)
        if service.runner:
            await service.work.run(service.runner.reconcile, run_id)
        return await service.work.run(lambda: service.runs.public(service.runs.get(run_id, owner)))

    @route("POST", "/runs/{runId}/submit", role="controller")
    async def submit(request, owner):
        require(service.runner is not None, "NODE_UNAVAILABLE", "ComfyUI runner is unavailable")
        run = await service.runner.submit(request.match_info["runId"], owner)
        return submission_result(run)

    def submission_result(run):
        require(run["status"] != "submission_unknown", "SUBMISSION_UNKNOWN",
                "Submission is unresolved; query this run, do not resubmit /prompt", snapshot=run)
        return run

    @route("POST", "/runs/{runId}/claim", role="controller")
    async def claim(request, owner):
        return await service.work.run(service.runs.claim, request.match_info["runId"], owner, "rust")

    @route("POST", "/runs/{runId}/submitted", role="controller")
    async def submitted(request, owner):
        payload = await read_body(request)
        validate_shape("submitted", payload)
        run_id = request.match_info["runId"]
        run = await service.work.run(service.runs.get, run_id, owner)
        require(run["submitOwner"] == "rust", "FORBIDDEN", "Only the Rust submit adapter can use this endpoint", status=403)
        if "promptId" in payload:
            require(service.runner is not None, "NODE_UNAVAILABLE", "ComfyUI queue observer is unavailable")
            running, pending = service.runner.adapter.queue()
            history = await service.work.run(service.runner.history_for, payload["promptId"])
            candidates = list(running) + list(pending) + [h.get("prompt") for h in history.values()]
            require(run["promptId"] == payload["promptId"] or any(service.runner._matches(run, item) and str(item[1]) == payload["promptId"] for item in candidates),
                    "BINDING_INVALID", "The reported prompt does not match the frozen run")
        return submission_result(await service.work.run(service.runs.submitted, run_id, owner, payload.get("claimToken"), payload.get("promptId")))

    @route("POST", "/runs/{runId}/cancel", role="controller")
    async def cancel(request, owner):
        payload = await read_body(request)
        validate_shape("cancel", payload)
        require(service.runner is not None, "NODE_UNAVAILABLE", "ComfyUI runner is unavailable")
        return await service.work.run(service.runner.cancel, request.match_info["runId"], owner, payload.get("cancelMessageId"))

    @route("GET", "/runs/{runId}/results/{resultId}/{batchIndex}", role="controller")
    async def result_file(request, owner):
        run = await service.work.run(service.runs.get, request.match_info["runId"], owner)
        result = next((r for r in run["results"] if r["resultId"] == request.match_info["resultId"]
                       and r["batchIndex"] == int(request.match_info["batchIndex"])), None)
        require(result is not None, "RESULT_NOT_FOUND", "Result does not exist", status=404)
        import folder_paths
        from pathlib import Path
        file = result["file"]
        path = resolve_inside(Path(folder_paths.get_output_directory()), file["filename"])
        path = await service.work.run(service.result_assets.verify, path, result)
        return web.FileResponse(path, headers={"Content-Type": "image/png"})

    @routes.get("/ps-bridge/v3/ws")
    async def websocket(request):
        forbidden = _network_access_error(request)
        if forbidden is not None:
            return forbidden
        ws = web.WebSocketResponse(max_msg_size=BRIDGE_MAX_MESSAGE_BYTES, heartbeat=30)
        await ws.prepare(request)
        client = None
        tasks = set()
        async def dispatch(raw):
            envelope = None
            try:
                envelope = loads(raw)
                await manager.handle(client, raw)
            except BridgeError as exc:
                await ws.send_json(manager.envelope("error", {"error": exc.error}, reply_to=envelope.get("messageId") if isinstance(envelope, dict) else None))
            except Exception:
                logger.exception("Bridge WebSocket dispatch failed")
                await ws.send_json(manager.envelope("error", {"error": {"code": "MESSAGE_INVALID", "message": "Invalid control message", "retryable": False}},
                                                   reply_to=envelope.get("messageId") if isinstance(envelope, dict) else None))
        try:
            first = await asyncio.wait_for(ws.receive(), timeout=5)
            require(first.type == WSMsgType.TEXT, "MESSAGE_INVALID", "First message must be a text hello")
            hello = loads(first.data)
            require(isinstance(hello, dict), "MESSAGE_INVALID", "Hello must be an object")
            require(hello.get("type") == "hello" and hello.get("protocolVersion") == 2 and hello.get("contractVersion") == 3,
                    "CONTRACT_UNSUPPORTED", "Expected protocol 2 / contract 3 hello")
            validate_shape("clientMessage", hello)
            payload = hello.get("payload", {})
            if bridge_auth_token():
                require(auth_token_matches(payload.get("authToken")), "UNAUTHENTICATED", "Bridge authentication required", status=401)
            client_id = hello.get("sourceClientId")
            role = await service.work.run(manager.identities.authenticate, client_id, payload.get("clientToken"), payload.get("role"))
            client = BridgeClient(client_id, role, ws)
            await manager.add(client)
            await manager.send(client, "welcome", {**contract_capabilities(), "serverEpoch": service.sessions.epoch}, reply_to=hello.get("messageId"))
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    # Long test capture waits cannot block replies from other clients.
                    if len(tasks) >= 32:
                        await ws.close(code=1008, message=b"Too many pending messages")
                        break
                    task = asyncio.create_task(dispatch(message.data))
                    tasks.add(task); task.add_done_callback(tasks.discard)
                elif message.type == WSMsgType.BINARY:
                    await ws.close(code=1003, message=b"Use asset upload for media")
        except (BridgeError, asyncio.TimeoutError) as exc:
            if isinstance(exc, BridgeError):
                await ws.send_json(manager.envelope("error", {"error": exc.error}))
            await ws.close(code=1008)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if client:
                cleanup = asyncio.create_task(manager.remove(client))
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    await cleanup
                    raise
        return ws

    # Old clients get a version error, not a silent fallback into the new state.
    async def legacy(request):
        forbidden = _authorize_http(request)
        return forbidden if forbidden is not None else web.json_response(
            {"ok": False, "error": {"code": "CONTRACT_UNSUPPORTED", "message": "Use /ps-bridge/v3 and VP nodes", "retryable": False}}, status=409)
    routes.get("/ps-bridge/health")(legacy)
    routes.get("/ps-bridge/ws")(legacy)
    from .pipeline_routes import register_pipeline_routes
    register_pipeline_routes(route, service, read_body)
    from .editor_pipeline import register_editor_pipeline
    register_editor_pipeline(route, service, manager, read_body)
    return routes, manager

def register_routes():
    global _ROUTES_REGISTERED
    if _ROUTES_REGISTERED:
        return
    from server import PromptServer
    from .native_validation import native_prompt_validation
    service = get_service()
    routes, manager = create_routes(service)
    for entry in routes:
        PromptServer.instance.routes.route(entry.method, entry.path)(entry.handler)
    service.manager = manager
    PromptServer.instance.app.middlewares.append(native_prompt_validation)
    loop = PromptServer.instance.loop
    service.runner.monitor = loop.create_task(service.runner.watch())
    async def shutdown(_app):
        service.runner.monitor.cancel()
        await service.pipeline.close()
        await asyncio.gather(service.runner.monitor, return_exceptions=True)
        await manager.close()
        service.work.close()
    PromptServer.instance.app.on_cleanup.append(shutdown)
    _ROUTES_REGISTERED = True
