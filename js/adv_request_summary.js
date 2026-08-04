export const PS_SUMMARY_WIDGET_NAME = "PS Summary";
export const ADV_REQUEST_CLASS = "Adv_Request";
export const SEND_NODE_CLASSES = new Set(["Adv_SendToPS"]);

export function normalizeExecutionMode(value) {
  if (value === undefined || value === null || value === "") return "auto";
  const text = String(value).trim().toLowerCase().replace(/-/g, "_");
  const aliases = {
    auto: "auto",
    current: "current_graph",
    currentgraph: "current_graph",
    current_graph: "current_graph",
    graph: "current_graph",
    api: "api_workflow",
    api_prompt: "api_workflow",
    api_prompt_workflow: "api_workflow",
    api_workflow: "api_workflow",
    backend: "api_workflow",
  };
  return aliases[text] || "auto";
}

export function shouldUseCurrentGraphForRun(executionMode = "auto", currentGraphTestMode = false) {
  return normalizeExecutionMode(executionMode) === "current_graph" || Boolean(currentGraphTestMode);
}

export function graphIdFromHash(hashValue, fallback = "") {
  const text = String(hashValue || "").trim();
  const raw = text.startsWith("#") ? text.slice(1) : text;
  if (!raw) return fallback;
  try {
    return decodeURIComponent(raw) || fallback;
  } catch {
    return raw || fallback;
  }
}

export function stableGraphId(rawGraphId = "", storedGraphId = "", force = false) {
  const raw = String(rawGraphId || "");
  const stored = String(storedGraphId || "");
  return force || !stored ? raw : stored;
}

export function graphTestModeState(states = {}, graphId = "") {
  if (!graphId || !states || typeof states !== "object") return false;
  return Object.prototype.hasOwnProperty.call(states, graphId) && Boolean(states[graphId]);
}

export function setGraphTestModeState(states = {}, graphId = "", enabled = false) {
  const next = Object.assign(Object.create(null), states && typeof states === "object" ? states : {});
  if (!graphId) return next;
  if (enabled) {
    next[graphId] = true;
  } else {
    delete next[graphId];
  }
  return next;
}

function sortedImageSlots(images) {
  if (!images || typeof images !== "object") return [];
  return Object.keys(images).sort((left, right) => {
    const leftNumber = Number((left.match(/\d+$/) || [99])[0]);
    const rightNumber = Number((right.match(/\d+$/) || [99])[0]);
    return leftNumber - rightNumber || left.localeCompare(right);
  });
}

function pixelText(data) {
  const images = data?.images && typeof data.images === "object" ? data.images : {};
  const slots = sortedImageSlots(images);
  const first = slots.length ? images[slots[0]] || {} : {};
  const width = Number(first.width || 0);
  const height = Number(first.height || 0);
  if (width > 0 && height > 0) {
    return `${width} x ${height}`;
  }
  const canvas = data?.canvas && typeof data.canvas === "object" ? data.canvas : {};
  const canvasWidth = Number(canvas.width || 0);
  const canvasHeight = Number(canvas.height || 0);
  return canvasWidth > 0 && canvasHeight > 0 ? `${canvasWidth} x ${canvasHeight}` : "- x -";
}

export function formatAdvRequestSummary(data = {}, request = {}) {
  const source = request && typeof request === "object" ? request : {};
  const batchCount = source.batch_count ?? 1;
  return `${pixelText(data)} | ${batchCount}p | ${source.resolution || "1k"} | ${source.strength ?? 0.65}`;
}

export function graphStatusFromNodeClasses(classNames = []) {
  const names = new Set(classNames.filter(Boolean).map((name) => String(name)));
  const hasAdvRequest = names.has(ADV_REQUEST_CLASS);
  const hasSendToPs = Array.from(SEND_NODE_CLASSES).some((name) => names.has(name));
  return {
    has_adv_request: hasAdvRequest,
    has_send_to_ps: hasSendToPs,
    current_graph_ready: hasAdvRequest && hasSendToPs,
  };
}
