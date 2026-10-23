import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

let policyId;
let previousSource;

test.beforeAll(async ({ request }) => {
  previousSource = (
    await (await request.get("/api/templates")).json()
  ).items.find((source) => source.name === "default");
  const source = await request.post("/api/templates/ingest", {
    data: {
      name: "default",
      kind: "curated",
      path: fileURLToPath(new URL("../fixtures/admx", import.meta.url)),
    },
  });
  expect(source.status()).toBe(201);
  const search = await request.get("/api/admx/search?q=SyntheticAuditPolicy");
  policyId = (await search.json()).items[0].qualified_id;
});

test.afterAll(async ({ request }) => {
  if (previousSource) {
    const restored = await request.post("/api/templates/ingest", {
      data: {
        name: "default",
        kind: previousSource.kind,
        path: previousSource.path,
      },
    });
    expect(restored.status()).toBe(201);
  }
});

async function seedPolicy(request, suffix, sides = ["computer", "user"]) {
  const name = `Synthetic selection ${suffix} ${Date.now()}`;
  const created = await request.post("/api/gpos", {
    data: {
      name,
      actor: "browser-test",
      reason: "Seed bulk selection regression",
    },
  });
  expect(created.status()).toBe(201);
  let payload = await created.json();
  for (const side of sides) {
    const configured = await request.post(
      `/api/admx/policies/${encodeURIComponent(policyId)}/configure`,
      {
        data: {
          gpo_guid: payload.gpo.guid,
          expected_revision: payload.gpo.revision,
          actor: "browser-test",
          reason: "Configure synthetic Both-class policy",
          side,
          values: { AuditEnabled: true },
        },
      },
    );
    expect(configured.status()).toBe(200);
    payload = await configured.json();
  }
  return { name, guid: payload.gpo.guid };
}

async function openBrowser(page, policy) {
  await page.goto("/");
  await page.locator(".gpo-item", { hasText: policy.name }).click();
  await page.getByRole("tab", { name: "Settings browser" }).click();
  await expect(page.locator(".browser-bulk-checkbox").first()).toBeVisible();
}

function selection(page, side) {
  return page.locator(`.browser-bulk-checkbox[data-bulk-side="${side}"]`);
}

test("clears selections and their reason when switching GPOs, retaining filters @smoke", async ({
  page,
  request,
}) => {
  const first = await seedPolicy(request, "first");
  const second = await seedPolicy(request, "second");
  await openBrowser(page, first);
  await page.locator("#browser-search").fill("Audit");
  await selection(page, "computer").check();
  await page
    .locator("#browser-bulk-reason")
    .fill("Applies to the first GPO only");
  await page.locator(".gpo-item", { hasText: second.name }).click();

  await expect(page.locator(".browser-bulk-checkbox")).toHaveCount(2);
  await expect(page.locator(".browser-bulk-checkbox:checked")).toHaveCount(0);
  await expect(page.locator("#browser-bulk-bar")).toBeHidden();
  await expect(page.locator("#browser-bulk-reason")).toHaveValue("");
  await expect(page.locator("#browser-search")).toHaveValue("Audit");
});

test("selects Both-class sides independently and applies hidden filter selections", async ({
  page,
  request,
}) => {
  const policy = await seedPolicy(request, "sides");
  await openBrowser(page, policy);
  await selection(page, "computer").check();
  await expect(page.locator("#browser-bulk-select-all")).toHaveJSProperty(
    "indeterminate",
    true,
  );
  await page.locator('#panel-browser .chip[data-side="user"]').click();
  await expect(page.locator(".browser-bulk-checkbox")).toHaveCount(1);
  await expect(selection(page, "user")).not.toBeChecked();
  await selection(page, "user").check();
  await expect(page.locator("#browser-bulk-count")).toHaveText("2 selected");

  await page
    .locator("#browser-bulk-reason")
    .fill("Remove both synthetic sides");
  await page.locator('[data-bulk-state="not_configured"]').click();
  await expect(page.locator("#browser-bulk-bar")).toBeHidden();
  const response = await request.get(`/api/gpos/${policy.guid}`);
  expect((await response.json()).gpo.settings).toEqual([]);
});

test("changing one Both-class side leaves the other configured", async ({
  page,
  request,
}) => {
  const policy = await seedPolicy(request, "one side");
  await openBrowser(page, policy);
  await selection(page, "computer").check();
  await page.locator('#panel-browser .chip[data-side="user"]').click();
  await expect(selection(page, "user")).not.toBeChecked();
  await page
    .locator("#browser-bulk-reason")
    .fill("Remove the computer side only");
  await page.locator('[data-bulk-state="not_configured"]').click();
  await expect(page.locator("#browser-bulk-bar")).toBeHidden();

  const response = await request.get(`/api/gpos/${policy.guid}`);
  const settings = (await response.json()).gpo.settings;
  expect(settings).toHaveLength(1);
  expect(settings[0].side).toBe("user");
});

