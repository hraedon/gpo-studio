import { $, $$, escapeHtml } from "./state.mjs";
import { api, toast } from "./api.mjs";
import { clearFormErrors, showFormErrors } from "./errors.mjs";
import { loadList } from "./render.mjs";

export function initStarter() {
  $("#open-starter-gpos").onclick = openStarterList;
  $("#create-starter-gpo").onclick = openCreateStarter;
  $("#starter-gpo-form").onsubmit = submitCreateStarter;
  $("#derive-form").onsubmit = submitDerive;
}

export async function openStarterList() {
  const list = $("#starter-list");
  list.innerHTML = '<div class="table-empty">Loading…</div>';
  $("#starter-list-dialog").showModal();
  try {
    const data = await api("/api/starter-gpos");
    if (!data.count) {
      list.innerHTML =
        '<div class="table-empty">No Starter GPOs yet. Create one to derive draft policies from a shared baseline.</div>';
      return;
    }
    list.innerHTML = data.items
      .map(
        (g) =>
          `<div class="starter-list-entry"><div><strong>${escapeHtml(g.name)}</strong><small>Template ${escapeHtml(g.template_version || "—")} · r${g.revision} · ${escapeHtml(g.description || "No description")}</small></div><div class="row-actions"><button type="button" data-derive-starter="${escapeHtml(g.guid)}" data-derive-name="${escapeHtml(g.name)}">Derive</button><button type="button" data-delete-starter="${escapeHtml(g.guid)}" data-delete-revision="${g.revision}" data-delete-name="${escapeHtml(g.name)}">×</button></div></div>`,
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
    list.innerHTML = `<div class="table-empty">${escapeHtml(error.message)}</div>`;
  }
}

function openCreateStarter() {
  const form = $("#starter-gpo-form");
  form.reset();
  clearFormErrors(form);
  form.reason.value = "Create starter GPO";
  $("#starter-gpo-dialog").showModal();
}

async function submitCreateStarter(event) {
  event.preventDefault();
  if (event.submitter && event.submitter.value === "cancel") {
    event.currentTarget.closest("dialog").close();
    return;
  }
  const f = event.currentTarget;
  try {
    await api("/api/starter-gpos", {
      method: "POST",
      body: JSON.stringify({
        name: f.name.value,
        description: f.description.value,
        template_version: f.template_version.value,
        actor: "local-operator",
        reason: f.reason.value,
      }),
    });
    $("#starter-gpo-dialog").close();
    await openStarterList();
    toast("Starter GPO created");
  } catch (error) {
    showFormErrors(f, error);
  }
}

function openDerive(guid, name) {
  const form = $("#derive-form");
  form.reset();
  clearFormErrors(form);
  form.source_guid.value = guid;
  form.name.value = name ? `${name} (derived)` : "";
  form.reason.value = "Derive from starter GPO";
  $("#derive-dialog").showModal();
}

async function submitDerive(event) {
  event.preventDefault();
  if (event.submitter && event.submitter.value === "cancel") {
    event.currentTarget.closest("dialog").close();
    return;
  }
  const f = event.currentTarget;
  try {
    const data = await api(
      `/api/starter-gpos/${f.source_guid.value}/derive`,
      {
        method: "POST",
        body: JSON.stringify({
          name: f.name.value,
          actor: "local-operator",
          reason: f.reason.value,
        }),
      },
    );
    $("#derive-dialog").close();
    $("#starter-list-dialog").close();
    await loadList(data.gpo.guid);
    toast("GPO derived from starter");
  } catch (error) {
    showFormErrors(f, error);
  }
}

async function deleteStarter(guid, revision, name) {
  if (
    !confirm(
      `Delete Starter GPO "${name}"? This removes it from the workspace and cannot be undone.`,
    )
  )
    return;
  try {
    await api(`/api/starter-gpos/${guid}`, {
      method: "DELETE",
      body: JSON.stringify({
        actor: "local-operator",
        reason: "Delete starter GPO",
        expected_revision: revision,
      }),
    });
    await openStarterList();
    toast("Starter GPO deleted");
  } catch (error) {
    toast(error.message);
  }
}
