import { expect, test } from "@playwright/test";
import { mkdtempSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

// Issue #140: parseInt sent "1e3" as 1 (and "1.5" as 1). A decimal field must
// carry whole decimal digits or nothing.

function fixturePack(originalPath) {
  // The managed server cleans this synthetic sibling of its default catalogue.
  const directory = mkdtempSync(
    join(dirname(originalPath), "source-decimal-input-"),
  );
  const key = "Software\\Policies\\Synthetic\\DecimalInput";
  writeFileSync(
    join(directory, "source.admx"),
    `<?xml version="1.0"?><policyDefinitions xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions" revision="1.0" schemaVersion="1.0"><policyNamespaces><target prefix="synthetic" namespace="Synthetic.DecimalInput"/></policyNamespaces><policies><policy name="Count" class="Machine" key="${key}" valueName="State" displayName="Synthetic decimal input"><enabledValue><decimal value="1"/></enabledValue><disabledValue><decimal value="0"/></disabledValue><elements><decimal id="Limit" valueName="Limit"/></elements></policy></policies></policyDefinitions>`,
  );
  writeFileSync(
    join(directory, "source.adml"),
    '<?xml version="1.0"?><policyDefinitionResources xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions" revision="1.0" schemaVersion="1.0"><resources><stringTable/></resources></policyDefinitionResources>',
  );
  return directory;
}

test("decimal fields refuse fractions and exponents instead of truncating them", async ({
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
    const { gpo } = await (
      await request.post("/api/gpos", {
        data: {
          name: `Synthetic Decimal Input ${testInfo.project.name} ${Date.now()}`,
          actor: "browser-test",
          reason: "Synthetic decimal input fixture",
        },
      })
    ).json();
    let configures = 0;
    page.on("request", (sent) => {
      if (sent.method() === "POST" && sent.url().endsWith("/configure"))
        configures++;
    });
    await page.goto("/");
    await page.locator(`[data-guid="${gpo.guid}"]`).click();
    await page.getByRole("tab", { name: "ADMX", exact: true }).click();
    await page
      .locator(".admx-result", { hasText: "Synthetic decimal input" })
      .click();
    await page.locator("#admx-configure-btn").click();
    const dialog = page.locator("#configure-dialog");
    await dialog.locator('[name="reason"]').fill("Synthetic decimal input");
    const field = dialog.locator('[data-elem-id="Limit"]');
    // The number input's step="1" already blocks "1.5"; "1e3" and "-1" pass
    // the browser's own checks and reached parseInt.
    for (const typed of ["1.5", "1e3", "-1"]) {
      await field.fill(typed);
      await page.locator("#toast").evaluate((toast) => {
        toast.textContent = "";
      });
      await dialog
        .getByRole("button", { name: "Apply policy", exact: true })
        .click();
      if (typed !== "1.5")
        await expect(page.locator("#toast")).toContainText(
          "Invalid number for Limit",
        );
      await expect(dialog).toBeVisible();
    }
    expect(configures).toBe(0);
    await field.fill("12");
    const response = page.waitForResponse(
      (result) =>
        result.url().endsWith("/configure") &&
        result.request().method() === "POST",
    );
    await dialog
      .getByRole("button", { name: "Apply policy", exact: true })
      .click();
    const result = await response;
    expect(result.request().postDataJSON().values).toEqual({ Limit: 12 });
    expect(result.status()).toBe(200);
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
