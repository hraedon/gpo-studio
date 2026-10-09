"""Group Policy Preferences XML framework with typed editors.

Serializes and parses GPP Groups and Registry XML per the MS-GPPREF protocol.
CLSIDs, element layout, and attribute placement follow Microsoft's documented
format so that output is interoperable with GPMC.
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable
from copy import deepcopy
from dataclasses import MISSING, dataclass, field, fields, replace
from typing import TYPE_CHECKING, Any, Literal, assert_never

from .gpp_native import (
    has_never_retained_name,
    merge_native,
    namespaced_name,
    native_element_xml,
    same_rendering,
)
from .ilt import IltFilter, IltOsCriteria, IltPredicate, parse_ilt, serialize_ilt
from .numeric import coerce_dword_qword
from .registry_pol import _MAX_MULTI_SZ_ITEMS
from .xml_safety import parse_xml_bounded

if TYPE_CHECKING:
    from .gpp_adapters import (
        GppApplication,
        GppDataSource,
        GppDevice,
        GppDrive,
        GppEnvironment,
        GppFile,
        GppFolder,
        GppFolderOptions,
        GppImmediateTask,
        GppIniFile,
        GppLocalGroup,
        GppLocalUser,
        GppNetworkShare,
        GppPowerOptions,
        GppPrinter,
        GppRegionalOptions,
        GppScheduledTask,
        GppService,
        GppShortcut,
    )

_GPP_NS = "http://www.microsoft.com/GroupPolicy/Settings"


def _ns(tag: str) -> str:
    return tag


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _find_local(elem: ET.Element, local: str) -> ET.Element | None:
    for child in elem:
        if _local_name(child.tag) == local:
            return child
    return None


def _findall_local(elem: ET.Element, local: str) -> list[ET.Element]:
    return [child for child in elem if _local_name(child.tag) == local]


GppScope = Literal["computer", "user"]
GppAction = Literal["add", "replace", "remove", "update"]
GppRegistryAction = Literal["create", "replace", "update", "delete"]

# CLSIDs from MS-GPPREF "Outer and Inner Element Names and CLSIDs" table.
_GROUPS_CLSID = "{3125E937-EB16-4b4c-9934-544FC6D24D26}"
_GROUP_CLSID = "{6D4A79E4-529C-4481-ABD0-F5BD7EA93BA7}"
_REGISTRY_SETTINGS_CLSID = "{A3CCFC41-DFDB-43a5-8D26-0FE8B954DA51}"
#: The namespace GPMC's XML report puts GPP Registry under. The report gives it
#: the same local name as Registry.pol policy (``RegistrySettings``, under
#: ``.../Settings/Registry``), so the namespace is what tells them apart. The
#: one copy every reader uses (report_parity, writer_conformance); pinned to
#: the native reports by test_gpp_registry_native.py.
GPP_REGISTRY_REPORT_NAMESPACE = "http://www.microsoft.com/GroupPolicy/Settings/Windows/Registry"
_REGISTRY_CLSID = "{9CD4B2F4-923D-47f5-A062-E897DD1DAD50}"

_ACTION_TO_CODE: dict[GppAction, str] = {
    "add": "C",
    "replace": "R",
    "update": "U",
    "remove": "D",
}
_CODE_TO_ACTION: dict[str, GppAction] = {v: k for k, v in _ACTION_TO_CODE.items()}

#: ``GppRegistry.action`` (the generic vocabulary the workbench shows and edits)
#: and the value's own action, which is what is written (``Properties@action``).
#: One <Registry> item has one action; the two are the same thing named twice.
_REGISTRY_ITEM_TO_VALUE_ACTION: dict[GppAction, GppRegistryAction] = {
    "add": "create",
    "replace": "replace",
    "update": "update",
    "remove": "delete",
}
_REGISTRY_VALUE_TO_ITEM_ACTION: dict[GppRegistryAction, GppAction] = {
    value: item for item, value in _REGISTRY_ITEM_TO_VALUE_ACTION.items()
}


def registry_action_edit(edited: GppRegistry, existing: GppRegistry) -> GppRegistry:
    """Reconcile an edit of a registry item's two names for its one action.

    The writer writes ``value.action``; ``GppRegistry.action`` was never
    written, so an edit to it -- the action column the workbench shows --
    exported the old action (WI-080 review). An edit that changes the item's
    action and not the value's is applied to the value; otherwise the value's
    stands. Either way the item's action is set to match, so the two agree.
    """
    value = edited.value
    if edited.action != existing.action and value.action == existing.value.action:
        value = replace(value, action=_REGISTRY_ITEM_TO_VALUE_ACTION[edited.action])
    return replace(edited, value=value, action=_REGISTRY_VALUE_TO_ITEM_ACTION[value.action])


_REGISTRY_ACTION_TO_CODE: dict[GppRegistryAction, str] = {
    "create": "C",
    "replace": "R",
    "update": "U",
    "delete": "D",
}
_CODE_TO_REGISTRY_ACTION: dict[str, GppRegistryAction] = {
    v: k for k, v in _REGISTRY_ACTION_TO_CODE.items()
}

_MEMBER_ACTION_TO_CODE: dict[GppAction, str] = {
    "add": "ADD",
    "replace": "REPLACE",
    "update": "UPDATE",
    "remove": "REMOVE",
}
_CODE_TO_MEMBER_ACTION: dict[str, GppAction] = {
    "ADD": "add",
    "REPLACE": "replace",
    "UPDATE": "update",
    "REMOVE": "remove",
    "C": "add",
    "R": "replace",
    "U": "update",
    "D": "remove",
}

# Known attributes on the <Group> element per MS-GPPREF.  Includes common
# Known (typed) attributes on the <Group> element.  Attributes not in this
# set are captured as unknown_attrs and re-emitted on export.  Includes
# legacy Studio attributes (action, removeUsers, removeGroups, description)
# for backward-compatible parsing of older Studio-generated XML — these are
# typed fields so must not be captured as unknown.
_COMMON_ITEM_ATTRS = frozenset({
    "applyOnce",  # legacy Studio input; emitted as FilterRunOnce
    "removePolicy",
    "userContext",
    "disabled",
    "bypassErrors",
})
_GROUP_KNOWN_ATTRS = frozenset({
    "clsid", "name",
    "action", "removeUsers", "removeGroups", "description",
}) | _COMMON_ITEM_ATTRS
_MEMBER_KNOWN_ATTRS = frozenset({"name", "sid", "action"})
# ``status`` and ``image`` are DERIVED on <Registry>: the writer emits them from
# the value name and the action code (measured, WI01A-Registry-GPMC), so the
# parser must not capture them as unknown content -- a stale ``image`` carried
# in an unknown bag would contradict an edited action. ``changed`` is not
# derived: Studio has no clock to honour, so an imported timestamp is kept as
# unknown content and re-emitted in its native position, and none is invented.
_REGISTRY_DERIVED_ATTRS = frozenset({"status", "image"})
_REGISTRY_KNOWN_ATTRS = (
    frozenset({"clsid", "name", "action", "uid"})
    | _REGISTRY_DERIVED_ATTRS
    | _COMMON_ITEM_ATTRS
)
_REGISTRY_VALUE_KNOWN_ATTRS = frozenset({
    "action", "hive", "key", "name", "type", "value", "default",
    "applyOnce", "removePolicy", "userContext", "disabled", "bypassErrors",
})
_GROUP_KNOWN_CHILDREN = frozenset({"Properties", "Members", "Filters"})
_REGISTRY_KNOWN_CHILDREN = frozenset({"Properties", "Filters"})
_GROUP_PROPS_KNOWN_ATTRS = frozenset({
    "action", "groupName", "groupSid", "description",
    "deleteAllUsers", "deleteAllGroups",
    "applyOnce", "removePolicy", "userContext", "disabled", "bypassErrors",
})
_GROUP_PROPS_KNOWN_CHILDREN = frozenset({"Members"})
# <Values> is the typed REG_MULTI_SZ payload (one <Value> per string), measured
# in WI01A-Registry-GPMC; the writer generates it, so it is never unknown.
_REGISTRY_PROPS_KNOWN_CHILDREN: frozenset[str] = frozenset({"Values"})
_GROUPS_ROOT_KNOWN_ATTRS = frozenset({"clsid"})
# MS-GPPREF <Groups> root holds both <Group> and <User> inner elements.
_GROUPS_ROOT_KNOWN_CHILDREN = frozenset({"Group", "User"})
_REGISTRY_SETTINGS_ROOT_KNOWN_ATTRS = frozenset({"clsid"})
_REGISTRY_SETTINGS_ROOT_KNOWN_CHILDREN = frozenset({"Registry"})

# Reserved attribute names that must not appear in unknown_attrs bags.
# These are the typed attribute names written during serialization; allowing
# them in unknown_attrs would let API callers override typed fields.
_GROUP_RESERVED_ATTRS = frozenset({
    "clsid", "name",
})
_MEMBER_RESERVED_ATTRS = frozenset({"name", "sid", "action"})
_REGISTRY_RESERVED_ATTRS = frozenset({"clsid", "name", "uid"}) | _REGISTRY_DERIVED_ATTRS
_REGISTRY_VALUE_RESERVED_ATTRS = frozenset({
    "action", "hive", "key", "name", "type", "value", "default",
})

# GPP Registry item shapes with a Windows capture behind them, as (action,
# shape) where shape is the value type or "key-only". Every pair below appears
# in tests/fixtures/native-gpp-registry-gpmc/WI01A-RegistryMatrix-GPMC (the
# 2026-10-08 action x type matrix: 4 actions x 6 types on the computer side,
# key-only x 4 actions on the user side), and
# test_gpp_registry_native.py holds this set equal to the pairs read off those
# native bytes -- an entry cannot be added here without a capture. Anything
# outside it (a default-value item: the GroupPolicy module cannot author one)
# is refused by the native backup export and the publication planner
# (`gpp_registry_unmeasured_shapes`, WI-075).
_GPP_REGISTRY_KEY_ONLY = "key-only"
_MEASURED_GPP_REGISTRY_SHAPES: frozenset[tuple[str, str]] = frozenset(
    (action, shape)
    for action in ("create", "replace", "update", "delete")
    for shape in (
        "REG_SZ", "REG_EXPAND_SZ", "REG_BINARY", "REG_DWORD", "REG_QWORD", "REG_MULTI_SZ",
        _GPP_REGISTRY_KEY_ONLY,
    )
)
_GPP_REGISTRY_HEX_WIDTH = {"REG_DWORD": 8, "REG_QWORD": 16}

_REGISTRY_HIVES = frozenset({
    "HKEY_LOCAL_MACHINE", "HKEY_CLASSES_ROOT", "HKEY_CURRENT_USER",
    "HKEY_CURRENT_CONFIG", "HKEY_USERS",
})


class GppError(ValueError):
    """Malformed or unsupported GPP content."""


_MAX_GPP_XML_SIZE = 10 * 1024 * 1024
_MAX_GPP_XML_DEPTH = 100
_MAX_GPP_XML_ELEMENTS = 100000
_MAX_GPP_XML_TEXT_LENGTH = 1024 * 1024
_MAX_GPP_XML_ATTR_LENGTH = 4096


def _bounded_parse(data: bytes) -> ET.Element:
    return parse_xml_bounded(
        data,
        max_size=_MAX_GPP_XML_SIZE,
        max_elements=_MAX_GPP_XML_ELEMENTS,
        max_depth=_MAX_GPP_XML_DEPTH,
        max_text_length=_MAX_GPP_XML_TEXT_LENGTH,
        max_attr_length=_MAX_GPP_XML_ATTR_LENGTH,
        error_class=GppError,
    )


def _capture_unknown_attrs(
    elem: ET.Element, known: frozenset[str]
) -> tuple[tuple[str, str], ...]:
    """Return attributes whose local name is not in the known set."""
    return tuple(
        (name, value)
        for name, value in elem.attrib.items()
        if _local_name(name) not in known
    )


def _capture_unknown_children(
    elem: ET.Element, known: frozenset[str]
) -> tuple[str, ...]:
    """Return raw XML of child elements whose local name is not in the known set."""
    return tuple(
        ET.tostring(child, encoding="unicode")
        for child in elem
        if _local_name(child.tag) not in known
    )


def _validate_unknown_attrs(
    unknown: tuple[tuple[str, str], ...],
    reserved: frozenset[str],
    context: str,
) -> None:
    """Raise GppError if any unknown attr local name collides with a reserved name."""
    for name, _value in unknown:
        if _local_name(name) in reserved:
            raise GppError(
                f"Unknown attribute {name!r} in {context} collides with a "
                f"reserved typed attribute name"
            )


def _validate_unknown_children(
    unknown: tuple[str, ...],
    reserved: frozenset[str],
    context: str,
) -> None:
    """Raise GppError if any unknown child local name collides with a reserved name."""
    for raw in unknown:
        data = raw.encode("utf-8")
        if len(data) > _MAX_GPP_XML_SIZE:
            raise GppError(
                f"Unknown child XML exceeds {_MAX_GPP_XML_SIZE} bytes in {context}"
            )
        if b"<!ENTITY" in data:
            raise GppError(f"XML entity declarations not allowed in {context}")
        try:
            child = _bounded_parse(data)
        except GppError as error:
            raise GppError(
                f"Malformed unknown child XML in {context}: {error}"
            ) from error
        if _local_name(child.tag) in reserved:
            raise GppError(
                f"Unknown child <{_local_name(child.tag)}> in {context} "
                f"collides with a reserved element name"
            )


def _apply_unknown_attrs(elem: ET.Element, unknown: tuple[tuple[str, str], ...]) -> None:
    for name, value in unknown:
        elem.set(name, value)


def _append_unknown_children(
    elem: ET.Element, unknown: tuple[str, ...], context: str
) -> None:
    for raw in unknown:
        try:
            child = _bounded_parse(raw.encode("utf-8"))
            elem.append(child)
        except GppError as error:
            raise GppError(
                f"Corrupted unknown XML in {context}: {error}"
            ) from error


def _action_to_code(action: GppAction) -> str:
    match action:
        case "add":
            return "C"
        case "replace":
            return "R"
        case "update":
            return "U"
        case "remove":
            return "D"
        case _:
            assert_never(action)


def _code_to_action(code: str) -> GppAction:
    if code not in _CODE_TO_ACTION:
        raise GppError(f"Unsupported GPP action code: {code!r}")
    return _CODE_TO_ACTION[code]


def _registry_action_to_code(action: GppRegistryAction) -> str:
    match action:
        case "create":
            return "C"
        case "replace":
            return "R"
        case "update":
            return "U"
        case "delete":
            return "D"
        case _:
            assert_never(action)


def _code_to_registry_action(code: str) -> GppRegistryAction:
    if code not in _CODE_TO_REGISTRY_ACTION:
        raise GppError(f"Unsupported GPP registry action code: {code!r}")
    return _CODE_TO_REGISTRY_ACTION[code]


def _registry_action_image(action: GppRegistryAction) -> str | None:
    """The ``image`` GPMC writes on <Registry> for *action*, or ``None``.

    ``image`` is the editor's icon index, measured on the Registry family
    itself: ``C``→0, ``R``→1, ``U``→2 (WI01A-Registry-GPMC) and ``D``→3
    (WI01A-RegistryShapes-GPMC, the revision-2 capture's DeleteMe item). Every
    action has one, so ``None`` is never returned today; the optional return
    stays so an unmeasured action added later cannot borrow a value.
    """
    match action:
        case "create":
            return "0"
        case "replace":
            return "1"
        case "update":
            return "2"
        case "delete":
            return "3"
        case _:
            assert_never(action)


def _native_uid(uid: str) -> str:
    """Render an item uid the way GPMC writes it: braced, upper-case.

    A non-GUID uid (imported from a foreign writer, say) is kept verbatim:
    rewriting it would change an identity Studio does not own.
    """
    try:
        parsed = uuid.UUID(uid)
    except ValueError:
        return uid
    return "{" + str(parsed).upper() + "}"


def _validate_gpp_action(value: str) -> GppAction:
    if value in ("add", "replace", "remove", "update"):
        return value  # type: ignore[return-value]
    raise GppError(f"Invalid GPP action: {value!r}")


def _validate_gpp_registry_action(value: str) -> GppRegistryAction:
    if value in ("create", "replace", "update", "delete"):
        return value  # type: ignore[return-value]
    raise GppError(f"Invalid GPP registry action: {value!r}")


def _normalize_hive(hive: str) -> str:
    """Normalize a hive string, accepting common abbreviations."""
    mapping = {
        "HKLM": "HKEY_LOCAL_MACHINE",
        "HKCU": "HKEY_CURRENT_USER",
        "HKCR": "HKEY_CLASSES_ROOT",
        "HKCC": "HKEY_CURRENT_CONFIG",
        "HKU": "HKEY_USERS",
    }
    upper = hive.upper()
    if upper in mapping:
        return mapping[upper]
    if upper in _REGISTRY_HIVES:
        return upper
    raise GppError(f"Invalid registry hive: {hive!r}")


@dataclass(frozen=True, slots=True)
class GppGroupMember:
    sid: str
    name: str = ""
    action: GppAction = "add"
    id: str = ""
    unknown_attrs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class GppCommonOptions:
    apply_once: bool = False
    remove_when_unapplied: bool = False
    user_security_context: bool = False
    disabled: bool = False
    stop_on_error: bool = False
    #: The imported ``FilterRunOnce@id`` (WI-080). Clients record an apply-once
    #: item as applied BY THIS ID, so a new id makes every client apply it
    #: again. Kept as imported and written whenever ``apply_once`` is set; an
    #: item that never had one gets the deterministic id `_append_item_filters`
    #: derives. It stays on the model while ``apply_once`` is off, so turning
    #: apply-once off and on again restores the same identity rather than
    #: re-arming the item on every client (GPMC's own behaviour for that toggle
    #: is unmeasured). Outside ==, like ``document_position``: it is identity
    #: bookkeeping that no edit can change.
    run_once_id: str = field(default="", compare=False)


@dataclass(frozen=True, slots=True)
class GppGroup:
    name: str
    sid: str = ""
    action: GppAction = "update"
    members: tuple[GppGroupMember, ...] = field(default_factory=tuple)
    description: str = ""
    remove_all_users: bool = False
    remove_all_groups: bool = False
    common: GppCommonOptions = field(default_factory=GppCommonOptions)
    ilt_filter: IltFilter | None = None
    id: str = ""
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_props_attrs: tuple[tuple[str, str], ...] = ()
    unknown_props_children: tuple[str, ...] = ()
    unknown_children: tuple[str, ...] = ()
    #: Slot in the source document's root, set on import (WI-073). See
    #: :func:`gpp_document_order`. ``None``: no slot. Outside ==; diff and hash compare the order.
    document_position: int | None = field(default=None, compare=False)
    #: The item's element exactly as imported, or ``""`` (WI-080). The writer
    #: reconciles it with the model (``gpp_native.merge_native``): what the
    #: model types wins, everything else is written as imported. Outside ==;
    #: diff and hash compare it.
    native_xml: str = field(default="", compare=False)


@dataclass(frozen=True, slots=True)
class GppRegistryValue:
    name: str
    value: str | int | list[str]
    registry_type: str = "REG_SZ"
    action: GppRegistryAction = "create"
    default: bool = False
    id: str = ""
    unknown_attrs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class GppRegistry:
    key: str
    hive: str = "HKEY_LOCAL_MACHINE"
    value: GppRegistryValue = field(
        default_factory=lambda: GppRegistryValue(name="", value="")
    )
    action: GppAction = "update"
    uid: str = ""
    id: str = ""
    common: GppCommonOptions = field(default_factory=GppCommonOptions)
    ilt_filter: IltFilter | None = None
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_props_children: tuple[str, ...] = ()
    unknown_children: tuple[str, ...] = ()
    #: Slot in the source document's root, set on import (WI-072). See
    #: :func:`gpp_document_order`. ``None``: no slot. Outside ==; diff and hash compare the order.
    document_position: int | None = field(default=None, compare=False)
    #: The item's element exactly as imported, or ``""`` (WI-080); see
    #: `GppGroup.native_xml`. Only an item imported from a <Registry> with one
    #: <Properties> has one.
    native_xml: str = field(default="", compare=False)


@dataclass(frozen=True, slots=True)
class GppCollection:
    """Typed GPP preference items with optional ephemeral source bytes.

    ``source_files`` holds the original XML bytes from
    :func:`parse_gpp_collection` so that :func:`serialize_gpp` can return
    them verbatim when no edits have been made (the D8 no-edit round-trip
    preservation contract).  This field is **ephemeral**: it is excluded
    from :func:`gpp_collection_to_dict` and therefore never persisted to
    the workspace.  After a persist/reload cycle (via
    :func:`gpp_collection_from_dict`), ``source_files`` is always empty
    and serialization reconstructs XML from the typed model.

    Any code path that mutates items on a collection that still carries
    ``source_files`` must call :func:`mark_edited` first; otherwise
    :func:`serialize_gpp` would return stale bytes that do not reflect
    the mutation.

    Document order (WI-072/073) is NOT ephemeral: each typed item's
    ``document_position`` and ``root_unknown_positions`` are persisted, so a
    reloaded collection writes its files in the imported order with the
    retained root content in place. See "Document order" below.

    Nor is each imported item's own element (WI-080): ``native_xml`` on the
    item and ``common.run_once_id`` are persisted, so a reloaded collection
    writes every item Windows wrote as Windows wrote it -- the ``Properties``
    attributes the model does not type, the attribute order, the values the
    writer would normalise and the ``FilterRunOnce`` id -- and an edited item
    with what the edit changed. See "Retained native items" below.
    """

    scope: GppScope
    groups: tuple[GppGroup, ...] = field(default_factory=tuple)
    registry: tuple[GppRegistry, ...] = field(default_factory=tuple)
    groups_unknown_attrs: tuple[tuple[str, str], ...] = ()
    groups_unknown_children: tuple[str, ...] = ()
    registry_unknown_attrs: tuple[tuple[str, str], ...] = ()
    registry_unknown_children: tuple[str, ...] = ()
    # Low-artifact adapter batches (Plan 024 WP-2).
    environment: tuple[GppEnvironment, ...] = field(default_factory=tuple)
    environment_unknown_attrs: tuple[tuple[str, str], ...] = ()
    environment_unknown_children: tuple[str, ...] = ()
    ini_files: tuple[GppIniFile, ...] = field(default_factory=tuple)
    ini_files_unknown_attrs: tuple[tuple[str, str], ...] = ()
    ini_files_unknown_children: tuple[str, ...] = ()
    regional_options: tuple[GppRegionalOptions, ...] = field(default_factory=tuple)
    regional_options_unknown_attrs: tuple[tuple[str, str], ...] = ()
    regional_options_unknown_children: tuple[str, ...] = ()
    power_options: tuple[GppPowerOptions, ...] = field(default_factory=tuple)
    power_options_unknown_attrs: tuple[tuple[str, str], ...] = ()
    power_options_unknown_children: tuple[str, ...] = ()
    devices: tuple[GppDevice, ...] = field(default_factory=tuple)
    devices_unknown_attrs: tuple[tuple[str, str], ...] = ()
    devices_unknown_children: tuple[str, ...] = ()
    folder_options: tuple[GppFolderOptions, ...] = field(default_factory=tuple)
    folder_options_unknown_attrs: tuple[tuple[str, str], ...] = ()
    folder_options_unknown_children: tuple[str, ...] = ()
    data_sources: tuple[GppDataSource, ...] = field(default_factory=tuple)
    data_sources_unknown_attrs: tuple[tuple[str, str], ...] = ()
    data_sources_unknown_children: tuple[str, ...] = ()
    # Plan 024 WP-3 resource adapter batch.
    drives: tuple[GppDrive, ...] = field(default_factory=tuple)
    drives_unknown_attrs: tuple[tuple[str, str], ...] = ()
    drives_unknown_children: tuple[str, ...] = ()
    files: tuple[GppFile, ...] = field(default_factory=tuple)
    files_unknown_attrs: tuple[tuple[str, str], ...] = ()
    files_unknown_children: tuple[str, ...] = ()
    folders: tuple[GppFolder, ...] = field(default_factory=tuple)
    folders_unknown_attrs: tuple[tuple[str, str], ...] = ()
    folders_unknown_children: tuple[str, ...] = ()
    network_shares: tuple[GppNetworkShare, ...] = field(default_factory=tuple)
    network_shares_unknown_attrs: tuple[tuple[str, str], ...] = ()
    network_shares_unknown_children: tuple[str, ...] = ()
    printers: tuple[GppPrinter, ...] = field(default_factory=tuple)
    printers_unknown_attrs: tuple[tuple[str, str], ...] = ()
    printers_unknown_children: tuple[str, ...] = ()
    shortcuts: tuple[GppShortcut, ...] = field(default_factory=tuple)
    shortcuts_unknown_attrs: tuple[tuple[str, str], ...] = ()
    shortcuts_unknown_children: tuple[str, ...] = ()
    applications: tuple[GppApplication, ...] = field(default_factory=tuple)
    applications_unknown_attrs: tuple[tuple[str, str], ...] = ()
    applications_unknown_children: tuple[str, ...] = ()
    # Privileged execution adapter batch (Plan 024 WP-4).
    services: tuple[GppService, ...] = field(default_factory=tuple)
    services_unknown_attrs: tuple[tuple[str, str], ...] = ()
    services_unknown_children: tuple[str, ...] = ()
    local_users: tuple[GppLocalUser, ...] = field(default_factory=tuple)
    local_users_unknown_attrs: tuple[tuple[str, str], ...] = ()
    local_users_unknown_children: tuple[str, ...] = ()
    local_groups: tuple[GppLocalGroup, ...] = field(default_factory=tuple)
    local_groups_unknown_attrs: tuple[tuple[str, str], ...] = ()
    local_groups_unknown_children: tuple[str, ...] = ()
    scheduled_tasks: tuple[GppScheduledTask, ...] = field(default_factory=tuple)
    scheduled_tasks_unknown_attrs: tuple[tuple[str, str], ...] = ()
    scheduled_tasks_unknown_children: tuple[str, ...] = ()
    immediate_tasks: tuple[GppImmediateTask, ...] = field(default_factory=tuple)
    immediate_tasks_unknown_attrs: tuple[tuple[str, str], ...] = ()
    immediate_tasks_unknown_children: tuple[str, ...] = ()
    #: Where each retained root unknown child sat in its source document, as
    #: ``(family, positions)`` with ``positions`` parallel to
    #: ``<family>_unknown_children`` (WI-072). Families: ``groups``,
    #: ``registry`` and every adapter key. A family absent here, or whose
    #: positions no longer match its unknown children one for one, has its
    #: unknown children written where Studio always put them (after the typed
    #: items; in Groups.xml, after the groups). Persisted.
    root_unknown_positions: tuple[tuple[str, tuple[int, ...]], ...] = field(
        default=(), compare=False
    )
    source_files: tuple[tuple[str, bytes], ...] = ()


def _xml_declaration(data: bytes) -> bytes:
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + data


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _apply_common_options(item: ET.Element, common: GppCommonOptions) -> None:
    """Write MS-GPPREF common options on the inner preference item.

    ``apply_once`` is not an XML attribute. GPMC represents it as a
    ``FilterRunOnce`` item-level-targeting predicate, which is appended by
    :func:`_append_item_filters`.
    """
    item.set("removePolicy", "1" if common.remove_when_unapplied else "0")
    item.set("userContext", "1" if common.user_security_context else "0")
    item.set("disabled", "1" if common.disabled else "0")
    # MS-GPPREF: bypassErrors="1" continues after an error; "0" stops.
    item.set("bypassErrors", "0" if common.stop_on_error else "1")


def _parse_common_options(
    source: ET.Element,
    legacy_source: ET.Element | None = None,
    *,
    apply_once: bool = False,
    run_once_id: str = "",
) -> GppCommonOptions:
    """Parse common options from an item, accepting old Studio placement.

    Before Plan 033, Studio incorrectly wrote these attributes on
    ``Properties``. The fallback keeps those artifacts readable without
    repeating the invalid placement on export.
    """
    def value(name: str, default: str) -> str:
        if name in source.attrib:
            return source.attrib[name]
        if legacy_source is not None and name in legacy_source.attrib:
            return legacy_source.attrib[name]
        return default

    return GppCommonOptions(
        apply_once=apply_once or value("applyOnce", "0") == "1",
        remove_when_unapplied=value("removePolicy", "0") == "1",
        user_security_context=value("userContext", "0") == "1",
        disabled=value("disabled", "0") == "1",
        # bypassErrors="0" means stop on error; absent defaults to "0" (stop).
        stop_on_error=value("bypassErrors", "0") == "0",
        run_once_id=run_once_id,
    )


def _parse_item_filters(
    item: ET.Element,
) -> tuple[IltFilter | None, bool, str]:
    """Parse ILT while promoting ``FilterRunOnce`` to a common option.

    Returns the filter, whether the item applies once, and the imported
    ``FilterRunOnce@id`` (the first one's; ``""`` when there is none), which
    Studio keeps and writes back (WI-080).
    """
    filters = _find_local(item, "Filters")
    if filters is None:
        return None, False, ""

    remaining = ET.Element(_ns("Filters"))
    apply_once = False
    run_once_id = ""
    for child in filters:
        if _local_name(child.tag) == "FilterRunOnce":
            if not apply_once:
                run_once_id = child.get("id", "")
            apply_once = True
        else:
            remaining.append(deepcopy(child))
    if not list(remaining):
        return None, apply_once, run_once_id
    return parse_ilt(remaining), apply_once, run_once_id


def _append_item_filters(
    item: ET.Element,
    ilt_filter: IltFilter | None,
    common: GppCommonOptions,
    identity_seed: str,
) -> None:
    """Append ILT plus GPMC-compatible apply-once targeting.

    ``FilterRunOnce@id`` is the imported id when the item has one
    (``common.run_once_id``, WI-080): clients track an apply-once item by that
    id, and a new one makes every client apply the item again. Only an item
    that never had an id gets one, derived from *identity_seed*.
    """
    if ilt_filter is None and not common.apply_once:
        return
    filters = (
        serialize_ilt(ilt_filter)
        if ilt_filter is not None
        else ET.Element(_ns("Filters"))
    )
    if common.apply_once:
        run_once = ET.Element(_ns("FilterRunOnce"))
        run_once.set("hidden", "1")
        run_once.set("not", "0")
        run_once.set("bool", "AND")
        if common.run_once_id:
            run_once.set("id", common.run_once_id)
        else:
            run_once_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"gpo-studio/gpp/run-once/{identity_seed}",
            )
            run_once.set("id", "{" + str(run_once_id).upper() + "}")
        filters.append(run_once)
    item.append(filters)


def _serialize_member(member: GppGroupMember) -> ET.Element:
    elem = ET.Element(_ns("Member"))
    elem.set("name", member.name)
    elem.set("sid", member.sid)
    code = _MEMBER_ACTION_TO_CODE.get(member.action)
    if code is None:
        raise GppError(f"Unsupported member action: {member.action!r}")
    elem.set("action", code)
    _apply_unknown_attrs(elem, member.unknown_attrs)
    return elem


def _serialize_group(group: GppGroup) -> ET.Element:
    elem = ET.Element(_ns("Group"))
    elem.set("clsid", _GROUP_CLSID)
    elem.set("name", group.name)
    _apply_common_options(elem, group.common)
    _apply_unknown_attrs(elem, group.unknown_attrs)
    props = ET.SubElement(elem, _ns("Properties"))
    props.set("action", _action_to_code(group.action))
    props.set("groupName", group.name)
    if group.sid:
        props.set("groupSid", group.sid)
    if group.description:
        props.set("description", group.description)
    props.set("deleteAllUsers", "1" if group.remove_all_users else "0")
    props.set("deleteAllGroups", "1" if group.remove_all_groups else "0")
    _apply_unknown_attrs(props, group.unknown_props_attrs)
    if group.members:
        members_elem = ET.SubElement(props, _ns("Members"))
        for member in group.members:
            members_elem.append(_serialize_member(member))
    _append_unknown_children(
        props, group.unknown_props_children, f"group {group.name!r} properties"
    )
    _append_item_filters(
        elem,
        group.ilt_filter,
        group.common,
        group.id or group.name,
    )
    _append_unknown_children(elem, group.unknown_children, f"group {group.name!r}")
    return elem


def serialize_gpp_groups(collection: GppCollection) -> bytes:
    """Serialize Groups.xml from a GppCollection to GPP XML bytes.

    The whole file, exactly as :func:`serialize_gpp` writes it: groups, the
    local users that share the root, and the root's retained content, in
    document order. There is deliberately no way to write one family of a
    shared root on its own (review N3): that is how WI-072 dropped content.
    """
    return _serialize_gpp_file(collection, _GROUPS_FILE, _gpp_file_families()[_GROUPS_FILE])


def _registry_wire_value(value: GppRegistryValue) -> str:
    """Encode a typed registry value as GPMC writes ``Properties@value``.

    Measured (WI01A-Registry-GPMC): REG_DWORD is eight upper-case hex digits
    (42 → ``0000002A``), REG_QWORD sixteen (2**32 → ``0000000100000000``), and
    REG_MULTI_SZ is its strings joined by single spaces -- lossy, which is why
    the writer also emits the authoritative <Values> list. Before batch 2
    Studio wrote decimal and ``;``-joined strings, a form no capture backs.
    REG_BINARY is its bytes as upper-case hex with no separators (bytes
    CA FE 00 01 → ``CAFE0001``, WI01A-RegistryShapes-GPMC); Studio's model
    allows spaces between bytes, which are dropped.
    """
    raw = value.value
    width = _GPP_REGISTRY_HEX_WIDTH.get(value.registry_type)
    if width is not None:
        if isinstance(raw, list):
            raise GppError(f"{value.registry_type} value must be an integer, got a list")
        try:
            number = coerce_dword_qword(raw, value.registry_type)
        except (TypeError, ValueError) as error:
            raise GppError(f"Invalid {value.registry_type} value {raw!r}: {error}") from error
        return f"{number:0{width}X}"
    if value.registry_type == "REG_MULTI_SZ":
        if not isinstance(raw, list):
            raise GppError("REG_MULTI_SZ value must be a list of strings")
        return " ".join(raw)
    if value.registry_type == "REG_BINARY":
        if not isinstance(raw, str):
            raise GppError("REG_BINARY value must be a hexadecimal string")
        try:
            return bytes.fromhex(raw.replace(" ", "")).hex().upper()
        except ValueError as error:
            raise GppError(f"Invalid REG_BINARY value {raw!r}: {error}") from error
    if raw == [] and not value.name and not value.default:
        # A key-only item's empty value: the model (and the API and validation)
        # accepts "" or [] for it; the wire form is value="" (measured), so both
        # write the same thing (review: [] used to raise here after the item
        # had been committed, and every later read of the GPO failed).
        return ""
    if isinstance(raw, list):
        # Only REG_MULTI_SZ holds a list. Joining one into a REG_SZ wrote
        # "a;b", which reads back as the single string "a;b" (review).
        raise GppError(f"{value.registry_type or 'An untyped'} value cannot be a list")
    return str(raw)


def _serialize_registry(reg: GppRegistry) -> ET.Element:
    """Serialize a GppRegistry to a single <Registry> XML element.

    Invariant: one <Registry> element = one domain object with exactly one
    value, one UID, one ILT filter, and one set of element metadata.

    Attribute set and order follow the native capture
    (tests/fixtures/native-gpp-registry-gpmc/WI01A-Registry-GPMC): ``clsid name status
    image changed uid`` and the common options on <Registry>; ``action
    displayDecimal default hive key name type value`` on <Properties>. The
    item ``name`` (and ``status``) is the VALUE name, as GPMC writes it; for a
    key-only item it is the key (measured, WI01A-RegistryShapes-GPMC), whose
    <Properties> carry ``name=""``, ``type="REG_SZ"`` and ``value=""``. A
    default-value item uses the key too, unmeasured -- the module cannot author
    one -- and is refused for native output (`gpp_registry_unmeasured_shapes`).
    """
    hive = _normalize_hive(reg.hive)
    value = reg.value
    elem = ET.Element(_ns("Registry"))
    elem.set("clsid", _REGISTRY_CLSID)
    display_name = value.name or reg.key
    elem.set("name", display_name)
    elem.set("status", display_name)
    image = _registry_action_image(value.action)
    if image is not None:
        elem.set("image", image)
    # Derived attributes are regenerated; a stale copy in the unknown bag
    # (stored before batch 2, when they were not typed) must not override them.
    unknown_attrs = tuple(
        (name, text)
        for name, text in reg.unknown_attrs
        if _local_name(name) not in _REGISTRY_DERIVED_ATTRS
    )
    _apply_unknown_attrs(elem, tuple(p for p in unknown_attrs if p[0] == "changed"))
    if reg.uid:
        elem.set("uid", _native_uid(reg.uid))
    _apply_common_options(elem, reg.common)
    _apply_unknown_attrs(elem, tuple(p for p in unknown_attrs if p[0] != "changed"))
    props = ET.SubElement(elem, _ns("Properties"))
    props.set("action", _registry_action_to_code(value.action))
    # ``displayDecimal`` is the editor's DWORD display radix, not the encoding:
    # the value is hex either way. An imported one is preserved in place;
    # otherwise GPMC's own default ("0") is written.
    display_decimal = next(
        (text for name, text in value.unknown_attrs if name == "displayDecimal"), "0"
    )
    props.set("displayDecimal", display_decimal)
    props.set("default", "1" if value.default else "0")
    props.set("hive", hive)
    props.set("key", reg.key)
    props.set("name", value.name)
    # A key-only item is typed REG_SZ on the wire (measured); Studio's model
    # also allows the empty type for it.
    is_key_only = not value.name and not value.default
    props.set("type", value.registry_type or ("REG_SZ" if is_key_only else ""))
    props.set("value", _registry_wire_value(value))
    _apply_unknown_attrs(
        props, tuple(p for p in value.unknown_attrs if p[0] != "displayDecimal")
    )
    if value.registry_type == "REG_MULTI_SZ" and isinstance(value.value, list):
        values_elem = ET.SubElement(props, _ns("Values"))
        for item in value.value:
            ET.SubElement(values_elem, _ns("Value")).text = item
    _append_unknown_children(
        props, reg.unknown_props_children, f"registry {reg.key!r} properties"
    )
    _append_item_filters(
        elem,
        reg.ilt_filter,
        reg.common,
        reg.uid or reg.id or f"{hive}/{reg.key}/{value.name}",
    )
    _append_unknown_children(elem, reg.unknown_children, f"registry {reg.key!r}")
    return elem


def serialize_gpp_registry(collection: GppCollection) -> bytes:
    """Serialize Registry.xml from a GppCollection, as :func:`serialize_gpp` writes it."""
    return _serialize_gpp_file(collection, _REGISTRY_FILE, _gpp_file_families()[_REGISTRY_FILE])


# ---------------------------------------------------------------------------
# Document order (WI-072, WI-073)
# ---------------------------------------------------------------------------
#
# GPP processes a file's items in document order. Two files hold more than one
# typed family under one root -- Groups.xml (<Group>, <User>) and
# ScheduledTasks.xml (<Task>/<TaskV2>, <ImmediateTaskV2>) -- and the model keeps
# each family in its own list. Any root may also hold children the model does
# not type, retained verbatim as that family's root unknown children (Power
# Options' <GlobalPowerOptionsV2>, for one).
#
# Order is kept as SLOTS rather than one merged list, so the per-family fields,
# the API and every existing caller stay as they are:
#
# * Import records each typed item's index among its root's children as its
#   ``document_position``, and each root unknown child's index in
#   ``GppCollection.root_unknown_positions``.
# * Within a family the LIST is authoritative, always: no recorded position can
#   reorder two items of one family against their list order. The family's
#   recorded positions are the slots it holds in the document, and its
#   positioned items fill them in list order. Reordering a family therefore
#   swaps its items between its own slots without moving any past another
#   family's items, and deleting an item frees its slot. Slots may tie: a
#   legacy multi-value <Registry> expands into one item per <Properties>, all
#   holding that element's slot, and list order then decides between them.
# * An item without a position that sits between positioned items of its
#   family is written straight after its list predecessor (straight before the
#   first positioned item when it leads the list).
# * Items without a position after their family's last positioned item -- an
#   item added through the API, or anything stored before positions existed --
#   are written after every positioned entry, in the order Studio always wrote
#   them: each family in the file's family order, then the root unknown
#   children. In Groups.xml the root unknowns come straight after the groups,
#   before the users, as 1.0 wrote them.
#
# With no recorded position anywhere this is exactly the order Studio wrote
# before 1.1, so a collection built by an existing caller, or loaded from an
# older workspace, is written as it always was -- except that adapter root
# unknowns, which used to be dropped (WI-072), are now written.

_GROUPS_FILE = "Groups/Groups.xml"
_REGISTRY_FILE = "Registry/Registry.xml"

#: A token naming one root child: ``(family, index)`` for the index-th item of
#: a typed family, or ``("unknown", index)`` for the file's index-th retained
#: root unknown child (after de-duplication, see `_file_unknown_children`).
DocumentToken = tuple[str, int]
_UNKNOWN = "unknown"
#: ``(0, slot, list index, family rank)`` for an entry placed by a slot, and
#: ``(1, family rank, list index, 0)`` for one written after every slotted entry.
#: Within a family the list index is strictly increasing along the list and the
#: slot never decreases, so the list order always wins, ties included.
_SortKey = tuple[int, int, int, int]

#: Positions are indices among one root's element children, and the bounded XML
#: parser refuses a document with more elements than this, so no imported
#: position can reach it. A larger stored value cannot have come from an import
#: and is refused rather than honoured.
MAX_DOCUMENT_POSITION = _MAX_GPP_XML_ELEMENTS - 1


def _gpp_file_families() -> dict[str, tuple[str, ...]]:
    """Each GPP file and the typed families its root holds, in writing order."""
    from .gpp_adapters import ADAPTER_FILE_PATHS, ADAPTER_KEYS

    families: dict[str, list[str]] = {_GROUPS_FILE: ["groups"], _REGISTRY_FILE: ["registry"]}
    for key in ADAPTER_KEYS:
        families.setdefault(ADAPTER_FILE_PATHS[key], []).append(key)
    return {path: tuple(keys) for path, keys in families.items()}


def _family_items(collection: GppCollection, key: str) -> tuple[Any, ...]:
    items: tuple[Any, ...] = getattr(collection, key)
    return items


def _family_has_content(collection: GppCollection, key: str) -> bool:
    return bool(
        _family_items(collection, key)
        or getattr(collection, f"{key}_unknown_attrs")
        or getattr(collection, f"{key}_unknown_children")
    )


def _checked_position(value: object, context: str) -> int | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= MAX_DOCUMENT_POSITION
    ):
        shown = repr(value)
        shown = shown if len(shown) <= 24 else shown[:21] + "..."
        raise GppError(
            f"Invalid document position {shown} in {context} "
            f"(an integer from 0 to {MAX_DOCUMENT_POSITION})"
        )
    return value


@dataclass(frozen=True, slots=True)
class _RootUnknowns:
    """A file root's retained attributes and children, read once."""

    attrs: tuple[tuple[str, str], ...]
    children: tuple[str, ...]
    #: Parallel to ``children``, or ``None`` when no usable position is recorded.
    positions: tuple[int, ...] | None


def _root_unknowns(
    collection: GppCollection, path: str, families: tuple[str, ...]
) -> _RootUnknowns:
    """The root's retained content, refusing copies that disagree (review N5).

    Families sharing a root (Groups.xml, ScheduledTasks.xml) each capture the
    root's unknown attributes and children on import, so the model holds one
    copy per family. Every copy that is not empty must be identical -- and so
    must their recorded positions, where both record them -- because the file
    has one root: two different copies are not two halves of it, and writing
    their union (or picking one) would invent or drop content silently. An
    import always writes identical copies; a caller may fill one family's copy
    and leave the other empty. This is checked when writing and when loading a
    stored collection, so neither path can reach the file with a conflict.
    """
    recorded = dict(collection.root_unknown_positions)
    attrs: tuple[tuple[str, str], ...] = ()
    children: tuple[str, ...] = ()
    positions: tuple[int, ...] | None = None
    for key in families:
        family_attrs: tuple[tuple[str, str], ...] = getattr(collection, f"{key}_unknown_attrs")
        family_children: tuple[str, ...] = getattr(collection, f"{key}_unknown_children")
        if family_attrs:
            if attrs and family_attrs != attrs:
                raise GppError(
                    f"{path}: the families sharing this root hold different retained "
                    f"root attributes ({families[0]} and {key} copies disagree)"
                )
            attrs = family_attrs
        if not family_children:
            continue
        if children and family_children != children:
            raise GppError(
                f"{path}: the families sharing this root hold different retained "
                f"root children ({families[0]} and {key} copies disagree)"
            )
        children = family_children
        family_positions = recorded.get(key)
        if family_positions is None or len(family_positions) != len(family_children):
            continue
        checked = tuple(
            _checked_position(slot, f"{key} root unknowns") for slot in family_positions
        )
        usable = tuple(slot for slot in checked if slot is not None)
        if positions is not None and usable != positions:
            raise GppError(
                f"{path}: the families sharing this root record its retained root "
                f"children at different positions ({list(positions)} and {list(usable)})"
            )
        positions = usable
    return _RootUnknowns(attrs=attrs, children=children, positions=positions)


def _file_unknown_children(
    collection: GppCollection, path: str, families: tuple[str, ...]
) -> list[tuple[str, int | None]]:
    """The file's root unknown children, once, each with its recorded position."""
    unknowns = _root_unknowns(collection, path, families)
    if unknowns.positions is None:
        return [(raw, None) for raw in unknowns.children]
    return list(zip(unknowns.children, unknowns.positions, strict=True))


def _family_sort_keys(
    items: tuple[Any, ...], rank: int, key: str
) -> list[_SortKey]:
    """Sort keys for one family's items, per the rules above."""
    positions = [
        _checked_position(getattr(item, "document_position", None), f"{key} item")
        for item in items
    ]
    positioned = [index for index, position in enumerate(positions) if position is not None]
    if not positioned:
        return [(1, rank, index, 0) for index in range(len(items))]
    slots = sorted(position for position in positions if position is not None)
    effective = dict(zip(positioned, slots, strict=True))
    last = positioned[-1]
    keys: list[_SortKey] = []
    # A leading unpositioned item takes the first slot; one between positioned
    # items takes its predecessor's. The list index breaks every tie, so it
    # lands where the list puts it even when slots repeat (review P2).
    anchor = slots[0]
    for index in range(len(items)):
        if index in effective:
            anchor = effective[index]
        elif index > last:
            keys.append((1, rank, index, 0))
            continue
        keys.append((0, anchor, index, rank))
    return keys


def _ordered_tokens(
    collection: GppCollection,
    path: str,
    families: tuple[str, ...],
    *,
    recorded: bool = True,
) -> list[DocumentToken]:
    """The root children of *path*, in the order they are written.

    ``recorded=False`` ignores every recorded position, giving the order Studio
    wrote before positions existed.
    """
    # Unpositioned entries keep the pre-1.1 order: families in file order, the
    # root unknowns last -- except in Groups.xml, where 1.0 wrote the groups'
    # root unknowns between the groups and the users.
    buckets: list[str] = list(families)
    buckets.insert(1 if path == _GROUPS_FILE else len(buckets), _UNKNOWN)
    rank = {bucket: index for index, bucket in enumerate(buckets)}
    entries: list[tuple[_SortKey, DocumentToken]] = []
    for key in families:
        items = _family_items(collection, key)
        if not recorded:
            items = tuple(replace(item, document_position=None) for item in items)
        for index, sort_key in enumerate(_family_sort_keys(items, rank[key], key)):
            entries.append((sort_key, (key, index)))
    for index, (_raw, position) in enumerate(_file_unknown_children(collection, path, families)):
        unknown_key: _SortKey = (
            (1, rank[_UNKNOWN], index, 0)
            if position is None or not recorded
            else (0, position, index, rank[_UNKNOWN])
        )
        entries.append((unknown_key, (_UNKNOWN, index)))
    entries.sort(key=lambda entry: entry[0])
    return [token for _key, token in entries]


def _gpp_files_with_content(collection: GppCollection) -> list[tuple[str, tuple[str, ...]]]:
    """The files :func:`serialize_gpp` writes, in the order it always wrote them."""
    from .gpp_adapters import ADAPTER_FILE_PATHS, ADAPTER_KEYS

    families = _gpp_file_families()
    order: list[str] = []
    if _family_has_content(collection, "groups"):
        order.append(_GROUPS_FILE)
    if _family_has_content(collection, "registry"):
        order.append(_REGISTRY_FILE)
    for key in ADAPTER_KEYS:
        path = ADAPTER_FILE_PATHS[key]
        if path not in order and _family_has_content(collection, key):
            order.append(path)
    return [(path, families[path]) for path in order]


def gpp_document_order(
    collection: GppCollection, *, recorded: bool = True
) -> dict[str, tuple[DocumentToken, ...]]:
    """Each file :func:`serialize_gpp` writes, mapped to its root children in order.

    The order the serializer uses, as tokens (see `DocumentToken`).
    ``recorded=False`` gives the order Studio wrote before document positions
    existed, which is what an unpositioned collection still gets.
    """
    return {
        path: tuple(_ordered_tokens(collection, path, families, recorded=recorded))
        for path, families in _gpp_files_with_content(collection)
    }


def _root_identity(path: str, first_key: str) -> tuple[str, str]:
    if path == _GROUPS_FILE:
        return "Groups", _GROUPS_CLSID
    if path == _REGISTRY_FILE:
        return "RegistrySettings", _REGISTRY_SETTINGS_CLSID
    from .gpp_adapters import _ADAPTER_META

    root_tag, root_clsid, _, _ = _ADAPTER_META[first_key]
    return root_tag, root_clsid


def _family_elements(collection: GppCollection, key: str) -> list[ET.Element]:
    return [
        _written_for(key, item, collection.scope)[1]
        for item in _family_items(collection, key)
    ]


def _written_for(key: str, item: Any, scope: GppScope) -> tuple[ET.Element, ET.Element]:
    """``(the model's rendering, the element written)`` for one item.

    An imported item's edits to values its writer keeps in a payload (a task's
    command) are first carried into that payload (`_with_payload_edits`); then
    the item is rendered and reconciled with its retained element.
    """
    render = _item_renderer(key, scope)
    parse = _item_parser(key)
    item = _with_payload_edits(key, item, parse)
    rendered = render(item)
    return rendered, _written_element(item, rendered, render, parse)


def _with_payload_edits(key: str, item: Any, parse: Callable[[ET.Element], Any]) -> Any:
    """Carry an imported task's edited typed values into its <Task> payload.

    Only an item with a retained element has a record of what was imported,
    so only there can an edited value be told from an unset one
    (`gpp_adapters.reconcile_payload_edits`).
    """
    if key not in ("scheduled_tasks", "immediate_tasks"):
        return item
    raw: str = getattr(item, "native_xml", "")
    if not raw:
        return item
    try:
        imported = parse(_bounded_parse(raw.encode("utf-8")))
    except GppError:
        return item
    from .gpp_adapters import reconcile_payload_edits

    return reconcile_payload_edits(item, imported)


# ---------------------------------------------------------------------------
# Retained native items (WI-080)
# ---------------------------------------------------------------------------
#
# An imported item keeps its element as Windows wrote it (``native_xml``), and
# the writer reconciles it with the model: see ``gpp_native`` for the rules.
# Collections stored before WI-080 have no retained element and are written
# from the model alone, exactly as before.
#
# What a stored import does NOT get back: the whitespace between root children
# (GPMC's newline-and-tab indentation; no GPP element has mixed content), the
# XML declaration's exact bytes, and the element of a legacy multi-value
# <Registry> (one element, several items: none of them is the element). The
# root element is written as ``clsid`` then its retained attributes, in their
# imported order. tests/test_gpp_native_preservation.py holds every native
# capture to exactly that.
#
# The API never accepts ``native_xml``: the store carries an item's record
# over an edit (``store._keep_document_position``), and the writer decides,
# value by value, what the edit changed.


def item_carries_cpassword(key: str, item: Any, scope: GppScope = "computer") -> bool:
    """Whether *item*, as written, would hold a cpassword (attribute or element).

    For input that reaches the model without an XML import -- an API payload's
    unknown attributes, raw unknown children and raw filter predicates -- so
    it is refused where it arrives, not only at export (WI-080 review). An item
    the writer cannot render at all is refused elsewhere.
    """
    try:
        elem = _item_renderer(key, scope)(item)
    except ValueError:
        return False
    return has_never_retained_name(elem)


def retained_rendering(key: str, item: Any, scope: GppScope) -> str:
    """What an item's retained native element adds to what is written, or ``""``.

    ``""`` when the item has none, or when the element written for it is the
    model's own rendering anyway (an import of Studio's own export, say). Else
    the written element's XML: for an unedited GPMC import, the element as
    Windows wrote it. The canonical form hashes this rather than the raw
    ``native_xml``, so the hash moves exactly when the retained element changes
    what is written: re-importing Studio's own backup leaves a GPO's digests,
    and the backup id derived from them, as they were.

    Never raises: the hash of a GPO must not fail where its export would. A
    model the writer refuses is represented by its raw retained element.
    """
    raw: str = getattr(item, "native_xml", "")
    if not raw:
        return ""
    # Every GPO payload hashes twice, so the answer is cached. ``repr`` covers
    # every field, the ones outside == included; items are frozen.
    cache_key = (key, scope, repr(item))
    cached = _RETAINED_RENDERINGS.get(cache_key)
    if cached is not None:
        return cached
    try:
        rendered, written = _written_for(key, item, scope)
    except ValueError:  # GppError and the ILT codec's errors
        result = raw
    else:
        result = (
            "" if same_rendering(written, rendered) else ET.tostring(written, encoding="unicode")
        )
    if len(_RETAINED_RENDERINGS) >= _RETAINED_RENDERINGS_MAX:
        _RETAINED_RENDERINGS.clear()
    _RETAINED_RENDERINGS[cache_key] = result
    return result


_RETAINED_RENDERINGS: dict[tuple[str, str, str], str] = {}
_RETAINED_RENDERINGS_MAX = 8192


def _parse_single_registry(elem: ET.Element) -> GppRegistry:
    items = _parse_registry(elem)
    if len(items) != 1:
        raise GppError("a retained <Registry> element must hold exactly one <Properties>")
    return items[0]


def _item_parser(key: str) -> Callable[[ET.Element], Any]:
    """The parser that made one item of family *key* from its element."""
    if key == "groups":
        return _parse_group
    if key == "registry":
        return _parse_single_registry
    from .gpp_adapters import ITEM_PARSE_FUNCTIONS

    return ITEM_PARSE_FUNCTIONS[key]


def _item_renderer(key: str, scope: GppScope) -> Callable[[Any], ET.Element]:
    """The writer for one item of family *key* (from the model alone)."""
    if key == "groups":
        return _serialize_group
    if key == "registry":
        return _serialize_registry
    from .gpp_adapters import serialize_adapter_item

    return lambda item: serialize_adapter_item(key, item, scope)


def _written_element(
    item: Any,
    rendered: ET.Element,
    render: Callable[[Any], ET.Element],
    parse: Callable[[ET.Element], Any],
) -> ET.Element:
    """The element written for *item*: the model, reconciled with its import.

    An unchanged item is written as imported. A changed one is merged, and the
    merge must MEAN what the model means: it is parsed and written again, and
    unless that gives the model's own rendering back, the model's rendering is
    written instead. So a retained attribute can never override, or survive
    the loss of, a typed value -- whatever the merge rules missed (an option
    an older Studio wrote on ``Properties``, read from there, and rendered on
    the item, say) costs fidelity, never correctness.

    Rendering equality alone cannot see a typed value the writer does not put
    on the wire at all (review P1: an immediate task's command lives in its
    payload, and a writer that ignored the edit rendered the same before and
    after). So the element chosen is parsed back and held to the model's
    INTENDED values: every typed field the edit changed must read back as the
    edit, and if it reads back as the imported value instead, the edit would
    be lost, and the item is refused (`_refuse_lost_edits`) rather than
    written.

    A retained element that no longer parses or renders (the parser has since
    become stricter, say) is not an export failure: the item is written from
    the model, as it was before WI-080. ``native_xml`` was validated on load,
    so this is a change of parser, not of data.
    """
    raw: str = getattr(item, "native_xml", "")
    if not raw:
        return rendered
    try:
        original = _bounded_parse(raw.encode("utf-8"))
        imported_item = parse(original)
        imported = render(imported_item)
    except GppError:
        return rendered
    edited = _typed_view(item) != _typed_view(imported_item)
    if same_rendering(imported, rendered):
        if edited:
            _refuse_lost_edits(item, imported_item, original, parse)
        return original

    def is_modeled(path: tuple[int, ...], name: str) -> bool:
        # Does the parser read this attribute? Remove it and parse again: an
        # attribute the model does not depend on is content to keep.
        probe = deepcopy(original)
        target = probe
        for index in path:
            target = target[index]
        target.attrib.pop(name, None)
        try:
            return bool(parse(probe) != imported_item)
        except GppError:
            return True

    merged = merge_native(original, imported, rendered, is_modeled)
    try:
        means_the_model = same_rendering(render(parse(merged)), rendered)
    except GppError:
        means_the_model = False
    chosen = merged if means_the_model else rendered
    if edited:
        _refuse_lost_edits(item, imported_item, chosen, parse)
    return chosen


#: Bookkeeping, not values an operator edits: editor ids are assigned on
#: import, the retained element and document slot are provenance.
_NOT_TYPED_VALUES: frozenset[str] = frozenset({"id", "native_xml", "document_position"})


def _typed_view(value: Any) -> Any:
    """A comparable view of a model value without its bookkeeping fields."""
    if hasattr(value, "__dataclass_fields__"):
        return tuple(
            (f.name, _typed_view(getattr(value, f.name)))
            for f in fields(value)
            if f.name not in _NOT_TYPED_VALUES
        )
    if isinstance(value, (list, tuple)):
        return tuple(_typed_view(entry) for entry in value)
    return value


def _refuse_lost_edits(
    item: Any, imported_item: Any, written: ET.Element, parse: Callable[[ET.Element], Any]
) -> None:
    """Refuse an element in which an edited typed value reads back unedited.

    For each typed field the edit changed (the item differs from what its
    import parsed to), the written element is parsed back: a value that reads
    back as the edit, or as something else the writer normalised it to, is
    written; one that reads back as the IMPORTED value was dropped somewhere
    between the model and the wire, and exporting the item would silently undo
    the edit. That is a GppError, which every export path reports as a refusal.
    """
    try:
        written_item = parse(written)
    except GppError as error:
        raise GppError(f"an edited preference item cannot be read back: {error}") from error
    for f in fields(item):
        if f.name in _NOT_TYPED_VALUES:
            continue
        want = _typed_view(getattr(item, f.name))
        was = _typed_view(getattr(imported_item, f.name))
        if want == was:
            continue
        got = _typed_view(getattr(written_item, f.name))
        if got != want and got == was:
            raise GppError(
                f"{type(item).__name__} {_item_label(item)!r}: the edit to {f.name} "
                "cannot be written (it would export as the imported value)"
            )


def _item_label(item: Any) -> str:
    for name in ("name", "key", "path", "group_name", "service_name", "user_name", "dsn"):
        value = getattr(item, name, "")
        if value:
            return str(value)
    return ""


def _serialize_gpp_file(
    collection: GppCollection, path: str, families: tuple[str, ...]
) -> bytes:
    """Write one GPP file: root attributes, then every root child in document order."""
    root_tag, root_clsid = _root_identity(path, families[0])
    root = ET.Element(_ns(root_tag))
    root.set("clsid", root_clsid)
    for name, value in _root_unknowns(collection, path, families).attrs:
        root.set(name, value)
    elements: dict[str, list[ET.Element]] = {
        key: _family_elements(collection, key) for key in families
    }
    unknowns: list[ET.Element] = []
    for raw, _position in _file_unknown_children(collection, path, families):
        try:
            unknowns.append(_bounded_parse(raw.encode("utf-8")))
        except GppError as error:
            raise GppError(f"Corrupted unknown XML in {root_tag} root: {error}") from error
    elements[_UNKNOWN] = unknowns
    for family, index in _ordered_tokens(collection, path, families):
        root.append(elements[family][index])
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def serialize_gpp(collection: GppCollection) -> dict[str, bytes]:
    """Return a dict mapping filename to XML bytes for all non-empty sections.

    Every file keeps its root's retained unknown attributes and children, and
    every root child is written in document order (see the section above).
    A collection whose positions collide is refused here as well as on load,
    so nothing can be written that would not read back.
    """
    _validate_document_positions(collection)
    if collection.source_files:
        return dict(collection.source_files)
    if (
        collection.local_groups
        or collection.local_groups_unknown_attrs
        or collection.local_groups_unknown_children
    ):
        raise GppError(
            "GppCollection.local_groups is deprecated; use the canonical groups field"
        )
    return {
        path: _serialize_gpp_file(collection, path, families)
        for path, families in _gpp_files_with_content(collection)
    }


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_member(elem: ET.Element) -> GppGroupMember:
    action_raw = elem.get("action", "ADD")
    action_code = action_raw.upper() if len(action_raw) > 1 else action_raw
    if action_code not in _CODE_TO_MEMBER_ACTION:
        raise GppError(f"Unsupported member action code: {action_raw!r}")
    return GppGroupMember(
        sid=elem.get("sid", ""),
        name=elem.get("name", ""),
        action=_CODE_TO_MEMBER_ACTION[action_code],
        unknown_attrs=_capture_unknown_attrs(elem, _MEMBER_KNOWN_ATTRS),
    )


def _parse_group(elem: ET.Element) -> GppGroup:
    name = elem.get("name", "")
    props = _find_local(elem, "Properties")

    # MS-GPPREF places action, description, deleteAllUsers/Groups on Properties.
    # Legacy Studio XML placed them on the Group element itself.
    if props is not None:
        action = _code_to_action(props.get("action", elem.get("action", "U")))
        description = props.get("description", elem.get("description", ""))
        remove_all_users = props.get(
            "deleteAllUsers", elem.get("removeUsers", "0")
        ) == "1"
        remove_all_groups = props.get(
            "deleteAllGroups", elem.get("removeGroups", "0")
        ) == "1"
        sid = props.get("groupSid", "")
        ilt_filter, apply_once, run_once_id = _parse_item_filters(elem)
        common = _parse_common_options(
            elem,
            props,
            apply_once=apply_once,
            run_once_id=run_once_id,
        )
    else:
        action = _code_to_action(elem.get("action", "U"))
        description = elem.get("description", "")
        remove_all_users = elem.get("removeUsers", "0") == "1"
        remove_all_groups = elem.get("removeGroups", "0") == "1"
        sid = ""
        ilt_filter, apply_once, run_once_id = _parse_item_filters(elem)
        common = _parse_common_options(
            elem, apply_once=apply_once, run_once_id=run_once_id
        )

    # Members may be inside <Properties> (MS-GPPREF) or a sibling (legacy).
    members: list[GppGroupMember] = []
    members_elem = None
    if props is not None:
        members_elem = _find_local(props, "Members")
    if members_elem is None:
        members_elem = _find_local(elem, "Members")
    if members_elem is not None:
        for member_elem in _findall_local(members_elem, "Member"):
            members.append(_parse_member(member_elem))

    return GppGroup(
        name=name,
        sid=sid,
        action=action,
        members=tuple(members),
        description=description,
        remove_all_users=remove_all_users,
        remove_all_groups=remove_all_groups,
        common=common,
        ilt_filter=ilt_filter,
        unknown_attrs=_capture_unknown_attrs(elem, _GROUP_KNOWN_ATTRS),
        unknown_props_attrs=(
            _capture_unknown_attrs(props, _GROUP_PROPS_KNOWN_ATTRS)
            if props is not None else ()
        ),
        unknown_props_children=(
            _capture_unknown_children(props, _GROUP_PROPS_KNOWN_CHILDREN)
            if props is not None else ()
        ),
        unknown_children=_capture_unknown_children(elem, _GROUP_KNOWN_CHILDREN),
        native_xml=native_element_xml(elem),
    )


def parse_gpp_groups(data: bytes) -> tuple[GppGroup, ...]:
    """Parse GPP Groups XML bytes into a tuple of GppGroup."""
    root = _bounded_parse(data)
    return tuple(_parse_group(elem) for elem in _findall_local(root, "Group"))


def _parse_registry_number(raw: str, reg_type: str) -> int:
    """Read a REG_DWORD/REG_QWORD ``value`` in the hex form GPMC writes.

    Only the measured width is accepted (8 hex digits for DWORD, 16 for
    QWORD). The decimal form Studio wrote before batch 2 is refused rather than
    guessed at: ``42`` is 42 to that Studio and, very probably, 0x42 to the
    Windows extension, and an 8-digit decimal is indistinguishable from hex.
    """
    width = _GPP_REGISTRY_HEX_WIDTH[reg_type]
    if len(raw) != width or any(ch not in "0123456789abcdefABCDEF" for ch in raw):
        raise GppError(
            f"Invalid {reg_type} value {raw!r}: GPMC writes {width} hexadecimal "
            f"digits (42 is {42:0{width}X}). Decimal values written by Studio "
            "before batch 2 are not read; re-author the value."
        )
    return int(raw, 16)


def _registry_values_items(props: ET.Element, context: str) -> list[str] | None:
    """The strings of <Properties>/<Values>, or ``None`` when there is none.

    The writer regenerates <Values> from the typed list, so anything in it the
    model does not hold would be dropped on the first edit. It is refused here
    instead (review P2): a second <Values> container (which one would the
    extension apply?), attributes or text on <Values>, a child other than
    <Value>, and attributes or children on a <Value>. Windows' own files and
    report carry none of these (tests/fixtures/native-gpp-registry-gpmc).
    """
    containers = _findall_local(props, "Values")
    if not containers:
        return None
    if len(containers) > 1:
        raise GppError(f"{context}: more than one <Values> list")
    values_elem = containers[0]
    # Strict allowlist (batch-2 re-review): exactly the measured QNames -- no
    # namespace, as in every native Registry.xml -- no attributes, and only
    # whitespace (pretty-printing) as text or tail. A namespaced <Values> or
    # <Value>, or text after </Values>, used to import and vanish on edit.
    if values_elem.tag != "Values":
        raise GppError(f"{context}: <Values> in a namespace Studio has not measured")
    if values_elem.attrib or (values_elem.text or "").strip():
        raise GppError(f"{context}: <Values> carries content Studio does not model")
    if (values_elem.tail or "").strip():
        raise GppError(f"{context}: text after </Values> that Studio does not model")
    items: list[str] = []
    for child in values_elem:
        if child.tag != "Value":
            raise GppError(f"{context}: <Values> holds a <{child.tag}>")
        if child.attrib or len(child) or (child.tail or "").strip():
            raise GppError(f"{context}: a <Value> carries content Studio does not model")
        items.append(child.text or "")
    return items


def _parse_registry_multi_sz(props: ET.Element, raw: str) -> list[str]:
    """Read REG_MULTI_SZ from its <Values> list (measured, WI01A-Registry-GPMC).

    ``Properties@value`` is the strings space-joined, so it cannot carry a
    string that contains a space; <Values> is the authoritative copy. The two
    must agree -- a file where they do not is ambiguous, and nothing says which
    one the Windows extension applies.
    """
    items = _registry_values_items(props, "REG_MULTI_SZ value") or []
    if len(items) > _MAX_MULTI_SZ_ITEMS:
        raise GppError(f"REG_MULTI_SZ item count exceeds {_MAX_MULTI_SZ_ITEMS}")
    if not items:
        if raw:
            raise GppError(
                f"REG_MULTI_SZ value {raw!r} has no <Values> list. GPMC writes one "
                "<Value> per string; the ';'-joined form Studio wrote before batch 2 "
                "is not read."
            )
        return []
    if " ".join(items) != raw:
        raise GppError(
            f"REG_MULTI_SZ value {raw!r} disagrees with its <Values> list {items!r}"
        )
    return items


def _parse_registry_value(props: ET.Element) -> GppRegistryValue:
    raw = props.get("value", "")
    if (props.text or "").strip():
        # Mixed text in <Properties> is not part of any measured item and would
        # vanish on re-serialization (batch-2 re-review).
        raise GppError("registry <Properties> carries text Studio does not model")
    reg_type = props.get("type", "REG_SZ")
    action = _code_to_registry_action(props.get("action", "C"))
    name = props.get("name", "")
    default = props.get("default", "0") == "1"
    value: str | int | list[str]
    if reg_type in _GPP_REGISTRY_HEX_WIDTH:
        value = _parse_registry_number(raw, reg_type)
    elif reg_type == "REG_MULTI_SZ":
        value = _parse_registry_multi_sz(props, raw)
    elif reg_type == "REG_BINARY":
        # Measured: upper-case hex, no separators (WI01A-RegistryShapes-GPMC).
        # Case is tolerated; anything that is not whole bytes of hex is not.
        if len(raw) % 2 or any(ch not in "0123456789abcdefABCDEF" for ch in raw):
            raise GppError(
                f"Invalid REG_BINARY value {raw!r}: GPMC writes whole bytes as "
                "hexadecimal digits with no separators"
            )
        value = raw
    else:
        value = raw
    # GPMC's report renders an EMPTY <Values/> under every type; a populated
    # one on a non-multi-string value would be dropped on re-export.
    if reg_type != "REG_MULTI_SZ" and _registry_values_items(props, f"{reg_type} value {name!r}"):
        raise GppError(f"{reg_type} value {name!r} carries a <Values> list")
    # ``displayDecimal="0"`` is what the writer emits when nothing says
    # otherwise, so it is not kept as unknown content: a round trip of an
    # authored value would otherwise grow an attribute it never had. Any other
    # value (the editor's decimal radix) is preserved and re-emitted in place.
    unknown_attrs = tuple(
        pair
        for pair in _capture_unknown_attrs(props, _REGISTRY_VALUE_KNOWN_ATTRS)
        if pair != ("displayDecimal", "0")
    )
    return GppRegistryValue(
        name=name,
        value=value,
        registry_type=reg_type,
        action=action,
        default=default,
        unknown_attrs=unknown_attrs,
    )


def _parse_registry(elem: ET.Element) -> list[GppRegistry]:
    """Parse a single <Registry> element into one or more GppRegistry objects.

    Each <Properties> child produces one GppRegistry with a single value.
    Element-level metadata (uid, ilt_filter, unknown attrs/children) from the
    <Registry> element is applied to the first produced GppRegistry; subsequent
    Properties (legacy multi-value format) produce independent items with
    empty element metadata.

    Handles both MS-GPPREF format (one <Properties> per <Registry> with
    hive/key on Properties) and legacy Studio format (multiple <Properties>
    per <Registry> with key on Registry@name).
    """
    props_list = _findall_local(elem, "Properties")
    registry_name = elem.get("name", "")
    uid = elem.get("uid", "")
    ilt_filter, apply_once, run_once_id = _parse_item_filters(elem)
    unknown_attrs = _capture_unknown_attrs(elem, _REGISTRY_KNOWN_ATTRS)
    unknown_children = _capture_unknown_children(elem, _REGISTRY_KNOWN_CHILDREN)

    results: list[GppRegistry] = []

    if not props_list:
        results.append(GppRegistry(
            key=registry_name,
            hive="HKEY_LOCAL_MACHINE",
            value=GppRegistryValue(name="", value="", registry_type="", action="create"),
            uid=uid,
            ilt_filter=ilt_filter,
            unknown_attrs=unknown_attrs,
            unknown_children=unknown_children,
        ))
    else:
        for idx, props in enumerate(props_list):
            hive = _normalize_hive(props.get("hive", "HKEY_LOCAL_MACHINE"))
            key = props.get("key", "") or registry_name
            value = _parse_registry_value(props)
            common = _parse_common_options(
                elem,
                props,
                apply_once=apply_once,
                run_once_id=run_once_id,
            )
            unknown_props_children = _capture_unknown_children(
                props, _REGISTRY_PROPS_KNOWN_CHILDREN
            )
            if idx == 0:
                results.append(GppRegistry(
                    key=key, hive=hive, value=value, uid=uid,
                    # The item's action is the value's (WI-080 review); before,
                    # every import read "update" whatever the item did.
                    action=_REGISTRY_VALUE_TO_ITEM_ACTION[value.action],
                    common=common,
                    ilt_filter=ilt_filter,
                    unknown_attrs=unknown_attrs,
                    unknown_props_children=unknown_props_children,
                    unknown_children=unknown_children,
                    # A legacy multi-value element becomes several items, none
                    # of which is the element: only a one-to-one item keeps it.
                    native_xml=native_element_xml(elem) if len(props_list) == 1 else "",
                ))
            else:
                results.append(GppRegistry(
                    key=key, hive=hive, value=value, common=common,
                    unknown_props_children=unknown_props_children,
                ))

    return results


def parse_gpp_registry(data: bytes) -> tuple[GppRegistry, ...]:
    """Parse GPP Registry XML bytes into a tuple of GppRegistry.

    Each <Registry> XML element becomes one GppRegistry with exactly
    one value per MS-GPPREF.
    """
    root = _bounded_parse(data)

    parsed: list[GppRegistry] = []
    for elem in _findall_local(root, "Registry"):
        parsed.extend(_parse_registry(elem))

    return tuple(parsed)


def parse_gpp_collection(scope: GppScope, files: dict[str, bytes]) -> GppCollection:
    """Parse a dict of filename to XML bytes into a GppCollection."""
    groups: tuple[GppGroup, ...] = ()
    registry: tuple[GppRegistry, ...] = ()
    groups_unknown_attrs: tuple[tuple[str, str], ...] = ()
    groups_unknown_children: tuple[str, ...] = ()
    registry_unknown_attrs: tuple[tuple[str, str], ...] = ()
    registry_unknown_children: tuple[str, ...] = ()
    for filename, content in files.items():
        normalized = filename.replace("\\", "/")
        if normalized.endswith("Groups/Groups.xml"):
            groups = parse_gpp_groups(content)
            try:
                root = _bounded_parse(content)
            except GppError:
                root = None
            if root is not None:
                groups_unknown_attrs = _capture_unknown_attrs(
                    root, _GROUPS_ROOT_KNOWN_ATTRS
                )
                groups_unknown_children = _capture_unknown_children(
                    root, _GROUPS_ROOT_KNOWN_CHILDREN
                )
        elif normalized.endswith("Registry/Registry.xml"):
            registry = parse_gpp_registry(content)
            try:
                root = _bounded_parse(content)
            except GppError:
                root = None
            if root is not None:
                registry_unknown_attrs = _capture_unknown_attrs(
                    root, _REGISTRY_SETTINGS_ROOT_KNOWN_ATTRS
                )
                registry_unknown_children = _capture_unknown_children(
                    root, _REGISTRY_SETTINGS_ROOT_KNOWN_CHILDREN
                )
    adapter_data: dict[str, Any] = _parse_adapter_files(files)
    collection = GppCollection(
        scope=scope, groups=groups, registry=registry,
        groups_unknown_attrs=groups_unknown_attrs,
        groups_unknown_children=groups_unknown_children,
        registry_unknown_attrs=registry_unknown_attrs,
        registry_unknown_children=registry_unknown_children,
        source_files=tuple(sorted(files.items())),
        **adapter_data,
    )
    return _record_document_positions(collection, files)


def _family_element_names(key: str) -> frozenset[str]:
    """The root child element names that are one family's typed items."""
    if key == "groups":
        return frozenset({"Group"})
    if key == "registry":
        return frozenset({"Registry"})
    from .gpp_adapters import _ADAPTER_META, _ROOT_KNOWN_CHILDREN

    if key == "local_users":
        return frozenset({"User"})
    if key == "scheduled_tasks":
        return frozenset({"Task", "TaskV2"})
    if key == "immediate_tasks":
        return frozenset({"ImmediateTaskV2"})
    return _ROOT_KNOWN_CHILDREN[_ADAPTER_META[key][0]]


def _record_document_positions(
    collection: GppCollection, files: dict[str, bytes]
) -> GppCollection:
    """Record where each typed item and root unknown child sat (WI-072/073).

    A position is the child's index among its root's element children. The
    typed parsers read their elements in document order, one item per element
    (a legacy multi-value <Registry> yields one item per <Properties>, all of
    which share the element's position), so the k-th item of a family is the
    k-th matching child. If a count ever disagrees, nothing is recorded for
    that family, which writes it in the pre-1.1 order rather than guess.
    """
    file_families = _gpp_file_families()
    changes: dict[str, Any] = {}
    unknown_positions: dict[str, tuple[int, ...]] = {}
    for filename, content in files.items():
        normalized = filename.replace("\\", "/")
        path = next((p for p in file_families if normalized.endswith(p)), None)
        if path is None:
            continue
        children = list(_bounded_parse(content))
        typed: set[str] = set()
        for key in file_families[path]:
            names = _family_element_names(key)
            typed |= names
            items = _family_items(collection, key)
            slots: list[int] = []
            for index, child in enumerate(children):
                if _local_name(child.tag) not in names:
                    continue
                count = (
                    max(1, len(_findall_local(child, "Properties")))
                    if key == "registry" else 1
                )
                slots.extend([index] * count)
            if len(slots) == len(items):
                changes[key] = tuple(
                    replace(item, document_position=slot)
                    for item, slot in zip(items, slots, strict=True)
                )
        unknown_slots = tuple(
            index for index, child in enumerate(children)
            if _local_name(child.tag) not in typed
        )
        for key in file_families[path]:
            retained = getattr(collection, f"{key}_unknown_children")
            if retained and len(retained) == len(unknown_slots):
                unknown_positions[key] = unknown_slots
    return replace(
        collection,
        root_unknown_positions=tuple(sorted(unknown_positions.items())),
        **changes,
    )


def _parse_adapter_files(files: dict[str, bytes]) -> dict[str, Any]:
    """Parse low-artifact adapter files into GppCollection constructor kwargs.

    A single file may contain multiple adapter types (per MS-GPPREF: local
    users + local groups share Groups\\Groups.xml, scheduled tasks + immediate
    tasks share ScheduledTasks\\ScheduledTasks.xml).
    """
    from .gpp_adapters import ROOT_PARSE_FUNCTIONS

    results: dict[str, object] = {}
    for filename, content in files.items():
        normalized = filename.replace("\\", "/")
        for suffix, parse_entries in ROOT_PARSE_FUNCTIONS.items():
            if normalized.endswith(suffix):
                for adapter_key, parse_fn in parse_entries:
                    items, unknown_attrs, unknown_children = parse_fn(content)
                    results[adapter_key] = items
                    results[f"{adapter_key}_unknown_attrs"] = unknown_attrs
                    results[f"{adapter_key}_unknown_children"] = unknown_children
                break
    return results


# ---------------------------------------------------------------------------
# Editor ID management
# ---------------------------------------------------------------------------

def _ensure_group_editor_ids(group: GppGroup) -> GppGroup:
    new_members = tuple(
        replace(m, id=str(uuid.uuid4())) if not m.id else m
        for m in group.members
    )
    return replace(
        group,
        id=group.id or str(uuid.uuid4()),
        members=new_members,
    )


def _ensure_registry_editor_ids(registry: GppRegistry) -> GppRegistry:
    value = registry.value
    if not value.id:
        value = replace(value, id=str(uuid.uuid4()))
    reg_id = registry.id or str(uuid.uuid4())
    # Braced upper-case, as GPMC writes an item uid (WI01A-Registry-GPMC).
    uid = registry.uid or _native_uid(
        str(uuid.uuid5(uuid.NAMESPACE_URL, f"studio/registry/{reg_id}"))
    )
    return replace(
        registry,
        id=reg_id,
        uid=uid,
        value=value,
    )


def _ensure_simple_editor_id(item: Any) -> Any:
    """Assign a UUID to the id field if empty. Works on any dataclass with id."""
    if getattr(item, "id", ""):
        return item
    return replace(item, id=str(uuid.uuid4()))


def ensure_editor_ids(collection: GppCollection) -> GppCollection:
    """Return a copy with a uuid assigned to every empty-id item.

    Assigning editor IDs is a mutation, so ``source_files`` is cleared to
    prevent :func:`serialize_gpp` from returning stale verbatim bytes.
    """
    new_groups = tuple(_ensure_group_editor_ids(g) for g in collection.groups)
    new_registry = tuple(
        _ensure_registry_editor_ids(r) for r in collection.registry
    )
    from .gpp_adapters import ADAPTER_KEYS
    extra: dict[str, Any] = {}
    for key in ADAPTER_KEYS:
        items = getattr(collection, key)
        extra[key] = tuple(_ensure_simple_editor_id(i) for i in items)
    return replace(
        collection,
        groups=new_groups,
        registry=new_registry,
        source_files=(),
        **extra,
    )


def mark_edited(collection: GppCollection) -> GppCollection:
    """Return a copy with source_files cleared, forcing model-based serialization.

    Retained native elements (WI-080) stay: the writer still reconciles each
    item with its import. :func:`model_only` drops those too.
    """
    return replace(collection, source_files=())


def without_native_records(value: Any) -> Any:
    """*value* with every ``native_xml`` key left out, at any depth (WI-080).

    ``native_xml`` is an imported item's element as Windows wrote it: import
    provenance the writer reads, never editable content. The API neither
    serves it (its JSON response class applies this to every body) nor takes
    it (an inline GPO reference passes through this before it is read), so
    the only way an element is retained is an import. Lists, tuples and dicts
    are copied; anything else is returned as it is.
    """
    if isinstance(value, dict):
        return {
            key: without_native_records(entry)
            for key, entry in value.items()
            if key != "native_xml"
        }
    if isinstance(value, (list, tuple)):
        return [without_native_records(entry) for entry in value]
    return value


def model_only(collection: GppCollection) -> GppCollection:
    """The collection as the typed model alone would write it.

    No source bytes and no retained native elements (WI-080): what
    :func:`serialize_gpp` returns for this is the writer's rendering of the
    model, which is what a check of the PARSER and the WRITER must look at --
    a retained element would write an imported value back even where the
    model misread it. Exports never use this.
    """
    from .gpp_adapters import ADAPTER_KEYS

    def strip(items: tuple[Any, ...]) -> tuple[Any, ...]:
        return tuple(replace(item, native_xml="") if item.native_xml else item for item in items)

    stripped: dict[str, Any] = {key: strip(getattr(collection, key)) for key in ADAPTER_KEYS}
    return replace(
        collection,
        source_files=(),
        groups=strip(collection.groups),
        registry=strip(collection.registry),
        **stripped,
    )


# ---------------------------------------------------------------------------
# Dict (JSON) serialization for store / API
# ---------------------------------------------------------------------------

def _os_criteria_from_dict(data: Any, *, legacy_value: str = "") -> IltOsCriteria | None:
    """Load OS criteria, migrating the pre-WI-021 single-value shape.

    Existing workspaces stored an OS predicate as ``value="WIN7"``. Returning
    ``None`` for that shape makes the new serializer substitute five ``NE``
    ("Any") fields, silently broadening the filter. Treat the legacy value as
    the version criterion instead. Unknown values are preserved too: the ILT
    model deliberately carries platform vocabulary newer than Studio.
    """
    if not isinstance(data, dict):
        return IltOsCriteria(version=legacy_value) if legacy_value else None
    return IltOsCriteria(
        os_class=str(data.get("os_class", "NE")),
        version=str(data.get("version", "NE")),
        product_type=str(data.get("product_type", "NE")),
        edition=str(data.get("edition", "NE")),
        service_pack=str(data.get("service_pack", "NE")),
    )


def _ilt_filter_to_dict(ilt: IltFilter | None) -> dict[str, Any] | None:
    if ilt is None:
        return None
    return {
        "items": [
            {
                "type": p.type,
                "negate": p.negate,
                "value": p.value,
                "bool_op": p.bool_op,
                "unknown_attrs": list(p.unknown_attrs) if p.unknown_attrs else [],
                **(
                    {
                        "os_criteria": {
                            "os_class": p.os_criteria.os_class,
                            "version": p.os_criteria.version,
                            "product_type": p.os_criteria.product_type,
                            "edition": p.os_criteria.edition,
                            "service_pack": p.os_criteria.service_pack,
                        }
                    }
                    if p.os_criteria is not None
                    else {}
                ),
            }
            if isinstance(p, IltPredicate) else p
            for p in ilt.items
        ],
    }


def _parse_ilt_filter_from_dict(data: Any) -> IltFilter | None:
    if not data:
        return None
    if isinstance(data, dict):
        items_data = data.get("items")
        if items_data is not None:
            items: list[IltPredicate | str] = []
            for item in items_data:
                if isinstance(item, dict):
                    value = str(item["value"])
                    items.append(IltPredicate(
                        type=item["type"],
                        negate=bool(item["negate"]),
                        value=value,
                        bool_op=str(item.get("bool_op", "AND")),
                        unknown_attrs=tuple(
                            (str(k), str(v))
                            for k, v in item.get("unknown_attrs", [])
                        ),
                        os_criteria=(
                            _os_criteria_from_dict(
                                item.get("os_criteria"),
                                legacy_value=value,
                            )
                            if item["type"] == "os"
                            else None
                        ),
                    ))
                else:
                    items.append(str(item))
            return IltFilter(items=tuple(items))
        predicates_data = data.get("predicates", [])
        unknown = tuple(data.get("unknown_predicates", []))
        predicates: list[IltPredicate] = []
        for predicate in predicates_data:
            value = str(predicate["value"])
            predicates.append(
                IltPredicate(
                    type=predicate["type"],
                    negate=bool(predicate["negate"]),
                    value=value,
                    bool_op=str(predicate.get("bool_op", "AND")),
                    unknown_attrs=tuple(
                        (str(k), str(v))
                        for k, v in predicate.get("unknown_attrs", [])
                    ),
                    os_criteria=(
                        _os_criteria_from_dict(
                            predicate.get("os_criteria"),
                            legacy_value=value,
                        )
                        if predicate["type"] == "os"
                        else None
                    ),
                )
            )
        return IltFilter(items=tuple(predicates) + unknown)
    else:
        preds = tuple(
            IltPredicate(
                type=p["type"],
                negate=bool(p["negate"]),
                value=str(p["value"]),
            )
            for p in data
        )
        return IltFilter(items=preds)


def _common_options_to_dict(common: GppCommonOptions) -> dict[str, bool | str]:
    result: dict[str, bool | str] = {
        "apply_once": common.apply_once,
        "remove_when_unapplied": common.remove_when_unapplied,
        "user_security_context": common.user_security_context,
        "disabled": common.disabled,
        "stop_on_error": common.stop_on_error,
    }
    # Written only when there is one, so a dict made before WI-080 is unchanged.
    if common.run_once_id:
        result["run_once_id"] = common.run_once_id
    return result


def _common_options_from_dict(data: Any) -> GppCommonOptions:
    if not isinstance(data, dict):
        return GppCommonOptions()
    run_once_id = data.get("run_once_id", "")
    if not isinstance(run_once_id, str) or len(run_once_id) > _MAX_GPP_XML_ATTR_LENGTH:
        raise GppError("common.run_once_id must be a string of at most "
                       f"{_MAX_GPP_XML_ATTR_LENGTH} characters")
    return GppCommonOptions(
        apply_once=bool(data.get("apply_once", False)),
        remove_when_unapplied=bool(data.get("remove_when_unapplied", False)),
        user_security_context=bool(data.get("user_security_context", False)),
        disabled=bool(data.get("disabled", False)),
        stop_on_error=bool(data.get("stop_on_error", False)),
        # Absent in anything stored before WI-080: the writer derives an id.
        run_once_id=run_once_id,
    )


def _native_xml_from_dict(raw: object, key: str, context: str) -> str:
    """Load a stored ``native_xml`` (absent before WI-080: ``""``).

    It is written back verbatim wherever the model has not changed, so it is
    held to what import could have produced: one bounded, entity-free element
    of the family's own item type, carrying no cpassword.
    """
    if raw is None or raw == "":
        return ""
    if not isinstance(raw, str):
        raise GppError(f"native_xml of {context} must be a string")
    _validate_unknown_children((raw,), frozenset(), f"{context} native_xml")
    elem = _bounded_parse(raw.encode("utf-8"))
    if _local_name(elem.tag) not in _family_element_names(key):
        raise GppError(
            f"native_xml of {context} is a <{_local_name(elem.tag)}>, not an item of {key}"
        )
    if has_never_retained_name(elem):
        raise GppError(f"native_xml of {context} carries a cpassword")
    qualified = namespaced_name(elem)
    if qualified is not None:
        raise GppError(
            f"native_xml of {context} uses an XML namespace ({qualified}); no native GPP "
            "capture does, and import never retains one"
        )
    return raw


def gpp_collection_to_dict(collection: GppCollection) -> dict[str, Any]:
    """Serialize a GppCollection to a plain dict for JSON storage.

    Colliding document positions are refused before storage, the same check
    :func:`gpp_collection_from_dict` makes, so a stored GPO always loads.
    """
    _validate_document_positions(collection)
    if (
        collection.local_groups
        or collection.local_groups_unknown_attrs
        or collection.local_groups_unknown_children
    ):
        raise GppError(
            "GppCollection.local_groups is deprecated; use the canonical groups field"
        )
    return {
        "scope": collection.scope,
        "groups": [
            {
                "name": g.name,
                "sid": g.sid,
                "action": g.action,
                "members": [
                    {
                        "sid": m.sid,
                        "name": m.name,
                        "action": m.action,
                        "id": m.id,
                        "unknown_attrs": list(m.unknown_attrs) if m.unknown_attrs else [],
                    }
                    for m in g.members
                ],
                "description": g.description,
                "remove_all_users": g.remove_all_users,
                "remove_all_groups": g.remove_all_groups,
                "common": _common_options_to_dict(g.common),
                "ilt_filter": _ilt_filter_to_dict(g.ilt_filter),
                "id": g.id,
                "unknown_attrs": list(g.unknown_attrs) if g.unknown_attrs else [],
                "unknown_props_attrs": list(g.unknown_props_attrs) if g.unknown_props_attrs else [],
                "unknown_props_children": (
                    list(g.unknown_props_children) if g.unknown_props_children else []
                ),
                "unknown_children": list(g.unknown_children) if g.unknown_children else [],
                "document_position": g.document_position,
                **({"native_xml": g.native_xml} if g.native_xml else {}),
            }
            for g in collection.groups
        ],
        "registry": [
            {
                "key": r.key,
                "hive": r.hive,
                "action": r.action,
                "uid": r.uid,
                "common": _common_options_to_dict(r.common),
                "value": {
                    "name": r.value.name,
                    "value": r.value.value,
                    "registry_type": r.value.registry_type,
                    "action": r.value.action,
                    "default": r.value.default,
                    "id": r.value.id,
                    "unknown_attrs": list(r.value.unknown_attrs) if r.value.unknown_attrs else [],
                },
                "ilt_filter": _ilt_filter_to_dict(r.ilt_filter),
                "unknown_attrs": list(r.unknown_attrs) if r.unknown_attrs else [],
                "unknown_props_children": (
                    list(r.unknown_props_children) if r.unknown_props_children else []
                ),
                "unknown_children": list(r.unknown_children) if r.unknown_children else [],
                "id": r.id,
                "document_position": r.document_position,
                **({"native_xml": r.native_xml} if r.native_xml else {}),
            }
            for r in collection.registry
        ],
        "groups_unknown_attrs": (
            list(collection.groups_unknown_attrs)
            if collection.groups_unknown_attrs else []
        ),
        "groups_unknown_children": (
            list(collection.groups_unknown_children)
            if collection.groups_unknown_children else []
        ),
        "registry_unknown_attrs": (
            list(collection.registry_unknown_attrs)
            if collection.registry_unknown_attrs else []
        ),
        "registry_unknown_children": (
            list(collection.registry_unknown_children)
            if collection.registry_unknown_children else []
        ),
        **_adapters_to_dict(collection),
        "root_unknown_positions": [
            [family, list(positions)]
            for family, positions in collection.root_unknown_positions
        ],
    }


def _adapter_item_to_dict(item: Any) -> dict[str, Any]:
    """Serialize a low-artifact adapter item to a dict."""
    d: dict[str, Any] = {"id": item.id}
    for f in fields(type(item)):
        if f.name in ("id",):
            continue
        value = getattr(item, f.name)
        if f.name == "native_xml" and not value:
            continue  # absent before WI-080; a dict without it is unchanged
        if isinstance(value, tuple):
            d[f.name] = list(value)
        elif f.name == "common":
            d[f.name] = _common_options_to_dict(value)
        elif f.name == "ilt_filter":
            d[f.name] = _ilt_filter_to_dict(value)
        else:
            d[f.name] = value
    return d


def _adapters_to_dict(collection: GppCollection) -> dict[str, Any]:
    """Serialize all low-artifact adapter sections to dict entries."""
    from .gpp_adapters import ADAPTER_KEYS
    result: dict[str, Any] = {}
    for key in ADAPTER_KEYS:
        items = getattr(collection, key)
        result[key] = [_adapter_item_to_dict(i) for i in items]
        unknown_attrs = getattr(collection, f"{key}_unknown_attrs")
        result[f"{key}_unknown_attrs"] = list(unknown_attrs) if unknown_attrs else []
        unknown_children = getattr(collection, f"{key}_unknown_children")
        result[f"{key}_unknown_children"] = (
            list(unknown_children) if unknown_children else []
        )
    return result


def _promote_from_unknown_attrs(
    unknown: tuple[tuple[str, str], ...],
    name: str,
) -> str | None:
    """Find a historical typed attribute hiding in an unknown-attrs bag."""
    for k, v in unknown:
        if _local_name(k).lower() == name:
            return v
    return None


def _gpp_registry_value_from_dict(v: dict[str, Any]) -> GppRegistryValue:
    return GppRegistryValue(
        name=str(v.get("name", "")),
        value=v.get("value", ""),
        registry_type=str(v.get("registry_type", "REG_SZ")),
        action=_validate_gpp_registry_action(v.get("action", "create")),
        default=bool(v.get("default", False)),
        id=str(v.get("id", "")),
        unknown_attrs=tuple(
            (str(k), str(v2))
            for k, v2 in v.get("unknown_attrs", [])
        ),
    )


def _upgrade_stored_registry(reg: GppRegistry) -> GppRegistry:
    """Re-type content a pre-batch-2 import stored as unknown.

    Before batch 2 the parser did not know ``status``/``image`` on <Registry>
    or <Values> under <Properties>, so a native import kept them as unknown
    content -- and kept the REG_MULTI_SZ strings only there, the typed value
    being the space-joined ``value`` attribute read as ONE string. The writer
    now generates all three, so a stored copy would duplicate or contradict
    them. The derived attributes are dropped; a stored <Values> list becomes
    the typed REG_MULTI_SZ value it always was.

    Not recoverable here: a REG_QWORD imported before batch 2 had its 16 hex
    digits read as decimal, which nothing stored distinguishes from a genuine
    decimal (WI-075). A REG_DWORD import never succeeded -- ``int()`` refused
    the hex form outright.
    """
    unknown_attrs = tuple(
        (name, text)
        for name, text in reg.unknown_attrs
        if _local_name(name) not in _REGISTRY_DERIVED_ATTRS
    )
    value = reg.value
    props_children: list[str] = []
    for raw in reg.unknown_props_children:
        try:
            child = _bounded_parse(raw.encode("utf-8"))
        except GppError:
            props_children.append(raw)
            continue
        if _local_name(child.tag) != "Values":
            props_children.append(raw)
            continue
        items = [entry.text or "" for entry in _findall_local(child, "Value")]
        if value.registry_type == "REG_MULTI_SZ" and items:
            value = replace(value, value=items)
    if (
        unknown_attrs == reg.unknown_attrs
        and value is reg.value
        and tuple(props_children) == reg.unknown_props_children
    ):
        return reg
    return replace(
        reg,
        unknown_attrs=unknown_attrs,
        value=value,
        unknown_props_children=tuple(props_children),
    )


def gpp_collection_from_dict(data: dict[str, Any]) -> GppCollection:
    """Reconstruct a GppCollection from a plain dict."""
    scope_raw = str(data.get("scope", "computer"))
    if scope_raw not in ("computer", "user"):
        raise GppError(f"Invalid GPP scope: {scope_raw!r}")
    scope: GppScope = scope_raw  # type: ignore[assignment]
    raw_groups = list(data.get("groups", []))
    for legacy in data.get("local_groups", []):
        raw_groups.append({
            "name": legacy.get("group_name", ""),
            "sid": "",
            "action": legacy.get("action", "update"),
            "members": legacy.get("members", []),
            "description": legacy.get("description", ""),
            "remove_all_users": legacy.get("delete_all_users", False),
            "remove_all_groups": legacy.get("delete_all_groups", False),
            "common": legacy.get("common"),
            "ilt_filter": legacy.get("ilt_filter"),
            "id": legacy.get("id", ""),
            "unknown_attrs": legacy.get("unknown_attrs", []),
            "unknown_props_children": legacy.get("unknown_props_children", []),
            "unknown_children": legacy.get("unknown_children", []),
        })
    groups = tuple(
        GppGroup(
            name=str(g.get("name", "")),
            sid=str(g.get("sid", "")),
            action=_validate_gpp_action(g.get("action", "update")),
            members=tuple(
                GppGroupMember(
                    sid=str(m.get("sid", "")),
                    name=str(m.get("name", "")),
                    action=_validate_gpp_action(m.get("action", "add")),
                    id=str(m.get("id", "")),
                    unknown_attrs=tuple(
                        (str(k), str(v))
                        for k, v in m.get("unknown_attrs", [])
                    ),
                )
                for m in g.get("members", [])
            ),
            description=str(g.get("description", "")),
            remove_all_users=bool(g.get("remove_all_users", False)),
            remove_all_groups=bool(g.get("remove_all_groups", False)),
            common=_common_options_from_dict(g.get("common")),
            ilt_filter=_parse_ilt_filter_from_dict(g.get("ilt_filter")),
            id=str(g.get("id", "")),
            unknown_attrs=tuple(
                (str(k), str(v))
                for k, v in g.get("unknown_attrs", [])
            ),
            unknown_props_attrs=tuple(
                (str(k), str(v))
                for k, v in g.get("unknown_props_attrs", [])
            ),
            unknown_props_children=tuple(g.get("unknown_props_children", [])),
            unknown_children=tuple(g.get("unknown_children", [])),
            document_position=_position_from_dict(
                g.get("document_position"), f"group {g.get('name', '')!r}"
            ),
            native_xml=_native_xml_from_dict(
                g.get("native_xml"), "groups", f"group {g.get('name', '')!r}"
            ),
        )
        for g in raw_groups
    )
    # Validate group unknown attrs/children before constructing
    for g in groups:
        _validate_unknown_attrs(
            g.unknown_attrs, _GROUP_RESERVED_ATTRS, f"group {g.name!r}"
        )
        _validate_unknown_attrs(
            g.unknown_props_attrs,
            _GROUP_PROPS_KNOWN_ATTRS,
            f"group {g.name!r} properties",
        )
        _validate_unknown_children(
            g.unknown_children, _GROUP_KNOWN_CHILDREN, f"group {g.name!r}"
        )
        _validate_unknown_children(
            g.unknown_props_children,
            _GROUP_PROPS_KNOWN_CHILDREN,
            f"group {g.name!r} properties",
        )
        for m in g.members:
            _validate_unknown_attrs(
                m.unknown_attrs, _MEMBER_RESERVED_ATTRS, f"member {m.name!r}"
            )

    registry: list[GppRegistry] = []
    for r in data.get("registry", []):
        ilt_filter = _parse_ilt_filter_from_dict(r.get("ilt_filter"))
        elem_unknown_attrs = tuple(
            (str(k), str(v2))
            for k, v2 in r.get("unknown_attrs", [])
        )
        elem_unknown_children = tuple(r.get("unknown_children", []))
        elem_unknown_props_children = tuple(r.get("unknown_props_children", []))
        if "value" in r and isinstance(r["value"], dict):
            new_uid = str(r.get("uid", ""))
            new_elem_attrs = elem_unknown_attrs
            promoted = _promote_from_unknown_attrs(new_elem_attrs, "uid")
            if promoted is not None and not new_uid:
                new_uid = promoted
                new_elem_attrs = tuple(
                    (k, v) for k, v in new_elem_attrs
                    if _local_name(k) != "uid"
                )
            value = _gpp_registry_value_from_dict(r["value"])
            promoted_default = _promote_from_unknown_attrs(
                value.unknown_attrs, "default"
            )
            if promoted_default is not None and not value.default:
                value = replace(
                    value,
                    default=promoted_default == "1",
                    unknown_attrs=tuple(
                        (k, v) for k, v in value.unknown_attrs
                        if _local_name(k) != "default"
                    ),
                )
            registry.append(GppRegistry(
                key=str(r.get("key", "")),
                hive=_normalize_hive(str(r.get("hive", "HKEY_LOCAL_MACHINE"))),
                action=_validate_gpp_action(r.get("action", "update")),
                uid=new_uid,
                value=value,
                id=str(r.get("id", "")),
                common=_common_options_from_dict(r.get("common")),
                ilt_filter=ilt_filter,
                unknown_attrs=new_elem_attrs,
                unknown_props_children=elem_unknown_props_children,
                unknown_children=elem_unknown_children,
                document_position=_position_from_dict(
                    r.get("document_position"), f"registry {r.get('key', '')!r}"
                ),
                native_xml=_native_xml_from_dict(
                    r.get("native_xml"), "registry", f"registry {r.get('key', '')!r}"
                ),
            ))
        else:
            old_values = r.get("values", [])
            if not old_values:
                old_values = [{}]
            for idx, v in enumerate(old_values):
                v_ilt = _parse_ilt_filter_from_dict(v.get("ilt_filter"))
                if v_ilt is None and idx == 0:
                    v_ilt = ilt_filter
                v_elem_attrs = tuple(
                    (str(k), str(v2))
                    for k, v2 in v.get("unknown_elem_attrs", [])
                )
                if not v_elem_attrs and idx == 0:
                    v_elem_attrs = elem_unknown_attrs
                v_elem_children = tuple(v.get("unknown_children", []))
                if not v_elem_children and idx == 0:
                    v_elem_children = elem_unknown_children
                v_props_children = tuple(v.get("unknown_props_children", []))
                if not v_props_children and idx == 0:
                    v_props_children = elem_unknown_props_children
                v_uid = str(r.get("uid", "")) if idx == 0 else ""
                promoted_uid = _promote_from_unknown_attrs(
                    v_elem_attrs, "uid"
                )
                if promoted_uid is not None:
                    v_uid = promoted_uid
                    v_elem_attrs = tuple(
                        (k, val) for k, val in v_elem_attrs
                        if _local_name(k) != "uid"
                    )
                value = _gpp_registry_value_from_dict(v)
                promoted_default = _promote_from_unknown_attrs(
                    value.unknown_attrs, "default"
                )
                if promoted_default is not None:
                    value = replace(
                        value,
                        default=promoted_default == "1",
                        unknown_attrs=tuple(
                            (k, val) for k, val in value.unknown_attrs
                            if _local_name(k) != "default"
                        ),
                    )
                registry.append(GppRegistry(
                    key=str(r.get("key", "")),
                    hive=_normalize_hive(str(r.get("hive", "HKEY_LOCAL_MACHINE"))),
                    action=_validate_gpp_action(r.get("action", "update")),
                    uid=v_uid,
                    value=value,
                    id=str(r.get("id", "")) if idx == 0 else "",
                    common=_common_options_from_dict(r.get("common")),
                    ilt_filter=v_ilt,
                    unknown_attrs=v_elem_attrs,
                    unknown_props_children=v_props_children,
                    unknown_children=v_elem_children,
                    document_position=_position_from_dict(
                        r.get("document_position"), f"registry {r.get('key', '')!r}"
                    ),
                ))
    registry_tuple = tuple(_upgrade_stored_registry(r) for r in registry)
    for r in registry_tuple:
        _validate_unknown_attrs(
            r.unknown_attrs,
            _REGISTRY_RESERVED_ATTRS,
            f"registry {r.key!r}",
        )
        _validate_unknown_children(
            r.unknown_children,
            _REGISTRY_KNOWN_CHILDREN,
            f"registry {r.key!r}",
        )
        _validate_unknown_children(
            r.unknown_props_children,
            _REGISTRY_PROPS_KNOWN_CHILDREN,
            f"registry {r.key!r} properties",
        )
        _validate_unknown_attrs(
            r.value.unknown_attrs,
            _REGISTRY_VALUE_RESERVED_ATTRS,
            f"registry value {r.value.name!r}",
        )

    collection = GppCollection(
        scope=scope, groups=groups, registry=registry_tuple,
        groups_unknown_attrs=tuple(
            (str(k), str(v))
            for k, v in data.get("groups_unknown_attrs", [])
        ),
        groups_unknown_children=tuple(data.get("groups_unknown_children", [])),
        registry_unknown_attrs=tuple(
            (str(k), str(v))
            for k, v in data.get("registry_unknown_attrs", [])
        ),
        registry_unknown_children=tuple(data.get("registry_unknown_children", [])),
        **_adapters_from_dict(data),
    )
    collection = replace(
        collection,
        root_unknown_positions=_root_unknown_positions_from_dict(
            data.get("root_unknown_positions"), collection
        ),
    )
    _validate_document_positions(collection)
    return collection


def _validate_document_positions(collection: GppCollection) -> None:
    """Refuse a stored order in which two root children claim one slot.

    An import gives every root child of a file its own index, so a stored
    collection whose positions collide was not written by an import: two
    scheduled tasks at one slot, a task and an immediate task at one slot, two
    retained children at one slot, or a typed item and a retained child at one
    slot. Honouring it would let the tie-break, not the source, decide
    processing order (review P2), so it is refused with the slot and both
    claimants named. Gaps are fine: deleting items leaves them.

    Two cases share a slot legitimately. A legacy multi-value <Registry>
    expands into one item per <Properties>, all at that element's slot (list
    order decides between them). And files whose root holds two families
    (Groups.xml, ScheduledTasks.xml) record each retained root child once per
    family; those copies must agree (`_root_unknowns`), and then count once.
    """
    for path, families in _gpp_file_families().items():
        holders: dict[int, str] = {}
        for index, (_raw, slot) in enumerate(_file_unknown_children(collection, path, families)):
            if slot is not None:
                _claim_slot(holders, path, slot, f"retained root child #{index + 1}")
        for key in families:
            for index, item in enumerate(_family_items(collection, key)):
                slot = item.document_position
                if slot is None:
                    continue
                _claim_slot(
                    holders, path, slot,
                    "registry items" if key == "registry" else f"{key} item {index}",
                )


def _claim_slot(holders: dict[int, str], path: str, slot: int, holder: str) -> None:
    other = holders.setdefault(slot, holder)
    if other != holder:
        raise GppError(
            f"{path}: document position {slot} is claimed by both {other} and {holder}; "
            "a stored order with colliding positions is refused"
        )


def _position_from_dict(value: object, context: str) -> int | None:
    """Load a stored ``document_position``. Absent (stored before 1.1) is ``None``."""
    return _checked_position(value, context)


def _root_unknown_positions_from_dict(
    data: object, collection: GppCollection
) -> tuple[tuple[str, tuple[int, ...]], ...]:
    """Load ``root_unknown_positions``; absent (stored before 1.1) is empty.

    Accepts the ``[[family, [positions]], ...]`` form both
    :func:`gpp_collection_to_dict` and the workspace snapshot (``asdict``)
    write. A family Studio does not have, a position that is not a
    non-negative integer, a family listed twice, or a positions list that does
    not match its unknown children one for one is refused: each is a stored
    order Studio could not honour, and guessing would change processing order.
    """
    if data is None:
        return ()
    if not isinstance(data, (list, tuple)):
        raise GppError("root_unknown_positions must be a list of [family, positions] pairs")
    families = {key for keys in _gpp_file_families().values() for key in keys}
    loaded: dict[str, tuple[int, ...]] = {}
    for entry in data:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise GppError("root_unknown_positions must be a list of [family, positions] pairs")
        family, positions = entry
        if not isinstance(family, str) or family not in families:
            raise GppError(f"root_unknown_positions names an unknown family {family!r}")
        if family in loaded:
            raise GppError(f"root_unknown_positions lists {family!r} twice")
        if not isinstance(positions, (list, tuple)):
            raise GppError(f"root_unknown_positions for {family!r} must be a list")
        checked: list[int] = []
        for position in positions:
            value = _checked_position(position, f"{family} root unknowns")
            if value is None:
                raise GppError(f"root_unknown_positions for {family!r} holds a null position")
            checked.append(value)
        retained = getattr(collection, f"{family}_unknown_children")
        if len(checked) != len(retained):
            raise GppError(
                f"root_unknown_positions for {family!r} has {len(checked)} entries "
                f"for {len(retained)} retained root unknown children"
            )
        loaded[family] = tuple(checked)
    return tuple(sorted(loaded.items()))


def _adapter_item_from_dict(
    item_data: dict[str, Any],
    adapter_cls: type,
    key: str,
) -> Any:
    """Reconstruct a low-artifact adapter item from a dict."""
    if (
        adapter_cls.__name__ == "GppPrinter"
        and "skip_local" not in item_data
        and "use_local" in item_data
    ):
        # Stored before WI-081, when the field was ``use_local`` (written as a
        # ``useLocal`` attribute no capture contains; it is GPMC's skipLocal).
        item_data = dict(item_data)
        item_data["skip_local"] = item_data["use_local"]
    if adapter_cls.__name__ == "GppService":
        item_data = dict(item_data)
        if "program" not in item_data and "recovery_command" in item_data:
            item_data["program"] = item_data["recovery_command"]
        if (
            "reset_fail_count_delay_seconds" not in item_data
            and "reset_period_days" in item_data
        ):
            item_data["reset_fail_count_delay_seconds"] = (
                int(item_data["reset_period_days"]) * 86400
            )
        if "restart_service_delay_milliseconds" not in item_data:
            if "restart_service_delay_raw" in item_data:
                item_data["restart_service_delay_milliseconds"] = item_data[
                    "restart_service_delay_raw"
                ]
            elif "restart_delay_minutes" in item_data:
                item_data["restart_service_delay_milliseconds"] = (
                    int(item_data["restart_delay_minutes"]) * 60_000
                )
        if (
            "restart_computer_delay_milliseconds" not in item_data
            and "restart_computer_delay_seconds" in item_data
        ):
            item_data["restart_computer_delay_milliseconds"] = (
                int(item_data["restart_computer_delay_seconds"]) * 1000
            )
        if "append_failure_count" not in item_data and "append_arguments" in item_data:
            legacy_append = str(item_data["append_arguments"]).strip().lower()
            item_data["append_failure_count"] = legacy_append not in {
                "",
                "0",
                "false",
                "no",
            }
    kwargs: dict[str, Any] = {}
    for f in fields(adapter_cls):
        if f.name == "common":
            kwargs[f.name] = _common_options_from_dict(item_data.get("common"))
        elif f.name == "ilt_filter":
            kwargs[f.name] = _parse_ilt_filter_from_dict(item_data.get("ilt_filter"))
        elif f.name == "unknown_attrs":
            kwargs[f.name] = tuple(
                (str(k), str(v))
                for k, v in item_data.get("unknown_attrs", [])
            )
        elif f.name == "unknown_children":
            kwargs[f.name] = tuple(item_data.get("unknown_children", []))
        elif f.name == "document_position":
            # Absent in anything stored before 1.1: no recorded slot.
            kwargs[f.name] = _position_from_dict(
                item_data.get("document_position"), f"{adapter_cls.__name__} item"
            )
        elif f.name == "native_xml":
            kwargs[f.name] = _native_xml_from_dict(
                item_data.get("native_xml"), key, f"{adapter_cls.__name__} item"
            )
        else:
            if adapter_cls.__name__ == "GppScheduledTask" and f.name == "element_variant":
                kwargs[f.name] = item_data.get(f.name, "Task")
                continue
            if f.default is not MISSING:
                raw = item_data.get(f.name, f.default)
            elif f.default_factory is not MISSING:
                raw = item_data.get(f.name, f.default_factory())
            else:
                raw = item_data.get(f.name)
            if isinstance(raw, list):
                raw = tuple(raw)
            kwargs[f.name] = raw
    return adapter_cls(**kwargs)


def _adapters_from_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct all low-artifact adapter sections from a dict."""
    from .gpp_adapters import (
        ADAPTER_KEYS,
        GppApplication,
        GppDataSource,
        GppDevice,
        GppDrive,
        GppEnvironment,
        GppFile,
        GppFolder,
        GppFolderOptions,
        GppImmediateTask,
        GppIniFile,
        GppLocalGroup,
        GppLocalUser,
        GppNetworkShare,
        GppPowerOptions,
        GppPrinter,
        GppRegionalOptions,
        GppScheduledTask,
        GppService,
        GppShortcut,
    )
    adapter_classes: dict[str, type] = {
        "environment": GppEnvironment,
        "ini_files": GppIniFile,
        "regional_options": GppRegionalOptions,
        "power_options": GppPowerOptions,
        "devices": GppDevice,
        "folder_options": GppFolderOptions,
        "data_sources": GppDataSource,
        "drives": GppDrive,
        "files": GppFile,
        "folders": GppFolder,
        "network_shares": GppNetworkShare,
        "printers": GppPrinter,
        "shortcuts": GppShortcut,
        "applications": GppApplication,
        # Privileged execution adapters (Plan 024 WP-4).
        "services": GppService,
        "local_users": GppLocalUser,
        "local_groups": GppLocalGroup,
        "scheduled_tasks": GppScheduledTask,
        "immediate_tasks": GppImmediateTask,
    }
    result: dict[str, object] = {}
    for key in ADAPTER_KEYS:
        cls = adapter_classes[key]
        items_list = data.get(key, [])
        items = tuple(
            _adapter_item_from_dict(item_data, cls, key)
            for item_data in items_list
        )
        result[key] = items
        result[f"{key}_unknown_attrs"] = tuple(
            (str(k), str(v))
            for k, v in data.get(f"{key}_unknown_attrs", [])
        )
        result[f"{key}_unknown_children"] = tuple(
            data.get(f"{key}_unknown_children", [])
        )
    return result


def gpp_registry_unmeasured_shapes(collection: GppCollection) -> tuple[str, ...]:
    """GPP Registry items whose native wire form no Windows capture backs.

    An item is measured only if its exact (action, type) pair -- or (action,
    key-only) -- is in `_MEASURED_GPP_REGISTRY_SHAPES`, every member of which
    was captured as a whole item (WI01A-RegistryMatrix-GPMC). Nothing is
    composed from separately measured parts. A default-value item was never
    captured (the GroupPolicy module has no ``-Default`` parameter), so it is
    listed here and both the native export and the publication planner refuse
    it by this one rule (WI-075).
    """
    shapes: list[str] = []
    for reg in collection.registry:
        value = reg.value
        where = f"{collection.scope} {reg.hive}\\{reg.key}"
        if value.default:
            shapes.append(f"{where}: a default-value item")
            continue
        if not value.name:
            shape = _GPP_REGISTRY_KEY_ONLY
            if value.registry_type not in ("", "REG_SZ"):
                shapes.append(f"{where}: a key-only item typed {value.registry_type}")
                continue
            if value.value not in ("", []):
                # Every captured key-only item has value="" (review).
                shapes.append(f"{where}: a key-only item carrying a value")
                continue
        else:
            shape = value.registry_type
            where = f"{where} value {value.name!r}"
        if (value.action, shape) not in _MEASURED_GPP_REGISTRY_SHAPES:
            shapes.append(f"{where}: {value.action} of {shape or 'an untyped value'}")
    return tuple(shapes)


def contains_cpassword(xml: bytes) -> bool:
    """Return True if the XML holds a cpassword: an attribute OR an element.

    Any depth, any case, any namespace. Until the WI-080 review only attribute
    names were checked, so an element ``<cpassword>`` in a preference item --
    on import, in an unknown child the API accepted, or in a retained native
    element -- was stored and exported. Every import and export path calls
    this, and refuses (``cpassword_detected`` on export).
    """
    if b"cpassword" not in xml.lower():
        return False
    try:
        root = _bounded_parse(xml)
    except GppError:
        return True
    return has_never_retained_name(root)


# ---------------------------------------------------------------------------
# Low-artifact adapter re-exports (Plan 024 WP-2)
# ---------------------------------------------------------------------------
# Use __getattr__ for lazy re-exports to avoid a circular import:
# gpp_adapters.py imports helpers from gpp.py at module load time, so we
# cannot also import from gpp_adapters.py at gpp.py module load time.

_GPP_ADAPTER_EXPORTS: frozenset[str] = frozenset({
    "ADAPTER_FILE_PATHS", "ADAPTER_KEYS",
    "ROOT_PARSE_FUNCTIONS",
    "GppApplication", "GppDataSource", "GppDevice", "GppDrive", "GppEnvironment",
    "GppFile", "GppFolder", "GppFolderOptions", "GppImmediateTask", "GppIniFile",
    "GppLocalGroup", "GppLocalGroupMember", "GppLocalUser", "GppNetworkShare",
    "GppPowerOptions", "GppPrinter", "GppRegionalOptions", "GppScheduledTask",
    "GppService", "GppShortcut",
    "parse_gpp_applications", "parse_gpp_data_sources", "parse_gpp_devices",
    "parse_gpp_drives", "parse_gpp_environment", "parse_gpp_files",
    "parse_gpp_folder_options", "parse_gpp_folders",
    "parse_gpp_immediate_tasks", "parse_gpp_ini_files", "parse_gpp_local_groups",
    "parse_gpp_local_users", "parse_gpp_network_shares", "parse_gpp_power_options",
    "parse_gpp_printers", "parse_gpp_regional_options", "parse_gpp_scheduled_tasks",
    "parse_gpp_services", "parse_gpp_shortcuts",
    "serialize_gpp_applications", "serialize_gpp_data_sources", "serialize_gpp_devices",
    "serialize_gpp_drives", "serialize_gpp_environment", "serialize_gpp_files",
    "serialize_gpp_folder_options", "serialize_gpp_folders",
    "serialize_gpp_immediate_tasks", "serialize_gpp_ini_files",
    "serialize_gpp_local_groups", "serialize_gpp_local_users",
    "serialize_gpp_network_shares", "serialize_gpp_power_options",
    "serialize_gpp_printers", "serialize_gpp_regional_options",
    "serialize_gpp_scheduled_tasks", "serialize_gpp_services",
    "serialize_gpp_shortcuts",
})


def __getattr__(name: str) -> Any:
    if name in _GPP_ADAPTER_EXPORTS:
        from . import gpp_adapters
        return getattr(gpp_adapters, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return list(globals().keys()) + sorted(_GPP_ADAPTER_EXPORTS)
