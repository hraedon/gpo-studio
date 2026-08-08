import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

// theme.js is a classic head script with no exports; importing it runs the
// bootstrap against whatever globals are in place. Each test therefore builds
// a minimal browser, imports a fresh copy, and reads the effects back off the
// fakes.

function makeBrowser({
  stored = null,
  systemDark = false,
  storageDenied = false,
} = {}) {
  const listeners = { media: [], dom: [], click: [] };
  const toggle = {
    textContent: "",
    attributes: {},
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
    addEventListener(type, handler) {
      if (type === "click") listeners.click.push(handler);
    },
  };
  const documentStub = {
    documentElement: { dataset: {} },
    addEventListener(type, handler) {
      if (type === "DOMContentLoaded") listeners.dom.push(handler);
    },
    getElementById(id) {
      return id === "theme-toggle" ? toggle : null;
    },
  };
  const media = {
    matches: systemDark,
    addEventListener(type, handler) {
      if (type === "change") listeners.media.push(handler);
    },
  };
  const storage = new Map(
    stored === null ? [] : [["gpo-studio-theme", stored]],
  );
  const denied = () => {
    throw new Error("denied");
  };
  const windowStub = {
    matchMedia: () => media,
    localStorage: storageDenied
      ? { getItem: denied, setItem: denied }
      : {
          getItem: (key) => (storage.has(key) ? storage.get(key) : null),
          setItem: (key, value) => storage.set(key, value),
        },
  };
  vi.stubGlobal("window", windowStub);
  vi.stubGlobal("document", documentStub);
  return {
    documentStub,
    toggle,
    media,
    storage,
    domReady: () => listeners.dom.forEach((handler) => handler()),
    clickToggle: () => listeners.click.forEach((handler) => handler()),
    systemChange(dark) {
      media.matches = dark;
      listeners.media.forEach((handler) => handler());
    },
  };
}

async function boot(options) {
  const browser = makeBrowser(options);
  await import("../../src/gpo_studio/static/js/theme.js");
  return browser;
}

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("theme bootstrap", () => {
  test("defaults to the system preference and follows its changes", async () => {
    const browser = await boot({ systemDark: true });

    expect(browser.documentStub.documentElement.dataset.theme).toBe("dark");
    browser.systemChange(false);
    expect(browser.documentStub.documentElement.dataset.theme).toBe("light");
  });

  test("a stored explicit mode wins over the system preference", async () => {
    const browser = await boot({ stored: "light", systemDark: true });

    expect(browser.documentStub.documentElement.dataset.theme).toBe("light");
    // Explicit modes must not follow the OS.
    browser.systemChange(true);
    expect(browser.documentStub.documentElement.dataset.theme).toBe("light");
  });

  test("an unrecognised stored value falls back to auto rather than throwing", async () => {
    const browser = await boot({ stored: "solarized", systemDark: false });

    expect(browser.documentStub.documentElement.dataset.theme).toBe("light");
  });

  test("the toggle cycles auto → dark → light and persists each choice", async () => {
    const browser = await boot({ systemDark: false });
    browser.domReady();

    expect(browser.toggle.textContent).toBe("Auto");
    browser.clickToggle();
    expect(browser.documentStub.documentElement.dataset.theme).toBe("dark");
    expect(browser.storage.get("gpo-studio-theme")).toBe("dark");
    expect(browser.toggle.textContent).toBe("Dark");
    browser.clickToggle();
    expect(browser.documentStub.documentElement.dataset.theme).toBe("light");
    expect(browser.storage.get("gpo-studio-theme")).toBe("light");
    browser.clickToggle();
    expect(browser.storage.get("gpo-studio-theme")).toBe("auto");
    expect(browser.toggle.textContent).toBe("Auto");
  });

  test("denied storage still applies the theme for this page", async () => {
    const browser = await boot({ systemDark: true, storageDenied: true });

    expect(browser.documentStub.documentElement.dataset.theme).toBe("dark");
    browser.domReady();
    browser.clickToggle();
    expect(browser.documentStub.documentElement.dataset.theme).toBe("dark");
    expect(browser.toggle.textContent).toBe("Dark");
  });
});
