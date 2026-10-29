/* global document, window */
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

// #140 item 6: an editing dialog asks before Esc or × discards a change.
// Cancel, saving and dialogs that edit nothing close without asking, and the
// browser's own "Leave site?" warning is armed only while a change is open.

async function seed(request, testInfo, label) {
  const name = `Synthetic unsaved ${label} ${testInfo.project.name} ${Date.now()}`;
  const response = await request.post("/api/gpos", {
    data: { name, actor: "browser-test", reason: "Seed unsaved-edit guard" },
  });
  expect(response.status()).toBe(201);
  return (await response.json()).gpo;
}

async function selectPolicy(page, gpo) {
  await page.goto("/");
  await page.locator(".gpo-item", { hasText: gpo.name }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(gpo.name);
}

function prompt(dialog) {
  return dialog.getByRole("alertdialog", { name: "Discard your changes?" });
}

async function gpoCount(request) {
  return (await (await request.get("/api/gpos")).json()).items.length;
}

async function unloadArmed(page) {
  return page.evaluate(() => {
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  });
}

test("Esc asks before discarding a change and closes an unchanged dialog @smoke", async ({
  page,
  request,
}) => {
  await page.goto("/");
  const before = await gpoCount(request);
  const opener = page.locator("#new-gpo");
  const dialog = page.locator("#gpo-dialog");

  await opener.click();
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();

  await opener.click();
  const name = dialog.locator('[name="name"]');
  await name.fill("Unsaved synthetic draft");
  await page.keyboard.press("Escape");
  await expect(prompt(dialog)).toBeVisible();
  await expect(dialog).toBeVisible();
  await expect(
    prompt(dialog).getByRole("button", { name: "Keep editing" }),
  ).toBeFocused();

  // Esc in the prompt is Keep editing. Repeated Esc presses with no other
  // input in between must not close the native dialog behind the prompt.
  await page.keyboard.press("Escape");
  await expect(prompt(dialog)).toBeHidden();
  await expect(dialog).toBeVisible();
  await expect(name).toBeFocused();
  await expect(name).toHaveValue("Unsaved synthetic draft");
  await page.keyboard.press("Escape");
  await expect(prompt(dialog)).toBeVisible();
  await page.keyboard.press("Escape");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  await expect(name).toHaveValue("Unsaved synthetic draft");

  await expect(prompt(dialog)).toBeVisible();
  await prompt(dialog).getByRole("button", { name: "Discard" }).click();
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();
  expect(await gpoCount(request)).toBe(before);
});

test("× asks before discarding; Keep editing returns to the dialog", async ({
  page,
}) => {
  await page.goto("/");
  const opener = page.locator("#new-gpo");
  const dialog = page.locator("#gpo-dialog");
  const close = dialog.getByRole("button", { name: "Close", exact: true });

  await opener.click();
  await close.click();
  await expect(dialog).toBeHidden();

  await opener.click();
  const description = dialog.locator('[name="description"]');
  await description.fill("Not saved yet");
  await close.click();
  const ask = prompt(dialog);
  await expect(ask).toBeVisible();
  await expect(ask).toHaveAttribute("aria-modal", "true");
  await expect(ask).toHaveAccessibleDescription(
    "Your changes in this dialog have not been saved.",
  );
  const keep = ask.getByRole("button", { name: "Keep editing" });
  const discard = ask.getByRole("button", { name: "Discard" });
  await expect(keep).toBeFocused();
  // Focus stays in the prompt while it is open; the form behind it is inert.
  await page.keyboard.press("Tab");
  await expect(discard).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(keep).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(discard).toBeFocused();
  await expect(dialog.locator("form")).toHaveAttribute("inert", "");
  const audit = await new AxeBuilder({ page }).include("#gpo-dialog").analyze();
  expect(audit.violations).toEqual([]);

  await keep.click();
  await expect(ask).toBeHidden();
  await expect(dialog).toBeVisible();
  await expect(close).toBeFocused();
  await expect(description).toHaveValue("Not saved yet");
  await expect(dialog.locator("form")).not.toHaveAttribute("inert", "");

  await close.click();
  await ask.getByRole("button", { name: "Discard" }).click();
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();
});

test("Cancel discards a change without asking; × on the same change asks", async ({
  page,
}) => {
  await page.goto("/");
  const opener = page.locator("#new-gpo");
  const dialog = page.locator("#gpo-dialog");

  await opener.click();
  await dialog.locator('[name="name"]').fill("Cancelled synthetic draft");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(prompt(dialog)).toBeHidden();
  await expect(opener).toBeFocused();

  await opener.click();
  await expect(dialog.locator('[name="name"]')).toHaveValue("");
  await dialog.locator('[name="name"]').fill("Cancelled synthetic draft");
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(prompt(dialog)).toBeVisible();
  await prompt(dialog).getByRole("button", { name: "Discard" }).click();
  await expect(dialog).toBeHidden();
});

test("the leave-page warning is armed only while an editing dialog has changes", async ({
  page,
}) => {
  await page.goto("/");
  const dialog = page.locator("#gpo-dialog");
  expect(await unloadArmed(page)).toBe(false);

  await page.locator("#new-gpo").click();
  expect(await unloadArmed(page)).toBe(false);
  const name = `Synthetic unload draft ${Date.now()}`;
  await dialog.locator('[name="name"]').fill(name);
  expect(await unloadArmed(page)).toBe(true);

  // The browser's own prompt appears on a real reload; dismissing it stays.
  // A dismissed reload never settles; it is cut off when the page closes.
  const asked = page.waitForEvent("dialog");
  page.reload().catch(() => {});
  const native = await asked;
  expect(native.type()).toBe("beforeunload");
  await native.dismiss();
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('[name="name"]')).toHaveValue(name);

  // Undoing the change disarms it again, and saving never asks.
  await dialog.locator('[name="name"]').fill("");
  expect(await unloadArmed(page)).toBe(false);
  await dialog.locator('[name="name"]').fill(name);
  expect(await unloadArmed(page)).toBe(true);
  await dialog.getByRole("button", { name: "Create policy" }).click();
  await expect(dialog).toBeHidden();
  await expect(prompt(dialog)).toBeHidden();
  await expect(page.locator("#title")).toHaveText(name);
  expect(await unloadArmed(page)).toBe(false);
});

