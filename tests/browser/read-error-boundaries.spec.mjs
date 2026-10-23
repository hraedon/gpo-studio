import { expect, test } from "@playwright/test";
import { randomUUID } from "node:crypto";

const warning =
  "Synthetic current warning: review the newer task before retrying.";
const unavailable = "The local GPO Studio server is unavailable";
function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
async function flush(page) {
  await page.evaluate(
    () => new Promise((resolve) => globalThis.requestAnimationFrame(resolve)),
  );
}
async function heldFailure(
  page,
  path,
  failure,
  { method = "GET", fetchFirst = true } = {},
) {
  const arrived = deferred(),
    release = deferred(),
    done = deferred();
  let count = 0;
  await page.route(path, async (route) => {
    if (route.request().method() !== method) return route.continue();
    count++;
    let response;
    if (fetchFirst) response = await route.fetch();
    arrived.resolve({
      body: response ? await response.json() : null,
      status: response?.status(),
      sent: route.request().postDataJSON(),
    });
    await release.promise;
    if (failure === "network") await route.abort("failed");
    else
      await route.fulfill({
        status: 503,
        json: { error: { message: "Synthetic owned read unavailable" } },
      });
    done.resolve();
  });
  return {
    arrived: arrived.promise,
    done: done.promise,
    release: () => release.resolve(),
    count: () => count,
  };
}
async function newDraft(page) {
  await page.locator("#new-gpo").click();
  const dialog = page.locator("#gpo-dialog");
  const name = `Synthetic newer read-error draft ${randomUUID()}`;
  await dialog.getByLabel("Name", { exact: true }).fill(name);
  await dialog
    .getByLabel("Description", { exact: true })
    .fill("Keep the newer unsaved description");
  await page.evaluate(async (message) => {
    const { showPersistentError } = await import("/assets/js/api.mjs");
    showPersistentError(message);
  }, warning);
  await dialog.getByLabel("Description", { exact: true }).focus();
  return { dialog, name };
}
async function keptDraft(page, draft) {
  await expect(page.locator("#error-message")).toHaveText(`Error: ${warning}`);
  await expect(draft.dialog).toBeVisible();
  await expect(draft.dialog.getByLabel("Name", { exact: true })).toHaveValue(
    draft.name,
  );
  await expect(
    draft.dialog.getByLabel("Description", { exact: true }),
  ).toHaveValue("Keep the newer unsaved description");
  await expect(
    draft.dialog.getByLabel("Description", { exact: true }),
  ).toBeFocused();
  await expect(draft.dialog.locator(".form-error")).toHaveCount(0);
}

const readSurfaces = [
  {
    name: "GPMC capabilities",
    path: "**/api/imports/capabilities",
    open: "#import-gpmc",
    dialog: "#gpmc-import-dialog",
  },
  {
    name: "Starter list",
    path: "**/api/starter-gpos",
    open: "#open-starter-gpos",
    dialog: "#starter-list-dialog",
  },
];
for (const surface of readSurfaces) {
  for (const failure of ["network", "http"]) {
    test(`a retired ${surface.name} ${failure} failure preserves a newer warning and draft ${surface.name === "GPMC capabilities" && failure === "network" ? "@smoke" : ""}`, async ({
      page,
    }) => {
      await page.goto("/");
      await expect(page.locator("#app-status")).toBeHidden();
      const pending = await heldFailure(page, surface.path, failure);
      try {
        await page.locator(surface.open).click();
        expect((await pending.arrived).status).toBe(200);
        await page
          .locator(surface.dialog)
          .locator('.dialog-head button[aria-label="Close"]')
          .click();
        const draft = await newDraft(page);
        pending.release();
        await pending.done;
        await flush(page);
        await keptDraft(page, draft);
        expect(pending.count()).toBe(1);
      } finally {
        pending.release();
      }
    });
  }
}

