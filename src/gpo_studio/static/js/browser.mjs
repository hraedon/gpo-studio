import {
  state,
  $,
  $$,
  escapeHtml,
  applyPayload,
  updateGpoSummary,
} from "./state.mjs";
import { api, toast, audit, showPersistentError } from "./api.mjs";
import { renderAll, renderList } from "./render.mjs";
import {
  catalogueEpoch as revisionEpoch,
  observeCatalogue,
  observeCatalogueConflict,
  reviewedCatalogueIsCurrent,
  onCatalogueChange,
  initCatalogueMonitoring,
} from "./catalogue-context.mjs";

// Module-level caches. The ADMX catalogue and category tree are workspace-
// level, so they only need to be loaded once per session. The configured
// settings are GPO-level and re-fetched on every loadBrowser() call.
let admxLoaded = null,
  categoryTree = null,
  categoryTreeLoaded = false,
  browserTimer = null;

// Filter state persists across GPO switches, mirroring how state.side behaves
// for the settings tab.
const filters = { side: "all", state: "all", q: "", category: "" };
const selectedPolicies = new Map();
let browserGpoGuid = null;
let browserContext = 0;
let configuredRequest = 0;
let bulkApplying = false;
let catalogueEpoch = 0;
let categoryRevision = null,
  reviewContext = null;
const catalogueReviewMessage =
  "The ADMX catalogue changed. Review the current settings and select them again before making a bulk change.";

function policyKey(policy) {
  return JSON.stringify([policy.side, policy.policy_id]);
}

function selectBrowserGpo(guid) {
  if (browserGpoGuid === guid) return;
  browserGpoGuid = guid;
  browserContext++;
  configuredRequest++;
  selectedPolicies.clear();
  reviewContext = null;
  $("#browser-bulk-reason").value = "";
  renderBrowser({
    resolved: [],
    unresolved: [],
    resolved_count: 0,
    unresolved_count: 0,
  });
}

export function resetBrowserCatalogue() {
  catalogueEpoch++;
  admxLoaded = null;
  categoryTree = null;
  categoryTreeLoaded = false;
  categoryRevision = null;
  reviewContext = null;
  filters.category = "";
  $("#browser-category-filter").value = "";
  configuredRequest++;
  selectedPolicies.clear();
  populateCategorySelect();
  renderBrowser({
    resolved: [],
    unresolved: [],
    resolved_count: 0,
    unresolved_count: 0,
  });
  updateBulkBar();
}

export async function loadBrowser() {
  if (!state.current) return;
  const guid = state.current.guid;
  const epoch = catalogueEpoch,
    started = revisionEpoch();
  selectBrowserGpo(guid);
  updateBulkBar();
  if (admxLoaded === null) {
    try {
      const h = await api("/api/health");
      if (!observeCatalogue(h, started) || epoch !== catalogueEpoch) return;
      admxLoaded = h.admx_loaded === true;
    } catch (error) {
      if (epoch === catalogueEpoch) showPersistentError(error.message);
      return;
    }
  }
  if (
    epoch !== catalogueEpoch ||
    state.current?.guid !== guid ||
    browserGpoGuid !== guid
  )
    return;
  $("#browser-empty").hidden = admxLoaded;
  $("#browser-content").hidden = !admxLoaded;
  if (!admxLoaded) return;
  if (!categoryTreeLoaded) {
    if (!(await loadCategoryTree())) return;
  }
  if (
    epoch !== catalogueEpoch ||
    state.current?.guid !== guid ||
    browserGpoGuid !== guid
  )
    return;
  await loadConfiguredSettings();
}

async function loadCategoryTree() {
  const epoch = catalogueEpoch,
    started = revisionEpoch();
  try {
    const data = await api("/api/admx/categories/tree");
    const observed = observeCatalogue(data, started);
    if (!observed || epoch !== catalogueEpoch) return false;
    categoryRevision = observed.revision;
    categoryTreeLoaded = true;
    categoryTree = data.items || [];
    populateCategorySelect();
    return true;
  } catch (e) {
    if (epoch !== catalogueEpoch) return;
    showPersistentError(e.message);
    categoryTree = [];
    return false;
  }
}

