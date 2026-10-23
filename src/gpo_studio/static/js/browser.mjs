import {
  state,
  $,
  $$,
  escapeHtml,
  applyPayload,
  updateGpoSummary,
  getGpoGeneration,
  isGpoObservationCurrent,
} from "./state.mjs";
import { api, toast, audit, showPersistentError } from "./api.mjs";
import { createReadScope } from "./read-context.mjs";
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
const browserReads = createReadScope({ element: () => $("#panel-browser") });
const configuredReads = createReadScope({ element: () => $("#panel-browser") });

let admxLoaded = null,
  categoryTree = null,
  categoryTreeLoaded = false,
  browserTimer = null;

// Filter state persists across GPO switches, mirroring how state.side behaves
// for the settings tab.
const filters = { side: "all", state: "all", q: "", category: "" };
const selectedPolicies = new Map();
let browserGpoGuid = null,
  browserGpoGeneration = 0;
let browserContext = 0;
let configuredRequest = 0;
let bulkApplying = false;
let catalogueEpoch = 0;
let categoryRevision = null,
  reviewContext = null;
const catalogueReviewMessage =
  "The ADMX catalogue changed. Review the current settings and select them again before making a bulk change.";

const policyReviewMessage =
  "The policy settings changed. Review the current settings and select them again before making a bulk change.";

function policyKey(policy) {
  return JSON.stringify([policy.side, policy.policy_id]);
}

function selectBrowserGpo(guid) {
  const generation = getGpoGeneration(guid);
  if (browserGpoGuid === guid && browserGpoGeneration === generation) return;
  browserGpoGuid = guid;
  browserGpoGeneration = generation;
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
  browserReads.invalidate();
  configuredReads.invalidate();
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
  const guid = state.current.guid, revision = state.current.revision;
  const generation = getGpoGeneration(guid);
  const epoch = catalogueEpoch,
    started = revisionEpoch();
  selectBrowserGpo(guid);
  const read = browserReads.start({ isCurrent: () => epoch === catalogueEpoch &&
    state.current?.guid === guid && state.current?.revision === revision && generation === getGpoGeneration(guid) });
  updateBulkBar();
  if (admxLoaded === null) {
    try {
      const h = await /* request: settings.health */ api("/api/health");
      if (!observeCatalogue(h, started) || epoch !== catalogueEpoch) return;
      admxLoaded = h.admx_loaded === true;
    } catch (error) {
      read.reportError(error);
      return;
    }
  }
  if (
    !read.isCurrent() ||
    epoch !== catalogueEpoch ||
    state.current?.guid !== guid ||
    browserGpoGuid !== guid ||
    generation !== getGpoGeneration(guid)
  )
    return;
  $("#browser-empty").hidden = admxLoaded;
  $("#browser-content").hidden = !admxLoaded;
  if (!admxLoaded) return;
  if (!categoryTreeLoaded) {
    if (!(await loadCategoryTree(read))) return;
  }
  if (
    !read.isCurrent() ||
    epoch !== catalogueEpoch ||
    state.current?.guid !== guid ||
    browserGpoGuid !== guid ||
    generation !== getGpoGeneration(guid)
  )
    return;
  populateCategorySelect();
  await loadConfiguredSettings();
}

