import { expect, test } from "@playwright/test";

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
async function seed(request, suffix) {
  const response = await request.post("/api/gpos", {
    data: {
      name: `Synthetic Fresh Read ${suffix} ${Date.now()}`,
      actor: "browser-test",
      reason: "Synthetic detail freshness",
    },
  });
  expect(response.status()).toBe(201);
  return (await response.json()).gpo;
}
async function select(page, gpo) {
  await page.locator(`[data-guid="${gpo.guid}"]`).click();
  await expect(page.locator("#title")).toHaveText(gpo.name);
}
async function openSetting(page) {
  await page.getByRole("tab", { name: /Policy settings/ }).click();
  await page.locator("#add-setting").click();
  const dialog = page.locator("#setting-dialog");
  await expect(dialog.getByLabel("Configuration")).toBeFocused();
  await dialog
    .getByLabel("Registry key")
    .fill("Software\\Policies\\SyntheticReadFreshness");
  await dialog.getByLabel("Value name").fill("FreshValue");
  await dialog.locator('[name="value"]').fill("Committed revision two");
  return dialog;
}
async function flushUi(page) {
  await page.evaluate(
    () => new Promise((resolve) => globalThis.requestAnimationFrame(resolve)),
  );
}

test("a held explicit full read cannot erase a newer visible mutation @smoke", async ({
  page,
  request,
}) => {
  const gpo = await seed(request, "Current");
  await page.goto("/");
  await select(page, gpo);
  const release = deferred(),
    arrived = deferred(),
    completed = deferred();
  let reads = 0;
  await page.route(`**/api/gpos/${gpo.guid}`, async (route) => {
    if (route.request().method() !== "GET" || ++reads !== 1)
      return route.continue();
    const response = await route.fetch();
    arrived.resolve((await response.json()).gpo.revision);
    await release.promise;
    await route.fulfill({ response });
    completed.resolve();
  });
  try {
    await page.locator(`[data-guid="${gpo.guid}"]`).click();
    expect(await arrived.promise).toBe(1);
    const editor = await openSetting(page);
    await editor.getByRole("button", { name: "Save setting" }).click();
    await expect(editor).toBeHidden();
    await expect(page.locator("#revision")).toHaveText("Revision 2");
    release.resolve();
    await completed.promise;
    await flushUi(page);
    const saved = (await (await request.get(`/api/gpos/${gpo.guid}`)).json())
      .gpo;
    console.log(
      "FULL_READ_FRESHNESS",
      JSON.stringify({
        visible: await page.locator("#revision").textContent(),
        summary: await page.locator(`[data-guid="${gpo.guid}"]`).textContent(),
        storedRevision: saved.revision,
        storedSettings: saved.settings.length,
      }),
    );
    expect(saved.revision).toBe(2);
    expect(saved.settings[0].value_name).toBe("FreshValue");
    await expect(page.locator("#revision")).toHaveText("Revision 2");
    await expect(page.locator("#settings-table")).toContainText("FreshValue");
    expect(reads).toBe(1);
  } finally {
    release.resolve();
  }
});

async function backgroundSummary(
  page,
  request,
  suffix,
  { retryFailure = false, cachedRetry = false } = {},
) {
  const alpha = await seed(request, `${suffix} Alpha`);
  const beta = await seed(request, `${suffix} Beta`);
  const gamma = await seed(request, `${suffix} Gamma`);
  await page.goto("/");
  await select(page, alpha);
  const editor = await openSetting(page);
  const commit = deferred(),
    mutationArrived = deferred(),
    mutationDone = deferred();
  let writes = 0;
  await page.route(`**/api/gpos/${alpha.guid}/settings`, async (route) => {
    writes++;
    mutationArrived.resolve();
    await commit.promise;
    const response = await route.fetch();
    expect(response.status()).toBe(201);
    await route.fulfill({ response });
    mutationDone.resolve();
  });
  await editor.getByRole("button", { name: "Save setting" }).click();
  await mutationArrived.promise;
  await editor.getByRole("button", { name: "Cancel", exact: true }).click();
  await select(page, beta);

  const firstRelease = deferred(),
    firstArrived = deferred(),
    firstDone = deferred();
  const retryRelease = deferred(),
    retryArrived = deferred(),
    retryDone = deferred();
  let reads = 0,
    original;
  await page.route(`**/api/gpos/${alpha.guid}`, async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    reads++;
    const response = await route.fetch();
    const body = await response.json();
    if (reads === 1) {
      original = body;
      firstArrived.resolve(body);
      await firstRelease.promise;
      await route.fulfill({ response });
      firstDone.resolve();
    } else if (reads === 2) {
      retryArrived.resolve(body);
      await retryRelease.promise;
      if (retryFailure)
        await route.fulfill({
          status: 503,
          json: { error: { message: "Synthetic authoritative retry failure" } },
        });
      else if (cachedRetry)
        // Deliberately replay the first real snapshot to model a stale read cache.
        await route.fulfill({ response, json: original });
      else await route.fulfill({ response });
      retryDone.resolve();
    } else await route.fulfill({ response });
  });
  await page.locator(`[data-guid="${alpha.guid}"]`).click();
  expect((await firstArrived.promise).gpo.revision).toBe(1);
  commit.resolve();
  await mutationDone.promise;
  await expect(page.locator(`[data-guid="${alpha.guid}"]`)).toContainText("r2");
  await expect(page.locator("#title")).toHaveText(beta.name);
  return {
    alpha,
    beta,
    gamma,
    firstDone: firstDone.promise,
    retryArrived: retryArrived.promise,
    retryDone: retryDone.promise,
    releaseFirst: () => firstRelease.resolve(),
    releaseRetry: () => retryRelease.resolve(),
    reads: () => reads,
    writes: () => writes,
    releaseAll: () => {
      commit.resolve();
      firstRelease.resolve();
      retryRelease.resolve();
    },
  };
}

