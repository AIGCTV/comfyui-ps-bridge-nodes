# PS Bridge protocol v1

This document describes the public boundary between ComfyUI PS Bridge Nodes and
a bridge client. Vplugins is one compatible client, distributed separately.

## Transport

- HTTP base path: `/ps-bridge`
- WebSocket endpoint: `/ps-bridge/ws`
- Default bind scope: loopback clients only
- Maximum WebSocket message size: 128 MiB
- Supported payload encodings: JSON and MessagePack
- Maximum images in one request: 6, plus an optional mask

The current protocol number is `1`. Clients should inspect the snapshot returned
by `GET /ps-bridge/health` or the WebSocket `hello` message and reject
unsupported protocol versions.

## Access control

Loopback access needs no token. Private-network access is disabled unless both
of these environment variables are configured before ComfyUI starts:

```text
PS_BRIDGE_ALLOW_LAN=1
PS_BRIDGE_AUTH_TOKEN=<long-random-secret>
```

LAN HTTP requests send `Authorization: Bearer <token>`. A LAN WebSocket client
must make this its first frame:

```json
{
  "type": "authenticate",
  "data": {
    "token": "<token>"
  }
}
```

The token protects access but is not encryption. Use the bridge only on a
trusted network, or place ComfyUI behind HTTPS/WSS.

## HTTP endpoints

- `GET /ps-bridge/health` — health check and protocol/client snapshot
- `GET /ps-bridge/workflows` — available API-format workflows and manifest
- `GET /ps-bridge/workflows/{feature_id}` — one API-format workflow
- `GET /ps-bridge/inputs/{filename}` — a registered bridge input image

Workflow execution and input ingestion use WebSocket messages. Exact request
and response fields are versioned with the protocol. Unknown fields should be
ignored so compatible additions do not break older clients.

## WebSocket messages

Every frame is an envelope with a string `type` and an object `data`. Common
message types include:

- `client_capabilities` — ComfyUI client identity and supported features
- `ping` / `pong` — connection health
- `run_workflow` — image data and workflow parameters from Photoshop
- `slots_update` / `slots_snapshot` — live workflow parameter state
- `run_status` / `progress` — execution state
- `render_result` — output metadata returned to the Photoshop client
- `error` — structured failure information

Clients should use request IDs to correlate `run_workflow`, progress,
`render_result`, and `error` messages.

## Compatibility

The only supported workflow node IDs are `Adv_Request` and `Adv_SendToPS`.
Former node IDs are not registered, aliased, or migrated; workflows that use
them must be rebuilt with the two current nodes. The `run_workflow`,
`slots_update`, and `slots_snapshot` message types remain part of protocol v1.
