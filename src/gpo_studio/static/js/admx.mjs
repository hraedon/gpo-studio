import {state,$,$$,escapeHtml,applyPayload} from './state.mjs';
import {api,toast,showPersistentError} from './api.mjs';
import {clearFormErrors,handleFormFailure,showFormErrors} from './errors.mjs';
import {loadList,renderList,renderAll} from './render.mjs';
import {resetEditor,beginMutation,isCurrentEditor,isSameEditor,mutationFailed,endMutation,updateMutationList} from './mutation-context.mjs';

import {catalogueEpoch as revisionEpoch,observeCatalogue,observeCatalogueConflict,reviewedCatalogueIsCurrent,onCatalogueChange,initCatalogueMonitoring,CATALOGUE_REVIEW_MESSAGE} from './catalogue-context.mjs';

// Each option represents the revision shown when this editor opened. Sidebar
// refreshes and older saves may update live summaries, but cannot consent to a
// newer target revision on behalf of this form.
const configureTargets=new WeakMap();
function targetSnapshot(gpo){return Object.freeze({guid:gpo.guid,name:gpo.name,revision:gpo.revision})}

function markConfigureStale(){
  const form=$("#configure-form");
  form.dataset.catalogueStale="true";
  showFormErrors(form,{message:CATALOGUE_REVIEW_MESSAGE});
  form.querySelector('button[value="default"]').disabled=true;
}

