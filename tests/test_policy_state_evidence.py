"""Policy-level observation is native evidence, never recovered authoring intent."""

from __future__ import annotations

import json
import subprocess
import sys
import types
from collections.abc import Iterator
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

from gpo_studio.admx import AdmxCatalogue, PolicyDefinition, PolicyValue, parse_admx
from gpo_studio.api import app
from gpo_studio.model import RegistrySetting, Side
from gpo_studio.policy_config import PolicyConfiguration, resolve_policy
from gpo_studio.settings_browser import build_settings_browser
from gpo_studio.store import WorkspaceStore

KEY = r"Software\Policies\Synthetic\StateEvidence"


@pytest.fixture(scope="module")
def old_producer() -> Iterator[Any]:
    commit = "eec9cc0ba8fbf72ae74f43e8f1ca44a61f2ee3e6"
    source = subprocess.check_output(
        ["git", "show", f"{commit}:src/gpo_studio/policy_config.py"],
        cwd=Path(__file__).resolve().parents[1],
    )
    module = types.ModuleType("gpo_studio._state_evidence_old_producer")
    module.__package__ = "gpo_studio"
    sys.modules[module.__name__] = module
    try:
        exec(compile(source, f"{commit}:policy_config.py", "exec"), module.__dict__)
        yield module
    finally:
        del sys.modules[module.__name__]


def schema(child: str = "state", namespace: str = "Synthetic.Evidence") -> PolicyDefinition:
    xml = f'''<policyDefinitions><policyNamespaces>
    <target prefix="synthetic" namespace="{namespace}"/></policyNamespaces>
    <policies><policy name="Collision" class="Both" key="{KEY}" valueName="State">
    <enabledValue><decimal value="1"/></enabledValue>
    <disabledValue><decimal value="0"/></disabledValue>
    <elements><text id="{child}" key="{KEY}\\Children" valueName="Child"/></elements>
    </policy></policies></policyDefinitions>'''.encode()
    return parse_admx(xml)[0][0]


def current(
    policy: PolicyDefinition, side: Side, state: str = "enabled"
) -> tuple[RegistrySetting, ...]:
    return tuple(
        resolve_policy(
            policy,
            PolicyConfiguration(
                side, {policy.elements[0].id: "retained"} if state == "enabled" else {}, state
            ),
        )
    )


def stale(old_producer: Any, policy: PolicyDefinition, side: Side, typed: bool) -> RegistrySetting:
    past = replace(policy, key=KEY + r"\Past", value_name="PastState", elements=())
    if typed:
        # The reserved child selects typed framing for this same owner.
        past = replace(past, elements=policy.elements)
        row = resolve_policy(past, PolicyConfiguration(side, {}, "disabled"))[0]
        assert row.id.startswith("admx2:")
        return row
    return old_producer.resolve_policy(
        past, old_producer.PolicyConfiguration(side, {}, "disabled")
    )[0]


def observed(policy: PolicyDefinition, rows: tuple[RegistrySetting, ...]):
    return build_settings_browser(AdmxCatalogue(policies=(policy,)), rows)


@pytest.mark.parametrize("side", ["computer", "user"])
@pytest.mark.parametrize(
    "namespace", ["Synthetic.Evidence", "https://synthetic.example.test/evidence"]
)
@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("with_current", [False, True])
def test_real_past_producer_cannot_be_current_state_evidence(
    old_producer: Any, side: Side, namespace: str, typed: bool, with_current: bool
) -> None:
    policy = schema(namespace=namespace)
    past = stale(old_producer, policy, side, typed)
    rows = (*current(policy, side), past) if with_current else (past,)
    result = observed(policy, rows)
    group = result.resolved[0]
    assert group.state == "indeterminate" and group.state_reason == "no_complete_pattern"
    assert group.raw_settings == rows
    assert group.element_values == ({"state": "retained"} if with_current else {})
    assert [(u.setting, u.reason) for u in result.unresolved] == [
        (past, "unmatched policy state evidence")
    ]