test("dialogs that edit nothing close freely after input", async ({
  page,
  request,
}, testInfo) => {
  const gpo = await seed(request, testInfo, "readonly");
  await selectPolicy(page, gpo);

  const rsop = page.locator("#rsop-dialog");
  await page.locator("#open-rsop").click();
  await rsop.locator("[name=computer_name]").fill("LABCL01");
  expect(await unloadArmed(page)).toBe(false);
  await page.keyboard.press("Escape");
  await expect(rsop).toBeHidden();
  await expect(page.locator("[data-discard-prompt]")).toHaveCount(0);
  await page.locator("#open-rsop").click();
  await rsop.locator("[name=computer_name]").fill("LABCL02");
  await rsop.getByRole("button", { name: "Close" }).first().click();
  await expect(rsop).toBeHidden();
  // RSoP and the security template keep their input across a close, so
  // closing them loses nothing and they are not guarded.
  await page.locator("#open-rsop").click();
  await expect(rsop.locator("[name=computer_name]")).toHaveValue("LABCL02");
  await page.keyboard.press("Escape");
  await expect(rsop).toBeHidden();
  const template = page.locator("#security-template-dialog");
  await page.locator("#open-security-template").click();
  await template.locator("[name=families]").fill('{"audit": {}}');
  await page.keyboard.press("Escape");
  await expect(template).toBeHidden();
  await page.locator("#open-security-template").click();
  await expect(template.locator("[name=families]")).toHaveValue(
    '{"audit": {}}',
  );
  await page.keyboard.press("Escape");
  await expect(template).toBeHidden();

  const review = page.locator("#export-review-dialog");
  await page.locator("#export").click();
  await expect(review).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(review).toBeHidden();

  // The same kind of input in an editing dialog asks.
  const metadata = page.locator("#gpo-dialog");
  await page.locator("#edit-metadata").click();
  await metadata.locator('[name="description"]').fill("Changed description");
  await page.keyboard.press("Escape");
  await expect(prompt(metadata)).toBeVisible();
  await prompt(metadata).getByRole("button", { name: "Discard" }).click();
  await expect(metadata).toBeHidden();
});

