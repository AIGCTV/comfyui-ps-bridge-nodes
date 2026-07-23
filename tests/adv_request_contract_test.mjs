import assert from "node:assert/strict";
import {
  ADV_REQUEST_BACKEND_OUTPUTS,
  migrateAdvRequestWorkflowData,
  normalizeAdvRequestLinkSlot,
  normalizedAdvRequestWidgetValues,
} from "../js/adv_request_contract.js";

const oldAdvRequestNode = {
  type: "Adv_Request",
  inputs: [
    { name: "image_1", type: "IMAGE" },
    { name: "mask", type: "MASK" },
    { name: "image_count", type: "INT", widget: { name: "image_count" } },
    { name: "prompt", type: "STRING", widget: { name: "prompt" } },
    { name: "resolution", type: "STRING", widget: { name: "resolution" } },
    { name: "strength", type: "FLOAT", widget: { name: "strength" } },
    { name: "batch_count", type: "INT", widget: { name: "batch_count" } },
    { name: "send_to_ps", type: "BOOLEAN", widget: { name: "send_to_ps" } },
    { name: "params_json", type: "STRING", widget: { name: "params_json" } },
    { name: "image_1_file", type: "STRING", widget: { name: "image_1_file" } },
    { name: "image_2_file", type: "STRING", widget: { name: "image_2_file" } },
    { name: "image_3_file", type: "STRING", widget: { name: "image_3_file" } },
    { name: "image_4_file", type: "STRING", widget: { name: "image_4_file" } },
    { name: "image_5_file", type: "STRING", widget: { name: "image_5_file" } },
    { name: "image_6_file", type: "STRING", widget: { name: "image_6_file" } },
    { name: "mask_image_file", type: "STRING", widget: { name: "mask_image_file" } },
  ],
  widgets_values: [4, "cat", "1k", 0.7, 3, true, "{\"strength\":0.7}", "", "", "", "", "", "", ""],
};

assert.deepEqual(
  normalizedAdvRequestWidgetValues(oldAdvRequestNode).slice(0, 7),
  [4, "cat", "1k", 0.7, 3, 42, "{\"strength\":0.7}"],
);

const workflow = { nodes: [structuredClone(oldAdvRequestNode)] };
migrateAdvRequestWorkflowData(workflow);
assert.deepEqual(workflow.nodes[0].widgets_values.slice(0, 7), [4, "cat", "1k", 0.7, 3, 42, "{\"strength\":0.7}"]);

const oldOutputs = [
  ["IMAGE_1", "IMAGE"],
  ["IMAGE_2", "IMAGE"],
  ["IMAGE_3", "IMAGE"],
  ["IMAGE_4", "IMAGE"],
  ["IMAGE_5", "IMAGE"],
  ["IMAGE_6", "IMAGE"],
  ["MASK", "MASK"],
  ["PROMPT", "STRING"],
  ["RESOLUTION", "STRING"],
  ["STRENGTH", "FLOAT"],
  ["BATCH_COUNT", "INT"],
  ["SEND_TO_PS", "BOOLEAN"],
  ["PARAMS_JSON", "STRING"],
  ["REQUEST_JSON", "STRING"],
  ["WIDTH", "INT"],
  ["HEIGHT", "INT"],
].map(([name, type]) => ({ name, type }));

assert.equal(
  normalizeAdvRequestLinkSlot({
    originSlot: 7,
    outputs: oldOutputs,
    targetInputName: "text",
    targetNodeClass: "CLIPTextEncode",
    targetInputType: "STRING",
  }),
  7,
);
assert.equal(
  normalizeAdvRequestLinkSlot({
    originSlot: 14,
    outputs: oldOutputs,
    targetInputName: "width",
    targetInputType: "INT",
  }),
  12,
);

const priorBackendOutputs = [
  ["IMAGE_1", "IMAGE"],
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
  ["IMAGE_2", "IMAGE"],
  ["IMAGE_3", "IMAGE"],
  ["IMAGE_4", "IMAGE"],
  ["IMAGE_5", "IMAGE"],
  ["IMAGE_6", "IMAGE"],
].map(([name, type]) => ({ name, type }));

