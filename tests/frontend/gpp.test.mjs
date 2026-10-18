import { describe, expect, test } from "vitest";

import {
  findRevisionGppItem,
  gppCloneRequest,
  gppRestoreRequest,
  iltOperatorWarning,
  iltRows,
  memberEntry,
  moveGppItemIds,
  predicateEntry,
  retainedSummary,
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

describe("the id-based edit protocol (final-r13)", () => {
  // Retained (imported) content never round-trips through the browser: an
  // edit sends typed fields and the ids of what it keeps.
  const filter = {
    items: [
      {
        type: "ou",
        negate: false,
        value: "OU=A",
        bool_op: "AND",
        unknown_attrs: [["ouExtra", "o"]],
      },
      '<FilterBattery bool="AND" not="0" />',
      {
        type: "date",
        negate: false,
        value: "2026-01-01|2026-12-31",
        bool_op: "AND",
      },
      { type: "group", negate: false, value: "S-1-5-32-544", bool_op: "OR" },
    ],
    ids: ["p-ou", "p-raw", "p-date", "p-or"],
  };

  test("classifies each stored predicate with its id", () => {
    expect(
      iltRows(filter).map(({ id, raw, editable }) => [id, raw, editable]),
    ).toEqual([
      ["p-ou", false, true],
      ["p-raw", true, false],
      ["p-date", false, false],
      ["p-or", false, false],
    ]);
  });

  test("sends a read-only predicate as its id alone", () => {
    for (const row of iltRows(filter).filter((row) => !row.editable)) {
      expect(predicateEntry({ readonly: true, id: row.id })).toEqual({
        id: row.id,
      });
    }
  });

  test("sends an edited predicate's typed fields and id, never its retained attributes", () => {
    const entry = predicateEntry({
      id: "p-ou",
      type: "group",
      negate: true,
      value: "S-1-5-32-545",
    });
    expect(entry).toEqual({
      id: "p-ou",
      type: "group",
      negate: true,
      value: "S-1-5-32-545",
      bool_op: "AND",
    });
    expect(entry).not.toHaveProperty("unknown_attrs");
  });

  test("sends a new predicate without an id", () => {
    expect(
      predicateEntry({ id: "", type: "ou", negate: false, value: "OU=B" }),
    ).toEqual({
      type: "ou",
      negate: false,
      value: "OU=B",
      bool_op: "AND",
    });
  });

  test("sends members by id without their retained attributes", () => {
    expect(
      memberEntry({ id: "m-1", sid: "S-1-5-32-544", name: "A", action: "add" }),
    ).toEqual({ id: "m-1", sid: "S-1-5-32-544", name: "A", action: "add" });
    expect(
      memberEntry({ id: "", sid: "S-1-5-32-545", name: "", action: "add" }),
    ).toEqual({
      sid: "S-1-5-32-545",
      name: "",
      action: "add",
    });
  });

  test("describes retained content read-only, flagging content without an import record", () => {
    const item = {
      unknown_attrs: [["image", "2"]],
      unknown_props_attrs: [],
      unknown_children: ["<ItemChild />"],
      members: [{ unknown_attrs: [["memberExtra", "m"]] }],
    };
    expect(retainedSummary(item)).toBe(
      "Imported content Studio does not edit is kept as imported: 2 attributes, 1 element.",
    );
    expect(retainedSummary({ ...item, retained_unverified: true })).toContain(
      "no import record",
    );
    expect(retainedSummary({ unknown_attrs: [] })).toBe("");
  });
});
