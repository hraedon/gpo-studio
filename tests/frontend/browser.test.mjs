import { describe, expect, test } from "vitest";

import {
  formatElementValue,
  formatUnresolvedValue,
} from "../../src/gpo_studio/static/js/browser.mjs";

describe("formatElementValue", () => {
  test.each([
    [1, "1"],
    [true, "true"],
    [false, "false"],
    ["C:\\Path", "C:\\Path"],
    [["alpha", "beta"], "alpha; beta"],
    [42, "42"],
  ])("formats %p as %p", (input, expected) => {
    expect(formatElementValue(input)).toBe(expected);
  });

  test("formats an empty array as an empty string", () => {
    expect(formatElementValue([])).toBe("");
  });
});

describe("formatUnresolvedValue", () => {
  test("returns Delete value for a delete action", () => {
    expect(formatUnresolvedValue({ action: "delete", value: "ignored" })).toBe(
      "Delete value",
    );
  });

  test("joins multi-string values with a middle dot", () => {
    expect(
      formatUnresolvedValue({ action: "set", value: ["first", "second"] }),
    ).toBe("first · second");
  });

  test("stringifies scalar values without precision loss", () => {
    expect(
      formatUnresolvedValue({ action: "set", value: "18446744073709551615" }),
    ).toBe("18446744073709551615");
  });
});
