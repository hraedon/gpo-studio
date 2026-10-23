import { expect, test } from "@playwright/test";

async function seed(request, suffix) {
  const name = `Synthetic Navigation ${suffix} ${Date.now()}`;
  const payload = await (
    await request.post("/api/gpos", {
      data: { name, actor: "browser-test", reason: "Navigation test" },
    })
  ).json();
  return payload.gpo;
}

test("switching GPO refreshes the active revision diff", async ({
  page,
  request,
}) => {
  const first = await seed(request, "First");
  const second = await seed(request, "Second");
  await request.patch(`/api/gpos/${second.guid}`, {
    data: {
      name: second.name,
      description: "Second revision",
      actor: "browser-test",
      reason: "Synthetic second revision",
      expected_revision: 1,
    },
  });
  await page.goto("/");
  await page.locator(".gpo-item", { hasText: first.name }).click();
  await page.getByRole("tab", { name: "Diff", exact: true }).click();
  await expect(page.locator("#run-revision-diff")).toBeDisabled();
  await page.locator(".gpo-item", { hasText: second.name }).click();
  await expect(page.locator("#revision-diff-to")).toHaveValue("2");
  await page.locator("#run-revision-diff").click();
  await expect(page.locator("#revision-diff-results")).toContainText(
    "Second revision",
  );
  await page.locator(".gpo-item", { hasText: first.name }).click();
  await expect(page.locator("#revision-diff-to")).toHaveValue("1");
  await expect(page.locator("#run-revision-diff")).toBeDisabled();
  await expect(page.locator("#revision-diff-results")).toContainText(
    "Create another revision",
  );
});

test("a late navigation response cannot replace the latest selection", async ({
  page,
  request,
}) => {
  const first = await seed(request, "Slow");
  const second = await seed(request, "Latest");
  await page.goto("/");
  await expect(page.locator("#title")).not.toHaveText("Choose a policy");
  let release;
  const delayed = new Promise((resolve) => {
    release = resolve;
  });
  await page.route(`**/api/gpos/${first.guid}`, async (route) => {
    await delayed;
    await route.continue();
  });
  await page.locator(".gpo-item", { hasText: first.name }).click();
  await page.locator(".gpo-item", { hasText: second.name }).click();
  await expect(page.locator("#title")).toHaveText(second.name);
  const response = page.waitForResponse((response) =>
    response.url().endsWith(`/api/gpos/${first.guid}`),
  );
  release();
  await response;
  await expect(page.locator("#title")).toHaveText(second.name);
});
