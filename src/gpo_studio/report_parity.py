"""Report parity: Studio's model against Windows' own GPMC XML report.

Plan 034 rules that ``backup.py`` and ``report.py`` exit through a lane that
compares Studio's import of a GPMC backup with a fresh
``Get-GPOReport -ReportType Xml`` of the same content. This module is the
offline half of that lane: one inventory shape, built two ways.

* :func:`windows_inventory` reads Windows' report. Settings are grouped by side
  and by the report's ``Extension`` ``xsi:type`` (``RegistrySettings``,
  ``DriveMapSettings``, ``LugsSettings`` ...). Registry entries are listed by
  key path, value name and the value Windows rendered; preference items by
  element, ``name``, ``uid`` and ``Properties/@action``, in document order.
* :func:`studio_inventory` builds the same shape from Studio's typed model.
  Registry entries come from ``GPO.settings``. Preference items come from
  :func:`gpo_studio.gpp.serialize_gpp` over each collection **with its retained
  source bytes removed**, so the inventory is what the typed model would write
  after an edit, not a replay of the imported files. Comparing the imported
  bytes with a report generated from those same bytes would be a comparison of
  Windows with itself.

:func:`compare` returns per-family equality and every named divergence.
:data:`KNOWN_DIVERGENCES` lists the divergences that are understood: named
exclusions the lane does not measure, and defects whose fix belongs in a file a
live verdict binds, each pinned to a work item. :func:`classify` splits a
result into those and the unexplained remainder, which must be empty.

The module is unbound: no live lane verdict hashes it (see
``docs/plan-033/bound-source-cost.md``), so it can change without expiring
evidence. Its claims about Windows are hypotheses until the report-parity lane
has a verdict.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, assert_never

from .gpp import GppCollection, GppError, serialize_gpp
from .model import GPO, RegistrySetting, StudioError
from .xml_safety import parse_xml_bounded

Side = Literal["computer", "user"]
DivergenceKind = Literal["missing_in_studio", "extra_in_studio", "order", "admx_policy_rendering"]

SETTINGS_NS = "http://www.microsoft.com/GroupPolicy/Settings"
_XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
_MAX_REPORT_BYTES = 50 * 1024 * 1024
_REPORT_SIDES: tuple[tuple[str, Side], ...] = (("Computer", "computer"), ("User", "user"))

REGISTRY_FAMILY = "RegistrySettings"

#: Preference file -> the ``Extension`` ``xsi:type`` Windows' report uses for
#: it. Only pairs observed in a Windows-produced ``gpreport.xml`` in the corpus
#: are listed; a family Studio models but no capture has shown is reported
#: under ``unobserved:<path>`` rather than under a guessed name, so it can only
#: ever surface as a divergence until a capture names it.
OBSERVED_GPP_FAMILIES: dict[str, str] = {
    "Drives/Drives.xml": "DriveMapSettings",
    "EnvironmentVariables/EnvironmentVariables.xml": "EnvironmentVariablesSettings",
    "Files/Files.xml": "FilesSettings",
    "Folders/Folders.xml": "FoldersSettings",
    "Groups/Groups.xml": "LugsSettings",
    "IniFiles/IniFiles.xml": "IniFilesSettings",
    "PowerOptions/PowerOptions.xml": "PowerOptionsSettings",
    "Printers/Printers.xml": "PrintersSettings",
    "ScheduledTasks/ScheduledTasks.xml": "ScheduledTasksSettings",
    "Services/Services.xml": "ServiceSettings",
    "Shortcuts/Shortcuts.xml": "ShortcutSettings",
}

#: How Studio's registry types are expected to appear in a report ``Value``.
#: Only ``String`` and ``Number`` have been observed. Any other type renders as
#: ``<REG_TYPE>:<value>``, which cannot equal a Windows rendering and therefore
#: surfaces as a divergence until a capture says what Windows prints.
_OBSERVED_VALUE_KINDS: dict[str, str] = {"REG_SZ": "String", "REG_DWORD": "Number"}


class ReportParityError(StudioError):
    """A report or model could not be reduced to an inventory."""


@dataclass(frozen=True, slots=True, order=True)
class InventoryItem:
    """One setting as both sides can name it.

    Registry entries fill ``key``, ``name`` and ``value``; preference items fill
    ``name``, ``uid`` and ``action``. Fields a side cannot observe stay empty on
    both, so equality is exact.
    """

    element: str
    key: str = ""
    name: str = ""
    uid: str = ""
    action: str = ""
    value: str = ""

    def label(self) -> str:
        if self.key or self.element == "RegistrySetting":
            return f"{self.key} :: {self.name or '(Default)'} = {self.value}"
        parts = [self.element, repr(self.name)]
        if self.action:
            parts.append(f"[{self.action}]")
        if self.uid:
            parts.append(self.uid)
        return " ".join(parts)


@dataclass(frozen=True, slots=True)
class FamilyInventory:
    side: Side
    family: str
    items: tuple[InventoryItem, ...]


@dataclass(frozen=True, slots=True)
class Inventory:
    """Settings grouped by (side, report family), items in document order.

    ``admx_policies`` counts ``<Policy>`` elements per side: registry content
    Windows rendered through an ADMX definition instead of as a raw
    ``RegistrySetting``. Studio never produces any, so a non-zero count is the
    named exclusion ``admx-policy-rendering``.
    """

    families: tuple[FamilyInventory, ...]
    admx_policies: tuple[tuple[Side, int], ...] = ()

    def family(self, side: Side, family: str) -> tuple[InventoryItem, ...]:
        for entry in self.families:
            if entry.side == side and entry.family == family:
                return entry.items
        return ()

    def keys(self) -> tuple[tuple[Side, str], ...]:
        return tuple((entry.side, entry.family) for entry in self.families)

    def to_json(self) -> dict[str, object]:
        return {
            "families": [
                {
                    "side": entry.side,
                    "family": entry.family,
                    "items": [
                        {
                            "element": item.element,
                            "key": item.key,
                            "name": item.name,
                            "uid": item.uid,
                            "action": item.action,
                            "value": item.value,
                        }
                        for item in entry.items
                    ],
                }
                for entry in self.families
            ],
            "admx_policies": {side: count for side, count in self.admx_policies},
        }


def inventory_from_json(data: object) -> Inventory:
    """Rebuild an :class:`Inventory` from :meth:`Inventory.to_json` output."""
    if not isinstance(data, dict):
        raise ReportParityError("inventory must be an object")
    families_raw = data.get("families")
    admx_raw = data.get("admx_policies", {})
    if not isinstance(families_raw, list) or not isinstance(admx_raw, dict):
        raise ReportParityError("inventory has no families list")
    families: list[FamilyInventory] = []
    for entry in families_raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("items"), list):
            raise ReportParityError("inventory family is malformed")
        side = _side(entry.get("side"))
        family = entry.get("family")
        if not isinstance(family, str):
            raise ReportParityError("inventory family has no name")
        items: list[InventoryItem] = []
        for raw in entry["items"]:
            if not isinstance(raw, dict) or any(
                not isinstance(raw.get(k), str)
                for k in ("element", "key", "name", "uid", "action", "value")
            ):
                raise ReportParityError("inventory item is malformed")
            items.append(InventoryItem(
                element=raw["element"], key=raw["key"], name=raw["name"],
                uid=raw["uid"], action=raw["action"], value=raw["value"],
            ))
        families.append(FamilyInventory(side=side, family=family, items=tuple(items)))
    admx: list[tuple[Side, int]] = []
    for side_raw, count in sorted(admx_raw.items()):
        if type(count) is not int:
            raise ReportParityError("admx policy count must be an integer")
        admx.append((_side(side_raw), count))
    return Inventory(families=_sorted(families), admx_policies=tuple(admx))


def _side(value: object) -> Side:
    if value == "computer":
        return "computer"
    if value == "user":
        return "user"
    raise ReportParityError(f"unknown side {value!r}")


def _sorted(families: Iterable[FamilyInventory]) -> tuple[FamilyInventory, ...]:
    return tuple(sorted(families, key=lambda f: (f.side, f.family)))


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _child(elem: ET.Element, name: str) -> ET.Element | None:
    for child in elem:
        if _local(child.tag) == name:
            return child
    return None


def _text(elem: ET.Element | None) -> str:
    return (elem.text or "") if elem is not None else ""


def _gpp_item(elem: ET.Element) -> InventoryItem:
    props = _child(elem, "Properties")
    return InventoryItem(
        element=_local(elem.tag),
        name=elem.get("name", ""),
        uid=elem.get("uid", ""),
        action=props.get("action", "") if props is not None else "",
    )


def _plain_item(elem: ET.Element) -> InventoryItem:
    return InventoryItem(
        element=_local(elem.tag),
        name=elem.get("name") or _text(_child(elem, "Name")) or _text(_child(elem, "Command")),
    )


def _report_registry_items(extension: ET.Element) -> tuple[list[InventoryItem], int]:
    items: list[InventoryItem] = []
    policies = 0
    for child in extension:
        local = _local(child.tag)
        if local == "Policy":
            policies += 1
        elif local == "RegistrySetting":
            value_elem = _child(child, "Value")
            name = ""
            value = ""
            if value_elem is not None:
                name = _text(_child(value_elem, "Name"))
                for data in value_elem:
                    if _local(data.tag) != "Name":
                        texts = [t.strip() for t in data.itertext() if t.strip()]
                        value = f"{_local(data.tag)}:{'; '.join(texts)}"
                        break
            items.append(InventoryItem(
                element="RegistrySetting",
                key=_text(_child(child, "KeyPath")),
                name=name,
                value=value,
            ))
    return items, policies


def windows_inventory(report_xml: bytes) -> Inventory:
    """Inventory a ``Get-GPOReport -ReportType Xml`` document (any encoding)."""
    root = parse_xml_bounded(
        report_xml, max_size=_MAX_REPORT_BYTES, error_class=ReportParityError
    )
    if root.tag != f"{{{SETTINGS_NS}}}GPO":
        raise ReportParityError("not a GPMC settings report")
    families: dict[tuple[Side, str], list[InventoryItem]] = {}
    admx: dict[Side, int] = {}
    for scope_name, side in _REPORT_SIDES:
        scope = root.find(f"{{{SETTINGS_NS}}}{scope_name}")
        if scope is None:
            continue
        for data in scope.findall(f"{{{SETTINGS_NS}}}ExtensionData"):
            extension = data.find(f"{{{SETTINGS_NS}}}Extension")
            if extension is None:
                continue
            family = extension.get(_XSI_TYPE, "").rsplit(":", 1)[-1]
            if not family:
                raise ReportParityError("report extension has no xsi:type")
            bucket = families.setdefault((side, family), [])
            if family == REGISTRY_FAMILY:
                items, policies = _report_registry_items(extension)
                bucket.extend(items)
                if policies:
                    admx[side] = admx.get(side, 0) + policies
                continue
            for child in extension:
                # Preference families wrap their items in a clsid-bearing
                # container (<DriveMapSettings clsid=...>); other families
                # (Scripts) list their entries directly.
                if child.get("clsid") is not None:
                    bucket.extend(_gpp_item(item) for item in child)
                else:
                    bucket.append(_plain_item(child))
    return Inventory(
        families=_sorted(
            FamilyInventory(side=side, family=family, items=tuple(items))
            for (side, family), items in families.items()
        ),
        admx_policies=tuple(sorted(admx.items())),
    )


def _studio_registry_item(setting: RegistrySetting) -> InventoryItem:
    # Registry.pol encodes deletions as reserved value names; that is the name
    # a report reading the file can show. No captured report contains one, so
    # the rendering is unmeasured (the lane design names it).
    match setting.action:
        case "set":
            name = setting.value_name
        case "delete":
            name = f"**del.{setting.value_name}"
        case "delete_all_values":
            name = "**delvals."
        case _:
            assert_never(setting.action)
    kind = _OBSERVED_VALUE_KINDS.get(setting.registry_type, setting.registry_type)
    raw = setting.value
    rendered = "; ".join(raw) if isinstance(raw, list) else str(raw)
    value = f"{kind}:{rendered}" if setting.action == "set" else ""
    return InventoryItem(element="RegistrySetting", key=setting.key, name=name, value=value)


def studio_gpp_family(path: str) -> str:
    """The report family a Studio preference file is compared under."""
    return OBSERVED_GPP_FAMILIES.get(path, f"unobserved:{path}")


def _studio_gpp_items(collection: GppCollection) -> dict[str, list[InventoryItem]]:
    try:
        files = serialize_gpp(replace(collection, source_files=()))
    except GppError as exc:
        raise ReportParityError(
            f"{collection.scope} preferences cannot be rendered: {exc}"
        ) from exc
    out: dict[str, list[InventoryItem]] = {}
    for path, data in sorted(files.items()):
        root = parse_xml_bounded(
            data, max_size=_MAX_REPORT_BYTES, error_class=ReportParityError
        )
        out.setdefault(studio_gpp_family(path), []).extend(_gpp_item(item) for item in root)
    return out


def studio_inventory(gpo: GPO) -> Inventory:
    """Inventory Studio's typed model in the shape of a Windows report."""
    families: dict[tuple[Side, str], list[InventoryItem]] = {}
    for setting in gpo.settings:
        families.setdefault((setting.side, REGISTRY_FAMILY), []).append(
            _studio_registry_item(setting)
        )
    for collection in gpo.gpp_collections:
        for family, items in _studio_gpp_items(collection).items():
            families.setdefault((collection.scope, family), []).extend(items)
    return Inventory(
        families=_sorted(
            FamilyInventory(side=side, family=family, items=tuple(items))
            for (side, family), items in families.items()
            if items
        )
    )


