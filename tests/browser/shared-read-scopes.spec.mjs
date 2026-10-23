import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";

const warning = "Synthetic newer actionable warning";
const unavailable = "The local GPO Studio server is unavailable";
const surfaces = {
  health: "**/api/health",
  categories: "**/api/admx/categories/tree",
  configured: "**/api/gpos/*/configured-settings?*",
};
function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
async function flush(page) {
  await page.evaluate(
    () =>
      new Promise((resolve) =>
        globalThis.requestAnimationFrame(() =>
          globalThis.requestAnimationFrame(resolve),
        ),
      ),
  );
}
async function selectedPolicy(page, request) {
  const response = await request.post("/api/gpos", {
    data: { name: `Synthetic read scope ${randomUUID()}` },
  });
  expect(response.status()).toBe(201);
  const { gpo } = await response.json();
  await page.goto("/");
  await page.locator(`[data-guid="${gpo.guid}"]`).click();
  await expect(page.locator("#title")).toHaveText(gpo.name);
  return gpo;
}
async function hold(
  page,
  pattern,
  { failure, method = "GET", fetchFirst = true } = {},
) {
  const entered = deferred(),
    release = deferred(),
    done = deferred();
  let once = true;
  await page.route(pattern, async (route) => {
    if (!once || route.request().method() !== method) return route.continue();
    once = false;
    const response = fetchFirst ? await route.fetch() : null;
    if (response) expect(response.status()).toBe(200);
    entered.resolve({ response, request: route.request() });
    await release.promise;
    if (failure === "network") await route.abort("failed");
    else if (failure === "http")
      await route.fulfill({
        status: 503,
        json: { error: { message: "Synthetic scoped read failure" } },
      });
    else await route.fulfill({ response });
    done.resolve();
  });
  return {
    entered: entered.promise,
    release: async ({ settle = true } = {}) => {
      release.resolve();
      await done.promise;
      if (settle) await flush(page);
    },
  };
}
for (const [surface, pattern] of Object.entries(surfaces))
  for (const failure of ["http", "network"]) {
    test(`current ${surface}/${failure} still reports and focuses`, async ({
      page,
      request,
    }) => {
      await selectedPolicy(page, request);
      const held = await hold(page, pattern, { failure });
      await page.locator("#tab-browser").click();
      await held.entered;
      await held.release();
      await expect(page.locator("#error-message")).toContainText(
        failure === "network" ? unavailable : "Synthetic scoped read failure",
      );
      await expect(page.locator("#error-summary")).toBeFocused();
    });
    for (const retirement of [
      "new-warning",
      "new-editor",
      "leave-tab",
      "dismiss",
    ])
      test(`retired ${surface}/${failure}/${retirement} preserves newer context`, async ({
        page,
        request,
      }) => {
        await selectedPolicy(page, request);
        const held = await hold(page, pattern, { failure });
        await page.locator("#tab-browser").click();
        await held.entered;
        let focus = page.locator(
          surface === "health" ? "#search" : "#browser-search",
        );
        if (retirement === "new-editor") {
          await page.locator("#new-gpo").click();
          focus = page.locator('#gpo-form [name="description"]');
          await focus.fill("Synthetic retained draft");
        } else if (retirement === "leave-tab") {
          await page.locator("#tab-history").click();
          focus = page.locator("#search");
        }
        await page.evaluate(async (message) => {
          (await import("/assets/js/api.mjs")).showPersistentError(message);
        }, warning);
        if (retirement === "dismiss")
          await page.locator("#dismiss-error").click();
        await focus.focus();
        await held.release();
        if (retirement === "dismiss")
          await expect(page.locator("#error-summary")).toBeHidden();
        else
          await expect(page.locator("#error-message")).toHaveText(
            `Error: ${warning}`,
          );
        await expect(focus).toBeFocused();
        if (retirement === "new-editor")
          await expect(focus).toHaveValue("Synthetic retained draft");
      });
  }
