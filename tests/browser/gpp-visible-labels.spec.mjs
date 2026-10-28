import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

async function seed(page) {
  const response = await page.request.post("/api/gpos", {
    data: {
      name: `Synthetic GPP Labels ${Date.now()}`,
      actor: "browser-test",
      reason: "Synthetic label regression",
    },
  });
  expect(response.status()).toBe(201);
  const { gpo } = await response.json();
  await page.goto("/");
  await page.locator(`[data-guid="${gpo.guid}"]`).click();
  await expect(page.locator("#title")).toHaveText(gpo.name);
  await page.getByRole("tab", { name: /Preferences/ }).click();
  return gpo;
}

async function member(dialog, sid, name) {
  await dialog.locator("#gpp-add-member").click();
  const row = dialog.locator("#gpp-members-list .gpp-row").last();
  await row.getByRole("textbox", { name: "Member SID", exact: true }).fill(sid);
  await row
    .getByRole("textbox", { name: "Member name", exact: true })
    .fill(name);
  await expect(
    row.getByRole("combobox", { name: "Member action", exact: true }),
  ).toHaveValue("add");
  return row;
}

async function predicate(dialog, list, type, value) {
  await dialog
    .locator(
      list === "#gpp-ilt-list" ? "#gpp-add-predicate" : "#gpp-add-ilt-registry",
    )
    .click();
  const row = dialog.locator(`${list} .gpp-row`).last();
  await row
    .getByRole("combobox", { name: "Targeting predicate type", exact: true })
    .selectOption(type);
  await row
    .getByRole("textbox", { name: "Targeting predicate value", exact: true })
    .fill(value);
  return row;
}

async function labelsAndKeyboard(dialog, page) {
  const association = await dialog
    .locator(".gpp-row [data-field]")
    .evaluateAll((controls) =>
      controls.map((control) => ({
        labels: control.labels.length,
        visible: [...control.labels].every(
          (label) => label.getClientRects().length > 0,
        ),
        form: control.form === control.closest("form"),
      })),
    );
  for (const item of association)
    expect(item).toEqual({ labels: 1, visible: true, form: true });
  const body = await dialog.locator(".dialog-body").evaluate((element) => ({
    width: element.clientWidth,
    scroll: element.scrollWidth,
  }));
  expect(body.scroll).toBeLessThanOrEqual(body.width);
  const count = await dialog
    .locator('input:not([type="hidden"]),select,textarea,button')
    .count();
  for (let i = 0; i < count + 2; i++) {
    await page.keyboard.press("Tab");
    const focus = await page.evaluate(() => {
      const active = globalThis.document.activeElement;
      if (!active.matches("input,select,textarea,button")) return null;
      const bounds = active.getBoundingClientRect();
      const top = globalThis.document.elementFromPoint(
        bounds.x + bounds.width / 2,
        bounds.y + bounds.height / 2,
      );
      return {
        html: active.outerHTML,
        visible: active === top || active.contains(top),
      };
    });
    if (focus) expect(focus.visible, focus.html).toBe(true);
  }
  const axe = await new AxeBuilder({ page })
    .include(`#${await dialog.getAttribute("id")}`)
    .analyze();
  expect(axe.violations).toEqual([]);
}

