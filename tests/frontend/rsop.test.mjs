import { describe, expect, test } from "vitest";

import {
  LIMITATION_SENTENCES,
  formatEffectiveValue,
  parseTopology,
  plainLimitation,
  renderGpoResults,
  renderLimitations,
  renderRsopResult,
  renderSideSettings,
  renderWarnings,
  scrollRegion,
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
  const plainList = (html) =>
    html.slice(html.indexOf("<ul>"), html.indexOf("<details>"));
  const details = (html) =>
    html.slice(html.indexOf("<details>"), html.indexOf("</details>"));

  test("a mapped code renders as its plain sentence", () => {
    const html = renderLimitations([
      { code: "flags_not_decoded", message: "Flags is carried verbatim." },
    ]);
    expect(plainList(html)).toContain(LIMITATION_SENTENCES.flags_not_decoded);
    expect(plainList(html)).not.toContain("flags_not_decoded");
    expect(plainList(html)).not.toContain("Flags is carried verbatim.");
  });

  test("an unknown code still shows its API message in the plain list", () => {
    const html = renderLimitations([
      { code: "gpo_status_is_not_per_side", message: "It collapses. WI-032." },
    ]);
    expect(plainList(html)).toContain("It collapses. WI-032.");
  });

  test("the technical detail is collapsed and keeps every code and message", () => {
    const limitations = [
      { code: "flags_not_decoded", message: "Flags is carried verbatim." },
      { code: "single_capture_only", message: "Exactly one capture (R3)." },
      { code: "not_in_the_map", message: "Something else." },
    ];
    const html = renderLimitations(limitations);
    expect(html).toContain("<details><summary>Technical detail</summary>");
    expect(html).not.toContain("<details open");
    for (const { code, message } of limitations) {
      expect(details(html)).toContain(`<code>${code}</code> — ${message}`);
    }
  });

  test("every Folder Redirection code has a plain sentence", () => {
    for (const code of [
      "flags_not_decoded",
      "single_capture_only",
      "folder_names_documented_not_measured",
      "read_only_no_writer",
    ]) {
      expect(plainLimitation({ code, message: "api text" })).not.toBe(
        "api text",
      );
    }
  });

  test("escapes both fields in both lists", () => {
    const html = renderLimitations([
      { code: "<script>", message: "<img onerror=x>" },
    ]);
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<img");
    expect(plainList(html)).toContain("&lt;img onerror=x&gt;");
    expect(details(html)).toContain("<code>&lt;script&gt;</code>");
  });

  test("a code named like an Object property is not mistaken for a mapped one", () => {
    const html = renderLimitations([
      { code: "constructor", message: "Plain API text." },
    ]);
    expect(plainList(html)).toContain("Plain API text.");
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
  test("shows both sides rather than claiming it cannot (WI-032)", () => {
    // The table used to carry a disclaimer that the status was not a per-side
    // answer. It is one now, and the UI was the last thing still saying
    // otherwise -- caught by the browser suite, not by this one.
    const html = renderGpoResults([
      {
        precedence: 1,
        gpo_name: "Servers Override",
        status: "applied",
        computer_status: "applied",
        user_status: "no_settings_for_side",
        filtering_reasons: [],
        link_scope: "OU=Servers,DC=ad,DC=hraedon,DC=com",
      },
    ]);
    expect(html).not.toContain("applied on at least one side");
    expect(html).toContain(">Computer<");
    expect(html).toContain(">User<");
    expect(html).toContain("no_settings_for_side");
  });

  test("renders a missing per-side value without inventing one", () => {
    // An older cached response has no per-side fields. Showing an em dash is
    // honest; showing "applied" because the merged status said so is not.
    const html = renderGpoResults([
      {
        precedence: 1,
        gpo_name: "Legacy Row",
        status: "applied",
        filtering_reasons: [],
        link_scope: "OU=Servers,DC=ad,DC=hraedon,DC=com",
      },
    ]);
    expect(html).toContain("—");
    expect(html).not.toContain("undefined");
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

describe("scrollRegion", () => {
  // A table wider than the dialog scrolls sideways; a keyboard user can only
  // scroll a container that takes focus, and a focusable one needs a name.
  test("both result tables sit in focusable, named regions", () => {
    const settings = renderSideSettings("computer", [
      {
        hive: "HKLM",
        key: "Software\\Policies\\StudioLab",
        value_name: "Val",
        effective_value: "ou",
        winning_gpo_name: "Servers Override",
        overridden_by: [],
      },
    ]);
    const gpos = renderGpoResults([
      {
        precedence: 1,
        gpo_name: "Servers Override",
        status: "applied",
        filtering_reasons: [],
        link_scope: "OU=Servers,DC=ad,DC=hraedon,DC=com",
      },
    ]);
    expect(settings).toContain(
      '<div class="table-card" tabindex="0" role="region" aria-label="Computer settings">',
    );
    expect(gpos).toContain(
      '<div class="table-card" tabindex="0" role="region" aria-label="GPOs">',
    );
  });

  test("escapes the region name", () => {
    expect(scrollRegion('a"b')).toContain('aria-label="a&quot;b"');
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
