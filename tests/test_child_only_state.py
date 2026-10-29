"""Child-only ADMX policies are observed from their element rows (#147).

A child-only policy has no policy-level ``valueName``, ``enabledList`` or
``disabledList``: its state is visible only through its element rows.
Disabling writes a delete for every non-list element (WI-020). Before #147 the
settings browser never looked at those deletes and read every disabled
child-only policy as enabled.

inetres.admx ``NeedEdgeBrowser`` (Windows Server 2025) has three enums, each
with a ``<delete/>`` "None" item, so its disable is byte-identical to enabling
it with "None" chosen in all three. Only the editor IDs of Studio's own writes
can tell the two apart; imported rows read as indeterminate.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gpo_studio.admx import AdmxCatalogue, EnumItem, PolicyDefinition, PolicyElement
from gpo_studio.api import app
from gpo_studio.model import RegistrySetting
from gpo_studio.policy_config import PolicyConfiguration, resolve_policy, structured_policy_ids
from gpo_studio.settings_browser import SettingsBrowserResult, build_settings_browser
from gpo_studio.store import WorkspaceStore

KEY = r"Software\Policies\Synthetic\ChildOnly"
LEGACY_NS = "Synthetic.ChildOnly"
STRUCTURED_NS = "https://synthetic.example.test/child-only"
INDETERMINATE_DELETE = (
    "enum value is deleted, but the policy state is indeterminate, so "
    "this cannot be read as a choice or as a disabled policy"
)


def _delete_enum(element_id: str, value_name: str | None = None) -> PolicyElement:
    return PolicyElement(
        kind="enum",
        id=element_id,
        registry_value_name=value_name or element_id,
        enum_items=(
            EnumItem(
                id="item-0", display_name="", value="", registry_type="REG_SZ", action="delete"
            ),
            EnumItem(id="1", display_name="One", value=1, registry_type="REG_DWORD"),
            EnumItem(id="0", display_name="Zero", value=0, registry_type="REG_DWORD"),
        ),
    )


ELEMENTS: dict[str, PolicyElement] = {
    "text": PolicyElement(kind="text", id="Mode", registry_value_name="Mode"),
    "boolean": PolicyElement(kind="boolean", id="Mode", registry_value_name="Mode"),
    "decimal": PolicyElement(kind="decimal", id="Mode", registry_value_name="Mode"),
    "multitext": PolicyElement(kind="multitext", id="Mode", registry_value_name="Mode"),
    "enum": PolicyElement(
        kind="enum",
        id="Mode",
        registry_value_name="Mode",
        enum_items=(EnumItem(id="1", display_name="One", value=1, registry_type="REG_DWORD"),),
    ),
    "enum-with-delete-item": _delete_enum("Mode"),
}
COUNT = PolicyElement(kind="decimal", id="Count", registry_value_name="Count")
ENABLED_VALUES: dict[str, Any] = {
    "text": "synthetic",
    "boolean": True,
    "decimal": 7,
    "multitext": ["a", "b"],
    "enum": "1",
    "enum-with-delete-item": "1",
}


def _policy(
    *elements: PolicyElement, namespace: str = LEGACY_NS, class_: str = "Machine"
) -> PolicyDefinition:
    return PolicyDefinition(
        id="ChildOnly",
        class_=class_,  # type: ignore[arg-type]
        key=KEY,
        namespace=namespace,
        display_name="Synthetic child-only policy",
        explain_text="",
        supported_on="",
        elements=elements,
    )


def _edge_shape(namespace: str = LEGACY_NS) -> PolicyDefinition:
    # NeedEdgeBrowser's shape: Both-class, child-only, three delete-item enums.
    return _policy(
        _delete_enum("Edge"), _delete_enum("Edge2"), _delete_enum("Edge3"),
        namespace=namespace, class_="Both",
    )


def _read(policy: PolicyDefinition, rows: list[RegistrySetting]) -> SettingsBrowserResult:
    return build_settings_browser(AdmxCatalogue(policies=(policy,)), rows)


def _summary(result: SettingsBrowserResult) -> list[tuple[str, str | None, dict[str, Any]]]:
    return [(group.state, group.state_reason, group.element_values) for group in result.resolved]


def _imported(rows: list[RegistrySetting]) -> list[RegistrySetting]:
    # What a Registry.pol import stores: the same bytes with no Studio identity.
    return [replace(row, id=f"imported-{row.side}-{index}") for index, row in enumerate(rows)]


def _disabled(policy: PolicyDefinition, side: str = "computer") -> list[RegistrySetting]:
    return resolve_policy(policy, PolicyConfiguration(side, {}, state="disabled"))  # type: ignore[arg-type]


def _enabled(
    policy: PolicyDefinition, values: dict[str, Any], side: str = "computer"
) -> list[RegistrySetting]:
    return resolve_policy(policy, PolicyConfiguration(side, values))  # type: ignore[arg-type]


@pytest.mark.parametrize("kind", ELEMENTS)
@pytest.mark.parametrize("namespace", [LEGACY_NS, STRUCTURED_NS], ids=["legacy", "structured"])
def test_studio_disable_of_a_child_only_policy_reads_back_as_disabled(
    kind: str, namespace: str
) -> None:
    """Issue #147's closing condition, for every element kind."""
    policy = _policy(ELEMENTS[kind], namespace=namespace)
    assert structured_policy_ids(policy) is (namespace == STRUCTURED_NS)
    rows = _disabled(policy)
    assert [row.action for row in rows] == ["delete"]
    result = _read(policy, rows)
    assert _summary(result) == [("disabled", None, {})]
    assert result.unresolved == ()


