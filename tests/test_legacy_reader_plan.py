"""Actual historical producers and accepted schemas must share one coherent reader plan."""

from __future__ import annotations

import subprocess
import sys
import types
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

import gpo_studio.settings_browser as reader
from gpo_studio.admx import AdmxCatalogue, PolicyDefinition, PolicyElement, parse_admx
from gpo_studio.api import app
from gpo_studio.model import RegistrySetting, ValidationError
from gpo_studio.policy_config import (
    PolicyConfiguration,
    legacy_setting_parts,
    parse_structured_setting_id,
    policy_setting_positions,
    resolve_policy,
    setting_policy_owners,
)
from gpo_studio.settings_browser import build_settings_browser
from gpo_studio.store import WorkspaceStore

OLD = "eec9cc0ba8fbf72ae74f43e8f1ca44a61f2ee3e6"
KEY = r"Software\Policies\Synthetic\LegacyReader"


@pytest.fixture(scope="module")
def old_producer() -> Iterator[Any]:
    source = subprocess.check_output(
        ["git", "show", f"{OLD}:src/gpo_studio/policy_config.py"],
        cwd=Path(__file__).resolve().parents[1],
    )
    module = types.ModuleType("gpo_studio._legacy_reader_old_producer")
    module.__package__ = "gpo_studio"
    sys.modules[module.__name__] = module
    try:
        exec(compile(source, f"{OLD}:policy_config.py", "exec"), module.__dict__)
        yield module
    finally:
        del sys.modules[module.__name__]


def schema(*, empty_owner: bool = False, fixed: bool = False) -> PolicyDefinition:
    namespace = (
        ""
        if empty_owner
        else (
            '<policyNamespaces><target prefix="synthetic" '
            'namespace="https://synthetic.example.test/legacy" /></policyNamespaces>'
        )
    )
    name = "" if empty_owner else "Probe"
    element = '<text id="state" valueName="Option"/>' if empty_owner else ""
    fixed_lists = (
        """<enabledList><item valueName="Fixed"><value><string>fixed</string></value></item>
      </enabledList>
      <disabledList><item valueName="Disabled"><value><delete/></value></item></disabledList>"""
        if fixed
        else ""
    )
    xml = f'''<policyDefinitions>{namespace}<policies>
      <policy name="{name}" class="Both" key="{KEY}" valueName="State">{fixed_lists}
        <elements>{element}<list id="Entries"/></elements>
      </policy></policies></policyDefinitions>'''.encode()
    (policy,), _, _ = parse_admx(xml)
    return policy


@pytest.fixture
def api_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, WorkspaceStore]]:
    store = WorkspaceStore(tmp_path / "legacy-reader.db")
    monkeypatch.setattr(app.state, "store", store, raising=False)
    monkeypatch.setattr(app.state, "owns_store", False, raising=False)
    monkeypatch.setattr(app.state, "template_sources", [], raising=False)
    with TestClient(app, base_url="http://127.0.0.1:4467") as client:
        yield client, store
    store.close()


def storage(store: WorkspaceStore) -> tuple[str, ...]:
    return tuple(store._connection.iterdump())


