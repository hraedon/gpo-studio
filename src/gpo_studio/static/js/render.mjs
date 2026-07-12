import {state,$,$$,escapeHtml} from './state.mjs';
import {api} from './api.mjs';
import {openSetting,deleteSetting,openLink,deleteLink,openFilter,deleteFilter,restoreRevision} from './forms.mjs';

export async function loadList(selectGuid){
  const data=await api("/api/gpos");state.gpos=data.items;renderList();
  const guid=selectGuid||state.current?.guid||state.gpos[0]?.guid;
  if(guid)await selectGpo(guid);else showEmpty();
}
export function renderList(){
  const query=$("#search").value.toLowerCase();
  $("#gpo-list").innerHTML=state.gpos.filter(g=>g.name.toLowerCase().includes(query)).map(g=>`<button class="gpo-item ${state.current?.guid===g.guid?"active":""}" data-guid="${escapeHtml(g.guid)}"><strong>${escapeHtml(g.name)}</strong><small>${g.status} · r${g.revision}</small></button>`).join("");
  $$(".gpo-item").forEach(el=>el.onclick=()=>selectGpo(el.dataset.guid));
}
export function showEmpty(){$("#empty").hidden=false;$("#workspace").hidden=true;$("#top-actions").hidden=true;$("#title").textContent="Choose a policy"}
export async function selectGpo(guid){
  const data=await api(`/api/gpos/${guid}`);state.current=data.gpo;state.validation=data.validation;state.semanticHash=data.semantic_sha256||"";
  $("#empty").hidden=true;$("#workspace").hidden=false;$("#top-actions").hidden=false;
  renderAll();renderList();
}
export function renderAll(){
  const g=state.current;$("#title").textContent=g.name;$("#revision").textContent=`Revision ${g.revision}`;
  $("#fork-gpo").hidden=!(g.source_guid||g.status==="archived");
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
export function renderValidation(){
  const errors=state.validation.filter(i=>i.severity==="error"),warnings=state.validation.filter(i=>i.severity==="warning");
  const readiness=errors.length?['error',`${errors.length} error${errors.length===1?'':'s'}`]:warnings.length?['warn',`${warnings.length} warning${warnings.length===1?'':'s'}`]:['ok','Ready'];
  $("#readiness-pill").innerHTML=`<span class="pill ${readiness[0]}">${readiness[1]}</span>`;
  $("#validation-list").innerHTML=state.validation.length?state.validation.map(i=>`<div class="validation-item ${i.severity}"><span class="symbol">${i.severity==='error'?'×':'!'}</span><div>${escapeHtml(i.message)}<small>${escapeHtml(i.code)} · ${escapeHtml(i.path)}</small></div></div>`).join(""):`<div class="validation-item"><span class="symbol">✓</span><div>No validation findings<small>The draft can be exported.</small></div></div>`;
  $("#issue-strip").innerHTML=errors.length?`<div class="issue-banner"><strong>Export blocked.</strong> Resolve ${errors.length} validation error${errors.length===1?'':'s'} before creating a publication bundle.</div>`:"";
}
export function formatValue(setting){if(setting.action==="delete")return "Delete value";if(Array.isArray(setting.value))return setting.value.join(" · ");return String(setting.value)}
export function renderSettings(){
  const items=state.current.settings.filter(s=>state.side==="all"||s.side===state.side);
  $("#settings-table").innerHTML=items.map(s=>`<tr><td><span class="side ${s.side}">${s.side}</span></td><td><div class="mono truncate" title="${escapeHtml(s.hive+'\\'+s.key)}">${escapeHtml(s.hive+'\\'+s.key)}</div></td><td>${escapeHtml(s.value_name)||"(Default)"}</td><td class="mono">${escapeHtml(s.registry_type)}</td><td><div class="truncate" title="${escapeHtml(formatValue(s))}">${escapeHtml(formatValue(s))}</div></td><td><div class="row-actions"><button data-edit-setting="${escapeHtml(s.id)}">Edit</button><button data-delete-setting="${escapeHtml(s.id)}">×</button></div></td></tr>`).join("");
  $("#settings-empty").hidden=items.length>0;
  $$('[data-edit-setting]').forEach(el=>el.onclick=()=>openSetting(state.current.settings.find(s=>s.id===el.dataset.editSetting)));
  $$('[data-delete-setting]').forEach(el=>el.onclick=()=>deleteSetting(el.dataset.deleteSetting));
}
export function renderLinks(){
  const items=state.current.links;$("#links-table").innerHTML=items.map(l=>`<tr><td><strong>${l.order}</strong></td><td class="mono">${escapeHtml(l.target)}</td><td><span class="pill ${l.enabled?'ok':'warn'}">${l.enabled?'Enabled':'Disabled'}</span></td><td>${l.enforced?'Yes':'No'}</td><td><div class="row-actions"><button data-edit-link="${escapeHtml(l.id)}">Edit</button><button data-delete-link="${escapeHtml(l.id)}">×</button></div></td></tr>`).join("");
  $("#links-empty").hidden=items.length>0;$$('[data-edit-link]').forEach(el=>el.onclick=()=>openLink(items.find(l=>l.id===el.dataset.editLink)));$$('[data-delete-link]').forEach(el=>el.onclick=()=>deleteLink(el.dataset.deleteLink));
}
export function renderFilters(){
  const items=state.current.security_filters||[];
  $("#filters-table").innerHTML=items.map(f=>`<tr><td class="mono">${escapeHtml(f.principal)}</td><td>${escapeHtml(f.permission)}</td><td>${escapeHtml(f.target_type||"group")}</td><td>${f.inheritable?"Yes":"No"}</td><td><div class="row-actions"><button data-edit-filter="${escapeHtml(f.id)}">Edit</button><button data-delete-filter="${escapeHtml(f.id)}">×</button></div></td></tr>`).join("");
  $("#filters-empty").hidden=items.length>0;
  $$('[data-edit-filter]').forEach(el=>el.onclick=()=>openFilter(items.find(f=>f.id===el.dataset.editFilter)));
  $$('[data-delete-filter]').forEach(el=>el.onclick=()=>deleteFilter(el.dataset.deleteFilter));
}
export function renderWmi(){
  const w=state.current.wmi_filter;
  if(!w){$("#wmi-display").innerHTML='<div class="table-empty">No WMI filter set.</div>';return}
  $("#wmi-display").innerHTML=`<dl class="details"><dt>Name</dt><dd>${escapeHtml(w.name)}</dd><dt>Language</dt><dd>${escapeHtml(w.language)}</dd><dt>Query</dt><dd class="mono">${escapeHtml(w.query)}</dd>${w.description?`<dt>Description</dt><dd>${escapeHtml(w.description)}</dd>`:""}</dl>`;
}
export async function loadHistory(){
  const data=await api(`/api/gpos/${state.current.guid}/revisions`);$("#history-list").innerHTML=data.items.map((r,index)=>`<div class="revision-item"><div><p><strong>Revision ${r.revision}</strong> · ${escapeHtml(r.reason)}</p><small>${escapeHtml(r.actor)} · ${new Date(r.created_at).toLocaleString()}</small></div>${index?`<button data-restore="${r.revision}">Restore</button>`:'<span class="pill ok">Current</span>'}</div>`).join("");
  $$('[data-restore]').forEach(el=>el.onclick=()=>restoreRevision(Number(el.dataset.restore)));
}
