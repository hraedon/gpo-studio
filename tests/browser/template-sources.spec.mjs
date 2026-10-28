import { expect, test } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

async function sources(request) {
  return (await (await request.get("/api/templates")).json()).items;
}

async function open(page) {
  await page.locator("#open-templates").click();
  const dialog = page.locator("#templates-dialog");
  await expect(dialog.locator('[name="path"]')).toBeEnabled();
  return dialog;
}

test("folder reload shows all active sources, including API-added sources", async ({
  page,
  request,
}) => {
  const original = (await sources(request)).find(
    (source) => source.name === "default",
  );
  // The harness removes this private workspace, including its empty source,
  // when the server stops; this source adds no policies to other journeys.
  const directory = mkdtempSync(
    join(dirname(original.path), "additional-empty-source-"),
  );
  const extra = await request.post("/api/templates/ingest", {
    data: { name: "synthetic-extra-source", kind: "local", path: directory },
  });
  expect(extra.status()).toBe(201);
  const expected = await sources(request);
  await page.goto("/");
  const dialog = await open(page);
  await dialog.getByLabel("Folder path").fill(original.path);
  await dialog.getByRole("button", { name: "Load folder" }).click();
  await expect(dialog.locator("#templates-load")).toBeEnabled();
  await expect(dialog.locator(".template-source")).toHaveCount(expected.length);
  await expect(dialog.locator("#templates-sources")).toContainText(
    "synthetic-extra-source",
  );
  await expect(dialog.locator("#templates-sources")).toContainText(directory);
});

test("a background folder activation refreshes a reopened dialog without changing its next path", async ({
  page,
  request,
}) => {
  const original = (await sources(request)).find(
    (source) => source.name === "default",
  );
  const replacement = fileURLToPath(
    new URL("../fixtures/admx", import.meta.url),
  );
  let release;
  const held = new Promise((resolve) => {
    release = resolve;
  });
  await page.route("**/api/templates/ingest", async (route) => {
    await held;
    await route.continue();
  });
  try {
    await page.goto("/");
    let dialog = await open(page);
    await dialog.getByLabel("Folder path").fill(replacement);
    await dialog.getByRole("button", { name: "Load folder" }).click();
    await expect(dialog.getByLabel("Folder path")).toBeDisabled();
    await dialog
      .getByRole("button", { name: "Close", exact: true })
      .last()
      .click();
    dialog = await open(page);
    const nextPath = "/synthetic-next-folder-not-yet-loaded";
    await dialog.getByLabel("Folder path").fill(nextPath);
    await expect(dialog.locator("#templates-load")).toBeDisabled();
    release();
    await expect(dialog.locator("#templates-load")).toBeEnabled();
    await expect(dialog.locator("#templates-sources")).toContainText(
      replacement,
    );
    await expect(dialog.getByLabel("Folder path")).toHaveValue(nextPath);
    await expect(dialog.locator("#templates-status")).toContainText(
      "Loaded 7 policies",
    );
    const current = (await sources(request)).find(
      (source) => source.name === "default",
    );
    expect(current.path).toBe(replacement);
  } finally {
    release();
    await request.post("/api/templates/ingest", {
      data: { name: "default", kind: original.kind, path: original.path },
    });
  }
});
