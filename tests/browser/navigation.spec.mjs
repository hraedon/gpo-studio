import { expect, test } from "@playwright/test";

async function seed(request, suffix) {
  const name = `Synthetic Navigation ${suffix} ${Date.now()}`;
  const payload = await (
    await request.post("/api/gpos", {
      data: { name, actor: "browser-test", reason: "Navigation test" },
    })
  ).json();
  return payload.gpo;
}

test("switching GPO refreshes the active revision diff", async ({
  page,
  request,
}) => {
  const first = await seed(request, "First");
  const second = await seed(request, "Second");
  await request.patch(`/api/gpos/${second.guid}`, {
    data: {
      name: second.name,
      description: "Second revision",
      actor: "browser-test",
      reason: "Synthetic second revision",
      expected_revision: 1,
    },
  });
  await page.goto("/");
  await page.locator(".gpo-item", { hasText: first.name }).click();
  await page.getByRole("tab", { name: "Diff", exact: true }).click();
  await expect(page.locator("#run-revision-diff")).toBeDisabled();
  await page.locator(".gpo-item", { hasText: second.name }).click();
  await expect(page.locator("#revision-diff-to")).toHaveValue("2");
  await page.locator("#run-revision-diff").click();
  await expect(page.locator("#revision-diff-results")).toContainText(
    "Second revision",
  );
  await page.locator(".gpo-item", { hasText: first.name }).click();
  await expect(page.locator("#revision-diff-to")).toHaveValue("1");
  await expect(page.locator("#run-revision-diff")).toBeDisabled();
  await expect(page.locator("#revision-diff-results")).toContainText(
    "Create another revision",
  );
});

test("a late navigation response cannot replace the latest selection", async ({
  page,
  request,
}) => {
  const first = await seed(request, "Slow");
  const second = await seed(request, "Latest");
  await page.goto("/");
  await expect(page.locator("#title")).not.toHaveText("Choose a policy");
  let release;
  const delayed = new Promise((resolve) => {
    release = resolve;
  });
  await page.route(`**/api/gpos/${first.guid}`, async (route) => {
    await delayed;
    await route.continue();
  });
  await page.locator(".gpo-item", { hasText: first.name }).click();
  await page.locator(".gpo-item", { hasText: second.name }).click();
  await expect(page.locator("#title")).toHaveText(second.name);
  const response = page.waitForResponse((response) =>
    response.url().endsWith(`/api/gpos/${first.guid}`),
  );
  release();
  await response;
  await expect(page.locator("#title")).toHaveText(second.name);
});

test("an older policy list cannot hide a newer fork from the sidebar", async ({
  page,
  request,
}) => {
  const first = await seed(request, "List first");
  const second = await seed(request, "List latest");
  const archived = await request.patch(`/api/gpos/${second.guid}`, {
    data: {
      name: second.name,
      status: "archived",
      actor: "browser-test",
      reason: "Synthetic fork baseline",
      expected_revision: 1,
    },
  });
  expect(archived.status()).toBe(200);
  await page.goto("/");
  await page.locator(".gpo-item", { hasText: first.name }).click();
  let release;
  let started;
  const held = new Promise((resolve) => {
    release = resolve;
  });
  const ready = new Promise((resolve) => {
    started = resolve;
  });
  let firstList = true;
  await page.route("**/api/gpos", async (route) => {
    if (route.request().method() !== "GET" || !firstList) {
      await route.continue();
      return;
    }
    firstList = false;
    const response = await route.fetch();
    started();
    await held;
    await route.fulfill({ response });
  });
  const createdName = `Synthetic Older List ${Date.now()}`;
  await page.locator("#new-gpo").click();
  const creation = page.locator("#gpo-dialog");
  await creation.locator('[name="name"]').fill(createdName);
  await creation.getByRole("button", { name: "Create policy" }).click();
  await ready;
  await page.locator(".gpo-item", { hasText: second.name }).click();
  const latestName = `Synthetic Newer Fork ${Date.now()}`;
  await page.locator("#fork-gpo").click();
  const forkDialog = page.locator("#fork-dialog");
  await forkDialog.locator('[name="name"]').fill(latestName);
  const forkResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/gpos/${second.guid}/fork`) &&
      response.request().method() === "POST",
  );
  await forkDialog.getByRole("button", { name: "Fork", exact: true }).click();
  const latest = (await (await forkResponse).json()).gpo;
  await expect(forkDialog).toBeHidden();
  await expect(page.locator("#title")).toHaveText(latestName);
  await expect(
    page.locator(`.gpo-item[data-guid="${latest.guid}"]`),
  ).toBeVisible();
  const finished = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/gpos",
  );
  release();
  await (await finished).finished();
  await page.evaluate(
    () =>
      new Promise((resolve) =>
        globalThis.requestAnimationFrame(() =>
          globalThis.requestAnimationFrame(resolve),
        ),
      ),
  );
  await expect(page.locator("#title")).toHaveText(latestName);
  await expect(
    page.locator(`.gpo-item[data-guid="${latest.guid}"]`),
  ).toBeVisible();
});
