import {api,setStatus,showPersistentError} from './api.mjs';
import {getKnownGpoRevision} from './state.mjs';

function fieldCandidates(path){
  const parts=String(path||"").split(/[/.]|\[|\]/).filter(Boolean);
  const last=parts.at(-1)||"";
  const aliases={value_name:"value_name",registry_type:"registry_type",ilt_filter:"value",wmi_filter:"query"};
  return [path,last,aliases[last]].filter(Boolean)
}

export function clearFormErrors(form){
  const errorIds=new Set([...form.querySelectorAll(".field-error")].map(element=>element.id));
  form.querySelectorAll(".form-error,.field-error").forEach(element=>element.remove());
  form.querySelectorAll("[aria-invalid='true']").forEach(field=>{
    field.removeAttribute("aria-invalid");
    const descriptions=(field.getAttribute("aria-describedby")||"").split(/\s+/).filter(id=>id&&!errorIds.has(id));
    if(descriptions.length)field.setAttribute("aria-describedby",descriptions.join(" "));
    else field.removeAttribute("aria-describedby")
  })
}

function findIssueField(form,path){
  for(const name of fieldCandidates(path)){
    const escaped=globalThis.CSS?.escape?CSS.escape(name):name.replaceAll('"','\\"');
    const field=form.querySelector(`[name="${escaped}"],[data-field="${escaped}"]`);
    if(field)return field
  }
  return null
}

export function showFormErrors(form,error){
  clearFormErrors(form);
  const issues=error.issues?.length?error.issues:[{message:error.message||"The request failed."}];
  const summary=document.createElement("div");
  summary.className="form-error";
  summary.setAttribute("role","alert");
  summary.tabIndex=-1;
  const heading=document.createElement("strong");
  heading.textContent=issues.length===1?"Please correct this problem:":`Please correct these ${issues.length} problems:`;
  summary.appendChild(heading);
  const list=document.createElement("ul");
  issues.forEach((issue,index)=>{
    const item=document.createElement("li");
    const field=findIssueField(form,issue.path);
    const message=issue.message||issue.msg||JSON.stringify(issue);
    if(field){
      if(!field.id)field.id=`field-${form.id||"form"}-${index}`;
      const errorId=`${field.id}-error-${index}`;
      const link=document.createElement("a");
      link.href=`#${field.id}`;
      link.textContent=message;
      link.onclick=()=>queueMicrotask(()=>field.focus());
      item.appendChild(link);
      field.setAttribute("aria-invalid","true");
      field.setAttribute("aria-describedby",[...new Set([...(field.getAttribute("aria-describedby")||"").split(/\s+/).filter(Boolean),errorId])].join(" "));
      const detail=document.createElement("span");
      detail.id=errorId;
      detail.className="sr-only field-error";
      detail.textContent=message;
      field.insertAdjacentElement("afterend",detail)
    }else item.textContent=message;
    list.appendChild(item)
  });
  summary.appendChild(list);
  const container=form.querySelector(":scope > .dialog-body")||form;
  container.prepend(summary);
  summary.focus()
}

function appendDetail(container,label,value){
  const row=document.createElement("p");
  const strong=document.createElement("strong");
  strong.textContent=`${label}: `;
  row.append(strong,document.createTextNode(String(value)));
  container.appendChild(row)
}

function changeCount(diff){
  if(!diff)return 0;
  return ["settings","links","security_filters","gpp_groups","gpp_registry","metadata","cse_metadata","conflicts","link_conflicts","security_filter_conflicts","gpp_conflicts","metadata_conflicts","cse_metadata_conflicts","fdeploy","fdeploy_conflicts"].reduce((count,key)=>count+(Array.isArray(diff[key])?diff[key].length:0),0)+(diff.wmi_filter?1:0)+(diff.wmi_filter_conflict?1:0)
}

// A review owns its original editor and this opening of the shared dialog.
// Old reads/finalizers must not write into a reopened comparison.
const conflictReviews=new WeakMap();

