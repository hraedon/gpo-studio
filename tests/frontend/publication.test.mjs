import { describe, expect, test } from "vitest";

import {
  COVERAGE_LABELS,
  coverageBadge,
  groupSteps,
  renderAbsences,
  renderIssues,
  renderPublicationPlan,
  renderStep,
} from "../../src/gpo_studio/static/js/publication.mjs";
import { LIMITATION_SENTENCES } from "../../src/gpo_studio/static/js/rsop.mjs";

function step(overrides) {
  return {
    step_id: "s",
    operation: "write_registry_pol",
    target: "sysvol",
    detail: "Write Machine/Registry.pol",
    artifact_ids: [],
    version_half: null,
    sysvol_path: "Machine/Registry.pol",
    directory_attribute: null,
    directory_value: null,
    coverage: "measured",
    ...overrides,
  };
}

const PLAN = {
  gpo_guid: "31415926-5358-9793-2384-626433832795",
  gpo_name: "Synthetic",
  target: "both",
  risk_level: "low",
  requires_enhanced_approval: false,
  refused: false,
  steps: [
    step({
      operation: "update_gpt_ini",
      sysvol_path: "GPT.INI",
      version_half: "both",
    }),
    step({}),
    step({
      operation: "update_extension_lists",
      target: "ad",
      sysvol_path: null,
      directory_attribute: "gPCMachineExtensionNames",
      directory_value: "[{35378EAC}{D02B1F72}]",
    }),
    step({
      operation: "update_gplink",
      target: "ad",
      sysvol_path: null,
      coverage: "unmeasured",
      detail: "Update gPLink on OU=Servers,DC=synthetic,DC=test",
    }),
  ],
  rollback_steps: [
    step({
      operation: "restore_gpt_ini",
      sysvol_path: null,
      coverage: "unmeasured",
    }),
  ],
  planned_sysvol_paths: ["GPT.INI", "Machine/Registry.pol"],
  payload_digest: "ef".repeat(32),
  issues: [],
  absences: [
    {
      operation: "write_gpo_comment",
      sysvol_path: "GPO.cmt",
      coverage: "measured",
      detail: "The plan writes no GPO.cmt.",
    },
  ],
  limitations: [
    { code: "nothing_here_writes", message: "Review only." },
    { code: "ad_side_steps_unmeasured", message: "No links." },
  ],
};

describe("groupSteps", () => {
  test("splits SYSVOL from AD and keeps plan order inside each", () => {
    const groups = groupSteps(PLAN.steps);
    expect(groups.map((group) => group.heading)).toEqual([
      "SYSVOL",
      "Active Directory",
    ]);
    expect(groups[0].steps.map((s) => s.operation)).toEqual([
      "update_gpt_ini",
      "write_registry_pol",
    ]);
    expect(groups[1].steps.map((s) => s.operation)).toEqual([
      "update_extension_lists",
      "update_gplink",
    ]);
  });

  test("a whole-plan refusal gets its own group", () => {
    const groups = groupSteps([
      step({
        operation: "unsupported_side_status",
        target: "both",
        coverage: "refused",
      }),
    ]);
    expect(groups.map((group) => group.heading)).toEqual(["Whole plan"]);
  });
});

describe("coverage badges", () => {
  test.each(Object.entries(COVERAGE_LABELS))(
    "%s is labelled in words, not colour alone",
    (coverage, label) => {
      expect(coverageBadge(coverage)).toContain(`>${label}<`);
    },
  );

  test("a step shows its badge, operation, detail and typed facts", () => {
    const html = renderStep(PLAN.steps[2]);
    expect(html).toContain("Measured");
    expect(html).toContain("update_extension_lists");
    expect(html).toContain("gPCMachineExtensionNames");
    expect(html).toContain("[{35378EAC}{D02B1F72}]");
  });

  test("escapes plan text", () => {
    const html = renderStep(step({ detail: "<script>x</script>" }));
    expect(html).not.toContain("<script>");
  });
});

describe("renderPublicationPlan", () => {
  test("limitations come before the steps", () => {
    const html = renderPublicationPlan(PLAN);
    expect(html.indexOf("What this answer does not say")).toBeLessThan(
      html.indexOf("<h3>Steps</h3>"),
    );
    expect(html).toContain(LIMITATION_SENTENCES.nothing_here_writes);
  });

  test("shows the digest, the paths, the measured absence and the rollback", () => {
    const html = renderPublicationPlan(PLAN);
    expect(html).toContain(PLAN.payload_digest);
    expect(html).toContain("Machine/Registry.pol");
    expect(html).toContain("GPO.cmt");
    expect(html).toContain("<summary>Rollback steps</summary>");
    expect(html).toContain("restore_gpt_ini");
  });

  test("never shows a plan id", () => {
    expect(
      renderPublicationPlan({ ...PLAN, plan_id: "plan-123" }),
    ).not.toContain("plan-123");
  });

  test("refusals are stated above the steps", () => {
    const html = renderPublicationPlan({
      ...PLAN,
      refused: true,
      issues: [
        {
          check: "unsupported_side_status",
          level: "error",
          message: "Disabled side.",
          component: "plan",
        },
      ],
    });
    expect(html).toContain("The planner refuses this publication");
    expect(html.indexOf("unsupported_side_status")).toBeLessThan(
      html.indexOf("<h3>Steps</h3>"),
    );
  });

  test("empty lists render nothing for themselves", () => {
    expect(renderIssues([])).toBe("");
    expect(renderAbsences([])).toBe("");
  });
});

describe("plain limitation sentences", () => {
  test.each([
    "nothing_here_writes",
    "ad_side_steps_unmeasured",
    "one_shape_measured",
    "out_of_model_content_not_planned",
    "rollback_unmeasured",
  ])("%s has a plain sentence", (code) => {
    expect(LIMITATION_SENTENCES[code]).toBeTruthy();
  });
});
