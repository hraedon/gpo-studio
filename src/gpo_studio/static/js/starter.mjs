import { createReadScope } from "./read-context.mjs";
import {
  $,
  $$,
  escapeHtml,
  observeGpoDeleted,
  getGpoGeneration,
} from "./state.mjs";
import { api, toast, showPersistentError } from "./api.mjs";
import { clearFormErrors, showFormErrors } from "./errors.mjs";
import { loadList, renderList, showEmpty } from "./render.mjs";
import {
  resetEditor,
  beginMutation,
  isCurrentEditor,
  isSameEditor,
  updateMutationList,
  mutationFailed,
  endMutation,
} from "./mutation-context.mjs";

let listGeneration = 0,
  listRequest = 0;
const pendingDeletes = new Set();
const starterReads=createReadScope({element:()=>$("#starter-list-dialog")});

function listContext() {
  const dialog = $("#starter-list-dialog");
  return { dialog, session: dialog.editorSession, generation: listGeneration };
}

function isCurrentList(context) {
  return (
    context.dialog.open &&
    context.dialog.editorSession === context.session &&
    context.generation === listGeneration
  );
}

export function initStarter() {
  $("#open-starter-gpos").onclick = openStarterList;
  $("#create-starter-gpo").onclick = openCreateStarter;
  $("#starter-gpo-form").onsubmit = submitCreateStarter;
  $("#derive-form").onsubmit = submitDerive;
}

export async function openStarterList() {
  listGeneration++;
  $("#starter-list-dialog").showModal();
  await refreshStarterList(listContext());
}

async function refreshStarterList(context) {
  if (!isCurrentList(context)) return;
  const request = ++listRequest;
  const list = $("#starter-list");
  const read=starterReads.start({isCurrent:()=>request===listRequest&&isCurrentList(context),report:error=>{list.innerHTML=`<div class="table-empty">${escapeHtml(error.message)}</div>`}});
  list.innerHTML = '<div class="table-empty">Loading…</div>';
  try {
    const data = await /* request: starter.list */ api("/api/starter-gpos");
    if (!read.isCurrent()) return;
    if (!data.count) {
      list.innerHTML =
        '<div class="table-empty">No Starter GPOs yet. Create one to derive draft policies from a shared baseline.</div>';
      return;
    }
    list.innerHTML = data.items
      .map(
        (g) =>
          `<div class="starter-list-entry"><div><strong>${escapeHtml(g.name)}</strong><small>Template ${escapeHtml(g.template_version || "—")} · r${g.revision} · ${escapeHtml(g.description || "No description")}</small></div><div class="row-actions"><button type="button" data-derive-starter="${escapeHtml(g.guid)}" data-derive-name="${escapeHtml(g.name)}">Derive</button><button type="button" data-delete-starter="${escapeHtml(g.guid)}" data-delete-revision="${g.revision}" data-delete-name="${escapeHtml(g.name)}"${pendingDeletes.has(g.guid) ? " disabled" : ""}>×</button></div></div>`,
      )
      .join("");
    $$("[data-derive-starter]").forEach((btn) => {
      btn.onclick = () =>
        openDerive(btn.dataset.deriveStarter, btn.dataset.deriveName);
    });
    $$("[data-delete-starter]").forEach((btn) => {
      btn.onclick = () =>
        deleteStarter(
          btn.dataset.deleteStarter,
          Number(btn.dataset.deleteRevision),
          btn.dataset.deleteName,
        );
    });
  } catch (error) {
    read.reportError(error);
  }
}

function openCreateStarter() {
  const form = $("#starter-gpo-form");
  resetEditor(form);
  form.reset();
  clearFormErrors(form);
  form.reason.value = "Create starter GPO";
  $("#starter-gpo-dialog").showModal();
}

