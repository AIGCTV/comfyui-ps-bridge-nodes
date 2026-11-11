import {kind,value,SLOTS,randomSeed} from './node_bindings.js';
import {beginGesture} from './node_state.js';
import {t} from './i18n.js';

/** One DOM surface, with all durable values owned by the original widgets. */
export function installPanel(node,state,app,upload) {
  if(!document.getElementById('ps-bridge-panel-style')){
    const css=document.createElement('style');css.id='ps-bridge-panel-style';css.textContent=`
.ps-bridge-panel{box-sizing:border-box;width:100%;height:100%;padding:10px;color:#c7d4e2;font:13px/1.4 'Segoe UI','Microsoft YaHei',sans-serif;overflow:hidden;background:#151e28}
.ps-bridge-panel [hidden]{display:none!important}.ps-bridge-panel *{box-sizing:border-box}.ps-bridge-panel .panel{padding:12px;border:1px solid #334b60;background:#192633;border-radius:12px;height:100%;display:flex;flex-direction:column;gap:10px}
.ps-bridge-panel input:not([type=range]),.ps-bridge-panel select,.ps-bridge-panel textarea{width:100%;min-width:0;border:1px solid #3c5267;border-radius:7px;background:#243647;color:#e3edf4;padding:8px;font:inherit;outline:none}
.ps-bridge-panel input:focus,.ps-bridge-panel textarea:focus{border-color:#65c9e7}.ps-bridge-panel button{cursor:pointer;border:1px solid #3c5267;border-radius:7px;padding:8px 10px;background:#233749;color:#cad8e5;font:inherit;white-space:nowrap}
.ps-bridge-panel button.active{background:#24516a;border-color:#65c9e7;color:#e8f8ff}.ps-bridge-panel button:hover{border-color:#65c9e7}.ps-bridge-panel .row{display:flex;align-items:center;gap:8px}.ps-bridge-panel .row>*{flex:1}.ps-bridge-panel .row>.plus{flex:0 0 34px;padding:6px;font-size:17px}.ps-bridge-panel input[type=range]{accent-color:#65c9e7;width:100%;height:22px;cursor:pointer}
.ps-bridge-panel .row .number{flex:0 0 80px}.ps-bridge-panel textarea{resize:none;flex:1;line-height:1.65}.ps-bridge-panel label{flex:1;font-size:11px;color:#93a9bc}.ps-bridge-panel label input{margin-top:4px}.ps-bridge-panel .preview{flex:1;min-height:0;border-radius:7px;overflow:hidden;background:repeating-conic-gradient(#23394a 0% 25%,#1c2d3d 0% 50%) 0/16px 16px;display:flex;align-items:center;justify-content:center}.ps-bridge-panel .preview img{max-width:100%;max-height:100%;object-fit:contain}.ps-bridge-panel .foot{display:flex;justify-content:space-between;color:#93a9bc;font-size:11px}.ps-bridge-panel .error{color:#ffb3a7;font-size:12px;overflow-wrap:anywhere}.ps-bridge-panel.media{padding:6px}.ps-bridge-panel.media .panel{padding:10px}
.dom-widget:has(.ps-bridge-panel.media),.lg-node-widget:has(.ps-bridge-panel.media),.ps-bridge-panel.media{pointer-events:none!important}
.ps-bridge-panel.media button,.ps-bridge-panel.media select{pointer-events:auto}
.ps-bridge-panel.media img{user-select:none;-webkit-user-drag:none}
.ps-bridge-image .preview{position:relative}
.ps-bridge-image .preview img{position:absolute;inset:0;margin:auto}
.ps-bridge-image .foot{min-height:1.4em}
.ps-bridge-panel button:disabled,.ps-bridge-panel input:disabled,.ps-bridge-panel select:disabled,.ps-bridge-panel textarea:disabled{opacity:.55;cursor:default}`;
    document.head.append(css);
  }
  const type=kind(node),media=['VP_Image','VP_SendToPS'].includes(type);
  const element=document.createElement('div');element.className='ps-bridge-panel'+(media?' media':'')+(type==='VP_Image'?' ps-bridge-image':'');
  const panel=document.createElement('div');panel.className='panel';element.append(panel);
  const abort=new AbortController(),listen=(el,event,fn)=>el.addEventListener(event,fn,{signal:abort.signal});
  const row=()=>{const el=document.createElement('div');el.className='row';panel.append(el);return el;};
  const controls=[],renderers=[];let endGesture=null;
  const start=()=>{if(!endGesture&&!app.canvas?.read_only&&!state.disabled)endGesture=beginGesture(node);};
  const end=()=>{endGesture?.();endGesture=null;};
  const change=(patch,definition=false)=>{try{state.change(patch,definition,!endGesture);}catch(error){state.error=error.message;}state.refresh();};
  const input=(parent,name,field,number=false)=>{
    const el=document.createElement('input');el.type=number?'number':'text';el.setAttribute('aria-label',t(name));parent.append(el);controls.push(el);
    listen(el,'focus',start);listen(el,'blur',()=>{end();state.refresh();});
    const commit=()=>{if(number&&(el.value===''||!Number.isFinite(Number(el.value))))return;change({[field]:number?Number(el.value):el.value,...(type==='VP_Seed'&&field==='value'?{mode:'fixed'}:{})},['min','max','step'].includes(field));};
    listen(el,'input',commit);listen(el,'change',commit);
    renderers.push(()=>{if(document.activeElement!==el)el.value=String(value(node,field)??'');});return el;
  };
  const button=(parent,label,action)=>{const el=document.createElement('button');el.type='button';el.textContent=t(label);parent.append(el);controls.push(el);listen(el,'click',action);return el;};
  if(type==='VP_Seed'){
    const seed=input(panel,'Seed','value',true);seed.min='0';seed.max=String(Number.MAX_SAFE_INTEGER);seed.step='1';
    const buttons=row();for(const mode of ['fixed','random']){const b=button(buttons,mode==='fixed'?'Fixed':'Random',()=>change({mode}));renderers.push(()=>{b.classList.toggle('active',value(node,'mode')===mode);b.setAttribute('aria-pressed',String(value(node,'mode')===mode));});}
    button(buttons,'New seed',()=>change({value:randomSeed(),mode:'fixed'}));
  }
  if(type==='VP_Slider'){
    const top=row(),slider=document.createElement('input');slider.type='range';slider.setAttribute('aria-label',t('Slider'));top.append(slider);controls.push(slider);
    const number=input(top,'Value','value',true);number.className='number';
    listen(slider,'pointerdown',start);listen(slider,'keydown',start);listen(slider,'input',()=>change({value:Number(slider.value)}));
    for(const event of ['pointerup','pointercancel','keyup','blur'])listen(slider,event,end);
    const bounds=row();for(const [field,label]of [['min','MIN'],['max','MAX'],['step','STEP']]){const holder=document.createElement('label');holder.textContent=t(label);bounds.append(holder);input(holder,label,field,true).step='any';}
    renderers.push(()=>{for(const el of [slider,number]){el.min=String(value(node,'min'));el.max=String(value(node,'max'));el.step=String(value(node,'step'));}slider.value=String(value(node,'value'));});
  }
  if(type==='VP_Prompt'){
    const text=document.createElement('textarea');text.setAttribute('aria-label',t('Prompt'));text.placeholder=t('Enter a prompt…');panel.append(text);controls.push(text);
    listen(text,'focus',start);listen(text,'blur',()=>{end();state.refresh();});
    listen(text,'compositionstart',()=>{state.composing=true;});
    listen(text,'input',()=>{if(!state.composing)change({text:text.value});});
    listen(text,'compositionend',()=>{state.composing=false;change({text:text.value});document.dispatchEvent(new CustomEvent('ps-vplugins:compositionend'));});
    renderers.push(()=>{if(!state.composing&&text.value!==value(node,'text')){const pos=text.selectionStart;text.value=String(value(node,'text')??'');if(document.activeElement===text)text.setSelectionRange(Math.min(pos,text.value.length),Math.min(pos,text.value.length));}});
  }
  if(type==='VP_Batch')for(const i of [1,2,3,4]){
    if(i===1)row();const b=button(panel.lastElementChild,String(i),()=>change({value:i}));renderers.push(()=>{b.classList.toggle('active',value(node,'value')===i);b.setAttribute('aria-pressed',String(value(node,'value')===i));});
  }
  if(type==='VP_Image'){
    const top=row(),select=document.createElement('select');select.setAttribute('aria-label',t('Image source'));
    for(const slot of SLOTS){const option=document.createElement('option');option.value=slot;option.textContent=slot;select.append(option);}top.append(select);controls.push(select);
    listen(select,'change',()=>{change({slot:select.value},true);state.previewFile();});renderers.push(()=>{select.value=value(node,'slot');});
    const plus=button(top,'+',upload);plus.className='plus';plus.title=t('Upload image');plus.setAttribute('aria-label',t('Upload image'));
  }
  let image=null,dimensions=null,counter=null,preview=null;
  if(media){preview=document.createElement('div');preview.className='preview';image=document.createElement('img');image.alt=t('Image preview');image.draggable=false;image.style.display='none';preview.append(image);panel.append(preview);
    const foot=document.createElement('div');foot.className='foot';counter=document.createElement('span');dimensions=document.createElement('span');foot.append(counter,dimensions);panel.append(foot);
  }
  const error=document.createElement('div');error.className='error';error.setAttribute('role','status');panel.append(error);
  const refresh=state.refresh;state.refresh=()=>{renderers.forEach(fn=>fn());controls.forEach(el=>{el.disabled=!!state.disabled||!!app.canvas?.read_only;});
    error.textContent=state.error||state.graphError||'';error.hidden=!error.textContent;
    if(dimensions)dimensions.textContent=state.image?`${state.image.naturalWidth} × ${state.image.naturalHeight}`:'';
    fitMedia();refresh();};
  let height={VP_Image:420,VP_Seed:145,VP_Slider:165,VP_Prompt:230,VP_Batch:95,VP_SendToPS:330}[type];
  let files=[],index=0,navigation;
  let lastWidth=0,lastRatio=0,lastExtra=0;
  const heightOutsidePanel=()=>Math.max(node.inputs?.length||0,node.outputs?.length||0,1)*20+18;
  const controlsHeight=()=>(type==='VP_Image'?46:0)+(files.length>1?46:0);
  // PS Images fits each upload inside the preview area set by the node width.
  const previewRatio=()=>type==='VP_SendToPS'&&state.image?.naturalWidth
    ?state.image.naturalHeight/state.image.naturalWidth
    :0.65;
  const restoreMediaSize=()=>{
    height=Math.max(100,node.size[1]-heightOutsidePanel());
    lastWidth=node.size[0];
    lastRatio=previewRatio();
    lastExtra=controlsHeight();
  };
  const fitMedia=()=>{
    if(!media||app.configuringGraph)return;
    const width=node.size?.[0]||380;
    const extra=controlsHeight(),ratio=previewRatio();
    if(width===lastWidth&&ratio===lastRatio&&extra===lastExtra)return;
    lastWidth=width;lastRatio=ratio;lastExtra=extra;
    // Node-local width, no screen coordinates or zoom-dependent measurements.
    const previewHeight=Math.round(Math.max(120,Math.min(640,(width-56)*ratio)));
    height=previewHeight+58+extra;
    const nodeHeight=height+heightOutsidePanel();
    if(node.size&&Math.abs(node.size[1]-nodeHeight)>1)node.setSize([width,nodeHeight]);
    node.setDirtyCanvas?.(true,true);
  };
  // A panel belongs to one live node instance, not a reusable workflow node ID.
  // Vue must remount it when undo reconstructs a node with the same ID.
  const widget=node.addDOMWidget('ps_panel_'+crypto.randomUUID(),'custom',element,{
    serialize:false,hideOnZoom:false,getMinHeight:()=>height,getHeight:()=>height
  });
  // LiteGraph workflow/undo serialization checks the widget property, not options.
  widget.serialize=false;widget.computeSize=()=>[media?380:330,height];
  // DOM controls handle their own input; the decorative canvas widget must not
  // consume native node dragging when the pointer passes through its background.
  const hitWidget=node.getWidgetOnPos;
  if(media)node.getWidgetOnPos=function(...args){
    const hit=hitWidget?.apply(this,args);
    return hit===widget?undefined:hit;
  };
  // Multiple outputs are selectable without adding a configuration surface.
  const show=()=>{if(counter)counter.textContent=files.length>1?`${index+1} / ${files.length}`:'';if(files[index])state.showPreview(files[index]);};
  return {image,width:media?380:330,get height(){return height;},fitMedia,restoreMediaSize,dispose(){end();abort.abort();},setResults(urls){files=urls;index=0;
    if(!navigation&&urls.length>1){navigation=row();button(navigation,'Previous image',()=>{index=(index+files.length-1)%files.length;show();});button(navigation,'Next image',()=>{index=(index+1)%files.length;show();});}
    if(navigation)navigation.hidden=urls.length<2;show();},clear(){files=[];index=0;if(counter)counter.textContent='';if(navigation)navigation.hidden=true;}};
}
