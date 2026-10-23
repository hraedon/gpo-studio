import { expect, test } from "@playwright/test";

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

async function seedStarter(request, suffix = "Source") {
  const response = await request.post("/api/starter-gpos", {
    data: {
      name: `Synthetic Starter ${suffix} ${Date.now()}`,
      description: "Synthetic starter context fixture",
      actor: "browser-test",
      reason: "Seed synthetic starter",
    },
  });
  expect(response.status()).toBe(201);
  return (await response.json()).gpo;
}

async function openList(page) {
  await page.goto("/");
  await expect(page.locator("#app-status")).toBeHidden();
  await page.locator("#open-starter-gpos").click();
  await expect(page.locator("#starter-list")).not.toContainText("Loading…");
  return page.locator("#starter-list-dialog");
}

async function openCreate(page) {
  await page.locator("#create-starter-gpo").click();
  const dialog = page.locator("#starter-gpo-dialog");
  await expect(dialog.getByLabel("Name", { exact: true })).toBeFocused();
  return dialog;
}

async function hold(page, pattern, method = "POST", failure = false) {
  const pending = deferred(),
    arrived = deferred(),
    completed = deferred();
  let count = 0;
  await page.route(pattern, async (route) => {
    if (route.request().method() !== method) return route.continue();
    count++;
    if (failure) {
      arrived.resolve({ status: 503 });
      await pending.promise;
      await route.fulfill({
        status: 503,
        json: { error: { message: "Synthetic Starter server failure" } },
      });
    } else {
      const response = await route.fetch();
      arrived.resolve({
        status: response.status(),
        body: response.status() === 204 ? null : await response.json(),
        request: route.request().postDataJSON(),
      });
      await pending.promise;
      await route.fulfill({ response });
    }
    completed.resolve();
  });
  return {
    arrived: arrived.promise,
    completed: completed.promise,
    release: () => pending.resolve(),
    count: () => count,
  };
}

async function flushUi(page) {
  await page.evaluate(
    () => new Promise((resolve) => globalThis.requestAnimationFrame(resolve)),
  );
}

for (const failure of [false, true]) {
  test(`a late ${failure ? "failed" : "successful"} Starter creation leaves a reopened draft intact`, async ({
    page,
    request,
  }) => {
    await openList(page);
    const dialog = await openCreate(page);
    const firstName = `Synthetic Original Starter ${Date.now()}`;
    await dialog.getByLabel("Name", { exact: true }).fill(firstName);
    const pending = await hold(page, "**/api/starter-gpos", "POST", failure);
    try {
      await dialog
        .getByRole("button", { name: "Create starter", exact: true })
        .click();
      expect((await pending.arrived).status).toBe(failure ? 503 : 201);
      await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
      await openCreate(page);
      await dialog
        .getByLabel("Name", { exact: true })
        .fill("Synthetic Reopened Starter Draft");
      await dialog
        .getByLabel("Description")
        .fill("Keep this unfinished description");
      pending.release();
      await pending.completed;
      await flushUi(page);
      await expect(dialog).toBeVisible();
      await expect(dialog.getByLabel("Name", { exact: true })).toHaveValue(
        "Synthetic Reopened Starter Draft",
      );
      await expect(dialog.getByLabel("Description")).toHaveValue(
        "Keep this unfinished description",
      );
      await expect(dialog.locator(".form-error")).toHaveCount(0);
      await expect(
        dialog.getByRole("button", { name: "Create starter", exact: true }),
      ).toBeEnabled();
      await expect(page.locator("#gpo-list")).not.toContainText(firstName);
      const list = await (await request.get("/api/starter-gpos")).json();
      expect(list.items.some((item) => item.name === firstName)).toBe(!failure);
      if (failure)
        await expect(page.locator("#error-summary")).toContainText(
          "Synthetic Starter server failure",
        );
    } finally {
      pending.release();
    }
  });
}

test("Starter creation prevents duplicate submits, preserves values on failure, and retries without adding a sidebar policy", async ({
  page,
  request,
}) => {
  await openList(page);
  const dialog = await openCreate(page);
  const name = `Synthetic Retried Starter ${Date.now()}`;
  await dialog.getByLabel("Name", { exact: true }).fill(name);
  await dialog
    .getByLabel("Description")
    .fill("Preserve this Starter description");
  await dialog.getByLabel("Template version").fill("synthetic-v1");
  const pending = await hold(page, "**/api/starter-gpos", "POST", true);
  try {
    await dialog
      .getByRole("button", { name: "Create starter", exact: true })
      .click();
    await pending.arrived;
    await expect(dialog.getByLabel("Name", { exact: true })).toBeDisabled();
    await expect(dialog.locator(".form-pending")).toContainText(
      "keeps the save running",
    );
    await dialog.getByLabel("Name", { exact: true }).press("Enter");
    await page
      .locator("#starter-gpo-form")
      .evaluate((form) => form.requestSubmit());
    expect(pending.count()).toBe(1);
    await expect(
      dialog.getByRole("button", { name: "Cancel", exact: true }),
    ).toBeEnabled();
    pending.release();
    await pending.completed;
    await expect(dialog.locator(".form-error")).toContainText(
      "Synthetic Starter server failure",
    );
    await expect(dialog.getByLabel("Name", { exact: true })).toHaveValue(name);
    await expect(dialog.getByLabel("Description")).toHaveValue(
      "Preserve this Starter description",
    );
    await expect(
      dialog.getByRole("button", { name: "Create starter", exact: true }),
    ).toBeEnabled();
    await expect(dialog.locator(".form-pending")).toHaveCount(0);
    await page.unroute("**/api/starter-gpos");
    await dialog
      .getByRole("button", { name: "Create starter", exact: true })
      .click();
    await expect(dialog).toBeHidden();
    await expect(page.locator("#starter-list")).toContainText(name);
    await expect(page.locator("#gpo-list")).not.toContainText(name);
    const saved = (
      await (await request.get("/api/starter-gpos")).json()
    ).items.find((item) => item.name === name);
    expect(saved.template_version).toBe("synthetic-v1");
  } finally {
    pending.release();
  }
});

