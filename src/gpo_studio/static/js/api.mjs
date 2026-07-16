import {state,$} from './state.mjs';
export class ApiError extends Error{
  constructor(message,{status=0,payload=null}={}){super(message);this.name="ApiError";this.status=status;this.payload=payload;this.issues=payload?.error?.issues||[];this.code=payload?.error?.code||"";this.expectedRevision=payload?.error?.expected_revision??null;this.currentRevision=payload?.error?.current_revision??null}
}
function uiElement(selector){return typeof document==="undefined"?null:$(selector)}
export function setStatus(message){const el=uiElement("#app-status");if(!el)return;el.textContent=message;el.hidden=!message}
export function showPersistentError(message){const el=uiElement("#error-summary");if(!el)return;el.textContent=`Error: ${message}`;el.hidden=false;el.focus()}
export function clearPersistentError(){const el=uiElement("#error-summary");if(!el)return;el.textContent="";el.hidden=true}
export async function api(path,options={}){
  const {headers={},...requestOptions}=options;
  let response;
  try{response=await fetch(path,{...requestOptions,headers:{"Content-Type":"application/json",...headers}})}
  catch(error){const wrapped=new ApiError("The local GPO Studio server is unavailable. Your unsaved values are still in this form.");wrapped.cause=error;showPersistentError(wrapped.message);throw wrapped}
  const type=response.headers.get("content-type")||"";
  const payload=type.includes("json")?await response.json():await response.text();
  if(!response.ok)throw new ApiError(payload?.error?.message||payload?.detail||`Request failed (${response.status})`,{status:response.status,payload});
  clearPersistentError();
  return payload;
}
export function toast(message){const el=uiElement("#toast");if(!el)return;el.textContent=message;el.classList.add("show");setTimeout(()=>el.classList.remove("show"),2600)}
export function audit(reason){return{actor:"local-operator",reason,expected_revision:state.current.revision}}
