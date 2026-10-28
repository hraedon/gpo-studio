import { expect, test } from "@playwright/test";

const originalValues = ["alpha;beta", " gamma ", "", 'quote" and slash\\'];

async function seedRegistry(request, testInfo) {
  const name = `Synthetic Multi-string ${testInfo.project.name} ${Date.now()}`;
  const created = await request.post("/api/gpos", {
    data: {
      name,
      actor: "browser-test",
      reason: "Seed synthetic multi-string browser regression",
    },
  });
  expect(created.status()).toBe(201);
  const { gpo } = await created.json();
  const seeded = await request.post(
    `/api/gpos/${gpo.guid}/preferences/registry`,
    {
      data: {
        actor: "browser-test",
        reason: "Seed lossless multi-string values",
        expected_revision: gpo.revision,
        scope: "computer",
        registry: {
          key: "SOFTWARE\\Synthetic\\MultiString",
          hive: "HKEY_LOCAL_MACHINE",
          action: "update",
          value: {
            name: "SyntheticValues",
            registry_type: "REG_MULTI_SZ",
            action: "update",
            value: originalValues,
          },
        },
      },
    },
  );
  expect(seeded.status(), await seeded.text()).toBe(201);
  const { gpo: current } = await seeded.json();
  return { name, gpo: current };
}

async function openEditor(page, seeded) {
  await page.goto("/");
  await page.locator(".gpo-item", { hasText: seeded.name }).click();
  await page.getByRole("tab", { name: /Preferences/ }).click();
  await page.getByRole("button", { name: /^Edit registry item/ }).click();
  return page.getByRole("dialog", { name: "Edit registry", exact: true });
}

async function currentValues(request, guid) {
  const response = await request.get(`/api/gpos/${guid}`);
  expect(response.status()).toBe(200);
  const { gpo } = await response.json();
  const collection = gpo.gpp_collections.find(
    (item) => item.scope === "computer",
  );
  return { values: collection.registry[0].value.value, revision: gpo.revision };
}

test("@smoke unchanged GPP multi-string edit preserves every string", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedRegistry(request, testInfo);
  const dialog = await openEditor(page, seeded);
  await expect(dialog.getByLabel("Registry value data")).toHaveValue(
    JSON.stringify(originalValues),
  );
  await expect(dialog.locator("[data-value-help]")).toContainText(
    "JSON array of strings",
  );
  await dialog.getByRole("button", { name: "Save registry" }).click();
  await expect(dialog).toBeHidden();
  expect((await currentValues(request, seeded.gpo.guid)).values).toEqual(
    originalValues,
  );
});

test("GPP multi-string JSON edits preserve data and allow an empty list", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedRegistry(request, testInfo);
  const replacements = ["", " revised;value ", "first second", "", "last"];
  let dialog = await openEditor(page, seeded);
  await dialog
    .getByLabel("Registry value data")
    .fill(JSON.stringify(replacements));
  await dialog.getByRole("button", { name: "Save registry" }).click();
  await expect(dialog).toBeHidden();
  expect((await currentValues(request, seeded.gpo.guid)).values).toEqual(
    replacements,
  );

  await page.getByRole("button", { name: /^Edit registry item/ }).click();
  dialog = page.getByRole("dialog", { name: "Edit registry", exact: true });
  await dialog.getByLabel("Registry value data").fill("[]");
  await dialog.getByRole("button", { name: "Save registry" }).click();
  await expect(dialog).toBeHidden();
  expect((await currentValues(request, seeded.gpo.guid)).values).toEqual([]);
});

test("invalid GPP multi-string JSON stays in the form without a mutation", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedRegistry(request, testInfo);
  const dialog = await openEditor(page, seeded);
  const writes = [];
  page.on("request", (outgoing) => {
    if (
      outgoing.method() === "PUT" &&
      outgoing.url().includes("/preferences/registry/")
    )
      writes.push(outgoing);
  });
  for (const invalid of [
    "alpha;beta",
    '["alpha",2]',
    '{"value":"alpha"}',
    "null",
  ]) {
    await dialog.getByLabel("Registry value data").fill(invalid);
    await dialog.getByRole("button", { name: "Save registry" }).click();
    await expect(dialog.locator(".form-error")).toContainText(
      "REG_MULTI_SZ must be a JSON array",
    );
    await expect(dialog.getByLabel("Registry value data")).toHaveValue(invalid);
    await expect(dialog.getByLabel("Registry value data")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    await expect(dialog).toBeVisible();
  }
  expect(writes).toHaveLength(0);
  expect(await currentValues(request, seeded.gpo.guid)).toEqual({
    values: originalValues,
    revision: seeded.gpo.revision,
  });
});

test("GPP multi-string keeps embedded newlines intact for server validation", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedRegistry(request, testInfo);
  const dialog = await openEditor(page, seeded);
  const values = ["one\ntwo", "", " spaced;entry "];
  await dialog.getByLabel("Registry value data").fill(JSON.stringify(values));
  const outgoing = page.waitForRequest(
    (candidate) =>
      candidate.method() === "PUT" &&
      candidate.url().includes("/preferences/registry/"),
  );
  await dialog.getByRole("button", { name: "Save registry" }).click();
  expect((await outgoing).postDataJSON().registry.value.value).toEqual(values);
  // The existing server refuses control characters in GPP XML values. Keep
  // that validation visible and preserve the exact draft for correction.
  await expect(dialog.locator(".form-error")).toContainText(
    "control, format or separator",
  );
  await expect(dialog.getByLabel("Registry value data")).toHaveValue(
    JSON.stringify(values),
  );
  expect(await currentValues(request, seeded.gpo.guid)).toEqual({
    values: originalValues,
    revision: seeded.gpo.revision,
  });
});
