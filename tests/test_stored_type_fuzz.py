"""Every stored field, holding every JSON type (final-r18).

Stored JSON used to be read with Python's coercions: ``bool("false")`` is
``True``, so an estate import stored and exported the opposite policy (Sol
final-r18 B3), and a list where a closed-set string belongs failed membership
with a 500 (DeepSeek final-r18 B1). The typed load (`stored_types`) holds each
value to its field's type and reports one of the wrong type, replacing it by
the default; validation reports it, so the GPO is blocked.

This fuzz plants each JSON type -- null, a boolean, a number, a string, an
array, an object -- in every field of a stored GPO that holds one of every
kind of item, and requires: the GPO loads, validates and builds its payload
without raising; a value of the wrong type blocks every content export; and,
through every GPO route for a broad set of fields, nothing answers 500 and a
mutation commits only what it answers. Plants that load the same GPO (the
wrong types of one field, replaced by its default) are driven once. For
the boolean fields that reach a plan, a stored boolean is exported exactly.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import tempfile
import types
import typing
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

import pytest
from fastapi.testclient import TestClient
from test_closed_set_fuzz import _sample

from gpo_studio import api
from gpo_studio.api import app
from gpo_studio.store import WorkspaceStore, _replaced_on_read, gpo_from_dict
from gpo_studio.stored_types import _check, _hints
from gpo_studio.validation import validate_gpo

JSON_VALUES: dict[str, Any] = {
    "null": None,
    "boolean": True,
    "number": 7,
    "string": "false",
    "array": ["x"],
    "object": {"x": 1},
}
CONTENT_EXPORTS = ("studio_export", "gpmc_export", "powershell_plan")


def _fields(
    value: Any, data: Any, path: tuple[Any, ...] = ()
) -> Iterator[tuple[str, tuple[Any, ...], Any]]:
    """(label, path into the stored dict, annotation) for every stored field;
    the first element of each array stands for the rest."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type) and isinstance(data, dict):
        for name, annotation, _default in _hints(type(value)):
            if name not in data or name in ("load_problems", "source_files"):
                continue
            yield f"{type(value).__name__}.{name}", (*path, name), annotation
            yield from _fields(getattr(value, name), data[name], (*path, name))
    elif isinstance(value, (tuple, list)) and isinstance(data, list) and value and data:
        yield from _fields(value[0], data[0], (*path, 0))


def _stored(store: WorkspaceStore, guid: str) -> dict[str, Any]:
    (payload,) = store._connection.execute(
        "SELECT snapshot_json FROM gpos WHERE guid=?", (guid,)
    ).fetchone()
    return dict(json.loads(payload))


def _planted(clean: dict[str, Any], path: tuple[Any, ...], value: Any) -> dict[str, Any]:
    data = copy.deepcopy(clean)
    target = data
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = copy.deepcopy(value)
    return data


def _scratch() -> WorkspaceStore:
    return WorkspaceStore(Path(tempfile.mkdtemp(prefix="type-fuzz-")) / "w.db")


def _inventory() -> tuple[dict[str, Any], dict[str, tuple[tuple[Any, ...], Any]]]:
    with closing(_scratch()) as store:
        guid = _sample(store)
        clean = _stored(store, guid)
        fields: dict[str, tuple[tuple[Any, ...], Any]] = {}
        for label, path, annotation in _fields(store.get_gpo(guid), clean):
            fields.setdefault(label, (path, annotation))
    return clean, fields


CLEAN, FIELDS = _inventory()
LABELS = sorted(FIELDS)

#: The exhaustive fuzz -- every field through every route, each plant in the
#: whole sample GPO -- runs only when asked (CI's own ``type-fuzz-full`` job):
#: it takes over ten minutes, more than the test jobs' budget. The default run
#: still plants every JSON type in every field, in a GPO cut down to that
#: field's item, and drives every route with one field of every kind.
FULL = os.environ.get("GPO_STUDIO_FULL_FUZZ") == "1"


