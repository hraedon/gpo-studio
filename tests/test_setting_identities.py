"""Authoring identities must preserve every reviewed registry record and owner."""

from __future__ import annotations

import json
import subprocess
import sys
import types
from collections.abc import Iterator
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from gpo_studio.admx import (
    AdmxCatalogue,
    PolicyDefinition,
    PolicyElement,
    PolicyListItem,
    PolicyValue,
    PolicyValueList,
)
from gpo_studio.api import app
from gpo_studio.canonical import policy_semantic_dict
from gpo_studio.export import _gpmc_preg_bytes
from gpo_studio.model import GPO, RegistrySetting, ValidationError
from gpo_studio.policy_config import (
    PolicyConfiguration,
    legacy_setting_parts,
    parse_structured_setting_id,
    policy_setting_positions,
    resolve_policy,
    setting_policy_owners,
    structured_policy_ids,
)
from gpo_studio.settings_browser import build_settings_browser
from gpo_studio.store import WorkspaceStore

KEY = r"Software\Policies\Synthetic\Identity"


def policy(name: str = "Simple", **fields: Any) -> PolicyDefinition:
    return PolicyDefinition(
        id=name,
        class_="Machine",
        key=KEY + "\\" + name,
        namespace="Synthetic.Identities",
        display_name="Synthetic " + name,
        explain_text="Synthetic identity test",
        supported_on="",
        **fields,
    )


def text(name: str, value_name: str = "Option") -> PolicyElement:
    return PolicyElement(kind="text", id=name, registry_value_name=value_name)


def schemas() -> tuple[PolicyDefinition, ...]:
    state = PolicyValue("decimal", "1", "REG_DWORD")
    return (
        policy(value_name="State", enabled_value=state),
        policy("Other", value_name="OtherState", enabled_value=state),
        policy(
            "StateElement",
            value_name="State",
            enabled_value=state,
            elements=(text("state", "ZZZOption"),),
        ),
        policy(
            "ListElement",
            elements=(
                PolicyElement(
                    kind="list",
                    id="Items",
                    registry_key=KEY + "\\Items",
                    attributes=(("valuePrefix", "Item"),),
                ),
                text("Items-1"),
            ),
        ),
        policy(
            "StateListElement",
            enabled_list=PolicyValueList(
                (PolicyListItem("ListValue", PolicyValue("string", "fixed", "REG_SZ")),)
            ),
            elements=(text("listitem-1"),),
        ),
        policy(
            "StateListListElement",
            enabled_list=PolicyValueList(
                (PolicyListItem("FixedValue", PolicyValue("string", "fixed", "REG_SZ")),)
            ),
            elements=(
                PolicyElement(
                    kind="list",
                    id="listitem",
                    registry_key=KEY + "\\DynamicItems",
                    attributes=(("valuePrefix", "Item"),),
                ),
            ),
        ),
        policy(
            "DisabledElement",
            value_name="State",
            enabled_value=state,
            elements=(text("Option", "Option"), text("disabled-element-Option", "Second")),
        ),
    )


async def single_decode_test_app(scope: Any, receive: Any, send: Any) -> None:
    # The pinned TestClient unquotes httpx2.URL.path, which is already decoded.
    # Preserve the real ASGI single-decode contract for literal percent escapes.
    if scope["type"] == "http":
        scope = {**scope, "path": unquote(scope["raw_path"].decode("ascii"))}
    await app(scope, receive, send)


@pytest.fixture
def harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, WorkspaceStore]]:
    store = WorkspaceStore(tmp_path / "workspace.db")
    monkeypatch.setattr(app.state, "store", store, raising=False)
    monkeypatch.setattr(app.state, "owns_store", False, raising=False)
    monkeypatch.setattr(
        app.state, "admx_catalogue", AdmxCatalogue(policies=schemas()), raising=False
    )
    monkeypatch.setattr(app.state, "template_sources", [], raising=False)
    monkeypatch.delattr(app.state, "catalogue_snapshot", raising=False)
    try:
        with TestClient(single_decode_test_app) as client:
            yield client, store
    finally:
        store.close()
        if hasattr(app.state, "catalogue_snapshot"):
            del app.state.catalogue_snapshot


def draft(client: TestClient, name: str = "Synthetic target") -> dict[str, Any]:
    response = client.post("/api/gpos", json={"name": name})
    assert response.status_code == 201
    return dict(response.json()["gpo"])


def configured(client: TestClient, gpo: dict[str, Any], name: str, **fields: Any) -> Any:
    policy_id = name if ":" in name else "Synthetic.Identities:" + name
    return client.post(
        f"/api/admx/policies/{quote(policy_id, safe='')}/configure",
        json={
            "gpo_guid": gpo["guid"],
            "side": "computer",
            "expected_revision": gpo["revision"],
            "reason": "Apply reviewed synthetic settings",
            **fields,
        },
    )


