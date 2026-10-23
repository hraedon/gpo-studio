import { test, expect } from "@playwright/test";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const policyId = "Synthetic.ConflictSnapshot:BothSnapshot";
let pack;
let original;
function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
async function flush(page) {
  await page.evaluate(
    () => new Promise((resolve) => globalThis.requestAnimationFrame(resolve)),
  );
}
async function snapshot(page, request, guid) {
  const payload = (await (await request.get(`/api/gpos/${guid}`)).json()).gpo;
  return {
    viewRevision: await page.locator("#revision").textContent(),
    summary: await page.locator(`[data-guid="${guid}"]`).textContent(),
    backendRevision: payload.revision,
    backendName: payload.name,
    settings: payload.settings.map((item) => ({
      side: item.side,
      key: item.key,
      name: item.value_name,
      value: item.value,
    })),
    conflictOpen: await page
      .locator("#conflict-dialog")
      .evaluate((dialog) => dialog.open),
    editorOpen: await page
      .locator("#gpo-dialog")
      .evaluate((dialog) => dialog.open),
    editorName: await page.locator('#gpo-form [name="name"]').inputValue(),
  };
}

test.beforeAll(async ({ request }) => {
  original = (await (await request.get("/api/templates")).json()).items.find(
    (item) => item.name === "default",
  );
  expect(original).toBeTruthy();
  pack = mkdtempSync(join(tmpdir(), "studio-conflict-policy-"));
  mkdirSync(join(pack, "en-US"));
  writeFileSync(
    join(pack, "probe.admx"),
    String.raw`<policyDefinitions><policyNamespaces><target prefix="synthetic" namespace="Synthetic.ConflictSnapshot"/></policyNamespaces><policies><policy name="BothSnapshot" class="Both" key="Software\Policies\Synthetic\ConflictSnapshot" valueName="Enabled" displayName="$(string.Policy)"><enabledValue><decimal value="11"/></enabledValue><disabledValue><decimal value="0"/></disabledValue></policy></policies></policyDefinitions>`,
  );
  writeFileSync(
    join(pack, "en-US/probe.adml"),
    '<policyDefinitionResources><resources><stringTable><string id="Policy">Synthetic conflict snapshot policy</string></stringTable></resources></policyDefinitionResources>',
  );
  expect(
    (
      await request.post("/api/templates/ingest", {
        data: { name: "default", kind: "local", path: pack },
      })
    ).status(),
  ).toBe(201);
});
test.afterAll(async ({ request }) => {
  if (original)
    expect(
      (
        await request.post("/api/templates/ingest", {
          data: { name: "default", kind: original.kind, path: original.path },
        })
      ).status(),
    ).toBe(201);
  if (pack) rmSync(pack, { recursive: true, force: true });
});

