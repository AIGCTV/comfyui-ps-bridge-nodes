import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import {
  ADV_REQUEST_BACKEND_OUTPUTS,
  ADV_REQUEST_BACKEND_OUTPUT_INDEX,
  ADV_REQUEST_CLASS,
  ADV_REQUEST_HIDDEN_OUTPUTS,
  ADV_REQUEST_MAX_IMAGES,
  normalizeAdvRequestWidgetPatch,
  outputName as contractOutputName,
  outputsMatchBackend,
  sanitizeAdvRequestWorkflowData,
} from "./adv_request_contract.js";
import {
  disableWidgetSerialization,
  normalizeAdvRequestNodeWidgets,
  restoreAdvRequestNodeWidgets,
  serializeAdvRequestNode,
} from "./adv_request_persistence.js";
import {
  PS_SUMMARY_WIDGET_NAME,
  formatAdvRequestSummary,
  graphIdFromHash,
  graphStatusFromNodeClasses,
  graphTestModeState,
  normalizeExecutionMode,
  setGraphTestModeState,
  stableGraphId,
  shouldUseCurrentGraphForRun,
} from "./adv_request_summary.js";

const EXTENSION_NAME = "comfyui_ps_bridge.bridge";
const BRIDGE_CLIENT_VERSION = 2;
const CLIENT_ID = `comfy-${Math.random().toString(36).slice(2, 11)}`;
const TEST_MODE_WIDGET_NAME = "Test Mode";
const SEND_NODE_CLASS = "Adv_SendToPS";
const ADV_REQUEST_MAX_BATCH_COUNT = 4;
const REQUEST_PARAM_OBJECT_KEYS = ["options", "settings", "params"];
const REQUEST_TOP_LEVEL_PARAM_KEYS = [
  "resolution",
  "batchCount",
  "batch_count",
  "strength",
  "width",
  "height",
  "steps",
  "cfg",
  "denoise",
  "seed",
];

let socket = null;
let reconnectTimer = null;
let activeRequestId = "";
let activeFeatureId = "";
let activeExecutionMode = "auto";
let currentGraphTestMode = false;
let currentGraphTestModeById = {};
let activeGraphId = "";
let frontendLastActiveAt = Date.now();
let activeBridgeState = {
  images: {},
  image_count: 0,
  multi_image: false,
  canvas: null,
  selection: null,
  updated_at: 0,
};

