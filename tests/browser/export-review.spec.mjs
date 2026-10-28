import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

const kinds = [
  ["#export", "export.zip"],
  ["#plan", "plan.ps1"],
  ["#gpmc-backup", "gpmc-backup"],
];

async function seed(request, label) {
  const created = await request.post("/api/gpos", {
    data: { name: `Synthetic export review ${label} ${Date.now()}` },
  });
  expect(created.status()).toBe(201);
  const gpo = (await created.json()).gpo;
  const changed = await request.post(`/api/gpos/${gpo.guid}/settings`, {
    data: {
      expected_revision: 1,
      setting: {
        side: "computer",
        hive: "HKLM",
        key: "Software\\Policies\\SyntheticExportReview",
        value_name: "Reviewed",
        registry_type: "REG_SZ",
        value: "Reviewed artifact bytes",
      },
    },
  });
  expect(changed.status()).toBe(201);
  return await changed.json();
}

async function open(page, payload, selector = "#export") {
  await page.goto("/");
  await page.locator(".gpo-item", { hasText: payload.gpo.name }).click();
  await page.locator(selector).click();
  await expect(page.locator("#export-review-dialog")).toBeVisible();
  await expect(page.locator("#export-review-summary")).toContainText(
    payload.review_model_sha256,
  );
}

async function change(request, payload) {
  const response = await request.patch(`/api/gpos/${payload.gpo.guid}`, {
    data: {
      name: payload.gpo.name,
      description: "Synthetic change after review",
      expected_revision: payload.gpo.revision,
    },
  });
  expect(response.status()).toBe(200);
}

async function downloadedBytes(page, testInfo) {
  const pending = page.waitForEvent("download");
  await page.locator("#export-review-download").click();
  const download = await pending;
  const path = testInfo.outputPath(download.suggestedFilename());
  await download.saveAs(path);
  return await readFile(path);
}

for (const [selector, kind] of kinds) {
  test(`downloads the reviewed ${kind} response bytes @smoke`, async ({
    page,
    request,
  }, testInfo) => {
    const payload = await seed(request, kind);
    const expected = await request.get(`/api/gpos/${payload.gpo.guid}/${kind}`);
    expect(expected.status()).toBe(200);
    await open(page, payload, selector);
    const captured = page.waitForRequest((req) =>
      req.url().includes(`/${kind}?`),
    );
    const bytes = await downloadedBytes(page, testInfo);
    const query = new URL((await captured).url()).searchParams;
    expect(query.get("expected_revision")).toBe("2");
    expect(query.get("expected_review_hash")).toBe(payload.review_model_sha256);
    expect(bytes).toEqual(await expected.body());
    await expect(page.locator("#export-review-dialog")).not.toBeVisible();
  });

  test(`refuses stale ${kind} review inline without a download`, async ({
    page,
    request,
    browser,
  }) => {
    const payload = await seed(request, `stale-${kind}`);
    await open(page, payload, selector);
    const other = await browser.newContext({
      baseURL: new URL(page.url()).origin,
    });
    try {
      await change(other.request, payload);
    } finally {
      await other.close();
    }
    const downloads = [];
    page.on("download", (download) => downloads.push(download));
    const response = page.waitForResponse((res) =>
      res.url().includes(`/${kind}?`),
    );
    await page.locator("#export-review-download").click();
    expect((await response).status()).toBe(409);
    await expect(page.locator("#export-review-issues")).toContainText(
      "changed after this export review",
    );
    await expect(page.locator("#export-review-issues")).toBeFocused();
    await expect(page.locator("#export-review-dialog")).toBeVisible();
    expect(downloads).toHaveLength(0);
    await page.locator("#export-review-cancel").click();
    await expect(page.locator(selector)).toBeFocused();
  });
}

test("keeps download available on HTTP without Web Crypto", async ({
  page,
  request,
}, testInfo) => {
  await page.addInitScript(() => {
    Object.defineProperty(globalThis, "crypto", { value: undefined });
  });
  const payload = await seed(request, "portable-http");
  const response = await request.get(
    `/api/gpos/${payload.gpo.guid}/export.zip`,
  );
  await open(page, payload);
  expect(await downloadedBytes(page, testInfo)).toEqual(await response.body());
});

