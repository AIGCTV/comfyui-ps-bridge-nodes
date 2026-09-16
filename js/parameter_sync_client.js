import {t} from "./i18n.js";
import { parameterChange } from "./node_bindings.js";
import { EditorPipelinePreview } from "./editor_pipeline.js";
import { EditorExecutionClient } from "./editor_execution.js";
export const stable = value => JSON.stringify(value, (_, v) => v && !Array.isArray(v) && typeof v === "object"
  ? Object.fromEntries(Object.keys(v).sort().map(k => [k, v[k]])) : v);

/** Editor-only control connection. Tokens exist in memory, never in graph properties. */
export class ParameterSyncClient {
  constructor({fetchApi, apply, preview, capture, executionHost=null, pipelineDiagnostic=(_record)=>{}, resultPreview=(_run)=>{}, nodePreview=(_run,_outputs)=>{}, previewInvalidated=()=>{}, changed=(_message)=>{}, executionError=changed, modeChanged=(_state)=>{}, disconnected=()=>{}, nodeId=(_manifest,node)=>String(node.id)}) {
    this.fetchApi=fetchApi;this.apply=apply;this.preview=preview;this.capture=capture;this.changed=changed;
    this.modeChanged=modeChanged;this.disconnected=disconnected;
    this.nodeId=nodeId;
    this.resultPreview=resultPreview;
    this.pipelineDiagnostic=pipelineDiagnostic;
    this.pipeline=new EditorPipelinePreview(this,{apply:(run,outputs)=>{
      this.execution?.finalOutput(run,outputs);nodePreview(run,outputs);
    },invalidate:previewInvalidated});
    this.execution=executionHost?new EditorExecutionClient(this,executionHost):null;
    this.executionError=executionError;
    this.previewRunId=null;this.resultPreviewRunId=null;
    /** @type {WebSocket|undefined} */ this.socket=undefined;
    /** @type {ReturnType<typeof setTimeout>|undefined} */ this.flushTimer=undefined;
    this.clientId="editor-"+crypto.randomUUID(); this.clientToken=null;
    this.pending=new Map(); this.session=null; this.connection=null; this.generation=0;
    this.changes=new Map(); this.modes={}; this.chain=Promise.resolve();
    this.inFlight=null;
    this.rejected={values:{},modes:{}};
  }
  headers() {
    return {"Content-Type":"application/json",
      ...(this.clientToken?{"X-PS-Bridge-Client-Id":this.clientId,"X-PS-Bridge-Client-Token":this.clientToken}:{})};
  }
  async http(path,body) {
    const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),16000);
    try {
      const response=await this.fetchApi("/ps-bridge/v3"+path,{method:body===undefined?"GET":"POST",headers:this.headers(),signal:abort.signal,...(body===undefined?{}:{body:JSON.stringify(body)})});
      const data=await response.json();
      if(!response.ok||!data.ok)throw Object.assign(new Error(data.error?.message || "Bridge request failed"),{detail:data.error});
      return data.data;
    } finally {clearTimeout(timer);}
  }
  async connect() {
    if(this.connection)return this.connection;
    if(this.socket?.readyState===WebSocket.OPEN)return;
    this.connection=(async()=>{
      const enrolled=await this.http("/clients",{clientId:this.clientId,role:"editor",...(this.clientToken?{clientToken:this.clientToken}:{})});
      this.clientToken=enrolled.clientToken;
      const url=new URL("./ps-bridge/v3/ws",location.href);url.protocol=location.protocol==="https:"?"wss:":"ws";
      const socket=this.socket=new WebSocket(url);
      await new Promise((resolve,reject)=>{
        const timer=setTimeout(()=>{socket.close();reject(new Error(t("Bridge connection failed")));},16000);
        socket.onopen=()=>{clearTimeout(timer);resolve(undefined);};
        socket.onerror=socket.onclose=()=>{clearTimeout(timer);reject(new Error(t("Bridge connection failed")));};
      });
      this.socket.onmessage=e=>{if(this.socket===socket)this.receive(JSON.parse(e.data));};
      this.socket.onclose=()=>this.failTransport(socket);
      const health=await this.rpc("hello",{role:"editor",clientToken:this.clientToken},false);
      if(health.testModeVersion!==3) {
        this.socket.close();throw new Error(t("Test mode requires version 3 on both ends. Update and restart the bridge"));
      }
      if(!health.nodeCatalog?.VP_Prompt || !health.nodeCatalog?.VP_Batch || health.nodeCatalog?.VP_Mask) {
        this.socket.close();throw new Error(t("Update and restart ComfyUI; the server does not support the six-node contract"));
      }
      this.changed(t("Connected, unattached"));
    })().finally(()=>this.connection=null);
    return this.connection;
  }
  failTransport(socket,error=new Error(t("Bridge disconnected. Attach again"))) {
    if(this.socket!==socket)return;
    this.socket=undefined;this.abandon();
    for(const p of this.pending.values()){clearTimeout(p.timer);p.reject(error);}
    this.pending.clear();this.changed(t("Offline"));this.disconnected();
    socket?.close();
  }
  context() {
    if(!this.session)throw new Error(t("This workflow has no attached parameter session"));
    return {sessionId:this.session.sessionId,serverEpoch:this.session.serverEpoch,attachToken:this.session.attachToken};
  }
  /** @param {string} type @param {any} payload @param {boolean} scoped @param {any} context */
  rpc(type,payload,scoped=true,context=null) {
    if(this.socket?.readyState!==WebSocket.OPEN)return Promise.reject(new Error(t("Bridge is not connected")));
    const socket=this.socket,messageId=crypto.randomUUID(), envelope={type,protocolVersion:2,contractVersion:3,messageId,sourceClientId:this.clientId,payload};
    if(scoped)Object.assign(envelope,context||this.context());
    return new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{
        this.pending.delete(messageId);const error=new Error(t('{type} timed out',{type}));
        reject(error);this.failTransport(socket,error);
      },16000);
      this.pending.set(messageId,{resolve,reject,timer,generation:scoped||type==="session.attach"?this.generation:null});this.socket?.send(JSON.stringify(envelope));
    });
  }
  matches(message) {
    const s=this.session;
    return s && message.sessionId===s.sessionId && message.serverEpoch===s.serverEpoch
      && message.attachToken===s.attachToken && stable(message.payload.scope)===stable(s.scope);
  }
  applyCurrent() {
    if(!this.session)return;
    // Server state stays authoritative, but must not erase locally pending edits.
    const visible={...this.session,values:{...this.session.values,...this.rejected.values,...this.inFlight?.values,...Object.fromEntries(this.changes)},
      seedModes:{...this.session.seedModes,...this.rejected.modes,...this.inFlight?.modes,...this.modes}};
    this.apply(this.session.manifest,visible);
    if(!this.inFlight&&!this.changes.size&&!Object.keys(this.modes).length&&!Object.keys(this.rejected.values).length&&!Object.keys(this.rejected.modes).length)
      this.rpc("parameter.applied",{scope:this.session.scope,appliedRevision:this.session.revision}).catch(()=>{});
  }
  receive(message) {
    if(message.protocolVersion!==2||message.contractVersion!==3)return;
    const pending=this.pending.get(message.replyTo);
    if(pending&&pending.generation!==null&&pending.generation!==this.generation) {
      clearTimeout(pending.timer);this.pending.delete(message.replyTo);pending.reject(new Error(t("Ignored a stale workflow reply")));return;
    }
    if(message.type==="error") {
      if(pending){clearTimeout(pending.timer);this.pending.delete(message.replyTo);pending.reject(Object.assign(new Error(message.payload.error.message),{detail:message.payload.error}));}
      return;
    }
    if(message.type==="test.mode.state") {
      this.modeChanged(message.payload);
    } else if(message.type==="session.attached"&&pending) {
      const {snapshot,attachToken}=message.payload;
      this.session={...snapshot,attachToken};
      try {this.apply(this.session.manifest,this.session);} catch(error) {
        this.session=null;clearTimeout(pending.timer);this.pending.delete(message.replyTo);pending.reject(error);return;
      }
      if(this.execution)this.executionReady=this.execution.attach();
      this.changed(t('Bound · revision {revision}',{revision:snapshot.revision}));
    } else if(["parameter.committed","state.snapshot"].includes(message.type)&&this.matches(message)) {
      const snapshot=message.payload;
      if(snapshot.revision>=this.session.revision) {
        Object.assign(this.session,snapshot);
        try {
          this.applyCurrent();
        } catch(error) {this.changed(error.message);}
      }
    } else if(message.type==="editor.execution"&&this.session&&message.sessionId===this.session.sessionId
      &&message.serverEpoch===this.session.serverEpoch&&message.attachToken===this.session.attachToken) {
      this.execution?.receive(message.payload);
    } else if(message.type==="editor.pipeline"&&this.matches(message)) {
      if(message.payload.kind==="snapshot"&&!("editorExecution" in message.payload))
        this.execution?.unavailable("执行反馈版本不匹配，请更新完整节点包、重启 ComfyUI 并刷新浏览器。");
      if(message.payload.editorExecution)this.execution?.receive(message.payload.editorExecution);
      this.pipeline.receive(message.payload);
    } else if(message.type==="media.preview"&&this.matches(message)) {
      if(this.pipeline.active&&message.payload.runId!==this.pipeline.active.runId)this.pipeline.reset();
      this.previewRunId=message.payload.runId;
      this.preview(message.payload).catch(error=>this.changed(error.message));
    } else if(message.type==="run.status"&&this.matches(message)&&message.payload.status==="succeeded"
      &&message.payload.definitionSha256===this.session.definitionSha256) {
      if(this.pipeline.active||(this.previewRunId&&this.previewRunId!==message.payload.runId)
        ||this.resultPreviewRunId===message.payload.runId)return;
      try {this.resultPreview(message.payload);this.resultPreviewRunId=message.payload.runId;} catch(error) {this.changed(error.message);}
    } else if(message.type==="test.capture"&&this.matches(message)) {
      const context=this.context();
      this.capture(message.payload).then(payload=>{
        if(this.matches(message))return this.rpc("test.snapshot",payload,true,context);
      }).catch(error=>{
        if(this.matches(message))this.rpc("test.snapshot",{...message.payload,error:{code:"DRAFT_CHANGED",message:error.message,retryable:false}},true,context).catch(()=>{});
      });
    }
    if(pending){clearTimeout(pending.timer);this.pending.delete(message.replyTo);pending.resolve(message.payload);}
  }
  async attach(scope,graph,takeover=false) {
    if(this.session)await this.detach().catch(()=>{});
    const generation=this.generation;
    await this.connect();
    if(generation!==this.generation)throw new Error(t("Workflow changed while connecting"));
    await this.rpc("session.attach",{scope,graph,...(takeover?{takeover:true}:{})},false);
    await this.rpc("parameter.applied",{scope,appliedRevision:this.session.revision});
    await this.executionReady;
  }
  edit(node,changes) {
    if(!this.session)return;
    const id=this.nodeId(this.session.manifest,node);
    if(id===undefined)return;
    const patch=parameterChange(this.session.manifest,{id},changes);
    for(const p of patch.changes){this.changes.set(p.paramId,p.value);delete this.rejected.values[p.paramId];}
    for(const id of Object.keys(patch.seedModes))delete this.rejected.modes[id];
    Object.assign(this.modes,patch.seedModes);
  }
  flush() {
    const generation=this.generation;
    const work=async()=>{
      while(this.session&&(this.changes.size||Object.keys(this.modes).length)) {
        if(generation!==this.generation)throw new Error(t("Attachment changed"));
        const changes=[...this.changes].map(([paramId,value])=>({paramId,value})),seedModes=this.modes;
        this.changes.clear();this.modes={};
        this.inFlight={values:Object.fromEntries(changes.map(p=>[p.paramId,p.value])),modes:seedModes};
        try {
          await this.rpc("parameter.patch",{scope:this.session.scope,baseRevision:this.session.revision,changes,seedModes});
        } catch(error) {
          if(generation===this.generation){
            Object.assign(this.rejected.values,this.inFlight?.values);Object.assign(this.rejected.modes,this.inFlight?.modes);
          }
          this.inFlight=null;
          if(generation===this.generation&&error.detail?.snapshot&&this.session&&stable(error.detail.snapshot.scope)===stable(this.session.scope)){
            Object.assign(this.session,error.detail.snapshot);this.applyCurrent();
          }
          throw error;
        }
        this.inFlight=null;this.applyCurrent();
      }
      if(Object.keys(this.rejected.values).length||Object.keys(this.rejected.modes).length)
        throw new Error(t("Uncommitted conflict: edit to confirm your input, or attach the current draft again"));
    };
    this.chain=this.chain.then(work,work);return this.chain;
  }
  abandon() {
    this.execution?.reset();
    clearTimeout(this.flushTimer);
    this.generation++;this.changes.clear();this.modes={};this.inFlight=null;this.rejected={values:{},modes:{}};
    this.session=null;
    this.pipeline.reset();this.previewRunId=null;this.resultPreviewRunId=null;
    for(const [id,pending] of this.pending)if(pending.generation!==null) {
      clearTimeout(pending.timer);this.pending.delete(id);pending.reject(new Error(t("Ignored a stale workflow reply")));
    }
    this.changed(t("Unattached"));
  }
  async detach() {
    const s=this.session;
    this.abandon();
    if(s)await this.rpc("session.detach",{scope:s.scope},true,
      {sessionId:s.sessionId,serverEpoch:s.serverEpoch,attachToken:s.attachToken});
  }
}
