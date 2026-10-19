"""Stored JSON, held to the model's types before the loaders read it (final-r18).

A GPO is stored and imported as JSON, and the loaders used to read it with
Python's coercions: ``bool("false")`` is ``True``, so an estate import whose
``computer_enabled`` was the string ``"false"`` was stored -- and exported --
as the opposite policy (Sol final-r18 B3); a list where a closed-set string
belongs reached ``value in frozenset`` and failed (DeepSeek final-r18 B1).

`check_stored` walks a stored dict alongside the dataclass it loads into, by
the annotations (the same derivation as `closed_sets`), and checks each
value's JSON type: a ``bool`` field holds ``true``/``false``, an ``int`` field
a number that is not a boolean, a ``str`` field a string, a tuple a list, a
nested item an object, a union any of its members. A value of the wrong type
is never coerced: it is recorded (``stored_value_wrong_type``, an error) and
replaced by the field's default, so the loader reads a value of the right
type, the GPO is blocked -- reported, readable, never exported -- and what
it would export can never be the opposite of what was stored. Closed-set
membership is `closed_sets`' check; this one is the type.
"""

from __future__ import annotations

import copy
import dataclasses
import types
import typing
from collections.abc import Callable
from functools import cache
from typing import Any, Literal, get_args, get_origin

#: A stored value of the wrong type: (path, what is there, what belongs).
Problem = tuple[str, str]

_MISSING = object()

# ---------------------------------------------------------------------------
# Legacy shapes (DeepSeek final-r19 B2)
# ---------------------------------------------------------------------------
#
# Every older stored shape a loader still accepts is rewritten here, into the
# current one, BEFORE the type check -- so the check, not the loader, judges
# every value, and the loaders read the current shape only. A legacy value of
# the wrong type is a problem like any other (never coerced: ``use_local:
# "false"`` once exported ``skipLocal="1"``); one the rewrite needs to scale
# (days to seconds) is scaled only when it is a number. `LEGACY_KEYS` names
# every key the rewrites read; a test holds every key a loader reads to the
# model's fields plus these.


def _flag_alias(old: str, new: str) -> Callable[[dict[str, Any], str, list[Problem]], None]:
    def rewrite(data: dict[str, Any], path: str, problems: list[Problem]) -> None:
        if new not in data and old in data:
            value = data.pop(old)
            if isinstance(value, bool):
                data[new] = value
            else:
                problems.append(
                    (f"{path}/{old}", f"{_json_name(value)} where a boolean belongs")
                )
        else:
            data.pop(old, None)

    return rewrite


def _moved(old: str, new: str) -> Callable[[dict[str, Any], str, list[Problem]], None]:
    def rewrite(data: dict[str, Any], path: str, problems: list[Problem]) -> None:
        if new not in data and old in data:
            data[new] = data.pop(old)  # the field's own check judges it
        else:
            data.pop(old, None)

    return rewrite


def _scaled(
    old: str, new: str, factor: int
) -> Callable[[dict[str, Any], str, list[Problem]], None]:
    def rewrite(data: dict[str, Any], path: str, problems: list[Problem]) -> None:
        if new not in data and old in data:
            value = data.pop(old)
            if isinstance(value, int) and not isinstance(value, bool):
                data[new] = value * factor  # the range is validation's, as before
            else:
                problems.append(
                    (f"{path}/{old}", f"{_json_name(value)} where a number belongs")
                )
        else:
            data.pop(old, None)

    return rewrite


def _append_arguments(data: dict[str, Any], path: str, problems: list[Problem]) -> None:
    # A string field before it was a boolean: its documented reading, for a
    # string only (anything else is a problem, not a truthiness guess).
    if "append_failure_count" not in data and "append_arguments" in data:
        value = data.pop("append_arguments")
        if isinstance(value, str):
            legacy = value.strip().lower()
            data["append_failure_count"] = legacy not in {"", "0", "false", "no"}
        elif isinstance(value, bool):
            data["append_failure_count"] = value
        else:
            problems.append(
                (f"{path}/append_arguments", f"{_json_name(value)} where a string belongs")
            )
    else:
        data.pop("append_arguments", None)


def _ilt_predicates(data: dict[str, Any], path: str, problems: list[Problem]) -> None:
    # ``{"predicates": [...], "unknown_predicates": [...]}``, before ``items``.
    if "items" in data:
        data.pop("predicates", None)
        data.pop("unknown_predicates", None)
        return
    if "predicates" not in data and "unknown_predicates" not in data:
        return
    items: list[Any] = []
    for key in ("predicates", "unknown_predicates"):
        value = data.pop(key, [])
        if isinstance(value, list):
            items.extend(value)
        else:
            problems.append((f"{path}/{key}", f"{_json_name(value)} where an array belongs"))
    data["items"] = items


