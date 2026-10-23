import { afterEach, expect, test, vi } from "vitest";
import { state } from "../../src/gpo_studio/static/js/state.mjs";
import {
  actionApi,
  clearPersistentError,
  showPersistentError,
} from "../../src/gpo_studio/static/js/api.mjs";
import {
  createReadScope,
  invalidateReadViews,
  readDialogOpened,
  readDialogClosed,
  resumeReadViews,
} from "../../src/gpo_studio/static/js/read-context.mjs";

afterEach(() => {
  invalidateReadViews();
  vi.unstubAllGlobals();
  state.editorGeneration = 0;
});
function setup() {
  const summary = { hidden: true, focus: vi.fn() };
  vi.stubGlobal("document", { querySelector: () => summary });
  const dialog = { open: true, editorSession: Symbol() };
  const owner = {
    hidden: false,
    isConnected: true,
    closest: (selector) => (selector === "dialog" ? dialog : null),
  };
  const sink = vi.fn(),
    scope = createReadScope({ element: () => owner });
  clearPersistentError();
  return { owner, dialog, sink, scope };
}
test("current failures reach the chosen sink as the original error", () => {
  const { scope, sink } = setup(),
    error = new Error("current");
  const read = scope.start({ report: sink });
  expect(read.reportError(error)).toBe(true);
  expect(sink).toHaveBeenCalledWith(error);
});
for (const retirement of [
  "intent",
  "editor",
  "hidden",
  "detached",
  "closed",
  "reopened",
  "domain",
  "tab",
])
  test(`retires publication/error ownership on ${retirement}`, () => {
    const { scope, sink, owner, dialog } = setup();
    let domain = true;
    const read = scope.capture({ isCurrent: () => domain, report: sink });
    if (retirement === "intent") scope.invalidate();
    if (retirement === "editor") state.editorGeneration++;
    if (retirement === "hidden") owner.hidden = true;
    if (retirement === "detached") owner.isConnected = false;
    if (retirement === "closed") dialog.open = false;
    if (retirement === "reopened") dialog.editorSession = Symbol();
    if (retirement === "domain") domain = false;
    if (retirement === "tab") invalidateReadViews();
    expect(read.isCurrent()).toBe(false);
    expect(read.reportError(new Error("late"))).toBe(false);
    expect(sink).not.toHaveBeenCalled();
  });
for (const retirement of ["warning", "dismiss"])
  test(`${retirement} retires error/focus only, retaining successful data ownership`, () => {
    const { scope, sink } = setup(),
      read = scope.capture({ report: sink });
    if (retirement === "warning") showPersistentError("new warning");
    else clearPersistentError();
    expect(read.isCurrent()).toBe(true);
    expect(read.reportError(new Error("late"))).toBe(false);
    expect(sink).not.toHaveBeenCalled();
  });
test("automatic successful-action resolution preserves the current read error lease", async () => {
  const { scope, sink } = setup();
  showPersistentError("prior warning");
  const read = scope.capture({ report: sink });
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response("{}", { headers: { "Content-Type": "application/json" } }),
      ),
  );
  await actionApi("/api/synthetic", { method: "POST" });
  expect(read.reportError(new Error("current"))).toBe(true);
  expect(sink).toHaveBeenCalledOnce();
});

test("replacing an owner body retires its continuation even when the replacement has the same ID", () => {
  const original = { hidden: false, isConnected: true },
    replacement = { hidden: false, isConnected: true };
  let owner = original;
  const scope = createReadScope({ element: () => owner }),
    sink = vi.fn();
  const read = scope.capture({ report: sink });
  owner = replacement;
  expect(read.isCurrent()).toBe(false);
  expect(read.reportError(new Error("retired body"))).toBe(false);
  expect(sink).not.toHaveBeenCalled();
});

