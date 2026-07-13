import {state,$,$$,escapeHtml} from './state.mjs';
import {api,toast,audit} from './api.mjs';
import {renderAll,renderList} from './render.mjs';

const ILT_TYPES=["ou","group","registry","ip_range","environment","wmi_query"];
const ILT_LABELS={ou:"OU",group:"Group",registry:"Registry",ip_range:"IP range",environment:"Environment",wmi_query:"WMI query"};
const ILT_HINTS={ou:"Distinguished name, e.g. OU=Servers,DC=studio,DC=local",group:"Group SID or name, e.g. S-1-5-32-544",registry:"Key\\ValueName path",ip_range:"CIDR (10.0.0.0/8) or range (10.0.0.1-10.0.0.99)",environment:"NAME=value",wmi_query:"WQL query (SELECT ... FROM ...)"};
const ILT_PREVIEW={ou:v=>"Member of "+v,group:v=>"Member of group "+v,registry:v=>"Registry "+v+" matches",ip_range:v=>"IP in "+v,environment:v=>"Environment "+v,wmi_query:v=>"WMI query: "+v};
const REG_TYPES=["REG_SZ","REG_EXPAND_SZ","REG_BINARY","REG_DWORD","REG_MULTI_SZ","REG_QWORD"];
const REG_VALUE_HINTS={REG_SZ:"Text value",REG_EXPAND_SZ:"Text with %VAR% expansion",REG_BINARY:"Hex bytes, e.g. 01FFA0",REG_DWORD:"Non-negative decimal integer",REG_MULTI_SZ:"Semicolon or newline-separated values",REG_QWORD:"Non-negative decimal integer"};
const REG_VALUE_ACTIONS=["create","replace","update","delete"];
const GROUP_ACTIONS=["add","replace","remove","update"];

export function renderGpp(){
  const g=state.current;if(!g)return;
  const scope=state.gppScope;
  const collection=(g.gpp_collections||[]).find(c=>c.scope===scope);
  const groups=collection?collection.groups:[];
  const registry=collection?collection.registry:[];
  $("#gpp-groups-table").innerHTML=groups.map(grp=>{
    const memberNames=grp.members.map(m=>escapeHtml(m.name||m.sid)).join(", ")||"—";
    const iltCount=grp.ilt_filter&&grp.ilt_filter.items?grp.ilt_filter.items.filter(i=>typeof i==="object").length:0;
    const ilt=iltCount?`ILT: ${iltCount} predicate${iltCount===1?'':'s'}`:"—";
    return `<tr><td class="mono">${escapeHtml(grp.action)}</td><td>${escapeHtml(grp.name)}</td><td class="mono">${escapeHtml(grp.sid)||"—"}</td><td><div class="truncate" title="${memberNames}">${memberNames}</div></td><td>${ilt}</td><td><div class="row-actions"><button data-edit-gpp-group="${escapeHtml(grp.id)}">Edit</button><button data-delete-gpp-group="${escapeHtml(grp.id)}">×</button></div></td></tr>`;
  }).join("");
  $("#gpp-groups-empty").hidden=groups.length>0;
  $$("[data-edit-gpp-group]").forEach(el=>el.onclick=()=>{const grp=groups.find(g=>g.id===el.dataset.editGppGroup);openGppGroup(scope,grp)});
  $$("[data-delete-gpp-group]").forEach(el=>el.onclick=()=>deleteGppGroup(scope,el.dataset.deleteGppGroup));
  $("#gpp-registry-table").innerHTML=registry.map(reg=>{
    const values=reg.values.map(v=>`${escapeHtml(v.name)}=${escapeHtml(formatRegValue(v))}`).join(", ")||"—";
    const iltCount=reg.ilt_filter&&reg.ilt_filter.items?reg.ilt_filter.items.filter(i=>typeof i==="object").length:(reg.values||[]).reduce((n,v)=>n+(v.ilt_filter&&v.ilt_filter.items?v.ilt_filter.items.filter(i=>typeof i==="object").length:0),0);
    const ilt=iltCount?`ILT: ${iltCount} predicate${iltCount===1?'':'s'}`:"—";
    return `<tr><td class="mono">${escapeHtml(reg.action)}</td><td><div class="mono truncate" title="${escapeHtml(reg.key)}">${escapeHtml(reg.key)}</div></td><td><div class="truncate" title="${escapeHtml(values)}">${escapeHtml(values)}</div></td><td>${ilt}</td><td><div class="row-actions"><button data-edit-gpp-registry="${escapeHtml(reg.id)}">Edit</button><button data-delete-gpp-registry="${escapeHtml(reg.id)}">×</button></div></td></tr>`;
  }).join("");
  $("#gpp-registry-empty").hidden=registry.length>0;
  $$("[data-edit-gpp-registry]").forEach(el=>el.onclick=()=>{const reg=registry.find(r=>r.id===el.dataset.editGppRegistry);openGppRegistry(scope,reg)});
  $$("[data-delete-gpp-registry]").forEach(el=>el.onclick=()=>deleteGppRegistry(scope,el.dataset.deleteGppRegistry));
}

