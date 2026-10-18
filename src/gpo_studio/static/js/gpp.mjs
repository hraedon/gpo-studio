import {state,$,$$,escapeHtml,applyPayload} from './state.mjs';
import {api,toast,audit,showPersistentError} from './api.mjs';
import {clearFormErrors,showFormErrors,handleFormFailure} from './errors.mjs';
import {renderAll,renderList} from './render.mjs';

const ILT_TYPES=["ou","group","registry","ip_range","environment","wmi_query"];
const ILT_LABELS={ou:"OU",group:"Group",registry:"Registry",ip_range:"IP range",environment:"Environment",wmi_query:"WMI query"};
const ILT_HINTS={ou:"Distinguished name, e.g. OU=Servers,DC=studio,DC=local",group:"Group SID or name, e.g. S-1-5-32-544",registry:"Key\\ValueName path",ip_range:"CIDR (10.0.0.0/8) or range (10.0.0.1-10.0.0.99)",environment:"NAME=value",wmi_query:"WQL query (SELECT ... FROM ...)"};
const ILT_PREVIEW={ou:v=>"Member of "+v,group:v=>"Member of group "+v,registry:v=>"Registry "+v+" matches",ip_range:v=>"IP in "+v,environment:v=>"Environment "+v,wmi_query:v=>"WMI query: "+v};
const REG_TYPES=["REG_SZ","REG_EXPAND_SZ","REG_BINARY","REG_DWORD","REG_MULTI_SZ","REG_QWORD"];
const REG_VALUE_HINTS={REG_SZ:"Text value",REG_EXPAND_SZ:"Text with %VAR% expansion",REG_BINARY:"Hex bytes, e.g. 01FFA0",REG_DWORD:"Non-negative decimal integer",REG_MULTI_SZ:"Semicolon or newline-separated values",REG_QWORD:"Non-negative decimal integer"};
const REG_VALUE_ACTIONS=["create","replace","update","delete"];
const GROUP_ACTIONS=["add","replace","remove","update"];
function applyCurrent(data){applyPayload(data);renderAll();renderList()}


function copyName(name,existingNames){
  const used=new Set(existingNames.map(value=>value.trim().toLocaleLowerCase()));
  for(let number=1;;number++){
    const suffix=number===1?" (copy)":` (copy ${number})`;
    const candidate=name.slice(0,255-suffix.length)+suffix;
    if(!used.has(candidate.trim().toLocaleLowerCase()))return candidate;
  }
}

// Clone and item restore run on the server (final-r12): retained content an
// imported item carries is copied there from the store, never sent by the
// browser, because the add and edit routes accept retained content only as the
// stored item already holds it. The browser names the copy and nothing else.
export function gppCloneRequest(guid,scope,kind,item,existingNames=[]){
  const segment=kind==="groups"?"groups":"registry";
  const body={scope};
  if(kind==="groups")body.name=copyName(item.name,existingNames);
  return {path:`/api/gpos/${guid}/preferences/${segment}/${item.id}/clone`,body};
}

export function gppRestoreRequest(guid,scope,kind,id,revision){
  const segment=kind==="groups"?"groups":"registry";
  return {path:`/api/gpos/${guid}/preferences/${segment}/${id}/restore`,body:{scope,revision}};
}

// The edit protocol (final-r13): retained (imported) content never round-trips
// through the browser. An edit sends typed fields and the ids of what it keeps;
// the server merges it onto the stored item. A targeting row the editor cannot
// edit -- raw, a type it does not author, or joined with OR -- is sent as its id
// alone: kept as stored, in the row's place. Removing a row deletes that
// predicate and only its own retained content.
export function iltRows(filter){
  const items=filter?.items||[];
  const ids=filter?.ids||[];
  return items.map((item,index)=>{
    const raw=typeof item!=="object";
    const editable=!raw&&ILT_TYPES.includes(item.type)&&item.bool_op!=="OR";
    return {id:ids[index]||"",item,raw,editable};
  });
}

export function predicateEntry(row){
  if(row.readonly)return {id:row.id};
  const entry={type:row.type,negate:row.negate,value:row.value,bool_op:"AND"};
  if(row.id)entry.id=row.id;
  return entry;
}

export function memberEntry(member){
  const entry={sid:member.sid,name:member.name,action:member.action};
  if(member.id)entry.id=member.id;
  return entry;
}

