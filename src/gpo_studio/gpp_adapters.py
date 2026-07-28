"""Low-artifact Group Policy Preferences adapters.

Implements Environment Variables, INI Files, Regional Options, Power Options,
Devices, Folder Options, Data Sources, Drive Maps, Files, Folders, Network
Shares, Printers, Shortcuts, and Applications GPP CSE types per the
MS-GPPREF protocol.  CLSIDs, element layout, and attribute placement follow
Microsoft's documented format so that output is interoperable with GPMC.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, assert_never

from .gpp import (
    GppAction,
    GppCommonOptions,
    GppError,
    GppScope,
    _action_to_code,
    _append_item_filters,
    _append_unknown_children,
    _apply_common_options,
    _apply_unknown_attrs,
    _bounded_parse,
    _capture_unknown_attrs,
    _capture_unknown_children,
    _code_to_action,
    _find_local,
    _findall_local,
    _local_name,
    _ns,
    _parse_common_options,
    _parse_item_filters,
    _xml_declaration,
)
from .ilt import IltFilter

# CLSIDs from MS-GPPREF "Outer and Inner Element Names and CLSIDs" table
# (https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/12512ed6-0632-4e90-a112-d3d2cd41df6c).
_ENV_VARS_CLSID = "{BF141A63-327B-438a-B9BF-2C188F13B7AD}"
_ENV_VAR_CLSID = "{78570023-8373-4a19-BA80-2F150738EA19}"
_INI_FILES_CLSID = "{694C651A-08F2-47fa-A427-34C4F62BA207}"
_INI_CLSID = "{EEFACE84-D3D8-4680-8D4B-BF103E759448}"
_REGIONAL_CLSID = "{BDBA23C2-DE02-434e-8D89-13E53CB6710B}"
_REGIONAL_OPTIONS_CLSID = "{C126A328-BECF-4acc-BA8D-C9C7F6B84E49}"
_POWER_OPTIONS_CLSID = "{7B0F9381-C3B8-4525-8167-87349B671D94}"
_POWER_SCHEME_CLSID = "{DE828AFA-7E71-480e-8081-5447CBE87754}"
_DEVICES_CLSID = "{4DD26924-3F32-47aa-BF33-36D51BD1E54E}"
_DEVICE_CLSID = "{2E1C95D0-85FB-403a-A57C-A508854FB7C8}"
_FOLDER_OPTIONS_CLSID = "{8AB5F5D7-F676-48ab-A94E-1186E120EFDC}"
_GLOBAL_FOLDER_OPTIONS_VISTA_CLSID = "{DBF1E3CD-4CA2-407c-BE84-5F67D3BE754D}"
_DATA_SOURCES_CLSID = "{380F820F-F21B-41ac-A3CC-24D4F80F067B}"
_DATA_SOURCE_CLSID = "{5C209626-D820-4d69-8D50-1FACD6214488}"
_DRIVES_CLSID = "{8FDDCC1A-0C3C-43cd-A6B4-71A6DF20DA8C}"
_DRIVE_CLSID = "{935D1B74-9CB8-4e3c-9914-7DD559B7A417}"
_FILES_CLSID = "{215B2E53-57CE-475c-80FE-9EEC14635851}"
_FILE_CLSID = "{50BE44C8-567A-4ed1-B1D0-9234FE1F38AF}"
_FOLDERS_CLSID = "{77CC39E7-3D16-4f8f-AF86-EC0BBEE2C861}"
_FOLDER_CLSID = "{07DA02F5-F9CD-4397-A550-4AE21B6B4BD3}"
_NETWORK_SHARES_CLSID = "{520870D8-A6E7-47e8-A8D8-E6A4E76EAEC2}"
_NET_SHARE_CLSID = "{2888C5E7-94FC-4739-90AA-2C1536D68BC0}"
_PRINTERS_CLSID = "{1F577D12-3D1B-471e-A1B7-060317597B9C}"
_PRINTER_CLSID = "{9A5E9697-9095-436d-A0EE-4D128FDFBCE5}"
_SHORTCUTS_CLSID = "{872ECB34-B2EC-401b-A585-D32574AA90EE}"
_SHORTCUT_CLSID = "{4F2F7C55-2790-433e-8127-0739D1CFA327}"
_APPLICATIONS_CLSID = "{16DB8EC4-EBFC-4958-98EE-712E9DD3A966}"
_APPLICATION_CLSID = "{C8535E2E-148D-494d-8E9A-71FC46649B5E}"
# Privileged execution adapters (Plan 024 WP-4).
# Per MS-GPPREF, Local Users and Groups share the single <Groups> root and
# Groups\Groups.xml file with <User> and <Group> inner elements.  Immediate
# Tasks share the <ScheduledTasks> root and ScheduledTasks\ScheduledTasks.xml
# file with <ImmediateTaskV2> inner elements.
_NT_SERVICES_CLSID = "{2CFB484A-4E96-4b5d-A0B6-093D2F91E6AE}"
_NT_SERVICE_CLSID = "{AB6F0B67-341F-4e51-92F9-005FBFBA1A43}"
_GROUPS_CLSID = "{3125E937-EB16-4b4c-9934-544FC6D24D26}"
_USER_CLSID = "{DF5F1855-51E5-4d24-8B1A-D9BDE98BA1D1}"
_GROUP_CLSID = "{6D4A79E4-529C-4481-ABD0-F5BD7EA93BA7}"
_SCHEDULED_TASKS_CLSID = "{CC63F200-7309-4ba0-B154-A71CD118DBCC}"
_TASK_CLSID = "{2DEECB1C-261F-4e13-9B21-16FB83BC03BD}"
_TASK_V2_CLSID = "{D8896631-B747-47a7-84A6-C155337F3BC8}"
_IMMEDIATE_TASK_V2_CLSID = "{9756B581-76EC-4169-9AFC-0CA8D43ADB5F}"

# Legacy Studio placed common options on <Properties>. Keep these names in the
# known set for backward-compatible reads, but emit them only on the item.
_COMMON_PROPS_ATTRS = frozenset({
    "applyOnce", "removePolicy", "userContext", "disabled", "bypassErrors",
})

# Known attribute sets for separating typed fields from unknown attrs.
_ITEM_KNOWN_ATTRS = frozenset({
    "clsid",
    "name",
    "applyOnce",
    "removePolicy",
    "userContext",
    "disabled",
    "bypassErrors",
})
_ITEM_KNOWN_CHILDREN = frozenset({"Properties", "Filters"})
_ROOT_KNOWN_ATTRS = frozenset({"clsid"})

# Root known children per adapter type (the typed item element name).
_ROOT_KNOWN_CHILDREN: dict[str, frozenset[str]] = {
    "EnvironmentVariables": frozenset({"EnvironmentVariable"}),
    "IniFiles": frozenset({"Ini"}),
    "Regional": frozenset({"RegionalOptions"}),
    "PowerOptions": frozenset({"PowerScheme"}),
    "Devices": frozenset({"Device"}),
    "FolderOptions": frozenset({"GlobalFolderOptionsVista"}),
    "DataSources": frozenset({"DataSource"}),
    "Drives": frozenset({"Drive"}),
    "Files": frozenset({"File"}),
    "Folders": frozenset({"Folder"}),
    "NetworkShareSettings": frozenset({"NetShare"}),
    "Printers": frozenset({"SharedPrinter"}),
    "Shortcuts": frozenset({"Shortcut"}),
    "Applications": frozenset({"Application"}),
    # Privileged execution adapters (Plan 024 WP-4).
    "NTServices": frozenset({"NTService"}),
    # MS-GPPREF folds local users and local groups into a single <Groups> root
    # containing both <User> and <Group> inner elements.
    "Groups": frozenset({"User", "Group"}),
    # MS-GPPREF folds immediate tasks into the <ScheduledTasks> root alongside
    # scheduled <Task> and <TaskV2> elements.
    "ScheduledTasks": frozenset({"Task", "TaskV2", "ImmediateTaskV2"}),
}

# Properties known attrs per adapter type.
_PROPS_KNOWN_ATTRS: dict[str, frozenset[str]] = {
    "EnvironmentVariables": _COMMON_PROPS_ATTRS | frozenset({
        "name", "value", "action", "user",
    }),
    "IniFiles": _COMMON_PROPS_ATTRS | frozenset({
        "path", "section", "property", "value", "action",
    }),
    "Regional": _COMMON_PROPS_ATTRS | frozenset({
        "userLocale", "userIME", "userNumber", "userCurrency",
        "userTime", "userDate", "userTimeZone",
    }),
    "PowerOptions": _COMMON_PROPS_ATTRS | frozenset({
        "schemeName", "schemeGuid", "acPowerSetting", "dcPowerSetting", "action",
    }),
    "Devices": _COMMON_PROPS_ATTRS | frozenset({
        "deviceClass", "deviceName", "deviceAction",
    }),
    "FolderOptions": _COMMON_PROPS_ATTRS | frozenset({
        "showHidden", "showExtensions", "showSuperHidden",
        "showFullPath", "launchInSeparate", "action",
    }),
    "DataSources": _COMMON_PROPS_ATTRS | frozenset({
        "dsn", "driver", "description", "attributes", "userDsn", "action",
    }),
    "Drives": _COMMON_PROPS_ATTRS | frozenset({
        "letter", "path", "label", "persistent", "useLetter", "action",
    }),
    "Files": _COMMON_PROPS_ATTRS | frozenset({
        "fromPath", "targetPath", "readOnly", "hidden", "archive", "suppress", "action",
    }),
    "Folders": _COMMON_PROPS_ATTRS | frozenset({
        "path", "readOnly", "hidden", "archive", "suppress", "action",
    }),
    "NetworkShareSettings": _COMMON_PROPS_ATTRS | frozenset({
        "name", "path", "comment", "userLimit", "action",
    }),
    "Printers": _COMMON_PROPS_ATTRS | frozenset({
        "path", "action", "setDefault", "useLocal", "comment",
    }),
    "Shortcuts": _COMMON_PROPS_ATTRS | frozenset({
        "name", "targetPath", "arguments", "startIn", "iconPath", "iconIndex",
        "window", "shortcutPath", "action",
    }),
    "Applications": _COMMON_PROPS_ATTRS | frozenset({
        "name", "path", "commandLine", "runAs", "action",
    }),
    # Privileged execution adapters (Plan 024 WP-4).
    "NTServices": _COMMON_PROPS_ATTRS | frozenset({
        "serviceName", "displayName", "startupType", "serviceAction",
        "firstFailure", "secondFailure", "resetPeriod", "restartDelay",
        "recoveryCommand", "timeout", "accountName", "action",
    }),
    # <Groups> root holds both <User> and <Group> items with different
    # Properties attribute sets; the union covers both.
    "Groups": (
        _COMMON_PROPS_ATTRS
        | frozenset({
            "userName", "fullName", "description",
            "passwordNeverExpires", "userCannotChangePassword",
            "acctDisabled", "acctLockedOut", "action",
        })
        | frozenset({
            "groupName", "groupSid", "description",
            "deleteAllUsers", "deleteAllGroups",
        })
    ),
    "ScheduledTasks": (
        _COMMON_PROPS_ATTRS
        | frozenset({
            "name", "runAs", "program", "arguments", "startIn",
            "enabled", "triggerType", "triggerTime", "triggerDays", "action",
        })
        | frozenset({
            "name", "runAs", "program", "arguments", "startIn", "action",
        })
    ),
}


_PROPS_KNOWN_CHILDREN: dict[str, frozenset[str]] = {
    "scheduled_tasks": frozenset({"Task"}),
    "immediate_tasks": frozenset({"Task"}),
    "local_groups": frozenset({"Members"}),
}


def _bool_str(value: bool) -> str:
    return "1" if value else "0"


def _device_action_to_code(action: Literal["enable", "disable"]) -> str:
    match action:
        case "enable":
            return "ENABLE"
        case "disable":
            return "DISABLE"
        case _:
            assert_never(action)


def _code_to_device_action(code: str) -> Literal["enable", "disable"]:
    upper = code.upper()
    if upper == "ENABLE":
        return "enable"
    if upper == "DISABLE":
        return "disable"
    raise GppError(f"Unsupported device action code: {code!r}")


# Replace is one of the four standard GPP actions and GPMC writes action="R"
# for printers; it was missing from the model entirely (WI-019), which made
# Studio reject any genuine backup containing a Replace printer.
_PrinterActionType = Literal["create", "delete", "update", "replace"]


def _printer_action_to_code(action: _PrinterActionType) -> str:
    match action:
        case "create":
            return "C"
        case "delete":
            return "D"
        case "update":
            return "U"
        case "replace":
            return "R"
        case _:
            assert_never(action)


def _code_to_printer_action(code: str) -> _PrinterActionType:
    mapping: dict[str, _PrinterActionType] = {
        "C": "create", "D": "delete", "U": "update", "R": "replace",
    }
    if code in mapping:
        return mapping[code]
    raise GppError(f"Unsupported printer action code: {code!r}")


def _shortcut_window_to_code(
    style: Literal["normal", "minimized", "maximized"],
) -> str:
    match style:
        case "normal":
            return "Normal"
        case "minimized":
            return "Minimized"
        case "maximized":
            return "Maximized"
        case _:
            assert_never(style)


def _code_to_shortcut_window(code: str) -> Literal["normal", "minimized", "maximized"]:
    # GPMC writes window="" when no window style was chosen. The attribute is
    # present but empty, which is not the same as absent, and was previously
    # rejected (WI-019). Both mean "default".
    if not code:
        return "normal"
    mapping: dict[str, Literal["normal", "minimized", "maximized"]] = {
        "Normal": "normal",
        "Minimized": "minimized",
        "Maximized": "maximized",
    }
    if code in mapping:
        return mapping[code]
    raise GppError(f"Unsupported shortcut window style: {code!r}")


# Privileged execution adapter code conversions (Plan 024 WP-4).

_ServiceStartupType = Literal["automatic", "manual", "disabled", "no_change"]
_ServiceAction = Literal["start", "stop", "restart", "no_change"]
_ServiceFailureAction = Literal["none", "restart", "reboot", "run_command"]


def _service_startup_to_code(startup: _ServiceStartupType) -> str:
    """Serialize a startup type the way GPMC writes it.

    Genuine GPMC captures use symbolic names (``AUTOMATIC``, ``NOCHANGE``).
    This previously emitted the numeric codes 2/3/4, which appear nowhere in
    the native corpus, and correspondingly rejected every genuine value on
    parse (WI-019).
    """
    match startup:
        case "automatic":
            return "AUTOMATIC"
        case "manual":
            return "MANUAL"
        case "disabled":
            return "DISABLED"
        case "no_change":
            return "NOCHANGE"
        case _:
            assert_never(startup)


def _code_to_service_startup(code: str) -> _ServiceStartupType:
    mapping: dict[str, _ServiceStartupType] = {
        "AUTOMATIC": "automatic",
        "MANUAL": "manual",
        "DISABLED": "disabled",
        "NOCHANGE": "no_change",
        # Numeric forms are accepted on parse only, for workspaces persisted by
        # earlier Studio versions that emitted them. They are never written.
        "2": "automatic",
        "3": "manual",
        "4": "disabled",
    }
    resolved = mapping.get(code.upper() if code.isalpha() else code)
    if resolved is not None:
        return resolved
    raise GppError(f"Unsupported service startup type code: {code!r}")


def _service_action_to_code(action: _ServiceAction) -> str:
    match action:
        case "start":
            return "START"
        case "stop":
            return "STOP"
        case "restart":
            return "RESTART"
        case "no_change":
            return "NOCHANGE"
        case _:
            assert_never(action)


def _code_to_service_action(code: str) -> _ServiceAction:
    mapping: dict[str, _ServiceAction] = {
        "START": "start", "STOP": "stop",
        "RESTART": "restart", "NOCHANGE": "no_change",
    }
    upper = code.upper()
    if upper in mapping:
        return mapping[upper]
    raise GppError(f"Unsupported service action code: {code!r}")


def _service_failure_to_code(action: _ServiceFailureAction) -> str:
    match action:
        case "none":
            return "NOACTION"
        case "restart":
            return "RESTART"
        case "reboot":
            return "REBOOT"
        case "run_command":
            return "RUNCOMMAND"
        case _:
            assert_never(action)


def _code_to_service_failure(code: str) -> _ServiceFailureAction:
    mapping: dict[str, _ServiceFailureAction] = {
        "NOACTION": "none", "RESTART": "restart",
        "REBOOT": "reboot", "RUNCOMMAND": "run_command",
    }
    upper = code.upper()
    if upper in mapping:
        return mapping[upper]
    raise GppError(f"Unsupported service failure action code: {code!r}")


_LocalGroupMemberAction = Literal["add", "remove"]


def _local_group_member_action_to_code(action: _LocalGroupMemberAction) -> str:
    match action:
        case "add":
            return "ADD"
        case "remove":
            return "REMOVE"
        case _:
            assert_never(action)


def _code_to_local_group_member_action(code: str) -> _LocalGroupMemberAction:
    upper = code.upper()
    if upper == "ADD":
        return "add"
    if upper == "REMOVE":
        return "remove"
    raise GppError(f"Unsupported local group member action code: {code!r}")


_ScheduledTaskTriggerType = Literal[
    "once", "daily", "weekly", "monthly", "at_logon", "at_startup"
]


def _trigger_type_to_code(trigger: _ScheduledTaskTriggerType) -> str:
    match trigger:
        case "once":
            return "ONCE"
        case "daily":
            return "DAILY"
        case "weekly":
            return "WEEKLY"
        case "monthly":
            return "MONTHLY"
        case "at_logon":
            return "ATLOGON"
        case "at_startup":
            return "ATSTARTUP"
        case _:
            assert_never(trigger)


def _code_to_trigger_type(code: str) -> _ScheduledTaskTriggerType:
    mapping: dict[str, _ScheduledTaskTriggerType] = {
        "ONCE": "once", "DAILY": "daily", "WEEKLY": "weekly",
        "MONTHLY": "monthly", "ATLOGON": "at_logon", "ATSTARTUP": "at_startup",
    }
    upper = code.upper()
    if upper in mapping:
        return mapping[upper]
    raise GppError(f"Unsupported scheduled task trigger type code: {code!r}")


def _deny_password(password: str, field_name: str, context: str) -> None:
    """Raise GppError if a credential field is non-empty.

    GPO Studio never serializes credentials into GPP XML.  Password fields
    exist on the dataclass for API symmetry but MUST remain empty; a non-empty
    value indicates a programming error and is rejected before serialization.
    """
    if password:
        raise GppError(
            f"Refusing to serialize {context}: {field_name} must be empty "
            f"(credential denial — GPO Studio never writes passwords to GPP XML)"
        )


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GppEnvironment:
    """A GPP Environment Variable preference item."""

    name: str
    value: str = ""
    action: GppAction = "update"
    user: bool = False
    id: str = ""
    common: GppCommonOptions = field(default_factory=GppCommonOptions)
    ilt_filter: IltFilter | None = None
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_children: tuple[str, ...] = ()
    unknown_props_children: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GppIniFile:
    """A GPP INI File preference item."""

    path: str
    section: str = ""
    property: str = ""
    value: str = ""
    action: GppAction = "update"
    id: str = ""
    common: GppCommonOptions = field(default_factory=GppCommonOptions)
    ilt_filter: IltFilter | None = None
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_children: tuple[str, ...] = ()
    unknown_props_children: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GppRegionalOptions:
    """A GPP Regional Options preference item."""

    user_locale: str = ""
    user_ime: str = ""
    user_number: str = ""
    user_currency: str = ""
    user_time: str = ""
    user_date: str = ""
    user_timezone: str = ""
    action: GppAction = "update"
    id: str = ""
    common: GppCommonOptions = field(default_factory=GppCommonOptions)
    ilt_filter: IltFilter | None = None
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_children: tuple[str, ...] = ()
    unknown_props_children: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GppPowerOptions:
    """A GPP Power Options (power scheme) preference item."""

    scheme_name: str = ""
    scheme_guid: str = ""
    ac_power_setting: str = ""
    dc_power_setting: str = ""
    action: GppAction = "update"
    id: str = ""
    common: GppCommonOptions = field(default_factory=GppCommonOptions)
    ilt_filter: IltFilter | None = None
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_children: tuple[str, ...] = ()
    unknown_props_children: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GppDevice:
    """A GPP Device preference item."""

    device_class: str = ""
    device_name: str = ""
    device_action: Literal["enable", "disable"] = "enable"
    action: GppAction = "update"
    id: str = ""
    common: GppCommonOptions = field(default_factory=GppCommonOptions)
    ilt_filter: IltFilter | None = None
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_children: tuple[str, ...] = ()
    unknown_props_children: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GppFolderOptions:
    """A GPP Folder Options preference item."""

    show_hidden: bool = False
    show_extensions: bool = True
    show_super_hidden: bool = False
    show_full_path: bool = False
    launch_in_separate: bool = False
    action: GppAction = "update"
    id: str = ""
    common: GppCommonOptions = field(default_factory=GppCommonOptions)
    ilt_filter: IltFilter | None = None
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_children: tuple[str, ...] = ()
    unknown_props_children: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GppDataSource:
    """A GPP Data Source (ODBC) preference item."""

    dsn: str
    driver: str = ""
    description: str = ""
    attributes: str = ""
    user_dsn: bool = False
    action: GppAction = "update"
    id: str = ""
    common: GppCommonOptions = field(default_factory=GppCommonOptions)
    ilt_filter: IltFilter | None = None
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_children: tuple[str, ...] = ()
    unknown_props_children: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GppDrive:
    """A GPP Drive Maps preference item."""

    letter: str = ""
    path: str = ""
    label: str = ""
    persistent: bool = True
    use_letter: bool = True
    action: GppAction = "update"
    id: str = ""
    common: GppCommonOptions = field(default_factory=GppCommonOptions)
    ilt_filter: IltFilter | None = None
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_children: tuple[str, ...] = ()
    unknown_props_children: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GppFile:
    """A GPP Files preference item."""

    source: str = ""
    target: str = ""
    read_only: bool = False
    hidden: bool = False
    archive: bool = True
    suppress: bool = False
    action: GppAction = "update"
    id: str = ""
    common: GppCommonOptions = field(default_factory=GppCommonOptions)
    ilt_filter: IltFilter | None = None
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_children: tuple[str, ...] = ()
    unknown_props_children: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GppFolder:
    """A GPP Folders preference item."""

    path: str = ""
    read_only: bool = False
    hidden: bool = False
    archive: bool = True
    suppress: bool = False
    action: GppAction = "update"
    id: str = ""
    common: GppCommonOptions = field(default_factory=GppCommonOptions)
    ilt_filter: IltFilter | None = None
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_children: tuple[str, ...] = ()
    unknown_props_children: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GppNetworkShare:
    """A GPP Network Shares preference item."""

    name: str = ""
    path: str = ""
    comment: str = ""
    user_limit: int = 0
    action: GppAction = "update"
    id: str = ""
    common: GppCommonOptions = field(default_factory=GppCommonOptions)
    ilt_filter: IltFilter | None = None
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_children: tuple[str, ...] = ()
    unknown_props_children: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GppPrinter:
    """A GPP Printers (shared printer) preference item."""

    path: str = ""
    action_type: _PrinterActionType = "create"
    set_default: bool = False
    use_local: bool = False
    comment: str = ""
    action: GppAction = "update"
    id: str = ""
    common: GppCommonOptions = field(default_factory=GppCommonOptions)
    ilt_filter: IltFilter | None = None
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_children: tuple[str, ...] = ()
    unknown_props_children: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GppShortcut:
    """A GPP Shortcuts preference item."""

    name: str = ""
    target_path: str = ""
    arguments: str = ""
    start_in: str = ""
    icon_path: str = ""
    icon_index: int = 0
    window_style: Literal["normal", "minimized", "maximized"] = "normal"
    shortcut_path: str = ""
    action: GppAction = "update"
    id: str = ""
    common: GppCommonOptions = field(default_factory=GppCommonOptions)
    ilt_filter: IltFilter | None = None
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_children: tuple[str, ...] = ()
    unknown_props_children: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GppApplication:
    """A GPP Applications preference item."""

    name: str = ""
    path: str = ""
    command_line: str = ""
    run_as: str = ""
    action: GppAction = "update"
    id: str = ""
    common: GppCommonOptions = field(default_factory=GppCommonOptions)
    ilt_filter: IltFilter | None = None
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_children: tuple[str, ...] = ()
    unknown_props_children: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Privileged execution adapters (Plan 024 WP-4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GppService:
    """A GPP NT Services preference item.

    ``account_password`` exists for API symmetry but MUST always be empty;
    serialization raises :class:`GppError` if it is non-empty (credential
    denial — GPO Studio never writes service passwords to GPP XML).
    """

    service_name: str = ""
    display_name: str = ""
    startup_type: _ServiceStartupType = "automatic"
    service_action: Literal["start", "stop", "restart", "no_change"] = "no_change"
    first_failure: Literal["none", "restart", "reboot", "run_command"] = "none"
    second_failure: Literal["none", "restart", "reboot", "run_command"] = "none"
    reset_period_days: int = 0
    restart_delay_minutes: int = 0
    recovery_command: str = ""
    timeout_seconds: int = 30
    account_name: str = ""
    account_password: str = ""
    action: GppAction = "update"
    id: str = ""
    common: GppCommonOptions = field(default_factory=GppCommonOptions)
    ilt_filter: IltFilter | None = None
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_children: tuple[str, ...] = ()
    unknown_props_children: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GppLocalUser:
    """A GPP Local Users and Groups — User preference item.

    ``password`` exists for API symmetry but MUST always be empty;
    serialization raises :class:`GppError` if it is non-empty (credential
    denial).
    """

    user_name: str = ""
    full_name: str = ""
    description: str = ""
    password_never_expires: bool = False
    user_cannot_change_password: bool = False
    account_disabled: bool = False
    account_locked_out: bool = False
    password: str = ""
    action: GppAction = "update"
    id: str = ""
    common: GppCommonOptions = field(default_factory=GppCommonOptions)
    ilt_filter: IltFilter | None = None
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_children: tuple[str, ...] = ()
    unknown_props_children: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GppLocalGroupMember:
    """A member of a GPP Local Group preference item."""

    name: str = ""
    sid: str = ""
    action: Literal["add", "remove"] = "add"
    unknown_attrs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class GppLocalGroup:
    """A GPP Local Users and Groups — Group preference item."""

    group_name: str = ""
    description: str = ""
    delete_all_users: bool = False
    delete_all_groups: bool = False
    members: tuple[GppLocalGroupMember, ...] = field(default_factory=tuple)
    action: GppAction = "update"
    id: str = ""
    common: GppCommonOptions = field(default_factory=GppCommonOptions)
    ilt_filter: IltFilter | None = None
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_children: tuple[str, ...] = ()
    unknown_props_children: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GppScheduledTask:
    """A GPP Scheduled Tasks preference item.

    ``run_as_password`` exists for API symmetry but MUST always be empty;
    serialization raises :class:`GppError` if it is non-empty (credential
    denial).
    """

    name: str = ""
    run_as: str = ""
    run_as_password: str = ""
    program: str = ""
    arguments: str = ""
    start_in: str = ""
    enabled: bool = True
    trigger_type: Literal[
        "once", "daily", "weekly", "monthly", "at_logon", "at_startup"
    ] = "once"
    trigger_time: str = ""
    trigger_days: str = ""
    task_xml: str = ""
    action: GppAction = "update"
    id: str = ""
    common: GppCommonOptions = field(default_factory=GppCommonOptions)
    ilt_filter: IltFilter | None = None
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_children: tuple[str, ...] = ()
    unknown_props_children: tuple[str, ...] = ()
    element_variant: Literal["Task", "TaskV2"] = "TaskV2"


@dataclass(frozen=True, slots=True)
class GppImmediateTask:
    """A GPP Immediate Tasks (one-shot) preference item.

    ``run_as_password`` exists for API symmetry but MUST always be empty;
    serialization raises :class:`GppError` if it is non-empty (credential
    denial).
    """

    name: str = ""
    run_as: str = ""
    run_as_password: str = ""
    program: str = ""
    arguments: str = ""
    start_in: str = ""
    task_xml: str = ""
    action: GppAction = "update"
    id: str = ""
    common: GppCommonOptions = field(default_factory=GppCommonOptions)
    ilt_filter: IltFilter | None = None
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_children: tuple[str, ...] = ()
    unknown_props_children: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Shared serialization helpers
# ---------------------------------------------------------------------------

# Each adapter registers its type info here so that the generic
# _serialize_item and _parse_item helpers can build the correct XML elements.
# The registry maps the "adapter key" (used in GppCollection field names and
# file paths) to the root element tag.

_ADAPTER_KEYS: tuple[str, ...] = (
    "environment",
    "ini_files",
    "regional_options",
    "power_options",
    "devices",
    "folder_options",
    "data_sources",
    "drives",
    "files",
    "folders",
    "network_shares",
    "printers",
    "shortcuts",
    "applications",
    # Privileged execution adapters (Plan 024 WP-4).
    "services",
    "local_users",
    "scheduled_tasks",
    "immediate_tasks",
)

# adapter_key -> (root_tag, root_clsid, item_tag, item_clsid)
_ADAPTER_META: dict[str, tuple[str, str, str, str]] = {
    "environment": ("EnvironmentVariables", _ENV_VARS_CLSID, "EnvironmentVariable", _ENV_VAR_CLSID),
    "ini_files": ("IniFiles", _INI_FILES_CLSID, "Ini", _INI_CLSID),
    "regional_options": ("Regional", _REGIONAL_CLSID, "RegionalOptions", _REGIONAL_OPTIONS_CLSID),
    "power_options": ("PowerOptions", _POWER_OPTIONS_CLSID, "PowerScheme", _POWER_SCHEME_CLSID),
    "devices": ("Devices", _DEVICES_CLSID, "Device", _DEVICE_CLSID),
    "folder_options": (
        "FolderOptions", _FOLDER_OPTIONS_CLSID,
        "GlobalFolderOptionsVista", _GLOBAL_FOLDER_OPTIONS_VISTA_CLSID,
    ),
    "data_sources": ("DataSources", _DATA_SOURCES_CLSID, "DataSource", _DATA_SOURCE_CLSID),
    "drives": ("Drives", _DRIVES_CLSID, "Drive", _DRIVE_CLSID),
    "files": ("Files", _FILES_CLSID, "File", _FILE_CLSID),
    "folders": ("Folders", _FOLDERS_CLSID, "Folder", _FOLDER_CLSID),
    "network_shares": (
        "NetworkShareSettings", _NETWORK_SHARES_CLSID,
        "NetShare", _NET_SHARE_CLSID,
    ),
    "printers": ("Printers", _PRINTERS_CLSID, "SharedPrinter", _PRINTER_CLSID),
    "shortcuts": ("Shortcuts", _SHORTCUTS_CLSID, "Shortcut", _SHORTCUT_CLSID),
    "applications": ("Applications", _APPLICATIONS_CLSID, "Application", _APPLICATION_CLSID),
    # Privileged execution adapters (Plan 024 WP-4).
    # Per MS-GPPREF, local users and groups share the <Groups> root; immediate
    # tasks share the <ScheduledTasks> root.
    "services": ("NTServices", _NT_SERVICES_CLSID, "NTService", _NT_SERVICE_CLSID),
    "local_users": ("Groups", _GROUPS_CLSID, "User", _USER_CLSID),
    "local_groups": ("Groups", _GROUPS_CLSID, "Group", _GROUP_CLSID),
    "scheduled_tasks": (
        "ScheduledTasks", _SCHEDULED_TASKS_CLSID,
        "Task", _TASK_CLSID,
    ),
    "immediate_tasks": (
        "ScheduledTasks", _SCHEDULED_TASKS_CLSID,
        "ImmediateTaskV2", _IMMEDIATE_TASK_V2_CLSID,
    ),
}

# adapter_key -> file path used in serialize_gpp / parse_gpp_collection.
_ADAPTER_FILE_PATHS: dict[str, str] = {
    "environment": "EnvironmentVariables/EnvironmentVariables.xml",
    "ini_files": "IniFiles/IniFiles.xml",
    "regional_options": "RegionalOptions/RegionalOptions.xml",
    "power_options": "PowerOptions/PowerOptions.xml",
    "devices": "Devices/Devices.xml",
    "folder_options": "FolderOptions/FolderOptions.xml",
    "data_sources": "DataSources/DataSources.xml",
    "drives": "Drives/Drives.xml",
    "files": "Files/Files.xml",
    "folders": "Folders/Folders.xml",
    "network_shares": "NetworkShares/NetworkShares.xml",
    "printers": "Printers/Printers.xml",
    "shortcuts": "Shortcuts/Shortcuts.xml",
    "applications": "Applications/Applications.xml",
    # Privileged execution adapters (Plan 024 WP-4).
    "services": "Services/Services.xml",
    "local_users": "Groups/Groups.xml",
    "scheduled_tasks": "ScheduledTasks/ScheduledTasks.xml",
    "immediate_tasks": "ScheduledTasks/ScheduledTasks.xml",
}


def _build_item_element(
    adapter_key: str,
    item_name: str,
    action: GppAction,
    common: GppCommonOptions,
    ilt_filter: IltFilter | None,
    unknown_attrs: tuple[tuple[str, str], ...],
    unknown_children: tuple[str, ...],
    props_attrs: dict[str, str],
    action_code: str | None = None,
    identity_seed: str = "",
    item_tag_override: str | None = None,
    item_clsid_override: str | None = None,
    unknown_props_children: tuple[str, ...] = (),
) -> ET.Element:
    """Build a single GPP item element with <Properties> following MS-GPPREF."""
    _, _, item_tag, item_clsid = _ADAPTER_META[adapter_key]
    if item_tag_override is not None:
        item_tag = item_tag_override
    if item_clsid_override is not None:
        item_clsid = item_clsid_override
    elem = ET.Element(_ns(item_tag))
    elem.set("clsid", item_clsid)
    if item_name:
        elem.set("name", item_name)
    _apply_common_options(elem, common)
    _apply_unknown_attrs(elem, unknown_attrs)
    props = ET.SubElement(elem, _ns("Properties"))
    props.set("action", action_code if action_code is not None else _action_to_code(action))
    for key, value in props_attrs.items():
        props.set(key, value)
    _append_unknown_children(
        props, unknown_props_children, f"{adapter_key} item {item_name!r} properties"
    )
    _append_item_filters(
        elem,
        ilt_filter,
        common,
        identity_seed or f"{adapter_key}/{item_name}",
    )
    _append_unknown_children(elem, unknown_children, f"{adapter_key} item {item_name!r}")
    return elem


def _build_root_element(adapter_key: str) -> ET.Element:
    root_tag, root_clsid, _, _ = _ADAPTER_META[adapter_key]
    root = ET.Element(_ns(root_tag))
    root.set("clsid", root_clsid)
    return root


def _extract_common(
    elem: ET.Element, adapter_key: str
) -> tuple[
    GppAction,
    GppCommonOptions,
    IltFilter | None,
    tuple[tuple[str, str], ...],
    tuple[str, ...],
    ET.Element | None,
    tuple[str, ...],
]:
    """Extract action, common options, ILT, unknowns, and the Properties element."""
    props = _find_local(elem, "Properties")
    ilt_filter, apply_once = _parse_item_filters(elem)
    unknown_attrs = _capture_unknown_attrs(elem, _ITEM_KNOWN_ATTRS)
    unknown_children = _capture_unknown_children(elem, _ITEM_KNOWN_CHILDREN)
    if props is not None:
        action = _code_to_action(props.get("action", "U"))
        common = _parse_common_options(
            elem,
            props,
            apply_once=apply_once,
        )
        props_known = _PROPS_KNOWN_CHILDREN.get(adapter_key, frozenset())
        unknown_props_children = _capture_unknown_children(props, props_known)
    else:
        action = "update"
        common = _parse_common_options(elem, apply_once=apply_once)
        unknown_props_children = ()
    return (
        action, common, ilt_filter, unknown_attrs, unknown_children,
        props, unknown_props_children,
    )


def _capture_root_unknowns(root: ET.Element, adapter_key: str) -> tuple[
    tuple[tuple[str, str], ...], tuple[str, ...]
]:
    root_tag = _ADAPTER_META[adapter_key][0]
    unknown_attrs = _capture_unknown_attrs(root, _ROOT_KNOWN_ATTRS)
    unknown_children = _capture_unknown_children(
        root, _ROOT_KNOWN_CHILDREN[root_tag]
    )
    return unknown_attrs, unknown_children


# ---------------------------------------------------------------------------
# Environment Variables
# ---------------------------------------------------------------------------


def _serialize_environment(env: GppEnvironment) -> ET.Element:
    return _build_item_element(
        "environment",
        item_name=env.name,
        action=env.action,
        common=env.common,
        ilt_filter=env.ilt_filter,
        unknown_attrs=env.unknown_attrs,
        unknown_children=env.unknown_children,
        unknown_props_children=env.unknown_props_children,
        props_attrs={
            "name": env.name,
            "value": env.value,
            "user": _bool_str(env.user),
        },
    )


def serialize_gpp_environment(
    items: tuple[GppEnvironment, ...],
    scope: GppScope,  # noqa: ARG001 - reserved for scope-specific CLSIDs
) -> bytes:
    """Serialize Environment Variables items to GPP XML bytes."""
    root = _build_root_element("environment")
    for env in items:
        root.append(_serialize_environment(env))
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def _parse_environment_item(elem: ET.Element) -> GppEnvironment:
    action, common, ilt_filter, unknown_attrs, unknown_children, props, unknown_props_children = (
        _extract_common(elem, "environment")
    )
    name = elem.get("name", "")
    if props is not None:
        name = props.get("name", name)
        value = props.get("value", "")
        user = props.get("user", "0") == "1"
    else:
        value = ""
        user = False
    return GppEnvironment(
        name=name,
        value=value,
        action=action,
        user=user,
        common=common,
        ilt_filter=ilt_filter,
        unknown_attrs=unknown_attrs,
        unknown_children=unknown_children,
        unknown_props_children=unknown_props_children,
    )


def parse_gpp_environment(data: bytes) -> tuple[GppEnvironment, ...]:
    """Parse Environment Variables GPP XML bytes."""
    root = _bounded_parse(data)
    return tuple(
        _parse_environment_item(elem)
        for elem in _findall_local(root, "EnvironmentVariable")
    )


# ---------------------------------------------------------------------------
# INI Files
# ---------------------------------------------------------------------------


def _serialize_ini(ini: GppIniFile) -> ET.Element:
    return _build_item_element(
        "ini_files",
        item_name=ini.property or ini.path,
        action=ini.action,
        common=ini.common,
        ilt_filter=ini.ilt_filter,
        unknown_attrs=ini.unknown_attrs,
        unknown_children=ini.unknown_children,
        unknown_props_children=ini.unknown_props_children,
        props_attrs={
            "path": ini.path,
            "section": ini.section,
            "property": ini.property,
            "value": ini.value,
        },
    )


def serialize_gpp_ini_files(
    items: tuple[GppIniFile, ...],
    scope: GppScope,  # noqa: ARG001
) -> bytes:
    """Serialize INI Files items to GPP XML bytes."""
    root = _build_root_element("ini_files")
    for ini in items:
        root.append(_serialize_ini(ini))
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def _parse_ini_item(elem: ET.Element) -> GppIniFile:
    action, common, ilt_filter, unknown_attrs, unknown_children, props, unknown_props_children = (
        _extract_common(elem, "ini_files")
    )
    if props is not None:
        path = props.get("path", "")
        section = props.get("section", "")
        property_ = props.get("property", "")
        value = props.get("value", "")
    else:
        path = ""
        section = ""
        property_ = ""
        value = ""
    return GppIniFile(
        path=path,
        section=section,
        property=property_,
        value=value,
        action=action,
        common=common,
        ilt_filter=ilt_filter,
        unknown_attrs=unknown_attrs,
        unknown_children=unknown_children,
        unknown_props_children=unknown_props_children,
    )


def parse_gpp_ini_files(data: bytes) -> tuple[GppIniFile, ...]:
    """Parse INI Files GPP XML bytes."""
    root = _bounded_parse(data)
    return tuple(
        _parse_ini_item(elem)
        for elem in _findall_local(root, "Ini")
    )


# ---------------------------------------------------------------------------
# Regional Options
# ---------------------------------------------------------------------------


def _serialize_regional_options(reg: GppRegionalOptions) -> ET.Element:
    return _build_item_element(
        "regional_options",
        item_name=reg.user_locale,
        action=reg.action,
        common=reg.common,
        ilt_filter=reg.ilt_filter,
        unknown_attrs=reg.unknown_attrs,
        unknown_children=reg.unknown_children,
        unknown_props_children=reg.unknown_props_children,
        props_attrs={
            "userLocale": reg.user_locale,
            "userIME": reg.user_ime,
            "userNumber": reg.user_number,
            "userCurrency": reg.user_currency,
            "userTime": reg.user_time,
            "userDate": reg.user_date,
            "userTimeZone": reg.user_timezone,
        },
    )


def serialize_gpp_regional_options(
    items: tuple[GppRegionalOptions, ...],
    scope: GppScope,  # noqa: ARG001
) -> bytes:
    """Serialize Regional Options items to GPP XML bytes."""
    root = _build_root_element("regional_options")
    for reg in items:
        root.append(_serialize_regional_options(reg))
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def _parse_regional_options_item(elem: ET.Element) -> GppRegionalOptions:
    action, common, ilt_filter, unknown_attrs, unknown_children, props, unknown_props_children = (
        _extract_common(elem, "regional_options")
    )
    if props is not None:
        return GppRegionalOptions(
            user_locale=props.get("userLocale", ""),
            user_ime=props.get("userIME", ""),
            user_number=props.get("userNumber", ""),
            user_currency=props.get("userCurrency", ""),
            user_time=props.get("userTime", ""),
            user_date=props.get("userDate", ""),
            user_timezone=props.get("userTimeZone", ""),
            action=action,
            common=common,
            ilt_filter=ilt_filter,
            unknown_attrs=unknown_attrs,
            unknown_children=unknown_children,
            unknown_props_children=unknown_props_children,
        )
    return GppRegionalOptions(
        action=action,
        common=common,
        ilt_filter=ilt_filter,
        unknown_attrs=unknown_attrs,
        unknown_children=unknown_children,
        unknown_props_children=unknown_props_children,
    )


def parse_gpp_regional_options(data: bytes) -> tuple[GppRegionalOptions, ...]:
    """Parse Regional Options GPP XML bytes."""
    root = _bounded_parse(data)
    return tuple(
        _parse_regional_options_item(elem)
        for elem in _findall_local(root, "RegionalOptions")
    )


# ---------------------------------------------------------------------------
# Power Options
# ---------------------------------------------------------------------------


def _serialize_power_options(pwr: GppPowerOptions) -> ET.Element:
    return _build_item_element(
        "power_options",
        item_name=pwr.scheme_name,
        action=pwr.action,
        common=pwr.common,
        ilt_filter=pwr.ilt_filter,
        unknown_attrs=pwr.unknown_attrs,
        unknown_children=pwr.unknown_children,
        unknown_props_children=pwr.unknown_props_children,
        props_attrs={
            "schemeName": pwr.scheme_name,
            "schemeGuid": pwr.scheme_guid,
            "acPowerSetting": pwr.ac_power_setting,
            "dcPowerSetting": pwr.dc_power_setting,
        },
    )


def serialize_gpp_power_options(
    items: tuple[GppPowerOptions, ...],
    scope: GppScope,  # noqa: ARG001
) -> bytes:
    """Serialize Power Options items to GPP XML bytes."""
    root = _build_root_element("power_options")
    for pwr in items:
        root.append(_serialize_power_options(pwr))
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def _parse_power_options_item(elem: ET.Element) -> GppPowerOptions:
    action, common, ilt_filter, unknown_attrs, unknown_children, props, unknown_props_children = (
        _extract_common(elem, "power_options")
    )
    if props is not None:
        return GppPowerOptions(
            scheme_name=props.get("schemeName", ""),
            scheme_guid=props.get("schemeGuid", ""),
            ac_power_setting=props.get("acPowerSetting", ""),
            dc_power_setting=props.get("dcPowerSetting", ""),
            action=action,
            common=common,
            ilt_filter=ilt_filter,
            unknown_attrs=unknown_attrs,
            unknown_children=unknown_children,
            unknown_props_children=unknown_props_children,
        )
    return GppPowerOptions(
        action=action,
        common=common,
        ilt_filter=ilt_filter,
        unknown_attrs=unknown_attrs,
        unknown_children=unknown_children,
        unknown_props_children=unknown_props_children,
    )


def parse_gpp_power_options(data: bytes) -> tuple[GppPowerOptions, ...]:
    """Parse Power Options GPP XML bytes."""
    root = _bounded_parse(data)
    return tuple(
        _parse_power_options_item(elem)
        for elem in _findall_local(root, "PowerScheme")
    )


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------


def _serialize_device(dev: GppDevice) -> ET.Element:
    return _build_item_element(
        "devices",
        item_name=dev.device_name,
        action=dev.action,
        common=dev.common,
        ilt_filter=dev.ilt_filter,
        unknown_attrs=dev.unknown_attrs,
        unknown_children=dev.unknown_children,
        unknown_props_children=dev.unknown_props_children,
        props_attrs={
            "deviceClass": dev.device_class,
            "deviceName": dev.device_name,
            "deviceAction": _device_action_to_code(dev.device_action),
        },
    )


def serialize_gpp_devices(
    items: tuple[GppDevice, ...],
    scope: GppScope,  # noqa: ARG001
) -> bytes:
    """Serialize Devices items to GPP XML bytes."""
    root = _build_root_element("devices")
    for dev in items:
        root.append(_serialize_device(dev))
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def _parse_device_item(elem: ET.Element) -> GppDevice:
    action, common, ilt_filter, unknown_attrs, unknown_children, props, unknown_props_children = (
        _extract_common(elem, "devices")
    )
    if props is not None:
        device_action = _code_to_device_action(props.get("deviceAction", "ENABLE"))
        return GppDevice(
            device_class=props.get("deviceClass", ""),
            device_name=props.get("deviceName", ""),
            device_action=device_action,
            action=action,
            common=common,
            ilt_filter=ilt_filter,
            unknown_attrs=unknown_attrs,
            unknown_children=unknown_children,
            unknown_props_children=unknown_props_children,
        )
    return GppDevice(
        action=action,
        common=common,
        ilt_filter=ilt_filter,
        unknown_attrs=unknown_attrs,
        unknown_children=unknown_children,
        unknown_props_children=unknown_props_children,
    )


def parse_gpp_devices(data: bytes) -> tuple[GppDevice, ...]:
    """Parse Devices GPP XML bytes."""
    root = _bounded_parse(data)
    return tuple(
        _parse_device_item(elem)
        for elem in _findall_local(root, "Device")
    )


# ---------------------------------------------------------------------------
# Folder Options
# ---------------------------------------------------------------------------


def _serialize_folder_options(fo: GppFolderOptions) -> ET.Element:
    return _build_item_element(
        "folder_options",
        item_name="",
        action=fo.action,
        common=fo.common,
        ilt_filter=fo.ilt_filter,
        unknown_attrs=fo.unknown_attrs,
        unknown_children=fo.unknown_children,
        unknown_props_children=fo.unknown_props_children,
        props_attrs={
            "showHidden": _bool_str(fo.show_hidden),
            "showExtensions": _bool_str(fo.show_extensions),
            "showSuperHidden": _bool_str(fo.show_super_hidden),
            "showFullPath": _bool_str(fo.show_full_path),
            "launchInSeparate": _bool_str(fo.launch_in_separate),
        },
    )


def serialize_gpp_folder_options(
    items: tuple[GppFolderOptions, ...],
    scope: GppScope,  # noqa: ARG001
) -> bytes:
    """Serialize Folder Options items to GPP XML bytes."""
    root = _build_root_element("folder_options")
    for fo in items:
        root.append(_serialize_folder_options(fo))
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def _parse_folder_options_item(elem: ET.Element) -> GppFolderOptions:
    action, common, ilt_filter, unknown_attrs, unknown_children, props, unknown_props_children = (
        _extract_common(elem, "folder_options")
    )
    if props is not None:
        return GppFolderOptions(
            show_hidden=props.get("showHidden", "0") == "1",
            show_extensions=props.get("showExtensions", "1") == "1",
            show_super_hidden=props.get("showSuperHidden", "0") == "1",
            show_full_path=props.get("showFullPath", "0") == "1",
            launch_in_separate=props.get("launchInSeparate", "0") == "1",
            action=action,
            common=common,
            ilt_filter=ilt_filter,
            unknown_attrs=unknown_attrs,
            unknown_children=unknown_children,
            unknown_props_children=unknown_props_children,
        )
    return GppFolderOptions(
        action=action,
        common=common,
        ilt_filter=ilt_filter,
        unknown_attrs=unknown_attrs,
        unknown_children=unknown_children,
        unknown_props_children=unknown_props_children,
    )


def parse_gpp_folder_options(data: bytes) -> tuple[GppFolderOptions, ...]:
    """Parse Folder Options GPP XML bytes."""
    root = _bounded_parse(data)
    return tuple(
        _parse_folder_options_item(elem)
        for elem in _findall_local(root, "GlobalFolderOptionsVista")
    )


# ---------------------------------------------------------------------------
# Data Sources
# ---------------------------------------------------------------------------


def _serialize_data_source(ds: GppDataSource) -> ET.Element:
    return _build_item_element(
        "data_sources",
        item_name=ds.dsn,
        action=ds.action,
        common=ds.common,
        ilt_filter=ds.ilt_filter,
        unknown_attrs=ds.unknown_attrs,
        unknown_children=ds.unknown_children,
        unknown_props_children=ds.unknown_props_children,
        props_attrs={
            "dsn": ds.dsn,
            "driver": ds.driver,
            "description": ds.description,
            "attributes": ds.attributes,
            "userDsn": _bool_str(ds.user_dsn),
        },
    )


def serialize_gpp_data_sources(
    items: tuple[GppDataSource, ...],
    scope: GppScope,  # noqa: ARG001
) -> bytes:
    """Serialize Data Sources items to GPP XML bytes."""
    root = _build_root_element("data_sources")
    for ds in items:
        root.append(_serialize_data_source(ds))
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def _parse_data_source_item(elem: ET.Element) -> GppDataSource:
    action, common, ilt_filter, unknown_attrs, unknown_children, props, unknown_props_children = (
        _extract_common(elem, "data_sources")
    )
    dsn = elem.get("name", "")
    if props is not None:
        dsn = props.get("dsn", dsn)
        return GppDataSource(
            dsn=dsn,
            driver=props.get("driver", ""),
            description=props.get("description", ""),
            attributes=props.get("attributes", ""),
            user_dsn=props.get("userDsn", "0") == "1",
            action=action,
            common=common,
            ilt_filter=ilt_filter,
            unknown_attrs=unknown_attrs,
            unknown_children=unknown_children,
            unknown_props_children=unknown_props_children,
        )
    return GppDataSource(
        dsn=dsn,
        action=action,
        common=common,
        ilt_filter=ilt_filter,
        unknown_attrs=unknown_attrs,
        unknown_children=unknown_children,
        unknown_props_children=unknown_props_children,
    )


def parse_gpp_data_sources(data: bytes) -> tuple[GppDataSource, ...]:
    """Parse Data Sources GPP XML bytes."""
    root = _bounded_parse(data)
    return tuple(
        _parse_data_source_item(elem)
        for elem in _findall_local(root, "DataSource")
    )


# ---------------------------------------------------------------------------
# Drive Maps
# ---------------------------------------------------------------------------


def _serialize_drive(drive: GppDrive) -> ET.Element:
    return _build_item_element(
        "drives",
        item_name=drive.letter or drive.path,
        action=drive.action,
        common=drive.common,
        ilt_filter=drive.ilt_filter,
        unknown_attrs=drive.unknown_attrs,
        unknown_children=drive.unknown_children,
        unknown_props_children=drive.unknown_props_children,
        props_attrs={
            "letter": drive.letter,
            "path": drive.path,
            "label": drive.label,
            "persistent": _bool_str(drive.persistent),
            "useLetter": _bool_str(drive.use_letter),
        },
    )


def serialize_gpp_drives(
    items: tuple[GppDrive, ...],
    scope: GppScope,  # noqa: ARG001
) -> bytes:
    """Serialize Drive Maps items to GPP XML bytes."""
    root = _build_root_element("drives")
    for drive in items:
        root.append(_serialize_drive(drive))
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def _parse_drive_item(elem: ET.Element) -> GppDrive:
    action, common, ilt_filter, unknown_attrs, unknown_children, props, unknown_props_children = (
        _extract_common(elem, "drives")
    )
    if props is not None:
        return GppDrive(
            letter=props.get("letter", ""),
            path=props.get("path", ""),
            label=props.get("label", ""),
            persistent=props.get("persistent", "1") == "1",
            use_letter=props.get("useLetter", "1") == "1",
            action=action,
            common=common,
            ilt_filter=ilt_filter,
            unknown_attrs=unknown_attrs,
            unknown_children=unknown_children,
            unknown_props_children=unknown_props_children,
        )
    return GppDrive(
        action=action,
        common=common,
        ilt_filter=ilt_filter,
        unknown_attrs=unknown_attrs,
        unknown_children=unknown_children,
        unknown_props_children=unknown_props_children,
    )


def parse_gpp_drives(data: bytes) -> tuple[GppDrive, ...]:
    """Parse Drive Maps GPP XML bytes."""
    root = _bounded_parse(data)
    return tuple(
        _parse_drive_item(elem)
        for elem in _findall_local(root, "Drive")
    )


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def _serialize_file(fi: GppFile) -> ET.Element:
    return _build_item_element(
        "files",
        item_name=fi.target,
        action=fi.action,
        common=fi.common,
        ilt_filter=fi.ilt_filter,
        unknown_attrs=fi.unknown_attrs,
        unknown_children=fi.unknown_children,
        unknown_props_children=fi.unknown_props_children,
        props_attrs={
            "fromPath": fi.source,
            "targetPath": fi.target,
            "readOnly": _bool_str(fi.read_only),
            "hidden": _bool_str(fi.hidden),
            "archive": _bool_str(fi.archive),
            "suppress": _bool_str(fi.suppress),
        },
    )


def serialize_gpp_files(
    items: tuple[GppFile, ...],
    scope: GppScope,  # noqa: ARG001
) -> bytes:
    """Serialize Files items to GPP XML bytes."""
    root = _build_root_element("files")
    for fi in items:
        root.append(_serialize_file(fi))
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def _parse_file_item(elem: ET.Element) -> GppFile:
    action, common, ilt_filter, unknown_attrs, unknown_children, props, unknown_props_children = (
        _extract_common(elem, "files")
    )
    if props is not None:
        return GppFile(
            source=props.get("fromPath", ""),
            target=props.get("targetPath", ""),
            read_only=props.get("readOnly", "0") == "1",
            hidden=props.get("hidden", "0") == "1",
            archive=props.get("archive", "1") == "1",
            suppress=props.get("suppress", "0") == "1",
            action=action,
            common=common,
            ilt_filter=ilt_filter,
            unknown_attrs=unknown_attrs,
            unknown_children=unknown_children,
            unknown_props_children=unknown_props_children,
        )
    return GppFile(
        action=action,
        common=common,
        ilt_filter=ilt_filter,
        unknown_attrs=unknown_attrs,
        unknown_children=unknown_children,
        unknown_props_children=unknown_props_children,
    )


def parse_gpp_files(data: bytes) -> tuple[GppFile, ...]:
    """Parse Files GPP XML bytes."""
    root = _bounded_parse(data)
    return tuple(
        _parse_file_item(elem)
        for elem in _findall_local(root, "File")
    )


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------


def _serialize_folder(folder: GppFolder) -> ET.Element:
    return _build_item_element(
        "folders",
        item_name=folder.path,
        action=folder.action,
        common=folder.common,
        ilt_filter=folder.ilt_filter,
        unknown_attrs=folder.unknown_attrs,
        unknown_children=folder.unknown_children,
        unknown_props_children=folder.unknown_props_children,
        props_attrs={
            "path": folder.path,
            "readOnly": _bool_str(folder.read_only),
            "hidden": _bool_str(folder.hidden),
            "archive": _bool_str(folder.archive),
            "suppress": _bool_str(folder.suppress),
        },
    )


def serialize_gpp_folders(
    items: tuple[GppFolder, ...],
    scope: GppScope,  # noqa: ARG001
) -> bytes:
    """Serialize Folders items to GPP XML bytes."""
    root = _build_root_element("folders")
    for folder in items:
        root.append(_serialize_folder(folder))
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def _parse_folder_item(elem: ET.Element) -> GppFolder:
    action, common, ilt_filter, unknown_attrs, unknown_children, props, unknown_props_children = (
        _extract_common(elem, "folders")
    )
    if props is not None:
        return GppFolder(
            path=props.get("path", ""),
            read_only=props.get("readOnly", "0") == "1",
            hidden=props.get("hidden", "0") == "1",
            archive=props.get("archive", "1") == "1",
            suppress=props.get("suppress", "0") == "1",
            action=action,
            common=common,
            ilt_filter=ilt_filter,
            unknown_attrs=unknown_attrs,
            unknown_children=unknown_children,
            unknown_props_children=unknown_props_children,
        )
    return GppFolder(
        action=action,
        common=common,
        ilt_filter=ilt_filter,
        unknown_attrs=unknown_attrs,
        unknown_children=unknown_children,
        unknown_props_children=unknown_props_children,
    )


def parse_gpp_folders(data: bytes) -> tuple[GppFolder, ...]:
    """Parse Folders GPP XML bytes."""
    root = _bounded_parse(data)
    return tuple(
        _parse_folder_item(elem)
        for elem in _findall_local(root, "Folder")
    )


# ---------------------------------------------------------------------------
# Network Shares
# ---------------------------------------------------------------------------


def _serialize_network_share(share: GppNetworkShare) -> ET.Element:
    return _build_item_element(
        "network_shares",
        item_name=share.name,
        action=share.action,
        common=share.common,
        ilt_filter=share.ilt_filter,
        unknown_attrs=share.unknown_attrs,
        unknown_children=share.unknown_children,
        unknown_props_children=share.unknown_props_children,
        props_attrs={
            "name": share.name,
            "path": share.path,
            "comment": share.comment,
            "userLimit": str(share.user_limit),
        },
    )


def serialize_gpp_network_shares(
    items: tuple[GppNetworkShare, ...],
    scope: GppScope,  # noqa: ARG001
) -> bytes:
    """Serialize Network Shares items to GPP XML bytes."""
    root = _build_root_element("network_shares")
    for share in items:
        root.append(_serialize_network_share(share))
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def _parse_network_share_item(elem: ET.Element) -> GppNetworkShare:
    action, common, ilt_filter, unknown_attrs, unknown_children, props, unknown_props_children = (
        _extract_common(elem, "network_shares")
    )
    name = elem.get("name", "")
    if props is not None:
        name = props.get("name", name)
        try:
            user_limit = int(props.get("userLimit", "0"))
        except ValueError as error:
            raise GppError(
                f"Invalid Network Share userLimit: {props.get('userLimit')!r}"
            ) from error
        return GppNetworkShare(
            name=name,
            path=props.get("path", ""),
            comment=props.get("comment", ""),
            user_limit=user_limit,
            action=action,
            common=common,
            ilt_filter=ilt_filter,
            unknown_attrs=unknown_attrs,
            unknown_children=unknown_children,
            unknown_props_children=unknown_props_children,
        )
    return GppNetworkShare(
        name=name,
        action=action,
        common=common,
        ilt_filter=ilt_filter,
        unknown_attrs=unknown_attrs,
        unknown_children=unknown_children,
        unknown_props_children=unknown_props_children,
    )


def parse_gpp_network_shares(data: bytes) -> tuple[GppNetworkShare, ...]:
    """Parse Network Shares GPP XML bytes."""
    root = _bounded_parse(data)
    return tuple(
        _parse_network_share_item(elem)
        for elem in _findall_local(root, "NetShare")
    )


# ---------------------------------------------------------------------------
# Printers
# ---------------------------------------------------------------------------


def _serialize_printer(printer: GppPrinter) -> ET.Element:
    # Printers use a C/D/U action code on <Properties> rather than the generic
    # GppAction code, so we pass an explicit action_code override.
    return _build_item_element(
        "printers",
        item_name=printer.path,
        action=printer.action,
        common=printer.common,
        ilt_filter=printer.ilt_filter,
        unknown_attrs=printer.unknown_attrs,
        unknown_children=printer.unknown_children,
        unknown_props_children=printer.unknown_props_children,
        props_attrs={
            "path": printer.path,
            "setDefault": _bool_str(printer.set_default),
            "useLocal": _bool_str(printer.use_local),
            "comment": printer.comment,
        },
        action_code=_printer_action_to_code(printer.action_type),
    )


def serialize_gpp_printers(
    items: tuple[GppPrinter, ...],
    scope: GppScope,  # noqa: ARG001
) -> bytes:
    """Serialize Printers items to GPP XML bytes."""
    root = _build_root_element("printers")
    for printer in items:
        root.append(_serialize_printer(printer))
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def _parse_printer_item(elem: ET.Element) -> GppPrinter:
    action, common, ilt_filter, unknown_attrs, unknown_children, props, unknown_props_children = (
        _extract_common(elem, "printers")
    )
    # Printers use <Properties action="..."> for the printer-specific
    # action_type (C/D/U), not the generic GppAction.  The generic action
    # lives on the item element's action attribute (defaulting to "update")
    # so override the value _extract_common derived from Properties.
    item_action = elem.get("action")
    action = _code_to_action(item_action) if item_action else "update"
    if props is not None:
        return GppPrinter(
            path=props.get("path", ""),
            action_type=_code_to_printer_action(props.get("action", "U")),
            set_default=props.get("setDefault", "0") == "1",
            use_local=props.get("useLocal", "0") == "1",
            comment=props.get("comment", ""),
            action=action,
            common=common,
            ilt_filter=ilt_filter,
            unknown_attrs=unknown_attrs,
            unknown_children=unknown_children,
            unknown_props_children=unknown_props_children,
        )
    return GppPrinter(
        action=action,
        common=common,
        ilt_filter=ilt_filter,
        unknown_attrs=unknown_attrs,
        unknown_children=unknown_children,
        unknown_props_children=unknown_props_children,
    )


def parse_gpp_printers(data: bytes) -> tuple[GppPrinter, ...]:
    """Parse Printers GPP XML bytes."""
    root = _bounded_parse(data)
    return tuple(
        _parse_printer_item(elem)
        for elem in _findall_local(root, "SharedPrinter")
    )


# ---------------------------------------------------------------------------
# Shortcuts
# ---------------------------------------------------------------------------


def _serialize_shortcut(sc: GppShortcut) -> ET.Element:
    return _build_item_element(
        "shortcuts",
        item_name=sc.name,
        action=sc.action,
        common=sc.common,
        ilt_filter=sc.ilt_filter,
        unknown_attrs=sc.unknown_attrs,
        unknown_children=sc.unknown_children,
        unknown_props_children=sc.unknown_props_children,
        props_attrs={
            "name": sc.name,
            "targetPath": sc.target_path,
            "arguments": sc.arguments,
            "startIn": sc.start_in,
            "iconPath": sc.icon_path,
            "iconIndex": str(sc.icon_index),
            "window": _shortcut_window_to_code(sc.window_style),
            "shortcutPath": sc.shortcut_path,
        },
    )


def serialize_gpp_shortcuts(
    items: tuple[GppShortcut, ...],
    scope: GppScope,  # noqa: ARG001
) -> bytes:
    """Serialize Shortcuts items to GPP XML bytes."""
    root = _build_root_element("shortcuts")
    for sc in items:
        root.append(_serialize_shortcut(sc))
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def _parse_shortcut_item(elem: ET.Element) -> GppShortcut:
    action, common, ilt_filter, unknown_attrs, unknown_children, props, unknown_props_children = (
        _extract_common(elem, "shortcuts")
    )
    if props is not None:
        try:
            icon_index = int(props.get("iconIndex", "0"))
        except ValueError as error:
            raise GppError(
                f"Invalid Shortcut iconIndex: {props.get('iconIndex')!r}"
            ) from error
        return GppShortcut(
            name=props.get("name", ""),
            target_path=props.get("targetPath", ""),
            arguments=props.get("arguments", ""),
            start_in=props.get("startIn", ""),
            icon_path=props.get("iconPath", ""),
            icon_index=icon_index,
            window_style=_code_to_shortcut_window(props.get("window", "Normal")),
            shortcut_path=props.get("shortcutPath", ""),
            action=action,
            common=common,
            ilt_filter=ilt_filter,
            unknown_attrs=unknown_attrs,
            unknown_children=unknown_children,
            unknown_props_children=unknown_props_children,
        )
    return GppShortcut(
        action=action,
        common=common,
        ilt_filter=ilt_filter,
        unknown_attrs=unknown_attrs,
        unknown_children=unknown_children,
        unknown_props_children=unknown_props_children,
    )


def parse_gpp_shortcuts(data: bytes) -> tuple[GppShortcut, ...]:
    """Parse Shortcuts GPP XML bytes."""
    root = _bounded_parse(data)
    return tuple(
        _parse_shortcut_item(elem)
        for elem in _findall_local(root, "Shortcut")
    )


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------


def _serialize_application(app: GppApplication) -> ET.Element:
    return _build_item_element(
        "applications",
        item_name=app.name,
        action=app.action,
        common=app.common,
        ilt_filter=app.ilt_filter,
        unknown_attrs=app.unknown_attrs,
        unknown_children=app.unknown_children,
        unknown_props_children=app.unknown_props_children,
        props_attrs={
            "name": app.name,
            "path": app.path,
            "commandLine": app.command_line,
            "runAs": app.run_as,
        },
    )


def serialize_gpp_applications(
    items: tuple[GppApplication, ...],
    scope: GppScope,  # noqa: ARG001
) -> bytes:
    """Serialize Applications items to GPP XML bytes."""
    root = _build_root_element("applications")
    for app in items:
        root.append(_serialize_application(app))
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def _parse_application_item(elem: ET.Element) -> GppApplication:
    action, common, ilt_filter, unknown_attrs, unknown_children, props, unknown_props_children = (
        _extract_common(elem, "applications")
    )
    if props is not None:
        return GppApplication(
            name=props.get("name", ""),
            path=props.get("path", ""),
            command_line=props.get("commandLine", ""),
            run_as=props.get("runAs", ""),
            action=action,
            common=common,
            ilt_filter=ilt_filter,
            unknown_attrs=unknown_attrs,
            unknown_children=unknown_children,
            unknown_props_children=unknown_props_children,
        )
    return GppApplication(
        action=action,
        common=common,
        ilt_filter=ilt_filter,
        unknown_attrs=unknown_attrs,
        unknown_children=unknown_children,
        unknown_props_children=unknown_props_children,
    )


def parse_gpp_applications(data: bytes) -> tuple[GppApplication, ...]:
    """Parse Applications GPP XML bytes."""
    root = _bounded_parse(data)
    return tuple(
        _parse_application_item(elem)
        for elem in _findall_local(root, "Application")
    )


# ---------------------------------------------------------------------------
# NT Services (Plan 024 WP-4)
# ---------------------------------------------------------------------------


def _serialize_service(svc: GppService) -> ET.Element:
    _deny_password(
        svc.account_password, "account_password", f"service {svc.service_name!r}"
    )
    return _build_item_element(
        "services",
        item_name=svc.service_name,
        action=svc.action,
        common=svc.common,
        ilt_filter=svc.ilt_filter,
        unknown_attrs=svc.unknown_attrs,
        unknown_children=svc.unknown_children,
        unknown_props_children=svc.unknown_props_children,
        props_attrs={
            "serviceName": svc.service_name,
            "displayName": svc.display_name,
            "startupType": _service_startup_to_code(svc.startup_type),
            "serviceAction": _service_action_to_code(svc.service_action),
            "firstFailure": _service_failure_to_code(svc.first_failure),
            "secondFailure": _service_failure_to_code(svc.second_failure),
            "resetPeriod": str(svc.reset_period_days),
            "restartDelay": str(svc.restart_delay_minutes),
            "recoveryCommand": svc.recovery_command,
            "timeout": str(svc.timeout_seconds),
            "accountName": svc.account_name,
        },
    )


def serialize_gpp_services(
    items: tuple[GppService, ...],
    scope: GppScope,  # noqa: ARG001
) -> bytes:
    """Serialize NT Services items to GPP XML bytes."""
    root = _build_root_element("services")
    for svc in items:
        root.append(_serialize_service(svc))
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def _parse_service_item(elem: ET.Element) -> GppService:
    action, common, ilt_filter, unknown_attrs, unknown_children, props, unknown_props_children = (
        _extract_common(elem, "services")
    )
    service_name = elem.get("name", "")
    if props is not None:
        service_name = props.get("serviceName", service_name)

        def _int_attr(key: str, default: int) -> int:
            raw = props.get(key, str(default)) if props is not None else str(default)
            try:
                return int(raw)
            except ValueError as error:
                raise GppError(
                    f"Invalid NTService {key}: {raw!r}"
                ) from error

        return GppService(
            service_name=service_name,
            display_name=props.get("displayName", ""),
            startup_type=_code_to_service_startup(
                props.get("startupType", "0")
            ),
            service_action=_code_to_service_action(
                props.get("serviceAction", "NOCHANGE")
            ),
            first_failure=_code_to_service_failure(
                props.get("firstFailure", "NOACTION")
            ),
            second_failure=_code_to_service_failure(
                props.get("secondFailure", "NOACTION")
            ),
            reset_period_days=_int_attr("resetPeriod", 0),
            restart_delay_minutes=_int_attr("restartDelay", 0),
            recovery_command=props.get("recoveryCommand", ""),
            timeout_seconds=_int_attr("timeout", 30),
            account_name=props.get("accountName", ""),
            action=action,
            common=common,
            ilt_filter=ilt_filter,
            unknown_attrs=unknown_attrs,
            unknown_children=unknown_children,
            unknown_props_children=unknown_props_children,
        )
    return GppService(
        service_name=service_name,
        action=action,
        common=common,
        ilt_filter=ilt_filter,
        unknown_attrs=unknown_attrs,
        unknown_children=unknown_children,
        unknown_props_children=unknown_props_children,
    )


def parse_gpp_services(data: bytes) -> tuple[GppService, ...]:
    """Parse NT Services GPP XML bytes."""
    root = _bounded_parse(data)
    return tuple(
        _parse_service_item(elem)
        for elem in _findall_local(root, "NTService")
    )


# ---------------------------------------------------------------------------
# Local Users (Plan 024 WP-4)
# ---------------------------------------------------------------------------


def _serialize_local_user(user: GppLocalUser) -> ET.Element:
    _deny_password(
        user.password, "password", f"local user {user.user_name!r}"
    )
    return _build_item_element(
        "local_users",
        item_name=user.user_name,
        action=user.action,
        common=user.common,
        ilt_filter=user.ilt_filter,
        unknown_attrs=user.unknown_attrs,
        unknown_children=user.unknown_children,
        unknown_props_children=user.unknown_props_children,
        props_attrs={
            "userName": user.user_name,
            "fullName": user.full_name,
            "description": user.description,
            "passwordNeverExpires": _bool_str(user.password_never_expires),
            "userCannotChangePassword": _bool_str(
                user.user_cannot_change_password
            ),
            "acctDisabled": _bool_str(user.account_disabled),
            "acctLockedOut": _bool_str(user.account_locked_out),
        },
    )


def serialize_gpp_local_users(
    items: tuple[GppLocalUser, ...],
    scope: GppScope,  # noqa: ARG001
) -> bytes:
    """Serialize Local Users items to GPP XML bytes."""
    root = _build_root_element("local_users")
    for user in items:
        root.append(_serialize_local_user(user))
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def _parse_local_user_item(elem: ET.Element) -> GppLocalUser:
    action, common, ilt_filter, unknown_attrs, unknown_children, props, unknown_props_children = (
        _extract_common(elem, "local_users")
    )
    user_name = elem.get("name", "")
    if props is not None:
        user_name = props.get("userName", user_name)
        return GppLocalUser(
            user_name=user_name,
            full_name=props.get("fullName", ""),
            description=props.get("description", ""),
            password_never_expires=(
                props.get("passwordNeverExpires", "0") == "1"
            ),
            user_cannot_change_password=(
                props.get("userCannotChangePassword", "0") == "1"
            ),
            account_disabled=props.get("acctDisabled", "0") == "1",
            account_locked_out=props.get("acctLockedOut", "0") == "1",
            action=action,
            common=common,
            ilt_filter=ilt_filter,
            unknown_attrs=unknown_attrs,
            unknown_children=unknown_children,
            unknown_props_children=unknown_props_children,
        )
    return GppLocalUser(
        user_name=user_name,
        action=action,
        common=common,
        ilt_filter=ilt_filter,
        unknown_attrs=unknown_attrs,
        unknown_children=unknown_children,
        unknown_props_children=unknown_props_children,
    )


def parse_gpp_local_users(data: bytes) -> tuple[GppLocalUser, ...]:
    """Parse Local Users GPP XML bytes."""
    root = _bounded_parse(data)
    return tuple(
        _parse_local_user_item(elem)
        for elem in _findall_local(root, "User")
    )


# ---------------------------------------------------------------------------
# Local Groups (Plan 024 WP-4)
# ---------------------------------------------------------------------------


def _serialize_local_group_member(member: GppLocalGroupMember) -> ET.Element:
    elem = ET.Element(_ns("Member"))
    elem.set("name", member.name)
    elem.set("sid", member.sid)
    elem.set("action", _local_group_member_action_to_code(member.action))
    _apply_unknown_attrs(elem, member.unknown_attrs)
    return elem


def _serialize_local_group(group: GppLocalGroup) -> ET.Element:
    elem = _build_item_element(
        "local_groups",
        item_name=group.group_name,
        action=group.action,
        common=group.common,
        ilt_filter=group.ilt_filter,
        unknown_attrs=group.unknown_attrs,
        unknown_children=group.unknown_children,
        unknown_props_children=group.unknown_props_children,
        props_attrs={
            "groupName": group.group_name,
            "description": group.description,
            "deleteAllUsers": _bool_str(group.delete_all_users),
            "deleteAllGroups": _bool_str(group.delete_all_groups),
        },
    )
    if group.members:
        props = _find_local(elem, "Properties")
        assert props is not None
        members_elem = ET.SubElement(props, _ns("Members"))
        for member in group.members:
            members_elem.append(_serialize_local_group_member(member))
    return elem


def serialize_gpp_local_groups(
    items: tuple[GppLocalGroup, ...],
    scope: GppScope,  # noqa: ARG001
) -> bytes:
    """Serialize Local Groups items to GPP XML bytes."""
    root = _build_root_element("local_groups")
    for group in items:
        root.append(_serialize_local_group(group))
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def _parse_local_group_member(elem: ET.Element) -> GppLocalGroupMember:
    return GppLocalGroupMember(
        name=elem.get("name", ""),
        sid=elem.get("sid", ""),
        action=_code_to_local_group_member_action(elem.get("action", "ADD")),
        unknown_attrs=_capture_unknown_attrs(
            elem, frozenset({"name", "sid", "action"})
        ),
    )


def _parse_local_group_item(elem: ET.Element) -> GppLocalGroup:
    action, common, ilt_filter, unknown_attrs, unknown_children, props, unknown_props_children = (
        _extract_common(elem, "local_groups")
    )
    group_name = elem.get("name", "")
    members: tuple[GppLocalGroupMember, ...] = ()
    if props is not None:
        group_name = props.get("groupName", group_name)
        description = props.get("description", "")
        delete_all_users = props.get("deleteAllUsers", "0") == "1"
        delete_all_groups = props.get("deleteAllGroups", "0") == "1"
        members_elem = _find_local(props, "Members")
        if members_elem is not None:
            members = tuple(
                _parse_local_group_member(m)
                for m in _findall_local(members_elem, "Member")
            )
    else:
        description = ""
        delete_all_users = False
        delete_all_groups = False
    return GppLocalGroup(
        group_name=group_name,
        description=description,
        delete_all_users=delete_all_users,
        delete_all_groups=delete_all_groups,
        members=members,
        action=action,
        common=common,
        ilt_filter=ilt_filter,
        unknown_attrs=unknown_attrs,
        unknown_children=unknown_children,
        unknown_props_children=unknown_props_children,
    )


def parse_gpp_local_groups(data: bytes) -> tuple[GppLocalGroup, ...]:
    """Parse Local Groups GPP XML bytes."""
    root = _bounded_parse(data)
    return tuple(
        _parse_local_group_item(elem)
        for elem in _findall_local(root, "Group")
    )


# ---------------------------------------------------------------------------
# Scheduled Tasks (Plan 024 WP-4)
# ---------------------------------------------------------------------------


def _extract_task_xml(props: ET.Element) -> str:
    """Serialize the <Task> child of Properties as an opaque XML string."""
    task_elem = _find_local(props, "Task")
    if task_elem is None:
        return ""
    return ET.tostring(task_elem, encoding="unicode")


def _project_triggers_from_task_xml(
    task_xml: str,
) -> tuple[_ScheduledTaskTriggerType, str, str] | None:
    """Recover (trigger_type, trigger_time, trigger_days) from a Task payload.

    A TaskV2 keeps its schedule inside the embedded <Task>, so without this the
    scalar trigger fields would be lost the moment Studio stopped emitting the
    (ignored) v1 attributes. The embedded payload is the authority and the
    scalars are a projection of it -- in both directions.

    Returns ``None`` when the payload carries no trigger this scalar model can
    represent (multiple triggers, SessionStateChangeTrigger, and similar). The
    caller then leaves the scalars at their defaults rather than inventing a
    schedule; ``task_xml`` still round-trips the real thing verbatim.
    """
    if not task_xml:
        return None
    try:
        task_elem = _bounded_parse(task_xml.encode("utf-8"))
    except GppError:
        return None
    triggers = _find_local(task_elem, "Triggers")
    if triggers is None or len(triggers) != 1:
        return None
    trigger = triggers[0]
    kind = _local_name(trigger.tag)
    start = _find_local(trigger, "StartBoundary")
    when = (start.text or "") if start is not None else ""
    if when == _UNSPECIFIED_START_BOUNDARY:
        when = ""
    elif when.startswith(f"{_PLACEHOLDER_START_DATE}T"):
        # Authored as a bare time of day; project it back in the same form.
        when = when.split("T", 1)[1]
    if kind == "TimeTrigger":
        return ("once", when, "")
    if kind != "CalendarTrigger":
        return None
    if _find_local(trigger, "ScheduleByDay") is not None:
        return ("daily", when, "")
    by_week = _find_local(trigger, "ScheduleByWeek")
    if by_week is not None:
        days = _find_local(by_week, "DaysOfWeek")
        first = _local_name(days[0].tag) if days is not None and len(days) else ""
        return ("weekly", when, first)
    by_month = _find_local(trigger, "ScheduleByMonth")
    if by_month is not None:
        days = _find_local(by_month, "DaysOfMonth")
        day = ""
        if days is not None and len(days):
            day = (days[0].text or "").strip()
        return ("monthly", when, day)
    return None


def _project_from_task_xml(
    task_xml: str,
) -> tuple[str, str, str]:
    """Extract (program, arguments, start_in) projections from Task XML.

    Returns empty strings for any field not present.
    """
    if not task_xml:
        return ("", "", "")
    try:
        task_elem = _bounded_parse(task_xml.encode("utf-8"))
    except GppError:
        return ("", "", "")
    actions = _find_local(task_elem, "Actions")
    if actions is None:
        return ("", "", "")
    exec_elem = _find_local(actions, "Exec")
    if exec_elem is None:
        return ("", "", "")
    command = _find_local(exec_elem, "Command")
    arguments = _find_local(exec_elem, "Arguments")
    working_dir = _find_local(exec_elem, "WorkingDirectory")
    return (
        command.text or "" if command is not None else "",
        arguments.text or "" if arguments is not None else "",
        working_dir.text or "" if working_dir is not None else "",
    )


def _append_task_xml_to_props(elem: ET.Element, task_xml: str) -> None:
    """Parse task_xml and append it as a child of the Properties element."""
    if not task_xml:
        return
    props = _find_local(elem, "Properties")
    if props is None:
        return
    try:
        task_elem = _bounded_parse(task_xml.encode("utf-8"))
    except GppError as error:
        raise GppError(
            f"Corrupted task_xml during serialization: {error}"
        ) from error
    props.append(task_elem)


# Structural template taken verbatim from genuine GPMC TaskV2 captures in
# tests/fixtures/native-gpp-gpmc. Every element and default below appears in
# real Windows Server 2025 output; nothing here is invented.
_TASK_V2_SETTINGS = (
    "<Settings>"
    "<IdleSettings><Duration>PT10M</Duration><WaitTimeout>PT1H</WaitTimeout>"
    "<StopOnIdleEnd>true</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>"
    "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
    "<DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>"
    "<StopIfGoingOnBatteries>true</StopIfGoingOnBatteries>"
    "<AllowHardTerminate>false</AllowHardTerminate>"
    "<RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>"
    "<AllowStartOnDemand>true</AllowStartOnDemand>"
    "<Enabled>{enabled}</Enabled>"
    "<Hidden>false</Hidden><RunOnlyIfIdle>false</RunOnlyIfIdle><WakeToRun>false</WakeToRun>"
    "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit><Priority>7</Priority>"
    "</Settings>"
)

_ALL_MONTHS = (
    "<Months><January></January><February></February><March></March><April></April>"
    "<May></May><June></June><July></July><August></August><September></September>"
    "<October></October><November></November><December></December></Months>"
)

#: Trigger forms with a genuine capture behind them. "at_logon" and "at_startup"
#: are deliberately absent: Studio's model offers them but no capture shows what
#: GPMC emits, and inventing a LogonTrigger/BootTrigger shape is precisely how
#: WI-018 and WI-021 happened.
_SYNTHESIZABLE_TRIGGERS: frozenset[str] = frozenset({"once", "daily", "weekly", "monthly"})

#: The Task Scheduler schema requires a StartBoundary, but Studio's scalar model
#: allows an unspecified trigger_time. This stands in for "unspecified" and is
#: mapped back to the empty string on parse, so the round trip stays lossless
#: rather than the model silently acquiring a 1970 timestamp it never authored.
#: A boundary in the past simply means the schedule is already active.
_PLACEHOLDER_START_DATE = "1970-01-01"
_UNSPECIFIED_START_BOUNDARY = f"{_PLACEHOLDER_START_DATE}T00:00:00"

#: Task Scheduler 1.0 stores a time of day ("03:00:00"); Task Scheduler 2.0
#: needs a full ISO 8601 StartBoundary. Emitting the bare time produces a
#: payload Windows rejects outright -- the task is simply never created, with no
#: error surfaced anywhere. Found by the endpoint lane after every unit test
#: passed, because a Studio-to-Studio round trip cannot tell the two apart.
_BARE_TIME_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")


def _xml_text(value: str) -> str:
    """Escape text for embedding in the hand-built Task payload."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _start_boundary(trigger_time: str) -> str:
    """Normalize a scalar trigger time into an ISO 8601 StartBoundary."""
    if not trigger_time:
        return _UNSPECIFIED_START_BOUNDARY
    if _BARE_TIME_RE.match(trigger_time):
        padded = trigger_time if trigger_time.count(":") == 2 else f"{trigger_time}:00"
        hour, rest = padded.split(":", 1)
        return f"{_PLACEHOLDER_START_DATE}T{int(hour):02d}:{rest}"
    return trigger_time


