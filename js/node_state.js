import {t} from "./i18n.js";
import {kind, widget, value, graphNodes, SLOTS, MAX_SEED, normalizeSlider} from './node_bindings.js';

/** @typedef {'local'|'remote'|'restore'|'execution'} ChangeSource */
const revisions = new WeakMap();
export function revision(graph) { return revisions.get(graph) || 0; }
export function changed(graph) { if (graph) revisions.set(graph, revision(graph) + 1); }

/** Validate a complete patch before changing any widget. */
export function prepareChange(node, patch) {
  const next = {...patch};
  for (const name of Object.keys(next)) if (!widget(node, name)) throw new Error(t('Parameter not found: {name}',{name}));
  const read = name => Object.hasOwn(next, name) ? next[name] : value(node, name);
  switch (kind(node)) {
    case 'VP_Seed':
      if ('value' in next && (!Number.isSafeInteger(next.value) || next.value < 0 || next.value > MAX_SEED))
        throw new Error(t("Seed is outside the safe integer range"));
      if ('mode' in next && !['fixed', 'random'].includes(next.mode)) throw new Error(t("Invalid Seed mode"));
      break;
    case 'VP_Slider':
      if (['value', 'min', 'max', 'step'].some(k => k in next))
        next.value = normalizeSlider(read('value'), read('min'), read('max'), read('step'));
      break;
    case 'VP_Batch':
      if ('value' in next && (!Number.isInteger(next.value) || next.value < 1 || next.value > 4))
        throw new Error(t("Batch must be an integer from 1 to 4"));
      break;
    case 'VP_Prompt':
      if ('text' in next && typeof next.text !== 'string') throw new Error(t("Prompt must be text"));
      break;
    case 'VP_Image':
      if ('slot' in next && (!SLOTS.includes(next.slot) || graphNodes(node.graph?.rootGraph || node.graph)
        .some(n => n !== node && kind(n) === 'VP_Image' && value(n, 'slot') === next.slot)))
        throw new Error(t("This image name is in use. Choose a free name; up to six image names are supported"));
      break;
  }
  return Object.fromEntries(Object.entries(next).filter(([k, v]) => !Object.is(value(node, k), v)));
}

/** @param {object} node @param {Record<string, unknown>} patch
 * @param {{source?: ChangeSource, definition?: boolean, transaction?: boolean}} options */
export function commitChange(node, patch, {source = 'local', definition = false, transaction = true} = {}) {
  const changes = prepareChange(node, patch);
  if (!Object.keys(changes).length) return false;
  const graph = node.graph;
  if (source === 'local' && transaction) graph?.beforeChange?.();
  for (const [name, v] of Object.entries(changes)) widget(node, name).value = v;
  changed(graph?.rootGraph || graph);
  node.__psUI?.refresh?.();
  if (source === 'local' && transaction) graph?.afterChange?.();
  if (source === 'local' && typeof document !== 'undefined')
    document.dispatchEvent(new CustomEvent('ps-vplugins:edit', {detail: {node, changes, definition}}));
  return true;
}

/** A gesture has one native undo boundary; intermediate values still use the same commit path. */
export function beginGesture(node) {
  const graph = node.graph;
  graph?.beforeChange?.();
  let ended = false;
  return () => { if (!ended) { ended = true; graph?.afterChange?.(); } };
}
