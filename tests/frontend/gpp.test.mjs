import { describe, expect, test } from "vitest";

import {
  findRevisionGppItem,
  gppCloneRequest,
  gppRestoreRequest,
  iltOperatorWarning,
  moveGppItemIds,
} from "../../src/gpo_studio/static/js/gpp.mjs";

describe("OS targeting warnings", () => {
  test.each([
    ["WINTHRESHOLDSRV", "Windows Server 2016, 2019, 2022, and 2025"],
    ["WINTHRESHOLD", "Windows 10 and Windows 11"],
  ])("explains the %s family token", (version, expected) => {
    expect(
      iltOperatorWarning({ type: "os", os_criteria: { version } }),
    ).toContain(expected);
  });

  test("does not warn for unrelated predicates", () => {
    expect(iltOperatorWarning({ type: "ou", value: "OU=Servers" })).toBe("");
  });
});

describe("GPP clone and restore requests", () => {
  // Clone and restore run on the server, which copies the stored item --
  // members, targeting and retained (imported) content included -- so the
  // browser sends the item's identity and the copy's name, never its content.
  // tests/test_gpp_clone_restore.py holds the copy itself.
  const group = {
    id: "group-original",
    name: "Administrators",
    members: [
      {
        id: "member-original",
        sid: "S-1-5-32-545",
        unknown_attrs: [["synthetic", "preserved"]],
      },
    ],
    unknown_children: ["<Synthetic preserved='1' />"],
  };

  test("clones a group by id, under a free copy name, sending no content", () => {
    const request = gppCloneRequest("gpo-1", "computer", "groups", group, [
      "Administrators",
      "Administrators (copy)",
    ]);

    expect(request).toEqual({
      path: "/api/gpos/gpo-1/preferences/groups/group-original/clone",
      body: { scope: "computer", name: "Administrators (copy 2)" },
    });
    expect(group.members[0].id).toBe("member-original");
  });

  test("clones a registry item by id", () => {
    expect(
      gppCloneRequest("gpo-1", "user", "registry", { id: "registry-original" }),
    ).toEqual({
      path: "/api/gpos/gpo-1/preferences/registry/registry-original/clone",
      body: { scope: "user" },
    });
  });

  test("restores an item from a named revision, sending no content", () => {
    expect(
      gppRestoreRequest("gpo-1", "computer", "groups", "group-1", 3),
    ).toEqual({
      path: "/api/gpos/gpo-1/preferences/groups/group-1/restore",
      body: { scope: "computer", revision: 3 },
    });
  });
});

describe("revision GPP lookup", () => {
  test("finds an item only in the requested scope and kind", () => {
    const computerGroup = { id: "shared-id", name: "Computer group" };
    const snapshot = {
      gpp_collections: [
        { scope: "computer", groups: [computerGroup], registry: [] },
        {
          scope: "user",
          groups: [{ id: "shared-id", name: "User group" }],
          registry: [{ id: "registry-id", key: "Software" }],
        },
      ],
    };

    expect(
      findRevisionGppItem(snapshot, "computer", "groups", "shared-id"),
    ).toBe(computerGroup);
    expect(
      findRevisionGppItem(snapshot, "computer", "registry", "registry-id"),
    ).toBeNull();
    expect(
      findRevisionGppItem(snapshot, "user", "registry", "registry-id"),
    ).toMatchObject({ key: "Software" });
  });
});

describe("GPP reorder shaping", () => {
  const items = [{ id: "first" }, { id: "second" }, { id: "third" }];

  test("moves one item without mutating the rendered collection", () => {
    expect(moveGppItemIds(items, "second", -1)).toEqual([
      "second",
      "first",
      "third",
    ]);
    expect(items.map((item) => item.id)).toEqual(["first", "second", "third"]);
  });

  test.each([
    ["first", -1],
    ["third", 1],
    ["missing", 1],
  ])("rejects an unavailable move for %s by %i", (id, offset) => {
    expect(moveGppItemIds(items, id, offset)).toBeNull();
  });
});
