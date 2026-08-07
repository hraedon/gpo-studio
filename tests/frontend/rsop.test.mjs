import { describe, expect, test } from "vitest";

import {
  formatEffectiveValue,
  parseTopology,
  renderGpoResults,
  renderLimitations,
  renderRsopResult,
  renderSideSettings,
  renderWarnings,
  splitPrincipals,
} from "../../src/gpo_studio/static/js/rsop.mjs";

describe("parseTopology", () => {
  test("accepts a topology carrying nodes and gpos", () => {
    expect(parseTopology('{"nodes": [], "gpos": []}')).toEqual({
      nodes: [],
      gpos: [],
      wmi_filter_results: {},
    });
  });

  test("keeps caller-supplied WMI filter results", () => {
    const parsed = parseTopology(
      '{"nodes": [], "gpos": [], "wmi_filter_results": {"w": false}}',
    );
    expect(parsed.wmi_filter_results).toEqual({ w: false });
  });

  test.each([
    ["not json", "not valid JSON"],
    ["[]", "JSON object"],
    ['{"nodes": []}', "`nodes` and `gpos` arrays"],
    ['{"nodes": [], "gpos": [], "target": {}}', "unrecognised keys"],
  ])("refuses %s", (text, fragment) => {
    expect(() => parseTopology(text)).toThrow(new RegExp(fragment));
  });

  test("an unrecognised key is refused rather than dropped", () => {
    // A silently ignored key looks like an input that was honoured, which is
    // the same failure the limitations exist to prevent one level up.
    expect(() =>
      parseTopology('{"nodes": [], "gpos": [], "targets": []}'),
    ).toThrow(/targets/);
  });
});

describe("splitPrincipals", () => {
  test("splits on newlines, commas and semicolons and drops blanks", () => {
    expect(
      splitPrincipals("LAB\\GroupA\nLAB\\GroupB, LAB\\GroupC;\n\n"),
    ).toEqual(["LAB\\GroupA", "LAB\\GroupB", "LAB\\GroupC"]);
  });

  test("an empty box is no memberships, not one empty one", () => {
    expect(splitPrincipals("")).toEqual([]);
    expect(splitPrincipals(undefined)).toEqual([]);
  });
});

describe("renderLimitations", () => {
  test("renders the code and its message", () => {
    const html = renderLimitations([
      { code: "gpo_status_is_not_per_side", message: "It collapses. WI-032." },
    ]);
    expect(html).toContain("gpo_status_is_not_per_side");
    expect(html).toContain("It collapses. WI-032.");
  });

  test("escapes both fields", () => {
    const html = renderLimitations([
      { code: "<script>", message: "<img onerror=x>" },
    ]);
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<img");
  });

  test("renders nothing when there are none", () => {
    expect(renderLimitations([])).toBe("");
    expect(renderLimitations(undefined)).toBe("");
  });
});

describe("renderWarnings", () => {
  test("lists each warning and escapes it", () => {
    expect(renderWarnings(["wmi_filter_unknown"])).toContain(
      "wmi_filter_unknown",
    );
    expect(renderWarnings(["<b>"])).not.toContain("<b>");
  });
});

describe("formatEffectiveValue", () => {
  test.each([
    [["first", "second"], "first · second"],
    ["18446744073709551615", "18446744073709551615"],
    [null, ""],
  ])("formats %s", (value, expected) => {
    expect(formatEffectiveValue(value)).toBe(expected);
  });
});

describe("renderSideSettings", () => {
  const setting = {
    hive: "HKLM",
    key: "Software\\Policies\\StudioLab",
    value_name: "Val",
    effective_value: "ou",
    winning_gpo_name: "Servers Override",
    is_enforced: false,
    overridden_by: ["Domain Baseline"],
    unevaluable_gpos: [],
  };

  test("names the winner and what it overrode", () => {
    const html = renderSideSettings("computer", [setting]);
    expect(html).toContain("Computer settings");
    expect(html).toContain("Servers Override");
    expect(html).toContain("Domain Baseline");
  });

  test("says a value is conditional when an unevaluable GPO writes it", () => {
    const html = renderSideSettings("computer", [
      { ...setting, unevaluable_gpos: ["g-unknown"] },
    ]);
    expect(html).toContain("Conditional");
    expect(html).toContain("g-unknown");
  });

  test("an empty side says so per side rather than going blank", () => {
    expect(renderSideSettings("user", [])).toContain(
      "No user-side value is predicted to apply",
    );
  });
});

describe("renderGpoResults", () => {
  test("says on the table that the status is not per side", () => {
    // The API's whole point about WI-032 is undone if the UI labels the column
    // "Applied to". The disclaimer travels with the table.
    const html = renderGpoResults([
      {
        precedence: 1,
        gpo_name: "Servers Override",
        status: "applied",
        filtering_reasons: [],
        link_scope: "OU=Servers,DC=ad,DC=hraedon,DC=com",
      },
    ]);
    expect(html).toContain("applied on at least one side");
    expect(html).toContain("WI-032");
  });

  test("shows the blocking reasons a GPO carries", () => {
    const html = renderGpoResults([
      {
        precedence: 1,
        gpo_name: "ReadDenied",
        status: "blocked",
        filtering_reasons: ["security_filter_read_denied"],
        link_scope: "OU=Servers,DC=ad,DC=hraedon,DC=com",
      },
    ]);
    expect(html).toContain("security_filter_read_denied");
  });
});

describe("renderRsopResult", () => {
  const body = {
    is_conclusive: true,
    limitations: [{ code: "gpo_status_is_not_per_side", message: "WI-032." }],
    warnings: [],
    computer_settings: [],
    user_settings: [],
    gpo_results: [],
  };

  test("puts the limitations before the answer", () => {
    const html = renderRsopResult(body);
    expect(html.indexOf("gpo_status_is_not_per_side")).toBeLessThan(
      html.indexOf("Computer settings"),
    );
  });

  test("says plainly when the prediction is not conclusive", () => {
    const html = renderRsopResult({ ...body, is_conclusive: false });
    expect(html).toContain("not conclusive");
  });

  test("says nothing about conclusiveness when it is conclusive", () => {
    // The control: a banner shown unconditionally would pass the test above
    // and tell an operator nothing.
    expect(renderRsopResult(body)).not.toContain("not conclusive");
  });
});
