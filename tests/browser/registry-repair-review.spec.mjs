import { expect, test } from "@playwright/test";

async function seed(request, testInfo) {
  const created = await request.post("/api/gpos", {
    data: {
      name: `Synthetic review state ${testInfo.project.name} ${Date.now()}`,
      actor: "browser-test",
      reason: "Seed reviewed source",
    },
  });
  expect(created.status()).toBe(201);
  let gpo = (await created.json()).gpo;
  for (const index of [0, 1]) {
    const response = await request.post(`/api/gpos/${gpo.guid}/settings`, {
      data: {
        expected_revision: gpo.revision,
        actor: "browser-test",
        reason: "Seed inspected row",
        setting: {
          side: "computer",
          hive: "HKLM",
          key: `Software\\Policies\\SyntheticReviewState${index}`,
          value_name: "Value",
          registry_type: "REG_SZ",
          value: `VISIBLE_ROW_${index}`,
          action: "set",
          comment: `Inspected row ${index}`,
        },
      },
    });
    expect(response.status()).toBe(201);
    gpo = (await response.json()).gpo;
  }
  return gpo;
}

async function open(page, gpo) {
  await page.goto("/");
  await page.locator(".gpo-item", { hasText: gpo.name }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(gpo.name);
  await page.locator("#repair-registry").click();
  const dialog = page.locator("#registry-repair-dialog");
  await expect(dialog.locator("pre").first()).toContainText("VISIBLE_ROW_0");
  return dialog;
}

async function chooseAll(dialog) {
  await dialog.getByRole("button", { name: "Keep all as raw rows" }).click();
  await dialog.locator('[name="registry_only"]').check();
}

async function reviewAll(dialog) {
  await chooseAll(dialog);
  await dialog.getByRole("button", { name: "Preview choices" }).click();
  await expect(dialog.locator("#registry-repair-preview")).toBeVisible();
  await expect(dialog.locator("#registry-repair-apply")).toBeEnabled();
}

async function finish(page, promise) {
  await (await promise).finished();
  await page.evaluate(
    () =>
      new Promise((resolve) =>
        globalThis.requestAnimationFrame(() =>
          globalThis.requestAnimationFrame(resolve),
        ),
      ),
  );
}

function gate() {
  let release;
  const promise = new Promise((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

const changedFacts = {
  value(source) {
    const row = JSON.parse(source.rows[0].record_json);
    row.value = "UNINSPECTED_VALUE";
    source.rows[0].record_json = JSON.stringify(row);
  },
  order(source) {
    [source.rows[0].record_json, source.rows[1].record_json] = [
      source.rows[1].record_json,
      source.rows[0].record_json,
    ];
  },
  owner(source) {
    const row = JSON.parse(source.rows[0].record_json);
    row.id = "admx-synthetic:Changed-computer-state";
    source.rows[0].record_json = JSON.stringify(row);
    source.rows[0].identity.claims = [["synthetic:Changed", "computer"]];
  },
  omissions(source) {
    source.omitted_components.links = 1;
  },
  name(source) {
    source.source_name = "Uninspected synthetic source name";
  },
  domain(source) {
    source.domain = "changed.synthetic.test";
  },
};

for (const [name, change] of Object.entries(changedFacts)) {
  test(`refuses preview ${name} facts that differ from inspection at the same revision @smoke`, async ({
    page,
    request,
  }, testInfo) => {
    const gpo = await seed(request, testInfo);
    let applies = 0;
    page.on("request", (request) => {
      if (request.url().endsWith("/registry-repair/apply")) applies++;
    });
    await page.route(
      `**/api/gpos/${gpo.guid}/registry-repair/preview`,
      async (route) => {
        const response = await route.fetch();
        const body = await response.json();
        expect(body.source.source_revision).toBe(gpo.revision);
        change(body.source);
        await route.fulfill({ response, json: body });
      },
    );
    const dialog = await open(page, gpo);
    await chooseAll(dialog);
    const response = page.waitForResponse((r) =>
      r.url().endsWith("/registry-repair/preview"),
    );
    await dialog.getByRole("button", { name: "Preview choices" }).click();
    await finish(page, response);
    await expect(dialog.locator('[name="name"]')).toBeEnabled();
    await expect(dialog.locator(".form-error")).toContainText(
      "reload the policy",
    );
    await expect(dialog.locator("#registry-repair-preview")).toBeHidden();
    await expect(dialog.locator("#registry-repair-apply")).toBeDisabled();
    await expect(dialog.locator("pre").first()).toContainText("VISIBLE_ROW_0");
    await expect(dialog.locator('[name="row-0"]')).toHaveValue("keep_raw");
    expect(applies).toBe(0);
  });
}

function reverseObjectKeys(value) {
  if (Array.isArray(value)) return value.map(reverseObjectKeys);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.entries(value)
      .reverse()
      .map(([key, item]) => [key, reverseObjectKeys(item)]),
  );
}

test("equivalent source facts with reordered object keys remain reviewable @smoke", async ({
  page,
  request,
}, testInfo) => {
  const gpo = await seed(request, testInfo);
  await page.route(
    `**/api/gpos/${gpo.guid}/registry-repair/preview`,
    async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.source = reverseObjectKeys(body.source);
      await route.fulfill({ response, json: body });
    },
  );
  await reviewAll(await open(page, gpo));
});

test("a current source conflict retires the preview after pending cleanup and retains choices @smoke", async ({
  page,
  request,
}, testInfo) => {
  const gpo = await seed(request, testInfo);
  const dialog = await open(page, gpo);
  await dialog.locator('[name="reason"]').fill("Reviewed operator reason");
  const name = await dialog.locator('[name="name"]').inputValue();
  await reviewAll(dialog);
  const advanced = await request.post(`/api/gpos/${gpo.guid}/settings`, {
    data: {
      expected_revision: gpo.revision,
      actor: "other-operator",
      reason: "Advance synthetic source",
      setting: {
        side: "computer",
        hive: "HKLM",
        key: "Software\\Policies\\SyntheticReviewState2",
        value_name: "Value",
        registry_type: "REG_SZ",
        value: "NEW_ROW_VISIBLE",
        action: "set",
      },
    },
  });
  expect(advanced.status()).toBe(201);
  const latest = (await advanced.json()).gpo;
  const response = page.waitForResponse((r) =>
    r.url().endsWith("/registry-repair/apply"),
  );
  await dialog.locator("#registry-repair-apply").click();
  expect((await response).status()).toBe(409);
  await finish(page, response);
  await expect(dialog.locator('[name="name"]')).toBeEnabled();
  await expect(dialog.locator("#registry-repair-apply")).toBeDisabled();
  await expect(dialog.locator("#registry-repair-preview")).toBeHidden();
  await expect(dialog.locator(".form-error")).toContainText(
    "reload the policy",
  );
  await expect(dialog.locator('[name="row-0"]')).toHaveValue("keep_raw");
  await expect(dialog.locator('[name="name"]')).toHaveValue(name);
  await expect(dialog.locator('[name="reason"]')).toHaveValue(
    "Reviewed operator reason",
  );
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.locator(".gpo-item", { hasText: gpo.name }).click();
  await expect(page.locator("#revision")).toHaveText(
    `Revision ${latest.revision}`,
  );
  await page.locator("#repair-registry").click();
  await expect(dialog.locator("#registry-repair-source")).toContainText(
    "3 registry rows",
  );
  await expect(dialog.locator('[name="row-0"]')).toHaveValue("");
  await expect(dialog.locator('[name="row-2"]')).toHaveValue("");
  await expect(dialog.locator('[name="registry_only"]')).not.toBeChecked();
  await reviewAll(dialog);
  await expect(dialog.locator("#registry-repair-preview")).toContainText(
    "keep 3 of 3",
  );
  const created = page.waitForResponse((r) =>
    r.url().endsWith("/registry-repair/apply"),
  );
  await dialog.locator("#registry-repair-apply").click();
  const saved = await created;
  expect(saved.status()).toBe(201);
  const recovered = (await saved.json()).gpo;
  expect(recovered.settings).toHaveLength(3);
  expect(recovered.settings[2].value).toBe("NEW_ROW_VISIBLE");
  expect(recovered.description).toContain(`revision ${latest.revision}`);
});

test("a name collision retires its preview and permits a renamed fresh review @smoke", async ({
  page,
  request,
}, testInfo) => {
  const gpo = await seed(request, testInfo),
    occupied = await seed(request, testInfo);
  const dialog = await open(page, gpo);
  await dialog.locator('[name="name"]').fill(occupied.name);
  await reviewAll(dialog);
  const response = page.waitForResponse((r) =>
    r.url().endsWith("/registry-repair/apply"),
  );
  await dialog.locator("#registry-repair-apply").click();
  expect((await response).status()).toBe(409);
  await finish(page, response);
  await expect(dialog.locator('[name="name"]')).toBeEnabled();
  await expect(dialog.locator("#registry-repair-apply")).toBeDisabled();
  await expect(dialog.locator("#registry-repair-preview")).toBeHidden();
  await expect(dialog.locator(".form-error")).not.toContainText(
    "reload the policy",
  );
  await expect(dialog.locator('[name="row-0"]')).toHaveValue("keep_raw");
  await dialog.locator('[name="name"]').fill(`Fresh ${gpo.name}`);
  await dialog.getByRole("button", { name: "Preview choices" }).click();
  await expect(dialog.locator("#registry-repair-apply")).toBeEnabled();
  const created = page.waitForResponse((r) =>
    r.url().endsWith("/registry-repair/apply"),
  );
  await dialog.locator("#registry-repair-apply").click();
  expect((await created).status()).toBe(201);
});

test("a retired conflict cannot invalidate a newer recovery review @smoke", async ({
  page,
  request,
}, testInfo) => {
  const first = await seed(request, testInfo),
    second = await seed(request, testInfo),
    held = gate(),
    started = gate();
  await page.route(
    `**/api/gpos/${first.guid}/registry-repair/apply`,
    async (route) => {
      const response = await route.fetch();
      expect(response.status()).toBe(409);
      started.release();
      await held.promise;
      await route.fulfill({ response });
    },
  );
  const dialog = await open(page, first);
  await reviewAll(dialog);
  expect(
    (
      await request.patch(`/api/gpos/${first.guid}`, {
        data: {
          expected_revision: first.revision,
          actor: "other-operator",
          reason: "Advance synthetic source",
          name: first.name,
          description: "New source revision",
        },
      })
    ).status(),
  ).toBe(200);
  await dialog.locator("#registry-repair-apply").click();
  await started.promise;
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.locator(".gpo-item", { hasText: second.name }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(second.name);
  await page.locator("#repair-registry").click();
  await expect(dialog.locator("pre").first()).toContainText("VISIBLE_ROW_0");
  await reviewAll(dialog);
  const response = page.waitForResponse((r) =>
    r.url().endsWith(`/api/gpos/${first.guid}/registry-repair/apply`),
  );
  held.release();
  await finish(page, response);
  await expect(dialog.locator("#registry-repair-preview")).toBeVisible();
  await expect(dialog.locator("#registry-repair-apply")).toBeEnabled();
});

test("a transient creation refusal keeps its valid review available for retry @smoke", async ({
  page,
  request,
}, testInfo) => {
  const gpo = await seed(request, testInfo);
  let attempts = 0;
  await page.route(
    `**/api/gpos/${gpo.guid}/registry-repair/apply`,
    async (route) => {
      if (++attempts > 1) return route.continue();
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          error: { message: "Synthetic temporary refusal" },
        }),
      });
    },
  );
  const dialog = await open(page, gpo);
  await reviewAll(dialog);
  const response = page.waitForResponse((r) =>
    r.url().endsWith("/registry-repair/apply"),
  );
  await dialog.locator("#registry-repair-apply").click();
  expect((await response).status()).toBe(503);
  await finish(page, response);
  await expect(dialog.locator('[name="name"]')).toBeEnabled();
  await expect(dialog.locator("#registry-repair-preview")).toBeVisible();
  await expect(dialog.locator("#registry-repair-apply")).toBeEnabled();
  const created = page.waitForResponse((r) =>
    r.url().endsWith("/registry-repair/apply"),
  );
  await dialog.locator("#registry-repair-apply").click();
  expect((await created).status()).toBe(201);
  expect(attempts).toBe(2);
});
