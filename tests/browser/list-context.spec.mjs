import { expect, test } from "@playwright/test";

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

async function seed(request, label) {
  const response = await request.post("/api/gpos", {
    data: {
      name: `Synthetic List ${label} ${Date.now()}`,
      actor: "browser-test",
      reason: "Seed list response context regression",
    },
  });
  expect(response.status()).toBe(201);
  return (await response.json()).gpo;
}

async function select(page, gpo) {
  await page.locator(`[data-guid="${gpo.guid}"]`).click();
  await expect(page.locator("#title")).toHaveText(gpo.name);
}

async function flush(page) {
  await page.evaluate(
    () => new Promise((resolve) => globalThis.requestAnimationFrame(resolve)),
  );
}

for (const phase of ["list", "policy"]) {
  test(`a post-create ${phase} response preserves a subsequently opened metadata editor @smoke`, async ({
    page,
    request,
  }) => {
    const alpha = await seed(request, "Editor Alpha");
    await page.goto("/");
    await select(page, alpha);
    const held = deferred();
    const arrived = deferred();
    const completed = deferred();
    await page.route(
      phase === "list" ? "**/api/gpos" : "**/api/gpos/*",
      async (route) => {
        if (route.request().method() !== "GET") return route.continue();
        const response = await route.fetch();
        arrived.resolve();
        await held.promise;
        await route.fulfill({ response });
        completed.resolve();
      },
    );
    try {
      await page.locator("#new-gpo").click();
      const dialog = page.locator("#gpo-dialog");
      await dialog
        .getByLabel("Name", { exact: true })
        .fill(`Synthetic Created List Beta ${Date.now()}`);
      await dialog.getByRole("button", { name: "Create policy" }).click();
      await arrived.promise;
      await expect(dialog).toBeHidden();
      await page.locator("#edit-metadata").click();
      const renamed = `${alpha.name} retained`;
      await dialog.getByLabel("Name", { exact: true }).fill(renamed);
      held.resolve();
      await completed.promise;
      await flush(page);
      await expect(page.locator("#title")).toHaveText(alpha.name);
      await expect(dialog).toBeVisible();
      await expect(dialog.getByLabel("Name", { exact: true })).toHaveValue(
        renamed,
      );
      const saving = page.waitForRequest((sent) => sent.method() === "PATCH");
      await dialog.getByRole("button", { name: "Save changes" }).click();
      const sent = await saving;
      expect(sent.url()).toContain(`/api/gpos/${alpha.guid}`);
      expect(sent.postDataJSON().expected_revision).toBe(alpha.revision);
      await expect(dialog).toBeHidden();
      await expect(page.locator("#title")).toHaveText(renamed);
    } finally {
      held.resolve();
    }
  });
}

test("a held list retains a background creation learned after its snapshot, and a fresh list can remove it", async ({
  page,
  request,
}) => {
  const alpha = await seed(request, "Background Alpha");
  await page.goto("/");
  await select(page, alpha);
  const createHeld = deferred();
  const createArrived = deferred();
  const createCompleted = deferred();
  const listHeld = deferred();
  const listArrived = deferred();
  let created;
  let holdList = true;
  await page.route("**/api/gpos", async (route) => {
    if (route.request().method() === "POST") {
      createArrived.resolve();
      await createHeld.promise;
      const response = await route.fetch();
      expect(response.status()).toBe(201);
      created = (await response.json()).gpo;
      await route.fulfill({ response });
      createCompleted.resolve();
    } else if (holdList) {
      holdList = false;
      const response = await route.fetch();
      listArrived.resolve();
      await listHeld.promise;
      await route.fulfill({ response });
    } else {
      const response = await route.fetch();
      const data = await response.json();
      // A new authoritative listing may legitimately remove a formerly known
      // object. The old snapshot exception must not become a permanent cache.
      data.items = data.items.filter((gpo) => gpo.guid !== created.guid);
      await route.fulfill({ response, json: data });
    }
  });
  try {
    await page.locator("#new-gpo").click();
    const dialog = page.locator("#gpo-dialog");
    await dialog
      .getByLabel("Name", { exact: true })
      .fill(`Synthetic Background List Gamma ${Date.now()}`);
    await dialog.getByRole("button", { name: "Create policy" }).click();
    await createArrived.promise;
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.evaluate(() => {
      globalThis.listRefresh = import("/assets/js/render.mjs").then(
        ({ loadList }) => loadList(),
      );
    });
    await listArrived.promise;
    createHeld.resolve();
    await createCompleted.promise;
    await expect(page.locator(`[data-guid="${created.guid}"]`)).toBeVisible();
    listHeld.resolve();
    await page.evaluate(() => globalThis.listRefresh);
    await expect(page.locator(`[data-guid="${created.guid}"]`)).toBeVisible();
    await expect(page.locator("#title")).toHaveText(alpha.name);
    await page.evaluate(async () => {
      const { loadList } = await import("/assets/js/render.mjs");
      await loadList();
    });
    await expect(page.locator(`[data-guid="${created.guid}"]`)).toHaveCount(0);
    await expect(page.locator("#title")).toHaveText(alpha.name);
  } finally {
    createHeld.resolve();
    listHeld.resolve();
  }
});

test("a fresh listing can remove the selected policy and return to an empty workspace", async ({
  page,
  request,
}) => {
  const alpha = await seed(request, "Removed Alpha");
  const beta = await seed(request, "Remaining Beta");
  await page.goto("/");
  await select(page, alpha);
  let empty = false;
  await page.route("**/api/gpos", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    data.items = empty
      ? []
      : data.items.filter((gpo) => gpo.guid === beta.guid);
    await route.fulfill({ response, json: data });
  });
  await page.evaluate(async () => {
    const { loadList } = await import("/assets/js/render.mjs");
    await loadList();
  });
  await expect(page.locator(`[data-guid="${alpha.guid}"]`)).toHaveCount(0);
  await expect(page.locator("#title")).toHaveText(beta.name);
  empty = true;
  await page.evaluate(async () => {
    const { loadList } = await import("/assets/js/render.mjs");
    await loadList();
  });
  await expect(page.locator(".gpo-item")).toHaveCount(0);
  await expect(page.locator("#workspace")).toBeHidden();
  await expect(page.locator("#title")).toHaveText("Choose a policy");
  expect(
    await page.evaluate(async () => {
      const { state } = await import("/assets/js/state.mjs");
      return state.current;
    }),
  ).toBeNull();
});
