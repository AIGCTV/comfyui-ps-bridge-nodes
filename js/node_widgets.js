import {t,localizeNode} from "./i18n.js";
import {responseError, requestError, safeErrorText} from './transport_errors.js';
import {NODE_TYPES,kind,value,graphNodes,issues,SLOTS,SLOT_LABELS} from './node_bindings.js';

import {commitChange} from './node_state.js';
import {ResourceScope} from './resource_scope.js';
import {editorFor,installEditorStyle} from './inline_editor.js';
import {installPanel} from './node_panel.js';
import {applyingWidgetValue,setWidgetValue} from './widget_values.js';

const root=app=>app.rootGraph||app.graph;
export const installStyle=installEditorStyle;
/** Enforce backend socketless declarations without changing widgets or output order.
 * Returns formerly connected fields so callers can report a legacy workflow repair. */
export function adaptNodeInputs(node){
  if(!NODE_TYPES.includes(kind(node)))return [];
  const inputs=node.constructor.nodeData?.input;
  const names=new Set(Object.entries({...inputs?.required,...inputs?.optional})
    .filter(([,spec])=>spec[1]?.socketless===true).map(([name])=>name));
  for(const w of node.widgets||[])if(names.has(w.name)){
    w.options??={};w.options.socketless=true;w.hidden=true;w.options.advanced=false;
    if(w.hidden)w.advanced=false;
    if(w.element)w.element.hidden=w.hidden;
  }
  const connected=[];
  for(let i=(node.inputs||[]).length-1;i>=0;i--)if(names.has(node.inputs[i].name)){
    if(node.inputs[i].link!=null)connected.push(node.inputs[i].name);
    node.removeInput(i);
  }
  return connected.reverse();
}
export function refreshNode(node){node.__psUI?.refresh();}
export function checkNodes(app){
  const nodes=graphNodes(root(app)),errors=issues(nodes);
  for(const node of nodes)if(node.__psUI){node.__psUI.graphError=errors.find(e=>e.node===node)?.message||'';node.__psUI.refresh();}
  return errors;
}

