"""Validate this package's raw /prompt values before ComfyUI scalar coercion.

ComfyUI 0.34 casts INT inputs before node validate_inputs. An aiohttp middleware
on the existing app rejects invalid bridge inputs without replacing /prompt,
queuePrompt, execution functions, or changing any ComfyUI core files.
"""
import json
from aiohttp import web
from .errors import BridgeError
from .manifest import validate_node_uniqueness
from .parameters import configured_fields
from .protocol import NODE_CATALOG, LEGACY_NODES


def validate_native_prompt(data):
    prompt = data.get('prompt') if isinstance(data, dict) else None
    if not isinstance(prompt, dict):
        return
    bridge = {key: node for key, node in prompt.items() if isinstance(node, dict)
              and node.get('class_type') in set(NODE_CATALOG) | set(LEGACY_NODES)}
    if not bridge:
        return
    validate_node_uniqueness(bridge)
    for node_id, node in bridge.items():
        try:
            configured_fields(node['class_type'], node.get('inputs', {}))
        except BridgeError as error:
            error.error['field'] = str(node_id)
            raise


@web.middleware
async def native_prompt_validation(request, handler):
    if request.method == 'POST' and request.path == '/prompt':
        try:
            # aiohttp caches the body; the original core handler reads the same bytes.
            validate_native_prompt(json.loads((await request.read()).decode('utf-8')))
        except BridgeError as error:
            field = error.error.get('field', '')
            return web.json_response({'error': {'type':'ps_vplugins_validation', 'message':str(error),
                'details':error.code, 'extra_info':{}}, 'node_errors': {field:{'errors':[
                    {'type':error.code, 'message':error.error['message'], 'details':field, 'extra_info':{}}]}} if field else {}}, status=400)
    return await handler(request)
