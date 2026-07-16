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
let groupSource=null;
let registrySource=null;
function applyCurrent(data){applyPayload(data);renderAll();renderList()}

function copy(value){return JSON.parse(JSON.stringify(value))}

function copyName(name,existingNames){
  const used=new Set(existingNames.map(value=>value.trim().toLocaleLowerCase()));
  for(let number=1;;number++){
    const suffix=number===1?" (copy)":` (copy ${number})`;
    const candidate=name.slice(0,255-suffix.length)+suffix;
    if(!used.has(candidate.trim().toLocaleLowerCase()))return candidate;
  }
}

export function prepareGppGroupClone(group,existingNames=[]){
  const cloned=copy(group);
  cloned.id="";
  cloned.name=copyName(cloned.name,existingNames);
  cloned.members=(cloned.members||[]).map(member=>({...member,id:""}));
  return cloned;
}

export function prepareGppRegistryClone(registry){
  const cloned=copy(registry);
  cloned.id="";
  cloned.uid="";
  if(cloned.value)cloned.value.id="";
  return cloned;
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

function iltSummary(filter){
  const items=filter?.items||[];
  if(!items.length)return "—";
  const hasReadonly=items.some(item=>typeof item!=="object"||item.bool_op==="OR"||!ILT_TYPES.includes(item.type));
  return `ILT: ${items.length} predicate${items.length===1?'':'s'}${hasReadonly?' · preserved read-only parts':''}`;
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
    return `<tr><td class="mono">${escapeHtml(grp.action)}</td><td>${label}</td><td class="mono">${escapeHtml(grp.sid)||"—"}</td><td><div class="truncate" title="${memberNames}">${memberNames}</div></td><td>${escapeHtml(iltSummary(grp.ilt_filter))}</td><td><div class="row-actions"><button data-move-gpp-group="${escapeHtml(grp.id)}" data-offset="-1" aria-label="Move group ${label} earlier" title="Move earlier" ${index===0?'disabled':''}>↑</button><button data-move-gpp-group="${escapeHtml(grp.id)}" data-offset="1" aria-label="Move group ${label} later" title="Move later" ${index===groups.length-1?'disabled':''}>↓</button><button data-edit-gpp-group="${escapeHtml(grp.id)}" aria-label="Edit group ${label}">Edit</button><button data-clone-gpp-group="${escapeHtml(grp.id)}" aria-label="Clone group ${label}">Clone</button><button data-restore-gpp-group="${escapeHtml(grp.id)}" aria-label="Restore group ${label} from a revision">Restore</button><button data-delete-gpp-group="${escapeHtml(grp.id)}" aria-label="Delete group ${label}">×</button></div></td></tr>`;
  }).join("");
  $("#gpp-groups-empty").hidden=groups.length>0;
  $$("[data-edit-gpp-group]").forEach(el=>el.onclick=()=>{const grp=groups.find(g=>g.id===el.dataset.editGppGroup);openGppGroup(scope,grp)});
  $$("[data-move-gpp-group]").forEach(el=>el.onclick=()=>moveGppItem(scope,"groups",el.dataset.moveGppGroup,Number(el.dataset.offset)));
  $$("[data-clone-gpp-group]").forEach(el=>el.onclick=()=>{const grp=groups.find(g=>g.id===el.dataset.cloneGppGroup);if(grp)openGppGroup(scope,prepareGppGroupClone(grp,groups.map(g=>g.name)),true)});
  $$("[data-restore-gpp-group]").forEach(el=>el.onclick=()=>restoreGppItem(scope,"groups",el.dataset.restoreGppGroup));
  $$("[data-delete-gpp-group]").forEach(el=>el.onclick=()=>deleteGppGroup(scope,el.dataset.deleteGppGroup));
  $("#gpp-registry-table").innerHTML=registry.map((reg,index)=>{
    const v=reg.value;
    const values=v?`${escapeHtml(v.name)}=${escapeHtml(formatRegValue(v))}`:"—";
    const label=escapeHtml(reg.key);
    return `<tr><td class="mono">${escapeHtml(reg.action)}</td><td><div class="mono truncate" title="${label}">${label}</div></td><td><div class="truncate" title="${escapeHtml(values)}">${escapeHtml(values)}</div></td><td>${escapeHtml(iltSummary(reg.ilt_filter))}</td><td><div class="row-actions"><button data-move-gpp-registry="${escapeHtml(reg.id)}" data-offset="-1" aria-label="Move registry item ${label} earlier" title="Move earlier" ${index===0?'disabled':''}>↑</button><button data-move-gpp-registry="${escapeHtml(reg.id)}" data-offset="1" aria-label="Move registry item ${label} later" title="Move later" ${index===registry.length-1?'disabled':''}>↓</button><button data-edit-gpp-registry="${escapeHtml(reg.id)}" aria-label="Edit registry item ${label}">Edit</button><button data-clone-gpp-registry="${escapeHtml(reg.id)}" aria-label="Clone registry item ${label}">Clone</button><button data-restore-gpp-registry="${escapeHtml(reg.id)}" aria-label="Restore registry item ${label} from a revision">Restore</button><button data-delete-gpp-registry="${escapeHtml(reg.id)}" aria-label="Delete registry item ${label}">×</button></div></td></tr>`;
  }).join("");
  $("#gpp-registry-empty").hidden=registry.length>0;
  $$("[data-edit-gpp-registry]").forEach(el=>el.onclick=()=>{const reg=registry.find(r=>r.id===el.dataset.editGppRegistry);openGppRegistry(scope,reg)});
  $$("[data-move-gpp-registry]").forEach(el=>el.onclick=()=>moveGppItem(scope,"registry",el.dataset.moveGppRegistry,Number(el.dataset.offset)));
  $$("[data-clone-gpp-registry]").forEach(el=>el.onclick=()=>{const reg=registry.find(r=>r.id===el.dataset.cloneGppRegistry);if(reg)openGppRegistry(scope,prepareGppRegistryClone(reg),true)});
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
    notice.textContent="Browser ILT limitation: new predicates are combined with AND only. Imported OR, grouped, or unsupported expressions remain read-only and are preserved.";
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

