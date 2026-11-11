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


def prepare_native_shared(data):
    """ComfyUI's public on-prompt hook freezes one value per shared source.

    Prepared Bridge runs already own an immutable snapshot and must remain exact.
    Only the submitted API copy is changed; the saved workflow keeps random mode.
    """
    import copy
    import secrets
    from .protocol import BRIDGE_MAX_SEED
    if not isinstance(data, dict) or not isinstance(data.get('prompt'), dict):
        return data
    if data.get('extra_data', {}).get('extra_pnginfo', {}).get('ps_bridge_v3'):
        return data
    result = copy.deepcopy(data)
    groups = {}
    for node in result['prompt'].values():
        kind, inputs = node.get('class_type'), node.get('inputs', {})
        for name, _, schema in configured_fields(kind, inputs):
            source = (schema['source']['kind'], schema['source']['key'])
            if source not in groups:
                fields = {name: schema['default']}
                if kind == 'VP_Seed':
                    fields.update(value=secrets.randbelow(BRIDGE_MAX_SEED + 1) if inputs['mode'] == 'random' else inputs[name], mode='fixed')
                if kind == 'VP_Slider':
                    fields.update({field: inputs[field] for field in ('min', 'max', 'step')})
                groups[source] = fields
            inputs.update(groups[source])
    return result


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
            error.error['nodeId'] = str(node_id)
            raise


@web.middleware
async def native_prompt_validation(request, handler):
    if request.method == 'POST' and request.path == '/prompt':
        try:
            # aiohttp caches the body; the original core handler reads the same bytes.
            validate_native_prompt(json.loads((await request.read()).decode('utf-8')))
        except BridgeError as error:
            node_id = error.error.get('nodeId', '')
            extra = {'input_name': error.error['inputName']} if error.error.get('inputName') else {}
            return web.json_response({'error': {'type':'ps_vplugins_validation', 'message':str(error),
                'details':error.code, 'extra_info':{}}, 'node_errors': {node_id:{'errors':[
                    {'type':error.code, 'message':error.error['message'], 'details':error.error.get('field', ''), 'extra_info':extra}]}} if node_id else {}}, status=400)
    return await handler(request)
