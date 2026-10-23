import { afterEach, describe, expect, test, vi } from "vitest";

import {
  api,
  audit,
  showPersistentError,
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
      reportNetworkError: false,
    });
    showPersistentError("A newer operation needs attention");
    const focusCalls = summary.focus.mock.calls.length;
    const cause = new Error("Synthetic retired transport failure");
    rejectFetch(cause);
    await expect(pending).rejects.toMatchObject({ cause });
    expect(summary.hidden).toBe(false);
    expect(message.textContent).toContain("newer operation");
    expect(summary.focus).toHaveBeenCalledTimes(focusCalls);
    expect(fetchMock.mock.calls[0][1]).not.toHaveProperty("reportNetworkError");
  });

  test("ordinary transport failures still report the unavailable server", async () => {
    const { summary, message } = banner();
    const cause = new Error("Synthetic connection failure");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(cause));
    await expect(api("/api/example")).rejects.toMatchObject({
      message: expect.stringContaining("server is unavailable"),
      cause,
    });
    expect(summary.hidden).toBe(false);
    expect(message.textContent).toContain("server is unavailable");
    expect(summary.focus).toHaveBeenCalled();
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
