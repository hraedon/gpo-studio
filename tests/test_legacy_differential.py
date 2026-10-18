"""The legacy shapes read now exactly as the loader they replaced read them (final-r20).

DeepSeek final-r20 B1: rewriting the legacy shapes into the current one before
the type check (final-r19) changed what one of them meant -- a multi-value
registry item with a ``uid`` on the entry AND among its first value's element
attributes exported the value's uid at 58b80aa and was refused after. This
test is the class: the 58b80aa loader is the oracle. Its ``gpp`` and
``stored_types`` modules are vendored verbatim (`oracles.legacy_58b80aa`,
with their provenance) and run beside the current ones, and over generated
combinations of every legacy shape -- uids
at entry and value level, missing and extra keys, every legacy key of the
loader-key table -- it requires:

* where the input is well typed and the old loader accepted it (no load
  problem, a well-typed model, a written file), the current load is the same
  model and writes identical bytes;
* where the old loader refused it (a load problem, an exception, a refused
  write), the current one refuses it too: no less strict;
* where the input is not well typed, the current load refuses it -- the old
  loader's coercions (``bool("false")``, ``int("5")``, ``str(7)``) are what
  the typed load exists to end.

One difference is declared rather than matched: an ``os`` predicate in a bare
array (see `test_the_one_declared_difference_an_os_predicate_in_a_bare_array`).
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import itertools
import subprocess
from pathlib import Path
from typing import Any

import pytest
from oracles import legacy_58b80aa
from writer_corpus import synthetic_collections

from gpo_studio import gpp
from gpo_studio.gpp import GppCollection, gpp_collection_to_dict
from gpo_studio.stored_types import LEGACY_KEYS, check_stored

ROOT = Path(__file__).resolve().parents[1]


def _oracle() -> Any:
    """The vendored 58b80aa loader (`oracles.legacy_58b80aa`): no git needed."""
    return legacy_58b80aa.gpp()


def test_the_vendored_oracle_is_the_commit() -> None:
    """Each vendored file is the 58b80aa blob byte for byte -- checked against
    git where the commit is in the checkout (skipped where it is not: a
    shallow or squashed history; the recorded digest is checked on every
    load regardless)."""
    for name, (path, digest) in legacy_58b80aa.SOURCES.items():
        try:
            blob = subprocess.run(
                ["git", "show", f"{legacy_58b80aa.COMMIT}:{path}"],
                cwd=ROOT,
                capture_output=True,
                check=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError):
            pytest.skip(f"commit {legacy_58b80aa.COMMIT[:7]} is not in this checkout")
        assert hashlib.sha256(blob).hexdigest() == digest, path
        assert legacy_58b80aa.vendored_bytes(name) == blob, name


def _corpus_item(key: str) -> dict[str, Any]:
    collection = next(c for c in synthetic_collections() if getattr(c, key))
    return dict(gpp_collection_to_dict(collection)[key][0])


def _collection(**families: list[Any]) -> dict[str, Any]:
    data = gpp_collection_to_dict(GppCollection(scope="computer"))
    data.update(families)
    return data


def _read(module: Any, data: dict[str, Any]) -> tuple[Any, dict[str, bytes] | None, bool]:
    """(collection, written files, accepted) as *module* reads and writes
    *data*; an exception anywhere is a refusal."""
    try:
        collection = module.gpp_collection_from_dict_tolerant(copy.deepcopy(data))
    except Exception:  # noqa: BLE001 - the oracle's crash is its refusal
        return None, None, False
    if collection.load_problems:
        return collection, None, False
    try:
        written = module.serialize_gpp(collection)
    except Exception:  # noqa: BLE001
        return collection, None, False
    return collection, written, True


def _well_typed(collection: Any) -> bool:
    problems: list[Any] = []
    check_stored(
        GppCollection,
        dataclasses.asdict(collection),
        "",
        problems,
        skip=frozenset({"load_problems", "source_files"}),
    )
    return not problems


def _differential(data: dict[str, Any], *, well_typed: bool, label: str) -> None:
    old, old_bytes, old_ok = _read(_oracle(), data)
    new, new_bytes, new_ok = _read(gpp, data)
    if not well_typed:
        assert not new_ok, f"{label}: an ill-typed legacy value was accepted"
        return
    if old_ok and _well_typed(old):
        assert new_ok, f"{label}: the old loader accepted it; now {new.load_problems}"
        assert dataclasses.asdict(new) == dataclasses.asdict(old), label
        assert new_bytes == old_bytes, label
    elif not old_ok:
        assert not new_ok, f"{label}: the old loader refused it; now accepted"


def _sample(cases: list[Any]) -> list[Any]:
    """Every case: each takes milliseconds (the default suite runs them all)."""
    return cases


# ---------------------------------------------------------------------------
# Multi-value registry items (``values``)
# ---------------------------------------------------------------------------

UID_E = "{AAAAAAAA-0000-4000-8000-000000000001}"
UID_V = "{BBBBBBBB-0000-4000-8000-000000000002}"
ILT = {"items": [{"type": "ou", "negate": False, "value": "OU=A,DC=studio,DC=local"}]}


def _registry_cases() -> list[tuple[str, dict[str, Any], bool]]:
    cases = []
    dims = itertools.product(
        (None, UID_E),  # the entry's uid
        (None, [["x-entry", "1"]], [["uid", UID_E.lower()]]),  # its element attributes
        ("absent", "empty", "one", "two"),  # its values
        (None, [["uid", UID_V]], [["UID", UID_V]], [["x-value", "2"]]),  # value 0's
        (None, [["default", "1"]], [["default", "0"]]),  # value 0's own attributes
        (None, True),  # value 0's default
    )
    for index, (uid, attrs, values, elem, own, default) in enumerate(dims):
        entry: dict[str, Any] = {"key": r"Software\Legacy", "hive": "HKEY_LOCAL_MACHINE"}
        if uid is not None:
            entry["uid"] = uid
        if attrs is not None:
            entry["unknown_attrs"] = attrs
        if index % 2:
            entry["id"] = "r1"
        if index % 3 == 0:
            entry["ilt_filter"] = ILT
        value: dict[str, Any] = {"name": "V", "value": "1", "registry_type": "REG_SZ"}
        if index % 5 == 0:
            value = {}  # every key missing: the old loader's defaults
        if elem is not None:
            value["unknown_elem_attrs"] = elem
        if own is not None:
            value["unknown_attrs"] = own
        if default is not None:
            value["default"] = default
        if index % 7 == 0:
            value["ilt_filter"] = ILT
        second = {"name": "W", "value": "2", "registry_type": "REG_SZ"}
        if index % 4 == 1:
            second["unknown_elem_attrs"] = [["uid", UID_V.replace("B", "C")]]
        if values != "absent":
            entry["values"] = {"empty": [], "one": [value], "two": [value, second]}[values]
        cases.append((f"registry#{index}", _collection(registry=[entry]), True))
    entry = {"key": r"Software\Legacy", "values": [{"name": "V", "value": "1"}]}
    for name, bad in (
        ("values a string", {"values": "x"}),
        ("value not an object", {"values": [7]}),
        ("name an array", {"values": [{"name": ["V"], "value": "1"}]}),
        ("default a string", {"values": [{"name": "V", "value": "1", "default": "false"}]}),
        ("uid attribute a number", {"values": [{"name": "V", "unknown_elem_attrs": [["uid", 7]]}]}),
    ):
        cases.append((f"registry {name}", _collection(registry=[{**entry, **bad}]), False))
    return cases


@pytest.mark.parametrize(("label", "data", "well_typed"), _sample(_registry_cases()))
def test_a_legacy_registry_item_reads_as_the_old_loader_read_it(
    label: str, data: dict[str, Any], well_typed: bool
) -> None:
    _differential(data, well_typed=well_typed, label=label)


def test_deepseeks_entry_and_value_uid_case() -> None:
    """The reported case, named: the value's uid wins, as at 58b80aa."""
    data = _collection(
        registry=[
            {
                "key": r"SOFTWARE\Legacy\One",
                "uid": UID_E,
                "values": [
                    {
                        "name": "V",
                        "value": "1",
                        "registry_type": "REG_SZ",
                        "unknown_elem_attrs": [["uid", UID_V]],
                    }
                ],
            }
        ]
    )
    _differential(data, well_typed=True, label="entry and value uid")
    collection = gpp.gpp_collection_from_dict_tolerant(data)
    assert collection.load_problems == ()
    assert collection.registry[0].uid == UID_V
    assert UID_V.encode() in b"".join(gpp.serialize_gpp(collection).values())


