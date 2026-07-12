import {state,$,escapeHtml} from './state.mjs';
import {api,toast} from './api.mjs';

function formatSettingValue(s){if(!s)return '—';if(s.action==='delete')return 'Delete value';if(Array.isArray(s.value))return s.value.join(' · ');return String(s.value)}
function formatSf(sf){if(!sf)return '—';return `${sf.permission} · ${sf.target_type||'group'} · ${sf.inheritable?'inheritable':'not inheritable'}`}
function formatWmi(w){if(!w)return '—';return `${w.name}: ${w.query}`}
function formatLink(l){if(!l)return '—';return `${l.target} · ${l.enabled?'enabled':'disabled'} · ${l.enforced?'enforced':'not enforced'} · order ${l.order}`}
function kindClass(kind){return kind==='added'?'diff-added':kind==='removed'?'diff-removed':'diff-modified'}

export function loadDiffSelectors(){
  const opts=state.gpos.map(g=>`<option value="${escapeHtml(g.guid)}">${escapeHtml(g.name)} (r${g.revision})</option>`).join('');
  ['diff-baseline','diff-draft','diff-observed'].forEach(id=>{const sel=$('#'+id),prev=sel.value;sel.innerHTML=opts;if(prev)sel.value=prev});
}

export function initDiff(){
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

function renderDiff(data){
  const parts=[];
  const hasChanges=data.settings.length||data.security_filters.length||data.wmi_filter||(data.links&&data.links.length);
  const hasConflicts=data.conflicts.length||data.security_filter_conflicts.length||data.wmi_filter_conflict;
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
  if(!hasChanges&&!hasConflicts)parts.push('<div class="table-empty">No differences found</div>');
  $('#diff-results').innerHTML=parts.join('');
}
