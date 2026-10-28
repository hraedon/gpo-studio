import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

async function seed(request, testInfo) {
  const name = `Synthetic registry recovery ${testInfo.project.name} ${Date.now()}`;
  const created = await request.post("/api/gpos", {
    data: { name, actor: "browser-test", reason: "Seed registry recovery" },
  });
  expect(created.status()).toBe(201);
  let gpo = (await created.json()).gpo;
  for (const [index, type, value] of [
    [0, "REG_QWORD", "18446744073709551615"],
    [1, "REG_MULTI_SZ", [" ", "Exact", "é"]],
  ]) {
    const result = await request.post(`/api/gpos/${gpo.guid}/settings`, {
      data: {
        expected_revision: gpo.revision,
        actor: "browser-test",
        reason: "Seed exact native value",
        setting: {
          side: "computer",
          hive: "HKLM",
          key: `Software\\Policies\\SyntheticRecovery${index}`,
          value_name: "Value",
          registry_type: type,
          value,
          action: "set",
          comment: `Exact row ${index}`,
        },
      },
    });
    expect(result.status()).toBe(201);
    gpo = (await result.json()).gpo;
  }
  return gpo;
}

async function open(page, gpo) {
  await page.goto("/");
  await page.locator(".gpo-item", { hasText: gpo.name }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(gpo.name);
  await page.locator("#repair-registry").click();
  const dialog = page.locator("#registry-repair-dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.locator("#registry-repair-source")).toContainText(
    "2 registry rows",
  );
  return dialog;
}

async function reviewAll(dialog) {
  await dialog.getByRole("button", { name: "Keep all as raw rows" }).click();
  await dialog.locator('[name="registry_only"]').check();
  await dialog.getByRole("button", { name: "Preview choices" }).click();
  await expect(dialog.locator("#registry-repair-preview")).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Create reviewed draft" }),
  ).toBeEnabled();
}

