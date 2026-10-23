import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";

async function seedLegacyDraft(request, identifier) {
  const guid = randomUUID();
  const name = `Synthetic legacy URL ${guid}`;
  const target = {
    id: identifier,
    side: "computer",
    hive: "HKLM",
    key: "Software\\Policies\\Synthetic\\SettingUrls",
    value_name: "Target",
    registry_type: "REG_SZ",
    value: "target-original",
    action: "set",
    comment: "",
  };
  const neighbor = {
    ...target,
    id: "admx-Synthetic.Url:Legacy-computer-Option",
    value_name: "Neighbor",
    value: "neighbor-original",
  };
  const imported = await request.post("/api/estate/import", {
    data: {
      kind: "gpo-lens-estate",
      gpos: [{ guid, display_name: name, settings: [target, neighbor] }],
    },
  });
  expect(imported.status()).toBe(200);
  expect((await imported.json()).imported).toBe(1);
  const created = await request.post("/api/gpos", {
    data: {
      name: `${name} draft`,
      actor: "browser-test",
      reason: "Create legacy editing draft",
    },
  });
  expect(created.status()).toBe(201);
  const draft = (await created.json()).gpo;
  const copied = await request.post(`/api/gpos/${draft.guid}/copy-settings`, {
    data: {
      source_guid: guid,
      expected_revision: draft.revision,
      actor: "browser-test",
      reason: "Copy accepted legacy workspace identities",
    },
  });
  expect(copied.status()).toBe(200);
  const { gpo } = await copied.json();
  return {
    gpo,
    target: gpo.settings.find((row) => row.value_name === "Target"),
    neighbor: gpo.settings.find((row) => row.value_name === "Neighbor"),
  };
}

async function selectSettings(page, gpo) {
  await page.goto("/");
  await page.locator(`[data-guid="${gpo.guid}"]`).click();
  await page.getByRole("tab", { name: /^Policy settings/ }).click();
  return page.locator("#settings-table tr").filter({
    has: page.getByRole("cell", { name: "Target", exact: true }),
  });
}

async function snapshot(request, guid) {
  const response = await request.get(`/api/gpos/${guid}`);
  expect(response.status()).toBe(200);
  return (await response.json()).gpo;
}

const identifiers = [
  ["empty", ""],
  ["fragment", "admx-Synthetic.Url:Legacy-computer-Option#tail"],
  ["query", "admx-Synthetic.Url:Legacy-computer-Option?tail=value"],
  ["percent", "admx-Synthetic.Url:Legacy-computer-Option%tail"],
  [
    "literal percent escape",
    "admx-Synthetic.Url:Legacy-computer-Option%2Ftail",
  ],
  ["slash and endpoint suffix", "legacy/branch/comment"],
  ["repeated endpoint suffix", "legacy/comment/comment"],
  ["leading and trailing slash", "/legacy/"],
  ["backslash", "admx-Synthetic.Url:Legacy-computer-Option\\tail"],
  ["Unicode", "admx-Synthetic.Url:Legacy-computer-Option λ雪"],
  ["ordinary", "legacy-ordinary-id"],
  ["structured", "admx2:61:computer:element:62:0"],
];