test("reports a partial bulk failure and leaves only the failed side selected", async ({
  page,
  request,
}) => {
  const policy = await seedPolicy(request, "partial failure");
  await openBrowser(page, policy);
  await page.locator("#browser-bulk-select-all").check();
  await page
    .locator("#browser-bulk-reason")
    .fill("Exercise a partial bulk failure");
  await page.route(
    `**/api/gpos/${policy.guid}/bulk-policy-state`,
    async (route) => {
      if (route.request().postDataJSON().side === "user") {
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            error: { message: "Synthetic concurrent update" },
          }),
        });
      } else {
        await route.continue();
      }
    },
  );
  await page.locator('[data-bulk-state="not_configured"]').click();
  await expect(page.locator("#error-summary")).toContainText(
    "1 policy changed before the remaining bulk change failed",
  );
  await expect(page.locator("#browser-bulk-count")).toHaveText("1 selected");
  await expect(selection(page, "user")).toBeChecked();
  await expect(selection(page, "computer")).toHaveCount(0);
  const response = await request.get(`/api/gpos/${policy.guid}`);
  const settings = (await response.json()).gpo.settings;
  expect(settings).toHaveLength(1);
  expect(settings[0].side).toBe("user");
});

test("ignores configured settings returned after switching to another GPO", async ({
  page,
  request,
}) => {
  const first = await seedPolicy(request, "slow first", ["computer"]);
  const second = await seedPolicy(request, "current second", ["user"]);
  await openBrowser(page, first);
  let release;
  const held = new Promise((resolve) => {
    release = resolve;
  });
  await page.route(
    `**/api/gpos/${first.guid}/configured-settings?*`,
    async (route) => {
      const response = await route.fetch();
      await held;
      await route.fulfill({ response });
    },
  );
  const started = page.waitForRequest((req) =>
    req.url().includes(`${first.guid}/configured-settings`),
  );
  await page.locator("#browser-search").fill("Audit");
  await started;
  await page.locator(".gpo-item", { hasText: second.name }).click();
  await expect(selection(page, "user")).toBeVisible();
  await selection(page, "user").check();
  const finished = page.waitForResponse((response) =>
    response.url().includes(`${first.guid}/configured-settings`),
  );
  release();
  await finished;
  await expect(selection(page, "computer")).toHaveCount(0);
  await expect(selection(page, "user")).toBeChecked();
});

test("does not apply an old GPO mutation response to the current GPO", async ({
  page,
  request,
}) => {
  const first = await seedPolicy(request, "slow mutation", ["computer"]);
  const second = await seedPolicy(request, "current mutation", ["user"]);
  await openBrowser(page, first);
  let release;
  const held = new Promise((resolve) => {
    release = resolve;
  });
  await page.route(
    `**/api/gpos/${first.guid}/bulk-policy-state`,
    async (route) => {
      const response = await route.fetch();
      await held;
      await route.fulfill({ response });
    },
  );
  await selection(page, "computer").check();
  await page.locator("#browser-bulk-reason").fill("Remove first GPO setting");
  const started = page.waitForRequest((req) =>
    req.url().endsWith(`${first.guid}/bulk-policy-state`),
  );
  await page.locator('[data-bulk-state="not_configured"]').click();
  await started;
  await page.locator(".gpo-item", { hasText: second.name }).click();
  await expect(selection(page, "user")).toBeVisible();
  const finished = page.waitForResponse((response) =>
    response.url().endsWith(`${first.guid}/bulk-policy-state`),
  );
  release();
  await finished;
  await expect(page.locator("#browser-bulk-select-all")).toBeEnabled();
  const currentGuid = await page.evaluate(async () => {
    const { state } = await import("/assets/js/state.mjs");
    return state.current.guid;
  });
  expect(currentGuid).toBe(second.guid);
  await expect(selection(page, "user")).not.toBeChecked();
  await expect(page.locator("#browser-bulk-bar")).toBeHidden();
});

test("bulk change refreshes the current revision before ADMX configuration", async ({
  page,
  request,
}) => {
  const policy = await seedPolicy(request, "bulk configure", ["computer"]);
  await openBrowser(page, policy);
  await selection(page, "computer").check();
  await page
    .locator("#browser-bulk-reason")
    .fill("Disable before reconfiguring");
  await page.locator('[data-bulk-state="disabled"]').click();
  await expect(page.locator("#browser-bulk-bar")).toBeHidden();
  const current = (await (await request.get(`/api/gpos/${policy.guid}`)).json())
    .gpo;
  await expect(page.locator("#revision")).toHaveText(
    `Revision ${current.revision}`,
  );
  await expect(
    page.locator(".gpo-item", { hasText: policy.name }),
  ).toContainText(`r${current.revision}`);
  await page.getByRole("tab", { name: "ADMX", exact: true }).click();
  const searched = page.waitForResponse((response) =>
    response.url().includes("/api/admx/search?q=SyntheticAuditPolicy"),
  );
  await page.locator("#admx-search").fill("SyntheticAuditPolicy");
  await searched;
  await expect(page.locator(".admx-result")).toHaveCount(1);
  await page
    .locator(".admx-result", { hasText: "Synthetic Audit Policy" })
    .click();
  await page.locator("#admx-configure-btn").click();
  const dialog = page.locator("#configure-dialog");
  await expect(dialog.locator("#configure-title")).toHaveText(
    "Configure: Synthetic Audit Policy",
  );
  await expect(dialog.locator("#configure-target-gpo")).toHaveValue(
    policy.guid,
  );
  const configure = page.waitForRequest(
    (request) =>
      request.method() === "POST" && request.url().endsWith("/configure"),
  );
  await dialog.getByRole("button", { name: "Apply policy" }).click();
  expect((await configure).postDataJSON().expected_revision).toBe(
    current.revision,
  );
  await expect(dialog).toBeHidden();
  const updated = (await (await request.get(`/api/gpos/${policy.guid}`)).json())
    .gpo;
  expect(updated.revision).toBe(current.revision + 1);
});
