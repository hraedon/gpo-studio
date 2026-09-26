import { describe, expect, test } from "vitest";
import {
  MAX_FILE_BYTES,
  readNativeFile,
  renderComparison,
  renderDocument,
  validateFile,
} from "../../src/gpo_studio/static/js/folder-redirection.mjs";

describe("native file transport", () => {
  test("preserves UTF-16LE, BOM, CRLF and non-ASCII paths byte for byte", async () => {
    const bytes = Buffer.concat([
      Buffer.from([255, 254]),
      Buffer.from(
        "\r\n[version]\r\nversion=100\r\nFullPath=資料😀\r\n",
        "utf16le",
      ),
    ]);
    const file = new File([bytes], "fdeploy1.ini");
    expect(Buffer.from(await readNativeFile(file), "base64")).toEqual(bytes);
  });

  test("handles the full size ceiling without spreading a megabyte into arguments", async () => {
    const bytes = Buffer.alloc(MAX_FILE_BYTES, 255);
    const encoded = await readNativeFile(new File([bytes], "large.ini"));
    expect(Buffer.from(encoded, "base64")).toEqual(bytes);
  });

  test("rejects oversized files before reading them", async () => {
    let read = false;
    const file = {
      name: "large.ini",
      size: MAX_FILE_BYTES + 1,
      arrayBuffer() {
        read = true;
        throw new Error("Must not read");
      },
    };
    await expect(readNativeFile(file)).rejects.toThrow("1 MiB");
    expect(read).toBe(false);
  });

  test("distinguishes a missing selection from an empty file", () => {
    expect(() => validateFile(undefined)).toThrow("Choose a current file");
    expect(() => validateFile(new File([], "empty.ini"))).toThrow(
      "empty.ini: the file is empty",
    );
  });
});

const DOCUMENT = {
  version: "100",
  is_marker: false,
  redirections: [
    {
      folder_guid: "{unknown}",
      folder_name: null,
      principal: "s-1-1-0",
      full_path: "",
      flags_text: "not-an-integer",
    },
  ],
  folders: [{ guid: "{unknown}", principals: ["s-1-1-0"] }],
  parse_warnings: ["Unrecognised line"],
  validation: [
    {
      severity: "error",
      code: "missing_full_path",
      message: "Missing path",
      path: "fdeploy",
    },
  ],
  report_lines: ["Unrecognised section [extra]", "FutureOption: value"],
};

test("renders malformed native values, unknown GUIDs and diagnostics without guessing", () => {
  const html = renderDocument(DOCUMENT, "policy.ini");
  expect(html).toContain("Unrecognised folder GUID");
  expect(html).toContain("{unknown}");
  expect(html).toContain("not-an-integer");
  expect(html).toContain("Empty value");
  expect(html).toContain("Unrecognised line");
  expect(html).toContain("missing_full_path");
  expect(html).toContain("FutureOption: value");
  expect(html.indexOf("missing_full_path")).toBeLessThan(
    html.indexOf("<table>"),
  );
});

test("escapes file names, rules, diagnostics, folder maps and reports", () => {
  const hostile = '<img src=x onerror="alert(1)">';
  const body = {
    ...DOCUMENT,
    version: hostile,
    redirections: [
      {
        folder_guid: hostile,
        folder_name: hostile,
        principal: hostile,
        full_path: hostile,
        flags_text: hostile,
      },
    ],
    folders: [{ guid: hostile, principals: [hostile] }],
    parse_warnings: [hostile],
    validation: [
      { severity: hostile, code: hostile, message: hostile, path: hostile },
    ],
    report_lines: [hostile],
  };
  const html = renderDocument(body, hostile, hostile);
  expect(html).not.toContain("<img");
  expect(html).toContain("&lt;img");
});

test("an empty diff does not claim different file bytes are identical", () => {
  const html = renderComparison({ changes: [] }, false);
  expect(html).toContain("No redirection-section changes detected");
  expect(html).toContain("The file bytes differ");
  expect(html).toContain("outside this comparison");
  expect(html).not.toContain("byte-identical");
  expect(renderComparison({ changes: [] }, true)).toContain("byte-identical");
});

test("a diff distinguishes an absent rule from a present empty value", () => {
  const html = renderComparison(
    {
      changes: [
        {
          kind: "added",
          folder_guid: "{unknown}",
          folder_name: null,
          principal: "s-1-1-0",
          old_full_path: null,
          new_full_path: "",
          old_flags_text: null,
          new_flags_text: "0",
        },
      ],
    },
    false,
  );
  expect(html).toContain("Not present");
  expect(html).toContain("Empty value");
  expect(html).toContain('class="mono">0</dd>');
});

test("escapes every native value in a diff", () => {
  const hostile = "<script>alert(1)</script>";
  const html = renderComparison(
    {
      changes: [
        {
          kind: hostile,
          folder_guid: hostile,
          folder_name: hostile,
          principal: hostile,
          old_full_path: hostile,
          new_full_path: hostile,
          old_flags_text: hostile,
          new_flags_text: hostile,
        },
      ],
    },
    false,
  );
  expect(html).not.toContain("<script>");
  expect(html).toContain("&lt;script&gt;");
});
