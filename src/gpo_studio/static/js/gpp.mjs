import {state,$,$$,escapeHtml,applyPayload} from './state.mjs';
import {api,toast,audit,showPersistentError} from './api.mjs';
import {clearFormErrors,showFormErrors,handleFormFailure} from './errors.mjs';
import {renderAll,renderList} from './render.mjs';
import {captureMutation,resetEditor,beginMutation,isSameEditor,completeMutation,mutationFailed,endMutation} from './mutation-context.mjs';

// The authored predicate types: the same set as ilt.AUTHORABLE_PREDICATE_TYPES
// (tests/test_gpp_clone_restore.py holds them equal). An OS predicate is
// edited as class/version/type/edition/service pack.
export const ILT_TYPES=["ou","group","registry","ip_range","environment","wmi_query","os"];
const ILT_LABELS={ou:"OU",group:"Group",registry:"Registry",ip_range:"IP range",environment:"Environment",wmi_query:"WMI query",os:"Operating system"};
const ILT_HINTS={ou:"Distinguished name, e.g. OU=Servers,DC=studio,DC=local",group:"Group SID or name, e.g. S-1-5-32-544",registry:"Key\\ValueName path",ip_range:"CIDR (10.0.0.0/8) or range (10.0.0.1-10.0.0.99)",environment:"NAME=value",wmi_query:"WQL query (SELECT ... FROM ...)",os:"class/version/type/edition/sp, e.g. NT/WINTHRESHOLD/WS/NE/NE"};
const ILT_PREVIEW={ou:v=>"Member of "+v,group:v=>"Member of group "+v,registry:v=>"Registry "+v+" matches",ip_range:v=>"IP in "+v,environment:v=>"Environment "+v,wmi_query:v=>"WMI query: "+v,os:v=>"Operating system "+v};
const REG_TYPES=["REG_SZ","REG_EXPAND_SZ","REG_BINARY","REG_DWORD","REG_MULTI_SZ","REG_QWORD"];
const REG_VALUE_HINTS={REG_SZ:"Text value",REG_EXPAND_SZ:"Text with %VAR% expansion",REG_BINARY:"Hex bytes, e.g. 01FFA0",REG_DWORD:"Non-negative decimal integer",REG_MULTI_SZ:'JSON array of strings, e.g. ["first", "second"]',REG_QWORD:"Non-negative decimal integer"};
const REG_VALUE_ACTIONS=["create","replace","update","delete"];
const GROUP_ACTIONS=["add","replace","remove","update"];
function applyCurrent(data){if(!applyPayload(data))return false;renderAll();renderList();return true}


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
    // Editable only when its join is AND: the editor writes AND, so a save
    // never rewrites another join (DeepSeek final-r14 N1).
    const editable=!raw&&ILT_TYPES.includes(item.type)&&item.bool_op==="AND";
    const bool=raw?rawJoin(item):item.bool_op;
    return {id:ids[index]||"",item,raw,editable,bool};
  });
}