// What an item carries that Studio does not edit, for the read-only note.
export function retainedSummary(item){
  if(!item)return "";
  const attrs=(item.unknown_attrs||[]).length+(item.unknown_props_attrs||[]).length+(item.members||[]).reduce((n,m)=>n+(m.unknown_attrs||[]).length,0)+(item.value?.unknown_attrs||[]).length;
  const elements=(item.unknown_children||[]).length+(item.unknown_props_children||[]).length;
  const parts=[];
  if(attrs)parts.push(`${attrs} attribute${attrs===1?"":"s"}`);
  if(elements)parts.push(`${elements} element${elements===1?"":"s"}`);
  if(!parts.length)return "";
  const verified=item.retained_unverified?" It has no import record: exports judge it by Studio's vocabulary — re-import from the source backup, or delete the item, if an export refuses it.":"";
  return `Imported content Studio does not edit is kept as imported: ${parts.join(", ")}.${verified}`;
}

export function findRevisionGppItem(snapshot,scope,kind,id){
  const collection=(snapshot.gpp_collections||[]).find(item=>item.scope===scope);
  const items=collection?.[kind]||[];
  return items.find(item=>item.id===id)||null;
}

export function moveGppItemIds(items,id,offset){
  const orderedIds=items.map(item=>item.id);
  const index=orderedIds.indexOf(id);
  const destination=index+offset;
  if(index<0||destination<0||destination>=orderedIds.length)return null;
  [orderedIds[index],orderedIds[destination]]=[orderedIds[destination],orderedIds[index]];
  return orderedIds;
}

export function iltOperatorWarning(predicate){
  if(predicate?.type!=="os")return "";
  const version=predicate.os_criteria?.version;
  if(version==="WINTHRESHOLDSRV")return "Matches the Windows Server 2016, 2019, 2022, and 2025 family; FilterOs cannot distinguish these releases. Use WMI or registry targeting for a specific build.";
  if(version==="WINTHRESHOLD")return "Matches both Windows 10 and Windows 11; FilterOs cannot distinguish these releases. Use WMI or registry targeting for a specific build.";
  return "";
}

function iltSummary(filter){
  const items=filter?.items||[];
  if(!items.length)return "—";
  const hasReadonly=iltRows(filter).some(row=>!row.editable);
  const operatorWarning=items.map(iltOperatorWarning).find(Boolean);
  return `ILT: ${items.length} predicate${items.length===1?'':'s'}${hasReadonly?' · preserved read-only parts':''}${operatorWarning?` · ${operatorWarning}`:''}`;
}