# ---------------------------------------------------------------------------
# Targeting: a bare array, and ``predicates``/``unknown_predicates``
# ---------------------------------------------------------------------------

RAW = '<FilterOrgUnit bool="AND" not="0" name="OU=B,DC=studio,DC=local" userContext="1"/>'


def _predicates() -> list[dict[str, Any]]:
    out = []
    for type_, negate, value, bool_op, attrs in itertools.product(
        ("absent", "ou"),
        ("absent", False, True),
        ("absent", "OU=A,DC=studio,DC=local"),
        ("absent", "OR"),
        ("absent", [["x-attr", "1"]]),
    ):
        predicate: dict[str, Any] = {}
        for key, item in (
            ("type", type_),
            ("negate", negate),
            ("value", value),
            ("bool_op", bool_op),
            ("unknown_attrs", attrs),
        ):
            if item != "absent":
                predicate[key] = item
        out.append(predicate)
    return out


def _ilt_cases() -> list[tuple[str, dict[str, Any], bool]]:
    group = _corpus_item("groups")
    cases = []
    full = {"type": "ou", "negate": True, "value": "OU=A,DC=studio,DC=local"}
    for index, predicate in enumerate(_predicates()):
        for shape, ilt in (
            ("array", [predicate]),
            ("array of two", [predicate, full]),
            ("predicates", {"predicates": [predicate]}),
            ("predicates and raw", {"predicates": [predicate], "unknown_predicates": [RAW]}),
        ):
            data = _collection(groups=[{**group, "ilt_filter": ilt}])
            cases.append((f"ilt {shape} #{index}", data, True))
    for name, ilt in (
        ("negate a number", [{**full, "negate": 1}]),
        ("negate a string", [{**full, "negate": "false"}]),
        ("type an array", [{**full, "type": ["ou"]}]),
        ("value an array", [{**full, "value": ["OU=A"]}]),
        ("predicates a string", {"predicates": "x"}),
        ("raw an object", {"predicates": [full], "unknown_predicates": {"x": 1}}),
    ):
        cases.append((f"ilt {name}", _collection(groups=[{**group, "ilt_filter": ilt}]), False))
    return cases


