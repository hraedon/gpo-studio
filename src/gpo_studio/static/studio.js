const state={gpos:[],current:null,validation:[],side:"all",editingSetting:null,editingLink:null};
const $=selector=>document.querySelector(selector);
const $$=selector=>[...document.querySelectorAll(selector)];
const escapeHtml=value=>String(value??"").replace(/[&<>'"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));

async function api(path,options={}){
  const response=await fetch(path,{headers:{"Content-Type":"application/json",...(options.headers||{})},...options});
  const type=response.headers.get("content-type")||"";
  const payload=type.includes("json")?await response.json():await response.text();
  if(!response.ok){throw new Error(payload?.error?.message||payload?.detail||`Request failed (${response.status})`)}
  return payload;
}
function toast(message){const el=$("#toast");el.textContent=message;el.classList.add("show");setTimeout(()=>el.classList.remove("show"),2600)}
function audit(reason){return{actor:"local-operator",reason,expected_revision:state.current.revision}}

async function loadList(selectGuid){
  const data=await api("/api/gpos");state.gpos=data.items;renderList();
  const guid=selectGuid||state.current?.guid||state.gpos[0]?.guid;
  if(guid)await selectGpo(guid);else showEmpty();
}
function renderList(){
  const query=$("#search").value.toLowerCase();
  $("#gpo-list").innerHTML=state.gpos.filter(g=>g.name.toLowerCase().includes(query)).map(g=>`<button class="gpo-item ${state.current?.guid===g.guid?"active":""}" data-guid="${g.guid}"><strong>${escapeHtml(g.name)}</strong><small>${g.status} · r${g.revision}</small></button>`).join("");
  $$(".gpo-item").forEach(el=>el.onclick=()=>selectGpo(el.dataset.guid));
}
function showEmpty(){$("#empty").hidden=false;$("#workspace").hidden=true;$("#top-actions").hidden=true;$("#title").textContent="Choose a policy"}
async function selectGpo(guid){
  const data=await api(`/api/gpos/${guid}`);state.current=data.gpo;state.validation=data.validation;
  $("#empty").hidden=true;$("#workspace").hidden=false;$("#top-actions").hidden=false;
  renderAll();renderList();
}
function renderAll(){
  const g=state.current;$("#title").textContent=g.name;$("#revision").textContent=`Revision ${g.revision}`;
  $("#plan").href=`/api/gpos/${g.guid}/plan.ps1`;$("#export").href=`/api/gpos/${g.guid}/export.zip`;
  $("#setting-count").textContent=g.settings.length;$("#link-count").textContent=g.links.length;
  $("#metadata").innerHTML=`<dt>GUID</dt><dd class="mono">${escapeHtml(g.guid)}</dd><dt>Description</dt><dd>${escapeHtml(g.description)||"—"}</dd><dt>Status</dt><dd><span class="pill ${g.status==='ready'?'ok':'warn'}">${escapeHtml(g.status)}</span></dd><dt>Computer</dt><dd>${g.computer_enabled?"Enabled":"Disabled"}</dd><dt>User</dt><dd>${g.user_enabled?"Enabled":"Disabled"}</dd><dt>Updated</dt><dd>${new Date(g.updated_at).toLocaleString()}</dd>`;
  renderValidation();renderSettings();renderLinks();
  if($("#panel-history").classList.contains("active"))loadHistory();
}
function renderValidation(){
  const errors=state.validation.filter(i=>i.severity==="error"),warnings=state.validation.filter(i=>i.severity==="warning");
  const readiness=errors.length?['error',`${errors.length} error${errors.length===1?'':'s'}`]:warnings.length?['warn',`${warnings.length} warning${warnings.length===1?'':'s'}`]:['ok','Ready'];
  $("#readiness-pill").innerHTML=`<span class="pill ${readiness[0]}">${readiness[1]}</span>`;
  $("#validation-list").innerHTML=state.validation.length?state.validation.map(i=>`<div class="validation-item ${i.severity}"><span class="symbol">${i.severity==='error'?'×':'!'}</span><div>${escapeHtml(i.message)}<small>${escapeHtml(i.code)} · ${escapeHtml(i.path)}</small></div></div>`).join(""):`<div class="validation-item"><span class="symbol">✓</span><div>No validation findings<small>The draft can be exported.</small></div></div>`;
  $("#issue-strip").innerHTML=errors.length?`<div class="issue-banner"><strong>Export blocked.</strong> Resolve ${errors.length} validation error${errors.length===1?'':'s'} before creating a publication bundle.</div>`:"";
}
function formatValue(setting){if(setting.action==="delete")return "Delete value";if(Array.isArray(setting.value))return setting.value.join(" · ");return String(setting.value)}
function renderSettings(){
  const items=state.current.settings.filter(s=>state.side==="all"||s.side===state.side);
  $("#settings-table").innerHTML=items.map(s=>`<tr><td><span class="side ${s.side}">${s.side}</span></td><td><div class="mono truncate" title="${escapeHtml(s.hive+'\\'+s.key)}">${escapeHtml(s.hive+'\\'+s.key)}</div></td><td>${escapeHtml(s.value_name)||"(Default)"}</td><td class="mono">${escapeHtml(s.registry_type)}</td><td><div class="truncate" title="${escapeHtml(formatValue(s))}">${escapeHtml(formatValue(s))}</div></td><td><div class="row-actions"><button data-edit-setting="${s.id}">Edit</button><button data-delete-setting="${s.id}">×</button></div></td></tr>`).join("");
  $("#settings-empty").hidden=items.length>0;
  $$('[data-edit-setting]').forEach(el=>el.onclick=()=>openSetting(state.current.settings.find(s=>s.id===el.dataset.editSetting)));
  $$('[data-delete-setting]').forEach(el=>el.onclick=()=>deleteSetting(el.dataset.deleteSetting));
}
function renderLinks(){
  const items=state.current.links;$("#links-table").innerHTML=items.map(l=>`<tr><td><strong>${l.order}</strong></td><td class="mono">${escapeHtml(l.target)}</td><td><span class="pill ${l.enabled?'ok':'warn'}">${l.enabled?'Enabled':'Disabled'}</span></td><td>${l.enforced?'Yes':'No'}</td><td><div class="row-actions"><button data-edit-link="${l.id}">Edit</button><button data-delete-link="${l.id}">×</button></div></td></tr>`).join("");
  $("#links-empty").hidden=items.length>0;$$('[data-edit-link]').forEach(el=>el.onclick=()=>openLink(items.find(l=>l.id===el.dataset.editLink)));$$('[data-delete-link]').forEach(el=>el.onclick=()=>deleteLink(el.dataset.deleteLink));
}
async function loadHistory(){
  const data=await api(`/api/gpos/${state.current.guid}/revisions`);$("#history-list").innerHTML=data.items.map((r,index)=>`<div class="revision-item"><div><p><strong>Revision ${r.revision}</strong> · ${escapeHtml(r.reason)}</p><small>${escapeHtml(r.actor)} · ${new Date(r.created_at).toLocaleString()}</small></div>${index?`<button data-restore="${r.revision}">Restore</button>`:'<span class="pill ok">Current</span>'}</div>`).join("");
  $$('[data-restore]').forEach(el=>el.onclick=()=>restoreRevision(Number(el.dataset.restore)));
}

function openGpo(edit=false){
  const form=$("#gpo-form");form.reset();form.dataset.edit=edit?"true":"false";$("#metadata-options").hidden=!edit;
  $("#gpo-dialog-title").textContent=edit?"Edit policy details":"New GPO";$("#gpo-submit").textContent=edit?"Save changes":"Create policy";
  if(edit){const g=state.current;form.name.value=g.name;form.description.value=g.description;form.computer_enabled.checked=g.computer_enabled;form.user_enabled.checked=g.user_enabled;form.status.value=g.status;form.reason.value="Update policy metadata"}else form.reason.value="Create draft";
  $("#gpo-dialog").showModal();
}
function openSetting(setting=null){
  const form=$("#setting-form");form.reset();state.editingSetting=setting;$("#setting-dialog-title").textContent=setting?"Edit policy setting":"Add policy setting";
  if(setting){for(const key of ["side","action","key","value_name","registry_type","comment"])form[key].value=setting[key];form.value.value=Array.isArray(setting.value)?setting.value.join("\n"):setting.value;form.reason.value="Update registry policy"}
  syncSettingForm();$("#setting-dialog").showModal();
}
function syncSettingForm(){
  const f=$("#setting-form"),type=f.registry_type.value,side=f.side.value;$("#hive-prefix").textContent=side==="computer"?"HKLM\\":"HKCU\\";
  $("#value-help").textContent=type==="REG_MULTI_SZ"?"Enter one item per line.":type==="REG_BINARY"?"Enter hexadecimal bytes, such as 01 FF A0.":type==="REG_DWORD"||type==="REG_QWORD"?"Enter a non-negative decimal integer.":"Enter text.";
  f.value.disabled=f.action.value==="delete";
}
function openLink(link=null){
  const form=$("#link-form");form.reset();state.editingLink=link;$("#link-dialog-title").textContent=link?"Edit link":"Add link";
  if(link){form.target.value=link.target;form.order.value=link.order;form.enabled.checked=link.enabled;form.enforced.checked=link.enforced;form.reason.value="Update link intent"}$("#link-dialog").showModal();
}

$("#gpo-form").onsubmit=async event=>{event.preventDefault();const f=event.currentTarget;try{let data;if(f.dataset.edit==="true"){data=await api(`/api/gpos/${state.current.guid}`,{method:"PATCH",body:JSON.stringify({...audit(f.reason.value),name:f.name.value,description:f.description.value,computer_enabled:f.computer_enabled.checked,user_enabled:f.user_enabled.checked,status:f.status.value})})}else{data=await api("/api/gpos",{method:"POST",body:JSON.stringify({name:f.name.value,description:f.description.value,actor:"local-operator",reason:f.reason.value})})}$("#gpo-dialog").close();await loadList(data.gpo.guid);toast(f.dataset.edit==="true"?"Policy details saved":"Draft policy created")}catch(error){toast(error.message)}};
$("#setting-form").onsubmit=async event=>{event.preventDefault();const f=event.currentTarget,type=f.registry_type.value;let value=f.value.value;if(type==="REG_DWORD"||type==="REG_QWORD")value=Number(value);else if(type==="REG_MULTI_SZ")value=value.split(/\r?\n/).filter(Boolean);const setting={side:f.side.value,hive:f.side.value==="computer"?"HKLM":"HKCU",key:f.key.value.replace(/^\\+|\\+$/g,""),value_name:f.value_name.value,registry_type:type,value,action:f.action.value,comment:f.comment.value};const path=state.editingSetting?`/api/gpos/${state.current.guid}/settings/${state.editingSetting.id}`:`/api/gpos/${state.current.guid}/settings`;try{const data=await api(path,{method:state.editingSetting?"PUT":"POST",body:JSON.stringify({...audit(f.reason.value),setting})});$("#setting-dialog").close();state.current=data.gpo;state.validation=data.validation;renderAll();renderList();toast("Registry policy saved")}catch(error){toast(error.message)}};
$("#link-form").onsubmit=async event=>{event.preventDefault();const f=event.currentTarget,link={target:f.target.value,order:Number(f.order.value),enabled:f.enabled.checked,enforced:f.enforced.checked};const path=state.editingLink?`/api/gpos/${state.current.guid}/links/${state.editingLink.id}`:`/api/gpos/${state.current.guid}/links`;try{const data=await api(path,{method:state.editingLink?"PUT":"POST",body:JSON.stringify({...audit(f.reason.value),link})});$("#link-dialog").close();state.current=data.gpo;state.validation=data.validation;renderAll();renderList();toast("Link intent saved")}catch(error){toast(error.message)}};

async function deleteSetting(id){if(!confirm("Remove this setting from the draft?"))return;try{const data=await api(`/api/gpos/${state.current.guid}/settings/${id}`,{method:"DELETE",body:JSON.stringify({...audit("Remove registry policy")})});state.current=data.gpo;state.validation=data.validation;renderAll();renderList();toast("Setting removed")}catch(error){toast(error.message)}}
async function deleteLink(id){if(!confirm("Remove this link from the draft?"))return;try{const data=await api(`/api/gpos/${state.current.guid}/links/${id}`,{method:"DELETE",body:JSON.stringify({...audit("Remove link intent")})});state.current=data.gpo;state.validation=data.validation;renderAll();renderList();toast("Link removed")}catch(error){toast(error.message)}}
async function restoreRevision(revision){if(!confirm(`Restore revision ${revision} as a new revision?`))return;try{const data=await api(`/api/gpos/${state.current.guid}/revisions/${revision}/restore`,{method:"POST",body:JSON.stringify({...audit(`Restore revision ${revision}`)})});state.current=data.gpo;state.validation=data.validation;renderAll();renderList();await loadHistory();toast(`Revision ${revision} restored`)}catch(error){toast(error.message)}}

$$(".tab").forEach(tab=>tab.onclick=()=>{$$(".tab").forEach(x=>x.classList.toggle("active",x===tab));$$(".panel").forEach(x=>x.classList.toggle("active",x.id===`panel-${tab.dataset.tab}`));if(tab.dataset.tab==="history")loadHistory()});
$$(".chip").forEach(chip=>chip.onclick=()=>{$$(".chip").forEach(x=>x.classList.toggle("active",x===chip));state.side=chip.dataset.side;renderSettings()});
$("#new-gpo").onclick=()=>openGpo();$("#empty-new").onclick=()=>openGpo();$("#edit-metadata").onclick=()=>openGpo(true);$("#add-setting").onclick=()=>openSetting();$("#add-link").onclick=()=>openLink();$("#search").oninput=renderList;
for(const name of ["side","action","registry_type"])$("#setting-form")[name].onchange=syncSettingForm;
loadList().catch(error=>toast(error.message));
