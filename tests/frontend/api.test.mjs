import { afterEach, describe, expect, test, vi } from "vitest";

import { api, audit } from "../../src/gpo_studio/static/js/api.mjs";
import { state } from "../../src/gpo_studio/static/js/state.mjs";

afterEach(() => {
  vi.unstubAllGlobals();
  state.current = null;
});

describe("api", () => {
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
