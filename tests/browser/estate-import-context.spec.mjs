import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

async function upload(dialog, name, document) {
  await dialog.locator("#estate-file").setInputFiles({
    name,
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(document)),
  });
  await expect(dialog.locator("#estate-file-info")).toContainText(name);
  await expect(dialog.locator("#estate-file-info")).not.toContainText(
    "Reading",
  );
}

for (const nextEditor of ["creation", "estate import"]) {
  test(`a retired estate import refreshes the list without selecting under newer ${nextEditor}${nextEditor === "creation" ? " @smoke" : ""}`, async ({
    page,
    request,
  }) => {
    // Model the first empty-list snapshot independently of other journeys sharing
    // the test server. The later import and refresh use the real API/store.
    let listReads = 0;
    await page.route("**/api/gpos", async (route) => {
      if (route.request().method() === "GET" && ++listReads === 1) {
        await route.fulfill({ json: { items: [], count: 0 } });
      } else await route.continue();
    });
    await page.goto("/");
    await expect(page.locator("#empty")).toBeVisible();
    await expect(page.locator("#title")).toHaveText("Choose a policy");
    await page.locator("#import-estate").click();
    const estateDialog = page.locator("#estate-dialog");
    const importedName = `Synthetic retired estate ${randomUUID()}`;
    const sourceGuid = randomUUID();
    await upload(estateDialog, "synthetic-old-estate.json", {
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
    });
    const release = deferred(),
      arrived = deferred(),
      completed = deferred();
    await page.route("**/api/estate/import", async (route) => {
      const response = await route.fetch();
      expect(response.status()).toBe(200);
      expect((await response.json()).imported).toBe(1);
      arrived.resolve();
      await release.promise;
      await route.fulfill({ response });
      completed.resolve();
    });
    try {
      await estateDialog
        .getByRole("button", { name: "Import", exact: true })
        .click();
      await arrived.promise;
      await estateDialog
        .getByRole("button", { name: "Cancel", exact: true })
        .click();
      const nextName = `Synthetic newer draft ${randomUUID()}`;
      let nextDialog;
      if (nextEditor === "creation") {
        await page.locator("#new-gpo").click();
        nextDialog = page.locator("#gpo-dialog");
        await expect(
          nextDialog.getByLabel("Name", { exact: true }),
        ).toBeFocused();
        await nextDialog.getByLabel("Name", { exact: true }).fill(nextName);
      } else {
        await page.locator("#import-estate").click();
        nextDialog = estateDialog;
        await upload(nextDialog, "synthetic-new-estate.json", {
          kind: "gpo-lens-estate",
          gpos: [],
        });
      }
      release.resolve();
      await completed.promise;
      await expect(page.locator("#gpo-list")).toContainText(importedName);
      await expect(page.locator("#title")).toHaveText("Choose a policy");
      await expect(nextDialog).toBeVisible();
      await expect(nextDialog.locator(".form-pending,.form-error")).toHaveCount(
        0,
      );
      const stored = (await (await request.get("/api/gpos")).json()).items.find(
        (gpo) => gpo.name === importedName,
      );
      expect(stored).toBeTruthy();
      const persisted = (
        await (await request.get(`/api/gpos/${stored.guid}`)).json()
      ).gpo;
      expect(persisted).toMatchObject({
        status: "archived",
        source_guid: sourceGuid,
        revision: 1,
      });
      if (nextEditor === "creation") {
        await expect(
          nextDialog.getByLabel("Name", { exact: true }),
        ).toHaveValue(nextName);
        await nextDialog.getByRole("button", { name: "Create policy" }).click();
        await expect(nextDialog).toBeHidden();
        await expect(page.locator("#title")).toHaveText(nextName);
      } else {
        await expect(nextDialog.locator("#estate-file-info")).toContainText(
          "synthetic-new-estate.json",
        );
        await expect(
          nextDialog.getByRole("button", { name: "Import", exact: true }),
        ).toBeEnabled();
      }
    } finally {
      release.resolve();
    }
  });
}