let admxLoaded=null,admxTimer=null,admxCatsLoaded=false,admxRequest=0,catalogueEpoch=0;
export function resetAdmxCatalogue(){catalogueEpoch++;admxLoaded=null;admxCatsLoaded=false;admxRequest++;clearTimeout(admxTimer);$("#admx-detail").hidden=true;$("#admx-detail").replaceChildren();$("#admx-results").replaceChildren();$("#admx-categories").replaceChildren()}
export async function checkAdmx(){
  const epoch=catalogueEpoch,started=revisionEpoch();
  if(admxLoaded===null){
    try{const h=await api("/api/health");if(!observeCatalogue(h,started)||epoch!==catalogueEpoch)return;admxLoaded=h.admx_loaded===true}
    catch(error){if(epoch===catalogueEpoch)showPersistentError(error.message);return}
    $("#admx-empty").hidden=admxLoaded;$("#admx-content").hidden=!admxLoaded;
  }
  if(admxLoaded){if(!admxCatsLoaded)loadAdmxCategories();loadAdmxResults($("#admx-search").value)}
}
async function loadAdmxResults(q){
  const request=++admxRequest,started=revisionEpoch();
  try{const data=await api(`/api/admx/search?q=${encodeURIComponent(q)}`);if(!observeCatalogue(data,started)||request!==admxRequest)return;$("#admx-results").hidden=false;$("#admx-detail").hidden=true;
    $("#admx-results").innerHTML=data.items.length?data.items.map(p=>`<button class="admx-result" data-id="${escapeHtml(p.qualified_id||p.id)}"><div><strong>${escapeHtml(p.display_name)}</strong><span class="side ${p.class_==='Machine'?'computer':'user'}">${escapeHtml(p.class_)}</span></div><small class="mono">${escapeHtml(p.key)}</small><p>${escapeHtml((p.explain_text||"").slice(0,120))}${(p.explain_text||"").length>120?'…':''}</p></button>`).join(""):'<div class="table-empty">No policies found.</div>';
    $$(".admx-result").forEach(el=>el.onclick=()=>loadAdmxDetail(el.dataset.id));
  }catch(e){showPersistentError(e.message)}
}
async function loadAdmxDetail(id){
  clearTimeout(admxTimer);
  const request=++admxRequest,started=revisionEpoch();
  try{const p=await api(`/api/admx/policies/${encodeURIComponent(id)}`);if(!observeCatalogue(p,started)||request!==admxRequest)return;$("#admx-results").hidden=true;$("#admx-detail").hidden=false;
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
    // Always offered, including for policies with no elements: a plain on/off
    // policy is the most common kind, and Enabled/Disabled/Not configured is
    // exactly what it needs. Previously the button only rendered when the
    // policy had element fields, so such policies could not be configured.
    {const btn=document.createElement("button");btn.className="primary";btn.textContent="Configure policy";btn.id="admx-configure-btn";$("#admx-detail").appendChild(btn);btn.onclick=()=>openConfigureDialog(p.qualified_id||p.id,p.display_name,p.class_,fields,p.catalogue_revision)}
    $("#admx-back").onclick=()=>{admxRequest++;$("#admx-detail").hidden=true;$("#admx-results").hidden=false};
  }catch(e){showPersistentError(e.message)}
}
async function loadAdmxCategories(){
  const epoch=catalogueEpoch,started=revisionEpoch();
  try{const data=await api("/api/admx/categories");if(!observeCatalogue(data,started)||epoch!==catalogueEpoch)return;admxCatsLoaded=true;$("#admx-categories").innerHTML=data.items.length?data.items.map(c=>`<div class="admx-cat">${escapeHtml(c.display_name)}<small class="mono">${escapeHtml(c.id)}</small></div>`).join(""):'<div class="table-empty">No categories.</div>';
  }catch(error){if(epoch===catalogueEpoch)showPersistentError(error.message)}
}
// Element options only apply when the policy is Enabled: GPMC greys out the
// options panel for Disabled and Not Configured, and the API rejects element
// values in those states rather than silently dropping them.
function syncConfigureState(){
  const enabled=$("#configure-state").value==="enabled";
  const options=$("#configure-options");
  if(!options)return;
  options.hidden=!enabled;
  $$("[data-elem-id]",options).forEach(el=>{el.disabled=!enabled});
}
function openConfigureDialog(policyId,policyName,policyClass,fieldsHtml,reviewedRevision){
  if(!reviewedCatalogueIsCurrent(reviewedRevision)){showPersistentError(CATALOGUE_REVIEW_MESSAGE);return}
  const form=$("#configure-form");resetEditor(form);form.reset();clearFormErrors(form);
  $("#configure-title").textContent=`Configure: ${policyName}`;
  const gpoSelect=$("#configure-target-gpo");
  const targets=state.gpos.filter(g=>g.status!=="archived");
  configureTargets.set(form,new Map(targets.map(g=>[g.guid,targetSnapshot(g)])));
  gpoSelect.innerHTML=targets.map(g=>`<option value="${escapeHtml(g.guid)}">${escapeHtml(g.name)} (r${g.revision})</option>`).join("");
  if(targets.some(g=>g.guid===state.current?.guid))gpoSelect.value=state.current.guid;
  else gpoSelect.value="";
  $("#configure-target-note").textContent=targets.length?"Choose the draft to change. Archived policies are read-only.":"Create a draft or fork an archived policy before applying settings.";
  form.querySelector('button[value="default"]').disabled=!targets.length;
  const sideHtml=policyClass==="Both"?`<label class="config-field">Configuration side<select id="configure-side"><option value="computer">Computer</option><option value="user">User</option></select></label>`:"";
  const stateHtml=`<label class="config-field">Policy state<select id="configure-state"><option value="enabled">Enabled</option><option value="disabled">Disabled</option><option value="not_configured">Not configured</option></select></label>`;
  const optionsHtml=fieldsHtml?`<div id="configure-options">${fieldsHtml}</div>`:"";
  $("#configure-fields").innerHTML=stateHtml+sideHtml+optionsHtml;
  $("#configure-state").onchange=syncConfigureState;
  syncConfigureState();
  form.dataset.catalogueRevision=reviewedRevision;delete form.dataset.catalogueStale;
  form.dataset.policyId=policyId;
  form.dataset.policyClass=policyClass;
  $("#configure-dialog").showModal();
}
export function initAdmx(){
initCatalogueMonitoring();
onCatalogueChange(()=>{
  resetAdmxCatalogue();
  if($("#configure-dialog").open)markConfigureStale();
  if(!$("#panel-admx").hidden)queueMicrotask(()=>checkAdmx());
});
$("#configure-form").onsubmit=async event=>{event.preventDefault();if(event.submitter&&event.submitter.value==="cancel"){event.currentTarget.closest("dialog").close();return}const f=event.currentTarget;if(f.dataset.catalogueStale||!reviewedCatalogueIsCurrent(f.dataset.catalogueRevision)){markConfigureStale();return}clearFormErrors(f);const policyId=f.dataset.policyId;const policyClass=f.dataset.policyClass;
  const targetGuid=$("#configure-target-gpo").value;if(!targetGuid){toast("Select a target GPO");return}
  const targetSnapshots=configureTargets.get(f);
  const targetGpo=targetSnapshots?.get(targetGuid);if(!targetGpo){toast("Close and reopen this editor to review the target GPO");return}
  let side=policyClass==="Machine"?"computer":policyClass==="User"?"user":$("#configure-side").value;
  const policyState=$("#configure-state").value;
  const values={};
  let ok=true;
  // Only Enabled carries element values; sending them in another state is a
  // 422 by design, so don't collect them.
  if(policyState==="enabled")$$("[data-elem-id]",f).forEach(el=>{
    if(!ok)return;const kind=el.dataset.kind;const id=el.dataset.elemId;
    if(kind==="boolean"){values[id]=el.checked}
    else if(kind==="decimal"){const n=parseInt(el.value,10);if(!Number.isFinite(n)){toast(`Invalid number for ${id}`);ok=false;return}values[id]=n}
    else if(kind==="text"||kind==="enum"){values[id]=el.value}
    else if(kind==="multitext"||kind==="list"){values[id]=el.value.split(/\r?\n/).filter(Boolean)}
  });
  if(!ok)return;
  const context=beginMutation(f,{target:targetGpo});if(!context)return;
  const reviewedRevision=f.dataset.catalogueRevision,started=revisionEpoch();
  try{const data=await api(`/api/admx/policies/${encodeURIComponent(policyId)}/configure`,{method:"POST",body:JSON.stringify({gpo_guid:targetGuid,side,values,state:policyState,actor:"local-operator",reason:f.reason.value,expected_revision:context.revision,expected_catalogue_revision:reviewedRevision})});
    updateMutationList(data,renderList);
    const selectTarget=isSameEditor(context);if(isCurrentEditor(context))context.dialog.close();if(selectTarget)await loadList(targetGuid)
    toast(policyState==="not_configured"?"Policy set to Not configured":"Policy settings applied to GPO")}
  catch(error){
    if(error.code==="catalogue_conflict"||!reviewedCatalogueIsCurrent(reviewedRevision)){
      observeCatalogueConflict(error,started);
      if(isCurrentEditor(context))markConfigureStale();else showPersistentError(CATALOGUE_REVIEW_MESSAGE);
    }else await mutationFailed(context,error,(form,err)=>handleFormFailure(form,err,{fieldNames:context.fieldNames,targetGuid:context.guid,preserveEditorTarget:true,isActive:()=>isSameEditor(context),onCurrent:(latest,{discardForm})=>{
      updateMutationList(latest,renderList);
      if(discardForm){applyPayload(latest);renderAll();renderList();return}
      const option=[...$("#configure-target-gpo").options].find(item=>item.value===latest.gpo.guid);
      if(latest.gpo.status==="archived"){
        if(option)option.disabled=true;
        $("#configure-target-gpo").value="";
        showFormErrors(form,{message:"The target policy is archived. Choose another draft or fork it before applying settings."});
        return false;
      }
      // Only explicit Review and reapply advances this target's snapshot.
      targetSnapshots.set(latest.gpo.guid,targetSnapshot(latest.gpo));
      if(option)option.textContent=`${latest.gpo.name} (r${latest.gpo.revision})`;
    }}));
  }finally{
    endMutation(context);
    if(isCurrentEditor(context)&&(f.dataset.catalogueStale||!reviewedCatalogueIsCurrent(reviewedRevision)))markConfigureStale();
  }
};
$("#admx-search").oninput=e=>{clearTimeout(admxTimer);admxTimer=setTimeout(()=>loadAdmxResults(e.target.value),250)};
}
