import { expect, test } from "@playwright/test";
import { mkdtempSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

// Issue #142: configure refuses an enum with no readable items
// (unsupported_element_kind), so the editor must not offer a free-text box
// whose entry would have no registry type.

function fixturePack(originalPath) {
  // The managed server cleans this synthetic sibling of its default catalogue.
  const directory = mkdtempSync(
    join(dirname(originalPath), "source-enum-unsupported-"),
  );
  const key = "Software\\Policies\\Synthetic\\EnumUnsupported";
  writeFileSync(
    join(directory, "source.admx"),
    `<?xml version="1.0"?><policyDefinitions xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions" revision="1.0" schemaVersion="1.0"><policyNamespaces><target prefix="synthetic" namespace="Synthetic.EnumUnsupported"/></policyNamespaces><policies><policy name="Itemless" class="Machine" key="${key}" valueName="State" displayName="Synthetic itemless enum"><enabledValue><decimal value="1"/></enabledValue><disabledValue><decimal value="0"/></disabledValue><elements><enum id="Mode" valueName="Mode"/></elements></policy></policies></policyDefinitions>`,
  );
  writeFileSync(
    join(directory, "source.adml"),
    '<?xml version="1.0"?><policyDefinitionResources xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions" revision="1.0" schemaVersion="1.0"><resources><stringTable/></resources></policyDefinitionResources>',
  );
  return directory;
}

test("an enum with no readable items renders as unsupported, not free text", async ({
  page,
  request,
}, testInfo) => {
  const original = (
    await (await request.get("/api/templates")).json()
  ).items.find((source) => source.name === "default");
  try {
    expect(
      (
        await request.post("/api/templates/ingest", {
          data: {
            name: "default",
            kind: "local",
            path: fixturePack(original.path),
          },
        })
      ).status(),
    ).toBe(201);
    const detail = await (
      await request.get("/api/admx/policies/Synthetic.EnumUnsupported:Itemless")
    ).json();
    expect(detail.elements).toMatchObject([
      { id: "Mode", kind: "enum", enum_items: [] },
    ]);
    const { gpo } = await (
      await request.post("/api/gpos", {
        data: {
          name: `Synthetic Itemless Enum ${testInfo.project.name} ${Date.now()}`,
          actor: "browser-test",
          reason: "Synthetic itemless enum fixture",
        },
      })
    ).json();
    await page.goto("/");
    await page.locator(`[data-guid="${gpo.guid}"]`).click();
    await page.getByRole("tab", { name: "ADMX", exact: true }).click();
    await page
      .locator(".admx-result", { hasText: "Synthetic itemless enum" })
      .click();
    await page.locator("#admx-configure-btn").click();
    const dialog = page.locator("#configure-dialog");
    await expect(dialog).toBeVisible();
    const options = dialog.locator("#configure-options");
    await expect(options).toContainText("Unsupported enum");
    await expect(options.locator('[data-elem-id="Mode"]')).toHaveCount(0);
    await expect(options.locator("input, select, textarea")).toHaveCount(0);

    // Enabling still reaches the server, which names the unsupported element.
    await dialog.locator('[name="reason"]').fill("Synthetic itemless enum");
    const response = page.waitForResponse(
      (result) =>
        result.url().endsWith("/configure") &&
        result.request().method() === "POST",
    );
    await dialog
      .getByRole("button", { name: "Apply policy", exact: true })
      .click();
    const result = await response;
    expect(result.status()).toBe(422);
    expect(JSON.stringify(await result.json())).toContain(
      "unsupported_element_kind",
    );
    const persisted = (
      await (await request.get(`/api/gpos/${gpo.guid}`)).json()
    ).gpo;
    expect(persisted.revision).toBe(1);
    expect(persisted.settings).toEqual([]);
  } finally {
    expect(
      (
        await request.post("/api/templates/ingest", {
          data: { name: "default", kind: "local", path: original.path },
        })
      ).status(),
    ).toBe(201);
  }
});
