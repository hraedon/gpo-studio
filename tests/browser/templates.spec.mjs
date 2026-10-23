import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

test("loads an ADMX folder in the UI and refreshes cached tabs @smoke", async ({
  page,
  request,
}) => {
  const original = (
    await (await request.get("/api/templates")).json()
  ).items.find((source) => source.name === "default");
  const empty = mkdtempSync(join(tmpdir(), "studio-empty-admx-"));
  try {
    const name = `Synthetic Template Target ${Date.now()}`;
    await request.post("/api/gpos", {
      data: { name, actor: "browser-test", reason: "Template UI test" },
    });
    await page.goto("/");
    await page.locator(".gpo-item", { hasText: name }).click();
    await page.getByRole("tab", { name: /^Settings browser/ }).click();
    await page.locator("#browser-category-filter").selectOption({ index: 1 });
    await expect(page.locator("#browser-category-filter")).not.toHaveValue("");
    await page.getByRole("tab", { name: "ADMX", exact: true }).click();
    await expect(page.locator(".admx-result").first()).toBeVisible();
    await page.locator("#open-templates").click();
    const dialog = page.locator("#templates-dialog");
    await expect(dialog.locator('[name="path"]')).toHaveValue(original.path);
    const accessibility = await new AxeBuilder({ page })
      .include("#templates-dialog")
      .analyze();
    expect(accessibility.violations).toEqual([]);
    await dialog.getByLabel("Folder path").fill(empty);
    await dialog.getByRole("button", { name: "Load folder" }).click();
    await expect(dialog.locator("#templates-status")).toContainText(
      "No usable policies were found",
    );
    await expect(dialog.locator("#templates-load")).toBeEnabled();
    await dialog
      .getByRole("button", { name: "Close", exact: true })
      .last()
      .click();
    await expect(page.locator("#admx-empty")).toBeVisible();
    await page.locator("#admx-empty").getByRole("button").click();
    await dialog.getByLabel("Folder path").fill(original.path);
    await dialog.getByRole("button", { name: "Load folder" }).click();
    await expect(dialog.locator("#templates-status")).toContainText(
      `Loaded ${original.policy_count} policies`,
    );
    await expect(dialog.locator("#templates-load")).toBeEnabled();
    await dialog
      .getByRole("button", { name: "Close", exact: true })
      .last()
      .click();
    await expect(page.locator(".admx-result").first()).toBeVisible();
    await page.getByRole("tab", { name: /^Settings browser/ }).click();
    await expect(page.locator("#browser-content")).toBeVisible();
    await expect(page.locator("#browser-category-filter")).toHaveValue("");
  } finally {
    await request.post("/api/templates/ingest", {
      data: { name: "default", kind: "local", path: original.path },
    });
    rmSync(empty, { recursive: true, force: true });
  }
});

test("invalid folder preserves catalogue and shows a persistent error", async ({
  page,
  request,
}) => {
  const original = (await (await request.get("/api/templates")).json()).items;
  await page.goto("/");
  await page.locator("#open-templates").click();
  const dialog = page.locator("#templates-dialog");
  await expect(dialog.locator("#templates-status")).toContainText(
    "Currently loaded sources",
  );
  await dialog
    .getByLabel("Folder path")
    .fill("/synthetic-missing-admx-folder/browser-test");
  await dialog.getByRole("button", { name: "Load folder" }).click();
  await expect(dialog.locator(".form-error")).toContainText(
    "Template directory does not exist",
  );
  await expect(dialog.locator("#templates-load")).toBeEnabled();
  expect((await (await request.get("/api/templates")).json()).items).toEqual(
    original,
  );
  await expect(dialog).toBeVisible();
});

test("a late category response cannot restore an obsolete catalogue", async ({
  page,
  request,
}) => {
  const original = (
    await (await request.get("/api/templates")).json()
  ).items.find((source) => source.name === "default");
  const name = `Synthetic Catalogue Race ${Date.now()}`;
  await request.post("/api/gpos", {
    data: { name, actor: "browser-test", reason: "Catalogue generation test" },
  });
  await page.goto("/");
  await page.locator(".gpo-item", { hasText: name }).click();
  let release;
  let finish;
  let started;
  const held = new Promise((resolve) => {
    release = resolve;
  });
  const done = new Promise((resolve) => {
    finish = resolve;
  });
  const ready = new Promise((resolve) => {
    started = resolve;
  });
  let first = true;
  await page.route("**/api/admx/categories/tree", async (route) => {
    if (!first) {
      await route.continue();
      return;
    }
    first = false;
    const response = await route.fetch();
    const data = await response.json();
    data.items = [
      {
        id: "obsolete-category",
        display_name: "OBSOLETE SOURCE",
        policy_count: 99,
        children: [],
      },
    ];
    started();
    await held;
    await route.fulfill({ response, json: data });
    finish();
  });
  await page.getByRole("tab", { name: /^Settings browser/ }).click();
  await ready;
  await page.locator("#open-templates").click();
  const dialog = page.locator("#templates-dialog");
  await expect(dialog.getByLabel("Folder path")).toHaveValue(original.path);
  await dialog.getByRole("button", { name: "Load folder" }).click();
  await expect(dialog.locator("#templates-load")).toBeEnabled();
  await expect(page.locator("#browser-category-filter option")).toHaveCount(2);
  release();
  await done;
  await dialog
    .getByRole("button", { name: "Close", exact: true })
    .last()
    .click();
  await expect(page.locator("#browser-category-filter")).not.toContainText(
    "OBSOLETE SOURCE",
  );
});
