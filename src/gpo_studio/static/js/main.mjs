import { createReadScope, invalidateReadViews, readDialogOpened, readDialogClosed } from "./read-context.mjs";
import {state,$,$$,getGpoGeneration} from './state.mjs';
import {api,toast,clearPersistentError,captureErrorReporter,actionApi} from './api.mjs';
import {clearFormErrors,showFormErrors} from './errors.mjs';
import {loadList,renderList,renderSettings,loadHistory} from './render.mjs';
import {openGpo,openSetting,openLink,openFilter,openWmi,openWmiCatalogue,openEstate,openFork,openCopySettings,syncSettingForm,initForms} from './forms.mjs';
import {initGpp} from './gpp.mjs';
import {checkAdmx,initAdmx} from './admx.mjs';
import {initDiff,loadDiffSelectors} from './diff.mjs';
import {initBrowser,loadBrowser} from './browser.mjs';
import {initStarter} from './starter.mjs';
import {initRsop} from './rsop.mjs';
import {initSecurityTemplate} from './security-template.mjs';
import {initFolderRedirection} from './folder-redirection.mjs';
import {initScripts} from './scripts.mjs';
import {initPublication} from './publication.mjs';
import {initTemplates} from './templates.mjs';
import {initRegistryRepair} from './registry-repair.mjs';
import {captureMutation,resetEditor,beginMutation,isCurrentEditor,isSameEditor,updateMutationList,mutationFailed,endMutation,isMutationPending,hasMutationInFlight} from './mutation-context.mjs';
import {captureDialogFocus,restoreDialogFocus} from './dialog-focus.mjs';
import {createDialogGuard} from './dialog-guard.mjs';

const tabs=$$(".tab[role='tab']");
let focusIntent=0;
document.addEventListener("focusin",()=>{focusIntent++});
const tabReads=createReadScope();
const importReads=createReadScope({element:()=>$("#gpmc-import-dialog")});

$("#dismiss-error").onclick=()=>{clearPersistentError();$("#main-content").focus()};

function setAppStatus(message){
  const status=$("#app-status");
  status.textContent=message;
  status.hidden=!message;
}

function activateTab(tab,{moveFocus=false}={}){
  invalidateReadViews();
  tabs.forEach(item=>{
    const selected=item===tab;
    item.classList.toggle("active",selected);
    item.setAttribute("aria-selected",String(selected));
    item.tabIndex=selected?0:-1;
    const panel=document.getElementById(item.getAttribute("aria-controls"));
    if(panel){panel.classList.toggle("active",selected);panel.hidden=!selected}
  });
  if(moveFocus){tab.focus();tab.scrollIntoView({block:"nearest",inline:"nearest"})}
  const read=tabReads.capture({isCurrent:()=>tab.getAttribute("aria-selected")==="true"});
  if(tab.dataset.tab==="history")loadHistory().catch(error=>read.reportError(error));
  if(tab.dataset.tab==="diff")loadDiffSelectors();
  if(tab.dataset.tab==="admx")checkAdmx();
  if(tab.dataset.tab==="browser")loadBrowser().catch(error=>read.reportError(error));
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
  return [...dialog.querySelectorAll(selector)].filter(element=>element.getClientRects().length>0&&element.getAttribute("aria-hidden")!=="true"&&!element.closest("[inert]"));
}

