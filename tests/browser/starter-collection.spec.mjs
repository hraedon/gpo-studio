import { expect, test } from "@playwright/test";

function deferred() {
  let resolve;
  const promise = new Promise((done) => (resolve = done));
  return { promise, resolve };
}
async function seed(request, starter = true) {
  const response = await request.post(
    starter ? "/api/starter-gpos" : "/api/gpos",
    {
      data: {
        name: `Synthetic collection ${starter ? "Starter" : "Policy"} ${Date.now()}`,
        actor: "browser-test",
        reason: "Seed synthetic collection member",
      },
    },
  );
  expect(response.status()).toBe(201);
  return (await response.json()).gpo;
}
async function select(page, gpo) {
  await page.locator(`[data-guid="${gpo.guid}"]`).click();
  await expect(page.locator("#title")).toHaveText(gpo.name);
}
async function open(page, gpo) {
  await page.goto("/");
  await expect(page.locator("#app-status")).toBeHidden();
  if (gpo) await select(page, gpo);
}
async function library(page) {
  await page.locator("#open-starter-gpos").click();
  await expect(page.locator("#starter-list")).not.toContainText("Loading…");
}
async function closeLibrary(page) {
  await page
    .locator("#starter-list-dialog .dialog-actions button[value='cancel']")
    .click();
}
async function remove(page, guid) {
  const confirmation = page.waitForEvent("dialog");
  const clicking = page.locator(`[data-delete-starter="${guid}"]`).click();
  await (await confirmation).accept();
  await clicking;
}
async function hold(page, pattern, method, before = false) {
  const release = deferred(),
    entered = deferred(),
    completed = deferred();
  let once = true;
  await page.route(pattern, async (route) => {
    if (route.request().method() !== method || !once) return route.continue();
    once = false;
    if (before) {
      entered.resolve();
      await release.promise;
    }
    const response = await route.fetch();
    if (!before) {
      entered.resolve(response);
      await release.promise;
    }
    await route.fulfill({ response });
    completed.resolve(response);
  });
  return {
    release: release.resolve,
    entered: entered.promise,
    completed: completed.promise,
  };
}
async function freshList(page) {
  await page.evaluate(async () => {
    const { loadList } = await import("/assets/js/render.mjs");
    await loadList(undefined, { select: false });
  });
}
async function recreate(request, original) {
  const response = await request.post("/api/estate/import", {
    data: {
      kind: "gpo-lens-estate",
      domain: "synthetic.example.test",
      gpos: [
        {
          guid: original.guid,
          display_name: `Synthetic recreated target ${original.guid}`,
          settings: [],
          links: [],
        },
      ],
    },
  });
  expect(response.status()).toBe(200);
  expect((await response.json()).imported).toBe(1);
  const current = await request.get(`/api/gpos/${original.guid}`);
  expect(current.status()).toBe(200);
  const gpo = (await current.json()).gpo;
  expect(gpo.revision).toBe(1);
  return gpo;
}

for (const kind of ["create", "delete"]) {
  test(`a before-server Starter ${kind} refreshes a reopened library`, async ({
    page,
    request,
  }) => {
    const ordinary = await seed(request, false);
    const removed = kind === "delete" ? await seed(request) : null;
    await open(page, ordinary);
    await library(page);
    const pending = await hold(
      page,
      kind === "create"
        ? "**/api/starter-gpos"
        : `**/api/starter-gpos/${removed.guid}`,
      kind === "create" ? "POST" : "DELETE",
      true,
    );
    let createdName;
    try {
      if (kind === "create") {
        await page.locator("#create-starter-gpo").click();
        createdName = `Synthetic before-server create ${Date.now()}`;
        await page.locator("#starter-gpo-form [name='name']").fill(createdName);
        await page.locator("#starter-gpo-form button[value='default']").click();
        await pending.entered;
        await page
          .locator("#starter-gpo-dialog .dialog-actions button[value='cancel']")
          .click();
      } else {
        await remove(page, removed.guid);
        await pending.entered;
      }
      await closeLibrary(page);
      await library(page);
      if (removed)
        await expect(
          page.locator(`[data-delete-starter="${removed.guid}"]`),
        ).toHaveCount(1);
      else
        await expect(page.locator("#starter-list")).not.toContainText(
          createdName,
        );
      pending.release();
      expect((await pending.completed).status()).toBe(
        kind === "create" ? 201 : 204,
      );
      if (removed) {
        await expect(
          page.locator(`[data-delete-starter="${removed.guid}"]`),
        ).toHaveCount(0);
        await expect(page.locator(`[data-guid="${removed.guid}"]`)).toHaveCount(
          0,
        );
      } else {
        await expect(page.locator("#starter-list")).toContainText(createdName);
        await expect(page.locator("#gpo-list")).toContainText(createdName);
      }
      await expect(page.locator("#starter-list-dialog")).toBeVisible();
      await expect(page.locator("#title")).toHaveText(ordinary.name);
    } finally {
      pending.release();
    }
  });
}

