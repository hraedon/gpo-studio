import {state,getGpoGeneration} from './state.mjs';

function available(element){
  return element instanceof HTMLElement&&element.isConnected&&
    !element.closest('[hidden],[inert]')&&!element.disabled&&
    element.getAttribute('aria-disabled')!=="true"&&element.getClientRects().length>0;
}

// Remember the invoking action, rather than a row index: saving replaces row
// elements, and ordering or filters can change while a dialog is open.
export function captureDialogFocus(dialog){
  const opener=document.activeElement;
  return {opener,id:opener?.id,tag:opener?.tagName,
    attributes:[...(opener?.attributes||[])].filter(attribute=>attribute.name.startsWith('data-')).map(attribute=>[attribute.name,attribute.value]),
    panel:opener?.closest?.('[role="tabpanel"]')||null,
    guid:state.current?.guid??null,generation:getGpoGeneration(state.current?.guid),
    editorGeneration:state.editorGeneration,session:dialog.editorSession};
}

export function restoreDialogFocus(dialog,context){
  if(!context||dialog.editorSession!==context.session||
    state.editorGeneration!==context.editorGeneration||
    (state.current?.guid??null)!==context.guid||
    getGpoGeneration(context.guid)!==context.generation||
    document.querySelector('dialog[open]')||
    (context.panel&&!available(context.panel)))return false;
  // A click, a newer error, or another intentional focus move wins over a
  // scheduled return. The browser's native return to the opener is harmless.
  const active=document.activeElement;
  if(active!==context.opener&&active!==document.body&&active!==document.documentElement&&available(active))return false;
  const root=context.panel||document;
  let target=available(context.opener)?context.opener:null;
  if(!target&&context.id){
    const candidate=document.getElementById(context.id);
    if(candidate?.tagName===context.tag&&root.contains(candidate)&&available(candidate))target=candidate;
  }
  if(!target&&context.attributes.length){
    const candidates=[...root.querySelectorAll(context.tag)].filter(element=>
      available(element)&&context.attributes.every(([name,value])=>element.getAttribute(name)===value));
    // Never choose the first of multiple matching editing identities.
    if(candidates.length===1)target=candidates[0];
  }
  if(!target)return false;
  target.focus();return document.activeElement===target;
}
