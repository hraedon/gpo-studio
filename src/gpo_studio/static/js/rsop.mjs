import { $, escapeHtml } from "./state.mjs";
import { api } from "./api.mjs";
import { clearFormErrors, showFormErrors } from "./errors.mjs";

// The RSOP panel (WI-030).
//
// The topology arrives as JSON rather than through a builder, and that is a
// stated limit rather than an unfinished one: the workspace holds draft GPOs,
// not an OU tree, so there is nothing here to draw a site/domain/OU hierarchy
// from. A builder would have to invent the estate it is predicting over.
//
// What the panel does add over calling the endpoint directly is that it renders
// `limitations` ABOVE the answer. A collapsed applied-GPO status (WI-032) reads
// as a per-side answer to anyone who has not been told otherwise, and the place
// that matters is where somebody is looking at the result.

const TOPOLOGY_KEYS = ["nodes", "gpos", "wmi_filter_results"];

export function parseTopology(text) {
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    throw new Error(`Topology is not valid JSON: ${error.message}`, {
      cause: error,
    });
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(
      "Topology must be a JSON object with `nodes` and `gpos` keys.",
    );
  }
  if (!Array.isArray(parsed.nodes) || !Array.isArray(parsed.gpos)) {
    throw new Error("Topology must carry `nodes` and `gpos` arrays.");
  }
  const unknown = Object.keys(parsed).filter((k) => !TOPOLOGY_KEYS.includes(k));
  if (unknown.length) {
    // Refused rather than dropped. A silently ignored key looks like an input
    // that was honoured, which is the failure this whole module is careful
    // about at a larger scale.
    throw new Error(`Topology has unrecognised keys: ${unknown.join(", ")}.`);
  }
  return {
    nodes: parsed.nodes,
    gpos: parsed.gpos,
    wmi_filter_results: parsed.wmi_filter_results || {},
  };
}

