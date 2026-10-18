"""Stored values that reached a writer, legacy shapes, empty changes (final-r19).

* DeepSeek final-r19 B1: a closed-set value outside its set in a preference
  item that also carries a retained native record reached the writer's
  ``assert_never`` through the digests -- the detail and the report answered
  500, and a mutation committed before its answer failed. A collection holding
  such a value now has its records marked at load (read raw, never written),
  a GPO with a validation error has every record read raw by its digests and
  report, and every mutation builds its answer before it commits.
* DeepSeek final-r19 B2: the typed load exempted the legacy shapes the
  loaders still read (a bare array of targeting predicates, a printer's
  ``use_local``, a service's pre-WI-081 delays, a multi-value registry item),
  so they still raised or were coerced -- ``use_local: "false"`` exported
  ``skipLocal="1"``. Each is rewritten into the current shape and type-checked
  by the typed load; a test holds every key a loader reads to the model's
  fields and the rewrites' legacy keys.
* Sol final-r19 B1: an empty ``copy-settings`` (or an empty bulk change) went
  round the expected-revision check and the defaulted-value refusal.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import inspect
import json
import os
import typing
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from writer_corpus import synthetic_collections

from gpo_studio import api, backup_inventory, fdeploy, gpp, store
from gpo_studio.api import app
from gpo_studio.closed_sets import _walk
from gpo_studio.diff import diff_gpos
from gpo_studio.gpp import gpp_collection_to_dict
from gpo_studio.gpp_adapters import ADAPTER_KEYS
from gpo_studio.model import GPO, ConflictError, RegistrySetting, ValidationError
from gpo_studio.report import policy_report
from gpo_studio.store import WorkspaceStore, _replaced_on_read, gpo_from_dict
from gpo_studio.stored_types import LEGACY_KEYS, _hints
from gpo_studio.validation import validate_gpo

FAMILIES = ("groups", "registry", *ADAPTER_KEYS)
BOGUS = "BOGUS"
AUDIT = {"actor": "t", "reason": "r19"}


def _with_native_records() -> tuple[gpp.GppCollection, ...]:
    """The writer corpus with every item carrying its own rendering as its
    retained native record -- what an import of Studio's own export holds."""
    out = []
    for collection in synthetic_collections():
        changes = {}
        for key in FAMILIES:
            render = gpp._item_renderer(key, collection.scope)
            changes[key] = tuple(
                replace(item, native_xml=ET.tostring(render(item), encoding="unicode"))
                for item in getattr(collection, key)
            )
        out.append(replace(collection, **changes))
    return tuple(out)


def _stored(store_: WorkspaceStore, guid: str) -> dict[str, Any]:
    (payload,) = store_._connection.execute(
        "SELECT snapshot_json FROM gpos WHERE guid=?", (guid,)
    ).fetchone()
    return dict(json.loads(payload))


def _plant_head(store_: WorkspaceStore, guid: str, data: dict[str, Any]) -> int:
    connection = store_._connection
    (revision,) = connection.execute("SELECT revision FROM gpos WHERE guid=?", (guid,)).fetchone()
    data = {**data, "guid": guid, "revision": revision + 1}
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True)
    connection.execute(
        "UPDATE gpos SET snapshot_json=?, revision=? WHERE guid=?", (payload, revision + 1, guid)
    )
    connection.execute(
        "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
        (guid, revision + 1, "t", "plant", "2026-10-18T00:00:00+00:00", payload),
    )
    connection.commit()
    return revision + 1


#: The exhaustive form (CI's ``type-fuzz-full`` job): each plant in the whole
#: corpus. The default cuts the GPO down to the planted item's collection and
#: family (`test_stored_type_fuzz._slim`), which is what the plant reaches.
FULL = os.environ.get("GPO_STUDIO_FULL_FUZZ") == "1"


def _cut(data: dict[str, Any], path: tuple[Any, ...]) -> tuple[dict[str, Any], tuple[Any, ...]]:
    """*data* cut to the collection and family *path* (into its preferences)
    runs through, and the path into the cut copy."""
    if FULL:
        return copy.deepcopy(data), path
    out = copy.deepcopy(data)
    collection = out["gpp_collections"][path[1]]
    for key in FAMILIES:
        if key != path[2]:
            collection[key] = []
    out["gpp_collections"] = [collection]
    return out, (path[0], 0, *path[2:])


