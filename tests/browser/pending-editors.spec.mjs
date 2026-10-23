import { expect, test } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const admxTest = test.extend({
  bothTemplates: [
    async ({ request }, use, testInfo) => {
      const sources = await (await request.get("/api/templates")).json();
      const original = sources.items.find(
        (source) => source.name === "default",
      ).path;
      const directory = testInfo.outputPath("pending-templates");
      mkdirSync(directory, { recursive: true });
      writeFileSync(
        join(directory, "pending.admx"),
        `<?xml version="1.0" encoding="utf-8"?>
<policyDefinitions xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions">
  <policyNamespaces><target prefix="pending" namespace="Synthetic.Policies.PendingEditor" /></policyNamespaces>
  <policies><policy name="PendingFeature" class="Both" key="Software\\Policies\\Synthetic\\PendingEditor"
    valueName="FeatureState" displayName="Synthetic feature with options">
    <enabledValue><decimal value="1" /></enabledValue><disabledValue><decimal value="0" /></disabledValue>
    <elements><boolean id="SubOption" valueName="SubOption" /></elements>
  </policy></policies>
</policyDefinitions>`,
      );
      writeFileSync(
        join(directory, "pending.adml"),
        `<?xml version="1.0" encoding="utf-8"?>
<policyDefinitionResources xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions">
  <resources><stringTable /></resources>
</policyDefinitionResources>`,
      );
      const loaded = await request.post("/api/templates/ingest", {
        data: { name: "default", kind: "local", path: directory },
      });
      try {
        expect(loaded.status()).toBe(201);
        expect((await loaded.json()).policy_count).toBe(1);
        await use();
      } finally {
        const restored = await request.post("/api/templates/ingest", {
          data: { name: "default", kind: "local", path: original },
        });
        expect(restored.status()).toBe(201);
      }
    },
    { auto: true },
  ],
});

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

async function seed(request, prefix) {
  const response = await request.post("/api/gpos", {
    data: {
      name: `Synthetic Pending ${prefix} ${Date.now()}`,
      actor: "browser-test",
      reason: "Synthetic pending editor test",
    },
  });
  expect(response.status()).toBe(201);
  return (await response.json()).gpo;
}

async function openPolicy(page, gpo) {
  await page.goto("/");
  await page.locator(`[data-guid="${gpo.guid}"]`).click();
  await expect(page.locator("#title")).toHaveText(gpo.name);
}

async function hold(page, pattern, method = "POST") {
  const release = deferred(),
    arrived = deferred(),
    completed = deferred();
  let count = 0;
  await page.route(pattern, async (route) => {
    if (route.request().method() !== method) return route.continue();
    count++;
    const response = await route.fetch();
    arrived.resolve({
      status: response.status(),
      body: await response.json(),
      request: route.request().postDataJSON(),
    });
    await release.promise;
    await route.fulfill({ response });
    completed.resolve();
  });
  return {
    release: () => release.resolve(),
    arrived: arrived.promise,
    completed: completed.promise,
    count: () => count,
  };
}