function formatRegValue(v){
  if(v.action==="delete")return "delete";
  if(Array.isArray(v.value))return v.value.join(";");
  return String(v.value);
}

export function initGpp(){
  $$(".gpp-scope-row .chip").forEach(chip=>chip.onclick=()=>{$$(".gpp-scope-row .chip").forEach(x=>x.classList.toggle("active",x===chip));state.gppScope=chip.dataset.gppScope;renderGpp()});
  $("#add-gpp-group").onclick=()=>openGppGroup(state.gppScope);
  $("#add-gpp-registry").onclick=()=>openGppRegistry(state.gppScope);
  $("#gpp-add-member").onclick=()=>addMemberRow();
  $("#gpp-add-predicate").onclick=()=>addPredicateRow("gpp-ilt-list","gpp-ilt-preview");
  $("#gpp-add-value").onclick=()=>addValueRow();
  $("#gpp-add-ilt-registry").onclick=()=>addPredicateRow("gpp-ilt-registry-list","gpp-ilt-registry-preview");
  $("#gpp-group-form").onsubmit=submitGppGroup;
  $("#gpp-registry-form").onsubmit=submitGppRegistry;
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
    if(group.ilt_filter&&group.ilt_filter.items)group.ilt_filter.items.forEach(item=>{
      if(typeof item==="string")addPredicateRow("gpp-ilt-list","gpp-ilt-preview",{unknown:true,raw:item});
      else addPredicateRow("gpp-ilt-list","gpp-ilt-preview",item);
    });
    f.reason.value="Update GPP group";
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
  row.querySelector("button").onclick=()=>row.remove();
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
  if(state.editingGppGroup){group.id=state.editingGppGroup.id;group.unknown_attrs=state.editingGppGroup.unknown_attrs||[];group.unknown_props_attrs=state.editingGppGroup.unknown_props_attrs||[];group.unknown_children=state.editingGppGroup.unknown_children||[]}
  const path=state.editingGppGroup?`/api/gpos/${state.current.guid}/preferences/groups/${state.editingGppGroup.id}`:`/api/gpos/${state.current.guid}/preferences/groups`;
  try{const data=await api(path,{method:state.editingGppGroup?"PUT":"POST",body:JSON.stringify({scope,...audit(f.reason.value),group})});$("#gpp-group-dialog").close();state.current=data.gpo;state.validation=data.validation;state.policyHash=data.policy_semantic_sha256||"";renderAll();renderList();toast("GPP group saved")}catch(error){showFormErrors(f,error)}
}

