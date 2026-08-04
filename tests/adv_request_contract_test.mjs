import assert from "node:assert/strict";
import {
  ADV_REQUEST_CLASS,
  ADV_REQUEST_SERIALIZED_WIDGET_ORDER,
  ADV_REQUEST_WIDGET_DEFAULTS,
  ADV_REQUEST_WIDGET_SCHEMA_PROPERTY,
  ADV_REQUEST_WIDGET_SCHEMA_VERSION,
  defaultAdvRequestWidgetValues,
  normalizeAdvRequestWidgetPatch,
  normalizeAdvRequestWidgetValues,
  sanitizeAdvRequestWorkflowData,
} from "../js/adv_request_contract.js";

const EXPECTED_WIDGET_ORDER = [
  "image_count",
  "prompt",
  "resolution",
  "strength",
  "batch_count",
  "seed",
  "control_after_generate",
];
const EXPECTED_DEFAULTS = [1, "", "1k", 0.65, 1, 42, "randomize"];

function currentNode(widgetsValues, properties = {}) {
  return {
    type: ADV_REQUEST_CLASS,
    properties: {
      "Node name for S&R": ADV_REQUEST_CLASS,
      [ADV_REQUEST_WIDGET_SCHEMA_PROPERTY]: ADV_REQUEST_WIDGET_SCHEMA_VERSION,
      ...properties,
    },
    widgets_values: widgetsValues,
  };
}

assert.equal(ADV_REQUEST_WIDGET_SCHEMA_VERSION, 2);
assert.equal(ADV_REQUEST_WIDGET_SCHEMA_PROPERTY, "ps_bridge_widget_schema");
assert.deepEqual(ADV_REQUEST_SERIALIZED_WIDGET_ORDER, EXPECTED_WIDGET_ORDER);
assert.deepEqual(
  EXPECTED_WIDGET_ORDER.map((name) => ADV_REQUEST_WIDGET_DEFAULTS[name]),
  EXPECTED_DEFAULTS,
);

const firstDefaults = defaultAdvRequestWidgetValues();
const secondDefaults = defaultAdvRequestWidgetValues();
assert.deepEqual(firstDefaults, EXPECTED_DEFAULTS);
assert.deepEqual(secondDefaults, EXPECTED_DEFAULTS);
assert.notEqual(firstDefaults, secondDefaults);
firstDefaults[0] = 6;
assert.deepEqual(defaultAdvRequestWidgetValues(), EXPECTED_DEFAULTS);

const validValues = [3, "cat", "2k", 0.7, 2, 1379, "fixed"];
assert.deepEqual(normalizeAdvRequestWidgetValues(validValues), validValues);
assert.deepEqual(
  normalizeAdvRequestWidgetValues([2.6, " cat ", "4k", -0.2, 9, -10, "increment"]),
  [3, " cat ", "4k", 0, 4, 0, "increment"],
);
assert.deepEqual(
  normalizeAdvRequestWidgetValues([99, null, "   ", Number.NaN, 0, Number.POSITIVE_INFINITY, {}]),
  [6, "", "1k", 0.65, 1, 42, "randomize"],
);
assert.equal(
  normalizeAdvRequestWidgetValues([1, "", "1k", 0.65, 1, Number.MAX_VALUE, "fixed"])[5],
  Number.MAX_SAFE_INTEGER,
);
for (const mode of ["fixed", "increment", "decrement", "randomize"]) {
  assert.equal(normalizeAdvRequestWidgetValues([...EXPECTED_DEFAULTS.slice(0, 6), mode])[6], mode);
}
assert.deepEqual(normalizeAdvRequestWidgetValues(), EXPECTED_DEFAULTS);
assert.deepEqual(normalizeAdvRequestWidgetValues("not-an-array"), EXPECTED_DEFAULTS);
assert.deepEqual(normalizeAdvRequestWidgetPatch({ strength: 0.4 }), { strength: 0.4 });
assert.deepEqual(normalizeAdvRequestWidgetPatch({ strength: "0.4", batch_count: "3" }), {
  strength: 0.4,
  batch_count: 3,
});
assert.deepEqual(
  normalizeAdvRequestWidgetPatch({ image_count: 99, batch_count: 0, control_after_generate: "invalid" }),
  { image_count: 6, batch_count: 1, control_after_generate: "randomize" },
);
assert.deepEqual(normalizeAdvRequestWidgetPatch({}), {});
assert.deepEqual(normalizeAdvRequestWidgetPatch(null), {});

