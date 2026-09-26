import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

const fixtures = new URL(
  "../fixtures/native-folder-redirection-gpmc/",
  import.meta.url,
);
const provenance = JSON.parse(
  readFileSync(new URL("provenance.json", fixtures), "utf8"),
);
const GUID = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}";
const PARSE = "/api/folder-redirection/fdeploy";

function nativeBytes(text) {
  return Buffer.concat([Buffer.from([255, 254]), Buffer.from(text, "utf16le")]);
}

function capture(name) {
  const transcript = readFileSync(new URL(name, fixtures), "utf8");
  const bytes = nativeBytes(transcript.split("\n").slice(2).join("\r\n"));
  // Browser tests consume the real banked capture, reconstructed with the
  // same hash check as the Python capture tests, never a lookalike fixture.
  expect(createHash("sha256").update(bytes).digest("hex")).toBe(
    provenance.files[name].raw_sha256,
  );
  return bytes;
}

const NATIVE = capture("fdeploy1.ini.txt");
const TEXT = NATIVE.subarray(2).toString("utf16le");
const upload = (buffer, name = "fdeploy1.ini") => ({
  name,
  mimeType: "application/octet-stream",
  buffer,
});

async function openReview(page) {
  await page.goto("/");
  await page
    .getByRole("button", { name: "Folder Redirection", exact: true })
    .click();
  return page.locator("#folder-redirection-form");
}

test("reviews native R3 bytes and exposes the evidence limits @smoke", async ({
  page,
}) => {
  const form = await openReview(page);
  await form
    .getByLabel("Current file", { exact: true })
    .setInputFiles(upload(NATIVE));
  const request = page.waitForRequest((request) =>
    request.url().endsWith(PARSE),
  );
  await form.getByRole("button", { name: "Review files" }).click();
  expect(
    Buffer.from((await request).postDataJSON().content_base64, "base64"),
  ).toEqual(NATIVE);
  const results = page.locator("#folder-redirection-results");
  await expect(
    results.getByRole("cell", { name: "1021", exact: true }),
  ).toBeVisible();
  await expect(results).toContainText("Documents");
  await expect(results).toContainText("s-1-1-0");
  await expect(results).toContainText(
    "\\\\zz-studio-fileserver\\zzredir\\%USERNAME%\\Documents",
  );
  await expect(results).toContainText("flags_not_decoded");
  await expect(results).toContainText("single_capture_only");
  const html = await results.innerHTML();
  expect(html.indexOf("single_capture_only")).toBeLessThan(
    html.indexOf("<table>"),
  );
  await expect(page.locator("#folder-redirection-status")).toContainText(
    "File review ready",
  );
});

test("compares earlier to current and preserves raw flags", async ({
  page,
}) => {
  const form = await openReview(page);
  const current = nativeBytes(
    TEXT.replace("Flags=1021", "Flags=77").replace("zzredir", "new-share"),
  );
  await form
    .getByLabel("Current file", { exact: true })
    .setInputFiles(upload(current, "current.ini"));
  await form
    .getByLabel("Earlier file (optional)")
    .setInputFiles(upload(NATIVE, "earlier.ini"));
  await form.getByRole("button", { name: "Review files" }).click();
  const comparison = page.locator(".fdeploy-comparison");
  await expect(comparison).toContainText("modified");
  await expect(comparison).toContainText("1021");
  await expect(
    comparison.locator("dd").filter({ hasText: /^77$/ }),
  ).toBeVisible();
  await expect(comparison).toContainText("new-share");
  await expect(page.locator(".fdeploy-document")).toHaveCount(2);
});

test("different versions with no rule changes never imply identical files", async ({
  page,
}) => {
  const form = await openReview(page);
  await form
    .getByLabel("Current file", { exact: true })
    .setInputFiles(
      upload(nativeBytes(TEXT.replace("version=100", "version=101"))),
    );
  await form
    .getByLabel("Earlier file (optional)")
    .setInputFiles(upload(NATIVE));
  await form.getByRole("button", { name: "Review files" }).click();
  const comparison = page.locator(".fdeploy-comparison");
  await expect(comparison).toContainText(
    "No redirection-section changes detected",
  );
  await expect(comparison).toContainText("The file bytes differ");
  await expect(page.locator(".fdeploy-document").last()).toContainText(
    "Version: 101",
  );
});

