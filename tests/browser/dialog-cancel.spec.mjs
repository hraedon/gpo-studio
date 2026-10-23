import { expect, test } from "@playwright/test";

test("creation can be cancelled with invalid required fields @smoke", async ({
  page,
  request,
}) => {
  await page.goto("/");
  const before = await (await request.get("/api/gpos")).json();
  const opener = page.locator("#new-gpo");
  const dialog = page.locator("#gpo-dialog");
  for (const action of ["Cancel", "Close", "Escape"]) {
    await opener.click();
    await dialog.locator('[name="reason"]').fill("");
    await expect(dialog.locator('[name="name"]')).toHaveValue("");
    if (action === "Escape") await page.keyboard.press("Escape");
    else
      await dialog.getByRole("button", { name: action, exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
  }
  expect(await (await request.get("/api/gpos")).json()).toEqual(before);
});

test("Enter creates a draft instead of implicitly activating Close @smoke", async ({
  page,
}) => {
  await page.goto("/");
  await page.locator("#new-gpo").click();
  const dialog = page.locator("#gpo-dialog");
  const name = `Synthetic Enter Draft ${Date.now()}`;
  await dialog.locator('[name="name"]').fill(name);
  await dialog.locator('[name="name"]').press("Enter");
  await expect(dialog).toBeHidden();
  await expect(page.locator("#title")).toHaveText(name);
});

test("every dialog has non-submitting dismissal controls", async ({ page }) => {
  await page.goto("/");
  const failures = await page
    .locator("dialog")
    .evaluateAll((dialogs) =>
      dialogs.flatMap((dialog) =>
        [...dialog.querySelectorAll('button[value="cancel"]')]
          .filter((button) => button.type !== "button")
          .map(() => dialog.id),
      ),
    );
  expect(failures).toEqual([]);
  // Exercise actual invalid edit/import/tool forms as well as creation.
  for (const [opener, dialogId] of [
    ["#import-gpmc", "#gpmc-import-dialog"],
    ["#open-templates", "#templates-dialog"],
  ]) {
    await page.locator(opener).click();
    const dialog = page.locator(dialogId);
    await dialog.locator("input[required]").first().fill("");
    await dialog
      .getByRole("button", { name: "Close", exact: true })
      .first()
      .click();
    await expect(dialog).toBeHidden();
  }
});