def storage(store: WorkspaceStore) -> tuple[str, ...]:
    """Logical head/history/document equality, including failed transaction effects."""
    return tuple(store._connection.iterdump())


def inject_legacy(store: WorkspaceStore, settings: tuple[RegistrySetting, ...]) -> GPO:
    # Seed with unique identities, then simulate bytes written by an older build.
    initial = tuple(replace(item, id=f"seed-{index}") for index, item in enumerate(settings))
    gpo = store.create_gpo(
        "Synthetic legacy head",
        settings=initial,
        identity="test",
        reason="Seed clean recovery revision",
    )
    current = store.update_metadata(
        gpo.guid,
        1,
        {"description": "Legacy head"},
        identity="test",
        reason="Seed head for legacy injection",
    )
    row = store._connection.execute(
        "SELECT snapshot_json FROM gpos WHERE guid=?", (gpo.guid,)
    ).fetchone()
    data = json.loads(row[0])
    data["settings"] = [asdict(item) for item in settings]
    payload = json.dumps(data)
    store._connection.execute("UPDATE gpos SET snapshot_json=? WHERE guid=?", (payload, gpo.guid))
    store._connection.execute(
        "UPDATE revisions SET snapshot_json=? WHERE gpo_guid=? AND revision=2", (payload, gpo.guid)
    )
    store._connection.commit()
    return store.get_gpo(current.guid)


def record(identifier: str = "shared", name: str = "One") -> RegistrySetting:
    return RegistrySetting(identifier, "computer", "HKLM", KEY, name, "REG_SZ", name)


