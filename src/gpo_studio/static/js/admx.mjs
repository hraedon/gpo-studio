import {state,$,$$,escapeHtml} from './state.mjs';
import {api,toast} from './api.mjs';
import {loadList} from './render.mjs';

let admxLoaded=null,admxTimer=null,admxCatsLoaded=false;
export async function checkAdmx(){
  if(admxLoaded===null){try{const h=await api("/api/health");admxLoaded=h.admx_loaded===true}catch{admxLoaded=false}$("#admx-empty").hidden=admxLoaded;$("#admx-content").hidden=!admxLoaded}
  if(admxLoaded){if(!admxCatsLoaded){admxCatsLoaded=true;loadAdmxCategories()}loadAdmxResults($("#admx-search").value)}
}
async function loadAdmxResults(q){
  try{const data=await api(`/api/admx/search?q=${encodeURIComponent(q)}`);$("#admx-results").hidden=false;$("#admx-detail").hidden=true;
    $("#admx-results").innerHTML=data.items.length?data.items.map(p=>`<button class="admx-result" data-id="${escapeHtml(p.qualified_id||p.id)}"><div><strong>${escapeHtml(p.display_name)}</strong><span class="side ${p.class_==='Machine'?'computer':'user'}">${escapeHtml(p.class_)}</span></div><small class="mono">${escapeHtml(p.key)}</small><p>${escapeHtml((p.explain_text||"").slice(0,120))}${(p.explain_text||"").length>120?'…':''}</p></button>`).join(""):'<div class="table-empty">No policies found.</div>';
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
    $("#admx-detail").innerHTML=`<div class="admx-detail-head"><button class="quiet" id="admx-back">← Back</button><h3>${escapeHtml(p.display_name)}</h3></div><dl class="details"><dt>ID</dt><dd class="mono">${escapeHtml(p.id)}</dd>${p.namespace?`<dt>Namespace</dt><dd class="mono">${escapeHtml(p.namespace)}</dd>`:""}<dt>Class</dt><dd>${escapeHtml(p.class_)}</dd><dt>Key</dt><dd class="mono">${escapeHtml(p.key)}</dd><dt>Category</dt><dd>${escapeHtml(p.parent_category)||"—"}</dd><dt>Supported on</dt><dd>${escapeHtml(p.supported_on)||"—"}</dd><dt>Explanation</dt><dd>${escapeHtml(p.explain_text)||"—"}</dd>${p.elements&&p.elements.length?`<dt>Elements</dt><dd>${p.elements.map(e=>`<div class="mono">${escapeHtml(e.kind)}: ${escapeHtml(e.id)}</div>`).join("")}</dd>`:""}${p.presentation&&p.presentation.length?`<dt>Presentation</dt><dd>${p.presentation.map(e=>`<div>${escapeHtml(e.kind)}: ${escapeHtml(e.label||e.id)}</div>`).join("")}</dd>`:""}</dl>`;
    if(fields){const btn=document.createElement("button");btn.className="primary";btn.textContent="Configure policy";btn.id="admx-configure-btn";$("#admx-detail").appendChild(btn);btn.onclick=()=>openConfigureDialog(p.qualified_id||p.id,p.display_name,p.class_,fields)}
    $("#admx-back").onclick=()=>{$("#admx-detail").hidden=true;$("#admx-results").hidden=false};
  }catch(e){toast(e.message)}
}
async function loadAdmxCategories(){
  try{const data=await api("/api/admx/categories");$("#admx-categories").innerHTML=data.items.length?data.items.map(c=>`<div class="admx-cat">${escapeHtml(c.display_name)}<small class="mono">${escapeHtml(c.id)}</small></div>`).join(""):'<div class="table-empty">No categories.</div>';
  }catch(error){toast(error.message)}
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
export function initAdmx(){
$("#configure-form").onsubmit=async event=>{event.preventDefault();if(event.submitter&&event.submitter.value==="cancel"){event.currentTarget.closest("dialog").close();return}const f=event.currentTarget;const policyId=f.dataset.policyId;const policyClass=f.dataset.policyClass;
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
  try{await api(`/api/admx/policies/${encodeURIComponent(policyId)}/configure`,{method:"POST",body:JSON.stringify({gpo_guid:targetGuid,side,values,actor:"local-operator",reason:f.reason.value,expected_revision:targetGpo.revision})});
    $("#configure-dialog").close();await loadList(targetGuid);toast("Policy settings applied to GPO")}
  catch(error){toast(error.message)}
};
$("#admx-search").oninput=e=>{clearTimeout(admxTimer);admxTimer=setTimeout(()=>loadAdmxResults(e.target.value),250)};
}
