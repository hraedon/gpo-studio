import {state,$} from './state.mjs';
import {api,toast,audit} from './api.mjs';
import {loadList,renderAll,renderList,loadHistory} from './render.mjs';

export function openGpo(edit=false){
  const form=$("#gpo-form");form.reset();clearFormErrors(form);form.dataset.edit=edit?"true":"false";$("#metadata-options").hidden=!edit;
  $("#gpo-dialog-title").textContent=edit?"Edit policy details":"New GPO";$("#gpo-submit").textContent=edit?"Save changes":"Create policy";
  if(edit){const g=state.current;form.name.value=g.name;form.description.value=g.description;form.computer_enabled.checked=g.computer_enabled;form.user_enabled.checked=g.user_enabled;form.status.value=g.status;form.domain.value=g.domain||"studio.local";form.reason.value="Update policy metadata"}else form.reason.value="Create draft";
  $("#gpo-dialog").showModal();
}
export function openSetting(setting=null){
  const form=$("#setting-form");form.reset();clearFormErrors(form);state.editingSetting=setting;$("#setting-dialog-title").textContent=setting?"Edit policy setting":"Add policy setting";
  if(setting){for(const key of ["side","action","key","value_name","registry_type","comment"])form[key].value=setting[key];form.value.value=Array.isArray(setting.value)?setting.value.join("\n"):setting.value;form.reason.value="Update registry policy"}
  syncSettingForm();$("#setting-dialog").showModal();
}
export function syncSettingForm(){
  const f=$("#setting-form"),type=f.registry_type.value,side=f.side.value;$("#hive-prefix").textContent=side==="computer"?"HKLM\\":"HKCU\\";
  $("#value-help").textContent=type==="REG_MULTI_SZ"?"Enter one item per line.":type==="REG_BINARY"?"Enter hexadecimal bytes, such as 01 FF A0.":type==="REG_DWORD"||type==="REG_QWORD"?"Enter a non-negative decimal integer.":"Enter text.";
  f.value.disabled=f.action.value==="delete";
}
export function openLink(link=null){
  const form=$("#link-form");form.reset();clearFormErrors(form);state.editingLink=link;$("#link-dialog-title").textContent=link?"Edit link":"Add link";
  if(link){form.target.value=link.target;form.order.value=link.order;form.enabled.checked=link.enabled;form.enforced.checked=link.enforced;form.reason.value="Update link intent"}$("#link-dialog").showModal();
}
export function openFilter(filter=null){
  const form=$("#filter-form");form.reset();clearFormErrors(form);state.editingFilter=filter;$("#filter-dialog-title").textContent=filter?"Edit security filter":"Add security filter";
  if(filter){form.principal.value=filter.principal;form.permission.value=filter.permission;form.target_type.value=filter.target_type||"group";form.inheritable.checked=filter.inheritable;form.reason.value="Update security filter"}$("#filter-dialog").showModal();
}
export function openWmi(){
  const form=$("#wmi-form");form.reset();clearFormErrors(form);
  const w=state.current.wmi_filter;
  if(w){form.name.value=w.name;form.description.value=w.description;form.language.value=w.language;form.query.value=w.query;form.reason.value="Update WMI filter"}
  $("#wmi-dialog").showModal();
}
export function openEstate(){
  const form=$("#estate-form");form.reset();clearFormErrors(form);
  $("#estate-dialog").showModal();
}
export function openFork(){
  const form=$("#fork-form");form.reset();clearFormErrors(form);
  if(state.current)form.name.value=`${state.current.name} (fork)`;
  $("#fork-dialog").showModal();
}
function clearFormErrors(form){form.querySelectorAll(".form-error").forEach(el=>el.remove())}
function showFormErrors(form,error){clearFormErrors(form);if(error.issues&&error.issues.length){const ref=form.firstChild;error.issues.forEach(i=>{const div=document.createElement("div");div.className="form-error";div.textContent=i.message||i.msg||JSON.stringify(i);form.insertBefore(div,ref)})}else{toast(error.message)}}
export function initForms(){
$("#gpo-form").onsubmit=async event=>{event.preventDefault();const f=event.currentTarget;try{let data;if(f.dataset.edit==="true"){data=await api(`/api/gpos/${state.current.guid}`,{method:"PATCH",body:JSON.stringify({...audit(f.reason.value),name:f.name.value,description:f.description.value,computer_enabled:f.computer_enabled.checked,user_enabled:f.user_enabled.checked,status:f.status.value,domain:f.domain.value})})}else{data=await api("/api/gpos",{method:"POST",body:JSON.stringify({name:f.name.value,description:f.description.value,actor:"local-operator",reason:f.reason.value})})}$("#gpo-dialog").close();await loadList(data.gpo.guid);toast(f.dataset.edit==="true"?"Policy details saved":"Draft policy created")}catch(error){showFormErrors(f,error)}};
$("#setting-form").onsubmit=async event=>{event.preventDefault();const f=event.currentTarget,type=f.registry_type.value;let value=f.value.value;if(type==="REG_DWORD"||type==="REG_QWORD")value=Number(value);else if(type==="REG_MULTI_SZ")value=value.split(/\r?\n/).filter(Boolean);const setting={side:f.side.value,hive:f.side.value==="computer"?"HKLM":"HKCU",key:f.key.value.replace(/^\\+|\\+$/g,""),value_name:f.value_name.value,registry_type:type,value,action:f.action.value,comment:f.comment.value};const path=state.editingSetting?`/api/gpos/${state.current.guid}/settings/${state.editingSetting.id}`:`/api/gpos/${state.current.guid}/settings`;try{const data=await api(path,{method:state.editingSetting?"PUT":"POST",body:JSON.stringify({...audit(f.reason.value),setting})});$("#setting-dialog").close();state.current=data.gpo;state.validation=data.validation;state.semanticHash=data.semantic_sha256||"";renderAll();renderList();toast("Registry policy saved")}catch(error){showFormErrors(f,error)}};
$("#link-form").onsubmit=async event=>{event.preventDefault();const f=event.currentTarget,link={target:f.target.value,order:Number(f.order.value),enabled:f.enabled.checked,enforced:f.enforced.checked};const path=state.editingLink?`/api/gpos/${state.current.guid}/links/${state.editingLink.id}`:`/api/gpos/${state.current.guid}/links`;try{const data=await api(path,{method:state.editingLink?"PUT":"POST",body:JSON.stringify({...audit(f.reason.value),link})});$("#link-dialog").close();state.current=data.gpo;state.validation=data.validation;state.semanticHash=data.semantic_sha256||"";renderAll();renderList();toast("Link intent saved")}catch(error){showFormErrors(f,error)}};
$("#filter-form").onsubmit=async event=>{event.preventDefault();const f=event.currentTarget,filter={principal:f.principal.value,permission:f.permission.value,target_type:f.target_type.value,inheritable:f.inheritable.checked};const path=state.editingFilter?`/api/gpos/${state.current.guid}/security-filters/${state.editingFilter.id}`:`/api/gpos/${state.current.guid}/security-filters`;try{const data=await api(path,{method:state.editingFilter?"PUT":"POST",body:JSON.stringify({...audit(f.reason.value),filter})});$("#filter-dialog").close();state.current=data.gpo;state.validation=data.validation;state.semanticHash=data.semantic_sha256||"";renderAll();renderList();toast("Security filter saved")}catch(error){showFormErrors(f,error)}};
$("#wmi-form").onsubmit=async event=>{event.preventDefault();const f=event.currentTarget,wmi_filter={name:f.name.value,description:f.description.value,language:f.language.value,query:f.query.value};try{const data=await api(`/api/gpos/${state.current.guid}/wmi-filter`,{method:"PUT",body:JSON.stringify({...audit(f.reason.value),wmi_filter})});$("#wmi-dialog").close();state.current=data.gpo;state.validation=data.validation;state.semanticHash=data.semantic_sha256||"";renderAll();renderList();toast("WMI filter saved")}catch(error){showFormErrors(f,error)}};
$("#estate-form").onsubmit=async event=>{event.preventDefault();const f=event.currentTarget;try{const body=JSON.parse(f.json.value);const data=await api("/api/estate/import",{method:"POST",body:JSON.stringify(body)});$("#estate-dialog").close();await loadList();toast(`Imported ${data.imported} GPO(s), skipped ${data.skipped}`)}catch(error){if(error instanceof SyntaxError){showFormErrors(f,{issues:[{message:"Invalid JSON: "+error.message}]})}else{showFormErrors(f,error)}}};
$("#fork-form").onsubmit=async event=>{event.preventDefault();const f=event.currentTarget;try{const data=await api(`/api/gpos/${state.current.guid}/fork`,{method:"POST",body:JSON.stringify({name:f.name.value,actor:"local-operator",reason:f.reason.value})});$("#fork-dialog").close();await loadList(data.gpo.guid);toast("Forked to draft")}catch(error){showFormErrors(f,error)}};
}
export async function deleteSetting(id){if(!confirm("Remove this setting from the draft?"))return;try{const data=await api(`/api/gpos/${state.current.guid}/settings/${id}`,{method:"DELETE",body:JSON.stringify({...audit("Remove registry policy")})});state.current=data.gpo;state.validation=data.validation;state.semanticHash=data.semantic_sha256||"";renderAll();renderList();toast("Setting removed")}catch(error){toast(error.message)}}
export async function deleteLink(id){if(!confirm("Remove this link from the draft?"))return;try{const data=await api(`/api/gpos/${state.current.guid}/links/${id}`,{method:"DELETE",body:JSON.stringify({...audit("Remove link intent")})});state.current=data.gpo;state.validation=data.validation;state.semanticHash=data.semantic_sha256||"";renderAll();renderList();toast("Link removed")}catch(error){toast(error.message)}}
export async function deleteFilter(id){if(!confirm("Remove this security filter?"))return;try{const data=await api(`/api/gpos/${state.current.guid}/security-filters/${id}`,{method:"DELETE",body:JSON.stringify({...audit("Remove security filter")})});state.current=data.gpo;state.validation=data.validation;state.semanticHash=data.semantic_sha256||"";renderAll();renderList();toast("Security filter removed")}catch(error){toast(error.message)}}
export async function restoreRevision(revision){if(!confirm(`Restore revision ${revision} as a new revision?`))return;try{const data=await api(`/api/gpos/${state.current.guid}/revisions/${revision}/restore`,{method:"POST",body:JSON.stringify({...audit(`Restore revision ${revision}`)})});state.current=data.gpo;state.validation=data.validation;state.semanticHash=data.semantic_sha256||"";renderAll();renderList();await loadHistory();toast(`Revision ${revision} restored`)}catch(error){toast(error.message)}}
