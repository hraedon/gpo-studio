import { test, expect } from "@playwright/test";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Use real, different catalogues with the same qualified ID. A new folder must
// never reinterpret a policy that another tab has already reviewed.
const policyId = "Synthetic.CatalogueContext:SamePolicy";
const policyUrl = `/api/admx/policies/${encodeURIComponent(policyId)}`;
let directory, original;

function writePack(variant) {
  const folder = join(directory, variant);
  mkdirSync(folder);
  writeFileSync(
    join(folder, "synthetic.admx"),
    `<policyDefinitions>
<policyNamespaces><target prefix="synthetic" namespace="Synthetic.CatalogueContext"/></policyNamespaces>
<policies><policy name="SamePolicy" class="Both"
 key="Software\\Policies\\Synthetic\\Catalogue${variant}" valueName="Value${variant}"
 displayName="$(string.Policy)" presentation="$(presentation.Policy)">
<enabledValue><decimal value="${variant === "A" ? 11 : 22}"/></enabledValue>
<disabledValue><decimal value="0"/></disabledValue>
<elements><text id="DraftText" valueName="Text${variant}"/></elements>
</policy></policies></policyDefinitions>`,
  );
  writeFileSync(
    join(folder, "synthetic.adml"),
    `<policyDefinitionResources><resources>
<stringTable><string id="Policy">Synthetic catalogue context policy</string></stringTable>
<presentationTable><presentation id="Policy"><textBox refId="DraftText"><label>Draft text</label></textBox></presentation></presentationTable>
</resources></policyDefinitionResources>`,
  );
}

async function loadFolder(page, path) {
  await page.goto("/");
  await page.locator("#open-templates").click();
  const dialog = page.locator("#templates-dialog");
  await expect(dialog.getByLabel("Folder path")).toBeEnabled();
  await dialog.getByLabel("Folder path").fill(path);
  const response = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/templates/ingest") &&
      r.request().method() === "POST",
  );
  await dialog.getByRole("button", { name: "Load folder" }).click();
  const loaded = await response;
  expect(loaded.status()).toBe(201);
  await expect(dialog.locator("#templates-load")).toBeEnabled();
  return loaded.json();
}

async function create(request, name) {
  const response = await request.post("/api/gpos", {
    data: { name: `${name} ${Date.now()}` },
  });
  expect(response.status()).toBe(201);
  return (await response.json()).gpo;
}

async function review(page, gpo) {
  await page.goto("/");
  await page.locator(".gpo-item", { hasText: gpo.name }).click();
  await page.getByRole("tab", { name: "ADMX", exact: true }).click();
  await page
    .locator(".admx-result", { hasText: "Synthetic catalogue context policy" })
    .click();
  await expect(page.locator("#admx-detail")).toContainText("CatalogueA");
  await page.locator("#admx-configure-btn").click();
  return page.locator("#configure-dialog");
}

test.beforeAll(async ({ request }) => {
  const sources = await (await request.get("/api/templates")).json();
  original = sources.items.find((source) => source.name === "default");
  expect(original).toBeTruthy();
  directory = mkdtempSync(join(tmpdir(), "gpo-studio-catalogue-context-"));
  writePack("A");
  writePack("B");
});

test.beforeEach(async ({ request }) => {
  const response = await request.post("/api/templates/ingest", {
    data: { name: "default", kind: "local", path: join(directory, "A") },
  });
  expect(response.status()).toBe(201);
});

test.afterEach(async ({ request }) => {
  const response = await request.post("/api/templates/ingest", {
    data: { name: "default", kind: "local", path: original.path },
  });
  expect(response.status()).toBe(201);
});

test.afterAll(() => rmSync(directory, { recursive: true, force: true }));

