from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gpo_studio.admx import (
    AdmxCatalogue,
    Category,
    EnumItem,
    PolicyDefinition,
    PolicyElement,
    PolicyValue,
    SupportedOnDefinition,
)
from gpo_studio.api import app
from gpo_studio.model import RegistrySetting
from gpo_studio.policy_config import PolicyConfiguration, PolicyState, resolve_policy
from gpo_studio.settings_browser import (
    SettingsBrowserResult,
    build_category_tree,
    build_settings_browser,
    search_configured_settings,
)
from gpo_studio.store import WorkspaceStore

NS = "Synthetic.Policies.Test"
KEY = r"Software\Policies\Synthetic\Test"


def _policy(
    *,
    id: str = "TestPolicy",
    class_: str = "Machine",
    namespace: str = NS,
    display_name: str = "Test Policy",
    explain_text: str = "Explains the test policy.",
    parent_category: str = "TestCategory",
    supported_on: str = "Supported_Test",
    value_name: str = "",
    enabled_value: Any | None = None,
    disabled_value: Any | None = None,
    enabled_list: Any | None = None,
    disabled_list: Any | None = None,
    elements: tuple[PolicyElement, ...] = (),
) -> PolicyDefinition:
    return PolicyDefinition(
        id=id,
        class_=class_,
        key=KEY,
        display_name=display_name,
        explain_text=explain_text,
        supported_on=supported_on,
        namespace=namespace,
        parent_category=parent_category,
        value_name=value_name,
        enabled_value=enabled_value,
        disabled_value=disabled_value,
        enabled_list=enabled_list,
        disabled_list=disabled_list,
        elements=elements,
    )


def _catalogue(
    policies: tuple[PolicyDefinition, ...] = (),
    categories: tuple[Category, ...] = (),
    supported_on: tuple[SupportedOnDefinition, ...] = (),
) -> AdmxCatalogue:
    return AdmxCatalogue(
        policies=policies,
        categories=categories,
        supported_on=supported_on,
    )


def _setting(
    *,
    id: str,
    side: str = "computer",
    value: str | int | list[str] = 1,
    action: str = "set",
    registry_type: str = "REG_DWORD",
) -> RegistrySetting:
    return RegistrySetting(
        id=id,
        side=side,
        hive="HKLM" if side == "computer" else "HKCU",
        key=KEY,
        value_name="TestValue",
        registry_type=registry_type,
        value=value,
        action=action,
    )


def _admx_id(qualified_id: str, side: str, suffix: str) -> str:
    return f"admx-{qualified_id}-{side}-{suffix}"


# --- build_settings_browser: resolution ------------------------------------


def test_settings_with_admx_prefix_resolve_to_policy() -> None:
    policy = _policy(value_name="TestValue")
    cat = _catalogue(policies=(policy,))
    qid = policy.qualified_id
    settings = [
        _setting(id=_admx_id(qid, "computer", "state"), value=1),
    ]
    result = build_settings_browser(cat, settings)
    assert len(result.resolved) == 1
    assert result.resolved[0].policy_id == qid
    assert result.resolved[0].display_name == "Test Policy"
    assert len(result.unresolved) == 0


def test_settings_without_admx_prefix_are_unresolved() -> None:
    policy = _policy()
    cat = _catalogue(policies=(policy,))
    settings = [_setting(id="manual-some-key", value=1)]
    result = build_settings_browser(cat, settings)
    assert len(result.resolved) == 0
    assert len(result.unresolved) == 1
    assert result.unresolved[0].reason == "no matching policy"


def test_settings_with_unknown_policy_are_unresolved() -> None:
    cat = _catalogue(policies=(_policy(),))
    settings = [
        _setting(id=_admx_id("Unknown.Namespace:Ghost", "computer", "state"), value=1),
    ]
    result = build_settings_browser(cat, settings)
    assert len(result.resolved) == 0
    assert len(result.unresolved) == 1
    assert result.unresolved[0].reason == "template not loaded"


def test_empty_settings_yield_empty_result() -> None:
    cat = _catalogue(policies=(_policy(),))
    result = build_settings_browser(cat, [])
    assert result.resolved == ()
    assert result.unresolved == ()


# --- build_settings_browser: state derivation -------------------------------


def test_state_delete_on_state_setting_yields_disabled() -> None:
    policy = _policy(value_name="TestValue")
    cat = _catalogue(policies=(policy,))
    qid = policy.qualified_id
    settings = [
        _setting(id=_admx_id(qid, "computer", "state"), action="delete", value=0),
    ]
    result = build_settings_browser(cat, settings)
    assert result.resolved[0].state == "disabled"


def test_state_set_on_state_setting_yields_enabled() -> None:
    policy = _policy(value_name="TestValue")
    cat = _catalogue(policies=(policy,))
    qid = policy.qualified_id
    settings = [
        _setting(id=_admx_id(qid, "computer", "state"), action="set", value=1),
    ]
    result = build_settings_browser(cat, settings)
    assert result.resolved[0].state == "enabled"


def test_no_state_setting_defaults_to_enabled() -> None:
    policy = _policy()
    cat = _catalogue(policies=(policy,))
    qid = policy.qualified_id
    settings = [
        _setting(id=_admx_id(qid, "computer", "SomeElement"), value=42),
    ]
    result = build_settings_browser(cat, settings)
    assert result.resolved[0].state == "enabled"


# --- build_settings_browser: registry-tuple reverse-index -------------------


