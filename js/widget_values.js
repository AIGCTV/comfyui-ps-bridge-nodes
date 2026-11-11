// DOMWidget value setters can synchronously invoke callbacks, unlike Canvas widgets.
// State application must update the host widget without becoming another user edit.
const applying=new WeakSet();
export const applyingWidgetValue=widget=>applying.has(widget);
export function setWidgetValue(widget,value) {
  if(Object.is(widget.value,value))return;
  const nested=applying.has(widget);applying.add(widget);
  try{widget.value=value;}finally{if(!nested)applying.delete(widget);}
}