export function renderGpp(){
  const g=state.current;if(!g)return;
  const scope=state.gppScope;
  const collection=(g.gpp_collections||[]).find(c=>c.scope===scope);
  const groups=collection?collection.groups:[];
  const registry=collection?collection.registry:[];
  $("#gpp-groups-table").innerHTML=groups.map((grp,index)=>{
    const memberNames=grp.members.map(m=>escapeHtml(m.name||m.sid)).join(", ")||"—";
    const label=escapeHtml(grp.name);
    return `<tr><td class="mono">${escapeHtml(grp.action)}</td><td>${label}</td><td class="mono">${escapeHtml(grp.sid)||"—"}</td><td><div class="truncate" title="${memberNames}">${memberNames}</div></td><td>${escapeHtml(iltSummary(grp.ilt_filter))}${grp.retained_unverified?' <span class="gpp-readonly-warn" title="Retained content without an import record">· unverified import content</span>':''}</td><td><div class="row-actions"><button data-move-gpp-group="${escapeHtml(grp.id)}" data-offset="-1" aria-label="Move group ${label} earlier" title="Move earlier" ${index===0?'disabled':''}>↑</button><button data-move-gpp-group="${escapeHtml(grp.id)}" data-offset="1" aria-label="Move group ${label} later" title="Move later" ${index===groups.length-1?'disabled':''}>↓</button><button data-edit-gpp-group="${escapeHtml(grp.id)}" aria-label="Edit group ${label}">Edit</button><button data-clone-gpp-group="${escapeHtml(grp.id)}" aria-label="Clone group ${label}">Clone</button><button data-restore-gpp-group="${escapeHtml(grp.id)}" aria-label="Restore group ${label} from a revision">Restore</button><button data-delete-gpp-group="${escapeHtml(grp.id)}" aria-label="Delete group ${label}">×</button></div></td></tr>`;
  }).join("");
  $("#gpp-groups-empty").hidden=groups.length>0;
  $$("[data-edit-gpp-group]").forEach(el=>el.onclick=()=>{const grp=groups.find(g=>g.id===el.dataset.editGppGroup);openGppGroup(scope,grp)});
  $$("[data-move-gpp-group]").forEach(el=>el.onclick=()=>moveGppItem(scope,"groups",el.dataset.moveGppGroup,Number(el.dataset.offset)));
  $$("[data-clone-gpp-group]").forEach(el=>el.onclick=()=>{const grp=groups.find(g=>g.id===el.dataset.cloneGppGroup);if(grp)cloneGppItem(scope,"groups",grp,groups.map(g=>g.name))});
  $$("[data-restore-gpp-group]").forEach(el=>el.onclick=()=>restoreGppItem(scope,"groups",el.dataset.restoreGppGroup));
  $$("[data-delete-gpp-group]").forEach(el=>el.onclick=()=>deleteGppGroup(scope,el.dataset.deleteGppGroup));
  $("#gpp-registry-table").innerHTML=registry.map((reg,index)=>{
    const v=reg.value;
    const values=v?`${escapeHtml(v.name)}=${escapeHtml(formatRegValue(v))}`:"—";
    const label=escapeHtml(reg.key);
    return `<tr><td class="mono">${escapeHtml(reg.action)}</td><td><div class="mono truncate" title="${label}">${label}</div></td><td><div class="truncate" title="${escapeHtml(values)}">${escapeHtml(values)}</div></td><td>${escapeHtml(iltSummary(reg.ilt_filter))}${reg.retained_unverified?' <span class="gpp-readonly-warn" title="Retained content without an import record">· unverified import content</span>':''}</td><td><div class="row-actions"><button data-move-gpp-registry="${escapeHtml(reg.id)}" data-offset="-1" aria-label="Move registry item ${label} earlier" title="Move earlier" ${index===0?'disabled':''}>↑</button><button data-move-gpp-registry="${escapeHtml(reg.id)}" data-offset="1" aria-label="Move registry item ${label} later" title="Move later" ${index===registry.length-1?'disabled':''}>↓</button><button data-edit-gpp-registry="${escapeHtml(reg.id)}" aria-label="Edit registry item ${label}">Edit</button><button data-clone-gpp-registry="${escapeHtml(reg.id)}" aria-label="Clone registry item ${label}">Clone</button><button data-restore-gpp-registry="${escapeHtml(reg.id)}" aria-label="Restore registry item ${label} from a revision">Restore</button><button data-delete-gpp-registry="${escapeHtml(reg.id)}" aria-label="Delete registry item ${label}">×</button></div></td></tr>`;
  }).join("");
  $("#gpp-registry-empty").hidden=registry.length>0;
  $$("[data-edit-gpp-registry]").forEach(el=>el.onclick=()=>{const reg=registry.find(r=>r.id===el.dataset.editGppRegistry);openGppRegistry(scope,reg)});
  $$("[data-move-gpp-registry]").forEach(el=>el.onclick=()=>moveGppItem(scope,"registry",el.dataset.moveGppRegistry,Number(el.dataset.offset)));
  $$("[data-clone-gpp-registry]").forEach(el=>el.onclick=()=>{const reg=registry.find(r=>r.id===el.dataset.cloneGppRegistry);if(reg)cloneGppItem(scope,"registry",reg)});
  $$("[data-restore-gpp-registry]").forEach(el=>el.onclick=()=>restoreGppItem(scope,"registry",el.dataset.restoreGppRegistry));
  $$("[data-delete-gpp-registry]").forEach(el=>el.onclick=()=>deleteGppRegistry(scope,el.dataset.deleteGppRegistry));
}

function formatRegValue(v){
  if(v.action==="delete")return "delete";
  if(Array.isArray(v.value))return v.value.join(";");
  return String(v.value);
}