for (const theme of ["light", "dark"]) {
  for (const viewport of [
    { width: 360, height: 640 },
    { width: 1280, height: 800 },
  ]) {
    test(`generated GPP labels remain visible and associated in ${theme} at ${viewport.width}px`, async ({
      page,
    }) => {
      await page.emulateMedia({ colorScheme: theme });
      await page.setViewportSize(viewport);
      const gpo = await seed(page);
      await page.locator("#add-gpp-group").click();
      const group = page.locator("#gpp-group-dialog");
      await group.getByLabel("Group name").fill("Synthetic labeled group");
      const first = await member(
        group,
        "S-1-5-21-1-1-1-1001",
        "Synthetic first",
      );
      await member(group, "S-1-5-21-1-1-1-1002", "Synthetic second");
      await first
        .locator("label.gpp-field")
        .first()
        .click({ position: { x: 5, y: 5 } });
      await expect(first.locator('[data-field="sid"]')).toBeFocused();
      await predicate(
        group,
        "#gpp-ilt-list",
        "ou",
        "OU=Synthetic,DC=example,DC=test",
      );
      const second = await predicate(
        group,
        "#gpp-ilt-list",
        "group",
        "S-1-5-32-544",
      );
      await second
        .getByRole("button", { name: "Move ILT predicate earlier" })
        .click();
      await expect(
        group.locator('#gpp-ilt-list [data-field="type"]').first(),
      ).toHaveValue("group");
      await labelsAndKeyboard(group, page);
      await group.getByRole("button", { name: "Cancel", exact: true }).click();
      await page.locator("#add-gpp-registry").click();
      const registry = page.locator("#gpp-registry-dialog");
      await registry
        .getByLabel("Registry key")
        .fill("Software\\SyntheticLabeledValues");
      await registry
        .getByRole("textbox", { name: "Registry value name", exact: true })
        .fill("Tokens");
      await registry
        .getByRole("combobox", { name: "Registry value type", exact: true })
        .selectOption("REG_MULTI_SZ");
      const data = registry.getByRole("textbox", {
        name: "Registry value data",
        exact: true,
      });
      await data.fill('["alpha;beta", " gamma ", ""]');
      await expect(data).toHaveAttribute("aria-label", "Registry value data");
      await registry
        .getByRole("combobox", { name: "Registry value action", exact: true })
        .selectOption("delete");
      await expect(data).toBeDisabled();
      await registry
        .getByRole("combobox", { name: "Registry value action", exact: true })
        .selectOption("create");
      await expect(data).toBeEnabled();
      await expect(data).toHaveValue('["alpha;beta", " gamma ", ""]');
      await predicate(
        registry,
        "#gpp-ilt-registry-list",
        "ou",
        "OU=Synthetic,DC=example,DC=test",
      );
      await labelsAndKeyboard(registry, page);
      await registry
        .getByRole("button", { name: "Cancel", exact: true })
        .click();
      expect(
        (await (await page.request.get(`/api/gpos/${gpo.guid}`)).json()).gpo
          .revision,
      ).toBe(1);
    });
  }
}

test("labeled rows preserve payload order and pending controls", async ({
  page,
}) => {
  const gpo = await seed(page);
  await page.locator("#add-gpp-group").click();
  const dialog = page.locator("#gpp-group-dialog");
  await dialog.getByLabel("Group name").fill("Synthetic submitted group");
  await member(dialog, "S-1-5-21-1-1-1-1001", "Synthetic first");
  await member(dialog, "S-1-5-21-1-1-1-1002", "Synthetic second");
  await predicate(
    dialog,
    "#gpp-ilt-list",
    "ou",
    "OU=Synthetic,DC=example,DC=test",
  );
  const moved = await predicate(
    dialog,
    "#gpp-ilt-list",
    "group",
    "S-1-5-32-544",
  );
  await moved
    .getByRole("button", { name: "Move ILT predicate earlier" })
    .click();
  let release, arrived;
  const waiting = new Promise((resolve) => {
    release = resolve;
  });
  const sent = new Promise((resolve) => {
    arrived = resolve;
  });
  await page.route(
    `**/api/gpos/${gpo.guid}/preferences/groups`,
    async (route) => {
      const response = await route.fetch();
      arrived(route.request().postDataJSON());
      await waiting;
      await route.fulfill({ response });
    },
  );
  try {
    await dialog.getByRole("button", { name: "Save group" }).click();
    const payload = await sent;
    expect(payload.group.members.map((item) => item.name)).toEqual([
      "Synthetic first",
      "Synthetic second",
    ]);
    expect(payload.group.ilt_filter.items.map((item) => item.type)).toEqual([
      "group",
      "ou",
    ]);
    const enabled = await dialog
      .locator(
        'input:not([type="hidden"]),select,textarea,button:not([value="cancel"])',
      )
      .evaluateAll((controls) =>
        controls
          .filter((control) => !control.disabled)
          .map((control) => control.outerHTML),
      );
    expect(enabled).toEqual([]);
    await expect(
      dialog.getByRole("button", { name: "Cancel", exact: true }),
    ).toBeEnabled();
    release();
    await expect(dialog).toBeHidden();
    await page.locator("#add-gpp-registry").click();
    const registry = page.locator("#gpp-registry-dialog");
    await registry
      .getByLabel("Registry key")
      .fill("Software\\SyntheticLabeledPayload");
    await registry
      .getByRole("textbox", { name: "Registry value name", exact: true })
      .fill("Tokens");
    await registry
      .getByRole("combobox", { name: "Registry value type", exact: true })
      .selectOption("REG_MULTI_SZ");
    const value = ["alpha;beta", " gamma ", ""];
    await registry
      .getByRole("textbox", { name: "Registry value data", exact: true })
      .fill(JSON.stringify(value));
    await registry.getByRole("button", { name: "Save registry" }).click();
    await expect(registry).toBeHidden();
    const saved = (
      await (await page.request.get(`/api/gpos/${gpo.guid}`)).json()
    ).gpo;
    expect(saved.gpp_collections[0].registry[0].value.value).toEqual(value);
    expect(
      saved.gpp_collections[0].groups[0].members.map((item) => item.name),
    ).toEqual(["Synthetic first", "Synthetic second"]);
    expect(
      saved.gpp_collections[0].groups[0].ilt_filter.items.map(
        (item) => item.type,
      ),
    ).toEqual(["group", "ou"]);
  } finally {
    release();
  }
});

