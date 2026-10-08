import { state, $, escapeHtml } from "./state.mjs";
import { api, ApiError } from "./api.mjs";
import { clearFormErrors, showFormErrors } from "./errors.mjs";
import { renderLimitations } from "./rsop.mjs";

// The Scripts export panel (Plan 034).
//
// It builds a request for `/api/gpos/{guid}/gpmc-backup-with-scripts`, which
// returns a GPMC backup through the same function the scripts-metadata lane
// (R10) measures. The panel offers only what that lane measured: computer
// startup entries, legacy and PowerShell, with PowerShell run first. Shutdown,
// logon, logoff, user-side scripts and the other PowerShell orders are not
// offered, because the endpoint refuses them; showing a control whose every
// value is refused would only move the refusal later.
//
// Order is position. The INI numbers entries 0, 1, 2... and that numbering is
// the only order Windows reads, so the rows are reordered with Move up / Move
// down rather than given a number field that could disagree with it.
//
// As in the other panels, `limitations` render ABOVE the preview: a backup
// Windows will import is not a script that has been shown to run.

export const KINDS = {
  legacy: {
    field: "startup",
    label: "Startup script",
    list: "#scripts-legacy-rows",
    file: "scripts.ini",
  },
  powershell: {
    field: "powershell_startup",
    label: "PowerShell startup script",
    list: "#scripts-powershell-rows",
    file: "psscripts.ini",
  },
};

export function emptyRows() {
  return { legacy: [], powershell: [] };
}

function entry(row) {
  const out = { command: String(row.command ?? "").trim() };
  const parameters = String(row.parameters ?? "");
  if (parameters) out.parameters = parameters;
  return out;
}

// The request body for the export and preview endpoints. Throws when there is
// nothing to export or a row has no command, so the operator is told before a
// request is sent; the endpoint enforces the same rules with 422s.
export function buildScriptsRequest(rows) {
  const problems = [];
  for (const [kind, spec] of Object.entries(KINDS)) {
    rows[kind].forEach((row, index) => {
      if (!String(row.command ?? "").trim()) {
        problems.push(`${spec.label} ${index + 1} has no command.`);
      }
    });
  }
  if (!rows.legacy.length && !rows.powershell.length) {
    problems.push("Add at least one startup script.");
  }
  if (problems.length) throw new Error(problems.join(" "));
  const computer = {
    startup: rows.legacy.map(entry),
    powershell_startup: rows.powershell.map(entry),
  };
  // Only PowerShell entries carry an order; "first" is the one value the lane
  // imported, and a legacy-only policy writes no psscripts.ini at all.
  if (rows.powershell.length) {
    computer.powershell_order = "run_windows_powershell_scripts_first";
  }
  return { computer };
}

export function moveRow(list, index, offset) {
  const target = index + offset;
  if (target < 0 || target >= list.length) return list;
  const copy = [...list];
  [copy[index], copy[target]] = [copy[target], copy[index]];
  return copy;
}

export function renderRows(kind, rows) {
  const spec = KINDS[kind];
  if (!rows.length) {
    return `<li class="scripts-empty">No ${escapeHtml(spec.label.toLowerCase())}s.</li>`;
  }
  return rows
    .map((row, index) => {
      const name = `${spec.label} ${index + 1}`;
      const id = `scripts-${kind}-${index}`;
      return `<li class="script-row"><fieldset>
        <legend>${escapeHtml(name)}</legend>
        <label for="${id}-command">Command<input id="${id}-command" name="computer.${spec.field}[${index}].command" data-field="command" data-kind="${kind}" data-index="${index}" maxlength="1024" value="${escapeHtml(row.command)}" placeholder="${kind === "powershell" ? "configure.ps1" : "configure.cmd"}" autocomplete="off"></label>
        <label for="${id}-parameters">Parameters<input id="${id}-parameters" name="computer.${spec.field}[${index}].parameters" data-field="parameters" data-kind="${kind}" data-index="${index}" maxlength="8191" value="${escapeHtml(row.parameters)}" autocomplete="off"></label>
        <div class="script-row-actions">
          <button type="button" data-move="-1" data-kind="${kind}" data-index="${index}" aria-label="Move up: ${escapeHtml(name)}"${index === 0 ? " disabled" : ""}>Move up</button>
          <button type="button" data-move="1" data-kind="${kind}" data-index="${index}" aria-label="Move down: ${escapeHtml(name)}"${index === rows.length - 1 ? " disabled" : ""}>Move down</button>
          <button type="button" data-remove data-kind="${kind}" data-index="${index}" aria-label="Remove: ${escapeHtml(name)}">Remove</button>
        </div>
      </fieldset></li>`;
    })
    .join("");
}

