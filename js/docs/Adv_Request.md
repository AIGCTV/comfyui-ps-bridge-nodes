# PS Bridge Send To ComfyUI

Node ID: `Adv_Request`

Receives one to six images and workflow parameters from a compatible Photoshop
bridge client.

## Inputs

- `image_count`: number of active image slots
- `prompt`: prompt text supplied by the client
- `resolution`, `strength`, `batch_count`, `seed`: common generation controls
- `control_after_generate`: ComfyUI's seed control mode

The workflow stores these seven values in that exact order. Refresh, Test Mode,
the request summary, and bridge-internal file fields are runtime-only controls
and are not persisted in `widgets_values`.

Current workflows carry `properties.ps_bridge_widget_schema = 2`. A missing or
different marker, or any value array whose length is not seven, is reset to the
current defaults. Old layouts are not migrated.

## Outputs

`IMAGE_1` through `IMAGE_6`, `MASK`, prompt and numeric controls, image
dimensions, plus canonical `PARAMS_JSON` and `REQUEST_JSON`.

Connect the image output you want to process to the rest of the workflow. End
the workflow with `PS Bridge Send To Photoshop` (`Adv_SendToPS`).