@pytest.mark.parametrize("side", ["computer", "user"])
@pytest.mark.parametrize("fixed", [False, True])
def test_real_legacy_producer_empty_list_never_promotes_state_or_fixed_writes(
    old_producer: Any,
    side: str,
    fixed: bool,
    api_store: tuple[TestClient, WorkspaceStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, store = api_store
    policy = schema(fixed=fixed)
    catalogue = AdmxCatalogue(policies=(policy,))
    monkeypatch.setattr(app.state, "admx_catalogue", catalogue, raising=False)
    rows = tuple(
        old_producer.resolve_policy(policy, old_producer.PolicyConfiguration(side, {"Entries": []}))
    )
    assert rows and all(row.id.startswith("admx-") for row in rows)
    gpo = store.create_gpo(
        "Synthetic legacy no-prefix list",
        settings=rows,
        identity="test",
        reason="Actual old producer",
    )
    before = storage(store)
    historical_before = store.get_revision(gpo.guid, 1).snapshot
    response = client.get(f"/api/gpos/{gpo.guid}/configured-settings")
    assert response.status_code == 200, response.text
    values = response.json()["resolved"][0]["element_values"]
    assert values.get("Entries", []) == []
    assert response.json()["unresolved"] == []
    assert storage(store) == before
    reapply = client.post(
        f"/api/admx/policies/{quote(policy.qualified_id, safe='')}/configure",
        json={
            "gpo_guid": gpo.guid,
            "expected_revision": 1,
            "side": side,
            "values": {"Entries": values.get("Entries", [])},
        },
    )
    assert reapply.status_code == 200, reapply.text
    updated = store.get_gpo(gpo.guid)
    assert updated is not None
    assert {
        (row.hive, row.key, row.value_name, row.registry_type, row.value, row.action)
        for row in updated.settings
    } == {
        (row.hive, row.key, row.value_name, row.registry_type, row.value, row.action)
        for row in rows
    }
    # Reconfiguration keeps immutable old row IDs/order in the historical revision.
    assert store.get_revision(gpo.guid, 1).snapshot == historical_before
    removed = client.post(
        f"/api/admx/policies/{quote(policy.qualified_id, safe='')}/configure",
        json={
            "gpo_guid": gpo.guid,
            "expected_revision": 2,
            "side": side,
            "state": "not_configured",
            "values": {},
        },
    )
    assert removed.status_code == 200 and removed.json()["gpo"]["settings"] == []


class CountedElements(tuple):
    visits = 0

    def __iter__(self):
        for item in super().__iter__():
            type(self).visits += 1
            yield item


@pytest.mark.parametrize("size", [20, 80, 160])
@pytest.mark.parametrize("producer", ["legacy", "new"])
@pytest.mark.parametrize("structured", [False, True], ids=["ordinary", "structured"])
def test_list_membership_does_not_repeat_schema_traversal(
    old_producer: Any,
    size: int,
    producer: str,
    structured: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scalar_id = "state" if structured else "Plain"
    policy = replace(
        schema(),
        namespace="Synthetic.ReaderCost",
        elements=CountedElements(
            tuple(
                PolicyElement(
                    kind="list", id=f"Entries{i}", attributes=(("valuePrefix", f"P{i}_"),)
                )
                for i in range(size)
            )
            + (PolicyElement(kind="text", id=scalar_id, registry_value_name="Option"),)
        ),
    )
    values = {f"Entries{i}": [f"value{i}"] for i in range(size)} | {scalar_id: "scalar"}
    if producer == "legacy":
        rows = old_producer.resolve_policy(
            policy, old_producer.PolicyConfiguration("computer", values)
        )
    else:
        rows = resolve_policy(policy, PolicyConfiguration("computer", values))
    parse_calls = 0
    parse = reader.parse_structured_setting_id

    def counted_parse(setting_id: str):
        nonlocal parse_calls
        parse_calls += 1
        return parse(setting_id)

    monkeypatch.setattr(reader, "parse_structured_setting_id", counted_parse)
    CountedElements.visits = 0
    result = build_settings_browser(AdmxCatalogue(policies=(policy,)), rows)
    assert result.resolved[0].element_values == values
    assert CountedElements.visits <= 6 * (size + 1)
    assert parse_calls <= 8 * (len(rows) + 1)


@pytest.mark.parametrize("side", ["computer", "user"])
def test_parser_accepted_empty_owner_all_writer_roles_repeat_and_remove(
    side: str,
    api_store: tuple[TestClient, WorkspaceStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, store = api_store
    policy = schema(empty_owner=True, fixed=True)
    assert policy.qualified_id == ""
    catalogue = AdmxCatalogue(policies=(policy,))
    monkeypatch.setattr(app.state, "admx_catalogue", catalogue, raising=False)
    values = {"state": "scalar", "Entries": ["one", "two"]}
    enabled = resolve_policy(policy, PolicyConfiguration(side, values))
    disabled = resolve_policy(policy, PolicyConfiguration(side, {}, "disabled"))
    rows = enabled + disabled
    identities = [parse_structured_setting_id(row.id) for row in rows]
    assert all(identity is not None and identity.policy_id == "" for identity in identities)
    assert {identity.kind for identity in identities if identity is not None} == {
        "state",
        "state-list",
        "element",
        "element-list",
        "disabled-element",
    }
    assert all(setting_policy_owners(row, catalogue) == (("", side),) for row in rows)
    assert build_settings_browser(catalogue, enabled).resolved[0].element_values == values
    gpo = store.create_gpo(
        "Synthetic empty-owner policy", identity="test", reason="Accepted parser input"
    )
    path = "/api/admx/policies//configure"
    first_ids = None
    for revision in (1, 2):
        response = client.post(
            path,
            json={
                "gpo_guid": gpo.guid,
                "expected_revision": revision,
                "side": side,
                "values": values,
            },
        )
        assert response.status_code == 200, response.text
        actual_ids = [row["id"] for row in response.json()["gpo"]["settings"]]
        assert set(actual_ids) == {row.id for row in enabled}
        if first_ids is None:
            first_ids = actual_ids
        else:
            assert actual_ids == first_ids
    before = storage(store)
    stale = client.post(
        path, json={"gpo_guid": gpo.guid, "expected_revision": 1, "side": side, "values": values}
    )
    assert stale.status_code == 409 and storage(store) == before
    removed = client.post(
        path,
        json={
            "gpo_guid": gpo.guid,
            "expected_revision": 3,
            "side": side,
            "state": "not_configured",
            "values": {},
        },
    )
    assert removed.status_code == 200 and removed.json()["gpo"]["settings"] == []


@pytest.mark.parametrize("edited_side", [False, True])
def test_historical_empty_owner_remains_a_possible_producer_before_side_filtering(
    old_producer: Any,
    edited_side: bool,
    api_store: tuple[TestClient, WorkspaceStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, store = api_store
    empty = PolicyDefinition(
        "",
        "User",
        KEY,
        "Synthetic empty",
        "",
        "",
        elements=(
            PolicyElement(kind="text", id="Tail-computer-Value", registry_value_name="Data"),
        ),
    )
    other = PolicyDefinition(
        "-user-Tail",
        "Machine",
        KEY + r"\Other",
        "Synthetic other",
        "",
        "",
        elements=(PolicyElement(kind="text", id="Value", registry_value_name="Other"),),
    )
    (original,) = old_producer.resolve_policy(
        empty, old_producer.PolicyConfiguration("user", {"Tail-computer-Value": "data"})
    )
    assert original.id == "admx--user-Tail-computer-Value"
    assert legacy_setting_parts(original.id) == (
        ("", "user", "Tail-computer-Value"),
        ("-user-Tail", "computer", "Value"),
    )
    row = replace(original, side="computer", hive="HKLM") if edited_side else original
    catalogue = AdmxCatalogue(policies=(empty, other))
    monkeypatch.setattr(app.state, "admx_catalogue", catalogue, raising=False)
    assert set(setting_policy_owners(row, catalogue)) == {("", "user"), ("-user-Tail", "computer")}
    for owner in ("", "-user-Tail"):
        with pytest.raises(ValidationError):
            policy_setting_positions(
                (row,), catalogue, {(owner, "user" if owner == "" else "computer")}
            )
    gpo = store.create_gpo(
        "Synthetic ambiguous empty owner",
        settings=(row,),
        identity="test",
        reason="Historical empty owner",
    )
    before = storage(store)
    browser = client.get(f"/api/gpos/{gpo.guid}/configured-settings")
    assert browser.status_code == 200 and browser.json()["resolved"] == []
    assert browser.json()["unresolved"][0]["reason"] == "ambiguous legacy policy ownership"
    for policy, side in ((empty, "user"), (other, "computer")):
        response = client.post(
            f"/api/admx/policies/{quote(policy.qualified_id, safe='')}/configure",
            json={
                "gpo_guid": gpo.guid,
                "expected_revision": 1,
                "side": side,
                "state": "not_configured",
                "values": {},
            },
        )
        assert (
            response.status_code == 422
            and response.json()["error"]["issues"][0]["code"] == "ambiguous_setting_owner"
        )
        assert storage(store) == before
        bulk = client.post(
            f"/api/gpos/{gpo.guid}/bulk-policy-state",
            json={
                "expected_revision": 1,
                "side": side,
                "policy_ids": [policy.qualified_id],
                "target_state": "not_configured",
            },
        )
        assert bulk.status_code == 422
        assert bulk.json()["error"]["issues"][0]["code"] == "ambiguous_setting_owner"
        assert storage(store) == before
    assert store.get_gpo(gpo.guid).settings == (row,)


@pytest.mark.parametrize("side", ["computer", "user"])
def test_raw_address_memberships_exclude_policy_and_scalar_records_from_list_fallback(
    side: str,
) -> None:
    policy = schema(empty_owner=True, fixed=True)
    catalogue = AdmxCatalogue(policies=(policy,))
    generated = resolve_policy(
        policy, PolicyConfiguration(side, {"state": "scalar", "Entries": []})
    )
    raw = tuple(replace(row, id=f"raw-{index}") for index, row in enumerate(generated))
    result = build_settings_browser(catalogue, raw)
    assert not result.unresolved
    assert result.resolved[0].raw_settings == raw
    assert result.resolved[0].element_values == {"state": "scalar"}


@pytest.mark.parametrize("side", ["computer", "user"])
def test_raw_list_address_fallback_and_record_order_are_preserved(side: str) -> None:
    policy = replace(
        schema(),
        value_name="",
        elements=(
            PolicyElement(
                kind="list",
                id="Entries",
                registry_value_name="Item1",
                attributes=(("valuePrefix", "Item"),),
            ),
        ),
    )
    row = RegistrySetting(
        "raw-import-list",
        side,
        "HKLM" if side == "computer" else "HKCU",
        KEY,
        "Item1",
        "REG_SZ",
        "raw item",
    )
    result = build_settings_browser(AdmxCatalogue(policies=(policy,)), (row,))
    assert not result.unresolved
    assert result.resolved[0].raw_settings == (row,)
    assert result.resolved[0].element_values == {"Entries": ["raw item"]}


def test_uncertain_legacy_list_and_fixed_list_suffix_is_retained_as_unresolved(
    old_producer: Any,
) -> None:
    from gpo_studio.admx import PolicyListItem, PolicyValue, PolicyValueList

    policy = replace(
        schema(),
        value_name="",
        enabled_list=PolicyValueList(
            (PolicyListItem("Fixed", PolicyValue("string", "fixed", "REG_SZ")),)
        ),
        elements=(PolicyElement(kind="list", id="listitem", registry_key=KEY + r"\Entries"),),
    )
    rows = tuple(
        old_producer.resolve_policy(
            policy, old_producer.PolicyConfiguration("computer", {"listitem": ["dynamic"]})
        )
    )
    assert rows[0].id == rows[1].id
    result = build_settings_browser(AdmxCatalogue(policies=(policy,)), rows)
    assert result.resolved[0].raw_settings == rows
    assert result.resolved[0].element_values.get("listitem", []) == []
    assert [(item.setting, item.reason) for item in result.unresolved] == [
        (rows[1], "ambiguous legacy policy component"),
    ]


@pytest.mark.parametrize("side", ["computer", "user"])
def test_empty_owner_ordinary_legacy_writer_family_first_repeat_and_remove(
    side: str,
    api_store: tuple[TestClient, WorkspaceStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, store = api_store
    policy = replace(
        schema(empty_owner=True),
        elements=(PolicyElement(kind="text", id="Plain", registry_value_name="Option"),),
    )
    catalogue = AdmxCatalogue(policies=(policy,))
    monkeypatch.setattr(app.state, "admx_catalogue", catalogue, raising=False)
    rows = resolve_policy(policy, PolicyConfiguration(side, {"Plain": "text"}))
    assert all(row.id.startswith(f"admx--{side}-") for row in rows)
    assert all(setting_policy_owners(row, catalogue) == (("", side),) for row in rows)
    assert build_settings_browser(catalogue, rows).resolved[0].element_values == {"Plain": "text"}
    gpo = store.create_gpo(
        "Synthetic ordinary empty owner", identity="test", reason="Accepted legacy family"
    )
    first = None
    for revision in (1, 2):
        response = client.post(
            "/api/admx/policies//configure",
            json={
                "gpo_guid": gpo.guid,
                "side": side,
                "expected_revision": revision,
                "values": {"Plain": "text"},
            },
        )
        assert response.status_code == 200, response.text
        current = response.json()["gpo"]["settings"]
        if first is None:
            first = current
        else:
            assert current == first
    removed = client.post(
        "/api/admx/policies//configure",
        json={
            "gpo_guid": gpo.guid,
            "side": side,
            "expected_revision": 3,
            "state": "not_configured",
            "values": {},
        },
    )
    assert removed.status_code == 200 and removed.json()["gpo"]["settings"] == []


def boundary_schema(
    name: str, *, scalar: str | None = None, list_id: str | None = None
) -> PolicyDefinition:
    elements = []
    if list_id is not None:
        elements.append(f'<list id="{list_id}" key="{KEY}\\AList" valuePrefix="Item"/>')
    if scalar is not None:
        elements.append(f'<text id="{scalar}" key="{KEY}\\ZScalar" valueName="Option"/>')
    xml = f'''<policyDefinitions><policyNamespaces>
      <target prefix="synthetic" namespace="Synthetic.ReaderClosure"/></policyNamespaces>
      <policies><policy name="{name}" class="Both" key="{KEY}" valueName="State">
        <elements>{"".join(elements)}</elements></policy></policies></policyDefinitions>'''.encode()
    return parse_admx(xml)[0][0]


@pytest.mark.parametrize(
    "name,scalar,list_id",
    [
        ("SideElem", "computer-Option", None),
        ("SideUser", "user-Option", None),
        ("EndComputer", None, "computer"),
        ("EndUser", None, "user"),
        ("EndTail", None, "Tail-computer"),
        ("EndTailUser", None, "Tail-user"),
        ("Owner-computer", None, None),
        ("Owner-user", None, None),
        ("Disabled1", "1", "disabled-element"),
        ("Disabled12", "12", "disabled-element"),
        ("DisabledLarge", "12345678901234567890", "disabled-element"),
    ],
)
@pytest.mark.parametrize("side", ["computer", "user"])
def test_assembled_legacy_boundaries_use_one_structured_family_and_repeat_remove(
    name: str,
    scalar: str | None,
    list_id: str | None,
    side: str,
    api_store: tuple[TestClient, WorkspaceStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gpo_studio.policy_config import structured_policy_ids

    client, store = api_store
    policy = boundary_schema(name, scalar=scalar, list_id=list_id)
    assert structured_policy_ids(policy)
    catalogue = AdmxCatalogue(policies=(policy,))
    monkeypatch.setattr(app.state, "admx_catalogue", catalogue, raising=False)
    values = {}
    if scalar is not None:
        values[scalar] = "scalar"
    if list_id is not None:
        values[list_id] = ["one", "two"]
    rows = resolve_policy(policy, PolicyConfiguration(side, values))
    assert all(parse_structured_setting_id(row.id) is not None for row in rows)
    assert build_settings_browser(catalogue, rows).resolved[0].element_values == values
    gpo = store.create_gpo(
        "Synthetic assembled identity boundary", identity="test", reason="Full writer frames"
    )
    path = f"/api/admx/policies/{quote(policy.qualified_id, safe='')}/configure"
    first = None
    for revision in (1, 2):
        saved = client.post(
            path,
            json={
                "gpo_guid": gpo.guid,
                "expected_revision": revision,
                "side": side,
                "values": values,
            },
        )
        assert saved.status_code == 200, saved.text
        actual = saved.json()["gpo"]["settings"]
        if first is None:
            first = actual
        else:
            assert actual == first
        before = storage(store)
        read = client.get(f"/api/gpos/{gpo.guid}/configured-settings")
        assert read.status_code == 200 and read.json()["unresolved"] == []
        assert read.json()["resolved"][0]["element_values"] == values
        assert storage(store) == before
    removed = client.post(
        path,
        json={
            "gpo_guid": gpo.guid,
            "expected_revision": 3,
            "side": side,
            "state": "not_configured",
            "values": {},
        },
    )
    assert removed.status_code == 200 and removed.json()["gpo"]["settings"] == []


@pytest.mark.parametrize("scalar", ["1", "12"])
@pytest.mark.parametrize("removed_list", [False, True])
def test_old_producer_disabled_namespace_does_not_invent_scalar_content(
    old_producer: Any,
    scalar: str,
    removed_list: bool,
    api_store: tuple[TestClient, WorkspaceStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, store = api_store
    policy = boundary_schema("LegacyDisabledNamespace", scalar=scalar, list_id="disabled-element")
    values = {scalar: "scalar", "disabled-element": [f"item{i}" for i in range(1, int(scalar) + 1)]}
    original = tuple(
        old_producer.resolve_policy(policy, old_producer.PolicyConfiguration("computer", values))
    )
    if removed_list:
        policy = replace(
            policy, elements=tuple(element for element in policy.elements if element.id == scalar)
        )
        rows = tuple(row for row in original if row.value_name == f"Item{scalar}")
        expected = {}
    else:
        rows = original
        expected = values
    catalogue = AdmxCatalogue(policies=(policy,))
    monkeypatch.setattr(app.state, "admx_catalogue", catalogue, raising=False)
    gpo = store.create_gpo(
        "Synthetic historical disabled namespace",
        settings=rows,
        identity="test",
        reason="Actual old producer",
    )
    before = storage(store)
    response = client.get(f"/api/gpos/{gpo.guid}/configured-settings")
    assert response.status_code == 200
    assert response.json()["resolved"][0]["element_values"] == expected
    if removed_list:
        assert [item["id"] for item in response.json()["unresolved"]] == [rows[0].id]
        assert response.json()["unresolved"][0]["reason"] == "ambiguous legacy policy component"
    else:
        assert response.json()["unresolved"] == []
    assert storage(store) == before


@pytest.mark.parametrize("matching_address", [False, True])
def test_legacy_reserved_scalar_spelling_never_bypasses_its_address(
    old_producer: Any,
    matching_address: bool,
) -> None:
    policy = boundary_schema("ReservedScalar", scalar="disabled-element-12")
    if matching_address:
        rows = tuple(
            old_producer.resolve_policy(
                policy,
                old_producer.PolicyConfiguration("computer", {"disabled-element-12": "scalar"}),
            )
        )
        expected = {"disabled-element-12": "scalar"}
    else:
        historical = boundary_schema("ReservedScalar", scalar="12", list_id="disabled-element")
        generated = old_producer.resolve_policy(
            historical,
            old_producer.PolicyConfiguration(
                "computer", {"12": "scalar", "disabled-element": [f"item{i}" for i in range(1, 13)]}
            ),
        )
        rows = tuple(row for row in generated if row.value_name == "Item12")
        expected = {}
    result = build_settings_browser(AdmxCatalogue(policies=(policy,)), rows)
    assert result.resolved[0].raw_settings == rows
    assert result.resolved[0].element_values == expected
    assert bool(result.unresolved) is not matching_address
