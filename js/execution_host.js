/** Host-version integration lives here. These Pinia/rgthree methods are internal
 * capabilities, not a stable ComfyUI extension API. Never submit a prompt here.
 */
export class ExecutionHost {
  constructor(app,api,{loadModule=url=>import(/* @vite-ignore */ url)}={}) {
    this.app=app;this.api=api;this.loadModule=loadModule;this.jobs=new Map();this.rgthree=null;
  }
  store(name="execution") {return this.app.extensionManager?._p?._s?.get(name);}
  async attach() {
    const store=this.store();
    if(!["storeJob","clearActiveJobIfStale","clearWorkflowStatus","getWorkflowStatus"].every(name=>typeof store?.[name]==="function")
      ||!store.queuedJobs||!store.nodeProgressStatesByJob
      ||typeof this.store("jobPreview")?.clearPreview!=="function"
      ||typeof this.store("nodeOutput")?.revokePreviewsByLocatorId!=="function")
      throw new Error("当前 ComfyUI 前端不支持完整测试运行反馈与清理，请更新节点包与前端后刷新页面。");
    this.rgthree=null;
    this.graph=this.app.rootGraph||this.app.graph;
    this.workflow=this.app.extensionManager?.workflow?.activeWorkflow;
    if(!this.workflow)throw new Error("无法识别当前 ComfyUI 工作流，测试运行反馈尚未启用。");
    try {
      const extensions=await this.api.getExtensions?.();
      if(extensions?.some(path=>path.includes("rgthree"))) {
        const module=await this.loadModule(this.api.apiURL?.("/rgthree/common/prompt_service.js")||"/rgthree/common/prompt_service.js");
        if(typeof module.SERVICE?.getOrMakePrompt==="function")this.rgthree=module.SERVICE;
      }
    } catch { /* Optional extension absence never disables native feedback. */ }
  }
  current() {
    return this.graph===(this.app.rootGraph||this.app.graph)&&this.workflow===this.app.extensionManager?.workflow?.activeWorkflow;
  }
  register(event,registration) {
    if(!this.current()||!event.promptId||!registration?.prompt||this.jobs.has(event.runId))return;
    const prompt=structuredClone(registration.prompt);
    // The host lookup expects titles although API prompts permit absent _meta.
    for(const node of Object.values(prompt))node._meta??={title:node.class_type};
    this.store().storeJob({id:event.promptId,nodes:Object.keys(prompt),promptOutput:prompt,
      workflow:this.workflow});
    this.rgthree?.getOrMakePrompt(event.promptId)?.setPrompt({output:prompt});
    this.jobs.set(event.runId,{promptId:event.promptId,workflow:this.workflow,previews:new Map(),terminal:false});
    if(this.jobs.size>32) {
      const completed=[...this.jobs].find(([,job])=>job.terminal);
      if(completed){this.clearPreviews(completed[1]);this.jobs.delete(completed[0]);}
      else throw new Error("Too many active editor jobs");
    }
  }
  owned(event) {
    const job=this.jobs.get(event.runId),active=this.store()?.activeJobId;
    return this.current()&&job?.promptId===event.promptId&&(!active||active===event.promptId);
  }
  dispatch(type,detail) {this.api.dispatchEvent(new CustomEvent(type,{detail}));}
  native(event,type,data) {
    const job=this.jobs.get(event.runId);
    if(!job||job.promptId!==event.promptId||data?.prompt_id!==event.promptId||job.terminal)return;
    const isTerminal=["execution_success","execution_error","execution_interrupted"].includes(type);
    if(!this.owned(event)) {
      // A later ordinary Queue may already own the host. Still remember this
      // job's real terminal so detachment cannot clear the newer workflow status.
      if(isTerminal)job.terminal=true;
      return;
    }
    if(type==="executed"||type==="status")return;
    if(type==="execution_start"&&this.store()?.activeJobId===event.promptId)return;
    if(type==="executing")this.dispatch(type,data.display_node??data.node??null);
    else this.dispatch(type,structuredClone(data));
    if(isTerminal) {
      this.dispatch("executing",null);job.terminal=true;
    }
  }
  preview(event,descriptor,blob) {
    if(!this.owned(event)||this.jobs.get(event.runId)?.terminal)return;
    const before=new Map(Object.entries(this.app.nodePreviewImages||{}));
    this.dispatch("b_preview_with_metadata",{blob,nodeId:descriptor.nodeId,displayNodeId:descriptor.displayNodeId,
      parentNodeId:descriptor.parentNodeId,realNodeId:descriptor.realNodeId,jobId:event.promptId});
    this.dispatch("b_preview",blob);
    const job=this.jobs.get(event.runId);
    for(const [locator,urls] of Object.entries(this.app.nodePreviewImages||{}))
      if(urls!==before.get(locator))job.previews.set(locator,[...urls]);
  }
  clearPreviews(job) {
    this.store("jobPreview")?.clearPreview(job.promptId);
    for(const [locator,urls] of job.previews) {
      const current=this.app.nodePreviewImages?.[locator];
      if(current?.length===urls.length&&current.every((url,index)=>url===urls[index]))
        this.store("nodeOutput")?.revokePreviewsByLocatorId(locator);
    }
    job.previews.clear();
  }
  reset() {
    // Clearing local display is not an execution interruption or a cancellation.
    // Do not clear a normal Queue job that has subsequently become active.
    const store=this.store();
    const jobs=[...this.jobs.values()],ids=new Set(jobs.map(job=>job.promptId));
    const otherJobs=Object.entries(store?.queuedJobs||{}).filter(([id])=>!ids.has(id));
    if(ids.has(store?.activeJobId)) {
      // This exported host capability performs its own full state/URL cleanup.
      // Unlike a terminal event it does not claim the backend run was cancelled.
      store.clearActiveJobIfStale(new Set(otherJobs.map(([id])=>id)));
      this.dispatch("executing",null);
    }
    for(const job of jobs) {
      this.clearPreviews(job);
      if(store?.queuedJobs)delete store.queuedJobs[job.promptId];
      if(store?.nodeProgressStatesByJob)delete store.nodeProgressStatesByJob[job.promptId];
      if(!job.terminal&&store?.getWorkflowStatus(job.workflow)==="running"
        &&!otherJobs.some(([,other])=>other.workflow===job.workflow))store.clearWorkflowStatus(job.workflow);
    }
    // rgthree can still display our last job after a newer normal Queue starts.
    // Clear only its own stale reference without dispatching a global terminal.
    if(ids.has(this.rgthree?.currentExecution?.id)) {
      this.rgthree.currentExecution=null;this.rgthree.dispatchProgressUpdate?.();
    }
    this.jobs.clear();this.graph=null;this.workflow=null;
  }
}
