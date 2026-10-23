import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

async function seedPolicy(page) {
  const name = `Synthetic Dialog Layout ${Date.now()}`;
  const response = await page.request.post("/api/gpos", {
    data: { name, actor: "browser-test", reason: "Synthetic dialog layout" },
  });
  expect(response.status()).toBe(201);
  const { gpo } = await response.json();
  await page.goto("/");
  await page.locator(`[data-guid="${gpo.guid}"]`).click();
  await expect(page.locator("#title")).toHaveText(name);
}

async function expectUnobscuredFocus(page) {
  const focused = await page.evaluate(() => {
    const doc = globalThis.document;
    const active = doc.activeElement;
    if (!active.matches("input,select,textarea,button")) return null;
    const rect = active.getBoundingClientRect();
    const top = doc.elementFromPoint(
      rect.x + rect.width / 2,
      rect.y + rect.height / 2,
    );
    return {
      field: active.outerHTML,
      visible: active === top || active.contains(top),
    };
  });
  if (focused) expect(focused.visible, focused.field).toBe(true);
}

for (const theme of ["light", "dark"]) {
  for (const viewport of [
    { width: 360, height: 640 },
    { width: 1280, height: 800 },
  ]) {
    test(`keeps dialog fields and actions visible in ${theme} at ${viewport.width}px`, async ({
      page,
    }) => {
      await page.emulateMedia({ colorScheme: theme });
      await page.setViewportSize(viewport);
      await seedPolicy(page);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      const cases = [
        ["overview", "#edit-metadata", "#gpo-dialog"],
        ["settings", "#add-setting", "#setting-dialog"],
        ["security", "#edit-wmi", "#wmi-dialog"],
        ["preferences", "#add-gpp-group", "#gpp-group-dialog"],
        ["preferences", "#add-gpp-registry", "#gpp-registry-dialog"],
      ];
      for (const [tab, opener, selector] of cases) {
        await page.locator(`[data-tab="${tab}"]`).click();
        await page.locator(opener).click();
        const dialog = page.locator(selector);
        if (selector === "#gpp-group-dialog") {
          await dialog.locator("#gpp-add-member").click();
          await dialog.locator("#gpp-add-predicate").click();
        }
        const controls = await dialog.evaluate(
          (element) =>
            [
              ...element.querySelectorAll("button,input,select,textarea"),
            ].filter(
              (control) =>
                !control.disabled &&
                control.type !== "hidden" &&
                control.getClientRects().length,
            ).length,
        );
        for (let index = 0; index < controls + 2; index++) {
          await page.keyboard.press("Tab");
          await expectUnobscuredFocus(page);
        }
        await expect(dialog.locator(".dialog-head button")).toBeInViewport();
        await expect(dialog.locator(".dialog-actions")).toBeInViewport();
        await page.keyboard.press("Escape");
      }
      // Result/status regions that used to follow the footer must now scroll
      // with the body instead of creating extra grid rows.
      const structures = await page
        .locator("dialog form")
        .evaluateAll((forms) =>
          forms.map((form) => ({
            classes: [...form.children].map((child) => child.className),
            associated: [
              ...form.querySelectorAll("input,select,textarea"),
            ].every((field) => field.form === form),
          })),
        );
      for (const structure of structures) {
        expect(structure.classes).toEqual([
          "dialog-head",
          "dialog-body",
          "dialog-actions",
        ]);
        expect(structure.associated).toBe(true);
      }
    });
  }
}

test("labels configuration checkboxes and retains help through field-error recovery", async ({
  page,
}) => {
  await page.setViewportSize({ width: 360, height: 640 });
  await seedPolicy(page);
  await page.locator("#edit-metadata").click();
  const metadata = page.locator("#gpo-dialog");
  for (const name of ["Computer configuration", "User configuration"]) {
    const checkbox = metadata.getByRole("checkbox", { name, exact: true });
    await expect(checkbox).toBeChecked();
    await metadata.getByText(name, { exact: true }).click();
    await expect(checkbox).not.toBeChecked();
  }
  const metadataAxe = await new AxeBuilder({ page })
    .include("#gpo-dialog")
    .analyze();
  expect(
    metadataAxe.violations.filter((issue) => issue.id === "label"),
  ).toEqual([]);
  await page.keyboard.press("Escape");
  await page.locator('[data-tab="security"]').click();
  await page.locator("#add-filter").click();
  const dialog = page.locator("#filter-dialog");
  const checkbox = dialog.getByRole("checkbox", {
    name: "Inheritable",
    exact: true,
  });
  const helpId = "filter-inheritable-help";
  await expect(checkbox).toHaveAttribute("aria-describedby", helpId);
  await dialog.getByLabel("Principal (SID or account)").fill("S-1-5-32-544");
  let attempt = 0;
  await page.route("**/api/gpos/*/security-filters", async (route) => {
    if (++attempt > 1) return route.continue();
    await route.fulfill({
      status: 422,
      json: {
        error: {
          message: "Synthetic field failure",
          issues: [
            {
              path: "filter.inheritable",
              message: "Synthetic inheritance issue",
            },
            {
              path: "filter.inheritable",
              message: "Synthetic additional detail",
            },
          ],
        },
      },
    });
  });
  await dialog.getByRole("button", { name: "Save filter" }).click();
  const summary = dialog.locator(".dialog-body > .form-error");
  await expect(summary).toContainText("Synthetic inheritance issue");
  const descriptions = (await checkbox.getAttribute("aria-describedby")).split(
    " ",
  );
  expect(descriptions).toContain(helpId);
  expect(new Set(descriptions).size).toBe(3);
  for (const id of descriptions)
    await expect(dialog.locator(`[id="${id}"]`)).toHaveCount(1);
  await summary.getByRole("link").first().click();
  await expect(checkbox).toBeFocused();
  await expectUnobscuredFocus(page);
  await dialog.getByRole("button", { name: "Save filter" }).click();
  await expect(dialog).toBeHidden();
  await page.locator("#add-filter").click();
  await expect(checkbox).toHaveAttribute("aria-describedby", helpId);
  await expect(checkbox).not.toHaveAttribute("aria-invalid");
  await expect(dialog.locator(".field-error,.form-error")).toHaveCount(0);
  const filterAxe = await new AxeBuilder({ page })
    .include("#filter-dialog")
    .analyze();
  expect(filterAxe.violations.filter((issue) => issue.id === "label")).toEqual(
    [],
  );
});
