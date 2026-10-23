import { expect, test } from "@playwright/test";
import { fileURLToPath } from "node:url";

const pack = fileURLToPath(
  new URL("../fixtures/admx-list-observations", import.meta.url),
);

for (const [theme, width] of [
  ["light", 1280],
  ["dark", 360],
]) {
  const smoke = width === 1280 ? " @smoke" : "";
  test(`observes complete list states and preserves explicit bulk choices in ${theme} at ${width}px${smoke}`, async ({
    page,
    request,
  }, testInfo) => {
    const original = (
      await (await request.get("/api/templates")).json()
    ).items.find((source) => source.name === "default");
    try {
      const activated = await request.post("/api/templates/ingest", {
        data: { name: "default", kind: "local", path: pack },
      });
      expect(activated.status()).toBe(201);
      const token = (await (await request.get("/api/health")).json())
        .catalogue_revision;
      let { gpo } = await (
        await request.post("/api/gpos", {
          data: {
            name: `Synthetic List Observation ${testInfo.project.name} ${width} ${Date.now()}`,
            actor: "browser-test",
            reason: "Synthetic observation fixture",
          },
        })
      ).json();
      for (const [id, side] of [
        ["SetOnly", "computer"],
        ["Mixed", "user"],
        ["Identical", "computer"],
        ["Partial", "computer"],
      ]) {
        const configured = await request.post(
          `/api/admx/policies/Synthetic.Observations:${id}/configure`,
          {
            data: {
              gpo_guid: gpo.guid,
              side,
              state: "disabled",
              values: {},
              expected_revision: gpo.revision,
              expected_catalogue_revision: token,
              actor: "browser-test",
              reason: "Synthetic explicit disabled state",
            },
          },
        );
        expect(configured.status()).toBe(200);
        ({ gpo } = await configured.json());
      }
      const partial = gpo.settings.find(
        (row) => row.key.endsWith("\\Partial") && row.value_name === "One",
      );
      const updated = await request.put(
        `/api/gpos/${gpo.guid}/settings/${encodeURIComponent(partial.id)}`,
        {
          data: {
            setting: { ...partial, value: "1" },
            expected_revision: gpo.revision,
            actor: "browser-test",
            reason: "Synthetic native mixed pattern",
          },
        },
      );
      expect(updated.status()).toBe(200);
      ({ gpo } = await updated.json());
      const beforeHistory = await (
        await request.get(`/api/gpos/${gpo.guid}/revisions`)
      ).json();
      const writes = [];
      page.on("request", (req) => {
        if (!["GET", "HEAD"].includes(req.method())) writes.push(req);
      });
      await page.setViewportSize({ width, height: width === 360 ? 640 : 800 });
      await page.emulateMedia({ colorScheme: theme });
      await page.goto("/");
      await page.locator(`[data-guid="${gpo.guid}"]`).click();
      await expect(page.locator("#title")).toHaveText(gpo.name);
      await page.locator("#tab-browser").click();
      const row = (name) =>
        page
          .locator("#browser-resolved-table > tr")
          .filter({ has: page.getByRole("button", { name, exact: true }) });
      await expect(row("Synthetic set-only state")).toContainText("disabled");
      await expect(row("Synthetic mixed actions")).toContainText("disabled");
      await expect(row("Synthetic identical states")).toContainText(
        "Indeterminate",
      );
      await expect(row("Synthetic incomplete state")).toContainText(
        "Indeterminate",
      );
      const filter = page.locator("#browser-state-filter");
      await filter.selectOption("disabled");
      await expect(page.locator(".browser-policy-toggle")).toHaveCount(2);
      await expect(row("Synthetic identical states")).toHaveCount(0);
      await filter.selectOption("indeterminate");
      await expect(page.locator(".browser-policy-toggle")).toHaveCount(2);
      const identical = page.getByRole("button", {
        name: "Synthetic identical states",
        exact: true,
      });
      await identical.focus();
      await page.keyboard.press("Enter");
      await expect(page.locator(".browser-detail-row:visible")).toContainText(
        "Enabled and Disabled write the same observed policy-level pattern.",
      );
      await page.keyboard.press("Enter");
      const incomplete = page.getByRole("button", {
        name: "Synthetic incomplete state",
        exact: true,
      });
      await incomplete.focus();
      await page.keyboard.press("Enter");
      await expect(incomplete).toBeFocused();
      await expect(page.locator(".browser-detail-row:visible")).toContainText(
        "The registry records do not match a complete Enabled or Disabled policy-level pattern.",
      );
      await row("Synthetic identical states").getByRole("checkbox").check();
      await expect(page.locator("#browser-bulk-count")).toHaveText(
        "1 selected",
      );
      await filter.selectOption("enabled");
      await expect(page.locator(".browser-policy-toggle")).toHaveCount(0);
      await expect(page.locator("#browser-resolved-empty")).toBeVisible();
      await expect(page.locator("#browser-bulk-count")).toHaveText(
        "1 selected",
      );
      await filter.selectOption("indeterminate");
      await expect(
        row("Synthetic identical states").getByRole("checkbox"),
      ).toBeChecked();
      expect(writes).toEqual([]);
      expect(
        (await (await request.get(`/api/gpos/${gpo.guid}`)).json()).gpo,
      ).toEqual(gpo);
      expect(
        await (await request.get(`/api/gpos/${gpo.guid}/revisions`)).json(),
      ).toEqual(beforeHistory);
      await page
        .locator("#browser-bulk-reason")
        .fill("Synthetic explicit reviewed disabled target");
      const response = page.waitForResponse(
        (r) =>
          r.url().endsWith(`/api/gpos/${gpo.guid}/bulk-policy-state`) &&
          r.request().method() === "POST",
      );
      await page.locator('[data-bulk-state="disabled"]').click();
      const result = await response;
      expect(result.status()).toBe(200);
      expect(result.request().postDataJSON()).toMatchObject({
        target_state: "disabled",
        expected_revision: gpo.revision,
        expected_catalogue_revision: token,
        policy_ids: ["Synthetic.Observations:Identical"],
      });
      await expect(page.locator("#revision")).toHaveText(
        `Revision ${gpo.revision + 1}`,
      );
      await expect(row("Synthetic identical states")).toContainText(
        "Indeterminate",
      );
      expect(writes).toHaveLength(1);
      const persisted = (
        await (await request.get(`/api/gpos/${gpo.guid}`)).json()
      ).gpo;
      expect(persisted.revision).toBe(gpo.revision + 1);
      expect(persisted.settings).toEqual(gpo.settings);
    } finally {
      const restored = await request.post("/api/templates/ingest", {
        data: { name: "default", kind: "local", path: original.path },
      });
      expect(restored.status()).toBe(201);
    }
  });
}
