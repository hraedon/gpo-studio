import { $, escapeHtml } from "./state.mjs";
import { api } from "./api.mjs";
import { clearFormErrors, showFormErrors } from "./errors.mjs";
import { renderLimitations } from "./rsop.mjs";

// The security-template panel (Plan 034 WP-3).
//
// One dialog for two endpoints, because they answer the same question about
// two halves of the same file: `/api/security-template/policy-families` and
// `/api/security-template/object-security` each render a `GptTmpl.inf` from
// typed families, in the emission direction their lanes certified.
//
// Families arrive as JSON rather than through a form, following the RSOP
// panel's split: the simple part of the request is fields, the structured part
// is a textarea. The reason is weaker here than there -- these families *could*
// be drawn as forms -- so it is an honest "not yet" rather than a stated limit,
// and the dialog says which endpoint's shape the textarea takes.
//
// What the panel adds over calling the endpoints directly is the same thing
// the RSOP panel adds: `limitations` render ABOVE the answer. An INF that
// Windows will accept is not an INF that has been shown to apply, and an empty
// `issues` list on the object-security side is a ruling rather than approval
// (WI-055). Both are things a reader learns too late underneath the output.

export const MODES = {
  "policy-families": {
    path: "/api/security-template/policy-families",
    label: "Policy families",
    keys: ["account", "audit", "user_rights", "security_options"],
    sample: '{\n  "audit": {"logon_events": "success"}\n}',
  },
  "object-security": {
    path: "/api/security-template/object-security",
    label: "Object security",
    keys: ["registry_keys", "files", "services"],
    sample:
      '{\n  "registry_keys": [\n    {"key_path": "MACHINE\\\\Software\\\\Contoso", "raw_sddl": "D:PAR(A;CI;KA;;;BA)"}\n  ]\n}',
  },
};

export function parseFamilies(text, mode) {
  const spec = MODES[mode];
  if (!spec) throw new Error(`Unknown mode: ${mode}.`);
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    throw new Error(`Families are not valid JSON: ${error.message}`, {
      cause: error,
    });
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Families must be a JSON object.");
  }
  const unknown = Object.keys(parsed).filter((key) => !spec.keys.includes(key));
  if (unknown.length) {
    // Refused here as well as by the endpoint, which also forbids extra keys.
    // Two refusals rather than one because they answer different people: the
    // server's 422 is the contract, and this is the one that names the four
    // keys this mode accepts while the caller is still looking at the field.
    throw new Error(
      `Unrecognised keys for ${spec.label}: ${unknown.join(", ")}. ` +
        `This mode accepts ${spec.keys.join(", ")}.`,
    );
  }
  return parsed;
}

export function renderIssues(issues) {
  if (!issues || !issues.length) {
    return '<div class="table-empty">No validation issue. On object security this is a ruling rather than approval — ACL content is deliberately unjudged.</div>';
  }
  const rows = issues
    .map(
      (issue) =>
        `<tr><td><span class="pill ${issue.severity === "error" ? "" : "warn"}">${escapeHtml(issue.severity)}</span></td><td class="mono">${escapeHtml(issue.code)}</td><td>${escapeHtml(issue.message)}</td><td class="mono truncate" title="${escapeHtml(issue.path)}">${escapeHtml(issue.path)}</td></tr>`,
    )
    .join("");
  return `<div class="table-card"><table><thead><tr><th>Severity</th><th>Code</th><th>Message</th><th>Path</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

export function renderTemplate(body) {
  return [
    renderLimitations(body.limitations),
    "<h3>Validation</h3>",
    renderIssues(body.issues),
    "<h3>GptTmpl.inf</h3>",
    '<p class="rsop-note">Text, for reading. The bytes Windows consumes are UTF-16LE with a byte-order mark and CRLF endings; the endpoint returns those as <code>inf_base64</code>.</p>',
    `<pre class="mono inf-output">${escapeHtml(body.inf_text)}</pre>`,
  ]
    .filter(Boolean)
    .join("");
}

export function initSecurityTemplate() {
  $("#open-security-template").onclick = openSecurityTemplate;
  $("#security-template-form").onsubmit = submitSecurityTemplate;
  $("#security-template-mode").onchange = applyMode;
}

function applyMode(event) {
  const spec = MODES[event.currentTarget.value];
  if (!spec) return;
  const families = $("#security-template-form").families;
  families.placeholder = spec.sample;
  $("#security-template-scope-row").hidden =
    event.currentTarget.value !== "policy-families";
}

export function openSecurityTemplate() {
  const form = $("#security-template-form");
  clearFormErrors(form);
  $("#security-template-results").innerHTML =
    '<div class="table-empty">Describe the families, then render.</div>';
  $("#security-template-dialog").showModal();
}

async function submitSecurityTemplate(event) {
  event.preventDefault();
  if (event.submitter && event.submitter.value === "cancel") {
    event.currentTarget.closest("dialog").close();
    return;
  }
  const form = event.currentTarget;
  clearFormErrors(form);
  const results = $("#security-template-results");
  const mode = form.mode.value;
  let families;
  try {
    families = parseFamilies(form.families.value, mode);
  } catch (error) {
    showFormErrors(form, error);
    return;
  }
  // `scope` belongs to the policy-family request only; sending it to the
  // object-security endpoint would be a 422, because both shapes forbid
  // unknown keys.
  const payload =
    mode === "policy-families"
      ? { scope: form.scope.value, ...families }
      : families;
  results.innerHTML = '<div class="table-empty">Rendering…</div>';
  try {
    const body = await api(MODES[mode].path, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    results.innerHTML = renderTemplate(body);
  } catch (error) {
    results.innerHTML = "";
    showFormErrors(form, error);
  }
}