test("deletion refreshes collections without replacing an unrelated editor", async ({
  page,
  request,
}) => {
  const removed = await seed(request),
    ordinary = await seed(request, false);
  await open(page, ordinary);
  await library(page);
  const pending = await hold(
    page,
    `**/api/starter-gpos/${removed.guid}`,
    "DELETE",
    true,
  );
  try {
    await remove(page, removed.guid);
    await pending.entered;
    await closeLibrary(page);
    await page.locator("#edit-metadata").click();
    const field = page.locator("#gpo-form [name='description']");
    await field.fill("Keep this unrelated unsaved description");
    pending.release();
    await pending.completed;
    await expect(page.locator(`[data-guid="${removed.guid}"]`)).toHaveCount(0);
    await expect(page.locator("#gpo-dialog")).toBeVisible();
    await expect(field).toHaveValue("Keep this unrelated unsaved description");
    await expect(field).toBeFocused();
    await expect(page.locator("#title")).toHaveText(ordinary.name);
    await expect(page.locator("#starter-list-dialog")).toBeHidden();
  } finally {
    pending.release();
  }
});

test("deleting the selected Starter clears its view without selecting a survivor", async ({
  page,
  request,
}) => {
  const removed = await seed(request);
  await seed(request, false);
  await open(page, removed);
  await library(page);
  await remove(page, removed.guid);
  await expect(page.locator("#toast")).toHaveText("Starter GPO deleted");
  await closeLibrary(page);
  await expect(page.locator("#workspace")).toBeHidden();
  await expect(page.locator("#title")).toHaveText("Choose a policy");
  await expect(page.locator(`[data-guid="${removed.guid}"]`)).toHaveCount(0);
});

for (const phase of ["detail", "list", "mutation"]) {
  test(`an old ${phase} response cannot resurrect a deletion after a fresh list`, async ({
    page,
    request,
  }) => {
    const removed = await seed(request),
      ordinary = await seed(request, false);
    await open(page, phase === "mutation" ? removed : ordinary);
    const pending = await hold(
      page,
      phase === "list" ? "**/api/gpos" : `**/api/gpos/${removed.guid}`,
      phase === "mutation" ? "PATCH" : "GET",
    );
    try {
      if (phase === "detail") {
        await page
          .locator(`[data-guid="${removed.guid}"]`)
          .evaluate((button) => {
            const original = button.onclick;
            button.onclick = (...args) =>
              (globalThis.heldCollectionSelection = original(...args));
          });
        await page.locator(`[data-guid="${removed.guid}"]`).click();
      } else if (phase === "list") {
        await page.evaluate(() => {
          globalThis.heldCollectionRead = import("/assets/js/render.mjs").then(
            ({ loadList }) => loadList(undefined, { select: false }),
          );
        });
      } else {
        await page.locator("#edit-metadata").click();
        await page
          .locator("#gpo-form [name='description']")
          .fill("Committed before deletion; response held");
        await page.locator("#gpo-form button[value='default']").click();
      }
      expect((await pending.entered).status()).toBe(200);
      if (phase === "mutation") {
        await page
          .locator("#gpo-dialog .dialog-actions button[value='cancel']")
          .click();
        await select(page, ordinary);
      }
      await library(page);
      await remove(page, removed.guid);
      await expect(page.locator("#toast")).toHaveText("Starter GPO deleted");
      await closeLibrary(page);
      await freshList(page);
      expect((await request.get(`/api/gpos/${removed.guid}`)).status()).toBe(
        404,
      );
      await expect(page.locator(`[data-guid="${removed.guid}"]`)).toHaveCount(
        0,
      );
      pending.release();
      await pending.completed;
      if (phase === "list")
        await page.evaluate(() => globalThis.heldCollectionRead);
      if (phase === "detail")
        await page.evaluate(() => globalThis.heldCollectionSelection);
      // Fence the owned consumer continuation, including completeMutation.
      await page.waitForFunction(
        () => !globalThis.document.querySelector("#gpo-form[aria-busy='true']"),
      );
      await page.evaluate(
        () =>
          new Promise((resolve) => globalThis.requestAnimationFrame(resolve)),
      );
      await expect(page.locator(`[data-guid="${removed.guid}"]`)).toHaveCount(
        0,
      );
      await expect(page.locator("#title")).toHaveText(ordinary.name);
    } finally {
      pending.release();
    }
  });
}

