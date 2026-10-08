import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

// One end-to-end pass over the Scripts export panel (Plan 034). The endpoint
// behind it returns the R10 lane builder's bytes for the certified request
// (`tests/test_scripts_surface.py`); this spec checks that an operator can
// build that request from the panel, sees what it does not establish before
// the output, and can do all of it from the keyboard and at phone width.

async function seedPolicy(request, testInfo, label) {
  const name = `Synthetic Scripts ${label} ${testInfo.project.name} ${Date.now()}`;
  const response = await request.post("/api/gpos", {
    data: { name, actor: "browser-test", reason: "Seed scripts browser test" },
  });
  expect(response.status()).toBe(201);
  return { name, payload: await response.json() };
}

async function openForPolicy(page, name) {
  await page.goto("/");
  await page.locator(".gpo-item", { hasText: name }).click();
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
  await page.getByRole("button", { name: "Scripts", exact: true }).click();
  const form = page.locator("#scripts-form");
  await expect(form).toBeVisible();
  return form;
}

async function fillCertifiedShape(form) {
  await form.getByRole("button", { name: "Add startup script" }).click();
  await form.getByRole("button", { name: "Add startup script" }).click();
  await form.getByRole("button", { name: "Add PowerShell script" }).click();
  const first = form.getByRole("group", {
    name: "Startup script 1",
    exact: true,
  });
  await first.getByLabel("Command").fill("zz-studio-marker.cmd");
  await first.getByLabel("Parameters").fill("/c alpha beta");
  await form
    .getByRole("group", { name: "Startup script 2", exact: true })
    .getByLabel("Command")
    .fill("zz-studio-second.cmd");
  const ps = form.getByRole("group", {
    name: "PowerShell startup script 1",
    exact: true,
  });
  await ps.getByLabel("Command").fill("zz-studio-marker.ps1");
  await ps.getByLabel("Parameters").fill("-Mode Alpha");
}

test("previews the measured shape with its limits above the files @smoke", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo, "preview");
  const form = await openForPolicy(page, seeded.name);
  await fillCertifiedShape(form);
  await form.getByRole("button", { name: "Preview" }).click();

  const results = page.locator("#scripts-results");
  await expect(results).toContainText("0CmdLine=zz-studio-marker.cmd");
  await expect(results).toContainText("1Parameters=");
  await expect(results).toContainText("StartExecutePSFirst=true");
  await expect(results).toContainText("payload_not_carried");
  await expect(results).toContainText("execution_unmeasured");
  const html = await results.innerHTML();
  expect(html.indexOf("What this answer does not say")).toBeLessThan(
    html.indexOf("Machine/Scripts/scripts.ini"),
  );
});

test("downloads the backup the preview described", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo, "download");
  const form = await openForPolicy(page, seeded.name);
  await fillCertifiedShape(form);
  await form.getByRole("button", { name: "Preview" }).click();
  const digest = await page.locator(".scripts-digest dd").nth(1).textContent();

  const downloadEvent = page.waitForEvent("download");
  await form.getByRole("button", { name: "Download backup" }).click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toMatch(/-gpmc-backup-scripts\.zip$/);
  const { createHash } = await import("node:crypto");
  const { readFile } = await import("node:fs/promises");
  const bytes = await readFile(await download.path());
  expect(createHash("sha256").update(bytes).digest("hex")).toBe(digest);
});

test("a refusal outside the lane is shown, not hidden", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo, "refusal");
  const form = await openForPolicy(page, seeded.name);
  await form.getByRole("button", { name: "Add startup script" }).click();
  const row = form.getByRole("group", {
    name: "Startup script 1",
    exact: true,
  });
  await row.getByLabel("Command").fill("a.cmd");
  await row.getByLabel("Parameters").fill("x & y");
  await form.getByRole("button", { name: "Preview" }).click();
  await expect(form.locator(".form-error")).toContainText("metacharacter");
  await expect(page.locator("#scripts-results")).toBeEmpty();

  // An empty row is refused before anything is sent.
  await row.getByLabel("Command").fill("");
  await form.getByRole("button", { name: "Preview" }).click();
  await expect(form.locator(".form-error")).toContainText(
    "Startup script 1 has no command",
  );
});

