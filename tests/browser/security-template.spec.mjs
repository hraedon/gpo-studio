import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

// One end-to-end pass over the security-template panel (Plan 034 WP-3), for
// the reason the RSOP spec gives: without it the module is wired only in unit
// tests, which is the state surfacing exists to end.
//
// The assertions that matter here are the negative ones and the ordering one.
// Both endpoints answer in a direction Windows certified and in no other, and
// the panel's job is to say so before the answer rather than after it.

test("renders a policy-family template and says what it does not establish", async ({
  page,
}) => {
  await page.goto("/");
  await page.locator("#open-security-template").click();

  const form = page.locator("#security-template-form");
  await expect(form).toBeVisible();
  await form
    .locator("[name=families]")
    .fill('{"audit": {"logon_events": "success"}}');
  await form.getByRole("button", { name: "Render" }).click();

  const results = page.locator("#security-template-results");
  // The corrected native key (the lane's first run rejected
  // `AuditDirectoryServiceAccess`), and the audit value that was asked for.
  await expect(results).toContainText("AuditDSAccess");
  await expect(results).toContainText("AuditLogonEvents = 1");
  // A member server omits Kerberos, and is told why rather than left to notice.
  await expect(results).not.toContainText("MaxTicketAge");
  await expect(results).toContainText("kerberos_omitted_for_member_server");
  await expect(results).toContainText("round_trip_not_application");
});

test("the limitations are above the template, not below it", async ({
  page,
}) => {
  await page.goto("/");
  await page.locator("#open-security-template").click();

  const form = page.locator("#security-template-form");
  await form.locator("[name=families]").fill("{}");
  await form.getByRole("button", { name: "Render" }).click();

  const results = page.locator("#security-template-results");
  await expect(results).toContainText("What this answer does not say");
  const html = await results.innerHTML();
  expect(html.indexOf("What this answer does not say")).toBeLessThan(
    html.indexOf("GptTmpl.inf"),
  );
});

test("a domain controller gets the Kerberos section", async ({ page }) => {
  await page.goto("/");
  await page.locator("#open-security-template").click();

  const form = page.locator("#security-template-form");
  await form.locator("[name=scope]").selectOption("domain_controller");
  await form.locator("[name=families]").fill("{}");
  await form.getByRole("button", { name: "Render" }).click();

  const results = page.locator("#security-template-results");
  // The units measured on a real DC: hours beside minutes, the same ten hours
  // twice, which is the pair a unit mix-up would make look consistent.
  await expect(results).toContainText("MaxTicketAge = 10");
  await expect(results).toContainText("MaxServiceAge = 600");
  await expect(results).not.toContainText("kerberos_omitted_for_member_server");
});

test("object security renders and declares the ACL ruling", async ({
  page,
}) => {
  await page.goto("/");
  await page.locator("#open-security-template").click();

  const form = page.locator("#security-template-form");
  await form.locator("[name=mode]").selectOption("object-security");
  await form
    .locator("[name=families]")
    .fill(
      '{"registry_keys": [{"key_path": "MACHINE\\\\Software\\\\Contoso", "raw_sddl": "D:PAR(A;CI;KA;;;WD)"}]}',
    );
  await form.getByRole("button", { name: "Render" }).click();

  const results = page.locator("#security-template-results");
  await expect(results).toContainText("Registry Keys");
  // Everyone full control renders clean. The empty validation list must not be
  // presented as approval -- WI-055 ruled the content unjudged, and this is
  // where an operator would otherwise read silence as a pass.
  await expect(results).toContainText("deliberately unjudged");
  await expect(results).toContainText("acl_content_is_not_judged");
  await expect(results).toContainText("restricted_groups_not_surfaced");
});

test("scope is hidden for object security, which has no such distinction", async ({
  page,
}) => {
  await page.goto("/");
  await page.locator("#open-security-template").click();

  const form = page.locator("#security-template-form");
  const scope = page.locator("#security-template-scope-row");
  await expect(scope).toBeVisible();
  await form.locator("[name=mode]").selectOption("object-security");
  await expect(scope).toBeHidden();
  // Sending `scope` to that endpoint would be a 422: both request shapes
  // forbid unknown keys, so hiding the control and dropping the value have to
  // agree.
  await form.locator("[name=families]").fill('{"services": []}');
  await form.getByRole("button", { name: "Render" }).click();
  await expect(page.locator("#security-template-results")).toContainText(
    "[Unicode]",
  );
});

test("refuses families it cannot read rather than forwarding them", async ({
  page,
}) => {
  await page.goto("/");
  await page.locator("#open-security-template").click();

  const form = page.locator("#security-template-form");
  await form.locator("[name=families]").fill("{ not json");
  await form.getByRole("button", { name: "Render" }).click();
  await expect(form.locator(".form-error")).toContainText("not valid JSON");

  // A key belonging to the other mode is named, with the keys this one takes.
  await form.locator("[name=families]").fill('{"services": []}');
  await form.getByRole("button", { name: "Render" }).click();
  await expect(form.locator(".form-error")).toContainText("Unrecognised keys");
});

test("the open dialog meets the same accessibility bar as the rest", async ({
  page,
}) => {
  await page.goto("/");
  await page.locator("#open-security-template").click();
  await expect(page.locator("#security-template-form")).toBeVisible();

  // The workspace-wide scan runs with every dialog closed, so a new one is
  // only covered if something opens it first.
  const results = await new AxeBuilder({ page }).analyze();
  const serious = results.violations.filter((violation) =>
    ["serious", "critical"].includes(violation.impact),
  );
  expect(serious).toEqual([]);
});
