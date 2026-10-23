export const state={gpos:[],current:null,editorGeneration:0,validation:[],policyHash:"",reviewHash:"",artifactCapabilities:{},pendingConflict:null,side:"all",gppScope:"computer",editingSetting:null,editingLink:null,editingFilter:null,editingGppGroup:null,editingGppRegistry:null};
export const $=selector=>document.querySelector(selector);
export const $$=selector=>[...document.querySelectorAll(selector)];
export const escapeHtml=value=>String(value??"").replace(/[&<>'"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
// A list snapshot may precede a mutation response already received by this tab.
// Track observations after its dispatch, without turning summaries into a
// permanent cache: a subsequent fresh listing can remove an absent object.
let summaryVersion=0;
const summaryVersions=new Map();
export function getGpoSummaryVersion(){return summaryVersion}
export function replaceGpoSummaries(items,since){
  const learned=state.gpos.filter(gpo=>(summaryVersions.get(gpo.guid)||0)>since);
  state.gpos=items;
  for(const gpo of learned){
    const existing=state.gpos.find(item=>item.guid===gpo.guid);
    if(existing){if(existing.revision<=gpo.revision)Object.assign(existing,gpo)}
    else state.gpos.push(gpo);
  }
  for(const [guid,version] of summaryVersions)if(version<=since)summaryVersions.delete(guid);
}
export function updateGpoSummary(gpo){
  const fields={guid:gpo.guid,name:gpo.name,status:gpo.status,revision:gpo.revision,updated_at:gpo.updated_at};
  const existing=state.gpos.find(item=>item.guid===gpo.guid);
  if(existing){if(existing.revision>gpo.revision)return;Object.assign(existing,fields)}else state.gpos.push(fields);
  summaryVersions.set(gpo.guid,++summaryVersion);
}
export function getKnownGpoRevision(guid){
  return Math.max(state.gpos.find(gpo=>gpo.guid===guid)?.revision||0,state.current?.guid===guid?state.current.revision:0);
}
export function getKnownGpoRevision(guid){
  return Math.max(state.gpos.find(gpo=>gpo.guid===guid)?.revision||0,state.current?.guid===guid?state.current.revision:0);
}
export function applyPayload(payload){
  updateGpoSummary(payload.gpo);
  state.current=payload.gpo;
  state.validation=payload.validation||[];
  state.policyHash=payload.policy_semantic_sha256||"";
  state.reviewHash=payload.review_model_sha256||"";
  state.artifactCapabilities=payload.artifact_capabilities||{};
}
