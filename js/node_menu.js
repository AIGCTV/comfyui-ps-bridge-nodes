import {nodeName} from './i18n.js';
export const NODE_MENU_ORDER=["VP_Image","VP_Prompt","VP_Slider","VP_Batch","VP_Seed","VP_SendToPS"];

export function orderNodeMenu(items){
  if(!Array.isArray(items)||!items.length||!items.every(item=>item?.has_submenu===false&&NODE_MENU_ORDER.includes(item.value)))return items;
  return [...items].sort((a,b)=>NODE_MENU_ORDER.indexOf(a.value)-NODE_MENU_ORDER.indexOf(b.value))
    .map(item=>({...item,...(item.content?{content:nodeName(item.value,item.content)}:{})}));
}

export function installNodeMenuOrder(litegraph){
  // Legacy Add Node alphabetizes its entries after reading the node registry.
  // Chain the current constructor (including other extensions) and change only
  // a submenu consisting entirely of our node IDs. Keep options/prototype/statics.
  litegraph.ContextMenu=new Proxy(litegraph.ContextMenu,{
    construct(target,[items,...args],newTarget){
      return Reflect.construct(target,[orderNodeMenu(items),...args],newTarget);
    }
  });
}