function populateCategorySelect() {
  const sel = $("#browser-category-filter");
  const options = ['<option value="">All categories</option>'];
  function walk(node, depth) {
    const prefix = depth ? "\u2014 ".repeat(depth) : "";
    options.push(
      `<option value="${escapeHtml(node.id)}">${escapeHtml(prefix)}${escapeHtml(node.display_name)} (${node.policy_count})</option>`,
    );
    (node.children || []).forEach((child) => walk(child, depth + 1));
  }
  (categoryTree || []).forEach((node) => walk(node, 0));
  const previous = sel.value;
  sel.innerHTML = options.join("");
  if (previous) sel.value = previous;
}

async function loadConfiguredSettings() {
  if (!state.current) return;
  const guid = state.current.guid;
  const revision = state.current.revision;
  selectBrowserGpo(guid);
  const request = ++configuredRequest,
    started = revisionEpoch();
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.state !== "all") params.set("state", filters.state);
  if (filters.category) params.set("category", filters.category);
  if (filters.side !== "all") params.set("side", filters.side);
  try {
    const data = await api(`/api/gpos/${guid}/configured-settings?${params}`);
    const observed = observeCatalogue(data, started);
    if (
      !observed ||
      request !== configuredRequest ||
      browserGpoGuid !== guid ||
      state.current?.guid !== guid ||
      state.current.revision !== revision
    )
      return;
    if (!Number.isInteger(data.gpo_revision) || data.gpo_revision !== revision)
      throw new Error(
        "The policy snapshot changed or could not be verified. Reload the policy and review its settings before making a bulk change.",
      );
    if (categoryRevision !== observed.revision) return;
    if (
      reviewContext &&
      (reviewContext.catalogueRevision !== observed.revision ||
        reviewContext.gpoRevision !== data.gpo_revision)
    )
      selectedPolicies.clear();
    reviewContext = {
      guid,
      catalogueRevision: observed.revision,
      gpoRevision: data.gpo_revision,
    };
    renderBrowser(data);
  } catch (e) {
    if (request === configuredRequest && state.current?.guid === guid) {
      reviewContext = null;
      selectedPolicies.clear();
      renderBrowser({
        resolved: [],
        unresolved: [],
        resolved_count: 0,
        unresolved_count: 0,
      });
      showPersistentError(e.message);
    }
  }
}

export function formatElementValue(v) {
  if (Array.isArray(v)) return v.join("; ");
  if (typeof v === "boolean") return v ? "true" : "false";
  return String(v);
}

function formatElementValues(values) {
  if (!values || !Object.keys(values).length) return "—";
  return Object.entries(values)
    .map(
      ([k, v]) =>
        `<div class="mono">${escapeHtml(k)} = ${escapeHtml(formatElementValue(v))}</div>`,
    )
    .join("");
}

export function formatUnresolvedValue(item) {
  if (item.action === "delete") return "Delete value";
  if (Array.isArray(item.value)) return item.value.join(" · ");
  return String(item.value);
}

function renderBrowser(data) {
  $("#browser-count").textContent = data.resolved.length;
  const shown = data.resolved.length;
  const total = data.resolved_count;
  $("#browser-resolved-count").textContent =
    shown < total ? `${shown} of ${total}` : total;
  $("#browser-unresolved-count").textContent = data.unresolved_count;
  renderResolved(data.resolved);
  renderUnresolved(data.unresolved);
}

const observationReasons = {
  both_patterns_match: "Enabled and Disabled write the same observed policy-level pattern.",
  no_complete_pattern: "The registry records do not match a complete Enabled or Disabled policy-level pattern.",
  no_state_evidence: "These records contain no positive policy-level state evidence. An empty pattern does not establish an authoring choice.",
  unsupported_state_pattern: "This policy-level pattern cannot be determined safely from the available registry records.",
};

