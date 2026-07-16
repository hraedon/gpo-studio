import { describe, expect, test } from "vitest";

import {
  findRevisionGppItem,
  moveGppItemIds,
  prepareGppGroupClone,
  prepareGppRegistryClone,
} from "../../src/gpo_studio/static/js/gpp.mjs";

describe("GPP clone shaping", () => {
  test("gives a group and its members new editor identities without losing content", () => {
    const source = {
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
      ilt_filter: {
        items: [{ type: "ou", value: "OU=Lab,DC=studio,DC=test" }],
      },
    };

    const cloned = prepareGppGroupClone(source, [
      "Administrators",
      "Administrators (copy)",
    ]);

    expect(cloned).toMatchObject({
      id: "",
      name: "Administrators (copy 2)",
      members: [{ id: "", sid: "S-1-5-32-545" }],
      unknown_children: ["<Synthetic preserved='1' />"],
    });
    expect(cloned.ilt_filter).toEqual(source.ilt_filter);
    expect(source.members[0].id).toBe("member-original");
  });

  test("gives a registry item, UID, and nested value new identities", () => {
    const source = {
      id: "registry-original",
      uid: "uid-original",
      key: String.raw`Software\Studio`,
      value: {
        id: "value-original",
        name: "Enabled",
        registry_type: "REG_DWORD",
        value: "1",
        unknown_attrs: [["synthetic", "preserved"]],
      },
      unknown_children: ["<Synthetic preserved='1' />"],
    };

    expect(prepareGppRegistryClone(source)).toEqual({
      ...source,
      id: "",
      uid: "",
      value: { ...source.value, id: "" },
    });
    expect(source.value.id).toBe("value-original");
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
