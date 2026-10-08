import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

// One end-to-end pass over the publication preview (Plan 034). The coverage
// marks themselves are held to the publication-completeness lane by
// `tests/test_publication_surface.py`; this spec checks what an operator
// sees: that nothing here writes is said first, that the limits sit above
// the steps, that steps are grouped by where they would act with their
// coverage in words, and that a refusal reads as one.

async function seedPolicy(request, testInfo, label) {
  const name = `Synthetic Publication ${label} ${testInfo.project.name} ${Date.now()}`;
  const created = await request.post("/api/gpos", {
    data: { name, actor: "browser-test", reason: "Seed publication test" },
  });
  expect(created.status()).toBe(201);
  const guid = (await created.json()).gpo.guid;
  const setting = await request.post(`/api/gpos/${guid}/settings`, {
    data: {
      expected_revision: 1,
      actor: "browser-test",
      reason: "Registry content to publish",
      setting: {
        side: "computer",
        hive: "HKLM",
        key: "Software\\Policies\\PublicationJourney",
        value_name: "Enabled",
        registry_type: "REG_DWORD",
        value: "1",
        action: "set",
      },
    },
  });
  expect(setting.status()).toBe(201);
  const link = await request.post(`/api/gpos/${guid}/links`, {
    data: {
      expected_revision: 2,
      actor: "browser-test",
      reason: "A link, which no lane has measured",
      link: { target: "OU=Servers,DC=synthetic,DC=test" },
    },
  });
  expect(link.status()).toBe(201);
  return { name, guid };
}

async function openPreview(page, name) {
  await page.goto("/");
  await page.locator(".gpo-item", { hasText: name }).click();
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
  await page.getByRole("button", { name: "Publication preview" }).click();
  const dialog = page.locator("#publication-dialog");
  await expect(dialog).toBeVisible();
  await expect(page.locator("#publication-status")).toContainText("Plan ready");
  return dialog;
}

test("shows the plan, its coverage and that nothing writes @smoke", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo, "plan");
  const dialog = await openPreview(page, seeded.name);
  await expect(dialog.locator("#publication-banner")).toContainText(
    "Nothing here writes.",
  );

  const results = page.locator("#publication-results");
  await expect(
    results.getByRole("heading", { name: "SYSVOL", exact: true }),
  ).toBeVisible();
  await expect(
    results.getByRole("heading", { name: "Active Directory", exact: true }),
  ).toBeVisible();
  const registry = results.locator(".publication-step", {
    hasText: "write_registry_pol",
  });
  await expect(registry).toContainText("Measured");
  await expect(registry).toContainText("Machine/Registry.pol");
  const link = results.locator(".publication-step", {
    hasText: "update_gplink",
  });
  await expect(link).toContainText("Unmeasured");
  await expect(results).toContainText("ad_side_steps_unmeasured");
  await expect(results).toContainText("nothing_here_writes");

  const html = await results.innerHTML();
  expect(html.indexOf("What this answer does not say")).toBeLessThan(
    html.indexOf("<h3>Steps</h3>"),
  );
  expect(html).not.toContain("plan-");
});

test("a SYSVOL-only plan reads as refused", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo, "refused");
  const dialog = await openPreview(page, seeded.name);
  await dialog.getByLabel("Publish to").selectOption("sysvol");
  const results = page.locator("#publication-results");
  await expect(results).toContainText("The planner refuses this publication");
  await expect(results).toContainText("extension_lists_unreachable");
  await expect(
    results.locator(".publication-step", {
      hasText: "extension_lists_unreachable",
    }),
  ).toContainText("Refused");
});

test("works from the keyboard and returns focus", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo, "keyboard");
  await page.goto("/");
  await page.locator(".gpo-item", { hasText: seeded.name }).click();
  const opener = page.getByRole("button", { name: "Publication preview" });
  await opener.focus();
  await page.keyboard.press("Enter");
  const dialog = page.locator("#publication-dialog");
  await expect(dialog).toBeVisible();
  await expect(page.locator("#publication-target")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(opener).toBeFocused();
});

test("stays usable at 390px and meets the accessibility bar", async ({
  page,
  request,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const seeded = await seedPolicy(request, testInfo, "narrow");
  const dialog = await openPreview(page, seeded.name);
  const form = dialog.locator("form");
  expect(
    await form.evaluate(
      (element) => element.scrollWidth <= element.clientWidth + 1,
    ),
  ).toBe(true);
  // The dialog itself fits the viewport. (The page behind it is not
  // measured: the workspace rail can overflow at this width when a policy
  // name is long, which predates these panels.)
  expect(
    await page.locator("#publication-dialog").evaluate((element) => {
      const box = element.getBoundingClientRect();
      return (
        box.left >= 0 &&
        box.right <= element.ownerDocument.documentElement.clientWidth + 1
      );
    }),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("publication-narrow.png"),
  });

  const audit = await new AxeBuilder({ page }).analyze();
  expect(
    audit.violations.filter((violation) =>
      ["serious", "critical"].includes(violation.impact),
    ),
  ).toEqual([]);

  await dialog
    .getByRole("button", { name: "Close", exact: true })
    .last()
    .click();
  await expect(dialog).not.toBeVisible();
});
