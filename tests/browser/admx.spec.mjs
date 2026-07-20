import { expect, test } from "@playwright/test";

// Browser runtime coverage for the Administrative Templates surface. This tab
// had NO end-to-end coverage at all: workspace.spec.mjs covers registry policy,
// security, history and export, and gpp.spec.mjs covers Preferences, but the
// ADMX search / detail / configure flow was never driven. The browser server
// now seeds a synthetic ADMX pack so the tab renders something real.
//
// What these tests pin, all of which was previously unverifiable in a browser:
//   * presentation controls resolved from the ADML presentationTable
//   * the Enabled / Disabled / Not configured state selector
//   * element options hidden and disabled outside the Enabled state
//   * a policy with no elements still being configurable

async function seedPolicy(request, testInfo) {
  const name = `Synthetic ADMX Target ${testInfo.project.name} ${Date.now()}`;
  const response = await request.post("/api/gpos", {
    data: {
      name,
      description: "Synthetic ADMX browser automation fixture",
      actor: "browser-test",
      reason: "Seed ADMX browser test through the public API",
    },
  });
  expect(response.status()).toBe(201);
  return { name, payload: await response.json() };
}

// The tabs live inside a selected GPO, so a policy must be opened from one.
async function openPolicy(page, seeded, displayName) {
  await page.goto("/");
  await page.locator(".gpo-item", { hasText: seeded.name }).click();
  await page.getByRole("tab", { name: "ADMX" }).click();
  await page.locator(".admx-result", { hasText: displayName }).click();
  await expect(page.locator("#admx-detail")).toBeVisible();
}

async function openConfigureDialog(page) {
  await page.locator("#admx-configure-btn").click();
  const dialog = page.locator("#configure-dialog");
  await expect(dialog).toBeVisible();
  return dialog;
}

async function configure(page, seeded, { state, subOption }) {
  const dialog = await openConfigureDialog(page);
  await dialog.locator("#configure-state").selectOption(state);
  if (subOption !== undefined) {
    await dialog.locator("[data-elem-id]").setChecked(subOption);
  }
  await dialog
    .locator("#configure-target-gpo")
    .selectOption({ value: seeded.payload.gpo.guid });
  await dialog.getByRole("button", { name: "Apply policy" }).click();
  await expect(dialog).toBeHidden();
}

test("resolves presentation controls from the ADML presentation table @smoke", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo);
  await openPolicy(page, seeded, "Synthetic feature with options");
  // The label lives only in the ADML presentationTable; an inline-only parser
  // renders no control here at all.
  await expect(page.locator("#admx-detail")).toContainText(
    "Enable the sub option",
  );
});

test("shows the policy namespace on the detail view", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo);
  await openPolicy(page, seeded, "Synthetic feature with options");
  await expect(page.locator("#admx-detail")).toContainText(
    "Synthetic.Policies.Browser",
  );
});

test("configures a policy as Enabled with an element value", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo);
  await openPolicy(page, seeded, "Synthetic feature with options");
  await configure(page, seeded, { state: "enabled", subOption: true });

  const response = await request.get(`/api/gpos/${seeded.payload.gpo.guid}`);
  const settings = (await response.json()).gpo.settings;
  const names = settings.map((s) => s.value_name).sort();
  expect(names).toEqual(["FeatureState", "SubOption"]);
});

test("hides and disables element options outside the Enabled state", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo);
  await openPolicy(page, seeded, "Synthetic feature with options");
  const dialog = await openConfigureDialog(page);
  const options = dialog.locator("#configure-options");
  await expect(options).toBeVisible();

  await dialog.locator("#configure-state").selectOption("disabled");
  await expect(options).toBeHidden();
  // Disabled as well as hidden, so the value cannot be submitted and assistive
  // technology does not announce a control that has no effect.
  await expect(dialog.locator("[data-elem-id]")).toBeDisabled();

  await dialog.locator("#configure-state").selectOption("enabled");
  await expect(options).toBeVisible();
  await expect(dialog.locator("[data-elem-id]")).toBeEnabled();
});

test("configures a policy as Disabled, writing the disabled value", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo);
  await openPolicy(page, seeded, "Synthetic feature with options");
  await configure(page, seeded, { state: "disabled" });

  const response = await request.get(`/api/gpos/${seeded.payload.gpo.guid}`);
  const settings = (await response.json()).gpo.settings;
  expect(settings).toHaveLength(1);
  expect(settings[0].value_name).toBe("FeatureState");
  expect(settings[0].value).toBe("0");
});

test("Not configured removes the settings the policy previously wrote", async ({
  page,
  request,
}, testInfo) => {
  const seeded = await seedPolicy(request, testInfo);
  await openPolicy(page, seeded, "Synthetic feature with options");
  await configure(page, seeded, { state: "enabled", subOption: true });
  expect(
    (await (await request.get(`/api/gpos/${seeded.payload.gpo.guid}`)).json())
      .gpo.settings.length,
  ).toBe(2);

  await page.reload();
  await openPolicy(page, seeded, "Synthetic feature with options");
  await configure(page, seeded, { state: "not_configured" });

  const response = await request.get(`/api/gpos/${seeded.payload.gpo.guid}`);
  expect((await response.json()).gpo.settings).toEqual([]);
});

test("a policy with no elements is still configurable", async ({
  page,
  request,
}, testInfo) => {
  // The configure button used to render only when the policy had element
  // fields, so a plain on/off policy could not be configured at all.
  const seeded = await seedPolicy(request, testInfo);
  await openPolicy(page, seeded, "Synthetic toggle without options");
  await expect(page.locator("#admx-configure-btn")).toBeVisible();
  await configure(page, seeded, { state: "enabled" });

  const response = await request.get(`/api/gpos/${seeded.payload.gpo.guid}`);
  const settings = (await response.json()).gpo.settings;
  expect(settings.map((s) => s.value_name)).toEqual(["ToggleState"]);
});
