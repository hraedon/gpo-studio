"""Every closed-set field of a GPO, held to its set (final-r17).

The model types many fields as a closed set -- a ``Literal`` or an alias of
one: a setting's ``registry_type`` and ``action``, a hive, a GPP item's
action, a targeting predicate's type, a scope. The API's request models
enforce those sets, but not every way in does: an estate import read
settings verbatim, stored ``registry_type="REG_FOO"``, and every later read
and export of that GPO failed (500) in a writer that assumed the set
(DeepSeek final-r17 B1).

`closed_set_issues` walks a GPO's dataclasses by their annotations and
reports every value outside its field's set as a validation error, so an
import or a mutation carrying one is refused, and a stored one makes the GPO
blocked -- reported, readable, never built. It derives the sets from the
annotations, so a field added later is covered the day it lands.
"""

from __future__ import annotations

import dataclasses
import sys
import types
import typing
from collections.abc import Iterator
from functools import cache
from typing import Any, Literal, get_args, get_origin

from .model import ValidationIssue

#: A closed set: its members, and whether ``None`` is allowed too.
ClosedSet = tuple[frozenset[Any], bool]


def _namespace() -> dict[str, Any]:
    """Every name an annotation of a GPO dataclass can use."""
    from . import backup_inventory, fdeploy, gpp, gpp_adapters, ilt, model

    space: dict[str, Any] = {"Literal": Literal, "typing": typing}
    for module in (model, ilt, gpp, gpp_adapters, fdeploy, backup_inventory):
        space.update(vars(module))
    return space


def _closed(annotation: Any) -> ClosedSet | None:
    """The closed set *annotation* names, or ``None`` if it names none."""
    origin = get_origin(annotation)
    if origin is Literal:
        return frozenset(get_args(annotation)), False
    if origin in (typing.Union, types.UnionType):
        members: set[Any] = set()
        optional = False
        for arg in get_args(annotation):
            if arg is type(None):
                optional = True
                continue
            inner = _closed(arg)
            if inner is None:
                return None  # a union with an open member is open
            members |= inner[0]
            optional = optional or inner[1]
        return (frozenset(members), optional) if members else None
    return None


@cache
def _field_sets(cls: type) -> tuple[tuple[str, ClosedSet], ...]:
    """(field name, closed set) for each closed-set field of dataclass *cls*."""
    space = _namespace()
    space.update(vars(sys.modules[cls.__module__]))
    found: list[tuple[str, ClosedSet]] = []
    for item in dataclasses.fields(cls):
        annotation: Any = item.type
        if isinstance(annotation, str):
            try:
                annotation = eval(annotation, space)  # noqa: S307 - our own annotations
            except Exception:  # an annotation naming a type, not a set
                continue
        closed = _closed(annotation)
        if closed is not None:
            found.append((item.name, closed))
    return tuple(found)


def _walk(value: Any, path: str) -> Iterator[tuple[str, Any, ClosedSet]]:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for name, closed in _field_sets(type(value)):
            yield f"{path}/{name}", getattr(value, name), closed
        for item in dataclasses.fields(value):
            yield from _walk(getattr(value, item.name), f"{path}/{item.name}")
    elif isinstance(value, (tuple, list)):
        for index, entry in enumerate(value):
            yield from _walk(entry, f"{path}/{index}")


def closed_set_issues(root: Any, prefix: str = "") -> list[ValidationIssue]:
    """An error for every closed-set field of *value* (a GPO, or any part of
    one, at *path*) holding a value outside its set."""
    issues: list[ValidationIssue] = []
    for path, value, (members, optional) in _walk(root, prefix):
        if value in members or (optional and value is None):
            continue
        shown = repr(value)
        shown = shown if len(shown) <= 40 else shown[:37] + "..."
        allowed = ", ".join(sorted(repr(member) for member in members))
        issues.append(
            ValidationIssue(
                "error",
                "value_outside_closed_set",
                f"{path.lstrip('/')} is {shown}, which is not one of {allowed}. Studio "
                "cannot write it; correct or delete it, or re-import the GPO.",
                path.lstrip("/"),
            )
        )
    return issues
