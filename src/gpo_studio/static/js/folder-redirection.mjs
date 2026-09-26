import { $, escapeHtml } from "./state.mjs";
import { api } from "./api.mjs";
import { clearFormErrors, showFormErrors } from "./errors.mjs";
import { renderLimitations } from "./rsop.mjs";

// Match the reader's decoded-byte ceiling, before reading or encoding a file.
export const MAX_FILE_BYTES = 1024 * 1024;
const PARSE_PATH = "/api/folder-redirection/fdeploy";

export function validateFile(file) {
  if (!file) throw new Error("Choose a current file to review.");
  if (file.size === 0) throw new Error(`${file.name}: the file is empty.`);
  if (file.size > MAX_FILE_BYTES) {
    throw new Error(`${file.name}: the file exceeds the 1 MiB review limit.`);
  }
}

export async function readNativeFile(file) {
  validateFile(file);
  const bytes = new Uint8Array(await file.arrayBuffer());
  // Preserve native bytes, including BOM and CRLF; File.text() would decode
  // them as UTF-8. Chunking avoids an argument-stack overflow at the size cap.
  const chunks = [];
  for (let offset = 0; offset < bytes.length; offset += 32768) {
    chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + 32768)));
  }
  return btoa(chunks.join(""));
}

function folderLabel(name, guid) {
  return `${escapeHtml(name || "Unrecognised folder GUID")}<br><span class="mono">${escapeHtml(guid)}</span>`;
}

function fieldValue(value) {
  // An absent side and a present but empty native value are different facts.
  if (value === null) return '<span class="muted">Not present</span>';
  if (value === "") return '<span class="muted">Empty value</span>';
  return escapeHtml(value);
}

function renderDiagnostics(body) {
  const warnings = body.parse_warnings
    .map((warning) => `<li>${escapeHtml(warning)}</li>`)
    .join("");
  const issues = body.validation
    .map(
      (issue) =>
        `<li><strong>${escapeHtml(issue.severity)}</strong>: ${escapeHtml(issue.message)} <code>${escapeHtml(issue.code)}</code> <span class="mono">${escapeHtml(issue.path)}</span></li>`,
    )
    .join("");
  if (!warnings && !issues) {
    return '<p class="rsop-note">No structural issues reported. This does not establish how Windows applies the policy.</p>';
  }
  return `<div class="rsop-warnings"><strong>File warnings and structural issues</strong><ul>${warnings}${issues}</ul></div>`;
}