function renderResolved(items) {
  const tbody = $("#browser-resolved-table");
  tbody.innerHTML = items
    .map((p, index) => {
      const detailId = `browser-detail-${index}`;
      const categoryPath = (p.category_path || []).join(" › ") || "—";
      const ambiguousBadge = p.ambiguous
        ? ` <span class="browser-ambiguous" title="Also matches: ${escapeHtml((p.ambiguous_with || []).join(", "))}">ambiguous</span>`
        : "";
      const checked = selectedPolicies.has(policyKey(p)) ? " checked" : "";
      const stateLabel = p.state === "indeterminate" ? "Indeterminate" : p.state;
      const stateReason = p.state === "indeterminate"
        ? observationReasons[p.state_reason] || "The observed state cannot be determined from these registry records."
        : "";
      return (
        `<tr><td><input type="checkbox" class="browser-bulk-checkbox" data-bulk-policy-id="${escapeHtml(p.policy_id)}" data-bulk-side="${escapeHtml(p.side)}" aria-label="Select ${escapeHtml(p.display_name)}"${checked}></td>` +
        `<td><span class="side ${escapeHtml(p.side)}">${escapeHtml(p.side)}</span></td>` +
        `<td><button type="button" class="browser-policy-toggle" aria-expanded="false" aria-controls="${detailId}">${escapeHtml(p.display_name)}${ambiguousBadge}</button></td>` +
        `<td><span class="pill ${p.state === "enabled" ? "ok" : "warn"}">${escapeHtml(stateLabel)}</span></td>` +
        `<td>${escapeHtml(categoryPath)}</td>` +
        `<td>${escapeHtml(p.supported_on) || "—"}</td></tr>` +
        `<tr class="browser-detail-row" id="${detailId}" hidden><td colspan="6">` +
        `<dl class="details">` +
        `<dt>Policy ID</dt><dd class="mono">${escapeHtml(p.policy_id)}</dd>` +
        `<dt>Explanation</dt><dd>${escapeHtml(p.explain_text) || "—"}</dd>` +
        `<dt>Namespace</dt><dd class="mono">${escapeHtml(p.namespace) || "—"}</dd>` +
        `<dt>Supported on</dt><dd>${escapeHtml(p.supported_on) || "—"}</dd>` +
        `<dt>Raw settings</dt><dd>${p.raw_setting_count}</dd>` +
        (stateReason ? `<dt>Observed state</dt><dd>${escapeHtml(stateReason)}</dd>` : "") +
        `<dt>Element values</dt><dd>${formatElementValues(p.element_values)}</dd>` +
        (p.ambiguous
          ? `<dt>Ambiguous with</dt><dd class="mono">${escapeHtml((p.ambiguous_with || []).join(", ")) || "—"}</dd>`
          : "") +
        `</dl></td></tr>`
      );
    })
    .join("");
  $("#browser-resolved-empty").hidden = items.length > 0;
  $$(".browser-policy-toggle").forEach((btn) => {
    btn.onclick = () => {
      const expanded = btn.getAttribute("aria-expanded") === "true";
      btn.setAttribute("aria-expanded", String(!expanded));
      const detail = document.getElementById(btn.getAttribute("aria-controls"));
      if (detail) detail.hidden = expanded;
    };
  });
  $$(".browser-bulk-checkbox").forEach((cb) => {
    cb.onchange = () => {
      const policy = {
        policy_id: cb.dataset.bulkPolicyId,
        side: cb.dataset.bulkSide,
        ...reviewContext,
      };
      if (cb.checked) selectedPolicies.set(policyKey(policy), policy);
      else selectedPolicies.delete(policyKey(policy));
      syncSelectAllCheckbox(items);
      updateBulkBar();
    };
  });
  syncSelectAllCheckbox(items);
  updateBulkBar();
}

