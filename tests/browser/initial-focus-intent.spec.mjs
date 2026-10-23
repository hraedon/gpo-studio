import { expect, test } from "@playwright/test";

async function holdOpening(page) {
  await page.evaluate(() => {
    const native = globalThis.requestAnimationFrame,
      queued = [];
    globalThis.requestAnimationFrame = (callback) => {
      queued.push(callback);
      return queued.length;
    };
    try {
      globalThis.document.querySelector("#new-gpo").click();
    } finally {
      globalThis.requestAnimationFrame = native;
    }
    globalThis.releaseInitialFocus = () =>
      new Promise((resolve) =>
        native(() => {
          for (const callback of queued) callback(globalThis.performance.now());
          native(resolve);
        }),
      );
    if (queued.length !== 1)
      throw Error(`Expected one application focus frame, got ${queued.length}`);
  });
}
test("initial dialog focus never overrides an intentional field move @smoke", async ({
  page,
}) => {
  await page.goto("/");
  await holdOpening(page);
  const description = page.locator('#gpo-form [name="description"]');
  await description.fill("Synthetic intentional draft");
  await expect(description).toBeFocused();
  await page.evaluate(() => globalThis.releaseInitialFocus());
  await expect(description).toBeFocused();
  await expect(description).toHaveValue("Synthetic intentional draft");
  await page.keyboard.press("Escape");
});
test("untouched initial dialog focus still reaches the first field", async ({
  page,
}) => {
  await page.goto("/");
  await holdOpening(page);
  await page.evaluate(() => globalThis.releaseInitialFocus());
  await expect(page.locator('#gpo-form [name="name"]')).toBeFocused();
  await page.keyboard.press("Escape");
});
test("an old opening frame cannot focus a reopened editor", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator("#app-status")).toBeHidden();
  await holdOpening(page);
  await page.evaluate(() => {
    globalThis.releaseFirstOpening = globalThis.releaseInitialFocus;
  });
  await page.keyboard.press("Escape");
  await holdOpening(page);
  const description = page.locator('#gpo-form [name="description"]');
  await description.fill("Synthetic reopened intentional draft");
  await page.evaluate(() => globalThis.releaseFirstOpening());
  await expect(description).toBeFocused();
  await expect(description).toHaveValue("Synthetic reopened intentional draft");
  await page.evaluate(() => globalThis.releaseInitialFocus());
  await expect(description).toBeFocused();
});
test("a current inline save error wins over the initial focus frame", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator("#app-status")).toBeHidden();
  await holdOpening(page);
  await page.route("**/api/gpos", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await route.fulfill({
      status: 503,
      json: { error: { message: "Synthetic current creation failure" } },
    });
  });
  const dialog = page.locator("#gpo-dialog");
  await dialog
    .getByLabel("Name", { exact: true })
    .fill("Synthetic initial focus refusal");
  await dialog.locator("#gpo-submit").click();
  const error = dialog.locator(".form-error");
  await expect(error).toContainText("Synthetic current creation failure");
  await expect(error).toBeFocused();
  await page.evaluate(() => globalThis.releaseInitialFocus());
  await expect(error).toBeFocused();
  await expect(dialog.getByLabel("Name", { exact: true })).toHaveValue(
    "Synthetic initial focus refusal",
  );
});