export function renderDocument(body, filename, label = "Current file") {
  const rows = body.redirections
    .map(
      (rule) =>
        `<tr><td>${folderLabel(rule.folder_name, rule.folder_guid)}</td><td class="mono">${escapeHtml(rule.principal)}</td><td class="mono">${fieldValue(rule.full_path)}</td><td class="mono">${fieldValue(rule.flags_text)}</td></tr>`,
    )
    .join("");
  const folders = body.folders
    .map(
      (folder) =>
        `<li><span class="mono">${escapeHtml(folder.guid)}</span>: ${folder.principals.map(escapeHtml).join(", ") || "No principals listed"}</li>`,
    )
    .join("");
  return `<section class="fdeploy-document">
    <h3>${escapeHtml(label)}: ${escapeHtml(filename)}</h3>
    <p>${body.is_marker ? "Empty marker file" : `Version: ${escapeHtml(body.version ?? "Absent")}`} · ${body.redirections.length} redirection section(s)</p>
    ${renderDiagnostics(body)}
    ${rows ? `<div class="table-card"><table><caption>Redirection sections</caption><thead><tr><th scope="col">Folder</th><th scope="col">Principal</th><th scope="col">Full path</th><th scope="col">Flags (raw)</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<p class="table-empty">No redirection sections in this file.</p>'}
    ${folders ? `<details><summary>Folder-to-principal map</summary><ul>${folders}</ul></details>` : ""}
    <details><summary>Full file report</summary><pre class="mono fdeploy-report">${escapeHtml(body.report_lines.join("\n"))}</pre></details>
  </section>`;
}

export function renderComparison(body, identicalBytes) {
  const rows = body.changes
    .map(
      (change) => `<article class="fdeploy-change">
        <h4>${escapeHtml(change.kind)} · ${escapeHtml(change.folder_name || "Unrecognised folder GUID")}</h4>
        <p class="mono">${escapeHtml(change.folder_guid)}<br>Principal: ${escapeHtml(change.principal)}</p>
        <div class="fdeploy-change-values">
          <section><h5>Earlier file</h5><dl>
            <dt>Full path</dt><dd class="mono">${fieldValue(change.old_full_path)}</dd>
            <dt>Flags (raw)</dt><dd class="mono">${fieldValue(change.old_flags_text)}</dd>
          </dl></section>
          <section><h5>Current file</h5><dl>
            <dt>Full path</dt><dd class="mono">${fieldValue(change.new_full_path)}</dd>
            <dt>Flags (raw)</dt><dd class="mono">${fieldValue(change.new_flags_text)}</dd>
          </dl></section>
        </div>
      </article>`,
    )
    .join("");
  return `<section class="fdeploy-comparison"><h3>Redirection changes</h3>
    <p class="rsop-note">Compares redirection sections by folder GUID and principal. Repeated identities use the last section; read both files' warnings below. Changes to version, the folder map, other sections and formatting are outside this comparison. Additional entries within a redirection section can also cause a change; inspect the full file reports for those values.</p>
    <p>${identicalBytes ? "The files are byte-identical." : "The file bytes differ."}</p>
    ${rows || '<p class="table-empty">No redirection-section changes detected.</p>'}
  </section>`;
}

export function initFolderRedirection() {
  const dialog = $("#folder-redirection-dialog");
  const form = $("#folder-redirection-form");
  const results = $("#folder-redirection-results");
  const status = $("#folder-redirection-status");
  const submit = $("#folder-redirection-submit");
  // Closing or changing either file invalidates every pending read/request.
  // An old completion must never relabel its results as the new selection.
  let generation = 0;

  function invalidate() {
    generation += 1;
    submit.disabled = false;
    results.setAttribute("aria-busy", "false");
    results.replaceChildren();
    clearFormErrors(form);
    status.textContent = "";
  }

  $("#open-folder-redirection").onclick = () => {
    invalidate();
    form.reset();
    dialog.showModal();
  };
  dialog.addEventListener("close", invalidate);
  form.querySelectorAll("[data-close-folder-redirection]").forEach((button) => {
    button.onclick = () => dialog.close();
  });
  form.addEventListener("change", invalidate);

  async function parseFile(content, file, label) {
    try {
      return await api(PARSE_PATH, {
        method: "POST",
        body: JSON.stringify({ content_base64: content }),
      });
    } catch (error) {
      throw new Error(`${label} (${file.name}): ${error.message}`, {
        cause: error,
      });
    }
  }

  form.onsubmit = async (event) => {
    event.preventDefault();
    if (submit.disabled) return;
    invalidate();
    const requestGeneration = generation;
    const isCurrent = () => generation === requestGeneration && dialog.open;
    const current = form.elements.current_file.files[0];
    const earlier = form.elements.earlier_file.files[0];
    try {
      // Check both sizes before either file is read or sent to the server.
      validateFile(current);
      if (earlier) validateFile(earlier);
      submit.disabled = true;
      results.setAttribute("aria-busy", "true");
      status.textContent = "Reading files…";
      const [currentBytes, earlierBytes] = await Promise.all([
        readNativeFile(current),
        earlier ? readNativeFile(earlier) : Promise.resolve(null),
      ]);
      if (!isCurrent()) return;
      status.textContent = "Reviewing files…";
      const [currentBody, earlierBody] = await Promise.all([
        parseFile(currentBytes, current, "Current file"),
        earlier
          ? parseFile(earlierBytes, earlier, "Earlier file")
          : Promise.resolve(null),
      ]);
      if (!isCurrent()) return;
      let comparison = "";
      if (earlier) {
        const diff = await api(`${PARSE_PATH}/diff`, {
          method: "POST",
          body: JSON.stringify({
            old_base64: earlierBytes,
            new_base64: currentBytes,
          }),
        });
        if (!isCurrent()) return;
        comparison = renderComparison(diff, earlierBytes === currentBytes);
      }
      results.innerHTML = [
        renderLimitations(currentBody.limitations),
        comparison,
        earlierBody
          ? renderDocument(earlierBody, earlier.name, "Earlier file")
          : "",
        renderDocument(currentBody, current.name),
      ].join("");
      status.textContent = earlier
        ? "Comparison ready. Review the limits and both files' warnings below."
        : "File review ready. Review the limits and warnings below.";
    } catch (error) {
      if (!isCurrent()) return;
      results.replaceChildren();
      status.textContent = "Review failed.";
      showFormErrors(form, error);
    } finally {
      if (isCurrent()) {
        submit.disabled = false;
        results.setAttribute("aria-busy", "false");
      }
    }
  };
}