for (const timing of ["held-get", "held-diff", "shown-dialog"]) {
  for (const action of ["reload", "reapply"]) {
    test(`${timing}: ${action} cannot replace known revision five with reviewed revision four ${timing === "shown-dialog" && action === "reapply" ? "@smoke" : ""}`, async ({
      page,
      request,
    }) => {
      const bulkGate = deferred(),
        secondArrived = deferred(),
        secondDone = deferred();
      const detailGate = deferred(),
        detailArrived = deferred(),
        detailDone = deferred();
      const record = {
        timing,
        action,
        bulk: [],
        patch: null,
        reads: [],
        patches: [],
      };
      const diffArrived = deferred();

      try {
        const created = await request.post("/api/gpos", {
          data: {
            name: `Synthetic Conflict ${timing} ${action} ${Date.now()}`,
            actor: "browser-test",
            reason: "Synthetic conflict snapshot",
          },
        });
        expect(created.status()).toBe(201);
        let gpo = (await created.json()).gpo;
        const revision = (
          await (
            await request.get(
              `/api/admx/policies/${encodeURIComponent(policyId)}`,
            )
          ).json()
        ).catalogue_revision;
        for (const side of ["computer", "user"]) {
          const configured = await request.post(
            `/api/admx/policies/${encodeURIComponent(policyId)}/configure`,
            {
              data: {
                gpo_guid: gpo.guid,
                side,
                state: "enabled",
                values: {},
                actor: "browser-test",
                reason: "Seed both sides",
                expected_revision: gpo.revision,
                expected_catalogue_revision: revision,
              },
            },
          );
          expect(configured.status()).toBe(200);
          gpo = (await configured.json()).gpo;
        }
        expect(gpo.revision).toBe(3);
        record.guid = gpo.guid;
        record.initialRevision = gpo.revision;
        await page.goto("/");
        await expect(page.locator("#app-status")).toBeHidden();
        const initialDetail = page.waitForResponse(
          (response) =>
            response.url().endsWith(`/api/gpos/${gpo.guid}`) &&
            response.request().method() === "GET",
        );
        await page.locator(`[data-guid="${gpo.guid}"]`).click();
        await (await initialDetail).finished();
        await flush(page);
        await expect(page.locator("#revision")).toHaveText("Revision 3");
        await page.getByRole("tab", { name: /^Settings browser/ }).click();
        await expect(page.locator(".browser-bulk-checkbox")).toHaveCount(2);
        await page.locator("#browser-bulk-select-all").check();
        await page
          .locator("#browser-bulk-reason")
          .fill("Disable both reviewed sides");
        await page.route(
          `**/api/gpos/${gpo.guid}/bulk-policy-state`,
          async (route) => {
            const body = route.request().postDataJSON();
            const index = record.bulk.length;
            const row = { body };
            record.bulk.push(row);
            if (index === 1) {
              secondArrived.resolve();
              await bulkGate.promise;
            }
            const response = await route.fetch();
            row.status = response.status();
            const result = await response.json();
            row.returnedRevision = result.gpo?.revision;
            await route.fulfill({ response });
            if (index === 1) secondDone.resolve();
          },
        );
        await page.locator('[data-bulk-state="disabled"]').click();
        await secondArrived.promise;
        record.afterFirstBulk = await snapshot(page, request, gpo.guid);
        expect(record.afterFirstBulk.viewRevision).toBe("Revision 3");
        expect(record.afterFirstBulk.backendRevision).toBe(4);
        expect(record.afterFirstBulk.summary).toContain("r4");
        await page.getByRole("tab", { name: "Overview", exact: true }).click();
        await page.locator("#edit-metadata").click();
        const metadata = page.locator("#gpo-dialog");
        await expect(
          metadata.getByLabel("Name", { exact: true }),
        ).toBeFocused();
        await metadata
          .getByLabel("Name", { exact: true })
          .fill(`Synthetic unsubmitted ${timing} ${action} ${gpo.guid} draft`);
        await metadata
          .getByLabel("Description", { exact: true })
          .fill("Synthetic retained description");
        await metadata
          .getByLabel("Change reason", { exact: true })
          .fill(`Synthetic reviewed ${timing} ${action} reason`);
        let detailCaptured = false;
        await page.route(`**/api/gpos/${gpo.guid}`, async (route) => {
          if (route.request().method() === "PATCH") {
            const response = await route.fetch();
            record.patch = {
              body: route.request().postDataJSON(),
              status: response.status(),
              error: (await response.json()).error,
            };
            record.patches.push(record.patch);
            await route.fulfill({ response });
            return;
          }
          if (route.request().method() !== "GET") return route.continue();
          const response = await route.fetch();
          const revision = (await response.json()).gpo.revision;
          record.reads.push(revision);
          if (detailCaptured) return route.fulfill({ response });
          detailCaptured = true;
          record.reviewedPayloadRevision = revision;
          detailArrived.resolve();
          if (timing === "held-get") await detailGate.promise;
          await route.fulfill({ response });
          detailDone.resolve();
        });
        if (timing === "held-diff") {
          let diffCaptured = false;
          await page.route(
            `**/api/gpos/${gpo.guid}/revisions/diff?*`,
            async (route) => {
              const response = await route.fetch();
              if (!diffCaptured) {
                diffCaptured = true;
                diffArrived.resolve();
                await detailGate.promise;
              }
              await route.fulfill({ response });
            },
          );
        }
        await metadata
          .getByRole("button", { name: "Save changes", exact: true })
          .click();
        await detailArrived.promise;
        if (timing === "held-diff") await diffArrived.promise;
        expect(record.patch.status).toBe(409);
        expect(record.patch.body.expected_revision).toBe(3);
        expect(record.patch.error.current_revision).toBe(4);
        expect(record.reviewedPayloadRevision).toBe(4);
        const conflict = page.locator("#conflict-dialog");
        if (timing === "shown-dialog") {
          await expect(conflict).toBeVisible();
          await expect(conflict.locator("#conflict-details")).toContainText(
            "Current workspace revision: 4",
          );
        }
        bulkGate.resolve();
        await secondDone.promise;
        await expect(page.locator("#revision")).toHaveText("Revision 5");
        await page.evaluate(() => {
          const revision = globalThis.document.querySelector("#revision");
          globalThis.conflictProbeRevisions = [revision.textContent];
          new globalThis.MutationObserver(() =>
            globalThis.conflictProbeRevisions.push(revision.textContent),
          ).observe(revision, {
            childList: true,
            subtree: true,
            characterData: true,
          });
        });
        record.afterSecondBulk = await snapshot(page, request, gpo.guid);
        expect(record.afterSecondBulk.backendRevision).toBe(5);
        expect(record.afterSecondBulk.summary).toContain("r5");
        expect(record.bulk.map((row) => row.body.side)).toEqual([
          "computer",
          "user",
        ]);
        expect(record.bulk.map((row) => row.body.expected_revision)).toEqual([
          3, 4,
        ]);
        expect(record.bulk.map((row) => row.status)).toEqual([200, 200]);
        expect(record.bulk.map((row) => row.returnedRevision)).toEqual([4, 5]);
        if (timing !== "shown-dialog") {
          detailGate.resolve();
          await detailDone.promise;
        }
        await expect(conflict).toBeVisible();
        await expect(conflict.locator("#conflict-details")).toContainText(
          `Current workspace revision: ${timing === "shown-dialog" ? 4 : 5}`,
        );
        expect(record.reads).toEqual(timing === "shown-dialog" ? [4] : [4, 5]);
        record.beforeConsent = await snapshot(page, request, gpo.guid);
        record.comparison = await conflict
          .locator("#conflict-details")
          .textContent();
        await page
          .locator(
            action === "reload" ? "#conflict-reload" : "#conflict-reapply",
          )
          .click();
        if (action === "reapply") {
          if (timing === "shown-dialog") {
            await expect(conflict).toBeVisible();
            await expect(conflict.locator("#conflict-details")).toContainText(
              "Current workspace revision: 5",
            );
            await expect(conflict).toContainText(
              "choose Review and reapply again",
            );
            expect(record.patch.body.expected_revision).toBe(3);
            expect(record.patches).toHaveLength(1);
            expect(
              await metadata.evaluate((dialog) => dialog.editorTarget.revision),
            ).toBe(3);
            await expect(
              metadata.getByLabel("Name", { exact: true }),
            ).toHaveValue(
              `Synthetic unsubmitted ${timing} ${action} ${gpo.guid} draft`,
            );
            await page.locator("#conflict-reapply").click();
          }
          await expect(conflict).toBeHidden();
          await expect(metadata).toBeVisible();
          await expect(
            metadata.getByLabel("Name", { exact: true }),
          ).toHaveValue(
            `Synthetic unsubmitted ${timing} ${action} ${gpo.guid} draft`,
          );
        } else {
          await expect(conflict).toBeHidden();
          await expect(metadata).toBeHidden();
        }
        await flush(page);
        record.afterConsent = await snapshot(page, request, gpo.guid);
        expect(record.afterConsent.backendRevision).toBe(5);
        expect(record.afterConsent.settings.map((item) => item.value)).toEqual([
          "0",
          "0",
        ]);
        expect(record.afterConsent.summary).toContain("r5");
        await expect(page.locator("#revision")).toHaveText("Revision 5");
        expect(
          await page.evaluate(() => globalThis.conflictProbeRevisions),
        ).not.toContain("Revision 4");
        if (action === "reapply") {
          await metadata
            .getByRole("button", { name: "Save changes", exact: true })
            .click();
          await expect(metadata).toBeHidden();
          await expect(page.locator("#revision")).toHaveText("Revision 6");
          expect(record.patch.body.expected_revision).toBe(5);
          expect(record.patches).toHaveLength(2);
          expect(record.patch.body.actor).toBe("local-operator");
          expect(record.patch.body.reason).toBe(
            `Synthetic reviewed ${timing} ${action} reason`,
          );
          expect(record.patch.body.description).toBe(
            "Synthetic retained description",
          );
          expect(record.patch.status).toBe(200);
          const final = (
            await (await request.get(`/api/gpos/${gpo.guid}`)).json()
          ).gpo;
          expect(final.name).toBe(
            `Synthetic unsubmitted ${timing} ${action} ${gpo.guid} draft`,
          );
          expect(final.settings.map((item) => item.value)).toEqual(["0", "0"]);
        }
      } finally {
        bulkGate.resolve();
        detailGate.resolve();
      }
    });
  }
}