export async function reconcileConflict(form,error,{onCurrent,isActive=()=>true,fieldNames=null,targetGuid=null,preserveEditorTarget=false}={}){
  if(error.status!==409||!Number.isInteger(error.expectedRevision)||!Number.isInteger(error.currentRevision))return false;
  const currentGuid=targetGuid||form.dataset.gpoGuid||location.pathname.split("/").filter(Boolean).at(-1);
  const guid=currentGuid&&currentGuid!=="undefined"?currentGuid:null;
  const dialog=document.querySelector("#conflict-dialog"),details=document.querySelector("#conflict-details");
  if(!guid||!dialog||!details||!isActive()){showPersistentError(error.message);return true}
  const editor=form.closest("dialog"),editorSession=editor?.editorSession;
  const reload=document.querySelector("#conflict-reload"),reapply=document.querySelector("#conflict-reapply");
  const review={latest:null,pending:false,session:null};
  conflictReviews.set(dialog,review);
  const active=()=>conflictReviews.get(dialog)===review&&dialog.open&&dialog.editorSession===review.session&&editor?.editorSession===editorSession&&isActive();
  const floor=()=>Math.max(error.currentRevision,review.latest?.gpo.revision||0,getKnownGpoRevision(guid));
  const unsaved=(fieldNames||[...new FormData(form).keys()]).filter(name=>!name.toLowerCase().includes("actor"));
  const renderComparison=(latest,diff,notice="")=>{
    clearFormErrors(dialog.querySelector("form"));details.replaceChildren();
    appendDetail(details,"Your form revision",error.expectedRevision);
    appendDetail(details,"Current workspace revision",latest.gpo.revision);
    appendDetail(details,"Changes saved since you opened the form",changeCount(diff));
    appendDetail(details,"Unsaved fields retained",unsaved.join(", ")||"form action");
    if(notice){const message=document.createElement("p");message.setAttribute("role","status");message.textContent=notice;details.appendChild(message)}
    review.latest=latest;
  };
  const readCurrent=async(withDiff)=>{
    for(let attempt=0;attempt<2;attempt++){
      const latest=await api(`/api/gpos/${encodeURIComponent(guid)}`,{cache:"no-store"});
      if(!active())return null;
      if(latest.gpo.guid!==guid||!Number.isInteger(latest.gpo.revision))throw new Error("The current policy could not be verified. Your unsaved values are retained.");
      let diff=null;
      if(latest.gpo.revision>=floor()){
        if(withDiff&&error.expectedRevision>0&&latest.gpo.revision>error.expectedRevision){
          diff=await api(`/api/gpos/${encodeURIComponent(guid)}/revisions/diff?from_revision=${error.expectedRevision}&to_revision=${latest.gpo.revision}`,{cache:"no-store"});
          if(!active())return null;
        }
        // Newer data can arrive during either the full read or the diff read.
        if(latest.gpo.revision>=floor())return{latest,diff};
      }
    }
    throw new Error("The policy changed again while loading its current revision. Your unsaved values are retained. Try Load current revision or Review and reapply again.");
  };
  const usable=result=>{
    if(!result||!active())return false;
    if(result.latest.gpo.revision<floor())throw new Error("The policy changed again. Your unsaved values are retained. Refresh the comparison before continuing.");
    return true;
  };
  const run=async(operation)=>{
    if(!active()||review.pending)return;
    review.pending=true;reload.disabled=true;reapply.disabled=true;
    clearFormErrors(dialog.querySelector("form"));
    try{await operation()}
    catch(fetchError){if(active()){
      // Preserve an existing comparison when a refresh fails.
      if(!review.latest)details.textContent="The current revision could not be loaded. Your unsaved values are retained. Choose an action to try again, or keep editing.";
      showFormErrors(dialog.querySelector("form"),fetchError)
    }}
    finally{if(active()){review.pending=false;reload.disabled=false;reapply.disabled=false}}
  };
  reload.onclick=()=>run(async()=>{
    // Discard is explicit consent to load the authoritative snapshot now,
    // rather than applying the potentially obsolete comparison payload.
    const result=await readCurrent(false);if(!usable(result))return;
    onCurrent?.(result.latest,{discardForm:true});
    dialog.close();editor?.close();
    setStatus(`Loaded revision ${result.latest.gpo.revision}. Your unsaved values were discarded.`)
  });
  reapply.onclick=()=>run(async()=>{
    if(!review.latest||review.latest.gpo.revision<floor()){
      const result=await readCurrent(true);if(!usable(result))return;
      renderComparison(result.latest,result.diff,"The policy changed again. Review this refreshed comparison, then choose Review and reapply again to keep your values.");
      details.focus();return;
    }
    const latest=review.latest;
    // Only the snapshot actually displayed may advance the editor's CAS target.
    if(onCurrent?.(latest,{discardForm:false})===false){
      dialog.close();setStatus("");form.querySelector(".form-error")?.focus();return;
    }
    if(editor&&!preserveEditorTarget)editor.editorTarget={guid:latest.gpo.guid,revision:latest.gpo.revision,name:latest.gpo.name};
    dialog.close();
    setStatus(`Your kept values are shown against revision ${latest.gpo.revision}. Submit again to reapply them.`);
    form.querySelector("button[value='default'],button[type='submit']")?.focus()
  });
  details.textContent="Loading the current revision. Your unsaved values are retained.";
  reload.disabled=false;reapply.disabled=false;
  dialog.showModal();review.session=dialog.editorSession;
  // The write has already failed. Release its pending controls now so Keep
  // editing is immediately usable even while this owned comparison read waits.
  void run(async()=>{
    const result=await readCurrent(true);if(usable(result))renderComparison(result.latest,result.diff);
  });
  return true
}

export async function handleFormFailure(form,error,options={}){
  if(await reconcileConflict(form,error,options))return;
  if(!options.isActive||options.isActive())showFormErrors(form,error);
  else showPersistentError(error.message)
}