def _task_v2_trigger_xml(task: GppScheduledTask) -> str:
    """Build the <Triggers> payload for a TaskV2 from the scalar model."""
    boundary = _start_boundary(task.trigger_time)
    enabled = "true" if task.enabled else "false"
    if task.trigger_type == "once":
        return (
            f"<Triggers><TimeTrigger><StartBoundary>{_xml_text(boundary)}</StartBoundary>"
            f"<Enabled>{enabled}</Enabled></TimeTrigger></Triggers>"
        )
    if task.trigger_type == "daily":
        schedule = "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
    elif task.trigger_type == "weekly":
        days = task.trigger_days or "Sunday"
        schedule = (
            "<ScheduleByWeek><WeeksInterval>1</WeeksInterval>"
            f"<DaysOfWeek><{_xml_text(days)}/></DaysOfWeek></ScheduleByWeek>"
        )
    else:
        day = task.trigger_days or "1"
        schedule = (
            f"<ScheduleByMonth><DaysOfMonth><Day>{_xml_text(day)}</Day></DaysOfMonth>"
            f"{_ALL_MONTHS}</ScheduleByMonth>"
        )
    return (
        f"<Triggers><CalendarTrigger><StartBoundary>{_xml_text(boundary)}</StartBoundary>"
        f"<Enabled>{enabled}</Enabled>{schedule}</CalendarTrigger></Triggers>"
    )