async function loadCategoryTree(read) {
  const epoch = catalogueEpoch,
    started = revisionEpoch();
  try {
    const data = await /* request: settings.categories */ api("/api/admx/categories/tree");
    const observed = observeCatalogue(data, started);
    if (!observed || epoch !== catalogueEpoch) return false;
    categoryRevision = observed.revision;
    categoryTreeLoaded = true;
    categoryTree = data.items || [];
    if (!read.isCurrent()) return false;
    populateCategorySelect();
    return true;
  } catch (e) {
    if (epoch === catalogueEpoch) read.reportError(e);
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
  const generation = getGpoGeneration(guid);
  selectBrowserGpo(guid);
  const request = ++configuredRequest,
    started = revisionEpoch();
  const read = configuredReads.start({
    isCurrent: () => request === configuredRequest && browserGpoGuid === guid &&
      state.current?.guid === guid && state.current?.revision === revision &&
      generation === getGpoGeneration(guid),
    report: error => {
      reviewContext = null;
      selectedPolicies.clear();
      renderBrowser({ resolved: [], unresolved: [], resolved_count: 0, unresolved_count: 0 });
      showPersistentError(error.message);
    },
  });
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.state !== "all") params.set("state", filters.state);
  if (filters.category) params.set("category", filters.category);
  if (filters.side !== "all") params.set("side", filters.side);
  try {
    const data = await /* request: settings.configured */ api(`/api/gpos/${guid}/configured-settings?${params}`);
    const observed = observeCatalogue(data, started);
    if (
      !observed ||
      !read.isCurrent() ||
      request !== configuredRequest ||
      browserGpoGuid !== guid ||
      state.current?.guid !== guid ||
      state.current.revision !== revision ||
      generation !== getGpoGeneration(guid)
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
        reviewContext.gpoRevision !== data.gpo_revision ||
        reviewContext.generation !== generation)
    )
      selectedPolicies.clear();
    reviewContext = {
      guid,
      generation,
      catalogueRevision: observed.revision,
      gpoRevision: data.gpo_revision,
    };
    renderBrowser(data);
  } catch (e) {
    read.reportError(e);
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
      return (
        `<tr><td><input type="checkbox" class="browser-bulk-checkbox" data-bulk-policy-id="${escapeHtml(p.policy_id)}" data-bulk-side="${escapeHtml(p.side)}" aria-label="Select ${escapeHtml(p.display_name)}"${checked}></td>` +
        `<td><span class="side ${escapeHtml(p.side)}">${escapeHtml(p.side)}</span></td>` +
        `<td><button type="button" class="browser-policy-toggle" aria-expanded="false" aria-controls="${detailId}">${escapeHtml(p.display_name)}${ambiguousBadge}</button></td>` +
        `<td><span class="pill ${p.state === "enabled" ? "ok" : "warn"}">${escapeHtml(p.state)}</span></td>` +
        `<td>${escapeHtml(categoryPath)}</td>` +
        `<td>${escapeHtml(p.supported_on) || "—"}</td></tr>` +
        `<tr class="browser-detail-row" id="${detailId}" hidden><td colspan="6">` +
        `<dl class="details">` +
        `<dt>Policy ID</dt><dd class="mono">${escapeHtml(p.policy_id)}</dd>` +
        `<dt>Explanation</dt><dd>${escapeHtml(p.explain_text) || "—"}</dd>` +
        `<dt>Namespace</dt><dd class="mono">${escapeHtml(p.namespace) || "—"}</dd>` +
        `<dt>Supported on</dt><dd>${escapeHtml(p.supported_on) || "—"}</dd>` +
        `<dt>Raw settings</dt><dd>${p.raw_setting_count}</dd>` +
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
    reviewContext.gpoRevision !== state.current?.revision ||
    reviewContext.generation !== getGpoGeneration(reviewContext.guid);
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
  const staleCatalogue =
    reviewContext &&
    !reviewedCatalogueIsCurrent(reviewContext.catalogueRevision);
  if (
    !reviewContext ||
    staleCatalogue ||
    reviewContext.gpoRevision !== state.current.revision ||
    reviewContext.generation !== getGpoGeneration(reviewContext.guid)
  ) {
    selectedPolicies.clear();
    updateBulkBar();
    showPersistentError(
      staleCatalogue ? catalogueReviewMessage : policyReviewMessage,
    );
    return;
  }
  const guid = state.current.guid;
  const revision = reviewContext.gpoRevision;
  const generation = reviewContext.generation;
  const reviewedRevision = reviewContext.catalogueRevision,
    started = revisionEpoch();
  const context = browserContext;
  const selected = [...selectedPolicies.values()];
  const selectedCatalogueChanged = selected.some(
    (policy) => policy.catalogueRevision !== reviewedRevision,
  );
  if (
    selectedCatalogueChanged ||
    selected.some(
      (policy) =>
        policy.gpoRevision !== revision || policy.generation !== generation,
    )
  ) {
    selectedPolicies.clear();
    updateBulkBar();
    showPersistentError(
      selectedCatalogueChanged ? catalogueReviewMessage : policyReviewMessage,
    );
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
        if (generation !== getGpoGeneration(guid))
          throw new Error(
            "This policy was deleted. Reload and review the target before applying more changes.",
          );
        if (!reviewedCatalogueIsCurrent(reviewedRevision))
          throw new Error(catalogueReviewMessage);
        payload = await /* request: settings.bulk */ api(`/api/gpos/${guid}/bulk-policy-state`, {
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
        if (!isGpoObservationCurrent(payload.gpo)) return;
        completed.push(...batch);
        updateGpoSummary(payload.gpo);
        renderList();
      }
    }
    if (
      state.current?.guid === guid &&
      browserContext === context &&
      generation === getGpoGeneration(guid)
    ) {
      if (state.current.revision === revision) {
        if (!applyPayload(payload)) return;
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
    if (
      state.current?.guid === guid &&
      browserContext === context &&
      generation === getGpoGeneration(guid)
    ) {
      const canRetain = sameCatalogue && state.current.revision === revision;
      if (payload && state.current.revision === revision) {
        if (!applyPayload(payload)) return;
        renderAll({ refreshBrowser: false });
        renderList();
      }
      selectedPolicies.clear();
      if (canRetain) {
        const nextRevision = payload?.gpo.revision ?? revision;
        reviewContext = {
          guid,
          generation,
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
    browserReads.invalidate();
    configuredReads.invalidate();
    configuredRequest++;
    filters.q = e.target.value;
    clearTimeout(browserTimer);
    browserTimer = setTimeout(() => {
      loadBrowser();
    }, 250);
  };
  $("#browser-state-filter").onchange = (e) => {
    filters.state = e.target.value;
    loadBrowser();
  };
  $("#browser-category-filter").onchange = (e) => {
    filters.category = e.target.value;
    loadBrowser();
  };
  $$("#panel-browser .filter-row .chip").forEach((chip) => {
    chip.onclick = () => {
      $$("#panel-browser .filter-row .chip").forEach((c) => {
        c.classList.toggle("active", c === chip);
        c.setAttribute("aria-pressed", String(c === chip));
      });
      filters.side = chip.dataset.side;
      loadBrowser();
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
