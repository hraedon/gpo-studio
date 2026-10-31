import { expect, test } from "@playwright/test";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const ids = Array.from(
  { length: 101 },
  (_, index) =>
    `Synthetic.BulkBoundary:Chunk${String(index + 1).padStart(3, "0")}`,
);
let directory, original, catalogueRevision;

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function writePack(variant) {
  const path = join(directory, variant);
  mkdirSync(path);
  writeFileSync(
    join(path, "boundary.admx"),
    String.raw`<policyDefinitions>
<policyNamespaces><target prefix="synthetic" namespace="Synthetic.BulkBoundary"/></policyNamespaces>
<policies>${ids
      .map(
        (id, index) => `<policy name="${id.split(":")[1]}" class="Both"
 key="Software\\Policies\\Synthetic\\BulkBoundary${variant}\\Item${index + 1}" valueName="Flag"
 displayName="Synthetic boundary policy ${String(index + 1).padStart(3, "0")}">
<enabledValue><decimal value="11"/></enabledValue><disabledValue><decimal value="0"/></disabledValue>
</policy>`,
      )
      .join("\n")}</policies></policyDefinitions>`,
  );
  writeFileSync(
    join(path, "boundary.adml"),
    "<policyDefinitionResources><resources><stringTable/></resources></policyDefinitionResources>",
  );
}
async function loadPack(request, variant) {
  const response = await request.post("/api/templates/ingest", {
    data: { name: "default", kind: "local", path: join(directory, variant) },
  });
  expect(response.status()).toBe(201);
  const result = await response.json();
  expect(result.policy_count).toBe(101);
  expect(result.catalogue_revision).toEqual(expect.any(String));
  return result.catalogue_revision;
}

test.beforeAll(async ({ request }) => {
  original = (await (await request.get("/api/templates")).json()).items.find(
    (source) => source.name === "default",
  );
  expect(original).toBeTruthy();
  directory = mkdtempSync(join(tmpdir(), "studio-bulk-boundary-"));
  writePack("A");
  writePack("B");
  catalogueRevision = await loadPack(request, "A");
});
test.afterAll(async ({ request }) => {
  try {
    if (original)
      expect(
        (
          await request.post("/api/templates/ingest", {
            data: { name: "default", kind: original.kind, path: original.path },
          })
        ).status(),
      ).toBe(201);
  } finally {
    if (directory) rmSync(directory, { recursive: true, force: true });
  }
});

