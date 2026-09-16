import {t} from "./i18n.js";
/** Test domain v3: explicit mode, active root workflow following, atomic capture. */
import { exportWorkflow, draftChanged } from "./workflow_export.js";
import { stable } from "./parameter_sync_client.js";
import { graphNodes } from "./node_bindings.js";
import {bindExportedNodes,exportedNodeId} from "./export_bindings.js";

export class TestModeClient {
  constructor(app,client,{changed=()=>{},metadata=()=>({})}={}) {
    this.app=app;this.client=client;this.onChange=changed;this.metadata=metadata;
    this.graphId=crypto.randomUUID();this.draftRevision=0;this.signature=null;
    this.probe=null;this.lastAudit=0;this.generation=0;this.chain=Promise.resolve();
    this.state=null;this.intent=false;this.online=false;this.error=null;
    this.timer=undefined;this.tabIds=new Map();this.lastTab=null;this.reconnectAt=0;this.failedProbe=null;
    this.switchBarrier=null;
    this.locallyExited=false;this.pendingExit=null;this.exitPromise=null;this.subscribePromise=null;
    this.enableOperation=null;this.enablePromise=null;
    this.resumeTarget=null;
  }
  get enabled() { return Boolean(this.intent||(this.state?.enabled&&!this.locallyExited)); }
  get owned() { return this.state?.enabled&&this.state.ownerClientId===this.client.clientId; }
  get following() { return this.owned&&!this.locallyExited; }
  get exitPending() { return Boolean(this.pendingExit||(this.locallyExited&&this.enableOperation?.sent)); }
  identity(state=this.state) {
    return state&&{origin:globalThis.location?.origin||"",serverEpoch:state.serverEpoch,
      modeId:state.modeId,ownerClientId:state.ownerClientId};
  }
  sameIdentity(identity,state=this.state) { return stable(identity)===stable(this.identity(state)); }
  observe(state) {
    if(state.version!==3)return;
    const prior=this.state;
    if(prior&&prior.serverEpoch===state.serverEpoch&&state.modeRevision<prior.modeRevision)return;
    this.state=state;this.online=true;
    if(prior&&(prior.serverEpoch!==state.serverEpoch||prior.modeId!==state.modeId)&&prior.enabled) {
      this.intent=false;this.switchBarrier=null;this.cancelLocal();
    }
    if(!state.enabled&&!this.enableOperation)this.intent=false;
    if(this.pendingExit&&(!state.enabled||!this.sameIdentity(this.pendingExit)))this.pendingExit=null;
    if(this.client.session?.scope.domain==="test"&&state.status!=="switching"&&
       (!state.target||stable(state.target.scope)!==stable(this.client.session.scope)))this.cancelLocal();
    this.onChange();
  }
  disconnected() {
    this.online=false;this.cancelLocal({preserveDraft:true});this.error=t("Test editor disconnected. Reconnect or turn testing off");this.onChange();
  }
  subscribe() {
    if(this.subscribePromise)return this.subscribePromise;
    this.subscribePromise=(async()=>{
      await this.client.connect();
      this.observe(await this.client.rpc("test.mode.subscribe",{},false));
    })().finally(()=>{this.subscribePromise=null;});
    return this.subscribePromise;
  }
  context(revision=true) {
    if(!this.state)throw new Error(t("Test mode state is unavailable"));
    return {serverEpoch:this.state.serverEpoch,modeId:this.state.modeId,
      ...(revision?{expectedModeRevision:this.state.modeRevision}:{})};
  }
  async mutate(type,payload={},revision=true,current=()=>!this.locallyExited) {
    const identity=this.identity();
    if(!current())return;
    try {this.observe(await this.client.rpc(type,{...this.context(revision),...payload},false));}
    catch(error) {
      if(error.detail?.code!=="REVISION_CONFLICT")throw error;
      await this.subscribe();
      if(!current()||!this.sameIdentity(identity))throw error;
      this.observe(await this.client.rpc(type,{...this.context(revision),...payload},false));
    }
  }
  queue(work) { this.chain=this.chain.then(work,work);return this.chain; }
  rememberSwitchBarrier() {
    this.switchBarrier=this.owned&&this.state.status==="switching"?stable(this.context()):null;
  }
  hasSwitchBarrier() {
    return this.owned&&this.state.status==="switching"&&this.switchBarrier===stable(this.context());
  }
  tab() {
    const graph=this.app.rootGraph||this.app.graph;
    const workflow=this.app.extensionManager?.workflow?.activeWorkflow;
    return {key:workflow?.key||workflow?.path||graph?.id||graph,
      rootGraphId:String(graph?.id||this.graphId),name:workflow?.filename||workflow?.name||workflow?.path||this.metadata().name||"Unsaved Workflow"};
  }
  enable(metadata=this.metadata(),takeover=false) {
    if(this.exitPending)return this.disable();
    if(this.enablePromise)return this.enablePromise;
    const operation=this.enableOperation={cancelled:false,sent:false};
    this.locallyExited=false;this.intent=true;this.error=null;this.onChange();
    this.enablePromise=(async()=>{
      try {
        await this.subscribe();
        if(operation.cancelled)return;
        if(!this.owned) {
          operation.sent=true;
          const response=await this.client.rpc("test.mode.set",{...this.context(),enabled:true,...(takeover?{takeover:true}:{})},false);
          this.observe(response);
          if(operation.cancelled) {
            // Only the correlated enable reply may identify the mode we just created.
            if(response.enabled&&response.ownerClientId===this.client.clientId&&this.sameIdentity(this.identity(response))) {
              this.pendingExit=this.identity(response);await this.disable();
            }
            return;
          }
        }
        this.rememberSwitchBarrier();
        return await this.refresh(metadata);
      } catch(error) {
        if(operation.cancelled)return;
        this.intent=false;this.error=error.message;this.onChange();throw error;
      } finally {
        if(this.enableOperation===operation)this.enableOperation=null;
        this.onChange();
      }
    })().finally(()=>{this.enablePromise=null;});
    return this.enablePromise;
  }
  disable() {
    this.locallyExited=true;this.intent=false;
    if(this.enableOperation)this.enableOperation.cancelled=true;
    if(!this.pendingExit&&this.owned)this.pendingExit=this.identity();
    this.cancelLocal();this.onChange();
    if(this.exitPromise)return this.exitPromise;
    if(!this.pendingExit)return Promise.resolve();
    const identity=this.pendingExit;
    this.exitPromise=(async()=>{
      try {
        if(!this.state?.enabled||!this.sameIdentity(identity))return;
        await this.mutate("test.mode.set",{enabled:false},true,()=>this.sameIdentity(identity)&&this.owned);
        this.error=null;
      } catch(error) {this.error=error.message;throw error;}
      finally {this.reconnectAt=Date.now()+5000;this.onChange();}
    })().finally(()=>{this.exitPromise=null;});
    return this.exitPromise;
  }
  cancelLocal({preserveDraft=false}={}) {
    clearTimeout(this.timer);this.generation++;
    if(!preserveDraft) {
      this.draftRevision++;this.signature=null;this.probe=null;this.resumeTarget=null;
    }
    this.lastAudit=0;
    if(this.client.abandon)this.client.abandon();else this.client.detach();
  }
  rememberAttachment() {
    const session=this.client.session;
    if(!session)return;
    // Retain no token or captured media. Reconnection must obtain fresh authority
    // and re-export the graph before this original scope can be attached again.
    this.resumeTarget={identity:this.identity(),scope:structuredClone(session.scope),manifest:session.manifest,
      graph:this.app.rootGraph||this.app.graph,tab:this.tab().key,signature:this.signature,probe:this.probe};
  }
  async resume() {
    const saved=this.resumeTarget,generation=this.generation;
    if(!saved||!this.following||!this.sameIdentity(saved.identity)||saved.graph!==(this.app.rootGraph||this.app.graph)
      ||saved.tab!==this.tab().key||saved.probe!==this.definitionProbe(saved.manifest))return false;
    const current=()=>generation===this.generation&&this.following&&this.sameIdentity(saved.identity)&&this.resumeTarget===saved;
    try {
      const unchanged=this.draftGuard(saved.manifest),graph=await this.exported(false);
      if(!current())return true;
      if(!unchanged()||this.definition(graph,saved.manifest)!==saved.signature)return false;
      bindExportedNodes(this.app,saved.manifest,graph.api);
      await this.client.attach(saved.scope,{graphId:saved.scope.graphId,draftRevision:saved.scope.draftRevision,api:graph.api,ui:graph.ui});
      if(!current())return true;
      if(!unchanged())throw draftChanged("Workflow changed while reconnecting");
      this.signature=this.definition(graph,this.client.session.manifest);
      this.probe=this.definitionProbe(this.client.session.manifest);this.lastAudit=Date.now();
      this.rememberAttachment();this.error=null;this.failedProbe=null;this.onChange();return true;
    } catch(error) {
      if(!current())return true;
      if(["DRAFT_CHANGED","WORKFLOW_VERSION_MISMATCH","SESSION_EXPIRED","TEST_TARGET_UNAVAILABLE"].includes(error.detail?.code))return false;
      // A transient reconnect failure remains retryable without publishing a
      // replacement scope and orphaning a still-running native prompt.
      this.online=false;throw error;
    }
  }
  async exported(finishEditor=true) { return exportWorkflow(this.app,()=>this.client.flush(),{finishEditor}); }
  // Public command retained for scripted editor tests; it now enables explicit mode.
  async publish(metadata) { return this.enable(metadata); }
  refresh(metadata=this.metadata()) {
    this.cancelLocal();const generation=this.generation,modeId=this.state?.modeId;
    this.error=null;
    return this.queue(async()=>{
      if(!this.following||generation!==this.generation||modeId!==this.state.modeId)return;
      const current=()=>generation===this.generation&&this.following&&modeId===this.state.modeId;
      try {
        if(!this.hasSwitchBarrier()) {
          await this.mutate("test.invalidate",{status:"switching"},true,current);
          this.rememberSwitchBarrier();
        }
        if(!current())return;
        const tab=this.tab();this.lastTab=tab.key;
        if(!this.tabIds.has(tab.key))this.tabIds.set(tab.key,crypto.randomUUID());
        this.graphId=this.tabIds.get(tab.key);
        const unchanged=this.draftGuard({parameters:[],runtimeTargets:[]});
        const graph=await this.exported();if(!current())return;
        if(!unchanged())throw draftChanged("Workflow changed during export");
        const result=await this.client.http("/workflows/inspect",{...graph,metadata});if(!current())return;
        if(!unchanged())throw draftChanged("Workflow changed during export");
        const scope={domain:"test",providerId:metadata.providerId,workflowId:metadata.workflowId,
          graphId:this.graphId,draftRevision:this.draftRevision,ownerClientId:this.client.clientId};
        this.switchBarrier=null;
        await this.mutate("test.target",{scope,manifest:result.manifest,api:result.api,ui:graph.ui,
          name:String(tab.name).slice(0,512),rootGraphId:tab.rootGraphId},true,current);if(!current())return;
        if(!unchanged())throw draftChanged("Workflow changed during export");
        bindExportedNodes(this.app,result.manifest,graph.api);
        await this.client.attach(scope,{graphId:this.graphId,draftRevision:this.draftRevision,api:graph.api,ui:graph.ui});
        if(!current())return;
        if(!unchanged())throw draftChanged("Workflow changed during export");
        this.signature=this.definition(graph,this.client.session.manifest);
        this.probe=this.definitionProbe(this.client.session.manifest);this.lastAudit=Date.now();
        this.rememberAttachment();
        this.error=null;this.failedProbe=null;this.onChange();return scope;
      } catch(error) {
        if(!current())return;
        this.client.abandon?.();
        const detail=error.detail||{};
        if(detail.retryable||["DRAFT_CHANGED","DRAFT_PENDING","REVISION_CONFLICT"].includes(detail.code)) {
          this.error=null;
          await this.mutate("test.invalidate",{status:"switching"}).catch(()=>{});
          this.rememberSwitchBarrier();this.follow({delay:300});this.onChange();return;
        }
        const message=detail.field?`${error.message} (${detail.field})`:error.message;
        this.error=message;
        this.failedProbe=this.definitionProbe({parameters:[],runtimeTargets:[]});
        await this.mutate("test.invalidate",{status:"unavailable",error:{code:detail.code||"TEST_TARGET_UNAVAILABLE",message,retryable:false}}).catch(()=>{});
        this.onChange();
        throw error;
      }
    });
  }
  follow({delay=0}={}) {
    if(!this.following)return;
    clearTimeout(this.timer);
    const resume=()=>{
      // afterConfigureGraph can run before the host finishes changing its active
      // workflow. Wait for that actual boundary, not an arbitrary tab debounce.
      if(this.app.configuringGraph) {this.timer=setTimeout(resume,16);return;}
      if(this.following)this.refresh().catch(()=>{});
    };
    this.timer=setTimeout(resume,delay);
  }
  definition(graph,manifest) {
    const api=structuredClone(graph.api);
    for(const node of Object.values(api))delete node._meta;
    for(const p of manifest.parameters) {
      if(!api[p.target.nodeId])return "missing";
      api[p.target.nodeId].inputs[p.target.inputName]=p.default;
      if(p.modeTarget)api[p.modeTarget.nodeId].inputs[p.modeTarget.inputName]=p.defaultMode;
    }
    for(const p of manifest.runtimeTargets)if(api[p.nodeId])api[p.nodeId].inputs[p.inputName]="";
    return stable({api,nodes:graphNodes(this.app.rootGraph||this.app.graph).map(n=>({id:n.id,type:n.comfyClass||n.type})).sort((a,b)=>String(a.id).localeCompare(String(b.id)))});
  }
  async capture(request) {
    const session=this.client.session;
    if(this.locallyExited)throw draftChanged("Testing has exited locally");
    if(!session||stable(request.scope)!==stable(session.scope)||request.parameterRevision!==session.revision)throw draftChanged("Capture scope or revision changed");
    // Composition temporarily prevents capture, but does not change the graph's
    // identity. Keep its attachment so compositionend can commit the user's text.
    if(graphNodes(this.app.rootGraph||this.app.graph).some(n=>n.__psUI?.composing))
      throw draftChanged("Finish IME input first");
    const before=this.draftRevision,unchanged=this.draftGuard(session.manifest);
    try {
      const graph=await this.exported();
      if(before!==this.draftRevision||session!==this.client.session||request.parameterRevision!==session.revision
          ||!unchanged()||this.definition(graph,session.manifest)!==this.signature)
        throw draftChanged("Workflow changed during capture");
      return {...request,...graph};
    } catch(error) {
      // An old capture must never withdraw a newer attachment's authority.
      if(session===this.client.session&&before===this.draftRevision&&
          (error.detail?.code==="DRAFT_CHANGED"||!unchanged())) {
        await this.invalidate();this.follow();
      }
      throw error;
    }
  }
  async invalidate() {
    this.cancelLocal();const generation=this.generation,modeId=this.state?.modeId;
    if(this.following)return this.queue(async()=>{
      if(generation===this.generation&&this.following&&modeId===this.state.modeId) {
        await this.mutate("test.invalidate",{status:"switching"});
        this.rememberSwitchBarrier();
      }
    });
  }
  async changedGraph() { await this.invalidate(); }
  // Cheap editor-side definition probe: excludes layout and mutable bound parameters.
  // Custom async serializers still receive an authoritative full audit every 30s.
  definitionProbe(manifest) {
    const ignored=new Map();
    const ignore=(id,name)=>{const key=String(id);if(!ignored.has(key))ignored.set(key,new Set());ignored.get(key).add(name);};
    for(const p of manifest.parameters){ignore(p.target.nodeId,p.target.inputName);if(p.modeTarget)ignore(p.modeTarget.nodeId,p.modeTarget.inputName);}
    for(const p of manifest.runtimeTargets)ignore(p.nodeId,p.inputName);
    return stable(graphNodes(this.app.rootGraph||this.app.graph).map(n=>({id:n.id,type:n.comfyClass||n.type,mode:n.mode,
      inputs:n.inputs?.map(p=>({name:p.name,type:p.type,link:p.link})),properties:n.properties,
      widgets:n.widgets?.filter(w=>w.serialize!==false&&!ignored.get(exportedNodeId(this.app,manifest,n))?.has(w.name))
        .map(w=>({name:w.name,value:w.value}))})).sort((a,b)=>String(a.id).localeCompare(String(b.id))));
  }
  // Keep an async export tied to its original graph and ordinary-node definition.
  draftGuard(manifest) {
    const graph=this.app.rootGraph||this.app.graph,tab=this.tab().key,probe=this.definitionProbe(manifest);
    return ()=>graph===(this.app.rootGraph||this.app.graph)&&tab===this.tab().key&&probe===this.definitionProbe(manifest);
  }
  async heartbeat(now=Date.now()) {
    if(this.locallyExited) {
      if(this.pendingExit&&now>=this.reconnectAt) {
        this.reconnectAt=now+5000;
        if(!this.online)await this.subscribe();
        if(this.pendingExit)await this.disable();
      }
      return;
    }
    if(this.owned&&!this.online) {
      if(now<this.reconnectAt)return;
      this.reconnectAt=now+5000;
      await this.subscribe();
      if(this.owned&&!await this.resume())await this.refresh();
      return;
    }
    if(this.owned&&this.online) {
      const scope=this.client.session?.scope;
      await this.mutate("test.heartbeat",scope?.domain==="test"?{scope}:{},false);
      if(this.lastTab!==this.tab().key) {await this.invalidate();this.follow();return;}
      if(this.state.status==="unavailable"&&this.failedProbe!==this.definitionProbe({parameters:[],runtimeTargets:[]})) {
        await this.refresh();return;
      }
    }
    const session=this.client.session;
    if(!session||graphNodes(this.app.rootGraph||this.app.graph).some(n=>n.__psUI?.composing))return;
    const probe=this.definitionProbe(session.manifest);
    // Invalidate before exporting an edited graph. Export/inspect may fail while
    // a wire or node is being edited; that must never leave the old target ready.
    if(probe!==this.probe) {await this.invalidate();this.follow({delay:300});return;}
    if(now-this.lastAudit>=30000) {
      const unchanged=this.draftGuard(session.manifest);
      let graph;
      try {graph=await this.exported(false);}
      catch(error) {if(session===this.client.session){await this.invalidate();this.follow({delay:300});}return;}
      if(session!==this.client.session)return;
      if(!unchanged()||(this.signature&&this.definition(graph,session.manifest)!==this.signature)) {
        await this.invalidate();this.follow();return;
      }
      this.probe=this.definitionProbe(session.manifest);this.lastAudit=now;
    }
  }
}
