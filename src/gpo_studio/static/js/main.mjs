import {state,$,$$} from './state.mjs';
import {api,toast,clearPersistentError} from './api.mjs';
import {clearFormErrors,showFormErrors} from './errors.mjs';
import {loadList,renderList,renderSettings,loadHistory} from './render.mjs';
import {openGpo,openSetting,openLink,openFilter,openWmi,openWmiCatalogue,openEstate,openFork,syncSettingForm,initForms} from './forms.mjs';
import {initGpp} from './gpp.mjs';
import {checkAdmx,initAdmx} from './admx.mjs';
import {initDiff,loadDiffSelectors} from './diff.mjs';

const tabs=$$(".tab[role='tab']");

function reportPersistentError(message){
  const summary=$("#error-summary");
  summary.textContent=`Error: ${message}`;
  summary.hidden=false;
  summary.focus();
}

function setAppStatus(message){
  const status=$("#app-status");
  status.textContent=message;
  status.hidden=!message;
}

function activateTab(tab,{moveFocus=false}={}){
  tabs.forEach(item=>{
    const selected=item===tab;
    item.classList.toggle("active",selected);
    item.setAttribute("aria-selected",String(selected));
    item.tabIndex=selected?0:-1;
    const panel=document.getElementById(item.getAttribute("aria-controls"));
    if(panel){panel.classList.toggle("active",selected);panel.hidden=!selected}
  });
  if(moveFocus){tab.focus();tab.scrollIntoView({block:"nearest",inline:"nearest"})}
  if(tab.dataset.tab==="history")loadHistory().catch(error=>reportPersistentError(error.message));
  if(tab.dataset.tab==="diff")loadDiffSelectors();
  if(tab.dataset.tab==="admx")checkAdmx();
}

tabs.forEach((tab,index)=>{
  tab.addEventListener("click",()=>activateTab(tab));
  tab.addEventListener("keydown",event=>{
    let nextIndex=null;
    if(event.key==="ArrowRight")nextIndex=(index+1)%tabs.length;
    if(event.key==="ArrowLeft")nextIndex=(index-1+tabs.length)%tabs.length;
    if(event.key==="Home")nextIndex=0;
    if(event.key==="End")nextIndex=tabs.length-1;
    if(nextIndex===null)return;
    event.preventDefault();
    activateTab(tabs[nextIndex],{moveFocus:true});
  });
});

function focusableElements(dialog){
  const selector='a[href],button:not([disabled]),input:not([disabled]):not([type="hidden"]),select:not([disabled]),textarea:not([disabled]),summary,[tabindex]:not([tabindex="-1"])';
  return [...dialog.querySelectorAll(selector)].filter(element=>element.getClientRects().length>0&&element.getAttribute("aria-hidden")!=="true");
}

function initialiseDialog(dialog){
  const nativeShowModal=dialog.showModal.bind(dialog);
  dialog.showModal=()=>{
    if(dialog.open)return;
    const active=document.activeElement;
    dialog.returnFocus=active instanceof HTMLElement?active:null;
    nativeShowModal();
    requestAnimationFrame(()=>{
      const preferred=dialog.querySelector("[autofocus],[data-initial-focus],[aria-invalid='true']");
      const firstField=focusableElements(dialog).find(element=>element.matches("input,select,textarea"));
      const target=(preferred instanceof HTMLElement&&preferred.getClientRects().length?preferred:null)||firstField||focusableElements(dialog)[0]||dialog;
      if(target instanceof HTMLElement)target.focus();
    });
  };
  dialog.addEventListener("keydown",event=>{
    if(event.key!=="Tab")return;
    const focusable=focusableElements(dialog);
    if(!focusable.length){event.preventDefault();dialog.focus();return}
    const first=focusable[0],last=focusable[focusable.length-1];
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}
    else if(!dialog.contains(document.activeElement)){event.preventDefault();first.focus()}
  });
  dialog.addEventListener("close",()=>{
    const returnFocus=dialog.returnFocus;
    dialog.returnFocus=null;
    requestAnimationFrame(()=>{
      if(document.querySelector("dialog[open]"))return;
      if(returnFocus instanceof HTMLElement&&returnFocus.isConnected&&!returnFocus.hasAttribute("disabled"))returnFocus.focus();
    });
  });
}

$$('dialog').forEach(initialiseDialog);

function accessibleRowPrefix(control){
  const container=control.closest("[id]");
  if(!container)return "Item";
  if(container.id.includes("members"))return "Member";
  if(container.id.includes("values"))return "Registry value";
  if(container.id.includes("ilt"))return "Targeting predicate";
  return "Item";
}

function enhanceGeneratedControls(root=document){
  const controls=[];
  if(root instanceof Element&&root.matches("button,input,select,textarea"))controls.push(root);
  if(root.querySelectorAll)controls.push(...root.querySelectorAll("button,input,select,textarea"));
  controls.forEach(control=>{
    if(control.matches("button")&&!control.getAttribute("aria-label")&&control.textContent.trim()==="×"){
      const deletion=Object.keys(control.dataset).find(key=>key.startsWith("delete"));
      const subject=deletion?deletion.slice(6).replace(/([A-Z])/g," $1").toLowerCase():control.closest(".gpp-row")?"row":"item";
      control.setAttribute("aria-label",`Remove ${subject}`);
    }
    if(!control.matches("input,select,textarea")||control.labels?.length||control.getAttribute("aria-label")||!control.dataset.field)return;
    const field=control.dataset.field.replaceAll("_"," ");
    control.setAttribute("aria-label",`${accessibleRowPrefix(control)} ${field}`);
  });
}

