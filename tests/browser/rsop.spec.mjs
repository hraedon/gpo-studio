import AxeBuilder from "@axe-core/playwright";
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
  // WI-032 closed: the panel shows both sides instead of explaining that it
  // cannot. The negative assertion is the one that matters -- a stale
  // disclaimer left in the UI is how the last copy of a fixed limitation
  // survives, and this is where it was caught.
  await expect(results).not.toContainText("applied on at least one side");
  await expect(results).not.toContainText("gpo_status_is_not_per_side");
  await expect(results.locator("th", { hasText: "Computer" })).toBeVisible();
  await expect(results.locator("th", { hasText: "User" })).toBeVisible();
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

// The workspace-wide axe scan runs with every dialog closed, so the RSOP
// dialog is covered only if something opens it and fills it. Each state that
// renders its own markup is scanned: the empty prompt, a conclusive result, a
// result with warnings and a blocked GPO, an inconclusive result, a refusal
// before the request and a refusal from the server. Both themes are held to
// the bar the rest of the interface meets.

const WMI_UNKNOWN = "wmi-not-evaluated";
const WMI_UNEVALUATABLE = "wmi-unevaluatable";
const GPO_FILTERED = "33333333-4444-5555-6666-777777777777";

function wmiFilter(id) {
  return {
    id,
    name: `Synthetic ${id}`,
    query: "SELECT * FROM Win32_OperatingSystem WHERE ProductType = 1",
  };
}

// The OU GPO's WMI filter was never evaluated, so it applies with a warning; a
// third GPO's filter could not be evaluated, so it is blocked with a reason.
const WARNED_TOPOLOGY = {
  nodes: [
    {
      ...TOPOLOGY.nodes[0],
      links: [
        ...TOPOLOGY.nodes[0].links,
        { gpo_guid: GPO_FILTERED, scope: "ou", scope_dn: OU_DN, order: 2 },
      ],
    },
    TOPOLOGY.nodes[1],
  ],
  gpos: [
    TOPOLOGY.gpos[0],
    { ...TOPOLOGY.gpos[1], wmi_filter: wmiFilter(WMI_UNKNOWN) },
    {
      guid: GPO_FILTERED,
      name: "Filtered Synthetic",
      settings: [setting("s-c", "filtered")],
      wmi_filter: wmiFilter(WMI_UNEVALUATABLE),
    },
  ],
  wmi_filter_results: { [WMI_UNEVALUATABLE]: "unevaluatable" },
};

// No topology the current engine accepts yields an unevaluable GPO (every
// filtering cell is measured), but the panel still renders the inconclusive
// banner and the per-setting conditional note for an answer that carries one.
// The server's real answer is altered in flight so that markup is scanned too.
async function answerInconclusively(route) {
  const response = await route.fetch();
  const body = await response.json();
  const winner = body.gpo_results.find((gpo) => gpo.gpo_guid === GPO_OU);
  winner.status = "unevaluable";
  winner.computer_status = "unevaluable";
  body.is_conclusive = false;
  body.computer_settings[0].unevaluable_gpos = [GPO_OU];
  await route.fulfill({ response, json: body });
}

// A REG_DWORD whose value is not a number passes the browser's JSON check and
// is refused by the server's validation (422).
const SERVER_REFUSED_TOPOLOGY = {
  ...TOPOLOGY,
  gpos: [
    {
      ...TOPOLOGY.gpos[0],
      settings: [
        { ...setting("s-a", "not-a-number"), registry_type: "REG_DWORD" },
      ],
    },
    TOPOLOGY.gpos[1],
  ],
};

async function expectNoSeriousViolations(page, state) {
  const audit = await new AxeBuilder({ page }).analyze();
  const serious = audit.violations
    .filter((violation) => ["serious", "critical"].includes(violation.impact))
    .map(({ id, impact, nodes }) => ({
      id,
      impact,
      targets: nodes.map((node) => node.target.join(" ")),
    }));
  expect(serious, state).toEqual([]);
}

async function chooseTheme(page, theme) {
  // The toggle cycles Auto -> Dark -> Light. An explicit choice is used for
  // both themes, so neither depends on the browser's colour-scheme default.
  const toggle = page.locator("#theme-toggle");
  const label = theme === "dark" ? "Dark" : "Light";
  for (let step = 0; step < 3; step += 1) {
    if ((await toggle.textContent()) === label) break;
    await toggle.click();
  }
  await expect(toggle).toHaveText(label);
  await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
}

async function compute(form, topology) {
  await form.locator("[name=topology]").fill(JSON.stringify(topology, null, 2));
  await form.getByRole("button", { name: "Compute" }).click();
}

for (const theme of ["light", "dark"]) {
  test(`the open dialog meets the same accessibility bar in the ${theme} theme`, async ({
    page,
  }) => {
    await page.goto("/");
    await chooseTheme(page, theme);
    await page.locator("#open-rsop").click();

    const form = page.locator("#rsop-form");
    const results = page.locator("#rsop-results");
    await expect(form).toBeVisible();
    await expect(results).toContainText("Enter a target and a topology");
    await expectNoSeriousViolations(page, `${theme}: empty dialog`);

    await form.locator("[name=computer_name]").fill("LABCL01");
    await form.locator("[name=computer_dn]").fill(`CN=LABCL01,${OU_DN}`);
    await form.locator("[name=domain]").fill("ad.hraedon.com");

    await compute(form, TOPOLOGY);
    await expect(results).toContainText("Servers Override");
    // Computer settings and the GPO list; no user-side value applies.
    await expect(results.locator("table")).toHaveCount(2);
    await expectNoSeriousViolations(page, `${theme}: conclusive result`);

    await compute(form, WARNED_TOPOLOGY);
    await expect(results.locator(".rsop-warnings")).toBeVisible();
    await expect(results).toContainText("wmi_filter_unevaluatable");
    await expectNoSeriousViolations(page, `${theme}: warnings and a block`);

    await page.route("**/api/rsop/compute", answerInconclusively);
    await compute(form, TOPOLOGY);
    await expect(results.locator(".rsop-inconclusive")).toBeVisible();
    await expect(results.locator(".rsop-conditional")).toBeVisible();
    await expectNoSeriousViolations(page, `${theme}: inconclusive result`);
    await page.unroute("**/api/rsop/compute", answerInconclusively);

    await form.locator("[name=topology]").fill("{ not json");
    await form.getByRole("button", { name: "Compute" }).click();
    await expect(form.locator(".form-error")).toContainText("not valid JSON");
    await expect(form.locator(".form-error")).toBeFocused();
    await expectNoSeriousViolations(
      page,
      `${theme}: refused before the request`,
    );

    await compute(form, SERVER_REFUSED_TOPOLOGY);
    await expect(form.locator(".form-error")).toBeVisible();
    await expect(form.locator(".form-error")).not.toContainText(
      "not valid JSON",
    );
    await expect(results).toBeEmpty();
    await expectNoSeriousViolations(page, `${theme}: refused by the server`);

    await page.keyboard.press("Escape");
    await expect(page.locator("#rsop-dialog")).not.toBeVisible();
    await expect(page.locator("#open-rsop")).toBeFocused();
  });
}
