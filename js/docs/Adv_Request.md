# Adv Request

Receives one to six images and workflow parameters from a compatible Photoshop
bridge client.

## Inputs

- `image_count`: number of active image slots
- `prompt`: prompt text supplied by the client
- `resolution`, `strength`, `batch_count`, `seed`: common generation controls

## Outputs

`IMAGE_1` through `IMAGE_6`, `MASK`, prompt and numeric controls, image
dimensions, plus canonical `PARAMS_JSON` and `REQUEST_JSON`.

Connect the image output you want to process to the rest of the workflow. End
the workflow with `Adv_SendToPS`.