async function uploadEstate(dialog, importedName, sourceGuid) {
  await dialog.locator("#estate-file").setInputFiles({
    name: "synthetic-read-error-estate.json",
    mimeType: "application/json",
    buffer: Buffer.from(
      JSON.stringify({
        kind: "gpo-lens-estate",
        domain: "synthetic.example.test",
        gpos: [
          {
            guid: sourceGuid,
            display_name: importedName,
            settings: [],
            links: [],
            security_filters: [],
          },
        ],
      }),
    ),
  });
  await expect(dialog.locator("#estate-file-info")).toContainText(
    "synthetic-read-error-estate.json",
  );
  await expect(dialog.locator("#estate-file-info")).not.toContainText(
    "Reading",
  );
}
for (const originRetired of [false, true]) {
  for (const failure of ["network", "http"]) {
    test(`estate refresh ${failure} failure preserves a newer editor opened ${originRetired ? "before refresh dispatch" : "during refresh"}`, async ({
      page,
      request,
    }) => {
      await page.goto("/");
      await expect(page.locator("#app-status")).toBeHidden();
      await page.locator("#import-estate").click();
      const estate = page.locator("#estate-dialog");
      const importedName = `Synthetic committed estate ${randomUUID()}`,
        sourceGuid = randomUUID();
      await uploadEstate(estate, importedName, sourceGuid);
      const refresh = await heldFailure(page, "**/api/gpos", failure);
      const writeArrived = deferred(),
        writeRelease = deferred();
      let writes = 0;
      await page.route("**/api/estate/import", async (route) => {
        writes++;
        const response = await route.fetch();
        expect(response.status()).toBe(200);
        expect((await response.json()).imported).toBe(1);
        writeArrived.resolve();
        if (originRetired) await writeRelease.promise;
        await route.fulfill({ response });
      });
      try {
        await estate
          .getByRole("button", { name: "Import", exact: true })
          .click();
        await writeArrived.promise;
        let draft;
        if (originRetired) {
          await estate
            .getByRole("button", { name: "Cancel", exact: true })
            .click();
          draft = await newDraft(page);
          writeRelease.resolve();
        }
        const fetched = await refresh.arrived;
        expect(fetched.status).toBe(200);
        expect(
          fetched.body.items.some((gpo) => gpo.name === importedName),
        ).toBe(true);
        await expect(estate).toBeHidden();
        if (!originRetired) draft = await newDraft(page);
        const stored = (
          await (await request.get("/api/gpos")).json()
        ).items.find((gpo) => gpo.name === importedName);
        expect(stored).toBeTruthy();
        const persisted = (
          await (await request.get(`/api/gpos/${stored.guid}`)).json()
        ).gpo;
        expect(persisted).toMatchObject({
          status: "archived",
          source_guid: sourceGuid,
          revision: 1,
        });
        refresh.release();
        await refresh.done;
        await flush(page);
        await keptDraft(page, draft);
        await expect(page.locator("#toast")).toContainText("Imported 1 GPO(s)");
        expect(writes).toBe(1);
        expect(refresh.count()).toBe(1);
      } finally {
        writeRelease.resolve();
        refresh.release();
      }
    });
  }
}

for (const [index, surface] of readSurfaces.entries()) {
  const failure = index === 0 ? "network" : "http";
  test(`an active ${surface.name} ${failure} failure remains visible`, async ({
    page,
  }) => {
    await page.goto("/");
    await expect(page.locator("#app-status")).toBeHidden();
    const pending = await heldFailure(page, surface.path, failure);
    try {
      await page.locator(surface.open).click();
      await pending.arrived;
      pending.release();
      await pending.done;
      const dialog = page.locator(surface.dialog);
      await expect(dialog).toBeVisible();
      await expect(
        dialog.locator(index === 0 ? ".form-error" : "#starter-list"),
      ).toContainText(
        failure === "network"
          ? unavailable
          : "Synthetic owned read unavailable",
      );
    } finally {
      pending.release();
    }
  });
}