def _synthesize_task_v2_xml(task: GppScheduledTask) -> str:
    """Build an embedded <Task> payload for a TaskV2 authored through scalars.

    Genuine GPMC TaskV2 items carry their actions and triggers HERE, never in
    scalar Properties attributes. Studio previously emitted the Task Scheduler
    1.0 scalar set on a TaskV2 element with no payload at all, which the
    Scheduled Tasks CSE silently ignored -- the task was never created
    (WI-018, endpoint-confirmed 2026-07-27).
    """
    if task.trigger_type not in _SYNTHESIZABLE_TRIGGERS:
        raise GppError(
            f"Scheduled task {task.name!r} uses trigger type "
            f"{task.trigger_type!r}, which has no captured GPMC form. Supply "
            f"task_xml explicitly, or use one of: "
            f"{', '.join(sorted(_SYNTHESIZABLE_TRIGGERS))}."
        )
    run_as = task.run_as or "%LogonDomain%\\%LogonUser%"
    enabled = "true" if task.enabled else "false"
    return (
        '<Task version="1.2">'
        "<RegistrationInfo><Author>GPO Studio</Author><Description></Description>"
        "</RegistrationInfo>"
        '<Principals><Principal id="Author">'
        f"<UserId>{_xml_text(run_as)}</UserId>"
        "<LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel>"
        "</Principal></Principals>"
        + _TASK_V2_SETTINGS.format(enabled=enabled)
        + _task_v2_trigger_xml(task)
        + '<Actions Context="Author"><Exec>'
        + f"<Command>{_xml_text(task.program)}</Command>"
        + f"<Arguments>{_xml_text(task.arguments)}</Arguments>"
        + f"<WorkingDirectory>{_xml_text(task.start_in)}</WorkingDirectory>"
        + "</Exec></Actions>"
        + "</Task>"
    )