assert.equal(
  normalizeAdvRequestLinkSlot({
    originSlot: 11,
    outputs: priorBackendOutputs,
    targetInputName: "image",
    targetInputType: "IMAGE",
  }),
  1,
);
assert.equal(
  normalizeAdvRequestLinkSlot({
    originSlot: 7,
    outputs: priorBackendOutputs,
    targetInputName: "width",
    targetInputType: "INT",
  }),
  12,
);

const backendOutputs = ADV_REQUEST_BACKEND_OUTPUTS.map(([name, type]) => ({ name, type }));
assert.deepEqual(ADV_REQUEST_BACKEND_OUTPUTS, [
  ["IMAGE_1", "IMAGE"],
  ["IMAGE_2", "IMAGE"],
  ["IMAGE_3", "IMAGE"],
  ["IMAGE_4", "IMAGE"],
  ["IMAGE_5", "IMAGE"],
  ["IMAGE_6", "IMAGE"],
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
]);

const visibleBackendIndexesForCount = (count) => [
  0,
  ...Array.from({ length: count - 1 }, (_, index) => index + 1),
  6,
  7,
  8,
  9,
  10,
  11,
  12,
  13,
];
const visibleBackendIndexesForCount1 = [0, 6, 7, 8, 9, 10, 11, 12, 13];
assert.deepEqual(
  visibleBackendIndexesForCount1.map((index) => backendOutputs[index].name),
  ["IMAGE_1", "MASK", "PROMPT", "RESOLUTION", "STRENGTH", "BATCH_COUNT", "SEED", "WIDTH", "HEIGHT"],
);

const visibleBackendIndexesForCount4 = [0, 1, 2, 3, 6, 7, 8, 9, 10, 11, 12, 13];
assert.equal(
  normalizeAdvRequestLinkSlot({
    originSlot: 4,
    outputs: backendOutputs,
    visibleBackendIndexes: visibleBackendIndexesForCount4,
    targetInputName: "mask",
    targetInputType: "MASK",
    preferVisibleSlot: true,
  }),
  6,
);
assert.equal(
  normalizeAdvRequestLinkSlot({
    originSlot: 6,
    outputs: backendOutputs,
    visibleBackendIndexes: visibleBackendIndexesForCount4,
    targetInputName: "mask",
    targetInputType: "MASK",
    preferVisibleSlot: true,
  }),
  6,
);

for (let imageIndex = 2; imageIndex <= 6; imageIndex += 1) {
  const visibleSlot = imageIndex - 1;
  const backendSlot = imageIndex - 1;
  const visibleBackendIndexes = visibleBackendIndexesForCount(imageIndex);

  assert.equal(backendOutputs[backendSlot].name, `IMAGE_${imageIndex}`);
  assert.equal(backendOutputs[backendSlot].type, "IMAGE");
  assert.equal(
    normalizeAdvRequestLinkSlot({
      originSlot: visibleSlot,
      outputs: backendOutputs,
      visibleBackendIndexes,
      preferVisibleSlot: true,
    }),
    backendSlot,
  );
  assert.equal(
    normalizeAdvRequestLinkSlot({
      originSlot: visibleSlot,
      outputs: backendOutputs,
      visibleBackendIndexes,
      targetInputName: "text",
      targetNodeClass: "CLIPTextEncode",
      targetInputType: "STRING",
      preferVisibleSlot: true,
    }),
    backendSlot,
  );
}

assert.equal(
  normalizeAdvRequestLinkSlot({
    originSlot: 4,
    outputs: backendOutputs,
    visibleBackendIndexes: visibleBackendIndexesForCount4,
    targetInputName: "width",
    targetInputType: "INT",
    preferVisibleSlot: true,
  }),
  6,
);

assert.equal(
  normalizeAdvRequestLinkSlot({
    originSlot: 9,
    outputs: backendOutputs,
    visibleBackendIndexes: visibleBackendIndexesForCount(2),
    targetInputType: "STRING",
  }),
  9,
);

console.log("Adv_Request contract tests passed");