def test_registry_tuple_state_value_resolves_without_admx_id() -> None:
    policy = _policy(value_name="TestValue")
    cat = _catalogue(policies=(policy,))
    settings = [
        RegistrySetting(
            id="raw-imported-state",
            side="computer",
            hive="HKLM",
            key=KEY,
            value_name="TestValue",
            registry_type="REG_DWORD",
            value=1,
        ),
    ]
    result = build_settings_browser(cat, settings)
    assert len(result.resolved) == 1
    assert result.resolved[0].policy_id == policy.qualified_id
    assert result.resolved[0].state == "enabled"
    assert len(result.unresolved) == 0


def test_registry_tuple_delete_resolves_as_disabled() -> None:
    policy = _policy(value_name="TestValue")
    cat = _catalogue(policies=(policy,))
    settings = [
        RegistrySetting(
            id="raw-imported-state",
            side="computer",
            hive="HKLM",
            key=KEY,
            value_name="TestValue",
            registry_type="REG_SZ",
            value="",
            action="delete",
        ),
    ]
    result = build_settings_browser(cat, settings)
    assert len(result.resolved) == 1
    assert result.resolved[0].state == "disabled"


def test_registry_tuple_element_value_decodes() -> None:
    elem = PolicyElement(kind="decimal", id="Count", registry_value_name="Count")
    policy = _policy(value_name="State", elements=(elem,))
    cat = _catalogue(policies=(policy,))
    settings = [
        RegistrySetting(
            id="raw-element",
            side="computer",
            hive="HKLM",
            key=KEY,
            value_name="Count",
            registry_type="REG_DWORD",
            value=42,
        ),
    ]
    result = build_settings_browser(cat, settings)
    assert len(result.resolved) == 1
    assert result.resolved[0].element_values["Count"] == 42


def test_both_class_policy_matches_both_sides() -> None:
    policy = _policy(class_="Both", value_name="SharedState")
    cat = _catalogue(policies=(policy,))
    settings = [
        RegistrySetting(
            id="comp-state",
            side="computer",
            hive="HKLM",
            key=KEY,
            value_name="SharedState",
            registry_type="REG_DWORD",
            value=1,
        ),
        RegistrySetting(
            id="user-state",
            side="user",
            hive="HKCU",
            key=KEY,
            value_name="SharedState",
            registry_type="REG_DWORD",
            value=1,
        ),
    ]
    result = build_settings_browser(cat, settings)
    assert len(result.resolved) == 2
    assert {cs.side for cs in result.resolved} == {"computer", "user"}
    assert len(result.unresolved) == 0


def test_ambiguous_registry_tuple_surfaces_all_matches() -> None:
    ns1 = "NS.One"
    ns2 = "NS.Two"
    p1 = _policy(id="Policy1", namespace=ns1, value_name="SharedValue")
    p2 = _policy(id="Policy2", namespace=ns2, value_name="SharedValue")
    cat = _catalogue(policies=(p1, p2))
    settings = [
        RegistrySetting(
            id="ambiguous-raw",
            side="computer",
            hive="HKLM",
            key=KEY,
            value_name="SharedValue",
            registry_type="REG_DWORD",
            value=1,
        ),
    ]
    result = build_settings_browser(cat, settings)
    assert len(result.resolved) == 2
    qids = {cs.policy_id for cs in result.resolved}
    assert qids == {p1.qualified_id, p2.qualified_id}
    assert all(cs.ambiguous for cs in result.resolved)
    for cs in result.resolved:
        assert len(cs.ambiguous_with) == 1
        assert cs.ambiguous_with[0] != cs.policy_id
        assert cs.ambiguous_with[0] in qids
    assert len(result.unresolved) == 0


def test_enabled_list_value_resolves_to_policy() -> None:
    from gpo_studio.admx import PolicyListItem, PolicyValue, PolicyValueList

    policy = _policy(
        value_name="",
        enabled_list=PolicyValueList(
            default_key=KEY,
            items=(
                PolicyListItem(
                    value_name="ListVal",
                    value=PolicyValue(
                        kind="decimal", data="1", registry_type="REG_DWORD"
                    ),
                ),
            ),
        ),
    )
    cat = _catalogue(policies=(policy,))
    settings = [
        RegistrySetting(
            id="raw-listitem",
            side="computer",
            hive="HKLM",
            key=KEY,
            value_name="ListVal",
            registry_type="REG_DWORD",
            value=1,
        ),
    ]
    result = build_settings_browser(cat, settings)
    assert len(result.resolved) == 1
    assert result.resolved[0].policy_id == policy.qualified_id
    assert result.resolved[0].state == "enabled"


def test_explicit_disabled_value_resolves_as_disabled() -> None:
    from gpo_studio.admx import PolicyValue

    policy = _policy(
        value_name="State",
        enabled_value=PolicyValue(
            kind="decimal", data="1", registry_type="REG_DWORD"
        ),
        disabled_value=PolicyValue(
            kind="decimal", data="0", registry_type="REG_DWORD"
        ),
    )
    cat = _catalogue(policies=(policy,))
    settings = [
        RegistrySetting(
            id="raw-state",
            side="computer",
            hive="HKLM",
            key=KEY,
            value_name="State",
            registry_type="REG_DWORD",
            value=0,
        ),
    ]
    result = build_settings_browser(cat, settings)
    assert len(result.resolved) == 1
    assert result.resolved[0].state == "disabled"


def test_synthetic_id_with_ambiguous_tuple_match_marks_ambiguous() -> None:
    p1 = _policy(id="Policy1", value_name="SharedValue")
    p2 = _policy(id="Policy2", value_name="SharedValue")
    cat = _catalogue(policies=(p1, p2))
    settings = [
        RegistrySetting(
            id=f"admx-{p1.qualified_id}-computer-state",
            side="computer",
            hive="HKLM",
            key=KEY,
            value_name="SharedValue",
            registry_type="REG_DWORD",
            value=1,
        ),
    ]
    result = build_settings_browser(cat, settings)
    assert len(result.resolved) == 2
    assert all(cs.ambiguous for cs in result.resolved)


