import { state, $, $$, escapeHtml, applyPayload } from "./state.mjs";
import { api, toast, audit, showPersistentError } from "./api.mjs";

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
const selectedPolicyIds = new Set();

export async function loadBrowser() {
  if (!state.current) return;
  if (admxLoaded === null) {
    try {
      const h = await api("/api/health");
      admxLoaded = h.admx_loaded === true;
    } catch {
      admxLoaded = false;
    }
  }
  $("#browser-empty").hidden = admxLoaded;
  $("#browser-content").hidden = !admxLoaded;
  if (!admxLoaded) return;
  if (!categoryTreeLoaded) {
    categoryTreeLoaded = true;
    await loadCategoryTree();
  }
  await loadConfiguredSettings();
}

async function loadCategoryTree() {
  try {
    const data = await api("/api/admx/categories/tree");
    categoryTree = data.items || [];
    populateCategorySelect();
  } catch (e) {
    toast(e.message);
    categoryTree = [];
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
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.state !== "all") params.set("state", filters.state);
  if (filters.category) params.set("category", filters.category);
  if (filters.side !== "all") params.set("side", filters.side);
  try {
    const data = await api(
      `/api/gpos/${state.current.guid}/configured-settings?${params}`,
    );
    renderBrowser(data);
  } catch (e) {
    toast(e.message);
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
      const checked = selectedPolicyIds.has(p.policy_id) ? " checked" : "";
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
      if (cb.checked) selectedPolicyIds.add(cb.dataset.bulkPolicyId);
      else selectedPolicyIds.delete(cb.dataset.bulkPolicyId);
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
    selectedPolicyIds.has(p.policy_id),
  );
  selectAll.checked = selectedVisible.length === items.length;
  selectAll.indeterminate =
    selectedVisible.length > 0 && selectedVisible.length < items.length;
}

function updateBulkBar() {
  const count = selectedPolicyIds.size;
  const bar = $("#browser-bulk-bar");
  bar.hidden = count === 0;
  $("#browser-bulk-count").textContent = `${count} selected`;
}

async function applyBulkState(targetState) {
  if (!selectedPolicyIds.size) return;
  const reason = $("#browser-bulk-reason").value.trim();
  if (!reason) {
    toast("Enter a reason for the bulk change");
    $("#browser-bulk-reason").focus();
    return;
  }
  const checkboxes = $$(".browser-bulk-checkbox:checked");
  const bySide = new Map();
  for (const cb of checkboxes) {
    const side = cb.dataset.bulkSide;
    if (!bySide.has(side)) bySide.set(side, []);
    bySide.get(side).push(cb.dataset.bulkPolicyId);
  }
  try {
    for (const [side, policyIds] of bySide) {
      const data = await api(
        `/api/gpos/${state.current.guid}/bulk-policy-state`,
        {
          method: "POST",
          body: JSON.stringify({
            ...audit(reason),
            policy_ids: policyIds,
            side,
            target_state: targetState,
          }),
        },
      );
      applyPayload(data);
    }
    selectedPolicyIds.clear();
    $("#browser-bulk-reason").value = "";
    await loadBrowser();
    toast(`Bulk ${targetState.replace("_", " ")} applied`);
  } catch (error) {
    await loadBrowser();
    showPersistentError(error.message);
  }
}

export function initBrowser() {
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
        selectedPolicyIds.add(cb.dataset.bulkPolicyId);
      });
    } else {
      checkboxes.forEach((cb) => {
        cb.checked = false;
        selectedPolicyIds.delete(cb.dataset.bulkPolicyId);
      });
    }
    updateBulkBar();
  };
  $$("[data-bulk-state]").forEach((btn) => {
    btn.onclick = () => applyBulkState(btn.dataset.bulkState);
  });
}
