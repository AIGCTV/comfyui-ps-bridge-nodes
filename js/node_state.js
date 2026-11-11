import {setWidgetValue} from './widget_values.js';
import {t} from "./i18n.js";
import {kind, widget, value, graphNodes, SLOTS, MAX_SEED, normalizeSlider} from './node_bindings.js';
import {groupKey,groupMembers,sharedFields,sharedValues} from './shared_parameters.js';

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
      if ('slot' in next && !SLOTS.includes(next.slot))
        throw new Error(t("Invalid image slot; choose main or ref1–ref5"));
      break;
  }
  return Object.fromEntries(Object.entries(next).filter(([k, v]) => !Object.is(value(node, k), v)));
}

/** @param {object} node @param {Record<string, unknown>} patch
 * @param {{source?: ChangeSource, definition?: boolean, transaction?: boolean}} options */
export function commitChange(node, patch, {source = 'local', definition = false, transaction = true} = {}) {
  let changes = prepareChange(node, patch);
  if('slot' in changes||'source_json' in changes){
    const old=Object.fromEntries(Object.keys(changes).map(name=>[name,value(node,name)]));
    for(const [name,v] of Object.entries(changes))setWidgetValue(widget(node,name),v);
    const leader=groupMembers(node).find(n=>n!==node);
    for(const [name,v] of Object.entries(old))setWidgetValue(widget(node,name),v);
    if(leader)changes=prepareChange(node,{...changes,...sharedValues(leader)});
  }
  const graph = node.graph;
  const shared=Object.fromEntries(Object.entries(changes).filter(([name])=>sharedFields[kind(node)]?.includes(name)));
  const peers=('slot' in changes||'source_json' in changes)?[]:groupMembers(node).filter(n=>n!==node).map(n=>({node:n,patch:prepareChange(n,shared)}));
  if(!Object.keys(changes).length&&!peers.some(p=>Object.keys(p.patch).length))return false;
  if (source === 'local' && transaction) graph?.beforeChange?.();
  for (const [name, v] of Object.entries(changes)) setWidgetValue(widget(node, name),v);
  for(const peer of peers){
    for(const [name,v] of Object.entries(peer.patch))setWidgetValue(widget(peer.node,name),v);
    peer.node.__psUI?.refresh?.();
    if(kind(peer.node)==='VP_Image'&&'file_name' in shared)peer.node.__psUI?.previewFile?.();
  }
  changed(graph?.rootGraph || graph);
  node.__psUI?.refresh?.();
  if (source === 'local' && transaction) graph?.afterChange?.();
  if (source === 'local' && typeof document !== 'undefined')
    document.dispatchEvent(new CustomEvent('ps-vplugins:edit', {detail: {node, changes, definition}}));
  return true;
}

/** A new member inherits existing state; a graph load uses stable traversal order. */
export function reconcileShared(graph, newcomer=null) {
  const seen=new Set(), normalized=new Set();
  for(const node of newcomer?[newcomer]:graphNodes(graph)) {
    if(seen.has(node))continue;
    const members=groupMembers(node);
    const leader=newcomer?members.find(n=>n!==node)||node:members[0];
    if(!leader)continue;
    const patch=sharedValues(leader);
    for(const member of members){
      // Apply complete group state, even when the leader itself is unchanged.
      const changes=prepareChange(member,patch);
      if(Object.keys(changes).length)normalized.add(groupKey(member));
      for(const [name,v] of Object.entries(changes))setWidgetValue(widget(member,name),v);
      member.__psUI?.refresh?.();
      if('file_name' in changes)member.__psUI?.previewFile?.();
      seen.add(member);
    }
  }
  return [...normalized];
}

/** Hidden output identities must be repaired without asking users to edit IDs. */
export function reconcileResultIds(graph) {
  const seen=new Set();
  for(const node of graphNodes(graph))if(kind(node)==='VP_SendToPS') {
    let id=value(node,'result_id');
    if(typeof id!=='string'||!id.trim()||seen.has(id)) {
      id='result-'+crypto.randomUUID();
      commitChange(node,{result_id:id},{source:'restore'});
    }
    seen.add(id);
  }
}

/** A gesture has one native undo boundary; intermediate values still use the same commit path. */
export function beginGesture(node) {
  const graph = node.graph;
  graph?.beforeChange?.();
  let ended = false;
  return () => { if (!ended) { ended = true; graph?.afterChange?.(); } };
}
