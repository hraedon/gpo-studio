import { test, expect } from "@playwright/test";
import {
  mkdirSync,
  mkdtempSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

let directory, original, exactFolder;

function pack(path, variant) {
  mkdirSync(path);
  writeFileSync(
    join(path, "synthetic.admx"),
    `<policyDefinitions>
<policyNamespaces><target prefix="synthetic" namespace="Synthetic.ExactFolder"/></policyNamespaces>
<policies><policy name="ExactFolder" class="Machine" displayName="$(string.Name)"
 key="Software\\Policies\\Synthetic\\ExactFolder${variant}" valueName="State">
<enabledValue><decimal value="1"/></enabledValue>
</policy></policies></policyDefinitions>`,
  );
  writeFileSync(
    join(path, "synthetic.adml"),
    `<policyDefinitionResources><resources>
<stringTable><string id="Name">Synthetic exact folder ${variant}</string></stringTable>
</resources></policyDefinitionResources>`,
  );
}

async function open(page) {
  await page.goto("/");
  await page.locator("#open-templates").click();
  const dialog = page.locator("#templates-dialog");
  await expect(dialog.getByLabel("Folder path")).toBeEnabled();
  return dialog;
}

test.beforeAll(async ({ request }) => {
  original = (await (await request.get("/api/templates")).json()).items.find(
    (source) => source.name === "default",
  );
  expect(original).toBeTruthy();
  // Match the API's canonical path when the temporary root is an alias.
  directory = realpathSync(
    mkdtempSync(join(tmpdir(), "gpo-studio-exact-folder-")),
  );
  const sibling = join(directory, "PolicyDefinitions");
  // Windows retains non-breaking spaces in names, unlike trailing ASCII spaces.
  // https://learn.microsoft.com/en-us/troubleshoot/windows-client/shell-experience/file-folder-name-whitespace-characters
  exactFolder = `${sibling}\u00a0`;
  pack(sibling, "TrimmedSibling");
  pack(exactFolder, "ExactTarget");
});

test.afterEach(async ({ request }) => {
  const restored = await request.post("/api/templates/ingest", {
    data: { name: "default", kind: "local", path: original.path },
  });
  expect(restored.status()).toBe(201);
});

test.afterAll(() => rmSync(directory, { recursive: true, force: true }));

test("loads the exact folder entered instead of a whitespace-trimmed sibling @smoke", async ({
  page,
  request,
}) => {
  const dialog = await open(page);
  await dialog.getByLabel("Folder path").fill(exactFolder);
  const response = page.waitForResponse(
    (result) =>
      result.request().method() === "POST" &&
      result.url().endsWith("/api/templates/ingest"),
  );
  await dialog.getByRole("button", { name: "Load folder" }).click();
  const loaded = await response;
  expect(loaded.status()).toBe(201);
  const data = await loaded.json();
  const active = (
    await (await request.get("/api/templates")).json()
  ).items.find((source) => source.name === "default");
  const policies = (
    await (await request.get("/api/admx/search?q=ExactFolder")).json()
  ).items;
  console.log(
    JSON.stringify({
      entered: exactFolder,
      trailingCodePoint: exactFolder.codePointAt(exactFolder.length - 1),
      submitted: loaded.request().postDataJSON().path,
      activated: active.path,
      observedKeys: policies.map((policy) => policy.key),
    }),
  );
  expect(active.path).toBe(exactFolder);
  expect(data.path).toBe(exactFolder);
  expect(loaded.request().postDataJSON().path).toBe(exactFolder);
  expect(policies).toHaveLength(1);
  expect(policies[0].key).toBe(
    "Software\\Policies\\Synthetic\\ExactFolderExactTarget",
  );
  await expect(dialog.getByLabel("Folder path")).toBeEnabled();
  await expect(dialog.getByLabel("Folder path")).toHaveValue(exactFolder);
});

test("rejects whitespace-only paths locally and preserves the active catalogue", async ({
  page,
  request,
}) => {
  const before = await (await request.get("/api/templates")).json();
  const dialog = await open(page);
  const sent = [];
  page.on("request", (result) => {
    if (
      result.method() === "POST" &&
      result.url().endsWith("/api/templates/ingest")
    )
      sent.push(result.postDataJSON());
  });
  await dialog.getByLabel("Folder path").fill("   ");
  await dialog.getByRole("button", { name: "Load folder" }).click();
  await expect(dialog.locator(".form-error")).toBeVisible();
  expect(sent).toEqual([]);
  await expect(dialog.locator(".form-error")).toContainText("whitespace-only");
  await expect(dialog.getByLabel("Folder path")).toHaveValue("   ");
  await expect(dialog.getByLabel("Folder path")).toBeEnabled();
  await expect(
    dialog.getByRole("button", { name: "Load folder" }),
  ).toBeEnabled();
  await dialog
    .locator(".dialog-actions")
    .getByRole("button", { name: "Close", exact: true })
    .click();
  await expect(dialog).toBeHidden();
  expect(await (await request.get("/api/templates")).json()).toEqual(before);
});
