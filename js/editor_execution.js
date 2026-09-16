const terminal=new Set(["execution_success","execution_error","execution_interrupted"]);
const continuous=new Set(["progress","progress_state","progress_text"]);
const nativeKinds=new Set(["execution_start","executing","execution_cached",...continuous,...terminal]);
const MAX_EVENTS=64,MAX_BYTES=4*1024*1024,MAX_EVENT_BYTES=2*1024*1024;
const encoder=new TextEncoder();
const same=(a,b)=>JSON.stringify(a,Object.keys(a||{}).sort())===JSON.stringify(b,Object.keys(b||{}).sort());
const frame=callback=>globalThis.requestAnimationFrame?.(callback)??setTimeout(callback,16);
const cancelFrame=id=>globalThis.cancelAnimationFrame?.(id)??clearTimeout(id);

/** Common display adapter on the existing editor attachment and recovery path.
 * Image IO never shares the state-event drain. No subscription or controller.
 */
export class EditorExecutionClient {
  constructor(client,host) {this.client=client;this.host=host;this.generation=0;this.state=null;}
  async attach() {
    this.reset();
    const session=this.client.session;
    /** @type {any} */
    const state=this.state={session,ready:false,pending:[],pendingBytes:0,runs:new Map(),frames:new Map(),raf:null,
      bufferedCount:0,bufferedBytes:0,
      abort:new AbortController(),recovering:new Map()};
    try {
      await this.host.attach();
      if(!this.current(state))return;
      state.ready=true;
      const pending=state.pending.splice(0);state.pendingBytes=0;
      for(const event of pending)this.receive(event);
    } catch(error) {
      if(this.current(state))this.unavailable(error.message);
    }
  }
  current(state) {return this.state===state&&this.client.session===state.session&&!state.abort.signal.aborted;}
  valid(state,event) {
    return event?.version===1&&event.serverEpoch===state.session.serverEpoch&&same(event.scope,state.session.scope)
      &&event.definitionSha256===state.session.definitionSha256&&typeof event.runId==="string"&&typeof event.promptId==="string"
      &&Number.isSafeInteger(event.liveSequence)&&event.liveSequence>=0
      &&["registered","snapshot","native","preview","observation"].includes(event.kind)
      &&(event.kind!=="native"||(nativeKinds.has(event.data?.type)&&event.data?.data?.prompt_id===event.promptId));
  }
  receive(event) {
    try {this.consume(event);} catch(error) {this.unavailable("执行反馈不可用："+error.message);}
  }
  unavailable(message) {
    if(!this.state)return;
    this.reset();(this.client.executionError||this.client.changed)(message);
  }
  consume(event) {
    const state=this.state;
    if(!state||!this.current(state)||!this.valid(state,event))return;
    const run=state.runs.get(event.runId),recovery=state.recovering.get(event.runId);
    if((run&&run.promptId!==event.promptId)||(recovery&&recovery.promptId!==event.promptId))return;
    if(run&&event.liveSequence<=run.sequence)return;
    if(!state.ready) {
      const bytes=encoder.encode(JSON.stringify(event)).byteLength;
      if(bytes>MAX_EVENT_BYTES||state.pending.length>=MAX_EVENTS||state.pendingBytes+bytes>MAX_BYTES)
        throw new Error("执行反馈初始化消息超出限制，请重新连接测试模式。");
      state.pending.push(event);state.pendingBytes+=bytes;
      return;
    }
    if(recovery) {this.buffer(state,recovery,event);return;}
    if(event.kind==="snapshot") {
      this.restore(state,event);return;
    }
    if(event.kind!=="registered"&&(!run||event.liveSequence!==run.sequence+1)) {
      this.recover(state,event);return;
    }
    this.apply(state,event);
    const current=state.runs.get(event.runId);if(current)current.sequence=event.liveSequence;
  }
  apply(state,event) {
    let run=state.runs.get(event.runId);
    if(event.kind==="registered") {
      if(run&&run.promptId!==event.promptId)return;
      if(!event.data?.prompt||typeof event.data.prompt!=="object")throw new Error("Invalid execution registration");
      this.host.register(event,event.data);
      if(!run)state.runs.set(event.runId,{promptId:event.promptId,sequence:event.liveSequence,terminal:false,previewSequence:0,previewAbort:null,finalNodes:new Set()});
      if(state.runs.size>32) {
        const completed=[...state.runs].find(([,entry])=>entry.terminal);
        if(completed)state.runs.delete(completed[0]);
        else throw new Error("Too many active editor runs");
      }
      return;
    }
    if(!run||run.promptId!==event.promptId||run.terminal)return;
    if(event.kind==="native") {
      const {type,data}=event.data||{};
      if(!nativeKinds.has(type)||data?.prompt_id!==event.promptId)return;
      if(continuous.has(type)) {
        state.frames.set(event.runId+":"+type,{event,type,data});
        if(state.raf===null)state.raf=frame(()=>{state.raf=null;if(this.current(state))this.flushFrames(state);});
      } else {
        this.flushFrames(state);
        this.host.native(event,type,data);
        if(type==="executing"||terminal.has(type)) {
          run.previewAbort?.abort();run.previewAbort=null;
        }
        if(terminal.has(type))run.terminal=true;
      }
    } else if(event.kind==="preview") {
      this.preview(state,event).catch(()=>{}); // A disposable frame never changes business status.
    } else if(event.kind==="observation") {
      this.client.changed(event.data.status==="recovering"?"执行观察连接恢复中":"执行观察连接已恢复");
    }
  }
  flushFrames(state) {
    if(state.raf!==null){cancelFrame(state.raf);state.raf=null;}
    for(const {event,type,data} of state.frames.values())this.host.native(event,type,data);
    state.frames.clear();
  }
  restore(state,event) {
    if(!this.valid(state,event)||event.kind!=="snapshot"||!event.data?.registration?.prompt)
      throw new Error("Invalid execution snapshot");
    if(Object.values(event.data.events||{}).some(data=>data?.prompt_id!==event.promptId))
      throw new Error("Execution snapshot contains another prompt");
    this.flushFrames(state);
    this.apply(state,{...event,kind:"registered",data:event.data.registration});
    const events=event.data.events||{};
    for(const type of ["execution_start","execution_cached","executing","progress_state","progress","progress_text",...terminal])
      if(events[type])this.apply(state,{...event,kind:"native",data:{type,data:events[type]}});
    if(event.data.preview)this.apply(state,{...event,kind:"preview",data:event.data.preview});
    if(event.data.observation==="recovering")this.client.changed("执行观察连接恢复中");
    state.runs.get(event.runId).sequence=event.liveSequence;
  }
  buffer(state,recovery,event) {
    recovery.highWater=Math.max(recovery.highWater,event.liveSequence);
    if(recovery.pending.has(event.liveSequence))return;
    const bytes=encoder.encode(JSON.stringify(event)).byteLength;
    if(bytes>MAX_EVENT_BYTES)throw new Error("执行反馈消息超出限制");
    // Overflow discards display events, never their observed high-water mark.
    // A later snapshot must close every discarded sequence before recovery ends.
    if(state.bufferedCount>=MAX_EVENTS||state.bufferedBytes+bytes>MAX_BYTES)
      for(const record of state.recovering.values())this.clearBuffer(state,record);
    recovery.pending.set(event.liveSequence,{event,bytes});
    state.bufferedCount++;state.bufferedBytes+=bytes;
  }
  clearBuffer(state,recovery,through=Infinity) {
    for(const [sequence,entry] of recovery.pending)if(sequence<=through) {
      recovery.pending.delete(sequence);state.bufferedCount--;state.bufferedBytes-=entry.bytes;
    }
  }
  async recover(state,event) {
    const runId=event.runId;
    if(state.recovering.size>=32) {this.unavailable("执行反馈恢复任务过多，请重新连接测试模式。");return;}
    const recovery={promptId:event.promptId,highWater:event.liveSequence,pending:new Map()};
    state.recovering.set(runId,recovery);
    try {this.buffer(state,recovery,event);} catch(error) {this.unavailable(error.message);return;}
    try {
      for(let attempt=0;attempt<3&&this.current(state);attempt++) {
        const snapshot=await this.client.http("/editor/pipeline/snapshot",this.body(state,runId));
        if(!this.current(state))return;
        const live=snapshot.editorExecution,run=state.runs.get(runId);
        if(!this.valid(state,live)||live.kind!=="snapshot"||live.runId!==runId||live.promptId!==recovery.promptId
          ||(run&&live.liveSequence<run.sequence))throw new Error("Invalid execution recovery identity or sequence");
        this.restore(state,live);
        // Reapply events received after the snapshot was captured, including a
        // terminal event that may be the last message this run will ever send.
        this.clearBuffer(state,recovery,live.liveSequence);
        for(const [sequence,entry] of [...recovery.pending].sort(([a],[b])=>a-b)) {
          const current=state.runs.get(runId);
          if(entry.event.kind==="snapshot")this.restore(state,entry.event);
          else if(sequence===current.sequence+1) {
            this.apply(state,entry.event);current.sequence=sequence;
          } else break;
          this.clearBuffer(state,recovery,sequence);
        }
        this.client.pipeline?.receive(snapshot);
        if(state.runs.get(runId).sequence>=recovery.highWater)return;
      }
      throw new Error("Execution snapshot did not close the observed event gap");
    } catch(error) {
      if(this.current(state))this.unavailable("执行反馈恢复失败："+error.message);
    } finally {
      this.clearBuffer(state,recovery);state.recovering.delete(runId);
    }
  }
  body(state,runId) {
    return {...this.client.context(),payload:{scope:state.session.scope,runId}};
  }
  async preview(state,event) {
    const descriptor=event.data,run=state.runs.get(event.runId);
    if(!descriptor||run.finalNodes.has(descriptor.nodeId)||run.finalNodes.has(descriptor.displayNodeId)
      ||descriptor.runId!==event.runId||descriptor.promptId!==event.promptId
      ||!/^[a-f0-9]{32}$/.test(descriptor.previewId)||!Number.isSafeInteger(descriptor.previewSequence)
      ||descriptor.previewSequence<=run.previewSequence||!Number.isSafeInteger(descriptor.byteSize)
      ||descriptor.byteSize<=0||descriptor.byteSize>8*1024*1024||!["image/png","image/jpeg"].includes(descriptor.mime))return;
    run.previewAbort?.abort();
    const abort=run.previewAbort=new AbortController();run.previewSequence=descriptor.previewSequence;
    run.previewNode=descriptor.nodeId;run.previewDisplay=descriptor.displayNodeId;
    const timeout=setTimeout(()=>abort.abort(),5000);
    try {
      const response=await this.client.fetchApi("/ps-bridge/v3/editor/pipeline/resources/live-preview-"+descriptor.previewId,
        {method:"POST",headers:this.client.headers(),body:JSON.stringify(this.body(state,event.runId)),signal:abort.signal});
      if(!response.ok)return;
      const reader=response.body?.getReader();if(!reader)return;
      const chunks=[];let size=0;
      try {
        for(;;) {
          const {done,value}=await reader.read();if(done)break;
          size+=value.byteLength;
          if(size>descriptor.byteSize)throw new Error("Sampling preview exceeds budget");
          chunks.push(value);
        }
      } catch(error) {await reader.cancel().catch(()=>{});throw error;}
      finally {reader.releaseLock();}
      if(size!==descriptor.byteSize||!this.current(state)||abort.signal.aborted||run.terminal
        ||run.previewSequence!==descriptor.previewSequence||run.previewAbort!==abort)return;
      this.host.preview(event,descriptor,new Blob(chunks,{type:descriptor.mime}));
    } finally {clearTimeout(timeout);}
  }
  finalOutput(identity,outputs) {
    const run=this.state?.runs.get(identity.runId);
    if(!run||run.promptId!==identity.promptId)return;
    for(const [node,entry] of Object.entries(outputs)) {
      run.finalNodes.add(node);run.finalNodes.add(entry.display_node??node);
    }
    if(run.finalNodes.has(run.previewNode)||run.finalNodes.has(run.previewDisplay))run.previewAbort?.abort();
  }
  reset() {
    const state=this.state;this.generation++;this.state=null;
    if(state) {
      state.abort.abort();if(state.raf!==null)cancelFrame(state.raf);
      for(const run of state.runs.values())run.previewAbort?.abort();
      this.host.reset();
    }
  }
}