@pytest.mark.parametrize("side", ["computer", "user"])
@pytest.mark.parametrize("state", ["enabled", "disabled"])
@pytest.mark.parametrize("child", ["state", "Child"])
def test_current_scalar_and_reserved_child_controls(side: Side, state: str, child: str) -> None:
    policy = schema(child)
    rows = current(policy, side, state)
    result = observed(policy, rows)
    assert result.resolved[0].state == state and result.resolved[0].state_reason is None
    assert result.resolved[0].element_values == (
        {child: "retained"} if state == "enabled" else {child: ""}
    )
    assert not result.unresolved


@pytest.mark.parametrize(
    "change",
    [
        "wrong_value",
        "wrong_type",
        "wrong_action",
        "duplicate",
        "missing",
        "identical",
        "reversed",
        "default",
        "delete",
    ],
)
def test_scalar_native_pattern_controls(change: str) -> None:
    p = schema()
    marker, child = current(p, "computer")
    rows = (marker, child)
    expected, reason = "indeterminate", "no_complete_pattern"
    if change == "wrong_value":
        rows = (replace(marker, value=9), child)
    elif change == "wrong_type":
        rows = (replace(marker, registry_type="REG_SZ", value="1"), child)
    elif change == "wrong_action":
        rows = (replace(marker, action="delete"), child)
    elif change == "duplicate":
        rows = (*rows, replace(marker, id="synthetic-duplicate"))
    elif change == "missing":
        rows, reason = (child,), "no_state_evidence"
    elif change == "identical":
        p, reason = replace(p, disabled_value=p.enabled_value), "both_patterns_match"
    elif change == "reversed":
        p = replace(p, enabled_value=p.disabled_value, disabled_value=p.enabled_value)
        expected, reason = "disabled", None
    elif change == "default":
        p = replace(p, enabled_value=None, disabled_value=None)
        rows = (replace(marker, action="delete", value=999),)
        expected, reason = "disabled", None
    elif change == "delete":
        p = replace(p, enabled_value=PolicyValue("delete", "", None))
        rows = (replace(marker, action="delete", value=999),)
        expected, reason = "enabled", None
    group = observed(p, rows).resolved[0]
    assert (group.state, group.state_reason) == (expected, reason)


@pytest.mark.parametrize("child", ["state", "Child", "listitem-01", "listitem-٢"])
def test_child_only_keeps_claimed_values_and_prior_fallback(old_producer: Any, child: str) -> None:
    p = replace(schema(child), value_name="", enabled_value=None, disabled_value=None)
    rows = tuple(
        old_producer.resolve_policy(
            p, old_producer.PolicyConfiguration("computer", {child: "retained"})
        )
    )
    result = observed(p, rows)
    assert result.resolved[0].state == "enabled" and result.resolved[0].state_reason is None
    assert result.resolved[0].element_values == {child: "retained"}
    assert not result.unresolved
    past = stale(old_producer, p, "computer", False)
    result = observed(p, (*rows, past))
    assert result.resolved[0].state == "indeterminate"
    assert result.resolved[0].element_values == {child: "retained"}
    assert result.unresolved[0].setting == past


@given(
    enabled=st.integers(0, 5),
    disabled=st.integers(0, 5),
    actual=st.integers(0, 6),
    side=st.sampled_from(["computer", "user"]),
)
@settings(max_examples=70, deadline=None)
def test_independent_scalar_native_maps(
    enabled: int, disabled: int, actual: int, side: Side
) -> None:
    p = replace(
        schema(),
        enabled_value=PolicyValue("decimal", str(enabled), "REG_DWORD"),
        disabled_value=PolicyValue("decimal", str(disabled), "REG_DWORD"),
    )
    row = RegistrySetting(
        "synthetic-independent",
        side,
        "HKLM" if side == "computer" else "HKCU",
        KEY.swapcase(),
        "sTATE",
        "REG_DWORD",
        actual,
    )
    expected = (
        "indeterminate"
        if (actual == enabled) == (actual == disabled)
        else "enabled"
        if actual == enabled
        else "disabled"
    )
    group = observed(p, (row,)).resolved[0]
    assert group.state == expected
    assert group.raw_settings == (row,)