def test_registry_tuple_matching_is_case_insensitive() -> None:
    policy = _policy(value_name="TestValue")
    cat = _catalogue(policies=(policy,))
    settings = [
        RegistrySetting(
            id="raw-state",
            side="computer",
            hive="HKLM",
            key=KEY.upper(),
            value_name="TESTVALUE",
            registry_type="REG_DWORD",
            value=1,
        ),
    ]
    result = build_settings_browser(cat, settings)
    assert len(result.resolved) == 1


# --- build_settings_browser: element value decoding -------------------------


def test_decode_boolean_element() -> None:
    elem = PolicyElement(kind="boolean", id="Toggle", registry_value_name="Toggle")
    policy = _policy(elements=(elem,))
    cat = _catalogue(policies=(policy,))
    qid = policy.qualified_id
    settings = [
        _setting(id=_admx_id(qid, "computer", "state"), value=1),
        _setting(id=_admx_id(qid, "computer", "Toggle"), value=1),
    ]
    result = build_settings_browser(cat, settings)
    assert result.resolved[0].element_values["Toggle"] is True


def test_decode_decimal_element() -> None:
    elem = PolicyElement(kind="decimal", id="Count", registry_value_name="Count")
    policy = _policy(elements=(elem,))
    cat = _catalogue(policies=(policy,))
    qid = policy.qualified_id
    settings = [
        _setting(id=_admx_id(qid, "computer", "state"), value=1),
        _setting(id=_admx_id(qid, "computer", "Count"), value=42),
    ]
    result = build_settings_browser(cat, settings)
    assert result.resolved[0].element_values["Count"] == 42


def test_decode_text_element() -> None:
    elem = PolicyElement(kind="text", id="Path", registry_value_name="Path")
    policy = _policy(elements=(elem,))
    cat = _catalogue(policies=(policy,))
    qid = policy.qualified_id
    settings = [
        _setting(id=_admx_id(qid, "computer", "state"), value=1),
        _setting(
            id=_admx_id(qid, "computer", "Path"),
            value="C:\\test",
            registry_type="REG_SZ",
        ),
    ]
    result = build_settings_browser(cat, settings)
    assert result.resolved[0].element_values["Path"] == "C:\\test"


def test_decode_list_element() -> None:
    elem = PolicyElement(kind="list", id="Items", registry_value_name="Items")
    policy = _policy(elements=(elem,))
    cat = _catalogue(policies=(policy,))
    qid = policy.qualified_id
    settings = [
        _setting(id=_admx_id(qid, "computer", "state"), value=1),
        _setting(
            id=_admx_id(qid, "computer", "Items-0"),
            value="alpha",
            registry_type="REG_SZ",
        ),
        _setting(
            id=_admx_id(qid, "computer", "Items-1"),
            value="beta",
            registry_type="REG_SZ",
        ),
    ]
    result = build_settings_browser(cat, settings)
    assert result.resolved[0].element_values["Items"] == ["alpha", "beta"]


def test_decode_enum_element() -> None:
    elem = PolicyElement(
        kind="enum",
        id="Mode",
        registry_value_name="Mode",
        enum_items=(
            EnumItem(id="Auto", display_name="Automatic", value=1, registry_type="REG_DWORD"),
            EnumItem(id="Manual", display_name="Manual", value=2, registry_type="REG_DWORD"),
        ),
    )
    policy = _policy(elements=(elem,))
    cat = _catalogue(policies=(policy,))
    qid = policy.qualified_id
    settings = [
        _setting(id=_admx_id(qid, "computer", "state"), value=1),
        _setting(id=_admx_id(qid, "computer", "Mode"), value=2),
    ]
    result = build_settings_browser(cat, settings)
    assert result.resolved[0].element_values["Mode"] == "Manual"


# --- build_category_tree ----------------------------------------------------


def test_flat_categories_become_roots() -> None:
    cats = (
        Category(id="A", parent_id="", display_name="Alpha"),
        Category(id="B", parent_id="", display_name="Beta"),
    )
    cat = _catalogue(categories=cats)
    roots = build_category_tree(cat)
    assert len(roots) == 2
    assert {r.id for r in roots} == {"A", "B"}
    assert all(r.children == [] for r in roots)


def test_nested_categories_form_tree() -> None:
    cats = (
        Category(id="Root", parent_id="", display_name="Root"),
        Category(id="Child", parent_id="Root", display_name="Child"),
        Category(id="Grandchild", parent_id="Child", display_name="Grandchild"),
    )
    cat = _catalogue(categories=cats)
    roots = build_category_tree(cat)
    assert len(roots) == 1
    assert roots[0].id == "Root"
    assert len(roots[0].children) == 1
    assert roots[0].children[0].id == "Child"
    assert len(roots[0].children[0].children) == 1
    assert roots[0].children[0].children[0].id == "Grandchild"


def test_policy_counts_propagate_to_parents() -> None:
    cats = (
        Category(id="Root", parent_id="", display_name="Root"),
        Category(id="Child", parent_id="Root", display_name="Child"),
    )
    policies = (
        _policy(id="P1", parent_category="Child"),
        _policy(id="P2", parent_category="Child"),
        _policy(id="P3", parent_category="Root"),
    )
    cat = _catalogue(policies=policies, categories=cats)
    roots = build_category_tree(cat)
    assert roots[0].policy_count == 3
    assert roots[0].children[0].policy_count == 2


