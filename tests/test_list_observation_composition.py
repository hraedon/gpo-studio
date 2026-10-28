"""Legacy role framing and native observed patterns must compose without hidden writes."""

from __future__ import annotations

import subprocess
import sys
import types
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gpo_studio.admx import (
    AdmxCatalogue,
    PolicyDefinition,
    PolicyListItem,
    PolicyValue,
    PolicyValueList,
    parse_admx,
)
from gpo_studio.api import app
from gpo_studio.model import RegistrySetting, Side
from gpo_studio.policy_config import PolicyConfiguration, resolve_policy, structured_policy_ids
from gpo_studio.settings_browser import build_settings_browser
from gpo_studio.store import WorkspaceStore

KEY = r"Software\Policies\Synthetic\StateComposition"


@pytest.fixture(scope="module")
def old_producer() -> Iterator[Any]:
    commit = "eec9cc0ba8fbf72ae74f43e8f1ca44a61f2ee3e6"
    source = subprocess.check_output(
        ["git", "show", f"{commit}:src/gpo_studio/policy_config.py"],
        cwd=Path(__file__).resolve().parents[1],
    )
    module = types.ModuleType("gpo_studio._composition_old_producer")
    module.__package__ = "gpo_studio"
    sys.modules[module.__name__] = module
    try:
        exec(compile(source, f"{commit}:policy_config.py", "exec"), module.__dict__)
        yield module
    finally:
        del sys.modules[module.__name__]


def schema(namespace: str, child: str = "Child") -> PolicyDefinition:
    xml = f'''<policyDefinitions><policyNamespaces>
      <target prefix="synthetic" namespace="{namespace}"/></policyNamespaces>
      <policies><policy name="Projection" class="Both" key="{KEY}" valueName="State">
        <enabledList><item valueName="Flag"><value><decimal value="1"/></value></item></enabledList>
        <disabledList><item valueName="Flag"><value><decimal value="0"/></value></item>
        </disabledList>
        <elements><text id="{child}" key="{KEY}\\Children" valueName="Child"/></elements>
      </policy></policies></policyDefinitions>'''.encode()
    return parse_admx(xml)[0][0]


def current_pattern(
    policy: PolicyDefinition, side: Side, state: str = "enabled"
) -> tuple[RegistrySetting, ...]:
    # Independent native-address admission uses ordinary raw IDs. This also keeps
    # a real legacy stale state ID distinct in the ordinary legacy-family control.
    source = replace(policy, elements=())
    return tuple(
        replace(row, id=f"synthetic-current-{index}")
        for index, row in enumerate(resolve_policy(source, PolicyConfiguration(side, {}, state)))
    )


def historical_row(
    old_producer: Any, policy: PolicyDefinition, side: Side, kind: str
) -> RegistrySetting:
    past = replace(
        policy,
        key=KEY + r"\Past",
        value_name="PastState",
        elements=(),
        enabled_list=PolicyValueList(
            (PolicyListItem("PastFlag", PolicyValue("decimal", "1", "REG_DWORD")),)
        ),
        disabled_list=None,
    )
    rows = old_producer.resolve_policy(past, old_producer.PolicyConfiguration(side, {}))
    return next(
        row for row in rows if row.id.endswith("-state" if kind == "state" else "-listitem-1")
    )


@pytest.mark.parametrize(
    ("namespace", "child"),
    [
        ("Synthetic.Composition", "Child"),
        ("https://synthetic.example.test/composition", "Child"),
        ("Synthetic.Composition", "state"),
    ],
)
@pytest.mark.parametrize("kind", ["state", "state-list"])
@pytest.mark.parametrize("side", ["computer", "user"])
def test_actual_legacy_state_rows_outside_current_patterns_retire_certainty(
    old_producer: Any,
    namespace: str,
    child: str,
    kind: str,
    side: Side,
) -> None:
    policy = schema(namespace, child)
    stale = historical_row(old_producer, policy, side, kind)
    rows = (*current_pattern(policy, side), stale)
    assert stale.id.startswith("admx-")
    result = build_settings_browser(AdmxCatalogue(policies=(policy,)), rows)
    assert result.resolved[0].state == "indeterminate"
    assert result.resolved[0].state_reason == "no_complete_pattern"
    assert result.resolved[0].raw_settings == rows