@pytest.mark.parametrize(("label", "data", "well_typed"), _sample(_ilt_cases()))
def test_a_legacy_targeting_shape_reads_as_the_old_loader_read_it(
    label: str, data: dict[str, Any], well_typed: bool
) -> None:
    _differential(data, well_typed=well_typed, label=label)


# ---------------------------------------------------------------------------
# Every legacy key of the printer and service rewrites
# ---------------------------------------------------------------------------

#: Each legacy key: the key it became, well-typed values, ill-typed values.
SERVICE_KEYS: dict[str, tuple[str, tuple[Any, ...], tuple[Any, ...]]] = {
    "recovery_command": ("program", ("fix.cmd", ""), (7, None)),
    "reset_period_days": ("reset_fail_count_delay_seconds", (0, 2, -1), ("2", True, None)),
    "restart_service_delay_raw": ("restart_service_delay_milliseconds", (0, 900), ("900",)),
    "restart_delay_minutes": ("restart_service_delay_milliseconds", (0, 3), ("3", 1.5)),
    "restart_computer_delay_seconds": ("restart_computer_delay_milliseconds", (5,), (["5"],)),
    "append_arguments": ("append_failure_count", ("", "0", "false", "no", "yes", True), (1,)),
}
PRINTER_KEYS: dict[str, tuple[str, tuple[Any, ...], tuple[Any, ...]]] = {
    "use_local": ("skip_local", (True, False), ("false", 0, None)),
}


