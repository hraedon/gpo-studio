import { createReadScope } from "./read-context.mjs";
import { $, $$ } from "./state.mjs";
import { api } from "./api.mjs";
import { clearFormErrors, showFormErrors } from "./errors.mjs";
import { checkAdmx } from "./admx.mjs";
import { loadBrowser } from "./browser.mjs";

import {
  catalogueEpoch,
  observeCatalogue,
  onCatalogueChange,
  announceCatalogueChange,
} from "./catalogue-context.mjs";

let dialogGeneration = 0,
  sourcesRequest = 0,
  loadingFolder = false;

const reads=createReadScope({element:()=>$("#templates-dialog")});

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

async function refreshSources(generation, { setPath = false, report = error => showFormErrors($("#templates-form"),error) } = {}) {
  const request = ++sourcesRequest, started = catalogueEpoch();
  const read=reads.capture({isCurrent:()=>generation===dialogGeneration&&request===sourcesRequest,report});
  try {
    const data = await /* request: templates.sources */ api("/api/templates");
    // Catalogue admission is independent of whether this dialog still owns UI.
    if (!observeCatalogue(data, started) || !read.isCurrent()) return null;
    renderSources(data.items);
    if(setPath)$("#templates-form").elements.path.value=data.items.find(source=>source.name==="default")?.path||"";
    return data.items;
  } catch(error) { read.reportError(error); return null; }
}

async function openTemplates() {
  reads.invalidate();
  const generation = ++dialogGeneration;
  const form = $("#templates-form");
  $("#templates-load").disabled = true;
  form.elements.path.disabled = true;
  clearFormErrors(form);
  $("#templates-status").textContent = "Checking loaded templates…";
  $("#templates-dialog").showModal();
  const read=reads.capture();
  try {
    const items = await refreshSources(generation, { setPath: true });
    if (items && read.isCurrent())
      $("#templates-status").textContent = items.length
        ? "Currently loaded sources:"
        : "No template sources are loaded.";
  } catch (error) {
    read.reportError(error);
  } finally {
    if (read.isCurrent()) {
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
  $("#templates-form").addEventListener("input",()=>reads.invalidate());
  $("#templates-dialog").addEventListener("close",()=>reads.invalidate());
  onCatalogueChange(() => {
    if (!$("#templates-dialog").open) return;
    const generation = dialogGeneration,
      ownLoad = loadingFolder;
    const read=reads.capture({isCurrent:()=>generation===dialogGeneration,report:error=>showFormErrors($("#templates-form"),error)});
    refreshSources(generation)
      .then((items) => {
        if (
          items && read.isCurrent() &&
          !ownLoad &&
          !loadingFolder &&
          generation === dialogGeneration
        )
          $("#templates-status").textContent =
            "The template catalogue changed. Review the current sources below.";
      })
      .catch(error=>read.reportError(error));
  });
  $("#open-templates").onclick = openTemplates;
  $$("[data-open-templates]").forEach((button) => {
    button.onclick = openTemplates;
  });
  $("#templates-form").onsubmit = async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = $("#templates-load");
    if (button.disabled || loadingFolder) return;
    const path = form.elements.path.value;
    clearFormErrors(form);
    if (!path.trim()) {
      showFormErrors(form, {
        issues: [
          {
            path: "path",
            message: "ADMX folder must not be empty or whitespace-only.",
          },
        ],
      });
      return;
    }
    const generation = dialogGeneration,
      started = catalogueEpoch();
    loadingFolder = true;
    button.disabled = true;
    form.elements.path.disabled = true;
    $("#templates-status").textContent = "Loading templates…";
    let result;
    try {
      result = await /* request: templates.activate */ api("/api/templates/ingest", {
        method: "POST",
        body: JSON.stringify({
          name: "default",
          kind: "local",
          path,
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
        if (observeCatalogue(result, started)) describeLoad(result);
        else
          $("#templates-status").textContent =
            "The folder load completed, but the catalogue changed again. Review the current sources below.";
        announceCatalogueChange();
        await refreshSources(currentGeneration,{report:error=>{
          $("#templates-sources").textContent="The source list is unavailable. Reopen this dialog to retry.";
          $("#templates-status").append(` Source list refresh failed: ${error.message}`);
        }});
        const browserRead=reads.capture({isCurrent:()=>currentGeneration===dialogGeneration,report:error=>{
          $("#templates-status").append(` Templates are loaded, but the browser could not refresh: ${error.message}`);
        }});
        try {
          if (!$("#panel-admx").hidden) await checkAdmx();
          if (!$("#panel-browser").hidden) await loadBrowser();
        } catch (error) { browserRead.reportError(error); }
      }
    } finally {
      loadingFolder = false;
      if (generation === dialogGeneration) form.elements.path.disabled = false;
      button.disabled = form.elements.path.disabled;
    }
  };
}