test("comparison exposes duplicate-section diagnostics from both files", async ({
  page,
}) => {
  const form = await openReview(page);
  const repeated = nativeBytes(
    TEXT +
      `[${GUID}_s-1-1-0]\r\nFlags=1021\r\nFullPath=\\\\synthetic.test\\share\r\n`,
  );
  await form
    .getByLabel("Current file", { exact: true })
    .setInputFiles(upload(repeated));
  await form
    .getByLabel("Earlier file (optional)")
    .setInputFiles(upload(repeated));
  await form.getByRole("button", { name: "Review files" }).click();
  await expect(page.locator(".fdeploy-comparison")).toContainText(
    "byte-identical",
  );
  const documents = page.locator(".fdeploy-document");
  await expect(documents).toHaveCount(2);
  await expect(documents.first()).toContainText("duplicate_redirection");
  await expect(documents.last()).toContainText("duplicate_redirection");
});

test("recognises the native empty marker", async ({ page }) => {
  const form = await openReview(page);
  await form
    .getByLabel("Current file", { exact: true })
    .setInputFiles(upload(capture("fdeploy.ini.txt"), "fdeploy.ini"));
  await form.getByRole("button", { name: "Review files" }).click();
  await expect(page.locator(".fdeploy-document")).toContainText(
    "Empty marker file",
  );
  await expect(page.locator(".fdeploy-document")).toContainText(
    "0 redirection section(s)",
  );
});

test("rejects invalid encoding and allows correction without stale results", async ({
  page,
}) => {
  const form = await openReview(page);
  await form
    .getByLabel("Current file", { exact: true })
    .setInputFiles(upload(NATIVE));
  await form.getByRole("button", { name: "Review files" }).click();
  await expect(page.locator(".fdeploy-document")).toBeVisible();
  await form
    .getByLabel("Current file", { exact: true })
    .setInputFiles(upload(Buffer.from(TEXT), "bad.ini"));
  await expect(page.locator("#folder-redirection-results")).toBeEmpty();
  await form.getByRole("button", { name: "Review files" }).click();
  await expect(form.locator(".form-error")).toContainText(
    "Current file (bad.ini)",
  );
  await expect(form.locator(".form-error")).toContainText("UTF-16LE");
  await expect(
    form.getByRole("button", { name: "Review files" }),
  ).toBeEnabled();
  await form
    .getByLabel("Current file", { exact: true })
    .setInputFiles(upload(NATIVE));
  await form.getByRole("button", { name: "Review files" }).click();
  await expect(page.locator(".fdeploy-document")).toBeVisible();
  await expect(form.locator(".form-error")).toHaveCount(0);
});

test("checks both file sizes before sending any request", async ({ page }) => {
  const requests = [];
  page.on("request", (request) => {
    if (request.url().includes(PARSE)) requests.push(request);
  });
  const form = await openReview(page);
  await form
    .getByLabel("Current file", { exact: true })
    .setInputFiles(upload(NATIVE));
  await form
    .getByLabel("Earlier file (optional)")
    .setInputFiles(upload(Buffer.alloc(1024 * 1024 + 1), "large.ini"));
  await form.getByRole("button", { name: "Review files" }).click();
  await expect(form.locator(".form-error")).toContainText(
    "large.ini: the file exceeds the 1 MiB",
  );
  expect(requests).toHaveLength(0);
});