test("visible targeting labels retain imported OR row read-only behavior", async ({
  page,
}) => {
  const gpo = await seed(page);
  const response = await page.request.post(
    `/api/gpos/${gpo.guid}/preferences/groups`,
    {
      data: {
        expected_revision: 1,
        actor: "browser-test",
        reason: "Synthetic OR predicate",
        scope: "computer",
        group: {
          name: "Synthetic read-only targeting",
          ilt_filter: {
            items: [
              {
                type: "ou",
                value: "OU=Synthetic,DC=example,DC=test",
                bool_op: "OR",
              },
            ],
          },
        },
      },
    },
  );
  expect(response.status()).toBe(201);
  const seeded = await response.json();
  await page.reload();
  await page.locator(`[data-guid="${gpo.guid}"]`).click();
  await page.getByRole("tab", { name: /Preferences/ }).click();
  await page
    .getByRole("button", { name: "Edit group Synthetic read-only targeting" })
    .click();
  const dialog = page.locator("#gpp-group-dialog");
  const row = dialog.locator('#gpp-ilt-list .gpp-row[data-readonly="true"]');
  await expect(
    row.getByRole("combobox", {
      name: "Targeting predicate type",
      exact: true,
    }),
  ).toBeDisabled();
  await expect(
    row.getByRole("textbox", {
      name: "Targeting predicate value",
      exact: true,
    }),
  ).toBeDisabled();
  await expect(
    row.getByRole("checkbox", { name: "NOT", exact: true }),
  ).toBeDisabled();
  await expect(
    row.getByRole("button", { name: "Remove imported ILT predicate" }),
  ).toBeEnabled();
  const submitting = page.waitForRequest(
    (request) =>
      request.method() === "PUT" &&
      request.url().includes("/preferences/groups/"),
  );
  await dialog.getByRole("button", { name: "Save group" }).click();
  expect((await submitting).postDataJSON().group.ilt_filter.items[0]).toEqual({
    id: seeded.gpo.gpp_collections[0].groups[0].ilt_filter.ids[0],
  });
  await expect(dialog).toBeHidden();
  await expect(row.locator('[data-field="type"]')).toBeDisabled();
  const current = (
    await (await page.request.get(`/api/gpos/${gpo.guid}`)).json()
  ).gpo;
  expect(current.gpp_collections[0].groups[0].ilt_filter).toEqual(
    seeded.gpo.gpp_collections[0].groups[0].ilt_filter,
  );
});
