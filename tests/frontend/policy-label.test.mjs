import { expect, test } from "vitest";
import { policyDisplayLabel } from "../../src/gpo_studio/static/js/policy-label.mjs";

test("supplied nonblank display text stays exact, independent of identity", () => {
  expect(
    policyDisplayLabel({
      display_name: "  Synthetic named <policy> & review  ",
      qualified_id: "Synthetic:Original",
      policy_id: "different",
      id: "Original",
    }),
  ).toBe("  Synthetic named <policy> & review  ");
});
for (const display_name of [undefined, null, "", " \t\n ", "\u00a0"])
  test(`blank display metadata ${JSON.stringify(display_name)} uses the qualified identity without changing the source`, () => {
    const policy = Object.freeze({
      display_name,
      qualified_id: "Synthetic.Policies:ExactCase",
      policy_id: "grouped",
      id: "ExactCase",
    });
    expect(policyDisplayLabel(policy)).toBe("Synthetic.Policies:ExactCase");
    expect(policy.display_name).toBe(display_name);
    expect(policy.id).toBe("ExactCase");
  });
test("configured DTO and source-only DTO identities have consistent last fallbacks", () => {
  expect(
    policyDisplayLabel({
      display_name: " ",
      qualified_id: "",
      policy_id: "Synthetic:Grouped",
      id: "Source",
    }),
  ).toBe("Synthetic:Grouped");
  expect(policyDisplayLabel({ id: "Source", policy_id: "\t" })).toBe("Source");
  expect(
    policyDisplayLabel({ qualified_id: "  Exact qualified identity  " }),
  ).toBe("  Exact qualified identity  ");
});
for (const policy of [
  undefined,
  null,
  {},
  { display_name: "\t", qualified_id: " ", policy_id: "", id: "" },
  { display_name: 17, id: false },
])
  test(`unavailable display/identity metadata has a meaningful generic label ${JSON.stringify(policy)}`, () => {
    expect(policyDisplayLabel(policy)).toBe("Unnamed policy");
  });
