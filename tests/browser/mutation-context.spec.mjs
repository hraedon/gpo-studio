import { expect, test } from "@playwright/test";

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

async function seedPolicy(request, prefix, withHistory = false) {
  const name = `Synthetic ${prefix} ${Date.now()}`;
  const created = await request.post("/api/gpos", {
    data: { name, actor: "browser-test", reason: `${name} initial` },
  });
  expect(created.status()).toBe(201);
  let { gpo } = await created.json();
  if (withHistory) {
    const updated = await request.patch(`/api/gpos/${gpo.guid}`, {
      data: {
        name,
        description: `${name} second description`,
        status: "draft",
        computer_enabled: true,
        user_enabled: true,
        expected_revision: gpo.revision,
        actor: "browser-test",
        reason: `${name} second`,
      },
    });
    expect(updated.status()).toBe(200);
    gpo = (await updated.json()).gpo;
  }
  return gpo;
}

async function selectPolicy(page, gpo) {
  await page.locator(`[data-guid="${gpo.guid}"]`).click();
  await expect(page.locator("#title")).toHaveText(gpo.name);
}

async function flushUi(page) {
  await page.evaluate(
    () => new Promise((resolve) => globalThis.requestAnimationFrame(resolve)),
  );
}

test("discards another policy's delayed history and restores the displayed policy with its captured revision", async ({
  page,
  request,
}) => {
  const alpha = await seedPolicy(request, "History Alpha", true);
  const beta = await seedPolicy(request, "History Beta", true);
  await page.goto("/");
  await selectPolicy(page, alpha);
  const pending = deferred();
  const arrived = deferred();
  const completed = deferred();
  await page.route(`**/api/gpos/${alpha.guid}/revisions`, async (route) => {
    const response = await route.fetch();
    arrived.resolve();
    await pending.promise;
    await route.fulfill({ response });
    completed.resolve();
  });
  try {
    await page.getByRole("tab", { name: "History", exact: true }).click();
    await arrived.promise;
    await selectPolicy(page, beta);
    await expect(page.locator("#history-list")).toContainText(
      `${beta.name} second`,
    );
    pending.resolve();
    await completed.promise;
    await flushUi(page);
    await expect(page.locator("#history-list")).not.toContainText(alpha.name);
    await expect(page.locator("#history-list")).toContainText(
      `${beta.name} initial`,
    );

    const restored = page.waitForRequest(
      (sent) =>
        sent.method() === "POST" && sent.url().endsWith("/revisions/1/restore"),
    );
    page.once("dialog", (dialog) => dialog.accept());
    await page.locator('[data-restore="1"]').click();
    const sent = await restored;
    expect(sent.url()).toContain(`/api/gpos/${beta.guid}/revisions/1/restore`);
    expect(sent.postDataJSON().expected_revision).toBe(beta.revision);
    await expect(page.locator("#revision")).toHaveText("Revision 3");
    const saved = (await (await request.get(`/api/gpos/${beta.guid}`)).json())
      .gpo;
    expect(saved.description).toBe("");
    expect(
      (await (await request.get(`/api/gpos/${alpha.guid}`)).json()).gpo
        .revision,
    ).toBe(2);
  } finally {
    pending.resolve();
  }
});