function pendingUI() {
  const attributes = new Map(),
    children = [];
  const element = {
    getAttribute: (name) => attributes.get(name) ?? null,
    setAttribute: (name, value) => attributes.set(name, value),
    removeAttribute: (name) => attributes.delete(name),
    replaceChildren(...nodes) {
      children.forEach((node) => {
        node.parentNode = null;
      });
      children.splice(0, children.length, ...nodes);
      nodes.forEach((node) => {
        node.parentNode = element;
      });
    },
    get textContent() {
      return children.map((node) => node.textContent).join("");
    },
  };
  element.ownerDocument = {
    createTextNode: (text) => ({
      textContent: text,
      parentNode: null,
      remove() {
        const at = children.indexOf(this);
        if (at >= 0) children.splice(at, 1);
        this.parentNode = null;
      },
    }),
    createElement: () => element.ownerDocument.createTextNode(""),
  };
  const control = { disabled: false };
  const options = {
    controls: [control],
    busy: [element],
    loading: [{ element, message: "Loading first", recovery: "Run again" }],
  };
  return { element, control, options };
}
for (const retirement of [
  "intent",
  "editor",
  "hidden",
  "closed",
  "domain",
  "tab",
])
  test(`pending resources release after ${retirement} without requiring result ownership`, () => {
    const { scope, owner, dialog } = setup(),
      ui = pendingUI();
    let domain = true;
    const read = scope.start({ isCurrent: () => domain }),
      pending = read.beginPending(ui.options);
    expect(ui.control.disabled).toBe(true);
    expect(ui.element.getAttribute("aria-busy")).toBe("true");
    if (retirement === "intent") scope.invalidate();
    if (retirement === "editor") state.editorGeneration++;
    if (retirement === "hidden") owner.hidden = true;
    if (retirement === "closed") dialog.open = false;
    if (retirement === "reopened") dialog.editorSession = Symbol();
    if (retirement === "domain") domain = false;
    if (retirement === "tab") invalidateReadViews();
    expect(read.isCurrent()).toBe(false);
    expect(pending.message(ui.element, "Old progress")).toBe(false);
    pending.finish();
    expect(ui.control.disabled).toBe(false);
    expect(ui.element.getAttribute("aria-busy")).toBe(null);
    expect(ui.element.textContent).toBe("Run again");
  });
test("old pending completion/progress cannot unlock or replace a successor", () => {
  const { scope } = setup(),
    ui = pendingUI(),
    first = scope.start().beginPending(ui.options);
  const second = scope.start().beginPending({
    ...ui.options,
    loading: [
      {
        element: ui.element,
        message: "Loading second",
        recovery: "Retry second",
      },
    ],
  });
  expect(first.finish()).toBe(false);
  expect(first.message(ui.element, "Old progress")).toBe(false);
  expect(ui.control.disabled).toBe(true);
  expect(ui.element.textContent).toBe("Loading second");
  second.finish();
  expect(ui.control.disabled).toBe(false);
  expect(ui.element.textContent).toBe("Retry second");
});
test.each(["Current result", "Current error", "New view reset"])(
  "pending cleanup preserves replaced content: %s",
  (content) => {
    const { scope } = setup(),
      ui = pendingUI(),
      pending = scope.start().beginPending(ui.options);
    ui.element.replaceChildren(
      ui.element.ownerDocument.createTextNode(content),
    );
    pending.finish();
    expect(ui.element.textContent).toBe(content);
    expect(ui.control.disabled).toBe(false);
  },
);
test("suppressed error publication still recovers its exact loading indicator", () => {
  const { scope, sink } = setup(),
    ui = pendingUI(),
    read = scope.start({ report: sink }),
    pending = read.beginPending(ui.options);
  showPersistentError("Newer warning");
  expect(read.reportError(new Error("old"))).toBe(false);
  pending.finish();
  expect(ui.element.textContent).toBe("Run again");
  expect(sink).not.toHaveBeenCalled();
});
test("a retired read cannot acquire newer controls and original flags are preserved", () => {
  const { scope } = setup(),
    ui = pendingUI(),
    read = scope.start();
  scope.invalidate();
  expect(read.beginPending(ui.options).finish()).toBe(false);
  expect(ui.control.disabled).toBe(false);
  ui.control.disabled = true;
  ui.element.setAttribute("aria-busy", "false");
  const pending = scope.start().beginPending(ui.options);
  pending.finish();
  expect(ui.control.disabled).toBe(true);
  expect(ui.element.getAttribute("aria-busy")).toBe("false");
});

