import {state,$,$$,escapeHtml,applyPayload} from './state.mjs';
import {api,toast,audit,showPersistentError} from './api.mjs';
import {clearFormErrors,handleFormFailure,showFormErrors} from './errors.mjs';
import {loadList,renderAll,renderList,loadHistory} from './render.mjs';
import {captureMutation,resetEditor,beginMutation,isCurrentEditor,isSameEditor,updateMutationList,completeMutation,mutationFailed,endMutation} from './mutation-context.mjs';

export function openGpo(edit=false){
  const form=$("#gpo-form");resetEditor(form);form.reset();clearFormErrors(form);form.dataset.edit=edit?"true":"false";$("#metadata-options").hidden=!edit;
  $("#gpo-dialog-title").textContent=edit?"Edit policy details":"New GPO";$("#gpo-submit").textContent=edit?"Save changes":"Create policy";
  if(edit){const g=state.current;form.name.value=g.name;form.description.value=g.description;form.computer_enabled.checked=g.computer_enabled;form.user_enabled.checked=g.user_enabled;form.status.value=g.status;form.domain.value=g.domain||"studio.local";form.reason.value="Update policy metadata"}else form.reason.value="Create draft";
  $("#gpo-dialog").showModal();
}
export function openSetting(setting=null){
  const form=$("#setting-form");resetEditor(form);form.reset();clearFormErrors(form);state.editingSetting=setting;$("#setting-dialog-title").textContent=setting?"Edit policy setting":"Add policy setting";
  if(setting){for(const key of ["side","action","key","value_name","registry_type","comment"])form[key].value=setting[key];form.value.value=Array.isArray(setting.value)?JSON.stringify(setting.value,null,2):setting.value;form.reason.value="Update registry policy"}
  syncSettingForm();$("#setting-dialog").showModal();
}
export function syncSettingForm(){
  const f=$("#setting-form"),type=f.registry_type.value,side=f.side.value;$("#hive-prefix").textContent=side==="computer"?"HKLM\\":"HKCU\\";
  $("#value-help").textContent=type==="REG_MULTI_SZ"?'Enter a JSON array of strings, e.g. ["first", "second"]. Exact strings are kept in the workspace; [" "] keeps its space, but [""] exports to Registry.pol as [].':type==="REG_BINARY"?"Enter hexadecimal bytes, such as 01 FF A0.":type==="REG_DWORD"||type==="REG_QWORD"?"Enter a non-negative decimal integer.":"Enter text.";
  if(type==="REG_MULTI_SZ"&&!f.value.value)f.value.value="[]";
  f.value.disabled=f.action.value==="delete";
}
export function openLink(link=null){
  const form=$("#link-form");resetEditor(form);form.reset();clearFormErrors(form);state.editingLink=link;$("#link-dialog-title").textContent=link?"Edit link":"Add link";
  if(link){form.target.value=link.target;form.order.value=link.order;form.enabled.checked=link.enabled;form.enforced.checked=link.enforced;form.reason.value="Update link intent"}$("#link-dialog").showModal();
}
export function openFilter(filter=null){
  const form=$("#filter-form");resetEditor(form);form.reset();clearFormErrors(form);state.editingFilter=filter;$("#filter-dialog-title").textContent=filter?"Edit security filter":"Add security filter";
  if(filter){form.principal.value=filter.principal;form.permission.value=filter.permission;form.target_type.value=filter.target_type||"group";form.inheritable.checked=filter.inheritable;form.reason.value="Update security filter"}$("#filter-dialog").showModal();
}
export function openWmi(){
  const form=$("#wmi-form");resetEditor(form);form.reset();clearFormErrors(form);
  const w=state.current.wmi_filter;
  if(w){form.name.value=w.name;form.description.value=w.description;form.language.value=w.language;form.query.value=w.query;form.reason.value="Update WMI filter"}
  $("#wmi-dialog").showModal();
}
export async function openWmiCatalogue(){
  const list=$("#wmi-catalogue-list");list.innerHTML='<div class="table-empty">Loading…</div>';
  $("#wmi-catalogue-dialog").showModal();
  try{const data=await api("/api/wmi-filters");
    if(!data.count){list.innerHTML='<div class="table-empty">The WMI filter catalogue is empty or not configured. Set GPO_STUDIO_WMI_CATALOGUE to use it.</div>';return}
    list.innerHTML=data.items.map(f=>`<div class="wmi-catalogue-entry"><div><strong>${escapeHtml(f.name)}</strong>${f.description?` <small>${escapeHtml(f.description)}</small>`:''}<code class="mono">${escapeHtml(f.query)}</code></div><button type="button" data-wmi-filter-id="${escapeHtml(f.id)}">Use this</button></div>`).join("");
    $$("[data-wmi-filter-id]").forEach(btn=>btn.onclick=()=>{
      const entry=data.items.find(f=>f.id===btn.dataset.wmiFilterId);if(!entry)return;
      $("#wmi-catalogue-dialog").close();
      const form=$("#wmi-form");resetEditor(form);form.reset();clearFormErrors(form);
      form.name.value=entry.name;form.description.value=entry.description||"";form.language.value=entry.language||"WQL";form.query.value=entry.query||"";
      form.reason.value=`Apply WMI filter '${entry.name}' from catalogue`;
      $("#wmi-dialog").showModal();
    });
  }catch(error){list.innerHTML=`<div class="table-empty">${escapeHtml(error.message)}</div>`}
}
let estateParsedJson=null,estateMode='upload',estateFileSelection=0;
function setEstateMode(mode){estateMode=mode;$$('.estate-toggle .chip').forEach(c=>c.classList.toggle('active',c.dataset.mode===mode));$('#estate-upload-section').hidden=mode!=='upload';$('#estate-paste-section').hidden=mode!=='paste'}
export function openEstate(){
  const form=$("#estate-form");resetEditor(form);form.reset();clearFormErrors(form);
  estateParsedJson=null;estateFileSelection++;setEstateMode('upload');$('#estate-file-info').hidden=true;
  $("#estate-dialog").showModal();
}
export function openFork(){
  const form=$("#fork-form");resetEditor(form);form.reset();clearFormErrors(form);
  if(state.current)form.name.value=`${state.current.name} (fork)`;
  $("#fork-dialog").showModal();
}
export function openCopySettings(){
  const form=$("#copy-settings-form");resetEditor(form);form.reset();clearFormErrors(form);
  const select=form.source_guid;select.innerHTML=state.gpos.filter(g=>g.guid!==state.current.guid).map(g=>`<option value="${escapeHtml(g.guid)}">${escapeHtml(g.name)} (r${g.revision})</option>`).join("");
  $("#copy-settings-dialog").showModal();
}
export function openComment(setting){
  const form=$("#comment-form");resetEditor(form);form.reset();clearFormErrors(form);form.setting_id.value=setting.id;
  form.comment.value=setting.comment||"";
  $("#comment-dialog").showModal();
}
function applyCurrent(data){applyPayload(data);renderAll();renderList()}
async function mutationFailure(form,error,context){await handleFormFailure(form,error,{onCurrent:applyCurrent,fieldNames:context.fieldNames,isActive:()=>isCurrentEditor(context)&&(context.workspace||isSameEditor(context))})}
function registryValue(form){
  const raw=form.value.value,type=form.registry_type.value;
  if(type==="REG_MULTI_SZ"){
    if(form.action.value==="delete")return [];
    let value;
    try{value=JSON.parse(raw)}catch{const error=new Error('Use a JSON array of strings, e.g. ["first", "second"].');error.issues=[{path:"value",message:error.message}];throw error}
    if(!Array.isArray(value)||value.some(item=>typeof item!=="string")){const error=new Error("Every REG_MULTI_SZ array item must be a string.");error.issues=[{path:"value",message:error.message}];throw error}
    return value;
  }
  return type==="REG_DWORD"||type==="REG_QWORD"?raw.trim():raw;
}

