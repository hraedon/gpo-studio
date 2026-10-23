import { mkdirSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";
import { expect, test } from "@playwright/test";

function syntheticBackup(testInfo) {
  const directory = testInfo.outputPath("synthetic-backup");
  const guid = "11111111-2222-3333-4444-555555555555";
  const name = `Synthetic inbox baseline ${testInfo.project.name} ${testInfo.retry} ${Date.now()}`;
  for (const side of ["Machine", "User"]) {
    const sideDirectory = join(directory, guid, side);
    mkdirSync(sideDirectory, { recursive: true });
    writeFileSync(
      join(sideDirectory, "Registry.pol"),
      Buffer.from([0x50, 0x52, 0x65, 0x67, 1, 0, 0, 0]),
    );
  }
  writeFileSync(
    join(directory, "manifest.xml"),
    `<?xml version="1.0" encoding="utf-8"?>
<BackupInstances xmlns="http://www.microsoft.com/GroupPolicy/Types"><BackupInstance>
<BackupTime>2026-01-01T00:00:00</BackupTime><ID>synthetic-backup</ID><GPO>
<Identifier>${guid}</Identifier><DisplayName>${name}</DisplayName><Domain>synthetic.test</Domain>
</GPO></BackupInstance></BackupInstances>`,
  );
  return { path: relative(process.cwd(), directory), name };
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

async function settle(page) {
  await page.evaluate(
    () =>
      new Promise((resolve) =>
        globalThis.requestAnimationFrame(() =>
          globalThis.requestAnimationFrame(resolve),
        ),
      ),
  );
}

async function openImport(page) {
  await page.locator("#import-gpmc").click();
  const dialog = page.locator("#gpmc-import-dialog");
  await expect(dialog.locator("#gpmc-import-submit")).toBeEnabled();
  return dialog;
}

for (const outcome of ["success", "failure"]) {
  test(`a late import ${outcome} preserves a reopened draft and pending controls`, async ({
    page,
    request,
  }, testInfo) => {
    const backupPath = syntheticBackup(testInfo).path;
    const name = `Synthetic import view ${outcome} ${Date.now()}`;
    const created = await request.post("/api/gpos", {
      data: { name, actor: "browser-test", reason: "Import context fixture" },
    });
    expect(created.status()).toBe(201);
    const { gpo } = await created.json();
    await page.goto("/");
    await page.locator(`[data-guid="${gpo.guid}"]`).click();
    await expect(page.locator("#title")).toHaveText(name);
    const dialog = await openImport(page);
    await dialog
      .getByLabel("Inbox-relative backup path")
      .fill(outcome === "success" ? backupPath : "missing-synthetic-backup");
    const release = deferred(),
      arrived = deferred(),
      completed = deferred();
    let sends = 0,
      saved;
    await page.route("**/api/backups/import", async (route) => {
      sends++;
      const response = await route.fetch();
      expect(response.status()).toBe(outcome === "success" ? 201 : 422);
      if (outcome === "success") saved = (await response.json()).gpo;
      arrived.resolve();
      await release.promise;
      await route.fulfill({ response });
      completed.resolve();
    });
    try {
      await dialog.locator("#gpmc-import-submit").click();
      await arrived.promise;
      await expect(
        dialog.getByLabel("Inbox-relative backup path"),
      ).toBeDisabled();
      await expect(dialog.locator(".form-pending")).toContainText(
        "Closing this dialog keeps the save running",
      );
      await page
        .locator("#gpmc-import-form")
        .evaluate((form) => form.requestSubmit());
      expect(sends).toBe(1);
      await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
      await openImport(page);
      await dialog
        .getByLabel("Inbox-relative backup path")
        .fill("keep-this-new-draft");
      await dialog.getByLabel("Import reason").fill("Keep this new reason");
      release.resolve();
      await completed.promise;
      await settle(page);
      await expect(dialog).toBeVisible();
      await expect(dialog.getByLabel("Inbox-relative backup path")).toHaveValue(
        "keep-this-new-draft",
      );
      await expect(dialog.getByLabel("Import reason")).toHaveValue(
        "Keep this new reason",
      );
      await expect(dialog.locator("#gpmc-import-submit")).toBeEnabled();
      await expect(
        dialog.locator(".form-pending,[aria-invalid='true'],.form-error"),
      ).toHaveCount(0);
      await expect(page.locator("#title")).toHaveText(name);
      expect(sends).toBe(1);
      if (saved) {
        const persisted = await (
          await request.get(`/api/gpos/${saved.guid}`)
        ).json();
        expect(persisted.gpo.status).toBe("archived");
        expect(persisted.gpo.source_guid).toBe(
          "11111111-2222-3333-4444-555555555555",
        );
        expect(persisted.gpo.revision).toBe(1);
        await expect(page.locator(`[data-guid="${saved.guid}"]`)).toBeVisible();
      } else {
        await expect(page.locator("#error-summary")).toContainText(
          "Backup path is not a directory",
        );
      }
    } finally {
      release.resolve();
    }
  });
}

test("an older inbox check cannot disable a newly opened import", async ({
  page,
}) => {
  await page.goto("/");
  const release = deferred(),
    arrived = deferred(),
    completed = deferred();
  let checks = 0;
  await page.route("**/api/imports/capabilities", async (route) => {
    checks++;
    if (checks > 1) {
      await route.continue();
      return;
    }
    arrived.resolve();
    await release.promise;
    await route.fulfill({ json: { gpmc_backup: { inbox_configured: false } } });
    completed.resolve();
  });
  try {
    await page.locator("#import-gpmc").click();
    await arrived.promise;
    const dialog = page.locator("#gpmc-import-dialog");
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await openImport(page);
    await dialog
      .getByLabel("Inbox-relative backup path")
      .fill("synthetic-backup");
    release.resolve();
    await completed.promise;
    await settle(page);
    await expect(dialog.locator("#gpmc-import-submit")).toBeEnabled();
    await expect(dialog.locator("#gpmc-import-preview")).toContainText(
      "Import inbox found",
    );
    await expect(dialog.getByLabel("Inbox-relative backup path")).toHaveValue(
      "synthetic-backup",
    );
  } finally {
    release.resolve();
  }
});

test("Enter imports the reviewed backup as a read-only baseline @smoke", async ({
  page,
  request,
}, testInfo) => {
  const backup = syntheticBackup(testInfo);
  const backupPath = backup.path;
  await page.goto("/");
  const dialog = await openImport(page);
  await dialog.getByLabel("Inbox-relative backup path").fill(backupPath);
  await dialog
    .getByLabel("Actor", { exact: true })
    .fill("synthetic-import-operator");
  await dialog.getByLabel("Import reason").fill("Review synthetic baseline");
  const received = page.waitForResponse(
    (r) =>
      r.request().method() === "POST" &&
      r.url().endsWith("/api/backups/import"),
  );
  const sent = page.waitForRequest(
    (r) => r.method() === "POST" && r.url().endsWith("/api/backups/import"),
  );
  await dialog.getByLabel("Import reason").press("Enter");
  const body = (await sent).postDataJSON();
  const response = await received;
  expect(response.status(), JSON.stringify(await response.json())).toBe(201);
  expect(body).toEqual({
    path: backupPath,
    actor: "synthetic-import-operator",
    reason: "Review synthetic baseline",
  });
  await expect(dialog).not.toBeVisible();
  await expect(page.locator("#title")).toHaveText(backup.name);
  await expect(page.locator("#edit-metadata")).toBeDisabled();
  const guid = await page
    .locator("#gpo-list .gpo-item.active")
    .getAttribute("data-guid");
  const history = await (
    await request.get(`/api/gpos/${guid}/revisions`)
  ).json();
  expect(history.items[0]).toMatchObject({
    actor: body.actor,
    reason: body.reason,
    revision: 1,
  });
});
