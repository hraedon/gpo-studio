import { describe, expect, test } from "vitest";

import { formatValue } from "../../src/gpo_studio/static/js/render.mjs";
import { escapeHtml } from "../../src/gpo_studio/static/js/state.mjs";

describe("escapeHtml", () => {
  test("escapes active HTML characters and tolerates nullish values", () => {
    expect(escapeHtml(`<script data-test='x'>&"</script>`)).toBe(
      "&lt;script data-test=&#39;x&#39;&gt;&amp;&quot;&lt;/script&gt;",
    );
    expect(escapeHtml(null)).toBe("");
    expect(escapeHtml(undefined)).toBe("");
  });

  test("stringifies numeric boundary values without precision loss", () => {
    expect(escapeHtml("18446744073709551615")).toBe("18446744073709551615");
  });
});

describe("formatValue", () => {
  test.each([
    [{ action: "delete", value: "ignored" }, "Delete value"],
    [{ action: "set", value: ["first", "second"] }, "first · second"],
    [{ action: "set", value: "18446744073709551615" }, "18446744073709551615"],
  ])("formats registry values", (setting, expected) => {
    expect(formatValue(setting)).toBe(expected);
  });
});