for (const reopen of [false, true]) {
  test(`late responses cannot overwrite ${reopen ? "a reopened dialog" : "a changed selection"}`, async ({
    page,
  }) => {
    const form = await openReview(page);
    let release;
    let intercepted;
    const ready = new Promise((resolve) => {
      intercepted = resolve;
    });
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    await page.route(
      `**${PARSE}`,
      async (route) => {
        const response = await route.fetch();
        intercepted();
        await gate;
        await route.fulfill({ response });
      },
      { times: 1 },
    );
    await form
      .getByLabel("Current file", { exact: true })
      .setInputFiles(upload(NATIVE, "old-selection.ini"));
    await form.getByRole("button", { name: "Review files" }).click();
    await ready;
    await expect(
      form.getByRole("button", { name: "Review files" }),
    ).toBeDisabled();
    await form
      .getByLabel("Current file", { exact: true })
      .setInputFiles(upload(capture("fdeploy.ini.txt"), "marker.ini"));
    await form.getByRole("button", { name: "Review files" }).click();
    await expect(page.locator(".fdeploy-document")).toContainText(
      "Empty marker file",
    );
    if (reopen) {
      await form
        .getByRole("button", { name: "Close", exact: true })
        .first()
        .click();
      await page
        .getByRole("button", { name: "Folder Redirection", exact: true })
        .click();
    }
    const completed = page.waitForResponse((response) =>
      response.url().endsWith(PARSE),
    );
    release();
    await (await completed).finished();
    // Flush the response body's client continuation without a timing sleep.
    await page.evaluate(
      () =>
        new Promise((resolve) =>
          globalThis.requestAnimationFrame(() =>
            globalThis.requestAnimationFrame(resolve),
          ),
        ),
    );
    if (reopen) {
      await expect(page.locator("#folder-redirection-results")).toBeEmpty();
      await expect(
        form.getByLabel("Current file", { exact: true }),
      ).toHaveValue("");
    } else {
      await expect(page.locator(".fdeploy-document")).toContainText(
        "Empty marker file",
      );
      await expect(page.locator(".fdeploy-document")).not.toContainText(
        "old-selection.ini",
      );
    }
    await expect(
      form.getByRole("button", { name: "Review files" }),
    ).toBeEnabled();
  });
}

test("native content is displayed as text and the populated dialog is accessible", async ({
  page,
}) => {
  const form = await openReview(page);
  const hostile = '<img src=x onerror="document.body.dataset.injected=1">';
  const bytes = nativeBytes(TEXT.replace("zzredir", hostile));
  await form
    .getByLabel("Current file", { exact: true })
    .setInputFiles(upload(bytes));
  await form.getByRole("button", { name: "Review files" }).click();
  const results = page.locator("#folder-redirection-results");
  await expect(results).toContainText(hostile);
  await expect(results.locator("img")).toHaveCount(0);
  await expect(page.locator("body")).not.toHaveAttribute("data-injected", "1");
  const audit = await new AxeBuilder({ page }).analyze();
  expect(
    audit.violations.filter((violation) =>
      ["serious", "critical"].includes(violation.impact),
    ),
  ).toEqual([]);
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "Folder Redirection", exact: true }),
  ).toBeFocused();
});

test("closing an empty form is not blocked by its required file field", async ({
  page,
}) => {
  const form = await openReview(page);
  await form.getByRole("button", { name: "Close", exact: true }).last().click();
  await expect(page.locator("#folder-redirection-dialog")).not.toBeVisible();
});

test("an invalid earlier file is named and never produces a misleading comparison", async ({
  page,
}) => {
  const form = await openReview(page);
  await form
    .getByLabel("Current file", { exact: true })
    .setInputFiles(upload(NATIVE));
  await form
    .getByLabel("Earlier file (optional)")
    .setInputFiles(upload(Buffer.from("invalid"), "broken-earlier.ini"));
  await form.getByRole("button", { name: "Review files" }).click();
  await expect(form.locator(".form-error")).toContainText(
    "Earlier file (broken-earlier.ini)",
  );
  await expect(page.locator("#folder-redirection-results")).toBeEmpty();
  await expect(
    form.getByRole("button", { name: "Review files" }),
  ).toBeEnabled();
});

test("long paths keep the narrow dialog usable", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const form = await openReview(page);
  const longPath = TEXT.replace("zzredir", "synthetic-share-".repeat(15));
  await form
    .getByLabel("Current file", { exact: true })
    .setInputFiles(upload(nativeBytes(longPath)));
  await form
    .getByLabel("Earlier file (optional)")
    .setInputFiles(upload(NATIVE));
  await form.getByRole("button", { name: "Review files" }).click();
  await expect(page.locator(".fdeploy-comparison")).toContainText("modified");
  expect(
    await form.evaluate(
      (element) => element.scrollWidth <= element.clientWidth + 1,
    ),
  ).toBe(true);
  await page.locator(".fdeploy-comparison").scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("narrow-review.png") });
  await form.getByRole("button", { name: "Close", exact: true }).last().click();
  await expect(page.locator("#folder-redirection-dialog")).not.toBeVisible();
});