export function openGppGroup(scope,group=null,cloning=false){
  const f=$("#gpp-group-form");f.reset();clearFormErrors(f);
  state.editingGppGroup=group&&!cloning?group:null;
  groupSource=group;
  f.scope.value=scope;
  $("#gpp-group-dialog-title").textContent=cloning?"Clone group":group?"Edit group":"Add group";
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
    if(group.ilt_filter&&group.ilt_filter.items)group.ilt_filter.items.forEach(item=>{
      if(typeof item==="string")addPredicateRow("gpp-ilt-list","gpp-ilt-preview",{unknown:true,raw:item});
      else addPredicateRow("gpp-ilt-list","gpp-ilt-preview",item);
    });
    f.reason.value=cloning?"Clone GPP group":"Update GPP group";
  }else{
    f.reason.value="Add GPP group";
  }
  updateIltPreview("gpp-ilt-list","gpp-ilt-preview");
  $("#gpp-group-dialog").showModal();
}

function addMemberRow(member=null){
  const list=$("#gpp-members-list");
  const row=document.createElement("div");
  row.className="gpp-row";
  row.innerHTML=`<input data-field="sid" placeholder="SID (required)" maxlength="255" required><input data-field="name" placeholder="Name" maxlength="255"><select data-field="action">${GROUP_ACTIONS.map(a=>`<option value="${a}">${a}</option>`).join("")}</select><button type="button" class="quiet">×</button>`;
  row.dataset.unknownAttrs="[]";
  if(member){row.querySelector('[data-field=sid]').value=member.sid;row.querySelector('[data-field=name]').value=member.name||"";row.querySelector('[data-field=action]').value=member.action;row.dataset.id=member.id||"";row.dataset.unknownAttrs=JSON.stringify(member.unknown_attrs||[])}
  const removeButton=row.querySelector("button");removeButton.setAttribute("aria-label","Remove member row");removeButton.onclick=()=>row.remove();
  list.appendChild(row);
}

