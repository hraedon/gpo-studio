import { afterEach, expect, test, vi } from "vitest";
import { state } from "../../src/gpo_studio/static/js/state.mjs";
import {
  api,
  clearPersistentError,
  showPersistentError,
} from "../../src/gpo_studio/static/js/api.mjs";
import {
  createReadScope,
  invalidateReadViews,
} from "../../src/gpo_studio/static/js/read-context.mjs";

afterEach(() => {
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
  test(`retires UI/error/finalizer ownership on ${retirement}`, () => {
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
  await api("/api/synthetic", { method: "POST" });
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