for (const outcome of ["success", "failure"]) {
  test(`keeps a reopened setting form on another policy after a late ${outcome}, with one pending submission`, async ({
    page,
    request,
  }) => {
    const alpha = await seedPolicy(request, `Setting Alpha ${outcome}`);
    const beta = await seedPolicy(request, `Setting Beta ${outcome}`);
    await page.goto("/");
    await selectPolicy(page, alpha);
    await page.getByRole("tab", { name: /Policy settings/ }).click();
    await page.locator("#add-setting").click();
    const dialog = page.locator("#setting-dialog");
    await dialog
      .getByLabel("Registry key")
      .fill("Software\\Policies\\SyntheticPending");
    await dialog.getByLabel("Value name").fill("OriginalAlphaValue");
    if (outcome === "failure")
      await dialog
        .getByRole("combobox", { name: "Type", exact: true })
        .selectOption("REG_DWORD");
    await dialog
      .locator('textarea[name="value"]')
      .fill(
        outcome === "failure" ? "not-an-integer" : "Synthetic original value",
      );
    const pending = deferred();
    const arrived = deferred();
    const completed = deferred();
    let sent = 0;
    await page.route(`**/api/gpos/${alpha.guid}/settings`, async (route) => {
      sent++;
      const response = await route.fetch();
      expect(response.status()).toBe(outcome === "success" ? 201 : 422);
      arrived.resolve();
      await pending.promise;
      await route.fulfill({ response });
      completed.resolve();
    });
    try {
      await dialog.getByRole("button", { name: "Save setting" }).click();
      await arrived.promise;
      await expect(
        dialog.getByRole("button", { name: "Save setting" }),
      ).toBeDisabled();
      await dialog.getByLabel("Registry key").press("Enter");
      await page
        .locator("#setting-form")
        .evaluate((form) => form.requestSubmit());
      await expect(
        dialog.getByRole("button", { name: "Cancel", exact: true }),
      ).toBeEnabled();
      expect(sent).toBe(1);
      await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
      await selectPolicy(page, beta);
      await page.locator("#add-setting").click();
      await dialog
        .getByLabel("Registry key")
        .fill("Software\\Policies\\SyntheticNewView");
      await dialog.getByLabel("Value name").fill("UnsavedBetaValue");
      await dialog
        .locator('textarea[name="value"]')
        .fill("Keep this Beta draft");
      pending.resolve();
      await completed.promise;
      await expect(
        dialog.getByRole("button", { name: "Save setting" }),
      ).toBeEnabled();
      await flushUi(page);
      expect(sent).toBe(1);
      await expect(page.locator("#title")).toHaveText(beta.name);
      await expect(dialog).toBeVisible();
      await expect(dialog.getByLabel("Registry key")).toHaveValue(
        "Software\\Policies\\SyntheticNewView",
      );
      await expect(dialog.getByLabel("Value name")).toHaveValue(
        "UnsavedBetaValue",
      );
      await expect(dialog.locator('textarea[name="value"]')).toHaveValue(
        "Keep this Beta draft",
      );
      await expect(
        dialog.locator(".form-error,[aria-invalid='true']"),
      ).toHaveCount(0);
      const savedAlpha = (
        await (await request.get(`/api/gpos/${alpha.guid}`)).json()
      ).gpo;
      const savedBeta = (
        await (await request.get(`/api/gpos/${beta.guid}`)).json()
      ).gpo;
      expect(savedAlpha.revision).toBe(outcome === "success" ? 2 : 1);
      expect(savedAlpha.settings).toHaveLength(outcome === "success" ? 1 : 0);
      if (outcome === "success")
        expect(savedAlpha.settings[0].value_name).toBe("OriginalAlphaValue");
      else
        await expect(page.locator("#error-summary")).toContainText(alpha.name);
      expect(savedBeta.revision).toBe(1);
      expect(savedBeta.settings).toEqual([]);
    } finally {
      pending.resolve();
    }
  });
}

test("a delayed restore completion leaves another policy's view unchanged", async ({
  page,
  request,
}) => {
  const alpha = await seedPolicy(request, "Restore Alpha", true);
  const beta = await seedPolicy(request, "Restore Beta", true);
  await page.goto("/");
  await selectPolicy(page, alpha);
  await page.getByRole("tab", { name: "History", exact: true }).click();
  await expect(page.locator("#history-list")).toContainText(
    `${alpha.name} initial`,
  );
  const pending = deferred();
  const arrived = deferred();
  const completed = deferred();
  let body;
  await page.route(
    `**/api/gpos/${alpha.guid}/revisions/1/restore`,
    async (route) => {
      body = route.request().postDataJSON();
      const response = await route.fetch();
      expect(response.status()).toBe(200);
      arrived.resolve();
      await pending.promise;
      await route.fulfill({ response });
      completed.resolve();
    },
  );
  try {
    page.once("dialog", (dialog) => dialog.accept());
    await page.locator('[data-restore="1"]').click();
    await arrived.promise;
    expect(body.expected_revision).toBe(alpha.revision);
    await selectPolicy(page, beta);
    await expect(page.locator("#history-list")).toContainText(
      `${beta.name} second`,
    );
    pending.resolve();
    await completed.promise;
    await flushUi(page);
    await expect(page.locator("#title")).toHaveText(beta.name);
    await expect(page.locator("#revision")).toHaveText("Revision 2");
    await expect(page.locator("#history-list")).toContainText(
      `${beta.name} second`,
    );
    await expect(page.locator("#history-list")).not.toContainText(alpha.name);
    const restoredAlpha = (
      await (await request.get(`/api/gpos/${alpha.guid}`)).json()
    ).gpo;
    expect(restoredAlpha.revision).toBe(3);
    expect(restoredAlpha.description).toBe("");
    expect(
      (await (await request.get(`/api/gpos/${beta.guid}`)).json()).gpo.revision,
    ).toBe(2);
  } finally {
    pending.resolve();
  }
});

