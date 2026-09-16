import {t} from "./i18n.js";
/** Shared canvas geometry. No graph mutations, DOM, networking or serialization. */
import {value} from './node_bindings.js';

/** @typedef {{id:string, kind:string, label?:string, field?:string, text?:string, selected?:boolean,
 * x:number,y:number,w:number,h:number}} Control */
export const NODE_WIDTH = 300;
export const layouts = {
  VP_Image: {height: 300, grow: true, underSlots: true},
  VP_Prompt: {height: 160, grow: true},
  VP_Seed: {height: 140},
  VP_Slider: {height: 158},
  VP_Batch: {height: 100},
  VP_SendToPS: {height: 0},
};

/** Node definitions describe controls; the renderer and hit testing know no node types. */
export const definitions = {
  VP_Seed: [[{id:'seed',kind:'number',field:'value',label:'Seed'}],
    [{id:'fixed',kind:'button',text:'Fixed'},{id:'random',kind:'button',text:'Random'},
      {id:'once',kind:'button',text:'New seed'}]],
  VP_Slider: [[{id:'slider',kind:'slider',field:'value',label:'Slider',weight:2},
    {id:'value',kind:'number',field:'value',label:'Value'}],
    ['min','max','step'].map(field=>({id:field,kind:'number',field,label:field.toUpperCase(),caption:true}))],
  VP_Batch: [[1,2,3,4].map(n=>({id:'batch'+n,kind:'button',text:String(n),number:n}))],
  VP_Prompt: [[{id:'text',kind:'text',field:'text',label:'Prompt',grow:true}]],
  VP_Image: [[{id:'slot',kind:'combo',field:'slot',label:'Image Name',weight:8},
    {id:'upload',kind:'button',text:'＋',label:'Choose a local image',weight:1}],
    [{id:'preview',kind:'preview',grow:true}]],
};

export function controlLayout(type, width, y, height, error = '') {
  const rows = definitions[type] || [];
  const panel = {x:10,y:y+10,w:Math.max(0,width-20),h:Math.max(0,height-20)};
  const contentWidth = Math.max(0, panel.w - 20), gap = 8, rowGap = 9;
  const fixed = rows.reduce((sum,row)=>sum+(row.some(c=>c.grow)?0:row.some(c=>c.caption)?48:32),0);
  const grow = Math.max(32,panel.h-20-fixed-Math.max(0,rows.length-1)*rowGap-(error?30:0));
  let top=panel.y+10;
  const controls=[];
  for(const row of rows) {
    const height=row.some(c=>c.grow)?grow:row.some(c=>c.caption)?48:32;
    const units=row.reduce((n,c)=>n+(c.weight||1),0);
    let x=panel.x+10;
    for(const c of row) {
      const w=(contentWidth-gap*(row.length-1))*(c.weight||1)/units;
      controls.push({...c,...(c.label?{label:t(c.label)}:{}),...(c.text?{text:t(c.text)}:{}),x,y:top+(c.caption?16:0),w,h:height-(c.caption?16:0)});
      x+=w+gap;
    }
    top+=height+rowGap;
  }
  const preview=controls.find(c=>c.kind==='preview');
  // Native outputs share this geometry and sit directly over the preview.
  const outputPositions=preview?Array.from({length:5},(_,i)=>[preview.x+preview.w-8,preview.y+12+i*20]):[];
  return {panel,controls,outputPositions,error: error ? {x:panel.x+10,y:Math.min(top,panel.y+panel.h-25),w:contentWidth,h:24} : null};
}

export function hitControl(layout, x, y) {
  return layout.controls.find(c=>c.kind!=='preview' && x>=c.x&&x<=c.x+c.w&&y>=c.y&&y<=c.y+c.h);
}
export const colors={panel:'#19222d',field:'#243343',border:'#405268',text:'#e0eaf4',muted:'#9baec2',accent:'#65c9e7'};
const checkerboards=new WeakMap();
function checkerboard(ctx) {
  let pattern=checkerboards.get(ctx);
  if(!pattern){
    const tile=new OffscreenCanvas(16,16),paint=tile.getContext('2d');
    if(!paint)throw new Error('Canvas 2D context is unavailable');
    paint.fillStyle='#1c2834';paint.fillRect(0,0,16,16);
    paint.fillStyle='#263443';paint.fillRect(8,0,8,8);paint.fillRect(0,8,8,8);
    pattern=ctx.createPattern(tile,'repeat');checkerboards.set(ctx,pattern);
  }
  return pattern;
}
/** @param {CanvasRenderingContext2D} ctx @param {{x:number,y:number,w:number,h:number}} r
 * @param {string} fill @param {string|null} stroke */
