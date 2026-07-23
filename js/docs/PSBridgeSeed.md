# PS Bridge Seed

Reads a named integer seed from the latest bridge request.

- `slot_id`: parameter name; defaults to `seed`
- `fallback`: seed used when the slot is absent

The result is clamped to ComfyUI's supported seed range.
