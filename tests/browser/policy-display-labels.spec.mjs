import { expect, test } from "@playwright/test";
import { mkdtempSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

const namespace = "Synthetic.PolicyLabels";
const policies = [
  { id: "MissingName", attribute: "", name: "" },
  { id: "EmptyName", attribute: 'displayName=""', name: "" },
  { id: "WhitespaceName", attribute: 'displayName=" &#x20; "', name: "   " },
  {
    id: "NamedPolicy",
    attribute: 'displayName="Synthetic ordinary policy"',
    name: "Synthetic ordinary policy",
  },
];
function fixturePack(originalPath) {
  // These deliberately incomplete display attributes are currently accepted by
  // the parser. This test makes no ADMX standards-compliance claim.
  const directory = mkdtempSync(join(dirname(originalPath), "policy-labels-"));
  writeFileSync(
    join(directory, "labels.admx"),
    `<?xml version="1.0"?><policyDefinitions xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions"><policyNamespaces><target prefix="synthetic" namespace="${namespace}"/></policyNamespaces><policies>${policies.map((p) => `<policy name="${p.id}" class="Machine" key="Software\\Policies\\Synthetic\\PolicyLabels\\${p.id}" valueName="State" ${p.attribute}><enabledValue><decimal value="1"/></enabledValue><disabledValue><decimal value="0"/></disabledValue></policy>`).join("")}</policies></policyDefinitions>`,
  );
  writeFileSync(
    join(directory, "labels.adml"),
    '<?xml version="1.0"?><policyDefinitionResources xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions"><resources><stringTable/></resources></policyDefinitionResources>',
  );
  return directory;
}
for (const [theme, width] of [
  ["light", 360],
  ["dark", 360],
  ["light", 1280],
  ["dark", 1280],
]) {
  const smoke = theme === "light" && width === 360 ? " @smoke" : "";
  test(`accepted unnamed policies have visible keyboard labels in ${theme} at ${width}px${smoke}`, async ({
    page,
    request,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 800 });
    await page.emulateMedia({ colorScheme: theme });
    const original = (
      await (await request.get("/api/templates")).json()
    ).items.find((source) => source.name === "default");
    try {
      expect(
        (
          await request.post("/api/templates/ingest", {
            data: {
              name: "default",
              kind: "local",
              path: fixturePack(original.path),
            },
          })
        ).status(),
      ).toBe(201);
      const health = await (await request.get("/api/health")).json(),
        token = health.catalogue_revision;
      const created = await request.post("/api/gpos", {
        data: {
          name: `Synthetic policy labels ${theme} ${width} ${Date.now()}`,
          actor: "browser-test",
          reason: "Synthetic display-label fixture",
        },
      });
      expect(created.status()).toBe(201);
      const { gpo } = await created.json();
      let revision = 1;
      const parsed = [];
      for (const p of policies) {
        const id = `${namespace}:${p.id}`,
          detail = await (
            await request.get(`/api/admx/policies/${encodeURIComponent(id)}`)
          ).json();
        expect(detail.id).toBe(p.id);
        expect(detail.qualified_id).toBe(id);
        expect(detail.display_name).toBe(p.name);
        parsed.push(detail);
        const configured = await request.post(
          `/api/admx/policies/${encodeURIComponent(id)}/configure`,
          {
            data: {
              gpo_guid: gpo.guid,
              side: "computer",
              state: "enabled",
              values: {},
              expected_revision: revision,
              expected_catalogue_revision: token,
              actor: "browser-test",
              reason: "Seed synthetic configured policy",
            },
          },
        );
        expect(configured.status()).toBe(200);
        revision++;
      }
      const before = (await (await request.get(`/api/gpos/${gpo.guid}`)).json())
        .gpo;
      const history = (
        await (await request.get(`/api/gpos/${gpo.guid}/revisions`)).json()
      ).items;
      const writes = [];
      page.on("request", (r) => {
        if (!["GET", "HEAD"].includes(r.method()))
          writes.push({
            method: r.method(),
            url: r.url(),
            body: r.postDataJSON(),
          });
      });
      await page.goto("/");
      await expect(page.locator("#app-status")).toBeHidden();
      await page.locator(`[data-guid="${gpo.guid}"]`).click();
      await expect(page.locator("#title")).toHaveText(gpo.name);
      const loaded = page.waitForResponse((r) =>
        r.url().includes(`/api/gpos/${gpo.guid}/configured-settings`),
      );
      await page.locator("#tab-browser").click();
      const configured = await loaded;
      expect(configured.status()).toBe(200);
      const dto = await configured.json();
      expect(dto.resolved_count).toBe(4);
      expect(dto.gpo_revision).toBe(revision);
      expect(dto.catalogue_revision).toBe(token);
      for (const p of policies) {
        const id = `${namespace}:${p.id}`,
          label = p.name.trim() ? p.name : id;
        const checkbox = page.getByRole("checkbox", {
          name: `Select ${label}`,
          exact: true,
        });
        await expect(checkbox).toHaveAttribute("data-bulk-policy-id", id);
        const row = checkbox.locator("xpath=ancestor::tr"),
          toggle = row.getByRole("button", { name: label, exact: true });
        await expect(toggle).toBeVisible();
        const box = await toggle.boundingBox();
        expect(box.width).toBeGreaterThan(0);
        expect(box.height).toBeGreaterThan(0);
        await toggle.focus();
        await page.keyboard.press("Enter");
        await expect(toggle).toBeFocused();
        await expect(toggle).toHaveAttribute("aria-expanded", "true");
        await expect(
          page.locator(`#${await toggle.getAttribute("aria-controls")}`),
        ).toContainText(id);
        await page.keyboard.press("Space");
        await expect(toggle).toHaveAttribute("aria-expanded", "false");
      }
      await page.screenshot({
        path: testInfo.outputPath("configured-labels.png"),
      });
      await page.locator("#tab-admx").click();
      await expect(page.locator(".admx-result")).toHaveCount(4);
      const requests = [];
      page.on("request", (r) => {
        if (r.url().includes("/api/admx/policies/")) requests.push(r.url());
      });
      for (const p of policies) {
        const id = `${namespace}:${p.id}`,
          label = p.name.trim() ? p.name : id;
        const result = page
          .locator(".admx-result")
          .filter({ has: page.locator("strong", { hasText: label }) });
        await expect(result.locator("strong")).toHaveText(label);
        await expect(result).toHaveAttribute("data-id", id);
        await result.focus();
        await page.keyboard.press("Enter");
        await expect(page.locator("#admx-detail h3")).toHaveText(label);
        expect(requests.at(-1)).toContain(
          `/api/admx/policies/${encodeURIComponent(id)}`,
        );
        await page.locator("#admx-configure-btn").focus();
        await page.keyboard.press("Enter");
        await expect(page.locator("#configure-title")).toHaveText(
          `Configure: ${label}`,
        );
        await expect(page.locator("#configure-form")).toHaveAttribute(
          "data-policy-id",
          id,
        );
        await expect(page.locator("#configure-form")).toHaveAttribute(
          "data-catalogue-revision",
          token,
        );
        await expect(page.locator("#configure-target-gpo")).toHaveValue(
          gpo.guid,
        );
        await page.keyboard.press("Escape");
        await expect(page.locator("#configure-dialog")).toBeHidden();
        await page.locator("#admx-back").click();
      }
      expect(writes).toEqual([]);
      expect(
        (await (await request.get(`/api/gpos/${gpo.guid}`)).json()).gpo,
      ).toEqual(before);
      expect(
        (await (await request.get(`/api/gpos/${gpo.guid}/revisions`)).json())
          .items,
      ).toEqual(history);
      await page.screenshot({
        path: testInfo.outputPath("catalogue-labels.png"),
      });
      // One explicit action proves the fallback never becomes request identity.
      const id = `${namespace}:MissingName`;
      await page.locator(`.admx-result[data-id="${id}"]`).click();
      await page.locator("#admx-configure-btn").click();
      await page.locator("#configure-state").selectOption("disabled");
      const reason = "Synthetic explicit original-ID apply";
      await page.locator('#configure-form [name="reason"]').fill(reason);
      const applied = page.waitForResponse(
        (r) =>
          r.request().method() === "POST" && r.url().endsWith("/configure"),
      );
      await page.locator('#configure-form button[value="default"]').click();
      const response = await applied;
      expect(response.status()).toBe(200);
      expect(response.url()).toContain(
        `/api/admx/policies/${encodeURIComponent(id)}/configure`,
      );
      expect(response.request().postDataJSON()).toEqual({
        gpo_guid: gpo.guid,
        side: "computer",
        values: {},
        state: "disabled",
        actor: "local-operator",
        reason,
        expected_revision: revision,
        expected_catalogue_revision: token,
      });
      expect(writes).toHaveLength(1);
      const persisted = (
        await (await request.get(`/api/gpos/${gpo.guid}`)).json()
      ).gpo;
      expect(persisted.revision).toBe(revision + 1);
      expect(persisted.name).toBe(before.name);
      expect(persisted.settings.map((row) => row.id)).toEqual(
        before.settings.map((row) => row.id),
      );
      expect(persisted.settings).toHaveLength(4);
      expect(
        persisted.settings.find((row) => row.key.endsWith("\\MissingName"))
          .value,
      ).toBe("0");
      await testInfo.attach("accepted-source-labels-and-original-identities", {
        body: JSON.stringify({ parsed, dto, writes, persisted }, null, 2),
        contentType: "application/json",
      });
    } finally {
      expect(
        (
          await request.post("/api/templates/ingest", {
            data: { name: "default", kind: "local", path: original.path },
          })
        ).status(),
      ).toBe(201);
    }
  });
}