for (const failure of ["network", "http"]) {
  test(`an active initial policy list ${failure} failure reports and stops Loading`, async ({
    page,
  }) => {
    const pending = await heldFailure(page, "**/api/gpos", failure);
    try {
      await page.goto("/");
      await pending.arrived;
      await expect(page.locator("#app-status")).toHaveText("Loading policies…");
      pending.release();
      await pending.done;
      await expect(page.locator("#error-summary")).toContainText(
        failure === "network"
          ? unavailable
          : "Synthetic owned read unavailable",
      );
      await expect(page.locator("#app-status")).toBeHidden();
      expect(pending.count()).toBe(1);
    } finally {
      pending.release();
    }
  });
}

test("an initial list failure cannot replace a newer warning without any editor change", async ({
  page,
}) => {
  const pending = await heldFailure(page, "**/api/gpos", "http");
  try {
    await page.goto("/");
    await pending.arrived;
    await page.evaluate(async (message) => {
      const { showPersistentError } = await import("/assets/js/api.mjs");
      showPersistentError(message);
    }, warning);
    pending.release();
    await pending.done;
    await flush(page);
    await expect(page.locator("#error-message")).toHaveText(
      `Error: ${warning}`,
    );
    await expect(page.locator("#app-status")).toBeHidden();
  } finally {
    pending.release();
  }
});

async function settingDraft(page, request, label) {
  const created = await request.post("/api/gpos", {
    data: { name: `Synthetic mutation error ${label} ${randomUUID()}` },
  });
  expect(created.status()).toBe(201);
  const gpo = (await created.json()).gpo;
  await page.goto("/");
  await page.locator(`[data-guid="${gpo.guid}"]`).click();
  await expect(page.locator("#title")).toHaveText(gpo.name);
  await page.getByRole("tab", { name: /Policy settings/ }).click();
  await page.locator("#add-setting").click();
  const dialog = page.locator("#setting-dialog");
  await dialog
    .getByLabel("Registry key")
    .fill("Software\\Policies\\SyntheticReadErrors");
  await dialog.getByLabel("Value name").fill("RetainedValue");
  await dialog
    .locator('[name="value"]')
    .fill("Keep this unsaved registry value");
  return { gpo, dialog };
}
for (const retired of [false, true]) {
  test(`a ${retired ? "closed" : "current"} mutation transport failure still reports its ${retired ? "named outcome" : "inline error and draft"}`, async ({
    page,
    request,
  }) => {
    const { gpo, dialog } = await settingDraft(page, request, String(retired));
    const pending = await heldFailure(
      page,
      `**/api/gpos/${gpo.guid}/settings`,
      "network",
      { method: "POST", fetchFirst: false },
    );
    try {
      await dialog.getByRole("button", { name: "Save setting" }).click();
      const sent = (await pending.arrived).sent;
      expect(sent.expected_revision).toBe(1);
      expect(sent.setting.value_name).toBe("RetainedValue");
      let newer;
      if (retired) {
        await dialog
          .getByRole("button", { name: "Cancel", exact: true })
          .click();
        newer = await newDraft(page);
      }
      pending.release();
      await pending.done;
      await flush(page);
      if (retired) {
        await expect(page.locator("#error-summary")).toContainText(
          `${gpo.name}: ${unavailable}`,
        );
        await expect(newer.dialog).toBeVisible();
        await expect(
          newer.dialog.getByLabel("Name", { exact: true }),
        ).toHaveValue(newer.name);
        await expect(
          newer.dialog.getByLabel("Description", { exact: true }),
        ).toBeFocused();
      } else {
        await expect(dialog.locator(".form-error")).toContainText(unavailable);
        await expect(dialog.locator('[name="value"]')).toHaveValue(
          "Keep this unsaved registry value",
        );
        await expect(
          dialog.getByRole("button", { name: "Save setting" }),
        ).toBeEnabled();
        await expect(page.locator("#error-summary")).toBeHidden();
      }
      const stored = (await (await request.get(`/api/gpos/${gpo.guid}`)).json())
        .gpo;
      expect(stored.revision).toBe(1);
      expect(stored.settings).toEqual([]);
      expect(pending.count()).toBe(1);
    } finally {
      pending.release();
    }
  });
}