function gate() {
  let release;
  const promise = new Promise((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

async function finishReleasedResponse(page, responsePromise) {
  await (await responsePromise).finished();
  // Let the response body and UI continuations run before asserting absence
  // of a stale paint. An immediately true negative assertion is insufficient.
  await page.evaluate(
    () =>
      new Promise((resolve) => {
        globalThis.requestAnimationFrame(() =>
          globalThis.requestAnimationFrame(resolve),
        );
      }),
  );
}

test("creates a reviewed raw draft with exact QWORDs and leaves source/history intact @smoke", async ({
  page,
  request,
}, testInfo) => {
  const gpo = await seed(request, testInfo);
  const before = await (await request.get(`/api/gpos/${gpo.guid}`)).json();
  const history = await (
    await request.get(`/api/gpos/${gpo.guid}/revisions`)
  ).json();
  const dialog = await open(page, gpo);
  await expect(dialog.locator("pre").first()).toContainText(
    "18446744073709551615",
  );
  await expect(dialog.locator("#registry-repair-omissions")).toContainText(
    "security filters: 0",
  );
  await dialog.locator('[name="name"]').fill(`Recovered ${gpo.name}`);
  await reviewAll(dialog);
  const saved = page.waitForResponse((r) =>
    r.url().endsWith(`/api/gpos/${gpo.guid}/registry-repair/apply`),
  );
  await dialog.getByRole("button", { name: "Create reviewed draft" }).click();
  const result = await saved;
  expect(result.status()).toBe(201);
  const recovered = (await result.json()).gpo;
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    `Recovered ${gpo.name}`,
  );
  const inspection = await (
    await request.get(`/api/gpos/${recovered.guid}/registry-repair`)
  ).json();
  expect(inspection.rows[0].record_json).toContain("18446744073709551615");
  expect(recovered.settings.map((s) => s.id)).not.toEqual(
    gpo.settings.map((s) => s.id),
  );
  expect(await (await request.get(`/api/gpos/${gpo.guid}`)).json()).toEqual(
    before,
  );
  expect(
    await (await request.get(`/api/gpos/${gpo.guid}/revisions`)).json(),
  ).toEqual(history);
});

test("requires complete explicit choices and invalidates a preview on any review input", async ({
  page,
  request,
}, testInfo) => {
  const dialog = await open(page, await seed(request, testInfo));
  const apply = dialog.getByRole("button", { name: "Create reviewed draft" });
  await expect(apply).toBeDisabled();
  await expect(dialog.locator('select[name="row-0"]')).toHaveValue("");
  await dialog.locator('select[name="row-0"]').selectOption("keep_raw");
  await dialog.locator('select[name="row-1"]').selectOption("omit");
  await dialog.locator('[name="registry_only"]').check();
  await dialog.getByRole("button", { name: "Preview choices" }).click();
  await expect(dialog.locator("#registry-repair-preview")).toContainText(
    "keep 1 of 2",
  );
  for (const name of ["name", "actor", "reason"]) {
    await expect(apply).toBeEnabled();
    await dialog.locator(`[name="${name}"]`).fill(`Changed ${name}`);
    await expect(apply).toBeDisabled();
    await expect(dialog.locator("#registry-repair-preview")).toBeHidden();
    await dialog.getByRole("button", { name: "Preview choices" }).click();
  }
  await expect(apply).toBeEnabled();
  await dialog.locator('select[name="row-0"]').selectOption("omit");
  await expect(apply).toBeDisabled();
});

test("a retired source response cannot overwrite a reopened recovery dialog", async ({
  page,
  request,
}, testInfo) => {
  const gpo = await seed(request, testInfo),
    held = gate(),
    started = gate();
  let count = 0;
  await page.route(`**/api/gpos/${gpo.guid}/registry-repair`, async (route) => {
    if (++count !== 1) {
      await route.continue();
      return;
    }
    const response = await route.fetch();
    const body = await response.json();
    body.source_name = "Retired source response";
    started.release();
    await held.promise;
    await route.fulfill({ response, json: body });
  });
  await page.goto("/");
  await page.locator(".gpo-item", { hasText: gpo.name }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(gpo.name);
  await page.locator("#repair-registry").click();
  await started.promise;
  await page
    .locator("#registry-repair-dialog")
    .getByRole("button", { name: "Cancel", exact: true })
    .click();
  await page.locator("#repair-registry").click();
  const dialog = page.locator("#registry-repair-dialog");
  await expect(dialog.locator("#registry-repair-source")).toContainText(
    "2 registry rows",
  );
  await dialog.locator('[name="name"]').fill("Newer unsaved recovery");
  const oldResponse = page.waitForResponse((response) =>
    response.url().endsWith(`/api/gpos/${gpo.guid}/registry-repair`),
  );
  held.release();
  await finishReleasedResponse(page, oldResponse);
  await expect(dialog.locator("#registry-repair-source")).not.toContainText(
    "Retired source response",
  );
  await expect(dialog.locator('[name="name"]')).toHaveValue(
    "Newer unsaved recovery",
  );
  await expect(dialog.locator("#registry-repair-apply")).toBeDisabled();
});

test("a retired semantic POST cannot enable creation in a newer editor or clear its error", async ({
  page,
  request,
}, testInfo) => {
  const gpo = await seed(request, testInfo),
    held = gate(),
    started = gate();
  await page.route(
    `**/api/gpos/${gpo.guid}/registry-repair/preview`,
    async (route) => {
      const response = await route.fetch();
      started.release();
      await held.promise;
      await route.fulfill({ response });
    },
  );
  const dialog = await open(page, gpo);
  await dialog.getByRole("button", { name: "Keep all as raw rows" }).click();
  await dialog.locator('[name="registry_only"]').check();
  await dialog.getByRole("button", { name: "Preview choices" }).click();
  await started.promise;
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.locator("#repair-registry").click();
  await expect(dialog.locator("#registry-repair-source")).toContainText(
    "2 registry rows",
  );
  await dialog.locator('[name="reason"]').fill("Newer recovery draft");
  await page.evaluate(async () =>
    (await import("/assets/js/api.mjs")).showPersistentError(
      "Newer independent error",
    ),
  );
  const oldResponse = page.waitForResponse((response) =>
    response.url().endsWith(`/api/gpos/${gpo.guid}/registry-repair/preview`),
  );
  held.release();
  await finishReleasedResponse(page, oldResponse);
  await expect(dialog.locator("#registry-repair-apply")).toBeDisabled();
  await expect(dialog.locator("#registry-repair-preview")).toBeHidden();
  await expect(page.locator("#error-message")).toContainText(
    "Newer independent error",
  );
  await expect(dialog.locator('[name="reason"]')).toHaveValue(
    "Newer recovery draft",
  );
});

test("creation completing after Cancel updates membership and preserves a newer metadata draft", async ({
  page,
  request,
}, testInfo) => {
  const gpo = await seed(request, testInfo),
    held = gate(),
    committed = gate();
  let recovered;
  await page.route(
    `**/api/gpos/${gpo.guid}/registry-repair/apply`,
    async (route) => {
      const response = await route.fetch();
      recovered = (await response.json()).gpo;
      committed.release();
      await held.promise;
      await route.fulfill({ response });
    },
  );
  const dialog = await open(page, gpo);
  await reviewAll(dialog);
  await dialog.getByRole("button", { name: "Create reviewed draft" }).click();
  await committed.promise;
  await expect(dialog.locator('[name="name"]')).toBeDisabled();
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.locator("#edit-metadata").click();
  await page
    .locator('#gpo-form [name="description"]')
    .fill("Newer unsaved description");
  held.release();
  await expect(
    page.locator(".gpo-item", { hasText: recovered.name }),
  ).toBeVisible();
  await expect(page.locator("#gpo-dialog")).toBeVisible();
  await expect(page.locator('#gpo-form [name="description"]')).toHaveValue(
    "Newer unsaved description",
  );
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(gpo.name);
});

test("stale source revisions refuse creation while retaining the reviewed choices", async ({
  page,
  request,
}, testInfo) => {
  const gpo = await seed(request, testInfo),
    dialog = await open(page, gpo);
  await reviewAll(dialog);
  const changed = await request.patch(`/api/gpos/${gpo.guid}`, {
    data: {
      expected_revision: gpo.revision,
      actor: "other-operator",
      reason: "Advance source",
      name: gpo.name,
      description: "New source revision",
    },
  });
  expect(changed.status()).toBe(200);
  const response = page.waitForResponse((r) =>
    r.url().endsWith("/registry-repair/apply"),
  );
  await dialog.getByRole("button", { name: "Create reviewed draft" }).click();
  expect((await response).status()).toBe(409);
  await expect(dialog.locator('[name="row-0"]')).toHaveValue("keep_raw");
  await expect(dialog).toContainText("current is");
});

test("recovery fits a narrow screen and meets the accessibility bar @smoke", async ({
  page,
  request,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const dialog = await open(page, await seed(request, testInfo));
  await reviewAll(dialog);
  expect(
    await dialog.evaluate(
      (element) => element.scrollWidth <= element.clientWidth + 1,
    ),
  ).toBe(true);
  expect(
    await dialog
      .locator("form")
      .evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("registry-repair-narrow.png"),
  });
  const audit = await new AxeBuilder({ page }).analyze();
  expect(
    audit.violations.filter((item) =>
      ["serious", "critical"].includes(item.impact),
    ),
  ).toEqual([]);
});

for (const kind of ["ascii-limit", "astral-unicode", "existing-suffix"]) {
  test(`a ${kind} source gets a valid distinct default recovery name @smoke`, async ({
    page,
    request,
  }, testInfo) => {
    let gpo = await seed(request, testInfo);
    const prefix = `${testInfo.project.name}${kind}`.padEnd(20, "X");
    const name =
      kind === "ascii-limit"
        ? prefix.padEnd(255, "L")
        : kind === "existing-suffix"
          ? prefix.padEnd(235, "L") + " (Registry Recovery)"
          : prefix + "😀".repeat(128);
    const renamed = await request.patch(`/api/gpos/${gpo.guid}`, {
      data: {
        name,
        expected_revision: gpo.revision,
        actor: "browser-test",
        reason: "Seed valid long Unicode name",
      },
    });
    expect(renamed.status()).toBe(200);
    gpo = (await renamed.json()).gpo;
    const dialog = await open(page, gpo);
    const defaultName = await dialog.locator('[name="name"]').inputValue();
    await reviewAll(dialog);
    const saved = page.waitForResponse((r) =>
      r.url().endsWith(`/api/gpos/${gpo.guid}/registry-repair/apply`),
    );
    await dialog.getByRole("button", { name: "Create reviewed draft" }).click();
    const result = await saved;
    expect(result.status()).toBe(201);
    const created = (await result.json()).gpo;
    expect(created.name).toBe(defaultName);
    expect(created.name).not.toBe(name);
    expect(defaultName).toMatch(/ \(registry recovery(?: 2)?\)$/);
    expect(defaultName.length).toBeLessThanOrEqual(255);
    expect(
      (await (await request.get(`/api/gpos/${gpo.guid}`)).json()).gpo.name,
    ).toBe(name);
  });
}

test("the registry-only acknowledgement checkbox sits beside its label text", async ({
  page,
  request,
}, testInfo) => {
  // Issue #140: the dialog's block-level input rule put the checkbox above
  // the sentence it acknowledges.
  const dialog = await open(page, await seed(request, testInfo));
  const label = dialog.locator("label", {
    has: page.locator('[name="registry_only"]'),
  });
  const box = await dialog.locator('[name="registry_only"]').boundingBox();
  const text = await label.evaluate((element) => {
    const range = element.ownerDocument.createRange();
    range.selectNodeContents(element);
    range.setStartAfter(element.querySelector("input"));
    const rect = range.getBoundingClientRect();
    return { x: rect.x, y: rect.y };
  });
  expect(box.x + box.width).toBeLessThanOrEqual(text.x);
  expect(box.y).toBeLessThan(text.y + 24);
  expect(text.y).toBeLessThan(box.y + box.height);
});