for (const [label, identifier] of identifiers) {
  test(`comment, edit and delete preserve the exact ${label} setting ID`, async ({
    page,
    request,
  }) => {
    const { gpo, target, neighbor } = await seedLegacyDraft(
      request,
      identifier,
    );
    const row = await selectSettings(page, gpo);
    const path = `/api/gpos/${gpo.guid}/settings/${encodeURIComponent(target.id)}`;
    expect(await snapshot(request, gpo.guid)).toEqual(gpo);

    await row.getByRole("button", { name: "Comment", exact: true }).click();
    const commentForm = page.locator("#comment-form");
    await commentForm.locator('[name="comment"]').fill("comment-target");
    await commentForm
      .locator('[name="reason"]')
      .fill("Comment exact legacy row");
    const commented = page.waitForResponse(
      (response) => response.request().method() === "PATCH",
    );
    await commentForm.getByRole("button", { name: "Save comment" }).click();
    const commentResponse = await commented;
    expect(commentResponse.status()).toBe(200);
    expect(new URL(commentResponse.url()).pathname).toBe(`${path}/comment`);
    expect(commentResponse.request().postDataJSON()).toEqual({
      actor: "local-operator",
      reason: "Comment exact legacy row",
      expected_revision: gpo.revision,
      comment: "comment-target",
    });
    await expect(page.locator("#comment-dialog")).toBeHidden();
    let saved = await snapshot(request, gpo.guid);
    expect(saved.revision).toBe(gpo.revision + 1);
    expect(saved.settings.find((item) => item.id === target.id)).toEqual({
      ...target,
      comment: "comment-target",
    });
    expect(saved.settings.find((item) => item.id === neighbor.id)).toEqual(
      neighbor,
    );

    await row.getByRole("button", { name: "Edit", exact: true }).click();
    const settingForm = page.locator("#setting-form");
    await settingForm.locator('[name="value"]').fill("edit-target");
    await settingForm.locator('[name="reason"]').fill("Edit exact legacy row");
    const edited = page.waitForResponse(
      (response) => response.request().method() === "PUT",
    );
    await settingForm.getByRole("button", { name: "Save setting" }).click();
    const editResponse = await edited;
    expect(editResponse.status()).toBe(200);
    expect(new URL(editResponse.url()).pathname).toBe(path);
    expect(editResponse.request().postDataJSON()).toMatchObject({
      actor: "local-operator",
      reason: "Edit exact legacy row",
      expected_revision: gpo.revision + 1,
    });
    await expect(page.locator("#setting-dialog")).toBeHidden();
    saved = await snapshot(request, gpo.guid);
    expect(saved.revision).toBe(gpo.revision + 2);
    expect(saved.settings.find((item) => item.id === target.id)).toEqual({
      ...target,
      value: "edit-target",
      comment: "comment-target",
    });
    expect(saved.settings.find((item) => item.id === neighbor.id)).toEqual(
      neighbor,
    );

    page.once("dialog", (dialog) => dialog.accept());
    const deleted = page.waitForResponse(
      (response) => response.request().method() === "DELETE",
    );
    await row.locator("[data-delete-setting]").click();
    const deleteResponse = await deleted;
    expect(deleteResponse.status()).toBe(200);
    expect(new URL(deleteResponse.url()).pathname).toBe(path);
    expect(deleteResponse.request().postDataJSON()).toEqual({
      actor: "local-operator",
      reason: "Remove registry policy",
      expected_revision: gpo.revision + 2,
    });
    await expect(row).toHaveCount(0);
    saved = await snapshot(request, gpo.guid);
    expect(saved.revision).toBe(gpo.revision + 3);
    expect(saved.settings).toEqual([neighbor]);
    const history = await (
      await request.get(`/api/gpos/${gpo.guid}/revisions`)
    ).json();
    expect(history.items.map((item) => item.revision)).toEqual([5, 4, 3, 2, 1]);
    expect(history.items.map((item) => [item.actor, item.reason])).toEqual([
      ["local-operator", "Remove registry policy"],
      ["local-operator", "Edit exact legacy row"],
      ["local-operator", "Comment exact legacy row"],
      ["browser-test", "Copy accepted legacy workspace identities"],
      ["browser-test", "Create legacy editing draft"],
    ]);
    const original = await (
      await request.get(`/api/gpos/${gpo.guid}/revisions/${gpo.revision}`)
    ).json();
    expect(original.snapshot.settings).toEqual(gpo.settings);
    expect(await snapshot(request, gpo.guid)).toEqual(saved);
  });
}

