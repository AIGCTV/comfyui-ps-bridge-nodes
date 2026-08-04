# PS Bridge Send To Photoshop

Node ID: `Adv_SendToPS`

Terminal output node for PS Bridge workflows.

- `image`: generated ComfyUI image to return
- `alpha`: optional opacity mask; `0` is transparent and `1` is opaque
- `filename_prefix`: optional temporary-file prefix

The node preserves the active bridge request ID and sends result metadata to
the requesting client. Add it at the end of every bridge workflow.