# --- search_configured_settings ---------------------------------------------


def _browser_result(
    *settings: tuple[str, PolicyState, list[str]],
) -> SettingsBrowserResult:
    from gpo_studio.settings_browser import ConfiguredSetting

    resolved = tuple(
        ConfiguredSetting(
            policy_id=f"NS:Policy{i}",
            display_name=name,
            explain_text=f"Explains {name}",
            category_path=path,
            category_ids=[f"cat-{n.lower()}" for n in path],
            side="computer",
            state=state,
            element_values={},
            raw_settings=(),
            supported_on="",
            namespace="NS",
        )
        for i, (name, state, path) in enumerate(settings)
    )
    return SettingsBrowserResult(resolved=resolved)


def test_search_filter_by_state() -> None:
    result = _browser_result(
        ("Enabled Policy", "enabled", ["Cat"]),
        ("Disabled Policy", "disabled", ["Cat"]),
    )
    filtered = search_configured_settings(result, None, "enabled", None)
    assert len(filtered.resolved) == 1
    assert filtered.resolved[0].display_name == "Enabled Policy"


def test_search_filter_by_query_display_name() -> None:
    result = _browser_result(
        ("Firewall Rules", "enabled", ["Security"]),
        ("Audit Policy", "enabled", ["Security"]),
    )
    filtered = search_configured_settings(result, "firewall", None, None)
    assert len(filtered.resolved) == 1
    assert filtered.resolved[0].display_name == "Firewall Rules"


def test_search_filter_by_query_policy_id() -> None:
    result = _browser_result(
        ("Policy A", "enabled", ["Cat"]),
        ("Policy B", "enabled", ["Cat"]),
    )
    filtered = search_configured_settings(result, "Policy0", None, None)
    assert len(filtered.resolved) == 1
    assert filtered.resolved[0].policy_id == "NS:Policy0"


def test_search_filter_by_category_id() -> None:
    result = _browser_result(
        ("Policy A", "enabled", ["Security", "Windows"]),
        ("Policy B", "enabled", ["Network"]),
    )
    filtered = search_configured_settings(result, None, None, "cat-security")
    assert len(filtered.resolved) == 1
    assert filtered.resolved[0].display_name == "Policy A"


def test_search_no_filters_returns_all() -> None:
    result = _browser_result(
        ("A", "enabled", ["X"]),
        ("B", "disabled", ["Y"]),
    )
    filtered = search_configured_settings(result, None, None, None)
    assert len(filtered.resolved) == 2


# --- API integration tests ---------------------------------------------------

_ADMX_BROWSER = b"""<?xml version="1.0" encoding="utf-8"?>
<policyDefinitions xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions">
  <policyNamespaces>
    <target prefix="browsertest" namespace="Synthetic.Policies.BrowserTest" />
  </policyNamespaces>
  <categories>
    <category name="BrowserRoot" displayName="$(string.BrowserRoot)" />
    <category name="BrowserChild" displayName="$(string.BrowserChild)">
      <parentCategory ref="BrowserRoot" />
    </category>
  </categories>
  <supportedOn>
    <definition name="Supported_Browser" displayName="$(string.Supported_Browser)" />
  </supportedOn>
  <policies>
    <policy name="ComputerPolicy" class="Machine" key="Software\\Policies\\BrowserTest"
            displayName="$(string.ComputerPolicy)" explainText="$(string.ComputerPolicy_Explain)"
            supportedOn="Supported_Browser" valueName="CompState">
      <parentCategory ref="BrowserChild" />
      <supportedOn ref="Supported_Browser" />
      <enabledValue><decimal value="1" /></enabledValue>
      <disabledValue><decimal value="0" /></disabledValue>
      <elements>
        <boolean id="CompOption" valueName="CompOption" />
      </elements>
    </policy>
    <policy name="UserPolicy" class="User" key="Software\\Policies\\BrowserTest"
            displayName="$(string.UserPolicy)" explainText="$(string.UserPolicy_Explain)"
            supportedOn="Supported_Browser" valueName="UserState">
      <parentCategory ref="BrowserChild" />
      <supportedOn ref="Supported_Browser" />
      <enabledValue><decimal value="1" /></enabledValue>
      <disabledValue><decimal value="0" /></disabledValue>
    </policy>
  </policies>
</policyDefinitions>"""

_ADML_BROWSER = b"""<?xml version="1.0" encoding="utf-8"?>
<policyDefinitionResources xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions">
  <resources>
    <stringTable>
      <string id="BrowserRoot">Browser Root</string>
      <string id="BrowserChild">Browser Child</string>
      <string id="Supported_Browser">Synthetic OS</string>
      <string id="ComputerPolicy">Computer Policy</string>
      <string id="ComputerPolicy_Explain">Computer-side test policy.</string>
      <string id="UserPolicy">User Policy</string>
      <string id="UserPolicy_Explain">User-side test policy.</string>
    </stringTable>
    <presentationTable>
      <presentation id="ComputerPolicy">
        <checkBox refId="CompOption">Comp Option</checkBox>
      </presentation>
      <presentation id="UserPolicy" />
    </presentationTable>
  </resources>
</policyDefinitionResources>"""

_BROWSER_QID = "Synthetic.Policies.BrowserTest"


def _setup_browser_env(tmp_path, monkeypatch):
    admx_dir = tmp_path / "browser_admx"
    admx_dir.mkdir()
    (admx_dir / "browser.admx").write_bytes(_ADMX_BROWSER)
    (admx_dir / "browser.adml").write_bytes(_ADML_BROWSER)
    monkeypatch.setenv("GPO_STUDIO_ADMX_DIR", str(admx_dir))
    if hasattr(app.state, "admx_catalogue"):
        del app.state.admx_catalogue
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False