test("dialogs filled after opening start from their filled state", async ({
  page,
  request,
}, testInfo) => {
  const gpo = await seed(request, testInfo, "dynamic");
  let revision = gpo.revision;
  const added = await request.post(`/api/gpos/${gpo.guid}/settings`, {
    data: {
      expected_revision: revision,
      actor: "browser-test",
      reason: "Seed recovery row",
      setting: {
        side: "computer",
        hive: "HKLM",
        key: "Software\\Policies\\SyntheticUnsaved",
        value_name: "Value",
        registry_type: "REG_SZ",
        value: "kept",
        action: "set",
      },
    },
  });
  expect(added.status()).toBe(201);
  revision = (await added.json()).gpo.revision;
  const group = await request.post(`/api/gpos/${gpo.guid}/preferences/groups`, {
    data: {
      expected_revision: revision,
      actor: "browser-test",
      reason: "Seed group with a member",
      scope: "computer",
      group: {
        name: "SyntheticUnsavedGroup",
        sid: "S-1-5-32-544",
        members: [{ sid: "S-1-5-21-99-99-99-1001", name: "Member One" }],
      },
    },
  });
  expect(group.status()).toBe(201);
  await selectPolicy(page, gpo);

  // Registry recovery reads its rows from the server after showModal().
  const repair = page.locator("#registry-repair-dialog");
  await page.locator("#repair-registry").click();
  await expect(repair.locator("#registry-repair-source")).toContainText(
    "1 registry rows",
  );
  await expect(repair.locator('[name="name"]')).toHaveValue(
    /registry recovery/,
  );
  await page.keyboard.press("Escape");
  await expect(repair).toBeHidden();
  await page.locator("#repair-registry").click();
  await expect(repair.locator("#registry-repair-source")).toContainText(
    "1 registry rows",
  );
  await repair.locator('[name="row-0"]').selectOption("omit");
  await page.keyboard.press("Escape");
  await expect(prompt(repair)).toBeVisible();
  await prompt(repair).getByRole("button", { name: "Discard" }).click();
  await expect(repair).toBeHidden();

  // A GPP group's member rows are built when it opens; adding or removing a
  // row afterwards is a change.
  await page.getByRole("tab", { name: /Preferences/ }).click();
  const edit = page.getByRole("button", {
    name: "Edit group SyntheticUnsavedGroup",
  });
  const gpp = page.locator("#gpp-group-dialog");
  const rows = gpp.locator("#gpp-members-list .gpp-row");
  await edit.click();
  await expect(rows).toHaveCount(1);
  await page.keyboard.press("Escape");
  await expect(gpp).toBeHidden();

  await edit.click();
  await gpp.getByRole("button", { name: "＋ Add member" }).click();
  await expect(rows).toHaveCount(2);
  await page.keyboard.press("Escape");
  await expect(prompt(gpp)).toBeVisible();
  await prompt(gpp).getByRole("button", { name: "Keep editing" }).click();
  await rows.nth(1).getByRole("button", { name: "Remove member row" }).click();
  await page.keyboard.press("Escape");
  await expect(gpp).toBeHidden();

  await edit.click();
  await rows.first().getByRole("button", { name: "Remove member row" }).click();
  await expect(rows).toHaveCount(0);
  await gpp.getByRole("button", { name: "Close", exact: true }).click();
  await expect(prompt(gpp)).toBeVisible();
  await prompt(gpp).getByRole("button", { name: "Discard" }).click();
  await expect(gpp).toBeHidden();

  // ADMX configure builds its fields before opening.
  await page.getByRole("tab", { name: "ADMX" }).click();
  await page
    .locator(".admx-result", { hasText: "Synthetic feature with options" })
    .click();
  const configure = page.locator("#configure-dialog");
  await page.locator("#admx-configure-btn").click();
  await expect(configure).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(configure).toBeHidden();
  await page.locator("#admx-configure-btn").click();
  await configure.locator("#configure-state").selectOption("disabled");
  await page.keyboard.press("Escape");
  await expect(prompt(configure)).toBeVisible();
  await prompt(configure).getByRole("button", { name: "Discard" }).click();
  await expect(configure).toBeHidden();
  expect(
    await page.evaluate(() =>
      [...document.querySelectorAll("dialog[open]")].map((d) => d.id),
    ),
  ).toEqual([]);
});

