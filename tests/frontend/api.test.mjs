import { afterEach, describe, expect, test, vi } from "vitest";

import {
  api,
  audit,
  showPersistentError,
  clearPersistentError,
  captureErrorReporter,
} from "../../src/gpo_studio/static/js/api.mjs";
import { state } from "../../src/gpo_studio/static/js/state.mjs";

afterEach(() => {
  vi.unstubAllGlobals();
  state.current = null;
});

describe("api", () => {
  function banner() {
    const summary = { textContent: "", hidden: true, focus: vi.fn() };
    const message = { textContent: "" };
    vi.stubGlobal("document", {
      querySelector: (selector) =>
        ({ "#error-summary": summary, "#error-message": message })[selector],
    });
    return { summary, message };
  }

  function success() {
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }

  test("background reads retain a persistent error until an explicit action clears it", async () => {
    const { summary, message } = banner();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(success()));
    showPersistentError("One batch saved; another needs review");
    await api("/api/health");
    expect(summary.hidden).toBe(false);
    expect(message.textContent).toContain("another needs review");
  });

  test("a successful mutation clears the error that preceded it", async () => {
    const { summary, message } = banner();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(success()));
    showPersistentError("Earlier operation failed");
    await api("/api/example", { method: "POST" });
    expect(summary.hidden).toBe(true);
    expect(message.textContent).toBe("");
  });

  test("a late successful mutation cannot clear a newer operation's error", async () => {
    const { summary, message } = banner();
    let release;
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockImplementation(
          () => new Promise((resolve) => (release = resolve)),
        ),
    );
    showPersistentError("Earlier error");
    const pending = api("/api/example", { method: "POST" });
    showPersistentError("A newer operation needs attention");
    release(success());
    await pending;
    expect(summary.hidden).toBe(false);
    expect(message.textContent).toContain("newer operation");
  });

  test("an owned read failure leaves a newer error and its focus untouched", async () => {
    const { summary, message } = banner();
    let rejectFetch;
    const fetchMock = vi.fn().mockImplementation(
      () =>
        new Promise((resolve, reject) => {
          rejectFetch = reject;
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const pending = api("/api/example", {
      cache: "no-store",
    });
    showPersistentError("A newer operation needs attention");
    const focusCalls = summary.focus.mock.calls.length;
    const cause = new Error("Synthetic retired transport failure");
    rejectFetch(cause);
    await expect(pending).rejects.toMatchObject({ cause });
    expect(summary.hidden).toBe(false);
    expect(message.textContent).toContain("newer operation");
    expect(summary.focus).toHaveBeenCalledTimes(focusCalls);
  });

  test.each(["GET", "POST"])(
    "%s transport failures reach the caller without publishing UI errors",
    async (method) => {
      const { summary, message } = banner();
      const cause = new Error("Synthetic connection failure");
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(cause));
      await expect(api("/api/example", { method })).rejects.toMatchObject({
        message: expect.stringContaining("server is unavailable"),
        cause,
        status: 0,
      });
      expect(summary.hidden).toBe(true);
      expect(message.textContent).toBe("");
      expect(summary.focus).not.toHaveBeenCalled();
    },
  );

  test("a current read reporter publishes once while a retired owner remains quiet", () => {
    const { summary, message } = banner();
    const retired = captureErrorReporter(() => false);
    const current = captureErrorReporter(() => true);
    expect(retired("Retired failure")).toBe(false);
    expect(summary.hidden).toBe(true);
    expect(current("Current refresh failed")).toBe(true);
    expect(message.textContent).toContain("Current refresh failed");
    expect(current("Duplicate outcome")).toBe(false);
    expect(message.textContent).not.toContain("Duplicate");
    expect(summary.focus).toHaveBeenCalledTimes(1);
  });

  test("an older read reporter cannot overwrite a newer actionable error", () => {
    const { summary, message } = banner();
    const report = captureErrorReporter(() => true);
    showPersistentError("A newer operation needs attention");
    const calls = summary.focus.mock.calls.length;
    expect(report("Old refresh failed")).toBe(false);
    expect(message.textContent).toContain("newer operation");
    expect(summary.focus).toHaveBeenCalledTimes(calls);
  });

  test("dismissing an error retires a pending read's reporter", () => {
    const { summary, message } = banner();
    showPersistentError("Earlier warning");
    const report = captureErrorReporter(() => true);
    clearPersistentError();
    expect(report("Late refresh failure")).toBe(false);
    expect(summary.hidden).toBe(true);
    expect(message.textContent).toBe("");
  });

  test("a successful action with no error to clear retains a current read's reporter", async () => {
    const { summary, message } = banner();
    const report = captureErrorReporter(() => true);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(success()));
    await api("/api/example", { method: "POST" });
    expect(report("Current policy reread failed")).toBe(true);
    expect(summary.hidden).toBe(false);
    expect(message.textContent).toContain("Current policy reread failed");
  });

  test("resolving an earlier warning does not dismiss a current read's outcome", async () => {
    const { summary, message } = banner();
    showPersistentError("Earlier warning");
    const report = captureErrorReporter(() => true);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(success()));
    await api("/api/example", { method: "POST" });
    expect(summary.hidden).toBe(true);
    expect(report("Current policy reread failed")).toBe(true);
    expect(message.textContent).toContain("Current policy reread failed");
  });

  test("shapes JSON requests and preserves caller headers", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      api("/api/example", {
        method: "POST",
        headers: { "X-Synthetic-Test": "enabled" },
        body: JSON.stringify({ value: "fixture" }),
      }),
    ).resolves.toEqual({ ok: true });

    expect(fetchMock).toHaveBeenCalledWith("/api/example", {
      headers: {
        "Content-Type": "application/json",
        "X-Synthetic-Test": "enabled",
      },
      method: "POST",
      body: JSON.stringify({ value: "fixture" }),
    });
  });

  test("exposes structured validation issues", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: {
              message: "Validation failed",
              issues: [{ path: "settings.0.value", message: "Invalid value" }],
            },
          }),
          { status: 422, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await expect(api("/api/example")).rejects.toMatchObject({
      message: "Validation failed",
      issues: [{ path: "settings.0.value", message: "Invalid value" }],
    });
  });
});

test("audit shapes an optimistic-concurrency mutation", () => {
  state.current = { revision: 17 };
  expect(audit("Synthetic edit")).toEqual({
    actor: "local-operator",
    reason: "Synthetic edit",
    expected_revision: 17,
  });
});