def test_api_configured_settings_returns_resolved_and_unresolved(
    tmp_path, monkeypatch
) -> None:
    _setup_browser_env(tmp_path, monkeypatch)
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "Browser test"}).json()["gpo"]
        qid = f"{_BROWSER_QID}%3AComputerPolicy"
        client.post(
            f"/api/admx/policies/{qid}/configure",
            json={
                "gpo_guid": gpo["guid"],
                "side": "computer",
                "values": {"CompOption": True},
                "state": "enabled",
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "Configure",
            },
        )
        gpo = client.get(f"/api/gpos/{gpo['guid']}").json()["gpo"]
        client.put(
            f"/api/gpos/{gpo['guid']}/settings/manual-setting",
            json={
                "setting": {
                    "side": "computer",
                    "hive": "HKLM",
                    "key": r"Software\Policies\Manual",
                    "value_name": "ManualVal",
                    "registry_type": "REG_DWORD",
                    "value": "1",
                },
                "expected_revision": gpo["revision"],
            },
        )
        resp = client.get(f"/api/gpos/{gpo['guid']}/configured-settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["resolved_count"] == 1
        assert data["unresolved_count"] == 1
        assert data["resolved"][0]["policy_id"] == f"{_BROWSER_QID}:ComputerPolicy"
        assert data["resolved"][0]["state"] == "enabled"
        assert data["unresolved"][0]["reason"] == "no matching policy"


def test_api_configured_settings_filter_by_state(tmp_path, monkeypatch) -> None:
    _setup_browser_env(tmp_path, monkeypatch)
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "State filter"}).json()["gpo"]
        qid = f"{_BROWSER_QID}%3AComputerPolicy"
        gpo = client.post(
            f"/api/admx/policies/{qid}/configure",
            json={
                "gpo_guid": gpo["guid"],
                "side": "computer",
                "values": {"CompOption": True},
                "state": "enabled",
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "Enable",
            },
        ).json()["gpo"]
        resp = client.get(
            f"/api/gpos/{gpo['guid']}/configured-settings?state=disabled"
        )
        assert resp.status_code == 200
        assert resp.json()["resolved_count"] == 0
        resp = client.get(
            f"/api/gpos/{gpo['guid']}/configured-settings?state=enabled"
        )
        assert resp.json()["resolved_count"] == 1


def test_api_configured_settings_filter_by_side(tmp_path, monkeypatch) -> None:
    _setup_browser_env(tmp_path, monkeypatch)
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "Side filter"}).json()["gpo"]
        comp_qid = f"{_BROWSER_QID}%3AComputerPolicy"
        gpo = client.post(
            f"/api/admx/policies/{comp_qid}/configure",
            json={
                "gpo_guid": gpo["guid"],
                "side": "computer",
                "values": {"CompOption": True},
                "state": "enabled",
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "Enable comp",
            },
        ).json()["gpo"]
        user_qid = f"{_BROWSER_QID}%3AUserPolicy"
        gpo = client.post(
            f"/api/admx/policies/{user_qid}/configure",
            json={
                "gpo_guid": gpo["guid"],
                "side": "user",
                "values": {},
                "state": "enabled",
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "Enable user",
            },
        ).json()["gpo"]
        resp = client.get(
            f"/api/gpos/{gpo['guid']}/configured-settings?side=computer"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["resolved_count"] == 1
        assert data["resolved"][0]["side"] == "computer"
        resp = client.get(
            f"/api/gpos/{gpo['guid']}/configured-settings?side=user"
        )
        data = resp.json()
        assert data["resolved_count"] == 1
        assert data["resolved"][0]["side"] == "user"


def test_api_bulk_policy_state_mixed_sides(tmp_path, monkeypatch) -> None:
    _setup_browser_env(tmp_path, monkeypatch)
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "Mixed sides"}).json()["gpo"]
        comp_qid = f"{_BROWSER_QID}%3AComputerPolicy"
        user_qid = f"{_BROWSER_QID}%3AUserPolicy"
        gpo = client.post(
            f"/api/admx/policies/{comp_qid}/configure",
            json={
                "gpo_guid": gpo["guid"],
                "side": "computer",
                "values": {"CompOption": True},
                "state": "enabled",
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "Enable comp",
            },
        ).json()["gpo"]
        gpo = client.post(
            f"/api/admx/policies/{user_qid}/configure",
            json={
                "gpo_guid": gpo["guid"],
                "side": "user",
                "values": {},
                "state": "enabled",
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "Enable user",
            },
        ).json()["gpo"]
        resp = client.post(
            f"/api/gpos/{gpo['guid']}/bulk-policy-state",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "Disable all",
                "policy_ids": [
                    f"{_BROWSER_QID}:ComputerPolicy",
                    f"{_BROWSER_QID}:UserPolicy",
                ],
                "policy_sides": {
                    f"{_BROWSER_QID}:ComputerPolicy": "computer",
                    f"{_BROWSER_QID}:UserPolicy": "user",
                },
                "target_state": "disabled",
            },
        )
        assert resp.status_code == 200
        disabled = resp.json()["gpo"]
        assert disabled["revision"] == gpo["revision"] + 1
        # Every policy-level write is the disabled value; element value names
        # additionally get a delete record since WI-020 (gpedit parity), and
        # those carry no data.
        assert all(
            item["value"] in ("0", 0) if item["action"] == "set" else item["value"] == ""
            for item in disabled["settings"]
        )
        assert any(item["action"] == "set" for item in disabled["settings"])