export function splitPrincipals(text) {
  return String(text || "")
    .split(/[\n,;]/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

// One plain sentence per limitation code the API can return. The API's own
// code and message are kept, unchanged, under "Technical detail". A code not
// in this map falls back to its API message, so no limitation is ever hidden.
// Sources: `_policy_family_limitations`, `_object_security_limitations` and
// `_fdeploy_limitations` in api.py (`_rsop_limitations` currently returns
// none).
export const LIMITATION_SENTENCES = {
  // Security template: both families. The object-security message also covers
  // permissions and inheritance; that detail stays under "Technical detail".
  round_trip_not_application:
    "Windows checked this template with secedit (validate, import and export) but never applied it. Nothing here shows that Windows applies these settings on a computer.",
  // Security template: policy families.
  representative_values_only:
    "Only a sample of values has been through Windows' security database: password and lockout, all nine audit settings, two user rights and four registry value types. That does not show that every value you can enter here behaves as intended.",
  gpmc_editing_unmeasured:
    "It has not been tested whether the Group Policy Management Editor can open and edit this template.",
  kerberos_omitted_for_member_server:
    "The Kerberos Policy section is left out because a member server exports it empty. Choose the Domain controller scope to include it.",
  empty_sections_unmeasured:
    "Some sections are written with a header and no entries (listed under Technical detail). Windows has never been tested with an empty section here. Add entries, or remove those sections before you deploy the template.",
  // Security template: object security.
  acl_content_is_not_judged:
    "Validation checks structure only. It does not judge who is granted access: a grant of Full Control to Everyone raises no issue. A clean result does not approve these permissions.",
  restricted_groups_not_surfaced:
    "Group Membership (restricted groups) can't be rendered here. Its output has never been checked against Windows, and it writes SIDs in a different form from Windows (WI-064).",
  first_tranche_only:
    "Only a small sample has been checked against Windows: three rows each of Registry Keys, File Security and Service settings. Other shapes, such as empty service descriptors, file paths with environment variables or non-canonical SDDL, are untested.",
  // Folder Redirection (owner-approved wording).
  flags_not_decoded:
    "The Flags number is shown exactly as stored. What each option bit means has not been measured yet.",
  single_capture_only:
    "Only one real Folder Redirection file has been checked against Windows. Files that redirect several folders or several groups are untested.",
  folder_names_documented_not_measured:
    "Folder names come from Microsoft's documented list. Only Documents has been confirmed against a real file; an unknown folder is shown by its ID.",
  read_only_no_writer:
    "This screen only reads files. GPO Studio cannot create or edit Folder Redirection policy yet.",
};

export function plainLimitation(item) {
  return Object.hasOwn(LIMITATION_SENTENCES, item.code)
    ? LIMITATION_SENTENCES[item.code]
    : item.message;
}

export function renderLimitations(limitations) {
  if (!limitations || !limitations.length) return "";
  const plain = limitations
    .map((item) => `<li>${escapeHtml(plainLimitation(item))}</li>`)
    .join("");
  const technical = limitations
    .map(
      (item) =>
        `<li><code>${escapeHtml(item.code)}</code> — ${escapeHtml(item.message)}</li>`,
    )
    .join("");
  return `<div class="rsop-limitations" role="note"><strong>What this answer does not say</strong><ul>${plain}</ul><details><summary>Technical detail</summary><ul>${technical}</ul></details></div>`;
}

export function renderWarnings(warnings) {
  if (!warnings || !warnings.length) return "";
  const items = warnings
    .map((warning) => `<li>${escapeHtml(warning)}</li>`)
    .join("");
  return `<div class="rsop-warnings" role="note"><strong>Warnings from this computation</strong><ul>${items}</ul></div>`;
}

export function formatEffectiveValue(value) {
  if (Array.isArray(value)) return value.join(" · ");
  return String(value ?? "");
}

export function renderSideSettings(side, settings) {
  const label = side === "computer" ? "Computer" : "User";
  if (!settings || !settings.length) {
    return `<h3>${label} settings</h3><div class="table-empty">No ${side}-side value is predicted to apply.</div>`;
  }
  const rows = settings
    .map((setting) => {
      const conditional = setting.unevaluable_gpos?.length
        ? `<div class="rsop-conditional">Conditional: ${escapeHtml(setting.unevaluable_gpos.join(", "))} write this value and could not be evaluated.</div>`
        : "";
      const path = `${setting.hive}\\${setting.key}`;
      return `<tr><td class="mono truncate" title="${escapeHtml(path)}">${escapeHtml(path)}</td><td>${escapeHtml(setting.value_name) || "(Default)"}</td><td>${escapeHtml(formatEffectiveValue(setting.effective_value))}${conditional}</td><td>${escapeHtml(setting.winning_gpo_name)}${setting.is_enforced ? ' <span class="pill">enforced</span>' : ""}</td><td>${setting.overridden_by?.length ? escapeHtml(setting.overridden_by.join(", ")) : "—"}</td></tr>`;
    })
    .join("");
  return `<h3>${label} settings</h3><div class="table-card"><table><thead><tr><th>Key</th><th>Value name</th><th>Effective value</th><th>Winning GPO</th><th>Overrode</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

export function renderGpoResults(gpoResults) {
  if (!gpoResults || !gpoResults.length) {
    return "<h3>GPOs</h3><div class=\"table-empty\">No GPO was linked anywhere on the target's path.</div>";
  }
  const rows = gpoResults
    .map((result) => {
      const pill =
        result.status === "applied"
          ? "ok"
          : result.status === "unevaluable"
            ? "warn"
            : "";
      const sidePill = (value) =>
        value === "applied" ? "ok" : value === "unevaluable" ? "warn" : "";
      const side = (value) =>
        `<td><span class="pill ${sidePill(value)}">${escapeHtml(value ?? "—")}</span></td>`;
      return `<tr><td>${result.precedence}</td><td>${escapeHtml(result.gpo_name)}</td><td><span class="pill ${pill}">${escapeHtml(result.status)}</span></td>${side(result.computer_status)}${side(result.user_status)}<td>${result.filtering_reasons?.length ? escapeHtml(result.filtering_reasons.join(", ")) : "—"}</td><td class="mono truncate" title="${escapeHtml(result.link_scope)}">${escapeHtml(result.link_scope)}</td></tr>`;
    })
    .join("");
  // Both sides are shown because both are now answered (WI-032, closed
  // 2026-09-07). This note used to say the status was not a per-side answer;
  // leaving that in place after the per-side columns arrived would make the UI
  // the last thing still saying so.
  return `<h3>GPOs</h3><p class="rsop-note">"Status" combines both sides. "Computer" and "User" show each side on its own. <code>out_of_scope</code> means that side never looked at the GPO. <code>no_settings_for_side</code> means it did, but the GPO has no settings for that side. Neither means the GPO was filtered out.</p><div class="table-card"><table><thead><tr><th>Order</th><th>GPO</th><th>Status</th><th>Computer</th><th>User</th><th>Reasons</th><th>Linked at</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

export function renderRsopResult(body) {
  const conclusive = body.is_conclusive
    ? ""
    : '<div class="rsop-inconclusive" role="note"><strong>This prediction is not conclusive.</strong> At least one GPO could not be evaluated. The winners below hold only if those GPOs do not apply.</div>';
  return [
    renderLimitations(body.limitations),
    conclusive,
    renderWarnings(body.warnings),
    renderSideSettings("computer", body.computer_settings),
    renderSideSettings("user", body.user_settings),
    renderGpoResults(body.gpo_results),
  ]
    .filter(Boolean)
    .join("");
}

export function initRsop() {
  $("#open-rsop").onclick = openRsop;
  $("#rsop-form").onsubmit = submitRsop;
}

export function openRsop() {
  const form = $("#rsop-form");
  clearFormErrors(form);
  $("#rsop-results").innerHTML =
    '<div class="table-empty">Enter a target and a topology, then select Compute.</div>';
  $("#rsop-dialog").showModal();
}

async function submitRsop(event) {
  event.preventDefault();
  if (event.submitter && event.submitter.value === "cancel") {
    event.currentTarget.closest("dialog").close();
    return;
  }
  const form = event.currentTarget;
  clearFormErrors(form);
  const results = $("#rsop-results");
  let topology;
  try {
    topology = parseTopology(form.topology.value);
  } catch (error) {
    showFormErrors(form, error);
    return;
  }
  results.innerHTML = '<div class="table-empty">Computing…</div>';
  try {
    const body = await api("/api/rsop/compute", {
      method: "POST",
      body: JSON.stringify({
        query_id: form.query_id.value || "ui-query",
        target: {
          computer_name: form.computer_name.value,
          computer_dn: form.computer_dn.value,
          user_name: form.user_name.value,
          user_dn: form.user_dn.value,
          domain: form.domain.value,
          computer_group_memberships: splitPrincipals(
            form.computer_group_memberships.value,
          ),
          user_group_memberships: splitPrincipals(
            form.user_group_memberships.value,
          ),
          loopback_mode: form.loopback_mode.value,
        },
        ...topology,
      }),
    });
    results.innerHTML = renderRsopResult(body);
  } catch (error) {
    results.innerHTML = "";
    showFormErrors(form, error);
  }
}