_REGISTRY_VALUE_KEYS = (
    "name", "value", "registry_type", "action", "default", "id", "unknown_attrs"
)


def _legacy_registry(data: dict[str, Any], path: str, problems: list[Problem]) -> None:
    # A <Registry> item stored before the one-value model: ``values``, a list
    # of value objects each with its own targeting and element attributes,
    # expanded into one current item per value (the first keeps the item's
    # uid, id and, where the value has none, its targeting and retained
    # element content).
    entries = data.get("registry")
    if not isinstance(entries, list):
        return
    out: list[Any] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or "value" in entry:
            out.append(entry)
            continue
        where = f"{path}/registry/{index}"
        values = entry.get("values", [])
        if not isinstance(values, list):
            problems.append((f"{where}/values", f"{_json_name(values)} where an array belongs"))
            values = []
        for position, value in enumerate(values or [{}]):
            if not isinstance(value, dict):
                problems.append(
                    (f"{where}/values/{position}", f"{_json_name(value)} where an object belongs")
                )
                continue
            first = position == 0
            item: dict[str, Any] = {
                key: entry[key]
                for key in ("key", "hive", "action", "common", "document_position")
                if key in entry
            }
            if first:
                item.update({key: entry[key] for key in ("uid", "id") if key in entry})
            # The legacy loader read a value's name and data as optional ("").
            item["value"] = {
                "name": "",
                "value": "",
                **{key: value[key] for key in _REGISTRY_VALUE_KEYS if key in value},
            }
            for key, own in (
                ("ilt_filter", "ilt_filter"),
                ("unknown_attrs", "unknown_elem_attrs"),
                ("unknown_children", "unknown_children"),
                ("unknown_props_children", "unknown_props_children"),
            ):
                if value.get(own):
                    item[key] = value[own]
                elif first and key in entry:
                    item[key] = entry[key]
            # The promotions the legacy loader made, exactly (DeepSeek final-r19
            # B1 of round 20): a ``uid`` among the element's retained attributes
            # IS the item's uid, over the entry's, and a ``default`` among the
            # value's is its default flag -- each matched by local name ignoring
            # case, removed only where the local name is exactly lower case.
            uid = _promoted(item.get("unknown_attrs"), "uid")
            if uid is not None:
                item["uid"] = uid
                item["unknown_attrs"] = _without(item["unknown_attrs"], "uid")
            default = _promoted(item["value"].get("unknown_attrs"), "default")
            if default is not None:
                item["value"]["default"] = default == "1"
                item["value"]["unknown_attrs"] = _without(
                    item["value"]["unknown_attrs"], "default"
                )
            out.append(item)
    data["registry"] = out


def _attr_local_name(name: Any) -> str:
    text = str(name)
    return text.split("}", 1)[-1] if "}" in text else text


def _attr_pairs(attrs: Any) -> list[Any] | None:
    """*attrs* if it is a list of name/value pairs, else ``None`` (left for
    the type check to judge)."""
    if not isinstance(attrs, list) or not all(
        isinstance(pair, (list, tuple)) and len(pair) == 2 for pair in attrs
    ):
        return None
    return attrs


def _promoted(attrs: Any, name: str) -> str | None:
    pairs = _attr_pairs(attrs)
    for key, value in pairs or ():
        if _attr_local_name(key).lower() == name:
            # Promoted only as text: anything else stays for the type check.
            return value if isinstance(key, str) and isinstance(value, str) else None
    return None


def _without(attrs: Any, name: str) -> Any:
    pairs = _attr_pairs(attrs)
    if pairs is None:
        return attrs
    return [pair for pair in pairs if _attr_local_name(pair[0]) != name]


#: Rewrites of each item's legacy keys, by the class it loads into.
LEGACY_REWRITES: dict[str, tuple[Callable[[dict[str, Any], str, list[Problem]], None], ...]] = {
    # Stored before WI-081, when the field was ``use_local``.
    "GppPrinter": (_flag_alias("use_local", "skip_local"),),
    "GppService": (
        _moved("recovery_command", "program"),
        _scaled("reset_period_days", "reset_fail_count_delay_seconds", 86400),
        _moved("restart_service_delay_raw", "restart_service_delay_milliseconds"),
        _scaled("restart_delay_minutes", "restart_service_delay_milliseconds", 60_000),
        _scaled("restart_computer_delay_seconds", "restart_computer_delay_milliseconds", 1000),
        _append_arguments,
    ),
    "IltFilter": (_ilt_predicates,),
    "GppCollection": (_legacy_registry,),
}