function collectMembers(){
  return [...$("#gpp-members-list").querySelectorAll(".gpp-row")].map(row=>({sid:row.querySelector('[data-field=sid]').value.trim(),name:row.querySelector('[data-field=name]').value.trim(),action:row.querySelector('[data-field=action]').value,id:row.dataset.id||"",unknown_attrs:JSON.parse(row.dataset.unknownAttrs||"[]")})).filter(m=>m.sid);
}

async function submitGppGroup(event){
  event.preventDefault();
  if(event.submitter&&event.submitter.value==="cancel"){event.currentTarget.closest("dialog").close();return}
  const f=event.currentTarget,scope=f.scope.value;
  if($("#gpp-ilt-list").querySelectorAll('.gpp-row[data-readonly="true"]').length&&!confirm("This item contains ILT predicates that cannot be edited in the browser. They will be preserved on save. Continue?"))return;
  const partialMember=[...$("#gpp-members-list").querySelectorAll(".gpp-row")].find(row=>row.querySelector('[data-field=name]').value.trim()&&!row.querySelector('[data-field=sid]').value.trim());
  if(partialMember){showFormErrors(f,{issues:[{message:"Each member with a name must also have a SID."}]});return}
  const group={name:f.name.value.trim(),sid:f.sid.value.trim(),action:f.action.value,description:f.description.value.trim(),remove_all_users:f.remove_all_users.checked,remove_all_groups:f.remove_all_groups.checked,members:collectMembers(),ilt_filter:collectIlt("gpp-ilt-list")};
  if(groupSource){group.unknown_attrs=groupSource.unknown_attrs||[];group.unknown_props_attrs=groupSource.unknown_props_attrs||[];group.unknown_children=groupSource.unknown_children||[]}
  if(state.editingGppGroup)group.id=state.editingGppGroup.id;
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
    const segment=kind==="groups"?"groups":"registry";
    const property=kind==="groups"?"group":"registry";
    const body={scope,...audit(`Restore GPP ${property} from revision ${revision}`),[property]:copy(item)};
    const data=await api(`/api/gpos/${state.current.guid}/preferences/${segment}/${id}`,{method:"PUT",body:JSON.stringify(body)});
    applyCurrent(data);toast(`${kind==="groups"?"Group":"Registry item"} restored from revision ${revision}`);
  }catch(error){showPersistentError(error.message)}
}

export function openGppRegistry(scope,registry=null,cloning=false){
  const f=$("#gpp-registry-form");f.reset();clearFormErrors(f);
  state.editingGppRegistry=registry&&!cloning?registry:null;
  registrySource=registry;
  f.scope.value=scope;
  $("#gpp-registry-dialog-title").textContent=cloning?"Clone registry":registry?"Edit registry":"Add registry";
  $("#gpp-values-list").innerHTML="";
  $("#gpp-ilt-registry-list").innerHTML="";
  $("#gpp-ilt-registry-preview").textContent="";
    if(registry){
      f.key.value=registry.key;
      f.hive.value=registry.hive||"HKEY_LOCAL_MACHINE";
      f.action.value=registry.action;
      addValueRow(registry.value||{name:"",value:"",registry_type:"",action:"create"});
      if(registry.ilt_filter&&registry.ilt_filter.items)registry.ilt_filter.items.forEach(item=>{
        if(typeof item==="string")addPredicateRow("gpp-ilt-registry-list","gpp-ilt-registry-preview",{unknown:true,raw:item});
        else addPredicateRow("gpp-ilt-registry-list","gpp-ilt-registry-preview",item);
      });
      f.reason.value=cloning?"Clone GPP registry":"Update GPP registry";
  }else{
      f.reason.value="Add GPP registry";
      addValueRow();
  }
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
  row.dataset.unknownAttrs="[]";
  if(value){row.querySelector('[data-field=name]').value=value.name;typeSel.value=value.registry_type||"";valueInput.value=Array.isArray(value.value)?value.value.join(";"):String(value.value);actionSel.value=value.action;defaultBox.checked=!!value.default;if(defaultBox.checked)row.querySelector('[data-field=name]').disabled=true;row.dataset.id=value.id||"";row.dataset.unknownAttrs=JSON.stringify(value.unknown_attrs||[])}
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
    const result={name,value,registry_type:type,action,default:isDefault,id:row.dataset.id||"",unknown_attrs:JSON.parse(row.dataset.unknownAttrs||"[]")};
    return result;
}