export function renderIssues(issues) {
  if (!issues || !issues.length) return "";
  const items = issues
    .map(
      (issue) =>
        `<li>${escapeHtml(issue.message)} <code>${escapeHtml(issue.code)}</code> <span class="mono">${escapeHtml(issue.path)}</span></li>`,
    )
    .join("");
  return `<div class="rsop-warnings" role="note"><strong>Warnings</strong><ul>${items}</ul></div>`;
}

export function renderScriptsPreview(body) {
  const files = body.files
    .map(
      (file) =>
        `<h4 class="mono">${escapeHtml(file.path)}</h4><p class="rsop-note">${file.size} bytes, SHA-256 <span class="mono">${escapeHtml(file.sha256)}</span></p><pre class="mono inf-output" tabindex="0" role="region" aria-label="${escapeHtml(file.path)} contents">${escapeHtml(file.text)}</pre>`,
    )
    .join("");
  return [
    renderLimitations(body.limitations),
    renderIssues(body.issues),
    "<h3>Backup contents</h3>",
    `<dl class="details scripts-digest"><dt>Backup ID</dt><dd class="mono">${escapeHtml(body.backup_id)}</dd><dt>ZIP SHA-256</dt><dd class="mono">${escapeHtml(body.bundle_sha256)}</dd><dt>Computer extension list</dt><dd class="mono">${escapeHtml(body.machine_extension_names) || "—"}</dd></dl>`,
    '<p class="rsop-note">Shown as text for reading. Windows reads each file as UTF-16LE with a byte-order mark and CRLF line endings, which is what the download contains.</p>',
    files,
  ]
    .filter(Boolean)
    .join("");
}

export function filenameFrom(disposition, fallback) {
  const match = /filename="([^"]+)"/.exec(disposition || "");
  return match ? match[1] : fallback;
}

let rows = emptyRows();

// Bumped by every edit, by reopening and by closing the dialog. A preview
// response is shown only if no bump happened while it was in flight: a late
// response describes rows that are gone, and showing it would put one set of
// contents and digest beside a Download that builds from another.
let generation = 0;

function cancelPendingPreview() {
  generation += 1;
}

function rowsPath() {
  return `/api/gpos/${encodeURIComponent(state.current.guid)}/gpmc-backup-with-scripts`;
}

function redraw(focusSelector) {
  for (const [kind, spec] of Object.entries(KINDS)) {
    $(spec.list).innerHTML = renderRows(kind, rows[kind]);
  }
  if (focusSelector) {
    const target = $(focusSelector);
    if (target && !target.disabled) target.focus();
  }
}

function invalidateResults() {
  cancelPendingPreview();
  $("#scripts-results").replaceChildren();
  $("#scripts-status").textContent = "";
}

function availability() {
  if (!state.current) {
    return "Choose a policy first. Scripts are exported as a GPMC backup of the selected policy.";
  }
  const capability = state.artifactCapabilities.scripts_export || {};
  return capability.enabled === false ? capability.reason : "";
}

export function openScripts() {
  const form = $("#scripts-form");
  clearFormErrors(form);
  rows = emptyRows();
  redraw();
  invalidateResults();
  const blocked = availability();
  $("#scripts-policy").textContent = state.current
    ? `Policy: ${state.current.name}`
    : "No policy selected.";
  const notice = $("#scripts-availability");
  notice.textContent = blocked;
  notice.hidden = !blocked;
  for (const id of [
    "#scripts-add-legacy",
    "#scripts-add-powershell",
    "#scripts-preview",
    "#scripts-download",
  ]) {
    $(id).disabled = Boolean(blocked);
  }
  $("#scripts-dialog").showModal();
}