def _serialize_scheduled_task(task: GppScheduledTask) -> ET.Element:
    _deny_password(
        task.run_as_password,
        "run_as_password",
        f"scheduled task {task.name!r}",
    )
    tag_override: str | None = None
    clsid_override: str | None = None
    if task.element_variant == "TaskV2":
        tag_override = "TaskV2"
        clsid_override = _TASK_V2_CLSID
    elif task.element_variant == "Task":
        tag_override = "Task"
        clsid_override = _TASK_CLSID
    else:
        assert_never(task.element_variant)
    # A TaskV2 carries its actions and triggers in an embedded <Task> payload;
    # the Task Scheduler 1.0 scalar attributes belong to the v1 <Task> element
    # and are silently ignored on a v2 item (WI-018). The two shapes are
    # therefore mutually exclusive, not additive.
    if task.element_variant == "TaskV2":
        props_attrs = {"name": task.name, "runAs": task.run_as}
        task_xml = task.task_xml or _synthesize_task_v2_xml(task)
    else:
        props_attrs = {
            "name": task.name,
            "runAs": task.run_as,
            "program": task.program,
            "arguments": task.arguments,
            "startIn": task.start_in,
            "enabled": _bool_str(task.enabled),
            "triggerType": _trigger_type_to_code(task.trigger_type),
            "triggerTime": task.trigger_time,
            "triggerDays": task.trigger_days,
        }
        task_xml = task.task_xml
    elem = _build_item_element(
        "scheduled_tasks",
        item_name=task.name,
        action=task.action,
        common=task.common,
        ilt_filter=task.ilt_filter,
        unknown_attrs=task.unknown_attrs,
        unknown_children=task.unknown_children,
        unknown_props_children=task.unknown_props_children,
        props_attrs=props_attrs,
        item_tag_override=tag_override,
        item_clsid_override=clsid_override,
    )
    _append_task_xml_to_props(elem, task_xml)
    return elem