async function frozen(dialog) {
  const enabled = await dialog
    .locator(
      'input:not([type="hidden"]),textarea,select,button:not([value="cancel"])',
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
  await expect(
    dialog.getByRole("button", { name: "Close", exact: true }),
  ).toBeEnabled();
}

async function openSetting(page) {
  await page.getByRole("tab", { name: /Policy settings/ }).click();
  await page.locator("#add-setting").click();
  const dialog = page.locator("#setting-dialog");
  await dialog
    .getByLabel("Registry key")
    .fill("Software\\Policies\\SyntheticPendingEditor");
  await dialog.getByLabel("Value name").fill("Text");
  await dialog.locator('[name="value"]').fill("Captured original value");
  return dialog;
}

async function openAdmx(page) {
  await page.getByRole("tab", { name: "ADMX", exact: true }).click();
  await page
    .locator(".admx-result", { hasText: "Synthetic feature with options" })
    .click();
  await page.locator("#admx-configure-btn").click();
  return page.locator("#configure-dialog");
}

test("freezes setting fields while saving so later typing cannot be discarded", async ({
  page,
  request,
}) => {
  const gpo = await seed(request, "Freeze Setting");
  await openPolicy(page, gpo);
  const dialog = await openSetting(page);
  const pending = await hold(page, `**/api/gpos/${gpo.guid}/settings`);
  try {
    await dialog.getByRole("button", { name: "Save setting" }).click();
    expect((await pending.arrived).status).toBe(201);
    await frozen(dialog);
    await page
      .locator("#setting-form")
      .evaluate((form) => form.requestSubmit());
    expect(pending.count()).toBe(1);
    pending.release();
    await pending.completed;
    await expect(dialog).toBeHidden();
    const saved = (await (await request.get(`/api/gpos/${gpo.guid}`)).json())
      .gpo;
    expect(saved.settings[0].value).toBe("Captured original value");
  } finally {
    pending.release();
  }
});

test("reopening metadata releases old UI ownership and an old finally cannot unlock the new request", async ({
  page,
  request,
}) => {
  const gpo = await seed(request, "Reopen Metadata");
  await openPolicy(page, gpo);
  const old = await hold(page, "**/api/gpos");
  const newer = await hold(page, `**/api/gpos/${gpo.guid}`, "PATCH");
  try {
    await page.locator("#new-gpo").click();
    const dialog = page.locator("#gpo-dialog");
    const createdName = `Synthetic independent creation ${Date.now()}`;
    await dialog.getByLabel("Name", { exact: true }).fill(createdName);
    await dialog.getByRole("button", { name: "Create policy" }).click();
    expect((await old.arrived).status).toBe(201);
    await frozen(dialog);
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.locator("#edit-metadata").click();
    await expect(dialog.getByLabel("Name", { exact: true })).toBeEnabled();
    const renamed = `${gpo.name} renamed`;
    await dialog.getByLabel("Name", { exact: true }).fill(renamed);
    await dialog.getByRole("button", { name: "Save changes" }).click();
    expect((await newer.arrived).status).toBe(200);
    old.release();
    await old.completed;
    await expect(
      page.locator(`[data-guid="${(await old.arrived).body.gpo.guid}"]`),
    ).toContainText(createdName);
    await frozen(dialog);
    await expect(dialog.getByLabel("Name", { exact: true })).toHaveValue(
      renamed,
    );
    newer.release();
    await newer.completed;
    await expect(dialog).toBeHidden();
    await expect(page.locator("#title")).toHaveText(renamed);
  } finally {
    old.release();
    newer.release();
  }
});

test("restores originally disabled delete controls after a real server error and allows correction", async ({
  page,
  request,
}) => {
  const gpo = await seed(request, "Disabled Restoration");
  await openPolicy(page, gpo);
  const dialog = await openSetting(page);
  await dialog.getByLabel("Action").selectOption("delete");
  const value = dialog.locator('[name="value"]');
  await expect(value).toBeDisabled();
  await dialog.getByLabel("Change reason").fill("x".repeat(501));
  const pending = await hold(page, `**/api/gpos/${gpo.guid}/settings`);
  try {
    await dialog.getByRole("button", { name: "Save setting" }).click();
    expect((await pending.arrived).status).toBe(422);
    await frozen(dialog);
    pending.release();
    await pending.completed;
    await expect(dialog.locator(".form-error")).toBeVisible();
    await expect(dialog.getByLabel("Action")).toBeEnabled();
    await expect(
      dialog.getByRole("button", { name: "Save setting" }),
    ).toBeEnabled();
    await expect(value).toBeDisabled();
    await expect(value).toHaveAttribute("aria-describedby", "value-help");
    await dialog.getByLabel("Change reason").fill("Synthetic corrected reason");
    await dialog.getByRole("button", { name: "Save setting" }).click();
    await expect(dialog).toBeHidden();
  } finally {
    pending.release();
  }
});

test("conflict review retains pre-freeze field names and reapply restores editable values", async ({
  page,
  request,
}) => {
  const gpo = await seed(request, "Conflict Fields");
  await openPolicy(page, gpo);
  const dialog = await openSetting(page);
  const concurrent = await request.patch(`/api/gpos/${gpo.guid}`, {
    data: {
      expected_revision: 1,
      name: gpo.name,
      description: "Synthetic concurrent metadata",
      computer_enabled: true,
      user_enabled: true,
      status: "draft",
      actor: "other-browser",
      reason: "Synthetic concurrent edit",
    },
  });
  expect(concurrent.status()).toBe(200);
  await dialog.getByRole("button", { name: "Save setting" }).click();
  const conflict = page.locator("#conflict-dialog");
  await expect(conflict).toBeVisible();
  await expect(conflict.locator("#conflict-details")).toContainText(
    "key, value_name, registry_type, value",
  );
  await expect(conflict.locator("#conflict-details")).toContainText("reason");
  await conflict.getByRole("button", { name: "Review and reapply" }).click();
  await expect(dialog.locator('[name="value"]')).toBeEnabled();
  await expect(dialog.locator('[name="value"]')).toHaveValue(
    "Captured original value",
  );
  await expect(dialog.locator('[name="value"]')).toHaveAttribute(
    "aria-describedby",
    "value-help",
  );
  const submitted = page.waitForRequest(
    (sent) =>
      sent.method() === "POST" &&
      sent.url().endsWith(`/api/gpos/${gpo.guid}/settings`),
  );
  await dialog.getByRole("button", { name: "Save setting" }).click();
  expect((await submitted).postDataJSON().expected_revision).toBe(2);
  await expect(dialog).toBeHidden();
  await expect(page.locator("#revision")).toHaveText("Revision 3");
});

test("freezes preference row actions and makes a reopened group editable before the old response completes", async ({
  page,
  request,
}) => {
  const gpo = await seed(request, "Preference Rows");
  await openPolicy(page, gpo);
  await page.getByRole("tab", { name: /Preferences/ }).click();
  await page.locator("#add-gpp-group").click();
  const dialog = page.locator("#gpp-group-dialog");
  await dialog.getByLabel("Group name").fill("Synthetic First Group");
  await dialog.locator("#gpp-add-member").click();
  await dialog.locator('[data-field="sid"]').fill("S-1-5-32-544");
  const pending = await hold(
    page,
    `**/api/gpos/${gpo.guid}/preferences/groups`,
  );
  try {
    await dialog.getByRole("button", { name: "Save group" }).click();
    expect((await pending.arrived).status).toBe(201);
    await frozen(dialog);
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.locator("#add-gpp-group").click();
    await expect(dialog.getByLabel("Group name")).toBeEnabled();
    await dialog.getByLabel("Group name").fill("Synthetic Reopened Group");
    await expect(dialog.locator("#gpp-add-member")).toBeEnabled();
    pending.release();
    await pending.completed;
    await expect(page.locator("#revision")).toHaveText("Revision 2");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel("Group name")).toHaveValue(
      "Synthetic Reopened Group",
    );
    await expect(
      dialog.getByRole("button", { name: "Save group" }),
    ).toBeEnabled();
  } finally {
    pending.release();
  }
});

