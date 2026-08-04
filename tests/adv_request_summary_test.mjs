import assert from "node:assert/strict";
import {
  formatAdvRequestSummary,
  graphIdFromHash,
  graphStatusFromNodeClasses,
  graphTestModeState,
  normalizeExecutionMode,
  setGraphTestModeState,
  stableGraphId,
  shouldUseCurrentGraphForRun,
} from "../js/adv_request_summary.js";

assert.equal(normalizeExecutionMode("current-graph"), "current_graph");
assert.equal(normalizeExecutionMode("api"), "api_workflow");
assert.equal(normalizeExecutionMode(undefined), "auto");
assert.equal(shouldUseCurrentGraphForRun("auto", true), true);
assert.equal(shouldUseCurrentGraphForRun("auto", false), false);
assert.equal(shouldUseCurrentGraphForRun("current_graph", false), true);
assert.equal(shouldUseCurrentGraphForRun("api_workflow", true), true);
assert.equal(graphIdFromHash("#71d4f097-2d97-4e60-9f39-dc7b9a786283", "fallback"), "71d4f097-2d97-4e60-9f39-dc7b9a786283");
assert.equal(graphIdFromHash("", "fallback"), "fallback");
assert.equal(stableGraphId("new-hash", "", false), "new-hash");
assert.equal(stableGraphId("new-hash", "active-graph", false), "active-graph");
assert.equal(stableGraphId("new-hash", "active-graph", true), "new-hash");

let testModes = {};
testModes = setGraphTestModeState(testModes, "graph-a", true);
assert.equal(graphTestModeState(testModes, "graph-a"), true);
assert.equal(graphTestModeState(testModes, "graph-b"), false);
testModes = setGraphTestModeState(testModes, "graph-b", true);
testModes = setGraphTestModeState(testModes, "graph-a", false);
assert.equal(graphTestModeState(testModes, "graph-a"), false);
assert.equal(graphTestModeState(testModes, "graph-b"), true);

assert.equal(
  formatAdvRequestSummary(),
  "- x - | 1p | 1k | 0.65",
);

const single = formatAdvRequestSummary(
  {
    request_id: "task-001",
    feature_id: "roundtrip",
    execution_mode: "current_graph",
    image_count: 1,
    images: {
      IMAGE_1: { width: 1024, height: 768 },
    },
    selection: { filename: "selection.png" },
  },
  {
    prompt: "a portrait, soft light",
    resolution: "1k",
    strength: 0.65,
    batch_count: 2,
    seed: 12345,
  },
);
assert.equal(
  single,
  "1024 x 768 | 2p | 1k | 0.65",
);

const multi = formatAdvRequestSummary(
  {
    feature_id: "multi",
    image_count: 3,
    images: {
      IMAGE_3: { width: 256, height: 256 },
      IMAGE_1: { width: 512, height: 512 },
    },
  },
  {
    prompt: "word ".repeat(40),
    resolution: "2k",
    strength: 0.4,
    batch_count: 1,
    seed: 9,
  },
);
assert.equal(multi, "512 x 512 | 1p | 2k | 0.4");

assert.equal(
  formatAdvRequestSummary(
    {
      canvas: { width: 640, height: 480 },
    },
    {
      resolution: "1k",
      strength: 0.8,
      batch_count: 3,
      seed: 777,
    },
  ),
  "640 x 480 | 3p | 1k | 0.8",
);

assert.deepEqual(
  graphStatusFromNodeClasses(["Adv_Request", "KSampler", "Adv_SendToPS"]),
  { has_adv_request: true, has_send_to_ps: true, current_graph_ready: true },
);
assert.deepEqual(
  graphStatusFromNodeClasses(["Adv_Request", "KSampler"]),
  { has_adv_request: true, has_send_to_ps: false, current_graph_ready: false },
);
assert.deepEqual(
  graphStatusFromNodeClasses(["Adv_Request", "PSBridgeSendToPS"]),
  { has_adv_request: true, has_send_to_ps: false, current_graph_ready: false },
);

console.log("Adv_Request summary tests passed");