test("a policy outside the measured shape cannot export scripts", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo, "other-content");
  const guid = seeded.payload.gpo.guid;
  const added = await request.post(`/api/gpos/${guid}/settings`, {
    data: {
      expected_revision: 1,
      actor: "browser-test",
      reason: "Give the policy registry content",
      setting: {
        side: "computer",
        hive: "HKLM",
        key: "Software\\Policies\\ScriptsJourney",
        value_name: "Enabled",
        registry_type: "REG_DWORD",
        value: "1",
        action: "set",
      },
    },
  });
  expect(added.status()).toBe(201);
  const form = await openForPolicy(page, seeded.name);
  await expect(page.locator("#scripts-availability")).toContainText(
    "registry settings or preferences",
  );
  await expect(form.getByRole("button", { name: "Preview" })).toBeDisabled();
  await expect(
    form.getByRole("button", { name: "Download backup" }),
  ).toBeDisabled();
});

test("works from the keyboard: add, reorder, remove, close", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo, "keyboard");
  await page.goto("/");
  await page.locator(".gpo-item", { hasText: seeded.name }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: seeded.name }),
  ).toBeVisible();

  const opener = page.getByRole("button", { name: "Scripts", exact: true });
  await opener.focus();
  await page.keyboard.press("Enter");
  const form = page.locator("#scripts-form");
  await expect(form).toBeVisible();

  const add = form.getByRole("button", { name: "Add startup script" });
  await add.focus();
  await page.keyboard.press("Enter");
  // Focus moves into the new row's first field.
  await expect(
    form
      .getByRole("group", { name: "Startup script 1", exact: true })
      .getByLabel("Command"),
  ).toBeFocused();
  await page.keyboard.type("first.cmd");
  await add.focus();
  await page.keyboard.press("Enter");
  await page.keyboard.type("second.cmd");

  // Move the second row up; focus follows the row to the control that is
  // still usable there.
  await form.getByRole("button", { name: "Move up: Startup script 2" }).focus();
  await page.keyboard.press("Enter");
  await expect(
    form.getByRole("button", { name: "Move down: Startup script 1" }),
  ).toBeFocused();
  await expect(
    form
      .getByRole("group", { name: "Startup script 1", exact: true })
      .getByLabel("Command"),
  ).toHaveValue("second.cmd");

  await form.getByRole("button", { name: "Remove: Startup script 1" }).focus();
  await page.keyboard.press("Enter");
  await expect(
    form
      .getByRole("group", { name: "Startup script 1", exact: true })
      .getByLabel("Command"),
  ).toHaveValue("first.cmd");
  await expect(
    form
      .getByRole("group", { name: "Startup script 1", exact: true })
      .getByLabel("Command"),
  ).toBeFocused();

  await page.keyboard.press("Escape");
  await expect(page.locator("#scripts-dialog")).not.toBeVisible();
  await expect(opener).toBeFocused();
});

test("stays usable at 390px and meets the accessibility bar", async ({
  page,
  request,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const seeded = await seedPolicy(request, testInfo, "narrow");
  await page.goto("/");
  await page.locator(".gpo-item", { hasText: seeded.name }).click();
  await page.getByRole("button", { name: "Scripts", exact: true }).click();
  const form = page.locator("#scripts-form");
  await fillCertifiedShape(form);
  await form
    .getByRole("group", { name: "Startup script 1", exact: true })
    .getByLabel("Parameters")
    .fill(`/c ${"synthetic-argument-".repeat(12)}`);
  await form.getByRole("button", { name: "Preview" }).click();
  await expect(page.locator("#scripts-results")).toContainText(
    "StartExecutePSFirst=true",
  );
  expect(
    await form.evaluate(
      (element) => element.scrollWidth <= element.clientWidth + 1,
    ),
  ).toBe(true);
  // The dialog itself fits the viewport. (The page behind it is not
  // measured: the workspace rail can overflow at this width when a policy
  // name is long, which predates these panels.)
  expect(
    await page.locator("#scripts-dialog").evaluate((element) => {
      const box = element.getBoundingClientRect();
      return (
        box.left >= 0 &&
        box.right <= element.ownerDocument.documentElement.clientWidth + 1
      );
    }),
  ).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("scripts-narrow.png") });

  const audit = await new AxeBuilder({ page }).analyze();
  expect(
    audit.violations.filter((violation) =>
      ["serious", "critical"].includes(violation.impact),
    ),
  ).toEqual([]);
});
