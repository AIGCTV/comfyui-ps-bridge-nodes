# ComfyUI PS Bridge Nodes

ComfyUI V3 custom nodes for exchanging images, masks, prompts, and workflow
parameters with Photoshop bridge clients.

This repository contains **only the ComfyUI custom nodes**. Vplugins is a
separate companion project and is not included or licensed by this repository.

## What is included

- `Adv_Request` — receives up to six images, a mask, prompt, seed, and custom
  parameters from a bridge client
- `Adv_SendToPS` — returns a generated image and optional alpha mask
- Legacy compatibility nodes for existing workflows
- Local HTTP/WebSocket bridge routes
- A safe minimal roundtrip example with no model names, prompts, or local paths

## Requirements

- A recent ComfyUI version with the V3 custom-node API
- Python 3.10 or newer
- `msgpack>=1.0.8`
- Photoshop and a compatible bridge client, such as Vplugins, for the complete
  Photoshop workflow

## Install

### ComfyUI Manager / Registry

After the package is published to the Comfy Registry, search for
`ComfyUI PS Bridge Nodes` in ComfyUI Manager and install it.

### Manual installation

Clone the public repository into `ComfyUI/custom_nodes`:

```bash
git clone https://github.com/AIGCTV/comfyui-ps-bridge-nodes.git
```

Install the dependency using the Python environment that runs ComfyUI:

```bash
python -m pip install -r comfyui-ps-bridge-nodes/requirements.txt
```

Restart ComfyUI after installation.

## Quick start

1. Open `example_workflows/PS_Bridge_Roundtrip.json` in ComfyUI.
2. Connect your compatible Photoshop bridge client.
3. Send an image to the `Adv_Request` node.
4. Run the workflow. `Adv_SendToPS` returns the result to the requesting client.

`data/workflows/example-roundtrip.json` is the matching API-format example used
by bridge clients. Private production workflows are intentionally not shipped.

## Network security

The bridge accepts loopback clients by default. No additional setting is needed
when Photoshop, the bridge client, and ComfyUI run on the same computer.

LAN access is fail-closed. To enable it, configure both variables before
starting ComfyUI:

```text
PS_BRIDGE_ALLOW_LAN=1
PS_BRIDGE_AUTH_TOKEN=<long-random-secret>
```

LAN HTTP clients use `Authorization: Bearer <token>`. LAN WebSocket clients send
an `authenticate` message as their first frame. The bundled browser extension
can read the token from `sessionStorage` or `localStorage` key
`ps_bridge_auth_token`.

The shared token does not encrypt traffic. Use only a trusted network, or put
ComfyUI behind HTTPS/WSS. Public-internet client addresses are rejected.

See [docs/bridge-protocol-v1.md](docs/bridge-protocol-v1.md) for the protocol
boundary and compatibility rules.

## Node compatibility

New workflows should use `Adv_Request` and `Adv_SendToPS`. These legacy IDs stay
registered so older workflows continue to open:

- `VpluginsRequest`
- `PSBridgeImageInput`
- `PSBridgeSendToPS`
- `PSBridgePrompt`
- `PSBridgeSeed`
- `PSBridgeFloat`
- `PSBridgeInt`
- `PSBridgeBoolean`
- `PSBridgeAnyReroute`

## Development

Run the Python suite with a ComfyUI Python environment:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

Run the frontend contract tests with Node.js:

```bash
node --test tests/adv_request_contract_test.mjs tests/adv_request_summary_test.mjs
```

The public release contents and exclusions are recorded in
[RELEASE_MANIFEST.md](RELEASE_MANIFEST.md).

## License

The ComfyUI PS Bridge Nodes source in this repository is released under the MIT
License. See [LICENSE](LICENSE).

Vplugins and other companion software are separate projects with their own
distribution terms.