async function openStaleMetadata(page, request, label) {
  const created = await request.post("/api/gpos", {
    data: { name: `Synthetic recovery ${label} ${Date.now()}` },
  });
  expect(created.status()).toBe(201);
  const gpo = (await created.json()).gpo;
  const opening = await (await request.get(`/api/gpos/${gpo.guid}`)).json();
  await page.goto("/");
  await page.locator(`[data-guid="${gpo.guid}"]`).click();
  await expect(page.locator("#revision")).toHaveText("Revision 1");
  await page.locator("#edit-metadata").click();
  const editor = page.locator("#gpo-dialog");
  await editor
    .getByLabel("Name", { exact: true })
    .fill(`Synthetic retained ${label}`);
  const changed = await request.patch(`/api/gpos/${gpo.guid}`, {
    data: {
      name: gpo.name,
      description: "Synthetic concurrent metadata",
      expected_revision: 1,
      actor: "other-operator",
      reason: "Synthetic recovery race",
    },
  });
  expect(changed.status()).toBe(200);
  return { gpo, opening, editor, conflict: page.locator("#conflict-dialog") };
}

test("failed fresh discard keeps the draft and comparison available for retry", async ({
  page,
  request,
}) => {
  const { gpo, editor, conflict } = await openStaleMetadata(
    page,
    request,
    "discard retry",
  );
  await editor
    .getByRole("button", { name: "Save changes", exact: true })
    .click();
  await expect(conflict.locator("#conflict-details")).toContainText(
    "Current workspace revision: 2",
  );
  let failed = true,
    reads = 0;
  await page.route(`**/api/gpos/${gpo.guid}`, async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    reads++;
    if (failed) {
      failed = false;
      return route.fulfill({
        status: 503,
        json: { error: { message: "Synthetic current-read failure" } },
      });
    }
    return route.continue();
  });
  await conflict.locator("#conflict-reload").click();
  await expect(conflict.locator(".form-error")).toContainText(
    "Synthetic current-read failure",
  );
  await expect(conflict.locator(".form-error")).toBeFocused();
  await expect(editor.getByLabel("Name", { exact: true })).toHaveValue(
    "Synthetic retained discard retry",
  );
  await expect(editor).toBeVisible();
  await expect(conflict.locator("#conflict-reload")).toBeEnabled();
  await conflict.locator("#conflict-reload").click();
  await expect(conflict).toBeHidden();
  await expect(editor).toBeHidden();
  await expect(page.locator("#revision")).toHaveText("Revision 2");
  expect(reads).toBe(2);
  expect(
    (await (await request.get(`/api/gpos/${gpo.guid}`)).json()).gpo.revision,
  ).toBe(2);
});