function renderUnresolved(items) {
  const tbody = $("#browser-unresolved-table");
  tbody.innerHTML = items
    .map((u) => {
      const path = `${u.hive}\\${u.key}`;
      return (
        `<tr><td><span class="side ${escapeHtml(u.side)}">${escapeHtml(u.side)}</span></td>` +
        `<td><div class="mono truncate" title="${escapeHtml(path)}">${escapeHtml(path)}</div></td>` +
        `<td>${escapeHtml(u.value_name) || "(Default)"}</td>` +
        `<td class="mono">${escapeHtml(u.registry_type)}</td>` +
        `<td><div class="truncate" title="${escapeHtml(formatUnresolvedValue(u))}">${escapeHtml(formatUnresolvedValue(u))}</div></td>` +
        `<td><small>${escapeHtml(u.reason)}</small></td></tr>`
      );
    })
    .join("");
  $("#browser-unresolved-empty").hidden = items.length > 0;
}

function syncSelectAllCheckbox(items) {
  const selectAll = $("#browser-bulk-select-all");
  if (!items.length) {
    selectAll.checked = false;
    selectAll.indeterminate = false;
    return;
  }
  const selectedVisible = items.filter((p) =>
    selectedPolicies.has(policyKey(p)),
  );
  selectAll.checked = selectedVisible.length === items.length;
  selectAll.indeterminate =
    selectedVisible.length > 0 && selectedVisible.length < items.length;
}

function updateBulkBar() {
  const readOnly = state.current?.status === "archived";
  const unreviewed =
    !reviewContext ||
    !reviewedCatalogueIsCurrent(reviewContext.catalogueRevision) ||
    reviewContext.guid !== state.current?.guid ||
    reviewContext.gpoRevision !== state.current?.revision;
  $("#browser-readonly").hidden = !readOnly;
  if (readOnly) selectedPolicies.clear();
  $("#browser-bulk-reason").disabled = bulkApplying || readOnly || unreviewed;
  const count = selectedPolicies.size;
  const bar = $("#browser-bulk-bar");
  bar.hidden = count === 0;
  $("#browser-bulk-count").textContent = `${count} selected`;
  $$("[data-bulk-state]").forEach((button) => {
    button.disabled = bulkApplying || readOnly || unreviewed;
  });
  $$(".browser-bulk-checkbox, #browser-bulk-select-all").forEach((checkbox) => {
    checkbox.disabled = bulkApplying || readOnly || unreviewed;
  });
}