// The join of a raw predicate, read from its start tag as an XML parser reads
// attributes: either quote style, whitespace around "=" (Sol final-r15 B2: a
// 1.0.0-era bool='OR' read as AND). No bool is AND, the default the parser
// (ilt.py) and Windows apply (DeepSeek final-r16 N6). "?" when the tag cannot
// be read or the value holds an entity -- which the editor treats as a join
// that may regroup the expression.
export function rawJoin(text){
  const start=/^\s*<[^\s/>=]+((?:\s+[^\s/>=]+\s*=\s*(?:"[^"<]*"|'[^'<]*'))*)\s*\/?>/u.exec(String(text));
  if(!start)return "?";
  const attribute=/\s([^\s/>=]+)\s*=\s*(?:"([^"<]*)"|'([^'<]*)')/gu;
  for(let match=attribute.exec(start[1]);match;match=attribute.exec(start[1])){
    if(match[1]!=="bool")continue;
    const value=match[2]??match[3];
    return value.includes("&")?"?":value;
  }
  return "AND";
}

export function osCriteriaText(criteria){
  return criteria?`${criteria.os_class}/${criteria.version}/${criteria.product_type}/${criteria.edition}/${criteria.service_pack}`:"";
}

// "class/version/type/edition/sp" -> os_criteria, or null when it is not five
// parts. A part may be empty: a stored criterion can be, and a save must not
// force the operator to change it (DeepSeek final-r15 N3).
export function osCriteriaFromText(text){
  const parts=String(text).split("/").map(part=>part.trim());
  if(parts.length!==5)return null;
  const [os_class,version,product_type,edition,service_pack]=parts;
  return {os_class,version,product_type,edition,service_pack};
}

export function predicateEntry(row){
  if(row.readonly)return {id:row.id};
  const entry={type:row.type,negate:row.negate,value:row.type==="os"?"":row.value,bool_op:"AND"};
  if(row.type==="os")entry.os_criteria=osCriteriaFromText(row.value);
  if(row.id)entry.id=row.id;
  return entry;
}

// The expression the rows make, in order: each predicate joins what precedes it
// with its own AND/OR (the first one's join is unused).
export function iltExpression(parts){
  return parts.map((part,index)=>index===0?part.text:`${part.bool} ${part.text}`).join(" ");
}

// Moving a row changes how the expression groups whenever a join other than
// AND (OR, or one that could not be read) is in effect before or after the
// move (a join on the first row is unused).
export function moveChangesGrouping(bools,from,to){
  if(from===to)return false;
  const moved=[...bools];
  const [item]=moved.splice(from,1);
  moved.splice(to,0,item);
  const regroups=list=>list.slice(1).some(bool=>bool!=="AND");
  return regroups(bools)||regroups(moved);
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
  if(!note){note=document.createElement("p");note.className="gpp-note gpp-retained-note";note.setAttribute("role","note");(form.querySelector(":scope > .dialog-body")||form).appendChild(note)}
  const text=retainedSummary(item);
  note.textContent=text;note.hidden=!text;
}

export function openGppGroup(scope,group=null){
  const f=$("#gpp-group-form");resetEditor(f);f.reset();clearFormErrors(f);
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
  row.innerHTML=`<label class="gpp-field">Member SID<input data-field="sid" placeholder="SID (required)" maxlength="255" required></label><label class="gpp-field">Member name<input data-field="name" placeholder="Name" maxlength="255"></label><label class="gpp-field">Member action<select data-field="action">${GROUP_ACTIONS.map(a=>`<option value="${a}">${a}</option>`).join("")}</select></label><button type="button" class="quiet">×</button>`;
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
  const groupOsProblem=osProblem("gpp-ilt-list");
  if(groupOsProblem){showFormErrors(f,{issues:[{message:groupOsProblem}]});return}
  const partialMember=[...$("#gpp-members-list").querySelectorAll(".gpp-row")].find(row=>row.querySelector('[data-field=name]').value.trim()&&!row.querySelector('[data-field=sid]').value.trim());
  if(partialMember){showFormErrors(f,{issues:[{message:"Each member with a name must also have a SID."}]});return}
  const group={name:f.name.value.trim(),sid:f.sid.value.trim(),action:f.action.value,description:f.description.value.trim(),remove_all_users:f.remove_all_users.checked,remove_all_groups:f.remove_all_groups.checked,members:collectMembers(),ilt_filter:collectIlt("gpp-ilt-list")};
  const broadMembershipChange=group.action==="replace"||group.action==="remove"||group.remove_all_users||group.remove_all_groups||group.members.some(member=>member.action==="replace"||member.action==="remove");
  if(broadMembershipChange&&!confirm(`Save broad membership change for group "${group.name}"?\n\nAction: ${group.action}. Remove all users: ${group.remove_all_users?'yes':'no'}. Remove all groups: ${group.remove_all_groups?'yes':'no'}. Review the required change reason before continuing.`))return;
  const path=state.editingGppGroup?`/api/gpos/${state.current.guid}/preferences/groups/${state.editingGppGroup.id}`:`/api/gpos/${state.current.guid}/preferences/groups`;
  const context=beginMutation(f);if(!context)return;clearFormErrors(f);
  try{const data=await api(path,{method:state.editingGppGroup?"PUT":"POST",body:JSON.stringify({scope,...audit(f.reason.value),expected_revision:context.revision,group})});completeMutation(context,data,{apply:applyCurrent,renderList});toast("GPP group saved")}catch(error){await mutationFailed(context,error,(form,err)=>handleFormFailure(form,err,{onCurrent:applyCurrent,fieldNames:context.fieldNames,isActive:()=>isSameEditor(context)}))}finally{endMutation(context)}
}

export async function deleteGppGroup(scope,id){
  const context=captureMutation();
  const collection=(state.current.gpp_collections||[]).find(item=>item.scope===scope);
  const group=(collection?.groups||[]).find(item=>item.id===id);
  if(!confirm(`Remove GPP group "${group?.name||id}" from the ${scope} draft?\n\nIts action, members, and item-level targeting will be removed in one new revision.`))return;
  try{const data=await api(`/api/gpos/${context.guid}/preferences/groups/${id}`,{method:"DELETE",body:JSON.stringify({scope,...audit("Remove GPP group")})});completeMutation(context,data,{apply:applyCurrent,renderList});toast("GPP group removed")}catch(error){await mutationFailed(context,error)}
}

export async function moveGppItem(scope,kind,id,offset){
  const context=captureMutation();
  const collection=(state.current.gpp_collections||[]).find(item=>item.scope===scope);
  const items=collection?.[kind]||[];
  const orderedIds=moveGppItemIds(items,id,offset);
  if(!orderedIds)return;
  try{
    const label=kind==="groups"?"groups":"registry items";
    const data=await api(`/api/gpos/${context.guid}/preferences/reorder`,{method:"POST",body:JSON.stringify({scope,kind,ordered_ids:orderedIds,...audit(`Reorder GPP ${label}`)})});
    completeMutation(context,data,{apply:applyCurrent,renderList});toast(`GPP ${label} reordered`);
  }catch(error){await mutationFailed(context,error)}
}

export async function cloneGppItem(scope,kind,item,existingNames=[]){
  const context=captureMutation();
  const label=kind==="groups"?"group":"registry item";
  try{
    const request=gppCloneRequest(context.guid,scope,kind,item,existingNames);
    const data=await api(request.path,{method:"POST",body:JSON.stringify({...request.body,...audit(`Clone GPP ${label}`)})});
    const applied=completeMutation(context,data,{apply:applyCurrent,renderList});toast(`GPP ${label} cloned`);
    if(!applied||document.querySelector("dialog[open]"))return;
    // Open the copy (the last item) for editing.
    const collection=(state.current.gpp_collections||[]).find(entry=>entry.scope===scope);
    const items=collection?.[kind]||[];
    const copied=items[items.length-1];
    if(copied){if(kind==="groups")openGppGroup(scope,copied);else openGppRegistry(scope,copied)}
  }catch(error){await mutationFailed(context,error)}
}

export async function restoreGppItem(scope,kind,id){
  const context=captureMutation();
  const revisionText=prompt("Revision containing the item version to restore:");
  if(revisionText===null)return;
  const revision=Number(revisionText);
  if(!Number.isInteger(revision)||revision<1){toast("Enter a valid revision number");return}
  try{
    const historical=await api(`/api/gpos/${context.guid}/revisions/${revision}`);
    if(state.current?.guid!==context.guid||state.current.revision!==context.revision){showPersistentError("The selected policy changed. Start the item restore again from its current revision.");return}
    const item=findRevisionGppItem(historical.snapshot,scope,kind,id);
    if(!item){toast(`This item does not exist in revision ${revision}`);return}
    const currentCollection=(state.current.gpp_collections||[]).find(collection=>collection.scope===scope);
    const current=(currentCollection?.[kind]||[]).find(entry=>entry.id===id);
    const label=kind==="groups"?(current?.name||item.name):(current?.key||item.key);
    if(!confirm(`Restore ${kind==="groups"?"group":"registry item"} "${label}" from revision ${revision}?\n\nOnly this item will be overwritten. The restore is appended as a new revision; all other current policy content remains unchanged.`))return;
    const property=kind==="groups"?"group":"registry";
    const request=gppRestoreRequest(context.guid,scope,kind,id,revision);
    const data=await api(request.path,{method:"POST",body:JSON.stringify({...request.body,...audit(`Restore GPP ${property} from revision ${revision}`)})});
    completeMutation(context,data,{apply:applyCurrent,renderList});toast(`${kind==="groups"?"Group":"Registry item"} restored from revision ${revision}`);
  }catch(error){await mutationFailed(context,error)}
}

export function openGppRegistry(scope,registry=null){
  const f=$("#gpp-registry-form");resetEditor(f);f.reset();clearFormErrors(f);
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
  row.innerHTML=`<label class="gpp-field">Registry value name<input data-field="name" placeholder="Value name (empty = key-only)" maxlength="255"></label><label class="gpp-field">Registry value type<select data-field="type"><option value="">(key-only)</option>${REG_TYPES.map(t=>`<option value="${t}">${t}</option>`).join("")}</select></label><label class="gpp-field">Registry value data<input data-field="value" aria-label="Registry value data" placeholder="Value"></label><label class="gpp-field">Registry value action<select data-field="action">${REG_VALUE_ACTIONS.map(a=>`<option value="${a}">${a}</option>`).join("")}</select></label><label title="Configure the key's default value"><input type="checkbox" data-field="default"> Default</label><button type="button" class="quiet">×</button>`;
  const valueHelp=document.createElement("small");
  valueHelp.id="gpp-registry-value-help";valueHelp.dataset.valueHelp="";valueHelp.textContent='REG_MULTI_SZ uses a JSON array of strings, e.g. ["first", "second"]. Spaces, empty strings and semicolons are kept in the workspace. Use [] for an empty list.';
  row.appendChild(valueHelp);
  const typeSel=row.querySelector('[data-field=type]');
  const valueInput=row.querySelector('[data-field=value]');
  const actionSel=row.querySelector('[data-field=action]');
  const defaultBox=row.querySelector('[data-field=default]');
  defaultBox.onchange=()=>{
    if(defaultBox.checked){row.querySelector('[data-field=name]').value="";row.querySelector('[data-field=name]').disabled=true}
    else{row.querySelector('[data-field=name]').disabled=false}
  };
  typeSel.onchange=()=>{if(!typeSel.value&&!defaultBox.checked)valueInput.value="";if(typeSel.value==="REG_MULTI_SZ"&&!valueInput.value)valueInput.value="[]";valueHelp.hidden=typeSel.value!=="REG_MULTI_SZ";const descriptions=(valueInput.getAttribute("aria-describedby")||"").split(/\s+/).filter(id=>id&&id!==valueHelp.id);if(!valueHelp.hidden)descriptions.push(valueHelp.id);if(descriptions.length)valueInput.setAttribute("aria-describedby",descriptions.join(" "));else valueInput.removeAttribute("aria-describedby");valueInput.placeholder=typeSel.value?REG_VALUE_HINTS[typeSel.value]||"Value":defaultBox.checked?"Default value":"Key-only entry";valueInput.disabled=actionSel.value==="delete"||(!typeSel.value&&!defaultBox.checked)};
  actionSel.onchange=()=>{valueInput.disabled=actionSel.value==="delete"||(!typeSel.value&&!defaultBox.checked)};
  if(value){row.querySelector('[data-field=name]').value=value.name;typeSel.value=value.registry_type||"";valueInput.value=Array.isArray(value.value)?JSON.stringify(value.value):String(value.value);actionSel.value=value.action;defaultBox.checked=!!value.default;if(defaultBox.checked)row.querySelector('[data-field=name]').disabled=true}
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
    else if(type==="REG_MULTI_SZ"){
      try{value=JSON.parse(raw)}catch{throw new Error('REG_MULTI_SZ must be a JSON array of strings, for example ["first", "second"].')}
      if(!Array.isArray(value)||value.some(item=>typeof item!=="string"))throw new Error("REG_MULTI_SZ must be a JSON array containing only strings.");
    }
    else if(type==="REG_DWORD"||type==="REG_QWORD")value=raw.trim();
    else value=raw;
    return {name,value,registry_type:type,action,default:isDefault};
}

async function submitGppRegistry(event){
  event.preventDefault();
  if(event.submitter&&event.submitter.value==="cancel"){event.currentTarget.closest("dialog").close();return}
  const f=event.currentTarget,scope=f.scope.value;
  let value;
  try{value=collectValue()}catch(error){showFormErrors(f,{issues:[{path:"value",message:error.message}]});return}
  if(!value){showFormErrors(f,{issues:[{message:"A registry value is required."}]});return}
  if(!value.default&&String(value.value).trim()&&!value.name.trim()&&value.registry_type){showFormErrors(f,{issues:[{message:"Each value with data must also have a name."}]});return}
  const registryOsProblem=osProblem("gpp-ilt-registry-list");
  if(registryOsProblem){showFormErrors(f,{issues:[{message:registryOsProblem}]});return}
  const commonIlt=collectIlt("gpp-ilt-registry-list");
  const badDword=(value.action!=="delete"&&(value.registry_type==="REG_DWORD"||value.registry_type==="REG_QWORD")&&!/^(?:0|[1-9][0-9]*)$/.test(value.value));
  if(badDword){showFormErrors(f,{issues:[{message:`${value.registry_type} value for "${value.name||"(default)"}" must be a non-negative decimal integer.`}]});return}
  const registry={key:f.key.value.trim(),hive:f.hive.value,action:f.action.value,value,ilt_filter:commonIlt};
  const path=state.editingGppRegistry?`/api/gpos/${state.current.guid}/preferences/registry/${state.editingGppRegistry.id}`:`/api/gpos/${state.current.guid}/preferences/registry`;
  const context=beginMutation(f);if(!context)return;clearFormErrors(f);
  try{const data=await api(path,{method:state.editingGppRegistry?"PUT":"POST",body:JSON.stringify({scope,...audit(f.reason.value),expected_revision:context.revision,registry})});completeMutation(context,data,{apply:applyCurrent,renderList});toast("GPP registry saved")}catch(error){await mutationFailed(context,error,(form,err)=>handleFormFailure(form,err,{onCurrent:applyCurrent,fieldNames:context.fieldNames,isActive:()=>isSameEditor(context)}))}finally{endMutation(context)}
}

export async function deleteGppRegistry(scope,id){
  const context=captureMutation();
  const collection=(state.current.gpp_collections||[]).find(item=>item.scope===scope);
  const registry=(collection?.registry||[]).find(item=>item.id===id);
  if(!confirm(`Remove GPP registry item "${registry?.key||id}" from the ${scope} draft?\n\nIts value and item-level targeting will be removed in one new revision.`))return;
  try{const data=await api(`/api/gpos/${context.guid}/preferences/registry/${id}`,{method:"DELETE",body:JSON.stringify({scope,...audit("Remove GPP registry")})});completeMutation(context,data,{apply:applyCurrent,renderList});toast("GPP registry removed")}catch(error){await mutationFailed(context,error)}
}

function addPredicateRow(listId,previewId,row=null){
  const list=$("#"+listId);
  const el=document.createElement("div");
  el.className="gpp-row";
  el.innerHTML=`<span class="gpp-connector mono" aria-hidden="true"></span><label class="gpp-field">Targeting predicate type<select data-field="type">${ILT_TYPES.map(t=>`<option value="${t}">${ILT_LABELS[t]}</option>`).join("")}</select></label><label><input type="checkbox" data-field="negate"> NOT</label><label class="gpp-field">Targeting predicate value<input data-field="value" placeholder="${ILT_HINTS.ou}"></label><button type="button" class="quiet" data-move="-1">↑</button><button type="button" class="quiet" data-move="1">↓</button><button type="button" class="quiet" data-remove>×</button>`;
  const typeSel=el.querySelector('[data-field=type]');
  const valueInput=el.querySelector('[data-field=value]');
  const negateBox=el.querySelector('[data-field=negate]');
  const update=()=>{valueInput.placeholder=ILT_HINTS[typeSel.value]||"";updateIltPreview(listId,previewId)};
  typeSel.onchange=update;
  valueInput.oninput=()=>updateIltPreview(listId,previewId);
  negateBox.onchange=()=>updateIltPreview(listId,previewId);
  el.dataset.bool=row?.bool||"AND";
  if(row)el.dataset.id=row.id||"";
  const predicate=row?.item;
  const warn=text=>{const span=document.createElement("span");span.className="gpp-readonly-warn";span.textContent=text;el.insertBefore(span,el.querySelector("[data-move]"))};
  const readonly=(text,title)=>{el.dataset.readonly="true";typeSel.disabled=true;valueInput.disabled=true;negateBox.disabled=true;el.title=title;warn(text)};
  if(row&&row.raw){valueInput.value="[unsupported predicate]";el.dataset.label=`imported ${(/^<\s*([\w:.-]+)/.exec(predicate)||[])[1]||"predicate"}`;readonly("⚠ Unknown — kept on save","Unknown predicate: kept as imported; it can be moved or removed, not edited")}
  else if(row&&!row.editable&&ILT_TYPES.includes(predicate.type)){typeSel.value=predicate.type;valueInput.value=predicate.type==="os"?osCriteriaText(predicate.os_criteria):predicate.value;negateBox.checked=predicate.negate;readonly(`⚠ ${predicate.bool_op||"?"} — kept on save`,`Predicate joined with ${predicate.bool_op||"an unknown operator"}: kept as imported; it can be moved or removed, not edited here`)}
  else if(row&&!row.editable){const unsupportedOption=document.createElement("option");unsupportedOption.value=predicate.type;unsupportedOption.textContent=predicate.type;typeSel.appendChild(unsupportedOption);typeSel.value=predicate.type;valueInput.value=predicate.value;negateBox.checked=predicate.negate;readonly("Unsupported — kept on save","Unsupported predicate type: kept as imported; it can be moved or removed, not edited")}
  else if(row){typeSel.value=predicate.type;valueInput.value=predicate.type==="os"?osCriteriaText(predicate.os_criteria):predicate.value;negateBox.checked=predicate.negate;const operatorWarning=iltOperatorWarning(predicate);if(operatorWarning){el.title=operatorWarning;warn(operatorWarning)}}
  update();
  const removeButton=el.querySelector("[data-remove]");
  removeButton.setAttribute("aria-label",el.dataset.readonly==="true"?"Remove imported ILT predicate":"Remove ILT predicate");
  removeButton.onclick=()=>{el.remove();updateIltPreview(listId,previewId)};
  el.querySelectorAll("[data-move]").forEach(button=>{
    const offset=Number(button.dataset.move);
    button.setAttribute("aria-label",offset<0?"Move ILT predicate earlier":"Move ILT predicate later");
    button.onclick=()=>{
      const rows=[...list.querySelectorAll(".gpp-row")];
      const from=rows.indexOf(el),to=from+offset;
      if(to<0||to>=rows.length)return;
      const regroups=moveChangesGrouping(rows.map(r=>r.dataset.bool||"AND"),from,to);
      if(offset<0)rows[to].before(el);else rows[to].after(el);
      updateIltPreview(listId,previewId,regroups?"Moving this predicate changed how AND and OR group the expression. Check the expression below before saving.":"");
    };
  });
  list.appendChild(el);
}

function osProblem(listId){
  const bad=[...$("#"+listId).querySelectorAll(".gpp-row")].find(row=>{
    if(row.dataset.readonly==="true"||row.querySelector('[data-field=type]').value!=="os")return false;
    const criteria=osCriteriaFromText(row.querySelector('[data-field=value]').value);
    // Five parts; one may be empty as stored, but not all (DeepSeek final-r16 N2).
    return !criteria||!Object.values(criteria).some(Boolean);
  });
  return bad?"An operating system predicate needs class/version/type/edition/service pack, at least one of them set, e.g. NT/WINTHRESHOLD/WS/NE/NE.":"";
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

function updateIltPreview(listId,previewId,moveWarning=null){
  const rows=[...$("#"+listId).querySelectorAll(".gpp-row")];
  const parts=[];
  rows.forEach((row,index)=>{
    const bool=row.dataset.bool||"AND";
    const connector=row.querySelector(".gpp-connector");
    if(connector){connector.textContent=index===0?"":bool;connector.title=bool==="?"?"This imported predicate's join could not be read":""}
    const type=row.querySelector('[data-field=type]').value;
    const negate=row.querySelector('[data-field=negate]').checked;
    const value=row.querySelector('[data-field=value]').value.trim();
    let text;
    if(row.dataset.label)text=`[${row.dataset.label}]`;
    else if(!value)return;
    else text=(ILT_PREVIEW[type]||(v=>`${type} ${v}`))(value);
    if(row.dataset.readonly==="true"&&!row.dataset.label)text=`[imported: ${text}]`;
    if(negate)text="NOT "+text;
    parts.push({bool,text});
  });
  const preview=$("#"+previewId);
  preview.textContent=iltExpression(parts);
  let warning=preview.parentElement?.querySelector(`[data-move-warning="${previewId}"]`);
  if(moveWarning!==null){
    if(!warning&&moveWarning){warning=document.createElement("p");warning.className="gpp-note gpp-move-warning";warning.setAttribute("role","status");warning.dataset.moveWarning=previewId;preview.insertAdjacentElement("afterend",warning)}
    if(warning){warning.textContent=moveWarning;warning.hidden=!moveWarning}
  }
}
