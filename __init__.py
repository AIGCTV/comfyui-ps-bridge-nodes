from typing_extensions import override

from comfy_api.latest import ComfyExtension, io

from .ps_bridge.nodes import (
    AdvSendToPS,
    AdvRequest,
)
from .ps_bridge.routes import register_routes


WEB_DIRECTORY = "./js"


class PSBridgeExtension(ComfyExtension):
    @override
    async def on_load(self) -> None:
        register_routes()

    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [
            AdvRequest,
            AdvSendToPS,
        ]


async def comfy_entrypoint() -> PSBridgeExtension:
    return PSBridgeExtension()


__all__ = ["WEB_DIRECTORY", "comfy_entrypoint"]
