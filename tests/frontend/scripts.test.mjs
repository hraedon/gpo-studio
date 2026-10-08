import { describe, expect, test } from "vitest";

import {
  KINDS,
  buildScriptsRequest,
  emptyRows,
  filenameFrom,
  moveRow,
  renderIssues,
  renderRows,
  renderScriptsPreview,
} from "../../src/gpo_studio/static/js/scripts.mjs";
import { LIMITATION_SENTENCES } from "../../src/gpo_studio/static/js/rsop.mjs";

describe("buildScriptsRequest", () => {
  test("restates the certified candidate in the endpoint's shape", () => {
    // The request `tests/test_scripts_surface.py` holds byte-equal to the R10
    // lane builder; the panel must be able to send exactly it.
    const body = buildScriptsRequest({
      legacy: [
        { command: "zz-studio-marker.cmd", parameters: "/c alpha beta" },
        { command: "zz-studio-second.cmd", parameters: "" },
      ],
      powershell: [
        { command: "zz-studio-marker.ps1", parameters: "-Mode Alpha" },
      ],
    });
    expect(body).toEqual({
      computer: {
        startup: [
          { command: "zz-studio-marker.cmd", parameters: "/c alpha beta" },
          { command: "zz-studio-second.cmd" },
        ],
        powershell_startup: [
          { command: "zz-studio-marker.ps1", parameters: "-Mode Alpha" },
        ],
        powershell_order: "run_windows_powershell_scripts_first",
      },
    });
  });

  test("a legacy-only policy sends no PowerShell order", () => {
    const body = buildScriptsRequest({
      legacy: [{ command: "a.cmd", parameters: "" }],
      powershell: [],
    });
    expect(body.computer).not.toHaveProperty("powershell_order");
    expect(body.computer.powershell_startup).toEqual([]);
  });

  test("never offers a side, trigger or order the lane did not measure", () => {
    const body = buildScriptsRequest({
      legacy: [{ command: "a.cmd" }],
      powershell: [{ command: "b.ps1" }],
    });
    expect(Object.keys(body)).toEqual(["computer"]);
    expect(Object.keys(body.computer).sort()).toEqual([
      "powershell_order",
      "powershell_startup",
      "startup",
    ]);
  });

  test("keeps parameters verbatim, including leading spaces", () => {
    const body = buildScriptsRequest({
      legacy: [{ command: "  a.cmd ", parameters: " /x" }],
      powershell: [],
    });
    expect(body.computer.startup).toEqual([
      { command: "a.cmd", parameters: " /x" },
    ]);
  });

  test.each([
    [emptyRows(), "Add at least one startup script"],
    [
      { legacy: [{ command: " " }], powershell: [] },
      "Startup script 1 has no command",
    ],
    [
      { legacy: [], powershell: [{ command: "" }] },
      "PowerShell startup script 1 has no command",
    ],
  ])("refuses %j before sending it", (rows, fragment) => {
    expect(() => buildScriptsRequest(rows)).toThrow(fragment);
  });
});

describe("moveRow", () => {
  test("swaps with the neighbour and leaves the input untouched", () => {
    const rows = ["a", "b", "c"];
    expect(moveRow(rows, 1, -1)).toEqual(["b", "a", "c"]);
    expect(moveRow(rows, 1, 1)).toEqual(["a", "c", "b"]);
    expect(rows).toEqual(["a", "b", "c"]);
  });

  test("does nothing past either end", () => {
    expect(moveRow(["a", "b"], 0, -1)).toEqual(["a", "b"]);
    expect(moveRow(["a", "b"], 1, 1)).toEqual(["a", "b"]);
  });
});

describe("renderRows", () => {
  test("each row is a named group whose inputs carry the API path", () => {
    const html = renderRows("powershell", [
      { command: "a.ps1", parameters: "" },
      { command: "b.ps1", parameters: "-X" },
    ]);
    expect(html).toContain("<legend>PowerShell startup script 2</legend>");
    expect(html).toContain('name="computer.powershell_startup[1].command"');
    expect(html).toContain('aria-label="Move up: PowerShell startup script 1"');
  });

  test("the first row cannot move up and the last cannot move down", () => {
    const html = renderRows("legacy", [{ command: "a" }, { command: "b" }]);
    expect(html).toMatch(
      /data-move="-1" data-kind="legacy" data-index="0"[^>]*disabled/,
    );
    expect(html).toMatch(
      /data-move="1" data-kind="legacy" data-index="1"[^>]*disabled/,
    );
    expect(html).not.toMatch(
      /data-move="1" data-kind="legacy" data-index="0"[^>]*disabled/,
    );
  });

  test("escapes what the operator typed", () => {
    const html = renderRows("legacy", [
      { command: '"><img src=x>', parameters: "" },
    ]);
    expect(html).not.toContain("<img");
    expect(html).toContain("&quot;&gt;&lt;img src=x&gt;");
  });

  test("an empty list says so", () => {
    expect(renderRows("legacy", [])).toContain("No startup scripts.");
  });

  test("every kind names its file", () => {
    expect(KINDS.legacy.file).toBe("scripts.ini");
    expect(KINDS.powershell.file).toBe("psscripts.ini");
  });
});

describe("renderScriptsPreview", () => {
  const body = {
    backup_id: "{49227F28-6122-5999-8CEB-923CB9BF3001}",
    bundle_sha256: "ab".repeat(32),
    bundle_size: 2647,
    machine_extension_names: "[{42B5FAAE}{40B6664F}]",
    user_extension_names: "",
    files: [
      {
        path: "Machine/Scripts/scripts.ini",
        text: "\r\n[Startup]\r\n0CmdLine=<b>a.cmd</b>\r\n",
        sha256: "cd".repeat(32),
        size: 60,
      },
    ],
    issues: [
      {
        severity: "warning",
        code: "environment_variable_path",
        message: "parameters reference environment variables",
        path: "parameters",
      },
    ],
    limitations: [
      { code: "payload_not_carried", message: "No bodies." },
      { code: "execution_unmeasured", message: "Never run." },
    ],
  };

  test("limitations come before the file contents", () => {
    const html = renderScriptsPreview(body);
    expect(html.indexOf("What this answer does not say")).toBeLessThan(
      html.indexOf("Machine/Scripts/scripts.ini"),
    );
    expect(html).toContain(LIMITATION_SENTENCES.payload_not_carried);
  });

  test("shows the digest, the warnings, and escapes file text", () => {
    const html = renderScriptsPreview(body);
    expect(html).toContain(body.bundle_sha256);
    expect(html).toContain("environment_variable_path");
    expect(html).toContain("&lt;b&gt;a.cmd&lt;/b&gt;");
    expect(html).not.toContain("<b>a.cmd</b>");
  });

  test("no warnings renders nothing for them", () => {
    expect(renderIssues([])).toBe("");
  });
});

describe("filenameFrom", () => {
  test("reads the server's attachment name", () => {
    expect(
      filenameFrom('attachment; filename="X-gpmc-backup-scripts.zip"', "f.zip"),
    ).toBe("X-gpmc-backup-scripts.zip");
  });

  test("falls back when there is none", () => {
    expect(filenameFrom(null, "f.zip")).toBe("f.zip");
  });
});

describe("plain limitation sentences", () => {
  test.each([
    "payload_not_carried",
    "execution_unmeasured",
    "gpme_editing_unmeasured",
    "one_entry_shape_measured",
  ])("%s has a plain sentence", (code) => {
    expect(LIMITATION_SENTENCES[code]).toBeTruthy();
  });
});