def _collection(gpo: GPO, full_path: tuple[Any, ...]) -> gpp.GppCollection:
    """The planted collection of a GPO `_load` read."""
    return gpo.gpp_collections[full_path[1] if FULL else 0]


def _set(data: Any, path: tuple[Any, ...], value: Any) -> None:
    for step in path[:-1]:
        data = data[step]
    data[path[-1]] = copy.deepcopy(value)


@pytest.fixture(scope="module")
def native_workspace(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[WorkspaceStore, str, dict[str, Any]]]:
    path = Path(tmp_path_factory.mktemp("native")) / "native.db"
    with closing(WorkspaceStore(path)) as store_:
        gpo = store_.create_gpo(
            "Native records", identity="t", reason="t", gpp_collections=_with_native_records()
        )
        assert not any(c.load_problems for c in store_.get_gpo(gpo.guid).gpp_collections)
        yield store_, gpo.guid, _stored(store_, gpo.guid)


@pytest.fixture
def native(
    native_workspace: tuple[WorkspaceStore, str, dict[str, Any]],
) -> tuple[WorkspaceStore, str, dict[str, Any]]:
    """The module's workspace (each test plants its own head from the clean
    snapshot), made the app's for this test."""
    app.state.store = native_workspace[0]
    app.state.owns_store = False
    return native_workspace


# ---------------------------------------------------------------------------
# DeepSeek final-r19 B1
# ---------------------------------------------------------------------------


def _closed_paths(clean: GPO) -> dict[str, tuple[Any, ...]]:
    """One stored path per (class, field) of every closed-set field of every
    preference item, by the walker validation uses."""
    out: dict[str, tuple[Any, ...]] = {}
    for index, collection in enumerate(clean.gpp_collections):
        for path, _value, _closed in _walk(collection, ""):
            steps: tuple[Any, ...] = tuple(
                int(step) if step.isdigit() else step for step in path.strip("/").split("/")
            )
            if len(steps) < 3 or steps[0] not in FAMILIES:
                continue  # the collection's own scope: a different shape
            owner: Any = collection
            for step in steps[:-1]:
                owner = owner[step] if isinstance(step, int) else getattr(owner, step)
            label = f"{type(owner).__name__}.{steps[-1]}"
            out.setdefault(label, ("gpp_collections", index, *steps))
    return out


def test_a_closed_set_value_beside_a_retained_record_answers_on_every_route(native: Any) -> None:
    """DeepSeek's repro: a drive's action ``BOGUS`` beside its native record."""
    store_, guid, clean = native
    data = copy.deepcopy(clean)
    index = next(i for i, c in enumerate(data["gpp_collections"]) if c["drives"])
    data["gpp_collections"][index]["drives"][0]["action"] = BOGUS
    revision = _plant_head(store_, guid, data)
    client = TestClient(app, base_url="http://127.0.0.1")
    detail = client.get(f"/api/gpos/{guid}")
    assert detail.status_code == 200, detail.text[:300]
    codes = {issue["code"] for issue in detail.json()["validation"]}
    assert "value_outside_closed_set" in codes
    assert detail.json()["artifact_capabilities"]["studio_export"]["enabled"] is False
    assert client.get(f"/api/gpos/{guid}/report.txt").status_code == 200
    for route in ("plan.ps1", "export.zip", "gpmc-backup", "publication-plan"):
        assert client.get(f"/api/gpos/{guid}/{route}").status_code == 422, route
    for route in (
        f"/api/gpos/{guid}/diff?against_revision=1",
        f"/api/gpos/{guid}/revisions/diff?from_revision=1&to_revision={revision}",
    ):
        assert client.get(route).status_code == 200, route
    patched = client.patch(
        f"/api/gpos/{guid}", json={**AUDIT, "expected_revision": revision, "name": "Renamed"}
    )
    assert patched.status_code == 200, patched.text[:300]
    assert patched.json()["gpo"]["revision"] == store_.get_gpo(guid).revision == revision + 1
    # Still kept as stored, still blocked, still answering.
    assert BOGUS in json.dumps(_stored(store_, guid))
    assert client.get(f"/api/gpos/{guid}").status_code == 200