def _slim(data: dict[str, Any], path: tuple[Any, ...]) -> dict[str, Any]:
    """*data* cut down to the item *path* runs through: every other item list
    emptied, every other optional record dropped, and of the preferences only
    the collection and family on the path. The field and everything around it
    on the path are as in the whole sample (`_fields` always takes element 0)."""
    out = copy.deepcopy(data)
    for key, value in data.items():
        if key == path[0]:
            continue
        if isinstance(value, list):
            out[key] = []
        elif key in ("wmi_filter", "fdeploy", "backup_inventory"):
            out[key] = None
    if path[0] == "gpp_collections" and len(path) > 2:
        collection = out["gpp_collections"][path[1]]
        for key, value in list(collection.items()):
            if key != path[2] and isinstance(value, list) and key != "load_problems":
                collection[key] = []
        out["gpp_collections"] = out["gpp_collections"][: path[1] + 1]
    return out


def test_the_fuzz_reaches_every_stored_field() -> None:
    assert len(LABELS) >= 448
    for expected in (
        "GPO.computer_enabled",
        "GPOLink.enabled",
        "SecurityFilter.deny",
        "RegistrySetting.value",
        "GppGroup.remove_all_users",
        "GppCommonOptions.disabled",
        "IltPredicate.negate",
        "GppRegistryValue.default",
        "GppService.first_failure",
    ):
        assert expected in LABELS, expected


def _wrong_type(annotation: Any, value: Any) -> bool:
    problems: list[Any] = []
    _check(annotation, copy.deepcopy(value), "", problems)
    return bool(problems)


def _distinct_plants(
    label: str, *, wrong_only: bool = False, slim: bool = False
) -> list[tuple[str, Any]]:
    """(kind, loaded GPO) for each JSON type planted in *label*'s field, each
    loaded GPO once: a value of the wrong type is replaced by the field's
    default, so the wrong types of one field mostly load the same GPO (and
    `GPO.__eq__` leaves out the load problems, which name the type). Every
    plant is loaded -- the load never raises -- and a GPO already seen is not
    driven again. *wrong_only*: of the right types, keep none. *slim*: plant
    in the GPO cut down to the field's item (`_slim`)."""
    path, annotation = FIELDS[label]
    base = _slim(CLEAN, path) if slim else CLEAN
    seen: list[Any] = []
    out: list[tuple[str, Any]] = []
    for kind, value in JSON_VALUES.items():
        gpo = gpo_from_dict(_planted(base, path, value))
        wrong = _wrong_type(annotation, value)
        if wrong:
            assert gpo.load_problems, (label, kind)
        if wrong_only and not wrong:
            continue
        if any(gpo == other and bool(gpo.load_problems) == bool(other.load_problems)
               for other in seen):
            continue
        seen.append(gpo)
        out.append((kind, gpo))
    return out


@pytest.mark.parametrize("label", LABELS)
def test_every_stored_field_holds_every_json_type(label: str) -> None:
    """Every JSON type in every field: the load and validation never raise,
    and a value of the wrong type is an error whose payload (which never
    raises) blocks every content export. The default run plants in the GPO
    cut down to the field's item; the full run in the whole sample, wrong
    types only (its routes drive the right ones)."""
    path, annotation = FIELDS[label]
    for kind, gpo in _distinct_plants(label, wrong_only=FULL, slim=not FULL):
        issues = validate_gpo(gpo)
        if _wrong_type(annotation, JSON_VALUES[kind]):
            assert any(i.severity == "error" for i in issues), (label, kind)
            payload = api._gpo_payload(gpo)
            for name in CONTENT_EXPORTS:
                assert payload["artifact_capabilities"][name]["enabled"] is False, (
                    label,
                    kind,
                    name,
                )


#: Fields the full run drives through every GPO route: every field outside
#: the preference families, and every field of the first group, its member,
#: targeting predicate and common options, and of the first registry item and
#: value.
ROUTED_FULL = sorted(
    label
    for label in LABELS
    if label.split(".")[0]
    in {
        "GPO",
        "RegistrySetting",
        "GPOLink",
        "SecurityFilter",
        "WmiFilter",
        "CseMetadataEntry",
        "CseFileEntry",
        "GppCollection",
        "GppGroup",
        "GppGroupMember",
        "IltFilter",
        "IltPredicate",
        "IltOsCriteria",
        "GppCommonOptions",
        "GppRegistry",
        "GppRegistryValue",
    }
)




def _kind(annotation: Any) -> str:
    """A field's kind, for choosing the default run's routed fields."""
    origin = get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        inner = "+".join(sorted(_kind(arg) for arg in args))
        return f"optional {inner}" if len(args) < len(get_args(annotation)) else inner
    if origin is Literal:
        return "closed set"
    if origin in (tuple, list):
        return "array"
    if dataclasses.is_dataclass(annotation):
        return "object"
    return getattr(annotation, "__name__", str(annotation))


