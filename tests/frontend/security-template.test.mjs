import { describe, expect, test } from "vitest";

import {
  MODES,
  parseFamilies,
  renderIssues,
  renderTemplate,
} from "../../src/gpo_studio/static/js/security-template.mjs";

describe("parseFamilies", () => {
  test("accepts the keys its mode declares", () => {
    expect(
      parseFamilies(
        '{"audit": {"logon_events": "success"}}',
        "policy-families",
      ),
    ).toEqual({ audit: { logon_events: "success" } });
    expect(parseFamilies('{"services": []}', "object-security")).toEqual({
      services: [],
    });
  });

  test.each([
    ["not json", "policy-families", "not valid JSON"],
    ["[]", "policy-families", "JSON object"],
    ['{"services": []}', "policy-families", "Unrecognised keys"],
    ['{"audit": {}}', "object-security", "Unrecognised keys"],
  ])("refuses %s in %s mode", (text, mode, fragment) => {
    expect(() => parseFamilies(text, mode)).toThrow(new RegExp(fragment));
  });

  test("a key from the other mode is refused, not forwarded", () => {
    // Both endpoints forbid unknown keys, so forwarding would be a 422 with a
    // server-shaped message. Refusing here names the keys this mode takes
    // while the caller is still looking at the field.
    expect(() =>
      parseFamilies('{"registry_keys": []}', "policy-families"),
    ).toThrow(/account, audit, user_rights, security_options/);
  });

  test("restricted groups is not a key either mode accepts", () => {
    // WI-064: the family is omitted from the surface entirely, so the panel
    // must not appear to offer it.
    for (const mode of Object.keys(MODES)) {
      expect(() => parseFamilies('{"restricted_groups": []}', mode)).toThrow(
        /Unrecognised keys/,
      );
    }
  });
});

describe("renderIssues", () => {
  test("an empty list says why it is empty", () => {
    // The silence is a ruling (WI-055), and a bare "no issues" reads as
    // approval of the access granted.
    expect(renderIssues([])).toMatch(/deliberately unjudged/);
  });

  test("renders severity, code, message and path", () => {
    const html = renderIssues([
      {
        severity: "warning",
        code: "lockout_disabled",
        message: "Account lockout is disabled.",
        path: "AccountPolicyFamily/LockoutPolicy/lockout_threshold",
      },
    ]);
    expect(html).toContain("lockout_disabled");
    expect(html).toContain("Account lockout is disabled.");
    expect(html).toContain(
      "AccountPolicyFamily/LockoutPolicy/lockout_threshold",
    );
  });

  test("escapes issue text", () => {
    const html = renderIssues([
      {
        severity: "error",
        code: "x",
        message: "<script>alert(1)</script>",
        path: "p",
      },
    ]);
    expect(html).not.toContain("<script>");
  });
});

describe("renderTemplate", () => {
  const body = {
    inf_text: "[Unicode]\nUnicode = yes\n",
    inf_base64: "",
    sections: [],
    issues: [],
    limitations: [
      {
        code: "round_trip_not_application",
        message: "/configure is never invoked.",
      },
    ],
  };

  test("limitations come before the template", () => {
    // The panel's one real job beyond reaching the endpoint, and the same
    // contract the RSOP panel keeps: an INF that Windows accepts is not an INF
    // shown to apply, and underneath the output is too late to say so.
    const html = renderTemplate(body);
    expect(html.indexOf("round_trip_not_application")).toBeLessThan(
      html.indexOf("GptTmpl.inf"),
    );
  });

  test("says the returned text is not the bytes Windows consumes", () => {
    expect(renderTemplate(body)).toMatch(/UTF-16LE/);
  });

  test("escapes the template text", () => {
    const html = renderTemplate({ ...body, inf_text: "<b>not markup</b>" });
    expect(html).not.toContain("<b>not markup</b>");
    expect(html).toContain("&lt;b&gt;");
  });
});

describe("MODES", () => {
  test("each mode names a distinct endpoint and key set", () => {
    const paths = Object.values(MODES).map((mode) => mode.path);
    expect(new Set(paths).size).toBe(paths.length);
    for (const mode of Object.values(MODES)) {
      expect(mode.path.startsWith("/api/security-template/")).toBe(true);
      expect(mode.keys.length).toBeGreaterThan(0);
    }
  });
});
