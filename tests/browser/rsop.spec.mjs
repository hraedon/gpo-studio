import { expect, test } from "@playwright/test";

// One end-to-end pass over the RSOP panel. Without it the module is wired only
// in unit tests, which is the state WI-030 existed to end one level down: a
// thing that works in isolation and that no operator can actually reach.

const DOMAIN_DN = "DC=ad,DC=hraedon,DC=com";
const OU_DN = `OU=Servers,${DOMAIN_DN}`;
const GPO_DOMAIN = "11111111-2222-3333-4444-555555555555";
const GPO_OU = "22222222-3333-4444-5555-666666666666";

function setting(id, value) {
  return {
    id,
    side: "computer",
    hive: "HKLM",
    key: "Software\\Policies\\StudioLab",
    value_name: "Val",
    registry_type: "REG_SZ",
    value,
  };
}

const TOPOLOGY = {
  nodes: [
    {
      dn: OU_DN,
      name: "Servers",
      scope: "ou",
      parent_dn: DOMAIN_DN,
      links: [{ gpo_guid: GPO_OU, scope: "ou", scope_dn: OU_DN, order: 1 }],
    },
    {
      dn: DOMAIN_DN,
      name: "ad",
      scope: "domain",
      links: [
        {
          gpo_guid: GPO_DOMAIN,
          scope: "domain",
          scope_dn: DOMAIN_DN,
          order: 1,
        },
      ],
    },
  ],
  gpos: [
    {
      guid: GPO_DOMAIN,
      name: "Domain Baseline",
      settings: [setting("s-a", "domain")],
    },
    {
      guid: GPO_OU,
      name: "Servers Override",
      settings: [setting("s-b", "ou")],
    },
  ],
};

test("predicts effective policy and shows what the answer does not say", async ({
  page,
}) => {
  await page.goto("/");
  await page.locator("#open-rsop").click();

  const form = page.locator("#rsop-form");
  await expect(form).toBeVisible();
  await form.locator("[name=computer_name]").fill("LABCL01");
  await form.locator("[name=computer_dn]").fill(`CN=LABCL01,${OU_DN}`);
  await form.locator("[name=domain]").fill("ad.hraedon.com");
  await form.locator("[name=topology]").fill(JSON.stringify(TOPOLOGY, null, 2));
  await form.getByRole("button", { name: "Compute" }).click();

  const results = page.locator("#rsop-results");
  // The OU link beats the domain link, and the domain GPO is named as the one
  // it overrode.
  await expect(results).toContainText("Servers Override");
  await expect(results).toContainText("Domain Baseline");
  // WI-032 travels with the answer, not only with the docs.
  await expect(results).toContainText("gpo_status_is_not_per_side");
  await expect(results).toContainText("applied on at least one side");
});

test("refuses a topology it cannot read rather than guessing", async ({
  page,
}) => {
  await page.goto("/");
  await page.locator("#open-rsop").click();

  const form = page.locator("#rsop-form");
  await form.locator("[name=domain]").fill("ad.hraedon.com");
  await form.locator("[name=topology]").fill("{ not json");
  await form.getByRole("button", { name: "Compute" }).click();

  await expect(form.locator(".form-error")).toContainText("not valid JSON");
});
