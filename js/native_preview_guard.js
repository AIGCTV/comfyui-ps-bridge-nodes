// ComfyUI's classic image loader commits node.imgs after asynchronous onload.
// Keep that native loader, but validate its commit against the current output.
const STATIC_PREVIEW_NODES=new Set(["PreviewImage","SaveImage","VP_SendToPS"]);
const guardsByApp=new WeakMap();
const fileKey=file=>file&&typeof file.filename==="string"
  ?JSON.stringify([file.type||"output",file.subfolder||"",file.filename]):null;

function property(node,name) {
  const own=Object.getOwnPropertyDescriptor(node,name);
  if(own?.configurable===false)return null;
  let descriptor=own,prototype=Object.getPrototypeOf(node);
  while(!descriptor&&prototype){descriptor=Object.getOwnPropertyDescriptor(prototype,name);prototype=Object.getPrototypeOf(prototype);}
  if(descriptor&&("get" in descriptor||"set" in descriptor)&&!descriptor.set)return null;
  if(descriptor&&"writable" in descriptor&&!descriptor.writable)return null;
  let value=node[name];
  const get=()=>descriptor?.get?descriptor.get.call(node):value;
  const set=next=>{if(descriptor?.set)descriptor.set.call(node,next);else value=next;};
  return {get,set,install(setter){Object.defineProperty(node,name,{configurable:true,enumerable:own?.enumerable??true,get,set:setter});},
    restore(){
      const current=get();
      if(own)Object.defineProperty(node,name,"value" in own?{...own,value:current}:own);
      else {delete node[name];if(!descriptor?.set&&current!==undefined)node[name]=current;}
    }};
}

export class NativeImagePreviewGuard {
  /** @param {any} node @param {{viewURL?:string,alive?:()=>boolean}} options */
  constructor(node,{viewURL,alive=()=>true}={}) {
    this.node=node;this.alive=alive;this.viewURL=viewURL?new URL(viewURL):null;
    this.images=property(node,"imgs");this.index=property(node,"imageIndex");
    if(!this.images||!this.index)throw new Error("Native image preview properties cannot be protected");
    this.active=true;this.accepted=null;this.previous=node.images;this.preview=node.preview;this.pendingIndex=null;this.committed=null;
    /** @type {((count:number)=>void)|null} */ this.onCommit=null;
    this.index.install(value=>{
      // Native onLoaded resets imageIndex immediately before assigning imgs.
      // Roll that reset back too when the same callback submits stale images.
      const pending=value===null?{before:this.index.get()}:null;
      this.pendingIndex=pending;this.index.set(value);
      if(pending)queueMicrotask(()=>{if(this.pendingIndex===pending)this.pendingIndex=null;});
    });
    this.images.install(value=>{
      if(!this.active||this.accepts(value)) {
        // A delayed duplicate of an immutable cached image cannot clear a user's
        // newer frame selection after that binding has already been displayed.
        const prior=this.images.get();
        if(this.active&&this.committed===this.binding().reference&&Array.isArray(value)&&Array.isArray(prior)
          &&value.length===prior.length&&value.every((item,i)=>this.sourceKey(item?.src)===this.sourceKey(prior[i]?.src))) {
          if(this.pendingIndex)this.index.set(this.pendingIndex.before);
        } else {
          this.images.set(value);this.committed=this.binding().reference;
          if(this.active&&Array.isArray(value)&&value.length&&this.onCommit
            &&this.binding().reference===this.accepted
            &&JSON.stringify(this.binding().keys)===JSON.stringify((this.accepted||[]).map(fileKey).filter(Boolean))) {
            const notify=this.onCommit;this.onCommit=null;
            try{notify(value.length);}catch{/* Diagnostics never change native image loading. */}
          }
        }
        this.pendingIndex=null;
      } else if(this.pendingIndex) {
        this.index.set(this.pendingIndex.before);this.pendingIndex=null;
      }
    });
  }
  /** @param {any[]} images @param {((count:number)=>void)|null} onCommit */
  expect(images,onCommit=null) {
    this.previous=this.node.images;this.preview=this.node.preview;this.accepted=images;
    this.onCommit=onCommit;
  }
  invalidate() {
    // Retain the guard after detach: already-started native promises cannot be
    // cancelled. A subsequent ordinary Queue assigns a new node.images reference.
    this.previous=this.node.images;this.preview=this.node.preview;this.accepted=[];this.committed=null;this.onCommit=null;
  }
  binding() {
    const native=this.node.images;
    const expected=native!==this.previous&&native!==this.accepted?native:this.accepted;
    const currentPreview=this.node.preview;
    const previewUrls=Array.isArray(currentPreview)&&currentPreview!==this.preview?currentPreview:[];
    return previewUrls.length?{reference:currentPreview,keys:previewUrls.map(source=>this.sourceKey(source))}
      :{reference:expected,keys:(expected||[]).map(fileKey).filter(Boolean)};
  }
  sourceKey(source) {
    if(typeof source!=="string")return null;
    if(source.startsWith("blob:"))return source;
    try {
      const url=new URL(source,this.viewURL||undefined);
      if(this.viewURL&&(url.origin!==this.viewURL.origin||url.pathname!==this.viewURL.pathname))return null;
      if(!this.viewURL&&!url.pathname.endsWith("/view"))return null;
      return fileKey({filename:url.searchParams.get("filename"),subfolder:url.searchParams.get("subfolder")||"",
        type:url.searchParams.get("type")||"output"});
    } catch {return null;}
  }
  accepts(elements) {
    if(!this.alive())return false;
    const {keys}=this.binding();
    if(!Array.isArray(elements)||!elements.length)return !keys.length;
    // The native loader filters failed frames. Accept only an ordered subsequence
    // (including duplicate counts), never the same asset set in another order.
    let index=0;
    return elements.every(element=>{
      const key=this.sourceKey(element?.src);
      if(key===null)return false;
      while(index<keys.length&&keys[index]!==key)index++;
      if(index===keys.length)return false;
      index++;return true;
    });
  }
  dispose() {
    if(!this.active)return;
    this.active=false;this.images.restore();this.index.restore();this.pendingIndex=null;this.onCommit=null;
  }
}