@pytest.mark.parametrize("typed", [False, True])
def test_api_reads_filters_exports_history_and_explicit_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, old_producer: Any, typed: bool
) -> None:
    p = schema()
    rows = (stale(old_producer, p, "computer", typed),)
    store = WorkspaceStore(tmp_path / "state.db")
    monkeypatch.setattr(app.state, "store", store, raising=False)
    monkeypatch.setattr(app.state, "owns_store", False, raising=False)
    monkeypatch.setattr(app.state, "template_sources", [], raising=False)
    try:
        with TestClient(app, base_url="http://127.0.0.1:4509") as client:
            monkeypatch.setattr(
                app.state, "admx_catalogue", AdmxCatalogue(policies=(p,)), raising=False
            )
            gpo = store.create_gpo(
                "Synthetic state evidence",
                settings=rows,
                identity="test",
                reason="Historical native control",
            )
            before = tuple(store._connection.iterdump())
            before_artifacts = {
                kind: client.get(f"/api/gpos/{gpo.guid}/{kind}").content
                for kind in ("export.zip", "plan.ps1", "gpmc-backup")
            }
            for state, count in [(None, 1), ("indeterminate", 1), ("enabled", 0), ("disabled", 0)]:
                response = client.get(
                    f"/api/gpos/{gpo.guid}/configured-settings",
                    params={} if state is None else {"state": state},
                )
                assert response.status_code == 200 and response.json()["resolved_count"] == count
                assert response.json()["unresolved_count"] == 1
                assert response.json()["gpo_revision"] == 1
            detail = client.get(f"/api/gpos/{gpo.guid}").json()
            assert detail["validation"] == []
            assert detail["artifact_capabilities"]["gpmc_export"]["enabled"] is True
            for kind in ("export.zip", "plan.ps1", "gpmc-backup"):
                artifact = client.get(f"/api/gpos/{gpo.guid}/{kind}")
                assert artifact.status_code == 200 and artifact.content == before_artifacts[kind]
            assert tuple(store._connection.iterdump()) == before
            assert len(store.revisions(gpo.guid)) == 1
            body = {
                "gpo_guid": gpo.guid,
                "side": "computer",
                "state": "indeterminate",
                "expected_revision": 1,
            }
            url = f"/api/admx/policies/{p.qualified_id}/configure"
            assert client.post(url, json=body).status_code == 422
            assert tuple(store._connection.iterdump()) == before
            for revision, state in enumerate(("enabled", "disabled", "not_configured"), 1):
                response = client.post(
                    url,
                    json={
                        **body,
                        "state": state,
                        "values": {"state": "chosen"} if state == "enabled" else {},
                        "expected_revision": revision,
                    },
                )
                assert response.status_code == 200, response.text
            assert store.get_gpo(gpo.guid).settings == ()
    finally:
        store.close()


def test_no_marker_unclaimed_state_frame_is_uncertain() -> None:
    p = replace(schema(), value_name="", enabled_value=None, disabled_value=None, elements=())
    row = RegistrySetting(
        f"admx-{p.qualified_id}-computer-state",
        "computer",
        "HKLM",
        KEY,
        "PastState",
        "REG_DWORD",
        0,
    )
    result = observed(p, (row,))
    assert result.resolved[0].state == "indeterminate"
    assert [(u.setting, u.reason) for u in result.unresolved] == [
        (row, "unmatched policy state evidence")
    ]