/** Host widgets own serialized values; the DOM panel supplies interaction and previews. */
export function installNode(node,app,api=app.api){
  const type=kind(node);
  if(!NODE_TYPES.includes(type)||node.__psUI)return;
  installStyle();
  node.color='#24364b';node.bgcolor='#151e28';node.boxcolor='#65c9e7';
  const state=/** @type {import('../types/editor.js').NodeUIState} */ ({composing:false,graphError:'',error:'',image:null,url:null,previewKind:null,scroll:0,
    hover:null,pressed:null,layout:null,textCache:null,disabled:false,refresh:()=>{}});
  Object.defineProperty(node,'__psUI',{value:state,configurable:true});
  const resources=new ResourceScope(()=>!!node.graph&&(node.graph.rootGraph||node.graph)===root(app));
  state.resources=resources;
  const report=(message,notify=true)=>{state.error=message;state.refresh();
    if(notify&&type==='VP_SendToPS')app.extensionManager.toast.add({severity:'warn',summary:'PS Vplugins',detail:message,life:5000});};
  state.refresh=()=>{node.boxcolor=state.graphError?'#ffb3a7':'#65c9e7';node.setDirtyCanvas?.(true,true);};
  state.slotLabel=()=>t(SLOT_LABELS[SLOTS.indexOf(value(node,'slot'))]||'');
  state.change=(changes,definition=false,transaction=true)=>{
    if(app.canvas.read_only||state.disabled)throw new Error(t("This node cannot be edited right now"));
    const result=commitChange(node,changes,{definition,transaction});
    state.error='';
    if(definition)checkNodes(app);else state.refresh();
    return result;
  };
  state.syncInputs=()=>{
    const connected=adaptNodeInputs(node);
    if(connected.length){
      const message=t('{node}: removed old parameter links ({fields}). Check the current parameter values.',{node:node.title,fields:connected.join(', ')});
      report(message,false);app.extensionManager.toast.add({severity:'warn',summary:'PS Vplugins',detail:message,life:12000});
    }
  };
  state.syncInputs();
  localizeNode(node);

  let previewElement,panel;

  const revoke=()=>{if(state.url)URL.revokeObjectURL(state.url);state.url=null;};
  let previewError='';
  state.loadPreview=async(loader,previewKind='local',fallbackURL=null)=>{
    const operation=resources.start('preview');
    if(!operation.current())return;
    if(!fallbackURL){
      revoke();state.image=null;node.imgs=[];if(previewElement)previewElement.style.display='none';state.previewKind=null;state.refresh();
    }
    let ownedURL=null;
    try{
      const media=await loader(operation.signal);
      if(!operation.current())return;
      let url=media instanceof Blob?(ownedURL=URL.createObjectURL(media)):media;
      if(!url){revoke();state.image=null;node.imgs=[];state.previewKind=null;state.refresh();return;}
      const image=new Image();
      const cancel=()=>{image.removeAttribute('src');};
      operation.signal.addEventListener('abort',cancel,{once:true});
      try{
        image.src=url;
        try{await image.decode();}
        catch(error){
          // An execution preview may have expired. Reuse the same local input.
          if(!fallbackURL||!operation.current())throw error;
          url=fallbackURL;
          image.src=url;
          await image.decode();
        }
      }finally{operation.signal.removeEventListener('abort',cancel);}
      if(!operation.current())return;
      revoke();state.url=ownedURL;ownedURL=null;state.image=image;state.previewKind=previewKind;
      if(previewElement){previewElement.src=url;previewElement.style.display='block';}
      if(state.error===previewError)state.error="";previewError="";state.refresh();
    }catch(error){
      if(operation.current()&&!operation.signal.aborted){revoke();state.image=null;state.previewKind=null;previewError=t('Preview failed: {reason}',{reason:safeErrorText(error.message)||t('No safe preview error detail was provided')});report(previewError);}
    }finally{if(ownedURL)URL.revokeObjectURL(ownedURL);}
  };
  state.showPreview=(url,fallbackURL=null)=>{
    if(!url){panel?.clear();resources.cancel('preview');revoke();state.image=null;node.imgs=[];if(previewElement){previewElement.removeAttribute('src');previewElement.style.display='none';}state.previewKind=null;state.refresh();return Promise.resolve();}
    return state.loadPreview(async()=>url,'local',fallbackURL);
  };
  const viewURL=({filename,subfolder='',type})=>api.apiURL('/view')+'?'+new URLSearchParams({filename,subfolder,type});
  const inputPreviewURL=file=>{
    const path=String(file).replaceAll('\\','/').split('/'),filename=path.pop();
    return viewURL({filename,subfolder:path.join('/'),type:'input'});
  };
  state.previewFile=()=>{
    if(type!=='VP_Image'||value(node,'request_id'))return;
    const file=value(node,'file_name');
    state.showPreview(file?inputPreviewURL(file):null);
  };
  const canUpload=()=>!app.canvas.read_only&&!state.disabled;
  /** @param {File} file */
  const uploadFile=async file=>{
    if(!canUpload())return;
    const operation=resources.start('upload');
    if(!operation.current())return;
    try{
      const form=new FormData();form.append('image',file);form.append('type','input');form.append('overwrite','false');
      let response;
      try{response=await api.fetchApi('/upload/image',{method:'POST',body:form,signal:operation.signal});}
      catch(error){throw requestError('/upload/image',error);}
      if(!response.ok)throw await responseError(response,'/upload/image');
      let data;
      try{data=await response.json();}catch{throw await responseError(response,'/upload/image',null);}
      if(!operation.current()||!canUpload())return;
      if(typeof data.name!=='string'||!data.name)throw new Error(t("The upload returned an invalid file name"));
      const uploaded=[data.subfolder,data.name].filter(Boolean).join('/');
      for(const peer of graphNodes(root(app)).filter(n=>kind(n)==='VP_Image')) {
        const fileWidget=peer.widgets?.find(w=>w.name==='file_name');
        if(Array.isArray(fileWidget?.options?.values)&&!fileWidget.options.values.includes(uploaded))
          fileWidget.options.values.push(uploaded);
      }
      state.change({file_name:uploaded,selection_file:'',request_id:''},true);
      state.previewFile();
    }catch(error){if(operation.current())report(error.message);}
  };
  const upload=()=>{
    const picker=document.createElement('input');picker.type='file';picker.accept='image/png,image/jpeg,image/webp';
    picker.onchange=()=>{
      const file=picker.files?.[0];
      if(file)void uploadFile(file);
    };
    picker.click();
  };
  if(type==='VP_Image'){
    const imageType=/^image\/(png|jpeg|webp)$/i;
    const imageName=/\.(png|jpe?g|webp)$/i;
    /** @param {DragEvent} event */
    node.onDragOver=event=>canUpload()&&Array.from(event.dataTransfer?.items??[])
      .some(item=>item.kind==='file'&&(!item.type||imageType.test(item.type)));
    /** @param {DragEvent} event */
    node.onDragDrop=event=>{
      const file=Array.from(event.dataTransfer?.files??[]).find(file=>imageName.test(file.name));
      if(!file||!canUpload())return false;
      void uploadFile(file);
      return true;
    };
  }
  // Native widgets are rendered by both canvas and Vue nodes. Their values are
  // the serialized source of truth; never share widget objects across nodes.
  for(const w of node.widgets||[]) {
    if(w.name==='request_id'||w.options?.serialize===false)continue;
    const original=w.callback;
    let previous=w.value,busy=false;
    w.callback=function(next,...args) {
      if(busy||applyingWidgetValue(w))return;
      if(app.configuringGraph){previous=next;return;}
      busy=true;
      try {
        setWidgetValue(w,previous);
        const definition=['slot','required','file_name','selection_file','min','max','step','source_json','param_id','label','result_id','filename_prefix'].includes(w.name);
        state.change({[w.name]:next,...(type==='VP_Seed'&&w.name==='value'?{mode:'fixed'}:{})},definition);
        previous=w.value;
        original?.call(this,w.value,...args);
        if(type==='VP_Image')state.previewFile();
      }catch(error){report(error.message);}finally{busy=false;}
    };
    // Remember remote updates as well as local edits for native callback ordering.
    const refresh=state.refresh;
    state.refresh=()=>{previous=w.value;refresh();};
    if(w.element) {
      w.element.addEventListener('compositionstart',()=>{state.composing=true;});
      w.element.addEventListener('compositionend',()=>{state.composing=false;
        document.dispatchEvent(new CustomEvent('ps-vplugins:compositionend'));});
    }
  }
  panel=installPanel(node,state,app,upload);previewElement=panel.image;
  // The host background handler creates a second image-preview widget from
  // nodeOutputs asynchronously. These nodes already own a DOM media surface.
  // Keep execution outputs intact; replace only that default preview renderer.
  const media=type==='VP_Image'||type==='VP_SendToPS';
  if(media){node.onDrawBackground=null;node.hideOutputImages=true;}
  const resetLayout=()=>{
    // Older canvas layouts serialized absolute socket positions and widget offsets.
    // Let the host place sockets at the current node edge after resize/load.
    for(const slot of [...(node.inputs||[]),...(node.outputs||[])])delete slot.pos;
    delete node.widgets_start_y;
    if(node.widgets)for(let i=node.widgets.length-1;i>=0;i--){
      const w=node.widgets[i];if(w.name==='$$canvas-image-preview'){w.onRemove?.();node.widgets.splice(i,1);}
    }
  };
  resetLayout();
  const oldConfigure=node.onConfigure,oldRemoved=node.onRemoved,oldExecuted=node.onExecuted;
  node.computeSize=function(...args){
    const rows=Math.max(this.inputs?.length||0,this.outputs?.length||0,1);
    return [panel.width,panel.height+rows*20+18];
  };
  node.onConfigure=function(...args){
    state.closeMenu?.();editorFor(app).cancelNode(this);resources.invalidate();oldConfigure?.apply(this,args);
    localizeNode(this);
    // Full graph loads reconcile after all link endpoints have been restored.
    if(!app.configuringGraph)state.syncInputs();
    resetLayout();
    // Undo restores saved media geometry before the asynchronous image reload.
    if(media)panel.restoreMediaSize();
    else {
      const minimum=this.computeSize();
      this.setSize([Math.max(this.size[0],minimum[0]),minimum[1]]);
    }
    state.refresh();
  };
  node.onRemoved=function(...args){state.closeMenu?.();panel.dispose();resources.dispose();editorFor(app).cancelNode(this);revoke();oldRemoved?.apply(this,args);
    queueMicrotask(()=>{if(!app.configuringGraph)checkNodes(app);});};
  node.onExecuted=function(output){
    oldExecuted?.call(this,output);
    if(type==='VP_SendToPS'&&output?.images?.length){
      this.imgs=[];this.images=[];
      panel.setResults(output.images.map(file=>viewURL(file)));
    }
    if(type==='VP_Seed'&&output?.actual_seed?.length)commitChange(this,{value:output.actual_seed[0]},{source:'execution'});
    if(type==='VP_Image'&&output?.ps_preview&&!value(this,'request_id')&&output.ps_preview_file?.[0]===value(this,'file_name'))
      state.showPreview(output.ps_preview[0]?viewURL(output.ps_preview[0]):null,
        output.ps_preview_file[0]?inputPreviewURL(output.ps_preview_file[0]):null);
  };
  const resized=node.onResize;
  if(media)node.onResize=function(...args){resized?.apply(this,args);panel.fitMedia();};
  const minimum=node.computeSize();node.setSize([minimum[0],minimum[1]]);
  state.refresh();
}
