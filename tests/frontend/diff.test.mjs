import { describe, expect, test } from "vitest";

import { renderDiff } from "../../src/gpo_studio/static/js/diff.mjs";

const FOLDER = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}";

function rule(path) {
  return {
    folder_guid: FOLDER,
    principal: "s-1-1-0",
    full_path: path,
    flags: 1021,
    flags_text: "1021",
    entries: [],
  };
}

function render(data) {
  const target = { innerHTML: "" };
  renderDiff(data, target);
  return target.innerHTML;
}

describe("renderDiff and imported fdeploy1.ini rows (WI-068)", () => {
  test("a redirection change alone is a difference, not 'No differences found'", () => {
    const html = render({
      settings: [],
      links: [],
      security_filters: [],
      fdeploy: [
        {
          kind: "modified",
          folder_guid: FOLDER,
          principal: "s-1-1-0",
          old: rule("\\\\server\\a"),
          new: rule("\\\\server\\<b>"),
        },
      ],
    });
    expect(html).toContain("Folder Redirection, imported fdeploy1.ini (1)");
    expect(html).toContain(`${FOLDER} for s-1-1-0`);
    expect(html).toContain("\\\\server\\&lt;b&gt; · Flags 1021");
    expect(html).not.toContain("No differences found");
  });

  test("a redirection conflict is shown as a conflict", () => {
    const html = render({
      settings: [],
      links: [],
      security_filters: [],
      conflicts: [],
      fdeploy_conflicts: [
        {
          folder_guid: FOLDER,
          principal: "s-1-1-0",
          baseline: rule("\\\\server\\a"),
          draft: rule("\\\\server\\b"),
          observed: null,
        },
      ],
    });
    expect(html).toContain("CONFLICT: Folder Redirection (1)");
    expect(html).not.toContain("No differences found");
  });

  test("a response without the fields still renders", () => {
    expect(render({ settings: [], links: [], security_filters: [] })).toContain(
      "No differences found",
    );
  });
});