test("repeated obsolete reads stop after one retry and preserve the draft until explicit recovery", async ({
  page,
  request,
}) => {
  const { gpo, opening, editor, conflict } = await openStaleMetadata(
    page,
    request,
    "bounded reads",
  );
  let stale = true,
    reads = 0;
  await page.route(`**/api/gpos/${gpo.guid}`, async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    reads++;
    if (stale) return route.fulfill({ json: opening });
    return route.continue();
  });
  await editor
    .getByRole("button", { name: "Save changes", exact: true })
    .click();
  await expect(conflict.locator(".form-error")).toContainText(
    "The policy changed again while loading",
  );
  expect(reads).toBe(2);
  await expect(conflict.locator("#conflict-details")).not.toContainText(
    "Current workspace revision: 1",
  );
  await expect(editor.getByLabel("Name", { exact: true })).toHaveValue(
    "Synthetic retained bounded reads",
  );
  await expect(page.locator("#revision")).toHaveText("Revision 1");
  stale = false;
  await conflict.locator("#conflict-reapply").click();
  await expect(conflict.locator("#conflict-details")).toContainText(
    "Current workspace revision: 2",
  );
  await expect(conflict).toContainText("choose Review and reapply again");
  expect(reads).toBe(3);
  await expect(page.locator("#revision")).toHaveText("Revision 1");
  await conflict.locator("#conflict-reapply").click();
  await expect(conflict).toBeHidden();
  await expect(page.locator("#revision")).toHaveText("Revision 2");
  await editor
    .getByRole("button", { name: "Save changes", exact: true })
    .click();
  await expect(editor).toBeHidden();
  const saved = (await (await request.get(`/api/gpos/${gpo.guid}`)).json()).gpo;
  expect(saved.revision).toBe(3);
  expect(saved.name).toBe("Synthetic retained bounded reads");
});