async function applyBulkState(targetState) {
  if (
    bulkApplying ||
    state.current?.status === "archived" ||
    !selectedPolicies.size ||
    state.current?.guid !== browserGpoGuid
  )
    return;
  const reason = $("#browser-bulk-reason").value.trim();
  if (!reason) {
    toast("Enter a reason for the bulk change");
    $("#browser-bulk-reason").focus();
    return;
  }
  if (
    !reviewContext ||
    !reviewedCatalogueIsCurrent(reviewContext.catalogueRevision) ||
    reviewContext.gpoRevision !== state.current.revision
  ) {
    selectedPolicies.clear();
    updateBulkBar();
    showPersistentError(catalogueReviewMessage);
    return;
  }
  const guid = state.current.guid;
  const revision = reviewContext.gpoRevision;
  const reviewedRevision = reviewContext.catalogueRevision,
    started = revisionEpoch();
  const context = browserContext;
  const selected = [...selectedPolicies.values()];
  if (
    selected.some(
      (policy) =>
        policy.catalogueRevision !== reviewedRevision ||
        policy.gpoRevision !== revision,
    )
  ) {
    selectedPolicies.clear();
    updateBulkBar();
    showPersistentError(catalogueReviewMessage);
    return;
  }
  const mutationAudit = audit(reason);
  let payload = null;
  const completed = [];
  bulkApplying = true;
  updateBulkBar();
  try {
    // The API's policy_sides map cannot encode one Both-class policy twice.
    // Keep each side in its own revision, carrying the returned revision forward.
    for (const side of ["computer", "user"]) {
      const policies = selected.filter((policy) => policy.side === side);
      for (let start = 0; start < policies.length; start += 100) {
        const batch = policies.slice(start, start + 100);
        if (!reviewedCatalogueIsCurrent(reviewedRevision))
          throw new Error(catalogueReviewMessage);
        payload = await api(`/api/gpos/${guid}/bulk-policy-state`, {
          method: "POST",
          body: JSON.stringify({
            ...mutationAudit,
            expected_revision: payload?.gpo.revision ?? revision,
            expected_catalogue_revision: reviewedRevision,
            policy_ids: batch.map((policy) => policy.policy_id),
            side,
            target_state: targetState,
          }),
        });
        completed.push(...batch);
        updateGpoSummary(payload.gpo);
        renderList();
      }
    }
    if (state.current?.guid === guid && browserContext === context) {
      if (state.current.revision === revision) {
        applyPayload(payload);
        renderAll({ refreshBrowser: false });
        renderList();
      }
      selectedPolicies.clear();
      $("#browser-bulk-reason").value = "";
      await loadBrowser();
    }
    toast(`Bulk ${targetState.replace("_", " ")} applied`);
  } catch (error) {
    observeCatalogueConflict(error, started);
    const sameCatalogue =
      reviewedCatalogueIsCurrent(reviewedRevision) &&
      error.code !== "catalogue_conflict";
    if (state.current?.guid === guid && browserContext === context) {
      const canRetain = sameCatalogue && state.current.revision === revision;
      if (payload && state.current.revision === revision) {
        applyPayload(payload);
        renderAll({ refreshBrowser: false });
        renderList();
      }
      selectedPolicies.clear();
      if (canRetain) {
        const nextRevision = payload?.gpo.revision ?? revision;
        reviewContext = {
          guid,
          catalogueRevision: reviewedRevision,
          gpoRevision: nextRevision,
        };
        const done = new Set(completed.map(policyKey));
        selected
          .filter((policy) => !done.has(policyKey(policy)))
          .forEach((policy) =>
            selectedPolicies.set(policyKey(policy), {
              ...policy,
              gpoRevision: nextRevision,
            }),
          );
      }
      await loadBrowser();
    }
    const message =
      error.code === "catalogue_conflict"
        ? catalogueReviewMessage
        : error.message;
    showPersistentError(
      completed.length
        ? `${completed.length} ${completed.length === 1 ? "policy" : "policies"} changed before the remaining bulk change failed: ${message}`
        : message,
    );
  } finally {
    bulkApplying = false;
    updateBulkBar();
  }
}

export function initBrowser() {
  initCatalogueMonitoring();
  onCatalogueChange(() => {
    resetBrowserCatalogue();
    if (!bulkApplying && !$("#panel-browser").hidden)
      queueMicrotask(() => loadBrowser());
  });
  $("#browser-search").oninput = (e) => {
    clearTimeout(browserTimer);
    browserTimer = setTimeout(() => {
      filters.q = e.target.value;
      loadConfiguredSettings();
    }, 250);
  };
  $("#browser-state-filter").onchange = (e) => {
    filters.state = e.target.value;
    loadConfiguredSettings();
  };
  $("#browser-category-filter").onchange = (e) => {
    filters.category = e.target.value;
    loadConfiguredSettings();
  };
  $$("#panel-browser .filter-row .chip").forEach((chip) => {
    chip.onclick = () => {
      $$("#panel-browser .filter-row .chip").forEach((c) => {
        c.classList.toggle("active", c === chip);
        c.setAttribute("aria-pressed", String(c === chip));
      });
      filters.side = chip.dataset.side;
      loadConfiguredSettings();
    };
  });
  $("#browser-bulk-select-all").onchange = (e) => {
    const checkboxes = $$(".browser-bulk-checkbox");
    if (e.target.checked) {
      checkboxes.forEach((cb) => {
        cb.checked = true;
        const policy = {
          policy_id: cb.dataset.bulkPolicyId,
          side: cb.dataset.bulkSide,
          ...reviewContext,
        };
        selectedPolicies.set(policyKey(policy), policy);
      });
    } else {
      checkboxes.forEach((cb) => {
        cb.checked = false;
        selectedPolicies.delete(
          policyKey({
            policy_id: cb.dataset.bulkPolicyId,
            side: cb.dataset.bulkSide,
          }),
        );
      });
    }
    updateBulkBar();
  };
  $$("[data-bulk-state]").forEach((btn) => {
    btn.onclick = () => applyBulkState(btn.dataset.bulkState);
  });
}
