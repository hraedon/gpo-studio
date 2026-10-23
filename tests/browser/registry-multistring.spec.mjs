import { expect, test } from "@playwright/test";

async function seed(request) {
  const name = `Synthetic Raw Multi-string ${Date.now()}`;
  const created = await request.post("/api/gpos", {
    data: { name, actor: "browser-test", reason: "Multi-string preservation" },
  });
  expect(created.status()).toBe(201);
  const payload = await created.json();
  const values = [
    "alpha;beta",
    " gamma ",
    "",
    "first\nsecond",
    'quote " and \\ path',
  ];
  const saved = await request.post(`/api/gpos/${payload.gpo.guid}/settings`, {
    data: {
      actor: "browser-test",
      reason: "Seed exact multi-string values",
      expected_revision: 1,
      setting: {
        side: "computer",
        hive: "HKLM",
        key: "Software\\Policies\\Synthetic\\Multi",
        value_name: "Members",
        registry_type: "REG_MULTI_SZ",
        value: values,
        action: "set",
      },
    },
  });
  expect(saved.status()).toBe(201);
  return { name, guid: payload.gpo.guid, values };
}

async function edit(page, seeded) {
  await page.goto("/");
  await page.locator(".gpo-item", { hasText: seeded.name }).click();
  await page.getByRole("tab", { name: /^Policy settings/ }).click();
  await page.locator("[data-edit-setting]").click();
  return page.locator("#setting-dialog");
}

test("@smoke unchanged registry multi-string save preserves exact members", async ({
  page,
  request,
}) => {
  const seeded = await seed(request);
  const dialog = await edit(page, seeded);
  expect(
    JSON.parse(await dialog.locator('[name="value"]').inputValue()),
  ).toEqual(seeded.values);
  await dialog.getByRole("button", { name: "Save setting" }).click();
  await expect(dialog).toBeHidden();
  const saved = await (await request.get(`/api/gpos/${seeded.guid}`)).json();
  expect(saved.gpo.settings[0].value).toEqual(seeded.values);
  expect(saved.gpo.revision).toBe(3);
});

test("registry multi-string accepts exact JSON edits and rejects malformed values before saving", async ({
  page,
  request,
}) => {
  const seeded = await seed(request);
  const dialog = await edit(page, seeded);
  let mutations = 0;
  page.on("request", (request) => {
    if (
      request.method() === "PUT" &&
      request.url().includes(`/api/gpos/${seeded.guid}/settings/`)
    )
      mutations++;
  });
  for (const invalid of ["alpha;beta", '["alpha", 1]', '{"alpha":"beta"}']) {
    await dialog.locator('[name="value"]').fill(invalid);
    await dialog.getByRole("button", { name: "Save setting" }).click();
    await expect(dialog.locator(".form-error")).toContainText(
      /(JSON array|must be a string)/,
    );
    await expect(dialog.locator('[name="value"]')).toHaveAttribute(
      "aria-describedby",
      /value-help/,
    );
  }
  expect(mutations).toBe(0);
  const updated = [" one;two ", "", "line1\nline2"];
  await dialog.locator('[name="value"]').fill(JSON.stringify(updated));
  await dialog.getByRole("button", { name: "Save setting" }).click();
  await expect(dialog).toBeHidden();
  expect(mutations).toBe(1);
  const saved = await (await request.get(`/api/gpos/${seeded.guid}`)).json();
  expect(saved.gpo.settings[0].value).toEqual(updated);
});

test("deleting a multi-string value does not require valid editor JSON", async ({
  page,
  request,
}) => {
  const seeded = await seed(request);
  const dialog = await edit(page, seeded);
  await dialog.locator('[name="value"]').fill("not JSON");
  await dialog
    .getByRole("combobox", { name: "Action", exact: true })
    .selectOption("delete");
  await expect(dialog.locator('[name="value"]')).toBeDisabled();
  await dialog.getByRole("button", { name: "Save setting" }).click();
  await expect(dialog).toBeHidden();
  const saved = await (await request.get(`/api/gpos/${seeded.guid}`)).json();
  expect(saved.gpo.settings[0].action).toBe("delete");
});

test("single-empty multi-string guidance states the Registry.pol distinction", async ({
  page,
  request,
}) => {
  const seeded = await seed(request);
  const dialog = await edit(page, seeded);
  await dialog.locator('[name="value"]').fill('[""]');
  await expect(dialog.locator("#value-help")).toContainText(
    '[""] exports to Registry.pol as []',
  );
  await dialog.getByRole("button", { name: "Save setting" }).click();
  await expect(dialog).toBeHidden();
  const saved = await (await request.get(`/api/gpos/${seeded.guid}`)).json();
  expect(saved.gpo.settings[0].value).toEqual([""]);
});