def serialize_gpp_scheduled_tasks(
    items: tuple[GppScheduledTask, ...],
    scope: GppScope,  # noqa: ARG001
) -> bytes:
    """Serialize Scheduled Tasks items to GPP XML bytes."""
    root = _build_root_element("scheduled_tasks")
    for task in items:
        root.append(_serialize_scheduled_task(task))
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def _parse_scheduled_task_item(elem: ET.Element) -> GppScheduledTask:
    action, common, ilt_filter, unknown_attrs, unknown_children, props, unknown_props_children = (
        _extract_common(elem, "scheduled_tasks")
    )
    element_variant: Literal["Task", "TaskV2"] = (
        "TaskV2" if _local_name(elem.tag) == "TaskV2" else "Task"
    )
    name = elem.get("name", "")
    if props is not None:
        name = props.get("name", name)
        task_xml = _extract_task_xml(props)
        program = props.get("program", "")
        arguments = props.get("arguments", "")
        start_in = props.get("startIn", "")
        if not program and task_xml:
            program, arguments, start_in = _project_from_task_xml(task_xml)
        trigger_type = _code_to_trigger_type(props.get("triggerType", "ONCE"))
        trigger_time = props.get("triggerTime", "")
        trigger_days = props.get("triggerDays", "")
        if "triggerType" not in props.attrib:
            projected = _project_triggers_from_task_xml(task_xml)
            if projected is not None:
                trigger_type, trigger_time, trigger_days = projected
        return GppScheduledTask(
            name=name,
            run_as=props.get("runAs", ""),
            program=program,
            arguments=arguments,
            start_in=start_in,
            enabled=props.get("enabled", "1") == "1",
            trigger_type=trigger_type,
            trigger_time=trigger_time,
            trigger_days=trigger_days,
            task_xml=task_xml,
            action=action,
            common=common,
            ilt_filter=ilt_filter,
            unknown_attrs=unknown_attrs,
            unknown_children=unknown_children,
            unknown_props_children=unknown_props_children,
            element_variant=element_variant,
        )
    return GppScheduledTask(
        name=name,
        action=action,
        common=common,
        ilt_filter=ilt_filter,
        unknown_attrs=unknown_attrs,
        unknown_children=unknown_children,
        unknown_props_children=unknown_props_children,
        element_variant=element_variant,
    )


