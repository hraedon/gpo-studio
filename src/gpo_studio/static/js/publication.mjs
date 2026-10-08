import { state, $, escapeHtml } from "./state.mjs";
import { api } from "./api.mjs";
import { renderLimitations } from "./rsop.mjs";

// The publication preview (Plan 034), review only.
//
// `/api/gpos/{guid}/publication-plan` returns `publication.py`'s account of
// what publishing the selected policy would take, with a `coverage` mark on
// every step: `measured` when the publication-completeness lane grades that
// step against Windows, `unmeasured` when no lane does, `refused` when the
// planner will not publish it. Nothing in this panel writes, and it says so
// before anything else.
//
// Steps are grouped by where they would act -- SYSVOL or the directory (AD) --
// because the lane's coverage splits that way: it measured the SYSVOL file set
// and the extension-list attributes, and nothing else on the directory side.
// A refusal step that applies to the whole plan has the plan's own target and
// is listed under "Whole plan".

export const COVERAGE_LABELS = {
  measured: "Measured",
  unmeasured: "Unmeasured",
  refused: "Refused",
};

const COVERAGE_PILLS = { measured: "ok", unmeasured: "warn", refused: "error" };

export const GROUPS = [
  { target: "sysvol", heading: "SYSVOL" },
  { target: "ad", heading: "Active Directory" },
  { target: "both", heading: "Whole plan" },
];

export function groupSteps(steps) {
  return GROUPS.map((group) => ({
    ...group,
    steps: steps.filter((step) => step.target === group.target),
  })).filter((group) => group.steps.length);
}

export function coverageBadge(coverage) {
  const label = COVERAGE_LABELS[coverage] || coverage;
  return `<span class="pill ${COVERAGE_PILLS[coverage] || ""}">${escapeHtml(label)}</span>`;
}

function stepFacts(step) {
  const facts = [];
  if (step.sysvol_path) {
    facts.push(`Path <span class="mono">${escapeHtml(step.sysvol_path)}</span>`);
  }
  if (step.directory_attribute) {
    facts.push(
      `<span class="mono">${escapeHtml(step.directory_attribute)}</span> = <span class="mono">${escapeHtml(step.directory_value ?? "")}</span>`,
    );
  }
  if (step.version_half) {
    facts.push(`Version half: ${escapeHtml(step.version_half)}`);
  }
  return facts.length
    ? `<p class="publication-facts">${facts.join("<br>")}</p>`
    : "";
}

export function renderStep(step) {
  return `<li class="publication-step" data-coverage="${escapeHtml(step.coverage)}">
    <div class="publication-step-head">${coverageBadge(step.coverage)} <span class="mono">${escapeHtml(step.operation)}</span></div>
    <p>${escapeHtml(step.detail)}</p>
    ${stepFacts(step)}
  </li>`;
}

function renderGroups(steps, level) {
  return groupSteps(steps)
    .map(
      (group) =>
        `<section class="publication-group"><h${level}>${escapeHtml(group.heading)}</h${level}><ul class="publication-steps">${group.steps.map(renderStep).join("")}</ul></section>`,
    )
    .join("");
}

export function renderIssues(issues) {
  if (!issues || !issues.length) return "";
  const items = issues
    .map(
      (issue) =>
        `<li><strong>${escapeHtml(issue.level)}</strong>: ${escapeHtml(issue.message)} <code>${escapeHtml(issue.check)}</code></li>`,
    )
    .join("");
  return `<div class="rsop-inconclusive" role="note"><strong>The planner refuses this publication</strong><ul>${items}</ul></div>`;
}

export function renderAbsences(absences) {
  if (!absences || !absences.length) return "";
  const items = absences
    .map(
      (absence) =>
        `<li>${coverageBadge(absence.coverage)} <span class="mono">${escapeHtml(absence.sysvol_path)}</span>: ${escapeHtml(absence.detail)}</li>`,
    )
    .join("");
  return `<h3>Files the plan does not write</h3><ul class="publication-absences">${items}</ul>`;
}

export function renderPublicationPlan(body) {
  const paths = body.planned_sysvol_paths.length
    ? `<ul class="mono">${body.planned_sysvol_paths.map((path) => `<li>${escapeHtml(path)}</li>`).join("")}</ul>`
    : '<p class="rsop-note">This plan names no SYSVOL file.</p>';
  return [
    renderLimitations(body.limitations),
    renderIssues(body.issues),
    `<dl class="details publication-summary"><dt>Target</dt><dd>${escapeHtml(body.target)}</dd><dt>Risk</dt><dd>${escapeHtml(body.risk_level)}</dd><dt>Payload digest</dt><dd class="mono">${escapeHtml(body.payload_digest)}</dd></dl>`,
    "<h3>Steps</h3>",
    body.steps.length
      ? renderGroups(body.steps, 4)
      : '<p class="table-empty">This plan has no steps.</p>',
    renderAbsences(body.absences),
    "<h3>SYSVOL paths the plan names</h3>",
    paths,
    "<details><summary>Rollback steps</summary>",
    body.rollback_steps.length
      ? renderGroups(body.rollback_steps, 4)
      : '<p class="rsop-note">No rollback steps.</p>',
    "</details>",
  ]
    .filter(Boolean)
    .join("");
}

let generation = 0;

async function load() {
  const results = $("#publication-results");
  const status = $("#publication-status");
  const target = $("#publication-target").value;
  generation += 1;
  const mine = generation;
  results.setAttribute("aria-busy", "true");
  status.textContent = "Building the plan…";
  try {
    const body = await api(
      `/api/gpos/${encodeURIComponent(state.current.guid)}/publication-plan?target=${encodeURIComponent(target)}`,
    );
    if (mine !== generation) return;
    results.innerHTML = renderPublicationPlan(body);
    status.textContent = body.refused
      ? "Plan ready. The planner refuses this publication; see the reasons above the steps."
      : "Plan ready. Review the limits above the steps.";
  } catch (error) {
    if (mine !== generation) return;
    results.replaceChildren();
    const message = document.createElement("p");
    message.className = "form-error";
    message.setAttribute("role", "alert");
    message.textContent = `Error: ${error.message}`;
    results.appendChild(message);
    status.textContent = "The plan could not be built.";
  } finally {
    if (mine === generation) results.setAttribute("aria-busy", "false");
  }
}

export function openPublication() {
  if (!state.current) return;
  $("#publication-target").value = "both";
  $("#publication-policy").textContent = `Policy: ${state.current.name}`;
  $("#publication-results").replaceChildren();
  $("#publication-dialog").showModal();
  load();
}

export function initPublication() {
  const dialog = $("#publication-dialog");
  $("#publication-preview").onclick = openPublication;
  $("#publication-target").onchange = load;
  dialog.querySelectorAll("[data-close-publication]").forEach((button) => {
    button.onclick = () => dialog.close();
  });
  dialog.addEventListener("close", () => {
    generation += 1;
  });
}
