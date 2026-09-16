"""Six-node contract: scalar widgets are literal, single-valued and socketless."""
from __future__ import annotations
import secrets
from comfy_api.latest import io
from .errors import BridgeError, require
from .parameters import configured_fields, normalize, seed_schema
from .protocol import BRIDGE_MAX_SEED, NODE_CATEGORY

def text(name, default="", **kwargs):
    return io.String.Input(name, default=default, socketless=True, **kwargs)

def binding(param_id, label, source):
    return [text("param_id", param_id, advanced=True), text("label", label, advanced=True),
            text("source_json", source, advanced=True)]

def check_graph(cls):
    """Defense for direct native /prompt execution, including file-only workflows."""
    prompt = getattr(getattr(cls, "hidden", None), "prompt", None)
    if prompt:
        from .manifest import validate_node_uniqueness
        validate_node_uniqueness(prompt)

class VPSeed(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="VP_Seed", display_name="PS Seed", category=NODE_CATEGORY,
            inputs=[text("param_id", "seed", advanced=True), text("label", "Seed", advanced=True),
                    io.Int.Input("value", default=42, min=0, max=BRIDGE_MAX_SEED, socketless=True),
                    io.Combo.Input("mode", options=["fixed", "random"], default="fixed", socketless=True)],
            outputs=[io.Int.Output("seed")], hidden=[io.Hidden.extra_pnginfo, io.Hidden.prompt],
            has_intermediate_output=True)

    @classmethod
    def execute(cls, param_id="seed", label="Seed", value=42, mode="fixed"):
        check_graph(cls)
        configured_fields("VP_Seed", dict(param_id=param_id, label=label, value=value, mode=mode))
        extra = getattr(getattr(cls, "hidden", None), "extra_pnginfo", None) or {}
        require(mode == "fixed" or not extra.get("ps_bridge_v3"), "BINDING_INVALID", "Prepared Seed must be fixed before execution")
        actual = secrets.randbelow(BRIDGE_MAX_SEED + 1) if mode == "random" else normalize(seed_schema(), value)
        return io.NodeOutput(actual, ui={"actual_seed": [actual]})

    @classmethod
    def fingerprint_inputs(cls, mode="fixed", **kwargs):
        return float("nan") if mode == "random" else kwargs.get("value", 42)

class VPSlider(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="VP_Slider", display_name="PS Slider", category=NODE_CATEGORY,
            inputs=[io.Float.Input("value", default=1, min=-BRIDGE_MAX_SEED, max=BRIDGE_MAX_SEED, step=0.01, socketless=True),
                    *[io.Float.Input(name, default=default, min=-BRIDGE_MAX_SEED, max=BRIDGE_MAX_SEED,
                                     step=0.01, socketless=True) for name, default in (("min", 0), ("max", 1), ("step", 0.01))],
                    *binding("strength", "Strength", '{"kind":"canonical","key":"strength"}')],
            outputs=[io.Float.Output("value")], hidden=[io.Hidden.prompt])

    @classmethod
    def execute(cls, value=1, min=0, max=1, step=0.01, param_id="strength", label="Strength", source_json='{"kind":"canonical","key":"strength"}'):
        check_graph(cls)
        schema = configured_fields("VP_Slider", {k: v for k, v in locals().items() if k != "cls"})[0][2]
        return io.NodeOutput(normalize(schema, value))

    @classmethod
    def validate_inputs(cls, **kwargs):
        try:
            configured_fields("VP_Slider", kwargs)
            return True
        except BridgeError as exc:
            return str(exc)

class VPPrompt(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="VP_Prompt", display_name="PS Prompt", category=NODE_CATEGORY,
            inputs=[text("text", multiline=True, dynamic_prompts=False),
                    *binding("prompt", "Prompt", '{"kind":"canonical","key":"prompt"}')],
            outputs=[io.String.Output("text")], hidden=[io.Hidden.prompt])

    @classmethod
    def execute(cls, text="", param_id="prompt", label="Prompt", source_json='{"kind":"canonical","key":"prompt"}'):
        check_graph(cls)
        configured_fields("VP_Prompt", dict(text=text, param_id=param_id, label=label, source_json=source_json))
        return io.NodeOutput(text)

class VPBatch(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="VP_Batch", display_name="PS Batch", category=NODE_CATEGORY,
            inputs=[io.Int.Input("value", default=1, min=1, max=4, step=1, socketless=True)],
            outputs=[io.Int.Output("count")], hidden=[io.Hidden.prompt])

    @classmethod
    def validate_inputs(cls, value):
        return True if type(value) is int and 1 <= value <= 4 else "PS Batch requires an integer from 1 to 4"

    @classmethod
    def execute(cls, value=1):
        check_graph(cls)
        require(type(value) is int and 1 <= value <= 4, "PARAMETER_INVALID", "Batch must be an integer from 1 to 4")
        return io.NodeOutput(value)
