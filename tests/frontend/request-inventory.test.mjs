import { readdirSync, readFileSync } from "node:fs";
import { expect, test } from "vitest";
import { discoverRequests } from "./request-inventory.mjs";
const directory = new URL("../../src/gpo_studio/static/js/", import.meta.url);
const registry = JSON.parse(
  readFileSync(
    new URL("../../docs/ui-request-ownership.json", import.meta.url),
    "utf8",
  ),
);
function check(sites, records) {
  expect(new Set(sites.map((s) => s.id)).size).toBe(sites.length);
  expect(
    sites
      .map(({ id, file, owner, channel, method, path }) => ({
        id,
        file,
        owner,
        channel,
        method,
        path,
      }))
      .sort((a, b) => a.id?.localeCompare(b.id)),
  ).toEqual(
    records
      .map(({ id, file, owner, channel, method, path }) => ({
        id,
        file,
        owner,
        channel,
        method,
        path,
      }))
      .sort((a, b) => a.id.localeCompare(b.id)),
  );
}
test("every transport call has a semantic policy; all 30 reads/previews/artifacts remain classified", () => {
  const sites = readdirSync(directory)
    .filter((file) => /\.m?js$/.test(file))
    .flatMap((file) =>
      discoverRequests(readFileSync(new URL(file, directory), "utf8"), file),
    );
  check(sites, registry);
  expect(
    registry.filter((record) =>
      ["read", "preview", "artifact", "silent-monitor"].includes(record.class),
    ),
  ).toHaveLength(30);
  for (const record of registry) {
    expect(record.policy.length).toBeGreaterThan(30);
    if (!["mutation", "transport"].includes(record.class))
      for (const field of [
        "ownerScope",
        "errorSink",
        "retirement",
        "cacheAdmission",
      ])
        expect(record[field]?.length).toBeGreaterThan(0);
    expect(
      readFileSync(new URL(`../../${record.adapter}`, import.meta.url), "utf8")
        .length,
    ).toBeGreaterThan(100);
  }
});
test("new unregistered calls and removed registry entries fail the semantic completeness comparison", () => {
  const known = discoverRequests(
    'import {api} from "./api.mjs"; /* request: example.read */ api("/api/example");',
    "example.mjs",
  );
  const entry = known.map(({ id, file, owner, channel, method, path }) => ({
    id,
    file,
    owner,
    channel,
    method,
    path,
  }));
  expect(() =>
    check(
      [...known, ...discoverRequests('fetch("/api/missed")', "new.mjs")],
      entry,
    ),
  ).toThrow();
  expect(() => check([], entry)).toThrow();
});
test("discovers API aliases/namespaces and simple fetch aliases without line-number identity", () => {
  const calls = discoverRequests(
    'import {api as read} from "./api.mjs"; import * as transport from "./api.mjs"; const retrieve=read, download=globalThis.fetch; /* request: x.one */ retrieve("/a"); /* request: x.two */ transport.api("/b",{method:"POST"}); /* request: x.three */ download("/c");',
    "aliases.mjs",
  );
  expect(calls.map((call) => [call.id, call.channel, call.method])).toEqual([
    ["x.one", "api", "GET"],
    ["x.two", "api", "POST"],
    ["x.three", "fetch", "GET"],
  ]);
});
for (const text of [
  "new XMLHttpRequest()",
  'new EventSource("/events")',
  'new globalThis.WebSocket("/events")',
  'navigator.sendBeacon("/new")',
])
  test(`unknown channel requires an explicit inventory decision: ${text}`, () => {
    expect(() => discoverRequests(text, "new.mjs")).toThrow(
      /Unclassified network channel/,
    );
  });
