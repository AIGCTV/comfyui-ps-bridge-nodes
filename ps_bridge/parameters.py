"""One normalization rule for nodes, manifests, patches and prepare."""
from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP, localcontext
from .errors import BridgeError, require
from .json_codec import loads, MAX_SAFE_INTEGER, validate_json

TYPES = {"int", "float", "string", "boolean", "enum"}
CANONICAL = {"prompt", "negativePrompt", "resolution", "strength", "count", "seed"}

def identifier(value, field="id"):
    require(isinstance(value, str) and 0 < len(value) <= 256 and not any(ord(c) < 32 for c in value),
            "MANIFEST_INVALID", "Expected a non-empty identifier", field=field)
    return value

def number(value):
    require(type(value) in (int, float), "PARAMETER_INVALID", "Expected a JSON number")
    try:
        validate_json(value)
    except (ValueError, UnicodeError) as exc:
        raise BridgeError("PARAMETER_INVALID", "Expected a finite safe number") from exc
    return Decimal(str(value))

def normalize(schema, value):
    kind = schema.get("type")
    require(kind in TYPES, "PARAMETER_INVALID", "Unknown parameter type")
    if schema.get("source") == {"kind": "canonical", "key": "count"}:
        require(type(value) is int, "PARAMETER_INVALID", "Batch count must be a JSON integer")
    if kind in {"int", "float"}:
        v, lo, hi, step = (number(v) for v in (value, schema.get("min"), schema.get("max"), schema.get("step")))
        require(lo <= hi and step > 0, "PARAMETER_INVALID", "Invalid numeric range or step")
        if kind == "int":
            require(all(v == v.to_integral_value() for v in (v, lo, hi, step)),
                    "PARAMETER_INVALID", "Integer parameter requires integral value and bounds")
        require(lo <= v <= hi, "PARAMETER_INVALID", "Value is outside the declared range")
        with localcontext() as ctx:
            # Preserve the same decimal grid even when a tiny step crosses zero
            # from a much larger negative minimum (JS uses exact BigInt ticks).
            ctx.prec = max(80, max(n.adjusted() for n in (v, lo, hi, step))
                           - min(n.as_tuple().exponent for n in (v, lo, hi, step)) + 20)
            ticks = ((v - lo) / step).to_integral_value(rounding=ROUND_HALF_UP)
            result = lo + ticks * step
        require(result <= hi, "PARAMETER_INVALID", "Nearest step falls outside the declared range")
        return int(result) if kind == "int" else float(result)
    if kind == "boolean":
        require(type(value) is bool, "PARAMETER_INVALID", "Expected a JSON boolean")
    else:
        require(isinstance(value, str), "PARAMETER_INVALID", "Expected a string, not null")
        try:
            validate_json(value)
        except UnicodeError as exc:
            raise BridgeError("PARAMETER_INVALID", "Invalid Unicode") from exc
        if kind == "enum":
            options = schema.get("options")
            require(isinstance(options, list) and options and all(isinstance(v, str) for v in options)
                    and len(set(options)) == len(options), "PARAMETER_INVALID", "Enum options must be unique strings")
            require(value in options, "PARAMETER_INVALID", "Value is not an enum option")
    return value

def seed_schema():
    return {"type": "int", "min": 0, "max": MAX_SAFE_INTEGER, "step": 1}

def configured_fields(class_type, inputs):
    if class_type not in {"VP_Seed", "VP_Slider", "VP_Prompt", "VP_Batch"}:
        return []
    if class_type == "VP_Batch":
        require(type(inputs.get("value")) is int, "PARAMETER_INVALID", "Batch must be a JSON integer")
        schema = {"paramId": "count", "label": "Batch", "type": "int", "min": 1, "max": 4, "step": 1,
                  "source": {"kind": "canonical", "key": "count"}}
    else:
        schema = {"paramId": identifier(inputs.get("param_id")), "label": inputs.get("label", "")}
        require(isinstance(schema["label"], str), "PARAMETER_INVALID", "label must be text")
        if class_type == "VP_Seed":
            require(inputs.get("mode") in ("fixed", "random"), "PARAMETER_INVALID", "Seed mode must be fixed or random")
            schema.update(seed_schema(), semantic="seed", defaultMode=inputs["mode"],
                          source={"kind": "canonical", "key": "seed"})
        else:
            schema["source"] = loads(inputs.get("source_json", "null"))
            if class_type == "VP_Slider":
                schema.update(type="float", **{key: inputs.get(key) for key in ("min", "max", "step")})
                require(number(schema["min"]) < number(schema["max"]), "PARAMETER_INVALID", "Slider minimum must be less than maximum")
            else:
                schema["type"] = "string"
    name = "text" if class_type == "VP_Prompt" else "value"
    schema["default"] = normalize(schema, inputs.get(name))
    validate_source(schema)
    return [(name, 0, schema)]

def validate_source(parameter):
    source, kind = parameter.get("source"), parameter["type"]
    require(isinstance(source, dict) and set(source) == {"kind", "key"} and
            source["kind"] in {"canonical", "parameter"}, "MANIFEST_INVALID", "Invalid parameter source")
    identifier(source["key"], "source.key")
    if source["kind"] == "canonical":
        key = source["key"]
        compatible = {"prompt": {"string", "enum"}, "negativePrompt": {"string", "enum"},
                      "resolution": {"string", "enum"}, "count": {"int"}, "strength": {"float"}, "seed": {"int"}}
        require(key in compatible and kind in compatible[key], "MANIFEST_INVALID", "Source role and type are incompatible")
        if key == "count":
            require(1 <= parameter["min"] <= parameter["max"] <= 4, "MANIFEST_INVALID", "Local count must be 1–4")
        if key == "seed":
            require(parameter.get("semantic") == "seed", "MANIFEST_INVALID", "Seed role requires VP_Seed")