def parse_gpp_scheduled_tasks(data: bytes) -> tuple[GppScheduledTask, ...]:
    """Parse Scheduled Tasks GPP XML bytes."""
    root = _bounded_parse(data)
    items: list[GppScheduledTask] = []
    for elem in root:
        if _local_name(elem.tag) in ("Task", "TaskV2"):
            items.append(_parse_scheduled_task_item(elem))
    return tuple(items)


# ---------------------------------------------------------------------------
# Immediate Tasks (Plan 024 WP-4)
# ---------------------------------------------------------------------------


def _serialize_immediate_task(task: GppImmediateTask) -> ET.Element:
    _deny_password(
        task.run_as_password,
        "run_as_password",
        f"immediate task {task.name!r}",
    )
    elem = _build_item_element(
        "immediate_tasks",
        item_name=task.name,
        action=task.action,
        common=task.common,
        ilt_filter=task.ilt_filter,
        unknown_attrs=task.unknown_attrs,
        unknown_children=task.unknown_children,
        unknown_props_children=task.unknown_props_children,
        props_attrs={
            "name": task.name,
            "runAs": task.run_as,
            "program": task.program,
            "arguments": task.arguments,
            "startIn": task.start_in,
        },
    )
    _append_task_xml_to_props(elem, task.task_xml)
    return elem