async function preview(form) {
  const body = buildScriptsRequest(rows);
  cancelPendingPreview();
  const mine = generation;
  $("#scripts-status").textContent = "Building preview…";
  let result;
  try {
    result = await api(`${rowsPath()}/preview`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  } catch (error) {
    // A refusal of rows that have since changed is no more current than a
    // preview of them would be.
    if (mine !== generation) return;
    throw error;
  }
  if (mine !== generation) return;
  $("#scripts-results").innerHTML = renderScriptsPreview(result);
  $("#scripts-status").textContent =
    "Preview ready. Review the limits above the file contents.";
  clearFormErrors(form);
}

async function download() {
  const body = buildScriptsRequest(rows);
  $("#scripts-status").textContent = "Preparing download…";
  const response = await fetch(rowsPath(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const payload = (response.headers.get("content-type") || "").includes("json")
      ? await response.json()
      : null;
    throw new ApiError(
      payload?.error?.message || `Request failed (${response.status})`,
      { status: response.status, payload },
    );
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filenameFrom(
    response.headers.get("content-disposition"),
    "gpmc-backup-scripts.zip",
  );
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
  $("#scripts-status").textContent =
    "Download started. The backup carries script metadata only, not the scripts themselves.";
}

export function initScripts() {
  const dialog = $("#scripts-dialog");
  const form = $("#scripts-form");
  $("#open-scripts").onclick = openScripts;
  form.querySelectorAll("[data-close-scripts]").forEach((button) => {
    button.onclick = () => dialog.close();
  });
  dialog.addEventListener("close", cancelPendingPreview);
  $("#scripts-add-legacy").onclick = () => {
    rows.legacy = [...rows.legacy, { command: "", parameters: "" }];
    invalidateResults();
    redraw(`#scripts-legacy-${rows.legacy.length - 1}-command`);
  };
  $("#scripts-add-powershell").onclick = () => {
    rows.powershell = [...rows.powershell, { command: "", parameters: "" }];
    invalidateResults();
    redraw(`#scripts-powershell-${rows.powershell.length - 1}-command`);
  };
  form.addEventListener("input", (event) => {
    const field = event.target.closest("[data-field]");
    if (!field) return;
    const list = rows[field.dataset.kind];
    const index = Number(field.dataset.index);
    if (!list || !list[index]) return;
    list[index] = { ...list[index], [field.dataset.field]: field.value };
    invalidateResults();
  });
  form.addEventListener("click", (event) => {
    const control = event.target.closest("[data-move],[data-remove]");
    if (!control) return;
    const kind = control.dataset.kind;
    const index = Number(control.dataset.index);
    invalidateResults();
    if (control.hasAttribute("data-remove")) {
      rows[kind] = rows[kind].filter((_, position) => position !== index);
      const next = Math.min(index, rows[kind].length - 1);
      redraw(
        next >= 0
          ? `#scripts-${kind}-${next}-command`
          : `#scripts-add-${kind === "legacy" ? "legacy" : "powershell"}`,
      );
      return;
    }
    const offset = Number(control.dataset.move);
    rows[kind] = moveRow(rows[kind], index, offset);
    redraw();
    // Keep focus on the same control for the row that moved, or on the other
    // move button when the row reached an end and this one became disabled.
    const moved = index + offset;
    const same = $(`[data-kind="${kind}"][data-index="${moved}"][data-move="${offset}"]`);
    const other = $(`[data-kind="${kind}"][data-index="${moved}"][data-move="${-offset}"]`);
    (same && !same.disabled ? same : other)?.focus();
  });
  form.onsubmit = async (event) => {
    event.preventDefault();
    try {
      await preview(form);
    } catch (error) {
      $("#scripts-results").replaceChildren();
      $("#scripts-status").textContent = "Preview refused.";
      showFormErrors(form, error);
    }
  };
  $("#scripts-download").onclick = async () => {
    const button = $("#scripts-download");
    button.disabled = true;
    try {
      await download();
    } catch (error) {
      $("#scripts-status").textContent = "Download refused.";
      showFormErrors(form, error);
    } finally {
      button.disabled = Boolean(availability());
    }
  };
}
