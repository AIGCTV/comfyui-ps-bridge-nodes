import {graphNodes,kind,value,NODE_TYPES} from "./node_bindings.js";
import {t} from "./i18n.js";
import {protectNativeImagePreview} from "./native_preview_guard.js";
import {pipelineDiagnostics} from "./pipeline_diagnostics.js";

const exportsByApp=new WeakMap();
/** Resolve formal API IDs to six-node instances, including nested subgraphs.
 * Parameter node kinds are singletons and image slots are unique in a root graph.
 * Never pick the first bare numeric ID from a recursive graph traversal.
 */
export function bindExportedNodes(app,manifest,api) {
  const nodes=graphNodes(app.rootGraph||app.graph),bindings=new Map();
  for(const [id,definition] of Object.entries(api)) {
    if(!NODE_TYPES.includes(definition.class_type))continue;
    const matches=nodes.filter(n=>kind(n)===definition.class_type&&
      (kind(n)==="VP_Image"?value(n,"slot")===definition.inputs.slot:
       kind(n)==="VP_SendToPS"?value(n,"result_id")===definition.inputs.result_id:true));
    if(matches.length!==1)throw new Error(t("The exported node target is missing or ambiguous"));
    bindings.set(id,matches[0]);
  }
  exportsByApp.set(app,{graph:app.rootGraph||app.graph,hash:manifest.definitionSha256,bindings,apiNodeIds:new Set(Object.keys(api))});
}
export function exportedNode(app,manifest,id) {
  const current=exportsByApp.get(app),graph=app.rootGraph||app.graph,nodes=graphNodes(graph);
  if(current&&current.graph===graph&&current.hash===manifest.definitionSha256) {
    const node=current.bindings.get(id);
    if(node&&nodes.includes(node))return node;
    throw new Error(t("The parameter target no longer exists"));
  }
  const matches=nodes.filter(n=>String(n.id)===id);
  if(matches.length!==1)throw new Error(t("The exported node target is missing or ambiguous"));
  return matches[0];
}
export function exportedNodeId(app,manifest,node) {
  const current=exportsByApp.get(app);
  if(current&&current.graph===(app.rootGraph||app.graph)&&current.hash===manifest.definitionSha256)
    return [...current.bindings].find(([,n])=>n===node)?.[0];
  return String(node.id);
}

function displayedNode(app,current,id) {
  if(current.bindings.has(id))return current.bindings.get(id);
  let graph=app.rootGraph||app.graph,node;
  const parts=id.split(":");
  for(let i=0;i<parts.length;i++) {
    node=graph?._nodes?.find(n=>String(n.id)===parts[i]);
    if(!node)return null;
    if(i<parts.length-1)graph=node.subgraph;
  }
  return node;
}

/** Restore native UI outputs, including ordinary and cached nodes. The submitted
 * API IDs are authoritative; bare numeric IDs collide inside nested subgraphs.
 * Keep the complete output object (text, audio, images and extension fields).
 */
export function previewNodeOutputs(app,api,manifest,run,nodeOutputs) {
  if(!run.promptId||run.definitionSha256!==manifest.definitionSha256)return;
  const current=exportsByApp.get(app),graph=app.rootGraph||app.graph;
  if(!current||current.graph!==graph||current.hash!==manifest.definitionSha256)return;
  if(!nodeOutputs||typeof nodeOutputs!=="object"||Array.isArray(nodeOutputs))throw new Error("BINDING_INVALID");
  const outputs=Object.entries(nodeOutputs).map(([node,entry])=>{
    const output=entry?.output,displayNode=entry?.display_node??node;
    if(typeof displayNode!=="string"||(!current.apiNodeIds.has(node)&&!current.apiNodeIds.has(displayNode))
      ||!output||typeof output!=="object"||Array.isArray(output))throw new Error("BINDING_INVALID");
    return {node,display_node:displayNode,prompt_id:run.promptId,output:structuredClone(output)};
  });
  for(const detail of outputs) {
    const node=displayedNode(app,current,detail.display_node);
    const identity={runId:run.runId,promptId:run.promptId,nodeId:detail.node};
    protectNativeImagePreview(app,api,node,detail.output.images,count=>pipelineDiagnostics.record({stage:"editor.image.committed",...identity,count}));
    pipelineDiagnostics.record({stage:"editor.node.dispatched",...identity});
    api.dispatchEvent(new CustomEvent("executed",{detail}));
  }
}

/** Called only after the editor connection validates the current attachment/scope.
 * Feed declared results into ComfyUI's native preview path, using formal export IDs.
 */
export function previewResults(app,api,manifest,run) {
  if(run.status!=="succeeded"||!run.promptId||run.definitionSha256!==manifest.definitionSha256)return;
  const outputs=manifest.results.map(binding=>{
    const node=exportedNode(app,manifest,binding.nodeId);
    if(kind(node)!=="VP_SendToPS"||value(node,"result_id")!==binding.resultId)throw new Error("BINDING_INVALID");
    const results=run.results.filter(r=>r.nodeId===binding.nodeId&&r.resultId===binding.resultId);
    if(results.some(r=>r.runId!==run.runId||r.promptId!==run.promptId))throw new Error("BINDING_INVALID");
    return {node:binding.nodeId,display_node:binding.nodeId,prompt_id:run.promptId,
      output:{images:results.sort((a,b)=>a.batchIndex-b.batchIndex).map(r=>r.file)}};
  });
  for(const detail of outputs)if(detail.output.images.length) {
    protectNativeImagePreview(app,api,exportedNode(app,manifest,detail.node),detail.output.images);
    api.dispatchEvent(new CustomEvent("executed",{detail}));
  }
}
