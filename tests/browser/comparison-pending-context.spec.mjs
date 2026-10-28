import { expect, test } from "@playwright/test";

async function seed(request, suffix) {
  const created = await request.post("/api/gpos", {
    data: {
      name: `Synthetic Comparison ${suffix} ${Date.now()}`,
      actor: "browser-test",
      reason: "Synthetic comparison fixture",
    },
  });
  expect(created.status()).toBe(201);
  let { gpo } = await created.json();
  const changed = await request.patch(`/api/gpos/${gpo.guid}`, {
    data: {
      name: gpo.name,
      description: "Synthetic comparison revised",
      expected_revision: 1,
      actor: "browser-test",
      reason: "Synthetic comparison revision",
    },
  });
  expect(changed.status()).toBe(200);
  ({ gpo } = await changed.json());
  return gpo;
}
async function prepare(page, request, type) {
  const gpo = await seed(request, "Selected");
  const second = await seed(request, "Draft"),
    third = await seed(request, "Observed");
  await page.goto("/");
  await page.locator(`[data-guid="${gpo.guid}"]`).click();
  await expect(page.locator("#title")).toHaveText(gpo.name);
  await page.locator("#tab-diff").click();
  await expect(page.locator("#revision-diff-from option")).toHaveCount(2);
  if (type === "estate") {
    await page.locator("#diff-baseline").selectOption(gpo.guid);
    await page.locator("#diff-draft").selectOption(second.guid);
    await page.locator("#diff-observed").selectOption(third.guid);
  }
  return {
    gpo,
    button: page.locator(
      type === "estate" ? "#run-diff" : "#run-revision-diff",
    ),
    results: page.locator(
      type === "estate" ? "#diff-results" : "#revision-diff-results",
    ),
    url:
      type === "estate"
        ? "**/api/estate/diff"
        : "**/api/gpos/*/revisions/diff?*",
  };
}
async function hold(page, url, outcome = "success") {
  let release, entered, done;
  const ready = new Promise((resolve) => (entered = resolve)),
    gate = new Promise((resolve) => (release = resolve)),
    finished = new Promise((resolve) => (done = resolve));
  await page.route(url, async (route) => {
    const response = await route.fetch();
    entered();
    await gate;
    if (outcome === "network") await route.abort("connectionfailed");
    else if (outcome === "http")
      await route.fulfill({
        status: 503,
        json: { error: { message: "Synthetic comparison refusal" } },
      });
    else await route.fulfill({ response });
    done();
  });
  return {
    ready,
    release: async () => {
      release();
      await finished;
      await page.evaluate(
        () =>
          new Promise((resolve) =>
            globalThis.requestAnimationFrame(() =>
              globalThis.requestAnimationFrame(resolve),
            ),
          ),
      );
    },
  };
}
for (const type of ["revision", "estate"])
  for (const outcome of ["success", "http", "network"]) {
    const smoke = type === "estate" && outcome === "success" ? " @smoke" : "";
    test(`${type} comparison releases its own pending UI after a canceled editor and ${outcome}${smoke}`, async ({
      page,
      request,
    }) => {
      const { gpo, button, results, url } = await prepare(page, request, type);
      const delayed = await hold(page, url, outcome);
      await button.click();
      await delayed.ready;
      await page.locator("#new-gpo").click();
      await expect(page.locator("#gpo-dialog")).toBeVisible();
      await page.keyboard.press("Escape");
      await delayed.release();
      await expect(button).toBeEnabled();
      await expect(results).not.toContainText(
        /Computing diff|Comparing revisions/,
      );
      await expect(results).not.toContainText("Synthetic comparison refusal");
      expect(
        (await (await request.get(`/api/gpos/${gpo.guid}`)).json()).gpo
          .revision,
      ).toBe(2);
    });
  }
for (const outcome of ["http", "network"])
  test(`current comparison ${outcome} reports visibly and can retry`, async ({
    page,
    request,
  }) => {
    const { button, results, url } = await prepare(page, request, "revision");
    const delayed = await hold(page, url, outcome);
    await button.click();
    await delayed.ready;
    await delayed.release();
    await expect(button).toBeEnabled();
    await expect(results).toContainText(
      outcome === "http"
        ? "Synthetic comparison refusal"
        : "server is unavailable",
    );
    await page.unroute(url);
    await button.click();
    await expect(results).toContainText("Metadata");
    await expect(button).toBeEnabled();
  });
test("an old comparison finalizer cannot unlock or erase a newer pending comparison", async ({
  page,
  request,
}) => {
  const { gpo, button, results, url } = await prepare(
    page,
    request,
    "revision",
  );
  let releaseFirst,
    releaseSecond,
    firstEntered,
    secondEntered,
    firstDone,
    secondDone;
  const firstReady = new Promise((r) => (firstEntered = r)),
    secondReady = new Promise((r) => (secondEntered = r)),
    firstGate = new Promise((r) => (releaseFirst = r)),
    secondGate = new Promise((r) => (releaseSecond = r)),
    firstFinished = new Promise((r) => (firstDone = r)),
    secondFinished = new Promise((r) => (secondDone = r));
  let requests = 0;
  await page.route(url, async (route) => {
    const sequence = ++requests,
      response = await route.fetch();
    if (sequence === 1) {
      firstEntered();
      await firstGate;
      await route.fulfill({ response });
      firstDone();
    } else {
      secondEntered();
      await secondGate;
      await route.fulfill({ response });
      secondDone();
    }
  });
  await button.click();
  await firstReady;
  await page.locator("#revision-diff-from").selectOption("2");
  await page.locator("#revision-diff-to").selectOption("1");
  await expect(button).toBeEnabled();
  await button.click();
  await secondReady;
  releaseFirst();
  await firstFinished;
  await page.evaluate(
    () =>
      new Promise((r) =>
        globalThis.requestAnimationFrame(() =>
          globalThis.requestAnimationFrame(r),
        ),
      ),
  );
  await expect(button).toBeDisabled();
  await expect(results).toContainText("Comparing revisions");
  releaseSecond();
  await secondFinished;
  await expect(button).toBeEnabled();
  await expect(results).toContainText("Metadata");
  expect(requests).toBe(2);
  expect(
    (await (await request.get(`/api/gpos/${gpo.guid}`)).json()).gpo.revision,
  ).toBe(2);
});