function round(ctx,r,fill,stroke=colors.border,radius=7) {
  ctx.beginPath();ctx.roundRect(r.x,r.y,r.w,r.h,radius);
  ctx.fillStyle=fill;ctx.fill();if(stroke){ctx.strokeStyle=stroke;ctx.stroke();}
}
function clipped(ctx,r,draw) {ctx.save();ctx.beginPath();ctx.rect(r.x,r.y,r.w,r.h);ctx.clip();draw();ctx.restore();}
function text(ctx,str,r,center=false,color=colors.text) {
  ctx.fillStyle=color;ctx.textAlign=center?'center':'left';ctx.textBaseline='middle';
  clipped(ctx,{x:r.x+5,y:r.y,w:r.w-10,h:r.h},()=>ctx.fillText(String(str),center?r.x+r.w/2:r.x+8,r.y+r.h/2));
}
/** Wrap only when text/width changes; retain blank lines and Unicode code points. */
export function wrapText(ctx, str, width) {
  const lines=[];
  for(const paragraph of String(str).split('\n')) {
    let line='';
    for(const ch of paragraph) {
      if(line&&ctx.measureText(line+ch).width>width){lines.push(line);line='';}
      line+=ch;
    }
    lines.push(line);
  }
  return lines;
}
export function drawControls(ctx,node,layout,state) {
  const error=state.graphError||state.error||'';
  ctx.save();ctx.font='12px system-ui';ctx.lineWidth=1;
  round(ctx,layout.panel,colors.panel,'#34465b',12);
  clipped(ctx,layout.panel,()=>{
    for(const c of layout.controls) {
      const active=state.hover===c.id||state.pressed===c.id;
      if(c.caption){ctx.fillStyle=colors.muted;ctx.textAlign='left';ctx.fillText(c.label,c.x,c.y-6);}
      if(c.kind==='preview') {
        ctx.save();ctx.beginPath();ctx.roundRect(c.x,c.y,c.w,c.h,8);ctx.clip();
        ctx.fillStyle=checkerboard(ctx);ctx.fillRect(c.x,c.y,c.w,c.h);
        const img=state.image;
        if(img?.naturalWidth){const scale=Math.min(c.w/img.naturalWidth,c.h/img.naturalHeight);
          const w=img.naturalWidth*scale,h=img.naturalHeight*scale;ctx.drawImage(img,c.x+(c.w-w)/2,c.y+(c.h-h)/2,w,h);}
        ctx.restore();continue;
      }
      if(c.kind==='slider') {
        const v=value(node,'value'),min=value(node,'min'),max=value(node,'max');
        const ratio=Math.max(0,Math.min(1,(v-min)/(max-min)||0));
        round(ctx,{x:c.x+6,y:c.y+12,w:c.w-12,h:8},'#405268',null,4);
        round(ctx,{x:c.x+6,y:c.y+12,w:Math.max(1,(c.w-12)*ratio),h:8},colors.accent,null,4);
        ctx.beginPath();ctx.arc(c.x+6+(c.w-12)*ratio,c.y+16,7,0,Math.PI*2);ctx.fillStyle=colors.accent;ctx.fill();continue;
      }
      const selected=((c.id==='fixed'||c.id==='random')&&c.id===value(node,'mode'))
        ||(typeof c.number==='number'&&c.number===value(node,'value'));
      round(ctx,c,selected||state.pressed===c.id?'#235069':colors.field,active||selected?'#63c5e5':colors.border);
      if(c.kind==='text') {
        const str=value(node,c.field)||'',key=(str||t('Enter a prompt…'))+'\0'+c.w;
        if(state.textCache?.key!==key)state.textCache={key,lines:wrapText(ctx,str||t("Enter a prompt…"),c.w-16)};
        const max=Math.max(0,state.textCache.lines.length*19.2-c.h+16);
        state.scroll=Math.min(state.scroll||0,max);
        clipped(ctx,{x:c.x+8,y:c.y+6,w:c.w-16,h:c.h-12},()=>{
          ctx.fillStyle=str?colors.text:colors.muted;ctx.textAlign='left';ctx.textBaseline='top';
          state.textCache.lines.forEach((line,i)=>{const y=c.y+8+i*19.2-state.scroll;if(y>c.y-20&&y<c.y+c.h)ctx.fillText(line,c.x+8,y);});
        });
        if(max){ctx.fillStyle=colors.muted;ctx.fillRect(c.x+c.w-4,c.y+4+(c.h-28)*state.scroll/max,2,20);}
      } else {
        text(ctx,c.kind==='combo'?state.slotLabel():c.field?value(node,c.field):c.text,c,c.kind==='button');
        if(c.kind==='combo'){ctx.strokeStyle=colors.text;ctx.beginPath();ctx.moveTo(c.x+c.w-19,c.y+14);ctx.lineTo(c.x+c.w-14,c.y+19);ctx.lineTo(c.x+c.w-9,c.y+14);ctx.stroke();}
      }
    }
    if(layout.error)text(ctx,error,layout.error,false,'#ffb3a7');
  });
  ctx.restore();
}
