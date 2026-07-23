export const ADV_REQUEST_CLASS = "Adv_Request";
export const ADV_REQUEST_MAX_IMAGES = 6;

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
  "params_json",
  "image_1_file",
  "image_2_file",
  "image_3_file",
  "image_4_file",
  "image_5_file",
  "image_6_file",
  "mask_image_file",
];

export const ADV_REQUEST_FIRST_VERSION_WIDGET_ORDER = [
  "image_count",
  "prompt",
  "resolution",
  "strength",
  "batch_count",
  "send_to_ps",
  "params_json",
  "image_1_file",
  "image_2_file",
  "image_3_file",
  "image_4_file",
  "image_5_file",
  "image_6_file",
  "mask_image_file",
];

const ADV_REQUEST_WIDGET_DEFAULTS = {
  image_count: 1,
  prompt: "",
  resolution: "1k",
  strength: 0.65,
  batch_count: 1,
  seed: 42,
  params_json: "{}",
  image_1_file: "",
  image_2_file: "",
  image_3_file: "",
  image_4_file: "",
  image_5_file: "",
  image_6_file: "",
  mask_image_file: "",
};

export function outputName(output) {
  return String(output?.name || output?.localized_name || output?.label || "").toUpperCase();
}

export function outputTypeForBackendIndex(index) {
  return ADV_REQUEST_BACKEND_OUTPUTS[index]?.[1] || "";
}

export function outputsMatchBackend(outputs = []) {
  return outputs.length === ADV_REQUEST_BACKEND_OUTPUTS.length
    && ADV_REQUEST_BACKEND_OUTPUTS.every(([name], index) => outputName(outputs[index]) === name);
}

function serializedWidgetNamesFromNodeData(nodeData) {
  const names = [];
  for (const input of nodeData?.inputs || []) {
    const name = input?.widget?.name;
    if (name && !names.includes(name)) {
      names.push(name);
    }
  }
  return names;
}

function widgetValueMapFromNodeData(nodeData) {
  const values = Array.isArray(nodeData?.widgets_values) ? nodeData.widgets_values : [];
  let names = serializedWidgetNamesFromNodeData(nodeData);
  if (!names.length || names.includes("send_to_ps") || !names.includes("seed")) {
    names = ADV_REQUEST_FIRST_VERSION_WIDGET_ORDER;
  }
  const mapped = {};
  names.forEach((name, index) => {
    if (name && values[index] !== undefined) {
      mapped[name] = values[index];
    }
  });
  return mapped;
}

export function normalizedAdvRequestWidgetValues(nodeData) {
  const mapped = widgetValueMapFromNodeData(nodeData);
  return ADV_REQUEST_SERIALIZED_WIDGET_ORDER.map((name) => {
    return mapped[name] !== undefined ? mapped[name] : ADV_REQUEST_WIDGET_DEFAULTS[name];
  });
}

export function migrateAdvRequestWorkflowData(workflow, className = ADV_REQUEST_CLASS) {
  if (!workflow || typeof workflow !== "object" || !Array.isArray(workflow.nodes)) {
    return workflow;
  }
  for (const nodeData of workflow.nodes) {
    if (!nodeData || nodeData.type !== className || !Array.isArray(nodeData.widgets_values)) {
      continue;
    }
    const names = serializedWidgetNamesFromNodeData(nodeData);
    if (names.includes("send_to_ps") || !names.includes("seed") || nodeData.widgets_values.length !== ADV_REQUEST_SERIALIZED_WIDGET_ORDER.length) {
      nodeData.widgets_values = normalizedAdvRequestWidgetValues(nodeData);
    }
  }
  return workflow;
}

function normalizedType(value) {
  return String(value || "").toUpperCase();
}

function visibleBackendIndexesContain(visibleBackendIndexes, backendIndex) {
  return visibleBackendIndexes.some((index) => Number(index) === Number(backendIndex));
}

export function outputNameForTargetInput(targetInputName, targetNodeClass = "") {
  const name = String(targetInputName || "").toLowerCase();
  if (name === "width") return "WIDTH";
  if (name === "height") return "HEIGHT";
  if (name === "batch_size") return "BATCH_COUNT";
  if (name === "seed") return "SEED";
  if (name === "text" && String(targetNodeClass || "").includes("CLIPTextEncode")) {
    return "PROMPT";
  }
  return null;
}

export function normalizeAdvRequestLinkSlot({
  originSlot,
  outputs = [],
  visibleBackendIndexes = [],
  targetInputName = "",
  targetNodeClass = "",
  targetInputType = "",
  linkType = "",
  preferVisibleSlot = false,
} = {}) {
  const slot = Number(originSlot);
  if (!Number.isInteger(slot)) {
    return ADV_REQUEST_BACKEND_OUTPUT_INDEX.get(String(originSlot || "").toUpperCase()) ?? originSlot;
  }

  if (!outputsMatchBackend(outputs)) {
    const backendIndex = ADV_REQUEST_BACKEND_OUTPUT_INDEX.get(outputName(outputs[slot]));
    if (backendIndex !== undefined) {
      return backendIndex;
    }
  }

  const visibleBackendIndex = visibleBackendIndexes[slot];
  if (visibleBackendIndex === undefined || visibleBackendIndex === slot) {
    if (preferVisibleSlot && visibleBackendIndex === slot) {
      return slot;
    }
  } else if (preferVisibleSlot && !visibleBackendIndexesContain(visibleBackendIndexes, slot)) {
    return visibleBackendIndex;
  }

  const targetOutputName = outputNameForTargetInput(targetInputName, targetNodeClass);
  const targetOutputIndex = ADV_REQUEST_BACKEND_OUTPUT_INDEX.get(targetOutputName);
  if (targetOutputIndex !== undefined) {
    return targetOutputIndex;
  }

  if (visibleBackendIndex === undefined || visibleBackendIndex === slot) {
    return slot;
  }

  const expectedType = normalizedType(targetInputType || linkType);
  if (!expectedType || expectedType === "*") {
    return slot;
  }

  const currentType = normalizedType(outputs[slot]?.type || outputTypeForBackendIndex(slot));
  const visibleType = normalizedType(outputTypeForBackendIndex(visibleBackendIndex));
  if (visibleType === expectedType && currentType !== expectedType) {
    return visibleBackendIndex;
  }
  return slot;
}
