export const state={gpos:[],current:null,validation:[],policyHash:"",reviewHash:"",artifactCapabilities:{},pendingConflict:null,side:"all",gppScope:"computer",editingSetting:null,editingLink:null,editingFilter:null,editingGppGroup:null,editingGppRegistry:null};
export const $=selector=>document.querySelector(selector);
export const $$=selector=>[...document.querySelectorAll(selector)];
export const escapeHtml=value=>String(value??"").replace(/[&<>'"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
export function applyPayload(payload){
  state.current=payload.gpo;
  state.validation=payload.validation||[];
  state.policyHash=payload.policy_semantic_sha256||"";
  state.reviewHash=payload.review_model_sha256||"";
  state.artifactCapabilities=payload.artifact_capabilities||{};
}