@pytest.mark.parametrize("kind", ELEMENTS)
def test_studio_enable_of_a_child_only_policy_still_reads_back_as_enabled(kind: str) -> None:
    policy = _policy(ELEMENTS[kind])
    result = _read(policy, _enabled(policy, {"Mode": ENABLED_VALUES[kind]}))
    assert _summary(result) == [("enabled", None, {"Mode": ENABLED_VALUES[kind]})]
    assert result.unresolved == ()


@pytest.mark.parametrize("kind", [kind for kind in ELEMENTS if kind != "enum-with-delete-item"])
def test_imported_disable_is_disabled_when_no_enabled_choice_writes_it(kind: str) -> None:
    # An enabled policy always writes this element's value, so a complete set
    # of deletes is consistent with Disabled only.
    policy = _policy(ELEMENTS[kind], _delete_enum("Other"))
    result = _read(policy, _imported(_disabled(policy)))
    assert _summary(result) == [("disabled", None, {})]
    assert result.unresolved == ()


@pytest.mark.parametrize("namespace", [LEGACY_NS, STRUCTURED_NS], ids=["legacy", "structured"])
def test_edge_shape_studio_writes_read_back_as_written(namespace: str) -> None:
    policy = _edge_shape(namespace)
    disabled = _disabled(policy)
    none_everywhere = _enabled(policy, {"Edge": "item-0", "Edge2": "item-0", "Edge3": "item-0"})
    # The bytes are identical; only the editor IDs differ.
    assert [r.identity() + (r.action, r.registry_type, r.value) for r in disabled] == [
        r.identity() + (r.action, r.registry_type, r.value) for r in none_everywhere
    ]
    assert [r.id for r in disabled] != [r.id for r in none_everywhere]

    assert _summary(_read(policy, disabled)) == [("disabled", None, {})]
    assert _summary(_read(policy, none_everywhere)) == [
        ("enabled", None, {"Edge": "item-0", "Edge2": "item-0", "Edge3": "item-0"})
    ]
    mixed = _enabled(policy, {"Edge": "1", "Edge2": "item-0", "Edge3": "0"})
    assert _summary(_read(policy, mixed)) == [
        ("enabled", None, {"Edge": "1", "Edge2": "item-0", "Edge3": "0"})
    ]
    for rows in (disabled, none_everywhere, mixed):
        assert _read(policy, rows).unresolved == ()


def test_imported_edge_shape_disable_is_indeterminate_and_says_why() -> None:
    policy = _edge_shape()
    rows = _imported(_disabled(policy))
    result = _read(policy, rows)
    assert _summary(result) == [("indeterminate", "disabled_matches_enabled_deletes", {})]
    # Each delete stays visible, rather than being read as "None" or dropped.
    assert [(u.setting, u.reason) for u in result.unresolved] == [
        (row, INDETERMINATE_DELETE) for row in rows
    ]


