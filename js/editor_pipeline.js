const EVENT_BYTES=2*1024*1024, RESOURCE_BYTES=16*1024*1024, QUEUE_BYTES=4*1024*1024;
const RESOURCE_CONCURRENCY=2, RESOURCE_TIMEOUT=16000;
const encoder=new TextEncoder();
const key=value=>JSON.stringify(value,(_,v)=>v&&typeof v==="object"&&!Array.isArray(v)
  ?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
const object=value=>value!==null&&typeof value==="object"&&!Array.isArray(value);

/** Sequence the control stream without waiting for UI resource downloads. Only
 * complete snapshots may replace omitted resources; node sequence guards prevent
 * an older download from replacing output already applied from a newer event.
 */
export class EditorPipelinePreview {
  constructor(client,{apply=(_run,_outputs)=>{},invalidate=()=>{}}={}) {
    this.client=client;this.apply=apply;this.invalidate=invalidate;this.active=null;
    // Include aborted requests until they settle, even after switching runs.
    this.downloads=new Set();this.downloadBytes=0;
  }
  reset() {
    const state=this.active;
    if(state){state.abort.abort();clearTimeout(state.retryTimer);this.cancelResources(state);}
    this.active=null;this.invalidate();
  }
  diagnostic(stage,state,sequence,detail={}) {
    try {this.client.pipelineDiagnostic?.({stage,runId:state.runId,promptId:state.promptId,sequence,...detail});} catch {}
  }
  valid(event,session=this.client.session) {
    return session&&event?.version===1&&event.serverEpoch===session.serverEpoch
      &&key(event.scope)===key(session.scope)&&typeof event.runId==="string"
      &&Number.isSafeInteger(event.runOrder)&&event.runOrder>0
      &&Number.isSafeInteger(event.sequence)&&event.sequence>=0&&object(event.payload);
  }
  receive(event) {
    if(!this.valid(event))return;
    let state=this.active;
    if(state&&event.runOrder<state.runOrder)return;
    if(state&&event.runOrder===state.runOrder&&event.runId!==state.runId)return;
    if(!state||event.runOrder>state.runOrder) {
      this.reset();
      state=this.active={runId:event.runId,runOrder:event.runOrder,session:this.client.session,
        promptId:null,run:null,sequence:-1,observedSequence:event.sequence,pending:new Map(),bytes:0,busy:false,recover:false,
        resourceRecoveries:0,resourceRecoveryPending:false,retryTimer:undefined,abort:new AbortController(),
        resources:[],resourceBytes:0,nodeSequences:new Map()};
    }
    if(event.sequence<=state.sequence||state.pending.has(event.sequence))return;
    this.diagnostic("editor.received",state,event.sequence,{promptId:event.promptId});
    state.observedSequence=Math.max(state.observedSequence,event.sequence);
    const bytes=encoder.encode(JSON.stringify(event)).byteLength;
    if(bytes>EVENT_BYTES)return;
    if(state.pending.size>=64||state.bytes+bytes>QUEUE_BYTES) {
      state.pending.clear();state.bytes=0;state.recover=true;
    } else {state.pending.set(event.sequence,{event,bytes});state.bytes+=bytes;}
    this.drain(state);
  }
  current(state) {return this.active===state&&this.client.session===state.session&&!state.abort.signal.aborted;}
  async snapshot(state) {
    const event=await this.client.http("/editor/pipeline/snapshot",{...this.client.context(),
      payload:{scope:state.session.scope,runId:state.runId}});
    if(!this.current(state))return;
    if(!this.valid(event,state.session)||event.kind!=="snapshot"||event.runId!==state.runId
      ||event.runOrder!==state.runOrder||event.sequence<state.sequence)throw new Error("Invalid editor run snapshot");
    this.show(state,event);
    state.sequence=event.sequence;
    if(state.sequence>=state.observedSequence)state.recover=false;
    for(const [sequence,entry] of state.pending)if(sequence<=state.sequence) {
      state.pending.delete(sequence);state.bytes-=entry.bytes;
    }
  }
  async drain(state) {
    if(state.busy)return;
    state.busy=true;
    try {
      while(this.current(state)&&(state.pending.size||state.recover)) {
        const first=[...state.pending].sort(([a],[b])=>a-b)[0];
        if(state.recover||!first||first[1].event.kind!=="snapshot"&&
           (state.sequence<0||first[0]!==state.sequence+1)) {
          state.recover=false;
          const previous=state.sequence;
          await this.snapshot(state);
          if(this.current(state)&&state.sequence===previous&&state.pending.size)throw new Error("Editor snapshot did not close an event gap");
          continue;
        }
        state.pending.delete(first[0]);state.bytes-=first[1].bytes;
        this.show(state,first[1].event);
        if(this.current(state))state.sequence=first[0];
      }
    } catch(error) {
      if(this.current(state)) {
        state.pending.clear();state.bytes=0;
        if(state.resourceRecoveryPending){state.resourceRecoveryPending=false;this.recoverResources(state,error);}
        else this.client.changed(error.message);
      }
    } finally {state.busy=false;}
  }
  show(state,event) {
    if(state.promptId&&event.promptId!==state.promptId)throw new Error("Editor prompt identity changed");
    if(event.promptId)state.promptId=event.promptId;
    const run=event.payload.run;
    if(run) {
      if(run.runId!==state.runId||run.promptId!==event.promptId||key(run.scope)!==key(state.session.scope)
        ||run.definitionSha256!==state.session.definitionSha256)throw new Error("Editor run identity changed");
      state.run=run;
    }
    if(event.editorExecution)this.client.execution?.receive(event.editorExecution);
    const descriptor=event.payload.nodeOutputsResource,outputs=event.payload.nodeOutputs;
    if(descriptor)this.validateResource(descriptor);
    if(event.kind==="snapshot"&&(descriptor||outputs)) {
      // A complete newer snapshot contains every omitted node, including cached
      // outputs. A node.output delta cannot safely cancel unknown older nodes.
      this.cancelResources(state,event.sequence);
      clearTimeout(state.retryTimer);state.retryTimer=undefined;state.resourceRecoveryPending=false;
    }
    if(descriptor)this.queueResource(state,event,descriptor);
    else if(outputs) {
      this.applyOutputs(state,outputs,event.sequence,event.promptId);
      if(event.kind==="snapshot")state.resourceRecoveries=0;
    }
  }
  applyOutputs(state,outputs,sequence,promptId) {
    if(!this.current(state)||state.promptId!==promptId||!state.run)return;
    if(!object(outputs))throw new Error("Invalid editor UI outputs");
    const fresh=Object.entries(outputs).filter(([node,entry])=>
      sequence>(state.nodeSequences.get("node:"+node)??-1)
      &&sequence>(state.nodeSequences.get("display:"+(entry?.display_node??node))??-1));
    if(!fresh.length)return;
    this.apply(state.run,Object.fromEntries(fresh));
    for(const [node,entry] of fresh) {
      state.nodeSequences.set("node:"+node,sequence);
      state.nodeSequences.set("display:"+(entry?.display_node??node),sequence);
      this.diagnostic("editor.output.dispatched",state,sequence,{nodeId:node});
    }
  }
  validateResource(descriptor) {
    if(!object(descriptor)||!/^\/ps-bridge\/v3\/editor\/pipeline\/resources\/[A-Za-z0-9_-]+$/.test(descriptor.url)
      ||!Number.isSafeInteger(descriptor.byteSize)||descriptor.byteSize<2||descriptor.byteSize>RESOURCE_BYTES
      ||!/^[a-f0-9]{64}$/.test(descriptor.sha256))throw new Error("Invalid editor UI resource");
  }
  cancelResources(state,through=Infinity) {
    state.resources=state.resources.filter(job=>job.sequence>through);
    state.resourceBytes=state.resources.reduce((total,job)=>total+job.bytes,0);
    for(const job of this.downloads)if(job.state===state&&job.sequence<=through) {
      job.cancelled=true;job.abort.abort();
      this.diagnostic("editor.resource.cancelled",state,job.sequence);
    }
  }
  queueResource(state,event,descriptor) {
    const bytes=encoder.encode(JSON.stringify(descriptor)).byteLength;
    if(state.resources.length>=64||state.resourceBytes+bytes>QUEUE_BYTES) {
      this.cancelResources(state);
      this.recoverResources(state,new Error("Editor UI resource queue overflow"));
      return;
    }
    state.resources.push({state,descriptor,bytes,sequence:event.sequence,promptId:event.promptId,
      snapshot:event.kind==="snapshot",abort:new AbortController(),cancelled:false,timedOut:false});
    state.resourceBytes+=bytes;this.startResources();
  }
  startResources() {
    const state=this.active;
    if(!state||!this.current(state))return;
    while(state.resources.length&&this.downloads.size<RESOURCE_CONCURRENCY) {
      const job=state.resources[0];
      if(this.downloadBytes+job.descriptor.byteSize>RESOURCE_CONCURRENCY*RESOURCE_BYTES)return;
      state.resources.shift();state.resourceBytes-=job.bytes;
      this.downloads.add(job);this.downloadBytes+=job.descriptor.byteSize;
      this.loadResource(job);
    }
  }
  async loadResource(job) {
    const {state,sequence}=job;
    const timer=setTimeout(()=>{job.timedOut=true;job.abort.abort();},RESOURCE_TIMEOUT);
    this.diagnostic("editor.resource.started",state,sequence,{byteSize:job.descriptor.byteSize});
    try {
      const outputs=await this.resource(state,job.descriptor,job.abort.signal);
      if(!this.current(state)||job.cancelled)return;
      this.diagnostic("editor.resource.completed",state,sequence,{byteSize:job.descriptor.byteSize});
      this.applyOutputs(state,outputs,sequence,job.promptId);
      if(job.snapshot)state.resourceRecoveries=0;
    } catch(error) {
      if(this.current(state)&&!job.cancelled) {
        this.diagnostic("editor.resource.failed",state,sequence,{reason:job.timedOut?"timeout":error.message});
        if(error.recoverSnapshot||job.timedOut)this.recoverResources(state,error);
        else this.client.changed(error.message);
      }
    } finally {
      clearTimeout(timer);this.downloads.delete(job);this.downloadBytes-=job.descriptor.byteSize;
      this.startResources();
    }
  }
  recoverResources(state,error) {
    if(!this.current(state)||state.resourceRecoveryPending)return;
    if(state.resourceRecoveries>=3) {
      this.client.changed("Editor UI recovery failed: "+error.message);return;
    }
    state.resourceRecoveries++;state.resourceRecoveryPending=true;
    this.diagnostic("editor.resource.recovering",state,state.sequence,{attempt:state.resourceRecoveries});
    state.retryTimer=setTimeout(()=>{
      state.retryTimer=undefined;
      if(this.current(state)){state.recover=true;this.drain(state);}
    },50);
  }
  async resource(state,descriptor,signal) {
    let response;
    try {
      response=await this.client.fetchApi(descriptor.url,{method:"POST",headers:this.client.headers(),
        body:JSON.stringify({...this.client.context(),payload:{scope:state.session.scope,runId:state.runId}}),signal});
    } catch(error) {throw Object.assign(error,{recoverSnapshot:true});}
    if(!response.ok) {
      await response.body?.cancel();
      throw Object.assign(new Error("Editor UI resource unavailable"),{recoverSnapshot:response.status===404||response.status>=500});
    }
    const length=response.headers.get("Content-Length");
    if(length!==null&&Number(length)!==descriptor.byteSize) {
      await response.body?.cancel();throw new Error("Editor UI resource size changed");
    }
    const reader=response.body?.getReader();
    if(!reader)throw new Error("Editor UI resource has no body");
    const data=new Uint8Array(descriptor.byteSize);let offset=0;
    try {
      while(true) {
        signal.throwIfAborted();
        const {done,value}=await reader.read();if(done)break;
        if(offset+value.byteLength>data.byteLength)throw new Error("Editor UI resource exceeds its limit");
        data.set(value,offset);offset+=value.byteLength;
      }
    } catch(error) {await reader.cancel().catch(()=>{});throw error;}
    finally {reader.releaseLock();}
    if(offset!==data.byteLength)throw new Error("Editor UI resource was truncated");
    const hash=[...new Uint8Array(await crypto.subtle.digest("SHA-256",data))].map(b=>b.toString(16).padStart(2,"0")).join("");
    signal.throwIfAborted();
    if(hash!==descriptor.sha256)throw new Error("Editor UI resource digest changed");
    const outputs=JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(data));
    if(!object(outputs))throw new Error("Invalid editor UI outputs");
    return outputs;
  }
}