for (const oldFailure of ["success", "http", "network"]) {
  test(`retired conflict ${oldFailure} cannot alter or unlock a reopened pending review`, async ({
    page,
    request,
  }) => {
    const { gpo, editor, conflict } = await openStaleMetadata(
      page,
      request,
      `retired ${oldFailure}`,
    );
    const gates = [deferred(), deferred()],
      arrived = [deferred(), deferred()],
      done = [deferred(), deferred()];
    let reads = 0;
    await page.route(`**/api/gpos/${gpo.guid}`, async (route) => {
      if (route.request().method() !== "GET") return route.continue();
      const index = reads++;
      if (index > 1) return route.continue();
      const response = await route.fetch();
      arrived[index].resolve();
      await gates[index].promise;
      if (index === 0 && oldFailure === "network") await route.abort("failed");
      else if (index === 0 && oldFailure === "http")
        await route.fulfill({
          status: 503,
          json: { error: { message: "Synthetic retired review failure" } },
        });
      else await route.fulfill({ response });
      done[index].resolve();
    });
    try {
      await editor
        .getByRole("button", { name: "Save changes", exact: true })
        .click();
      await arrived[0].promise;
      await expect(conflict.locator("#conflict-reload")).toBeDisabled();
      await expect(conflict.locator("#conflict-reapply")).toBeDisabled();
      await expect(
        conflict.getByRole("button", { name: "Keep editing", exact: true }),
      ).toBeEnabled();
      await expect(
        conflict.getByRole("button", { name: "Close", exact: true }),
      ).toBeEnabled();
      await conflict
        .getByRole("button", { name: "Keep editing", exact: true })
        .click();
      await expect(editor.getByLabel("Name", { exact: true })).toBeEnabled();
      await expect(
        editor.getByRole("button", { name: "Save changes", exact: true }),
      ).toBeEnabled();
      await editor
        .getByLabel("Name", { exact: true })
        .fill(`Synthetic editable while old read waits ${gpo.guid}`);
      await expect(editor.getByLabel("Name", { exact: true })).toHaveValue(
        `Synthetic editable while old read waits ${gpo.guid}`,
      );
      await editor.getByRole("button", { name: "Cancel", exact: true }).click();
      await page.locator("#edit-metadata").click();
      await editor
        .getByLabel("Name", { exact: true })
        .fill(`Synthetic newer conflict draft ${gpo.guid}`);
      await editor
        .getByRole("button", { name: "Save changes", exact: true })
        .click();
      await arrived[1].promise;
      await expect(conflict.locator("#conflict-details")).toHaveText(
        "Loading the current revision. Your unsaved values are retained.",
      );
      await expect(
        conflict.getByRole("button", { name: "Keep editing", exact: true }),
      ).toBeFocused();
      gates[0].resolve();
      await done[0].promise;
      await flush(page);
      await expect(conflict.locator("#conflict-reload")).toBeDisabled();
      await expect(conflict.locator("#conflict-reapply")).toBeDisabled();
      await expect(conflict.locator(".form-error")).toHaveCount(0);
      await expect(conflict.locator("#conflict-details")).toHaveText(
        "Loading the current revision. Your unsaved values are retained.",
      );
      await expect(
        conflict.getByRole("button", { name: "Keep editing", exact: true }),
      ).toBeFocused();
      await expect(editor.getByLabel("Name", { exact: true })).toHaveValue(
        `Synthetic newer conflict draft ${gpo.guid}`,
      );
      gates[1].resolve();
      await done[1].promise;
      await expect(conflict.locator("#conflict-details")).toContainText(
        "Current workspace revision: 2",
      );
      await expect(conflict.locator("#conflict-reload")).toBeEnabled();
      await conflict.locator("#conflict-reapply").click();
      await expect(conflict).toBeHidden();
      await expect(editor).toBeVisible();
      await editor
        .getByRole("button", { name: "Save changes", exact: true })
        .click();
      await expect(editor).toBeHidden();
      const saved = (await (await request.get(`/api/gpos/${gpo.guid}`)).json())
        .gpo;
      expect(saved.revision).toBe(3);
      expect(saved.name).toBe(`Synthetic newer conflict draft ${gpo.guid}`);
    } finally {
      gates.forEach((gate) => gate.resolve());
    }
  });
}

