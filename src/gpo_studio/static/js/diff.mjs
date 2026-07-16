import {state,$,escapeHtml} from './state.mjs';
import {api,toast} from './api.mjs';

function formatSettingValue(s){if(!s)return '—';if(s.action==='delete')return 'Delete value';if(Array.isArray(s.value))return s.value.join(' · ');return String(s.value)}
function formatSf(sf){if(!sf)return '—';return `${sf.permission} · ${sf.target_type||'group'} · ${sf.inheritable?'inheritable':'not inheritable'}`}
function formatWmi(w){if(!w)return '—';return `${w.name}: ${w.query}`}
function formatLink(l){if(!l)return '—';return `${l.target} · ${l.enabled?'enabled':'disabled'} · ${l.enforced?'enforced':'not enforced'} · order ${l.order}`}
function formatGppGroup(g){if(!g)return '—';const members=(g.members||[]).map(m=>m.name||m.sid).filter(Boolean).join(', ');return `${g.name||'(unnamed)'} · ${g.action} · sid ${g.sid||'—'}${members?` · members: ${members}`:''}`}
function formatGppRegistry(r){if(!r)return '—';const v=Array.isArray(r.value)?(r.value[0]||null):r.value;if(!v)return `${r.key||'(no key)'} · ${r.action||''}`;const valStr=`${v.name||'(default)'}=${Array.isArray(v.value)?v.value.join(';'):String(v.value)}`;return `${r.key||'(no key)'} · ${r.action||''} · ${valStr}`}
function formatCse(c){if(!c)return '—';return `${c.guid||''} · ${c.side||''} · ${(c.files||[]).length} file(s)`}
function kindClass(kind){return kind==='added'?'diff-added':kind==='removed'?'diff-removed':'diff-modified'}

export async function loadDiffSelectors(){
  const opts=state.gpos.map(g=>`<option value="${escapeHtml(g.guid)}">${escapeHtml(g.name)} (r${g.revision})</option>`).join('');
  ['diff-baseline','diff-draft','diff-observed'].forEach(id=>{const sel=$('#'+id),prev=sel.value;sel.innerHTML=opts;if(prev)sel.value=prev});
  if(!state.current)return;
  try{
    const history=await api(`/api/gpos/${state.current.guid}/revisions`);
    const revisionOptions=history.items.map(item=>`<option value="${item.revision}">Revision ${item.revision} — ${escapeHtml(item.reason)}</option>`).join('');
    const from=$('#revision-diff-from'),to=$('#revision-diff-to');
    from.innerHTML=revisionOptions;to.innerHTML=revisionOptions;
    if(history.items.length>1)from.value=String(history.items.at(-1).revision);
    if(history.items.length)to.value=String(history.items[0].revision);
    $('#run-revision-diff').disabled=history.items.length<2;
    $('#revision-diff-results').innerHTML=history.items.length<2?'<div class="table-empty">Create another revision to compare policy history.</div>':'';
  }catch(error){$('#revision-diff-results').innerHTML=`<div class="table-empty">Error: ${escapeHtml(error.message)}</div>`}
}

export function initDiff(){
  $('#run-revision-diff').onclick=async()=>{
    const button=$('#run-revision-diff'),from=$('#revision-diff-from').value,to=$('#revision-diff-to').value;
    if(from===to){$('#revision-diff-results').innerHTML='<div class="table-empty">Choose two different revisions.</div>';return}
    button.disabled=true;$('#revision-diff-results').innerHTML='<div class="table-empty">Comparing revisions…</div>';
    try{const data=await api(`/api/gpos/${state.current.guid}/revisions/diff?from_revision=${encodeURIComponent(from)}&to_revision=${encodeURIComponent(to)}`);renderDiff(data,$('#revision-diff-results'))}
    catch(error){$('#revision-diff-results').innerHTML=`<div class="table-empty">Error: ${escapeHtml(error.message)}</div>`}
    finally{button.disabled=false}
  };
  $('#run-diff').onclick=async()=>{
    const btn=$('#run-diff');btn.disabled=true;
    try{
      const baseline=$('#diff-baseline').value,draft=$('#diff-draft').value,observed=$('#diff-observed').value;
      if(!baseline||!draft||!observed){toast('Select all three GPOs');return}
      if(baseline===draft||baseline===observed||draft===observed){toast('Select three different GPOs');return}
      const results=$('#diff-results');results.innerHTML='<div class="table-empty">Computing diff…</div>';
      const data=await api('/api/estate/diff',{method:'POST',body:JSON.stringify({baseline_guid:baseline,draft_guid:draft,observed_guid:observed})});renderDiff(data)
    }catch(e){$('#diff-results').innerHTML=`<div class="table-empty">${escapeHtml(e.message)}</div>`}
    finally{btn.disabled=false}
  };
}

