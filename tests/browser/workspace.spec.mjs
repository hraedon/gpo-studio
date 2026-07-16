import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

async function seedPolicy(request, testInfo) {
  const name = `Synthetic Browser Policy ${testInfo.project.name} ${Date.now()}`;
  const response = await request.post("/api/gpos", {
    data: {
      name,
      description: "Synthetic browser automation fixture",
      actor: "browser-test",
      reason: "Seed browser test through the public API",
    },
  });
  expect(response.status()).toBe(201);
  return { name, payload: await response.json() };
}

test("loads a synthetic policy from a real workspace @smoke", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo);

  await page.goto("/");
  const policy = page.locator(".gpo-item", { hasText: seeded.name });
  await expect(policy).toBeVisible();
  await policy.click();

  await expect(
    page.getByRole("heading", { level: 1, name: seeded.name }),
  ).toBeVisible();
  await expect(page.getByText("Revision 1", { exact: true })).toBeVisible();
  await expect(page.locator("#validation-list")).toContainText(
    "No validation findings",
  );
  expect(seeded.payload.gpo.revision).toBe(1);
});

test("authors a maximum QWORD through the browser", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo);

  await page.goto("/");
  await page.locator(".gpo-item", { hasText: seeded.name }).click();
  await page.locator('[data-tab="settings"]').click();
  await page.getByRole("button", { name: "Add setting" }).click();

  const dialog = page.getByRole("dialog", { name: "Add policy setting" });
  await dialog
    .getByLabel("Registry key")
    .fill("Software\\Policies\\SyntheticBrowser");
  await dialog.getByLabel("Value name").fill("MaximumQword");
  await dialog.getByLabel("Type").selectOption("REG_QWORD");
  await dialog.locator('textarea[name="value"]').fill("18446744073709551615");
  await dialog.getByLabel("Comment").fill("Synthetic boundary-value coverage");
  await dialog.getByRole("button", { name: "Save setting" }).click();

  await expect(page.getByRole("cell", { name: "MaximumQword" })).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "18446744073709551615" }),
  ).toBeVisible();
  await expect(page.getByText("Revision 2", { exact: true })).toBeVisible();
});

test("reviews a revision diff and export boundary", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo);
  const guid = seeded.payload.gpo.guid;
  const mutation = await request.post(`/api/gpos/${guid}/settings`, {
    data: {
      expected_revision: 1,
      actor: "browser-test",
      reason: "Create revision for comparison",
      setting: {
        side: "computer",
        hive: "HKLM",
        key: "Software\\Policies\\ReviewJourney",
        value_name: "Enabled",
        registry_type: "REG_DWORD",
        value: "1",
        action: "set",
      },
    },
  });
  expect(mutation.status()).toBe(201);

  await page.goto("/");
  await page.locator(".gpo-item", { hasText: seeded.name }).click();
  await page.getByRole("tab", { name: "Diff" }).click();
  await page.getByLabel("From revision").selectOption("1");
  await page.getByLabel("To revision").selectOption("2");
  await page.getByRole("button", { name: "Compare revisions" }).click();
  await expect(page.locator("#revision-diff-results")).toContainText(
    "Settings (1)",
  );

  await page.getByRole("link", { name: "Export bundle" }).click();
  const review = page.getByRole("dialog", { name: "Review export readiness" });
  await expect(review).toContainText("Policy semantic SHA-256");
  await expect(review).toContainText("Review model SHA-256");
  const download = page.waitForEvent("download");
  await review.getByRole("button", { name: "Download export" }).click();
  await download;
});

