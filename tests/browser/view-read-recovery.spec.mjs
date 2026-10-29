import { expect, test } from "@playwright/test";
import { randomUUID } from "node:crypto";

const warning = "Synthetic newer warning remains actionable";
const surfaces = {
  history: {
    tab: "#tab-history",
    url: "**/api/gpos/*/revisions",
    result: "#history-list",
    ready: /Synthetic recovery revision/,
  },
  diff: {
    tab: "#tab-diff",
    url: "**/api/gpos/*/revisions",
    result: "#revision-diff-results",
    ready: null,
  },
  starter: {
    tab: "#open-starter-gpos",
    url: "**/api/starter-gpos",
    result: "#starter-list",
    ready: /Synthetic Recovery Starter/,
  },
  settings: {
    tab: "#tab-browser",
    url: "**/api/health",
    result: "#browser-load-status",
    ready: null,
  },
  admx: {
    tab: "#tab-admx",
    url: "**/api/health",
    result: "#admx-load-status",
    ready: null,
  },
};
function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
async function flush(page) {
  await page.evaluate(
    () =>
      new Promise((done) =>
        globalThis.requestAnimationFrame(() =>
          globalThis.requestAnimationFrame(done),
        ),
      ),
  );
}
async function prepare(page, request, surface) {
  const response = await request.post("/api/gpos", {
    data: { name: `Synthetic Recovery ${randomUUID()}` },
  });
  expect(response.status()).toBe(201);
  const { gpo } = await response.json();
  expect(
    (
      await request.patch(`/api/gpos/${gpo.guid}`, {
        data: {
          name: gpo.name,
          description: "Synthetic revised fixture",
          expected_revision: 1,
          actor: "browser-test",
          reason: "Synthetic recovery revision",
        },
      })
    ).status(),
  ).toBe(200);
  if (surface === "starter")
    expect(
      (
        await request.post("/api/starter-gpos", {
          data: {
            name: `Synthetic Recovery Starter ${randomUUID()}`,
            actor: "browser-test",
            reason: "Synthetic recovery starter fixture",
          },
        })
      ).status(),
    ).toBe(201);
  await page.goto("/");
  await expect(page.locator("#app-status")).toBeHidden();
  await page.locator(`[data-guid="${gpo.guid}"]`).click();
  await expect(page.locator("#title")).toHaveText(gpo.name);
  return gpo;
}
async function delay(page, pattern, outcome, secondFailure = false) {
  const entered = deferred(),
    gate = deferred(),
    done = deferred();
  let count = 0;
  await page.route(pattern, async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const sequence = ++count;
    if (sequence > 1 && !(secondFailure && sequence === 2))
      return route.continue();
    const response = await route.fetch();
    expect(response.status()).toBe(200);
    if (sequence === 1) {
      entered.resolve();
      await gate.promise;
    }
    if (outcome === "http" || sequence === 2)
      await route.fulfill({
        status: 503,
        json: { error: { message: "Synthetic retired view read failure" } },
      });
    else if (outcome === "network") await route.abort("connectionfailed");
    else await route.fulfill({ response });
    if (sequence === 1) done.resolve();
  });
  return {
    entered: entered.promise,
    count: () => count,
    release: async ({ settle = true } = {}) => {
      gate.resolve();
      await done.promise;
      if (settle) await flush(page);
    },
  };
}
async function overlay(page, surface) {
  await page
    .locator(surface === "starter" ? "#create-starter-gpo" : "#new-gpo")
    .click();
  const dialog = page.locator(
    surface === "starter" ? "#starter-gpo-dialog" : "#gpo-dialog",
  );
  const description = dialog.getByLabel("Description", { exact: true });
  await description.fill("Synthetic protected unfinished draft");
  return { dialog, description };
}
// The unfinished draft is a change, so Esc asks before discarding it.
async function discardWithEscape(page, dialog) {
  await page.keyboard.press("Escape");
  await dialog
    .getByRole("alertdialog", { name: "Discard your changes?" })
    .getByRole("button", { name: "Discard" })
    .click();
  await expect(dialog).toBeHidden();
}
async function warningAndFocus(page, target) {
  await page.evaluate(async (message) => {
    (await import("/assets/js/api.mjs")).showPersistentError(message);
  }, warning);
  await target.focus();
}
async function assertReady(page, surface) {
  const spec = surfaces[surface];
  if (spec.ready)
    await expect(page.locator(spec.result)).toContainText(spec.ready);
  else if (surface === "diff") {
    await expect(page.locator("#revision-diff-from option")).toHaveCount(2);
    await expect(page.locator("#run-revision-diff")).toBeEnabled();
  } else {
    await expect(
      page.locator(
        surface === "settings" ? "#browser-content" : "#admx-content",
      ),
    ).toBeVisible();
    await expect(
      page.locator(surface === "settings" ? "#browser-empty" : "#admx-empty"),
    ).toBeHidden();
    await expect(page.locator(spec.result)).toBeHidden();
    if (surface === "admx")
      await expect(page.locator(".admx-result")).not.toHaveCount(0);
    else
      await expect(
        page.locator("#browser-category-filter option"),
      ).not.toHaveCount(1);
  }
  await expect(page.locator(spec.result)).not.toContainText(
    /Loading|Computing/,
  );
}
for (const surface of Object.keys(surfaces))
  for (const outcome of ["success", "http", "network"]) {
    const smoke =
      surface === "starter" && outcome === "success" ? " @smoke" : "";
    test(`${surface} resumes one owned read after a stacked editor retires ${outcome}${smoke}`, async ({
      page,
      request,
    }) => {
      const gpo = await prepare(page, request, surface),
        spec = surfaces[surface];
      const writes = [];
      page.on("request", (req) => {
        if (!["GET", "HEAD"].includes(req.method())) writes.push(req.url());
      });
      const delayed = await delay(page, spec.url, outcome);
      try {
        await page.locator(spec.tab).click();
        await delayed.entered;
        const { dialog, description } = await overlay(page, surface);
        await warningAndFocus(page, description);
        await delayed.release();
        await expect(description).toHaveValue(
          "Synthetic protected unfinished draft",
        );
        await expect(description).toBeFocused();
        await expect(page.locator("#error-message")).toHaveText(
          `Error: ${warning}`,
        );
        expect(delayed.count()).toBe(1); // no read recovery over the top editor
        await discardWithEscape(page, dialog);
        const returnFocus = page.locator(
          surface === "starter" ? "#create-starter-gpo" : spec.tab,
        );
        await returnFocus.focus();
        await assertReady(page, surface);
        await expect(page.locator("#error-message")).toHaveText(
          `Error: ${warning}`,
        );
        await expect(returnFocus).toBeFocused();
        expect(delayed.count()).toBe(
          ["settings", "admx"].includes(surface) && outcome === "success"
            ? 1
            : 2,
        );
        expect(writes).toEqual([]);
        expect(
          (await (await request.get(`/api/gpos/${gpo.guid}`)).json()).gpo
            .revision,
        ).toBe(2);
      } finally {
        await delayed.release();
      }
    });
  }