admxTest(
  "ADMX freezes target/state/side and reapplies a conflict on the explicit target with disabled options preserved",
  async ({ page, request }) => {
    const alpha = await seed(request, "ADMX Underlay");
    const beta = await seed(request, "ADMX Explicit Target");
    await openPolicy(page, alpha);
    const dialog = await openAdmx(page);
    await dialog.locator("#configure-target-gpo").selectOption(beta.guid);
    await dialog.locator("#configure-side").selectOption("user");
    await dialog.locator("#configure-state").selectOption("disabled");
    await expect(dialog.locator("[data-elem-id]")).toBeDisabled();
    const concurrent = await request.patch(`/api/gpos/${beta.guid}`, {
      data: {
        expected_revision: 1,
        name: beta.name,
        description: "Synthetic target revision",
        computer_enabled: true,
        user_enabled: true,
        status: "draft",
        actor: "other-browser",
        reason: "Synthetic target edit",
      },
    });
    expect(concurrent.status()).toBe(200);
    const pending = await hold(page, "**/api/admx/policies/*/configure");
    try {
      await dialog.getByRole("button", { name: "Apply policy" }).click();
      expect((await pending.arrived).status).toBe(409);
      expect((await pending.arrived).request.gpo_guid).toBe(beta.guid);
      await frozen(dialog);
      pending.release();
      await pending.completed;
      const conflict = page.locator("#conflict-dialog");
      await expect(conflict).toBeVisible();
      await expect(conflict.locator("#conflict-details")).toContainText(
        "Current workspace revision: 2",
      );
      await expect(conflict.locator("#conflict-details")).toContainText(
        "configure-state",
      );
      await expect(conflict.locator("#conflict-details")).toContainText(
        "configure-side",
      );
      await conflict
        .getByRole("button", { name: "Review and reapply" })
        .click();
      await expect(page.locator("#title")).toHaveText(alpha.name);
      await expect(dialog.locator("#configure-target-gpo")).toBeEnabled();
      await expect(dialog.locator("#configure-target-gpo")).toHaveValue(
        beta.guid,
      );
      await expect(dialog.locator("#configure-state")).toHaveValue("disabled");
      await expect(dialog.locator("#configure-side")).toHaveValue("user");
      await expect(dialog.locator("[data-elem-id]")).toBeDisabled();
      const next = page.waitForRequest(
        (sent) => sent.method() === "POST" && sent.url().endsWith("/configure"),
      );
      await dialog.getByRole("button", { name: "Apply policy" }).click();
      expect((await next).postDataJSON()).toMatchObject({
        gpo_guid: beta.guid,
        expected_revision: 2,
        side: "user",
        state: "disabled",
      });
      await expect(dialog).toBeHidden();
      await expect(page.locator("#title")).toHaveText(beta.name);
      await expect(page.locator("#revision")).toHaveText("Revision 3");
    } finally {
      pending.release();
    }
  },
);