def test_imported_edge_shape_with_a_value_write_is_enabled() -> None:
    policy = _edge_shape()
    rows = _imported(_enabled(policy, {"Edge": "1", "Edge2": "item-0", "Edge3": "item-0"}))
    result = _read(policy, rows)
    assert _summary(result) == [
        ("enabled", None, {"Edge": "1", "Edge2": "item-0", "Edge3": "item-0"})
    ]
    assert result.unresolved == ()


@pytest.mark.parametrize("imported", [False, True], ids=["studio", "imported"])
def test_a_partial_set_of_disable_deletes_is_indeterminate(imported: bool) -> None:
    policy = _policy(ELEMENTS["text"], _delete_enum("Other"))
    rows = _disabled(policy)[:1]
    result = _read(policy, _imported(rows) if imported else rows)
    assert _summary(result) == [("indeterminate", "no_complete_pattern", {})]


def test_disable_deletes_beside_a_value_write_are_indeterminate() -> None:
    policy = _policy(ELEMENTS["text"], COUNT)
    disabled = _disabled(policy)
    enabled = _enabled(policy, {"Mode": "x", "Count": 3})
    rows = [disabled[0], enabled[1]]
    result = _read(policy, rows)
    assert result.resolved[0].state == "indeterminate"
    assert result.resolved[0].state_reason == "no_complete_pattern"


def test_imported_delete_an_enabled_policy_cannot_write_beside_a_value_is_indeterminate() -> None:
    policy = _policy(ELEMENTS["text"], COUNT)
    disabled = _imported(_disabled(policy))
    enabled = _imported(_enabled(policy, {"Mode": "x", "Count": 3}))
    result = _read(policy, [disabled[0], enabled[1]])
    assert _summary(result) == [("indeterminate", "no_complete_pattern", {"Count": 3})]
    assert [u.reason for u in result.unresolved] == [
        "element value is deleted, but the policy state is indeterminate, so "
        "this cannot be read as a value or as a disabled policy"
    ]


def test_a_child_only_policy_of_lists_writes_nothing_when_disabled() -> None:
    # Known gap, left for the owner: Studio's disable writes no row for a list
    # (no capture shows what gpedit writes, see _disabled_element_deletes), so
    # a disabled list-only policy leaves nothing to read back. 40 of the 1,261
    # child-only Server 2025 policies are list-only. If the writer learns a
    # disabled list form, this test must change with the reader.
    policy = _policy(PolicyElement(kind="list", id="Entries", attributes=(("valuePrefix", ""),)))
    assert _disabled(policy) == []
    assert _enabled(policy, {"Entries": []}) == []
    assert _read(policy, _disabled(policy)).resolved == ()


# --- the legacy ``disabled-element-`` ambiguity (#143) ----------------------


def _colliding(other: PolicyElement) -> PolicyDefinition:
    # Element "disabled-element-Mode" makes the legacy suffix of Mode's disable
    # ("disabled-element-Mode") also spell that element's own write.
    return _policy(_delete_enum("Mode"), other)


def _legacy_rows(policy: PolicyDefinition, rows: list[RegistrySetting]) -> list[RegistrySetting]:
    """Re-spell structured editor IDs the way a legacy-family writer did."""
    prefix = f"admx-{policy.qualified_id}-computer-"
    respelled = []
    for row in rows:
        kind, component = row.id.split(":")[3:5]
        suffix = bytes.fromhex(component).decode()
        if kind == "disabled-element":
            suffix = "disabled-element-" + suffix
        respelled.append(replace(row, id=prefix + suffix))
    return respelled


@pytest.mark.parametrize(
    ("other_kind", "expected"),
    [
        ("text", ("disabled", None)),
        ("enum-with-delete-item", ("indeterminate", "disabled_matches_enabled_deletes")),
    ],
)
def test_legacy_disabled_element_suffix_that_an_element_id_can_spell(
    other_kind: str, expected: tuple[str, str | None]
) -> None:
    other = replace(ELEMENTS[other_kind], id="disabled-element-Mode", registry_value_name="Other")
    policy = _colliding(other)
    # Today's writer avoids the collision with structured IDs ...
    assert structured_policy_ids(policy)
    disabled = _disabled(policy)
    assert _summary(_read(policy, disabled)) == [("disabled", None, {})]
    # ... but a historical legacy row's suffix proves nothing, so only the
    # bytes count: like an imported row.
    legacy = _legacy_rows(policy, disabled)
    assert legacy[0].id.endswith("-computer-disabled-element-Mode")
    result = _read(policy, legacy)
    assert [(group.state, group.state_reason) for group in result.resolved] == [expected]
    assert all(group.element_values == {} for group in result.resolved)


