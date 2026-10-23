import { $, $$ } from "./state.mjs";
import { api } from "./api.mjs";
import { clearFormErrors, showFormErrors } from "./errors.mjs";
import { checkAdmx, resetAdmxCatalogue } from "./admx.mjs";
import { loadBrowser, resetBrowserCatalogue } from "./browser.mjs";

let dialogGeneration = 0,
  sourcesRequest = 0,
  loadingFolder = false;

function renderSources(items) {
  const container = $("#templates-sources");
  container.replaceChildren();
  for (const source of items) {
    const row = document.createElement("p");
    row.className = "template-source";
    const title = document.createElement("strong");
    title.textContent = source.name;
    const path = document.createElement("code");
    path.textContent = source.path;
    const counts = document.createElement("small");
    counts.textContent = `${source.policy_count} policies · ${source.file_count} files · ${source.error_count} file issues`;
    row.append(title, path, counts);
    container.appendChild(row);
  }
}

async function refreshSources(generation, { setPath = false } = {}) {
  const request = ++sourcesRequest;
  let data;
  try {
    data = await api("/api/templates");
  } catch (error) {
    if (generation !== dialogGeneration || request !== sourcesRequest)
      return null;
    throw error;
  }
  if (generation !== dialogGeneration || request !== sourcesRequest)
    return null;
  renderSources(data.items);
  if (setPath)
    $("#templates-form").elements.path.value =
      data.items.find((source) => source.name === "default")?.path || "";
  return data.items;
}

async function openTemplates() {
  const generation = ++dialogGeneration;
  const form = $("#templates-form");
  $("#templates-load").disabled = true;
  form.elements.path.disabled = true;
  clearFormErrors(form);
  $("#templates-status").textContent = "Checking loaded templates…";
  $("#templates-dialog").showModal();
  try {
    const items = await refreshSources(generation, { setPath: true });
    if (items)
      $("#templates-status").textContent = items.length
        ? "Currently loaded sources:"
        : "No template sources are loaded.";
  } catch (error) {
    if (generation === dialogGeneration) showFormErrors(form, error);
  } finally {
    if (generation === dialogGeneration) {
      form.elements.path.disabled = false;
      $("#templates-load").disabled = loadingFolder;
    }
  }
}

function describeLoad(result) {
  const status = $("#templates-status");
  status.textContent = `Loaded ${result.policy_count} policies from ${result.file_count} files in ${result.path}.`;
  if (!result.policy_count)
    status.append(
      " No usable policies were found. Check that this is the ADMX folder and that matching ADML files are present.",
    );
  if (result.errors.length) {
    const list = document.createElement("ul");
    for (const issue of result.errors) {
      const item = document.createElement("li");
      item.textContent = `${issue.path}: ${issue.message}`;
      list.appendChild(item);
    }
    status.appendChild(list);
  }
}

export function initTemplates() {
  $("#open-templates").onclick = openTemplates;
  $$("[data-open-templates]").forEach((button) => {
    button.onclick = openTemplates;
  });
  $("#templates-form").onsubmit = async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = $("#templates-load");
    if (button.disabled || loadingFolder) return;
    const generation = dialogGeneration;
    loadingFolder = true;
    clearFormErrors(form);
    button.disabled = true;
    form.elements.path.disabled = true;
    $("#templates-status").textContent = "Loading templates…";
    let result;
    try {
      result = await api("/api/templates/ingest", {
        method: "POST",
        body: JSON.stringify({
          name: "default",
          kind: "local",
          path: form.elements.path.value.trim(),
        }),
      });
    } catch (error) {
      if (generation === dialogGeneration) {
        $("#templates-status").textContent =
          "The folder could not be loaded. Previously loaded templates are still available.";
        showFormErrors(form, error);
      } else
        $("#templates-status").textContent =
          `An earlier folder load failed: ${error.message}`;
    }
    try {
      if (result) {
        // Activation succeeded. Refresh the complete source list even after the
        // dialog was reopened, while retaining the new editor's chosen path.
        const currentGeneration = dialogGeneration;
        if (generation === currentGeneration)
          form.elements.path.value = result.path;
        describeLoad(result);
        resetAdmxCatalogue();
        resetBrowserCatalogue();
        try {
          await refreshSources(currentGeneration);
        } catch (error) {
          if (currentGeneration === dialogGeneration) {
            $("#templates-sources").textContent =
              "The source list is unavailable. Reopen this dialog to retry.";
            $("#templates-status").append(
              ` Source list refresh failed: ${error.message}`,
            );
          }
        }
        try {
          if (!$("#panel-admx").hidden) await checkAdmx();
          if (!$("#panel-browser").hidden) await loadBrowser();
        } catch (error) {
          if (currentGeneration === dialogGeneration)
            $("#templates-status").append(
              ` Templates are loaded, but the browser could not refresh: ${error.message}`,
            );
        }
      }
    } finally {
      loadingFolder = false;
      if (generation === dialogGeneration) form.elements.path.disabled = false;
      button.disabled = form.elements.path.disabled;
    }
  };
}