@dataclass(frozen=True, slots=True)
class Divergence:
    side: Side
    family: str
    kind: DivergenceKind
    item: InventoryItem | None = None
    detail: str = ""

    def describe(self) -> str:
        what = self.item.label() if self.item is not None else self.detail
        return f"{self.side}/{self.family}: {self.kind}: {what}"


@dataclass(frozen=True, slots=True)
class FamilyComparison:
    side: Side
    family: str
    windows_count: int
    studio_count: int
    divergences: tuple[Divergence, ...]

    @property
    def equal(self) -> bool:
        return not self.divergences


@dataclass(frozen=True, slots=True)
class ParityResult:
    families: tuple[FamilyComparison, ...]

    @property
    def equal(self) -> bool:
        return all(family.equal for family in self.families)

    @property
    def divergences(self) -> tuple[Divergence, ...]:
        return tuple(d for family in self.families for d in family.divergences)


def compare(windows: Inventory, studio: Inventory) -> ParityResult:
    """Compare per (side, family): identical items, in identical order.

    Items are compared as a multiset first, so a missing or extra item is named
    as such; only when both sides hold the same items is an order difference
    reported. Preference items are processed in document order, so order is
    part of what a GPO means, not presentation.
    """
    keys = sorted(set(windows.keys()) | set(studio.keys()))
    admx = dict(windows.admx_policies)
    comparisons: list[FamilyComparison] = []
    for side, family in keys:
        theirs = windows.family(side, family)
        ours = studio.family(side, family)
        divergences: list[Divergence] = []
        missing = Counter(theirs) - Counter(ours)
        extra = Counter(ours) - Counter(theirs)
        for item in theirs:
            if missing[item]:
                missing[item] -= 1
                divergences.append(Divergence(side, family, "missing_in_studio", item))
        for item in ours:
            if extra[item]:
                extra[item] -= 1
                divergences.append(Divergence(side, family, "extra_in_studio", item))
        if not divergences and theirs != ours:
            divergences.append(Divergence(
                side, family, "order",
                detail="windows " + ", ".join(i.label() for i in theirs)
                + " | studio " + ", ".join(i.label() for i in ours),
            ))
        if family == REGISTRY_FAMILY and admx.get(side):
            divergences.append(Divergence(
                side, family, "admx_policy_rendering",
                detail=f"{admx[side]} ADMX-rendered <Policy> element(s) not compared",
            ))
        comparisons.append(FamilyComparison(
            side=side, family=family, windows_count=len(theirs),
            studio_count=len(ours), divergences=tuple(divergences),
        ))
    return ParityResult(families=tuple(comparisons))