export function initGpp(){
  const scopeRow=$(".gpp-scope-row");
  if(scopeRow&&!$("#gpp-ilt-limitation")){
    const notice=document.createElement("div");
    notice.id="gpp-ilt-limitation";
    notice.className="issue-banner";
    notice.setAttribute("role","note");
    notice.textContent="Targeting predicates you add here are combined with AND. Imported OR, grouped or unsupported expressions are kept as they are: you can move or remove them, but not edit them here.";
    scopeRow.insertAdjacentElement("afterend",notice);
  }
  $$(".gpp-scope-row .chip").forEach(chip=>chip.onclick=()=>{$$(".gpp-scope-row .chip").forEach(x=>{x.classList.toggle("active",x===chip);x.setAttribute("aria-pressed",String(x===chip))});state.gppScope=chip.dataset.gppScope;renderGpp()});
  $("#add-gpp-group").onclick=()=>openGppGroup(state.gppScope);
  $("#add-gpp-registry").onclick=()=>openGppRegistry(state.gppScope);
  $("#gpp-add-member").onclick=()=>addMemberRow();
  $("#gpp-add-predicate").onclick=()=>addPredicateRow("gpp-ilt-list","gpp-ilt-preview");
  $("#gpp-add-ilt-registry").onclick=()=>addPredicateRow("gpp-ilt-registry-list","gpp-ilt-registry-preview");
  $("#gpp-group-form").onsubmit=submitGppGroup;
  $("#gpp-registry-form").onsubmit=submitGppRegistry;
}

function showRetained(formId,item){
  const form=$("#"+formId);
  let note=form.querySelector(".gpp-retained-note");
  if(!note){note=document.createElement("p");note.className="gpp-note gpp-retained-note";note.setAttribute("role","note");form.querySelector(".dialog-actions")?.insertAdjacentElement("beforebegin",note)||form.appendChild(note)}
  const text=retainedSummary(item);
  note.textContent=text;note.hidden=!text;
}

export function openGppGroup(scope,group=null){
  const f=$("#gpp-group-form");f.reset();clearFormErrors(f);
  state.editingGppGroup=group;
  f.scope.value=scope;
  $("#gpp-group-dialog-title").textContent=group?"Edit group":"Add group";
  $("#gpp-members-list").innerHTML="";
  $("#gpp-ilt-list").innerHTML="";
  $("#gpp-ilt-preview").textContent="";
  if(group){
    f.name.value=group.name;
    f.sid.value=group.sid||"";
    f.action.value=group.action;
    f.description.value=group.description||"";
    f.remove_all_users.checked=group.remove_all_users;
    f.remove_all_groups.checked=group.remove_all_groups;
    (group.members||[]).forEach(m=>addMemberRow(m));
    iltRows(group.ilt_filter).forEach(row=>addPredicateRow("gpp-ilt-list","gpp-ilt-preview",row));
    f.reason.value="Update GPP group";
  }else{
    f.reason.value="Add GPP group";
  }
  showRetained("gpp-group-form",group);
  updateIltPreview("gpp-ilt-list","gpp-ilt-preview");
  $("#gpp-group-dialog").showModal();
}

function addMemberRow(member=null){
  const list=$("#gpp-members-list");
  const row=document.createElement("div");
  row.className="gpp-row";
  row.innerHTML=`<input data-field="sid" placeholder="SID (required)" maxlength="255" required><input data-field="name" placeholder="Name" maxlength="255"><select data-field="action">${GROUP_ACTIONS.map(a=>`<option value="${a}">${a}</option>`).join("")}</select><button type="button" class="quiet">×</button>`;
  if(member){row.querySelector('[data-field=sid]').value=member.sid;row.querySelector('[data-field=name]').value=member.name||"";row.querySelector('[data-field=action]').value=member.action;row.dataset.id=member.id||""}
  const removeButton=row.querySelector("button");removeButton.setAttribute("aria-label","Remove member row");removeButton.onclick=()=>row.remove();
  list.appendChild(row);
}

function collectMembers(){
  return [...$("#gpp-members-list").querySelectorAll(".gpp-row")].map(row=>memberEntry({sid:row.querySelector('[data-field=sid]').value.trim(),name:row.querySelector('[data-field=name]').value.trim(),action:row.querySelector('[data-field=action]').value,id:row.dataset.id||""})).filter(m=>m.sid);
}

