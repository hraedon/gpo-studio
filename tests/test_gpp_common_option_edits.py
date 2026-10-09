"""A workbench edit keeps every common option it does not change (WI-082).

The group and registry payloads carried no common options, and the store
replaced the item wholesale, so every edit through the workbench reset
apply-once (dropping its run-once filter), disabled, remove-when-not-applied,
run-in-user-context and stop-on-error to their defaults. These tests go
through the real API, one option at a time, on both editable families, and
then check what the export writes.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.gpp import (
    GppCollection,
    GppCommonOptions,
    GppGroup,
    GppRegistry,
    GppRegistryValue,
)
from gpo_studio.store import WorkspaceStore

OPTIONS = (
    "apply_once",
    "remove_when_unapplied",
    "user_security_context",
    "disabled",
    "stop_on_error",
)
#: How each option is written on the item element (MS-GPPREF; apply-once is a
#: ``FilterRunOnce`` predicate, checked separately).
_ATTRIBUTE = {
    "remove_when_unapplied": ("removePolicy", "1"),
    "user_security_context": ("userContext", "1"),
    "disabled": ("disabled", "1"),
    "stop_on_error": ("bypassErrors", "0"),
}
_RUN_ONCE_ID = "{11111111-AAAA-4BBB-8CCC-DDDDDDDDDDDD}"
_GROUP_KEYS = (
    "name", "sid", "action", "description", "remove_all_users", "remove_all_groups",
    "members", "id", "ilt_filter", "unknown_attrs", "unknown_props_attrs",
    "unknown_props_children", "unknown_children",
)
_REGISTRY_KEYS = (
    "key", "hive", "action", "value", "id", "uid", "ilt_filter", "unknown_attrs",
    "unknown_props_children", "unknown_children",
)
_VALUE_KEYS = ("name", "value", "registry_type", "action", "default", "id", "unknown_attrs")


def _common(option: str) -> GppCommonOptions:
    common = replace(GppCommonOptions(), **{option: True})
    return replace(common, run_once_id=_RUN_ONCE_ID) if option == "apply_once" else common


def workbench_group(group: dict[str, Any]) -> dict[str, Any]:
    """What static/js/gpp.mjs sends back for a group: no common options."""
    return {key: group[key] for key in _GROUP_KEYS}


def workbench_registry(registry: dict[str, Any]) -> dict[str, Any]:
    """What static/js/gpp.mjs sends back for a registry item: no common options."""
    payload = {key: registry[key] for key in _REGISTRY_KEYS}
    payload["value"] = {key: registry["value"][key] for key in _VALUE_KEYS}
    return payload


def _seeded(store: WorkspaceStore, option: str) -> str:
    """A draft GPO with one group and one registry item carrying *option*."""
    common = _common(option)
    collection = GppCollection(
        scope="computer",
        groups=(GppGroup(name="Seeded", description="before", common=common),),
        registry=(
            GppRegistry(
                key=r"Software\Policies\GPOStudio\WI082",
                value=GppRegistryValue(name="Value", value="before", registry_type="REG_SZ"),
                common=common,
            ),
        ),
    )
    gpo = store.create_gpo(
        name=f"WI-082 {option}", identity="wi082", reason="seed",
        gpp_collections=(collection,),
    )
    return gpo.guid


def _written_items(client: TestClient, guid: str) -> dict[str, ET.Element]:
    bundle = client.get(f"/api/gpos/{guid}/export.zip")
    assert bundle.status_code == 200, bundle.text
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        groups = ET.fromstring(archive.read("Machine/Preferences/Groups/Groups.xml"))
        registry = ET.fromstring(archive.read("Machine/Preferences/Registry/Registry.xml"))
    return {"groups": groups[0], "registry": registry[0]}


def _assert_written(item: ET.Element, option: str) -> None:
    if option == "apply_once":
        run_once = item.find("Filters/FilterRunOnce")
        assert run_once is not None and run_once.get("id") == _RUN_ONCE_ID
        return
    name, value = _ATTRIBUTE[option]
    assert item.get(name) == value


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    with closing(WorkspaceStore(tmp_path / "wi082.db")) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        with TestClient(app, base_url="http://127.0.0.1") as test_client:
            yield test_client, store


@pytest.mark.parametrize("option", OPTIONS)
def test_a_workbench_edit_keeps_each_common_option(client: Any, option: str) -> None:
    test_client, store = client
    guid = _seeded(store, option)
    gpo = test_client.get(f"/api/gpos/{guid}").json()["gpo"]
    collection = gpo["gpp_collections"][0]
    group = workbench_group(collection["groups"][0])
    group["description"] = "edited"
    edited = test_client.put(
        f"/api/gpos/{guid}/preferences/groups/{group['id']}",
        json={"scope": "computer", "group": group, "actor": "wi082", "reason": "edit",
              "expected_revision": gpo["revision"]},
    )
    assert edited.status_code == 200, edited.text
    # The response shows the options it kept.
    edited_group = edited.json()["gpo"]["gpp_collections"][0]["groups"][0]
    assert edited_group["common"][option] is True
    registry = workbench_registry(collection["registry"][0])
    registry["value"]["value"] = "edited"
    edited = test_client.put(
        f"/api/gpos/{guid}/preferences/registry/{registry['id']}",
        json={"scope": "computer", "registry": registry, "actor": "wi082", "reason": "edit",
              "expected_revision": edited.json()["gpo"]["revision"]},
    )
    assert edited.status_code == 200, edited.text

    stored = store.get_gpo(guid).gpp_collections[0]
    expected = _common(option)
    for item in (stored.groups[0], stored.registry[0]):
        assert item.common == expected
        assert item.common.run_once_id == expected.run_once_id
    assert stored.groups[0].description == "edited"
    assert stored.registry[0].value.value == "edited"
    for item in _written_items(test_client, guid).values():
        _assert_written(item, option)


@pytest.mark.parametrize("option", OPTIONS)
def test_an_edit_changes_only_the_options_it_names(client: Any, option: str) -> None:
    test_client, store = client
    guid = _seeded(store, option)
    gpo = test_client.get(f"/api/gpos/{guid}").json()["gpo"]
    group = workbench_group(gpo["gpp_collections"][0]["groups"][0])
    other = next(name for name in OPTIONS if name != option)
    group["common"] = {other: True}
    edited = test_client.put(
        f"/api/gpos/{guid}/preferences/groups/{group['id']}",
        json={"scope": "computer", "group": group, "actor": "wi082", "reason": "edit",
              "expected_revision": gpo["revision"]},
    )
    assert edited.status_code == 200, edited.text
    common = store.get_gpo(guid).gpp_collections[0].groups[0].common
    assert getattr(common, option) is True and getattr(common, other) is True
    # And an explicit false turns the option off.
    group["common"] = {option: False}
    edited = test_client.put(
        f"/api/gpos/{guid}/preferences/groups/{group['id']}",
        json={"scope": "computer", "group": group, "actor": "wi082", "reason": "edit",
              "expected_revision": edited.json()["gpo"]["revision"]},
    )
    assert edited.status_code == 200, edited.text
    common = store.get_gpo(guid).gpp_collections[0].groups[0].common
    assert getattr(common, option) is False and getattr(common, other) is True
    # Turning apply-once off keeps the run-once identity (WI-080).
    assert common.run_once_id == _common(option).run_once_id


def test_an_added_item_takes_the_defaults_and_the_options_it_names(client: Any) -> None:
    test_client, store = client
    gpo = test_client.post(
        "/api/gpos", json={"name": "WI-082 add", "actor": "wi082", "reason": "add"},
    ).json()["gpo"]
    added = test_client.post(
        f"/api/gpos/{gpo['guid']}/preferences/groups",
        json={"scope": "computer", "actor": "wi082", "reason": "add",
              "expected_revision": gpo["revision"],
              "group": {"name": "Added", "common": {"disabled": True}}},
    )
    assert added.status_code == 201, added.text
    common = store.get_gpo(gpo["guid"]).gpp_collections[0].groups[0].common
    assert common == GppCommonOptions(disabled=True)


def test_the_api_cannot_set_the_run_once_identity(client: Any) -> None:
    test_client, store = client
    guid = _seeded(store, "apply_once")
    gpo = test_client.get(f"/api/gpos/{guid}").json()["gpo"]
    group = workbench_group(gpo["gpp_collections"][0]["groups"][0])
    group["common"] = {"run_once_id": "{00000000-0000-0000-0000-000000000000}"}
    edited = test_client.put(
        f"/api/gpos/{guid}/preferences/groups/{group['id']}",
        json={"scope": "computer", "group": group, "actor": "wi082", "reason": "edit",
              "expected_revision": gpo["revision"]},
    )
    assert edited.status_code == 200, edited.text
    assert store.get_gpo(guid).gpp_collections[0].groups[0].common.run_once_id == _RUN_ONCE_ID