const currentWorkflow = {
  nodes: [currentNode(structuredClone(validValues), { custom_property: "kept" })],
};
assert.equal(sanitizeAdvRequestWorkflowData(currentWorkflow), currentWorkflow);
assert.deepEqual(currentWorkflow.nodes[0].widgets_values, validValues);
assert.equal(currentWorkflow.nodes[0].properties.custom_property, "kept");
assert.equal(
  currentWorkflow.nodes[0].properties[ADV_REQUEST_WIDGET_SCHEMA_PROPERTY],
  ADV_REQUEST_WIDGET_SCHEMA_VERSION,
);

const invalidCurrentWorkflow = {
  nodes: [currentNode([12, 7, "", Number.NaN, -4, -1, "unsupported"])],
};
sanitizeAdvRequestWorkflowData(invalidCurrentWorkflow);
assert.deepEqual(
  invalidCurrentWorkflow.nodes[0].widgets_values,
  [6, "", "1k", 0.65, 1, 0, "randomize"],
);

for (const properties of [
  {},
  { [ADV_REQUEST_WIDGET_SCHEMA_PROPERTY]: 1 },
  { [ADV_REQUEST_WIDGET_SCHEMA_PROPERTY]: "2" },
]) {
  const workflow = {
    nodes: [{ type: ADV_REQUEST_CLASS, properties, widgets_values: structuredClone(validValues) }],
  };
  sanitizeAdvRequestWorkflowData(workflow);
  assert.deepEqual(workflow.nodes[0].widgets_values, EXPECTED_DEFAULTS);
  assert.equal(
    workflow.nodes[0].properties[ADV_REQUEST_WIDGET_SCHEMA_PROPERTY],
    ADV_REQUEST_WIDGET_SCHEMA_VERSION,
  );
}

for (const nonCurrentValues of [
  validValues.slice(0, 6),
  [...validValues, "runtime-widget"],
  Array.from({ length: 14 }, (_, index) => index),
]) {
  const workflow = { nodes: [currentNode(nonCurrentValues)] };
  sanitizeAdvRequestWorkflowData(workflow);
  assert.deepEqual(workflow.nodes[0].widgets_values, EXPECTED_DEFAULTS);
}

for (const missingValues of [undefined, null, "not-an-array", { length: 7 }]) {
  const node = currentNode(missingValues);
  if (missingValues === undefined) delete node.widgets_values;
  const workflow = { nodes: [node] };
  sanitizeAdvRequestWorkflowData(workflow);
  assert.deepEqual(workflow.nodes[0].widgets_values, EXPECTED_DEFAULTS);
  assert.equal(
    workflow.nodes[0].properties[ADV_REQUEST_WIDGET_SCHEMA_PROPERTY],
    ADV_REQUEST_WIDGET_SCHEMA_VERSION,
  );
}

const idempotentWorkflow = {
  nodes: [currentNode([4, "portrait", "2k", 0.55, 3, 99, "decrement"])],
};
sanitizeAdvRequestWorkflowData(idempotentWorkflow);
const once = structuredClone(idempotentWorkflow);
sanitizeAdvRequestWorkflowData(idempotentWorkflow);
assert.deepEqual(idempotentWorkflow, once);

const unrelatedNode = {
  type: "KSampler",
  properties: { untouched: true },
  widgets_values: [1, 2, 3],
};
const mixedWorkflow = {
  nodes: [structuredClone(unrelatedNode), currentNode(structuredClone(validValues))],
};
sanitizeAdvRequestWorkflowData(mixedWorkflow);
assert.deepEqual(mixedWorkflow.nodes[0], unrelatedNode);
assert.deepEqual(mixedWorkflow.nodes[1].widgets_values, validValues);

for (const invalidWorkflow of [null, undefined, [], {}, { nodes: null }]) {
  assert.equal(sanitizeAdvRequestWorkflowData(invalidWorkflow), invalidWorkflow);
}

console.log("Adv_Request schema v2 contract tests passed");