def test_api_category_tree(tmp_path, monkeypatch) -> None:
    _setup_browser_env(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/api/admx/categories/tree")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        root = data["items"][0]
        assert root["id"] == "BrowserRoot"
        assert root["display_name"] == "Browser Root"
        assert root["policy_count"] == 2
        assert len(root["children"]) == 1
        child = root["children"][0]
        assert child["id"] == "BrowserChild"
        assert child["policy_count"] == 2


# --- enum rows written before #139 -------------------------------------------


def _dword_enum_policy() -> PolicyDefinition:
    return _policy(
        elements=(
            PolicyElement(
                kind="enum",
                id="Mode",
                registry_value_name="TestValue",
                enum_items=(
                    EnumItem(id="1", display_name="One", value=1, registry_type="REG_DWORD"),
                    EnumItem(id="2", display_name="Two", value=2, registry_type="REG_DWORD"),
                ),
            ),
        ),
    )


def test_enum_row_with_the_wrong_registry_type_is_flagged_not_decoded() -> None:
    """Issue #142: a pre-#139 REG_SZ "1" must not read as the REG_DWORD item 1."""
    policy = _dword_enum_policy()
    qid = policy.qualified_id
    mistyped = _setting(id=_admx_id(qid, "computer", "Mode"), value="1", registry_type="REG_SZ")
    result = build_settings_browser(_catalogue(policies=(policy,)), [mistyped])
    assert result.resolved[0].element_values == {}
    assert result.resolved[0].raw_settings == (mistyped,)
    assert [(u.setting, u.reason) for u in result.unresolved] == [
        (
            mistyped,
            "enum value has the wrong registry type; reconfigure the policy to repair it",
        )
    ]


def test_enum_row_matching_no_item_is_flagged() -> None:
    policy = _dword_enum_policy()
    stray = _setting(id=_admx_id(policy.qualified_id, "computer", "Mode"), value=7)
    result = build_settings_browser(_catalogue(policies=(policy,)), [stray])
    assert result.resolved[0].element_values == {}
    assert [(u.setting, u.reason) for u in result.unresolved] == [
        (stray, "enum value matches no template item")
    ]


def test_enum_row_for_an_itemless_enum_is_flagged() -> None:
    # Before #139 every real enum parsed with no items and stored free text.
    policy = _policy(
        elements=(PolicyElement(kind="enum", id="Mode", registry_value_name="TestValue"),)
    )
    row = _setting(
        id=_admx_id(policy.qualified_id, "computer", "Mode"), value="Z:", registry_type="REG_SZ"
    )
    result = build_settings_browser(_catalogue(policies=(policy,)), [row])
    assert result.resolved[0].element_values == {}
    assert [(u.setting, u.reason) for u in result.unresolved] == [
        (row, "enum template has no readable items")
    ]


def test_correctly_typed_enum_row_decodes_without_a_repair_flag() -> None:
    policy = _dword_enum_policy()
    row = _setting(id=_admx_id(policy.qualified_id, "computer", "Mode"), value=2)
    result = build_settings_browser(_catalogue(policies=(policy,)), [row])
    assert result.resolved[0].element_values == {"Mode": "2"}
    assert result.unresolved == ()


_ADMX_ENUM_REPAIR = _ADMX_BROWSER.replace(
    b'<boolean id="CompOption" valueName="CompOption" />',
    b'<enum id="CompMode" valueName="CompMode">'
    b'<item displayName="$(string.ComputerPolicy)"><value><decimal value="1" /></value></item>'
    b'<item displayName="$(string.ComputerPolicy)"><value><decimal value="2" /></value></item>'
    b"</enum>",
)


def test_api_flags_a_mistyped_enum_row_until_the_policy_is_reconfigured(
    tmp_path, monkeypatch
) -> None:
    assert _ADMX_ENUM_REPAIR != _ADMX_BROWSER
    _setup_browser_env(tmp_path, monkeypatch)
    (tmp_path / "browser_admx" / "browser.admx").write_bytes(_ADMX_ENUM_REPAIR)
    qid = f"{_BROWSER_QID}%3AComputerPolicy"

    def configure(gpo: dict[str, Any]) -> dict[str, Any]:
        response = client.post(
            f"/api/admx/policies/{qid}/configure",
            json={
                "gpo_guid": gpo["guid"],
                "side": "computer",
                "values": {"CompMode": "1"},
                "state": "enabled",
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "Configure",
            },
        )
        assert response.status_code == 200, response.text
        return client.get(f"/api/gpos/{gpo['guid']}").json()["gpo"]

    with TestClient(app) as client:
        gpo = configure(client.post("/api/gpos", json={"name": "Enum repair"}).json()["gpo"])
        store: WorkspaceStore = app.state.store
        stored = store.get_gpo(gpo["guid"])
        (row,) = [s for s in stored.settings if s.value_name == "CompMode"]
        assert (row.registry_type, row.value) == ("REG_DWORD", 1)
        # What the pre-#139 writer stored for the same choice.
        store.put_settings(
            gpo["guid"],
            stored.revision,
            [replace(row, registry_type="REG_SZ", value="1")],
            identity="tester",
            reason="Seed a pre-fix enum row",
        )
        data = client.get(f"/api/gpos/{gpo['guid']}/configured-settings").json()
        assert data["resolved"][0]["element_values"] == {}
        assert [(u["value_name"], u["registry_type"], u["reason"]) for u in data["unresolved"]] == [
            (
                "CompMode",
                "REG_SZ",
                "enum value has the wrong registry type; reconfigure the policy to repair it",
            )
        ]

        # Reconfiguring rewrites the row with the template's type.
        gpo = configure(client.get(f"/api/gpos/{gpo['guid']}").json()["gpo"])
        (repaired,) = [s for s in store.get_gpo(gpo["guid"]).settings if s.value_name == "CompMode"]
        assert (repaired.registry_type, repaired.value) == ("REG_DWORD", 1)
        data = client.get(f"/api/gpos/{gpo['guid']}/configured-settings").json()
        assert data["resolved"][0]["element_values"] == {"CompMode": "1"}
        assert data["unresolved"] == []


def _delete_item_enum(*, value_name: str = "") -> PolicyDefinition:
    # The shape of inetres.admx NeedEdgeBrowser: "None" is a <delete/> item.
    return _policy(
        value_name=value_name,
        enabled_value=PolicyValue("decimal", "1", "REG_DWORD") if value_name else None,
        elements=(
            PolicyElement(
                kind="enum",
                id="Mode",
                registry_value_name="TestValue",
                enum_items=(
                    EnumItem(
                        id="item-0", display_name="None", value="", registry_type="REG_SZ",
                        action="delete",
                    ),
                    EnumItem(id="1", display_name="One", value=1, registry_type="REG_DWORD"),
                ),
            ),
        ),
    )


@pytest.mark.parametrize("value_name", ["", "State"], ids=["child-only", "with-state"])
def test_enabled_delete_item_round_trips_through_configure_and_read(value_name: str) -> None:
    """Issue #142: selecting a <delete/> item writes a delete that reads back as it."""
    policy = _delete_item_enum(value_name=value_name)
    rows = resolve_policy(policy, PolicyConfiguration("computer", {"Mode": "item-0"}))
    (child,) = [row for row in rows if row.value_name == "TestValue"]
    assert (child.action, child.registry_type, child.value) == ("delete", "REG_SZ", "")
    result = build_settings_browser(_catalogue(policies=(policy,)), rows)
    assert [(group.state, group.element_values) for group in result.resolved] == [
        ("enabled", {"Mode": "item-0"})
    ]
    assert result.unresolved == ()


def test_disabled_policy_delete_is_not_read_as_the_delete_item() -> None:
    policy = _delete_item_enum(value_name="State")
    policy = replace(policy, disabled_value=PolicyValue("decimal", "0", "REG_DWORD"))
    rows = resolve_policy(policy, PolicyConfiguration("computer", {}, state="disabled"))
    result = build_settings_browser(_catalogue(policies=(policy,)), rows)
    assert [(group.state, group.element_values) for group in result.resolved] == [
        ("disabled", {})
    ]
    assert result.unresolved == ()


def test_stray_enum_delete_in_an_enabled_policy_is_flagged() -> None:
    # Sol's converse case: the enum has no <delete/> item, so an element-level
    # delete in an enabled policy selects nothing the template defines.
    policy = replace(
        _dword_enum_policy(), value_name="State",
        enabled_value=PolicyValue("decimal", "1", "REG_DWORD"),
    )
    rows = resolve_policy(policy, PolicyConfiguration("computer", {"Mode": "1"}))
    stray = [
        replace(row, action="delete", registry_type="REG_SZ", value="")
        if row.value_name == "TestValue" else row
        for row in rows
    ]
    result = build_settings_browser(_catalogue(policies=(policy,)), stray)
    assert [(group.state, group.element_values) for group in result.resolved] == [
        ("enabled", {})
    ]
    assert [u.reason for u in result.unresolved] == ["enum value matches no template item"]


def test_mistyped_enum_row_with_a_foreign_id_says_to_replace_the_row() -> None:
    # Issue #142 review: reconfigure refuses a row it does not own
    # (conflicting_retained_setting), so "reconfigure" would be the wrong hint.
    policy = _dword_enum_policy()
    foreign = _setting(id="imported-row", value="1", registry_type="REG_SZ")
    result = build_settings_browser(_catalogue(policies=(policy,)), [foreign])
    assert result.resolved[0].element_values == {}
    assert [(u.setting, u.reason) for u in result.unresolved] == [
        (
            foreign,
            "enum value has the wrong registry type; remove or replace this raw "
            "row before reconfiguring the policy",
        )
    ]


_ADMX_DELETE_ITEM = _ADMX_BROWSER.replace(
    b'<boolean id="CompOption" valueName="CompOption" />',
    b'<enum id="CompMode" valueName="CompMode">'
    b'<item displayName="$(string.ComputerPolicy)"><value><delete /></value></item>'
    b'<item displayName="$(string.ComputerPolicy)"><value><decimal value="1" /></value></item>'
    b"</enum>",
)


def test_api_delete_item_and_foreign_mistyped_row(tmp_path, monkeypatch) -> None:
    assert _ADMX_DELETE_ITEM != _ADMX_BROWSER
    _setup_browser_env(tmp_path, monkeypatch)
    (tmp_path / "browser_admx" / "browser.admx").write_bytes(_ADMX_DELETE_ITEM)
    qid = f"{_BROWSER_QID}%3AComputerPolicy"
    with TestClient(app) as client:
        detail = client.get(f"/api/admx/policies/{qid}").json()
        assert [(i["id"], i["action"]) for i in detail["elements"][0]["enum_items"]] == [
            ("item-0", "delete"),
            ("1", "set"),
        ]
        gpo = client.post("/api/gpos", json={"name": "Delete item"}).json()["gpo"]
        body = {
            "gpo_guid": gpo["guid"],
            "side": "computer",
            "values": {"CompMode": "item-0"},
            "state": "enabled",
            "expected_revision": gpo["revision"],
            "actor": "tester",
            "reason": "Choose None",
        }
        response = client.post(f"/api/admx/policies/{qid}/configure", json=body)
        assert response.status_code == 200, response.text
        store: WorkspaceStore = app.state.store
        head = store.get_gpo(gpo["guid"])
        (row,) = [s for s in head.settings if s.value_name == "CompMode"]
        assert (row.action, row.registry_type, row.value) == ("delete", "REG_SZ", "")
        data = client.get(f"/api/gpos/{gpo['guid']}/configured-settings").json()
        assert data["resolved"][0]["element_values"] == {"CompMode": "item-0"}
        assert data["unresolved"] == []

        # A mistyped row under a foreign id cannot be repaired by reconfiguring.
        foreign = replace(row, id="imported-row", action="set", value="1")
        head = store.put_settings(
            head.guid, head.revision, [foreign], identity="tester", reason="Import a row"
        )
        head = store.replace_selected_settings(
            head.guid,
            head.revision,
            frozenset(i for i, s in enumerate(head.settings) if s.id == row.id),
            [],
            identity="tester",
            reason="Leave only the foreign row",
        )
        data = client.get(f"/api/gpos/{gpo['guid']}/configured-settings").json()
        assert [u["reason"] for u in data["unresolved"]] == [
            "enum value has the wrong registry type; remove or replace this raw "
            "row before reconfiguring the policy"
        ]
        retry = client.post(
            f"/api/admx/policies/{qid}/configure",
            json={**body, "values": {"CompMode": "1"}, "expected_revision": head.revision},
        )
        assert retry.status_code == 422
        assert retry.json()["error"]["issues"][0]["code"] == "conflicting_retained_setting"


def _with_state(policy: PolicyDefinition) -> PolicyDefinition:
    return replace(
        policy,
        value_name="State",
        enabled_value=PolicyValue("decimal", "1", "REG_DWORD"),
        disabled_value=PolicyValue("decimal", "0", "REG_DWORD"),
    )


def _state_row(policy: PolicyDefinition, value: int) -> RegistrySetting:
    row = _setting(id=_admx_id(policy.qualified_id, "computer", "state"), value=value)
    return replace(row, value_name="State")


@pytest.mark.parametrize("has_delete_item", [True, False])
def test_enum_delete_beside_an_indeterminate_state_is_reported(has_delete_item: bool) -> None:
    # Issue #142 review: the delete was claimed by its element, staged, and
    # then dropped because only an enabled state resolved staged deletes.
    policy = _with_state(_delete_item_enum() if has_delete_item else _dword_enum_policy())
    delete = _setting(
        id=_admx_id(policy.qualified_id, "computer", "Mode"),
        action="delete", registry_type="REG_SZ", value="",
    )
    result = build_settings_browser(
        _catalogue(policies=(policy,)), [_state_row(policy, 7), delete]
    )
    assert [(group.state, group.element_values) for group in result.resolved] == [
        ("indeterminate", {})
    ]
    assert (
        delete,
        "enum value is deleted, but the policy state is indeterminate, so "
        "this cannot be read as a choice or as a disabled policy",
    ) in [(u.setting, u.reason) for u in result.unresolved]


def test_disabled_element_delete_is_never_read_as_the_delete_item() -> None:
    # Pins the disabled-element guard in _decode_enum on its own: this
    # child-only policy is (wrongly, #147) observed as enabled, so only that
    # guard keeps the disable's delete from reading as the "None" choice.
    policy = _delete_item_enum()
    rows = resolve_policy(policy, PolicyConfiguration("computer", {}, state="disabled"))
    (row,) = rows
    assert row.action == "delete" and "disabled-element" in row.id
    result = build_settings_browser(_catalogue(policies=(policy,)), rows)
    assert [group.element_values for group in result.resolved] == [{}]
    assert result.unresolved == ()


@pytest.mark.parametrize("has_delete_item", [True, False])
def test_imported_delete_in_a_disabled_policy_selects_nothing(has_delete_item: bool) -> None:
    # Pins the enabled-state guard on its own: an imported **del. row has no
    # disabled-element identity, so only the observed disabled state keeps it
    # from reading as the delete item (or from being flagged as stray).
    policy = _with_state(_delete_item_enum() if has_delete_item else _dword_enum_policy())
    imported = _setting(id="imported-del", action="delete", registry_type="REG_SZ", value="")
    result = build_settings_browser(
        _catalogue(policies=(policy,)), [_state_row(policy, 0), imported]
    )
    assert [(group.state, group.element_values) for group in result.resolved] == [
        ("disabled", {})
    ]
    assert result.unresolved == ()


@pytest.mark.parametrize(
    ("registry_type", "value", "reason"),
    [
        pytest.param(
            "REG_QWORD", 1,
            "enum value has the wrong registry type; reconfigure the policy to repair it",
            id="qword-against-dword-item",
        ),
        pytest.param(
            "REG_DWORD", True, "enum value matches no template item", id="bool-against-int-item"
        ),
    ],
)
def test_enum_match_needs_registry_and_python_types(
    registry_type: str, value: object, reason: str
) -> None:
    # Issue #142 review: removing either type check left every test green.
    policy = _dword_enum_policy()
    row = _setting(
        id=_admx_id(policy.qualified_id, "computer", "Mode"),
        registry_type=registry_type, value=value,  # type: ignore[arg-type]
    )
    result = build_settings_browser(_catalogue(policies=(policy,)), [row])
    assert result.resolved[0].element_values == {}
    assert [(u.setting, u.reason) for u in result.unresolved] == [(row, reason)]
