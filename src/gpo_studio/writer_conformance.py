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

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .gpp import GppCollection, GppError, parse_gpp_collection, serialize_gpp
from .import_export import collect_gpp_collections, extract_side_settings
from .model import GPO
from .xml_safety import parse_xml_bounded

WRITER_SUMMARY_VERSION = "gpo-studio.writer-conformance.v1"

#: A GPMC XML report is lab-supplied input, not trusted content, so it is parsed
#: under the same structural bounds as the rest of the oracle evidence path.
MAX_REPORT_XML_BYTES = 32 * 1024 * 1024


class WriterConformanceError(ValueError):
    """A writer-conformance input could not be read safely."""

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


_SETTINGS_NS = "http://www.microsoft.com/GroupPolicy/Settings"
_XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"

#: GPMC renders each preference container under a report-specific root name.
#: Mapping it back to the MS-GPPREF on-disk root lets Studio's own parser read
#: GPMC's rendering, which is the point: agreement is then between two
#: independent readers of the same policy, not between Studio and itself.
_REPORT_ROOT_TO_GPP_FILE: dict[str, tuple[str, str]] = {
    "DriveMapSettings": ("Drives", "Drives/Drives.xml"),
    "LocalUsersAndGroups": ("Groups", "Groups/Groups.xml"),
    "ScheduledTasks": ("ScheduledTasks", "ScheduledTasks/ScheduledTasks.xml"),
}

#: Report-only bookkeeping GPMC adds that has no on-disk counterpart.
_REPORT_ONLY_ELEMENTS: frozenset[str] = frozenset({"GPOSettingOrder"})


def _strip_report_namespace(element: ET.Element, root_name: str) -> ET.Element:
    """Rebuild a GPMC report subtree as its namespace-free on-disk equivalent."""
    local_name = element.tag.split("}", 1)[-1]
    rebuilt = ET.Element(root_name if root_name else local_name)
    for key, value in element.attrib.items():
        if key == _XSI_TYPE:
            continue
        rebuilt.set(key.split("}", 1)[-1], value)
    # Text content is load-bearing: a genuine Task Scheduler 2.0 item carries its
    # command in <Task>/<Actions>/<Exec>/<Command> element text, not attributes.
    # Whitespace-only text and all tails are GPMC's pretty-printing, not policy,
    # and are dropped so they cannot show up as spurious differences.
    text = element.text
    rebuilt.text = text if text is not None and text.strip() else None
    for child in element:
        child_local = child.tag.split("}", 1)[-1]
        if child_local in _REPORT_ONLY_ELEMENTS:
            continue
        rebuilt.append(_strip_report_namespace(child, ""))
    return rebuilt


def _report_side_collection(side_element: ET.Element, scope: str) -> GppCollection | None:
    files: dict[str, bytes] = {}
    for extension_data in side_element.iter(f"{{{_SETTINGS_NS}}}ExtensionData"):
        for container in extension_data:
            if container.get(_XSI_TYPE) is None:
                continue
            for settings_root in container:
                local_name = settings_root.tag.split("}", 1)[-1]
                mapped = _REPORT_ROOT_TO_GPP_FILE.get(local_name)
                if mapped is None:
                    continue
                disk_root, file_path = mapped
                rebuilt = _strip_report_namespace(settings_root, disk_root)
                files[file_path] = ET.tostring(rebuilt, encoding="utf-8")
    if not files:
        return None
    return parse_gpp_collection(scope, files)  # type: ignore[arg-type]


def summary_from_gpmc_report(report_path: Path) -> dict[str, object]:
    """Summarize the preferences GPMC itself reported for an imported GPO.

    This is the writer lane's independent oracle.  ``Import-GPO`` copies GPP
    files through to SYSVOL byte-for-byte, so a backup round trip proves only
    that the payload survived.  The GPMC report proves GPMC *parsed* it: an item
    it could not type would not appear here as a typed element at all.

    Only preference families are summarized.  GPMC renders registry policy in
    Administrative Templates form, which has no faithful mapping back to
    ``registry.pol``; the registry side is covered independently by the
    harness's ``Get-GPRegistryValue`` readback.
    """
    root = parse_xml_bounded(
        report_path.read_bytes(),
        max_size=MAX_REPORT_XML_BYTES,
        error_class=WriterConformanceError,
    )
    preferences: dict[str, object] = {}
    for scope, side in (("computer", "Computer"), ("user", "User")):
        side_element = root.find(f"{{{_SETTINGS_NS}}}{side}")
        collection = (
            _report_side_collection(side_element, scope) if side_element is not None else None
        )
        preferences[scope] = (
            _collection_summary(collection) if collection else _empty_collection_summary()
        )
    return {"version": WRITER_SUMMARY_VERSION, "preferences": preferences}


