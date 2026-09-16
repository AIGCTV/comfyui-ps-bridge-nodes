import {t} from "./i18n.js";
import {value} from './node_bindings.js';
import {beginGesture} from './node_state.js';
import {colors} from './canvas_controls.js';

const editors=new WeakMap();
export function editorFor(app) {
  let editor=editors.get(app);
  if(!editor){editor=new InlineEditor(app);editors.set(app,editor);}
  return editor;
}
export function finishEditing(app) { return editors.get(app)?.finish(); }

export function installEditorStyle() {
  if(document.getElementById('ps-canvas-editor-style'))return;
  const style=document.createElement('style');style.id='ps-canvas-editor-style';
  style.textContent=`.ps-canvas-editor{position:fixed;box-sizing:border-box;margin:0;z-index:100;transform-origin:0 0;
    color:${colors.text};background:${colors.field};border:1px solid ${colors.accent};border-radius:7px;padding:6px 8px;
    font:12px/1.6 system-ui;word-break:break-all;outline:none;resize:none;box-shadow:0 0 0 1px ${colors.accent}44}
    .litegraph.litecontextmenu.ps-canvas-menu{position:fixed;box-sizing:border-box;padding:5px;margin:0;
      color:${colors.text};background:${colors.panel};border:1px solid ${colors.border};border-radius:9px;
      font:12px/1.6 system-ui;box-shadow:0 6px 18px #0006;overflow:auto;outline:none}
    .litegraph.litecontextmenu.ps-canvas-menu .litemenu-entry{position:relative;box-sizing:border-box;min-height:2.4em;padding:.4em 2em .4em .65em;
      margin:0;border:0;border-radius:5px;font:inherit;color:inherit;background:transparent;cursor:pointer;outline:none}
    .litegraph.litecontextmenu.ps-canvas-menu .litemenu-entry:hover,.litegraph.litecontextmenu.ps-canvas-menu .litemenu-entry:focus{background:${colors.field};color:${colors.text}}
    .litegraph.litecontextmenu.ps-canvas-menu .litemenu-entry.selected{color:${colors.accent};background:${colors.field}}
    .litegraph.litecontextmenu.ps-canvas-menu .litemenu-entry.selected::after{content:'✓';position:absolute;right:.65em}
    .litegraph.litecontextmenu.ps-canvas-menu .litemenu-entry.disabled{color:${colors.muted};opacity:.45;cursor:default;background:transparent}`;
  document.head.append(style);
}

/** The only editing overlay. Idle nodes have no DOM content. */
class InlineEditor {
  constructor(app) {this.app=app;/** @type {import('../types/editor.js').EditingSession|null} */ this.active=null;}
  open(node,control,change,report) {
    if(this.active&&this.active.node===node&&this.active.control.id===control.id){this.active.input.focus();return;}
    if(this.finish()===false)return;
    const multiline=control.kind==='text';
    const input=document.createElement(multiline?'textarea':'input');
    input.className='ps-canvas-editor';input.setAttribute('aria-label',control.label||control.field);
    if(input instanceof HTMLInputElement){input.type='text';input.inputMode='decimal';}
    input.spellcheck=false;
    const original=value(node,control.field);input.value=String(original??'');
    const abort=new AbortController(), end=beginGesture(node);
    const edit={node,control,input,change,report,original,lastCommitted:original,multiline,abort,end,
      graph:this.app.rootGraph||this.app.graph,owner:node.graph,frame:0};
    this.active=edit;document.body.append(input);
    const options={signal:abort.signal};
    input.addEventListener('compositionstart',()=>node.__psUI.composing=true,options);
    input.addEventListener('compositionend',()=>{
      node.__psUI.composing=false;
      if(multiline)change(control.field,input.value,false);
      document.dispatchEvent(new CustomEvent('ps-vplugins:compositionend'));
    },options);
    input.addEventListener('input',()=>{if(multiline&&!node.__psUI.composing)change(control.field,input.value,false);},options);
    input.addEventListener('scroll',()=>{node.__psUI.scroll=input.scrollTop;node.__psUI.refresh();},options);
    input.addEventListener('keydown',rawEvent=>{
      const event=/** @type {KeyboardEvent} */ (rawEvent);
      event.stopPropagation();
      if(event.isComposing||node.__psUI.composing)return;
      if(event.key==='Escape'){event.preventDefault();this.finish(true);}
      else if(event.key==='Enter'&&(!multiline||event.ctrlKey||event.metaKey)){event.preventDefault();this.finish();}
    },options);
    // Finish before the canvas receives pointerdown. Never eat confirmed IME text.
    document.addEventListener('pointerdown',event=>{
      if(event.target instanceof Node&&input.contains(event.target))return;
      if(node.__psUI.composing){event.preventDefault();event.stopImmediatePropagation();return;}
      this.finish();
    },{...options,capture:true});
    input.addEventListener('blur',()=>{if(!node.__psUI.composing)this.finish();},options);
    const position=()=>{
      if(this.active!==edit)return;
      if(node.graph!==edit.owner||(this.app.rootGraph||this.app.graph)!==edit.graph
        ||this.app.canvas.read_only||node.flags?.collapsed||node.__psUI?.disabled){this.close(edit);return;}
      const current=node.__psUI.layout?.controls.find(c=>c.id===control.id)||control;
      const rect=this.app.canvas.canvas.getBoundingClientRect(),ds=this.app.canvas.ds;
      Object.assign(input.style,{left:rect.left+(node.pos[0]+current.x+ds.offset[0])*ds.scale+'px',
        top:rect.top+(node.pos[1]+current.y+ds.offset[1])*ds.scale+'px',width:current.w+'px',height:current.h+'px',
        transform:`scale(${ds.scale})`});
      edit.frame=requestAnimationFrame(position);
    };
    position();input.focus();
    if(multiline)input.scrollTop=node.__psUI.scroll||0;else input.select();
  }
  finish(cancel=false) {
    const edit=this.active;if(!edit)return true;
    if(edit.node.__psUI.composing)return false;
    try {
      if(!cancel&&!edit.multiline){
        if(!edit.input.value.trim()||!Number.isFinite(Number(edit.input.value)))throw new Error(t("Enter a valid number"));
        edit.change(edit.control.field,Number(edit.input.value),false);
      }
    } catch(error){edit.report(error.message);}
    this.close(edit);return true;
  }
  sync(node) {
    const edit=this.active;if(!edit||edit.node!==node||node.__psUI.composing)return;
    const committed=value(node,edit.control.field);
    // Numeric drafts are not committed yet. Preserve them while remote snapshots arrive.
    if(edit.multiline||edit.input.value===String(edit.lastCommitted)){
      if(edit.input.value!==String(committed)){
        const start=edit.input.selectionStart,end=edit.input.selectionEnd,scroll=edit.input.scrollTop;
        edit.input.value=String(committed??'');
        edit.input.setSelectionRange(start,end);edit.input.scrollTop=scroll;
      }
    }
    edit.lastCommitted=committed;
  }
  close(edit=this.active) {
    if(!edit)return;
    this.active=null;edit.abort.abort();cancelAnimationFrame(edit.frame);
    if(edit.node.__psUI){edit.node.__psUI.composing=false;edit.node.__psUI.scroll=edit.input.scrollTop;}
    edit.input.remove();edit.end();edit.node.__psUI?.refresh();
  }
  cancelNode(node) {if(this.active?.node===node)this.close();}
}
