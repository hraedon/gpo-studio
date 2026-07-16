import {api,setStatus,showPersistentError} from './api.mjs';

function fieldCandidates(path){
  const parts=String(path||"").split(/[/.]|\[|\]/).filter(Boolean);
  const last=parts.at(-1)||"";
  const aliases={value_name:"value_name",registry_type:"registry_type",ilt_filter:"value",wmi_filter:"query"};
  return [path,last,aliases[last]].filter(Boolean)
}

export function clearFormErrors(form){
  form.querySelectorAll(".form-error,.field-error").forEach(element=>element.remove());
  form.querySelectorAll("[aria-invalid='true']").forEach(field=>{field.removeAttribute("aria-invalid");field.removeAttribute("aria-describedby")})
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
      const errorId=`${field.id}-error`;
      const link=document.createElement("a");
      link.href=`#${field.id}`;
      link.textContent=message;
      link.onclick=()=>queueMicrotask(()=>field.focus());
      item.appendChild(link);
      field.setAttribute("aria-invalid","true");
      field.setAttribute("aria-describedby",errorId);
      const detail=document.createElement("span");
      detail.id=errorId;
      detail.className="sr-only field-error";
      detail.textContent=message;
      field.insertAdjacentElement("afterend",detail)
    }else item.textContent=message;
    list.appendChild(item)
  });
  summary.appendChild(list);
  form.insertBefore(summary,form.firstChild);
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
  return ["settings","links","security_filters","gpp_groups","gpp_registry","metadata","cse_metadata","conflicts","link_conflicts","security_filter_conflicts","gpp_conflicts","metadata_conflicts","cse_metadata_conflicts"].reduce((count,key)=>count+(Array.isArray(diff[key])?diff[key].length:0),0)+(diff.wmi_filter?1:0)+(diff.wmi_filter_conflict?1:0)
}

export async function reconcileConflict(form,error,{onCurrent}={}){
  if(error.status!==409)return false;
  const currentGuid=form.dataset.gpoGuid||location.pathname.split("/").filter(Boolean).at(-1);
  const guid=currentGuid&&currentGuid!=="undefined"?currentGuid:null;
  let latest=null,diff=null;
  try{
    if(guid){
      latest=await api(`/api/gpos/${encodeURIComponent(guid)}`);
      const from=error.expectedRevision;
      const to=latest.gpo.revision;
      if(Number.isInteger(from)&&from>0&&to>from){
        diff=await api(`/api/gpos/${encodeURIComponent(guid)}/revisions/diff?from_revision=${from}&to_revision=${to}`)
      }
    }
  }catch(fetchError){showPersistentError(fetchError.message)}

  const dialog=document.querySelector("#conflict-dialog");
  const details=document.querySelector("#conflict-details");
  if(!dialog||!details||!latest){showPersistentError(error.message);return true}
  details.replaceChildren();
  appendDetail(details,"Your form revision",error.expectedRevision??"unknown");
  appendDetail(details,"Current workspace revision",latest.gpo.revision);
  appendDetail(details,"Server-side changes since your form opened",changeCount(diff));
  const unsaved=[...new FormData(form).entries()].filter(([name])=>!name.toLowerCase().includes("actor"));
  appendDetail(details,"Unsaved fields retained",unsaved.map(([name])=>name).join(", ")||"form action");

  const reload=document.querySelector("#conflict-reload");
  const reapply=document.querySelector("#conflict-reapply");
  reload.onclick=()=>{
    onCurrent?.(latest,{discardForm:true});
    dialog.close();
    form.closest("dialog")?.close();
    setStatus(`Loaded revision ${latest.gpo.revision}; unsaved form values were discarded by your choice.`)
  };
  reapply.onclick=()=>{
    onCurrent?.(latest,{discardForm:false});
    dialog.close();
    setStatus(`Reviewing your retained values against revision ${latest.gpo.revision}. Submit again to reapply.`);
    form.querySelector("button[value='default'],button[type='submit']")?.focus()
  };
  dialog.showModal();
  return true
}

export async function handleFormFailure(form,error,options={}){
  if(await reconcileConflict(form,error,options))return;
  showFormErrors(form,error)
}
