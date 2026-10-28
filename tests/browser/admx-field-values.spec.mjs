import { expect, test } from "@playwright/test";
import { mkdtempSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

const ids = [
  "__proto__",
  "constructor",
  "hasOwnProperty",
  "toString",
  "Normal",
];
const kinds = ["boolean", "decimal", "text", "multitext", "list", "enum"];

function fixturePack(originalPath) {
  // The managed server cleans this synthetic sibling of its default catalogue.
  const directory = mkdtempSync(
    join(dirname(originalPath), "source-field-values-"),
  );
  const policies = kinds
    .map((kind) => {
      const key = `Software\\Policies\\Synthetic\\SourceFields\\${kind}`;
      const elements = ids
        .map((id, index) => {
          const tag = kind === "multitext" ? "multiText" : kind;
          const attributes =
            kind === "list"
              ? `key="${key}\\Field${index}" valuePrefix="Entry"`
              : `valueName="Field${index}"`;
          const options =
            kind === "enum"
              ? '<item displayName="Synthetic first"><decimal value="1"/></item><item displayName="Synthetic second"><decimal value="2"/></item>'
              : "";
          return `<${tag} id="${id}" ${attributes}>${options}</${tag}>`;
        })
        .join("");
      return `<policy name="${kind}" class="Machine" key="${key}" valueName="State" displayName="Synthetic source ${kind}"><enabledValue><decimal value="1"/></enabledValue><disabledValue><decimal value="0"/></disabledValue><elements>${elements}</elements></policy>`;
    })
    .join("");
  writeFileSync(
    join(directory, "source.admx"),
    `<?xml version="1.0"?><policyDefinitions xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions" revision="1.0" schemaVersion="1.0"><policyNamespaces><target prefix="synthetic" namespace="Synthetic.SourceFields"/></policyNamespaces><policies>${policies}</policies></policyDefinitions>`,
  );
  writeFileSync(
    join(directory, "source.adml"),
    '<?xml version="1.0"?><policyDefinitionResources xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions" revision="1.0" schemaVersion="1.0"><resources><stringTable/></resources></policyDefinitionResources>',
  );
  return directory;
}

function valueFor(kind, index) {
  if (kind === "boolean") return index % 2 === 0;
  if (kind === "decimal") return [0, 12, 23, 34, 45][index];
  if (kind === "text") return `  Synthetic field ${index}  `;
  if (kind === "enum") return "2";
  return [`first${index}`, `second${index}`];
}

for (const kind of kinds) {
  const smoke = kind === "list" ? " @smoke" : "";
  test(`preserves source element property names for ${kind}${smoke}`, async ({
    page,
    request,
  }, testInfo) => {
    const original = (
      await (await request.get("/api/templates")).json()
    ).items.find((source) => source.name === "default");
    try {
      const directory = fixturePack(original.path);
      expect(
        (
          await request.post("/api/templates/ingest", {
            data: { name: "default", kind: "local", path: directory },
          })
        ).status(),
      ).toBe(201);
      const token = (await (await request.get("/api/health")).json())
        .catalogue_revision;
      const detail = await (
        await request.get(`/api/admx/policies/Synthetic.SourceFields:${kind}`)
      ).json();
      expect(detail.elements.map((element) => element.id)).toEqual(ids);
      expect(detail.elements.map((element) => element.kind)).toEqual(
        ids.map(() => kind),
      );
      const { gpo } = await (
        await request.post("/api/gpos", {
          data: {
            name: `Synthetic Source Fields ${kind} ${testInfo.project.name} ${Date.now()}`,
            actor: "browser-test",
            reason: "Synthetic source-ID fixture",
          },
        })
      ).json();
      await page.goto("/");
      await page.locator(`[data-guid="${gpo.guid}"]`).click();
      await page.getByRole("tab", { name: "ADMX", exact: true }).click();
      await page
        .locator(".admx-result", { hasText: `Synthetic source ${kind}` })
        .click();
      await page.locator("#admx-configure-btn").click();
      const dialog = page.locator("#configure-dialog");
      const expected = Object.fromEntries(
        ids.map((id, index) => [id, valueFor(kind, index)]),
      );
      for (const [index, id] of ids.entries()) {
        const control = dialog.locator(`[data-elem-id="${id}"]`);
        await expect(control).toHaveAttribute("data-kind", kind);
        const value = valueFor(kind, index);
        if (kind === "boolean") await control.setChecked(value);
        else if (kind === "enum") await control.selectOption(value);
        else
          await control.fill(
            Array.isArray(value) ? value.join("\n") : String(value),
          );
      }
      const reason = `Synthetic exact ${kind} field submission`;
      await dialog.locator('[name="reason"]').fill(reason);
      const response = page.waitForResponse(
        (result) =>
          result.url().includes(`/api/admx/policies/`) &&
          result.url().endsWith("/configure") &&
          result.request().method() === "POST",
      );
      await dialog
        .getByRole("button", { name: "Apply policy", exact: true })
        .click();
      const result = await response;
      const body = result.request().postDataJSON();
      const persisted = (
        await (await request.get(`/api/gpos/${gpo.guid}`)).json()
      ).gpo;
      const evidencePath = testInfo.outputPath("submitted-and-persisted.json");
      writeFileSync(
        evidencePath,
        JSON.stringify(
          {
            kind,
            body,
            status: result.status(),
            response: await result.json(),
            persisted,
          },
          null,
          2,
        ),
      );
      await testInfo.attach("submitted-and-persisted", {
        path: evidencePath,
        contentType: "application/json",
      });
      expect(Object.hasOwn(body.values, "__proto__")).toBe(true);
      expect(Object.keys(body.values).sort()).toEqual([...ids].sort());
      expect(body.values).toEqual(expected);
      expect(body).toMatchObject({
        gpo_guid: gpo.guid,
        side: "computer",
        state: "enabled",
        actor: "local-operator",
        reason,
        expected_revision: 1,
        expected_catalogue_revision: token,
      });
      expect(result.status()).toBe(200);
      await expect(dialog).toBeHidden();
      await expect(page.locator("#revision")).toHaveText("Revision 2");
      expect(persisted.revision).toBe(2);
      expect(persisted.settings).toHaveLength(kind === "list" ? 11 : 6);
      expect(
        persisted.settings
          .filter((row) => row.value_name === "State")
          .map((row) => row.value),
      ).toEqual(["1"]);
      for (const index of ids.keys()) {
        const value = valueFor(kind, index);
        if (kind === "list") {
          const rows = persisted.settings.filter((row) =>
            row.key.endsWith(`\\Field${index}`),
          );
          expect(
            rows.map((row) => [row.value_name, row.registry_type, row.value]),
          ).toEqual([
            ["Entry1", "REG_SZ", value[0]],
            ["Entry2", "REG_SZ", value[1]],
          ]);
        } else {
          const row = persisted.settings.find(
            (row) => row.value_name === `Field${index}`,
          );
          const type =
            kind === "boolean" || kind === "decimal" || kind === "enum"
              ? "REG_DWORD"
              : kind === "multitext"
                ? "REG_MULTI_SZ"
                : "REG_SZ";
          const storedValue =
            kind === "boolean"
              ? String(Number(value))
              : kind === "decimal"
                ? String(value)
                : kind === "enum"
                  ? "2"
                  : value;
          expect(row).toMatchObject({
            side: "computer",
            hive: "HKLM",
            action: "set",
            registry_type: type,
            value: storedValue,
          });
        }
      }
      expect(
        (await (await request.get(`/api/gpos/${gpo.guid}/revisions`)).json())
          .items,
      ).toHaveLength(2);
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