for (const surface of ["health", "categories"])
  test(`retired successful ${surface} caches without painting or chaining`, async ({
    page,
    request,
  }) => {
    await selectedPolicy(page, request);
    let configured = 0,
      categoryReads = 0;
    page.on("request", (req) => {
      if (req.url().includes("configured-settings?")) configured++;
      if (req.url().endsWith("categories/tree")) categoryReads++;
    });
    const held = await hold(page, surfaces[surface]);
    await page.locator("#tab-browser").click();
    await held.entered;
    await page.locator("#tab-history").click();
    await held.release();
    expect(configured).toBe(0);
    await expect(page.locator("#panel-browser")).toBeHidden();
    await page.locator("#tab-browser").click();
    await expect(page.locator("#browser-category-filter option")).toHaveCount(
      2,
    );
    await expect.poll(() => configured).toBe(1);
    expect(categoryReads).toBe(1);
  });

for (const failure of ["http", "network"])
  for (const retirement of ["active", "reopen", "warning", "new-editor"])
    test(`WMI catalogue ${failure}/${retirement}`, async ({
      page,
      request,
    }) => {
      await selectedPolicy(page, request);
      await page.locator("#tab-security").click();
      const held = await hold(page, "**/api/wmi-filters", {
        failure,
        fetchFirst: false,
      });
      await page.locator("#browse-wmi-catalogue").click();
      await held.entered;
      if (retirement === "reopen") {
        await page
          .locator('#wmi-catalogue-dialog button[value="cancel"]')
          .last()
          .click();
        await page.route("**/api/wmi-filters", (route) =>
          route.fulfill({ json: { count: 0, items: [] } }),
        );
        await page.locator("#browse-wmi-catalogue").click();
        await expect(page.locator("#wmi-catalogue-list")).toContainText(
          "catalogue is empty",
        );
      } else if (retirement === "new-editor") {
        await page
          .locator('#wmi-catalogue-dialog button[value="cancel"]')
          .last()
          .click();
        await page.locator("#new-gpo").click();
      } else if (retirement === "warning")
        await page.evaluate(
          async (message) =>
            (await import("/assets/js/api.mjs")).showPersistentError(message),
          warning,
        );
      const before = await page.locator("#wmi-catalogue-list").textContent();
      await held.release();
      if (retirement === "active")
        await expect(page.locator("#wmi-catalogue-list")).toContainText(
          failure === "network" ? unavailable : "Synthetic scoped read failure",
        );
      else await expect(page.locator("#wmi-catalogue-list")).toHaveText(before);
    });

const previews = [
  {
    name: "security",
    open: "#open-security-template",
    form: "#security-template-form",
    dialog: "#security-template-dialog",
    input: '[name="families"]',
    valid: "{}",
    edited: '{"audit":{"logon_events":"failure"}}',
    button: "Render",
    path: "**/api/security-template/policy-families",
    results: "#security-template-results",
  },
  {
    name: "rsop",
    open: "#open-rsop",
    form: "#rsop-form",
    dialog: "#rsop-dialog",
    input: '[name="topology"]',
    valid: '{"nodes":[],"gpos":[]}',
    edited: '{"nodes":[],"gpos":[],"wmi_filter_results":{}}',
    button: "Compute",
    path: "**/api/rsop/compute",
    results: "#rsop-results",
  },
];
for (const preview of previews)
  for (const failure of ["http", "network", "success"])
    for (const retirement of ["active", "input", "reopen", "warning"])
      test(`${preview.name} POST ${failure}/${retirement}`, async ({
        page,
      }) => {
        await page.goto("/");
        await page.locator(preview.open).click();
        const form = page.locator(preview.form),
          field = form.locator(preview.input);
        await field.fill(preview.valid);
        if (preview.name === "rsop") {
          await form.locator('[name="domain"]').fill("synthetic.test");
          await form.locator('[name="computer_name"]').fill("SyntheticClient");
        }
        // Use the real endpoint response, not a fabricated success schema.
        const held = await hold(page, preview.path, {
          failure,
          method: "POST",
        });
        await form
          .getByRole("button", { name: preview.button, exact: true })
          .click();
        await held.entered;
        if (retirement === "reopen") {
          await page
            .locator(`${preview.dialog} button[value="cancel"]`)
            .last()
            .click();
          await page.locator(preview.open).click();
        }
        if (retirement === "input" || retirement === "reopen")
          await field.fill(preview.edited);
        if (retirement === "warning")
          await page.evaluate(
            async (message) =>
              (await import("/assets/js/api.mjs")).showPersistentError(message),
            warning,
          );
        const before = await page.locator(preview.results).innerHTML();
        await field.focus();
        await held.release();
        if (retirement === "active" && failure !== "success") {
          await expect(form.locator(".form-error")).toContainText(
            failure === "network"
              ? unavailable
              : "Synthetic scoped read failure",
          );
          await expect(form.locator(".form-error")).toBeFocused();
        } else if (
          ["input", "reopen"].includes(retirement) ||
          (failure !== "success" && retirement === "warning")
        ) {
          await expect(page.locator(preview.results)).toHaveJSProperty(
            "innerHTML",
            before,
          );
          await expect(form.locator(".form-error")).toHaveCount(0);
          await expect(field).toBeFocused();
        } else
          await expect(page.locator(preview.results)).not.toHaveJSProperty(
            "innerHTML",
            before,
          );
      });