# ---------------------------------------------------------------------------
# Known divergences
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KnownDivergence:
    """A divergence that is understood, and why it is not a failure.

    ``work_item`` is set when the divergence is a Studio defect whose fix needs
    a bound file; ``None`` marks a named exclusion of the lane's scope.
    """

    name: str
    description: str
    work_item: str | None
    matches: Callable[[Divergence, tuple[Divergence, ...]], bool]


def _is(
    family: str, kind: DivergenceKind, element: str | None = None
) -> Callable[[Divergence, tuple[Divergence, ...]], bool]:
    def predicate(divergence: Divergence, siblings: tuple[Divergence, ...] = ()) -> bool:
        del siblings  # single-divergence matchers need no context
        if divergence.family != family or divergence.kind != kind:
            return False
        if element is None:
            return True
        return divergence.item is not None and divergence.item.element == element

    return predicate


def _drive_letter_pair(item: InventoryItem) -> InventoryItem | None:
    """The other half of a bare-letter / letter-colon drive name pair."""
    if len(item.name) == 1 and item.name.isalpha():
        return replace(item, name=f"{item.name}:")
    if len(item.name) == 2 and item.name[0].isalpha() and item.name[1] == ":":
        return replace(item, name=item.name[0])
    return None


def _legacy_drive_name(divergence: Divergence, siblings: tuple[Divergence, ...]) -> bool:
    """A Windows ``P`` and a Studio ``P:`` that are otherwise the same item.

    Matched only as a pair: a lone extra ``P:`` (Studio writes a drive Windows
    does not have) or a lone missing ``P`` is a real divergence, not a label.
    """
    item = divergence.item
    if divergence.family != "DriveMapSettings" or item is None or item.element != "Drive":
        return False
    other = _drive_letter_pair(item)
    if other is None:
        return False
    if divergence.kind == "missing_in_studio" and len(item.name) == 1:
        wanted: DivergenceKind = "extra_in_studio"
    elif divergence.kind == "extra_in_studio" and len(item.name) == 2:
        wanted = "missing_in_studio"
    else:
        return False
    return any(
        s.kind == wanted and s.family == divergence.family and s.side == divergence.side
        and s.item == other
        for s in siblings
    )