for (const failure of [false, true]) {
  test(`a late ${failure ? "failed" : "successful"} derivation preserves another source's reopened draft`, async ({
    page,
    request,
  }) => {
    const first = await seedStarter(request, "First");
    const second = await seedStarter(request, "Second");
    await openList(page);
    const title = await page.locator("#title").textContent();
    const sidebar = await page
      .locator(".gpo-item")
      .evaluateAll((items) => items.map((item) => item.dataset.guid));
    await page.locator(`[data-derive-starter="${first.guid}"]`).click();
    const dialog = page.locator("#derive-dialog");
    await expect(dialog.getByLabel("New GPO name")).toBeFocused();
    const derivedName = `Synthetic First Derivation ${Date.now()}`;
    await dialog.getByLabel("New GPO name").fill(derivedName);
    await dialog
      .getByLabel("Change reason")
      .fill("Synthetic source-specific reason");
    const pending = await hold(
      page,
      `**/api/starter-gpos/${first.guid}/derive`,
      "POST",
      failure,
    );
    try {
      await dialog
        .getByRole("button", { name: "Derive GPO", exact: true })
        .click();
      const response = await pending.arrived;
      expect(response.status).toBe(failure ? 503 : 201);
      if (!failure) {
        expect(response.request).toEqual({
          name: derivedName,
          actor: "local-operator",
          reason: "Synthetic source-specific reason",
        });
        expect(response.body.gpo.is_starter).toBe(false);
        expect(response.body.gpo.source_guid).toBe(first.guid);
      }
      await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
      await page.locator(`[data-derive-starter="${second.guid}"]`).click();
      await expect(dialog.getByLabel("New GPO name")).toBeFocused();
      await dialog
        .getByLabel("New GPO name")
        .fill("Synthetic Other Source Draft");
      await dialog.getByLabel("Change reason").fill("Keep this new reason");
      pending.release();
      await pending.completed;
      await flushUi(page);
      await expect(dialog).toBeVisible();
      await expect(page.locator("#starter-list-dialog")).toBeVisible();
      await expect(dialog.getByLabel("New GPO name")).toHaveValue(
        "Synthetic Other Source Draft",
      );
      await expect(dialog.getByLabel("Change reason")).toHaveValue(
        "Keep this new reason",
      );
      await expect(dialog.locator('[name="source_guid"]')).toHaveValue(
        second.guid,
      );
      await expect(dialog.locator(".form-error")).toHaveCount(0);
      await expect(
        dialog.getByRole("button", { name: "Derive GPO", exact: true }),
      ).toBeEnabled();
      await expect(page.locator("#title")).toHaveText(title);
      if (!failure)
        await expect(page.locator("#gpo-list")).toContainText(derivedName);
      else
        await expect(page.locator("#error-summary")).toContainText(
          "Synthetic Starter server failure",
        );
      const after = await page
        .locator(".gpo-item")
        .evaluateAll((items) => items.map((item) => item.dataset.guid));
      expect(after.sort()).toEqual(
        [...sidebar, ...(!failure ? [response.body.gpo.guid] : [])].sort(),
      );
    } finally {
      pending.release();
    }
  });
}