def test_every_legacy_adapter_key_is_generated() -> None:
    assert set(SERVICE_KEYS) == LEGACY_KEYS["GppService"]
    assert set(PRINTER_KEYS) == LEGACY_KEYS["GppPrinter"]


def _adapter_cases() -> list[tuple[str, dict[str, Any], bool]]:
    cases = []
    for family, keys in (("services", SERVICE_KEYS), ("printers", PRINTER_KEYS)):
        base = _corpus_item(family)
        targets = {target for target, _ok, _bad in keys.values()}
        for key, (_target, good, bad) in keys.items():
            for kept_target in (False, True):
                item = {k: v for k, v in base.items() if kept_target or k not in targets}
                for value in good:
                    data = _collection(**{family: [{**item, key: value}]})
                    cases.append((f"{family}.{key}={value!r} kept={kept_target}", data, True))
                for value in bad if not kept_target else ():
                    data = _collection(**{family: [{**item, key: value}]})
                    cases.append((f"{family}.{key}={value!r} (ill-typed)", data, False))
        both = {k: v for k, v in base.items() if k not in targets}
        if family == "services":
            for raw, minutes in itertools.product((None, 900), (None, 3)):
                item = dict(both)
                if raw is not None:
                    item["restart_service_delay_raw"] = raw
                if minutes is not None:
                    item["restart_delay_minutes"] = minutes
                data = _collection(services=[item])
                cases.append((f"services raw={raw} minutes={minutes}", data, True))
    return cases


@pytest.mark.parametrize(("label", "data", "well_typed"), _adapter_cases())
def test_a_legacy_adapter_key_reads_as_the_old_loader_read_it(
    label: str, data: dict[str, Any], well_typed: bool
) -> None:
    _differential(data, well_typed=well_typed, label=label)


def test_the_oracle_is_the_old_loader() -> None:
    """Control: the oracle reads a shape the current loader refuses differently
    -- a printer's ``use_local: "false"`` -- so it is not the current code."""
    printer = {k: v for k, v in _corpus_item("printers").items() if k != "skip_local"}
    data = _collection(printers=[{**printer, "use_local": "false"}])
    old, _bytes, old_ok = _read(_oracle(), data)
    assert old_ok and old.printers[0].skip_local == "false"  # coerced, and written
    new, _new_bytes, new_ok = _read(gpp, data)
    assert not new_ok and new.load_problems


def test_the_one_declared_difference_an_os_predicate_in_a_bare_array() -> None:
    """Declared, not matched: the old loader read an ``os`` predicate of a bare
    array without its criteria, which the writer then wrote as five "Any"
    fields -- the filter silently broadened (the WI-021 defect the current
    shape had been fixed for). It is read as its version criterion now."""
    group = _corpus_item("groups")
    os_predicate = {"type": "os", "negate": False, "value": "WIN10"}
    data = _collection(groups=[{**group, "ilt_filter": [os_predicate]}])
    old, _old_bytes, old_ok = _read(_oracle(), data)
    new, _new_bytes, new_ok = _read(gpp, data)
    assert old_ok and new_ok
    old_predicate = old.groups[0].ilt_filter.items[0]
    new_predicate = new.groups[0].ilt_filter.items[0]
    assert old_predicate.os_criteria is None
    assert new_predicate.os_criteria is not None and new_predicate.os_criteria.version == "WIN10"
