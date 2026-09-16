import {t,initLocalization} from "./i18n.js";
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { NODE_TYPES, kind, value, graphNodes, freeSlot } from "./node_bindings.js";
import { commitChange, changed } from "./node_state.js";
import { installStyle, installNode, refreshNode, checkNodes, installCanvasInteractions } from "./node_widgets.js";
import { editorFor } from "./inline_editor.js";
import { ParameterSyncClient } from "./parameter_sync_client.js";
import { applySnapshot } from "./workflow_export.js";
import { TestModeClient } from "./test_mode_client.js";
import { NODE_MENU_ORDER, installNodeMenuOrder } from "./node_menu.js";
import { installTestModeToolbar } from "./test_mode_toolbar.js";
import {exportedNode,exportedNodeId,previewResults,previewNodeOutputs} from "./export_bindings.js";
import {invalidateNativeImagePreviews,disposeNativeImagePreviews} from "./native_preview_guard.js";
import {ExecutionHost} from "./execution_host.js";
import {pipelineDiagnostics} from "./pipeline_diagnostics.js";

let status=t("Local mode"), configuring=false, heartbeatBusy=false;
let renderTestMode=()=>{};
let testError=null;
const notify=(message,severity="info")=>app.extensionManager.toast.add({severity,summary:"PS Vplugins",detail:message,life:5000});
const client=new ParameterSyncClient({
  executionHost:new ExecutionHost(app,api),
  pipelineDiagnostic:record=>pipelineDiagnostics.record(record),
  executionError:message=>notify(message,"warn"),
  fetchApi:(...args)=>api.fetchApi(...args),
  nodeId:(manifest,node)=>exportedNodeId(app,manifest,node),
  apply:(manifest,snapshot)=>applySnapshot(app,manifest,snapshot,refreshNode),
  resultPreview:run=>previewResults(app,api,client.session.manifest,run),
  nodePreview:(run,outputs)=>previewNodeOutputs(app,api,client.session.manifest,run,outputs),
  previewInvalidated:()=>{
    invalidateNativeImagePreviews(app);
    for(const node of graphNodes(app.rootGraph||app.graph))
      if(kind(node)==="VP_SendToPS")node.__psUI?.resources.cancel("preview");
  },
  preview:async payload=>{
    await Promise.all(payload.nodes.map(async binding=>{
      const node=exportedNode(app,client.session.manifest,binding.nodeId);
      if(!node?.__psUI)return;
      const body=JSON.stringify({...client.context(),payload:{scope:client.session.scope,runId:payload.runId,nodeId:binding.nodeId}});
      await node.__psUI.loadPreview(async signal=>{
        const response=await api.fetchApi("/ps-bridge/v3/editor/preview",{method:"POST",headers:client.headers(),body,signal});
        if(!response.ok)throw new Error(t("Preview unavailable"));
        return response.blob();
      },"business");
    }));
  },
  capture:request=>testMode.capture(request),
  modeChanged:state=>testMode.observe(state),
  disconnected:()=>testMode.disconnected(),
  changed:message=>{status=message;if(!client.session)clearPreviews(true);}
});
const testMode=new TestModeClient(app,client,{metadata,changed:()=>renderTestMode()});
function metadata() {
  const graph=app.rootGraph||app.graph;
  graph.extra ??= {};
  graph.extra.ps_vplugins ??= {featureId:"workflow-"+crypto.randomUUID(),workflowId:"workflow-"+crypto.randomUUID(),
    workflowVersion:"0.0.0-dev",name:"PS Vplugins Workflow",providerId:"comfy_bridge"};
  return {...graph.extra.ps_vplugins};
}
async function enableTest() {
  try {
    clearPreviews();
    if(testMode.exitPending||testMode.intent||(testMode.enabled&&testMode.owned))await testMode.disable();
    else if(testMode.state?.enabled&&testMode.state.ownerClientId!==client.clientId) {
      if(testMode.state.status!=="unavailable")throw new Error(t("Testing belongs to another browser"));
      const replace=await app.extensionManager.dialog.confirm({title:t("Replace offline test editor"),
        message:t("Replace the offline editor and test this workflow instead?")});
      if(replace)await testMode.enable(metadata(),true);
    } else await testMode.enable(metadata());
    testError=null;
  }
  catch(error){if(testError!==error.message){testError=error.message;notify(error.message,"error");}}
}
function clearPreviews(keepLocal=false) {
  for(const node of graphNodes(app.rootGraph||app.graph))if(node.__psUI?.showPreview){
    node.__psUI.closeMenu?.();
    node.__psUI.resources.invalidate();
    if(keepLocal&&node.__psUI.previewKind==="local"&&!value(node,"request_id"))continue;
    node.__psUI.showPreview(null);
  }
}
app.registerExtension({
  name:"comfyui_ps_bridge.six_nodes",
  async init() {await initLocalization(app,api);},
  commands:[
    {id:"ps-vplugins.test",label:t("PS Vplugins: Toggle test mode"),function:enableTest}
  ],
  actionBarButtons:[{icon:"pi pi-link",label:"PS Vplugins",tooltip:"PS Vplugins",class:"ps-vplugins-test-toggle",onClick:enableTest}],
  beforeRegisterVueAppNodeDefs(defs) {
    // Reorder only this category's entries; leave all other node definitions in place.
    const order=NODE_MENU_ORDER;
    const own=defs.filter(def=>order.includes(def.name)).sort((a,b)=>order.indexOf(a.name)-order.indexOf(b.name));
    let index=0;for(let i=0;i<defs.length;i++)if(order.includes(defs[i].name))defs[i]=own[index++];
  },
  nodeCreated(node) {
    installNode(node,app,api);
    if(kind(node)==="VP_Image"&&!configuring&&!app.configuringGraph)queueMicrotask(()=>{
      if(configuring||app.configuringGraph||!node.graph)return;
      const others=graphNodes(app.rootGraph||app.graph).filter(n=>n!==node&&kind(n)==="VP_Image"&&value(n,"slot")===value(node,"slot"));
      if(others.length){const slot=freeSlot(graphNodes(app.rootGraph||app.graph),node);if(slot)commitChange(node,{slot},{source:'restore'});}
      node.__psUI?.previewFile?.();
      // Let all newly created/copied images finish allocating before reporting
      // duplicates; a batch of six valid nodes must not emit transient warnings.
      queueMicrotask(()=>checkNodes(app));
    });
  },
  loadedGraphNode(node) {installNode(node,app,api);refreshNode(node);node.__psUI?.previewFile?.();},
  beforeConfigureGraph() {configuring=true;changed(app.rootGraph||app.graph);editorFor(app).close();clearPreviews();disposeNativeImagePreviews(app);testMode.changedGraph().catch(()=>{});},
  afterConfigureGraph() {configuring=false;for(const node of graphNodes(app.rootGraph||app.graph))node.__psUI?.syncInputs();checkNodes(app);testMode.follow();renderTestMode();},
  getNodeMenuItems(node) {
    if(!NODE_TYPES.includes(kind(node)))return [];
    return [{content:"PS Vplugins · "+t(status),disabled:true}];
  },
  async setup() {
    installStyle();
    installCanvasInteractions(app);
    installNodeMenuOrder(LiteGraph);
    renderTestMode=installTestModeToolbar(testMode);
    testMode.subscribe().catch(error=>{testMode.error=error.message;renderTestMode();});
    document.addEventListener("ps-vplugins:edit",event=>{
      const {node,changes,definition}=event.detail;
      if(definition){clearPreviews(true);testMode.invalidate().catch(e=>notify(e.message,"error"));testMode.follow({delay:300});return;}
      client.edit(node,changes);
      clearTimeout(client.flushTimer);client.flushTimer=setTimeout(()=>client.flush().catch(e=>notify(e.message,"error")),120);
    });
    document.addEventListener("ps-vplugins:compositionend",()=>{
      client.flush().then(()=>client.applyCurrent())
        .catch(error=>notify(error.message,"error"));
    });
    setInterval(async()=>{
      if((!client.session&&!testMode.owned&&!testMode.exitPending)||heartbeatBusy||
        (!testMode.exitPending&&(configuring||app.configuringGraph)))return;
      heartbeatBusy=true;
      try{await testMode.heartbeat();}catch(error){clearPreviews();testMode.error=error.message;renderTestMode();}
      finally{heartbeatBusy=false;}
    },2000);
  }
});
