import { beforeEach, describe, expect, test, vi } from "vitest";

let observations, api;
function gpo(guid = "synthetic-guid", revision = 1) {
  return {
    guid,
    revision,
    name: "Synthetic collection member",
    status: "draft",
  };
}
function payload(record = gpo()) {
  return { gpo: record, validation: [], review_model_sha256: "synthetic-hash" };
}
function stamp(data, since = observations.getGpoSummaryVersion()) {
  observations.stampGpoResponse(data, since);
  return data;
}
beforeEach(async () => {
  vi.resetModules();
  observations = await import("../../src/gpo_studio/static/js/state.mjs");
  ({ api } = await import("../../src/gpo_studio/static/js/api.mjs"));
  vi.unstubAllGlobals();
});

describe("collection deletion ordering", () => {
  test("old payload continuations stay retired even after a fresh authoritative list", () => {
    const old = stamp(payload());
    expect(observations.applyPayload(old)).toBe(true);
    observations.observeGpoDeleted(old.gpo.guid);
    observations.replaceGpoSummaries([], observations.getGpoSummaryVersion());
    expect(observations.updateGpoSummary(old.gpo)).toBe(false);
    expect(observations.applyPayload(old)).toBe(false);
    expect(observations.state.current).toBeNull();
    expect(observations.state.gpos).toEqual([]);
  });

  test("fresh same-GUID/revision recreation is admitted while its old incarnation stays retired", () => {
    const old = stamp(payload());
    observations.applyPayload(old);
    const opening = observations.getGpoGeneration(old.gpo.guid);
    observations.observeGpoDeleted(old.gpo.guid);
    const fresh = stamp(payload({ ...old.gpo, name: "Synthetic recreated" }));
    expect(observations.getGpoGeneration(old.gpo.guid)).not.toBe(opening);
    expect(observations.applyPayload(fresh)).toBe(true);
    expect(observations.applyPayload(old)).toBe(false);
    expect(observations.state.current.name).toBe("Synthetic recreated");
    // Copies must explicitly carry provenance; an unregistered old clone is
    // never implicitly promoted to the current generation.
    expect(observations.applyPayload({ gpo: { ...old.gpo } })).toBe(false);
  });

  test("an older list cannot restore a deletion but retains later upserts", () => {
    const removed = gpo("removed"),
      survivor = gpo("survivor");
    const since = observations.getGpoSummaryVersion();
    observations.updateGpoSummary(stamp(payload(removed)).gpo);
    observations.observeGpoDeleted(removed.guid);
    observations.updateGpoSummary(stamp(payload(survivor)).gpo);
    observations.replaceGpoSummaries([removed], since);
    expect(observations.state.gpos.map((row) => row.guid)).toEqual([
      survivor.guid,
    ]);
    observations.replaceGpoSummaries([], observations.getGpoSummaryVersion());
    expect(observations.state.gpos).toEqual([]);
  });

  test("a fresh listing can include a recreated GUID without clearing old barriers", () => {
    const old = stamp(payload());
    observations.observeGpoDeleted(old.gpo.guid);
    const fresh = stamp({
      items: [{ ...old.gpo, name: "Synthetic fresh member" }],
    });
    observations.replaceGpoSummaries(
      fresh.items,
      observations.getGpoSummaryVersion(),
    );
    expect(observations.state.gpos).toEqual(fresh.items);
    expect(observations.isGpoObservationCurrent(fresh.items[0])).toBe(true);
    expect(observations.isGpoObservationCurrent(old.gpo)).toBe(false);
  });

  test("deleting another member leaves the current object and digests alone", () => {
    const current = stamp(payload(gpo("selected")));
    observations.applyPayload(current);
    observations.updateGpoSummary(stamp(payload(gpo("other"))).gpo);
    expect(observations.observeGpoDeleted("other")).toBe(false);
    expect(observations.state.current).toBe(current.gpo);
    expect(observations.state.reviewHash).toBe("synthetic-hash");
    expect(observations.observeGpoDeleted("selected")).toBe(true);
    expect(observations.state.current).toBeNull();
    expect(observations.state.reviewHash).toBe("");
    expect(observations.state.artifactCapabilities).toEqual({});
  });
  test("explicit-target editors retain their opening view generation separately", async () => {
    const original = stamp(payload(gpo("underlay")));
    observations.applyPayload(original);
    const dialog = {
      open: true,
      editorSession: Symbol("opening"),
      editorTarget: {
        ...original.gpo,
        generation: observations.getGpoGeneration("underlay"),
      },
    };
    const form = { closest: () => dialog };
    const { captureMutation, isSameEditor } =
      await import("../../src/gpo_studio/static/js/mutation-context.mjs");
    const context = captureMutation(form);
    context.guid = "different-target";
    context.generation = observations.getGpoGeneration(context.guid);
    expect(isSameEditor(context)).toBe(true);
    observations.observeGpoDeleted("underlay");
    observations.applyPayload(stamp(payload(gpo("underlay"))));
    expect(isSameEditor(context)).toBe(false);
    dialog.editorTarget = {
      ...observations.state.current,
      generation: observations.getGpoGeneration("underlay"),
    };
    const reopened = captureMutation(form);
    reopened.guid = "different-target";
    reopened.generation = observations.getGpoGeneration(reopened.guid);
    expect(isSameEditor(reopened)).toBe(true);
  });
});

describe("transport provenance has no early UI effects", () => {
  test("API dispatch provenance retires a late result without publishing it", async () => {
    let release;
    vi.stubGlobal("fetch", () => new Promise((resolve) => (release = resolve)));
    const pending = api("/api/gpos/synthetic-guid");
    observations.observeGpoDeleted("synthetic-guid");
    release({
      ok: true,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => payload(),
    });
    const result = await pending;
    expect(observations.isGpoObservationCurrent(result.gpo)).toBe(false);
    expect(observations.state.current).toBeNull();
    expect(observations.state.gpos).toEqual([]);
  });

  test("a fresh API result registers provenance but waits for its owner to publish", async () => {
    observations.observeGpoDeleted("synthetic-guid");
    vi.stubGlobal("fetch", async () => ({
      ok: true,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => payload(),
    }));
    const result = await api("/api/gpos/synthetic-guid");
    expect(observations.isGpoObservationCurrent(result.gpo)).toBe(true);
    expect(observations.state.current).toBeNull();
    expect(observations.state.gpos).toEqual([]);
    expect(observations.applyPayload(result)).toBe(true);
  });
});
