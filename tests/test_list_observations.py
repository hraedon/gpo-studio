"""Synthetic observed-state counterexamples; no Windows qualification claimed."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpo_studio.admx import AdmxCatalogue, PolicyDefinition, build_catalogue
from gpo_studio.api import app
from gpo_studio.model import RegistrySetting, Side
from gpo_studio.policy_config import PolicyConfiguration, resolve_policy
from gpo_studio.settings_browser import ConfiguredSetting, build_settings_browser
from gpo_studio.store import WorkspaceStore

PACK = Path(__file__).parent / "fixtures" / "admx-list-observations"


def catalogue() -> AdmxCatalogue:
    return build_catalogue(
        (PACK / "observations.admx").read_bytes(), (PACK / "observations.adml").read_bytes()
    )


def policy(name: str) -> PolicyDefinition:
    return next(p for p in catalogue().policies if p.id == name)


def observed(
    name: str, rows: list[RegistrySetting] | tuple[RegistrySetting, ...]
) -> ConfiguredSetting:
    result = build_settings_browser(catalogue(), rows)
    return next(r for r in result.resolved if r.policy_id == f"Synthetic.Observations:{name}")


@pytest.mark.parametrize("side", ["computer", "user"])
@pytest.mark.parametrize(
    "name", ["SetOnly", "Mixed", "AllDelete", "Partial", "Subset", "Composite", "Scalar"]
)
def test_real_xml_disabled_patterns_are_observed_disabled(name: str, side: Side) -> None:
    rows = resolve_policy(policy(name), PolicyConfiguration(side, {}, "disabled"))
    assert observed(name, rows).state == "disabled"


def test_identical_and_mixed_patterns_are_not_invented_enabled() -> None:
    identical = resolve_policy(policy("Identical"), PolicyConfiguration("computer", {}, "enabled"))
    assert observed("Identical", identical).state == "indeterminate"
    rows = resolve_policy(policy("Partial"), PolicyConfiguration("computer", {}, "disabled"))
    mixed = [replace(row, value=1) if row.value_name == "One" else row for row in rows]
    assert observed("Partial", mixed).state == "indeterminate"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("GPO_STUDIO_DB", str(tmp_path / "workspace.db"))
    monkeypatch.setenv("GPO_STUDIO_ADMX_DIR", str(PACK))
    monkeypatch.delattr(app.state, "admx_catalogue", raising=False)
    store = WorkspaceStore(tmp_path / "workspace.db")
    monkeypatch.setattr(app.state, "store", store, raising=False)
    monkeypatch.setattr(app.state, "owns_store", False, raising=False)
    try:
        with TestClient(app) as http:
            yield http
    finally:
        store.close()


def test_api_disabled_read_and_filter_preserve_storage(client: TestClient) -> None:
    created = client.post(
        "/api/gpos",
        json={
            "name": "Synthetic observed state",
            "actor": "test",
            "reason": "Synthetic state proof",
        },
    )
    assert created.status_code == 201
    guid = created.json()["gpo"]["guid"]
    revision = 1
    token = client.get("/api/health").json()["catalogue_revision"]
    for name in ("SetOnly", "Mixed", "AllDelete", "Identical", "Subset"):
        response = client.post(
            f"/api/admx/policies/Synthetic.Observations:{name}/configure",
            json={
                "gpo_guid": guid,
                "side": "computer",
                "state": "disabled",
                "values": {},
                "expected_revision": revision,
                "expected_catalogue_revision": token,
                "actor": "test",
                "reason": "Synthetic explicit disabled",
            },
        )
        assert response.status_code == 200, response.text
        revision = response.json()["gpo"]["revision"]
    store: WorkspaceStore = app.state.store
    before = "\n".join(store._connection.iterdump())
    result = client.get(f"/api/gpos/{guid}/configured-settings").json()
    rows = {r["policy_id"].split(":")[-1]: r for r in result["resolved"]}
    assert (
        rows["SetOnly"]["state"]
        == rows["Mixed"]["state"]
        == rows["AllDelete"]["state"]
        == rows["Subset"]["state"]
        == "disabled"
    )
    assert rows["Identical"]["state"] == "indeterminate"
    for state, count in (("disabled", 4), ("enabled", 0), ("indeterminate", 1)):
        response = client.get(f"/api/gpos/{guid}/configured-settings", params={"state": state})
        assert response.status_code == 200
        assert response.json()["resolved_count"] == count
    assert result["gpo_revision"] == revision and result["catalogue_revision"] == token
    assert "\n".join(store._connection.iterdump()) == before


def raw(
    name: str,
    value_name: str,
    value: int | str = 0,
    *,
    action: str = "set",
    registry_type: str = "REG_DWORD",
    side: Side = "computer",
    key: str | None = None,
) -> RegistrySetting:
    return RegistrySetting(
        "synthetic-native-" + value_name,
        side,
        "HKLM" if side == "computer" else "HKCU",
        key or policy(name).key,
        value_name,
        registry_type,
        value,
        action,
    )  # type: ignore[arg-type]


@pytest.mark.parametrize("side", ["computer", "user"])
def test_independent_native_golden_patterns_and_subset_counterexample(side: Side) -> None:
    subset = [raw("Subset", "One", 1, side=side), raw("Subset", "Two", 2, side=side)]
    assert observed("Subset", subset).state == "disabled"  # Enabled is only a subset.
    assert observed("Subset", subset[:1]).state == "enabled"
    mixed = [
        raw("Mixed", "Flag", 0, side=side),
        raw("Mixed", "Legacy", "ignored", action="delete", registry_type="REG_SZ", side=side),
    ]
    assert observed("Mixed", mixed).state == "disabled"
    assert observed("Mixed", list(reversed(mixed))).state == "disabled"
    mismatched = [raw("Partial", "One", 1, side=side), raw("Partial", "Two", 0, side=side)]
    assert observed("Partial", mismatched).state == "indeterminate"
    assert observed("Partial", mismatched).state_reason == "no_complete_pattern"
    identical = observed("Identical", [raw("Identical", "Flag", 7, side=side)])
    assert identical.state == "indeterminate" and identical.state_reason == "both_patterns_match"


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "wrong_type",
        "wrong_value",
        "wrong_action",
        "duplicate",
        "unexpected_state",
        "delete_all",
    ],
)
def test_incomplete_or_unsupported_records_do_not_guess(change: str) -> None:
    rows = [raw("Partial", "One", 0), raw("Partial", "Two", 0)]
    if change == "missing":
        rows.pop()
    elif change == "wrong_type":
        rows[0] = replace(rows[0], registry_type="REG_SZ", value="0")
    elif change == "wrong_value":
        rows[0] = replace(rows[0], value=5)
    elif change == "wrong_action":
        rows[0] = replace(rows[0], action="delete")
    elif change == "duplicate":
        rows.append(replace(rows[0], id="another-native-id"))
    elif change == "unexpected_state":
        rows.append(
            replace(
                rows[0],
                id="admx-Synthetic.Observations:Partial-computer-listitem-9",
                key=policy("Partial").key + r"\Unexpected",
            )
        )
    else:
        rows.append(
            replace(
                rows[0],
                id="admx-Synthetic.Observations:Partial-computer-listitem-9",
                value_name="",
                action="delete_all_values",
            )
        )
    assert observed("Partial", rows).state == "indeterminate"


def test_casefold_addresses_keep_exact_native_values_and_default_key_rules() -> None:
    p = policy("Composite")
    rows = [
        raw("Composite", "Marker", 0),
        raw("Composite", "Default", 123, action="delete", key=p.key + r"\Default"),
        raw("Composite", "Override", 0, registry_type="REG_QWORD", key=p.key + r"\Override"),
    ]
    rows = [
        replace(row, key=row.key.swapcase(), value_name=row.value_name.swapcase()) for row in rows
    ]
    assert observed("Composite", rows).state == "disabled"
    rows[0] = replace(rows[0], value=1)
    assert observed("Composite", rows).state == "indeterminate"
    enabled = [
        raw("Composite", "Marker", 1),
        raw("Composite", "Default", "on", registry_type="REG_SZ", key=p.key + r"\Default"),
        raw(
            "Composite",
            "Override",
            18446744073709551615,
            registry_type="REG_QWORD",
            key=p.key + r"\Override",
        ),
    ]
    assert observed("Composite", enabled).state == "enabled"
    enabled[1] = replace(enabled[1], value="ON")
    assert observed("Composite", enabled).state == "indeterminate"


def test_empty_patterns_do_not_fabricate_state_or_configured_rows() -> None:
    for name in ("Empty", "OneEmpty"):
        rows = resolve_policy(policy(name), PolicyConfiguration("computer", {}, "disabled"))
        assert rows == []
        assert not build_settings_browser(catalogue(), rows).resolved
    rows = resolve_policy(
        policy("EmptyWithChild"), PolicyConfiguration("computer", {"Child": "retained"}, "enabled")
    )
    result = observed("EmptyWithChild", rows)
    assert result.state == "indeterminate" and result.state_reason == "no_state_evidence"
    rows = resolve_policy(policy("EmptyWithChild"), PolicyConfiguration("computer", {}, "disabled"))
    assert observed("EmptyWithChild", rows).state == "indeterminate"
    assert observed("OneEmpty", [raw("OneEmpty", "Flag", 1)]).state == "enabled"


def test_indeterminate_is_never_a_mutation_target_or_export_blocker(client: TestClient) -> None:
    gpo = client.post("/api/gpos", json={"name": "Synthetic no hidden intent"}).json()["gpo"]
    token = client.get("/api/health").json()["catalogue_revision"]
    body = {
        "gpo_guid": gpo["guid"],
        "side": "computer",
        "state": "disabled",
        "expected_revision": 1,
        "expected_catalogue_revision": token,
        "actor": "test",
        "reason": "Identical state fixture",
    }
    configured = client.post(
        "/api/admx/policies/Synthetic.Observations:Identical/configure", json=body
    )
    assert configured.status_code == 200
    store: WorkspaceStore = app.state.store
    before = "\n".join(store._connection.iterdump())
    body.update(state="indeterminate", expected_revision=2)
    assert (
        client.post(
            "/api/admx/policies/Synthetic.Observations:Identical/configure", json=body
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/gpos/{gpo['guid']}/bulk-policy-state",
            json={
                "target_state": "indeterminate",
                "policy_ids": ["Synthetic.Observations:Identical"],
                "side": "computer",
                "expected_revision": 2,
                "expected_catalogue_revision": token,
            },
        ).status_code
        == 422
    )
    assert (
        client.get(
            f"/api/gpos/{gpo['guid']}/configured-settings", params={"state": "not_configured"}
        ).status_code
        == 422
    )
    current = client.get(f"/api/gpos/{gpo['guid']}").json()
    assert current["validation"] == []
    assert current["artifact_capabilities"]["gpmc_export"]["enabled"] is True
    assert current["artifact_capabilities"]["studio_export"]["enabled"] is True
    assert len(client.get(f"/api/gpos/{gpo['guid']}/revisions").json()["items"]) == 2
    assert "\n".join(store._connection.iterdump()) == before


def test_exact_projection_rejects_disjoint_mixtures_and_handles_reversed_delete() -> None:
    from gpo_studio.admx import PolicyListItem, PolicyValue, PolicyValueList

    p = replace(
        policy("Subset"),
        enabled_list=PolicyValueList(
            (PolicyListItem("One", PolicyValue("decimal", "1", "REG_DWORD")),)
        ),
        disabled_list=PolicyValueList(
            (PolicyListItem("Two", PolicyValue("decimal", "0", "REG_DWORD")),)
        ),
    )
    cat = AdmxCatalogue(policies=(p,))
    rows = [raw("Subset", "One", 1), raw("Subset", "Two", 0)]
    assert build_settings_browser(cat, rows).resolved[0].state == "indeterminate"
    assert build_settings_browser(cat, rows[:1]).resolved[0].state == "enabled"
    assert build_settings_browser(cat, rows[1:]).resolved[0].state == "disabled"
    original = policy("AllDelete")
    reversed_policy = replace(
        original, enabled_list=original.disabled_list, disabled_list=original.enabled_list
    )
    deleted = [raw("AllDelete", "Flag", 999, action="delete")]
    assert (
        build_settings_browser(AdmxCatalogue(policies=(reversed_policy,)), deleted)
        .resolved[0]
        .state
        == "enabled"
    )


def test_unsupported_pattern_does_not_abort_other_policies() -> None:
    from gpo_studio.admx import PolicyValueList

    p = policy("SetOnly")
    assert p.enabled_list is not None
    bad = replace(p, enabled_list=PolicyValueList(p.enabled_list.items * 2))
    rows = [
        raw("SetOnly", "Flag", 1),
        raw("Mixed", "Flag", 0),
        raw("Mixed", "Legacy", "", action="delete", registry_type="REG_SZ"),
    ]
    cat = replace(catalogue(), policies=(bad, policy("Mixed")))
    result = {r.policy_id.split(":")[-1]: r for r in build_settings_browser(cat, rows).resolved}
    assert result["SetOnly"].state == "indeterminate"
    assert result["SetOnly"].state_reason == "unsupported_state_pattern"
    assert result["Mixed"].state == "disabled"


@pytest.mark.parametrize("size", [1, 12, 40, 100])
def test_pattern_work_is_bounded_per_resolved_policy_side(
    size: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gpo_studio.settings_browser as browser
    from gpo_studio.admx import PolicyListItem, PolicyValue, PolicyValueList

    p = replace(
        policy("SetOnly"),
        enabled_list=PolicyValueList(
            tuple(
                PolicyListItem(f"Flag{i}", PolicyValue("decimal", "1", "REG_DWORD"))
                for i in range(size)
            )
        ),
        disabled_list=PolicyValueList(
            tuple(
                PolicyListItem(f"Flag{i}", PolicyValue("decimal", "0", "REG_DWORD"))
                for i in range(size)
            )
        ),
    )
    rows = [
        RegistrySetting(
            f"native-{side}-{i}",
            side,
            "HKLM" if side == "computer" else "HKCU",
            p.key,
            f"Flag{i}",
            "REG_DWORD",
            0,
        )
        for side in ("computer", "user")
        for i in range(size)
    ]
    compile_pattern = browser._state_pattern
    select_family = browser.structured_policy_ids
    convert_value = browser.policy_value_write
    calls = {"compile": 0, "family": 0, "convert": 0}

    def counted_pattern(*args, **kwargs):
        calls["compile"] += 1
        return compile_pattern(*args, **kwargs)

    def counted_family(*args, **kwargs):
        calls["family"] += 1
        return select_family(*args, **kwargs)

    def counted_conversion(*args, **kwargs):
        calls["convert"] += 1
        return convert_value(*args, **kwargs)

    monkeypatch.setattr(browser, "_state_pattern", counted_pattern)
    monkeypatch.setattr(browser, "structured_policy_ids", counted_family)
    monkeypatch.setattr(browser, "policy_value_write", counted_conversion)
    cat = replace(catalogue(), policies=(p, replace(p, id="Unused", key=p.key + r"\Unused")))
    result = build_settings_browser(cat, rows)
    assert len(result.resolved) == 2
    assert all(row.state == "disabled" for row in result.resolved)
    assert calls == {"compile": 4, "family": 2, "convert": 4 * size}


def test_explicit_legal_bulk_on_indeterminate_row_remains_possible(client: TestClient) -> None:
    gpo = client.post("/api/gpos", json={"name": "Synthetic explicit legal action"}).json()["gpo"]
    token = client.get("/api/health").json()["catalogue_revision"]
    body = {
        "gpo_guid": gpo["guid"],
        "side": "computer",
        "state": "enabled",
        "expected_revision": 1,
        "expected_catalogue_revision": token,
    }
    assert (
        client.post(
            "/api/admx/policies/Synthetic.Observations:Identical/configure", json=body
        ).status_code
        == 200
    )
    result = client.post(
        f"/api/gpos/{gpo['guid']}/bulk-policy-state",
        json={
            "target_state": "disabled",
            "policy_ids": ["Synthetic.Observations:Identical"],
            "side": "computer",
            "expected_revision": 2,
            "expected_catalogue_revision": token,
            "actor": "test",
            "reason": "Explicit reviewed disabled target",
        },
    )
    assert result.status_code == 200
    assert result.json()["gpo"]["revision"] == 3
    observed_row = client.get(f"/api/gpos/{gpo['guid']}/configured-settings").json()["resolved"][0]
    assert observed_row["state"] == "indeterminate"
    assert observed_row["state_reason"] == "both_patterns_match"


def test_structured_child_listitem_is_not_policy_state_evidence() -> None:
    from gpo_studio.admx import PolicyElement

    p = replace(
        policy("Partial"),
        elements=(
            PolicyElement(
                kind="list", id="listitem", registry_key=policy("Partial").key + r"\Children"
            ),
        ),
    )
    enabled = resolve_policy(
        p, PolicyConfiguration("computer", {"listitem": ["retained child"]}, "enabled")
    )
    child = next(row for row in enabled if row.key.endswith(r"\Children"))
    disabled = resolve_policy(p, PolicyConfiguration("computer", {}, "disabled"))
    assert child.id.startswith("admx2:") and all(row.id.startswith("admx2:") for row in disabled)
    result = build_settings_browser(AdmxCatalogue(policies=(p,)), [*disabled, child])
    assert result.resolved[0].state == "disabled"
    assert result.resolved[0].element_values == {"listitem": ["retained child"]}


def test_wrong_native_side_or_hive_cannot_establish_a_list_state() -> None:
    wrong_hive = replace(raw("SetOnly", "Flag", 0), hive="HKCU")
    result = build_settings_browser(catalogue(), [wrong_hive])
    assert not result.resolved
    assert len(result.unresolved) == 1


@pytest.mark.parametrize("element_id", ["listitem-0", "listitem-01", "listitem-٢"])
def test_nonordinal_legacy_child_names_do_not_become_state_records(element_id: str) -> None:
    from gpo_studio.admx import PolicyElement

    p = replace(
        policy("Partial"),
        elements=(PolicyElement(kind="text", id=element_id, registry_value_name="Child"),),
    )
    enabled = resolve_policy(
        p, PolicyConfiguration("computer", {element_id: "retained child"}, "enabled")
    )
    child = next(row for row in enabled if row.value_name == "Child")
    disabled = resolve_policy(p, PolicyConfiguration("computer", {}, "disabled"))
    disabled = [row for row in disabled if row.value_name != "Child"]
    assert child.id.startswith("admx-")
    result = build_settings_browser(AdmxCatalogue(policies=(p,)), [*disabled, child])
    assert result.resolved[0].state == "disabled"


@pytest.mark.parametrize(
    "invalid", ["missing_type", "invalid_decimal", "unsupported_type", "overflow", "negative"]
)
def test_unrepresentable_source_value_reports_indeterminate(invalid: str) -> None:
    from gpo_studio.admx import PolicyListItem, PolicyValue, PolicyValueList

    value = {
        "missing_type": PolicyValue("decimal", "1", None),
        "invalid_decimal": PolicyValue("decimal", "not a decimal", "REG_DWORD"),
        "unsupported_type": PolicyValue("string", "00FF", "REG_BINARY"),
        "overflow": PolicyValue("decimal", "4294967296", "REG_DWORD"),
        "negative": PolicyValue("longDecimal", "-1", "REG_QWORD"),
    }[invalid]
    p = replace(policy("SetOnly"), enabled_list=PolicyValueList((PolicyListItem("Flag", value),)))
    row = build_settings_browser(
        AdmxCatalogue(policies=(p,)), [raw("SetOnly", "Flag", 0)]
    ).resolved[0]
    assert row.state == "indeterminate" and row.state_reason == "unsupported_state_pattern"


def test_ownership_ambiguity_remains_separate_from_native_state_evidence() -> None:
    p = policy("SetOnly")
    alternate = replace(p, id="AlternatePolicy")
    rows = build_settings_browser(
        AdmxCatalogue(policies=(p, alternate)), [raw("SetOnly", "Flag", 0)]
    ).resolved
    assert len(rows) == 2
    assert all(row.state == "disabled" and row.state_reason is None for row in rows)
    assert all(row.ambiguous and len(row.ambiguous_with) == 1 for row in rows)


@pytest.mark.parametrize(("policy_class", "side"), [("Machine", "user"), ("User", "computer")])
def test_metadata_owner_does_not_prove_state_on_unsupported_policy_side(
    policy_class: str, side: Side
) -> None:
    p = replace(policy("SetOnly"), class_=policy_class)  # type: ignore[arg-type]
    row = replace(
        raw("SetOnly", "Flag", 0, side=side), id=f"admx-{p.qualified_id}-{side}-listitem-1"
    )
    result = build_settings_browser(AdmxCatalogue(policies=(p,)), [row]).resolved[0]
    assert result.side == side  # Preserve the existing ownership/read boundary.
    assert result.state == "indeterminate"
    assert result.state_reason == "unsupported_state_pattern"


def test_reads_preserve_existing_retained_documents_and_history(client: TestClient) -> None:
    import base64

    from gpo_studio.model import BackupInventory

    store: WorkspaceStore = app.state.store
    inventory = BackupInventory(
        backup_xml_base64=base64.b64encode(
            b'<BackupInst xmlns="http://www.microsoft.com/GroupPolicy/GPOOperations"><GroupPolicyObject><GroupPolicyCoreSettings><ID>00000000-0000-4000-8000-000000000088</ID></GroupPolicyCoreSettings></GroupPolicyObject></BackupInst>'
        ).decode(),
        report_xml_base64=base64.b64encode(
            b'<GPO xmlns="http://www.microsoft.com/GroupPolicy/Settings" xmlns:t="http://www.microsoft.com/GroupPolicy/Types"><Identifier><t:Identifier>00000000-0000-4000-8000-000000000088</t:Identifier></Identifier></GPO>'
        ).decode(),
    )
    gpo = store.create_gpo(
        "Synthetic retained read fixture",
        settings=(raw("SetOnly", "Flag", 0),),
        backup_inventory=inventory,
        identity="test",
        reason="Synthetic document preservation",
    )
    gpo = store.update_metadata(
        gpo.guid,
        1,
        {"description": "Synthetic retained second revision"},
        identity="test",
        reason="Synthetic history preservation",
    )
    assert store._connection.execute("SELECT COUNT(*) FROM retained_documents").fetchone()[0] == 2
    before = "\n".join(store._connection.iterdump())
    for query in (
        {},
        {"state": "disabled"},
        {"state": "indeterminate"},
        {"side": "computer", "q": "set-only", "limit": 1},
    ):
        response = client.get(f"/api/gpos/{gpo.guid}/configured-settings", params=query)
        assert response.status_code == 200
        assert "\n".join(store._connection.iterdump()) == before
    assert store.get_gpo(gpo.guid).backup_inventory == inventory
    assert len(store.revisions(gpo.guid)) == 2
