import { expect, test } from "@playwright/test";

// Browser runtime coverage for Group Policy Preferences authoring and the
// revision diff rendering of GPP changes (WI-003). The existing
// workspace.spec.mjs exercises registry policy, security, history, and export;
// the Preferences tab (GPP groups, GPP registry, item-level targeting) and the
// GPP sections of the revision diff had no end-to-end coverage. These tests
// drive the real UI against a real workspace through the public API seeder.

async function seedPolicy(request, testInfo) {
  const name = `Synthetic GPP Policy ${testInfo.project.name} ${Date.now()}`;
  const response = await request.post("/api/gpos", {
    data: {
      name,
      description: "Synthetic GPP browser automation fixture",
      actor: "browser-test",
      reason: "Seed GPP browser test through the public API",
    },
  });
  expect(response.status()).toBe(201);
  return { name, payload: await response.json() };
}

async function openPreferences(page, seeded) {
  await page.goto("/");
  await page.locator(".gpo-item", { hasText: seeded.name }).click();
  await page.getByRole("tab", { name: /Preferences/ }).click();
}

test("authors a GPP local group with a member through the browser", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo);
  await openPreferences(page, seeded);

  await page.getByRole("button", { name: "＋ Add group" }).click();
  const dialog = page.getByRole("dialog", { name: "Add group" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Group name").fill("LabAdmins");
  await dialog.getByLabel("SID", { exact: true }).fill("S-1-5-32-544");
  // Action defaults to "update" (a non-broad membership change), so no
  // broad-change confirmation is expected.
  await dialog.getByRole("button", { name: "＋ Add member" }).click();
  await dialog
    .getByPlaceholder("SID (required)")
    .fill("S-1-5-21-99-99-99-1001");
  await dialog.getByPlaceholder("Name", { exact: true }).fill("Lab Operator");
  await dialog.getByRole("button", { name: "Save group" }).click();

  await expect(dialog).toBeHidden();
  const table = page.locator("#gpp-groups-table");
  await expect(table).toContainText("LabAdmins");
  await expect(table).toContainText("update");
  await expect(table).toContainText("S-1-5-32-544");
  await expect(table).toContainText("Lab Operator");
  await expect(page.getByText("Revision 2", { exact: true })).toBeVisible();
});

test("authors a GPP registry preference with item-level targeting", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo);
  await openPreferences(page, seeded);

  await page.getByRole("button", { name: "＋ Add registry" }).click();
  const dialog = page.getByRole("dialog", { name: "Add registry" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Registry key").fill("SOFTWARE\\LabApp\\Settings");

  const valueRow = dialog.locator("#gpp-values-list .gpp-row");
  await valueRow.locator('[data-field="name"]').fill("Enabled");
  await valueRow.locator('[data-field="type"]').selectOption("REG_DWORD");
  await valueRow.locator('[data-field="value"]').fill("1");

  await dialog.getByRole("button", { name: "＋ Add predicate" }).click();
  const iltRow = dialog.locator("#gpp-ilt-registry-list .gpp-row");
  await iltRow
    .locator('[data-field="value"]')
    .fill("OU=Lab,DC=studio,DC=local");
  await expect(page.locator("#gpp-ilt-registry-preview")).toContainText(
    "Member of OU=Lab,DC=studio,DC=local",
  );

  await dialog.getByRole("button", { name: "Save registry" }).click();

  await expect(dialog).toBeHidden();
  const table = page.locator("#gpp-registry-table");
  await expect(table).toContainText("SOFTWARE\\LabApp\\Settings");
  await expect(table).toContainText("Enabled=1");
  await expect(table).toContainText("ILT: 1 predicate");
  await expect(page.getByText("Revision 2", { exact: true })).toBeVisible();
});

test("rejects a non-integer REG_DWORD value inline before saving", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo);
  await openPreferences(page, seeded);

  await page.getByRole("button", { name: "＋ Add registry" }).click();
  const dialog = page.getByRole("dialog", { name: "Add registry" });
  await dialog.getByLabel("Registry key").fill("SOFTWARE\\LabApp\\Bad");
  const valueRow = dialog.locator("#gpp-values-list .gpp-row");
  await valueRow.locator('[data-field="name"]').fill("Broken");
  await valueRow.locator('[data-field="type"]').selectOption("REG_DWORD");
  await valueRow.locator('[data-field="value"]').fill("not-a-number");
  await dialog.getByRole("button", { name: "Save registry" }).click();

  // The dialog stays open and surfaces the validation error; no revision is
  // created (the policy is still at Revision 1).
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("must be a non-negative decimal integer");
  await expect(page.getByText("Revision 1", { exact: true })).toBeVisible();
});

test("renders GPP additions in the revision diff", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo);
  await openPreferences(page, seeded);

  // Author a GPP group so revision 2 differs from revision 1 in the GPP plane.
  await page.getByRole("button", { name: "＋ Add group" }).click();
  const dialog = page.getByRole("dialog", { name: "Add group" });
  await dialog.getByLabel("Group name").fill("DiffAdmins");
  await dialog.getByLabel("SID", { exact: true }).fill("S-1-5-32-544");
  await dialog.getByRole("button", { name: "Save group" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText("Revision 2", { exact: true })).toBeVisible();

  await page.getByRole("tab", { name: "Diff" }).click();
  await page.getByLabel("From revision").selectOption("1");
  await page.getByLabel("To revision").selectOption("2");
  await page.getByRole("button", { name: "Compare revisions" }).click();

  const results = page.locator("#revision-diff-results");
  await expect(results).toContainText("GPP groups");
  await expect(results).toContainText("DiffAdmins");
});