#: Keys every stored object of a class has always carried, although the
#: model gives them a default: the loaders required them (a missing one was a
#: ``KeyError``), so a stored object without one is a problem, not a default
#: (DeepSeek final-r20 B1: no less strict than the loader it replaced).
STORED_REQUIRED: dict[str, frozenset[str]] = {
    "IltPredicate": frozenset({"negate", "value"}),
}

#: Every key a rewrite reads.
LEGACY_KEYS: dict[str, frozenset[str]] = {
    "GppPrinter": frozenset({"use_local"}),
    "GppService": frozenset({
        "recovery_command",
        "reset_period_days",
        "restart_service_delay_raw",
        "restart_delay_minutes",
        "restart_computer_delay_seconds",
        "append_arguments",
    }),
    "IltFilter": frozenset({"predicates", "unknown_predicates"}),
    # Of a legacy <Registry> item (``values``) and of each of its values.
    "GppRegistry": frozenset({"values"}),
    "GppRegistryValue": frozenset({
        "ilt_filter", "unknown_elem_attrs", "unknown_children", "unknown_props_children"
    }),
}

#: Items an early release stored as a bare array: ``IltFilter`` as its
#: predicates (``[{"type", "negate", "value"}, ...]``), read as ``items``.
LEGACY_ARRAYS = frozenset({"IltFilter"})

#: What the legacy loader read of each predicate of a bare array: these three
#: keys, nothing else (DeepSeek final-r20 B1: read exactly as it read them).
_LEGACY_PREDICATE_KEYS = ("type", "negate", "value")


def _legacy_ilt_array(value: list[Any], path: str, problems: list[Problem]) -> dict[str, Any]:
    items: list[Any] = []
    for index, entry in enumerate(value):
        if isinstance(entry, dict):
            items.append({key: entry[key] for key in _LEGACY_PREDICATE_KEYS if key in entry})
        else:
            # The legacy loader read every entry as a predicate object; a raw
            # predicate string is the current shape's, never this one's.
            problems.append((f"{path}/{index}", f"{_json_name(entry)} where an object belongs"))
    return {"items": items}


def _json_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "an array"
    if isinstance(value, dict):
        return "an object"
    return type(value).__name__


@cache
def _hints(cls: type) -> tuple[tuple[str, Any, Any], ...]:
    """(field, resolved annotation or None, default or _MISSING) per field."""
    import sys

    from .closed_sets import _namespace

    space = _namespace()
    space.update(vars(sys.modules[cls.__module__]))
    out: list[tuple[str, Any, Any]] = []
    for item in dataclasses.fields(cls):
        annotation: Any = item.type
        if isinstance(annotation, str):
            try:
                annotation = eval(annotation, space)  # noqa: S307 - our own annotations
            except Exception:
                annotation = None
        if item.default is not dataclasses.MISSING:
            default = item.default
        elif item.default_factory is not dataclasses.MISSING:
            default = item.default_factory()
        else:
            default = _MISSING
        out.append((item.name, annotation, default))
    return tuple(out)


def _expected(annotation: Any) -> str:
    origin = get_origin(annotation)
    if annotation is bool:
        return "a boolean"
    if annotation is int or annotation is float:
        return "a number"
    if annotation is str or origin is Literal:
        return "a string"
    if origin in (tuple, list):
        return "an array"
    if dataclasses.is_dataclass(annotation):
        return "an object"
    if origin in (typing.Union, types.UnionType):
        return " or ".join(_expected(arg) for arg in get_args(annotation))
    return "a value of another type"


