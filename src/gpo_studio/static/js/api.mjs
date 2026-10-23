import {state,$} from './state.mjs';
export class ApiError extends Error{
  constructor(message,{status=0,payload=null}={}){super(message);this.name="ApiError";this.status=status;this.payload=payload;this.issues=payload?.error?.issues||[];this.code=payload?.error?.code||"";this.expectedRevision=payload?.error?.expected_revision??null;this.currentRevision=payload?.error?.current_revision??null}
}
function uiElement(selector){return typeof document==="undefined"?null:$(selector)}
let persistentErrorVersion=0,errorReportVersion=0;
export function setStatus(message){const el=uiElement("#app-status");if(!el)return;el.textContent=message;el.hidden=!message}
export function showPersistentError(message){persistentErrorVersion++;errorReportVersion++;const el=uiElement("#error-summary");if(!el)return;(uiElement("#error-message")||el).textContent=`Error: ${message}`;el.hidden=false;el.focus()}
function clearError(retireReports){persistentErrorVersion++;if(retireReports)errorReportVersion++;const el=uiElement("#error-summary");if(!el)return;(uiElement("#error-message")||el).textContent="";el.hidden=true}
export function clearPersistentError(){clearError(true)}
// A read may report only for its owner and the error context it started in.
export function captureErrorReporter(isCurrent){
  const version=errorReportVersion;
  return message=>{
    if(version!==errorReportVersion||!isCurrent())return false;
    showPersistentError(message);return true;
  };
}
export async function api(path,options={}){
  const {headers={},...requestOptions}=options;
  const errorVersion=persistentErrorVersion,method=(requestOptions.method||"GET").toUpperCase(),hadError=uiElement("#error-summary")?.hidden===false;
  let response;
  try{response=await fetch(path,{...requestOptions,headers:{"Content-Type":"application/json",...headers}})}
  // Like HTTP failures, transport failures reach the caller before any UI report.
  // The caller decides whether its view/editor still owns the outcome.
  catch(error){const wrapped=new ApiError("The local GPO Studio server is unavailable. Your unsaved values are still in this form.");wrapped.cause=error;throw wrapped}
  const type=response.headers.get("content-type")||"";
  const payload=type.includes("json")?await response.json():await response.text();
  if(!response.ok)throw new ApiError(payload?.error?.message||payload?.detail||`Request failed (${response.status})`,{status:response.status,payload});
  // Reads can be automatic or finish after an unrelated operation failed. Only
  // a successful action may clear the error it started with; newer errors stay.
  // Resolving an earlier warning does not dismiss a still-current read's outcome.
  if(method!=="GET"&&method!=="HEAD"&&hadError&&errorVersion===persistentErrorVersion)clearError(false);
  return payload;
}
export function toast(message){const el=uiElement("#toast");if(!el)return;el.textContent=message;el.classList.add("show");setTimeout(()=>el.classList.remove("show"),2600)}
export function audit(reason){return{actor:"local-operator",reason,expected_revision:state.current.revision}}