test("a failed delete preserves membership and does not retire an editor", async ({
  page,
  request,
}) => {
  const target = await seed(request);
  await open(page, target);
  await library(page);
  const concurrent = await request.patch(`/api/gpos/${target.guid}`, {
    data: {
      expected_revision: 1,
      name: target.name,
      description: "Synthetic edit before deletion CAS",
    },
  });
  expect(concurrent.status()).toBe(200);
  const generation = await page.evaluate(
    async (guid) =>
      (await import("/assets/js/state.mjs")).getGpoGeneration(guid),
    target.guid,
  );
  await remove(page, target.guid);
  await expect(page.locator("#error-summary")).toContainText(
    "Expected revision 1",
  );
  await expect(
    page.locator(`[data-delete-starter="${target.guid}"]`),
  ).toHaveCount(1);
  expect(
    await page.evaluate(
      async (guid) =>
        (await import("/assets/js/state.mjs")).getGpoGeneration(guid),
      target.guid,
    ),
  ).toBe(generation);
  await closeLibrary(page);
  await select(page, target);
  await expect(page.locator("#revision")).toHaveText("Revision 2");
  await page.locator("#edit-metadata").click();
  await page
    .locator("#gpo-form [name='description']")
    .fill("A valid edit after failed deletion");
  await page.locator("#gpo-form button[value='default']").click();
  await expect(page.locator("#gpo-dialog")).toBeHidden();
  expect(
    (await (await request.get(`/api/gpos/${target.guid}`)).json()).gpo.revision,
  ).toBe(3);
});

test("a pending selection of another policy survives Starter deletion", async ({
  page,
  request,
}) => {
  const removed = await seed(request),
    ordinary = await seed(request, false);
  await open(page, removed);
  await library(page);
  const deletion = await hold(
    page,
    `**/api/starter-gpos/${removed.guid}`,
    "DELETE",
    true,
  );
  const selection = await hold(page, `**/api/gpos/${ordinary.guid}`, "GET");
  try {
    await remove(page, removed.guid);
    await deletion.entered;
    await closeLibrary(page);
    await page.locator(`[data-guid="${ordinary.guid}"]`).click();
    await selection.entered;
    deletion.release();
    await deletion.completed;
    await expect(page.locator(`[data-guid="${removed.guid}"]`)).toHaveCount(0);
    selection.release();
    await selection.completed;
    await expect(page.locator("#title")).toHaveText(ordinary.name);
  } finally {
    deletion.release();
    selection.release();
  }
});