async function submitCreateStarter(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (event.submitter?.value === "cancel") {
    form.closest("dialog").close();
    return;
  }
  const context = beginMutation(form, { workspace: true });
  if (!context) return;
  context.name = form.name.value;
  clearFormErrors(form);
  try {
    const data = await /* request: starter.create */ api("/api/starter-gpos", {
      method: "POST",
      body: JSON.stringify({
        name: form.name.value,
        description: form.description.value,
        template_version: form.template_version.value,
        actor: "local-operator",
        reason: form.reason.value,
      }),
    });
    if (isCurrentEditor(context)) context.dialog.close();
    // Both server-authoritative collections include Starters. Completion may
    // refresh the current open library without reopening a closed one.
    updateMutationList(data, renderList);
    await Promise.all([
      refreshStarterList(listContext()),
      loadList(undefined, { select: false, reportErrors: false }),
    ]);
    toast("Starter GPO created");
  } catch (error) {
    await mutationFailed(context, error, showFormErrors);
  } finally {
    endMutation(context);
  }
}

function openDerive(guid, name) {
  const form = $("#derive-form");
  resetEditor(form);
  form.reset();
  clearFormErrors(form);
  form.source_guid.value = guid;
  form.name.value = name ? `${name} (derived)` : "";
  form.reason.value = "Derive from starter GPO";
  $("#derive-dialog").showModal();
}

async function submitDerive(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (event.submitter?.value === "cancel") {
    form.closest("dialog").close();
    return;
  }
  const context = beginMutation(form, { workspace: true });
  if (!context) return;
  const catalogue = listContext();
  const sourceGuid = form.source_guid.value;
  context.name = form.name.value;
  clearFormErrors(form);
  try {
    const data = await /* request: starter.derive */ api(`/api/starter-gpos/${sourceGuid}/derive`, {
      method: "POST",
      body: JSON.stringify({
        name: form.name.value,
        actor: "local-operator",
        reason: form.reason.value,
      }),
    });
    const selectDerived = isSameEditor(context);
    if (isCurrentEditor(context)) {
      context.dialog.close();
      if (isCurrentList(catalogue)) catalogue.dialog.close();
    }
    // Derive returns an ordinary draft; unlike Starter creation it belongs here.
    updateMutationList(data, renderList);
    if (selectDerived) await loadList(data.gpo.guid);
    toast("GPO derived from starter");
  } catch (error) {
    await mutationFailed(context, error, showFormErrors);
  } finally {
    endMutation(context);
  }
}

async function deleteStarter(guid, revision, name) {
  if (pendingDeletes.has(guid)) return;
  const generation = getGpoGeneration(guid);
  if (
    !confirm(
      `Delete Starter GPO "${name}"? This removes it from the workspace and cannot be undone.`,
    )
  )
    return;
  pendingDeletes.add(guid);
  const buttons = $$("[data-delete-starter]").filter(
    (button) => button.dataset.deleteStarter === guid,
  );
  buttons.forEach((button) => {
    button.disabled = true;
  });
  try {
    await /* request: starter.delete */ api(`/api/starter-gpos/${guid}`, {
      method: "DELETE",
      body: JSON.stringify({
        actor: "local-operator",
        reason: "Delete starter GPO",
        expected_revision: revision,
      }),
    });
    // A late acknowledgement for a retired deletion must not invalidate a
    // subsequent incarnation a second time.
    if (generation === getGpoGeneration(guid)) {
      const selected = observeGpoDeleted(guid);
      renderList();
      if (selected) showEmpty();
    }
    await Promise.all([
      refreshStarterList(listContext()),
      loadList(undefined, { select: false, reportErrors: false }),
    ]);
    toast("Starter GPO deleted");
  } catch (error) {
    showPersistentError(`${name}: ${error.message}`);
  } finally {
    pendingDeletes.delete(guid);
    $$("[data-delete-starter]").forEach((button) => {
      if (button.dataset.deleteStarter === guid) button.disabled = false;
    });
  }
}