async function submitGppGroup(event){
  event.preventDefault();
  if(event.submitter&&event.submitter.value==="cancel"){event.currentTarget.closest("dialog").close();return}
  const f=event.currentTarget,scope=f.scope.value;
  const partialMember=[...$("#gpp-members-list").querySelectorAll(".gpp-row")].find(row=>row.querySelector('[data-field=name]').value.trim()&&!row.querySelector('[data-field=sid]').value.trim());
  if(partialMember){showFormErrors(f,{issues:[{message:"Each member with a name must also have a SID."}]});return}
  const group={name:f.name.value.trim(),sid:f.sid.value.trim(),action:f.action.value,description:f.description.value.trim(),remove_all_users:f.remove_all_users.checked,remove_all_groups:f.remove_all_groups.checked,members:collectMembers(),ilt_filter:collectIlt("gpp-ilt-list")};
  const broadMembershipChange=group.action==="replace"||group.action==="remove"||group.remove_all_users||group.remove_all_groups||group.members.some(member=>member.action==="replace"||member.action==="remove");
  if(broadMembershipChange&&!confirm(`Save broad membership change for group "${group.name}"?\n\nAction: ${group.action}. Remove all users: ${group.remove_all_users?'yes':'no'}. Remove all groups: ${group.remove_all_groups?'yes':'no'}. Review the required change reason before continuing.`))return;
  const path=state.editingGppGroup?`/api/gpos/${state.current.guid}/preferences/groups/${state.editingGppGroup.id}`:`/api/gpos/${state.current.guid}/preferences/groups`;
  try{const data=await api(path,{method:state.editingGppGroup?"PUT":"POST",body:JSON.stringify({scope,...audit(f.reason.value),group})});$("#gpp-group-dialog").close();applyCurrent(data);toast("GPP group saved")}catch(error){await handleFormFailure(f,error,{onCurrent:applyCurrent})}
}

export async function deleteGppGroup(scope,id){
  const collection=(state.current.gpp_collections||[]).find(item=>item.scope===scope);
  const group=(collection?.groups||[]).find(item=>item.id===id);
  if(!confirm(`Remove GPP group "${group?.name||id}" from the ${scope} draft?\n\nIts action, members, and item-level targeting will be removed in one new revision.`))return;
  try{const data=await api(`/api/gpos/${state.current.guid}/preferences/groups/${id}`,{method:"DELETE",body:JSON.stringify({scope,...audit("Remove GPP group")})});applyCurrent(data);toast("GPP group removed")}catch(error){showPersistentError(error.message)}
}

export async function moveGppItem(scope,kind,id,offset){
  const collection=(state.current.gpp_collections||[]).find(item=>item.scope===scope);
  const items=collection?.[kind]||[];
  const orderedIds=moveGppItemIds(items,id,offset);
  if(!orderedIds)return;
  try{
    const label=kind==="groups"?"groups":"registry items";
    const data=await api(`/api/gpos/${state.current.guid}/preferences/reorder`,{method:"POST",body:JSON.stringify({scope,kind,ordered_ids:orderedIds,...audit(`Reorder GPP ${label}`)})});
    applyCurrent(data);toast(`GPP ${label} reordered`);
  }catch(error){showPersistentError(error.message)}
}

export async function cloneGppItem(scope,kind,item,existingNames=[]){
  const label=kind==="groups"?"group":"registry item";
  try{
    const request=gppCloneRequest(state.current.guid,scope,kind,item,existingNames);
    const data=await api(request.path,{method:"POST",body:JSON.stringify({...request.body,...audit(`Clone GPP ${label}`)})});
    applyCurrent(data);toast(`GPP ${label} cloned`);
    // Open the copy (the last item) for editing.
    const collection=(state.current.gpp_collections||[]).find(entry=>entry.scope===scope);
    const items=collection?.[kind]||[];
    const copied=items[items.length-1];
    if(copied){if(kind==="groups")openGppGroup(scope,copied);else openGppRegistry(scope,copied)}
  }catch(error){showPersistentError(error.message)}
}

