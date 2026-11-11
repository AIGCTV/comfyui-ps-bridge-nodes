import {kind, value, graphNodes} from './node_bindings.js';

export const sharedFields = {
  VP_Seed:['value','mode'], VP_Slider:['value','min','max','step'],
  VP_Prompt:['text'], VP_Batch:['value'],
  VP_Image:['required','file_name','selection_file','request_id'],
};
export function groupKey(node) {
  const type=kind(node);
  if(!sharedFields[type])return null;
  if(type==='VP_Image')return `${type}:${value(node,'slot')}`;
  if(type==='VP_Slider'||type==='VP_Prompt') {
    try {const source=JSON.parse(value(node,'source_json'));return `${type}:${source.kind}:${source.key}`;}
    catch {return null;}
  }
  return type;
}
export function groupMembers(node) {
  const key=groupKey(node);
  return key?graphNodes(node.graph?.rootGraph||node.graph).filter(n=>groupKey(n)===key):[];
}
export function sharedValues(node) {
  return Object.fromEntries((sharedFields[kind(node)]||[]).map(name=>[name,value(node,name)]));
}