test("Scripts asks before × or Esc discards authored rows", async ({
  page,
  request,
}, testInfo) => {
  const gpo = await seed(request, testInfo, "scripts");
  await selectPolicy(page, gpo);
  const opener = page.getByRole("button", { name: "Scripts", exact: true });
  const dialog = page.locator("#scripts-dialog");
  const close = dialog.locator(".dialog-head").getByRole("button", {
    name: "Close",
  });
  const rows = dialog.locator("#scripts-legacy-rows > li.script-row");

  await opener.click();
  await close.click();
  await expect(dialog).toBeHidden();

  await opener.click();
  await dialog.getByRole("button", { name: "Add startup script" }).click();
  await rows.first().getByLabel("Command").fill("synthetic.cmd");
  expect(await unloadArmed(page)).toBe(true);
  await page.keyboard.press("Escape");
  await expect(prompt(dialog)).toBeVisible();
  await prompt(dialog).getByRole("button", { name: "Keep editing" }).click();
  await close.click();
  await expect(prompt(dialog)).toBeVisible();
  await prompt(dialog).getByRole("button", { name: "Discard" }).click();
  await expect(dialog).toBeHidden();
  // Reopening starts from no rows, so a close really discards them.
  await opener.click();
  await expect(rows).toHaveCount(0);

  // The footer Close is the deliberate dismissal, like Cancel.
  await dialog.getByRole("button", { name: "Add startup script" }).click();
  await dialog
    .locator(".dialog-actions")
    .getByRole("button", { name: "Close" })
    .click();
  await expect(dialog).toBeHidden();
  await expect(prompt(dialog)).toHaveCount(0);
});

test("registry recovery is not changed while its rows load", async ({
  page,
  request,
}, testInfo) => {
  const gpo = await seed(request, testInfo, "loading");
  await selectPolicy(page, gpo);
  let release = () => {};
  await page.route(`**/api/gpos/${gpo.guid}/registry-repair`, async (route) => {
    await new Promise((resolve) => {
      release = resolve;
    });
    await route.continue().catch(() => {});
  });
  const repair = page.locator("#registry-repair-dialog");
  const source = repair.locator("#registry-repair-source");
  try {
    for (const close of ["Escape", "×"]) {
      await page.locator("#repair-registry").click();
      await expect(source).toHaveText("Loading source…");
      await expect(repair.locator('[name="name"]')).toHaveValue(
        /registry recovery/,
      );
      expect(await unloadArmed(page)).toBe(false);
      if (close === "Escape") await page.keyboard.press("Escape");
      else
        await repair
          .getByRole("button", { name: "Close", exact: true })
          .click();
      await expect(repair).toBeHidden();
      await expect(prompt(repair)).toHaveCount(0);
      release();
    }

    // Once loaded, the filled name and rows are still the starting state.
    await page.locator("#repair-registry").click();
    await expect(source).toHaveText("Loading source…");
    release();
    await expect(source).toContainText("0 registry rows");
    expect(await unloadArmed(page)).toBe(false);
    await page.keyboard.press("Escape");
    await expect(repair).toBeHidden();
  } finally {
    release();
  }
});

