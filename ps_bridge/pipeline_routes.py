"""Controller-authenticated optional Pipeline v1 routes."""
import asyncio
from aiohttp import web, WSMsgType
from .errors import BridgeError, require
from .json_codec import loads, canonical_bytes
from .protocol import BRIDGE_MAX_MESSAGE_BYTES


def register_pipeline_routes(route, service, read_body):
    pipeline = service.pipeline

    def request_shape(payload, fields):
        require(isinstance(payload, dict) and set(payload) == fields and type(payload.get("version")) is int and payload["version"] == 1,
                "MESSAGE_INVALID", "Invalid Pipeline v1 request")

    @route("GET", "/pipeline/capabilities", role="controller")
    async def capabilities(request, owner):
        return pipeline.capabilities()

    @route("POST", "/pipeline/runs/{runId}/observer", role="controller")
    async def observer(request, owner):
        payload = await read_body(request)
        request_shape(payload, {"version", "scope"})
        return await pipeline.observe(request.match_info["runId"], owner, payload["scope"])

    @route("POST", "/pipeline/snapshot", role="controller")
    async def snapshot(request, owner):
        payload = await read_body(request)
        request_shape(payload, {"version", "runId", "scope"})
        await service.work.run(pipeline.authorize, payload["runId"], owner, payload["scope"])
        if service.runner:
            await service.work.run(service.runner.reconcile, payload["runId"])
        return await service.work.run(pipeline.snapshot, payload["runId"], owner, payload["scope"])

    @route("POST", "/pipeline/runs/{runId}/submission-rejected", role="controller")
    async def rejected(request, owner):
        payload = await read_body(request)
        run_id = request.match_info["runId"]
        await service.work.run(service.runs.get, run_id, owner)
        if service.runner:
            await service.work.run(service.runner.reconcile, run_id)
        return await service.work.run(pipeline.submission_rejected, run_id, owner, payload)

    @route("GET", "/pipeline/ws", role="controller")
    async def websocket(request, owner):
        ws = web.WebSocketResponse(max_msg_size=BRIDGE_MAX_MESSAGE_BYTES, heartbeat=30)
        await ws.prepare(request)
        subscription = None
        receiver = None
        next_event = None
        try:
            first = await asyncio.wait_for(ws.receive(), 5)
            require(first.type == WSMsgType.TEXT, "MESSAGE_INVALID", "First pipeline message must subscribe")
            payload = loads(first.data)
            fields = {"version", "type", "scope"}
            if isinstance(payload, dict) and "runId" in payload:
                fields.add("runId")
            request_shape(payload, fields)
            require(payload["type"] == "subscribe", "MESSAGE_INVALID", "Expected a pipeline subscription")
            subscribing = asyncio.create_task(service.work.run(pipeline.subscribe, asyncio.get_running_loop(), owner, payload["scope"], payload.get("runId")))
            try:
                subscription = await asyncio.shield(subscribing)
            except asyncio.CancelledError:
                subscription = await subscribing
                raise
            receiver = asyncio.create_task(ws.receive())
            while not ws.closed:
                next_event = asyncio.create_task(subscription.next())
                done, _ = await asyncio.wait({receiver, next_event}, return_when=asyncio.FIRST_COMPLETED)
                if receiver in done:
                    break  # only one subscription per connection; reconnect for another run.
                event = next_event.result()
                if event is None:
                    await ws.close(code=1013, message=b"Recover this run with a snapshot")
                    break
                await ws.send_str(canonical_bytes(event).decode("utf-8"))
        except (BridgeError, asyncio.TimeoutError):
            await ws.close(code=1008, message=b"Invalid pipeline subscription")
        finally:
            tasks = [task for task in (receiver, next_event) if task]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if subscription:
                cleanup = asyncio.create_task(service.work.finish(pipeline.unsubscribe, subscription))
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    await cleanup
                    raise
        return ws
