import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

// A preview request is answered after the operator may have moved on. The
// panel must show a response only for the rows it was built from: a late
// response for rows that were edited, or for a dialog that was closed or
// reopened, would put one set of contents and digest beside a Download that
// builds from another. These tests drive the real handlers with a deferred
// response and a minimal DOM stand-in (vitest runs in node).

class FakeElement {
  constructor(id) {
    this.id = id;
    this.textContent = "";
    this.innerHTML = "";
    this.value = "";
    this.disabled = false;
    this.hidden = false;
    this.children = [];
    this.listeners = {};
    this.attributes = {};
  }
  addEventListener(type, handler) {
    (this.listeners[type] ||= []).push(handler);
  }
  dispatch(type, event = {}) {
    for (const handler of this.listeners[type] || []) handler(event);
  }
  querySelectorAll(selector) {
    return selector === "[data-close-scripts]" ? [closeButton] : [];
  }
  querySelector() {
    return null;
  }
  replaceChildren() {
    this.innerHTML = "";
    this.textContent = "";
  }
  setAttribute(name, value) {
    this.attributes[name] = value;
  }
  removeAttribute(name) {
    delete this.attributes[name];
  }
  focus() {}
  showModal() {
    this.open = true;
  }
  close() {
    this.open = false;
    this.dispatch("close");
  }
}

let elements;
let closeButton;

function el(selector) {
  if (!elements.has(selector))
    elements.set(selector, new FakeElement(selector));
  return elements.get(selector);
}

const PREVIEW = {
  backup_id: "{49227F28-6122-5999-8CEB-923CB9BF3001}",
  bundle_sha256: "ab".repeat(32),
  bundle_size: 2647,
  machine_extension_names: "[{42B5FAAE}{40B6664F}]",
  user_extension_names: "",
  files: [
    {
      path: "Machine/Scripts/scripts.ini",
      text: "\r\n[Startup]\r\n0CmdLine=old.cmd\r\n",
      sha256: "cd".repeat(32),
      size: 60,
    },
  ],
  issues: [],
  limitations: [],
};

function deferredFetch() {
  let resolve;
  const pending = new Promise((done) => {
    resolve = done;
  });
  const fetchMock = vi.fn(() => pending);
  vi.stubGlobal("fetch", fetchMock);
  return {
    fetchMock,
    respond: () =>
      resolve(
        new Response(JSON.stringify(PREVIEW), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
  };
}

function typeCommand(value) {
  const field = {
    value,
    dataset: { kind: "legacy", index: "0", field: "command" },
  };
  el("#scripts-form").dispatch("input", {
    target: { closest: () => field },
  });
}

let scripts;
let state;

beforeEach(async () => {
  vi.resetModules();
  elements = new Map();
  closeButton = new FakeElement("close");
  vi.stubGlobal("document", {
    querySelector: (selector) => el(selector),
    querySelectorAll: () => [],
  });
  scripts = await import("../../src/gpo_studio/static/js/scripts.mjs");
  ({ state } = await import("../../src/gpo_studio/static/js/state.mjs"));
  state.current = { guid: "synthetic-guid", name: "Synthetic policy" };
  state.artifactCapabilities = {};
  scripts.initScripts();
  el("#open-scripts").onclick();
  el("#scripts-add-legacy").onclick();
  typeCommand("old.cmd");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function startPreview() {
  const response = deferredFetch();
  const submitted = el("#scripts-form").onsubmit({ preventDefault() {} });
  expect(response.fetchMock).toHaveBeenCalledTimes(1);
  expect(el("#scripts-status").textContent).toBe("Building preview…");
  return { ...response, submitted };
}

describe("a preview response", () => {
  test("is shown when nothing changed while it was in flight", async () => {
    const { respond, submitted } = await startPreview();
    respond();
    await submitted;
    expect(el("#scripts-results").innerHTML).toContain(PREVIEW.bundle_sha256);
    expect(el("#scripts-status").textContent).toContain("Preview ready");
  });

  test.each([
    ["an edit", () => typeCommand("new.cmd")],
    ["adding a row", () => el("#scripts-add-powershell").onclick()],
    [
      "reopening",
      () => {
        el("#scripts-dialog").close();
        el("#open-scripts").onclick();
      },
    ],
    ["closing", () => closeButton.onclick()],
    ["closing with Escape", () => el("#scripts-dialog").close()],
  ])("is discarded after %s", async (_name, act) => {
    const { respond, submitted } = await startPreview();
    act();
    const statusAfterAct = el("#scripts-status").textContent;
    respond();
    await submitted;
    expect(el("#scripts-results").innerHTML).toBe("");
    expect(el("#scripts-results").innerHTML).not.toContain(
      PREVIEW.bundle_sha256,
    );
    expect(el("#scripts-status").textContent).toBe(statusAfterAct);
    expect(el("#scripts-status").textContent).not.toContain("Preview ready");
  });

  test("a refusal of edited rows is not reported either", async () => {
    let reject;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise((_, fail) => {
            reject = fail;
          }),
      ),
    );
    const submitted = el("#scripts-form").onsubmit({ preventDefault() {} });
    typeCommand("new.cmd");
    reject(new TypeError("network down"));
    await submitted;
    expect(el("#scripts-status").textContent).toBe("");
  });
});