test("a late new-policy response preserves a metadata editor and its original save target", async ({
  page,
  request,
}) => {
  const alpha = await seedPolicy(request, "Create Race Alpha");
  const betaName = `Synthetic Created Beta ${Date.now()}`;
  const betaDescription = "Synthetic original Beta description";
  await page.goto("/");
  await selectPolicy(page, alpha);
  const pending = deferred();
  const arrived = deferred();
  const completed = deferred();
  let createdBeta;
  await page.route("**/api/gpos", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const response = await route.fetch();
    createdBeta = (await response.json()).gpo;
    arrived.resolve(response.status());
    await pending.promise;
    await route.fulfill({ response });
    completed.resolve();
  });
  try {
    await page.locator("#new-gpo").click();
    const dialog = page.locator("#gpo-dialog");
    await dialog.getByLabel("Name", { exact: true }).fill(betaName);
    await dialog.getByLabel("Description").fill(betaDescription);
    await dialog.getByRole("button", { name: "Create policy" }).click();
    expect(await arrived.promise).toBe(201);
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.locator("#edit-metadata").click();
    const renamed = `${alpha.name} renamed`;
    const description = "Synthetic metadata retained while Beta finishes";
    await dialog.getByLabel("Name", { exact: true }).fill(renamed);
    await dialog.getByLabel("Description").fill(description);
    pending.resolve();
    await completed.promise;
    await expect(
      dialog.getByRole("button", { name: "Save changes" }),
    ).toBeEnabled();
    await flushUi(page);
    await expect(page.locator("#title")).toHaveText(alpha.name);
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByRole("heading", { name: "Edit policy details" }),
    ).toBeVisible();
    await expect(dialog.getByLabel("Name", { exact: true })).toHaveValue(
      renamed,
    );
    await expect(dialog.getByLabel("Description")).toHaveValue(description);
    const saving = page.waitForRequest((sent) => sent.method() === "PATCH");
    await dialog.getByRole("button", { name: "Save changes" }).click();
    const sent = await saving;
    expect(sent.url()).toContain(`/api/gpos/${alpha.guid}`);
    expect(sent.postDataJSON()).toMatchObject({
      expected_revision: 1,
      name: renamed,
      description,
    });
    await expect(dialog).toBeHidden();
    await expect(page.locator("#title")).toHaveText(renamed);
    const savedAlpha = (
      await (await request.get(`/api/gpos/${alpha.guid}`)).json()
    ).gpo;
    expect(savedAlpha).toMatchObject({
      name: renamed,
      description,
      revision: 2,
    });
    const savedBeta = (
      await (await request.get(`/api/gpos/${createdBeta.guid}`)).json()
    ).gpo;
    expect(savedBeta).toMatchObject({
      name: betaName,
      description: betaDescription,
      revision: 1,
    });
  } finally {
    pending.resolve();
  }
});

test("refuses to save an editor whose policy changed during delayed navigation and allows a fresh edit", async ({
  page,
  request,
}) => {
  const alpha = await seedPolicy(request, "Selection Race Alpha");
  const beta = await seedPolicy(request, "Selection Race Beta");
  await page.goto("/");
  await selectPolicy(page, alpha);
  const pending = deferred();
  const arrived = deferred();
  const completed = deferred();
  const patches = [];
  page.on("request", (sent) => {
    if (sent.method() === "PATCH") patches.push(sent);
  });
  await page.route(`**/api/gpos/${beta.guid}`, async (route) => {
    const response = await route.fetch();
    arrived.resolve(response.status());
    await pending.promise;
    await route.fulfill({ response });
    completed.resolve();
  });
  try {
    await page.locator(`[data-guid="${beta.guid}"]`).click();
    expect(await arrived.promise).toBe(200);
    await expect(page.locator("#title")).toHaveText(alpha.name);
    await page.locator("#edit-metadata").click();
    const dialog = page.locator("#gpo-dialog");
    await expect(dialog.getByLabel("Name", { exact: true })).toHaveValue(
      alpha.name,
    );
    await dialog
      .getByLabel("Name", { exact: true })
      .fill(`${alpha.name} unsaved`);
    await dialog
      .getByLabel("Description")
      .fill("Synthetic edit belongs to Alpha");
    pending.resolve();
    await completed.promise;
    await expect(page.locator("#title")).toHaveText(beta.name);
    await dialog.getByRole("button", { name: "Save changes" }).click();
    await expect(dialog.locator(".form-error")).toContainText(
      "The selected policy changed. Close and reopen this editor before saving.",
    );
    await flushUi(page);
    expect(patches).toHaveLength(0);
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel("Name", { exact: true })).toHaveValue(
      `${alpha.name} unsaved`,
    );
    await expect(page.locator("#title")).toHaveText(beta.name);
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await selectPolicy(page, alpha);
    await page.locator("#edit-metadata").click();
    await expect(dialog.getByLabel("Name", { exact: true })).toHaveValue(
      alpha.name,
    );
    const renamed = `${alpha.name} safely renamed`;
    const description = "Synthetic fresh Alpha edit";
    await dialog.getByLabel("Name", { exact: true }).fill(renamed);
    await dialog.getByLabel("Description").fill(description);
    await dialog.getByRole("button", { name: "Save changes" }).click();
    await expect(dialog).toBeHidden();
    await expect(page.locator("#title")).toHaveText(renamed);
    expect(patches).toHaveLength(1);
    expect(patches[0].url()).toContain(`/api/gpos/${alpha.guid}`);
    expect(patches[0].postDataJSON()).toMatchObject({
      expected_revision: 1,
      name: renamed,
      description,
    });
    const savedAlpha = (
      await (await request.get(`/api/gpos/${alpha.guid}`)).json()
    ).gpo;
    expect(savedAlpha).toMatchObject({
      name: renamed,
      description,
      revision: 2,
    });
    const savedBeta = (
      await (await request.get(`/api/gpos/${beta.guid}`)).json()
    ).gpo;
    expect(savedBeta).toMatchObject({
      name: beta.name,
      description: "",
      revision: 1,
    });
  } finally {
    pending.resolve();
  }
});
