import {
  cpSync,
  readdirSync,
  readFileSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { join, relative } from "node:path";

import { expect, test } from "@playwright/test";

// Editing an IMPORTED preference item through the workbench (final-r13).
// Retained (imported) content never round-trips through the browser: the
// editor sends typed fields and the ids of the predicates and members it keeps,
// and the server merges the edit onto the stored item. These journeys import a
// committed GPMC capture through the public API -- patched with an OU predicate
// carrying a retained attribute, a raw FilterBattery Studio does not type and a
// FilterDate Studio does not author -- fork it as an operator would, and drive
// the real editor.

const CAPTURE = "tests/fixtures/native-gpp-gpmc/WI01A-LocalGroups-GPMC";
const OU_VALUE = "OU=Lab,DC=studio,DC=test";
const PATCH =
  `<FilterOrgUnit bool="AND" not="0" name="${OU_VALUE}" userContext="1" directMember="0"/>` +
  '<FilterBattery bool="AND" not="0"/>' +
  '<FilterDate bool="AND" not="0" startDate="2026-01-01" endDate="2026-12-31"/>';

function findGroupsFile(root) {
  for (const entry of readdirSync(root)) {
    const path = join(root, entry);
    if (statSync(path).isDirectory()) {
      const found = findGroupsFile(path);
      if (found) return found;
    } else if (
      path.endsWith(join("User", "Preferences", "Groups", "Groups.xml"))
    ) {
      return path;
    }
  }
  return null;
}

// One import per server: the backup's GPO name is the import's, and names are
// unique. Each journey edits its own fork of it.
let importedGuid = null;

async function importOnce(request, testInfo) {
  if (importedGuid) return importedGuid;
  const backup = testInfo.outputPath("backup");
  cpSync(CAPTURE, backup, { recursive: true });
  const groups = findGroupsFile(backup);
  const text = readFileSync(groups, "utf-8");
  expect(text.split("<FilterRunOnce ").length).toBe(2);
  writeFileSync(
    groups,
    text.replace("<FilterRunOnce ", `${PATCH}<FilterRunOnce `),
  );
  // The server runs from the repository root; without an inbox it takes a
  // relative path.
  const imported = await request.post("/api/backups/import", {
    data: {
      path: relative(process.cwd(), backup),
      actor: "browser-test",
      reason: "Import a patched GPMC capture",
    },
  });
  expect(imported.status(), await imported.text()).toBe(201);
  importedGuid = (await imported.json()).gpo.guid;
  return importedGuid;
}

async function importAndFork(request, testInfo) {
  const imported = await importOnce(request, testInfo);
  const name = `Imported fork ${testInfo.project.name} ${testInfo.title.slice(0, 24)} ${Date.now()}`;
  const fork = await request.post(`/api/gpos/${imported}/fork`, {
    data: { name, actor: "browser-test", reason: "Edit the import" },
  });
  expect(fork.status()).toBe(201);
  return { name, guid: (await fork.json()).gpo.guid };
}

async function openUserPreferences(page, seeded) {
  await page.goto("/");
  await page.locator(".gpo-item", { hasText: seeded.name }).click();
  await page.getByRole("tab", { name: /Preferences/ }).click();
  await page.locator('.gpp-scope-row .chip[data-gpp-scope="user"]').click();
}

async function storedGroups(request, guid) {
  const response = await request.get(`/api/gpos/${guid}`);
  const gpo = (await response.json()).gpo;
  return gpo.gpp_collections.find((collection) => collection.scope === "user")
    .groups;
}

function predicateTypes(group) {
  return group.ilt_filter.items.map((item) =>
    typeof item === "string" ? item.match(/<(\w+)/)[1] : item.type,
  );
}

test("deletes a typed predicate of an imported item and keeps the raw one", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await importAndFork(request, testInfo);
  const [before] = await storedGroups(request, seeded.guid);
  expect(predicateTypes(before)).toEqual([
    "group",
    "ou",
    "FilterBattery",
    "date",
  ]);
  const groupAttrs = before.ilt_filter.items[0].unknown_attrs;
  expect(groupAttrs.length).toBeGreaterThan(0);

  await openUserPreferences(page, seeded);
  await page.getByRole("button", { name: "Edit group dev-team" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit group" });
  const ouRow = dialog.locator("#gpp-ilt-list .gpp-row").nth(1);
  await expect(ouRow.locator('[data-field="value"]')).toHaveValue(OU_VALUE);
  await ouRow.getByRole("button", { name: "Remove ILT predicate" }).click();
  await expect(dialog.locator("#gpp-ilt-list .gpp-row")).toHaveCount(3);
  await dialog.getByRole("button", { name: "Save group" }).click();
  await expect(dialog).toBeHidden();

  const [after] = await storedGroups(request, seeded.guid);
  expect(predicateTypes(after)).toEqual(["group", "FilterBattery", "date"]);
  // The surviving predicate keeps exactly its own retained attributes.
  expect(after.ilt_filter.items[0].unknown_attrs).toEqual(groupAttrs);
  expect(
    (await request.get(`/api/gpos/${seeded.guid}/export.zip`)).status(),
  ).toBe(200);
});

test("edits another field of an imported item holding a FilterDate predicate", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await importAndFork(request, testInfo);
  await openUserPreferences(page, seeded);
  await page.getByRole("button", { name: "Edit group dev-team" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit group" });
  await expect(
    dialog.locator('#gpp-ilt-list .gpp-row[data-readonly="true"]'),
  ).toHaveCount(2);
  await expect(dialog.locator(".gpp-retained-note")).toContainText(
    "Imported content Studio does not edit is kept as imported",
  );
  await dialog.getByLabel("Description").fill("Edited beside a FilterDate");
  await dialog.getByRole("button", { name: "Save group" }).click();
  await expect(dialog).toBeHidden();

  const [group] = await storedGroups(request, seeded.guid);
  expect(group.description).toBe("Edited beside a FilterDate");
  expect(predicateTypes(group)).toEqual([
    "group",
    "ou",
    "FilterBattery",
    "date",
  ]);
  expect(group.ilt_filter.items[3].value).toBe("2026-01-01|2026-12-31");
});

test("clones an imported group on the server and opens the copy", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await importAndFork(request, testInfo);
  await openUserPreferences(page, seeded);
  await page.getByRole("button", { name: "Clone group dev-team" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit group" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Group name")).toHaveValue("dev-team (copy)");
  await dialog.getByRole("button", { name: "Cancel" }).click();

  const groups = await storedGroups(request, seeded.guid);
  expect(groups.map((group) => group.name)).toEqual([
    "dev-team",
    "dev-team (copy)",
  ]);
  expect(groups[1].id).not.toBe(groups[0].id);
  expect(predicateTypes(groups[1])).toEqual(predicateTypes(groups[0]));
  expect(
    (await request.get(`/api/gpos/${seeded.guid}/export.zip`)).status(),
  ).toBe(200);
});

test("restores an imported group from a revision", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await importAndFork(request, testInfo);
  await openUserPreferences(page, seeded);
  // Remove the raw predicate, then restore the item as revision 1 held it.
  await page.getByRole("button", { name: "Edit group dev-team" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit group" });
  await dialog
    .locator('#gpp-ilt-list .gpp-row[data-readonly="true"]')
    .first()
    .getByRole("button", { name: "Remove imported ILT predicate" })
    .click();
  await dialog.getByRole("button", { name: "Save group" }).click();
  await expect(dialog).toBeHidden();
  const [edited] = await storedGroups(request, seeded.guid);
  expect(predicateTypes(edited)).toEqual(["group", "ou", "date"]);

  page.on("dialog", (prompt) =>
    prompt.type() === "prompt" ? prompt.accept("1") : prompt.accept(),
  );
  await page
    .getByRole("button", { name: "Restore group dev-team from a revision" })
    .click();
  await expect(page.getByText("Revision 3", { exact: true })).toBeVisible();

  const [restored] = await storedGroups(request, seeded.guid);
  expect(predicateTypes(restored)).toEqual([
    "group",
    "ou",
    "FilterBattery",
    "date",
  ]);
  expect(
    (await request.get(`/api/gpos/${seeded.guid}/export.zip`)).status(),
  ).toBe(200);
});

test("shows each predicate's AND/OR join and warns when a move regroups it", async ({
  page,
  request,
}, testInfo) => {
  // An OR join is authored through the API (the editor writes AND only).
  const name = `OR joins ${testInfo.project.name} ${Date.now()}`;
  const created = await request.post("/api/gpos", {
    data: { name, actor: "browser-test", reason: "Seed an OR join" },
  });
  const gpo = (await created.json()).gpo;
  const added = await request.post(`/api/gpos/${gpo.guid}/preferences/groups`, {
    data: {
      expected_revision: gpo.revision,
      actor: "browser-test",
      reason: "Seed an OR join",
      scope: "computer",
      group: {
        name: "Joined",
        sid: "S-1-5-32-544",
        ilt_filter: {
          items: [
            { type: "ou", value: "OU=A,DC=studio,DC=local" },
            { type: "group", value: "S-1-5-32-545", bool_op: "OR" },
          ],
        },
      },
    },
  });
  expect(added.status()).toBe(201);

  await page.goto("/");
  await page.locator(".gpo-item", { hasText: name }).click();
  await page.getByRole("tab", { name: /Preferences/ }).click();
  await page.getByRole("button", { name: "Edit group Joined" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit group" });
  const rows = dialog.locator("#gpp-ilt-list .gpp-row");
  await expect(rows.nth(1).locator(".gpp-connector")).toHaveText("OR");
  await expect(dialog.locator("#gpp-ilt-preview")).toHaveText(
    "Member of OU=A,DC=studio,DC=local OR [imported: Member of group S-1-5-32-545]",
  );
  await rows
    .nth(1)
    .getByRole("button", { name: "Move ILT predicate earlier" })
    .click();
  await expect(dialog.locator(".gpp-move-warning")).toContainText(
    "changed how AND and OR group the expression",
  );
  await expect(dialog.locator("#gpp-ilt-preview")).toHaveText(
    "[imported: Member of group S-1-5-32-545] AND Member of OU=A,DC=studio,DC=local",
  );
  await dialog.getByRole("button", { name: "Save group" }).click();
  await expect(dialog).toBeHidden();
  const stored = await request.get(`/api/gpos/${gpo.guid}`);
  const items = (await stored.json()).gpo.gpp_collections[0].groups[0]
    .ilt_filter.items;
  expect(items.map((item) => [item.type, item.bool_op])).toEqual([
    ["group", "OR"],
    ["ou", "AND"],
  ]);
});

// Sol final-r15 B2: a raw OR first in the list has no effect (the first join is
// unused); moved second, it joins with OR. The editor must show that and warn.
const REGISTRY_CAPTURE =
  "tests/fixtures/native-gpp-registry-gpmc/WI01A-Registry-GPMC";

function findRegistryFile(root) {
  for (const entry of readdirSync(root)) {
    const path = join(root, entry);
    if (statSync(path).isDirectory()) {
      const found = findRegistryFile(path);
      if (found) return found;
    } else if (path.endsWith(join("Preferences", "Registry", "Registry.xml"))) {
      return path;
    }
  }
  return null;
}

test("moving a raw OR predicate second shows the OR and warns", async ({
  page,
  request,
}, testInfo) => {
  const backup = testInfo.outputPath("registry-backup");
  cpSync(REGISTRY_CAPTURE, backup, { recursive: true });
  const file = findRegistryFile(backup);
  const text = readFileSync(file, "utf-8");
  writeFileSync(
    file,
    text.replace(
      "</Registry>",
      "<Filters><FilterBattery bool='OR' not='0'/>" +
        '<FilterOrgUnit bool="AND" not="0" name="OU=A,DC=studio,DC=test"/>' +
        "</Filters></Registry>",
    ),
  );
  const imported = await request.post("/api/backups/import", {
    data: {
      path: relative(process.cwd(), backup),
      actor: "browser-test",
      reason: "Import a capture with a raw OR first",
    },
  });
  expect(imported.status(), await imported.text()).toBe(201);
  const name = `Raw OR ${testInfo.project.name} ${Date.now()}`;
  const fork = await request.post(
    `/api/gpos/${(await imported.json()).gpo.guid}/fork`,
    { data: { name, actor: "browser-test", reason: "Edit the import" } },
  );
  const guid = (await fork.json()).gpo.guid;

  await page.goto("/");
  await page.locator(".gpo-item", { hasText: name }).click();
  await page.getByRole("tab", { name: /Preferences/ }).click();
  await page
    .locator("#gpp-registry-table")
    .getByRole("button", { name: /^Edit registry item / })
    .first()
    .click();
  const dialog = page.getByRole("dialog", { name: "Edit registry" });
  const rows = dialog.locator("#gpp-ilt-registry-list .gpp-row");
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toHaveAttribute("data-bool", "OR");
  await expect(dialog.locator("#gpp-ilt-registry-preview")).toHaveText(
    "[imported FilterBattery] AND Member of OU=A,DC=studio,DC=test",
  );
  await rows
    .nth(0)
    .getByRole("button", { name: "Move ILT predicate later" })
    .click();
  await expect(dialog.locator(".gpp-move-warning")).toContainText(
    "changed how AND and OR group the expression",
  );
  await expect(dialog.locator("#gpp-ilt-registry-preview")).toHaveText(
    "Member of OU=A,DC=studio,DC=test OR [imported FilterBattery]",
  );
  await expect(rows.nth(1).locator(".gpp-connector")).toHaveText("OR");
  await dialog.getByRole("button", { name: "Save registry" }).click();
  await expect(dialog).toBeHidden();

  const stored = await (await request.get(`/api/gpos/${guid}`)).json();
  const items = stored.gpo.gpp_collections.find(
    (collection) => collection.scope === "computer",
  ).registry[0].ilt_filter.items;
  expect(items[0].type).toBe("ou");
  expect(items[1]).toMatch(/^<FilterBattery bool="OR"/);
});
