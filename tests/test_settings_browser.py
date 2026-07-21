from __future__ import annotations

from fastapi.testclient import TestClient

from gpo_studio.admx import (
    AdmxCatalogue,
    Category,
    EnumItem,
    PolicyDefinition,
    PolicyElement,
    SupportedOnDefinition,
)
from gpo_studio.api import app
from gpo_studio.model import RegistrySetting
from gpo_studio.policy_config import PolicyState
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
    namespace: str = NS,
    display_name: str = "Test Policy",
    explain_text: str = "Explains the test policy.",
    parent_category: str = "TestCategory",
    supported_on: str = "Supported_Test",
    elements: tuple[PolicyElement, ...] = (),
) -> PolicyDefinition:
    return PolicyDefinition(
        id=id,
        class_="Machine",
        key=KEY,
        display_name=display_name,
        explain_text=explain_text,
        supported_on=supported_on,
        namespace=namespace,
        parent_category=parent_category,
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
    policy = _policy()
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
    policy = _policy()
    cat = _catalogue(policies=(policy,))
    qid = policy.qualified_id
    settings = [
        _setting(id=_admx_id(qid, "computer", "state"), action="delete", value=0),
    ]
    result = build_settings_browser(cat, settings)
    assert result.resolved[0].state == "disabled"


def test_state_set_on_state_setting_yields_enabled() -> None:
    policy = _policy()
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
            category_ids=path,
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
    filtered = search_configured_settings(result, None, None, "Security")
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