export async function deleteGppGroup(scope,id){
  if(!confirm("Remove this GPP group from the draft?"))return;
  try{const data=await api(`/api/gpos/${state.current.guid}/preferences/groups/${id}`,{method:"DELETE",body:JSON.stringify({scope,...audit("Remove GPP group")})});state.current=data.gpo;state.validation=data.validation;state.policyHash=data.policy_semantic_sha256||"";renderAll();renderList();toast("GPP group removed")}catch(error){toast(error.message)}
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
      (registry.values||[]).forEach(v=>addValueRow(v));
      const firstIlt=(registry.values||[]).find(v=>v.ilt_filter);
      if(firstIlt&&firstIlt.ilt_filter&&firstIlt.ilt_filter.items)firstIlt.ilt_filter.items.forEach(item=>{
        if(typeof item==="string")addPredicateRow("gpp-ilt-registry-list","gpp-ilt-registry-preview",{unknown:true,raw:item});
        else addPredicateRow("gpp-ilt-registry-list","gpp-ilt-registry-preview",item);
      });
      f.reason.value="Update GPP registry";
  }else{
    f.reason.value="Add GPP registry";
  }
  updateIltPreview("gpp-ilt-registry-list","gpp-ilt-registry-preview");
  $("#gpp-registry-dialog").showModal();
}

function addValueRow(value=null){
  const list=$("#gpp-values-list");
  const row=document.createElement("div");
  row.className="gpp-row";
  row.innerHTML=`<input data-field="name" placeholder="Value name (required)" maxlength="255" required><select data-field="type">${REG_TYPES.map(t=>`<option value="${t}">${t}</option>`).join("")}</select><input data-field="value" placeholder="Value"><select data-field="action">${REG_VALUE_ACTIONS.map(a=>`<option value="${a}">${a}</option>`).join("")}</select><button type="button" class="quiet">×</button>`;
  const typeSel=row.querySelector('[data-field=type]');
  const valueInput=row.querySelector('[data-field=value]');
  const actionSel=row.querySelector('[data-field=action]');
  typeSel.onchange=()=>{valueInput.placeholder=REG_VALUE_HINTS[typeSel.value];valueInput.disabled=actionSel.value==="delete"};
  actionSel.onchange=()=>{valueInput.disabled=actionSel.value==="delete"};
  row.dataset.unknownAttrs="[]";
  if(value){row.querySelector('[data-field=name]').value=value.name;typeSel.value=value.registry_type;valueInput.value=Array.isArray(value.value)?value.value.join(";"):String(value.value);actionSel.value=value.action;row.dataset.id=value.id||"";row.dataset.unknownAttrs=JSON.stringify(value.unknown_attrs||[]);row.dataset.iltFilter=value.ilt_filter?JSON.stringify(value.ilt_filter):"";row.dataset.unknownElemAttrs=JSON.stringify(value.unknown_elem_attrs||[]);row.dataset.unknownChildren=JSON.stringify(value.unknown_children||[])}
  valueInput.disabled=actionSel.value==="delete";
  typeSel.onchange();
  row.querySelector("button").onclick=()=>row.remove();
  list.appendChild(row);
}

function collectValues(){
  return [...$("#gpp-values-list").querySelectorAll(".gpp-row")].map(row=>{
    const name=row.querySelector('[data-field=name]').value.trim();
    const type=row.querySelector('[data-field=type]').value;
    const raw=row.querySelector('[data-field=value]').value;
    const action=row.querySelector('[data-field=action]').value;
    let value;
    if(action==="delete")value="";
    else if(type==="REG_MULTI_SZ")value=raw.split(/[\r\n;]+/).map(s=>s.trim()).filter(s=>s.length>0);
    else if(type==="REG_DWORD"||type==="REG_QWORD")value=raw.trim();
    else value=raw;
    const result={name,value,registry_type:type,action,id:row.dataset.id||"",unknown_attrs:JSON.parse(row.dataset.unknownAttrs||"[]")};
    if(row.dataset.iltFilter)result.ilt_filter=JSON.parse(row.dataset.iltFilter);
    if(row.dataset.unknownElemAttrs)result.unknown_elem_attrs=JSON.parse(row.dataset.unknownElemAttrs);
    if(row.dataset.unknownChildren)result.unknown_children=JSON.parse(row.dataset.unknownChildren);
    return result;
  }).filter(v=>v.name);
}