test("a reopened derivation starts independently and an old completion cannot unlock it", async ({
  page,
  request,
}) => {
  const first = await seedStarter(request, "Overlapping First");
  const second = await seedStarter(request, "Overlapping Second");
  await openList(page);
  await page.locator(`[data-derive-starter="${first.guid}"]`).click();
  const dialog = page.locator("#derive-dialog");
  await expect(dialog.getByLabel("New GPO name")).toBeFocused();
  const firstName = `Synthetic Earlier Draft ${Date.now()}`;
  const secondName = `Synthetic Newer Draft ${Date.now()}`;
  await dialog.getByLabel("New GPO name").fill(firstName);
  const older = await hold(page, `**/api/starter-gpos/${first.guid}/derive`);
  const newer = await hold(page, `**/api/starter-gpos/${second.guid}/derive`);
  try {
    await dialog
      .getByRole("button", { name: "Derive GPO", exact: true })
      .click();
    expect((await older.arrived).status).toBe(201);
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.locator(`[data-derive-starter="${second.guid}"]`).click();
    await expect(dialog.getByLabel("New GPO name")).toBeFocused();
    await expect(dialog.getByLabel("New GPO name")).toBeEnabled();
    await dialog.getByLabel("New GPO name").fill(secondName);
    await dialog
      .getByRole("button", { name: "Derive GPO", exact: true })
      .click();
    expect((await newer.arrived).status).toBe(201);
    await page.locator("#derive-form").evaluate((form) => form.requestSubmit());
    expect(newer.count()).toBe(1);
    older.release();
    await older.completed;
    await expect(page.locator("#gpo-list")).toContainText(firstName);
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel("New GPO name")).toHaveValue(secondName);
    await expect(dialog.getByLabel("New GPO name")).toBeDisabled();
    await expect(
      dialog.getByRole("button", { name: "Derive GPO", exact: true }),
    ).toBeDisabled();
    await expect(dialog.locator(".form-pending")).toHaveCount(1);
    newer.release();
    await newer.completed;
    await expect(dialog).toBeHidden();
    await expect(page.locator("#starter-list-dialog")).toBeHidden();
    await expect(page.locator("#title")).toHaveText(secondName);
    await expect(page.locator("#gpo-list")).toContainText(firstName);
    await expect(page.locator("#gpo-list")).toContainText(secondName);
  } finally {
    older.release();
    newer.release();
  }
});

for (const failure of [false, true]) {
  test(`an older Starter listing ${failure ? "failure" : "snapshot"} cannot replace a reopened list`, async ({
    page,
    request,
  }) => {
    await seedStarter(request, "Old List");
    await page.goto("/");
    await expect(page.locator("#app-status")).toBeHidden();
    const pending = deferred(),
      arrived = deferred(),
      completed = deferred();
    let requests = 0;
    await page.route("**/api/starter-gpos", async (route) => {
      if (route.request().method() !== "GET" || ++requests !== 1)
        return route.continue();
      const response = failure ? null : await route.fetch();
      arrived.resolve();
      await pending.promise;
      if (failure)
        await route.fulfill({
          status: 503,
          json: { error: { message: "Synthetic old listing failure" } },
        });
      else await route.fulfill({ response });
      completed.resolve();
    });
    try {
      await page.locator("#open-starter-gpos").click();
      await arrived.promise;
      const dialog = page.locator("#starter-list-dialog");
      await dialog
        .locator(".dialog-actions")
        .getByRole("button", { name: "Close", exact: true })
        .click();
      const fresh = await seedStarter(request, "Fresh List");
      await page.locator("#open-starter-gpos").click();
      await expect(page.locator("#starter-list")).toContainText(fresh.name);
      pending.resolve();
      await completed.promise;
      await flushUi(page);
      await expect(dialog).toBeVisible();
      await expect(page.locator("#starter-list")).toContainText(fresh.name);
      await expect(page.locator("#starter-list")).not.toContainText(
        "Synthetic old listing failure",
      );
      expect(requests).toBe(2);
    } finally {
      pending.resolve();
    }
  });
}

for (const reopen of [false, true]) {
  test(`a delayed Starter deletion never reopens or refreshes a ${reopen ? "newer" : "closed"} list`, async ({
    page,
    request,
  }) => {
    const removed = await seedStarter(request, "Delete");
    const survivor = await seedStarter(request, "Survivor");
    const dialog = await openList(page);
    let listingRequests = 0;
    page.on("request", (sent) => {
      if (sent.method() === "GET" && sent.url().endsWith("/api/starter-gpos"))
        listingRequests++;
    });
    const pending = await hold(
      page,
      `**/api/starter-gpos/${removed.guid}`,
      "DELETE",
    );
    try {
      const confirmed = page.waitForEvent("dialog");
      const clicking = page
        .locator(`[data-delete-starter="${removed.guid}"]`)
        .click();
      const confirmation = await confirmed;
      expect(confirmation.message()).toContain(removed.name);
      await confirmation.accept();
      await clicking;
      const response = await pending.arrived;
      expect(response.status).toBe(204);
      expect(response.request).toEqual({
        actor: "local-operator",
        reason: "Delete starter GPO",
        expected_revision: removed.revision,
      });
      await dialog
        .locator(".dialog-actions")
        .getByRole("button", { name: "Close", exact: true })
        .click();
      if (reopen) {
        await page.locator("#open-starter-gpos").click();
        await expect(page.locator("#starter-list")).toContainText(
          survivor.name,
        );
        await expect(page.locator("#starter-list")).not.toContainText(
          removed.name,
        );
      }
      const before = listingRequests;
      pending.release();
      await pending.completed;
      await flushUi(page);
      if (reopen) await expect(dialog).toBeVisible();
      else await expect(dialog).toBeHidden();
      expect(listingRequests).toBe(before);
      expect(
        (await (await request.get("/api/starter-gpos")).json()).items.some(
          (item) => item.guid === removed.guid,
        ),
      ).toBe(false);
    } finally {
      pending.release();
    }
  });
}