#: The default run's routed fields: of the full set, the first field (sorted)
#: of every kind (a boolean, a closed set, a string, a number, an array, an
#: object, their optional and union forms), and the hive.
_FIRST_OF_KIND: dict[str, str] = {}
for _label in ROUTED_FULL:
    _FIRST_OF_KIND.setdefault(_kind(FIELDS[_label][1]), _label)
ROUTED_DEFAULT = sorted(
    {
        *_FIRST_OF_KIND.values(),
        "GppRegistry.hive",  # a de-facto closed string a writer normalizes
    }
)
ROUTED = ROUTED_FULL if FULL else ROUTED_DEFAULT


def test_the_default_run_routes_every_kind_of_field() -> None:
    kinds = {_kind(FIELDS[label][1]) for label in ROUTED_FULL}
    assert {_kind(FIELDS[label][1]) for label in ROUTED_DEFAULT} == kinds
    assert {"bool", "closed set", "str", "int", "array", "object"} <= kinds, kinds
    assert set(ROUTED_DEFAULT) <= set(ROUTED_FULL)


@pytest.fixture(scope="module")
def module_served(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[TestClient, WorkspaceStore, str]]:
    path = Path(tmp_path_factory.mktemp("type-routes")) / "w.db"
    with closing(WorkspaceStore(path)) as store:
        guid = _sample(store)
        app.state.store = store
        app.state.owns_store = False
        with TestClient(app, base_url="http://127.0.0.1") as client:
            yield client, store, guid


@pytest.fixture
def served(
    module_served: tuple[TestClient, WorkspaceStore, str],
) -> tuple[TestClient, WorkspaceStore, str]:
    """The module's workspace, made the app's again for each test: a test
    between two that use it may have pointed the app at its own."""
    client, store, guid = module_served
    app.state.store = store
    app.state.owns_store = False
    return module_served


def _plant_head(store: WorkspaceStore, guid: str, data: dict[str, Any]) -> int:
    connection = store._connection
    (revision,) = connection.execute("SELECT revision FROM gpos WHERE guid=?", (guid,)).fetchone()
    data = dict(data)
    if data.get("guid") == CLEAN["guid"]:
        data["guid"] = guid  # the row's GPO, unless the guid itself is planted
    data["revision"] = revision + 1
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True)
    connection.execute(
        "UPDATE gpos SET snapshot_json=?, revision=? WHERE guid=?", (payload, revision + 1, guid)
    )
    connection.execute(
        "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
        (guid, revision + 1, "fuzz", "plant", "2026-10-18T00:00:00+00:00", payload),
    )
    connection.commit()
    return revision + 1


@pytest.mark.parametrize("label", ROUTED)
def test_no_gpo_route_answers_500_for_any_stored_json_type(served: Any, label: str) -> None:
    client, store, guid = served
    path, _annotation = FIELDS[label]
    scripts = {"computer": {"startup": [{"command": "setup.cmd"}]}}
    for kind, _gpo in _distinct_plants(label):
        revision = _plant_head(store, guid, _planted(CLEAN, path, JSON_VALUES[kind]))
        for method, route, body in (
            ("GET", f"/api/gpos/{guid}", None),
            ("GET", "/api/gpos", None),
            ("GET", f"/api/gpos/{guid}/plan.ps1", None),
            ("GET", f"/api/gpos/{guid}/export.zip", None),
            ("GET", f"/api/gpos/{guid}/gpmc-backup", None),
            ("POST", f"/api/gpos/{guid}/gpmc-backup-with-scripts/preview", scripts),
            ("GET", f"/api/gpos/{guid}/report.txt", None),
            ("GET", f"/api/gpos/{guid}/publication-plan", None),
            ("GET", f"/api/gpos/{guid}/diff?against_revision=1", None),
            (
                "GET",
                f"/api/gpos/{guid}/revisions/diff?from_revision=1&to_revision={revision}",
                None,
            ),
            ("POST", "/api/diff", {"baseline": guid, "draft": guid, "observed": guid}),
        ):
            response = client.request(method, route, json=body)
            assert response.status_code != 500, (label, kind, route, response.text[:200])
        # A mutation: committed only if it answers, and consistently (item 3).
        patched = client.patch(
            f"/api/gpos/{guid}",
            json={
                "actor": "fuzz",
                "reason": "rename",
                "expected_revision": revision,
                "name": f"Typed {label} {kind}",
            },
        )
        # A snapshot whose guid is not its row's is refused as a conflict.
        allowed = (200, 422, 409) if label == "GPO.guid" else (200, 422)
        assert patched.status_code in allowed, (label, kind, patched.text[:200])
        now = store.get_gpo(guid).revision
        assert now == (revision + 1 if patched.status_code == 200 else revision), (label, kind)


