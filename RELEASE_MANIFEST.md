# Release manifest

Release version: `0.2.0`

Release date: `2026-08-04`

The public release is assembled from an explicit allowlist. It contains:

- ComfyUI V3 entrypoint and Python package: `__init__.py`, `ps_bridge/`
- Browser extension and node help: `js/`
- Runtime dependency metadata: `requirements.txt`, `pyproject.toml`
- Public documentation and examples: `README.md`, `docs/bridge-protocol-v1.md`,
  `example_workflows/`, `data/workflows/manifest.json`, and
  `data/workflows/example-roundtrip.json`
- License, tests, and GitHub Actions configuration

The release intentionally excludes:

- Vplugins and every other companion application's source code
- Private production workflows, models, prompts, generated images, and caches
- Local paths, `.env` files, credentials, access tokens, and development notes
- Internal integration and research documents, including the private files under
  `docs/` that are not named above
- Git history from the private development repository

Suggested source archive name:
`aigctv-ps-bridge-nodes-0.2.0-20260804.zip`.

This project is distributed as source code; binary signing is not applicable.
Git tags and GitHub release assets should be created from the public repository.