async function metadataDraft(page) {
  await page.getByRole("tab", { name: "Overview", exact: true }).click();
  await page.locator("#edit-metadata").click();
  const dialog = page.locator("#gpo-dialog");
  await expect(dialog.getByLabel("Name", { exact: true })).toBeFocused();
  await dialog
    .getByLabel("Name", { exact: true })
    .fill("Synthetic newer editor draft");
  await dialog
    .getByLabel("Description")
    .fill("Keep this new editor description");
  return dialog;
}

async function expectMetadataDraft(dialog) {
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Name", { exact: true })).toHaveValue(
    "Synthetic newer editor draft",
  );
  await expect(dialog.getByLabel("Description")).toHaveValue(
    "Keep this new editor description",
  );
}

test("a summary-only newer target is selected through one fresh authoritative reread", async ({
  page,
  request,
}) => {
  const pending = await backgroundSummary(page, request, "Summary");
  try {
    pending.releaseFirst();
    expect((await pending.retryArrived).gpo.revision).toBe(2);
    pending.releaseRetry();
    await pending.retryDone;
    await expect(page.locator("#title")).toHaveText(pending.alpha.name);
    await expect(page.locator("#revision")).toHaveText("Revision 2");
    await expect(page.locator("#settings-table")).toContainText("FreshValue");
    expect(pending.reads()).toBe(2);
    expect(pending.writes()).toBe(1);
  } finally {
    pending.releaseAll();
  }
});

for (const retryFailure of [false, true]) {
  test(`a newer editor retires an authoritative reread's ${retryFailure ? "failure" : "success"}`, async ({
    page,
    request,
  }) => {
    const pending = await backgroundSummary(
      page,
      request,
      `Editor ${retryFailure}`,
      { retryFailure },
    );
    try {
      pending.releaseFirst();
      await pending.retryArrived;
      const dialog = await metadataDraft(page);
      pending.releaseRetry();
      await pending.retryDone;
      await flushUi(page);
      await expect(page.locator("#title")).toHaveText(pending.beta.name);
      await expectMetadataDraft(dialog);
      await expect(page.locator("#error-summary")).toBeHidden();
      expect(pending.reads()).toBe(2);
      expect(pending.writes()).toBe(1);
    } finally {
      pending.releaseAll();
    }
  });
}

test("a newer explicit selection retires an authoritative reread", async ({
  page,
  request,
}) => {
  const pending = await backgroundSummary(page, request, "Selection");
  try {
    pending.releaseFirst();
    await pending.retryArrived;
    await select(page, pending.gamma);
    pending.releaseRetry();
    await pending.retryDone;
    await flushUi(page);
    await expect(page.locator("#title")).toHaveText(pending.gamma.name);
    await expect(page.locator("#revision")).toHaveText("Revision 1");
    expect(pending.reads()).toBe(2);
    expect(pending.writes()).toBe(1);
  } finally {
    pending.releaseAll();
  }
});

test("a stale response does not start a recovery read after a newer editor opens", async ({
  page,
  request,
}) => {
  const pending = await backgroundSummary(page, request, "Before Recovery");
  try {
    const dialog = await metadataDraft(page);
    pending.releaseFirst();
    await pending.firstDone;
    await flushUi(page);
    await expect(page.locator("#title")).toHaveText(pending.beta.name);
    await expectMetadataDraft(dialog);
    expect(pending.reads()).toBe(1);
    expect(pending.writes()).toBe(1);
  } finally {
    pending.releaseAll();
  }
});

test("a current authoritative reread failure stays visible without selecting stale content", async ({
  page,
  request,
}) => {
  const pending = await backgroundSummary(page, request, "Failure", {
    retryFailure: true,
  });
  try {
    pending.releaseFirst();
    await pending.retryArrived;
    pending.releaseRetry();
    await pending.retryDone;
    await expect(page.locator("#error-summary")).toContainText(
      "Synthetic authoritative retry failure",
    );
    await expect(page.locator("#title")).toHaveText(pending.beta.name);
    await expect(
      page.locator(`[data-guid="${pending.alpha.guid}"]`),
    ).toContainText("r2");
    expect(pending.reads()).toBe(2);
    expect(pending.writes()).toBe(1);
    expect(
      (await (await request.get(`/api/gpos/${pending.alpha.guid}`)).json()).gpo
        .revision,
    ).toBe(2);
  } finally {
    pending.releaseAll();
  }
});

test("a repeated cached snapshot fails visibly after one retry rather than looping", async ({
  page,
  request,
}) => {
  const pending = await backgroundSummary(page, request, "Bounded", {
    cachedRetry: true,
  });
  try {
    pending.releaseFirst();
    await pending.retryArrived;
    pending.releaseRetry();
    await pending.retryDone;
    await expect(page.locator("#error-summary")).toContainText(
      "changed again while it was loading",
    );
    await expect(page.locator("#title")).toHaveText(pending.beta.name);
    expect(pending.reads()).toBe(2);
    expect(pending.writes()).toBe(1);
    expect(
      (await (await request.get(`/api/gpos/${pending.alpha.guid}`)).json()).gpo
        .settings,
    ).toHaveLength(1);
  } finally {
    pending.releaseAll();
  }
});