for (const failure of ["http", "network"])
  test(`configured ${failure} failure cannot clear newer reviewed rows/selection`, async ({
    page,
    request,
  }) => {
    const gpo = await selectedPolicy(page, request);
    const search = await (
      await request.get("/api/admx/search?q=SyntheticToggle")
    ).json();
    const configured = await request.post(
      `/api/admx/policies/${encodeURIComponent(search.items[0].qualified_id)}/configure`,
      {
        data: {
          gpo_guid: gpo.guid,
          expected_revision: gpo.revision,
          side: "computer",
          state: "enabled",
          values: {},
          actor: "browser-test",
          reason: "Synthetic reviewed selection",
        },
      },
    );
    expect(configured.status()).toBe(200);
    await page.reload();
    await page.locator(`[data-guid="${gpo.guid}"]`).click();
    await expect(page.locator("#title")).toHaveText(gpo.name);
    await page.locator("#tab-browser").click();
    const checkbox = page.locator(".browser-bulk-checkbox");
    await checkbox.check();
    await page.locator("#browser-bulk-reason").fill("Synthetic kept selection");
    const held = await hold(page, surfaces.configured, { failure });
    await page.locator("#browser-state-filter").selectOption("enabled");
    await held.entered;
    await page.evaluate(
      async (message) =>
        (await import("/assets/js/api.mjs")).showPersistentError(message),
      warning,
    );
    await page.locator("#browser-bulk-reason").focus();
    await held.release();
    await expect(checkbox).toBeChecked();
    await expect(page.locator("#browser-bulk-reason")).toHaveValue(
      "Synthetic kept selection",
    );
    await expect(page.locator("#error-message")).toHaveText(
      `Error: ${warning}`,
    );
  });

async function scriptsForm(page, request) {
  await selectedPolicy(page, request);
  await page.locator("#open-scripts").click();
  const form = page.locator("#scripts-form");
  await form
    .getByRole("button", { name: "Add startup script", exact: true })
    .click();
  await form
    .getByRole("button", { name: "Add startup script", exact: true })
    .click();
  await form
    .getByRole("button", { name: "Add PowerShell script", exact: true })
    .click();
  await form
    .getByRole("group", { name: "Startup script 1", exact: true })
    .getByLabel("Command")
    .fill("zz-studio-marker.cmd");
  await form
    .getByRole("group", { name: "Startup script 1", exact: true })
    .getByLabel("Parameters")
    .fill("/c alpha beta");
  await form
    .getByRole("group", { name: "Startup script 2", exact: true })
    .getByLabel("Command")
    .fill("zz-studio-second.cmd");
  await form
    .getByRole("group", { name: "PowerShell startup script 1", exact: true })
    .getByLabel("Command")
    .fill("zz-studio-marker.ps1");
  await form
    .getByRole("group", { name: "PowerShell startup script 1", exact: true })
    .getByLabel("Parameters")
    .fill("-Mode Alpha");
  return form;
}
for (const failure of ["http", "network"])
  for (const retirement of ["active", "reopen", "input"])
    test(`Scripts download ${failure}/${retirement} owns error and finalizer`, async ({
      page,
      request,
    }) => {
      const form = await scriptsForm(page, request),
        button = form.locator("#scripts-download");
      const held = await hold(page, "**/gpmc-backup-with-scripts", {
        failure,
        method: "POST",
      });
      await button.click();
      await held.entered;
      if (retirement === "reopen") {
        await form
          .getByRole("button", { name: "Close", exact: true })
          .last()
          .click();
        await page.locator("#open-scripts").click();
      }
      if (retirement === "input")
        await form
          .getByRole("group", { name: "Startup script 1", exact: true })
          .getByLabel("Parameters")
          .fill("/c edited");
      const before = await page.locator("#scripts-status").textContent();
      await held.release();
      if (retirement === "active")
        await expect(form.locator(".form-error")).toContainText(
          failure === "network" ? unavailable : "Synthetic scoped read failure",
        );
      else {
        await expect(form.locator(".form-error")).toHaveCount(0);
        await expect(page.locator("#scripts-status")).toHaveText(before);
      }
      await expect(button).toBeEnabled();
    });

