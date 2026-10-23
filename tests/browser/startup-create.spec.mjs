import { expect, test } from "@playwright/test";

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

test("a delayed startup selection preserves a submitted new draft @smoke", async ({
  page,
  request,
}) => {
  const seeded = await request.post("/api/gpos", {
    data: {
      name: `Synthetic startup seed ${Date.now()}`,
      actor: "browser-test",
      reason: "Hold the automatic startup selection during creation",
    },
  });
  expect(seeded.status()).toBe(201);
  const seed = (await seeded.json()).gpo;
  const initialArrived = deferred();
  const initialHeld = deferred();
  const createArrived = deferred();
  const createHeld = deferred();
  let firstList = true;
  let created;
  await page.route("**/api/gpos", async (route) => {
    if (route.request().method() === "GET") {
      if (firstList) {
        firstList = false;
        await route.fulfill({ json: { items: [seed] } });
      } else await route.continue();
      return;
    }
    const response = await route.fetch();
    expect(response.status()).toBe(201);
    created = (await response.json()).gpo;
    createArrived.resolve();
    await createHeld.promise;
    await route.fulfill({ response });
  });
  await page.route(`**/api/gpos/${seed.guid}`, async (route) => {
    const response = await route.fetch();
    initialArrived.resolve();
    await initialHeld.promise;
    await route.fulfill({ response });
  });
  try {
    await page.goto("/");
    await initialArrived.promise;
    await page.locator("#new-gpo").click();
    const dialog = page.locator("#gpo-dialog");
    const name = `Synthetic intended startup draft ${Date.now()}`;
    await dialog.getByLabel("Name", { exact: true }).fill(name);
    await dialog.getByLabel("Name", { exact: true }).press("Enter");
    await createArrived.promise;
    const initialDelivered = page.waitForResponse((response) =>
      response.url().endsWith(`/api/gpos/${seed.guid}`),
    );
    initialHeld.resolve();
    await (await initialDelivered).finished();
    await page.evaluate(
      () =>
        new Promise((resolve) =>
          globalThis.requestAnimationFrame(() =>
            globalThis.requestAnimationFrame(resolve),
          ),
        ),
    );
    // Startup is an automatic read, so it must not become a newer navigation
    // intent and retire the creation that the operator has already submitted.
    await expect(page.locator("#title")).toHaveText("Choose a policy");
    createHeld.resolve();
    await expect(dialog).toBeHidden();
    await expect(page.locator("#title")).toHaveText(name);
    await expect(page.locator(`[data-guid="${created.guid}"]`)).toBeVisible();
    const detail = await request.get(`/api/gpos/${created.guid}`);
    expect(detail.status()).toBe(200);
    expect((await detail.json()).gpo.name).toBe(name);
  } finally {
    initialHeld.resolve();
    createHeld.resolve();
  }
});