export async function restoreGppItem(scope,kind,id){
  const revisionText=prompt("Revision containing the item version to restore:");
  if(revisionText===null)return;
  const revision=Number(revisionText);
  if(!Number.isInteger(revision)||revision<1){toast("Enter a valid revision number");return}
  try{
    const historical=await api(`/api/gpos/${state.current.guid}/revisions/${revision}`);
    const item=findRevisionGppItem(historical.snapshot,scope,kind,id);
    if(!item){toast(`This item does not exist in revision ${revision}`);return}
    const currentCollection=(state.current.gpp_collections||[]).find(collection=>collection.scope===scope);
    const current=(currentCollection?.[kind]||[]).find(entry=>entry.id===id);
    const label=kind==="groups"?(current?.name||item.name):(current?.key||item.key);
    if(!confirm(`Restore ${kind==="groups"?"group":"registry item"} "${label}" from revision ${revision}?\n\nOnly this item will be overwritten. The restore is appended as a new revision; all other current policy content remains unchanged.`))return;
    const property=kind==="groups"?"group":"registry";
    const request=gppRestoreRequest(state.current.guid,scope,kind,id,revision);
    const data=await api(request.path,{method:"POST",body:JSON.stringify({...request.body,...audit(`Restore GPP ${property} from revision ${revision}`)})});
    applyCurrent(data);toast(`${kind==="groups"?"Group":"Registry item"} restored from revision ${revision}`);
  }catch(error){showPersistentError(error.message)}
}

export function openGppRegistry(scope,registry=null){
  const f=$("#gpp-registry-form");f.reset();clearFormErrors(f);
  state.editingGppRegistry=registry;
  f.scope.value=scope;
  $("#gpp-registry-dialog-title").textContent=registry?"Edit registry":"Add registry";
  $("#gpp-values-list").innerHTML="";
  $("#gpp-ilt-registry-list").innerHTML="";
  $("#gpp-ilt-registry-preview").textContent="";
    if(registry){
      f.key.value=registry.key;
      f.hive.value=registry.hive||"HKEY_LOCAL_MACHINE";
      f.action.value=registry.action;
      addValueRow(registry.value||{name:"",value:"",registry_type:"",action:"create"});
      iltRows(registry.ilt_filter).forEach(row=>addPredicateRow("gpp-ilt-registry-list","gpp-ilt-registry-preview",row));
      f.reason.value="Update GPP registry";
  }else{
      f.reason.value="Add GPP registry";
      addValueRow();
  }
  showRetained("gpp-registry-form",registry);
  updateIltPreview("gpp-ilt-registry-list","gpp-ilt-registry-preview");
  $("#gpp-registry-dialog").showModal();
}

function addValueRow(value=null){
  const list=$("#gpp-values-list");
  const row=document.createElement("div");
  row.className="gpp-row";
  row.innerHTML=`<input data-field="name" placeholder="Value name (empty = key-only)" maxlength="255"><select data-field="type"><option value="">(key-only)</option>${REG_TYPES.map(t=>`<option value="${t}">${t}</option>`).join("")}</select><input data-field="value" placeholder="Value"><select data-field="action">${REG_VALUE_ACTIONS.map(a=>`<option value="${a}">${a}</option>`).join("")}</select><label title="Configure the key's default value"><input type="checkbox" data-field="default"> Default</label><button type="button" class="quiet">×</button>`;
  const typeSel=row.querySelector('[data-field=type]');
  const valueInput=row.querySelector('[data-field=value]');
  const actionSel=row.querySelector('[data-field=action]');
  const defaultBox=row.querySelector('[data-field=default]');
  defaultBox.onchange=()=>{
    if(defaultBox.checked){row.querySelector('[data-field=name]').value="";row.querySelector('[data-field=name]').disabled=true}
    else{row.querySelector('[data-field=name]').disabled=false}
  };
  typeSel.onchange=()=>{if(!typeSel.value&&!defaultBox.checked)valueInput.value="";valueInput.placeholder=typeSel.value?REG_VALUE_HINTS[typeSel.value]||"Value":defaultBox.checked?"Default value":"Key-only entry";valueInput.disabled=actionSel.value==="delete"||(!typeSel.value&&!defaultBox.checked)};
  actionSel.onchange=()=>{valueInput.disabled=actionSel.value==="delete"||(!typeSel.value&&!defaultBox.checked)};
  if(value){row.querySelector('[data-field=name]').value=value.name;typeSel.value=value.registry_type||"";valueInput.value=Array.isArray(value.value)?value.value.join(";"):String(value.value);actionSel.value=value.action;defaultBox.checked=!!value.default;if(defaultBox.checked)row.querySelector('[data-field=name]').disabled=true}
  valueInput.disabled=actionSel.value==="delete"||(!typeSel.value&&!defaultBox.checked);
  typeSel.onchange();
  const removeButton=row.querySelector("button");removeButton.setAttribute("aria-label","Remove registry value row");removeButton.onclick=()=>row.remove();
  list.appendChild(row);
}