admxTest(
  "ADMX reopening starts another request while the old completion cannot unlock or close its editor",
  async ({ page, request }) => {
    const alpha = await seed(request, "ADMX Reopen Underlay");
    const beta = await seed(request, "ADMX Old Target");
    const gamma = await seed(request, "ADMX New Target");
    await openPolicy(page, alpha);
    const dialog = await openAdmx(page);
    await dialog.locator("#configure-target-gpo").selectOption(beta.guid);
    const firstRelease = deferred(),
      firstArrived = deferred(),
      firstDone = deferred();
    const secondRelease = deferred(),
      secondArrived = deferred(),
      secondDone = deferred();
    await page.route("**/api/admx/policies/*/configure", async (route) => {
      const target = route.request().postDataJSON().gpo_guid;
      const response = await route.fetch();
      const first = target === beta.guid;
      (first ? firstArrived : secondArrived).resolve(response.status());
      await (first ? firstRelease : secondRelease).promise;
      await route.fulfill({ response });
      (first ? firstDone : secondDone).resolve();
    });
    try {
      await dialog.getByRole("button", { name: "Apply policy" }).click();
      expect(await firstArrived.promise).toBe(200);
      await frozen(dialog);
      await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
      await page.locator("#admx-configure-btn").click();
      await expect(dialog.locator("#configure-target-gpo")).toBeEnabled();
      await dialog.locator("#configure-target-gpo").selectOption(gamma.guid);
      await expect(dialog.locator("[data-elem-id]")).toBeEnabled();
      await dialog.getByRole("button", { name: "Apply policy" }).click();
      expect(await secondArrived.promise).toBe(200);
      firstRelease.resolve();
      await firstDone.promise;
      await expect(page.locator(`[data-guid="${beta.guid}"]`)).toContainText(
        "r2",
      );
      await frozen(dialog);
      await expect(dialog.locator("#configure-target-gpo")).toHaveValue(
        gamma.guid,
      );
      secondRelease.resolve();
      await secondDone.promise;
      await expect(dialog).toBeHidden();
      await expect(page.locator("#title")).toHaveText(gamma.name);
    } finally {
      firstRelease.resolve();
      secondRelease.resolve();
    }
  },
);

admxTest(
  "loading the current revision after an ADMX conflict displays the actual explicit target",
  async ({ page, request }) => {
    const alpha = await seed(request, "ADMX Reload Underlay");
    const beta = await seed(request, "ADMX Reload Target");
    await openPolicy(page, alpha);
    const dialog = await openAdmx(page);
    await dialog.locator("#configure-target-gpo").selectOption(beta.guid);
    const concurrent = await request.patch(`/api/gpos/${beta.guid}`, {
      data: {
        expected_revision: 1,
        name: beta.name,
        description: "Synthetic actual target revision",
        computer_enabled: true,
        user_enabled: true,
        status: "draft",
        actor: "other-browser",
        reason: "Synthetic target edit",
      },
    });
    expect(concurrent.status()).toBe(200);
    await dialog.getByRole("button", { name: "Apply policy" }).click();
    const conflict = page.locator("#conflict-dialog");
    await expect(conflict).toBeVisible();
    await conflict
      .getByRole("button", { name: "Load current revision" })
      .click();
    await expect(dialog).toBeHidden();
    await expect(page.locator("#title")).toHaveText(beta.name);
    await expect(page.locator("#revision")).toHaveText("Revision 2");
    await expect(page.locator("#metadata")).toContainText(
      "Synthetic actual target revision",
    );
    const saved = (await (await request.get(`/api/gpos/${beta.guid}`)).json())
      .gpo;
    expect(saved.revision).toBe(2);
    expect(saved.settings).toEqual([]);
  },
);