def test_no_closed_set_value_beside_a_retained_record_reaches_a_writer(native: Any) -> None:
    """Every closed-set field of every preference item, each item carrying its
    retained record: the load, validation, payload, report, digests, a diff
    against the clean GPO and a committed edit all answer."""
    store_, guid, clean = native
    paths = _closed_paths(gpo_from_dict(clean))
    assert len(paths) >= 20, sorted(paths)
    for label, full_path in sorted(paths.items()):
        data, path = _cut(clean, full_path)
        base = gpo_from_dict(data)
        _set(data, path, BOGUS)
        gpo = gpo_from_dict(data)
        codes = {issue.code for issue in validate_gpo(gpo)}
        assert "value_outside_closed_set" in codes, label
        api._build_gpo_payload(gpo)
        policy_report(gpo)
        diff_gpos(base, gpo)
        revision = _plant_head(store_, guid, data)
        edited = store_.update_metadata(
            guid, revision, {"description": label}, identity="t", reason="r19"
        )
        assert edited.revision == revision + 1, label
        assert store_.get_gpo(guid).description == label


def test_a_mutation_whose_answer_cannot_be_built_is_not_committed(
    native: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DeepSeek final-r19 N3: the answer is built before the commit."""
    store_, guid, _clean = native

    def broken(gpo: Any) -> dict[str, Any]:
        raise RuntimeError("an answer that cannot be built")

    monkeypatch.setattr(api, "_build_gpo_payload", broken)
    before = store_.get_gpo(guid).revision
    with pytest.raises(RuntimeError):
        store_.update_metadata(guid, before, {"name": "X"}, identity="t", reason="r19")
    assert store_.get_gpo(guid).revision == before
    assert len(store_.revisions(guid)) == before


def test_a_writer_dispatch_is_never_reached_by_a_stored_closed_set_value(native: Any) -> None:
    """The class, not the instance: the records of a collection holding a value
    outside its set are marked at load, so no hash or diff renders them, and
    `serialize_gpp` refuses the collection with a named refusal."""
    _store, _guid, clean = native
    data = copy.deepcopy(clean)
    index = next(i for i, c in enumerate(data["gpp_collections"]) if c["services"])
    data["gpp_collections"][index]["services"][0]["startup_type"] = BOGUS
    collection = gpo_from_dict(data).gpp_collections[index]
    assert all(
        isinstance(item.native_xml, gpp.RefusedRecord)
        for key in FAMILIES
        for item in getattr(collection, key)
    )
    with pytest.raises(gpp.GppError):
        gpp.serialize_gpp(collection)


# ---------------------------------------------------------------------------
# DeepSeek final-r19 B2: legacy shapes
# ---------------------------------------------------------------------------


def _item_path(clean: dict[str, Any], key: str) -> tuple[Any, ...]:
    index = next(i for i, c in enumerate(clean["gpp_collections"]) if c[key])
    return ("gpp_collections", index, key, 0)


def _load(clean: dict[str, Any], full_path: tuple[Any, ...], change: dict[str, Any]) -> GPO:
    data, path = _cut(clean, full_path)
    item = data
    for step in path:
        item = item[step]
    for key, value in change.items():
        if value is _ABSENT:
            item.pop(key, None)
        else:
            item[key] = copy.deepcopy(value)
    gpo = gpo_from_dict(data)
    validate_gpo(gpo)
    api._build_gpo_payload(gpo)
    return gpo


_ABSENT = object()


def _blocked_and_frozen(gpo: GPO) -> bool:
    """Replaced on read: blocked, and every edit refused."""
    errors = [i for i in validate_gpo(gpo) if i.severity == "error"]
    return bool(errors) and bool(_replaced_on_read(gpo))


@pytest.mark.parametrize(
    ("stored", "skip_local"),
    [(True, True), (False, False), ("false", None), ("true", None), (0, None), (None, None)],
)
def test_a_legacy_use_local_is_a_boolean_or_refused(
    native: Any, stored: Any, skip_local: bool | None
) -> None:
    """``use_local: "false"`` exported ``skipLocal="1"``: the opposite policy."""
    _store, _guid, clean = native
    path = _item_path(clean, "printers")
    gpo = _load(clean, path, {"use_local": stored, "skip_local": _ABSENT})
    printer = _collection(gpo, path).printers[0]
    if skip_local is None:
        assert _blocked_and_frozen(gpo), stored
        assert printer.skip_local is False  # the default, never the coercion
    else:
        assert printer.skip_local is skip_local
        assert not _replaced_on_read(gpo)
        render = gpp._item_renderer("printers", _collection(gpo, path).scope)
        written = ET.tostring(render(replace(printer, native_xml="")), encoding="unicode")
        assert f'skipLocal="{int(skip_local)}"' in written


@pytest.mark.parametrize(
    ("key", "stored", "field", "expected"),
    [
        ("reset_period_days", 2, "reset_fail_count_delay_seconds", 172800),
        ("reset_period_days", "abc", None, None),
        ("reset_period_days", -1, None, None),
        ("reset_period_days", True, None, None),
        ("restart_delay_minutes", 3, "restart_service_delay_milliseconds", 180_000),
        ("restart_delay_minutes", ["x"], None, None),
        ("restart_computer_delay_seconds", 5, "restart_computer_delay_milliseconds", 5000),
        ("restart_computer_delay_seconds", "5", None, None),
        ("restart_service_delay_raw", 900, "restart_service_delay_milliseconds", 900),
        ("restart_service_delay_raw", "900", None, None),
        ("recovery_command", "fix.cmd", "program", "fix.cmd"),
        ("recovery_command", 7, None, None),
        ("append_arguments", "yes", "append_failure_count", True),
        ("append_arguments", "false", "append_failure_count", False),
        ("append_arguments", 1, None, None),
    ],
)
def test_a_legacy_service_key_is_read_by_its_type_or_refused(
    native: Any, key: str, stored: Any, field: str | None, expected: Any
) -> None:
    _store, _guid, clean = native
    path = _item_path(clean, "services")
    targets = {
        "reset_period_days": "reset_fail_count_delay_seconds",
        "restart_delay_minutes": "restart_service_delay_milliseconds",
        "restart_service_delay_raw": "restart_service_delay_milliseconds",
        "restart_computer_delay_seconds": "restart_computer_delay_milliseconds",
        "recovery_command": "program",
        "append_arguments": "append_failure_count",
    }
    gpo = _load(clean, path, {key: stored, targets[key]: _ABSENT})
    service = _collection(gpo, path).services[0]
    if field is None:
        assert _blocked_and_frozen(gpo), (key, stored)
    else:
        assert getattr(service, field) == expected
        assert not _replaced_on_read(gpo), (key, stored)


@pytest.mark.parametrize(
    ("predicates", "replaced"),
    [
        ([{"type": "ou", "negate": False, "value": "OU=A,DC=studio,DC=local"}], False),
        ([{"type": "ou", "negate": True}], False),  # value: optional, ""
        ([{"negate": False, "value": "OU=A"}], True),  # type: required
        ([{"type": "ou", "negate": 1, "value": "OU=A"}], True),
        ([{"type": "ou", "negate": "false", "value": "OU=A"}], True),
        ([{"type": "ou", "negate": False, "value": ["OU=A"]}], True),
        ([{"type": ["ou"], "negate": False, "value": "OU=A"}], True),
        (["not an object"], False),  # a raw predicate string: checked as one
        ({"predicates": [{"type": "ou", "negate": True, "value": "OU=A"}]}, False),
        ({"predicates": "x"}, True),
        ({"predicates": [], "unknown_predicates": {"x": 1}}, True),
    ],
)
def test_a_legacy_targeting_shape_is_type_checked(
    native: Any, predicates: Any, replaced: bool
) -> None:
    """The bare-array and ``predicates`` shapes: a missing key was a KeyError
    500; ``negate: "false"``/``1`` was silently read and re-saved."""
    _store, _guid, clean = native
    path = _item_path(clean, "groups")
    gpo = _load(clean, path, {"ilt_filter": predicates})
    assert bool(_replaced_on_read(gpo)) is replaced, predicates
    if replaced:
        assert _blocked_and_frozen(gpo)
    group = _collection(gpo, path).groups[0]
    first_stored = predicates[0] if isinstance(predicates, list) and predicates else None
    if isinstance(first_stored, dict) and first_stored.get("negate") is True:
        assert group.ilt_filter is not None
        first = group.ilt_filter.items[0]
        assert not isinstance(first, str) and first.negate is True


@pytest.mark.parametrize(
    ("values", "replaced"),
    [
        ([{"name": "V", "value": "1", "registry_type": "REG_SZ"}], False),
        ([], False),
        ("x", True),
        ([7], True),
        ([{"name": ["V"], "value": "1"}], True),
        ([{"name": "V", "value": "1", "default": "false"}], True),
    ],
)
def test_a_legacy_multi_value_registry_item_is_type_checked(
    native: Any, values: Any, replaced: bool
) -> None:
    _store, _guid, clean = native
    path = _item_path(clean, "registry")
    gpo = _load(clean, path, {"value": _ABSENT, "values": values})
    assert bool(_replaced_on_read(gpo)) is replaced, values


#: Each legacy key the rewrites read, with an item of its class to plant in.
LEGACY_PLANTS: dict[tuple[str, str], tuple[str, str | None]] = {
    ("GppPrinter", "use_local"): ("printers", "skip_local"),
    **{("GppService", key): ("services", None) for key in LEGACY_KEYS["GppService"]},
}
JSON_VALUES: dict[str, Any] = {
    "null": None,
    "boolean": True,
    "number": 7,
    "negative": -1,
    "huge": 2**63,
    "string": "false",
    "array": ["x"],
    "object": {"x": 1},
}


def test_every_legacy_key_is_fuzzed() -> None:
    planted = {key for (cls, key) in LEGACY_PLANTS}
    assert planted == LEGACY_KEYS["GppPrinter"] | LEGACY_KEYS["GppService"]


@pytest.mark.parametrize(("cls", "key"), sorted(LEGACY_PLANTS))
def test_every_legacy_key_holds_every_json_type(native: Any, cls: str, key: str) -> None:
    """DeepSeek final-r19 N2: the fuzz over the legacy keys. The load,
    validation and payload never raise; a value the read replaced blocks the
    GPO and every edit."""
    _store, _guid, clean = native
    family, _field = LEGACY_PLANTS[(cls, key)]
    path = _item_path(clean, family)
    for kind, value in JSON_VALUES.items():
        gpo = _load(clean, path, {**dict.fromkeys(_TARGETS, _ABSENT), key: value})
        if _replaced_on_read(gpo):
            assert _blocked_and_frozen(gpo), (cls, key, kind)


_TARGETS = frozenset({
    "skip_local",
    "program",
    "reset_fail_count_delay_seconds",
    "restart_service_delay_milliseconds",
    "restart_computer_delay_milliseconds",
    "append_failure_count",
})


def test_an_out_of_range_document_position_is_read_not_refused(native: Any) -> None:
    """DeepSeek final-r19 N2: ``-1`` answered 422 on the detail read."""
    store_, guid, clean = native
    path = _item_path(clean, "drives")
    for position in (-1, 2**63):
        gpo = _load(clean, path, {"document_position": position})
        assert _blocked_and_frozen(gpo), position
    data = copy.deepcopy(clean)
    _set(data, (*path, "document_position"), -1)
    _plant_head(store_, guid, data)
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get(f"/api/gpos/{guid}").status_code == 200
    assert client.get(f"/api/gpos/{guid}/report.txt").status_code == 200


def test_an_unreadable_collection_is_read_empty_and_blocks_edits(native: Any) -> None:
    """A named loader refusal no tolerance covers: the collection reads empty,
    recorded as replaced; never fatal on read, never re-saved empty."""
    store_, guid, clean = native
    data = copy.deepcopy(clean)
    data["gpp_collections"][0]["root_unknown_positions"] = "not a list"
    gpo = gpo_from_dict(data)
    assert _blocked_and_frozen(gpo)
    revision = _plant_head(store_, guid, data)
    with pytest.raises(ValidationError):
        store_.update_metadata(guid, revision, {"name": "X"}, identity="t", reason="r19")


# ---------------------------------------------------------------------------
# Every key a loader reads is one the typed load checks
# ---------------------------------------------------------------------------

#: The stored-data loaders, by module. Every ``*_from_dict`` of these modules
#: must be listed (checked below), so a new loader cannot escape the check.
LOADERS: dict[Any, tuple[str, ...]] = {
    store: (
        "_setting",
        "_link",
        "_cse_file_entry",
        "_cse_metadata_entry",
        "_security_filter",
        "_wmi_filter",
        "gpo_from_dict",
    ),
    gpp: (
        "_os_criteria_from_dict",
        "_parse_ilt_filter_from_dict",
        "_common_options_from_dict",
        "_gpp_registry_value_from_dict",
        "gpp_collection_from_dict",
        "gpp_collection_from_dict_tolerant",
        "_adapter_item_from_dict",
        "_adapters_from_dict",
        "_root_unknown_positions_from_dict",
        "_native_xml_from_dict",
        "_position_from_dict",
        "_provenance_from_dict",
    ),
    fdeploy: ("fdeploy_from_dict",),
    backup_inventory: ("inventory_from_dict",),
}


def _keys_read(function: Any) -> set[str]:
    """String keys *function* reads from a mapping: ``x["k"]``, ``x.get("k")``,
    ``x.pop("k")`` and ``"k" in x``."""
    source = inspect.getsource(function)
    tree = ast.parse("if 1:\n" + source if source.startswith(" ") else source)
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            keys.add(node.slice.value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("get", "pop")
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            keys.add(node.args[0].value)
        elif (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Constant)
            and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
        ):
            keys.add(node.left.value)
    return {key for key in keys if isinstance(key, str) and key.isidentifier()}


def _model_fields() -> set[str]:
    seen: set[type] = set()

    def visit(cls: type) -> None:
        if cls in seen:
            return
        seen.add(cls)
        for _name, annotation, _default in _hints(cls):
            for arg in _annotation_parts(annotation):
                if dataclasses.is_dataclass(arg) and isinstance(arg, type):
                    visit(arg)

    visit(GPO)
    return {name for cls in seen for name, _a, _d in _hints(cls)}


def _annotation_parts(annotation: Any) -> Iterator[Any]:
    yield annotation
    for arg in typing.get_args(annotation):
        yield from _annotation_parts(arg)


def test_every_key_a_loader_reads_is_checked_by_the_typed_load() -> None:
    """A loader reads a key only if the typed load checks it: a field of the
    model (checked by its annotation) or a legacy key a rewrite reads
    (`stored_types.LEGACY_KEYS`, checked by the rewrite). A legacy shape added
    to a loader without a rewrite fails here."""
    allowed = _model_fields() | {key for keys in LEGACY_KEYS.values() for key in keys}
    unchecked = {
        f"{module.__name__}.{name}": sorted(_keys_read(getattr(module, name)) - allowed)
        for module, names in LOADERS.items()
        for name in names
    }
    assert {where: keys for where, keys in unchecked.items() if keys} == {}


def test_every_loader_is_listed() -> None:
    for module, names in LOADERS.items():
        loaders = {
            name
            for name, value in vars(module).items()
            if inspect.isfunction(value)
            and value.__module__ == module.__name__
            and name.endswith("_from_dict")
            and not name.endswith("_to_dict")
        }
        assert loaders <= set(names), (module.__name__, sorted(loaders - set(names)))


def test_the_coverage_check_sees_a_legacy_key() -> None:
    """Control: a loader reading a key no field or rewrite names is caught."""

    def loader(data: dict[str, Any]) -> Any:
        return data.get("use_lokal"), data["skip_local"]

    allowed = _model_fields() | {key for keys in LEGACY_KEYS.values() for key in keys}
    assert _keys_read(loader) - allowed == {"use_lokal"}


# ---------------------------------------------------------------------------
# Sol final-r19 B1: an empty change is no way round the guards
# ---------------------------------------------------------------------------


@pytest.fixture
def copy_pair(tmp_path: Path) -> Iterator[tuple[TestClient, WorkspaceStore, str, str, str]]:
    with closing(WorkspaceStore(tmp_path / "copy.db")) as store_:
        target = store_.create_gpo("Target", identity="t", reason="t")
        empty = store_.create_gpo("Empty source", identity="t", reason="t")
        user_only = store_.create_gpo(
            "User-only source",
            identity="t",
            reason="t",
            settings=(
                RegistrySetting("u1", "user", "HKCU", r"Software\Policies\R19", "V", "REG_SZ", "x"),
            ),
        )
        app.state.store = store_
        app.state.owns_store = False
        yield (
            TestClient(app, base_url="http://127.0.0.1"),
            store_,
            target.guid,
            empty.guid,
            user_only.guid,
        )


def _copy(
    client: TestClient, target: str, source: str, revision: int, side: str | None = None
) -> Any:
    body: dict[str, Any] = {**AUDIT, "expected_revision": revision, "source_guid": source}
    if side is not None:
        body["side"] = side
    return client.post(f"/api/gpos/{target}/copy-settings", json=body)


def test_an_empty_copy_refuses_a_stale_revision(copy_pair: Any) -> None:
    client, store_, target, empty, user_only = copy_pair
    revision = store_.get_gpo(target).revision
    for source, side in ((empty, None), (user_only, "computer")):
        stale = _copy(client, target, source, revision + 5, side)
        assert stale.status_code == 409, stale.text[:200]
        assert store_.get_gpo(target).revision == revision
        fine = _copy(client, target, source, revision, side)
        assert fine.status_code == 200, fine.text[:200]
        assert store_.get_gpo(target).revision == revision  # nothing to change


def test_an_empty_copy_refuses_a_target_whose_read_replaced_a_value(copy_pair: Any) -> None:
    client, store_, target, empty, user_only = copy_pair
    data = _stored(store_, target)
    data["computer_enabled"] = "false"
    revision = _plant_head(store_, target, data)
    for source, side in ((empty, None), (user_only, "computer"), (user_only, None)):
        refused = _copy(client, target, source, revision, side)
        assert refused.status_code == 422, (side, refused.text[:200])
        codes = {issue["code"] for issue in refused.json()["error"]["issues"]}
        assert "stored_value_wrong_type" in codes
        assert store_.get_gpo(target).revision == revision


def test_an_empty_store_change_makes_the_mutation_checks(copy_pair: Any) -> None:
    """The store's empty paths themselves, whatever route calls them."""
    _client, store_, target, _empty, _user_only = copy_pair
    revision = store_.get_gpo(target).revision
    with pytest.raises(ConflictError):
        store_.put_settings(target, revision + 1, [], identity="t", reason="r19")
    with pytest.raises(ConflictError):
        store_.bulk_replace_settings_by_prefix(target, revision + 1, [], identity="t", reason="r")
    data = _stored(store_, target)
    data["user_enabled"] = ["no"]
    revision = _plant_head(store_, target, data)
    with pytest.raises(ValidationError):
        store_.put_settings(target, revision, [], identity="t", reason="r19")
    with pytest.raises(ValidationError):
        store_.bulk_replace_settings_by_prefix(target, revision, [], identity="t", reason="r")
    assert store_.get_gpo(target).revision == revision


def test_the_collection_dict_round_trip_still_holds_for_native_records() -> None:
    """Control for the fixture: the native-record corpus loads clean."""
    for collection in _with_native_records():
        loaded = gpp.gpp_collection_from_dict_tolerant(gpp_collection_to_dict(collection))
        assert loaded.load_problems == ()
        assert loaded == collection


# ---------------------------------------------------------------------------
# DeepSeek final-r19 N1: the digests' catch stays narrow, and that is safe
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raised", "status"), [(gpp.GppError("refused"), 200), (RuntimeError("defect"), 500)]
)
def test_a_writer_refusal_in_a_digest_is_read_raw_and_a_defect_is_a_500(
    native: Any, monkeypatch: pytest.MonkeyPatch, raised: Exception, status: int
) -> None:
    """A writer's named refusal while hashing a record is read as the raw
    record (`retained_rendering`); anything else is a defect and surfaces."""
    store_, guid, clean = native
    data, _path = _cut(clean, _item_path(clean, "drives"))
    _plant_head(store_, guid, data)

    def written_for(*_args: Any) -> Any:
        raise raised

    monkeypatch.setattr(gpp, "_written_for", written_for)
    monkeypatch.setattr(gpp, "_RETAINED_RENDERINGS", {})
    client = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    assert client.get(f"/api/gpos/{guid}").status_code == status