test("an old editor cannot save into a same-GUID/revision recreation", async ({
  page,
  request,
}) => {
  const removed = await seed(request);
  await open(page, removed);
  await library(page);
  const deletion = await hold(
    page,
    `**/api/starter-gpos/${removed.guid}`,
    "DELETE",
    true,
  );
  try {
    await remove(page, removed.guid);
    await deletion.entered;
    await closeLibrary(page);
    await page.locator("#edit-metadata").click();
    const field = page.locator("#gpo-form [name='description']");
    await field.fill("Keep old draft; never apply it to the recreated object");
    deletion.release();
    await deletion.completed;
    await expect(page.locator(`[data-guid="${removed.guid}"]`)).toHaveCount(0);
    const fresh = await recreate(request, removed);
    // A legitimate new read may learn the new incarnation while an old form
    // remains open. Its identity must still come from its original opening.
    await page.evaluate(async (guid) => {
      const { selectGpo } = await import("/assets/js/render.mjs");
      await selectGpo(guid);
    }, fresh.guid);
    await expect(page.locator("#title")).toHaveText(fresh.name);
    let saves = 0;
    page.on("request", (sent) => {
      if (
        sent.method() === "PATCH" &&
        sent.url().endsWith(`/api/gpos/${fresh.guid}`)
      )
        saves++;
    });
    await page.locator("#gpo-form button[value='default']").click();
    await expect
      .poll(async () =>
        saves
          ? "sent"
          : (await page.locator("#gpo-form .form-error").count())
            ? "blocked"
            : "waiting",
      )
      .toBe("blocked");
    await expect(page.locator("#gpo-form .form-error")).toContainText(
      "was deleted",
    );
    await expect(field).toHaveValue(
      "Keep old draft; never apply it to the recreated object",
    );
    expect(saves).toBe(0);
    expect(
      (await (await request.get(`/api/gpos/${fresh.guid}`)).json()).gpo
        .revision,
    ).toBe(1);
    await page
      .locator("#gpo-dialog .dialog-actions button[value='cancel']")
      .click();
    await expect(page.locator("#gpo-dialog")).toBeHidden();
  } finally {
    deletion.release();
  }
});

async function overlayLibrary(page) {
  // Exercise the exported collection action while retaining the editor beneath
  // it, so its old opening identity survives the deletion/recreation interval.
  await page.evaluate(async () =>
    (await import("/assets/js/starter.mjs")).openStarterList(),
  );
  await expect(page.locator("#starter-list")).not.toContainText("Loading…");
}

test("an old ADMX target snapshot cannot configure a recreated target", async ({
  page,
  request,
}) => {
  const target = await seed(request),
    ordinary = await seed(request, false);
  await open(page, ordinary);
  await page.getByRole("tab", { name: "ADMX", exact: true }).click();
  await page
    .locator(".admx-result", { hasText: "Synthetic feature with options" })
    .click();
  await page.locator("#admx-configure-btn").click();
  await page.locator("#configure-target-gpo").selectOption(target.guid);
  await page
    .locator("#configure-form [name='reason']")
    .fill("Retain original ADMX target review");
  await overlayLibrary(page);
  await remove(page, target.guid);
  await expect(page.locator("#toast")).toHaveText("Starter GPO deleted");
  await closeLibrary(page);
  await recreate(request, target);
  await freshList(page);
  let writes = 0;
  page.on("request", (sent) => {
    if (sent.method() === "POST" && sent.url().endsWith("/configure")) writes++;
  });
  await page.locator("#configure-form button[value='default']").click();
  await expect(page.locator("#configure-form .form-error")).toContainText(
    "target policy was deleted",
  );
  await expect(page.locator("#configure-form [name='reason']")).toHaveValue(
    "Retain original ADMX target review",
  );
  await expect(page.locator("#title")).toHaveText(ordinary.name);
  expect(writes).toBe(0);
});

test("an old conflict comparison cannot reapply to a recreated target", async ({
  page,
  request,
}) => {
  const target = await seed(request);
  await open(page, target);
  await page.locator("#edit-metadata").click();
  await page
    .locator("#gpo-form [name='description']")
    .fill("Keep old reviewed draft values");
  const external = await request.patch(`/api/gpos/${target.guid}`, {
    data: {
      expected_revision: 1,
      name: target.name,
      description: "Synthetic concurrent edit",
    },
  });
  expect(external.status()).toBe(200);
  await page.locator("#gpo-form button[value='default']").click();
  await expect(page.locator("#conflict-dialog")).toBeVisible();
  await expect(page.locator("#conflict-details")).toContainText(
    "Current workspace revision",
  );
  await overlayLibrary(page);
  await remove(page, target.guid);
  await expect(page.locator("#toast")).toHaveText("Starter GPO deleted");
  await closeLibrary(page);
  await recreate(request, target);
  await page.evaluate(
    async (guid) => (await import("/assets/js/render.mjs")).selectGpo(guid),
    target.guid,
  );
  let writes = 0;
  page.on("request", (sent) => {
    if (
      sent.method() === "PATCH" &&
      sent.url().endsWith(`/api/gpos/${target.guid}`)
    )
      writes++;
  });
  await page.locator("#conflict-reapply").click();
  await expect(page.locator("#conflict-dialog .form-error")).toContainText(
    "was deleted",
  );
  await expect(page.locator("#gpo-form [name='description']")).toHaveValue(
    "Keep old reviewed draft values",
  );
  await expect(page.locator("#conflict-reapply")).toBeDisabled();
  expect(writes).toBe(0);
});