async function submitMutation(event,requestFor,message){
  event.preventDefault();
  const form=event.currentTarget;
  if(event.submitter?.value==="cancel"){form.closest("dialog").close();return}
  const context=beginMutation(form);if(!context)return;
  clearFormErrors(form);
  try{
    const {path,method,body}=requestFor(form,context);
    const data=await api(path,{method,body:JSON.stringify({...body,expected_revision:context.revision})});
    completeMutation(context,data,{apply:applyCurrent,renderList});toast(message);
  }catch(error){await mutationFailed(context,error,mutationFailure)}finally{endMutation(context)}
}

export function initForms(){
$("#gpo-form").onsubmit=async event=>{
  event.preventDefault();
  const form=event.currentTarget;
  if(event.submitter?.value==="cancel"){form.closest("dialog").close();return}
  const editing=form.dataset.edit==="true";
  const context=beginMutation(form,{workspace:!editing});if(!context)return;
  clearFormErrors(form);
  try{
    const body={name:form.name.value,description:form.description.value,actor:"local-operator",reason:form.reason.value};
    if(editing)Object.assign(body,{expected_revision:context.revision,computer_enabled:form.computer_enabled.checked,user_enabled:form.user_enabled.checked,status:form.status.value,domain:form.domain.value});
    const data=await api(editing?`/api/gpos/${context.guid}`:"/api/gpos",{method:editing?"PATCH":"POST",body:JSON.stringify(body)});
    if(editing)completeMutation(context,data,{apply:applyCurrent,renderList});
    else{
      const selectCreated=isSameEditor(context);
      updateMutationList(data,renderList);
      if(isCurrentEditor(context))context.dialog.close();
      if(selectCreated)await loadList(data.gpo.guid)
    }
    toast(editing?"Policy details saved":"Draft policy created");
  }catch(error){await mutationFailed(context,error,mutationFailure)}finally{endMutation(context)}
};
$("#setting-form").onsubmit=event=>submitMutation(event,(f,context)=>{
  const setting={side:f.side.value,hive:f.side.value==="computer"?"HKLM":"HKCU",key:f.key.value.replace(/^\\+|\\+$/g,""),value_name:f.value_name.value,registry_type:f.registry_type.value,value:registryValue(f),action:f.action.value,comment:f.comment.value};
  return {path:state.editingSetting?`/api/gpos/${context.guid}/settings/${state.editingSetting.id}`:`/api/gpos/${context.guid}/settings`,method:state.editingSetting?"PUT":"POST",body:{...audit(f.reason.value),setting}};
},"Registry policy saved");
$("#link-form").onsubmit=event=>submitMutation(event,(f,context)=>({path:state.editingLink?`/api/gpos/${context.guid}/links/${state.editingLink.id}`:`/api/gpos/${context.guid}/links`,method:state.editingLink?"PUT":"POST",body:{...audit(f.reason.value),link:{target:f.target.value,order:Number(f.order.value),enabled:f.enabled.checked,enforced:f.enforced.checked}}}),"Link intent saved");
$("#filter-form").onsubmit=event=>submitMutation(event,(f,context)=>({path:state.editingFilter?`/api/gpos/${context.guid}/security-filters/${state.editingFilter.id}`:`/api/gpos/${context.guid}/security-filters`,method:state.editingFilter?"PUT":"POST",body:{...audit(f.reason.value),filter:{principal:f.principal.value,permission:f.permission.value,target_type:f.target_type.value,inheritable:f.inheritable.checked}}}),"Security filter saved");
$("#wmi-form").onsubmit=event=>submitMutation(event,(f,context)=>({path:`/api/gpos/${context.guid}/wmi-filter`,method:"PUT",body:{...audit(f.reason.value),wmi_filter:{name:f.name.value,description:f.description.value,language:f.language.value,query:f.query.value}}}),"WMI filter saved");
  $("#estate-form").onsubmit=async event=>{
    event.preventDefault();const form=event.currentTarget;
    if(event.submitter?.value==="cancel"){form.closest("dialog").close();return}
    const context=beginMutation(form,{workspace:true});if(!context)return;clearFormErrors(form);
    try{
      let body;
      if(estateMode==="upload"){
        if(!estateParsedJson){showFormErrors(form,{issues:[{message:"Choose a valid JSON file first"}]});return}
        body=estateParsedJson;
      }else body=JSON.parse(form.json.value);
      const data=await api("/api/estate/import",{method:"POST",body:JSON.stringify(body)});
      const selectImported=isSameEditor(context);
      if(isCurrentEditor(context))context.dialog.close();
      await loadList(undefined,{select:selectImported});toast(`Imported ${data.imported} GPO(s), skipped ${data.skipped}`);
    }catch(error){await mutationFailed(context,error,(f,e)=>showFormErrors(f,e instanceof SyntaxError?{message:"Invalid JSON: "+e.message}:e))}finally{endMutation(context)}
  };
  $('#estate-file').onchange=async()=>{
    const selection=++estateFileSelection;
    estateParsedJson=null;
    const form=$('#estate-form'),info=$('#estate-file-info');
    clearFormErrors(form);info.hidden=true;info.textContent="";
    const file=$('#estate-file').files[0];if(!file)return;
    const MAX_ESTATE_BYTES=5*1024*1024;
    if(file.size>MAX_ESTATE_BYTES){showFormErrors(form,{issues:[{message:`File too large (${(file.size/1024/1024).toFixed(1)}MB). Maximum is 5MB.`}]});return}
    const sizeKb=(file.size/1024).toFixed(0);
    info.hidden=false;info.textContent=`Reading ${file.name}…`;
    try{
      const text=await file.text();
      if(selection!==estateFileSelection)return;
      estateParsedJson=JSON.parse(text);
      info.innerHTML=`<strong>${escapeHtml(file.name)}</strong> (${sizeKb} KB)${file.size>1048576?' <span class="diff-modified">⚠ File exceeds 1 MB</span>':''}`;
    }catch(e){
      if(selection!==estateFileSelection)return;
      estateParsedJson=null;
      info.hidden=false;info.innerHTML=`<strong>${escapeHtml(file.name)}</strong> (${sizeKb} KB) <span class="diff-removed">✗ Unable to read valid JSON: ${escapeHtml(e.message)}</span>`;
    }
  };
  $$('.estate-toggle .chip').forEach(c=>c.onclick=()=>setEstateMode(c.dataset.mode));
  $("#fork-form").onsubmit=async event=>{
    event.preventDefault();const form=event.currentTarget;
    if(event.submitter?.value==="cancel"){form.closest("dialog").close();return}
    const context=beginMutation(form);if(!context)return;clearFormErrors(form);
    try{
      const data=await api(`/api/gpos/${context.guid}/fork`,{method:"POST",body:JSON.stringify({name:form.name.value,actor:"local-operator",reason:form.reason.value})});
      const selectCreated=isSameEditor(context);updateMutationList(data,renderList);
      if(isCurrentEditor(context))context.dialog.close();
      if(selectCreated)await loadList(data.gpo.guid)
      toast("Forked to draft");
    }catch(error){await mutationFailed(context,error,(f,e)=>showFormErrors(f,e))}finally{endMutation(context)}
  };
  $("#copy-settings-form").onsubmit=event=>submitMutation(event,(f,context)=>({path:`/api/gpos/${context.guid}/copy-settings`,method:"POST",body:{...audit(f.reason.value),source_guid:f.source_guid.value,side:f.side.value||null}}),"Settings copied");
  $("#comment-form").onsubmit=event=>submitMutation(event,(f,context)=>({path:`/api/gpos/${context.guid}/settings/${f.setting_id.value}/comment`,method:"PATCH",body:{...audit(f.reason.value),comment:f.comment.value}}),"Comment saved");
}
async function removeMutation(context,path,reason,message){
  try{const data=await api(path,{method:"DELETE",body:JSON.stringify({actor:"local-operator",reason,expected_revision:context.revision})});completeMutation(context,data,{apply:applyCurrent,renderList});toast(message)}catch(error){await mutationFailed(context,error)}
}
export async function deleteSetting(id){const context=captureMutation();if(!confirm(`Remove registry setting ${id} from ${context.name}? This creates a new revision.`))return;await removeMutation(context,`/api/gpos/${context.guid}/settings/${id}`,"Remove registry policy","Setting removed")}
export async function deleteLink(id){const context=captureMutation(),target=state.current.links.find(item=>item.id===id)?.target||id;if(!confirm(`Remove link intent for ${target} from ${context.name}? This creates a new revision.`))return;await removeMutation(context,`/api/gpos/${context.guid}/links/${id}`,"Remove link intent","Link removed")}
export async function deleteFilter(id){const context=captureMutation(),principal=state.current.security_filters.find(item=>item.id===id)?.principal||id;if(!confirm(`Remove security filter ${principal} from ${context.name}? The policy may then apply more widely. This creates a new revision.`))return;await removeMutation(context,`/api/gpos/${context.guid}/security-filters/${id}`,"Remove security filter","Security filter removed")}
const restoringGpos=new Set();
export async function restoreRevision(revision,target){
  if(!target||state.current?.guid!==target.guid||state.current.revision!==target.revision){showPersistentError("This policy changed. Reload History before restoring a revision.");return}
  if(restoringGpos.has(target.guid))return;
  if(!confirm(`Restore revision ${revision} of ${target.name}? It is saved as a new revision that replaces the current content. Earlier revisions are kept.`))return;
  const context=captureMutation();
  const buttons=$$('[data-restore]');buttons.forEach(button=>{button.disabled=true});restoringGpos.add(target.guid);
  try{
    const data=await api(`/api/gpos/${target.guid}/revisions/${revision}/restore`,{method:"POST",body:JSON.stringify({actor:"local-operator",reason:`Restore revision ${revision}`,expected_revision:target.revision})});
    completeMutation(context,data,{apply:applyCurrent,renderList});
    if(state.current?.guid===target.guid)await loadHistory();
    toast(`Revision ${revision} restored`);
  }catch(error){await mutationFailed(context,error)}finally{restoringGpos.delete(target.guid);buttons.forEach(button=>{button.disabled=false})}
}