test("Scripts holds actual response bodies: retired delivery/finalizer cannot unlock a newer pending download", async ({
  page,
  request,
}) => {
  await page.addInitScript(() => {
    const original = globalThis.fetch;
    globalThis.scriptBodies = [];
    globalThis.fetch = async (...args) => {
      const response = await original(...args);
      if (
        String(args[0]).endsWith("/gpmc-backup-with-scripts") &&
        args[1]?.method === "POST" &&
        response.ok
      ) {
        const nativeBlob = response.blob.bind(response);
        response.blob = async () => {
          const blob = await nativeBlob();
          await new Promise((resolve) => globalThis.scriptBodies.push(resolve));
          return blob;
        };
      }
      return response;
    };
  });
  const form = await scriptsForm(page, request),
    button = form.locator("#scripts-download");
  let downloads = 0,
    sent = 0;
  page.on("download", () => downloads++);
  page.on("request", (req) => {
    if (req.url().endsWith("/gpmc-backup-with-scripts")) sent++;
  });
  await button.click();
  await expect
    .poll(() => page.evaluate(() => globalThis.scriptBodies.length))
    .toBe(1);
  expect(sent).toBe(1);
  await button.evaluate((control) => control.click());
  expect(sent).toBe(1);
  await form.getByRole("button", { name: "Close", exact: true }).last().click();
  await page.locator("#open-scripts").click();
  // Reopening has fresh, empty rows; the empty request is locally refused.
  // Fill the same valid request again without changing the selected policy.
  await form
    .getByRole("button", { name: "Add startup script", exact: true })
    .click();
  await form
    .getByRole("button", { name: "Add startup script", exact: true })
    .click();
  await form
    .getByRole("button", { name: "Add PowerShell script", exact: true })
    .click();
  await form
    .getByRole("group", { name: "Startup script 1", exact: true })
    .getByLabel("Command")
    .fill("zz-studio-marker.cmd");
  await form
    .getByRole("group", { name: "Startup script 1", exact: true })
    .getByLabel("Parameters")
    .fill("/c alpha beta");
  await form
    .getByRole("group", { name: "Startup script 2", exact: true })
    .getByLabel("Command")
    .fill("zz-studio-second.cmd");
  await form
    .getByRole("group", { name: "PowerShell startup script 1", exact: true })
    .getByLabel("Command")
    .fill("zz-studio-marker.ps1");
  await form
    .getByRole("group", { name: "PowerShell startup script 1", exact: true })
    .getByLabel("Parameters")
    .fill("-Mode Alpha");
  await button.click();
  await expect
    .poll(() => page.evaluate(() => globalThis.scriptBodies.length))
    .toBe(2);
  expect(sent).toBe(2);
  await page.evaluate(() => globalThis.scriptBodies[0]());
  await flush(page);
  expect(downloads).toBe(0);
  await expect(button).toBeDisabled();
  const delivered = page.waitForEvent("download");
  await page.evaluate(() => globalThis.scriptBodies[1]());
  const download = await delivered;
  await expect(
    (await import("node:fs/promises"))
      .stat(await download.path())
      .then((stat) => stat.size),
  ).resolves.toBeGreaterThan(100);
  await expect(button).toBeEnabled();
  expect(downloads).toBe(1);
  expect(sent).toBe(2);
});

