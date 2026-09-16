"""Editor-only UI recovery over the existing authenticated attachment.

These routes are an A-side editor extension, not a new controller protocol.
"""
from __future__ import annotations

from aiohttp import web

from .errors import require
from .parameter_sync import validate_scope


def authorize_editor_run(service, owner, envelope):
    require(isinstance(envelope, dict) and isinstance(envelope.get("payload"), dict),
            "MESSAGE_INVALID", "Editor request requires an attachment and payload")
    payload = envelope["payload"]
    require(set(payload) == {"runId", "scope"} and isinstance(payload["runId"], str),
            "MESSAGE_INVALID", "Editor request requires runId and scope")
    validate_scope(payload["scope"])
    with service.sessions.lock:
        session, attachment = service.sessions.authorize(owner, envelope)
        require(attachment["role"] == "editor", "FORBIDDEN", "UI recovery requires an attached editor", status=403)
        run = service.runs.get(payload["runId"])
        require(run["scope"] == session["scope"] and run["controllerClientId"] == session["controller"]
                and run["definitionSha256"] == session["manifest"]["definitionSha256"],
                "FORBIDDEN", "UI output belongs to a different run owner or workflow", status=403)
        if run["scope"]["domain"] == "test":
            require(run["scope"]["ownerClientId"] == owner, "FORBIDDEN", "UI output belongs to another editor", status=403)
        return run


def register_editor_pipeline(route, service, manager, read_body):
    @route("POST", "/editor/pipeline/snapshot", role="editor")
    async def snapshot(request, owner):
        run = await service.work.run(authorize_editor_run, service, owner, await read_body(request))
        event = await service.work.run(service.pipeline.snapshot, run["runId"], run["controllerClientId"], run["scope"])
        # Only the existing editor endpoint gains this display-only field.
        # The controller's frozen Pipeline v1 messages remain byte-compatible.
        event["editorExecution"] = service.execution.snapshot(run["runId"])
        return event

    @route("POST", "/editor/pipeline/resources/{resource_id}", role="editor")
    async def resource(request, owner):
        run = await service.work.run(authorize_editor_run, service, owner, await read_body(request))
        resource_id = request.match_info["resource_id"]
        if resource_id.startswith("live-preview-"):
            data, mime = service.execution.preview(run["runId"], resource_id.removeprefix("live-preview-"))
            return web.Response(body=data, content_type=mime,
                                headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
        data = await service.work.run(service.pipeline.resource, run["runId"], request.match_info["resource_id"])
        require(len(data) <= 16 * 1024 * 1024, "MESSAGE_TOO_LARGE", "Editor UI resource exceeds its limit", status=413)
        return web.Response(body=data, content_type="application/json", charset="utf-8",
                            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