function collectValue(){
  const row=$("#gpp-values-list").querySelector(".gpp-row");
  if(!row)return null;
    const defaultBox=row.querySelector('[data-field=default]');
    const isDefault=defaultBox&&defaultBox.checked;
    const name=isDefault?"":row.querySelector('[data-field=name]').value.trim();
    const type=row.querySelector('[data-field=type]').value;
    const raw=row.querySelector('[data-field=value]').value;
    const action=row.querySelector('[data-field=action]').value;
    let value;
    if(action==="delete")value="";
    else if(type==="REG_MULTI_SZ")value=raw.split(/[\r\n;]+/).map(s=>s.trim()).filter(s=>s.length>0);
    else if(type==="REG_DWORD"||type==="REG_QWORD")value=raw.trim();
    else value=raw;
    return {name,value,registry_type:type,action,default:isDefault};
}

async function submitGppRegistry(event){
  event.preventDefault();
  if(event.submitter&&event.submitter.value==="cancel"){event.currentTarget.closest("dialog").close();return}
  const f=event.currentTarget,scope=f.scope.value;
  const value=collectValue();
  if(!value){showFormErrors(f,{issues:[{message:"A registry value is required."}]});return}
  if(!value.default&&String(value.value).trim()&&!value.name.trim()&&value.registry_type){showFormErrors(f,{issues:[{message:"Each value with data must also have a name."}]});return}
  const commonIlt=collectIlt("gpp-ilt-registry-list");
  const badDword=(value.action!=="delete"&&(value.registry_type==="REG_DWORD"||value.registry_type==="REG_QWORD")&&!/^(?:0|[1-9][0-9]*)$/.test(value.value));
  if(badDword){showFormErrors(f,{issues:[{message:`${value.registry_type} value for "${value.name||"(default)"}" must be a non-negative decimal integer.`}]});return}
  const registry={key:f.key.value.trim(),hive:f.hive.value,action:f.action.value,value,ilt_filter:commonIlt};
  const path=state.editingGppRegistry?`/api/gpos/${state.current.guid}/preferences/registry/${state.editingGppRegistry.id}`:`/api/gpos/${state.current.guid}/preferences/registry`;
  try{const data=await api(path,{method:state.editingGppRegistry?"PUT":"POST",body:JSON.stringify({scope,...audit(f.reason.value),registry})});$("#gpp-registry-dialog").close();applyCurrent(data);toast("GPP registry saved")}catch(error){await handleFormFailure(f,error,{onCurrent:applyCurrent})}
}

export async function deleteGppRegistry(scope,id){
  const collection=(state.current.gpp_collections||[]).find(item=>item.scope===scope);
  const registry=(collection?.registry||[]).find(item=>item.id===id);
  if(!confirm(`Remove GPP registry item "${registry?.key||id}" from the ${scope} draft?\n\nIts value and item-level targeting will be removed in one new revision.`))return;
  try{const data=await api(`/api/gpos/${state.current.guid}/preferences/registry/${id}`,{method:"DELETE",body:JSON.stringify({scope,...audit("Remove GPP registry")})});applyCurrent(data);toast("GPP registry removed")}catch(error){showPersistentError(error.message)}
}