test("authors a GPP registry with REG_MULTI_SZ value", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo);
  await openPreferences(page, seeded);

  await page.getByRole("button", { name: "＋ Add registry" }).click();
  const dialog = page.getByRole("dialog", { name: "Add registry" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Registry key").fill("SOFTWARE\\LabApp\\MultiString");

  const valueRow = dialog.locator("#gpp-values-list .gpp-row");
  await valueRow.locator('[data-field="name"]').fill("AllowedHosts");
  await valueRow.locator('[data-field="type"]').selectOption("REG_MULTI_SZ");
  await valueRow.locator('[data-field="value"]').fill("host-a;host-b;host-c");

  await dialog.getByRole("button", { name: "Save registry" }).click();

  await expect(dialog).toBeHidden();
  const table = page.locator("#gpp-registry-table");
  await expect(table).toContainText("SOFTWARE\\LabApp\\MultiString");
  await expect(table).toContainText("AllowedHosts=host-a;host-b;host-c");
  await expect(page.getByText("Revision 2", { exact: true })).toBeVisible();
});

test("authors a GPP group in the user scope", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo);
  await openPreferences(page, seeded);

  await page.locator('.gpp-scope-row .chip[data-gpp-scope="user"]').click();

  await page.getByRole("button", { name: "＋ Add group" }).click();
  const dialog = page.getByRole("dialog", { name: "Add group" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Group name").fill("UserScopeAdmins");
  await dialog.getByLabel("SID", { exact: true }).fill("S-1-5-32-544");
  await dialog.getByRole("button", { name: "Save group" }).click();

  await expect(dialog).toBeHidden();
  const table = page.locator("#gpp-groups-table");
  await expect(table).toContainText("UserScopeAdmins");
  await expect(page.getByText("Revision 2", { exact: true })).toBeVisible();
});

test("cancel closes dialog without creating a revision", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo);
  await openPreferences(page, seeded);

  await page.getByRole("button", { name: "＋ Add group" }).click();
  const dialog = page.getByRole("dialog", { name: "Add group" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Group name").fill("ShouldNotPersist");
  await dialog.getByLabel("SID", { exact: true }).fill("S-1-5-32-544");

  await dialog.getByRole("button", { name: "Cancel" }).click();

  await expect(dialog).toBeHidden();
  await expect(page.locator("#gpp-groups-table")).not.toContainText(
    "ShouldNotPersist",
  );
  await expect(page.getByText("Revision 1", { exact: true })).toBeVisible();
});

test("stale write is rejected with conflict message", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo);
  const guid = seeded.payload.gpo.guid;
  await openPreferences(page, seeded);

  await page.getByRole("button", { name: "＋ Add group" }).click();
  const dialog = page.getByRole("dialog", { name: "Add group" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Group name").fill("StaleGroup");
  await dialog.getByLabel("SID", { exact: true }).fill("S-1-5-32-544");

  const concurrent = await request.patch(`/api/gpos/${guid}`, {
    data: {
      expected_revision: 1,
      actor: "other-session",
      reason: "Concurrent edit behind the UI",
      name: seeded.name,
      description: "Changed concurrently",
      computer_enabled: true,
      user_enabled: true,
      status: "draft",
    },
  });
  expect(concurrent.status()).toBe(200);

  await dialog.getByRole("button", { name: "Save group" }).click();

  const conflict = page.getByRole("dialog", {
    name: "Review changes before reapplying",
  });
  await expect(conflict).toBeVisible();
  await expect(conflict).toContainText("Your form revision");
  await expect(conflict).toContainText("Current workspace revision");
  await expect(conflict).toContainText("Unsaved fields retained");
});

test("removes a GPP group member", async ({ page, request }, testInfo) => {
  const seeded = await seedPolicy(request, testInfo);
  await openPreferences(page, seeded);

  await page.getByRole("button", { name: "＋ Add group" }).click();
  const dialog = page.getByRole("dialog", { name: "Add group" });
  await dialog.getByLabel("Group name").fill("MemberTestGroup");
  await dialog.getByLabel("SID", { exact: true }).fill("S-1-5-32-544");
  await dialog.getByRole("button", { name: "＋ Add member" }).click();
  await dialog
    .getByPlaceholder("SID (required)")
    .fill("S-1-5-21-99-99-99-1001");
  await dialog.getByPlaceholder("Name", { exact: true }).fill("Temp Member");
  await dialog.getByRole("button", { name: "Save group" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.locator("#gpp-groups-table")).toContainText("Temp Member");

  await page
    .locator("#gpp-groups-table")
    .getByRole("button", { name: "Edit group MemberTestGroup" })
    .click();
  const editDialog = page.getByRole("dialog", { name: "Edit group" });
  await expect(editDialog).toBeVisible();

  await editDialog
    .locator("#gpp-members-list .gpp-row")
    .getByRole("button", { name: "Remove member row" })
    .click();
  await expect(editDialog.locator("#gpp-members-list .gpp-row")).toHaveCount(0);

  await editDialog.getByRole("button", { name: "Save group" }).click();
  await expect(editDialog).toBeHidden();
  await expect(page.locator("#gpp-groups-table")).not.toContainText(
    "Temp Member",
  );
  await expect(page.getByText("Revision 3", { exact: true })).toBeVisible();
});
