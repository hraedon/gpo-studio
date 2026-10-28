/* global document, requestAnimationFrame */
import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

test.beforeEach(async ({ page }) => {
  if (process.env.GPO_FOCUS_BASELINE)
    await page.route("**/assets/js/main.mjs", (route) =>
      route.fulfill({
        body: readFileSync(".review/baseline-main.mjs"),
        contentType: "application/javascript",
      }),
    );
});

async function seed(request, name) {
  const created = await request.post("/api/gpos", {
    data: { name, actor: "browser-test", reason: "Synthetic focus fixture" },
  });
  expect(created.status()).toBe(201);
  let { gpo } = await created.json();
  for (const value_name of ["FirstValue", "SecondValue"]) {
    const added = await request.post(`/api/gpos/${gpo.guid}/settings`, {
      data: {
        expected_revision: gpo.revision,
        actor: "browser-test",
        reason: "Synthetic focus fixture",
        setting: {
          side: "computer",
          hive: "HKLM",
          key: "Software\\Policies\\Synthetic\\DialogFocus",
          value_name,
          registry_type: "REG_SZ",
          value: "original",
          action: "set",
        },
      },
    });
    expect(added.status()).toBe(201);
    ({ gpo } = await added.json());
  }
  return gpo;
}

async function select(page, gpo) {
  await page.locator(`[data-guid="${gpo.guid}"]`).click();
  await expect(page.locator("#title")).toHaveText(gpo.name);
  await page.getByRole("tab", { name: /Policy settings/ }).click();
}

async function settle(page) {
  await page.evaluate(
    () =>
      new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      ),
  );
}

for (const action of ["edit", "comment"]) {
  test(`owned ${action} save returns to the same replacement row action @smoke`, async ({
    page,
    request,
  }) => {
    const gpo = await seed(request, `Synthetic focus ${action} ${Date.now()}`);
    await page.goto("/");
    await select(page, gpo);
    const id = gpo.settings[1].id;
    const opener = page.locator(`[data-${action}-setting="${id}"]`);
    await opener.focus();
    await page.keyboard.press("Enter");
    const form = page.locator(
      action === "edit" ? "#setting-form" : "#comment-form",
    );
    await form
      .locator(`[name="${action === "edit" ? "value" : "comment"}"]`)
      .fill("Synthetic updated second row");
    await form
      .getByRole("button", {
        name: `Save ${action === "edit" ? "setting" : "comment"}`,
        exact: true,
      })
      .focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("dialog[open]")).toHaveCount(0);
    await expect(page.locator("#revision")).toHaveText("Revision 4");
    await expect(page.locator("#settings-table tr").nth(1)).toContainText(
      "Synthetic updated second row",
    );
    await settle(page);
    await expect(opener).toBeFocused();
    const stored = await (await request.get(`/api/gpos/${gpo.guid}`)).json();
    expect(stored.gpo.settings[0].value).toBe("original");
    expect(
      stored.gpo.settings[1][action === "edit" ? "value" : "comment"],
    ).toBe("Synthetic updated second row");
    if (action === "edit") {
      await page.keyboard.press("Tab");
      await expect(
        page.locator(`[data-comment-setting="${id}"]`),
      ).toBeFocused();
    }
  });
}

test("Cancel and Escape keep a connected opener @smoke", async ({
  page,
  request,
}) => {
  const gpo = await seed(request, `Synthetic focus Cancel ${Date.now()}`);
  await page.goto("/");
  await select(page, gpo);
  const opener = page.locator(`[data-edit-setting="${gpo.settings[1].id}"]`);
  for (const action of ["cancel", "escape"]) {
    await opener.focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("#setting-dialog")).toBeVisible();
    if (action === "cancel")
      await page
        .locator("#setting-dialog")
        .getByRole("button", { name: "Cancel", exact: true })
        .click();
    else await page.keyboard.press("Escape");
    await expect(page.locator("#setting-dialog")).not.toBeVisible();
    await settle(page);
    await expect(opener).toBeFocused();
  }
  expect(
    (await (await request.get(`/api/gpos/${gpo.guid}`)).json()).gpo.revision,
  ).toBe(3);
});