test("another tab's folder change rejects the reviewed mapping and preserves the draft", async ({
  page,
  browser,
  request,
  baseURL,
}) => {
  // Separate contexts deliberately suppress BroadcastChannel hints. The server
  // precondition must protect the save even without a browser notification.
  const second = await browser.newContext({ baseURL });
  try {
    const other = await second.newPage();
    const gpo = await create(request, "Synthetic catalogue single-tab review");
    const dialog = await review(page, gpo);
    await dialog
      .locator('[data-elem-id="DraftText"]')
      .fill("Keep my reviewed draft");
    await dialog.locator('[name="reason"]').fill("Apply the policy I reviewed");
    const reviewed = (await (await request.get(policyUrl)).json())
      .catalogue_revision;
    const active = (await loadFolder(other, join(directory, "B")))
      .catalogue_revision;
    expect(active).not.toBe(reviewed);

    const saved = page.waitForResponse(
      (r) => r.url().endsWith("/configure") && r.request().method() === "POST",
    );
    await dialog.getByRole("button", { name: "Apply policy" }).click();
    const rejected = await saved;
    expect(rejected.request().postDataJSON().expected_catalogue_revision).toBe(
      reviewed,
    );
    expect(rejected.status()).toBe(409);
    expect((await rejected.json()).error.code).toBe("catalogue_conflict");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText(/catalogue changed/i);
    await expect(dialog.locator('[data-elem-id="DraftText"]')).toHaveValue(
      "Keep my reviewed draft",
    );
    await expect(dialog.locator('[name="reason"]')).toHaveValue(
      "Apply the policy I reviewed",
    );
    await expect(
      dialog.getByRole("button", { name: "Apply policy" }),
    ).toBeDisabled();
    await expect(dialog.locator('[data-elem-id="DraftText"]')).toBeEnabled();
    await expect(page.locator("#conflict-dialog")).toBeHidden();
    let current = (await (await request.get(`/api/gpos/${gpo.guid}`)).json())
      .gpo;
    expect(current.revision).toBe(gpo.revision);
    expect(current.settings).toEqual([]);

    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.reload();
    await page.locator(".gpo-item", { hasText: gpo.name }).click();
    await page.getByRole("tab", { name: "ADMX", exact: true }).click();
    await page
      .locator(".admx-result", {
        hasText: "Synthetic catalogue context policy",
      })
      .click();
    await expect(page.locator("#admx-detail")).toContainText("CatalogueB");
    await page.locator("#admx-configure-btn").click();
    await dialog
      .locator('[data-elem-id="DraftText"]')
      .fill("Newly reviewed mapping B");
    await dialog.getByRole("button", { name: "Apply policy" }).click();
    await expect(dialog).toBeHidden();
    current = (await (await request.get(`/api/gpos/${gpo.guid}`)).json()).gpo;
    expect(current.revision).toBe(gpo.revision + 1);
    expect(
      current.settings.some(
        (s) =>
          s.key.endsWith("CatalogueB") &&
          s.value_name === "ValueB" &&
          String(s.value) === "22",
      ),
    ).toBe(true);
    expect(current.settings.every((s) => !s.key.endsWith("CatalogueA"))).toBe(
      true,
    );
  } finally {
    await second.close();
  }
});

test("a folder change between bulk batches keeps completed work and rejects remaining stale mappings", async ({
  page,
  browser,
  request,
  baseURL,
}) => {
  const second = await browser.newContext({ baseURL });
  try {
    const other = await second.newPage();
    let gpo = await create(request, "Synthetic catalogue partial bulk review");
    const reviewed = (await (await request.get(policyUrl)).json())
      .catalogue_revision;
    for (const side of ["computer", "user"]) {
      const response = await request.post(`${policyUrl}/configure`, {
        data: {
          gpo_guid: gpo.guid,
          side,
          state: "enabled",
          values: { DraftText: "Seed reviewed A" },
          actor: "synthetic-test",
          reason: "Seed reviewed settings",
          expected_revision: gpo.revision,
          expected_catalogue_revision: reviewed,
        },
      });
      expect(response.status()).toBe(200);
      gpo = (await response.json()).gpo;
    }
    await page.goto("/");
    await page.locator(".gpo-item", { hasText: gpo.name }).click();
    await page.getByRole("tab", { name: /^Settings browser/ }).click();
    await expect(page.locator(".browser-bulk-checkbox")).toHaveCount(2);
    await page.locator("#browser-bulk-select-all").check();
    await page
      .locator("#browser-bulk-reason")
      .fill("Disable reviewed settings on both sides");
    const requests = [];
    await page.route(
      `**/api/gpos/${gpo.guid}/bulk-policy-state`,
      async (route) => {
        requests.push(route.request().postDataJSON());
        const response = await route.fetch();
        if (requests.length === 1) {
          expect(response.status()).toBe(200);
          await loadFolder(other, join(directory, "B"));
        }
        await route.fulfill({ response });
      },
    );
    const rejected = page.waitForResponse(
      (r) => r.url().endsWith("/bulk-policy-state") && r.status() === 409,
    );
    await page.locator('[data-bulk-state="disabled"]').click();
    expect((await (await rejected).json()).error.code).toBe(
      "catalogue_conflict",
    );
    expect(requests).toHaveLength(2);
    expect(requests.map((body) => body.expected_catalogue_revision)).toEqual([
      reviewed,
      reviewed,
    ]);
    expect(requests.map((body) => body.expected_revision)).toEqual([
      gpo.revision,
      gpo.revision + 1,
    ]);
    await expect(page.locator("#error-summary")).toContainText(
      /1 policy changed before/i,
    );
    await expect(page.locator("#browser-bulk-bar")).toBeHidden();
    await expect(page.locator(".browser-bulk-checkbox:checked")).toHaveCount(0);
    const refreshed = page.waitForResponse((response) =>
      response.url().endsWith("/api/health"),
    );
    await page.evaluate(() => globalThis.dispatchEvent(new Event("focus")));
    await refreshed;
    await expect(page.locator("#error-summary")).toContainText(
      /1 policy changed before/i,
    );
    await page.getByRole("button", { name: "Dismiss error message" }).click();
    await expect(page.locator("#error-summary")).toBeHidden();
    await expect(page.locator("#main-content")).toBeFocused();
    const current = (await (await request.get(`/api/gpos/${gpo.guid}`)).json())
      .gpo;
    expect(current.revision).toBe(gpo.revision + 1);
    expect(current.settings.every((s) => s.key.endsWith("CatalogueA"))).toBe(
      true,
    );
    expect(
      current.settings.find(
        (s) => s.side === "computer" && s.value_name === "ValueA",
      ).value,
    ).toBe("0");
    expect(
      current.settings.find(
        (s) => s.side === "user" && s.value_name === "ValueA",
      ).value,
    ).toBe("11");
  } finally {
    await second.close();
  }
});