@pytest.mark.parametrize("state", ["enabled", "disabled", "not_configured"])
@pytest.mark.parametrize(
    "ids",
    [
        ["Simple", "Simple"],
        ["Simple", "Synthetic.Identities:Simple"],
    ],
)
def test_duplicate_resolved_bulk_owners_leave_every_table_unchanged(
    harness: Any, state: str, ids: list[str]
) -> None:
    client, store = harness
    gpo = draft(client)
    before = storage(store)
    response = client.post(
        f"/api/gpos/{gpo['guid']}/bulk-policy-state",
        json={
            "policy_ids": ids,
            "side": "computer",
            "target_state": state,
            "expected_revision": 1,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["issues"][0]["code"] == "duplicate_policy_id"
    assert storage(store) == before


def test_distinct_bulk_owners_and_distinct_sides_remain_valid(
    harness: Any, monkeypatch: Any
) -> None:
    client, _store = harness
    both = replace(schemas()[0], class_="Both")
    monkeypatch.setattr(app.state, "admx_catalogue", AdmxCatalogue(policies=(both, schemas()[1])))
    gpo = draft(client)
    response = client.post(
        f"/api/gpos/{gpo['guid']}/bulk-policy-state",
        json={
            "policy_ids": ["Simple", "Synthetic.Identities:Simple", "Other"],
            "policy_sides": {
                "Simple": "computer",
                "Synthetic.Identities:Simple": "user",
                "Other": "computer",
            },
            "target_state": "enabled",
            "expected_revision": 1,
        },
    )
    assert response.status_code == 200, response.text
    assert len({item["id"] for item in response.json()["gpo"]["settings"]}) == 3


@pytest.mark.parametrize(
    ("name", "values", "expected"),
    [
        ("StateElement", {"state": "text-data"}, {"state": "text-data"}),
        (
            "ListElement",
            {"Items": ["one", "two"], "Items-1": "text-data"},
            {"Items": ["one", "two"], "Items-1": "text-data"},
        ),
        ("StateListElement", {"listitem-1": "text-data"}, {"listitem-1": "text-data"}),
        ("StateListListElement", {"listitem": ["one", "two"]}, {"listitem": ["one", "two"]}),
        (
            "DisabledElement",
            {"Option": "one", "disabled-element-Option": "two"},
            {"Option": "one", "disabled-element-Option": "two"},
        ),
    ],
)
def test_reserved_component_names_preserve_writes_and_browser_values(
    harness: Any, name: str, values: Any, expected: Any
) -> None:
    client, _store = harness
    gpo = draft(client)
    response = configured(client, gpo, name, values=values)
    assert response.status_code == 200, response.text
    saved = response.json()["gpo"]
    ids = [item["id"] for item in saved["settings"]]
    assert len(ids) == len(set(ids)) and all(item.startswith("admx2:") for item in ids)
    decoded = client.get(f"/api/gpos/{gpo['guid']}/configured-settings").json()
    assert decoded["resolved"][0]["element_values"] == expected
    repeated = configured(client, saved, name, values=values)
    assert repeated.status_code == 200
    assert repeated.json()["gpo"]["settings"] == saved["settings"]
    disabled = configured(client, repeated.json()["gpo"], name, state="disabled")
    assert disabled.status_code == 200, disabled.text
    assert all(item["id"].startswith("admx2:") for item in disabled.json()["gpo"]["settings"])
    removed = configured(client, disabled.json()["gpo"], name, state="not_configured")
    assert removed.status_code == 200 and removed.json()["gpo"]["settings"] == []


def overlapping_schemas() -> tuple[PolicyDefinition, PolicyDefinition]:
    parent = replace(
        policy("SafeParent", elements=(text("Child:SafeChild-computer-state", "Parent"),)),
        namespace="urn:synthetic:Parent",
    )
    child = replace(
        policy(
            "SafeChild", value_name="Child", enabled_value=PolicyValue("decimal", "1", "REG_DWORD")
        ),
        namespace="urn:synthetic:Parent:SafeParent-computer-Child",
    )
    return parent, child


@pytest.fixture(scope="module")
def legacy_producer() -> Iterator[Any]:
    """Run the real immutable old resolver, rather than inventing a legacy row ID."""
    commit = "0d83fb49781dfcc8b2c54b40480ef58540dc12c3"
    source = subprocess.check_output(
        ["git", "show", f"{commit}:src/gpo_studio/policy_config.py"],
        cwd=Path(__file__).resolve().parents[1],
    )
    module = types.ModuleType("gpo_studio._legacy_producer_0d83")
    module.__package__ = "gpo_studio"
    sys.modules[module.__name__] = module
    try:
        exec(compile(source, f"{commit}:policy_config.py", "exec"), module.__dict__)
        yield module
    finally:
        del sys.modules[module.__name__]


def legacy_child_row(legacy_producer: Any, *, mixed_sides: bool = False) -> RegistrySetting:
    _parent, child = overlapping_schemas()
    if mixed_sides:
        child = replace(child, class_="User")
    side = "user" if mixed_sides else "computer"
    rows = legacy_producer.resolve_policy(child, legacy_producer.PolicyConfiguration(side, {}))
    assert len(rows) == 1
    return rows[0]


def remapped_schemas() -> tuple[PolicyDefinition, PolicyDefinition]:
    parent, child = overlapping_schemas()
    return (
        replace(
            parent,
            key=child.key,
            elements=(replace(parent.elements[0], registry_value_name="Child"),),
        ),
        replace(child, key=child.key + r"\UpdatedMapping"),
    )


def test_current_catalogue_addresses_cannot_prove_a_legacy_rows_producer(
    legacy_producer: Any,
) -> None:
    parent, child = overlapping_schemas()
    row = legacy_child_row(legacy_producer)
    original = asdict(row)
    assert len(legacy_setting_parts(row.id)) == 2
    expected = {(parent.qualified_id, "computer"), (child.qualified_id, "computer")}
    catalogues = {"original": overlapping_schemas(), "remapped": remapped_schemas()}
    observed = {
        name: set(setting_policy_owners(row, AdmxCatalogue(policies=policies)))
        for name, policies in catalogues.items()
    }
    assert observed == {"original": expected, "remapped": expected}
    for policies in catalogues.values():
        catalogue = AdmxCatalogue(policies=policies)
        result = build_settings_browser(catalogue, (row,))
        assert not result.resolved
        assert result.unresolved[0].setting == row
        assert result.unresolved[0].reason == "ambiguous legacy policy ownership"
        for owner in expected:
            with pytest.raises(ValidationError) as failure:
                policy_setting_positions((row,), catalogue, {owner})
            assert failure.value.issues[0].code == "ambiguous_setting_owner"
        # Without the ownership refusal, this replacement could erase the row.
        assert (
            resolve_policy(policies[0], PolicyConfiguration("computer", {}, "not_configured")) == []
        )
    assert asdict(row) == original


def test_a_side_edit_cannot_disambiguate_a_multiply_parseable_legacy_frame(
    legacy_producer: Any,
) -> None:
    parent, child = overlapping_schemas()
    child = replace(child, class_="User")
    row = legacy_child_row(legacy_producer, mixed_sides=True)
    catalogue = AdmxCatalogue(policies=(parent, child))
    expected = {(parent.qualified_id, "computer"), (child.qualified_id, "user")}
    rows = {"original": row, "side_edited": replace(row, side="computer", hive="HKLM")}
    observed = {
        name: set(setting_policy_owners(current, catalogue)) for name, current in rows.items()
    }
    assert observed == {"original": expected, "side_edited": expected}
    for current in rows.values():
        result = build_settings_browser(catalogue, (current,))
        assert not result.resolved and len(result.unresolved) == 1
        for owner in expected:
            with pytest.raises(ValidationError) as failure:
                policy_setting_positions((current,), catalogue, {owner})
            assert failure.value.issues[0].code == "ambiguous_setting_owner"


def test_overlapping_legacy_owners_configure_and_remove_independently(
    harness: Any, monkeypatch: Any
) -> None:
    client, _store = harness
    parent, child = overlapping_schemas()
    monkeypatch.setattr(app.state, "admx_catalogue", AdmxCatalogue(policies=(parent, child)))
    gpo = draft(client)
    first = configured(
        client, gpo, parent.qualified_id, values={parent.elements[0].id: "parent-data"}
    ).json()["gpo"]
    response = configured(client, first, child.qualified_id)
    assert response.status_code == 200, response.text
    second = response.json()["gpo"]
    assert {item["value_name"] for item in second["settings"]} == {"Parent", "Child"}
    response = configured(
        client, second, parent.qualified_id, values={parent.elements[0].id: "changed"}
    )
    assert response.status_code == 200
    third = response.json()["gpo"]
    assert next(item["value"] for item in third["settings"] if item["value_name"] == "Child") == "1"
    for owner, retained in [(parent, "Child"), (child, None)]:
        response = configured(client, third, owner.qualified_id, state="not_configured")
        assert response.status_code == 200
        third = response.json()["gpo"]
        assert [item["value_name"] for item in third["settings"]] == (
            [retained] if retained else []
        )


@pytest.mark.parametrize("owner", ["parent", "child"])
@pytest.mark.parametrize("state", ["enabled", "not_configured"])
def test_current_address_matches_do_not_authorize_ambiguous_legacy_repair(
    harness: Any,
    monkeypatch: Any,
    owner: str,
    state: str,
) -> None:
    client, store = harness
    parent, child = overlapping_schemas()
    monkeypatch.setattr(app.state, "admx_catalogue", AdmxCatalogue(policies=(parent, child)))
    legacy_id = f"admx-{child.qualified_id}-computer-state"
    old = inject_legacy(
        store,
        (
            RegistrySetting(
                legacy_id, "computer", "HKLM", parent.key, "Parent", "REG_SZ", "parent-old"
            ),
            RegistrySetting(legacy_id, "computer", "HKLM", child.key, "Child", "REG_DWORD", 1),
        ),
    )
    before = storage(store)
    chosen = parent if owner == "parent" else child
    values = (
        {parent.elements[0].id: "parent-new"} if owner == "parent" and state == "enabled" else {}
    )
    response = configured(client, old.to_dict(), chosen.qualified_id, state=state, values=values)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["issues"][0]["code"] == "ambiguous_setting_owner"
    read = client.get(f"/api/gpos/{old.guid}/configured-settings")
    assert read.status_code == 200
    assert not read.json()["resolved"] and len(read.json()["unresolved"]) == 2
    assert storage(store) == before


def test_unknown_legacy_owner_refuses_reconfigure_without_changing_history(
    harness: Any, monkeypatch: Any
) -> None:
    client, store = harness
    parent, child = overlapping_schemas()
    monkeypatch.setattr(app.state, "admx_catalogue", AdmxCatalogue(policies=(parent,)))
    old = inject_legacy(
        store,
        (
            RegistrySetting(
                f"admx-{child.qualified_id}-computer-state",
                "computer",
                "HKLM",
                child.key,
                "Child",
                "REG_DWORD",
                1,
            ),
        ),
    )
    before = storage(store)
    response = configured(client, old.to_dict(), parent.qualified_id, state="not_configured")
    assert response.status_code == 422
    assert response.json()["error"]["issues"][0]["code"] == "ambiguous_setting_owner"
    assert storage(store) == before
    result = client.get(f"/api/gpos/{old.guid}/configured-settings").json()
    assert result["unresolved"][0]["reason"] == "ambiguous legacy policy ownership"
    assert client.get(f"/api/gpos/{old.guid}/revisions/2").status_code == 200


@pytest.mark.parametrize("operation", ["edit", "comment", "delete"])
def test_ambiguous_id_target_actions_cannot_collapse_or_remove_multiple_rows(
    harness: Any, operation: str
) -> None:
    client, store = harness
    old = inject_legacy(store, (record(name="One"), record(name="Two")))
    before = storage(store)
    path = f"/api/gpos/{old.guid}/settings/shared"
    if operation == "edit":
        response = client.put(
            path,
            json={
                "expected_revision": old.revision,
                "setting": {
                    "side": "computer",
                    "hive": "HKLM",
                    "key": KEY,
                    "value_name": "Changed",
                    "registry_type": "REG_SZ",
                    "value": "changed",
                },
            },
        )
    elif operation == "comment":
        response = client.patch(
            path + "/comment", json={"expected_revision": old.revision, "comment": "note"}
        )
    else:
        response = client.request("DELETE", path, json={"expected_revision": old.revision})
    assert response.status_code == 422
    assert response.json()["error"]["issues"][0]["code"] == "ambiguous_setting_id"
    assert storage(store) == before
    assert len(client.get(f"/api/gpos/{old.guid}").json()["gpo"]["settings"]) == 2


@pytest.mark.parametrize("operation", ["create", "batch", "metadata", "copy", "fork", "restore"])
def test_full_head_guard_covers_commit_paths_without_changing_legacy_data(
    harness: Any, operation: str
) -> None:
    client, store = harness
    old = inject_legacy(store, (record(name="One"), record(name="Two")))
    target = store.create_gpo("Synthetic clean target", identity="test", reason="Seed target")
    before = storage(store)
    with pytest.raises(ValidationError) as failure:
        if operation == "create":
            store.create_gpo(
                "Rejected duplicates", settings=old.settings, identity="test", reason="test"
            )
        elif operation == "batch":
            store.put_settings(target.guid, 1, list(old.settings), identity="test", reason="test")
        elif operation == "metadata":
            store.update_metadata(
                old.guid, old.revision, {"description": "new"}, identity="test", reason="test"
            )
        elif operation == "copy":
            store.put_settings(target.guid, 1, list(old.settings), identity="test", reason="copy")
        elif operation == "fork":
            store.fork_gpo(old.guid, "Rejected fork", identity="test", reason="test")
        else:
            store.restore_revision(old.guid, 2, old.revision, identity="test", reason="test")
    assert failure.value.issues[0].code == "duplicate_setting_id"
    assert storage(store) == before
    assert len(store.get_gpo(old.guid).settings) == 2


def test_import_preflight_rejects_bad_entry_and_keeps_valid_ids(harness: Any) -> None:
    _client, store = harness
    good = GPO(
        str(uuid4()),
        "Synthetic valid import",
        settings=(record("Case", "One"), record("case", "Two")),
    )
    bad = replace(
        good,
        guid=str(uuid4()),
        name="Synthetic invalid import",
        settings=(record(name="One"), record(name="Two")),
    )
    result = store.import_baseline_gpos(
        [good, bad], identity="test", reason="Import synthetic entries"
    )
    assert result["imported"] == 1 and result["rejected"] == 1
    assert [item.id for item in store.get_gpo(good.guid).settings] == ["Case", "case"]
    assert len(store.revisions(good.guid)) == 1


def test_ordinary_legacy_ids_support_import_copy_fork_and_clean_restore(harness: Any) -> None:
    client, store = harness
    old = inject_legacy(store, (record(name="One"), record(name="Two")))
    recovered = store.restore_revision(
        old.guid, 1, old.revision, identity="test", reason="Restore clean revision"
    )
    assert [item.id for item in recovered.settings] == ["seed-0", "seed-1"]
    copied = store.create_gpo("Synthetic copy target", identity="test", reason="Seed target")
    copied = store.put_settings(
        copied.guid, 1, list(recovered.settings), identity="test", reason="Copy valid IDs"
    )
    assert copied.settings == recovered.settings
    forked = store.fork_gpo(
        copied.guid, "Synthetic valid fork", identity="test", reason="Fork valid IDs"
    )
    assert [item.id for item in forked.settings] == ["forked-seed-0", "forked-seed-1"]
    assert len(client.get(f"/api/gpos/{old.guid}/revisions/2").json()["snapshot"]["settings"]) == 2


def test_reviewed_positions_use_store_cas_before_any_replacement(harness: Any) -> None:
    _client, store = harness
    gpo = store.create_gpo(
        "Synthetic stale selection", settings=(record("first"),), identity="test", reason="Seed"
    )
    current = store.update_metadata(
        gpo.guid, 1, {"description": "concurrent change"}, identity="test", reason="Change"
    )
    before = storage(store)
    from gpo_studio.model import ConflictError

    with pytest.raises(ConflictError):
        store.replace_selected_settings(
            gpo.guid, 1, frozenset({0}), [], identity="test", reason="Stale selection"
        )
    assert storage(store) == before and store.get_gpo(gpo.guid) == current


def test_schema_choice_is_stable_and_ordinary_ids_and_registry_bytes_stay_unchanged() -> None:
    ordinary = schemas()[0]
    assert not structured_policy_ids(ordinary)
    settings = resolve_policy(ordinary, PolicyConfiguration("computer", {}))
    assert settings[0].id == "admx-Synthetic.Identities:Simple-computer-state"
    for scalar_id in ("Items-1", "Items-0", "Items-named"):
        collision = replace(schemas()[3], elements=(schemas()[3].elements[0], text(scalar_id)))
        for count in [0, 1, 12]:
            resolved = resolve_policy(
                collision,
                PolicyConfiguration(
                    "computer",
                    {
                        "Items": [str(index) for index in range(count)],
                        scalar_id: "scalar",
                    },
                ),
            )
            assert all(parse_structured_setting_id(item.id) is not None for item in resolved)
            assert len({item.id for item in resolved}) == count + 1
    gpo = GPO(str(uuid4()), "Synthetic byte control", settings=tuple(settings))
    renamed = replace(
        gpo, settings=tuple(replace(item, id="editor-only-change") for item in settings)
    )
    assert policy_semantic_dict(gpo) == policy_semantic_dict(renamed)
    assert _gpmc_preg_bytes(list(gpo.settings)) == _gpmc_preg_bytes(list(renamed.settings))


def test_opaque_component_encoding_round_trips_and_rejects_malformed_ids() -> None:
    p = policy("Unicode", elements=(text("state"), text("組-computer-δ:tail", "Other")))
    resolved = resolve_policy(
        p, PolicyConfiguration("computer", {"state": "one", "組-computer-δ:tail": "two"})
    )
    identities = [parse_structured_setting_id(item.id) for item in resolved]
    assert [item.component for item in identities if item is not None] == [
        "state",
        "組-computer-δ:tail",
    ]
    assert len({item.id for item in resolved}) == 2
    for malformed in [
        "admx2:xx:computer:state::0",
        "admx2:61:other:state::0",
        "admx2:61:computer:state::01",
        "admx2:61:computer:element-list:62:0",
    ]:
        assert parse_structured_setting_id(malformed) is None
    assert len(legacy_setting_parts("admx-a-computer-b-computer-state")) == 2
    assert len(legacy_setting_parts("admx-a-computer-computer-state")) == 2
    with pytest.raises(ValidationError):
        policy_setting_positions(
            (record("admx-a-computer-b-computer-state"),), AdmxCatalogue(), {("a", "computer")}
        )


def test_route_unsafe_source_names_generate_ids_usable_by_existing_crud(
    harness: Any, monkeypatch: Any
) -> None:
    client, _store = harness
    p = replace(
        policy("RoutePolicy", elements=(text("Option/?#%\\"),)),
        namespace="https://synthetic.example/Policies/configure/pack%2Fone?stage=one#definition",
    )
    monkeypatch.setattr(app.state, "admx_catalogue", AdmxCatalogue(policies=(p,)))
    gpo = draft(client)
    detail_path = "/api/admx/policies/" + quote(p.qualified_id, safe="")
    detail = client.get(detail_path)
    assert detail.status_code == 200, detail.text
    assert detail.json()["qualified_id"] == p.qualified_id
    response = client.post(
        detail_path + "/configure",
        json={
            "gpo_guid": gpo["guid"],
            "side": "computer",
            "expected_revision": 1,
            "values": {p.elements[0].id: "original"},
        },
    )
    assert response.status_code == 200, response.text
    saved = response.json()["gpo"]
    identifier = saved["settings"][0]["id"]
    assert identifier.startswith("admx2:") and not set("/?#%\\").intersection(identifier)
    path = f"/api/gpos/{gpo['guid']}/settings/{identifier}"
    response = client.patch(
        path + "/comment", json={"expected_revision": saved["revision"], "comment": "note"}
    )
    assert response.status_code == 200
    saved = response.json()["gpo"]
    response = client.put(
        path,
        json={
            "expected_revision": saved["revision"],
            "setting": {
                "side": "computer",
                "hive": "HKLM",
                "key": p.key,
                "value_name": "Option",
                "registry_type": "REG_SZ",
                "value": "edited",
            },
        },
    )
    assert response.status_code == 200
    saved = response.json()["gpo"]
    response = client.request("DELETE", path, json={"expected_revision": saved["revision"]})
    assert response.status_code == 200, response.text
    assert response.json()["gpo"]["settings"] == []


def test_known_but_ambiguous_legacy_owners_cannot_be_guessed(
    harness: Any, monkeypatch: Any
) -> None:
    client, store = harness
    parent, child = overlapping_schemas()
    parent = replace(parent, key=child.key, elements=(text(parent.elements[0].id, "Child"),))
    monkeypatch.setattr(app.state, "admx_catalogue", AdmxCatalogue(policies=(parent, child)))
    old = inject_legacy(
        store,
        (
            RegistrySetting(
                f"admx-{child.qualified_id}-computer-state",
                "computer",
                "HKLM",
                child.key,
                "Child",
                "REG_DWORD",
                1,
            ),
        ),
    )
    before = storage(store)
    response = configured(client, old.to_dict(), parent.qualified_id, state="not_configured")
    assert response.status_code == 422
    assert response.json()["error"]["issues"][0]["code"] == "ambiguous_setting_owner"
    assert storage(store) == before


def test_explicit_reconfigure_repairs_legacy_state_collision_and_retains_both_writes(
    harness: Any,
) -> None:
    client, store = harness
    p = schemas()[2]
    legacy_id = f"admx-{p.qualified_id}-computer-state"
    old = inject_legacy(
        store,
        (
            RegistrySetting(legacy_id, "computer", "HKLM", p.key, "State", "REG_DWORD", 1),
            RegistrySetting(legacy_id, "computer", "HKLM", p.key, "ZZZOption", "REG_SZ", "old"),
        ),
    )
    response = configured(client, old.to_dict(), p.qualified_id, values={"state": "new"})
    assert response.status_code == 200, response.text
    rows = response.json()["gpo"]["settings"]
    assert {item["value_name"] for item in rows} == {"State", "ZZZOption"}
    assert len({item["id"] for item in rows}) == 2
    assert all(item["id"].startswith("admx2:") for item in rows)
    historical = store.get_revision(old.guid, 2).snapshot["settings"]
    assert [item["id"] for item in historical] == [legacy_id, legacy_id]


def test_ordinary_scalar_prefix_names_keep_ids_and_decode_exact_elements(
    harness: Any, monkeypatch: Any
) -> None:
    client, _store = harness
    p = policy("ScalarPrefixes", elements=(text("Option", "ZZZ"), text("Option-extra", "AAA")))
    assert not structured_policy_ids(p)
    monkeypatch.setattr(app.state, "admx_catalogue", AdmxCatalogue(policies=(p,)))
    gpo = draft(client)
    response = configured(
        client, gpo, p.qualified_id, values={"Option": "first", "Option-extra": "second"}
    )
    assert response.status_code == 200, response.text
    rows = response.json()["gpo"]["settings"]
    assert {item["id"] for item in rows} == {
        f"admx-{p.qualified_id}-computer-Option",
        f"admx-{p.qualified_id}-computer-Option-extra",
    }
    decoded = client.get(f"/api/gpos/{gpo['guid']}/configured-settings").json()
    assert decoded["resolved"][0]["element_values"] == {"Option": "first", "Option-extra": "second"}


@pytest.mark.parametrize("items", [[], ["one"], ["one", "two", "three"]])
def test_fixed_policy_list_is_not_reconstructed_as_editable_list_content(
    harness: Any,
    items: list[str],
) -> None:
    client, _store = harness
    gpo = draft(client)
    response = configured(client, gpo, "StateListListElement", values={"listitem": items})
    assert response.status_code == 200, response.text
    saved = response.json()["gpo"]
    assert len(saved["settings"]) == len(items) + 1
    assert len({item["id"] for item in saved["settings"]}) == len(items) + 1
    decoded = client.get(f"/api/gpos/{gpo['guid']}/configured-settings").json()
    assert decoded["resolved"][0]["element_values"].get("listitem", []) == items
    assert all(parse_structured_setting_id(item["id"]) is not None for item in saved["settings"])


def test_policy_list_selector_preserves_ordinary_listitem_spelling_and_native_bytes() -> None:
    original = next(p for p in schemas() if p.id == "StateListListElement")
    for field in ("enabled_list", "disabled_list"):
        candidate = replace(
            original,
            **{
                "enabled_list": None,
                "disabled_list": None,
                field: original.enabled_list,
            },
        )
        assert structured_policy_ids(candidate)
        for count in (0, 1, 12):
            settings = resolve_policy(
                candidate,
                PolicyConfiguration(
                    "computer",
                    {"listitem": [str(i) for i in range(count)]},
                ),
            )
            assert all(parse_structured_setting_id(item.id) is not None for item in settings)
            gpo = GPO(str(uuid4()), "Synthetic list byte control", settings=tuple(settings))
            renamed = replace(
                gpo,
                settings=tuple(
                    replace(item, id=f"editor-only-{index}") for index, item in enumerate(settings)
                ),
            )
            assert _gpmc_preg_bytes(list(gpo.settings)) == _gpmc_preg_bytes(list(renamed.settings))
    ordinary = replace(original, enabled_list=None, disabled_list=None)
    assert not structured_policy_ids(ordinary)
    (setting,) = resolve_policy(ordinary, PolicyConfiguration("computer", {"listitem": ["one"]}))
    assert setting.id == "admx-Synthetic.Identities:StateListListElement-computer-listitem-1"


def test_legacy_fixed_list_remains_readable_without_becoming_editable_list_content(
    harness: Any,
) -> None:
    client, store = harness
    definition = next(p for p in schemas() if p.id == "StateListListElement")
    legacy = RegistrySetting(
        id="admx-Synthetic.Identities:StateListListElement-computer-listitem-1",
        side="computer",
        hive="HKLM",
        key=definition.key,
        value_name="FixedValue",
        registry_type="REG_SZ",
        value="fixed",
    )
    gpo = store.create_gpo(
        "Synthetic legacy fixed list",
        settings=(legacy,),
        identity="test",
        reason="Seed legacy identity",
    )
    before = storage(store)
    detail = client.get(f"/api/gpos/{gpo.guid}")
    assert detail.status_code == 200
    assert detail.json()["gpo"]["settings"][0]["id"] == legacy.id
    configured_rows = client.get(f"/api/gpos/{gpo.guid}/configured-settings").json()
    assert configured_rows["resolved"][0]["element_values"].get("listitem", []) == []
    assert storage(store) == before


@pytest.mark.parametrize("remapped", [False, True])
@pytest.mark.parametrize("operation", ["configure", "bulk"])
def test_historical_ambiguous_single_row_refuses_without_changing_readable_records(
    harness: Any,
    monkeypatch: Any,
    legacy_producer: Any,
    remapped: bool,
    operation: str,
) -> None:
    client, store = harness
    parent, child = remapped_schemas() if remapped else overlapping_schemas()
    monkeypatch.setattr(app.state, "admx_catalogue", AdmxCatalogue(policies=(parent, child)))
    row = legacy_child_row(legacy_producer)
    old = inject_legacy(store, (row,))
    before = storage(store)
    if operation == "configure":
        refused = configured(client, old.to_dict(), parent.qualified_id, state="not_configured")
    else:
        refused = client.post(
            f"/api/gpos/{old.guid}/bulk-policy-state",
            json={
                "expected_revision": old.revision,
                "policy_ids": [parent.qualified_id],
                "side": "computer",
                "target_state": "not_configured",
                "actor": "test",
                "reason": "Review ambiguous synthetic policy",
            },
        )
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["issues"][0]["code"] == "ambiguous_setting_owner"
    head = client.get(f"/api/gpos/{old.guid}")
    assert head.status_code == 200
    assert [item["id"] for item in head.json()["gpo"]["settings"]] == [row.id]
    history = client.get(f"/api/gpos/{old.guid}/revisions/2")
    assert history.status_code == 200
    assert history.json()["snapshot"]["settings"][0]["id"] == row.id
    read = client.get(f"/api/gpos/{old.guid}/configured-settings")
    assert read.status_code == 200
    assert not read.json()["resolved"] and len(read.json()["unresolved"]) == 1
    assert read.json()["unresolved"][0]["reason"] == "ambiguous legacy policy ownership"
    # Ownership uncertainty is an editing constraint, not a blanket export ban.
    assert client.get(f"/api/gpos/{old.guid}/gpmc-backup").status_code == 200
    assert storage(store) == before


def test_exact_structured_and_single_frame_legacy_owners_keep_their_side_rules() -> None:
    ordinary = schemas()[0]
    row = resolve_policy(ordinary, PolicyConfiguration("computer", {}))[0]
    assert len(legacy_setting_parts(row.id)) == 1
    assert setting_policy_owners(row, AdmxCatalogue()) == ((ordinary.qualified_id, "computer"),)
    assert not setting_policy_owners(replace(row, side="user", hive="HKCU"), AdmxCatalogue())

    parent, child = overlapping_schemas()
    structured = resolve_policy(child, PolicyConfiguration("computer", {}))[0]
    assert structured.id.startswith("admx2:")
    for policies in (overlapping_schemas(), remapped_schemas()):
        catalogue = AdmxCatalogue(policies=policies)
        assert setting_policy_owners(structured, catalogue) == ((child.qualified_id, "computer"),)
        assert (
            policy_setting_positions((structured,), catalogue, {(parent.qualified_id, "computer")})
            == frozenset()
        )
        assert policy_setting_positions(
            (structured,), catalogue, {(child.qualified_id, "computer")}
        ) == frozenset({0})
        assert not setting_policy_owners(replace(structured, side="user", hive="HKCU"), catalogue)


def test_reviewed_fresh_draft_recovery_keeps_historical_ambiguous_source_unchanged(
    harness: Any,
    monkeypatch: Any,
    legacy_producer: Any,
) -> None:
    client, store = harness
    monkeypatch.setattr(app.state, "admx_catalogue", AdmxCatalogue(policies=remapped_schemas()))
    old = inject_legacy(store, (legacy_child_row(legacy_producer),))
    original = store.get_gpo(old.guid)
    history = tuple(store.revisions(old.guid))
    native = _gpmc_preg_bytes(list(original.settings))
    fresh = draft(client, "Synthetic explicitly reviewed recovery")
    chosen = asdict(original.settings[0])
    del chosen["id"]
    chosen["value"] = str(chosen["value"])
    recovered = client.post(
        f"/api/gpos/{fresh['guid']}/settings",
        json={
            "expected_revision": 1,
            "setting": chosen,
            "actor": "test",
            "reason": "Retain reviewed raw registry record with fresh identity",
        },
    )
    assert recovered.status_code == 201, recovered.text
    new_id = recovered.json()["gpo"]["settings"][0]["id"]
    UUID(new_id)
    assert new_id != original.settings[0].id
    assert _gpmc_preg_bytes(list(store.get_gpo(fresh["guid"]).settings)) == native
    assert client.get(f"/api/gpos/{fresh['guid']}/gpmc-backup").status_code == 200
    assert store.get_gpo(old.guid) == original
    assert tuple(store.revisions(old.guid)) == history
    assert client.get(f"/api/gpos/{old.guid}").status_code == 200