const inlineReads = [
  {
    name: "policy History",
    tab: "#tab-history",
    path: "**/api/gpos/*/revisions",
    result: "#history-list",
  },
  {
    name: "comparison History",
    tab: "#tab-diff",
    path: "**/api/gpos/*/revisions",
    result: "#revision-diff-results",
  },
  {
    name: "publication plan",
    open: "#publication-preview",
    path: "**/publication-plan?*",
    result: "#publication-results",
    dialog: "#publication-dialog",
  },
  {
    name: "template sources",
    open: "#open-templates",
    path: "**/api/templates",
    result: "#templates-form .form-error",
    dialog: "#templates-dialog",
  },
];
for (const surface of inlineReads)
  for (const failure of ["http", "network"])
    for (const retirement of ["active", "new-editor"])
      test(`${surface.name} ${failure}/${retirement} owns inline read`, async ({
        page,
        request,
      }) => {
        await selectedPolicy(page, request);
        const held = await hold(page, surface.path, { failure });
        await page.locator(surface.tab || surface.open).click();
        await held.entered;
        if (retirement === "new-editor") {
          if (surface.dialog)
            await page
              .locator(
                `${surface.dialog} button[value="cancel"], ${surface.dialog} [data-close-publication]`,
              )
              .last()
              .click();
          await page.locator("#new-gpo").click();
          await page
            .locator('#gpo-form [name="description"]')
            .fill("Synthetic newer inline context");
          await page.locator('#gpo-form [name="description"]').focus();
        }
        await held.release();
        if (retirement === "active")
          await expect(page.locator(surface.result)).toContainText(
            failure === "network"
              ? unavailable
              : "Synthetic scoped read failure",
          );
        else {
          if (surface.name === "template sources")
            await expect(page.locator(surface.result)).toHaveCount(0);
          else {
            await expect(page.locator(surface.result)).not.toContainText(
              "Synthetic scoped read failure",
            );
            await expect(page.locator(surface.result)).not.toContainText(
              unavailable,
            );
          }
          await expect(
            page.locator('#gpo-form [name="description"]'),
          ).toBeFocused();
        }
      });

for (const failure of ["http", "network"])
  test(`estate POST comparison ${failure} retires on input and does not unlock a successor`, async ({
    page,
    request,
  }) => {
    const selected = await selectedPolicy(page, request),
      ids = [selected.guid];
    for (let i = 0; i < 2; i++)
      ids.push(
        (
          await (
            await request.post("/api/gpos", {
              data: { name: `Synthetic estate comparison ${randomUUID()}` },
            })
          ).json()
        ).gpo.guid,
      );
    await page.reload();
    await page.locator(`[data-guid="${selected.guid}"]`).click();
    await page.locator("#tab-diff").click();
    await page.locator("#diff-baseline").selectOption(ids[0]);
    await page.locator("#diff-draft").selectOption(ids[1]);
    await page.locator("#diff-observed").selectOption(ids[2]);
    const held = await hold(page, "**/api/estate/diff", {
      failure,
      method: "POST",
    });
    await page.locator("#run-diff").click();
    await held.entered;
    await page.locator("#diff-baseline").selectOption(ids[1]);
    const before = await page.locator("#diff-results").innerHTML();
    await held.release();
    await expect(page.locator("#diff-results")).toHaveJSProperty(
      "innerHTML",
      before,
    );
    await expect(page.locator("#run-diff")).toBeEnabled();
    // A fresh comparison remains usable and its active failure stays visible.
    await page.locator("#diff-baseline").selectOption(ids[0]);
    const active = await hold(page, "**/api/estate/diff", {
      failure,
      method: "POST",
    });
    await page.locator("#run-diff").click();
    await active.entered;
    await active.release();
    await expect(page.locator("#diff-results")).toContainText(
      failure === "network" ? unavailable : "Synthetic scoped read failure",
    );
    await expect(page.locator("#run-diff")).toBeEnabled();
  });