async function submitGppRegistry(event){
  event.preventDefault();
  if(event.submitter&&event.submitter.value==="cancel"){event.currentTarget.closest("dialog").close();return}
  const f=event.currentTarget,scope=f.scope.value;
  if($("#gpp-ilt-registry-list").querySelectorAll('.gpp-row[data-readonly="true"]').length&&!confirm("This item contains ILT predicates that cannot be edited in the browser. They will be preserved on save. Continue?"))return;
  const value=collectValue();
  if(!value){showFormErrors(f,{issues:[{message:"A registry value is required."}]});return}
  if(!value.default&&String(value.value).trim()&&!value.name.trim()&&value.registry_type){showFormErrors(f,{issues:[{message:"Each value with data must also have a name."}]});return}
  const commonIlt=collectIlt("gpp-ilt-registry-list");
  const badDword=(value.action!=="delete"&&(value.registry_type==="REG_DWORD"||value.registry_type==="REG_QWORD")&&!/^(?:0|[1-9][0-9]*)$/.test(value.value));
  if(badDword){showFormErrors(f,{issues:[{message:`${value.registry_type} value for "${value.name||"(default)"}" must be a non-negative decimal integer.`}]});return}
  const registry={key:f.key.value.trim(),hive:f.hive.value,action:f.action.value,value,ilt_filter:commonIlt};
  if(registrySource){if(registrySource.uid)registry.uid=registrySource.uid;if(registrySource.unknown_attrs)registry.unknown_attrs=registrySource.unknown_attrs;if(registrySource.unknown_children)registry.unknown_children=registrySource.unknown_children}
  if(state.editingGppRegistry)registry.id=state.editingGppRegistry.id;
  const path=state.editingGppRegistry?`/api/gpos/${state.current.guid}/preferences/registry/${state.editingGppRegistry.id}`:`/api/gpos/${state.current.guid}/preferences/registry`;
  try{const data=await api(path,{method:state.editingGppRegistry?"PUT":"POST",body:JSON.stringify({scope,...audit(f.reason.value),registry})});$("#gpp-registry-dialog").close();applyCurrent(data);toast("GPP registry saved")}catch(error){await handleFormFailure(f,error,{onCurrent:applyCurrent})}
}

export async function deleteGppRegistry(scope,id){
  const collection=(state.current.gpp_collections||[]).find(item=>item.scope===scope);
  const registry=(collection?.registry||[]).find(item=>item.id===id);
  if(!confirm(`Remove GPP registry item "${registry?.key||id}" from the ${scope} draft?\n\nIts value and item-level targeting will be removed in one new revision.`))return;
  try{const data=await api(`/api/gpos/${state.current.guid}/preferences/registry/${id}`,{method:"DELETE",body:JSON.stringify({scope,...audit("Remove GPP registry")})});applyCurrent(data);toast("GPP registry removed")}catch(error){showPersistentError(error.message)}
}

