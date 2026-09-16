import {t,nodeName} from "./i18n.js";
/** Pure six-node rules shared by UI and tests. Node titles are never identities. */
export const PARAMETER_TYPES = ["VP_Seed", "VP_Slider", "VP_Prompt", "VP_Batch"];
export const NODE_TYPES = ["VP_Image", ...PARAMETER_TYPES, "VP_SendToPS"];
export const SLOTS = ["main", "ref1", "ref2", "ref3", "ref4", "ref5"];
export const SLOT_LABELS = ["main", "IMG2", "IMG3", "IMG4", "IMG5", "IMG6"];
export const RETIRED = ["VP_Strings", "VP_Mask", "VP_RunInfo", "VP_Boolean", "VP_Enum", "Adv_Request", "Adv_SendToPS"];
export const MAX_SEED = Number.MAX_SAFE_INTEGER;
const OUTPUT_TYPES={VP_Image:["IMAGE","MASK","MASK","INT","INT"],VP_Seed:["INT"],VP_Slider:["FLOAT"],
  VP_Prompt:["STRING"],VP_Batch:["INT"],VP_SendToPS:["IMAGE","MASK"]};
export const kind = node => node.comfyClass || node.type;
export const widget = (node, name) => node.widgets?.find(w => w.name === name);
export const value = (node, name) => widget(node, name)?.value;
export function graphNodes(graph) {
  const result = [], seen = new Set();
  function visit(g) {
    if (!g || seen.has(g)) return;
    seen.add(g);
    for (const node of g._nodes || []) {
      result.push(node);
      if (node.subgraph) visit(node.subgraph);
    }
  }
  visit(graph);
  return result;
}
export function issues(nodes) {
  const found = [], counts = new Map(), slots = new Map();
  const tooManyImages=nodes.filter(node=>kind(node)==="VP_Image").length>SLOTS.length;
  for (const node of nodes) {
    const type = kind(node);
    if(OUTPUT_TYPES[type]&&Array.isArray(node.outputs)
      &&JSON.stringify(node.outputs.map(p=>p.type))!==JSON.stringify(OUTPUT_TYPES[type]))
      found.push({node,message:t("The old port layout is incompatible. Recreate the six nodes")});
    if (RETIRED.includes(type)) found.push({node, message: t("This old node is incompatible. Recreate it using the six current nodes")});
    if (PARAMETER_TYPES.includes(type)) {
      if (counts.has(type)) found.push({node, message: t('Only one {node} is allowed per workflow',{node:nodeName(type)})});
      counts.set(type, node);
    }
    if (type === "VP_Image") {
      const slot = value(node, "slot");
      if (!SLOTS.includes(slot) || slots.has(slot)) found.push({node, message: tooManyImages
        ? t("At most six PS Images nodes are allowed. Remove the extra nodes") : t("Invalid or duplicate image name. Choose a free image name")});
      slots.set(slot, node);
    }
  }
  return found;
}
export function freeSlot(nodes, excluding) {
  const used = new Set(nodes.filter(n => n !== excluding && kind(n) === "VP_Image").map(n => value(n, "slot")));
  return SLOTS.find(slot => !used.has(slot));
}
export function assertGraph(graph) {
  const errors = issues(graphNodes(graph));
  if (errors.length) throw Object.assign(new Error(errors.map(e => e.message).join("；")),
    {detail:{code:"BINDING_INVALID",retryable:false}});
}
// Decimal grid, origin=min and ties upwards, matching Python Decimal normalization.
/** @param {number} n @returns {[bigint, number]} */
function decimalParts(n) {
  const [coefficient, exponent = "0"] = String(n).toLowerCase().split("e");
  const places = coefficient.includes(".") ? coefficient.length - coefficient.indexOf(".") - 1 : 0;
  return [BigInt(coefficient.replace(".", "")), places - Number(exponent)];
}
export function normalizeSlider(input, min, max, step) {
  if (![input, min, max, step].every(n => typeof n === "number" && Number.isFinite(n) && Math.abs(n) <= MAX_SEED)
      || min >= max || step <= 0 || input < min || input > max) throw new Error(t("Invalid value, range or step"));
  const parts = [input, min, max, step].map(decimalParts);
  const scale = Math.max(0, ...parts.map(p => p[1]));
  const [v, lo, hi, delta] = parts.map(([n, p]) => n * 10n ** BigInt(scale - p));
  const ticks = ((v - lo) * 2n + delta) / (2n * delta);
  const result = lo + ticks * delta;
  if (result > hi) throw new Error(t("The nearest step exceeds the maximum"));
  return Number(result.toString() + "e-" + scale);
}
export function randomSeed(cryptoObject = globalThis.crypto) {
  const words = new Uint32Array(2);
  cryptoObject.getRandomValues(words);
  return (words[0] & 0x1fffff) * 4294967296 + words[1];
}
/** @param {import('../types/editor.js').Manifest} manifest @param {{id:string|number}} node
 * @param {Record<string, unknown>} changes */
export function parameterChange(manifest, node, changes) {
  const entries = [], seedModes = {};
  for (const p of manifest.parameters) {
    if (p.target.nodeId !== String(node.id)) continue;
    if (Object.hasOwn(changes, p.target.inputName)) entries.push({paramId: p.paramId, value: changes[p.target.inputName]});
    if (p.modeTarget && Object.hasOwn(changes, "mode")) seedModes[p.paramId] = changes.mode;
  }
  return {changes: entries, seedModes};
}