def serialize_gpp_immediate_tasks(
    items: tuple[GppImmediateTask, ...],
    scope: GppScope,  # noqa: ARG001
) -> bytes:
    """Serialize Immediate Tasks items to GPP XML bytes."""
    root = _build_root_element("immediate_tasks")
    for task in items:
        root.append(_serialize_immediate_task(task))
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def _parse_immediate_task_item(elem: ET.Element) -> GppImmediateTask:
    action, common, ilt_filter, unknown_attrs, unknown_children, props, unknown_props_children = (
        _extract_common(elem, "immediate_tasks")
    )
    name = elem.get("name", "")
    if props is not None:
        name = props.get("name", name)
        task_xml = _extract_task_xml(props)
        program = props.get("program", "")
        arguments = props.get("arguments", "")
        start_in = props.get("startIn", "")
        if not program and task_xml:
            program, arguments, start_in = _project_from_task_xml(task_xml)
        return GppImmediateTask(
            name=name,
            run_as=props.get("runAs", ""),
            program=program,
            arguments=arguments,
            start_in=start_in,
            task_xml=task_xml,
            action=action,
            common=common,
            ilt_filter=ilt_filter,
            unknown_attrs=unknown_attrs,
            unknown_children=unknown_children,
            unknown_props_children=unknown_props_children,
        )
    return GppImmediateTask(
        name=name,
        action=action,
        common=common,
        ilt_filter=ilt_filter,
        unknown_attrs=unknown_attrs,
        unknown_children=unknown_children,
        unknown_props_children=unknown_props_children,
    )