test("a held successful bulk response cannot publish a deleted Starter", async ({
  page,
  request,
}) => {
  const target = await seed(request),
    ordinary = await seed(request, false);
  const policies = await (
    await request.get("/api/admx/search?q=SyntheticFeature")
  ).json();
  const policy = policies.items.find((item) => item.id === "SyntheticFeature");
  const configured = await request.post(
    `/api/admx/policies/${encodeURIComponent(policy.qualified_id)}/configure`,
    {
      data: {
        gpo_guid: target.guid,
        expected_revision: 1,
        expected_catalogue_revision: policies.catalogue_revision,
        side: "computer",
        state: "enabled",
        values: { SubOption: true },
        reason: "Configure synthetic Starter",
      },
    },
  );
  expect(configured.status()).toBe(200);
  await open(page, (await configured.json()).gpo);
  await page.getByRole("tab", { name: /^Settings browser/ }).click();
  await expect(page.locator(".browser-bulk-checkbox")).toHaveCount(1);
  await page.locator("#browser-bulk-select-all").check();
  await page
    .locator("#browser-bulk-reason")
    .fill("Disable reviewed Starter policy");
  const pending = await hold(
    page,
    `**/api/gpos/${target.guid}/bulk-policy-state`,
    "POST",
  );
  try {
    await page.locator('[data-bulk-state="disabled"]').click();
    expect((await pending.entered).status()).toBe(200);
    await library(page);
    await remove(page, target.guid);
    await expect(page.locator("#toast")).toHaveText("Starter GPO deleted");
    await closeLibrary(page);
    await select(page, ordinary);
    await freshList(page);
    pending.release();
    await pending.completed;
    await expect(page.locator("#toast")).toHaveText("Starter GPO deleted");
    await page.evaluate(
      () => new Promise((resolve) => globalThis.requestAnimationFrame(resolve)),
    );
    await expect(page.locator(`[data-guid="${target.guid}"]`)).toHaveCount(0);
    await expect(page.locator("#title")).toHaveText(ordinary.name);
  } finally {
    pending.release();
  }
});

test("a pending ADMX save cannot select its target over a recreated opening view", async ({
  page,
  request,
}) => {
  const underlay = await seed(request),
    target = await seed(request, false);
  await open(page, underlay);
  await page.getByRole("tab", { name: "ADMX", exact: true }).click();
  await page
    .locator(".admx-result", { hasText: "Synthetic feature with options" })
    .click();
  await page.locator("#admx-configure-btn").click();
  await page.locator("#configure-target-gpo").selectOption(target.guid);
  const pending = await hold(page, "**/api/admx/policies/*/configure", "POST");
  try {
    await page.locator("#configure-form button[value='default']").click();
    expect((await pending.entered).status()).toBe(200);
    await overlayLibrary(page);
    await remove(page, underlay.guid);
    await expect(page.locator("#toast")).toHaveText("Starter GPO deleted");
    await closeLibrary(page);
    const fresh = await recreate(request, underlay);
    await page.evaluate(
      async (guid) => (await import("/assets/js/render.mjs")).selectGpo(guid),
      fresh.guid,
    );
    await expect(page.locator("#title")).toHaveText(fresh.name);
    pending.release();
    await pending.completed;
    await expect(page.locator("#toast")).toHaveText(
      "Policy settings applied to GPO",
    );
    await expect(page.locator("#configure-dialog")).toBeHidden();
    await expect(page.locator(`[data-guid="${target.guid}"]`)).toContainText(
      "r2",
    );
    await expect(page.locator("#title")).toHaveText(fresh.name);
  } finally {
    pending.release();
  }
});