@pytest.mark.parametrize(
    ("label", "plan_text"),
    [
        ("GPO.computer_enabled", "GpoStatus"),
        ("GPO.user_enabled", "GpoStatus"),
        ("GPOLink.enabled", "-LinkEnabled"),
        ("GPOLink.enforced", "-Enforced"),
    ],
)
def test_a_stored_boolean_is_exported_exactly_or_refused(label: str, plan_text: str) -> None:
    """Sol final-r18 B3: "false" was exported as true. A boolean is exported as
    stored; anything else refuses the export."""
    from gpo_studio.egress import checked_plan
    from gpo_studio.model import ValidationError

    path, _annotation = FIELDS[label]
    plans = {}
    for stored in (True, False):
        gpo = gpo_from_dict(_planted(CLEAN, path, stored))
        # The sample's CSE metadata blocks only the GPMC backup; the plan builds.
        plans[stored] = checked_plan(gpo)
        assert plan_text in plans[stored]
    assert plans[True] != plans[False], label
    for kind, value in JSON_VALUES.items():
        if isinstance(value, bool):
            continue
        gpo = gpo_from_dict(_planted(CLEAN, path, value))
        with pytest.raises(ValidationError):
            checked_plan(gpo)
        for string in ("false", "true", "0", "1", ""):
            with pytest.raises(ValidationError):
                checked_plan(gpo_from_dict(_planted(CLEAN, path, string)))
        del kind


def test_an_estate_import_refuses_a_value_of_the_wrong_type(tmp_path: Path) -> None:
    """The way in Sol and DeepSeek found: "false" for a boolean, an array for a
    closed-set string. Refused (422), nothing stored."""
    with closing(WorkspaceStore(tmp_path / "estate.db")) as store:
        app.state.store = store
        app.state.owns_store = False
        client = TestClient(app, base_url="http://127.0.0.1")
        for change in (
            {"computer_enabled": "false"},
            {"links": [{"id": "l", "target": "OU=A,DC=studio,DC=local", "enabled": "false"}]},
            {"security_filters": [{"id": "f", "principal": "A", "permission": ["apply"]}]},
            {
                "settings": [
                    {
                        "id": "s",
                        "side": ["computer"],
                        "hive": "HKLM",
                        "key": "K",
                        "value_name": "V",
                        "registry_type": "REG_SZ",
                        "value": "x",
                    }
                ]
            },
        ):
            gpo = {
                "guid": "11111111-2222-3333-4444-555555555557",
                "display_name": "Typed",
                "domain": "studio.local",
                "settings": [],
                "links": [],
                "security_filters": [],
                "wmi_filter": None,
                **change,
            }
            response = client.post(
                "/api/estate/import",
                json={
                    "kind": "gpo-lens-estate",
                    "domain": "studio.local",
                    "exported_at": "2026-07-12T10:00:00Z",
                    "gpos": [gpo],
                },
            )
            assert response.status_code == 422, (change, response.status_code, response.text)
            assert store.list_gpos() == []


#: One of every kind of bad stored value: a value of the wrong type (a
#: boolean as a string, an array as a string), a closed-set value outside its
#: set, an unknown hive, retained content a current rule refuses, an
#: unreadable import record, a missing required field.
BAD_KINDS: dict[str, tuple[str, Any]] = {
    "wrong-type boolean": ("GPO.computer_enabled", "false"),
    "wrong-type string": ("GPOLink.target", ["OU=A"]),
    "closed set": ("SecurityFilter.permission", "own"),
    "unknown hive": ("GppRegistry.hive", "HKEY_NOWHERE"),
    "refused content": ("GppGroup.unknown_attrs", [["name", "collides"]]),
    "unreadable record": ("GPO.fdeploy", {"raw_text": 7}),
    "missing required field": ("WmiFilter.id", None),
}