def parse_gpp_immediate_tasks(data: bytes) -> tuple[GppImmediateTask, ...]:
    """Parse Immediate Tasks GPP XML bytes."""
    root = _bounded_parse(data)
    return tuple(
        _parse_immediate_task_item(elem)
        for elem in _findall_local(root, "ImmediateTaskV2")
    )


# ---------------------------------------------------------------------------
# Root-level parse helpers (for parse_gpp_collection integration)
# ---------------------------------------------------------------------------

# Type alias for the return of root parse functions.
# Returns (items, root_unknown_attrs, root_unknown_children).
RootParseResult = tuple[
    tuple[object, ...],
    tuple[tuple[str, str], ...],
    tuple[str, ...],
]


def _parse_environment_root(data: bytes) -> RootParseResult:
    root = _bounded_parse(data)
    unknown_attrs, unknown_children = _capture_root_unknowns(root, "environment")
    items: tuple[object, ...] = tuple(
        _parse_environment_item(elem)
        for elem in _findall_local(root, "EnvironmentVariable")
    )
    return items, unknown_attrs, unknown_children


def _parse_ini_files_root(data: bytes) -> RootParseResult:
    root = _bounded_parse(data)
    unknown_attrs, unknown_children = _capture_root_unknowns(root, "ini_files")
    items: tuple[object, ...] = tuple(
        _parse_ini_item(elem)
        for elem in _findall_local(root, "Ini")
    )
    return items, unknown_attrs, unknown_children


def _parse_regional_options_root(data: bytes) -> RootParseResult:
    root = _bounded_parse(data)
    unknown_attrs, unknown_children = _capture_root_unknowns(root, "regional_options")
    items: tuple[object, ...] = tuple(
        _parse_regional_options_item(elem)
        for elem in _findall_local(root, "RegionalOptions")
    )
    return items, unknown_attrs, unknown_children


def _parse_power_options_root(data: bytes) -> RootParseResult:
    root = _bounded_parse(data)
    unknown_attrs, unknown_children = _capture_root_unknowns(root, "power_options")
    items: tuple[object, ...] = tuple(
        _parse_power_options_item(elem)
        for elem in _findall_local(root, "PowerScheme")
    )
    return items, unknown_attrs, unknown_children


def _parse_devices_root(data: bytes) -> RootParseResult:
    root = _bounded_parse(data)
    unknown_attrs, unknown_children = _capture_root_unknowns(root, "devices")
    items: tuple[object, ...] = tuple(
        _parse_device_item(elem)
        for elem in _findall_local(root, "Device")
    )
    return items, unknown_attrs, unknown_children


def _parse_folder_options_root(data: bytes) -> RootParseResult:
    root = _bounded_parse(data)
    unknown_attrs, unknown_children = _capture_root_unknowns(root, "folder_options")
    items: tuple[object, ...] = tuple(
        _parse_folder_options_item(elem)
        for elem in _findall_local(root, "GlobalFolderOptionsVista")
    )
    return items, unknown_attrs, unknown_children


def _parse_data_sources_root(data: bytes) -> RootParseResult:
    root = _bounded_parse(data)
    unknown_attrs, unknown_children = _capture_root_unknowns(root, "data_sources")
    items: tuple[object, ...] = tuple(
        _parse_data_source_item(elem)
        for elem in _findall_local(root, "DataSource")
    )
    return items, unknown_attrs, unknown_children


def _parse_drives_root(data: bytes) -> RootParseResult:
    root = _bounded_parse(data)
    unknown_attrs, unknown_children = _capture_root_unknowns(root, "drives")
    items: tuple[object, ...] = tuple(
        _parse_drive_item(elem)
        for elem in _findall_local(root, "Drive")
    )
    return items, unknown_attrs, unknown_children


def _parse_files_root(data: bytes) -> RootParseResult:
    root = _bounded_parse(data)
    unknown_attrs, unknown_children = _capture_root_unknowns(root, "files")
    items: tuple[object, ...] = tuple(
        _parse_file_item(elem)
        for elem in _findall_local(root, "File")
    )
    return items, unknown_attrs, unknown_children


def _parse_folders_root(data: bytes) -> RootParseResult:
    root = _bounded_parse(data)
    unknown_attrs, unknown_children = _capture_root_unknowns(root, "folders")
    items: tuple[object, ...] = tuple(
        _parse_folder_item(elem)
        for elem in _findall_local(root, "Folder")
    )
    return items, unknown_attrs, unknown_children


def _parse_network_shares_root(data: bytes) -> RootParseResult:
    root = _bounded_parse(data)
    unknown_attrs, unknown_children = _capture_root_unknowns(root, "network_shares")
    items: tuple[object, ...] = tuple(
        _parse_network_share_item(elem)
        for elem in _findall_local(root, "NetShare")
    )
    return items, unknown_attrs, unknown_children


def _parse_printers_root(data: bytes) -> RootParseResult:
    root = _bounded_parse(data)
    unknown_attrs, unknown_children = _capture_root_unknowns(root, "printers")
    items: tuple[object, ...] = tuple(
        _parse_printer_item(elem)
        for elem in _findall_local(root, "SharedPrinter")
    )
    return items, unknown_attrs, unknown_children


def _parse_shortcuts_root(data: bytes) -> RootParseResult:
    root = _bounded_parse(data)
    unknown_attrs, unknown_children = _capture_root_unknowns(root, "shortcuts")
    items: tuple[object, ...] = tuple(
        _parse_shortcut_item(elem)
        for elem in _findall_local(root, "Shortcut")
    )
    return items, unknown_attrs, unknown_children


def _parse_applications_root(data: bytes) -> RootParseResult:
    root = _bounded_parse(data)
    unknown_attrs, unknown_children = _capture_root_unknowns(root, "applications")
    items: tuple[object, ...] = tuple(
        _parse_application_item(elem)
        for elem in _findall_local(root, "Application")
    )
    return items, unknown_attrs, unknown_children


def _parse_services_root(data: bytes) -> RootParseResult:
    root = _bounded_parse(data)
    unknown_attrs, unknown_children = _capture_root_unknowns(root, "services")
    items: tuple[object, ...] = tuple(
        _parse_service_item(elem)
        for elem in _findall_local(root, "NTService")
    )
    return items, unknown_attrs, unknown_children


def _parse_local_users_root(data: bytes) -> RootParseResult:
    root = _bounded_parse(data)
    unknown_attrs, unknown_children = _capture_root_unknowns(root, "local_users")
    items: tuple[object, ...] = tuple(
        _parse_local_user_item(elem)
        for elem in _findall_local(root, "User")
    )
    return items, unknown_attrs, unknown_children


def _parse_local_groups_root(data: bytes) -> RootParseResult:
    root = _bounded_parse(data)
    unknown_attrs, unknown_children = _capture_root_unknowns(root, "local_groups")
    items: tuple[object, ...] = tuple(
        _parse_local_group_item(elem)
        for elem in _findall_local(root, "Group")
    )
    return items, unknown_attrs, unknown_children


def _parse_scheduled_tasks_root(data: bytes) -> RootParseResult:
    root = _bounded_parse(data)
    unknown_attrs, unknown_children = _capture_root_unknowns(root, "scheduled_tasks")
    items: list[object] = []
    for elem in root:
        if _local_name(elem.tag) in ("Task", "TaskV2"):
            items.append(_parse_scheduled_task_item(elem))
    return tuple(items), unknown_attrs, unknown_children


def _parse_immediate_tasks_root(data: bytes) -> RootParseResult:
    root = _bounded_parse(data)
    unknown_attrs, unknown_children = _capture_root_unknowns(root, "immediate_tasks")
    items: tuple[object, ...] = tuple(
        _parse_immediate_task_item(elem)
        for elem in _findall_local(root, "ImmediateTaskV2")
    )
    return items, unknown_attrs, unknown_children


# Map of file path suffix -> list of (adapter_key, root parse function) pairs.
# Used by parse_gpp_collection to dispatch to the correct parser(s).
# MS-GPPREF folds local users and local groups into a single Groups\Groups.xml
# file, and immediate tasks into ScheduledTasks\ScheduledTasks.xml, so some
# suffixes map to multiple adapter keys.
ROOT_PARSE_FUNCTIONS: dict[str, list[tuple[str, Callable[[bytes], RootParseResult]]]] = {
    "EnvironmentVariables/EnvironmentVariables.xml": [("environment", _parse_environment_root)],
    "IniFiles/IniFiles.xml": [("ini_files", _parse_ini_files_root)],
    "RegionalOptions/RegionalOptions.xml": [("regional_options", _parse_regional_options_root)],
    "PowerOptions/PowerOptions.xml": [("power_options", _parse_power_options_root)],
    "Devices/Devices.xml": [("devices", _parse_devices_root)],
    "FolderOptions/FolderOptions.xml": [("folder_options", _parse_folder_options_root)],
    "DataSources/DataSources.xml": [("data_sources", _parse_data_sources_root)],
    "Drives/Drives.xml": [("drives", _parse_drives_root)],
    "Files/Files.xml": [("files", _parse_files_root)],
    "Folders/Folders.xml": [("folders", _parse_folders_root)],
    "NetworkShares/NetworkShares.xml": [("network_shares", _parse_network_shares_root)],
    "Printers/Printers.xml": [("printers", _parse_printers_root)],
    "Shortcuts/Shortcuts.xml": [("shortcuts", _parse_shortcuts_root)],
    "Applications/Applications.xml": [("applications", _parse_applications_root)],
    # Privileged execution adapters (Plan 024 WP-4).
    "Services/Services.xml": [("services", _parse_services_root)],
    # MS-GPPREF folds local users into Groups\Groups.xml.  <Group> elements in
    # this file are already parsed by parse_gpp_groups (as GppGroup), so the
    # adapter parser only extracts <User> elements (as GppLocalUser).
    "Groups/Groups.xml": [
        ("local_users", _parse_local_users_root),
    ],
    # MS-GPPREF folds immediate tasks into ScheduledTasks\ScheduledTasks.xml.
    # <Task> and <ImmediateTaskV2> have distinct element names, so both are
    # parsed without ambiguity.
    "ScheduledTasks/ScheduledTasks.xml": [
        ("scheduled_tasks", _parse_scheduled_tasks_root),
        ("immediate_tasks", _parse_immediate_tasks_root),
    ],
}

# Map of adapter_key -> per-item serialize function (for _build_adapter_root).
_ITEM_SERIALIZE_FUNCTIONS: dict[str, Callable[..., ET.Element]] = {
    "environment": _serialize_environment,
    "ini_files": _serialize_ini,
    "regional_options": _serialize_regional_options,
    "power_options": _serialize_power_options,
    "devices": _serialize_device,
    "folder_options": _serialize_folder_options,
    "data_sources": _serialize_data_source,
    "drives": _serialize_drive,
    "files": _serialize_file,
    "folders": _serialize_folder,
    "network_shares": _serialize_network_share,
    "printers": _serialize_printer,
    "shortcuts": _serialize_shortcut,
    "applications": _serialize_application,
    "services": _serialize_service,
    "local_users": _serialize_local_user,
    "scheduled_tasks": _serialize_scheduled_task,
    "immediate_tasks": _serialize_immediate_task,
}


def _build_adapter_root(
    adapter_key: str,
    items: tuple[Any, ...],
    scope: GppScope,  # noqa: ARG001 - reserved for scope-specific CLSIDs
) -> ET.Element:
    """Build the root ET.Element for an adapter without serializing to bytes.

    Used by _serialize_adapter_files to merge multiple adapters that share a
    single file path (e.g. local_users + local_groups → Groups\\Groups.xml).
    """
    root_tag, root_clsid, _, _ = _ADAPTER_META[adapter_key]
    root = ET.Element(_ns(root_tag))
    root.set("clsid", root_clsid)
    serialize_item_fn = _ITEM_SERIALIZE_FUNCTIONS[adapter_key]
    for item in items:
        root.append(serialize_item_fn(item))
    return root


# Map of adapter_key -> file path suffix (for serialize_gpp).
ADAPTER_FILE_PATHS: dict[str, str] = _ADAPTER_FILE_PATHS

# Map of adapter_key -> serialize function (for serialize_gpp).
ADAPTER_SERIALIZE_FUNCTIONS: dict[str, Callable[..., bytes]] = {
    "environment": serialize_gpp_environment,
    "ini_files": serialize_gpp_ini_files,
    "regional_options": serialize_gpp_regional_options,
    "power_options": serialize_gpp_power_options,
    "devices": serialize_gpp_devices,
    "folder_options": serialize_gpp_folder_options,
    "data_sources": serialize_gpp_data_sources,
    "drives": serialize_gpp_drives,
    "files": serialize_gpp_files,
    "folders": serialize_gpp_folders,
    "network_shares": serialize_gpp_network_shares,
    "printers": serialize_gpp_printers,
    "shortcuts": serialize_gpp_shortcuts,
    "applications": serialize_gpp_applications,
    # Privileged execution adapters (Plan 024 WP-4).
    "services": serialize_gpp_services,
    "local_users": serialize_gpp_local_users,
    "scheduled_tasks": serialize_gpp_scheduled_tasks,
    "immediate_tasks": serialize_gpp_immediate_tasks,
}

# Ordered list of adapter keys (for dict serialization).
ADAPTER_KEYS: tuple[str, ...] = _ADAPTER_KEYS
