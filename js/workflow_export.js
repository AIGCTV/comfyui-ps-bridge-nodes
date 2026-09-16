import {t} from "./i18n.js";
import { assertGraph, graphNodes, widget } from "./node_bindings.js";
import {commitChange,prepareChange,revision} from './node_state.js';
import {finishEditing} from './inline_editor.js';
import {exportedNode} from './export_bindings.js';

export const draftChanged = message => Object.assign(new Error(t(message)),
  {detail:{code:"DRAFT_CHANGED",retryable:true}});

export async function exportWorkflow(app, flush = async () => {}, {finishEditor=true}={}) {
  const graph = app.rootGraph || app.graph;
  if(finishEditor&&finishEditing(app)===false)throw draftChanged("Finish IME input first");
  await flush();
  if((app.rootGraph||app.graph)!==graph)throw draftChanged("Workflow changed during export");
  assertGraph(graph);
  for (const node of graphNodes(graph)) {
    if (node.__psUI?.composing) throw draftChanged("Finish IME input first");
  }
  const before=revision(graph);
  const exported = await app.graphToPrompt();
  if ((app.rootGraph || app.graph) !== graph) throw draftChanged("Workflow changed during export");
  if(revision(graph)!==before)throw draftChanged("Parameters changed during export");
  // Both are produced by the same official export; never synthesize UI from API.
  return {ui: exported.workflow, api: exported.output};
}

/** @param {any} app @param {import('../types/editor.js').Manifest} manifest
 * @param {import('../types/editor.js').Snapshot} snapshot @param {(node:any)=>void} refresh */
export function applySnapshot(app, manifest, snapshot, refresh) {
  const nodes = graphNodes(app.rootGraph || app.graph);
  // Validate the whole application before mutating any widget, especially during IME.
  const targets = manifest.parameters.map(p => {
    const node = exportedNode(app,manifest,p.target.nodeId);
    const target = node && widget(node, p.target.inputName);
    if (!target) throw new Error(t("The parameter target no longer exists"));
    if (node.__psUI?.composing) throw new Error(t("IME input is active; remote parameters cannot be applied yet"));
    const mode = p.modeTarget && widget(node, p.modeTarget.inputName);
    if(p.modeTarget&&exportedNode(app,manifest,p.modeTarget.nodeId)!==node)throw new Error(t("The Seed mode target no longer exists"));
    if (p.modeTarget && !mode) throw new Error(t("The Seed mode target no longer exists"));
    const patch={[p.target.inputName]:snapshot.values[p.paramId],...(mode&&p.modeTarget?{[p.modeTarget.inputName]:snapshot.seedModes[p.paramId]}:{})};
    prepareChange(node,patch);
    return {node,patch};
  });
  for (const {node,patch} of targets) {
    commitChange(node,patch,{source:'remote'});
    refresh(node);
  }
  (app.rootGraph || app.graph)?.setDirtyCanvas?.(true, true);
}
