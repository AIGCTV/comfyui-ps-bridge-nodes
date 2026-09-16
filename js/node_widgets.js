import {t,localizeNode} from "./i18n.js";
import {NODE_TYPES,kind,value,graphNodes,issues,SLOTS,SLOT_LABELS,randomSeed,normalizeSlider} from './node_bindings.js';
import {NODE_WIDTH,layouts,controlLayout,hitControl,drawControls} from './canvas_controls.js';
import {commitChange,beginGesture} from './node_state.js';
import {ResourceScope} from './resource_scope.js';
import {editorFor,installEditorStyle,finishEditing} from './inline_editor.js';
import {openCanvasMenu} from './canvas_menu.js';

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
    w.options??={};w.options.socketless=true;w.hidden=true;if(w.element)w.element.hidden=true;
  }
  const connected=[];
  for(let i=node.inputs.length-1;i>=0;i--)if(names.has(node.inputs[i].name)){
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

/** A single host boundary: native node order/transform/dragging, custom control geometry. */
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
  const report=message=>{state.error=message;state.refresh();
    if(type==='VP_SendToPS')app.extensionManager.toast.add({severity:'warn',summary:'PS Vplugins',detail:message,life:5000});};
  state.refresh=()=>{node.boxcolor=state.graphError?'#ffb3a7':'#65c9e7';editorFor(app).sync(node);node.setDirtyCanvas?.(true,true);};
  state.slotLabel=()=>t(SLOT_LABELS[SLOTS.indexOf(value(node,'slot'))]||'');
  state.change=(changes,definition=false,transaction=true)=>{
    if(app.canvas.read_only||state.disabled)throw new Error(t("This node cannot be edited right now"));
    const result=commitChange(node,changes,{definition,transaction});
    state.error='';
    if(definition)checkNodes(app);else state.refresh();
    return result;
  };
  const editField=(field,v,transaction=true)=>{
    const changes={[field]:v};
    if(type==='VP_Seed'&&field==='value')changes.mode='fixed';
    return state.change(changes,type==='VP_Slider'&&field!=='value',transaction);
  };
  state.syncInputs=()=>{
    const connected=adaptNodeInputs(node);
    if(connected.length){
      const message=t('{node}: removed old parameter links ({fields}). Check the current parameter values.',{node:node.title,fields:connected.join(', ')});
      report(message);app.extensionManager.toast.add({severity:'warn',summary:'PS Vplugins',detail:message,life:12000});
    }
  };
  state.syncInputs();
  localizeNode(node);

  const revoke=()=>{if(state.url)URL.revokeObjectURL(state.url);state.url=null;};
  state.loadPreview=async(loader,previewKind='local')=>{
    const operation=resources.start('preview');
    if(!operation.current())return;
    revoke();state.image=null;state.previewKind=null;state.refresh();
    let ownedURL=null;
    try{
      const media=await loader(operation.signal);
      if(!operation.current())return;
      const url=media instanceof Blob?(ownedURL=URL.createObjectURL(media)):media;
      if(!url){revoke();state.image=null;state.previewKind=null;state.refresh();return;}
      const image=new Image();image.src=url;
      const cancel=()=>{image.src='';};operation.signal.addEventListener('abort',cancel,{once:true});
      try{await image.decode();}finally{operation.signal.removeEventListener('abort',cancel);}
      if(!operation.current())return;
      revoke();state.url=ownedURL;ownedURL=null;state.image=image;state.previewKind=previewKind;state.refresh();
    }catch(error){
      if(operation.current()){revoke();state.image=null;state.previewKind=null;state.refresh();}
    }finally{if(ownedURL)URL.revokeObjectURL(ownedURL);}
  };
  state.showPreview=(url)=>{
    if(!url){resources.cancel('preview');revoke();state.image=null;state.previewKind=null;state.refresh();return Promise.resolve();}
    return state.loadPreview(async()=>url);
  };
  const viewURL=parts=>api.apiURL('/view')+'?'+new URLSearchParams(parts);
  state.previewFile=()=>{
    if(type!=='VP_Image'||value(node,'request_id'))return;
    const file=value(node,'file_name');if(!file){state.showPreview(null);return;}
    const path=String(file).replaceAll('\\','/').split('/'),filename=path.pop();
    state.showPreview(viewURL({filename,subfolder:path.join('/'),type:'input'}));
  };
  const upload=()=>{
    const picker=document.createElement('input');picker.type='file';picker.accept='image/png,image/jpeg,image/webp';
    picker.onchange=async()=>{
      const file=picker.files?.[0];if(!file)return;
      const operation=resources.start('upload');
      if(!operation.current())return;
      try{
        const form=new FormData();form.append('image',file);form.append('type','input');form.append('overwrite','false');
        const response=await api.fetchApi('/upload/image',{method:'POST',body:form,signal:operation.signal});
        if(!response.ok)throw new Error(t("Upload failed"));
        const data=await response.json();if(!operation.current())return;
        if(typeof data.name!=='string'||!data.name)throw new Error(t("The upload returned an invalid file name"));
        state.change({file_name:[data.subfolder,data.name].filter(Boolean).join('/'),selection_file:'',request_id:''},true);
        state.previewFile();
      }catch(error){if(operation.current())report(error.message);}
    };
    picker.click();
  };
  const activate=(c,event)=>{
    try{
      if(c.kind==='number'||c.kind==='text'){editorFor(app).open(node,c,editField,report);return;}
      if(c.id==='upload'){upload();return;}
      if(c.id==='slot'){
        const owner=node.graph;
        const items=SLOTS.map((slot,i)=>({content:t(SLOT_LABELS[i]),className:value(node,'slot')===slot?'selected':'',disabled:graphNodes(root(app)).some(n=>n!==node&&kind(n)==='VP_Image'&&value(n,'slot')===slot),
          callback:()=>{try{if(node.graph!==owner||!owner)return;resources.invalidate();state.change({slot,...(slot==='main'?{required:true}:{selection_file:''})},true);state.previewFile();}catch(error){report(error.message);}}}));
        state.closeMenu?.();state.closeMenu=openCanvasMenu(app,node,c,items,event);return;
      }
      if(c.id==='fixed'||c.id==='random')state.change({mode:c.id});
      else if(c.id==='once')state.change({value:randomSeed(),mode:'fixed'});
      else if(c.number)state.change({value:c.number});
    }catch(error){report(error.message);}
  };
  const layout=layouts[type];
  if(type==='VP_Image'){
    node.widgets_start_y=0;
    const oldArrange=node.arrange;
    node.arrange=function(...args){
      const geometry=controlLayout(type,this.size[0],0,Math.max(this.size[1],layout.height),state.graphError||state.error);
      for(const [i,output] of this.outputs.entries()){
        output.pos=globalThis.LiteGraph.vueNodesMode?undefined:geometry.outputPositions[i];
      }
      return oldArrange.apply(this,args);
    };
  }
  if(type!=='VP_SendToPS'){
    const paint=(ctx,n,width,y,height)=>{
      state.disabled=!!(app.canvas.read_only||custom.computedDisabled);
      state.layout=controlLayout(type,width,y,height,state.graphError||state.error);
      ctx.save();if(state.disabled)ctx.globalAlpha*=0.5;drawControls(ctx,n,state.layout,state);ctx.restore();
    };
    const custom=node.addCustomWidget({name:'ps_controls',type:'custom',value:'',serialize:false,
      options:{serialize:false,canvasOnly:true},
      beforeQueued(){if(finishEditing(app)===false)throw new Error(t("Finish IME input first"));},
      // Width is owned by the node. The host adds scalar label/value padding to
      // widget minWidth, which would otherwise inflate our full-panel minimum.
      computeLayoutSize:()=>({minHeight:layout.height,minWidth:0,...(layout.grow?{}:{maxHeight:layout.height})}),
      draw(ctx,n,width,y,height){
        // The host's legacy draw callback passes the default row height as H;
        // computedHeight is the actual allocation from computeLayoutSize.
        if(!layout.underSlots)paint(ctx,n,width,y,this.computedHeight??height);
      },
      onPointerDown(pointer,n,canvas){
        if(state.disabled||canvas.read_only)return true;
        const e=pointer.eDown,c=state.layout&&hitControl(state.layout,e.canvasX-n.pos[0],e.canvasY-n.pos[1]);
        if(!c)return false;
        state.pressed=c.id;state.refresh();
        let end=()=>{};
        if(c.kind==='slider'){
          end=beginGesture(n);
          const update=event=>{try{
            const min=value(n,'min'),max=value(n,'max'),step=value(n,'step');
            const ratio=Math.max(0,Math.min(1,(event.canvasX-n.pos[0]-c.x-6)/(c.w-12)));
            // The last tick may be below max; do not generate an invalid upper tick.
            const upper=min+Math.floor((max-min)/step+1e-10)*step;
            const v=Math.min(upper,min+ratio*(max-min));
            state.change({value:normalizeSlider(v,min,max,step)},false,false);
          }catch(error){report(error.message);}};
          update(e);pointer.onDrag=update;
        }else pointer.onClick=event=>activate(c,event);
        pointer.finally=()=>{end();state.pressed=null;state.refresh();};
        return true;
      },
    });
    state.canvasWidget=custom;
    if(layout.underSlots){
      // The host normally draws slots before widgets. For content beneath slots,
      // paint once in that same node pass, immediately before its native slots.
      const oldSlots=node.drawSlots;
      node.drawSlots=function(ctx,options){
        paint(ctx,this,this.size[0],custom.y,custom.computedHeight??layout.height);
        // A small native-label shadow stays legible over bright image pixels;
        // there is no filled rectangle behind the output names or sockets.
        ctx.save();ctx.shadowColor='#000c';ctx.shadowBlur=3;ctx.shadowOffsetX=0;ctx.shadowOffsetY=0;
        try{return oldSlots.call(this,ctx,options);}finally{ctx.restore();}
      };
    }
    // LiteGraph exposes rectangular widget hits. Refine this one custom widget with
    // the same control layout used to draw; decorative space keeps native node dragging.
    const oldHit=node.getWidgetOnPos;
    node.getWidgetOnPos=function(x,y,...args){
      const found=oldHit.call(this,x,y,...args);
      return found===custom&&(!state.layout||!hitControl(state.layout,x-this.pos[0],y-this.pos[1]))?undefined:found;
    };
    const oldMove=node.onMouseMove,oldLeave=node.onMouseLeave;
    node.onMouseMove=function(e,pos,...args){
      const hover=state.layout&&hitControl(state.layout,pos[0],pos[1])?.id;
      if(state.hover!==hover){state.hover=hover;state.refresh();}
      return oldMove?.call(this,e,pos,...args);
    };
    node.onMouseLeave=function(...args){state.hover=null;state.refresh();return oldLeave?.apply(this,args);};
  }
  const oldSize=node.computeSize,oldConfigure=node.onConfigure,oldRemoved=node.onRemoved,oldExecuted=node.onExecuted;
  node.computeSize=function(...args){
    const size=oldSize.apply(this,args);size[0]=Math.max(size[0],NODE_WIDTH);
    if(type==='VP_SendToPS')size[1]=Math.max(size[1],this.imgs?.length||this.images?.length?325:140);
    return size;
  };
  node.onConfigure=function(...args){
    state.closeMenu?.();editorFor(app).cancelNode(this);resources.invalidate();oldConfigure?.apply(this,args);
    localizeNode(this);
    // Full graph loads reconcile after all link endpoints have been restored.
    if(!app.configuringGraph)state.syncInputs();
    const minimum=this.computeSize();this.setSize([Math.max(this.size[0],minimum[0]),Math.max(this.size[1],minimum[1])]);
    state.refresh();
  };
  node.onRemoved=function(...args){state.closeMenu?.();resources.dispose();editorFor(app).cancelNode(this);revoke();oldRemoved?.apply(this,args);
    queueMicrotask(()=>{if(!app.configuringGraph)checkNodes(app);});};
  node.onExecuted=function(output){
    oldExecuted?.call(this,output);
    if(type==='VP_SendToPS'&&output?.images?.length)this.setSize([this.size[0],Math.max(this.size[1],325)]);
    if(type==='VP_Seed'&&output?.actual_seed?.length)commitChange(this,{value:output.actual_seed[0]},{source:'execution'});
    if(type==='VP_Image'&&output?.ps_preview&&!value(this,'request_id')&&output.ps_preview_file?.[0]===value(this,'file_name'))
      state.showPreview(output.ps_preview[0]?viewURL(output.ps_preview[0]):null);
  };
  const minimum=node.computeSize();node.setSize([minimum[0],minimum[1]+(layout.grow?40:0)]);
  state.refresh();
}

const initialized=new WeakSet();
export function installCanvasInteractions(app){
  const canvas=app.canvas.canvas;if(initialized.has(canvas))return;initialized.add(canvas);
  canvas.addEventListener('wheel',event=>{
    const r=canvas.getBoundingClientRect(),ds=app.canvas.ds;
    const x=(event.clientX-r.left)/ds.scale-ds.offset[0],y=(event.clientY-r.top)/ds.scale-ds.offset[1];
    const node=app.canvas.graph?.getNodeOnPos(x,y),state=node?.__psUI;
    const c=state?.layout&&hitControl(state.layout,x-node.pos[0],y-node.pos[1]);
    if(c?.kind!=='text'||!state.textCache||state.textCache.lines.length*19.2<=c.h-16)return;
    state.scroll=Math.max(0,Math.min(state.textCache.lines.length*19.2-c.h+16,state.scroll+event.deltaY));
    state.refresh();event.preventDefault();event.stopImmediatePropagation();
  },{capture:true,passive:false});
}
