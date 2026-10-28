import { expect, test } from "@playwright/test";

test("duplicate draft names show inline errors without unrelated revision recovery", async ({
  page,
  request,
}) => {
  const name = `Synthetic Duplicate Draft ${Date.now()}`;
  const seeded = await request.post("/api/gpos", {
    data: { name, actor: "browser-test", reason: "Seed duplicate-name test" },
  });
  expect(seeded.status()).toBe(201);
  await page.goto("/");
  await expect(page.locator("#app-status")).toBeHidden();
  let historyReads = 0;
  page.on("request", (req) => {
    if (req.method() === "GET" && req.url().includes("/revisions"))
      historyReads++;
  });
  await page.locator("#new-gpo").click();
  const dialog = page.locator("#gpo-dialog");
  await dialog.locator('[name="name"]').fill(name);
  await dialog
    .locator('[name="description"]')
    .fill("Unsaved description survives a name conflict");
  await dialog.getByRole("button", { name: "Create policy" }).click();
  await expect(dialog.locator(".form-error")).toContainText("already exists");
  await expect(page.locator("#conflict-dialog")).toBeHidden();
  await expect(dialog.locator('[name="name"]')).toBeEnabled();
  expect(historyReads).toBe(0);
  await expect(dialog.locator('[name="description"]')).toHaveValue(
    "Unsaved description survives a name conflict",
  );
  await dialog.locator('[name="name"]').fill(`${name} separate`);
  await dialog.getByRole("button", { name: "Create policy" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.locator("#title")).toHaveText(`${name} separate`);
});
