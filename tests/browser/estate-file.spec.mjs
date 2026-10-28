import { expect, test } from "@playwright/test";

const validFile = {
  name: "synthetic-estate.json",
  mimeType: "application/json",
  buffer: Buffer.from(JSON.stringify({ kind: "gpo-lens-estate", gpos: [] })),
};

async function openImport(page) {
  await page.goto("/");
  await page.locator("#import-estate").click();
  return page.locator("#estate-dialog");
}

for (const replacement of ["cleared", "invalid", "oversized"]) {
  test(`does not import the previous estate when its replacement is ${replacement}`, async ({
    page,
  }) => {
    let requests = 0;
    await page.route("**/api/estate/import", async (route) => {
      requests++;
      await route.fulfill({ json: { imported: 0, skipped: 0 } });
    });
    const dialog = await openImport(page);
    const input = page.locator("#estate-file");
    await input.setInputFiles(validFile);
    await expect(page.locator("#estate-file-info")).toContainText(
      "synthetic-estate.json",
    );
    await expect(page.locator("#estate-file-info")).not.toContainText(
      "Reading",
    );
    if (replacement === "cleared") await input.setInputFiles([]);
    else
      await input.setInputFiles({
        name: `${replacement}.json`,
        mimeType: "application/json",
        buffer:
          replacement === "oversized"
            ? Buffer.alloc(5 * 1024 * 1024 + 1, " ")
            : Buffer.from("{invalid"),
      });
    if (replacement === "invalid")
      await expect(page.locator("#estate-file-info")).toContainText(
        "Unable to read valid JSON",
      );
    if (replacement === "oversized")
      await expect(dialog.locator(".form-error")).toContainText(
        "File too large",
      );
    await dialog.getByRole("button", { name: "Import", exact: true }).click();
    await expect(dialog.locator(".form-error")).toContainText(
      "Choose a valid JSON file first",
    );
    await expect(dialog).toBeVisible();
    expect(requests).toBe(0);
  });
}

test("keeps the latest estate when an earlier file finishes reading later", async ({
  page,
}) => {
  await page.addInitScript(() => {
    const original = globalThis.File.prototype.text;
    globalThis.File.prototype.text = function () {
      if (this.name === "slow.json")
        return new Promise((resolve) => {
          globalThis.finishSlowEstate = () =>
            resolve(
              JSON.stringify({
                kind: "gpo-lens-estate",
                gpos: [],
                marker: "old",
              }),
            );
        });
      return original.call(this);
    };
  });
  let imported;
  await page.route("**/api/estate/import", async (route) => {
    imported = route.request().postDataJSON();
    await route.fulfill({ json: { imported: 0, skipped: 0 } });
  });
  const dialog = await openImport(page);
  const input = page.locator("#estate-file");
  await input.setInputFiles({ ...validFile, name: "slow.json" });
  await expect(page.locator("#estate-file-info")).toContainText(
    "Reading slow.json",
  );
  await input.setInputFiles({
    ...validFile,
    name: "latest.json",
    buffer: Buffer.from(
      JSON.stringify({ kind: "gpo-lens-estate", gpos: [], marker: "latest" }),
    ),
  });
  await expect(page.locator("#estate-file-info")).toContainText("latest.json");
  await expect(page.locator("#estate-file-info")).not.toContainText("Reading");
  await page.evaluate(() => globalThis.finishSlowEstate());
  await expect(page.locator("#estate-file-info")).toContainText("latest.json");
  await dialog.getByRole("button", { name: "Import", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(imported.marker).toBe("latest");
});
