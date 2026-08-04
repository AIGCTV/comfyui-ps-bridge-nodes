import assert from "node:assert/strict";
import {
  disableWidgetSerialization,
  normalizeAdvRequestNodeWidgets,
  restoreAdvRequestNodeWidgets,
  serializeAdvRequestNode,
} from "../js/adv_request_persistence.js";

const defaults = [1, "", "1k", 0.65, 1, 42, "randomize"];
const current = [3, "a cat", "2k", 0.4, 2, 1379, "fixed"];
const names = [
  "image_count",
  "prompt",
  "resolution",
  "strength",
  "batch_count",
  "seed",
  "control_after_generate",
];

function nodeWithValues(values = current) {
  const nativeWidgets = names.map((name, index) => ({ name, value: values[index] }));
  return {
    properties: { custom: "kept" },
    widgets: [
      nativeWidgets[0],
      { name: "Refresh", value: null },
      nativeWidgets[1],
      nativeWidgets[2],
      nativeWidgets[3],
      nativeWidgets[4],
      nativeWidgets[5],
      nativeWidgets[6],
      { name: "Test Mode", value: false },
      { name: "PS Summary", value: "runtime" },
    ],
  };
}

const node = nodeWithValues();
const serialized = { properties: { custom_serialized: "kept" }, widgets_values: Array(10).fill("shifted") };
serializeAdvRequestNode(node, serialized);
assert.deepEqual(serialized.widgets_values, current);
assert.equal(serialized.properties.ps_bridge_widget_schema, 2);
assert.equal(serialized.properties.custom_serialized, "kept");
assert.equal(node.properties.ps_bridge_widget_schema, 2);

const invalidNode = nodeWithValues(Array(7).fill("wrong"));
restoreAdvRequestNodeWidgets(invalidNode, {
  properties: { ps_bridge_widget_schema: 1 },
  widgets_values: Array(14).fill("legacy"),
});
assert.deepEqual(normalizeAdvRequestNodeWidgets(invalidNode), defaults);

const validNode = nodeWithValues(defaults);
restoreAdvRequestNodeWidgets(validNode, {
  properties: { ps_bridge_widget_schema: 2 },
  widgets_values: [6, "portrait", "4k", 0.8, 4, 99, "increment"],
});
assert.deepEqual(
  normalizeAdvRequestNodeWidgets(validNode),
  [6, "portrait", "4k", 0.8, 4, 99, "increment"],
);

const runtimeWidget = { name: "Refresh", options: { custom: true } };
disableWidgetSerialization(runtimeWidget);
assert.equal(runtimeWidget.serialize, false);
assert.deepEqual(runtimeWidget.options, { custom: true, serialize: false });

console.log("Adv_Request persistence lifecycle tests passed");