for (const failure of [
  "network",
  "validation",
  "wrong-identity",
  "wrong-bytes",
]) {
  test(`keeps ${failure} errors in the current review and allows retry`, async ({
    page,
    request,
  }, testInfo) => {
    const payload = await seed(request, failure);
    const pattern = `**/api/gpos/${payload.gpo.guid}/export.zip?*`;
    await open(page, payload);
    await page.route(pattern, async (route) => {
      if (failure === "network") return await route.abort("failed");
      if (failure === "validation") {
        return await route.fulfill({
          status: 422,
          contentType: "application/json",
          body: JSON.stringify({
            error: { message: "Synthetic export refusal" },
          }),
        });
      }
      const response = await route.fetch();
      const headers = response.headers();
      headers[
        failure === "wrong-identity"
          ? "x-gpo-studio-review-hash"
          : "x-gpo-studio-artifact-sha256"
      ] = "0".repeat(64);
      await route.fulfill({ response, headers });
    });
    const downloads = [];
    page.on("download", (download) => downloads.push(download));
    await page.locator("#export-review-download").click();
    await expect(page.locator("#export-review-issues")).toHaveClass(
      /error-summary/,
    );
    await expect(page.locator("#export-review-issues")).toBeFocused();
    await expect(page.locator("#export-review-download")).toBeEnabled();
    expect(downloads).toHaveLength(0);
    await page.unroute(pattern);
    expect((await downloadedBytes(page, testInfo)).length).toBeGreaterThan(0);
  });
}

for (const lateResult of ["success", "error", "network"]) {
  test(`retires pending export ${lateResult} after close and reopen`, async ({
    page,
    request,
  }) => {
    await page.addInitScript(() => {
      const nativeFetch = globalThis.fetch;
      globalThis.exportResponsesSettled = 0;
      globalThis.fetch = async (...args) => {
        try {
          return await nativeFetch(...args);
        } finally {
          if (String(args[0]).includes("/export.zip?")) {
            setTimeout(() => globalThis.exportResponsesSettled++, 0);
          }
        }
      };
    });
    const payload = await seed(request, `retired-${lateResult}`);
    const pattern = `**/api/gpos/${payload.gpo.guid}/export.zip?*`;
    let release;
    const gate = new Promise((resolve) => (release = resolve));
    let entered;
    const intercepted = new Promise((resolve) => (entered = resolve));
    let finished;
    const completed = new Promise((resolve) => (finished = resolve));
    let requests = 0;
    await page.route(pattern, async (route) => {
      requests++;
      const response = await route.fetch();
      entered();
      await gate;
      if (lateResult === "network") await route.abort("failed");
      else if (lateResult === "error") {
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({ error: { message: "Retired export error" } }),
        });
      } else await route.fulfill({ response });
      finished();
    });
    const downloads = [];
    page.on("download", (download) => downloads.push(download));
    await open(page, payload);
    await page.locator("#export-review-download").click();
    await intercepted;
    await expect(page.locator("#export-review-download")).toBeDisabled();
    // Even a scripted second click cannot start another pending request.
    await page.locator("#export-review-download").evaluate((button) => {
      button.dispatchEvent(
        new globalThis.MouseEvent("click", { bubbles: true }),
      );
    });
    expect(requests).toBe(1);
    await page.locator("#export-review-cancel").click();
    await page.locator("#export").click();
    const summary = await page.locator("#export-review-summary").textContent();
    await expect(page.locator("#export-review-cancel")).toBeFocused();
    release();
    await completed;
    // Retired handlers return immediately after fetch settles. A subsequent
    // task marks that continuation complete, including its finalizer.
    await page.waitForFunction(() => globalThis.exportResponsesSettled === 1);
    await expect(page.locator("#export-review-dialog")).toBeVisible();
    await expect(page.locator("#export-review-summary")).toHaveText(summary);
    await expect(page.locator("#export-review-download")).toBeEnabled();
    await expect(page.locator("#export-review-download")).toHaveText(
      "Download export",
    );
    await expect(page.locator("#export-review-form")).not.toHaveAttribute(
      "aria-busy",
    );
    await expect(page.locator("#export-review-issues")).not.toContainText(
      "Retired export error",
    );
    await expect(page.locator("#export-review-cancel")).toBeFocused();
    expect(downloads).toHaveLength(0);
  });
}