test("retains a stale security edit and explicitly reapplies it", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo);
  const guid = seeded.payload.gpo.guid;
  await page.goto("/");
  await page.locator(".gpo-item", { hasText: seeded.name }).click();
  await page.getByRole("tab", { name: "Security" }).click();
  await page.getByRole("button", { name: "Add filter" }).click();
  const form = page.getByRole("dialog", { name: "Add security filter" });
  await form.getByLabel("Principal (SID or account)").fill("S-1-5-32-544");

  const concurrent = await request.patch(`/api/gpos/${guid}`, {
    data: {
      expected_revision: 1,
      actor: "other-browser",
      reason: "Concurrent metadata edit",
      name: seeded.name,
      description: "Changed in another browser",
      computer_enabled: true,
      user_enabled: true,
      status: "draft",
    },
  });
  expect(concurrent.status()).toBe(200);
  const concurrentRevision = (await concurrent.json()).gpo.revision;

  await form.getByRole("button", { name: "Save filter" }).click();
  const conflict = page.getByRole("dialog", {
    name: "Review changes before reapplying",
  });
  await expect(conflict).toContainText("Unsaved fields retained");
  await conflict.getByRole("button", { name: "Review and reapply" }).click();
  await expect(form.getByLabel("Principal (SID or account)")).toHaveValue(
    "S-1-5-32-544",
  );
  await form.getByRole("button", { name: "Save filter" }).click();
  await expect(page.getByRole("cell", { name: "S-1-5-32-544" })).toBeVisible();
  await expect
    .poll(async () => {
      const label = await page.locator("#revision").textContent();
      return Number(label?.match(/\d+/)?.[0]);
    })
    .toBeGreaterThan(concurrentRevision);
});

test("restores history as a new append-only revision", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo);
  const guid = seeded.payload.gpo.guid;
  for (const [expected, description] of [
    [1, "revision two"],
    [2, "revision three"],
  ]) {
    const response = await request.patch(`/api/gpos/${guid}`, {
      data: {
        expected_revision: expected,
        actor: "browser-test",
        reason: `Create ${description}`,
        name: seeded.name,
        description,
        computer_enabled: true,
        user_enabled: true,
        status: "draft",
      },
    });
    expect(response.status()).toBe(200);
  }

  await page.goto("/");
  await page.locator(".gpo-item", { hasText: seeded.name }).click();
  await page.getByRole("tab", { name: "History" }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await page.locator('[data-restore="1"]').click();
  await expect(page.locator("#revision")).toHaveText("Revision 4");
  const history = await request.get(`/api/gpos/${guid}/revisions`);
  expect((await history.json()).items.map((item) => item.revision)).toEqual([
    4, 3, 2, 1,
  ]);
});

test("supports keyboard tab and dialog focus semantics", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo);
  await page.goto("/");
  await page.keyboard.press("Tab");
  await expect(page.locator(".skip-link")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();

  await page.locator(".gpo-item", { hasText: seeded.name }).click();
  const overview = page.getByRole("tab", { name: "Overview" });
  await overview.focus();
  await page.keyboard.press("ArrowRight");
  const settings = page.getByRole("tab", { name: /Policy settings/ });
  await expect(settings).toBeFocused();
  await expect(settings).toHaveAttribute("aria-selected", "true");

  const opener = page.getByRole("button", { name: "Add setting" });
  await opener.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Add policy setting" });
  await expect(dialog.getByLabel("Configuration")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(opener).toBeFocused();
});

test("has no serious or critical automated accessibility violations", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo);
  await page.goto("/");
  await page.locator(".gpo-item", { hasText: seeded.name }).click();

  for (const tab of ["Overview", "Policy settings", "Security", "Diff"]) {
    await page.getByRole("tab", { name: new RegExp(tab) }).click();
    const results = await new AxeBuilder({ page }).analyze();
    expect(
      results.violations.filter(({ impact }) =>
        ["serious", "critical"].includes(impact),
      ),
    ).toEqual([]);
  }

  await page.setViewportSize({ width: 360, height: 800 });
  await page.getByRole("tab", { name: /Policy settings/ }).click();
  await page.getByRole("button", { name: "Add setting" }).click();
  const dialogResults = await new AxeBuilder({ page }).analyze();
  expect(
    dialogResults.violations.filter(({ impact }) =>
      ["serious", "critical"].includes(impact),
    ),
  ).toEqual([]);
});