for (const failure of ["http", "network"])
  test(`export review ${failure} retains a newer warning and focus`, async ({
    page,
    request,
  }) => {
    await selectedPolicy(page, request);
    await page.locator("#export").click();
    const held = await hold(page, "**/export.zip?*", { failure });
    await page.locator("#export-review-download").click();
    await held.entered;
    await page.evaluate(
      async (message) =>
        (await import("/assets/js/api.mjs")).showPersistentError(message),
      warning,
    );
    await page
      .locator('#export-review-dialog button[value="cancel"]')
      .last()
      .focus();
    await held.release();
    await expect(page.locator("#error-message")).toHaveText(
      `Error: ${warning}`,
    );
    await expect(
      page.locator('#export-review-dialog button[value="cancel"]').last(),
    ).toBeFocused();
    await expect(page.locator("#export-review-issues")).not.toContainText(
      "Synthetic scoped read failure",
    );
    await expect(page.locator("#export-review-download")).toBeEnabled();
  });

async function restoreGroup(page, request) {
  const gpo = await selectedPolicy(page, request);
  const result = await request.post(
    `/api/gpos/${gpo.guid}/preferences/groups`,
    {
      data: {
        scope: "computer",
        expected_revision: gpo.revision,
        group: {
          name: "SyntheticRestoreGroup",
          sid: "S-1-5-32-544",
          action: "update",
        },
        actor: "browser-test",
        reason: "Synthetic restore read",
      },
    },
  );
  expect(result.status()).toBe(201);
  const payload = await result.json();
  await page.reload();
  await page.locator(`[data-guid="${gpo.guid}"]`).click();
  await page.locator("#tab-preferences").click();
  page.on("dialog", (dialog) =>
    dialog.accept(
      dialog.type() === "prompt" ? String(payload.gpo.revision) : undefined,
    ),
  );
  return { gpo: payload.gpo, button: page.locator("[data-restore-gpp-group]") };
}
for (const failure of ["http", "network", "success"])
  for (const retirement of ["active", "new-editor"])
    test(`GPP historical lookup ${failure}/${retirement} separates read from dispatched write`, async ({
      page,
      request,
    }) => {
      const { gpo, button } = await restoreGroup(page, request);
      let commits = 0;
      page.on("request", (req) => {
        if (req.url().endsWith("/restore") && req.method() === "POST")
          commits++;
      });
      const held = await hold(
        page,
        `**/api/gpos/${gpo.guid}/revisions/${gpo.revision}`,
        { failure },
      );
      await button.click();
      await held.entered;
      if (retirement === "new-editor") {
        await page.locator("#new-gpo").click();
        await page
          .locator('#gpo-form [name="description"]')
          .fill("Synthetic preserved lookup draft");
        await page.locator('#gpo-form [name="description"]').focus();
      }
      await held.release();
      if (retirement === "active" && failure !== "success")
        await expect(page.locator("#error-summary")).toContainText(
          failure === "network" ? unavailable : "Synthetic scoped read failure",
        );
      else if (retirement === "new-editor") {
        await expect(page.locator("#error-summary")).toBeHidden();
        await expect(
          page.locator('#gpo-form [name="description"]'),
        ).toBeFocused();
        expect(commits).toBe(0);
      } else await expect.poll(() => commits).toBe(1);
    });

for (const failure of ["http", "network"])
  test(`GPP actual restore ${failure} retains named write outcome after a newer editor`, async ({
    page,
    request,
  }) => {
    const { gpo, button } = await restoreGroup(page, request);
    const held = await hold(page, "**/preferences/groups/*/restore", {
      failure,
      method: "POST",
      fetchFirst: false,
    });
    await button.click();
    await held.entered;
    await page.locator("#new-gpo").click();
    await page
      .locator('#gpo-form [name="description"]')
      .fill("Synthetic retained write outcome draft");
    await held.release();
    await expect(page.locator("#error-summary")).toContainText(gpo.name);
    await expect(page.locator("#error-summary")).toContainText(
      failure === "network" ? unavailable : "Synthetic scoped read failure",
    );
    await expect(page.locator('#gpo-form [name="description"]')).toHaveValue(
      "Synthetic retained write outcome draft",
    );
  });