test("a replaced dialog opening keeps its new resource flags/content", () => {
  const { scope, dialog } = setup(),
    ui = pendingUI(),
    pending = scope.start().beginPending(ui.options);
  dialog.editorSession = Symbol("new opening");
  ui.control.disabled = true;
  ui.element.setAttribute("aria-busy", "true");
  ui.element.replaceChildren(
    ui.element.ownerDocument.createTextNode("New opening status"),
  );
  expect(pending.finish()).toBe(false);
  expect(ui.control.disabled).toBe(true);
  expect(ui.element.getAttribute("aria-busy")).toBe("true");
  expect(ui.element.textContent).toBe("New opening status");
});

test("reactivation waits for the covering dialog, preserves the original error lease, and has one read-only budget", async () => {
  const { owner, dialog, sink } = setup(),
    calls = [];
  let resumed;
  const scope = createReadScope({
    element: () => owner,
    reactivate: (options) => {
      calls.push(options);
      resumed = scope.start({ ...options, report: sink });
    },
  });
  readDialogOpened(dialog);
  const read = scope.start({ report: sink });
  const overlay = { open: true };
  readDialogOpened(overlay);
  state.editorGeneration++;
  showPersistentError("newer warning");
  read.complete();
  expect(await resumeReadViews()).toBe(0);
  overlay.open = false;
  readDialogClosed();
  expect(await resumeReadViews()).toBe(1);
  expect(calls).toHaveLength(1);
  expect(calls[0]).toMatchObject({
    recoveryBudget: 0,
    resume: true,
    errorAuthority: read.errorAuthority,
  });
  expect(resumed.isCurrent()).toBe(true);
  expect(resumed.reportError(new Error("automatic failure"))).toBe(false);
  expect(sink).not.toHaveBeenCalled();
  state.editorGeneration++;
  resumed.complete();
  expect(await resumeReadViews()).toBe(0);
  // An explicit retry, unlike automatic recovery, can acquire current authority.
  expect(
    scope.start({ report: sink }).reportError(new Error("explicit failure")),
  ).toBe(true);
  dialog.open = false;
  readDialogClosed();
});
for (const retirement of [
  "intent",
  "newer-read",
  "hidden",
  "domain",
  "reopened",
])
  test(`queued reactivation cannot cross ${retirement}`, async () => {
    const { owner, dialog } = setup(),
      run = vi.fn();
    let domain = true;
    const scope = createReadScope({ element: () => owner, reactivate: run });
    const read = scope.start({ isCurrent: () => domain });
    state.editorGeneration++;
    read.complete();
    if (retirement === "intent") scope.cancelReactivation();
    if (retirement === "newer-read") scope.capture();
    if (retirement === "hidden") owner.hidden = true;
    if (retirement === "domain") domain = false;
    if (retirement === "reopened") dialog.editorSession = Symbol();
    expect(await resumeReadViews()).toBe(0);
    expect(run).not.toHaveBeenCalled();
  });
test("sibling read chains for one view schedule only the latest recovery", async () => {
  const { owner } = setup(),
    first = vi.fn(),
    second = vi.fn();
  const a = createReadScope({
      element: () => owner,
      reactivate: first,
    }).start(),
    b = createReadScope({ element: () => owner, reactivate: second }).start();
  state.editorGeneration++;
  a.complete();
  b.complete();
  b.complete();
  expect(await resumeReadViews()).toBe(1);
  expect(first).not.toHaveBeenCalled();
  expect(second).toHaveBeenCalledOnce();
});
