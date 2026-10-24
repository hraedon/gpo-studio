import { $, escapeHtml } from "./state.mjs";
import { api, actionApi, toast } from "./api.mjs";
import { createReadScope } from "./read-context.mjs";
import { clearFormErrors, showFormErrors } from "./errors.mjs";
import { loadList, renderList } from "./render.mjs";
import {
  resetEditor,
  beginMutation,
  isCurrentEditor,
  isSameEditor,
  updateMutationList,
  mutationFailed,
  endMutation,
} from "./mutation-context.mjs";

const reads = createReadScope({ element: () => $("#registry-repair-dialog") });
let source = null, reviewed = null;
const field = (name) => $("#registry-repair-form").elements.namedItem(name);

function invalidatePreview() {
  reads.invalidate();
  reviewed = null;
  $("#registry-repair-preview").hidden = true;
  $("#registry-repair-apply").disabled = true;
}

function choicesBody() {
  if (!source) throw new Error("Wait for the source, or close and reopen recovery.");
  return {
    expected_revision: source.source_revision,
    name: field("name").value,
    actor: field("actor").value,
    reason: field("reason").value,
    registry_only: field("registry_only").checked,
    choices: source.rows.map((row) => ({ row: row.row, action: field(`row-${row.row}`).value })),
  };
}

function omissionText(data) {
  return `Not copied: ${Object.entries(data.omitted_components).map(([name, count]) => `${name.replaceAll("_", " ")}: ${count}`).join("; ")}. The original description and import provenance are not copied.`;
}

export async function openRegistryRepair() {
  const form = $("#registry-repair-form"), dialog = $("#registry-repair-dialog");
  invalidatePreview(); resetEditor(form); form.reset(); clearFormErrors(form);
  source = null;
  $("#registry-repair-rows").replaceChildren();
  $("#registry-repair-omissions").textContent = "";
  dialog.showModal();
  const target = dialog.editorTarget;
  if (!target) return;
  field("name").value = `${target.name} (registry recovery)`.slice(0, 255);
  const read = reads.start({ report: (error) => showFormErrors(form, error) });
  const pending = read.beginPending({
    controls: [...form.querySelectorAll("input,select,button:not([value='cancel'])")],
    busy: [form],
    loading: [{ element: $("#registry-repair-source"), message: "Loading source…", recovery: "Close and reopen recovery to reload the source." }],
  });
  try {
    const data = await /* request: registry-repair.source */ api(`/api/gpos/${target.guid}/registry-repair`);
    if (!read.isCurrent()) return;
    if (data.source_guid !== target.guid || data.source_revision !== target.revision)
      throw new Error("The source changed. Close and reopen recovery to review its current rows.");
    source = data;
    $("#registry-repair-source").textContent = `${data.source_name} · revision ${data.source_revision} · ${data.rows.length} registry rows. Domain ${data.domain}; Computer ${data.computer_enabled ? "enabled" : "disabled"}; User ${data.user_enabled ? "enabled" : "disabled"}.`;
    $("#registry-repair-omissions").textContent = omissionText(data);
    $("#registry-repair-rows").innerHTML = data.rows.map((row) => {
      const claims = row.identity.claims.map(([owner, side]) => `${owner || "(empty owner)"} (${side})`).join("; ") || "No decoded owner";
      return `<fieldset class="registry-repair-row"><legend>Source row ${row.row + 1}</legend><pre>${escapeHtml(row.record_json)}</pre><p>Stored editor claims: ${escapeHtml(claims)}. ${escapeHtml(row.identity.warnings.join(", "))}</p><label>Choice for row ${row.row + 1}<select name="row-${row.row}" required><option value="">Choose explicitly…</option><option value="keep_raw">Keep as a raw row with a new ID</option><option value="omit">Omit from the new draft</option></select></label></fieldset>`;
    }).join("");
  } catch (error) { read.reportError(error); }
  finally { pending.finish(); read.complete(); }
}

async function previewChoices(event) {
  event.preventDefault();
  const form = event.currentTarget;
  invalidatePreview(); clearFormErrors(form);
  let body;
  try { body = choicesBody(); } catch (error) { showFormErrors(form, error); return; }
  const originalSource = source, fingerprint = JSON.stringify(body);
  const read = reads.start({
    isCurrent: () => source === originalSource && JSON.stringify(choicesBody()) === fingerprint,
    report: (error) => showFormErrors(form, error),
  });
  const pending = read.beginPending({
    controls: [...form.querySelectorAll("input,select,button:not([value='cancel'])")], busy: [form],
  });
  let accepted = false;
  try {
    const data = await /* request: registry-repair.preview */ api(`/api/gpos/${originalSource.source_guid}/registry-repair/preview`, { method: "POST", body: fingerprint });
    if (!read.isCurrent()) return;
    reviewed = { body, digest: data.review_sha256, guid: originalSource.source_guid };
    $("#registry-repair-review").innerHTML = `<p><strong>${escapeHtml(data.name)}</strong>: keep ${data.kept_count} of ${data.source.rows.length} rows in original order. All kept rows receive new UUIDs. Other rows are omitted.</p><p>${escapeHtml(data.description)}</p><p>Actor: ${escapeHtml(data.actor)}. Reason: ${escapeHtml(data.reason)}.</p><p>${escapeHtml(omissionText(data.source))}</p><ul>${data.choices.map((choice) => `<li>Source row ${choice.row + 1}: ${choice.action === "keep_raw" ? "Keep as raw" : "Omit"}</li>`).join("")}</ul>`;
    $("#registry-repair-preview").hidden = false;
    $("#registry-repair-preview-title").focus({ preventScroll: true });
    $("#registry-repair-preview-title").scrollIntoView({ block: "start" });
    accepted = true;
  } catch (error) { read.reportError(error); }
  finally { pending.finish(); if (accepted) $("#registry-repair-apply").disabled = false; read.complete(); }
}

async function applyReviewed() {
  const form = $("#registry-repair-form"), review = reviewed;
  if (!review || JSON.stringify(choicesBody()) !== JSON.stringify(review.body)) {
    invalidatePreview(); showFormErrors(form, { message: "Preview the current choices before creation." }); return;
  }
  const context = beginMutation(form);
  if (!context) return;
  clearFormErrors(form);
  try {
    const data = await /* request: registry-repair.apply */ actionApi(`/api/gpos/${review.guid}/registry-repair/apply`, {
      method: "POST", body: JSON.stringify({ ...review.body, review_sha256: review.digest }),
    });
    const selectCreated = isSameEditor(context);
    updateMutationList(data, renderList);
    if (isCurrentEditor(context)) context.dialog.close();
    if (selectCreated) await loadList(data.gpo.guid);
    toast("Registry-only draft created; original policy preserved");
  } catch (error) {
    if (!error.status) error.message = "Could not confirm creation. Check the policy list before retrying. " + error.message;
    await mutationFailed(context, error, (activeForm, failure) => showFormErrors(activeForm, failure));
  } finally { endMutation(context); }
}

export function initRegistryRepair() {
  $("#repair-registry").onclick = openRegistryRepair;
  const form = $("#registry-repair-form");
  form.addEventListener("input", invalidatePreview);
  form.addEventListener("change", invalidatePreview);
  form.onsubmit = previewChoices;
  $("#registry-repair-apply").onclick = applyReviewed;
  for (const [id, action] of [["registry-repair-keep-all", "keep_raw"], ["registry-repair-omit-all", "omit"]]) {
    $("#" + id).onclick = () => {
      invalidatePreview();
      for (const row of source?.rows || []) field(`row-${row.row}`).value = action;
    };
  }
}