async function seed(request, count, label) {
  const response = await request.post("/api/gpos", {
    data: {
      name: `Synthetic bulk boundary ${label} ${Date.now()}`,
      actor: "browser-test",
      reason: "Seed chunk boundary",
    },
  });
  expect(response.status()).toBe(201);
  let gpo = (await response.json()).gpo;
  // Real API setup; only the subsequent browser-initiated operations are traced.
  for (const side of ["computer", "user"]) {
    for (let start = 0; start < count; start += 100) {
      const configured = await request.post(
        `/api/gpos/${gpo.guid}/bulk-policy-state`,
        {
          data: {
            actor: "browser-test",
            reason: "Configure synthetic boundary policies",
            expected_revision: gpo.revision,
            expected_catalogue_revision: catalogueRevision,
            policy_ids: ids.slice(start, Math.min(start + 100, count)),
            side,
            target_state: "enabled",
          },
        },
      );
      expect(configured.status()).toBe(200);
      gpo = (await configured.json()).gpo;
    }
  }
  expect(gpo.settings).toHaveLength(count * 2);
  expect(gpo.settings.every((setting) => setting.value === "11")).toBe(true);
  return gpo;
}
async function openSelected(page, gpo, count) {
  await page.goto("/");
  await page.locator(`[data-guid="${gpo.guid}"]`).click();
  await expect(page.locator("#revision")).toHaveText(
    `Revision ${gpo.revision}`,
  );
  await page
    .getByRole("tab", { name: "Settings browser", exact: true })
    .click();
  await expect(page.locator(".browser-bulk-checkbox")).toHaveCount(
    Math.min(count * 2, 200),
  );
  await expect(page.locator("#browser-resolved-count")).toHaveText(
    count === 101 ? "200 of 202" : "200",
  );
  // The API deliberately caps one filtered result at 200 rows. Select each
  // side's 101 visible entries, preserving the other side's hidden selections.
  await page.locator('#panel-browser .chip[data-side="computer"]').click();
  await expect(page.locator(".browser-bulk-checkbox")).toHaveCount(count);
  await expect(
    page.locator('.browser-bulk-checkbox[data-bulk-side="computer"]'),
  ).toHaveCount(count);
  await page.locator("#browser-bulk-select-all").check();
  await expect(page.locator("#browser-bulk-count")).toHaveText(
    `${count} selected`,
  );
  await page.locator('#panel-browser .chip[data-side="user"]').click();
  await expect(page.locator(".browser-bulk-checkbox")).toHaveCount(count);
  await expect(
    page.locator('.browser-bulk-checkbox[data-bulk-side="user"]'),
  ).toHaveCount(count);
  await page.locator("#browser-bulk-select-all").check();
  await page
    .locator("#browser-bulk-reason")
    .fill("Review both sides across the chunk boundary");
  await expect(page.locator(".browser-bulk-checkbox:checked")).toHaveCount(
    count,
  );
  await expect(page.locator("#browser-bulk-count")).toHaveText(
    `${count * 2} selected`,
  );
}
function expectedChunks(count) {
  return ["computer", "user"].flatMap((side) => {
    const chunks = [];
    for (let start = 0; start < count; start += 100)
      chunks.push({
        side,
        ids: ids.slice(start, Math.min(start + 100, count)),
      });
    return chunks;
  });
}
function checkCalls(calls, chunks, firstRevision) {
  expect(calls).toHaveLength(chunks.length);
  calls.forEach((call, index) => {
    expect(call.body.side).toBe(chunks[index].side);
    expect(call.body.policy_ids).toEqual(chunks[index].ids);
    expect(call.body.expected_revision).toBe(firstRevision + index);
    expect(call.body.expected_catalogue_revision).toBe(catalogueRevision);
    expect(call.body.actor).toBe("local-operator");
    expect(call.body.reason).toBe(
      "Review both sides across the chunk boundary",
    );
    expect(call.body.target_state).toBe("disabled");
    expect(call.status).toBe(200);
    expect(call.revision).toBe(firstRevision + index + 1);
  });
}
async function saved(request, guid) {
  return (await (await request.get(`/api/gpos/${guid}`)).json()).gpo;
}
async function trace(
  page,
  gpo,
  {
    failAt = -1,
    holdFirst = false,
    holdAt = holdFirst ? 0 : -1,
    switchAfterFirst = null,
  } = {},
) {
  const calls = [],
    arrived = deferred(),
    release = deferred();
  await page.route(
    `**/api/gpos/${gpo.guid}/bulk-policy-state`,
    async (route) => {
      const index = calls.length;
      const call = { body: route.request().postDataJSON() };
      calls.push(call);
      if (index === failAt) {
        call.status = 503;
        await route.fulfill({
          status: 503,
          json: { error: { message: "Synthetic chunk unavailable" } },
        });
        return;
      }
      const response = await route.fetch();
      call.status = response.status();
      const result = await response.json();
      call.revision = result.gpo?.revision;
      call.error = result.error;
      if (index === 0 && switchAfterFirst) await switchAfterFirst();
      if (index === holdAt) {
        arrived.resolve();
        await release.promise;
      }
      await route.fulfill({ response });
    },
  );
  return { calls, arrived: arrived.promise, release: () => release.resolve() };
}

async function recordCalls(calls) {
  await test.info().attach("bulk-request-trace", {
    body: JSON.stringify(calls, null, 2),
    contentType: "application/json",
  });
  console.log(
    "BULK_CHUNK_TRACE",
    JSON.stringify(
      calls.map((call) => ({
        side: call.body.side,
        count: call.body.policy_ids.length,
        expected: call.body.expected_revision,
        status: call.status,
        returned: call.revision ?? null,
      })),
    ),
  );
}