for (const surface of ["history", "admx"]) {
  test(`${surface} late completion after editor close recovers once without acquiring new error authority`, async ({
    page,
    request,
  }) => {
    await prepare(page, request, surface);
    const spec = surfaces[surface],
      delayed = await delay(
        page,
        spec.url,
        surface === "admx" ? "http" : "success",
        true,
      );
    try {
      await page.locator(spec.tab).click();
      await delayed.entered;
      const { dialog, description } = await overlay(page, surface);
      await warningAndFocus(page, description);
      await discardWithEscape(page, dialog);
      const focus = page.locator(spec.tab);
      await focus.focus();
      await delayed.release();
      await expect.poll(delayed.count).toBe(2);
      const status = page.locator(spec.result);
      await expect(status).toContainText(/Reload/);
      await expect(status).not.toContainText(
        /Loading|Synthetic retired view read failure/,
      );
      await expect(page.locator("#error-message")).toHaveText(
        `Error: ${warning}`,
      );
      await expect(focus).toBeFocused();
      await flush(page);
      expect(delayed.count()).toBe(2); // recovery budget exhausted
      await status.getByRole("button", { name: /Reload/ }).click();
      await assertReady(page, surface);
      expect(delayed.count()).toBe(3);
    } finally {
      await delayed.release();
    }
  });
}
test("an explicit view change cancels queued recovery and never dispatches a hidden read", async ({
  page,
  request,
}) => {
  await prepare(page, request, "admx");
  const delayed = await delay(page, surfaces.admx.url, "success");
  try {
    await page.locator("#tab-admx").click();
    await delayed.entered;
    await overlay(page, "admx");
    await delayed.release();
    // Close and dispatch the explicit new tab intent in one browser task,
    // before the scheduled recovery frame can run. Both use real DOM actions.
    await page.evaluate(() => {
      globalThis.document.querySelector("#gpo-dialog").close();
      const tab = globalThis.document.querySelector("#tab-history");
      tab.click();
      tab.focus();
    });
    await expect(page.locator("#history-list")).toContainText(
      "Synthetic recovery revision",
    );
    await expect(page.locator("#panel-admx")).toBeHidden();
    await expect(page.locator("#tab-history")).toBeFocused();
    await expect(page.locator("#error-summary")).toBeHidden();
    expect(delayed.count()).toBe(1);
  } finally {
    await delayed.release();
  }
});