for (const lateResult of ["success", "error"]) {
  test(`old export ${lateResult} cannot finalize a newer pending download`, async ({
    page,
    request,
  }, testInfo) => {
    await page.addInitScript(() => {
      const nativeFetch = globalThis.fetch;
      globalThis.exportResponsesSettled = 0;
      globalThis.fetch = async (...args) => {
        try {
          return await nativeFetch(...args);
        } finally {
          if (String(args[0]).includes("expected_review_hash=")) {
            setTimeout(() => globalThis.exportResponsesSettled++, 0);
          }
        }
      };
    });
    const payload = await seed(request, `new-pending-${lateResult}`);
    const gates = [0, 1].map(() => {
      let release, entered;
      const gate = new Promise((resolve) => (release = resolve));
      const intercepted = new Promise((resolve) => (entered = resolve));
      return { gate, intercepted, release, entered };
    });
    let count = 0;
    await page.route(`**/api/gpos/${payload.gpo.guid}/*?*`, async (route) => {
      const index = count++;
      const response = await route.fetch();
      gates[index].entered();
      await gates[index].gate;
      if (index === 0 && lateResult === "error") {
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({ error: { message: "Old review error" } }),
        });
      } else await route.fulfill({ response });
    });
    const downloads = [];
    page.on("download", (download) => downloads.push(download));
    await open(page, payload);
    await page.locator("#export-review-download").click();
    await gates[0].intercepted;
    await page.locator("#export-review-dialog [aria-label='Close']").click();
    await page.locator("#plan").click();
    await page.locator("#export-review-download").click();
    await gates[1].intercepted;
    gates[0].release();
    await page.waitForFunction(() => globalThis.exportResponsesSettled === 1);
    await expect(page.locator("#export-review-download")).toBeDisabled();
    await expect(page.locator("#export-review-download")).toHaveText(
      "Preparing download…",
    );
    await expect(page.locator("#export-review-form")).toHaveAttribute(
      "aria-busy",
      "true",
    );
    await expect(page.locator("#export-review-issues")).not.toContainText(
      "Old review error",
    );
    await expect(page.locator("#export-review-cancel")).toBeEnabled();
    expect(downloads).toHaveLength(0);
    const pendingDownload = page.waitForEvent("download");
    gates[1].release();
    const download = await pendingDownload;
    expect(download.suggestedFilename()).toMatch(/-plan\.ps1$/);
    const path = testInfo.outputPath(download.suggestedFilename());
    await download.saveAs(path);
    expect((await readFile(path)).toString("utf8")).toContain(
      "Reviewed artifact bytes",
    );
    expect(downloads).toHaveLength(1);
    expect(count).toBe(2);
  });
}

for (const invalidation of ["capability", "validation"]) {
  test(`stale ${invalidation} change is refused and refreshed review stays blocked`, async ({
    page,
    request,
    browser,
  }) => {
    const payload = await seed(request, invalidation);
    const selector = invalidation === "capability" ? "#export" : "#gpmc-backup";
    await open(page, payload, selector);
    const other = await browser.newContext({
      baseURL: new URL(page.url()).origin,
    });
    let current;
    try {
      const data = { expected_revision: payload.gpo.revision };
      const suffix =
        invalidation === "capability" ? "security-filters" : "settings";
      if (invalidation === "capability") {
        data.filter = {
          principal: "SYNTHETIC\\Workstations",
          permission: "read",
          deny: true,
        };
      } else {
        data.setting = { ...payload.gpo.settings[0], id: undefined };
      }
      const result = await other.request.post(
        `/api/gpos/${payload.gpo.guid}/${suffix}`,
        { data },
      );
      expect(result.status()).toBe(201);
      current = await result.json();
    } finally {
      await other.close();
    }
    const downloads = [];
    page.on("download", (download) => downloads.push(download));
    await page.locator("#export-review-download").click();
    await expect(page.locator("#export-review-issues")).toContainText(
      "changed after this export review",
    );
    expect(downloads).toHaveLength(0);
    await page.locator("#export-review-cancel").click();
    await page.goto("/");
    await page.locator(".gpo-item", { hasText: current.gpo.name }).click();
    await expect(page.locator(selector)).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    // The action is already disabled; its review handler must also fail closed.
    await page.locator(selector).dispatchEvent("click");
    await expect(page.locator("#export-review-download")).toBeDisabled();
    await expect(page.locator("#export-review-download")).toHaveText(
      "Download blocked",
    );
  });
}
