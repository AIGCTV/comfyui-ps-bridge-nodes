/** A transient, field-anchored host menu. It owns no node data or drawing layer. */
export function openCanvasMenu(app,node,control,items,event) {
  const menu=new globalThis.LiteGraph.ContextMenu(items,{event,className:'ps-canvas-menu'});
  const element=/** @type {HTMLDivElement} */ (menu.root),ds=app.canvas.ds,canvas=app.canvas.canvas,rect=canvas.getBoundingClientRect();
  const left=rect.left+(node.pos[0]+control.x+ds.offset[0])*ds.scale;
  const top=rect.top+(node.pos[1]+control.y+ds.offset[1])*ds.scale;
  const bottom=top+control.h*ds.scale;
  Object.assign(element.style,{width:Math.min(innerWidth-16,Math.max(120,control.w*ds.scale))+'px',
    fontSize:Math.max(12,12*ds.scale)+'px',maxHeight:innerHeight-16+'px',transform:'none'});
  const bounds=element.getBoundingClientRect();
  Object.assign(element.style,{left:Math.max(8,Math.min(left,innerWidth-bounds.width-8))+'px',
    top:Math.max(8,Math.min(bottom+bounds.height+4>innerHeight-8?top-bounds.height-4:bottom+4,innerHeight-bounds.height-8))+'px'});
  element.setAttribute('role','listbox');element.setAttribute('aria-label',control.label);
  const entries=Array.from(element.querySelectorAll('div')).filter(entry=>entry.classList.contains('litemenu-entry'));
  entries.forEach((entry,i)=>{entry.setAttribute('role','option');entry.setAttribute('aria-label',items[i].content);
    entry.setAttribute('aria-selected',String(!!items[i].className));entry.tabIndex=-1;});
  const enabled=entries.filter(entry=>entry.getAttribute('aria-disabled')!=='true');
  let selected=Math.max(0,enabled.findIndex(entry=>entry.getAttribute('aria-selected')==='true'));
  const close=()=>{menu.close();canvas.focus();};
  const options={signal:menu.controller.signal};
  element.addEventListener('keydown',e=>{
    e.stopPropagation();
    if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close();}
    else if(e.key==='ArrowDown'||e.key==='ArrowUp'){
      e.preventDefault();selected=(selected+(e.key==='ArrowDown'?1:-1)+enabled.length)%enabled.length;enabled[selected]?.focus();
    }else if(e.key==='Enter'||e.key===' '){e.preventDefault();enabled[selected]?.click();}
  },options);
  // Scrolling/zooming the graph closes the popup instead of leaving a stale anchor.
  document.addEventListener('wheel',e=>{if(e.target instanceof Node&&!element.contains(e.target))close();},{...options,capture:true});
  window.addEventListener('resize',close,options);
  (enabled[selected]||enabled[0])?.focus();
  return ()=>menu.close();
}