@pytest.mark.parametrize(
    "namespace", ["Synthetic.Composition", "https://synthetic.example.test/composition"]
)
@pytest.mark.parametrize(
    "child", ["Child", "listitem-0", "listitem-01", "listitem-٢", "listitem-²"]
)
@pytest.mark.parametrize("side", ["computer", "user"])
def test_nonstate_legacy_children_do_not_invalidate_complete_native_patterns(
    old_producer: Any,
    namespace: str,
    child: str,
    side: Side,
) -> None:
    policy = schema(namespace, child)
    legacy = old_producer.resolve_policy(
        policy, old_producer.PolicyConfiguration(side, {child: "retained"})
    )
    row = next(item for item in legacy if item.value_name == "Child")
    rows = (*current_pattern(policy, side, "disabled"), row)
    result = build_settings_browser(AdmxCatalogue(policies=(policy,)), rows)
    assert result.resolved[0].state == "disabled"
    assert result.resolved[0].state_reason is None
    assert result.resolved[0].element_values == {child: "retained"}
    assert result.resolved[0].raw_settings == rows
    assert not result.unresolved


@pytest.mark.parametrize("child", ["state", "listitem-1"])
def test_canonical_legacy_role_ambiguity_is_not_proof_of_current_pattern(
    old_producer: Any,
    child: str,
) -> None:
    policy = schema("https://synthetic.example.test/ambiguous-component", child)
    legacy = old_producer.resolve_policy(
        policy, old_producer.PolicyConfiguration("computer", {child: "retained"})
    )
    row = next(item for item in legacy if item.value_name == "Child")
    rows = (*current_pattern(policy, "computer"), row)
    before = tuple(rows)
    result = build_settings_browser(AdmxCatalogue(policies=(policy,)), rows)
    assert result.resolved[0].state == "indeterminate"
    assert result.resolved[0].state_reason == "no_complete_pattern"
    assert result.resolved[0].element_values == {child: "retained"}
    assert result.resolved[0].raw_settings == before and rows == before


@pytest.mark.parametrize("kind", ["state", "state-list"])
def test_api_composed_role_observation_keeps_native_exports_history_and_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    old_producer: Any,
    kind: str,
) -> None:
    policy = schema("https://synthetic.example.test/api-composition")
    assert structured_policy_ids(policy)
    rows = (
        *current_pattern(policy, "computer"),
        historical_row(old_producer, policy, "computer", kind),
    )
    store = WorkspaceStore(tmp_path / "composition.db")
    monkeypatch.setattr(app.state, "store", store, raising=False)
    monkeypatch.setattr(app.state, "owns_store", False, raising=False)
    monkeypatch.setattr(app.state, "template_sources", [], raising=False)
    try:
        with TestClient(app, base_url="http://127.0.0.1:4493") as client:
            monkeypatch.setattr(
                app.state, "admx_catalogue", AdmxCatalogue(policies=(policy,)), raising=False
            )
            gpo = store.create_gpo(
                "Synthetic composed state",
                settings=rows,
                identity="test",
                reason="Actual old producer",
            )
            gpo = store.update_metadata(
                gpo.guid,
                1,
                {"description": "Synthetic retained history"},
                identity="test",
                reason="History control",
            )
            before = tuple(store._connection.iterdump())
            for query, count in (
                ({}, 1),
                ({"state": "indeterminate"}, 1),
                ({"state": "enabled"}, 0),
                ({"state": "disabled"}, 0),
            ):
                response = client.get(f"/api/gpos/{gpo.guid}/configured-settings", params=query)
                assert response.status_code == 200 and response.json()["resolved_count"] == count
                if count:
                    result = response.json()["resolved"][0]
                    assert (
                        result["state"] == "indeterminate"
                        and result["state_reason"] == "no_complete_pattern"
                    )
                assert response.json()["gpo_revision"] == gpo.revision
                assert tuple(store._connection.iterdump()) == before
            detail = client.get(f"/api/gpos/{gpo.guid}").json()
            assert detail["validation"] == []
            assert detail["artifact_capabilities"]["gpmc_export"]["enabled"] is True
            assert detail["artifact_capabilities"]["studio_export"]["enabled"] is True
            assert len(store.revisions(gpo.guid)) == 2
            assert tuple(store._connection.iterdump()) == before
    finally:
        store.close()