test("a change still asks while its save is in flight, and survives a failed save", async ({
  page,
  request,
}, testInfo) => {
  const gpo = await seed(request, testInfo, "pending");
  await selectPolicy(page, gpo);
  await page.locator('[data-tab="links"]').click();
  const dialog = page.locator("#link-dialog");
  const target = dialog.locator('[name="target"]');
  let release = () => {};
  await page.route("**/api/gpos/*/links", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await new Promise((resolve) => {
      release = resolve;
    });
    await route
      .fulfill({
        status: 500,
        json: { error: { message: "Synthetic held save failure" } },
      })
      .catch(() => {});
  });
  try {
    await page.locator("#add-link").click();
    await target.fill("OU=Held,DC=example,DC=test");
    await dialog.getByRole("button", { name: "Save link" }).click();
    await expect(dialog.locator(".form-pending")).toBeVisible();
    expect(await unloadArmed(page)).toBe(true);
    await page.keyboard.press("Escape");
    await expect(prompt(dialog)).toBeVisible();
    await expect(prompt(dialog)).toHaveAccessibleDescription(
      "Your changes are still being saved. If the save fails, they will be lost.",
    );
    await prompt(dialog).getByRole("button", { name: "Keep editing" }).click();
    release();
    await expect(dialog.locator(".form-error")).toContainText(
      "Synthetic held save failure",
    );
    await expect(dialog).toBeVisible();
    await expect(target).toHaveValue("OU=Held,DC=example,DC=test");
    expect(await unloadArmed(page)).toBe(true);
    await page.keyboard.press("Escape");
    await expect(prompt(dialog)).toBeVisible();

    // Discard while a save is in flight closes; leaving stays guarded until
    // that save resolves.
    await prompt(dialog).getByRole("button", { name: "Keep editing" }).click();
    await dialog.getByRole("button", { name: "Save link" }).click();
    await expect(dialog.locator(".form-pending")).toBeVisible();
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await prompt(dialog).getByRole("button", { name: "Discard" }).click();
    await expect(dialog).toBeHidden();
    expect(await unloadArmed(page)).toBe(true);
    release();
    await expect(page.locator("#error-message")).toContainText(
      "Synthetic held save failure",
    );
    expect(await unloadArmed(page)).toBe(false);
  } finally {
    release();
  }
});

test("Shift+Tab after a click on a non-focusable part stays in the dialog", async ({
  page,
}) => {
  await page.goto("/");
  const dialog = page.locator("#gpo-dialog");
  const inDialog = () =>
    page.evaluate(() =>
      document.getElementById("gpo-dialog").contains(document.activeElement),
    );
  await page.locator("#new-gpo").click();
  await dialog.locator("#gpo-dialog-title").click();
  await page.keyboard.press("Shift+Tab");
  expect(await inDialog()).toBe(true);
  await expect(
    dialog.locator(".dialog-actions").getByRole("button", {
      name: "Create policy",
    }),
  ).toBeFocused();

  await dialog.locator('[name="name"]').fill("Synthetic trap");
  await page.keyboard.press("Escape");
  await prompt(dialog).locator("h3").click();
  await page.keyboard.press("Shift+Tab");
  await expect(
    prompt(dialog).getByRole("button", { name: "Discard" }),
  ).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(
    prompt(dialog).getByRole("button", { name: "Keep editing" }),
  ).toBeFocused();
  await prompt(dialog).getByRole("button", { name: "Discard" }).click();
  await expect(dialog).toBeHidden();
});
