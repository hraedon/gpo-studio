import {state,$} from './state.mjs';
export async function api(path,options={}){
  const response=await fetch(path,{headers:{"Content-Type":"application/json",...(options.headers||{})},...options});
  const type=response.headers.get("content-type")||"";
  const payload=type.includes("json")?await response.json():await response.text();
  if(!response.ok){const err=new Error(payload?.error?.message||payload?.detail||`Request failed (${response.status})`);err.issues=payload?.error?.issues||[];throw err}
  return payload;
}
export function toast(message){const el=$("#toast");el.textContent=message;el.classList.add("show");setTimeout(()=>el.classList.remove("show"),2600)}
export function audit(reason){return{actor:"local-operator",reason,expected_revision:state.current.revision}}
