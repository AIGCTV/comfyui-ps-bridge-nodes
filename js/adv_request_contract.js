export const ADV_REQUEST_CLASS = "Adv_Request";
export const ADV_REQUEST_MAX_IMAGES = 6;
export const ADV_REQUEST_WIDGET_SCHEMA_VERSION = 2;
export const ADV_REQUEST_WIDGET_SCHEMA_PROPERTY = "ps_bridge_widget_schema";

export const ADV_REQUEST_BACKEND_OUTPUTS = [
  ["IMAGE_1", "IMAGE"],
  ...Array.from({ length: ADV_REQUEST_MAX_IMAGES - 1 }, (_, index) => [`IMAGE_${index + 2}`, "IMAGE"]),
  ["MASK", "MASK"],
  ["PROMPT", "STRING"],
  ["RESOLUTION", "STRING"],
  ["STRENGTH", "FLOAT"],
  ["BATCH_COUNT", "INT"],
  ["SEED", "INT"],
  ["WIDTH", "INT"],
  ["HEIGHT", "INT"],
  ["PARAMS_JSON", "STRING"],
  ["REQUEST_JSON", "STRING"],
];

export const ADV_REQUEST_BACKEND_OUTPUT_INDEX = new Map(
  ADV_REQUEST_BACKEND_OUTPUTS.map(([name], index) => [name, index]),
);

export const ADV_REQUEST_HIDDEN_OUTPUTS = new Set(["PARAMS_JSON", "REQUEST_JSON"]);

export const ADV_REQUEST_SERIALIZED_WIDGET_ORDER = [
  "image_count",
  "prompt",
  "resolution",
  "strength",
  "batch_count",
  "seed",
  "control_after_generate",
];

export const ADV_REQUEST_WIDGET_DEFAULTS = {
  image_count: 1,
  prompt: "",
  resolution: "1k",
  strength: 0.65,
  batch_count: 1,
  seed: 42,
  control_after_generate: "randomize",
};

const ADV_REQUEST_CONTROL_MODES = new Set(["fixed", "increment", "decrement", "randomize"]);
const ADV_REQUEST_MAX_BATCH_COUNT = 4;
const ADV_REQUEST_MAX_SEED = Number.MAX_SAFE_INTEGER;

export function outputName(output) {
  return String(output?.name || output?.localized_name || output?.label || "").toUpperCase();
}

export function outputsMatchBackend(outputs = []) {
  return outputs.length === ADV_REQUEST_BACKEND_OUTPUTS.length
    && ADV_REQUEST_BACKEND_OUTPUTS.every(([name], index) => outputName(outputs[index]) === name);
}

function finiteNumber(value, fallback) {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : fallback;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }
  return fallback;
}

function clampedInteger(value, fallback, minimum, maximum) {
  const number = finiteNumber(value, fallback);
  return Math.max(minimum, Math.min(maximum, Math.round(number)));
}

export function normalizeAdvRequestWidgetValues(values) {
  const source = Array.isArray(values) ? values : [];
  const prompt = typeof source[1] === "string" ? source[1] : ADV_REQUEST_WIDGET_DEFAULTS.prompt;
  const resolution = typeof source[2] === "string" && source[2].trim()
    ? source[2]
    : ADV_REQUEST_WIDGET_DEFAULTS.resolution;
  const control = ADV_REQUEST_CONTROL_MODES.has(source[6])
    ? source[6]
    : ADV_REQUEST_WIDGET_DEFAULTS.control_after_generate;
  return [
    clampedInteger(source[0], ADV_REQUEST_WIDGET_DEFAULTS.image_count, 1, ADV_REQUEST_MAX_IMAGES),
    prompt,
    resolution,
    Math.max(0, Math.min(1, finiteNumber(source[3], ADV_REQUEST_WIDGET_DEFAULTS.strength))),
    clampedInteger(source[4], ADV_REQUEST_WIDGET_DEFAULTS.batch_count, 1, ADV_REQUEST_MAX_BATCH_COUNT),
    clampedInteger(source[5], ADV_REQUEST_WIDGET_DEFAULTS.seed, 0, ADV_REQUEST_MAX_SEED),
    control,
  ];
}

export function defaultAdvRequestWidgetValues() {
  return ADV_REQUEST_SERIALIZED_WIDGET_ORDER.map((name) => ADV_REQUEST_WIDGET_DEFAULTS[name]);
}

export function normalizeAdvRequestWidgetPatch(patch) {
  if (!patch || typeof patch !== "object" || Array.isArray(patch)) return {};
  const normalized = {};
  ADV_REQUEST_SERIALIZED_WIDGET_ORDER.forEach((name, index) => {
    if (!Object.prototype.hasOwnProperty.call(patch, name) || patch[name] === undefined) return;
    const values = defaultAdvRequestWidgetValues();
    values[index] = patch[name];
    normalized[name] = normalizeAdvRequestWidgetValues(values)[index];
  });
  return normalized;
}

export function sanitizeAdvRequestWorkflowData(workflow, className = ADV_REQUEST_CLASS) {
  if (!workflow || typeof workflow !== "object" || !Array.isArray(workflow.nodes)) {
    return workflow;
  }
  for (const nodeData of workflow.nodes) {
    if (!nodeData || nodeData.type !== className) {
      continue;
    }
    const properties = nodeData.properties && typeof nodeData.properties === "object"
      ? nodeData.properties
      : {};
    const currentSchema = properties[ADV_REQUEST_WIDGET_SCHEMA_PROPERTY] === ADV_REQUEST_WIDGET_SCHEMA_VERSION;
    nodeData.widgets_values = currentSchema
      && Array.isArray(nodeData.widgets_values)
      && nodeData.widgets_values.length === ADV_REQUEST_SERIALIZED_WIDGET_ORDER.length
      ? normalizeAdvRequestWidgetValues(nodeData.widgets_values)
      : defaultAdvRequestWidgetValues();
    nodeData.properties = {
      ...properties,
      [ADV_REQUEST_WIDGET_SCHEMA_PROPERTY]: ADV_REQUEST_WIDGET_SCHEMA_VERSION,
    };
  }
  return workflow;
}