function addPredicateRow(listId,previewId,predicate=null){
  const list=$("#"+listId);
  const row=document.createElement("div");
  row.className="gpp-row";
  row.innerHTML=`<select data-field="type">${ILT_TYPES.map(t=>`<option value="${t}">${ILT_LABELS[t]}</option>`).join("")}</select><label><input type="checkbox" data-field="negate"> NOT</label><input data-field="value" placeholder="${ILT_HINTS.ou}"><button type="button" class="quiet">×</button>`;
  const typeSel=row.querySelector('[data-field=type]');
  const valueInput=row.querySelector('[data-field=value]');
  const negateBox=row.querySelector('[data-field=negate]');
  const update=()=>{valueInput.placeholder=ILT_HINTS[typeSel.value]||"";updateIltPreview(listId,previewId)};
  typeSel.onchange=update;
  valueInput.oninput=()=>updateIltPreview(listId,previewId);
  negateBox.onchange=()=>updateIltPreview(listId,previewId);
  if(predicate&&predicate.unknown){row.dataset.readonly="true";row.dataset.unknownPredicate=predicate.raw;typeSel.disabled=true;valueInput.disabled=true;negateBox.disabled=true;valueInput.value="[unsupported predicate]";row.title="Unknown ILT predicate — preserved on save, cannot be edited";const warn=document.createElement("span");warn.className="gpp-readonly-warn";warn.textContent="⚠ Unknown — preserved on save";row.appendChild(warn)}
  else if(predicate&&ILT_TYPES.includes(predicate.type)&&predicate.bool_op==="OR"){row.dataset.readonly="true";row.dataset.preservedPredicate=JSON.stringify(predicate);typeSel.disabled=true;valueInput.disabled=true;negateBox.disabled=true;valueInput.value=predicate.value;negateBox.checked=predicate.negate;row.title="ILT predicate with OR combination — preserved on save, cannot be edited in browser";const warn=document.createElement("span");warn.className="gpp-readonly-warn";warn.textContent="⚠ OR — preserved on save";row.appendChild(warn)}
  else if(predicate&&ILT_TYPES.includes(predicate.type)){typeSel.value=predicate.type;valueInput.value=predicate.value;negateBox.checked=predicate.negate;row.dataset.unknownAttrs=predicate.unknown_attrs?JSON.stringify(predicate.unknown_attrs):""}
  else if(predicate){row.dataset.readonly="true";row.dataset.unknownPredicate=predicate.raw||"";typeSel.disabled=true;valueInput.disabled=true;negateBox.disabled=true;valueInput.value=predicate.value;negateBox.checked=predicate.negate;row.title="Unsupported ILT predicate type — preserved on save, cannot be edited";const warn=document.createElement("span");warn.className="gpp-readonly-warn";warn.textContent="⚠ Unsupported — preserved on save";row.appendChild(warn)}
  update();
  const removeButton=row.querySelector("button");
  if(row.dataset.readonly==="true"){
    removeButton.disabled=true;
    removeButton.setAttribute("aria-label","Preserved imported predicate cannot be removed in the browser");
    removeButton.title="Read-only imported predicates are preserved and cannot be removed in the browser";
  }else{
    removeButton.setAttribute("aria-label","Remove ILT predicate");
    removeButton.onclick=()=>{row.remove();updateIltPreview(listId,previewId)};
  }
  list.appendChild(row);
}

function collectIlt(listId){
  const list=$("#"+listId);
  const rows=list.querySelectorAll(".gpp-row");
  const items=[...rows].map(row=>{
    if(row.dataset.readonly==="true"&&row.dataset.unknownPredicate)return row.dataset.unknownPredicate;
    if(row.dataset.readonly==="true"&&row.dataset.preservedPredicate){try{return JSON.parse(row.dataset.preservedPredicate)}catch{return null}}
    const type=row.querySelector('[data-field=type]').value;
    const negate=row.querySelector('[data-field=negate]').checked;
    const value=row.querySelector('[data-field=value]').value.trim();
    if(!value)return null;
    const result={type,negate,value,bool_op:"AND"};
    const ua=row.dataset.unknownAttrs;
    if(ua)try{result.unknown_attrs=JSON.parse(ua)}catch(error){void error}
    return result;
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