def test_legacy_disable_suffix_is_identity_when_no_element_can_spell_it() -> None:
    policy = _edge_shape()
    assert not structured_policy_ids(policy)
    rows = _disabled(policy)
    assert all("-computer-disabled-element-" in row.id for row in rows)
    assert _summary(_read(policy, rows)) == [("disabled", None, {})]


# --- through the API ---------------------------------------------------------

_ADMX = rb"""<?xml version="1.0" encoding="utf-8"?>
<policyDefinitions xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions">
  <policyNamespaces>
    <target prefix="childonly" namespace="Synthetic.ChildOnlyApi" />
  </policyNamespaces>
  <categories><category name="Root" displayName="$(string.Root)" /></categories>
  <policies>
    <policy name="EdgeChoice" class="Both" key="Software\Policies\Synthetic\ChildOnlyApi\Edge"
            displayName="$(string.EdgeChoice)" explainText="$(string.EdgeChoice)">
      <parentCategory ref="Root" />
      <elements>
        <enum id="Edge" valueName="Edge">
          <item displayName="$(string.None)"><value><delete /></value></item>
          <item displayName="$(string.One)"><value><decimal value="1" /></value></item>
        </enum>
        <enum id="Edge2" valueName="Edge2">
          <item displayName="$(string.None)"><value><delete /></value></item>
          <item displayName="$(string.One)"><value><decimal value="1" /></value></item>
        </enum>
        <enum id="Edge3" valueName="Edge3">
          <item displayName="$(string.None)"><value><delete /></value></item>
          <item displayName="$(string.One)"><value><decimal value="1" /></value></item>
        </enum>
      </elements>
    </policy>
    <policy name="TextChoice" class="Machine" key="Software\Policies\Synthetic\ChildOnlyApi\Text"
            displayName="$(string.TextChoice)" explainText="$(string.TextChoice)">
      <parentCategory ref="Root" />
      <elements>
        <text id="Label" valueName="Label" />
        <enum id="Edge" valueName="Edge">
          <item displayName="$(string.None)"><value><delete /></value></item>
          <item displayName="$(string.One)"><value><decimal value="1" /></value></item>
        </enum>
      </elements>
    </policy>
  </policies>
</policyDefinitions>"""

_ADML = b"""<?xml version="1.0" encoding="utf-8"?>
<policyDefinitionResources xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions">
  <resources><stringTable>
    <string id="Root">Synthetic root</string>
    <string id="EdgeChoice">Synthetic edge choice</string>
    <string id="TextChoice">Synthetic text choice</string>
    <string id="None"></string>
    <string id="One">One</string>
  </stringTable></resources>
</policyDefinitionResources>"""

_QID = "Synthetic.ChildOnlyApi%3A"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    admx_dir = tmp_path / "admx"
    admx_dir.mkdir()
    (admx_dir / "childonly.admx").write_bytes(_ADMX)
    (admx_dir / "childonly.adml").write_bytes(_ADML)
    monkeypatch.setenv("GPO_STUDIO_ADMX_DIR", str(admx_dir))
    if hasattr(app.state, "admx_catalogue"):
        monkeypatch.delattr(app.state, "admx_catalogue")
    store = WorkspaceStore(tmp_path / "api.db")
    monkeypatch.setattr(app.state, "store", store, raising=False)
    monkeypatch.setattr(app.state, "owns_store", False, raising=False)
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        store.close()


def _configure(
    client: TestClient, gpo: dict[str, Any], policy: str, state: str, values: dict[str, Any]
) -> dict[str, Any]:
    response = client.post(
        f"/api/admx/policies/{_QID}{policy}/configure",
        json={
            "gpo_guid": gpo["guid"],
            "side": "computer",
            "values": values,
            "state": state,
            "expected_revision": gpo["revision"],
            "actor": "tester",
            "reason": f"Set {policy} {state}",
        },
    )
    assert response.status_code == 200, response.text
    return client.get(f"/api/gpos/{gpo['guid']}").json()["gpo"]