test("cancelled pending discard cannot close or overwrite a newer editor", async ({
  page,
  request,
}) => {
  const { gpo, editor, conflict } = await openStaleMetadata(
    page,
    request,
    "cancel discard",
  );
  await editor
    .getByRole("button", { name: "Save changes", exact: true })
    .click();
  await expect(conflict.locator("#conflict-details")).toContainText(
    "Current workspace revision: 2",
  );
  const arrived = deferred(),
    release = deferred(),
    done = deferred();
  let reads = 0;
  await page.route(`**/api/gpos/${gpo.guid}`, async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    reads++;
    const response = await route.fetch();
    arrived.resolve();
    await release.promise;
    await route.fulfill({ response });
    done.resolve();
  });
  try {
    await conflict.locator("#conflict-reload").click();
    await arrived.promise;
    await expect(conflict.locator("#conflict-reload")).toBeDisabled();
    await expect(conflict.locator("#conflict-reapply")).toBeDisabled();
    // Native repeated activation while disabled must not start a second read.
    await conflict.locator("#conflict-reload").evaluate((button) => {
      button.click();
      button.click();
    });
    await conflict.getByRole("button", { name: "Close", exact: true }).click();
    await editor.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.locator("#edit-metadata").click();
    await editor
      .getByLabel("Name", { exact: true })
      .fill("Synthetic newer editor after discard");
    await editor
      .getByLabel("Description", { exact: true })
      .fill("Keep these unsaved fields");
    release.resolve();
    await done.promise;
    await flush(page);
    expect(reads).toBe(1);
    await expect(conflict).toBeHidden();
    await expect(editor).toBeVisible();
    await expect(editor.getByLabel("Name", { exact: true })).toHaveValue(
      "Synthetic newer editor after discard",
    );
    await expect(editor.getByLabel("Description", { exact: true })).toHaveValue(
      "Keep these unsaved fields",
    );
    await expect(
      editor.getByLabel("Description", { exact: true }),
    ).toBeFocused();
    await expect(editor.locator(".form-error")).toHaveCount(0);
    await expect(
      editor.getByRole("button", { name: "Save changes", exact: true }),
    ).toBeEnabled();
    await expect(page.locator("#revision")).toHaveText("Revision 1");
    expect(
      (await (await request.get(`/api/gpos/${gpo.guid}`)).json()).gpo.revision,
    ).toBe(2);
  } finally {
    release.resolve();
  }
});
