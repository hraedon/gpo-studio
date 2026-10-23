import { test, expect } from "@playwright/test";

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

async function seed(request, label) {
  const response = await request.post("/api/gpos", {
    data: { name: `Synthetic ADMX snapshot ${label} ${Date.now()}` },
  });
  expect(response.status()).toBe(201);
  return (await response.json()).gpo;
}

async function open(page, gpo) {
  await page.goto("/");
  await page.locator(`[data-guid="${gpo.guid}"]`).click();
  await page.getByRole("tab", { name: "ADMX", exact: true }).click();
  await page
    .locator(".admx-result", { hasText: "Synthetic feature with options" })
    .click();
  await page.locator("#admx-configure-btn").click();
  return page.locator("#configure-dialog");
}

for (const sameTarget of [true, false]) {
  test(`late same-policy save cannot advance the ${sameTarget ? "selected target @smoke" : "other target"} in a reopened ADMX editor`, async ({
    page,
    request,
  }) => {
    const alpha = await seed(request, "underlay");
    const beta = sameTarget ? alpha : await seed(request, "explicit target");
    const dialog = await open(page, alpha);
    await dialog.locator("#configure-target-gpo").selectOption(beta.guid);
    await dialog.locator('[data-elem-id="SubOption"]').uncheck();
    const arrived = deferred(),
      release = deferred(),
      done = deferred();
    const sent = [];
    await page.route("**/api/admx/policies/*/configure", async (route) => {
      sent.push(route.request().postDataJSON());
      const response = await route.fetch();
      if (sent.length === 1) {
        arrived.resolve(response.status());
        await release.promise;
      }
      await route.fulfill({ response });
      if (sent.length === 1) done.resolve();
    });
    try {
      await dialog.getByRole("button", { name: "Apply policy" }).click();
      expect(await arrived.promise).toBe(200);
      await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
      await page.locator("#admx-configure-btn").click();
      await dialog.locator('[data-elem-id="SubOption"]').check();
      await dialog
        .locator('[name="reason"]')
        .fill("Keep my newer reviewed draft");
      const option = dialog.locator(
        `#configure-target-gpo option[value="${beta.guid}"]`,
      );
      await expect(option).toContainText("r1");
      release.resolve();
      await done.promise;
      await expect(page.locator(`[data-guid="${beta.guid}"]`)).toContainText(
        "r2",
      );
      await expect(dialog).toBeVisible();
      await expect(option).toContainText("r1");
      // The other target is selected only after the sidebar has advanced.
      // Its displayed option still represents the form's opening snapshot.
      await dialog.locator("#configure-target-gpo").selectOption(beta.guid);
      const rejected = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          response.url().endsWith("/configure"),
      );
      await dialog.getByRole("button", { name: "Apply policy" }).click();
      expect((await rejected).status()).toBe(409);
      expect(sent[1]).toMatchObject({
        gpo_guid: beta.guid,
        expected_revision: 1,
      });
      const conflict = page.locator("#conflict-dialog");
      await expect(conflict).toBeVisible();
      await expect(conflict.locator("#conflict-details")).toContainText(
        "Current workspace revision: 2",
      );
      await expect(dialog.locator('[data-elem-id="SubOption"]')).toBeChecked();
      await expect(dialog.locator('[name="reason"]')).toHaveValue(
        "Keep my newer reviewed draft",
      );
      let saved = (await (await request.get(`/api/gpos/${beta.guid}`)).json())
        .gpo;
      expect(saved.revision).toBe(2);
      expect(
        saved.settings.find((setting) => setting.value_name === "SubOption")
          .value,
      ).toBe("0");
      await conflict
        .getByRole("button", { name: "Review and reapply" })
        .click();
      await expect(option).toContainText("r2");
      await expect(page.locator("#title")).toHaveText(alpha.name);
      await dialog.getByRole("button", { name: "Apply policy" }).click();
      await expect(dialog).toBeHidden();
      expect(sent[2]).toMatchObject({
        gpo_guid: beta.guid,
        expected_revision: 2,
      });
      saved = (await (await request.get(`/api/gpos/${beta.guid}`)).json()).gpo;
      expect(saved.revision).toBe(3);
      expect(
        saved.settings.find((setting) => setting.value_name === "SubOption")
          .value,
      ).toBe("1");
    } finally {
      release.resolve();
    }
  });
}

test("conflict review cannot make an archived ADMX target editable", async ({
  page,
  request,
}) => {
  const alpha = await seed(request, "archive underlay");
  const beta = await seed(request, "archive target");
  const dialog = await open(page, alpha);
  await dialog.locator("#configure-target-gpo").selectOption(beta.guid);
  await dialog.locator('[data-elem-id="SubOption"]').check();
  const response = await request.patch(`/api/gpos/${beta.guid}`, {
    data: {
      name: beta.name,
      status: "archived",
      expected_revision: 1,
      actor: "other-operator",
      reason: "Archive synthetic target while its form is open",
    },
  });
  expect(response.status()).toBe(200);
  await dialog.getByRole("button", { name: "Apply policy" }).click();
  const conflict = page.locator("#conflict-dialog");
  await expect(conflict).toBeVisible();
  await conflict.getByRole("button", { name: "Review and reapply" }).click();
  await expect(dialog).toContainText("The target policy is archived");
  await expect(dialog.locator("#configure-target-gpo")).toHaveValue("");
  await expect(
    dialog.locator(`#configure-target-gpo option[value="${beta.guid}"]`),
  ).toHaveJSProperty("disabled", true);
  await expect(page.locator("#app-status")).toBeHidden();
  await expect(dialog.locator(".form-error")).toBeFocused();
  await expect(dialog.locator('[data-elem-id="SubOption"]')).toBeChecked();
  await dialog.getByRole("button", { name: "Apply policy" }).click();
  await expect(dialog).toBeVisible();
  let archived = (await (await request.get(`/api/gpos/${beta.guid}`)).json())
    .gpo;
  expect(archived.revision).toBe(2);
  expect(archived.settings).toEqual([]);
  await dialog.locator("#configure-target-gpo").selectOption(alpha.guid);
  await dialog.getByRole("button", { name: "Apply policy" }).click();
  await expect(dialog).toBeHidden();
  expect(
    (await (await request.get(`/api/gpos/${alpha.guid}`)).json()).gpo.revision,
  ).toBe(2);
  archived = (await (await request.get(`/api/gpos/${beta.guid}`)).json()).gpo;
  expect(archived.revision).toBe(2);
  expect(archived.settings).toEqual([]);
});