_MUTATIONS: list[tuple[str, str, dict[str, Any]]] = [
    ("PATCH", "", {"name": "Bad (renamed)"}),
    (
        "POST",
        "/settings",
        {
            "setting": {
                "side": "computer",
                "hive": "HKLM",
                "key": r"Software\Policies\R18",
                "value_name": "V",
                "registry_type": "REG_DWORD",
                "value": "1",
            }
        },
    ),
    ("POST", "/links", {"link": {"target": "OU=R18,DC=studio,DC=local"}}),
    ("POST", "/security-filters", {"filter": {"principal": "STUDIO\\R18", "sid": "S-1-5-32-545"}}),
    (
        "PUT",
        "/wmi-filter",
        {"wmi_filter": {"name": "R18", "query": "SELECT * FROM Win32_OperatingSystem"}},
    ),
    (
        "POST",
        "/preferences/groups",
        {"scope": "computer", "group": {"name": "R18", "sid": "S-1-5-32-545"}},
    ),
]


def _all_bad(guid: str) -> dict[str, Any]:
    data = copy.deepcopy(CLEAN)
    data["guid"] = guid
    for label, value in BAD_KINDS.values():
        path, _annotation = FIELDS[label]
        target = data
        for step in path[:-1]:
            target = target[step]
        if value is None:
            target.pop(path[-1])  # missing, not null
        else:
            target[path[-1]] = copy.deepcopy(value)
    return data


@pytest.mark.parametrize("kind", [*sorted(BAD_KINDS), "all at once"])
def test_a_mutation_over_bad_stored_values_answers_and_commits_consistently(
    served: Any, kind: str
) -> None:
    """Item 3 of the final-r18 ruling: a mutation never commits and then fails
    building its answer. Over every kind of bad stored value, and all of them
    together, each GPO-payload mutation answers (never 500), and the GPO's
    revision moves by one exactly when it answered 2xx."""
    client, store, guid = served
    if kind == "all at once":
        data = _all_bad(guid)
    else:
        label, value = BAD_KINDS[kind]
        path, _annotation = FIELDS[label]
        data = _planted(CLEAN, path, value)
        if value is None:
            target = data
            for step in path[:-1]:
                target = target[step]
            target.pop(path[-1])
    _plant_head(store, guid, data)
    loaded = store.get_gpo(guid)
    assert any(issue.severity == "error" for issue in validate_gpo(loaded)), kind
    # A value the read replaced (by the default) must not be stored over: a
    # change would turn the stored "false" into the default true and unblock
    # the GPO. Every change but a whole-revision restore is refused.
    replaced = bool(_replaced_on_read(loaded))
    assert replaced == (kind not in {"closed set", "unknown hive", "refused content"}), kind
    for method, suffix, body in _MUTATIONS:
        before = store.get_gpo(guid).revision
        response = client.request(
            method,
            f"/api/gpos/{guid}{suffix}",
            json={"actor": "t", "reason": "r18", "expected_revision": before, **body},
        )
        assert response.status_code in (200, 201, 422), (kind, suffix, response.text[:300])
        after = store.get_gpo(guid).revision
        if response.status_code == 422:
            assert after == before, (kind, suffix)
            if replaced:
                codes = {i["code"] for i in response.json()["error"]["issues"]}
                assert "stored_value_wrong_type" in codes, (kind, suffix, codes)
        else:
            assert not replaced, (kind, suffix)
            assert after == before + 1, (kind, suffix)
            assert response.json()["gpo"]["revision"] == after, (kind, suffix)
            # The answer reports the GPO it committed: still blocked.
            capabilities = response.json()["artifact_capabilities"]
            assert capabilities["studio_export"]["enabled"] is False, (kind, suffix)
    if replaced:
        fork = client.post(
            f"/api/gpos/{guid}/fork", json={"actor": "t", "reason": "r", "name": "F"}
        )
        assert fork.status_code == 422, (kind, fork.text[:200])
        # The way out: restore a revision without the replaced value.
        current = store.get_gpo(guid).revision
        restored = client.post(
            f"/api/gpos/{guid}/revisions/1/restore",
            json={"actor": "t", "reason": "r", "expected_revision": current},
        )
        assert restored.status_code == 200, (kind, restored.text[:300])
        assert not _replaced_on_read(store.get_gpo(guid)), kind
