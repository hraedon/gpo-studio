export const state={gpos:[],current:null,validation:[],policyHash:"",reviewHash:"",artifactCapabilities:{},pendingConflict:null,side:"all",gppScope:"computer",editingSetting:null,editingLink:null,editingFilter:null,editingGppGroup:null,editingGppRegistry:null};
export const $=selector=>document.querySelector(selector);
export const $$=selector=>[...document.querySelectorAll(selector)];
export const escapeHtml=value=>String(value??"").replace(/[&<>'"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
export function updateGpoSummary(gpo){
  const fields={guid:gpo.guid,name:gpo.name,status:gpo.status,revision:gpo.revision,updated_at:gpo.updated_at};
  const existing=state.gpos.find(item=>item.guid===gpo.guid);
  if(existing){if(existing.revision<=gpo.revision)Object.assign(existing,fields)}else state.gpos.push(fields);
}
export function applyPayload(payload){
  updateGpoSummary(payload.gpo);
  state.current=payload.gpo;
  state.validation=payload.validation||[];
  state.policyHash=payload.policy_semantic_sha256||"";
  state.reviewHash=payload.review_model_sha256||"";
  state.artifactCapabilities=payload.artifact_capabilities||{};
}