function addPredicateRow(listId,previewId,row=null){
  const list=$("#"+listId);
  const el=document.createElement("div");
  el.className="gpp-row";
  el.innerHTML=`<select data-field="type">${ILT_TYPES.map(t=>`<option value="${t}">${ILT_LABELS[t]}</option>`).join("")}</select><label><input type="checkbox" data-field="negate"> NOT</label><input data-field="value" placeholder="${ILT_HINTS.ou}"><button type="button" class="quiet" data-move="-1">↑</button><button type="button" class="quiet" data-move="1">↓</button><button type="button" class="quiet" data-remove>×</button>`;
  const typeSel=el.querySelector('[data-field=type]');
  const valueInput=el.querySelector('[data-field=value]');
  const negateBox=el.querySelector('[data-field=negate]');
  const update=()=>{valueInput.placeholder=ILT_HINTS[typeSel.value]||"";updateIltPreview(listId,previewId)};
  typeSel.onchange=update;
  valueInput.oninput=()=>updateIltPreview(listId,previewId);
  negateBox.onchange=()=>updateIltPreview(listId,previewId);
  if(row)el.dataset.id=row.id||"";
  const predicate=row?.item;
  const readonly=(text,title)=>{el.dataset.readonly="true";typeSel.disabled=true;valueInput.disabled=true;negateBox.disabled=true;el.title=title;const warn=document.createElement("span");warn.className="gpp-readonly-warn";warn.textContent=text;el.insertBefore(warn,el.querySelector("[data-move]"))};
  if(row&&row.raw){valueInput.value="[unsupported predicate]";readonly("⚠ Unknown — kept on save","Unknown predicate: kept as imported; it can be moved or removed, not edited")}
  else if(row&&!row.editable&&ILT_TYPES.includes(predicate.type)){typeSel.value=predicate.type;valueInput.value=predicate.value;negateBox.checked=predicate.negate;readonly("⚠ OR — kept on save","Predicate joined with OR: kept as imported; it can be moved or removed, not edited here")}
  else if(row&&!row.editable){const unsupportedOption=document.createElement("option");unsupportedOption.value=predicate.type;unsupportedOption.textContent=predicate.type==="os"?"Operating system":predicate.type;typeSel.appendChild(unsupportedOption);typeSel.value=predicate.type;const criteria=predicate.os_criteria;valueInput.value=criteria?`${criteria.os_class}/${criteria.version}/${criteria.product_type}/${criteria.edition}/${criteria.service_pack}`:predicate.value;negateBox.checked=predicate.negate;const operatorWarning=iltOperatorWarning(predicate);readonly(operatorWarning||"Unsupported — kept on save",operatorWarning||"Unsupported predicate type: kept as imported; it can be moved or removed, not edited")}
  else if(row){typeSel.value=predicate.type;valueInput.value=predicate.value;negateBox.checked=predicate.negate}
  update();
  const removeButton=el.querySelector("[data-remove]");
  removeButton.setAttribute("aria-label",el.dataset.readonly==="true"?"Remove imported ILT predicate":"Remove ILT predicate");
  removeButton.onclick=()=>{el.remove();updateIltPreview(listId,previewId)};
  el.querySelectorAll("[data-move]").forEach(button=>{
    const offset=Number(button.dataset.move);
    button.setAttribute("aria-label",offset<0?"Move ILT predicate earlier":"Move ILT predicate later");
    button.onclick=()=>{const sibling=offset<0?el.previousElementSibling:el.nextElementSibling;if(!sibling)return;if(offset<0)sibling.before(el);else sibling.after(el);updateIltPreview(listId,previewId)};
  });
  list.appendChild(el);
}

function collectIlt(listId){
  const rows=$("#"+listId).querySelectorAll(".gpp-row");
  const items=[...rows].map(el=>{
    if(el.dataset.readonly==="true")return predicateEntry({readonly:true,id:el.dataset.id});
    const value=el.querySelector('[data-field=value]').value.trim();
    if(!value)return null;
    return predicateEntry({id:el.dataset.id||"",type:el.querySelector('[data-field=type]').value,negate:el.querySelector('[data-field=negate]').checked,value});
  }).filter(item=>item!==null);
  if(!items.length)return null;
  return {items};
}

function updateIltPreview(listId,previewId){
  const rows=$("#"+listId).querySelectorAll(".gpp-row");
  const editableParts=[...rows].filter(row=>row.dataset.readonly!=="true").map(row=>{
    const type=row.querySelector('[data-field=type]').value;
    const negate=row.querySelector('[data-field=negate]').checked;
    const value=row.querySelector('[data-field=value]').value.trim();
    if(!value)return "";
    let text=ILT_PREVIEW[type](value);
    if(negate)text="NOT "+text;
    return text;
  }).filter(Boolean);
  const readonlyCount=[...rows].filter(row=>row.dataset.readonly==="true").length;
  const parts=[];
  if(editableParts.length)parts.push(editableParts.join(" AND "));
  if(readonlyCount)parts.push(`${readonlyCount} imported expression part${readonlyCount===1?' is':'s are'} preserved read-only`);
  $("#"+previewId).textContent=parts.join(" · ");
}
