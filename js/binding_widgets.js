import {kind,value} from './node_bindings.js';
import {t} from './i18n.js';
import {applyingWidgetValue,setWidgetValue} from './widget_values.js';

export const INTERNAL_FIELDS=new Set(['param_id','request_id','result_id','source_json']);
const roles={VP_Slider:[['strength','Strength']],VP_Prompt:[['prompt','Prompt'],['negativePrompt','Negative prompt'],['resolution','Resolution']]};

/** UI adapters leave the saved source_json and existing input order intact. */
export function sourcePatch(type,role,key='') {
  const custom=role==='parameter';
  if(!custom&&!roles[type]?.some(([name])=>name===role))throw Error(t('Invalid parameter source'));
  if(custom&&(!key.trim()||key.length>256||/[\u0000-\u001f]/u.test(key)))throw Error(t('Enter a valid custom parameter name'));
  const source={kind:custom?'parameter':'canonical',key:custom?key.trim():role};
  return {source_json:JSON.stringify(source),param_id:custom?`parameter-${crypto.randomUUID()}`:source.key};
}

export function installBindingWidgets(node,state) {
  const options=roles[kind(node)];if(!options)return;
  const choices=[...options.map(([key,label])=>({key,label:t(label)})),{key:'parameter',label:t('Custom parameter')}];
  const read=()=>{try{return JSON.parse(value(node,'source_json'));}catch{return null;}};
  const selected=()=>{const source=read();return source?.kind==='parameter'?'parameter':source?.key;};
  let refresh=()=>{};
  const apply=(role,key)=>{try{
    const patch=sourcePatch(kind(node),role,key);
    const previous=read(),next=JSON.parse(patch.source_json);
    if(previous?.kind!==next.kind||previous?.key!==next.key)state.change(patch,true);
  }catch(error){
    state.error=error.message;
    state.reportBindingError?.(error.message);
  }finally{refresh();}};
  const sourceWidget=node.addWidget('combo',t('Parameter source'),'',next=>{
    if(applyingWidgetValue(sourceWidget))return;
    const choice=choices.find(c=>c.label===next);if(choice)apply(choice.key,read()?.kind==='parameter'?read().key:'custom');
  },{values:choices.map(c=>c.label),serialize:false});
  const keyWidget=node.addWidget('text',t('Custom parameter name'),'',next=>{
    if(!applyingWidgetValue(keyWidget))apply('parameter',String(next));
  },{serialize:false});
  sourceWidget.advanced=true;
  refresh=()=>{
    const source=read(),custom=source?.kind==='parameter';
    setWidgetValue(sourceWidget,choices.find(c=>c.key===selected())?.label||t('Invalid parameter source'));
    setWidgetValue(keyWidget,custom?source.key:'');keyWidget.hidden=!custom;keyWidget.advanced=custom;
    if(keyWidget.element)keyWidget.element.hidden=!custom;
  };
  const oldRefresh=state.refresh;
  state.refresh=()=>{refresh();oldRefresh();};refresh();
}
