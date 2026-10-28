import { test, expect } from "@playwright/test";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
const namespace = "Synthetic.AdmxReadProbe",
  toggle = `${namespace}:Toggle`;
const warning = "Synthetic newer ADMX warning: review this current draft.";
let directory, original, revision;
function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function pack(variant) {
  const path = join(directory, variant);
  mkdirSync(path);
  writeFileSync(
    join(path, "probe.admx"),
    String.raw`<policyDefinitions><policyNamespaces><target prefix="synthetic" namespace="${namespace}"/></policyNamespaces><policies>
 <policy name="Feature" class="Machine" key="Software\Policies\Synthetic\AdmxRead${variant}" valueName="FeatureState" displayName="Synthetic probe feature" presentation="$(presentation.Feature)"><enabledValue><decimal value="1"/></enabledValue><disabledValue><decimal value="0"/></disabledValue><elements><text id="DraftText" valueName="DraftText"/></elements></policy>
 <policy name="Toggle" class="Machine" key="Software\Policies\Synthetic\AdmxRead${variant}" valueName="ToggleState" displayName="Synthetic probe toggle"><enabledValue><decimal value="1"/></enabledValue><disabledValue><decimal value="0"/></disabledValue></policy>
 </policies></policyDefinitions>`,
  );
  writeFileSync(
    join(path, "probe.adml"),
    '<policyDefinitionResources><resources><stringTable/><presentationTable><presentation id="Feature"><textBox refId="DraftText"><label>Probe draft text</label></textBox></presentation></presentationTable></resources></policyDefinitionResources>',
  );
}
async function activate(request, variant) {
  const response = await request.post("/api/templates/ingest", {
    data: { name: "default", kind: "local", path: join(directory, variant) },
  });
  expect(response.status()).toBe(201);
  const body = await response.json();
  expect(body.policy_count).toBe(2);
  return body.catalogue_revision;
}
test.beforeAll(async ({ request }) => {
  original = (await (await request.get("/api/templates")).json()).items.find(
    (x) => x.name === "default",
  );
  directory = mkdtempSync(join(tmpdir(), "studio-admx-read-"));
  pack("A");
  pack("B");
  revision = await activate(request, "A");
});
test.afterAll(async ({ request }) => {
  try {
    expect(
      (
        await request.post("/api/templates/ingest", {
          data: { name: "default", kind: original.kind, path: original.path },
        })
      ).status(),
    ).toBe(201);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
async function seed(request, label) {
  const response = await request.post("/api/gpos", {
    data: { name: `Synthetic ADMX ${label} ${randomUUID()}` },
  });
  expect(response.status()).toBe(201);
  return (await response.json()).gpo;
}
async function start(page, request, { openAdmx = true } = {}) {
  const alpha = await seed(request, "Alpha"),
    beta = await seed(request, "Beta");
  await page.goto("/");
  await page.locator(`[data-guid="${alpha.guid}"]`).click();
  await expect(page.locator("#title")).toHaveText(alpha.name);
  if (openAdmx) {
    await page.getByRole("tab", { name: "ADMX", exact: true }).click();
    await expect(page.locator(".admx-result")).toHaveCount(2);
  }
  return { alpha, beta };
}
async function hold(page, kind, failure) {
  const arrived = deferred(),
    release = deferred(),
    done = deferred();
  let captured = false;
  await page.route(
    kind === "search" ? "**/api/admx/search?*" : "**/api/admx/policies/*",
    async (route) => {
      const url = new URL(route.request().url());
      const matches =
        kind === "search"
          ? url.searchParams.get("q") === "feature"
          : decodeURIComponent(url.pathname).endsWith(toggle);
      if (!matches || captured) return route.continue();
      captured = true;
      const response = await route.fetch();
      const payload = await response.json();
      expect(response.status()).toBe(200);
      arrived.resolve(payload);
      await release.promise;
      if (failure === "success") await route.fulfill({ response });
      else if (failure === "network") await route.abort("failed");
      else
        await route.fulfill({
          status: 503,
          json: {
            error: { message: `Synthetic retired ADMX ${kind} failure` },
          },
        });
      done.resolve();
    },
  );
  return {
    arrived: arrived.promise,
    done: done.promise,
    release: () => release.resolve(),
  };
}
async function trigger(page, kind) {
  if (kind === "search") await page.locator("#admx-search").fill("feature");
  else
    await page
      .locator(".admx-result", { hasText: "Synthetic probe toggle" })
      .click();
}
async function configure(page, target) {
  await page
    .locator(".admx-result", { hasText: "Synthetic probe feature" })
    .click();
  await expect(page.locator("#admx-detail h3")).toHaveText(
    "Synthetic probe feature",
  );
  await page.locator("#admx-configure-btn").click();
  const dialog = page.locator("#configure-dialog");
  await expect(dialog.locator("#configure-target-gpo")).toHaveValue(
    target.guid,
  );
  await dialog
    .locator('[data-elem-id="DraftText"]')
    .fill("Keep this reviewed ADMX draft");
  await dialog.locator('[name="reason"]').fill("Keep this current reason");
  return dialog;
}
async function markWarning(page, focus) {
  await page.evaluate(async (message) => {
    const { showPersistentError } = await import("/assets/js/api.mjs");
    showPersistentError(message);
  }, warning);
  await focus.focus();
}
async function flush(page) {
  await page.evaluate(
    () => new Promise((resolve) => globalThis.requestAnimationFrame(resolve)),
  );
}
for (const kind of ["search", "detail"])
  for (const scenario of ["newer-read", "newer-editor", "catalogue"])
    for (const failure of ["http", "network"]) {
      test(`${kind}/${scenario}/${failure} cannot report an obsolete failure ${kind === "search" && scenario === "newer-read" && failure === "http" ? "@smoke" : ""}`, async ({
        page,
        request,
      }) => {
        const { alpha, beta } = await start(page, request);
        const pending = await hold(page, kind, failure);
        const writes = [];
        page.on("request", (req) => {
          if (req.method() === "POST" && req.url().endsWith("/configure"))
            writes.push(req.postDataJSON());
        });
        let activated = false;
        try {
          await trigger(page, kind);
          const old = await pending.arrived;
          expect(old.catalogue_revision).toBe(revision);
          let dialog = null,
            target = alpha,
            focus = page.locator("#admx-search");
          if (scenario === "newer-read" && kind === "search") {
            const response = page.waitForResponse(
              (r) =>
                new URL(r.url()).pathname === "/api/admx/search" &&
                new URL(r.url()).searchParams.get("q") === "toggle",
            );
            await page.locator("#admx-search").fill("toggle");
            await response;
            await expect(page.locator(".admx-result")).toHaveCount(1);
            await expect(page.locator(".admx-result")).toContainText(
              "Synthetic probe toggle",
            );
          } else {
            if (scenario === "newer-editor") {
              target = beta;
              await page.locator(`[data-guid="${beta.guid}"]`).click();
              await expect(page.locator("#title")).toHaveText(beta.name);
              await expect(
                page.locator(".admx-result", {
                  hasText: "Synthetic probe feature",
                }),
              ).toBeVisible();
            }
            dialog = await configure(page, target);
            focus = dialog.locator('[data-elem-id="DraftText"]');
          }
          if (scenario === "catalogue") {
            const next = await activate(request, "B");
            expect(next).not.toBe(revision);
            activated = true;
            const health = page.waitForResponse(
              (r) => new URL(r.url()).pathname === "/api/health",
            );
            await page.evaluate(() =>
              globalThis.dispatchEvent(new Event("focus")),
            );
            await health;
            await expect(dialog.locator("form")).toHaveAttribute(
              "data-catalogue-stale",
              "true",
            );
            await expect(
              dialog.getByRole("button", { name: "Apply policy" }),
            ).toBeDisabled();
            await expect(dialog.locator(".form-error")).toContainText(
              "The ADMX catalogue changed",
            );
          }
          await markWarning(page, focus);
          pending.release();
          await pending.done;
          await flush(page);
          const record = {
            kind,
            scenario,
            failure,
            oldCatalogue: old.catalogue_revision,
            expectedWarning: `Error: ${warning}`,
            actualWarning: await page.locator("#error-message").textContent(),
            focus: await page.evaluate(() => ({
              id: globalThis.document.activeElement.id,
              name: globalThis.document.activeElement.getAttribute("name"),
              element: globalThis.document.activeElement.tagName,
            })),
            writesBeforeIntentionalApply: writes.length,
          };
          expect(writes).toHaveLength(0);
          const persisted = (
            await (await request.get(`/api/gpos/${target.guid}`)).json()
          ).gpo;
          expect(persisted.revision).toBe(1);
          expect(persisted.settings).toEqual([]);
          if (dialog) {
            await expect(dialog).toBeVisible();
            await expect(
              dialog.locator('[data-elem-id="DraftText"]'),
            ).toHaveValue("Keep this reviewed ADMX draft");
            await expect(dialog.locator('[name="reason"]')).toHaveValue(
              "Keep this current reason",
            );
            await expect(dialog.locator("#configure-target-gpo")).toHaveValue(
              target.guid,
            );
            await expect(focus).toBeFocused();
            record.bodyToken = await dialog
              .locator("form")
              .getAttribute("data-catalogue-revision");
            expect(record.bodyToken).toBe(revision);
            record.targetOption = await dialog
              .locator(`#configure-target-gpo option[value="${target.guid}"]`)
              .textContent();
            expect(record.targetOption).toContain("r1");
            if (scenario === "catalogue") {
              await expect(dialog.locator("form")).toHaveAttribute(
                "data-catalogue-stale",
                "true",
              );
              await expect(
                dialog.getByRole("button", { name: "Apply policy" }),
              ).toBeDisabled();
            } else
              await expect(
                dialog.getByRole("button", { name: "Apply policy" }),
              ).toBeEnabled();
          } else {
            await expect(page.locator(".admx-result")).toHaveCount(1);
            await expect(page.locator(".admx-result")).toContainText(
              "Synthetic probe toggle",
            );
          }
          if (
            kind === "detail" &&
            scenario === "newer-editor" &&
            failure === "http"
          ) {
            const response = page.waitForResponse(
              (r) =>
                r.request().method() === "POST" &&
                r.url().endsWith("/configure"),
            );
            await dialog.getByRole("button", { name: "Apply policy" }).click();
            expect((await response).status()).toBe(200);
            expect(writes).toHaveLength(1);
            expect(writes[0]).toMatchObject({
              gpo_guid: beta.guid,
              expected_revision: 1,
              expected_catalogue_revision: revision,
              values: { DraftText: "Keep this reviewed ADMX draft" },
            });
            record.intentionalApply = writes[0];
            record.savedRevision = (
              await (await request.get(`/api/gpos/${beta.guid}`)).json()
            ).gpo.revision;
            expect(record.savedRevision).toBe(2);
            expect(
              (await (await request.get(`/api/gpos/${alpha.guid}`)).json()).gpo
                .revision,
            ).toBe(1);
          }
          if (!dialog) await expect(focus).toBeFocused();
          console.log("ADMX_READ_PROBE", JSON.stringify(record));
          expect(record.actualWarning).toBe(record.expectedWarning);
        } finally {
          pending.release();
          if (activated) revision = await activate(request, "A");
        }
      });
    }
for (const kind of ["search", "detail"])
  for (const failure of ["http", "network"]) {
    test(`active ${kind}/${failure} remains visible`, async ({
      page,
      request,
    }) => {
      await start(page, request);
      const pending = await hold(page, kind, failure);
      try {
        await trigger(page, kind);
        await pending.arrived;
        pending.release();
        await pending.done;
        await expect(page.locator("#error-summary")).toContainText(
          failure === "http"
            ? `Synthetic retired ADMX ${kind} failure`
            : "The local GPO Studio server is unavailable",
        );
      } finally {
        pending.release();
      }
    });
  }

async function holdCatalogueRead(page, kind, failure) {
  const arrived = deferred(),
    release = deferred(),
    done = deferred();
  let captured = false,
    calls = 0;
  await page.route(
    kind === "health" ? "**/api/health" : "**/api/admx/categories",
    async (route) => {
      calls++;
      if (captured) return route.continue();
      captured = true;
      const response = await route.fetch();
      expect(response.status()).toBe(200);
      arrived.resolve(await response.json());
      await release.promise;
      if (failure === "success") await route.fulfill({ response });
      else if (failure === "network") await route.abort("failed");
      else
        await route.fulfill({
          status: 503,
          json: { error: { message: `Synthetic ADMX ${kind} unavailable` } },
        });
      done.resolve();
    },
  );
  return {
    arrived: arrived.promise,
    done: done.promise,
    release: () => release.resolve(),
    calls: () => calls,
  };
}
async function newerCreation(page) {
  await page.locator("#new-gpo").click();
  const dialog = page.locator("#gpo-dialog"),
    name = `Synthetic newer catalogue reader ${randomUUID()}`;
  await dialog.getByLabel("Name", { exact: true }).fill(name);
  await dialog
    .getByLabel("Description", { exact: true })
    .fill("Keep the new creation fields");
  await markWarning(page, dialog.getByLabel("Description", { exact: true }));
  return { dialog, name };
}
async function keptCreation(page, { dialog, name }) {
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Name", { exact: true })).toHaveValue(name);
  await expect(dialog.getByLabel("Description", { exact: true })).toHaveValue(
    "Keep the new creation fields",
  );
  await expect(dialog.getByLabel("Description", { exact: true })).toBeFocused();
  await expect(page.locator("#error-message")).toHaveText(`Error: ${warning}`);
}
for (const kind of ["health", "categories"])
  for (const failure of ["http", "network"]) {
    test(`retired ${kind}/${failure} uses the same editor error lease`, async ({
      page,
      request,
    }) => {
      await start(page, request, { openAdmx: false });
      const pending = await holdCatalogueRead(page, kind, failure);
      try {
        await page.getByRole("tab", { name: "ADMX", exact: true }).click();
        expect((await pending.arrived).catalogue_revision).toBe(revision);
        const draft = await newerCreation(page);
        pending.release();
        await pending.done;
        await flush(page);
        await keptCreation(page, draft);
      } finally {
        pending.release();
      }
    });
  }
for (const [kind, failure] of [
  ["health", "http"],
  ["categories", "network"],
]) {
  test(`active ${kind}/${failure} remains visible without depending on a query outcome`, async ({
    page,
    request,
  }) => {
    await start(page, request, { openAdmx: false });
    const pending = await holdCatalogueRead(page, kind, failure);
    try {
      await page.getByRole("tab", { name: "ADMX", exact: true }).click();
      await pending.arrived;
      if (kind === "categories")
        await expect(page.locator(".admx-result")).toHaveCount(2);
      pending.release();
      await pending.done;
      await expect(page.locator("#error-summary")).toContainText(
        failure === "http"
          ? `Synthetic ADMX ${kind} unavailable`
          : "The local GPO Studio server is unavailable",
      );
    } finally {
      pending.release();
    }
  });
}

test("a successful retired health read remains a useful cache without taking over the newer editor", async ({
  page,
  request,
}) => {
  const { alpha } = await start(page, request, { openAdmx: false });
  const pending = await holdCatalogueRead(page, "health", "success");
  try {
    await page.getByRole("tab", { name: "ADMX", exact: true }).click();
    expect((await pending.arrived).catalogue_revision).toBe(revision);
    const draft = await newerCreation(page);
    pending.release();
    await pending.done;
    await flush(page);
    await keptCreation(page, draft);
    await draft.dialog
      .getByRole("button", { name: "Cancel", exact: true })
      .click();
    await page.getByRole("tab", { name: "ADMX", exact: true }).click();
    await expect(page.locator("#admx-content")).toBeVisible();
    await expect(page.locator(".admx-result")).toHaveCount(2);
    expect(pending.calls()).toBe(1);
    const configureDialog = await configure(page, alpha);
    await expect(configureDialog.locator("form")).toHaveAttribute(
      "data-catalogue-revision",
      revision,
    );
    await expect(
      configureDialog.getByRole("button", { name: "Apply policy" }),
    ).toBeEnabled();
  } finally {
    pending.release();
  }
});

test("leaving ADMX retires a detail failure even without another warning", async ({
  page,
  request,
}) => {
  const { alpha } = await start(page, request);
  const pending = await hold(page, "detail", "http");
  try {
    await trigger(page, "detail");
    await pending.arrived;
    const overview = page.getByRole("tab", { name: "Overview", exact: true });
    await overview.click();
    await expect(overview).toBeFocused();
    pending.release();
    await pending.done;
    await flush(page);
    await expect(page.locator("#error-summary")).toBeHidden();
    await expect(overview).toBeFocused();
    await expect(page.locator("#title")).toHaveText(alpha.name);
  } finally {
    pending.release();
  }
});

test("a newer query retires an old search error without relying on another warning", async ({
  page,
  request,
}) => {
  await start(page, request);
  const pending = await hold(page, "search", "http");
  try {
    await trigger(page, "search");
    await pending.arrived;
    const response = page.waitForResponse(
      (result) =>
        new URL(result.url()).pathname === "/api/admx/search" &&
        new URL(result.url()).searchParams.get("q") === "toggle",
    );
    await page.locator("#admx-search").fill("toggle");
    await response;
    await expect(page.locator(".admx-result")).toHaveCount(1);
    await expect(page.locator("#admx-search")).toBeFocused();
    pending.release();
    await pending.done;
    await flush(page);
    await expect(page.locator("#error-summary")).toBeHidden();
    await expect(page.locator("#admx-search")).toBeFocused();
    await expect(page.locator(".admx-result")).toContainText(
      "Synthetic probe toggle",
    );
  } finally {
    pending.release();
  }
});

for (const kind of ["search", "detail"])
  for (const outcome of ["http", "network", "success"]) {
    test(`new input retires ${kind}/${outcome} before debounce dispatch ${kind === "search" && outcome === "http" ? "@smoke" : ""}`, async ({
      page,
      request,
    }) => {
      await page.clock.install({ time: new Date("2030-01-01T00:00:00Z") });
      const { alpha } = await start(page, request);
      const search = page.locator("#admx-search");
      // This warning predates the old lookup, so error-generation ownership alone
      // cannot mask a missing request-intent lease.
      await markWarning(page, search);
      const pending = await hold(page, kind, outcome);
      let newQueries = 0,
        writes = 0;
      page.on("request", (req) => {
        const url = new URL(req.url());
        if (
          url.pathname === "/api/admx/search" &&
          url.searchParams.get("q") === "toggle"
        )
          newQueries++;
        if (req.method() === "POST" && url.pathname.endsWith("/configure"))
          writes++;
      });
      try {
        await trigger(page, kind);
        await pending.arrived;
        await page.clock.pauseAt(new Date("2030-01-01T01:00:00Z"));
        const before = await page.locator(".admx-result").allTextContents();
        await search.fill("toggle");
        expect(newQueries).toBe(0);
        pending.release();
        await pending.done;
        // Deliver browser callbacks while keeping the250ms debounce outstanding.
        await page.clock.runFor(20);
        const observed = {
          kind,
          outcome,
          newQueries,
          elapsed: await page.evaluate(
            () => Date.now() - Date.parse("2030-01-01T01:00:00Z"),
          ),
          message: await page.locator("#error-message").textContent(),
          focused: await page.evaluate(
            () => globalThis.document.activeElement.id,
          ),
          results: await page.locator(".admx-result").allTextContents(),
          detailHidden: await page
            .locator("#admx-detail")
            .evaluate((element) => element.hidden),
        };
        console.log("ADMX_INPUT_INTENT", JSON.stringify(observed));
        expect(observed.elapsed).toBe(20);
        expect(newQueries).toBe(0);
        expect(writes).toBe(0);
        await expect(page.locator("#error-message")).toHaveText(
          `Error: ${warning}`,
        );
        await expect(search).toBeFocused();
        expect(observed.results).toEqual(before);
        expect(observed.detailHidden).toBe(true);
        const response = page.waitForResponse(
          (result) =>
            new URL(result.url()).pathname === "/api/admx/search" &&
            new URL(result.url()).searchParams.get("q") === "toggle",
        );
        await page.clock.runFor(250);
        await response;
        await expect(page.locator(".admx-result")).toHaveCount(1);
        await expect(page.locator(".admx-result")).toContainText(
          "Synthetic probe toggle",
        );
        expect(newQueries).toBe(1);
        expect(writes).toBe(0);
        expect(
          (await (await request.get(`/api/gpos/${alpha.guid}`)).json()).gpo
            .revision,
        ).toBe(1);
      } finally {
        pending.release();
      }
    });
  }
