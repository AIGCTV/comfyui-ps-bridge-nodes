from comfy_api.latest import ComfyExtension, io
from .ps_bridge.nodes import NODE_CLASSES
from .ps_bridge.routes import register_routes

WEB_DIRECTORY = "./js"

class PSBridgeExtension(ComfyExtension):
    async def on_load(self) -> None:
        register_routes()

    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return NODE_CLASSES

async def comfy_entrypoint() -> PSBridgeExtension:
    return PSBridgeExtension()

__all__ = ["WEB_DIRECTORY", "comfy_entrypoint"]