function initialiseDialog(dialog){
  const form=dialog.querySelector("form");
  const head=form?.querySelector(":scope > .dialog-head");
  const actions=form?.querySelector(":scope > .dialog-actions");
  if(form&&head&&actions){
    const body=document.createElement("div");
    body.className="dialog-body";
    body.append(...[...form.childNodes].filter(node=>node!==head&&node!==actions));
    head.after(body);form.append(actions);
  }
  // Keep the live region outside the busy form and present before any save.
  const pendingAnnouncement=document.createElement("div");
  pendingAnnouncement.className="sr-only";
  pendingAnnouncement.dataset.pendingAnnouncement="";
  pendingAnnouncement.setAttribute("role","status");
  pendingAnnouncement.setAttribute("aria-live","polite");
  pendingAnnouncement.setAttribute("aria-atomic","true");
  dialog.appendChild(pendingAnnouncement);
  const nativeShowModal=dialog.showModal.bind(dialog);
  const nativeClose=dialog.close.bind(dialog),closingFocus=[];
  let openingFocus=null;
  dialog.close=(...args)=>{
    const closing=dialog.open?openingFocus:null;
    nativeClose(...args);
    if(closing)closingFocus.push(closing);
  };
  const guard=createDialogGuard(dialog,{close:()=>dialog.close("cancel"),isPending:()=>Boolean(form&&isMutationPending(form)),inFlight:()=>Boolean(form&&hasMutationInFlight(form))});
  dialog.showModal=()=>{
    if(dialog.open)return;
    pendingAnnouncement.textContent="";
    state.editorGeneration++;
    dialog.editorSession=Symbol("editor session");
    dialog.editorTarget={guid:state.current?.guid??null,revision:state.current?.revision??null,name:state.current?.name||"workspace",generation:getGpoGeneration(state.current?.guid)};
    const active=document.activeElement;
    dialog.returnFocus=active instanceof HTMLElement?active:null;
    const focus=openingFocus=captureDialogFocus(dialog);
    nativeShowModal();
    guard?.opened();
    readDialogOpened(dialog);
    const nativeFocus=document.activeElement,initialFocusIntent=focusIntent;
    requestAnimationFrame(()=>{
      if(!dialog.open||dialog.editorSession!==focus.session||state.editorGeneration!==focus.editorGeneration||focusIntent!==initialFocusIntent||document.activeElement!==nativeFocus)return;
      const preferred=dialog.querySelector("[autofocus],[data-initial-focus],[aria-invalid='true']");
      const firstField=focusableElements(dialog).find(element=>element.matches("input,select,textarea"));
      const target=(preferred instanceof HTMLElement&&preferred.getClientRects().length?preferred:null)||firstField||focusableElements(dialog)[0]||dialog;
      if(target instanceof HTMLElement)target.focus();
    });
  };
  // Esc is handled at keydown: a second Esc after a prevented cancel event
  // arrives as a cancel that cannot be prevented, and would close the dialog.
  dialog.addEventListener("keydown",event=>{
    if(!guard||event.key!=="Escape"||event.isComposing||!dialog.open)return;
    if(guard.promptOpen()){event.preventDefault();event.stopPropagation();guard.keepEditing();return}
    if(guard.shouldAsk()){event.preventDefault();event.stopPropagation();guard.ask()}
  },true);
  dialog.addEventListener("cancel",event=>{
    event.preventDefault();
    if(guard&&event.cancelable){
      if(guard.promptOpen()){guard.keepEditing();return}
      if(guard.shouldAsk()){guard.ask();return}
    }
    dialog.close();
  });
  dialog.addEventListener("click",event=>{
    const cancel=event.target.closest('button[value="cancel"]');
    if(!cancel||cancel.closest("dialog")!==dialog)return;
    event.preventDefault();
    // The × beside the title asks first; the Cancel action is a deliberate discard.
    if(guard&&cancel.closest(".dialog-head")&&guard.shouldAsk()){guard.ask();return}
    dialog.close("cancel");
  });
  dialog.addEventListener("keydown",event=>{
    if(event.key!=="Tab")return;
    const focusable=focusableElements(dialog);
    if(!focusable.length){event.preventDefault();dialog.focus();return}
    const first=focusable[0],last=focusable[focusable.length-1],active=document.activeElement;
    // A click on a non-focusable part focuses the <dialog> itself; from there
    // (or from inert content behind the discard prompt) wrap, not escape.
    if(active===dialog||active?.closest?.("[inert]")){event.preventDefault();(event.shiftKey?last:first).focus()}
    else if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}
    else if(!dialog.contains(document.activeElement)){event.preventDefault();first.focus()}
  });
  dialog.addEventListener("close",()=>{
    guard?.closed();
    const focus=closingFocus.shift()||openingFocus;
    if(openingFocus===focus){openingFocus=null;dialog.returnFocus=null}
    requestAnimationFrame(()=>restoreDialogFocus(dialog,focus));
    readDialogClosed();
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

$$('#panel-settings .filter-row .chip').forEach(chip=>chip.onclick=()=>{
  $$('#panel-settings .filter-row .chip').forEach(item=>item.classList.toggle("active",item===chip));
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
$("#copy-settings").onclick=()=>openCopySettings();
$("#search").oninput=renderList;

let exportReview=null;
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
  const blocked=capability.enabled===false||errors.length>0||!state.reviewHash;
  const summary=$("#export-review-summary");
  summary.replaceChildren();
  addReviewRow(summary,"Policy",`${state.current.name} (${state.current.guid})`);
  addReviewRow(summary,"Revision",state.current.revision);
  addReviewRow(summary,"Artifact",link.textContent.trim());
  addReviewRow(summary,"Policy semantic SHA-256",state.policyHash||"unavailable");
  addReviewRow(summary,"Review model SHA-256",state.reviewHash||"unavailable");
  addReviewRow(summary,"Preserved extension files",state.artifactCapabilities.preserved_content?.file_count||0);
  const issues=$("#export-review-issues");
  issues.textContent=blocked?(capability.reason||(!state.reviewHash?"The review hash is unavailable. Reload the policy before reviewing an export.":`${errors.length} validation error(s) block this download. Fix the Preflight findings first.`)):"No validation error blocks this download. Check the policy and digests before you continue.";
  issues.className=`review-issues ${blocked?"error-summary":"status-message"}`;
  $("#export-review-form").removeAttribute("aria-busy");
  exportReview={guid:state.current.guid,revision:state.current.revision,reviewHash:state.reviewHash,url:link.href,filename:link.dataset.exportKind==="powershell_plan"?`${state.current.guid}-plan.ps1`:"gpo-export.zip",pending:false};
  $("#export-review-download").disabled=blocked;
  $("#export-review-download").textContent=blocked?"Download blocked":"Download export";
  $("#export-review-dialog").showModal();
}));
$("#export-review-dialog").addEventListener("close",()=>{if(!$("#export-review-dialog").open)exportReview=null});
$("#export-review-download").onclick=async()=>{
  const review=exportReview,dialog=$("#export-review-dialog"),button=$("#export-review-download"),issues=$("#export-review-issues");
  if(!review||review.pending||button.disabled)return;
  const ownsReview=()=>exportReview===review&&dialog.open;
  const reportError=captureErrorReporter(ownsReview,error=>{
    issues.textContent=error instanceof TypeError?"The local GPO Studio server is unavailable. Keep this review open and try again.":error.message;
    issues.className="review-issues error-summary";
    issues.tabIndex=-1;issues.focus();
  });
  review.pending=true;
  button.disabled=true;button.textContent="Preparing download…";
  $("#export-review-form").setAttribute("aria-busy","true");
  const url=new URL(review.url);
  url.searchParams.set("expected_revision",String(review.revision));
  url.searchParams.set("expected_review_hash",review.reviewHash);
  try{
    const response=await /* request: export.review.bytes */ fetch(url,{cache:"no-store"});
    if(!ownsReview())return;
    if(!response.ok){
      const payload=await response.json().catch(()=>null);
      const details=payload?.error?.issues?.map(issue=>issue.message).filter(Boolean).join(" ");
      throw new Error(details||payload?.error?.message||`Download failed (${response.status}). Review the policy and try again.`);
    }
    if(response.headers.get("X-GPO-Studio-Guid")!==review.guid||
       response.headers.get("X-GPO-Studio-Revision")!==String(review.revision)||
       response.headers.get("X-GPO-Studio-Review-Hash")!==review.reviewHash){
      throw new Error("The download did not match this export review. Close this review, reload the policy and review it again.");
    }
    const bytes=await response.arrayBuffer();
    // Web Crypto is unavailable on supported ordinary HTTP origins. The
    // revision/hash response binding remains mandatory on every origin.
    if(globalThis.crypto?.subtle){
      const digest=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",bytes)),byte=>byte.toString(16).padStart(2,"0")).join("");
      if(digest!==response.headers.get("X-GPO-Studio-Artifact-SHA256"))throw new Error("The download bytes could not be verified. Try the download again.");
    }
    if(!ownsReview())return;
    const blobUrl=URL.createObjectURL(new Blob([bytes],{type:response.headers.get("content-type")||"application/octet-stream"}));
    const anchor=document.createElement("a");
    anchor.href=blobUrl;
    anchor.download=response.headers.get("content-disposition")?.match(/filename="([^"\r\n]+)"/)?.[1]||review.filename;
    document.body.appendChild(anchor);anchor.click();anchor.remove();
    setTimeout(()=>URL.revokeObjectURL(blobUrl),60_000);
    dialog.close();
  }catch(error){
    reportError(error);
  }finally{
    review.pending=false;
    if(ownsReview()){
      $("#export-review-form").removeAttribute("aria-busy");
      button.disabled=false;button.textContent="Download export";
    }
  }
};

