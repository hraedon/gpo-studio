"""Plan 033 WP-1B: Studio-origin writer conformance comparison.

WP-2 proved that Windows Server 2025 *accepts* a Studio-authored native backup
container.  Acceptance is not conformance: ``Import-GPO`` succeeding says
nothing about whether GPMC and the client-side extensions assign the same
meaning to the payload Studio wrote.

This module supplies the semantic comparison for the writer lane.  A single
canonical summary shape is derived two ways:

* :func:`summary_from_gpo` — from the Studio model that authored the candidate;
* :func:`summary_from_backup` — from a native ``Backup-GPO`` tree produced by
  Windows *after* importing that candidate.

:func:`compare_summaries` reports typed differences between the two.  An empty
difference tuple is the only outcome that may promote a capability row beyond
``unit-verified``; anything else is a finding.

Normalization is deliberately narrow and mirrors the WP-0 contract in
:mod:`gpo_studio.oracle_evidence`: only values that Windows or Studio are known
to regenerate are dropped.  Item identity GUIDs, ``changed`` timestamps, and
CLSIDs are regenerated or rewritten across a GPMC round trip and carry no
policy meaning, so they are excluded.  Every field that determines *what the
policy does* — action codes, typed registry values, paths, principals, trigger
semantics, filters, and common options — remains in the comparison.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .gpp import GppCollection
from .import_export import collect_gpp_collections, extract_side_settings
from .model import GPO

WRITER_SUMMARY_VERSION = "gpo-studio.writer-conformance.v1"

#: GPP families Studio can emit into a native backup container.  Kept in sync
#: with ``_GPP_EXTENSION_PROFILES`` in :mod:`gpo_studio.export`; a family absent
#: here is blocked at export rather than guessed at.
NATIVE_GPP_FAMILIES: tuple[str, ...] = ("drives", "groups", "local_users", "scheduled_tasks")

DifferenceKind = Literal["missing", "unexpected", "changed"]


@dataclass(frozen=True, slots=True)
class Difference:
    """One semantic divergence between the authored and round-tripped models."""

    kind: DifferenceKind
    path: str
    expected: object = None
    actual: object = None

    def describe(self) -> str:
        if self.kind == "missing":
            return f"{self.path}: present in Studio model, absent after round trip"
        if self.kind == "unexpected":
            return f"{self.path}: absent in Studio model, present after round trip"
        return f"{self.path}: expected {self.expected!r}, got {self.actual!r}"

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "path": self.path,
            "expected": self.expected,
            "actual": self.actual,
            "message": self.describe(),
        }


def _common(item: Any) -> dict[str, object]:
    common = item.common
    return {
        "apply_once": common.apply_once,
        "remove_when_unapplied": common.remove_when_unapplied,
        "user_security_context": common.user_security_context,
        "disabled": common.disabled,
        "stop_on_error": common.stop_on_error,
    }


def _ilt(item: Any) -> object:
    """Summarize an item-level targeting filter, or ``None`` when absent.

    The filter is reduced to its full item sequence, including predicates Studio
    could not type (which round-trip as raw strings).  Nothing here is
    normalized away: an ILT change silently alters which endpoints a preference
    applies to, so any divergence must fail the comparison.
    """
    ilt_filter = item.ilt_filter
    if ilt_filter is None:
        return None
    return [repr(entry) for entry in ilt_filter.items]


def _drive(item: Any) -> dict[str, object]:
    return {
        "letter": item.letter,
        "path": item.path,
        "label": item.label,
        "persistent": item.persistent,
        "use_letter": item.use_letter,
        "action": item.action,
        "common": _common(item),
        "ilt": _ilt(item),
    }


def _group(item: Any) -> dict[str, object]:
    return {
        "name": item.name,
        "sid": item.sid,
        "action": item.action,
        "description": item.description,
        "remove_all_users": item.remove_all_users,
        "remove_all_groups": item.remove_all_groups,
        "members": sorted(
            (
                {"name": member.name, "sid": member.sid, "action": member.action}
                for member in item.members
            ),
            key=lambda member: (str(member["name"]), str(member["sid"])),
        ),
        "common": _common(item),
        "ilt": _ilt(item),
    }


def _local_user(item: Any) -> dict[str, object]:
    return {
        "user_name": item.user_name,
        "full_name": item.full_name,
        "description": item.description,
        "password_never_expires": item.password_never_expires,
        "user_cannot_change_password": item.user_cannot_change_password,
        "account_disabled": item.account_disabled,
        "account_locked_out": item.account_locked_out,
        "action": item.action,
        "common": _common(item),
        "ilt": _ilt(item),
    }


def _scheduled_task(item: Any) -> dict[str, object]:
    return {
        "name": item.name,
        "run_as": item.run_as,
        "program": item.program,
        "arguments": item.arguments,
        "start_in": item.start_in,
        "enabled": item.enabled,
        "trigger_type": item.trigger_type,
        "trigger_time": item.trigger_time,
        "trigger_days": item.trigger_days,
        "element_variant": item.element_variant,
        "action": item.action,
        "common": _common(item),
        "ilt": _ilt(item),
    }


_FAMILY_SUMMARIZERS: dict[str, tuple[str, Any]] = {
    "drives": ("drives", _drive),
    "groups": ("groups", _group),
    "local_users": ("local_users", _local_user),
    "scheduled_tasks": ("scheduled_tasks", _scheduled_task),
}


def _sort_key(family: str, entry: dict[str, object]) -> str:
    """Order items by their natural policy key.

    GPMC does not guarantee preference-item order across a round trip, and item
    order within a GPP file has no policy meaning for these families, so the
    comparison is order-insensitive.
    """
    for field_name in ("letter", "name", "user_name"):
        value = entry.get(field_name)
        if isinstance(value, str) and value:
            return value.casefold()
    return repr(sorted(entry.items()))


def _collection_summary(collection: GppCollection) -> dict[str, object]:
    summary: dict[str, object] = {}
    for family in NATIVE_GPP_FAMILIES:
        attribute, summarizer = _FAMILY_SUMMARIZERS[family]
        entries = [summarizer(item) for item in getattr(collection, attribute)]
        summary[family] = sorted(entries, key=lambda entry: _sort_key(family, entry))
    return summary


def _empty_collection_summary() -> dict[str, object]:
    return {family: [] for family in NATIVE_GPP_FAMILIES}


def _registry_summary(settings: Any) -> list[dict[str, object]]:
    entries = [
        {
            "hive": setting.hive,
            "key": setting.key,
            "value_name": setting.value_name,
            "registry_type": setting.registry_type,
            "value": setting.value,
        }
        for setting in settings
    ]
    return sorted(
        entries,
        key=lambda entry: (
            str(entry["hive"]).casefold(),
            str(entry["key"]).casefold(),
            str(entry["value_name"]).casefold(),
        ),
    )


def summary_from_gpo(gpo: GPO) -> dict[str, object]:
    """Summarize the Studio model that authored a native backup candidate."""
    by_scope = {collection.scope: collection for collection in gpo.gpp_collections}
    summary: dict[str, object] = {
        "version": WRITER_SUMMARY_VERSION,
        "registry": {
            "computer": _registry_summary(
                [item for item in gpo.settings if item.side == "computer"]
            ),
            "user": _registry_summary([item for item in gpo.settings if item.side == "user"]),
        },
        "preferences": {},
    }
    preferences: dict[str, object] = {}
    for scope in ("computer", "user"):
        collection = by_scope.get(scope)
        preferences[scope] = (
            _collection_summary(collection) if collection else _empty_collection_summary()
        )
    summary["preferences"] = preferences
    return summary


def summary_from_backup(content_root: Path) -> dict[str, object]:
    """Summarize a native backup content root (``.../DomainSysvol/GPO``).

    Accepts either a Studio-authored container or a Windows ``Backup-GPO``
    tree; both are read through the ordinary Studio import path, which is the
    point — the comparison must exercise the code a user would actually run.
    """
    by_scope = {
        collection.scope: collection for collection in collect_gpp_collections(content_root)
    }
    preferences: dict[str, object] = {}
    for scope in ("computer", "user"):
        collection = by_scope.get(scope)
        preferences[scope] = (
            _collection_summary(collection) if collection else _empty_collection_summary()
        )
    return {
        "version": WRITER_SUMMARY_VERSION,
        "registry": {
            "computer": _registry_summary(extract_side_settings(content_root, "computer")),
            "user": _registry_summary(extract_side_settings(content_root, "user")),
        },
        "preferences": preferences,
    }


def _compare_lists(
    path: str,
    expected: list[Any],
    actual: list[Any],
    differences: list[Difference],
) -> None:
    for index in range(max(len(expected), len(actual))):
        item_path = f"{path}[{index}]"
        if index >= len(actual):
            differences.append(Difference("missing", item_path, expected=expected[index]))
        elif index >= len(expected):
            differences.append(Difference("unexpected", item_path, actual=actual[index]))
        else:
            _compare_values(item_path, expected[index], actual[index], differences)


def _compare_values(
    path: str,
    expected: object,
    actual: object,
    differences: list[Difference],
) -> None:
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            child = f"{path}.{key}" if path else str(key)
            if key not in actual:
                differences.append(Difference("missing", child, expected=expected[key]))
            elif key not in expected:
                differences.append(Difference("unexpected", child, actual=actual[key]))
            else:
                _compare_values(child, expected[key], actual[key], differences)
        return
    if isinstance(expected, list) and isinstance(actual, list):
        _compare_lists(path, expected, actual, differences)
        return
    if expected != actual:
        differences.append(Difference("changed", path, expected=expected, actual=actual))


def compare_summaries(
    expected: dict[str, object], actual: dict[str, object]
) -> tuple[Difference, ...]:
    """Return every semantic difference between two writer summaries."""
    differences: list[Difference] = []
    _compare_values("", expected, actual, differences)
    return tuple(differences)
