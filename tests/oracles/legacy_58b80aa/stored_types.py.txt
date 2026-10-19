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
from functools import cache
from typing import Any, Literal, get_args, get_origin

#: A stored value of the wrong type: (path, what is there, what belongs).
Problem = tuple[str, str]

_MISSING = object()

#: Items stored as an array by an early release, which their loader reads.
_LEGACY_ARRAYS = frozenset({"IltFilter"})


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
        if (
            isinstance(value, list)
            and annotation.__name__ in _LEGACY_ARRAYS
            and all(isinstance(entry, dict) for entry in value)
        ):
            return value  # a legacy array of predicate objects the loader still reads
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
    for name, annotation, default in _hints(cls):
        if name in skip:
            continue
        if name not in out:
            if default is _MISSING:
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


def problem_message(path: str, what: str) -> str:
    return (
        f"{STORED_WRONG_TYPE} at {path.lstrip('/') or 'the root'}: {what}. Studio read "
        "the default in its place and will not export the GPO; re-import it, or restore "
        "a revision without it"
    )