def _configured(client: TestClient, gpo: dict[str, Any], **query: str) -> dict[str, Any]:
    response = client.get(f"/api/gpos/{gpo['guid']}/configured-settings", params=query)
    assert response.status_code == 200, response.text
    return response.json()


def _states(data: dict[str, Any]) -> dict[str, tuple[str, str | None, dict[str, Any]]]:
    return {
        item["policy_id"].split(":")[1]: (
            item["state"], item["state_reason"], item["element_values"]
        )
        for item in data["resolved"]
    }


NONE_EVERYWHERE = {"Edge": "item-0", "Edge2": "item-0", "Edge3": "item-0"}


@pytest.mark.parametrize(
    ("policy", "state", "values", "expected"),
    [
        ("EdgeChoice", "disabled", {}, ("disabled", None, {})),
        ("EdgeChoice", "enabled", NONE_EVERYWHERE, ("enabled", None, NONE_EVERYWHERE)),
        (
            "EdgeChoice", "enabled", {**NONE_EVERYWHERE, "Edge2": "1"},
            ("enabled", None, {**NONE_EVERYWHERE, "Edge2": "1"}),
        ),
        ("TextChoice", "disabled", {}, ("disabled", None, {})),
        (
            "TextChoice", "enabled", {"Label": "x", "Edge": "item-0"},
            ("enabled", None, {"Label": "x", "Edge": "item-0"}),
        ),
    ],
)
def test_api_reads_back_each_state_studio_writes(
    client: TestClient,
    policy: str,
    state: str,
    values: dict[str, Any],
    expected: tuple[str, str | None, dict[str, Any]],
) -> None:
    gpo = client.post("/api/gpos", json={"name": f"Child-only {policy} {state}"}).json()["gpo"]
    gpo = _configure(client, gpo, policy, state, values)
    data = _configured(client, gpo)
    assert _states(data) == {policy: expected}
    assert data["unresolved"] == []
    # The browser's state filter finds it under the state it shows.
    assert _states(_configured(client, gpo, state=expected[0])) == {policy: expected}


def test_api_reports_an_imported_byte_identical_disable_as_indeterminate(
    client: TestClient,
) -> None:
    gpo = client.post("/api/gpos", json={"name": "Imported child-only"}).json()["gpo"]
    gpo = _configure(client, gpo, "EdgeChoice", "disabled", {})
    gpo = _configure(client, gpo, "TextChoice", "disabled", {})
    store: WorkspaceStore = app.state.store
    head = store.get_gpo(gpo["guid"])
    imported = _imported(list(head.settings))
    head = store.replace_selected_settings(
        head.guid, head.revision, frozenset(range(len(head.settings))), imported,
        identity="tester", reason="Replace with imported rows",
    )
    data = _configured(client, gpo)
    assert _states(data) == {
        "EdgeChoice": ("indeterminate", "disabled_matches_enabled_deletes", {}),
        # Label always writes a value when enabled, so its delete proves Disabled.
        "TextChoice": ("disabled", None, {}),
    }
    assert sorted((u["value_name"], u["reason"]) for u in data["unresolved"]) == [
        ("Edge", INDETERMINATE_DELETE),
        ("Edge2", INDETERMINATE_DELETE),
        ("Edge3", INDETERMINATE_DELETE),
    ]
    assert list(_states(_configured(client, gpo, state="indeterminate"))) == ["EdgeChoice"]
    assert list(_states(_configured(client, gpo, state="disabled"))) == ["TextChoice"]

    # Reconfiguring cannot claim imported rows; the operator replaces them.
    retry = client.post(
        f"/api/admx/policies/{_QID}EdgeChoice/configure",
        json={
            "gpo_guid": gpo["guid"], "side": "computer", "values": {}, "state": "disabled",
            "expected_revision": head.revision, "actor": "tester", "reason": "Disable",
        },
    )
    assert retry.status_code == 422
    assert retry.json()["error"]["issues"][0]["code"] == "conflicting_retained_setting"


def test_browser_explains_the_new_indeterminate_reason() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "src/gpo_studio/static/js/browser.mjs"
    ).read_text(encoding="utf-8")
    assert "  disabled_matches_enabled_deletes: " in script
