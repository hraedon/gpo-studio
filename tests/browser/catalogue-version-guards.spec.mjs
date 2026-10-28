import { test, expect } from "@playwright/test";
import { mkdirSync } from "node:fs";

async function seed(request) {
  const response = await request.post("/api/gpos", {
    data: {
      name: `Synthetic version guard ${Date.now()}`,
      actor: "browser-test",
      reason: "Synthetic catalogue guard",
    },
  });
  expect(response.status()).toBe(201);
  return (await response.json()).gpo;
}
async function select(page, gpo) {
  await page.goto("/");
  await page.locator(`[data-guid="${gpo.guid}"]`).click();
  await expect(page.locator("#title")).toHaveText(gpo.name);
}
async function configured(request) {
  let gpo = await seed(request);
  const policy = (
    await (await request.get("/api/admx/search?q=SyntheticToggle")).json()
  ).items[0];
  const response = await request.post(
    `/api/admx/policies/${encodeURIComponent(policy.qualified_id)}/configure`,
    {
      data: {
        gpo_guid: gpo.guid,
        side: "computer",
        state: "enabled",
        values: {},
        actor: "browser-test",
        reason: "Seed catalogue guard",
        expected_revision: gpo.revision,
      },
    },
  );
  expect(response.status()).toBe(200);
  gpo = (await response.json()).gpo;
  return gpo;
}

for (const missing of ["catalogue_revision", "gpo_revision"]) {
  test(`bulk fails closed when configured settings omit ${missing}`, async ({
    page,
    request,
  }) => {
    const gpo = await configured(request);
    await page.route(
      `**/api/gpos/${gpo.guid}/configured-settings?*`,
      async (route) => {
        const response = await route.fetch(),
          body = await response.json();
        delete body[missing];
        await route.fulfill({ response, json: body });
      },
    );
    await select(page, gpo);
    await page.getByRole("tab", { name: /^Settings browser/ }).click();
    await expect(page.locator("#error-summary")).toContainText(
      missing === "catalogue_revision"
        ? "did not identify"
        : "snapshot changed",
    );
    await expect(page.locator(".browser-bulk-checkbox")).toHaveCount(0);
    await expect(page.locator("#browser-bulk-select-all")).toBeDisabled();
    expect(
      (await (await request.get(`/api/gpos/${gpo.guid}`)).json()).gpo.revision,
    ).toBe(gpo.revision);
  });
}

test("a policy detail without a catalogue version cannot open an authoring form", async ({
  page,
  request,
}) => {
  const gpo = await seed(request);
  await select(page, gpo);
  await page.getByRole("tab", { name: "ADMX", exact: true }).click();
  await page.route("**/api/admx/policies/*", async (route) => {
    const response = await route.fetch(),
      body = await response.json();
    delete body.catalogue_revision;
    await route.fulfill({ response, json: body });
  });
  await page.locator(".admx-result").first().click();
  await expect(page.locator("#error-summary")).toContainText(
    "did not identify",
  );
  await expect(page.locator("#admx-configure-btn")).toHaveCount(0);
  await expect(page.locator("#configure-dialog")).toBeHidden();
});

test("configured settings cannot borrow a newer GPO revision than the displayed policy", async ({
  page,
  request,
}) => {
  const gpo = await configured(request);
  let advanced = false;
  await page.route(
    `**/api/gpos/${gpo.guid}/configured-settings?*`,
    async (route) => {
      if (!advanced) {
        advanced = true;
        const response = await request.patch(`/api/gpos/${gpo.guid}`, {
          data: {
            name: gpo.name,
            description: "Synthetic unseen metadata",
            computer_enabled: true,
            user_enabled: true,
            status: "draft",
            expected_revision: gpo.revision,
            actor: "other-browser",
            reason: "Synthetic unseen revision",
          },
        });
        expect(response.status()).toBe(200);
      }
      await route.fulfill({ response: await route.fetch() });
    },
  );
  await select(page, gpo);
  await page.getByRole("tab", { name: /^Settings browser/ }).click();
  await expect(page.locator("#error-summary")).toContainText(
    "policy snapshot changed",
  );
  await expect(page.locator(".browser-bulk-checkbox")).toHaveCount(0);
  await expect(page.locator("#revision")).toHaveText(
    `Revision ${gpo.revision}`,
  );
  await page.locator(`[data-guid="${gpo.guid}"]`).click();
  await expect(page.locator("#revision")).toHaveText(
    `Revision ${gpo.revision + 1}`,
  );
  await expect(page.locator(".browser-bulk-checkbox")).toHaveCount(1);
  await expect(page.locator(".browser-bulk-checkbox")).toBeEnabled();
  await expect(page.locator(".browser-bulk-checkbox:checked")).toHaveCount(0);
});

test("an observed replacement invalidates categories and cannot be undone by an older delayed tree", async ({
  page,
  request,
}, testInfo) => {
  const original = (
    await (await request.get("/api/templates")).json()
  ).items.find((source) => source.name === "default");
  const gpo = await seed(request);
  let release, arrived, finished;
  const waiting = new Promise((resolve) => {
    release = resolve;
  });
  const seen = new Promise((resolve) => {
    arrived = resolve;
  });
  const done = new Promise((resolve) => {
    finished = resolve;
  });
  await page.route("**/api/admx/categories/tree", async (route) => {
    const response = await route.fetch();
    arrived();
    await waiting;
    await route.fulfill({ response });
    finished();
  });
  try {
    await select(page, gpo);
    await page.getByRole("tab", { name: /^Settings browser/ }).click();
    await seen;
    const empty = testInfo.outputPath("empty-catalogue");
    mkdirSync(empty, { recursive: true });
    const loaded = await request.post("/api/templates/ingest", {
      data: { name: "default", kind: "local", path: empty },
    });
    expect(loaded.status()).toBe(201);
    const refreshed = page.waitForResponse((response) =>
      response.url().endsWith("/api/health"),
    );
    await page.evaluate(() => globalThis.dispatchEvent(new Event("focus")));
    await refreshed;
    await expect(page.locator("#browser-content")).toBeHidden();
    release();
    await done;
    await page.evaluate(
      () => new Promise((resolve) => globalThis.requestAnimationFrame(resolve)),
    );
    await expect(page.locator("#browser-category-filter option")).toHaveCount(
      1,
    );
    await expect(page.locator(".browser-bulk-checkbox")).toHaveCount(0);
  } finally {
    release();
    await request.post("/api/templates/ingest", {
      data: { name: "default", kind: "local", path: original.path },
    });
  }
});