KNOWN_DIVERGENCES: tuple[KnownDivergence, ...] = (
    KnownDivergence(
        name="admx-policy-rendering",
        description=(
            "Registry values an ADMX template describes are rendered as <Policy>; "
            "Studio models raw registry values only. Excluded from the lane."
        ),
        work_item=None,
        matches=_is(REGISTRY_FAMILY, "admx_policy_rendering"),
    ),
    KnownDivergence(
        name="scripts-not-modeled",
        description=(
            "Scripts are kept as source metadata, not typed settings, so the "
            "report's Scripts family has no Studio counterpart. Excluded: the "
            "Scripts metadata lane measures them."
        ),
        work_item=None,
        matches=_is("Scripts", "missing_in_studio", "Script"),
    ),
    KnownDivergence(
        name="legacy-studio-drive-name",
        description=(
            "Windows keeps a preference item's name attribute verbatim. Drive "
            "files written by Studio before report parity named a drive by its "
            "bare letter (P); Studio now derives the name as GPME does (P:), so "
            "a Windows backup of an older Studio-written GPO differs by the "
            "label alone. Not a defect in current output."
        ),
        work_item=None,
        matches=_legacy_drive_name,
    ),
    KnownDivergence(
        name="adapter-root-unknowns-dropped",
        description=(
            "Power Options' GlobalPowerOptionsV2 (the Windows 7+ power plan) is "
            "retained in the model as an unknown root child, but serialize_gpp "
            "rebuilds adapter roots from typed items only, so any edit drops it. "
            "The fix is in gpp.py, which two lanes bind."
        ),
        work_item="WI-072",
        matches=_is("PowerOptionsSettings", "missing_in_studio", "GlobalPowerOptionsV2"),
    ),
    KnownDivergence(
        name="scheduled-task-order",
        description=(
            "ScheduledTasks.xml interleaves TaskV2 and ImmediateTaskV2 items; "
            "the model holds them in two lists and serialize_gpp writes all "
            "scheduled tasks before all immediate tasks, so document order "
            "(processing order) changes. The fix is in the bound model."
        ),
        work_item="WI-073",
        matches=_is("ScheduledTasksSettings", "order"),
    ),
)

