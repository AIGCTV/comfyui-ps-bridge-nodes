# Public source manifest

Release version: `0.3.1`

Prepared: `2026-09-16`. Status: local source candidate; not pushed, tagged or published.

This source tree contains only the ComfyUI six-node package. It has no private Git history,
runtime data, credentials, companion application code, development tests or CI workflows.
`docs/contracts/bridge-v3/bridge.schema.json` is required at runtime and must remain installed.
`ps_bridge/test_mode.py` and `js/test_mode_client.js` are product features, not development tests.
Two GUI workflow templates and their same-name static JPG covers are included. API exports,
internal workflow variants, the optional template-cover comparison extension and retired Adv
frontend helpers are excluded.
The synthetic workflow bundle retains its original bytes and has no verified Photoshop support claim.
The package version is aligned in pyproject.toml, ps_bridge/__init__.py and ps_bridge/protocol.py.
Source signing is not applied. GitHub pushing and Registry publication are separate operations.

## Exact allowlist

- `.comfyignore`
- `.gitattributes`
- `.gitignore`
- `LICENSE`
- `README.md`
- `RELEASE_MANIFEST.md`
- `__init__.py`
- `data/workflows/example-roundtrip.json`
- `data/workflows/example-roundtrip.manifest.json`
- `data/workflows/example-roundtrip.ui.json`
- `data/workflows/manifest.json`
- `docs/contracts/bridge-v3/README.md`
- `docs/contracts/bridge-v3/bridge.schema.json`
- `docs/contracts/bridge-v3/errors.json`
- `docs/contracts/bridge-v3/node-catalog.json`
- `docs/contracts/bridge-v3/pipeline-v1.schema.json`
- `example_workflows/PS_Bridge_Roundtrip.jpg`
- `example_workflows/PS_Bridge_Roundtrip.json`
- `example_workflows/PS_Vplugins_Six_Nodes.jpg`
- `example_workflows/PS_Vplugins_Six_Nodes.json`
- `example_workflows/README.md`
- `js/assets/icon.png`
- `js/assets/vplugins.svg`
- `js/bridge.js`
- `js/canvas_controls.js`
- `js/canvas_menu.js`
- `js/docs/VP_Batch.md`
- `js/docs/VP_Image.md`
- `js/docs/VP_Prompt.md`
- `js/docs/VP_Seed.md`
- `js/docs/VP_SendToPS.md`
- `js/docs/VP_Slider.md`
- `js/editor_execution.js`
- `js/editor_pipeline.js`
- `js/execution_host.js`
- `js/export_bindings.js`
- `js/i18n.js`
- `js/inline_editor.js`
- `js/native_preview_guard.js`
- `js/node_bindings.js`
- `js/node_menu.js`
- `js/node_state.js`
- `js/node_widgets.js`
- `js/parameter_sync_client.js`
- `js/pipeline_diagnostics.js`
- `js/resource_scope.js`
- `js/test_mode_client.js`
- `js/test_mode_toolbar.js`
- `js/workflow_export.js`
- `locales/en/commands.json`
- `locales/en/main.json`
- `locales/en/nodeDefs.json`
- `locales/zh/commands.json`
- `locales/zh/main.json`
- `locales/zh/nodeDefs.json`
- `ps_bridge/__init__.py`
- `ps_bridge/asset_registry.py`
- `ps_bridge/backend_runner.py`
- `ps_bridge/diagnostics.py`
- `ps_bridge/editor_execution.py`
- `ps_bridge/editor_pipeline.py`
- `ps_bridge/errors.py`
- `ps_bridge/identity.py`
- `ps_bridge/json_codec.py`
- `ps_bridge/manager.py`
- `ps_bridge/manager_notifications.py`
- `ps_bridge/manager_state.py`
- `ps_bridge/manifest.py`
- `ps_bridge/media_nodes.py`
- `ps_bridge/native_execution.py`
- `ps_bridge/native_validation.py`
- `ps_bridge/nodes.py`
- `ps_bridge/output_nodes.py`
- `ps_bridge/parameter_nodes.py`
- `ps_bridge/parameter_sync.py`
- `ps_bridge/parameters.py`
- `ps_bridge/paths.py`
- `ps_bridge/pipeline.py`
- `ps_bridge/pipeline_routes.py`
- `ps_bridge/protocol.py`
- `ps_bridge/requests.py`
- `ps_bridge/result_assets.py`
- `ps_bridge/routes.py`
- `ps_bridge/run_preparation.py`
- `ps_bridge/schemas.py`
- `ps_bridge/security.py`
- `ps_bridge/service.py`
- `ps_bridge/storage.py`
- `ps_bridge/test_mode.py`
- `ps_bridge/test_mode_state.py`
- `ps_bridge/work.py`
- `pyproject.toml`
- `requirements.txt`
