import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

// theme.js is a classic head script with no exports; importing it runs the
// bootstrap against whatever globals are in place. Each test therefore builds
// a minimal browser, imports a fresh copy, and reads the effects back off the
// fakes.

function makeBrowser({
  stored = null,
  systemDark = false,
  storageDenied = false,
  legacyMedia = false,
} = {}) {
  const listeners = { media: [], dom: [], click: [], storage: [] };
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
  // Safari <14 ships only the deprecated addListener form, and a stub that
  // offers both would never exercise the fallback branch.
  const media = legacyMedia
    ? {
        matches: systemDark,
        addListener(handler) {
          listeners.media.push(handler);
        },
      }
    : {
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
    addEventListener(type, handler) {
      if (type === "storage") listeners.storage.push(handler);
    },
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
    // What another tab writing localStorage looks like to this one. The real
    // event never fires in the tab that made the change.
    storageEvent(key, newValue) {
      listeners.storage.forEach((handler) => handler({ key, newValue }));
    },
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

  test("an explicit choice in another tab reaches this one", async () => {
    const browser = await boot({ systemDark: false });
    browser.domReady();
    expect(browser.toggle.textContent).toBe("Auto");

    browser.storageEvent("gpo-studio-theme", "dark");

    expect(browser.documentStub.documentElement.dataset.theme).toBe("dark");
    // The button must not keep advertising the mode this tab has left.
    expect(browser.toggle.textContent).toBe("Dark");
  });

  test("another tab clearing the choice returns this one to auto", async () => {
    const browser = await boot({ stored: "light", systemDark: true });
    browser.domReady();

    browser.storageEvent("gpo-studio-theme", null);

    expect(browser.documentStub.documentElement.dataset.theme).toBe("dark");
    expect(browser.toggle.textContent).toBe("Auto");
    // Back on auto, this tab must resume following the system.
    browser.systemChange(false);
    expect(browser.documentStub.documentElement.dataset.theme).toBe("light");
  });

  test("an unrelated storage key leaves the theme alone", async () => {
    const browser = await boot({ stored: "light", systemDark: true });
    browser.domReady();

    browser.storageEvent("some-other-key", "dark");

    expect(browser.documentStub.documentElement.dataset.theme).toBe("light");
    expect(browser.toggle.textContent).toBe("Light");
  });

  test("auto follows the system on engines with only addListener", async () => {
    const browser = await boot({ systemDark: true, legacyMedia: true });

    expect(browser.documentStub.documentElement.dataset.theme).toBe("dark");
    browser.systemChange(false);
    expect(browser.documentStub.documentElement.dataset.theme).toBe("light");
  });
});