for (const count of [100, 101]) {
  test(`chunks ${count} reviewed Both-class policies per side with exact CAS propagation ${count === 101 ? "@smoke" : ""}`, async ({
    page,
    request,
  }) => {
    const gpo = await seed(request, count, `success ${count}`);
    await openSelected(page, gpo, count);
    const pending = await trace(page, gpo, { holdFirst: true });
    try {
      await page.locator('[data-bulk-state="disabled"]').click();
      await pending.arrived;
      expect(pending.calls).toHaveLength(1);
      await expect(page.locator('[data-bulk-state="disabled"]')).toBeDisabled();
      await expect(page.locator("#browser-bulk-reason")).toBeDisabled();
      // Delivering repeated click events cannot replay an in-flight chunk.
      await page.locator('[data-bulk-state="disabled"]').dispatchEvent("click");
      expect(pending.calls).toHaveLength(1);
      pending.release();
      await expect(page.locator("#browser-bulk-bar")).toBeHidden();
      const chunks = expectedChunks(count);
      checkCalls(pending.calls, chunks, gpo.revision);
      const result = await saved(request, gpo.guid);
      expect(result.revision).toBe(gpo.revision + chunks.length);
      expect(result.settings).toHaveLength(count * 2);
      expect(result.settings.every((setting) => setting.value === "0")).toBe(
        true,
      );
      await expect(page.locator("#revision")).toHaveText(
        `Revision ${result.revision}`,
      );
      await expect(page.locator(`[data-guid="${gpo.guid}"]`)).toContainText(
        `r${result.revision}`,
      );
      await expect(page.locator("#browser-bulk-reason")).toHaveValue("");
      await expect(page.locator("#error-summary")).toBeHidden();
      await recordCalls(pending.calls);
    } finally {
      pending.release();
    }
  });
}

for (const failAt of [1, 3]) {
  test(`failure of ${failAt === 1 ? "Computer" : "User"} chunk two retains only uncommitted selections until an explicit retry`, async ({
    page,
    request,
  }) => {
    const gpo = await seed(request, 101, `partial ${failAt}`);
    await openSelected(page, gpo, 101);
    const { calls } = await trace(page, gpo, { failAt });
    await page.locator('[data-bulk-state="disabled"]').click();
    const chunks = expectedChunks(101),
      completed = chunks.slice(0, failAt),
      remaining = chunks.slice(failAt);
    const changed = completed.reduce(
      (count, chunk) => count + chunk.ids.length,
      0,
    );
    const retained = 202 - changed;
    await expect(page.locator("#error-summary")).toContainText(
      `${changed} policies changed before the remaining bulk change failed: Synthetic chunk unavailable`,
    );
    expect(calls).toHaveLength(failAt + 1);
    checkCalls(calls.slice(0, failAt), completed, gpo.revision);
    expect(calls[failAt].body.policy_ids).toEqual(chunks[failAt].ids);
    expect(calls[failAt].body.expected_revision).toBe(gpo.revision + failAt);
    expect(calls[failAt].status).toBe(503);
    await expect(page.locator("#browser-bulk-count")).toHaveText(
      `${retained} selected`,
    );
    await expect(page.locator("#browser-bulk-reason")).toHaveValue(
      "Review both sides across the chunk boundary",
    );
    const selected = [];
    for (const side of ["computer", "user"]) {
      await page.locator(`#panel-browser .chip[data-side="${side}"]`).click();
      await expect(page.locator(".browser-bulk-checkbox")).toHaveCount(101);
      const wanted = remaining
        .filter((chunk) => chunk.side === side)
        .reduce((count, chunk) => count + chunk.ids.length, 0);
      await expect(page.locator(".browser-bulk-checkbox:checked")).toHaveCount(
        wanted,
      );
      selected.push(
        ...(await page
          .locator(".browser-bulk-checkbox:checked")
          .evaluateAll((boxes) =>
            boxes.map((box) => ({
              side: box.dataset.bulkSide,
              id: box.dataset.bulkPolicyId,
            })),
          )),
      );
      await expect(page.locator("#browser-bulk-count")).toHaveText(
        `${retained} selected`,
      );
    }
    expect(selected.map((item) => `${item.side}:${item.id}`).sort()).toEqual(
      remaining
        .flatMap((chunk) => chunk.ids.map((id) => `${chunk.side}:${id}`))
        .sort(),
    );
    const partial = await saved(request, gpo.guid);
    expect(partial.revision).toBe(gpo.revision + failAt);
    expect(
      partial.settings.filter((setting) => setting.value === "0"),
    ).toHaveLength(changed);
    expect(
      partial.settings.filter((setting) => setting.value === "11"),
    ).toHaveLength(retained);
    await expect(page.locator("#revision")).toHaveText(
      `Revision ${partial.revision}`,
    );
    // The failure has settled; there is no automatic retry of any chunk.
    expect(calls).toHaveLength(failAt + 1);
    await page.locator('[data-bulk-state="disabled"]').click();
    await expect(page.locator("#browser-bulk-bar")).toBeHidden();
    checkCalls(calls.slice(failAt + 1), remaining, partial.revision);
    const successful = calls
      .filter((call) => call.status === 200)
      .flatMap((call) =>
        call.body.policy_ids.map((id) => `${call.body.side}:${id}`),
      );
    expect(successful).toHaveLength(202);
    expect(new Set(successful).size).toBe(202);
    const final = await saved(request, gpo.guid);
    expect(final.revision).toBe(gpo.revision + 4);
    expect(final.settings.every((setting) => setting.value === "0")).toBe(true);
    await expect(page.locator("#revision")).toHaveText(
      `Revision ${final.revision}`,
    );
    await expect(page.locator("#error-summary")).toBeHidden();
    await recordCalls(calls);
  });
}