$("#import-gpmc").onclick=async()=>{
  importReads.invalidate();
  const form=$("#gpmc-import-form");
  resetEditor(form);form.reset();clearFormErrors(form);
  form.dataset.inboxConfigured="false";
  $("#gpmc-import-submit").disabled=true;
  $("#gpmc-import-submit").textContent="Import backup";
  $("#gpmc-import-dialog").showModal();
  const context=captureMutation(form);
  const read=importReads.start({isCurrent:()=>isCurrentEditor(context),report:error=>showFormErrors(form,error)});
  const pending=read.beginPending({busy:[$("#gpmc-import-preview")],loading:[{element:$("#gpmc-import-preview"),message:"Checking the import inbox…",recovery:"Close and reopen Import GPMC backup to check the current inbox before importing."}]});
  try{
    const info=(await /* request: import.capabilities */ api("/api/imports/capabilities")).gpmc_backup;
    if(!read.isCurrent())return;
    form.dataset.inboxConfigured=String(Boolean(info.inbox_configured));
    $("#gpmc-import-preview").textContent=info.inbox_configured?"Import inbox found. The path you enter is read relative to it.":"No import inbox is configured. Set GPO_STUDIO_INBOX_DIR to import backups from here.";
    $("#gpmc-import-submit").disabled=!info.inbox_configured;
  }catch(error){read.reportError(error)}finally{pending.finish()}
};
$("#gpmc-import-form").onsubmit=async event=>{
  event.preventDefault();
  const form=event.currentTarget;
  if(form.dataset.inboxConfigured!=="true"||!form.reportValidity())return;
  const context=beginMutation(form,{workspace:true});if(!context)return;
  context.name="GPMC backup import";
  clearFormErrors(form);
  try{
    const data=await /* request: import.backup */ actionApi("/api/backups/import",{method:"POST",body:JSON.stringify({path:form.relative_path.value,actor:form.actor.value,reason:form.reason.value})});
    const selectImported=isSameEditor(context);
    updateMutationList(data,renderList);
    if(isCurrentEditor(context))context.dialog.close();
    if(selectImported)await loadList(data.gpo.guid);
    toast(`Imported ${data.gpo.name} as an archived, read-only baseline. Fork it before editing.`);
  }catch(error){await mutationFailed(context,error,showFormErrors)}finally{endMutation(context)}
};
for(const name of ["side","action","registry_type"])$("#setting-form")[name].onchange=syncSettingForm;

initForms();
initGpp();
initAdmx();
initDiff();
initBrowser();
initStarter();
initRsop();
initSecurityTemplate();
initFolderRedirection();
initScripts();
initPublication();
initTemplates();
initRegistryRepair();
initialisePressedGroups();
setAppStatus("Loading policies…");
const initialRead=tabReads.capture();
const initialPending=initialRead.beginPending({loading:[{element:$("#app-status"),message:"Loading policies…"}],onFinish:()=>{if(!$("#app-status").textContent)$("#app-status").hidden=true}});
loadList().catch(error=>initialRead.reportError(error)).finally(()=>initialPending.finish());
