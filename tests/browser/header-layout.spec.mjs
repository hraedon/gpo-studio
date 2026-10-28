import { expect, test } from "@playwright/test";

async function expectHeaderInsideViewport(page) {
  const layout = await page.evaluate(() => {
    const doc = globalThis.document;
    const box = (element) => {
      const rect = element.getBoundingClientRect();
      return {
        left: rect.left,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom,
      };
    };
    return {
      viewport: globalThis.innerWidth,
      documentWidth: doc.documentElement.scrollWidth,
      title: box(doc.querySelector("#title")),
      actions: [...doc.querySelectorAll("#top-actions > *")].map((element) => {
        const range = doc.createRange();
        range.selectNodeContents(element);
        return {
          text: element.textContent.trim(),
          ...box(element),
          textBoxes: [...range.getClientRects()].map((rect) => ({
            left: rect.left,
            right: rect.right,
          })),
        };
      }),
    };
  });
  expect(layout.documentWidth, JSON.stringify(layout)).toBeLessThanOrEqual(
    layout.viewport,
  );
  expect(layout.title.left).toBeGreaterThanOrEqual(0);
  expect(layout.title.right).toBeLessThanOrEqual(layout.viewport);
  expect(layout.actions.map((action) => action.text)).toEqual([
    "Revision 1",
    "Policy report",
    "Review PowerShell",
    "GPMC backup",
    "Publication preview",
    "Export bundle",
  ]);
  for (const action of layout.actions) {
    expect(action.left, action.text).toBeGreaterThanOrEqual(0);
    expect(action.right, action.text).toBeLessThanOrEqual(layout.viewport);
    expect(
      action.bottom <= layout.title.top ||
        action.top >= layout.title.bottom ||
        action.right <= layout.title.left ||
        action.left >= layout.title.right,
      `Title overlaps ${action.text}`,
    ).toBe(true);
    for (const text of action.textBoxes) {
      expect(text.left, action.text).toBeGreaterThanOrEqual(action.left - 1);
      expect(text.right, action.text).toBeLessThanOrEqual(action.right + 1);
    }
  }
}

for (const theme of ["light", "dark"]) {
  for (const width of [1280, 360, 850, 851]) {
    const smoke = theme === "light" && width === 1280 ? " @smoke" : "";
    test(`wraps full header labels in ${theme} at ${width}px${smoke}`, async ({
      page,
      request,
    }, testInfo) => {
      await page.setViewportSize({ width, height: width === 360 ? 640 : 800 });
      await page.emulateMedia({ colorScheme: theme });
      const suffix = `${testInfo.project.name}-${theme}-${width}-${Date.now()}`;
      const names = [
        `Synthetic ${suffix}`,
        `Synthetic workstation security baseline ${suffix}`,
        `${suffix}Synthetic${"UnspacedPolicy".repeat(30)}`.slice(0, 255),
      ];
      const writes = [];
      page.on("request", (request) => {
        if (!["GET", "HEAD"].includes(request.method()))
          writes.push(`${request.method()} ${request.url()}`);
      });
      let lastGpo;
      for (const name of names) {
        const created = await request.post("/api/gpos", {
          data: {
            name,
            actor: "browser-test",
            reason: "Synthetic header layout control",
          },
        });
        expect(created.status()).toBe(201);
        const { gpo } = await created.json();
        lastGpo = gpo;
        await page.goto("/");
        await page.locator(`[data-guid="${gpo.guid}"]`).click();
        await expect(page.locator("#title")).toHaveText(name);
        await expectHeaderInsideViewport(page);
        // A user-style text-size control, without claiming browser zoom or
        // assistive-technology qualification. No fixed widths or clipped labels.
        await page.addStyleTag({
          content:
            "body, button, .button { font-size: 18px !important; } h1 { font-size: 36px !important; }",
        });
        await expectHeaderInsideViewport(page);
      }
      // The full tab strip remains intentionally scrollable inside its box.
      const tabs = page.locator(".tabs");
      expect(
        await tabs.evaluate(
          (element) => element.scrollWidth > element.clientWidth,
        ),
      ).toBe(true);
      await page.locator("#tab-overview").focus();
      await page.keyboard.press("End");
      await expect(page.locator("#tab-admx")).toBeFocused();
      await expect(page.locator("#tab-admx")).toBeInViewport();
      await page.keyboard.press("Home");
      await expect(page.locator("#tab-overview")).toBeFocused();

      const added = await request.post(`/api/gpos/${lastGpo.guid}/settings`, {
        data: {
          expected_revision: 1,
          actor: "browser-test",
          reason: "Synthetic nested table scroll control",
          setting: {
            side: "computer",
            hive: "HKLM",
            key: "Software\\Policies\\Synthetic\\HeaderLayout",
            value_name: `SyntheticValue${"LongName".repeat(24)}`,
            registry_type: "REG_SZ",
            value: "Synthetic retained value",
            action: "set",
          },
        },
      });
      expect(added.status()).toBe(201);
      await page.goto("/");
      await page.locator(`[data-guid="${lastGpo.guid}"]`).click();
      await page.locator("#tab-settings").click();
      await expect(page.locator("#settings-table tr")).toHaveCount(1);
      const card = page.locator("#panel-settings .table-card");
      await card.scrollIntoViewIfNeeded();
      const scrolling = await card.evaluate((element) => {
        element.scrollLeft = element.scrollWidth;
        return {
          clientWidth: element.clientWidth,
          scrollWidth: element.scrollWidth,
          scrollLeft: element.scrollLeft,
          documentWidth: globalThis.document.documentElement.scrollWidth,
          viewport: globalThis.innerWidth,
        };
      });
      expect(scrolling.scrollWidth).toBeGreaterThan(scrolling.clientWidth);
      expect(scrolling.scrollLeft).toBeGreaterThan(0);
      expect(scrolling.documentWidth).toBeLessThanOrEqual(scrolling.viewport);
      await expect(page.locator("[data-edit-setting]")).toBeInViewport();
      await page.locator("[data-edit-setting]").click();
      await expect(
        page.locator("#setting-form [name='value_name']"),
      ).toHaveValue(`SyntheticValue${"LongName".repeat(24)}`);
      await page.keyboard.press("Escape");
      expect(writes).toEqual([]);
      const persisted = await (
        await request.get(`/api/gpos/${lastGpo.guid}`)
      ).json();
      expect(persisted.gpo.revision).toBe(2);
      expect(persisted.gpo.settings).toHaveLength(1);
    });
  }
}