test("a newer intentional focus move wins over scheduled return @smoke", async ({
  page,
  request,
}) => {
  const gpo = await seed(request, `Synthetic focus choice ${Date.now()}`);
  await page.goto("/");
  await select(page, gpo);
  await page.locator(`[data-edit-setting="${gpo.settings[0].id}"]`).click();
  await settle(page);
  await page.evaluate(() => {
    document.querySelector("#setting-dialog").close("cancel");
    document.querySelector("#search").focus();
  });
  await settle(page);
  await expect(page.locator("#search")).toBeFocused();
});

test("a queued old close cannot consume the reopened dialog's focus target @smoke", async ({
  page,
  request,
}) => {
  const gpo = await seed(request, `Synthetic focus reopening ${Date.now()}`);
  await page.goto("/");
  await select(page, gpo);
  await page.locator(`[data-edit-setting="${gpo.settings[0].id}"]`).click();
  await settle(page);
  await page.evaluate((id) => {
    document.querySelector("#setting-dialog").close("cancel");
    const opener = document.querySelector(`[data-edit-setting="${id}"]`);
    opener.focus();
    opener.click();
  }, gpo.settings[1].id);
  await settle(page);
  await expect(page.locator("#setting-dialog")).toBeVisible();
  await page
    .locator('#setting-form [name="value"]')
    .fill("Synthetic reopened save");
  await page
    .locator("#setting-dialog")
    .getByRole("button", { name: "Save setting", exact: true })
    .click();
  await expect(page.locator("#setting-dialog")).not.toBeVisible();
  await expect(page.locator("#revision")).toHaveText("Revision 4");
  await settle(page);
  await expect(
    page.locator(`[data-edit-setting="${gpo.settings[1].id}"]`),
  ).toBeFocused();
});

for (const retire of ["reopened editor", "another policy"]) {
  test(`retired save cannot restore focus into ${retire} @smoke`, async ({
    page,
    request,
  }) => {
    const alpha = await seed(request, `Synthetic focus Alpha ${Date.now()}`);
    const beta = await seed(request, `Synthetic focus Beta ${Date.now()}`);
    await page.goto("/");
    await select(page, alpha);
    let release, arrived;
    const held = new Promise((resolve) => {
      release = resolve;
    });
    const sent = new Promise((resolve) => {
      arrived = resolve;
    });
    await page.route(
      `**/api/gpos/${alpha.guid}/settings/${alpha.settings[1].id}`,
      async (route) => {
        if (route.request().method() !== "PUT") return route.continue();
        const response = await route.fetch();
        expect(response.status()).toBe(200);
        arrived();
        await held;
        await route.fulfill({ response });
      },
    );
    try {
      await page
        .locator(`[data-edit-setting="${alpha.settings[1].id}"]`)
        .click();
      await page
        .locator('#setting-form [name="value"]')
        .fill("Synthetic pending save");
      await page
        .locator("#setting-dialog")
        .getByRole("button", { name: "Save setting", exact: true })
        .click();
      await sent;
      await page
        .locator("#setting-dialog")
        .getByRole("button", { name: "Cancel", exact: true })
        .click();
      await settle(page);
      let focused;
      if (retire === "reopened editor") {
        await page
          .locator(`[data-edit-setting="${alpha.settings[0].id}"]`)
          .click();
        focused = page.locator('#setting-form [name="value"]');
        await focused.fill("Synthetic unsaved new opening");
      } else {
        await select(page, beta);
        focused = page.locator("#search");
        await focused.focus();
      }
      const completed = page.waitForResponse(
        (response) => response.request().method() === "PUT",
      );
      release();
      await completed;
      if (retire === "reopened editor") {
        await expect(page.locator("#revision")).toHaveText("Revision 4");
        await expect(focused).toHaveValue("Synthetic unsaved new opening");
      } else await expect(page.locator("#title")).toHaveText(beta.name);
      await settle(page);
      await expect(focused).toBeFocused();
      expect(
        (await (await request.get(`/api/gpos/${alpha.guid}`)).json()).gpo
          .settings[1].value,
      ).toBe("Synthetic pending save");
    } finally {
      release();
    }
  });
}