#: XML attributes genuine GPMC never writes on a ``TaskV2`` item's
#: ``Properties``. They belong to the Task Scheduler 1.0 (``Task``) schema; a v2
#: item carries its actions and triggers inside an embedded ``<Task>`` payload.
_TASK_V1_ONLY_ATTRIBUTES: frozenset[str] = frozenset({
    "program",
    "arguments",
    "startIn",
    "enabled",
    "triggerType",
    "triggerTime",
    "triggerDays",
})


def native_shape_findings(gpo: GPO) -> tuple[str, ...]:
    """Report emitted items whose shape has no genuine GPMC precedent.

    Checks the SERIALIZED output, not the model. That distinction matters: the
    scheduled-task writer synthesizes an embedded ``<Task>`` payload at
    serialization time, so a model with an empty ``task_xml`` and populated
    scalar fields still emits a correct element. An earlier version of this
    function inspected the model as a proxy for the output and became wrong the
    moment the two legitimately diverged.

    A GPMC report round trip cannot detect this class of defect: GPMC echoes
    back attributes it does not act on, so an item can survive import, report,
    and re-export intact while the client-side extension ignores it entirely.
    Plan 033 forbids emitting a feature "under a synthetic format that Windows
    silently ignores", so shape is checked against the captured native corpus
    directly rather than inferred from a round trip.
    """
    findings: list[str] = []
    for collection in gpo.gpp_collections:
        if not collection.scheduled_tasks and not collection.immediate_tasks:
            continue
        try:
            emitted = serialize_gpp(collection).get("ScheduledTasks/ScheduledTasks.xml")
        except GppError as error:
            findings.append(f"scheduled tasks ({collection.scope}) do not serialize: {error}")
            continue
        if emitted is None:
            continue
        try:
            root = parse_xml_bounded(
                emitted,
                max_size=MAX_REPORT_XML_BYTES,
                error_class=WriterConformanceError,
            )
        except WriterConformanceError as error:  # pragma: no cover - we produced this XML
            findings.append(f"scheduled tasks ({collection.scope}) emit invalid XML: {error}")
            continue
        for item in root:
            if item.tag.split("}", 1)[-1] != "TaskV2":
                continue
            name = item.get("name", "")
            properties = next(
                (child for child in item if child.tag.split("}", 1)[-1] == "Properties"), None
            )
            if properties is None:
                findings.append(
                    f"scheduled task {name!r} ({collection.scope}) emits a TaskV2 with no "
                    "Properties element"
                )
                continue
            has_payload = any(
                child.tag.split("}", 1)[-1] == "Task" for child in properties
            )
            if not has_payload:
                findings.append(
                    f"scheduled task {name!r} ({collection.scope}) is emitted as TaskV2 "
                    "with no embedded <Task> payload; genuine GPMC TaskV2 items always "
                    "carry one, so the Scheduled Tasks CSE has nothing to act on"
                )
            scalars = sorted(
                attribute
                for attribute in _TASK_V1_ONLY_ATTRIBUTES
                if attribute in properties.attrib
            )
            if scalars:
                findings.append(
                    f"scheduled task {name!r} ({collection.scope}) is emitted with Task "
                    f"Scheduler 1.0 scalar properties ({', '.join(scalars)}) on a TaskV2 "
                    "element; genuine GPMC TaskV2 items never carry them"
                )
    return tuple(findings)


def compare_preferences(
    expected: dict[str, object], actual: dict[str, object]
) -> tuple[Difference, ...]:
    """Compare only the preference subtree of two summaries."""
    differences: list[Difference] = []
    _compare_values("preferences", expected["preferences"], actual["preferences"], differences)
    return tuple(differences)


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
