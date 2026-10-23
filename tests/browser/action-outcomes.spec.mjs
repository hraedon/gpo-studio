import { expect, test } from "@playwright/test";

async function warning(page, request) {
  const first = (
    await (
      await request.post("/api/gpos", {
        data: {
          name: `Synthetic Action Target ${Date.now()}`,
          actor: "browser-test",
          reason: "Synthetic action fixture",
        },
      })
    ).json()
  ).gpo;
  const failed = (
    await (
      await request.post("/api/gpos", {
        data: {
          name: `Synthetic Action Failed Selection ${Date.now()}`,
          actor: "browser-test",
          reason: "Synthetic action fixture",
        },
      })
    ).json()
  ).gpo;
  await page.goto("/");
  await page.locator(`[data-guid="${first.guid}"]`).click();
  await expect(page.locator("#title")).toHaveText(first.name);
  await page.route(`**/api/gpos/${failed.guid}`, (route) =>
    route.fulfill({
      status: 503,
      json: { error: { message: "Synthetic actionable selection failure" } },
    }),
  );
  await page.locator(`[data-guid="${failed.guid}"]`).click();
  await expect(page.locator("#error-message")).toContainText(
    "Synthetic actionable selection failure",
  );
  return { first, failed };
}
for (const retire of [true, false])
  test(`${retire ? "retired" : "current"} semantic POST preview preserves a real failed-selection warning`, async ({
    page,
    request,
  }) => {
    const { first } = await warning(page, request);
    await page.locator("#open-security-template").click();
    const field = page.locator('#security-template-form [name="families"]');
    await field.fill('{"audit":{"logon_events":"success"}}');
    let release, entered, done;
    const gate = new Promise((r) => (release = r)),
      ready = new Promise((r) => (entered = r)),
      finished = new Promise((r) => (done = r));
    await page.route(
      "**/api/security-template/policy-families",
      async (route) => {
        const response = await route.fetch();
        entered();
        await gate;
        await route.fulfill({ response });
        done();
      },
    );
    await page
      .locator('#security-template-form button[value="default"]')
      .click();
    await ready;
    if (retire) {
      await field.fill('{"audit":{"logon_events":"failure"}}');
      await field.focus();
    }
    release();
    await finished;
    await page.evaluate(
      () =>
        new Promise((r) =>
          globalThis.requestAnimationFrame(() =>
            globalThis.requestAnimationFrame(r),
          ),
        ),
    );
    await expect(page.locator("#error-summary")).toBeVisible();
    await expect(page.locator("#error-message")).toContainText(
      "Synthetic actionable selection failure",
    );
    if (retire) {
      await expect(field).toBeFocused();
      await expect(field).toHaveValue('{"audit":{"logon_events":"failure"}}');
      await expect(
        page.locator("#security-template-results"),
      ).not.toContainText("Rendering…");
    } else
      await expect(page.locator("#security-template-results")).toContainText(
        "Audit",
      );
    expect(
      (await (await request.get(`/api/gpos/${first.guid}`)).json()).gpo
        .revision,
    ).toBe(1);
  });
for (const newer of [false, true])
  test(`committed late write ${newer ? "retains a newer error" : "resolves its preceding error"} after its editor closes`, async ({
    page,
    request,
  }) => {
    const { first, failed } = await warning(page, request);
    let release, entered, done;
    const gate = new Promise((r) => (release = r)),
      ready = new Promise((r) => (entered = r)),
      finished = new Promise((r) => (done = r));
    let created;
    await page.route("**/api/gpos", async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      const response = await route.fetch();
      created = (await response.json()).gpo;
      entered();
      await gate;
      await route.fulfill({ response });
      done();
    });
    await page.locator("#new-gpo").click();
    await page
      .locator('#gpo-form [name="name"]')
      .fill(`Synthetic Committed Late Write ${Date.now()}`);
    await page.locator("#gpo-submit").click();
    await ready;
    await page.keyboard.press("Escape");
    if (newer) {
      await page.unroute(`**/api/gpos/${failed.guid}`);
      await page.route(`**/api/gpos/${failed.guid}`, (route) =>
        route.fulfill({
          status: 503,
          json: { error: { message: "Synthetic newer selection failure" } },
        }),
      );
      await page.locator(`[data-guid="${failed.guid}"]`).click();
      await expect(page.locator("#error-message")).toContainText(
        "Synthetic newer selection failure",
      );
    }
    release();
    await finished;
    if (newer)
      await expect(page.locator("#error-message")).toContainText(
        "Synthetic newer selection failure",
      );
    else await expect(page.locator("#error-summary")).toBeHidden();
    await expect(page.locator("#title")).toHaveText(first.name);
    expect(
      (await (await request.get(`/api/gpos/${created.guid}`)).json()).gpo.name,
    ).toBe(created.name);
  });
test("a committed 204 Starter deletion resolves its preceding warning", async ({
  page,
  request,
}) => {
  const starter = (
    await (
      await request.post("/api/starter-gpos", {
        data: {
          name: `Synthetic204 Starter ${Date.now()}`,
          actor: "browser-test",
          reason: "Synthetic204 action",
        },
      })
    ).json()
  ).gpo;
  await warning(page, request);
  await page.locator("#open-starter-gpos").click();
  await expect(
    page.locator(`[data-delete-starter="${starter.guid}"]`),
  ).toBeVisible();
  page.once("dialog", (dialog) => dialog.accept());
  const removed = page.waitForResponse(
    (r) =>
      r.url().includes(`/api/starter-gpos/${starter.guid}`) &&
      r.request().method() === "DELETE",
  );
  await page.locator(`[data-delete-starter="${starter.guid}"]`).click();
  expect((await removed).status()).toBe(204);
  await expect(page.locator("#error-summary")).toBeHidden();
  await expect(
    page.locator(`[data-delete-starter="${starter.guid}"]`),
  ).toHaveCount(0);
});