def test_uncertain_component_diagnostic_is_not_duplicated_as_state_evidence() -> None:
    from gpo_studio.admx import PolicyElement, PolicyListItem, PolicyValueList

    p = replace(
        schema(),
        elements=(PolicyElement(id="listitem", kind="list", registry_key=KEY + r"\Children"),),
        enabled_list=PolicyValueList(
            (PolicyListItem("Flag", PolicyValue("decimal", "1", "REG_DWORD")),)
        ),
    )
    # This historical spelling can be a policy-list write or a dynamic child item;
    # its stale address does not make either role certain.
    row = RegistrySetting(
        f"admx-{p.qualified_id}-computer-listitem-1",
        "computer",
        "HKLM",
        KEY + r"\Past",
        "Past",
        "REG_SZ",
        "retained",
    )
    result = observed(p, (row,))
    assert result.resolved[0].state == "indeterminate"
    assert result.resolved[0].element_values == {}
    assert [(u.setting, u.reason) for u in result.unresolved] == [
        (row, "ambiguous legacy policy component")
    ]


@pytest.mark.parametrize("side", ["computer", "user"])
def test_wrong_hive_marker_is_not_current_evidence(side: Side) -> None:
    p = schema()
    marker = current(p, side)[0]
    row = replace(marker, hive="HKCU" if side == "computer" else "HKLM")
    result = observed(p, (row,))
    assert result.resolved[0].state == "indeterminate"
    assert result.unresolved[0].setting == row


def test_actual_old_duplicate_frame_head_remains_readable_and_native_exportable(
    old_producer: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = schema()
    old = replace(
        p,
        key=KEY + r"\Past",
        value_name="PastState",
        enabled_value=PolicyValue("decimal", "0", "REG_DWORD"),
    )
    rows = tuple(
        old_producer.resolve_policy(
            old, old_producer.PolicyConfiguration("computer", {"state": "retained"})
        )
    )
    assert len(rows) == 2 and rows[0].id == rows[1].id
    store = WorkspaceStore(tmp_path / "old-head.db")
    monkeypatch.setattr(app.state, "store", store, raising=False)
    monkeypatch.setattr(app.state, "owns_store", False, raising=False)
    monkeypatch.setattr(app.state, "template_sources", [], raising=False)
    try:
        with TestClient(app, base_url="http://127.0.0.1:4509") as client:
            monkeypatch.setattr(
                app.state, "admx_catalogue", AdmxCatalogue(policies=(p,)), raising=False
            )
            gpo = store.create_gpo(
                "Synthetic historical duplicate frame",
                settings=tuple(replace(row, id=f"seed-{index}") for index, row in enumerate(rows)),
                identity="test",
                reason="Synthetic legacy seed",
            )
            # Recreate an immutable legacy snapshot, not a current authoring path.
            data = json.loads(
                store._connection.execute(
                    "SELECT snapshot_json FROM gpos WHERE guid=?", (gpo.guid,)
                ).fetchone()[0]
            )
            data["settings"] = [asdict(row) for row in rows]
            payload = json.dumps(data)
            store._connection.execute(
                "UPDATE gpos SET snapshot_json=? WHERE guid=?", (payload, gpo.guid)
            )
            store._connection.execute(
                "UPDATE revisions SET snapshot_json=? WHERE gpo_guid=?", (payload, gpo.guid)
            )
            store._connection.commit()
            before = tuple(store._connection.iterdump())
            response = client.get(f"/api/gpos/{gpo.guid}/configured-settings").json()
            group = response["resolved"][0]
            assert group["state"] == "indeterminate"
            assert group["element_values"] == {"state": "retained"}
            assert group["raw_setting_count"] == 2 and response["unresolved_count"] == 1
            assert client.get(f"/api/gpos/{gpo.guid}").json()["validation"] == []
            for kind in ("export.zip", "plan.ps1", "gpmc-backup"):
                assert client.get(f"/api/gpos/{gpo.guid}/{kind}").status_code == 200
            assert store.get_gpo(gpo.guid).settings == rows
            assert len(store.revisions(gpo.guid)) == 1
            assert tuple(store._connection.iterdump()) == before
    finally:
        store.close()