enhanceGeneratedControls();
new MutationObserver(records=>records.forEach(record=>record.addedNodes.forEach(node=>{
  if(node instanceof Element)enhanceGeneratedControls(node);
}))).observe(document.body,{childList:true,subtree:true});

function initialisePressedGroups(){
  $$('[role="group"]').forEach(group=>{
    const chips=[...group.querySelectorAll(".chip")];
    if(!chips.length)return;
    const sync=()=>chips.forEach(chip=>chip.setAttribute("aria-pressed",String(chip.classList.contains("active"))));
    chips.forEach(chip=>chip.addEventListener("click",()=>queueMicrotask(sync)));
    sync();
  });
}

$$('.filter-row .chip').forEach(chip=>chip.onclick=()=>{
  $$('.filter-row .chip').forEach(item=>item.classList.toggle("active",item===chip));
  state.side=chip.dataset.side;
  renderSettings();
});
$("#new-gpo").onclick=()=>openGpo();
$("#empty-new").onclick=()=>openGpo();
$("#edit-metadata").onclick=()=>openGpo(true);
$("#add-setting").onclick=()=>openSetting();
$("#add-link").onclick=()=>openLink();
$("#add-filter").onclick=()=>openFilter();
$("#edit-wmi").onclick=()=>openWmi();
$("#browse-wmi-catalogue").onclick=()=>openWmiCatalogue();
$("#import-estate").onclick=()=>openEstate();
$("#fork-gpo").onclick=()=>openFork();
$("#search").oninput=renderList;

let pendingExportUrl="";
function addReviewRow(container,label,value){
  const row=document.createElement("p"),strong=document.createElement("strong");
  strong.textContent=`${label}: `;
  row.append(strong,document.createTextNode(String(value)));
  container.appendChild(row);
}
$$('[data-export-kind]').forEach(link=>link.addEventListener("click",event=>{
  event.preventDefault();
  clearPersistentError();
  const capability=state.artifactCapabilities[link.dataset.exportKind]||{};
  const errors=state.validation.filter(issue=>issue.severity==="error");
  const blocked=capability.enabled===false||errors.length>0;
  const summary=$("#export-review-summary");
  summary.replaceChildren();
  addReviewRow(summary,"Policy",`${state.current.name} (${state.current.guid})`);
  addReviewRow(summary,"Revision",state.current.revision);
  addReviewRow(summary,"Artifact",link.textContent.trim());
  addReviewRow(summary,"Policy semantic SHA-256",state.policyHash||"unavailable");
  addReviewRow(summary,"Review model SHA-256",state.reviewHash||"unavailable");
  addReviewRow(summary,"Preserved extension files",state.artifactCapabilities.preserved_content?.file_count||0);
  const issues=$("#export-review-issues");
  issues.textContent=blocked?(capability.reason||`Download blocked by ${errors.length} validation error(s). Resolve the Preflight findings first.`):"Validation permits this download. Review the identity and digests before continuing.";
  issues.className=`review-issues ${blocked?"error-summary":"status-message"}`;
  pendingExportUrl=link.href;
  $("#export-review-download").disabled=blocked;
  $("#export-review-download").textContent=blocked?"Download blocked":"Download export";
  $("#export-review-dialog").showModal();
}));
$("#export-review-download").onclick=()=>{
  if(!pendingExportUrl)return;
  const anchor=document.createElement("a");
  anchor.href=pendingExportUrl;anchor.download="";document.body.appendChild(anchor);anchor.click();anchor.remove();
  $("#export-review-dialog").close();
};

$("#import-gpmc").onclick=async()=>{
  const form=$("#gpmc-import-form");
  form.reset();clearFormErrors(form);
  $("#gpmc-import-preview").textContent="Checking safe inbox capability…";
  $("#gpmc-import-submit").disabled=true;
  $("#gpmc-import-dialog").showModal();
  try{
    const info=(await api("/api/imports/capabilities")).gpmc_backup;
    $("#gpmc-import-preview").textContent=info.inbox_configured?"Import inbox is configured. This path will be resolved relative to it.":"No import inbox is configured. Configure GPO_STUDIO_INBOX_DIR before using this browser workflow.";
    $("#gpmc-import-submit").disabled=!info.inbox_configured;
    $("#gpmc-import-submit").textContent="Import backup";
  }catch(error){showFormErrors(form,error)}
};
$("#gpmc-import-submit").onclick=async()=>{
  const form=$("#gpmc-import-form");clearFormErrors(form);
  if(!form.reportValidity())return;
  const button=$("#gpmc-import-submit");button.disabled=true;
  try{
    const data=await api("/api/backups/import",{method:"POST",body:JSON.stringify({path:form.relative_path.value,actor:form.actor.value,reason:form.reason.value})});
    $("#gpmc-import-dialog").close();
    await loadList(data.gpo.guid);
    setAppStatus(`Imported ${data.gpo.name} as an archived, read-only baseline. Fork it before editing.`);
  }catch(error){showFormErrors(form,error)}finally{button.disabled=false}
};
for(const name of ["side","action","registry_type"])$("#setting-form")[name].onchange=syncSettingForm;

initForms();
initGpp();
initAdmx();
initDiff();
initialisePressedGroups();
setAppStatus("Loading policies…");
loadList().then(()=>setAppStatus("")).catch(error=>{
  setAppStatus("");
  reportPersistentError(error.message);
  toast(error.message);
});
