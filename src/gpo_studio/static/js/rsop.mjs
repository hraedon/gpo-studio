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

export function renderLimitations(limitations) {
  if (!limitations || !limitations.length) return "";
  const items = limitations
    .map(
      (item) =>
        `<li><code>${escapeHtml(item.code)}</code> — ${escapeHtml(item.message)}</li>`,
    )
    .join("");
  return `<div class="rsop-limitations" role="note"><strong>What this answer does not say</strong><ul>${items}</ul></div>`;
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
      return `<tr><td>${result.precedence}</td><td>${escapeHtml(result.gpo_name)}</td><td><span class="pill ${pill}">${escapeHtml(result.status)}</span></td><td>${result.filtering_reasons?.length ? escapeHtml(result.filtering_reasons.join(", ")) : "—"}</td><td class="mono truncate" title="${escapeHtml(result.link_scope)}">${escapeHtml(result.link_scope)}</td></tr>`;
    })
    .join("");
  // The heading says "at least one side" because the column does. Naming it
  // "Applied to" here would undo in the UI what the API is careful about.
  return `<h3>GPOs</h3><p class="rsop-note">Status is "applied on at least one side" — not a per-side answer (WI-032).</p><div class="table-card"><table><thead><tr><th>Order</th><th>GPO</th><th>Status</th><th>Reasons</th><th>Linked at</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

export function renderRsopResult(body) {
  const conclusive = body.is_conclusive
    ? ""
    : '<div class="rsop-inconclusive" role="note"><strong>This prediction is not conclusive.</strong> At least one GPO could not be evaluated, so the winners below are the answer only if those GPOs do not apply.</div>';
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
    '<div class="table-empty">Describe a target and a topology, then compute.</div>';
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