for (const surface of ["categories", "configured"])
  for (const outcome of ["http", "network", "success"])
    test(`Settings ${surface}/${outcome} retires on input before debounce dispatch`, async ({
      page,
      request,
    }) => {
      await page.clock.install({ time: new Date("2030-01-01T00:00:00Z") });
      const gpo = await selectedPolicy(page, request);
      const policies = await (
        await request.get("/api/admx/search?q=SyntheticToggle")
      ).json();
      const applied = await request.post(
        `/api/admx/policies/${encodeURIComponent(policies.items[0].qualified_id)}/configure`,
        {
          data: {
            gpo_guid: gpo.guid,
            expected_revision: gpo.revision,
            side: "computer",
            state: "enabled",
            values: {},
          },
        },
      );
      expect(applied.status()).toBe(200);
      await page.reload();
      await page.locator(`[data-guid="${gpo.guid}"]`).click();
      await expect(page.locator("#title")).toHaveText(gpo.name);
      // The warning predates the read: the intent guard, rather than a newer-error
      // lease, must reject the old outcome before the replacement request starts.
      await page.evaluate(
        async (message) =>
          (await import("/assets/js/api.mjs")).showPersistentError(message),
        warning,
      );
      const held = await hold(page, surfaces[surface], { failure: outcome });
      let newQueries = 0;
      page.on("request", (req) => {
        const url = new URL(req.url());
        if (
          url.pathname.endsWith("configured-settings") &&
          url.searchParams.get("q") === "zz-current-no-match"
        )
          newQueries++;
      });
      await page.locator("#tab-browser").click();
      await held.entered;
      await page.clock.pauseAt(new Date("2030-01-01T01:00:00Z"));
      const before = await page.locator("#browser-resolved-table").innerHTML();
      await page.locator("#browser-search").fill("zz-current-no-match");
      await held.release({ settle: false });
      await page.clock.runFor(20);
      expect(newQueries).toBe(0);
      await expect(page.locator("#error-message")).toHaveText(
        `Error: ${warning}`,
      );
      await expect(page.locator("#browser-search")).toBeFocused();
      await expect(page.locator("#browser-resolved-table")).toHaveJSProperty(
        "innerHTML",
        before,
      );
      const fresh = page.waitForResponse((response) => {
        const url = new URL(response.url());
        return (
          url.pathname.endsWith("configured-settings") &&
          url.searchParams.get("q") === "zz-current-no-match"
        );
      });
      await page.clock.runFor(250);
      await fresh;
      expect(newQueries).toBe(1);
    });

for (const surface of ["History", "Settings"])
  for (const outcome of ["http", "network", "success"])
    for (const newerWarning of [false, true])
      test(`A ${surface}/${outcome}/${newerWarning ? "warning" : "no-warning"} retires at B selection intent before B detail arrives`, async ({
        page,
        request,
      }) => {
        const a = await selectedPolicy(page, request);
        if (surface === "Settings") {
          const policies = await (
            await request.get("/api/admx/search?q=SyntheticToggle")
          ).json();
          const applied = await request.post(
            `/api/admx/policies/${encodeURIComponent(policies.items[0].qualified_id)}/configure`,
            {
              data: {
                gpo_guid: a.guid,
                expected_revision: a.revision,
                side: "computer",
                state: "enabled",
                values: {},
              },
            },
          );
          expect(applied.status()).toBe(200);
        }

        const b = (
          await (
            await request.post("/api/gpos", {
              data: { name: `Synthetic navigation B ${randomUUID()}` },
            })
          ).json()
        ).gpo;
        await page.reload();
        await page.locator(`[data-guid="${a.guid}"]`).click();
        await expect(page.locator("#title")).toHaveText(a.name);
        const path =
          surface === "History"
            ? `**/api/gpos/${a.guid}/revisions`
            : surfaces.configured;
        const old = await hold(page, path, { failure: outcome });
        await page
          .locator(surface === "History" ? "#tab-history" : "#tab-browser")
          .click();
        await old.entered;
        const next = await hold(page, `**/api/gpos/${b.guid}`);
        const button = page.locator(`[data-guid="${b.guid}"]`);
        await button.click();
        await next.entered;
        await expect(page.locator("#title")).toHaveText(a.name);
        if (newerWarning)
          await page.evaluate(
            async (message) =>
              (await import("/assets/js/api.mjs")).showPersistentError(message),
            warning,
          );
        const before = await page
          .locator(
            surface === "History" ? "#history-list" : "#browser-resolved-table",
          )
          .innerHTML();
        await button.focus();
        await old.release();
        if (newerWarning)
          await expect(page.locator("#error-message")).toHaveText(
            `Error: ${warning}`,
          );
        else await expect(page.locator("#error-summary")).toBeHidden();
        await expect(button).toBeFocused();
        await expect(
          page.locator(
            surface === "History" ? "#history-list" : "#browser-resolved-table",
          ),
        ).toHaveJSProperty("innerHTML", before);
        await next.release();
        await expect(page.locator("#title")).toHaveText(b.name);
      });

