export const state={gpos:[],current:null,editorGeneration:0,validation:[],editingWarnings:[],policyHash:"",reviewHash:"",artifactCapabilities:{},pendingConflict:null,side:"all",gppScope:"computer",editingSetting:null,editingLink:null,editingFilter:null,editingGppGroup:null,editingGppRegistry:null};
export const $=selector=>document.querySelector(selector);
export const $$=selector=>[...document.querySelectorAll(selector)];
export const escapeHtml=value=>String(value??"").replace(/[&<>'"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
// A list snapshot may precede a mutation response already received by this tab.
// Track observations after its dispatch, without turning summaries into a
// permanent cache: a subsequent fresh listing can remove an absent object.
let summaryVersion=0;
const summaryVersions=new Map();
// Per-GUID ordering metadata lasts for this page, so even a very late UI
// continuation stays retired after fresh listings. It never bans fresh reads.
const deletionVersions=new Map(),observationVersions=new WeakMap();
export function getGpoGeneration(guid){return deletionVersions.get(guid)||0}
export function stampGpoResponse(payload,since){
  if(!payload||typeof payload!=="object")return;
  if(payload.gpo&&typeof payload.gpo==="object")observationVersions.set(payload.gpo,since);
  if(Array.isArray(payload.items))for(const item of payload.items)if(item&&typeof item==="object")observationVersions.set(item,since);
}
export function isGpoObservationCurrent(gpo){return Boolean(gpo)&&getGpoGeneration(gpo.guid)<=(observationVersions.get(gpo)??0)}
export function observeGpoDeleted(guid){
  deletionVersions.set(guid,++summaryVersion);
  summaryVersions.delete(guid);
  state.gpos=state.gpos.filter(gpo=>gpo.guid!==guid);
  if(state.current?.guid!==guid)return false;
  state.current=null;state.validation=[];state.editingWarnings=[];state.policyHash="";state.reviewHash="";state.artifactCapabilities={};state.pendingConflict=null;
  return true;
}
export function getGpoSummaryVersion(){return summaryVersion}
export function replaceGpoSummaries(items,since){
  const learned=state.gpos.filter(gpo=>(summaryVersions.get(gpo.guid)||0)>since);
  state.gpos=items.filter(gpo=>getGpoGeneration(gpo.guid)<=since);
  for(const gpo of learned){
    const existing=state.gpos.find(item=>item.guid===gpo.guid);
    if(existing){if(existing.revision<=gpo.revision)Object.assign(existing,gpo)}
    else state.gpos.push(gpo);
  }
  for(const [guid,version] of summaryVersions)if(version<=since)summaryVersions.delete(guid);
}
export function updateGpoSummary(gpo){
  if(!isGpoObservationCurrent(gpo))return false;
  const fields={guid:gpo.guid,name:gpo.name,status:gpo.status,revision:gpo.revision,updated_at:gpo.updated_at};
  const existing=state.gpos.find(item=>item.guid===gpo.guid);
  if(existing){if(existing.revision>gpo.revision)return false;Object.assign(existing,fields)}else state.gpos.push(fields);
  summaryVersions.set(gpo.guid,++summaryVersion);
  return true;
}
export function getKnownGpoRevision(guid){
  return Math.max(state.gpos.find(gpo=>gpo.guid===guid)?.revision||0,state.current?.guid===guid?state.current.revision:0);
}
export function applyPayload(payload){
  if(!updateGpoSummary(payload.gpo))return false;
  state.current=payload.gpo;
  state.validation=payload.validation||[];
  state.editingWarnings=payload.editing_warnings||[];
  state.policyHash=payload.policy_semantic_sha256||"";
  state.reviewHash=payload.review_model_sha256||"";
  state.artifactCapabilities=payload.artifact_capabilities||{};
  return true;
}