test("a real catalogue switch between Computer chunks preserves the first 100 commits and clears obsolete selections", async ({
  page,
  request,
}) => {
  const gpo = await seed(request, 101, "catalogue change");
  await openSelected(page, gpo, 101);
  const reviewed = catalogueRevision;
  const { calls } = await trace(page, gpo, {
    switchAfterFirst: () => loadPack(request, "B"),
  });
  try {
    await page.locator('[data-bulk-state="disabled"]').click();
    await expect(page.locator("#error-summary")).toContainText(
      "100 policies changed before the remaining bulk change failed",
    );
    await expect(page.locator("#error-summary")).toContainText(
      "The ADMX catalogue changed",
    );
    expect(calls).toHaveLength(2);
    expect(calls.map((call) => call.body.side)).toEqual([
      "computer",
      "computer",
    ]);
    expect(calls.map((call) => call.body.expected_revision)).toEqual([
      gpo.revision,
      gpo.revision + 1,
    ]);
    expect(calls.map((call) => call.body.expected_catalogue_revision)).toEqual([
      reviewed,
      reviewed,
    ]);
    expect(calls.map((call) => call.status)).toEqual([200, 409]);
    expect(calls[1].error.code).toBe("catalogue_conflict");
    await expect(page.locator("#browser-bulk-bar")).toBeHidden();
    await expect(page.locator(".browser-bulk-checkbox:checked")).toHaveCount(0);
    const result = await saved(request, gpo.guid);
    expect(result.revision).toBe(gpo.revision + 1);
    expect(
      result.settings.filter((setting) => setting.value === "0"),
    ).toHaveLength(100);
    expect(
      result.settings.filter((setting) => setting.value === "11"),
    ).toHaveLength(102);
    expect(
      result.settings.every((setting) => setting.key.includes("BulkBoundaryA")),
    ).toBe(true);
    await recordCalls(calls);
  } finally {
    catalogueRevision = await loadPack(request, "A");
  }
});

test("a deletion observed during a chunk stops the bulk change with a message", async ({
  page,
  request,
}) => {
  // Issue #140: the chunk loop returned with no message at all.
  const gpo = await seed(request, 101, "deleted mid-bulk");
  await openSelected(page, gpo, 101);
  const pending = await trace(page, gpo, { holdFirst: true });
  try {
    await page.locator('[data-bulk-state="disabled"]').click();
    await pending.arrived;
    // The tab learns of the deletion while the first chunk is in flight.
    await page.evaluate(async (guid) => {
      const { observeGpoDeleted } = await import("/assets/js/state.mjs");
      observeGpoDeleted(guid);
    }, gpo.guid);
    pending.release();
    await expect(page.locator("#error-summary")).toContainText(
      "This policy was deleted",
    );
    expect(pending.calls).toHaveLength(1);
  } finally {
    pending.release();
  }
});

test("a deletion observed during the last chunk does not claim a remainder failed", async ({
  page,
  request,
}) => {
  // Issue #145: every selected policy had changed, yet the message read
  // "… changed before the remaining bulk change failed".
  const gpo = await seed(request, 101, "deleted in last chunk");
  await openSelected(page, gpo, 101);
  const pending = await trace(page, gpo, { holdAt: 3 });
  try {
    await page.locator('[data-bulk-state="disabled"]').click();
    await pending.arrived;
    await page.evaluate(async (guid) => {
      const { observeGpoDeleted } = await import("/assets/js/state.mjs");
      observeGpoDeleted(guid);
    }, gpo.guid);
    pending.release();
    await expect(page.locator("#error-summary")).toContainText(
      "202 policies changed, then the bulk change stopped: This policy was deleted",
    );
    await expect(page.locator("#error-summary")).not.toContainText("remaining");
    expect(pending.calls).toHaveLength(4);
  } finally {
    pending.release();
  }
});