for (const surface of ["health", "categories"])
  for (const outcome of ["http", "network"])
    test(`Settings ${surface}/${outcome} stays retired after leaving and reopening its tab`, async ({
      page,
      request,
    }) => {
      await selectedPolicy(page, request);
      const held = await hold(page, surfaces[surface], { failure: outcome });
      await page.locator("#tab-browser").click();
      await held.entered;
      await page.locator("#tab-history").click();
      await page.locator("#tab-browser").click();
      await expect(page.locator("#browser-category-filter option")).toHaveCount(
        2,
      );
      await expect(page.locator("#browser-content")).toBeVisible();
      await page.locator("#browser-search").focus();
      await held.release();
      await expect(page.locator("#error-summary")).toBeHidden();
      await expect(page.locator("#browser-search")).toBeFocused();
      await expect(page.locator("#browser-category-filter option")).toHaveCount(
        2,
      );
    });

for (const outcome of ["http", "network"])
  for (const retirement of ["active", "new-editor"])
    test(`explicit policy detail ${outcome}/${retirement} owns errors independently of underlay success`, async ({
      page,
      request,
    }) => {
      const a = await selectedPolicy(page, request),
        b = (
          await (
            await request.post("/api/gpos", {
              data: { name: `Synthetic detail owner B ${randomUUID()}` },
            })
          ).json()
        ).gpo;
      await page.reload();
      await page.locator(`[data-guid="${a.guid}"]`).click();
      await expect(page.locator("#title")).toHaveText(a.name);
      const held = await hold(page, `**/api/gpos/${b.guid}`, {
        failure: outcome,
      });
      await page.locator(`[data-guid="${b.guid}"]`).click();
      await held.entered;
      if (retirement === "new-editor") {
        await page.locator("#new-gpo").click();
        await page
          .locator('#gpo-form [name="description"]')
          .fill("Synthetic draft without a replacement warning");
        await page.locator('#gpo-form [name="description"]').focus();
      }
      await held.release();
      if (retirement === "active")
        await expect(page.locator("#error-summary")).toContainText(
          outcome === "network" ? unavailable : "Synthetic scoped read failure",
        );
      else {
        await expect(page.locator("#error-summary")).toBeHidden();
        await expect(
          page.locator('#gpo-form [name="description"]'),
        ).toBeFocused();
        await expect(
          page.locator('#gpo-form [name="description"]'),
        ).toHaveValue("Synthetic draft without a replacement warning");
      }
    });

test("post-create list error retires at a newer explicit selection without hiding the committed creation", async ({
  page,
  request,
}) => {
  const a = await selectedPolicy(page, request),
    b = (
      await (
        await request.post("/api/gpos", {
          data: { name: `Synthetic list owner B ${randomUUID()}` },
        })
      ).json()
    ).gpo;
  await page.reload();
  await page.locator(`[data-guid="${a.guid}"]`).click();
  await expect(page.locator("#title")).toHaveText(a.name);
  const held = await hold(page, "**/api/gpos", { failure: "http" });
  await page.locator("#new-gpo").click();
  const name = `Synthetic committed creation ${randomUUID()}`;
  await page.locator('#gpo-form [name="name"]').fill(name);
  await page.locator("#gpo-form button.primary").click();
  await held.entered;
  const button = page.locator(`[data-guid="${b.guid}"]`);
  await button.click();
  await expect(page.locator("#title")).toHaveText(b.name);
  await button.focus();
  await held.release();
  await expect(page.locator("#error-summary")).toBeHidden();
  await expect(button).toBeFocused();
  await expect(page.locator("#title")).toHaveText(b.name);
  expect(
    (await (await request.get("/api/gpos")).json()).items.some(
      (gpo) => gpo.name === name,
    ),
  ).toBe(true);
});