async function submitGppRegistry(event){
  event.preventDefault();
  if(event.submitter&&event.submitter.value==="cancel"){event.currentTarget.closest("dialog").close();return}
  const f=event.currentTarget,scope=f.scope.value;
  if($("#gpp-ilt-registry-list").querySelectorAll('.gpp-row[data-readonly="true"]').length&&!confirm("This item contains ILT predicates that cannot be edited in the browser. They will be preserved on save. Continue?"))return;
  const partialValue=[...$("#gpp-values-list").querySelectorAll(".gpp-row")].find(row=>row.querySelector('[data-field=value]').value.trim()&&!row.querySelector('[data-field=name]').value.trim());
  if(partialValue){showFormErrors(f,{issues:[{message:"Each value with data must also have a name."}]});return}
  const values=collectValues();
  const badDword=values.find(v=>v.action!=="delete"&&(v.registry_type==="REG_DWORD"||v.registry_type==="REG_QWORD")&&!/^(?:0|[1-9][0-9]*)$/.test(v.value));
  if(badDword){showFormErrors(f,{issues:[{message:`${badDword.registry_type} value for "${badDword.name}" must be a non-negative decimal integer.`}]});return}
  const registry={key:f.key.value.trim(),hive:f.hive.value,action:f.action.value,values,ilt_filter:collectIlt("gpp-ilt-registry-list")};
  if(state.editingGppRegistry){registry.id=state.editingGppRegistry.id}
  const path=state.editingGppRegistry?`/api/gpos/${state.current.guid}/preferences/registry/${state.editingGppRegistry.id}`:`/api/gpos/${state.current.guid}/preferences/registry`;
  try{const data=await api(path,{method:state.editingGppRegistry?"PUT":"POST",body:JSON.stringify({scope,...audit(f.reason.value),registry})});$("#gpp-registry-dialog").close();state.current=data.gpo;state.validation=data.validation;state.policyHash=data.policy_semantic_sha256||"";renderAll();renderList();toast("GPP registry saved")}catch(error){showFormErrors(f,error)}
}

export async function deleteGppRegistry(scope,id){
  if(!confirm("Remove this GPP registry item from the draft?"))return;
  try{const data=await api(`/api/gpos/${state.current.guid}/preferences/registry/${id}`,{method:"DELETE",body:JSON.stringify({scope,...audit("Remove GPP registry")})});state.current=data.gpo;state.validation=data.validation;state.policyHash=data.policy_semantic_sha256||"";renderAll();renderList();toast("GPP registry removed")}catch(error){toast(error.message)}
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
  row.querySelector("button").onclick=()=>{row.remove();updateIltPreview(listId,previewId)};
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
    if(ua)try{result.unknown_attrs=JSON.parse(ua)}catch{}
    return result;
  }).filter(item=>item!==null);
  if(!items.length)return null;
  return {items};
}

function updateIltPreview(listId,previewId){
  const rows=$("#"+listId).querySelectorAll(".gpp-row");
  const parts=[...rows].filter(row=>row.dataset.readonly!=="true").map(row=>{
    const type=row.querySelector('[data-field=type]').value;
    const negate=row.querySelector('[data-field=negate]').checked;
    const value=row.querySelector('[data-field=value]').value.trim();
    if(!value)return "";
    let text=ILT_PREVIEW[type](value);
    if(negate)text="NOT "+text;
    return text;
  }).filter(Boolean);
  $("#"+previewId).textContent=parts.length?parts.join(" AND "):"";
}

function clearFormErrors(form){form.querySelectorAll(".form-error").forEach(el=>el.remove())}
function showFormErrors(form,error){clearFormErrors(form);if(error.issues&&error.issues.length){const ref=form.firstChild;error.issues.forEach(i=>{const div=document.createElement("div");div.className="form-error";div.textContent=i.message||i.msg||JSON.stringify(i);form.insertBefore(div,ref)})}else{toast(error.message)}}