test("encoded legacy setting edits retain stale CAS refusal and unsaved fields", async ({
  page,
  request,
}) => {
  const { gpo, target, neighbor } = await seedLegacyDraft(
    request,
    "admx-Synthetic.Url:Legacy-computer-Option#stale",
  );
  const row = await selectSettings(page, gpo);
  await row.getByRole("button", { name: "Edit", exact: true }).click();
  await page.locator('#setting-form [name="value"]').fill("unsaved-target");
  const concurrent = await request.patch(`/api/gpos/${gpo.guid}`, {
    data: {
      expected_revision: gpo.revision,
      name: gpo.name,
      description: "Concurrent synthetic update",
      actor: "other-browser",
      reason: "Advance CAS before the encoded setting edit",
    },
  });
  expect(concurrent.status()).toBe(200);
  const before = await snapshot(request, gpo.guid);
  const refused = page.waitForResponse(
    (response) => response.request().method() === "PUT",
  );
  await page
    .locator("#setting-form")
    .getByRole("button", { name: "Save setting" })
    .click();
  const response = await refused;
  expect(response.status()).toBe(409);
  expect(new URL(response.url()).pathname).toBe(
    `/api/gpos/${gpo.guid}/settings/${encodeURIComponent(target.id)}`,
  );
  expect(response.request().postDataJSON().expected_revision).toBe(
    gpo.revision,
  );
  await expect(page.locator('#setting-form [name="value"]')).toHaveValue(
    "unsaved-target",
  );
  expect(await snapshot(request, gpo.guid)).toEqual(before);
  expect(before.settings.find((item) => item.id === target.id)).toEqual(target);
  expect(before.settings.find((item) => item.id === neighbor.id)).toEqual(
    neighbor,
  );
  const history = await (
    await request.get(`/api/gpos/${gpo.guid}/revisions`)
  ).json();
  expect(history.items.map((item) => item.revision)).toEqual([3, 2, 1]);
});

for (const identifier of [".", "..", "synthetic-uri-error"]) {
  test(`${identifier} URL failures refuse visibly before any mutation request`, async ({
    page,
    request,
  }) => {
    const { gpo, target, neighbor } = await seedLegacyDraft(
      request,
      identifier,
    );
    const row = await selectSettings(page, gpo);
    if (identifier === "synthetic-uri-error") {
      await page.evaluate(() => {
        const encode = globalThis.encodeURIComponent;
        globalThis.encodeURIComponent = (value) => {
          if (value === "synthetic-uri-error")
            throw new URIError("Synthetic URI failure");
          return encode(value);
        };
      });
    }
    const sent = [],
      errors = [];
    page.on("request", (request) => {
      if (!["GET", "HEAD"].includes(request.method())) sent.push(request.url());
    });
    page.on("pageerror", (error) => errors.push(error.message));
    for (const [button, formId, field, submit] of [
      ["Edit", "setting", "value", "Save setting"],
      ["Comment", "comment", "comment", "Save comment"],
    ]) {
      await row.getByRole("button", { name: button, exact: true }).click();
      const form = page.locator(`#${formId}-form`);
      await form.locator(`[name="${field}"]`).fill("unsaved dot-ID data");
      await form.getByRole("button", { name: submit, exact: true }).click();
      await expect(form.locator(".form-error")).toContainText(
        "recover it into a fresh draft with a new ID",
      );
      await expect(form.locator(`[name="${field}"]`)).toHaveValue(
        "unsaved dot-ID data",
      );
      await form.getByRole("button", { name: "Cancel", exact: true }).click();
      await expect(page.locator(`#${formId}-dialog`)).toBeHidden();
    }
    await row.locator("[data-delete-setting]").click();
    await expect(page.locator("#error-summary")).toContainText(
      "recover it into a fresh draft with a new ID",
    );
    expect(sent).toEqual([]);
    expect(errors).toEqual([]);
    expect(await snapshot(request, gpo.guid)).toEqual(gpo);
    expect(gpo.settings).toHaveLength(2);
    expect(gpo.settings).toContainEqual(target);
    expect(gpo.settings).toContainEqual(neighbor);
    const history = await (
      await request.get(`/api/gpos/${gpo.guid}/revisions`)
    ).json();
    expect(history.items.map((item) => item.revision)).toEqual([2, 1]);
  });
}
