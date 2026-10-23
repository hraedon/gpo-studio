import { $, $$ } from "./state.mjs";
import { api } from "./api.mjs";
import { clearFormErrors, showFormErrors } from "./errors.mjs";
import { checkAdmx, resetAdmxCatalogue } from "./admx.mjs";
import { loadBrowser, resetBrowserCatalogue } from "./browser.mjs";

let dialogGeneration = 0,
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

async function openTemplates() {
  const generation = ++dialogGeneration;
  const form = $("#templates-form");
  $("#templates-load").disabled = true;
  form.elements.path.disabled = true;
  clearFormErrors(form);
  $("#templates-status").textContent = "Checking loaded templates…";
  $("#templates-dialog").showModal();
  try {
    const data = await api("/api/templates");
    if (generation !== dialogGeneration) return;
    renderSources(data.items);
    const source = data.items.find((item) => item.name === "default");
    form.elements.path.value = source?.path || "";
    $("#templates-status").textContent = data.items.length
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
    $("#templates-status").textContent = "Loading templates…";
    try {
      const result = await api("/api/templates/ingest", {
        method: "POST",
        body: JSON.stringify({
          name: "default",
          kind: "local",
          path: form.elements.path.value.trim(),
        }),
      });
      if (generation === dialogGeneration) {
        form.elements.path.value = result.path;
        renderSources([
          {
            ...result,
            error_count: result.errors.length,
          },
        ]);
        const status = $("#templates-status");
        status.textContent = `Loaded ${result.policy_count} policies from ${result.file_count} files.`;
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
      resetAdmxCatalogue();
      resetBrowserCatalogue();
      if (!$("#panel-admx").hidden) await checkAdmx();
      if (!$("#panel-browser").hidden) await loadBrowser();
    } catch (error) {
      if (generation !== dialogGeneration) return;
      $("#templates-status").textContent =
        "The folder could not be loaded. Previously loaded templates are still available.";
      showFormErrors(form, error);
    } finally {
      loadingFolder = false;
      button.disabled = $("#templates-form").elements.path.disabled;
    }
  };
}