export function renderDiff(data,target=$('#diff-results')){
  data={settings:[],links:[],security_filters:[],wmi_filter:null,gpp_groups:[],gpp_registry:[],gpp_collection:[],metadata:[],cse_metadata:[],conflicts:[],security_filter_conflicts:[],wmi_filter_conflict:null,link_conflicts:[],gpp_conflicts:[],gpp_reorder_conflicts:[],gpp_collection_conflicts:[],metadata_conflicts:[],cse_metadata_conflicts:[],...data};
  const parts=[];
  const hasChanges=data.settings.length||data.security_filters.length||data.wmi_filter||(data.links&&data.links.length)||(data.gpp_groups&&data.gpp_groups.length)||(data.gpp_registry&&data.gpp_registry.length)||(data.gpp_collection&&data.gpp_collection.length)||(data.metadata&&data.metadata.length)||(data.cse_metadata&&data.cse_metadata.length);
  const hasConflicts=data.conflicts.length||data.security_filter_conflicts.length||data.wmi_filter_conflict||(data.link_conflicts&&data.link_conflicts.length)||(data.gpp_conflicts&&data.gpp_conflicts.length)||(data.gpp_reorder_conflicts&&data.gpp_reorder_conflicts.length)||(data.gpp_collection_conflicts&&data.gpp_collection_conflicts.length)||(data.metadata_conflicts&&data.metadata_conflicts.length)||(data.cse_metadata_conflicts&&data.cse_metadata_conflicts.length);
  if(data.settings.length){
    parts.push(`<div class="diff-section"><h3>Settings (${data.settings.length})</h3><table class="diff-table"><thead><tr><th>Kind</th><th>Key</th><th>Value name</th><th>Old</th><th>New</th></tr></thead><tbody>${data.settings.map(s=>{
      const ident=s.identity||[];const oldV=formatSettingValue(s.old);const newV=formatSettingValue(s.new);
      const key=s.old?`${s.old.hive}\\${s.old.key}`:s.new?`${s.new.hive}\\${s.new.key}`:(ident[2]||'');
      const vn=s.old?s.old.value_name:s.new?s.new.value_name:(ident[3]||'');
      return `<tr><td class="${kindClass(s.kind)}">${escapeHtml(s.kind)}</td><td class="mono">${escapeHtml(key)}</td><td>${escapeHtml(vn)}</td><td>${escapeHtml(oldV)}</td><td>${escapeHtml(newV)}</td></tr>`;
    }).join('')}</tbody></table></div>`);
  }
  if(data.links&&data.links.length){parts.push(`<div class="diff-section"><h3>Links (${data.links.length})</h3><table class="diff-table"><thead><tr><th>Kind</th><th>Target</th><th>Old</th><th>New</th></tr></thead><tbody>${data.links.map(l=>`<tr><td class="${kindClass(l.kind)}">${escapeHtml(l.kind)}</td><td class="mono">${escapeHtml(l.target)}</td><td>${escapeHtml(formatLink(l.old))}</td><td>${escapeHtml(formatLink(l.new))}</td></tr>`).join('')}</tbody></table></div>`)}
  if(data.security_filters.length){
    parts.push(`<div class="diff-section"><h3>Security filters (${data.security_filters.length})</h3><table class="diff-table"><thead><tr><th>Kind</th><th>Principal</th><th>Old</th><th>New</th></tr></thead><tbody>${data.security_filters.map(f=>`<tr><td class="${kindClass(f.kind)}">${escapeHtml(f.kind)}</td><td class="mono">${escapeHtml(f.principal)}</td><td>${escapeHtml(formatSf(f.old))}</td><td>${escapeHtml(formatSf(f.new))}</td></tr>`).join('')}</tbody></table></div>`);
  }
  if(data.wmi_filter){
    const w=data.wmi_filter;
    parts.push(`<div class="diff-section"><h3>WMI filter</h3><table class="diff-table"><thead><tr><th>Kind</th><th>Old</th><th>New</th></tr></thead><tbody><tr><td class="${kindClass(w.kind)}">${escapeHtml(w.kind)}</td><td>${escapeHtml(formatWmi(w.old))}</td><td>${escapeHtml(formatWmi(w.new))}</td></tr></tbody></table></div>`);
  }
  if(data.gpp_groups&&data.gpp_groups.length){
    parts.push(`<div class="diff-section"><h3>GPP groups (${data.gpp_groups.length})</h3><table class="diff-table"><thead><tr><th>Kind</th><th>Scope</th><th>Group</th><th>Old</th><th>New</th></tr></thead><tbody>${data.gpp_groups.map(g=>`<tr><td class="${kindClass(g.kind)}">${escapeHtml(g.kind)}</td><td>${escapeHtml(g.scope)}</td><td class="mono">${escapeHtml((g.new||g.old||{}).name||'')}</td><td>${escapeHtml(formatGppGroup(g.old))}</td><td>${escapeHtml(formatGppGroup(g.new))}</td></tr>`).join('')}</tbody></table></div>`);
  }
  if(data.gpp_registry&&data.gpp_registry.length){
    parts.push(`<div class="diff-section"><h3>GPP registry (${data.gpp_registry.length})</h3><table class="diff-table"><thead><tr><th>Kind</th><th>Scope</th><th>Key</th><th>Old</th><th>New</th></tr></thead><tbody>${data.gpp_registry.map(r=>`<tr><td class="${kindClass(r.kind)}">${escapeHtml(r.kind)}</td><td>${escapeHtml(r.scope)}</td><td class="mono">${escapeHtml((r.new||r.old||{}).key||'')}</td><td>${escapeHtml(formatGppRegistry(r.old))}</td><td>${escapeHtml(formatGppRegistry(r.new))}</td></tr>`).join('')}</tbody></table></div>`);
  }
  if(data.gpp_collection&&data.gpp_collection.length){
    parts.push(`<div class="diff-section"><h3>GPP collection metadata (${data.gpp_collection.length})</h3><table class="diff-table"><thead><tr><th>Kind</th><th>Scope</th><th>Old</th><th>New</th></tr></thead><tbody>${data.gpp_collection.map(c=>`<tr><td class="${kindClass(c.kind)}">${escapeHtml(c.kind)}</td><td>${escapeHtml(c.scope)}</td><td>${escapeHtml(c.old?'present':'absent')}</td><td>${escapeHtml(c.new?'present':'absent')}</td></tr>`).join('')}</tbody></table></div>`);
  }
  if(data.metadata&&data.metadata.length){
    parts.push(`<div class="diff-section"><h3>Metadata (${data.metadata.length})</h3><table class="diff-table"><thead><tr><th>Field</th><th>Old</th><th>New</th></tr></thead><tbody>${data.metadata.map(m=>`<tr><td class="mono">${escapeHtml(m.field)}</td><td>${escapeHtml(String(m.old))}</td><td>${escapeHtml(String(m.new))}</td></tr>`).join('')}</tbody></table></div>`);
  }
  if(data.cse_metadata&&data.cse_metadata.length){
    parts.push(`<div class="diff-section"><h3>CSE metadata (${data.cse_metadata.length})</h3><table class="diff-table"><thead><tr><th>Kind</th><th>Old</th><th>New</th></tr></thead><tbody>${data.cse_metadata.map(c=>`<tr><td class="${kindClass(c.kind||'modified')}">${escapeHtml(c.kind||'modified')}</td><td>${escapeHtml(formatCse(c.old))}</td><td>${escapeHtml(formatCse(c.new))}</td></tr>`).join('')}</tbody></table></div>`);
  }
  if(data.conflicts.length){
    parts.push(`<div class="diff-section"><h3 class="diff-conflict">CONFLICT: Settings (${data.conflicts.length})</h3><table class="diff-table"><thead><tr><th>Key</th><th>Value name</th><th>Baseline</th><th>Draft</th><th>Observed</th></tr></thead><tbody>${data.conflicts.map(c=>{
      const ident=c.identity||[];
      const key=c.baseline?.key||c.draft?.key||c.observed?.key||ident[2]||'';
      const vn=c.baseline?.value_name||c.draft?.value_name||c.observed?.value_name||ident[3]||'';
      return `<tr class="diff-conflict"><td class="mono">${escapeHtml(key)}</td><td>${escapeHtml(vn)}</td><td>${escapeHtml(formatSettingValue(c.baseline))}</td><td>${escapeHtml(formatSettingValue(c.draft))}</td><td>${escapeHtml(formatSettingValue(c.observed))}</td></tr>`;
    }).join('')}</tbody></table></div>`);
  }
  if(data.security_filter_conflicts.length){
    parts.push(`<div class="diff-section"><h3 class="diff-conflict">CONFLICT: Security filters (${data.security_filter_conflicts.length})</h3><table class="diff-table"><thead><tr><th>Principal</th><th>Baseline</th><th>Draft</th><th>Observed</th></tr></thead><tbody>${data.security_filter_conflicts.map(c=>`<tr class="diff-conflict"><td class="mono">${escapeHtml(c.principal)}</td><td>${escapeHtml(formatSf(c.baseline))}</td><td>${escapeHtml(formatSf(c.draft))}</td><td>${escapeHtml(formatSf(c.observed))}</td></tr>`).join('')}</tbody></table></div>`);
  }
  if(data.wmi_filter_conflict){
    const c=data.wmi_filter_conflict;
    parts.push(`<div class="diff-section"><h3 class="diff-conflict">CONFLICT: WMI filter</h3><table class="diff-table"><thead><tr><th>Baseline</th><th>Draft</th><th>Observed</th></tr></thead><tbody><tr class="diff-conflict"><td>${escapeHtml(formatWmi(c.baseline))}</td><td>${escapeHtml(formatWmi(c.draft))}</td><td>${escapeHtml(formatWmi(c.observed))}</td></tr></tbody></table></div>`);
  }
  if(data.link_conflicts&&data.link_conflicts.length){
    parts.push(`<div class="diff-section"><h3 class="diff-conflict">CONFLICT: Links (${data.link_conflicts.length})</h3><table class="diff-table"><thead><tr><th>Target</th><th>Baseline</th><th>Draft</th><th>Observed</th></tr></thead><tbody>${data.link_conflicts.map(c=>`<tr class="diff-conflict"><td class="mono">${escapeHtml(c.identity)}</td><td>${escapeHtml(formatLink(c.baseline))}</td><td>${escapeHtml(formatLink(c.draft))}</td><td>${escapeHtml(formatLink(c.observed))}</td></tr>`).join('')}</tbody></table></div>`);
  }
  if(data.gpp_conflicts&&data.gpp_conflicts.length){
    const rows=data.gpp_conflicts.map(c=>{
      if(c.kind==='registry'){
        const label=(c.draft||c.observed||c.baseline||{}).key||c.identity||'';
        return `<tr class="diff-conflict"><td>GPP registry</td><td>${escapeHtml(c.scope)}</td><td class="mono">${escapeHtml(label)}</td><td>${escapeHtml(formatGppRegistry(c.baseline))}</td><td>${escapeHtml(formatGppRegistry(c.draft))}</td><td>${escapeHtml(formatGppRegistry(c.observed))}</td></tr>`;
      }
      const label=(c.draft||c.observed||c.baseline||{}).name||(c.identity&&c.identity[1])||'';
      return `<tr class="diff-conflict"><td>GPP group</td><td>${escapeHtml(c.scope)}</td><td class="mono">${escapeHtml(label)}</td><td>${escapeHtml(formatGppGroup(c.baseline))}</td><td>${escapeHtml(formatGppGroup(c.draft))}</td><td>${escapeHtml(formatGppGroup(c.observed))}</td></tr>`;
    }).join('');
    parts.push(`<div class="diff-section"><h3 class="diff-conflict">CONFLICT: GPP (${data.gpp_conflicts.length})</h3><table class="diff-table"><thead><tr><th>Kind</th><th>Scope</th><th>Name/Key</th><th>Baseline</th><th>Draft</th><th>Observed</th></tr></thead><tbody>${rows}</tbody></table></div>`);
  }
  if(data.metadata_conflicts&&data.metadata_conflicts.length){
    parts.push(`<div class="diff-section"><h3 class="diff-conflict">CONFLICT: Metadata (${data.metadata_conflicts.length})</h3><table class="diff-table"><thead><tr><th>Field</th><th>Baseline</th><th>Draft</th><th>Observed</th></tr></thead><tbody>${data.metadata_conflicts.map(c=>`<tr class="diff-conflict"><td class="mono">${escapeHtml(c.field)}</td><td>${escapeHtml(String(c.baseline))}</td><td>${escapeHtml(String(c.draft))}</td><td>${escapeHtml(String(c.observed))}</td></tr>`).join('')}</tbody></table></div>`);
  }
  if(data.cse_metadata_conflicts&&data.cse_metadata_conflicts.length){
    parts.push(`<div class="diff-section"><h3 class="diff-conflict">CONFLICT: CSE metadata (${data.cse_metadata_conflicts.length})</h3><table class="diff-table"><thead><tr><th>GUID</th><th>Side</th><th>Baseline</th><th>Draft</th><th>Observed</th></tr></thead><tbody>${data.cse_metadata_conflicts.map(c=>`<tr class="diff-conflict"><td class="mono">${escapeHtml(c.guid||'')}</td><td>${escapeHtml(c.side||'')}</td><td>${escapeHtml(formatCse(c.baseline))}</td><td>${escapeHtml(formatCse(c.draft))}</td><td>${escapeHtml(formatCse(c.observed))}</td></tr>`).join('')}</tbody></table></div>`);
  }
  if(data.gpp_reorder_conflicts&&data.gpp_reorder_conflicts.length){
    parts.push(`<div class="diff-section"><h3 class="diff-conflict">CONFLICT: GPP reorder (${data.gpp_reorder_conflicts.length})</h3><table class="diff-table"><thead><tr><th>Kind</th><th>Scope</th><th>Baseline order</th><th>Draft order</th><th>Observed order</th></tr></thead><tbody>${data.gpp_reorder_conflicts.map(c=>`<tr class="diff-conflict"><td>${escapeHtml(c.element_type||'')}</td><td>${escapeHtml(c.scope||'')}</td><td class="mono">${escapeHtml((c.baseline_order||[]).join(' → '))}</td><td class="mono">${escapeHtml((c.draft_order||[]).join(' → '))}</td><td class="mono">${escapeHtml((c.observed_order||[]).join(' → '))}</td></tr>`).join('')}</tbody></table></div>`);
  }
  if(data.gpp_collection_conflicts&&data.gpp_collection_conflicts.length){
    parts.push(`<div class="diff-section"><h3 class="diff-conflict">CONFLICT: GPP collection metadata (${data.gpp_collection_conflicts.length})</h3><table class="diff-table"><thead><tr><th>Scope</th><th>Baseline</th><th>Draft</th><th>Observed</th></tr></thead><tbody>${data.gpp_collection_conflicts.map(c=>`<tr class="diff-conflict"><td>${escapeHtml(c.scope||'')}</td><td>${escapeHtml(c.baseline?'modified':'absent')}</td><td>${escapeHtml(c.draft?'modified':'absent')}</td><td>${escapeHtml(c.observed?'modified':'absent')}</td></tr>`).join('')}</tbody></table></div>`);
  }
  if(!hasChanges&&!hasConflicts)parts.push('<div class="table-empty">No differences found</div>');
  target.innerHTML=parts.join('');
}