_KNOWN_BY_NAME = {known.name: known for known in KNOWN_DIVERGENCES}


def classify(
    result: ParityResult,
) -> tuple[dict[str, tuple[Divergence, ...]], tuple[Divergence, ...]]:
    """Split divergences into known (by name) and unexplained."""
    known: dict[str, list[Divergence]] = {}
    unexplained: list[Divergence] = []
    everything = result.divergences
    for divergence in everything:
        for candidate in KNOWN_DIVERGENCES:
            if candidate.matches(divergence, everything):
                known.setdefault(candidate.name, []).append(divergence)
                break
        else:
            unexplained.append(divergence)
    return {name: tuple(items) for name, items in sorted(known.items())}, tuple(unexplained)


def known_divergence(name: str) -> KnownDivergence:
    return _KNOWN_BY_NAME[name]


# ---------------------------------------------------------------------------
# Studio's import of a backup, as the public endpoint composes it
# ---------------------------------------------------------------------------


def studio_gpo_from_backup(backup_dir: Path) -> GPO:
    """Read a single-GPO GPMC backup into the model fields this lane compares.

    Composes the same calls ``POST /api/backups/import`` makes for registry and
    preference content; ``tests/test_report_parity.py`` holds the two equal.
    """
    from .backup import read_backup
    from .import_export import collect_gpp_collections, extract_side_settings

    backup = read_backup(backup_dir)
    if len(backup.gpos) != 1:
        raise ReportParityError(f"expected one GPO in backup, found {len(backup.gpos)}")
    source = backup.gpos[0]
    root = source.content_root
    if root is None:
        raise ReportParityError("backup has no content root")
    settings = tuple(extract_side_settings(root, "computer") + extract_side_settings(root, "user"))
    return GPO(
        guid=source.guid,
        name=source.display_name or "Imported GPO",
        domain=source.domain or "studio.local",
        settings=settings,
        gpp_collections=collect_gpp_collections(root),
        computer_enabled=source.computer_enabled,
        user_enabled=source.user_enabled,
        backup_inventory=source.backup_inventory,
    )