/** @param {any} app @param {any} api @param {any} node @param {any[]} images @param {((count:number)=>void)|null} onCommit */
export function protectNativeImagePreview(app,api,node,images,onCommit=null) {
  if(!STATIC_PREVIEW_NODES.has(node?.comfyClass||node?.type)||!Array.isArray(images))return;
  let guards=guardsByApp.get(app);
  if(!guards){guards=new Map();guardsByApp.set(app,guards);}
  let guard=guards.get(node);
  if(!guard) {
    const graph=app.rootGraph||app.graph;
    const base=typeof location!=="undefined"?location.href:"http://localhost/";
    const viewURL=new URL(api.apiURL?.("/view")||"/view",base).href;
    guard=new NativeImagePreviewGuard(node,{viewURL,alive:()=>Boolean(node.graph)&&(app.rootGraph||app.graph)===graph});
    guards.set(node,guard);
    const removed=node.onRemoved;
    const onRemoved=function(...args){guard.dispose();guards.delete(node);guard.restoreRemoved();return removed?.apply(this,args);};
    node.onRemoved=onRemoved;
    guard.restoreRemoved=()=>{if(node.onRemoved===onRemoved)node.onRemoved=removed;};
  }
  guard.expect(images,onCommit);
}

export function invalidateNativeImagePreviews(app) {
  for(const guard of guardsByApp.get(app)?.values()||[])guard.invalidate();
}

export function disposeNativeImagePreviews(app) {
  const guards=guardsByApp.get(app);
  if(!guards)return;
  for(const guard of guards.values()){guard.dispose();guard.restoreRemoved?.();}
  guards.clear();guardsByApp.delete(app);
}
