import { expect, test } from "@playwright/test";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

test("keeps a side-edited structured child visible as unresolved @smoke", async ({
  page,
  request,
}) => {
  const original = (
    await (await request.get("/api/templates")).json()
  ).items.find((source) => source.name === "default");
  const directory = mkdtempSync(join(dirname(original.path), "location-"));
  writeFileSync(
    join(directory, "location.admx"),
    `<policyDefinitions><policyNamespaces><target prefix="synthetic" namespace="Synthetic.Location"/></policyNamespaces>
    <policies><policy name="Both" class="Both" displayName="Synthetic location policy"
    key="Software\\Policies\\Synthetic\\Location" valueName="State">
    <enabledValue><decimal value="1"/></enabledValue><disabledValue><decimal value="0"/></disabledValue>
    <elements><text id="state" valueName="Child"/></elements></policy></policies></policyDefinitions>`,
  );
  writeFileSync(
    join(directory, "location.adml"),
    "<policyDefinitionResources><resources><stringTable/></resources></policyDefinitionResources>",
  );
  try {
    const loaded = await request.post("/api/templates/ingest", {
      data: { name: "default", kind: "local", path: directory },
    });
    expect(loaded.status()).toBe(201);
    const name = `Synthetic side edit ${Date.now()}`;
    const created = await request.post("/api/gpos", {
      data: {
        name,
        actor: "browser-test",
        reason: "Seed structured location check",
      },
    });
    expect(created.status()).toBe(201);
    let { gpo } = await created.json();
    const configured = await request.post(
      "/api/admx/policies/Synthetic.Location%3ABoth/configure",
      {
        data: {
          gpo_guid: gpo.guid,
          expected_revision: gpo.revision,
          side: "computer",
          values: { state: "retained child value" },
          actor: "browser-test",
          reason: "Configure a structured child",
        },
      },
    );
    expect(configured.status(), await configured.text()).toBe(200);
    ({ gpo } = await configured.json());
    const child = gpo.settings.find((row) => row.value_name === "Child");
    expect(child.id).toMatch(/^admx2:/);
    const updated = await request.put(
      `/api/gpos/${gpo.guid}/settings/${encodeURIComponent(child.id)}`,
      {
        data: {
          expected_revision: gpo.revision,
          setting: { ...child, side: "user", hive: "HKCU" },
          actor: "browser-test",
          reason: "Move the raw registry row to the user side",
        },
      },
    );
    expect(updated.status()).toBe(200);
    await page.goto("/");
    await page.locator(".gpo-item", { hasText: name }).click();
    await page.getByRole("tab", { name: "Settings browser" }).click();
    await expect(page.locator("#browser-unresolved-count")).toHaveText("1");
    const row = page.locator("#browser-unresolved-table tr");
    await expect(row).toHaveCount(1);
    await expect(row).toContainText("retained child value");
    await expect(row).toContainText("HKCU");
    await expect(row).toContainText(
      "structured policy identity does not match registry location",
    );
    await page.locator('#panel-browser .chip[data-side="user"]').click();
    await expect(row).toHaveCount(1);
    await expect(row).toContainText("retained child value");
    await page.reload();
    await page.locator(".gpo-item", { hasText: name }).click();
    await page.getByRole("tab", { name: "Settings browser" }).click();
    await expect(page.locator("#browser-unresolved-count")).toHaveText("1");
  } finally {
    const restored = await request.post("/api/templates/ingest", {
      data: { name: "default", kind: original.kind, path: original.path },
    });
    try {
      expect(restored.status()).toBe(201);
    } finally {
      rmSync(directory, { recursive: true });
    }
  }
});