function wsUrl() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.host}/ps-bridge/ws?role=comfy&client_id=${encodeURIComponent(CLIENT_ID)}`;
}

function bridgeAuthToken() {
  try {
    return window.sessionStorage?.getItem("ps_bridge_auth_token")
      || window.localStorage?.getItem("ps_bridge_auth_token")
      || "";
  } catch {
    return "";
  }
}

function send(type, data = {}) {
  const payload = JSON.stringify({ type, data });
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(payload);
  }
}

function fallbackGraphId(graph = app.graph) {
  if (graph) {
    if (!graph.__psBridgeFallbackGraphId) {
      setHiddenProperty(graph, "__psBridgeFallbackGraphId", `graph-${CLIENT_ID}-${Math.random().toString(36).slice(2, 10)}`);
    }
    return graph.__psBridgeFallbackGraphId;
  }
  return `graph-${CLIENT_ID}`;
}

function rawCurrentGraphId(graph = app.graph) {
  const hash = typeof window !== "undefined" ? window.location?.hash : "";
  return graphIdFromHash(hash, fallbackGraphId(graph));
}

function adoptCurrentGraphId(graph = app.graph, options = {}) {
  const rawGraphId = rawCurrentGraphId(graph);
  if (!graph) return rawGraphId;
  const nextGraphId = stableGraphId(rawGraphId, graph.__psBridgeActiveGraphId, Boolean(options.force));
  setHiddenProperty(graph, "__psBridgeActiveGraphId", nextGraphId);
  return nextGraphId;
}

function currentGraphId(graph = app.graph) {
  return adoptCurrentGraphId(graph);
}

function pageVisible() {
  return typeof document === "undefined" || document.visibilityState !== "hidden";
}

function windowFocused() {
  return typeof document === "undefined" || document.hasFocus?.() === true;
}

function markFrontendActive() {
  frontendLastActiveAt = Date.now();
}

function activeGraphMetadata(graph = app.graph) {
  const graphId = currentGraphId(graph);
  return {
    graph_id: graphId,
    current_graph_test_mode: graphTestModeState(currentGraphTestModeById, graphId),
    page_visible: pageVisible(),
    window_focused: windowFocused(),
    last_active_at: frontendLastActiveAt,
  };
}

function clientCapabilities() {
  const graphState = activeGraphMetadata();
  return {
    client_id: CLIENT_ID,
    version: BRIDGE_CLIENT_VERSION,
    api_prompt_workflows: true,
    current_graph_workflows: true,
    graph_id: graphState.graph_id,
    current_graph_test_mode: graphState.current_graph_test_mode,
    page_visible: graphState.page_visible,
    window_focused: graphState.window_focused,
    last_active_at: graphState.last_active_at,
  };
}

function sendClientCapabilities() {
  send("client_capabilities", clientCapabilities());
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, 1500);
}

function connect() {
  if (socket && (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN)) {
    return;
  }
  socket = new WebSocket(wsUrl());
  socket.addEventListener("open", () => {
    const authValue = bridgeAuthToken();
    if (authValue) {
      send("authenticate", { token: authValue });
    }
    sendClientCapabilities();
    send("ping", { client_id: CLIENT_ID });
    send("slots_snapshot", collectSlots());
  });
  socket.addEventListener("message", async (event) => {
    try {
      const message = JSON.parse(event.data);
      await handleMessage(message.type, message.data ?? {});
    } catch (error) {
      console.error("[PS Bridge] Failed to handle message", error);
    }
  });
  socket.addEventListener("close", scheduleReconnect);
  socket.addEventListener("error", () => {
    try {
      socket.close();
    } catch {
      scheduleReconnect();
    }
  });
}

function nodeClass(node) {
  return node.comfyClass || node.type || "";
}

function isSendNodeClass(name) {
  return name === SEND_NODE_CLASS;
}

function hasBridgeSendNode(prompt) {
  for (const [, node] of apiPromptEntries(prompt)) {
    if (isSendNodeClass(node.class_type)) {
      return true;
    }
  }
  return false;
}

function assertBridgeSendNode(prompt) {
  if (hasBridgeSendNode(prompt)) {
    return;
  }
  throw new Error("Workflow is missing Adv_SendToPS, so generated images cannot be returned to Photoshop.");
}

function widget(node, name) {
  return node.widgets?.find((candidate) => candidate.name === name);
}

function widgetValue(node, name, fallback = undefined) {
  const found = widget(node, name);
  return found ? found.value : fallback;
}

function setWidgetValue(node, name, value, options = {}) {
  const found = widget(node, name);
  if (!found || value === undefined) return;
  found.value = value;
  if (options.callback !== false) {
    found.callback?.(value);
  }
}

function emptySlots() {
  return { prompt: {}, seed: {}, float: {}, int: {}, boolean: {} };
}

function numberValue(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}


function graphNodes(graph = app.graph) {
  return graph?._nodes || [];
}

function markCanvasDirty() {
  app.graph?.setDirtyCanvas(true, true);
}

function booleanValue(value, fallback = false) {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (["true", "1", "yes", "on"].includes(normalized)) return true;
    if (["false", "0", "no", "off"].includes(normalized)) return false;
  }
  return fallback;
}

function syncCurrentGraphTestModeFromGraph(options = {}) {
  if (options.adoptGraphId) {
    adoptCurrentGraphId(app.graph, { force: true });
  }
  const graphId = currentGraphId();
  const enabled = graphTestModeState(currentGraphTestModeById, graphId);
  const changed = activeGraphId !== graphId || currentGraphTestMode !== enabled;
  activeGraphId = graphId;
  currentGraphTestMode = enabled;
  if (changed || options.force) {
    refreshAllAdvRequestTestModeWidgets();
    if (options.dirty !== false) {
      markCanvasDirty();
    }
  }
  return changed;
}

function reportActiveGraphState(options = {}) {
  if (options.markActive !== false) {
    markFrontendActive();
  }
  syncCurrentGraphTestModeFromGraph({ dirty: options.dirty });
  sendClientCapabilities();
  if (options.snapshot !== false) {
    send("slots_snapshot", collectSlots());
  }
}

function setCurrentGraphTestMode(value, options = {}) {
  const graphId = currentGraphId();
  const enabled = booleanValue(value);
  const changed = currentGraphTestMode !== enabled;
  currentGraphTestModeById = setGraphTestModeState(currentGraphTestModeById, graphId, enabled);
  activeGraphId = graphId;
  currentGraphTestMode = enabled;
  markFrontendActive();
  if (changed) {
    refreshAllAdvRequestTestModeWidgets();
    markCanvasDirty();
  }
  sendClientCapabilities();
  if (options.notify !== false) {
    send("slots_snapshot", collectSlots());
  }
}

function toggleValue(value) {
  return value === undefined || value === null ? !currentGraphTestMode : booleanValue(value, !currentGraphTestMode);
}

function toggleCurrentGraphTestMode(value) {
  setCurrentGraphTestMode(toggleValue(value));
}

function clearSendNodePreviews(graph = app.graph) {
  for (const node of graphNodes(graph)) {
    if (!isSendNodeClass(nodeClass(node))) continue;
    node.imgs = null;
    node.imageIndex = null;
    node.images = null;
  }
  if (graph === app.graph) {
    markCanvasDirty();
  }
}

function setActiveBridgeState(data, graph = app.graph) {
  const images = data?.images && typeof data.images === "object" ? data.images : {};
  const imageCount = Number(data?.image_count ?? Object.keys(images).length ?? 0);
  activeBridgeState = {
    images,
    image_count: imageCount,
    multi_image: Boolean(data?.multi_image ?? imageCount > 1),
    canvas: data?.canvas || null,
    selection: data?.selection || null,
    updated_at: data?.updated_at || Date.now(),
  };
  for (const node of graphNodes(graph)) {
    configureAdvRequestNode(node);
  }
  if (graph === app.graph) {
    markCanvasDirty();
  }
}

function advRequestImageCount(node) {
  const count = Math.round(numberValue(widgetValue(node, "image_count", 1), 1));
  return Math.max(1, Math.min(ADV_REQUEST_MAX_IMAGES, count));
}

function advRequestBatchCount(value) {
  return Math.max(1, Math.min(ADV_REQUEST_MAX_BATCH_COUNT, Math.round(numberValue(value, 1))));
}

function advRequestAppliedImageCount(node) {
  const count = Number(node.__psBridgeAdvRequestImageCount);
  if (Number.isInteger(count)) {
    return Math.max(1, Math.min(ADV_REQUEST_MAX_IMAGES, count));
  }
  return advRequestImageCount(node);
}

function advRequestOutputImageIndex(name) {
  const match = String(name || "").toUpperCase().match(/^IMAGE_([1-6])$/);
  return match ? Number(match[1]) : null;
}

function advRequestOutputName(output) {
  return contractOutputName(output);
}

function advRequestOutputHasLinks(output) {
  return Array.isArray(output?.links) && output.links.length > 0;
}

function advRequestOutputShouldHide(node, output) {
  const name = advRequestOutputName(output);
  if (ADV_REQUEST_HIDDEN_OUTPUTS.has(name)) return true;
  const imageIndex = advRequestOutputImageIndex(name);
  return imageIndex !== null && imageIndex > advRequestAppliedImageCount(node) && !advRequestOutputHasLinks(output);
}

function advRequestOutputDefinition(name) {
  return ADV_REQUEST_BACKEND_OUTPUTS.find(([candidate]) => candidate === name);
}

function setHiddenProperty(target, name, value) {
  if (!target) return;
  try {
    Object.defineProperty(target, name, {
      value,
      writable: true,
      configurable: true,
      enumerable: false,
    });
  } catch {
    target[name] = value;
  }
}

function setAdvRequestOutputHidden(output, hidden) {
  setHiddenProperty(output, "__psBridgeVisualHidden", hidden);
}

function advRequestVisibleOutputDefinitions(count, linkedNames = new Set()) {
  return ADV_REQUEST_BACKEND_OUTPUTS.filter(([name]) => {
    if (ADV_REQUEST_HIDDEN_OUTPUTS.has(name)) return false;
    const imageIndex = advRequestOutputImageIndex(name);
    return imageIndex === null || imageIndex <= count || linkedNames.has(name);
  });
}

function syncAdvRequestOutputIndexMap(node) {
  node.__psBridgeAdvRequestOutputIndexMap = (node.outputs || []).map((output, index) => {
    const name = advRequestOutputName(output);
    return ADV_REQUEST_BACKEND_OUTPUT_INDEX.get(name) ?? index;
  });
}

function disconnectAdvRequestOutput(node, index) {
  if (typeof node.disconnectOutput === "function") {
    node.disconnectOutput(index);
  }
}

function advRequestVisibleSlotForBackendIndex(node, backendIndex) {
  const name = ADV_REQUEST_BACKEND_OUTPUTS[Number(backendIndex)]?.[0];
  if (!name) return -1;
  return (node.outputs || []).findIndex((output) => advRequestOutputName(output) === name);
}

function ensureAdvRequestBackendOutputVisible(node, backendIndex) {
  const name = ADV_REQUEST_BACKEND_OUTPUTS[Number(backendIndex)]?.[0];
  const imageIndex = advRequestOutputImageIndex(name);
  if (imageIndex !== null && imageIndex > advRequestImageCount(node)) {
    refreshAdvRequestOutputs(node, imageIndex);
  }
  return advRequestVisibleSlotForBackendIndex(node, backendIndex);
}

function normalizeAdvRequestOutgoingSlot(node, slot) {
  const numeric = Number(slot);
  if (Number.isInteger(numeric) && String(slot).trim() !== "") {
    if (numeric >= 0 && numeric < (node.outputs || []).length) {
      return numeric;
    }
    const visibleSlot = advRequestVisibleSlotForBackendIndex(node, numeric);
    return visibleSlot >= 0 ? visibleSlot : numeric;
  }
  const name = String(slot || "").toUpperCase();
  const namedSlot = (node.outputs || []).findIndex((output) => advRequestOutputName(output) === name);
  return namedSlot >= 0 ? namedSlot : slot;
}

function advRequestGraphLinkOutputNames(node, outputs = node.outputs || []) {
  const graph = node.graph || app.graph;
  const namesByLinkId = new Map();
  if (!graph?.links) return namesByLinkId;
  const nodeId = String(node.id);
  for (const [linkId, link] of Object.entries(graph.links)) {
    if (!link || String(link.origin_id) !== nodeId) continue;
    const output = outputs[Number(link.origin_slot)];
    let name = advRequestOutputName(output);
    if (!name && outputsMatchBackend(outputs)) {
      name = ADV_REQUEST_BACKEND_OUTPUTS[Number(link.origin_slot)]?.[0] || "";
    }
    if (name) {
      namesByLinkId.set(String(linkId), name);
    }
  }
  return namesByLinkId;
}

function remapAdvRequestGraphLinksByOutputNames(node, namesByLinkId) {
  const graph = node.graph || app.graph;
  if (!graph?.links) return;
  const nodeId = String(node.id);
  for (const [linkId, link] of Object.entries(graph.links)) {
    if (!link || String(link.origin_id) !== nodeId) continue;
    const name = namesByLinkId.get(String(linkId));
    if (!name) continue;
    const nextSlot = (node.outputs || []).findIndex((output) => advRequestOutputName(output) === name);
    if (nextSlot >= 0) {
      link.origin_slot = nextSlot;
    } else {
      const targetNode = graph.getNodeById?.(link.target_id);
      targetNode?.disconnectInput?.(Number(link.target_slot));
    }
  }
}

function repairAdvRequestOutputLinks(node) {
  const graph = node.graph || app.graph;
  if (!graph?.links || !node.outputs) return;
  for (const output of node.outputs) {
    output.links = [];
  }
  const nodeId = String(node.id);
  for (const [linkId, link] of Object.entries(graph.links)) {
    if (!link || String(link.origin_id) !== nodeId) continue;
    const index = Number(link.origin_slot);
    const output = node.outputs[index];
    if (!output) continue;
    if (!Array.isArray(output.links)) {
      output.links = [];
    }
    const normalizedId = Number.isNaN(Number(linkId)) ? linkId : Number(linkId);
    if (!output.links.includes(normalizedId)) {
      output.links.push(normalizedId);
    }
  }
  for (const output of node.outputs) {
    if (Array.isArray(output.links) && !output.links.length) {
      output.links = null;
    }
  }
}

function repairAdvRequestOutputLinksForGraph(graph = app.graph) {
  for (const node of graphNodes(graph)) {
    if (nodeClass(node) !== ADV_REQUEST_CLASS) continue;
    repairAdvRequestOutputLinks(node);
  }
}

function refreshAdvRequestOutputs(node, explicitCount = undefined) {
  if (nodeClass(node) !== ADV_REQUEST_CLASS) return;
  const count = explicitCount === undefined
    ? advRequestImageCount(node)
    : Math.max(1, Math.min(ADV_REQUEST_MAX_IMAGES, Math.round(numberValue(explicitCount, 1))));
  setWidgetValue(node, "image_count", count, { callback: false });
  node.__psBridgeAdvRequestImageCount = count;

  const backendNames = new Set(ADV_REQUEST_BACKEND_OUTPUTS.map(([name]) => name));
  const currentOutputs = node.outputs || [];
  const linkOutputNames = advRequestGraphLinkOutputNames(node, currentOutputs);
  const linkedNames = new Set(linkOutputNames.values());
  for (let index = 0; index < currentOutputs.length; index += 1) {
    const name = advRequestOutputName(currentOutputs[index]);
    if (name && !backendNames.has(name)) {
      disconnectAdvRequestOutput(node, index);
    }
  }

  const outputByName = new Map();
  for (const output of currentOutputs) {
    const name = advRequestOutputName(output);
    if (name && !outputByName.has(name)) {
      outputByName.set(name, output);
    }
  }

  node.outputs = advRequestVisibleOutputDefinitions(count, linkedNames).map(([name, type]) => {
    const output = outputByName.get(name) || { name, type, links: null };
    output.name = name;
    output.type = type;
    setAdvRequestOutputHidden(output, false);
    return output;
  });
  remapAdvRequestGraphLinksByOutputNames(node, linkOutputNames);
  repairAdvRequestOutputLinks(node);
  syncAdvRequestOutputIndexMap(node);
  node.__psBridgeAdvRequestOutputsInitialized = true;
  node.setSize?.(node.computeSize?.() || node.size);
  markCanvasDirty();
}

function syncAdvRequestSlots(node) {
  if (nodeClass(node) !== ADV_REQUEST_CLASS) return;
  normalizeAdvRequestNodeWidgets(node);
  for (let index = 0; index < (node.outputs || []).length; index += 1) {
    const output = node.outputs[index];
    const definition = advRequestOutputDefinition(advRequestOutputName(output));
    if (definition) {
      output.type = definition[1];
    }
    setAdvRequestOutputHidden(output, false);
  }
  syncAdvRequestOutputIndexMap(node);
}

function moveWidgetAfter(node, name, afterName) {
  const widgets = node.widgets || [];
  const currentIndex = widgets.findIndex((candidate) => candidate?.name === name);
  const afterIndex = widgets.findIndex((candidate) => candidate?.name === afterName);
  if (currentIndex < 0 || afterIndex < 0 || currentIndex === afterIndex + 1) return false;
  const [found] = widgets.splice(currentIndex, 1);
  const nextAfterIndex = widgets.findIndex((candidate) => candidate?.name === afterName);
  widgets.splice(nextAfterIndex + 1, 0, found);
  return true;
}

function moveWidgetBefore(node, name, beforeName) {
  const widgets = node.widgets || [];
  const currentIndex = widgets.findIndex((candidate) => candidate?.name === name);
  const beforeIndex = widgets.findIndex((candidate) => candidate?.name === beforeName);
  if (currentIndex < 0 || beforeIndex < 0 || currentIndex === beforeIndex - 1) return false;
  const [found] = widgets.splice(currentIndex, 1);
  const nextBeforeIndex = widgets.findIndex((candidate) => candidate?.name === beforeName);
  widgets.splice(Math.max(0, nextBeforeIndex), 0, found);
  return true;
}

function removeWidget(node, found) {
  const widgets = node.widgets || [];
  const index = widgets.indexOf(found);
  if (index < 0) return false;
  widgets.splice(index, 1);
  return true;
}

function ensureAdvRequestRefreshWidget(node) {
  let refresh = widget(node, "Refresh") || widget(node, "refresh image slots");
  if (!refresh) {
    refresh = node.addWidget?.("button", "Refresh", null, () => refreshAdvRequestOutputs(node));
  }
  if (!refresh) return false;
  refresh.name = "Refresh";
  disableWidgetSerialization(refresh);
  refresh.callback = () => refreshAdvRequestOutputs(node);
  return moveWidgetAfter(node, "Refresh", "image_count");
}

function testModeWidget(node) {
  return (node.widgets || []).find((candidate) => {
    return candidate?.__psBridgeTestModeWidget || candidate?.name === TEST_MODE_WIDGET_NAME;
  });
}

function updateAdvRequestTestModeWidget(node) {
  const found = testModeWidget(node);
  if (!found) return false;
  found.name = TEST_MODE_WIDGET_NAME;
  found.type = "toggle";
  found.value = currentGraphTestMode;
  disableWidgetSerialization(found);
  found.__psBridgeTestModeWidget = true;
  found.options = { ...(found.options || {}), on: "ON", off: "OFF" };
  found.callback = toggleCurrentGraphTestMode;
  return true;
}

function refreshAllAdvRequestTestModeWidgets() {
  for (const node of graphNodes()) {
    if (nodeClass(node) === ADV_REQUEST_CLASS) {
      updateAdvRequestTestModeWidget(node);
    }
  }
}

function ensureAdvRequestTestModeWidget(node) {
  if (!isCurrentGraphNode(node)) return false;
  let found = testModeWidget(node);
  if (found && found.type !== "toggle") {
    removeWidget(node, found);
    found = null;
  }
  if (!found) {
    found = node.addWidget?.("toggle", TEST_MODE_WIDGET_NAME, currentGraphTestMode, toggleCurrentGraphTestMode, {
      on: "ON",
      off: "OFF",
    });
  }
  if (!found) return false;
  updateAdvRequestTestModeWidget(node);
  return moveWidgetBefore(node, TEST_MODE_WIDGET_NAME, PS_SUMMARY_WIDGET_NAME);
}

function moveWidgetToEnd(node, name) {
  const widgets = node.widgets || [];
  const currentIndex = widgets.findIndex((candidate) => candidate?.name === name);
  if (currentIndex < 0 || currentIndex === widgets.length - 1) return false;
  const [found] = widgets.splice(currentIndex, 1);
  widgets.push(found);
  return true;
}

function advRequestWidgetRequest(node) {
  return {
    image_count: advRequestImageCount(node),
    prompt: String(widgetValue(node, "prompt", "") || ""),
    resolution: String(widgetValue(node, "resolution", "1k") || ""),
    strength: numberValue(widgetValue(node, "strength", 0.65), 0.65),
    batch_count: advRequestBatchCount(widgetValue(node, "batch_count", 1)),
    seed: Math.max(0, Math.round(numberValue(widgetValue(node, "seed", 42), 42))),
    control_after_generate: String(widgetValue(node, "control_after_generate", "randomize") || "randomize"),
  };
}

function currentSummaryData() {
  return {
    request_id: activeRequestId,
    feature_id: activeFeatureId,
    execution_mode: activeExecutionMode,
    images: activeBridgeState.images || {},
    image_count: activeBridgeState.image_count || 0,
    multi_image: activeBridgeState.multi_image || false,
    canvas: activeBridgeState.canvas || null,
    selection: activeBridgeState.selection || null,
    updated_at: activeBridgeState.updated_at || 0,
  };
}

function isCurrentGraphNode(node) {
  return !node.graph || node.graph === app.graph;
}

function styleSummaryElement(element) {
  element.style.boxSizing = "border-box";
  element.style.width = "100%";
  element.style.minHeight = "24px";
  element.style.padding = "4px 8px";
  element.style.border = "1px solid rgba(160,160,160,0.35)";
  element.style.borderRadius = "4px";
  element.style.background = "rgba(0,0,0,0.18)";
  element.style.color = "rgba(235,235,235,0.92)";
  element.style.font = "11px/1.25 monospace";
  element.style.whiteSpace = "nowrap";
  element.style.overflow = "hidden";
  element.style.textOverflow = "ellipsis";
  element.style.pointerEvents = "none";
}

function ensureAdvRequestSummaryWidget(node) {
  if (!isCurrentGraphNode(node)) return false;
  let summary = widget(node, PS_SUMMARY_WIDGET_NAME);
  if (summary) {
    disableWidgetSerialization(summary);
    return moveWidgetToEnd(node, PS_SUMMARY_WIDGET_NAME);
  }

  if (typeof document !== "undefined" && typeof node.addDOMWidget === "function") {
    const container = document.createElement("div");
    styleSummaryElement(container);
    summary = node.addDOMWidget(PS_SUMMARY_WIDGET_NAME, "custom", container, {
      serialize: false,
      getValue() {
        return container.textContent || "";
      },
      setValue(value) {
        container.textContent = String(value || "");
      },
    });
    if (summary) {
      summary.name = PS_SUMMARY_WIDGET_NAME;
      disableWidgetSerialization(summary);
      summary.__psBridgeSummaryElement = container;
      summary.computeSize = function () {
        return [node.size?.[0] || 300, 32];
      };
      moveWidgetToEnd(node, PS_SUMMARY_WIDGET_NAME);
      return true;
    }
  }

  summary = node.addWidget?.("text", PS_SUMMARY_WIDGET_NAME, "", () => {});
  if (!summary) return false;
  disableWidgetSerialization(summary);
  summary.readonly = true;
  summary.disabled = true;
  summary.computeSize = function () {
    return [node.size?.[0] || 300, 32];
  };
  moveWidgetToEnd(node, PS_SUMMARY_WIDGET_NAME);
  return true;
}

function updateAdvRequestSummary(node) {
  if (nodeClass(node) !== ADV_REQUEST_CLASS || !isCurrentGraphNode(node)) return;
  ensureAdvRequestSummaryWidget(node);
  const summary = widget(node, PS_SUMMARY_WIDGET_NAME);
  if (!summary) return;
  const text = formatAdvRequestSummary(currentSummaryData(), advRequestWidgetRequest(node));
  summary.value = text;
  if (summary.__psBridgeSummaryElement) {
    summary.__psBridgeSummaryElement.textContent = text;
  }
}

function configureAdvRequestNode(node) {
  if (nodeClass(node) !== ADV_REQUEST_CLASS) return;
  patchAdvRequestWidgetCallbacks(node);
  let changed = false;
  changed = ensureAdvRequestRefreshWidget(node) || changed;
  changed = ensureAdvRequestTestModeWidget(node) || changed;
  changed = ensureAdvRequestSummaryWidget(node) || changed;
  syncAdvRequestSlots(node);
  updateAdvRequestSummary(node);
  if (!node.__psBridgeAdvRequestOutputsInitialized) {
    refreshAdvRequestOutputs(node);
  } else if (changed) {
    node.setSize?.(node.computeSize?.() || node.size);
    markCanvasDirty();
  }
}

function patchAdvRequestWidgetCallbacks(node) {
  const imageCountWidget = widget(node, "image_count");
  if (imageCountWidget && !imageCountWidget.__advRequestCallbackPatched) {
    const originalCallback = imageCountWidget.callback;
    imageCountWidget.callback = function (...args) {
      const result = originalCallback?.apply(this, args);
      setWidgetValue(node, "image_count", advRequestImageCount(node), { callback: false });
      updateAdvRequestSummary(node);
      markCanvasDirty();
      return result;
    };
    imageCountWidget.__advRequestCallbackPatched = true;
  }

  const batchCountWidget = widget(node, "batch_count");
  if (batchCountWidget && !batchCountWidget.__advRequestCallbackPatched) {
    const originalCallback = batchCountWidget.callback;
    batchCountWidget.callback = function (...args) {
      const result = originalCallback?.apply(this, args);
      setWidgetValue(node, "batch_count", advRequestBatchCount(batchCountWidget.value), { callback: false });
      updateAdvRequestSummary(node);
      markCanvasDirty();
      return result;
    };
    batchCountWidget.__advRequestCallbackPatched = true;
  }

  for (const name of ["prompt", "resolution", "strength", "seed"]) {
    const found = widget(node, name);
    if (!found || found.__advRequestSummaryCallbackPatched) continue;
    const originalCallback = found.callback;
    found.callback = function (...args) {
      const result = originalCallback?.apply(this, args);
      updateAdvRequestSummary(node);
      markCanvasDirty();
      return result;
    };
    found.__advRequestSummaryCallbackPatched = true;
  }
}

function patchAdvRequestNode(nodeType) {
  if (nodeType.prototype.__advRequestPatched) return;
  nodeType.prototype.__advRequestPatched = true;

  const originalOnConfigure = nodeType.prototype.onConfigure;
  nodeType.prototype.onConfigure = function (...args) {
    const result = originalOnConfigure?.apply(this, args);
    restoreAdvRequestNodeWidgets(this, args[0]);
    configureAdvRequestNode(this);
    return result;
  };

  const originalOnSerialize = nodeType.prototype.onSerialize;
  nodeType.prototype.onSerialize = function (...args) {
    const result = originalOnSerialize?.apply(this, args);
    serializeAdvRequestNode(this, args[0]);
    return result;
  };

  const originalConnect = nodeType.prototype.connect;
  if (typeof originalConnect === "function") {
    nodeType.prototype.connect = function (slot, targetNode, targetSlot, ...args) {
      const normalizedSlot = normalizeAdvRequestOutgoingSlot(this, slot);
      return originalConnect.call(this, normalizedSlot, targetNode, targetSlot, ...args);
    };
  }
}

function isHiddenAdvRequestOutputLink(graph, link) {
  if (!graph || !link) return false;
  const originNode = graph.getNodeById?.(link.origin_id);
  if (!originNode || nodeClass(originNode) !== ADV_REQUEST_CLASS) return false;
  const output = originNode.outputs?.[Number(link.origin_slot)];
  return Boolean(output && advRequestOutputShouldHide(originNode, output));
}

function installAdvRequestCanvasPatch(attempt = 0) {
  const canvasPrototype = app.canvas?.constructor?.prototype || globalThis.LGraphCanvas?.prototype;
  if (!canvasPrototype) {
    if (attempt < 20) {
      setTimeout(() => installAdvRequestCanvasPatch(attempt + 1), 50);
    }
    return;
  }
  if (canvasPrototype.__psBridgeAdvRequestCanvasPatched) return;

  const originalDrawNode = canvasPrototype.drawNode;
  if (typeof originalDrawNode === "function") {
    canvasPrototype.drawNode = function (node, ctx) {
      return originalDrawNode.call(this, node, ctx);
    };
  }

  const originalRenderLink = canvasPrototype.renderLink;
  if (typeof originalRenderLink === "function") {
    canvasPrototype.renderLink = function (ctx, start, end, link, ...args) {
      if (isHiddenAdvRequestOutputLink(this.graph, link)) {
        try {
          link.path = new Path2D();
        } catch {
          link.path = null;
        }
        return undefined;
      }
      return originalRenderLink.call(this, ctx, start, end, link, ...args);
    };
  }

  canvasPrototype.__psBridgeAdvRequestCanvasPatched = true;
}

function remapAdvRequestPromptLinks(promptData, graph = app.graph) {
  const output = promptData?.output;
  if (!output || typeof output !== "object") return;

  const outputMapsByNodeId = new Map();
  for (const node of graphNodes(graph)) {
    if (nodeClass(node) !== ADV_REQUEST_CLASS) continue;
    configureAdvRequestNode(node);
    syncAdvRequestOutputIndexMap(node);
    outputMapsByNodeId.set(String(node.id), node.__psBridgeAdvRequestOutputIndexMap || []);
  }
  if (!outputMapsByNodeId.size) return;

  for (const node of Object.values(output)) {
    const inputs = node?.inputs;
    if (!inputs || typeof inputs !== "object") continue;
    for (const value of Object.values(inputs)) {
      if (!Array.isArray(value) || value.length < 2) continue;
      const outputMap = outputMapsByNodeId.get(String(value[0]));
      if (!outputMap) continue;
      const visibleIndex = Number(value[1]);
      if (!Number.isInteger(visibleIndex)) continue;
      const backendIndex = outputMap[visibleIndex];
      if (backendIndex !== undefined) {
        value[1] = backendIndex;
      }
    }
  }
}

function configureAdvRequestNodesForPrompt(graph = app.graph) {
  for (const node of graphNodes(graph)) {
    if (nodeClass(node) === ADV_REQUEST_CLASS) {
      configureAdvRequestNode(node);
    }
  }
  repairAdvRequestOutputLinksForGraph(graph);
}

function inputSlotForApiInput(node, inputName) {
  let toSlot = node.inputs?.findIndex((input) => input.name === inputName);
  if (toSlot == null || toSlot === -1) {
    try {
      const foundWidget = node.widgets?.find((candidate) => candidate.name === inputName);
      if (foundWidget && node.convertWidgetToInput?.(foundWidget)) {
        toSlot = node.inputs?.length - 1;
      }
    } catch {
      // Keep the existing ComfyUI importer behavior: skip inputs that cannot be converted.
    }
  }
  return toSlot;
}

function repairAdvRequestApiLinks(apiData, graph = app.graph) {
  if (!apiData || typeof apiData !== "object") return;
  configureAdvRequestNodesForPrompt(graph);
  for (const [nodeId, nodeData] of Object.entries(apiData)) {
    const targetNode = graph.getNodeById?.(nodeId);
    if (!targetNode || !nodeData?.inputs) continue;
    for (const [inputName, value] of Object.entries(nodeData.inputs)) {
      if (!Array.isArray(value) || value.length < 2) continue;
      const [fromId, fromSlot] = value;
      const fromNode = graph.getNodeById?.(fromId);
      if (!fromNode || nodeClass(fromNode) !== ADV_REQUEST_CLASS) continue;
      configureAdvRequestNode(fromNode);
      const toSlot = inputSlotForApiInput(targetNode, inputName);
      if (toSlot == null || toSlot === -1) continue;
      let normalizedFromSlot = ensureAdvRequestBackendOutputVisible(fromNode, fromSlot);
      if (normalizedFromSlot < 0) {
        normalizedFromSlot = normalizeAdvRequestOutgoingSlot(fromNode, fromSlot);
      }
      const currentLinkId = targetNode.inputs?.[toSlot]?.link;
      const currentLink = currentLinkId != null ? graph.links?.[currentLinkId] : null;
      if (
        currentLink
        && String(currentLink.origin_id) === String(fromId)
        && Number(currentLink.origin_slot) === Number(normalizedFromSlot)
      ) {
        continue;
      }
      if (currentLinkId != null) {
        targetNode.disconnectInput?.(toSlot);
      }
      fromNode.connect(Number(normalizedFromSlot), targetNode, toSlot);
    }
  }
  configureAdvRequestNodesForPrompt(graph);
  graph.setDirtyCanvas?.(true, true);
}

function installGraphToPromptPatch() {
  if (app.__psBridgeGraphToPromptPatched || typeof app.graphToPrompt !== "function") return;
  const originalGraphToPrompt = app.graphToPrompt;
  app.graphToPrompt = async function (...args) {
    const graph = args[0] || this.graph || app.graph;
    const requestId = graph?.__psBridgeRequestId || activeRequestId;
    configureAdvRequestNodesForPrompt(graph);
    const promptData = await originalGraphToPrompt.apply(this, args);
    remapAdvRequestPromptLinks(promptData, graph);
    applyRequestIdToApiPrompt(promptData.output, requestId);
    if (graph?.__psBridgeRequestId === requestId) {
      setHiddenProperty(graph, "__psBridgeRequestId", "");
    }
    return promptData;
  };
  app.__psBridgeGraphToPromptPatched = true;
}

function installApiJsonLoadPatch() {
  if (app.__psBridgeLoadApiJsonPatched || typeof app.loadApiJson !== "function") return;
  const originalLoadApiJson = app.loadApiJson;
  app.loadApiJson = function (apiData, ...args) {
    const result = originalLoadApiJson.call(this, apiData, ...args);
    repairAdvRequestApiLinks(apiData, this.graph || app.graph);
    return result;
  };
  app.__psBridgeLoadApiJsonPatched = true;
}

function installActiveGraphListeners() {
  if (app.__psBridgeActiveGraphListenersInstalled) return;
  if (typeof window !== "undefined") {
    window.addEventListener("hashchange", () => reportActiveGraphState({ dirty: false }));
    window.addEventListener("focus", () => reportActiveGraphState({ dirty: false }));
    window.addEventListener("blur", () => reportActiveGraphState({ dirty: false, markActive: false }));
  }
  if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", () => {
      reportActiveGraphState({ dirty: false, markActive: pageVisible() });
    });
  }
  app.__psBridgeActiveGraphListenersInstalled = true;
}

function collectSlots(graph = app.graph) {
  const requestNode = graphNodes(graph).find((node) => nodeClass(node) === ADV_REQUEST_CLASS);
  if (requestNode) {
    configureAdvRequestNode(requestNode);
  }
  const graphState = activeGraphMetadata(graph);
  return {
    request_id: activeRequestId,
    feature_id: activeFeatureId,
    execution_mode: activeExecutionMode,
    graph_id: graphState.graph_id,
    current_graph_test_mode: graphState.current_graph_test_mode,
    page_visible: graphState.page_visible,
    window_focused: graphState.window_focused,
    last_active_at: graphState.last_active_at,
    graph: currentGraphStatus(graph),
    slots: emptySlots(),
    adv_request: requestNode ? advRequestWidgetRequest(requestNode) : {},
  };
}

function currentGraphStatus(graph = app.graph) {
  return graphStatusFromNodeClasses(graphNodes(graph).map((node) => nodeClass(node)));
}

function applyRequestIdToApiPrompt(prompt, requestId = activeRequestId) {
  for (const [, node] of apiPromptEntries(prompt)) {
    if (!isSendNodeClass(node.class_type)) continue;
    node.inputs = { ...(node.inputs || {}), request_id: requestId };
  }
}

function syncCurrentAdvRequestFromRunData(data) {
  const status = currentGraphStatus(app.graph);
  if (!status.has_adv_request) {
    return status;
  }
  setActiveBridgeState(data);
  applyAdvRequestToGraph(data);
  return status;
}

async function fetchWorkflow(featureId) {
  if (!featureId) {
    throw new Error("Missing feature_id");
  }
  const response = await api.fetchApi(`/ps-bridge/workflows/${encodeURIComponent(featureId)}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Workflow ${featureId} failed to load (${response.status})`);
  }
  return await response.json();
}

function cloneWorkflow(workflow) {
  if (typeof structuredClone === "function") {
    return structuredClone(workflow);
  }
  return JSON.parse(JSON.stringify(workflow));
}

function workflowForGraphConfigure(workflow) {
  return sanitizeAdvRequestWorkflowData(cloneWorkflow(workflow));
}

function isApiPromptNode(value) {
  return Boolean(value && typeof value === "object" && typeof value.class_type === "string" && value.inputs);
}

function isApiPromptWorkflow(workflow) {
  return Boolean(
    workflow
      && typeof workflow === "object"
      && !Array.isArray(workflow.nodes)
      && Object.values(workflow).some(isApiPromptNode),
  );
}

function apiPromptEntries(prompt) {
  return Object.entries(prompt || {}).filter(([, node]) => isApiPromptNode(node));
}

function promptFromSlots(slots) {
  const promptSlots = slots?.prompt;
  if (!promptSlots || typeof promptSlots !== "object") return "";
  for (const key of ["prompt", "positive", "main", "text"]) {
    if (promptSlots[key] !== undefined && promptSlots[key] !== null) {
      return String(promptSlots[key]);
    }
  }
  const values = Object.values(promptSlots);
  return values.length === 1 ? String(values[0]) : "";
}

function paramsFromRunData(data) {
  const params = {};
  for (const key of REQUEST_PARAM_OBJECT_KEYS) {
    const value = data?.[key];
    if (value && typeof value === "object" && !Array.isArray(value)) {
      Object.assign(params, value);
    }
  }
  for (const key of REQUEST_TOP_LEVEL_PARAM_KEYS) {
    if (data?.[key] !== undefined && data[key] !== null) {
      params[key] = data[key];
    }
  }
  const slots = data?.slots || {};
  for (const group of ["seed", "float", "int", "boolean"]) {
    const values = slots[group];
    if (values && typeof values === "object" && Object.keys(values).length) {
      params[group] = {
        ...(params[group] && typeof params[group] === "object" ? params[group] : {}),
        ...values,
      };
    }
  }
  return params;
}

function paramsJsonString(value) {
  if (typeof value === "string") {
    return value.trim() ? value : "{}";
  }
  if (value && typeof value === "object") {
    return JSON.stringify(value);
  }
  return "{}";
}

function paramsJsonObject(value) {
  if (typeof value === "string" && value.trim()) {
    try {
      const parsed = JSON.parse(value);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    } catch {
      return {};
    }
  }
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function singleParamValue(value) {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const values = Object.values(value);
    return values.length === 1 ? values[0] : undefined;
  }
  return value;
}

function paramValue(params, ...keys) {
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(params, key)) {
      const value = singleParamValue(params[key]);
      if (value !== undefined) return value;
    }
  }
  for (const group of ["seed", "float", "int", "boolean"]) {
    const values = params[group];
    if (!values || typeof values !== "object" || Array.isArray(values)) continue;
    for (const key of keys) {
      if (Object.prototype.hasOwnProperty.call(values, key)) {
        return values[key];
      }
    }
  }
  return undefined;
}

function advRequestForRunData(data) {
  const payload = data?.payload && typeof data.payload === "object" ? data.payload : {};
  const request =
    (data?.adv_request && typeof data.adv_request === "object" ? data.adv_request : null)
    || (payload?.adv_request && typeof payload.adv_request === "object" ? payload.adv_request : {})
    || {};
  const params = {
    ...paramsFromRunData(data),
    ...paramsFromRunData(payload),
    ...paramsJsonObject(request.params_json ?? request.paramsJson),
  };
  const prompt = request.prompt ?? payload.prompt ?? data?.prompt ?? promptFromSlots(payload.slots || data?.slots);
  const imageCount = Math.max(
    1,
    Math.min(
      ADV_REQUEST_MAX_IMAGES,
      Math.round(numberValue(request.image_count ?? payload.image_count ?? data?.image_count ?? paramValue(params, "image_count") ?? 1, 1)),
    ),
  );
  const batchCount =
    request.batch_count
    ?? request.batchCount
    ?? payload.batch_count
    ?? payload.batchCount
    ?? data?.batch_count
    ?? data?.batchCount
    ?? paramValue(params, "batch_count", "batchCount")
    ?? 1;
  const seed = request.seed ?? payload.seed ?? data?.seed ?? paramValue(params, "seed", "MAIN") ?? 42;
  const resolution = request.resolution ?? payload.resolution ?? data?.resolution ?? paramValue(params, "resolution");
  const normalizedSeed = normalizeAdvRequestWidgetPatch({ seed }).seed;
  return {
    image_count: imageCount,
    prompt: String(prompt ?? ""),
    resolution: String(resolution || "1k"),
    strength: Math.max(
      0,
      Math.min(1, numberValue(request.strength ?? payload.strength ?? data?.strength ?? paramValue(params, "strength"), 0.65)),
    ),
    batch_count: advRequestBatchCount(batchCount),
    seed: normalizedSeed,
    params_json: paramsJsonString(request.params_json ?? request.paramsJson ?? params),
  };
}

function explicitObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : null;
}

function explicitValue(sources, ...keys) {
  for (const source of sources) {
    if (!source) continue;
    for (const key of keys) {
      if (Object.prototype.hasOwnProperty.call(source, key) && source[key] !== undefined && source[key] !== null) {
        return source[key];
      }
    }
  }
  return undefined;
}

function normalizeAdvRequestPatchValue(name, value) {
  return normalizeAdvRequestWidgetPatch({ [name]: value })[name];
}

function advRequestPatchForRunData(data) {
  const root = explicitObject(data) || {};
  const payload = explicitObject(root.payload) || {};
  const sources = [
    explicitObject(root.adv_request),
    explicitObject(payload.adv_request),
    payload,
    root,
  ].filter(Boolean);
  const params = {
    ...paramsFromRunData(root),
    ...paramsFromRunData(payload),
  };
  const patch = {};
  const set = (name, value) => {
    if (value !== undefined) {
      patch[name] = normalizeAdvRequestPatchValue(name, value);
    }
  };

  set("image_count", explicitValue(sources, "image_count") ?? paramValue(params, "image_count"));

  let prompt = explicitValue(sources, "prompt");
  if (prompt === undefined) {
    prompt = paramValue(params, "prompt");
  }
  if (prompt === undefined) {
    const slotSources = [payload.slots, root.slots].filter((slots) => explicitObject(slots?.prompt));
    for (const slots of slotSources) {
      const values = Object.values(slots.prompt);
      if (values.length) {
        prompt = promptFromSlots(slots);
        break;
      }
    }
  }
  set("prompt", prompt);
  set("resolution", explicitValue(sources, "resolution") ?? paramValue(params, "resolution"));
  set("strength", explicitValue(sources, "strength") ?? paramValue(params, "strength"));
  set(
    "batch_count",
    explicitValue(sources, "batch_count", "batchCount") ?? paramValue(params, "batch_count", "batchCount"),
  );
  set("seed", explicitValue(sources, "seed") ?? paramValue(params, "seed", "MAIN"));
  set(
    "control_after_generate",
    explicitValue(sources, "control_after_generate", "controlAfterGenerate")
      ?? paramValue(params, "control_after_generate", "controlAfterGenerate"),
  );

  return patch;
}

function applyAdvRequestToApiPrompt(prompt, data) {
  const request = advRequestForRunData(data);
  for (const [, node] of apiPromptEntries(prompt)) {
    if (node.class_type !== ADV_REQUEST_CLASS) continue;
    const inputs = node.inputs || {};
    inputs.image_count = request.image_count;
    inputs.prompt = request.prompt;
    inputs.resolution = request.resolution;
    inputs.strength = request.strength;
    inputs.batch_count = request.batch_count;
    inputs.seed = request.seed;
    inputs.params_json = request.params_json;
    for (let index = 1; index <= ADV_REQUEST_MAX_IMAGES; index += 1) {
      inputs[`image_${index}_file`] = "";
    }
    inputs.mask_image_file = "";
    node.inputs = inputs;
  }
}

function applyAdvRequestToGraph(data, graph = app.graph) {
  const request = advRequestForRunData(data);
  for (const node of graphNodes(graph)) {
    if (nodeClass(node) !== ADV_REQUEST_CLASS) continue;
    setWidgetValue(node, "image_count", request.image_count, { callback: false });
    setWidgetValue(node, "prompt", request.prompt, { callback: false });
    setWidgetValue(node, "resolution", request.resolution, { callback: false });
    setWidgetValue(node, "strength", request.strength, { callback: false });
    setWidgetValue(node, "batch_count", request.batch_count, { callback: false });
    setWidgetValue(node, "seed", request.seed, { callback: false });
    syncAdvRequestSlots(node);
    updateAdvRequestSummary(node);
    if (graph !== app.graph) {
      refreshAdvRequestOutputs(node, request.image_count);
    }
  }
  if (graph === app.graph) {
    markCanvasDirty();
  }
}

function applyAdvRequestPatchToGraph(data, graph = app.graph) {
  const patch = advRequestPatchForRunData(data);
  if (!Object.keys(patch).length) return false;
  for (const node of graphNodes(graph)) {
    if (nodeClass(node) !== ADV_REQUEST_CLASS) continue;
    for (const [name, value] of Object.entries(patch)) {
      setWidgetValue(node, name, value, { callback: false });
    }
    syncAdvRequestSlots(node);
    updateAdvRequestSummary(node);
    if (Object.prototype.hasOwnProperty.call(patch, "image_count")) {
      refreshAdvRequestOutputs(node, patch.image_count);
    }
  }
  if (graph === app.graph) {
    markCanvasDirty();
  }
  return true;
}

function collectApiPromptSnapshot(prompt, data) {
  const requestNode = apiPromptEntries(prompt).find(([, node]) => node.class_type === ADV_REQUEST_CLASS)?.[1];
  return {
    request_id: activeRequestId,
    feature_id: activeFeatureId,
    slots: emptySlots(),
    adv_request: requestNode ? advRequestForRunData(data) : {},
  };
}

function createDetachedGraph(workflow) {
  const GraphConstructor = app.graph?.constructor;
  if (!GraphConstructor) {
    throw new Error("Unable to create detached ComfyUI graph");
  }
  const graph = new GraphConstructor();
  graph.configure(workflowForGraphConfigure(workflow));
  return graph;
}

function prepareGraphForPrompt(graph) {
  for (const node of graph.computeExecutionOrder(false)) {
    configureAdvRequestNode(node);
    if (node.widgets) {
      for (const widget of node.widgets) {
        widget.beforeQueued?.();
      }
    }
    const innerNodes = node.getInnerNodes ? node.getInnerNodes() : [node];
    for (const innerNode of innerNodes) {
      if (innerNode.isVirtualNode && innerNode.applyToGraph) {
        innerNode.applyToGraph();
      }
    }
  }
}

function runAfterQueued(graph, workflow) {
  for (const nodeData of workflow.nodes || []) {
    const node = graph.getNodeById?.(nodeData.id);
    if (!node?.widgets) continue;
    for (const widget of node.widgets) {
      widget.afterQueued?.();
    }
  }
}

function promptErrorMessage(error) {
  if (error?.response?.error?.message) {
    let message = error.response.error.message;
    if (error.response.error.details) {
      message += `: ${error.response.error.details}`;
    }
    return message;
  }
  return error?.message || String(error);
}

async function postPrompt(output, workflow, number = 0) {
  const body = {
    client_id: api.clientId || "",
    prompt: output,
    extra_data: { extra_pnginfo: { workflow } },
  };
  if (number === -1) {
    body.front = true;
  } else if (number !== 0) {
    body.number = number;
  }
  const response = await api.fetchApi("/prompt", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw { response: await response.json() };
  }
  return await response.json();
}

async function queueDetachedWorkflow(featureId, data) {
  const workflow = await fetchWorkflow(featureId);
  if (isApiPromptWorkflow(workflow)) {
    return await queueApiPromptWorkflow(workflow, data);
  }
  const graph = createDetachedGraph(workflow);
  setHiddenProperty(graph, "__psBridgeRequestId", String(data.request_id || activeRequestId || ""));
  setActiveBridgeState(data, graph);
  applyAdvRequestToGraph(data, graph);
  clearSendNodePreviews(graph);
  const slotsSnapshot = collectSlots(graph);
  prepareGraphForPrompt(graph);

  const promptData = await app.graphToPrompt(graph);
  assertBridgeSendNode(promptData.output);
  try {
    const result = await api.queuePrompt(0, promptData);
    runAfterQueued(graph, promptData.workflow);
    api.dispatchEvent(new CustomEvent("promptQueued", { detail: { number: 0, batchCount: 1 } }));
    await app.ui?.queue?.update?.();
    return { result, slotsSnapshot };
  } catch (error) {
    throw new Error(promptErrorMessage(error));
  }
}

async function queueCurrentGraphWorkflow(data) {
  const status = currentGraphStatus(app.graph);
  if (!status.has_adv_request) {
    throw new Error("Current graph is missing Adv_Request. Add Adv_Request or use an API workflow.");
  }
  setActiveBridgeState(data);
  applyAdvRequestToGraph(data);
  if (!status.has_send_to_ps) {
    throw new Error("Current graph is missing Adv_SendToPS, so generated images cannot be returned to Photoshop.");
  }
  clearSendNodePreviews();
  const slotsSnapshot = collectSlots(app.graph);
  prepareGraphForPrompt(app.graph);
  setHiddenProperty(app.graph, "__psBridgeRequestId", String(data.request_id || activeRequestId || ""));

  const promptData = await app.graphToPrompt(app.graph);
  assertBridgeSendNode(promptData.output);
  try {
    const result = await api.queuePrompt(0, promptData);
    runAfterQueued(app.graph, promptData.workflow);
    api.dispatchEvent(new CustomEvent("promptQueued", { detail: { number: 0, batchCount: 1 } }));
    await app.ui?.queue?.update?.();
    return { result, slotsSnapshot };
  } catch (error) {
    throw new Error(promptErrorMessage(error));
  }
}

async function queueApiPromptWorkflow(workflow, data) {
  const prompt = cloneWorkflow(workflow);
  setActiveBridgeState(data);
  applyAdvRequestToGraph(data);
  applyAdvRequestToApiPrompt(prompt, data);
  applyRequestIdToApiPrompt(prompt, String(data.request_id || activeRequestId || ""));
  assertBridgeSendNode(prompt);
  const slotsSnapshot = collectApiPromptSnapshot(prompt, data);
  try {
    const result = await postPrompt(prompt, prompt);
    api.dispatchEvent(new CustomEvent("promptQueued", { detail: { number: 0, batchCount: 1 } }));
    await app.ui?.queue?.update?.();
    return { result, slotsSnapshot };
  } catch (error) {
    throw new Error(promptErrorMessage(error));
  }
}

async function runWorkflow(data) {
  activeRequestId = data.request_id || "";
  activeFeatureId = data.feature_id || data.featureId || "";
  activeExecutionMode = normalizeExecutionMode(data.execution_mode || data.mode);
  if (!activeFeatureId) {
    throw new Error("Missing feature_id");
  }
  syncCurrentGraphTestModeFromGraph({ dirty: false });
  syncCurrentAdvRequestFromRunData(data);
  const shouldUseCurrentGraph = shouldUseCurrentGraphForRun(activeExecutionMode, currentGraphTestMode);
  if (!shouldUseCurrentGraph && activeExecutionMode === "api_workflow") {
    send("run_status", { request_id: activeRequestId, feature_id: activeFeatureId, execution_mode: activeExecutionMode, status: "backend_mode" });
    return;
  }
  send("run_status", { request_id: activeRequestId, feature_id: activeFeatureId, status: "loading_workflow" });
  send("run_status", { request_id: activeRequestId, feature_id: activeFeatureId, status: "queueing" });
  const { slotsSnapshot } = shouldUseCurrentGraph
    ? await queueCurrentGraphWorkflow(data)
    : await queueDetachedWorkflow(activeFeatureId, data);
  send("slots_snapshot", slotsSnapshot);
}

async function handleMessage(type, data) {
  if (type === "run_workflow") {
    try {
      await runWorkflow(data);
    } catch (error) {
      send("error", {
        request_id: activeRequestId,
        feature_id: activeFeatureId,
        message: error.message || String(error),
      });
    }
    return;
  }
  if (type === "slots_update") {
    const updatePayload = explicitObject(data.payload) || {};
    const executionMode = data.execution_mode || data.mode || updatePayload.execution_mode || updatePayload.mode;
    if (executionMode) {
      activeExecutionMode = normalizeExecutionMode(executionMode);
    }
    applyAdvRequestPatchToGraph(data);
    send("slots_snapshot", collectSlots());
  }
}

function installExecutionEvents() {
  api.addEventListener("execution_start", () => {
    clearSendNodePreviews();
    send("run_status", { request_id: activeRequestId, feature_id: activeFeatureId, status: "running" });
  });
  api.addEventListener("progress", ({ detail }) => {
    const value = Number(detail?.value ?? 0);
    const max = Number(detail?.max ?? 0);
    if (max > 0) {
      send("progress", {
        request_id: activeRequestId,
        feature_id: activeFeatureId,
        value,
        max,
        percent: Math.round((value / max) * 100),
      });
    }
  });
  api.addEventListener("executing", ({ detail }) => {
    if (!detail) {
      send("run_status", { request_id: activeRequestId, feature_id: activeFeatureId, status: "completed" });
    }
  });
  api.addEventListener("execution_error", ({ detail }) => {
    send("error", {
      request_id: activeRequestId,
      feature_id: activeFeatureId,
      message: detail?.exception_message || "ComfyUI execution error",
      details: detail,
    });
  });
}

app.registerExtension({
  name: EXTENSION_NAME,
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name === ADV_REQUEST_CLASS) {
      patchAdvRequestNode(nodeType);
    }
  },
  async beforeConfigureGraph(graphData) {
    sanitizeAdvRequestWorkflowData(graphData);
  },
  async setup() {
    installAdvRequestCanvasPatch();
    installGraphToPromptPatch();
    installApiJsonLoadPatch();
    installActiveGraphListeners();
    installExecutionEvents();
    syncCurrentGraphTestModeFromGraph({ dirty: false });
    connect();
  },
  async afterConfigureGraph() {
    syncCurrentGraphTestModeFromGraph({ dirty: false, adoptGraphId: true });
    for (const node of graphNodes()) {
      configureAdvRequestNode(node);
    }
    sendClientCapabilities();
    send("slots_snapshot", collectSlots());
  },
  nodeCreated(node) {
    if (nodeClass(node) === ADV_REQUEST_CLASS) {
      queueMicrotask(() => configureAdvRequestNode(node));
      connect();
      return;
    }
    if (isSendNodeClass(nodeClass(node))) {
      connect();
      setTimeout(() => send("slots_snapshot", collectSlots()), 0);
    }
  },
});