def _check(annotation: Any, value: Any, path: str, problems: list[Problem]) -> Any:
    """*value* if it has the type *annotation* names (recursing into arrays and
    objects, which are repaired in place), else ``_MISSING`` with a problem."""
    if annotation is None or annotation is Any or annotation is object:
        return value
    origin = get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        for arg in get_args(annotation):
            trial: list[Problem] = []
            checked = _check(arg, value, path, trial)
            if checked is not _MISSING and not trial:
                return checked
        problems.append((path, f"{_json_name(value)} where {_expected(annotation)} belongs"))
        return _MISSING
    if annotation is type(None):
        ok = value is None
    elif annotation is bool:
        ok = isinstance(value, bool)
    elif annotation is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif annotation is float:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif annotation is str:
        ok = isinstance(value, str)
    elif annotation is bytes:
        ok = isinstance(value, (bytes, str))
    elif origin is Literal:
        ok = any(type(value) is type(member) for member in get_args(annotation))
    elif origin in (tuple, list):
        if not isinstance(value, (list, tuple)):
            ok = False
        else:
            args = get_args(annotation)
            items = list(value)
            homogeneous = (origin is list and len(args) == 1) or (
                len(args) == 2 and args[1] is Ellipsis
            )
            if homogeneous:
                kept = []
                for index, entry in enumerate(items):
                    checked = _check(args[0], entry, f"{path}/{index}", problems)
                    if checked is not _MISSING:
                        kept.append(checked)
                return kept
            if args and len(args) != len(items):
                ok = False
            else:
                out = []
                for index, (arg, entry) in enumerate(zip(args, items, strict=False)):
                    checked = _check(arg, entry, f"{path}/{index}", problems)
                    if checked is _MISSING:
                        return _MISSING
                    out.append(checked)
                return out if args else items
    elif dataclasses.is_dataclass(annotation) and isinstance(annotation, type):
        if isinstance(value, list) and annotation.__name__ in LEGACY_ARRAYS:
            value = _legacy_ilt_array(value, path, problems)  # then checked as current
        if not isinstance(value, dict):
            ok = False
        else:
            return check_stored(annotation, value, path, problems)
    elif origin is dict or annotation is dict:
        ok = isinstance(value, dict)
    else:
        return value  # a type this check does not judge
    if not ok:
        problems.append((path, f"{_json_name(value)} where {_expected(annotation)} belongs"))
        return _MISSING
    return value


def _zero(annotation: Any) -> Any:
    """A value of *annotation*'s type for a required field with no default."""
    origin = get_origin(annotation)
    if annotation is bool:
        return False
    if annotation is int or annotation is float:
        return 0
    if origin in (tuple, list):
        return []
    if origin is Literal:
        return get_args(annotation)[0]
    if origin in (typing.Union, types.UnionType):
        args = get_args(annotation)
        return None if type(None) in args else _zero(args[0])
    return ""


def check_stored(
    cls: type,
    data: dict[str, Any],
    path: str = "",
    problems: list[Problem] | None = None,
    *,
    skip: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """A copy of *data* with every field of the wrong JSON type replaced by its
    default, and each such field appended to *problems* (see the module doc).
    Keys that are not fields of *cls* are left as they are (legacy shapes the
    loaders still read); fields in *skip* are not checked here."""
    problems = problems if problems is not None else []
    out = dict(data)
    for rewrite in LEGACY_REWRITES.get(cls.__name__, ()):
        rewrite(out, path, problems)
    for name, annotation, default in _hints(cls):
        if name in skip:
            continue
        if name not in out:
            if default is _MISSING or name in STORED_REQUIRED.get(cls.__name__, ()):
                # A required field missing from a stored object: as wrong as a
                # value of the wrong type, and a loader would fail on it.
                what = f"nothing where {_expected(annotation)} belongs"
                problems.append((f"{path}/{name}", what))
                out[name] = _zero(annotation)
            continue
        checked = _check(annotation, out[name], f"{path}/{name}", problems)
        if checked is _MISSING:
            if default is _MISSING:
                out[name] = _zero(annotation)
            elif dataclasses.is_dataclass(default) and not isinstance(default, type):
                out[name] = dataclasses.asdict(default)
            else:
                out[name] = copy.deepcopy(default)
        else:
            out[name] = checked
    return out


def stored_flag(value: Any, default: bool) -> bool:
    """A stored boolean, never coerced by truthiness: anything but ``true`` or
    ``false`` is the default (`check_stored` has already reported it)."""
    return value if isinstance(value, bool) else default


#: How every problem `problem_message` words begins.
STORED_WRONG_TYPE = "stored value of the wrong type"


#: The recoveries a GPO whose read replaced a stored value has -- every one
#: that exists, and only those (Sol final-r20 B1). An edit is not one: it
#: would store the default in the value's place.
RECOVERIES = (
    "restore an earlier revision that does not hold it, replace the GPO from a "
    "fresh import of its source backup (the backup import with replace_guid and "
    "expected_revision), or delete the GPO (confirm=<guid>; every stored row is "
    "archived, docs/workspace-recovery.md)"
)


def problem_message(path: str, what: str) -> str:
    return (
        f"{STORED_WRONG_TYPE} at {path.lstrip('/') or 'the root'}: {what}. Studio read "
        "the default in its place, so it will neither export nor change the GPO; "
        f"{RECOVERIES}"
    )
