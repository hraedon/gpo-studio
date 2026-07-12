const state={gpos:[],current:null,validation:[],semanticHash:"",side:"all",editingSetting:null,editingLink:null,editingFilter:null};
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
  const data=await api(`/api/gpos/${guid}`);state.current=data.gpo;state.validation=data.validation;state.semanticHash=data.semantic_sha256||"";
  $("#empty").hidden=true;$("#workspace").hidden=false;$("#top-actions").hidden=false;
  renderAll();renderList();
}
function renderAll(){
  const g=state.current;$("#title").textContent=g.name;$("#revision").textContent=`Revision ${g.revision}`;
  $("#plan").href=`/api/gpos/${g.guid}/plan.ps1`;$("#export").href=`/api/gpos/${g.guid}/export.zip`;$("#gpmc-backup").href=`/api/gpos/${g.guid}/gpmc-backup`;
  $("#setting-count").textContent=g.settings.length;$("#link-count").textContent=g.links.length;$("#filter-count").textContent=g.security_filters?g.security_filters.length:0;
  const metaParts=[`<dt>GUID</dt><dd class="mono">${escapeHtml(g.guid)}</dd><dt>Description</dt><dd>${escapeHtml(g.description)||"—"}</dd><dt>Status</dt><dd><span class="pill ${g.status==='ready'?'ok':'warn'}">${escapeHtml(g.status)}</span></dd><dt>Computer</dt><dd>${g.computer_enabled?"Enabled":"Disabled"}</dd><dt>User</dt><dd>${g.user_enabled?"Enabled":"Disabled"}</dd><dt>Domain</dt><dd>${escapeHtml(g.domain||"studio.local")}</dd>`];
  if(g.source_guid)metaParts.push(`<dt>Source GUID</dt><dd class="mono">${escapeHtml(g.source_guid)}</dd>`);
  if(g.cse_metadata&&g.cse_metadata.length)metaParts.push(`<dt>CSE extensions</dt><dd>${g.cse_metadata.length} extension${g.cse_metadata.length===1?'':'s'}</dd>`);
  metaParts.push(`<dt>Semantic hash</dt><dd class="mono" title="${escapeHtml(state.semanticHash)}">${escapeHtml(state.semanticHash.slice(0,16))}…</dd><dt>Updated</dt><dd>${new Date(g.updated_at).toLocaleString()}</dd>`);
  $("#metadata").innerHTML=metaParts.join("");
  renderValidation();renderSettings();renderLinks();renderFilters();renderWmi();
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
function renderFilters(){
  const items=state.current.security_filters||[];
  $("#filters-table").innerHTML=items.map(f=>`<tr><td class="mono">${escapeHtml(f.principal)}</td><td>${escapeHtml(f.permission)}</td><td>${f.inheritable?"Yes":"No"}</td><td><div class="row-actions"><button data-edit-filter="${f.id}">Edit</button><button data-delete-filter="${f.id}">×</button></div></td></tr>`).join("");
  $("#filters-empty").hidden=items.length>0;
  $$('[data-edit-filter]').forEach(el=>el.onclick=()=>openFilter(items.find(f=>f.id===el.dataset.editFilter)));
  $$('[data-delete-filter]').forEach(el=>el.onclick=()=>deleteFilter(el.dataset.deleteFilter));
}
function renderWmi(){
  const w=state.current.wmi_filter;
  if(!w){$("#wmi-display").innerHTML='<div class="table-empty">No WMI filter set.</div>';return}
  $("#wmi-display").innerHTML=`<dl class="details"><dt>Name</dt><dd>${escapeHtml(w.name)}</dd><dt>Language</dt><dd>${escapeHtml(w.language)}</dd><dt>Query</dt><dd class="mono">${escapeHtml(w.query)}</dd>${w.description?`<dt>Description</dt><dd>${escapeHtml(w.description)}</dd>`:""}</dl>`;
}
async function loadHistory(){
  const data=await api(`/api/gpos/${state.current.guid}/revisions`);$("#history-list").innerHTML=data.items.map((r,index)=>`<div class="revision-item"><div><p><strong>Revision ${r.revision}</strong> · ${escapeHtml(r.reason)}</p><small>${escapeHtml(r.actor)} · ${new Date(r.created_at).toLocaleString()}</small></div>${index?`<button data-restore="${r.revision}">Restore</button>`:'<span class="pill ok">Current</span>'}</div>`).join("");
  $$('[data-restore]').forEach(el=>el.onclick=()=>restoreRevision(Number(el.dataset.restore)));
}

function openGpo(edit=false){
  const form=$("#gpo-form");form.reset();form.dataset.edit=edit?"true":"false";$("#metadata-options").hidden=!edit;
  $("#gpo-dialog-title").textContent=edit?"Edit policy details":"New GPO";$("#gpo-submit").textContent=edit?"Save changes":"Create policy";
  if(edit){const g=state.current;form.name.value=g.name;form.description.value=g.description;form.computer_enabled.checked=g.computer_enabled;form.user_enabled.checked=g.user_enabled;form.status.value=g.status;form.domain.value=g.domain||"studio.local";form.reason.value="Update policy metadata"}else form.reason.value="Create draft";
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
function openFilter(filter=null){
  const form=$("#filter-form");form.reset();state.editingFilter=filter;$("#filter-dialog-title").textContent=filter?"Edit security filter":"Add security filter";
  if(filter){form.principal.value=filter.principal;form.permission.value=filter.permission;form.inheritable.checked=filter.inheritable;form.reason.value="Update security filter"}$("#filter-dialog").showModal();
}
function openWmi(){
  const form=$("#wmi-form");form.reset();
  const w=state.current.wmi_filter;
  if(w){form.name.value=w.name;form.description.value=w.description;form.language.value=w.language;form.query.value=w.query;form.reason.value="Update WMI filter"}
  $("#wmi-dialog").showModal();
}

$("#gpo-form").onsubmit=async event=>{event.preventDefault();const f=event.currentTarget;try{let data;if(f.dataset.edit==="true"){data=await api(`/api/gpos/${state.current.guid}`,{method:"PATCH",body:JSON.stringify({...audit(f.reason.value),name:f.name.value,description:f.description.value,computer_enabled:f.computer_enabled.checked,user_enabled:f.user_enabled.checked,status:f.status.value,domain:f.domain.value})})}else{data=await api("/api/gpos",{method:"POST",body:JSON.stringify({name:f.name.value,description:f.description.value,actor:"local-operator",reason:f.reason.value})})}$("#gpo-dialog").close();await loadList(data.gpo.guid);toast(f.dataset.edit==="true"?"Policy details saved":"Draft policy created")}catch(error){toast(error.message)}};
$("#setting-form").onsubmit=async event=>{event.preventDefault();const f=event.currentTarget,type=f.registry_type.value;let value=f.value.value;if(type==="REG_DWORD"||type==="REG_QWORD")value=Number(value);else if(type==="REG_MULTI_SZ")value=value.split(/\r?\n/).filter(Boolean);const setting={side:f.side.value,hive:f.side.value==="computer"?"HKLM":"HKCU",key:f.key.value.replace(/^\\+|\\+$/g,""),value_name:f.value_name.value,registry_type:type,value,action:f.action.value,comment:f.comment.value};const path=state.editingSetting?`/api/gpos/${state.current.guid}/settings/${state.editingSetting.id}`:`/api/gpos/${state.current.guid}/settings`;try{const data=await api(path,{method:state.editingSetting?"PUT":"POST",body:JSON.stringify({...audit(f.reason.value),setting})});$("#setting-dialog").close();state.current=data.gpo;state.validation=data.validation;state.semanticHash=data.semantic_sha256||"";renderAll();renderList();toast("Registry policy saved")}catch(error){toast(error.message)}};
$("#link-form").onsubmit=async event=>{event.preventDefault();const f=event.currentTarget,link={target:f.target.value,order:Number(f.order.value),enabled:f.enabled.checked,enforced:f.enforced.checked};const path=state.editingLink?`/api/gpos/${state.current.guid}/links/${state.editingLink.id}`:`/api/gpos/${state.current.guid}/links`;try{const data=await api(path,{method:state.editingLink?"PUT":"POST",body:JSON.stringify({...audit(f.reason.value),link})});$("#link-dialog").close();state.current=data.gpo;state.validation=data.validation;state.semanticHash=data.semantic_sha256||"";renderAll();renderList();toast("Link intent saved")}catch(error){toast(error.message)}};
$("#filter-form").onsubmit=async event=>{event.preventDefault();const f=event.currentTarget,filter={principal:f.principal.value,permission:f.permission.value,inheritable:f.inheritable.checked};const path=state.editingFilter?`/api/gpos/${state.current.guid}/security-filters/${state.editingFilter.id}`:`/api/gpos/${state.current.guid}/security-filters`;try{const data=await api(path,{method:state.editingFilter?"PUT":"POST",body:JSON.stringify({...audit(f.reason.value),filter})});$("#filter-dialog").close();state.current=data.gpo;state.validation=data.validation;state.semanticHash=data.semantic_sha256||"";renderAll();renderList();toast("Security filter saved")}catch(error){toast(error.message)}};
$("#wmi-form").onsubmit=async event=>{event.preventDefault();const f=event.currentTarget,wmi_filter={name:f.name.value,description:f.description.value,language:f.language.value,query:f.query.value};try{const data=await api(`/api/gpos/${state.current.guid}/wmi-filter`,{method:"PUT",body:JSON.stringify({...audit(f.reason.value),wmi_filter})});$("#wmi-dialog").close();state.current=data.gpo;state.validation=data.validation;state.semanticHash=data.semantic_sha256||"";renderAll();renderList();toast("WMI filter saved")}catch(error){toast(error.message)}};

async function deleteSetting(id){if(!confirm("Remove this setting from the draft?"))return;try{const data=await api(`/api/gpos/${state.current.guid}/settings/${id}`,{method:"DELETE",body:JSON.stringify({...audit("Remove registry policy")})});state.current=data.gpo;state.validation=data.validation;state.semanticHash=data.semantic_sha256||"";renderAll();renderList();toast("Setting removed")}catch(error){toast(error.message)}}
async function deleteLink(id){if(!confirm("Remove this link from the draft?"))return;try{const data=await api(`/api/gpos/${state.current.guid}/links/${id}`,{method:"DELETE",body:JSON.stringify({...audit("Remove link intent")})});state.current=data.gpo;state.validation=data.validation;state.semanticHash=data.semantic_sha256||"";renderAll();renderList();toast("Link removed")}catch(error){toast(error.message)}}
async function deleteFilter(id){if(!confirm("Remove this security filter?"))return;try{const data=await api(`/api/gpos/${state.current.guid}/security-filters/${id}`,{method:"DELETE",body:JSON.stringify({...audit("Remove security filter")})});state.current=data.gpo;state.validation=data.validation;state.semanticHash=data.semantic_sha256||"";renderAll();renderList();toast("Security filter removed")}catch(error){toast(error.message)}}
async function restoreRevision(revision){if(!confirm(`Restore revision ${revision} as a new revision?`))return;try{const data=await api(`/api/gpos/${state.current.guid}/revisions/${revision}/restore`,{method:"POST",body:JSON.stringify({...audit(`Restore revision ${revision}`)})});state.current=data.gpo;state.validation=data.validation;state.semanticHash=data.semantic_sha256||"";renderAll();renderList();await loadHistory();toast(`Revision ${revision} restored`)}catch(error){toast(error.message)}}

let admxLoaded=null,admxTimer=null,admxCatsLoaded=false;
async function checkAdmx(){
  if(admxLoaded===null){try{const h=await api("/api/health");admxLoaded=h.admx_loaded===true}catch(e){admxLoaded=false}$("#admx-empty").hidden=admxLoaded;$("#admx-content").hidden=!admxLoaded}
  if(admxLoaded){if(!admxCatsLoaded){admxCatsLoaded=true;loadAdmxCategories()}loadAdmxResults($("#admx-search").value)}
}
async function loadAdmxResults(q){
  try{const data=await api(`/api/admx/search?q=${encodeURIComponent(q)}`);$("#admx-results").hidden=false;$("#admx-detail").hidden=true;
    $("#admx-results").innerHTML=data.items.length?data.items.map(p=>`<button class="admx-result" data-id="${escapeHtml(p.id)}"><div><strong>${escapeHtml(p.display_name)}</strong><span class="side ${p.class_==='Machine'?'computer':'user'}">${escapeHtml(p.class_)}</span></div><small class="mono">${escapeHtml(p.key)}</small><p>${escapeHtml((p.explain_text||"").slice(0,120))}${(p.explain_text||"").length>120?'…':''}</p></button>`).join(""):'<div class="table-empty">No policies found.</div>';
    $$(".admx-result").forEach(el=>el.onclick=()=>loadAdmxDetail(el.dataset.id));
  }catch(e){toast(e.message)}
}
async function loadAdmxDetail(id){
  try{const p=await api(`/api/admx/policies/${encodeURIComponent(id)}`);$("#admx-results").hidden=true;$("#admx-detail").hidden=false;
    const fields=(p.elements||[]).map(e=>{
      const pres=(p.presentation||[]).find(pr=>pr.ref_id===e.id)||{};
      const label=escapeHtml(pres.label||e.id);
      if(e.kind==="boolean")return `<label class="config-field"><input type="checkbox" data-elem-id="${escapeHtml(e.id)}" data-kind="boolean"> ${label}</label>`;
      if(e.kind==="decimal")return `<label class="config-field">${label}<input type="number" step="1" data-elem-id="${escapeHtml(e.id)}" data-kind="decimal" value="0"></label>`;
      if(e.kind==="text")return `<label class="config-field">${label}<input type="text" data-elem-id="${escapeHtml(e.id)}" data-kind="text"></label>`;
      if(e.kind==="multitext")return `<label class="config-field">${label}<textarea rows="3" data-elem-id="${escapeHtml(e.id)}" data-kind="multitext" placeholder="One item per line"></textarea></label>`;
      if(e.kind==="list")return `<label class="config-field">${label}<textarea rows="3" data-elem-id="${escapeHtml(e.id)}" data-kind="list" placeholder="One item per line"></textarea></label>`;
      if(e.kind==="enum"){
        if(e.enum_items&&e.enum_items.length){
          const opts=e.enum_items.map(it=>`<option value="${escapeHtml(it.id)}">${escapeHtml(it.display_name)}</option>`).join("");
          return `<label class="config-field">${label}<select data-elem-id="${escapeHtml(e.id)}" data-kind="enum"><option value="">— Select —</option>${opts}</select></label>`;
        }
        return `<label class="config-field">${label}<input type="text" data-elem-id="${escapeHtml(e.id)}" data-kind="enum" placeholder="Enter enum value"></label>`;
      }
      return `<div class="config-field"><small>Unsupported element kind: ${escapeHtml(e.kind)}</small></div>`;
    }).join("");
    $("#admx-detail").innerHTML=`<div class="admx-detail-head"><button class="quiet" id="admx-back">← Back</button><h3>${escapeHtml(p.display_name)}</h3></div><dl class="details"><dt>ID</dt><dd class="mono">${escapeHtml(p.id)}</dd><dt>Class</dt><dd>${escapeHtml(p.class_)}</dd><dt>Key</dt><dd class="mono">${escapeHtml(p.key)}</dd><dt>Category</dt><dd>${escapeHtml(p.parent_category)||"—"}</dd><dt>Supported on</dt><dd>${escapeHtml(p.supported_on)||"—"}</dd><dt>Explanation</dt><dd>${escapeHtml(p.explain_text)||"—"}</dd>${p.elements&&p.elements.length?`<dt>Elements</dt><dd>${p.elements.map(e=>`<div class="mono">${escapeHtml(e.kind)}: ${escapeHtml(e.id)}</div>`).join("")}</dd>`:""}${p.presentation&&p.presentation.length?`<dt>Presentation</dt><dd>${p.presentation.map(e=>`<div>${escapeHtml(e.kind)}: ${escapeHtml(e.label||e.id)}</div>`).join("")}</dd>`:""}</dl>`;
    if(fields){const btn=document.createElement("button");btn.className="primary";btn.textContent="Configure policy";btn.id="admx-configure-btn";$("#admx-detail").appendChild(btn);btn.onclick=()=>openConfigureDialog(p.id,p.display_name,p.class_,fields)}
    $("#admx-back").onclick=()=>{$("#admx-detail").hidden=true;$("#admx-results").hidden=false};
  }catch(e){toast(e.message)}
}
async function loadAdmxCategories(){
  try{const data=await api("/api/admx/categories");$("#admx-categories").innerHTML=data.items.length?data.items.map(c=>`<div class="admx-cat">${escapeHtml(c.display_name)}<small class="mono">${escapeHtml(c.id)}</small></div>`).join(""):'<div class="table-empty">No categories.</div>';
  }catch(e){}
}
function openConfigureDialog(policyId,policyName,policyClass,fieldsHtml){
  const form=$("#configure-form");form.reset();
  $("#configure-title").textContent=`Configure: ${policyName}`;
  const gpoSelect=$("#configure-target-gpo");
  gpoSelect.innerHTML=state.gpos.map(g=>`<option value="${escapeHtml(g.guid)}">${escapeHtml(g.name)} (r${g.revision})</option>`).join("");
  const sideHtml=policyClass==="Both"?`<label class="config-field">Configuration side<select id="configure-side"><option value="computer">Computer</option><option value="user">User</option></select></label>`:"";
  $("#configure-fields").innerHTML=sideHtml+fieldsHtml;
  form.dataset.policyId=policyId;
  form.dataset.policyClass=policyClass;
  $("#configure-dialog").showModal();
}
$("#configure-form").onsubmit=async event=>{event.preventDefault();const f=event.currentTarget;const policyId=f.dataset.policyId;const policyClass=f.dataset.policyClass;
  const targetGuid=$("#configure-target-gpo").value;if(!targetGuid){toast("Select a target GPO");return}
  const targetGpo=state.gpos.find(g=>g.guid===targetGuid);if(!targetGpo){toast("Target GPO not found");return}
  let side=policyClass==="Machine"?"computer":policyClass==="User"?"user":$("#configure-side").value;
  const values={};
  let ok=true;
  $$("[data-elem-id]",f).forEach(el=>{
    if(!ok)return;const kind=el.dataset.kind;const id=el.dataset.elemId;
    if(kind==="boolean"){values[id]=el.checked}
    else if(kind==="decimal"){const n=parseInt(el.value,10);if(!Number.isFinite(n)){toast(`Invalid number for ${id}`);ok=false;return}values[id]=n}
    else if(kind==="text"||kind==="enum"){values[id]=el.value}
    else if(kind==="multitext"||kind==="list"){values[id]=el.value.split(/\r?\n/).filter(Boolean)}
  });
  if(!ok)return;
  try{const data=await api(`/api/admx/policies/${encodeURIComponent(policyId)}/configure`,{method:"POST",body:JSON.stringify({gpo_guid:targetGuid,side,values,actor:"local-operator",reason:f.reason.value,expected_revision:targetGpo.revision})});
    $("#configure-dialog").close();await loadList(targetGuid);toast("Policy settings applied to GPO")}
  catch(error){toast(error.message)}
};
$("#admx-search").oninput=e=>{clearTimeout(admxTimer);admxTimer=setTimeout(()=>loadAdmxResults(e.target.value),250)};

$$(".tab").forEach(tab=>tab.onclick=()=>{$$(".tab").forEach(x=>x.classList.toggle("active",x===tab));$$(".panel").forEach(x=>x.classList.toggle("active",x.id===`panel-${tab.dataset.tab}`));if(tab.dataset.tab==="history")loadHistory();if(tab.dataset.tab==="admx")checkAdmx()});
$$(".chip").forEach(chip=>chip.onclick=()=>{$$(".chip").forEach(x=>x.classList.toggle("active",x===chip));state.side=chip.dataset.side;renderSettings()});
$("#new-gpo").onclick=()=>openGpo();$("#empty-new").onclick=()=>openGpo();$("#edit-metadata").onclick=()=>openGpo(true);$("#add-setting").onclick=()=>openSetting();$("#add-link").onclick=()=>openLink();$("#add-filter").onclick=()=>openFilter();$("#edit-wmi").onclick=()=>openWmi();$("#search").oninput=renderList;
for(const name of ["side","action","registry_type"])$("#setting-form")[name].onchange=syncSettingForm;
loadList().catch(error=>toast(error.message));
