import { expect, test } from "@playwright/test";

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

async function openCreation(page, name) {
  await page.goto("/");
  await expect(page.locator("#app-status")).toBeHidden();
  await page.locator("#new-gpo").click();
  const dialog = page.locator("#gpo-dialog");
  await dialog.getByLabel("Name", { exact: true }).fill(name);
  return dialog;
}

test("submits creation once while a request is pending and preserves values for retry", async ({
  page,
}) => {
  const pending = deferred();
  let attempts = 0;
  await page.route("**/api/gpos", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    attempts++;
    if (attempts === 1) {
      await pending.promise;
      return route.fulfill({
        status: 503,
        json: { error: { message: "Synthetic temporary create failure" } },
      });
    }
    return route.continue();
  });
  const name = `Synthetic Pending Creation ${Date.now()}`;
  const dialog = await openCreation(page, name);
  await dialog.getByLabel("Description").fill("Retained synthetic description");
  const submit = dialog.getByRole("button", { name: "Create policy" });
  await submit.click();
  await expect.poll(() => attempts).toBe(1);
  await expect(submit).toBeDisabled();
  await dialog.getByLabel("Name", { exact: true }).press("Enter");
  // Force a submit event as well: a disabled button alone does not protect
  // against another submitter or a programmatic form submission.
  await page.locator("#gpo-form").evaluate((form) => form.requestSubmit());
  await expect(
    dialog.getByRole("button", { name: "Cancel", exact: true }),
  ).toBeEnabled();
  expect(attempts).toBe(1);
  pending.resolve();
  await expect(dialog.locator(".form-error")).toContainText(
    "Synthetic temporary create failure",
  );
  await expect(submit).toBeEnabled();
  expect(attempts).toBe(1);
  await expect(dialog.getByLabel("Name", { exact: true })).toHaveValue(name);
  await expect(dialog.getByLabel("Description")).toHaveValue(
    "Retained synthetic description",
  );
  await submit.click();
  await expect(dialog).not.toBeVisible();
  await expect(page.locator("#title")).toHaveText(name);
  expect(attempts).toBe(2);
  // Successful completion also releases the lock for the next creation.
  await page.locator("#new-gpo").click();
  await expect(page.locator("#gpo-submit")).toBeEnabled();
});

test("can close a pending creation without a late error changing the reopened form", async ({
  page,
}) => {
  const pending = deferred();
  await page.route("**/api/gpos", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await pending.promise;
    return route.fulfill({
      status: 503,
      json: { error: { message: "Synthetic cancelled-view failure" } },
    });
  });
  const dialog = await openCreation(page, "Synthetic First Pending View");
  await dialog.getByRole("button", { name: "Create policy" }).click();
  await expect(page.locator("#gpo-submit")).toBeDisabled();
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await page.locator("#new-gpo").click();
  await dialog.getByLabel("Name", { exact: true }).fill("Synthetic New View");
  pending.resolve();
  await expect(page.locator("#gpo-submit")).toBeEnabled();
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".form-error")).toHaveCount(0);
  await expect(dialog.getByLabel("Name", { exact: true })).toHaveValue(
    "Synthetic New View",
  );
});