for (const kind of ["creation", "estate import"]) {
  test(`${kind} remains usable when initial policy loading changes the underlay`, async ({
    page,
    request,
  }) => {
    await seed(request, `Initial ${kind}`);
    const release = deferred(),
      arrived = deferred(),
      completed = deferred();
    await page.route("**/api/gpos/*", async (route) => {
      if (route.request().method() !== "GET") return route.continue();
      const response = await route.fetch();
      arrived.resolve((await response.json()).gpo);
      await release.promise;
      await route.fulfill({ response });
      completed.resolve();
    });
    try {
      await page.goto("/");
      const selected = await arrived.promise;
      await expect(page.locator("#title")).toHaveText("Choose a policy");
      await page
        .locator(kind === "creation" ? "#new-gpo" : "#import-estate")
        .click();
      const dialog = page.locator(
        kind === "creation" ? "#gpo-dialog" : "#estate-dialog",
      );
      release.resolve();
      await completed.promise;
      await expect(page.locator("#title")).toHaveText(selected.name);
      if (kind === "creation") {
        const name = `Synthetic startup creation ${Date.now()}`;
        await dialog.getByLabel("Name", { exact: true }).fill(name);
        const created = page.waitForResponse(
          (response) =>
            response.request().method() === "POST" &&
            response.url().endsWith("/api/gpos"),
        );
        await dialog.getByRole("button", { name: "Create policy" }).click();
        const response = await created;
        expect(response.status()).toBe(201);
        await expect(
          page.locator(`[data-guid="${(await response.json()).gpo.guid}"]`),
        ).toContainText(name);
      } else {
        await dialog.locator("#estate-file").setInputFiles({
          name: "synthetic-empty-estate.json",
          mimeType: "application/json",
          buffer: Buffer.from(
            JSON.stringify({
              kind: "gpo-lens-estate",
              domain: "synthetic.example.test",
              gpos: [],
            }),
          ),
        });
        await expect(dialog.locator("#estate-file-info")).not.toContainText(
          "Reading",
        );
        const imported = page.waitForResponse(
          (response) =>
            response.request().method() === "POST" &&
            response.url().endsWith("/api/estate/import"),
        );
        await dialog
          .getByRole("button", { name: "Import", exact: true })
          .click();
        expect((await imported).status()).toBe(200);
      }
      await expect(dialog).toBeHidden();
      await expect(page.locator("#title")).toHaveText(selected.name);
    } finally {
      release.resolve();
    }
  });
}

test("a successful creation closes its original session after pending navigation without changing the selected policy", async ({
  page,
  request,
}) => {
  const alpha = await seed(request, "Create View Alpha");
  const beta = await seed(request, "Create View Beta");
  await openPolicy(page, alpha);
  const navigation = await hold(page, `**/api/gpos/${beta.guid}`, "GET");
  const creation = await hold(page, "**/api/gpos");
  try {
    await page.locator(`[data-guid="${beta.guid}"]`).click();
    expect((await navigation.arrived).status).toBe(200);
    await page.locator("#new-gpo").click();
    const dialog = page.locator("#gpo-dialog");
    const name = `Synthetic completed original session ${Date.now()}`;
    await dialog.getByLabel("Name", { exact: true }).fill(name);
    await dialog.getByRole("button", { name: "Create policy" }).click();
    const result = await creation.arrived;
    expect(result.status).toBe(201);
    navigation.release();
    await navigation.completed;
    await expect(page.locator("#title")).toHaveText(beta.name);
    await expect(dialog).toBeVisible();
    creation.release();
    await creation.completed;
    await expect(dialog).toBeHidden();
    await expect(page.locator("#title")).toHaveText(beta.name);
    await expect(
      page.locator(`[data-guid="${result.body.gpo.guid}"]`),
    ).toContainText(name);
    expect(
      (await (await request.get(`/api/gpos/${result.body.gpo.guid}`)).json())
        .gpo.name,
    ).toBe(name);
  } finally {
    navigation.release();
    creation.release();
  }
});
