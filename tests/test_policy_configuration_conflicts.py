"""ADMX configuration must not introduce collisions with retained registry rows."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from test_setting_identities import configured, draft, schemas, storage
from test_setting_identities import harness as harness

from gpo_studio.admx import AdmxCatalogue, PolicyElement
from gpo_studio.api import app
from gpo_studio.policy_config import PolicyConfiguration, resolve_policy


def apply(client: Any, gpo: Any, *, bulk: bool, state: str = "enabled") -> Any:
    if not bulk:
        return configured(client, gpo, "Simple", state=state)
    return client.post(
        f"/api/gpos/{gpo['guid']}/bulk-policy-state",
        json={
            "policy_ids": ["Simple", "Other"],
            "side": "computer",
            "target_state": state,
            "expected_revision": gpo["revision"],
        },
    )


@pytest.mark.parametrize("bulk", [False, True])
@pytest.mark.parametrize("state", ["enabled", "disabled"])
@pytest.mark.parametrize("action", ["set", "delete"])
@pytest.mark.parametrize("identifier", ["imported-computer-0", "raw-row", "other-owner"])
def test_retained_address_conflict_refuses_without_any_write(
    harness: Any, bulk: bool, state: str, action: str, identifier: str
) -> None:
    client, store = harness
    row = resolve_policy(schemas()[0], PolicyConfiguration("computer", {}))[0]
    row = replace(
        row,
        id=("admx-Synthetic.Identities:Absent-computer-state"
            if identifier == "other-owner" else identifier),
        key=row.key.swapcase(),
        value_name=row.value_name.swapcase(),
        action=action,
    )
    gpo = store.create_gpo(
        "Synthetic retained row", settings=(row,), identity="test", reason="Synthetic seed"
    )
    before = storage(store)
    response = apply(client, gpo.to_dict(), bulk=bulk, state=state)
    assert response.status_code == 422, response.text
    issue = response.json()["error"]["issues"][0]
    assert issue["code"] == "conflicting_retained_setting"
    assert issue["path"] == "settings/0"
    assert storage(store) == before


@pytest.mark.parametrize("bulk", [False, True])
def test_public_fork_reconfigure_refuses_without_touching_source_or_fork(
    harness: Any, bulk: bool
) -> None:
    client, store = harness
    original = configured(client, draft(client), "Simple", state="disabled")
    assert original.status_code == 200
    original = original.json()["gpo"]
    fork = client.post(f"/api/gpos/{original['guid']}/fork", json={"name": "Synthetic fork"})
    assert fork.status_code == 201
    fork = fork.json()["gpo"]
    assert fork["settings"][0]["id"].startswith("forked-")
    before = storage(store)
    response = apply(client, fork, bulk=bulk)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["issues"][0]["code"] == "conflicting_retained_setting"
    assert storage(store) == before


@pytest.mark.parametrize("bulk", [False, True])
def test_owned_rows_are_replaced_and_unrelated_dirty_rows_do_not_block(
    harness: Any, bulk: bool
) -> None:
    client, store = harness
    owned = resolve_policy(schemas()[0], PolicyConfiguration("computer", {}, "disabled"))[0]
    unrelated = replace(owned, id="raw-first", value_name="Unrelated")
    rows = (owned, unrelated, replace(unrelated, id="raw-second"))
    gpo = store.create_gpo(
        "Synthetic unrelated duplicates", settings=rows, identity="test", reason="Synthetic seed"
    )
    response = apply(client, gpo.to_dict(), bulk=bulk)
    assert response.status_code == 200, response.text
    settings = response.json()["gpo"]["settings"]
    assert tuple(r for r in store.get_gpo(gpo.guid).settings if r.id.startswith("raw-")) == rows[1:]
    assert next(r for r in settings if r["id"] == owned.id)["value"] == "1"


@pytest.mark.parametrize("bulk", [False, True])
def test_stale_revision_precedes_address_refusal(harness: Any, bulk: bool) -> None:
    client, store = harness
    gpo = configured(client, draft(client), "Simple").json()["gpo"]
    gpo["revision"] -= 1
    before = storage(store)
    assert apply(client, gpo, bulk=bulk).status_code == 409
    assert storage(store) == before


def test_same_address_on_other_side_is_not_a_conflict(harness: Any, monkeypatch: Any) -> None:
    client, store = harness
    policy = replace(schemas()[0], class_="Both")
    monkeypatch.setattr(app.state, "admx_catalogue", AdmxCatalogue(policies=(policy,)))
    other = replace(resolve_policy(policy, PolicyConfiguration("user", {}))[0], id="raw-user")
    gpo = store.create_gpo(
        "Synthetic other side", settings=(other,), identity="test", reason="Synthetic seed"
    )
    response = apply(client, gpo.to_dict(), bulk=False)
    assert response.status_code == 200, response.text
    assert other in store.get_gpo(gpo.guid).settings


@pytest.mark.parametrize("bulk", [False, True])
def test_not_configured_preserves_unowned_rows_without_claiming_ownership(
    harness: Any, bulk: bool
) -> None:
    client, store = harness
    row = replace(resolve_policy(schemas()[0], PolicyConfiguration("computer", {}))[0], id="raw")
    gpo = store.create_gpo(
        "Synthetic raw row", settings=(row,), identity="test", reason="Synthetic seed"
    )
    response = apply(client, gpo.to_dict(), bulk=bulk, state="not_configured")
    assert response.status_code == 200, response.text
    assert store.get_gpo(gpo.guid).settings == (row,)


def test_bulk_replacement_owners_are_all_removed_before_conflict_check(
    harness: Any, monkeypatch: Any
) -> None:
    client, store = harness
    first, second = schemas()[:2]
    second = replace(second, key=first.key, value_name=first.value_name)
    monkeypatch.setattr(app.state, "admx_catalogue", AdmxCatalogue(policies=(first, second)))
    rows = tuple(resolve_policy(p, PolicyConfiguration("computer", {}))[0] for p in (first, second))
    gpo = store.create_gpo(
        "Synthetic owned conflicts", settings=rows, identity="test", reason="Synthetic seed"
    )
    response = apply(client, gpo.to_dict(), bulk=True, state="not_configured")
    assert response.status_code == 200, response.text
    assert store.get_gpo(gpo.guid).settings == ()


@pytest.mark.parametrize("bulk", [False, True])
def test_new_plan_collisions_refuse_without_any_write(
    harness: Any, monkeypatch: Any, bulk: bool
) -> None:
    client, store = harness
    first, second = schemas()[:2]
    if bulk:
        second = replace(second, key=first.key, value_name=first.value_name.swapcase())
    else:
        first = replace(first, elements=(PolicyElement(
            kind="text", id="Child", registry_value_name=first.value_name.swapcase()
        ),))
    monkeypatch.setattr(app.state, "admx_catalogue", AdmxCatalogue(policies=(first, second)))
    gpo = draft(client)
    before = storage(store)
    response = (
        apply(client, gpo, bulk=True)
        if bulk else configured(client, gpo, "Simple", values={"Child": "Synthetic child"})
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["issues"][0]["code"] == "conflicting_policy_settings"
    assert storage(store) == before
