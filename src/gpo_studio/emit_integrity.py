"""Emit-time integrity: what every writer refuses to write, and why.

Final-r8 found a principal holding a line feed written into ``GptTmpl.inf`` as
a new privilege grant, and a service name doing the same in the
object-security template -- writers that trusted their input. Field
inventories kept missing fields (an item ``uid``, ``FilterRunOnce@id``, a
power scheme GUID), so the rule now lives in the writers: each one checks
every name and value it emits as it emits it, against one table per format
here, and refuses what does not fit. A refusal is an error everywhere: the
store refuses the mutation, an export or render answers 422.

These per-writer checks are defence in depth. The boundary is egress
(:mod:`gpo_studio.egress`): every API route that returns an artifact is
declared with its format and validates the artifact's final form before it
responds, and ``tests/test_egress.py`` enumerates the routes at run time. A
hand-kept list of writers (round 8) and a static scan of the source (round 9)
were both bypassed by ordinary Python; the response is where they converge.
In outline:

* ``gpp.serialize_gpp`` -- GPP XML, typed and retained items and imported
  files alike: :data:`GPP_XML`;
* ``export._xml_to_bytes`` -- the GPMC backup documents ``Backup.xml``,
  ``bkupInfo.xml`` and ``manifest.xml``: :data:`BACKUP_XML`;
* ``security_template.format_security_template`` -- ``GptTmpl.inf`` for the
  policy-family and object-security render endpoints: :data:`INF_SECTIONS`,
  :data:`INF_KEYS` and :func:`inf_problems`, the lossless path included;
* ``export._native_ini_bytes`` -- ``scripts.ini`` / ``psscripts.ini``:
  :data:`SCRIPTS_INI`;
* PowerShell -- the plan (``plan.ps1``, ``apply.ps1``) and the AD discovery
  scripts -- every value through :func:`ps_literal`, the one quoting
  primitive (:data:`PS_PARAMETERS`, :data:`DISCOVERY_PARAMETERS`); plan
  comments through :func:`escape_line`;
* ``registry_pol.serialize`` -- ``Registry.pol``: :data:`REGISTRY_POL`;
* ``firewall_policy`` rule strings (``|``-delimited REG_SZ values, then
  ``Registry.pol`` and the plan): one line, no ``|``;
* ``report.policy_report`` -- ``report.txt``: :func:`escape_line`.

JSON outputs (manifests, publication and restore plans) are not listed: JSON
escapes every control character, so no value can change their structure.

**Characters.** Every emitted value refuses Unicode controls (Cc), format
characters (Cf, the bidirectional overrides and zero-width characters among
them), line and paragraph separators (Zl, Zp) and lone surrogates (Cs) --
:func:`line_problem`. The exceptions are formats that encode a character
explicitly, each documented where it is allowed:

* XML attribute values and element text: TAB and LF, which the XML writer
  emits as ``&#9;``/``&#10;`` in attributes and reads back exactly. Allowed
  only in values classified ``text`` (a description, a message body); every
  other XML value is ``line``. CR is never allowed: XML reads it back as LF.
* PowerShell: a ``text`` value (``-Comment``, registry data) may hold any
  character; each control, format or separator character is written as a
  ``[char]0xNNNN`` term of a concatenation, so a CRLF in registry data is
  carried exactly and every statement stays one physical line.
* ``Registry.pol``: names are NUL-terminated UTF-16 followed by ``;``, so a
  ``;`` in a name is data (MS-GPREG 2.2.1) and only NUL is refused; value data
  is length-prefixed, so every character but NUL is encoded.
* The plain-text report: a refused character is written as a visible
  ``\\uXXXX`` escape, so a value can never start a line of its own.

Identity-typed values additionally go through the shared validators in
:mod:`gpo_studio.identity_values` (SID, starred SID, DN, GUID, name, time).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, assert_never, get_args

from .identity_values import (
    date_problem,
    datetime_problem,
    dn_problem,
    guid_problem,
    name_problem,
    principal_problem,
    security_principal_problem,
    sid_problem,
    time_problem,
)
from .xml_safety import parse_xml_bounded

#: Categories no emitted value may hold (outside the documented encodings).
_LINE_BREAKING = frozenset({"Cc", "Cf", "Zl", "Zp", "Cs"})



def _refused(value: str, allowed: str = "") -> list[str]:
    return [
        f"U+{ord(character):04X}"
        for character in value
        if character not in allowed and unicodedata.category(character) in _LINE_BREAKING
    ]


def line_problem(value: str) -> str | None:
    """Why *value* cannot be one line of any format Studio writes, or ``None``."""
    found = _refused(value)
    if found:
        return f"contains {', '.join(sorted(set(found)))} (a control, format or separator)"
    return None


def text_problem(value: str) -> str | None:
    """As :func:`line_problem`, but TAB and LF are allowed (an encoded multi-line value)."""
    found = _refused(value, allowed="\t\n")
    if found:
        return f"contains {', '.join(sorted(set(found)))} (a control, format or separator)"
    return None


_TOKEN = re.compile(r"[A-Za-z0-9_.:+\-]*", re.ASCII)
_NUMBER = re.compile(r"-?[0-9]+", re.ASCII)
_FLAG = re.compile(r"[01]", re.ASCII)
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{1,2}:[0-9]{2}:[0-9]{2}", re.ASCII)
_GUID_LIST = re.compile(r"(?:\[(?:\{[0-9A-Fa-f-]{36}\})+\])*", re.ASCII)
_HEX_BYTES = re.compile(r"[0-9A-Fa-f]{2}(?: [0-9A-Fa-f]{2})*", re.ASCII)
_RIGHT = re.compile(r"Se[A-Za-z]+", re.ASCII)


def _pattern(pattern: re.Pattern[str], what: str) -> Callable[[str], str | None]:
    def check(value: str) -> str | None:
        return None if pattern.fullmatch(value) else f"is not {what}"

    return check


Kind = Literal[
    "sid",
    "principal",
    "name",
    "ou",
    "dn",
    "guid",
    "braced_guid",
    "guid_list",
    "time",
    "datetime",
    "date",
    "timestamp",
    "flag",
    "token",
    "number",
    "hex_bytes",
    "right",
    "line",
    "text",
]


def _check(kind: Kind) -> Callable[[str], str | None]:
    """The check for *kind*. Exhaustive: a ``Kind`` added without a check is a
    type error here, not a ``KeyError`` at the first value (DeepSeek final-r9 N7)."""
    match kind:
        case "sid":
            return sid_problem
        case "principal":
            return principal_problem
        case "name":
            return name_problem
        case "ou":
            return lambda value: dn_problem(value, min_rdns=1)
        case "dn":
            return dn_problem
        case "guid":
            return guid_problem
        case "braced_guid":
            return lambda value: guid_problem(value, braces="required")
        case "guid_list":
            return _pattern(_GUID_LIST, "a list of bracketed GUID pairs")
        case "time":
            return time_problem
        case "datetime":
            return datetime_problem
        case "date":
            return date_problem
        case "timestamp":
            return _pattern(_TIMESTAMP, "a timestamp (YYYY-MM-DD H:MM:SS)")
        case "flag":
            return _pattern(_FLAG, "0 or 1")
        case "token":
            return _pattern(_TOKEN, "a token (letters, digits and _.:+-)")
        case "number":
            return _pattern(_NUMBER, "an integer")
        case "hex_bytes":
            return _pattern(_HEX_BYTES, "space-separated hexadecimal bytes")
        case "right":
            return _pattern(_RIGHT, "a user right constant (Se...)")
        case "line":
            return line_problem
        case "text":
            return text_problem
        case _:
            assert_never(kind)


CHECKS: Mapping[Kind, Callable[[str], str | None]] = {
    kind: _check(kind) for kind in get_args(Kind)
}


@dataclass(frozen=True, slots=True)
class Field:
    kind: Kind
    #: The value may not be empty (an empty task boundary is not "unset").
    required: bool = False


def value_problem(field: Field, value: str) -> str | None:
    """Why *value* cannot be emitted as *field*, or ``None``."""
    if value == "":
        return "is empty" if field.required else None
    return CHECKS[field.kind](value)


class EmitError(ValueError):
    """A writer was asked to emit what its format cannot carry intact."""

    def __init__(self, problems: list[EmitProblem]) -> None:
        self.problems = problems
        super().__init__("; ".join(str(p) for p in problems[:5]))


@dataclass(frozen=True, slots=True)
class EmitProblem:
    where: str  # the file or artifact
    name: str  # the emitted name: element@attribute, [section] key, -Parameter
    value: str
    problem: str

    def __str__(self) -> str:
        return f"{self.where}: {self.name} {self.value!r} {self.problem}"


# ---------------------------------------------------------------------------
# XML: GPP preference files and GPMC backup documents
# ---------------------------------------------------------------------------

#: Keys are (root element, element, attribute); ``#text`` is element text.
#: ``*`` matches any root or element. Lookup is exact, then any root, then any
#: root and element -- the last only for attributes every item carries.
XmlTable = Mapping[tuple[str, str, str], Field]

_LINE = Field("line")
_TEXT = Field("text")
_TOKEN_F = Field("token")
_FLAG_F = Field("flag")
_NUMBER_F = Field("number")


def _family(root: str, element: str, **attributes: Kind) -> dict[tuple[str, str, str], Field]:
    return {(root, element, name): Field(kind) for name, kind in attributes.items()}


#: ``class`` is a Python keyword, so it cannot be a keyword argument.
_OS_CLASS: dict[str, Kind] = {"class": "token"}
#: Power Options writes each setting twice, on AC and on battery (DC).
_POWER_SETTINGS: dict[str, Kind] = {
    f"{name}{side}": "token"
    for side in ("AC", "DC")
    for name in (
        "adaptive", "allowHybridSleep", "critBatAction", "critBatteryLvl", "displayOff",
        "hibernate", "lidClose", "linkPwrMgmt", "lowBatteryAction", "lowBatteryLvl",
        "lowBatteryNot", "pbAction", "procStateMax", "procStateMin", "requireWakePwd",
        "sleepAfter", "strtMenuAction", "turnOffHD",
    )
}

GPP_XML: XmlTable = {
    # Every item and root: identity, timestamps, the common options.
    ("*", "*", "clsid"): Field("braced_guid"),
    ("*", "*", "uid"): Field("braced_guid"),
    ("*", "*", "changed"): Field("timestamp"),
    ("*", "*", "image"): _NUMBER_F,
    ("*", "*", "bypassErrors"): _FLAG_F,
    ("*", "*", "disabled"): _FLAG_F,
    ("*", "*", "removePolicy"): _FLAG_F,
    ("*", "*", "userContext"): _FLAG_F,
    ("*", "*", "name"): _LINE,
    ("*", "*", "status"): _LINE,
    ("*", "*", "desc"): _TEXT,
    ("*", "Properties", "action"): _TOKEN_F,
    # Item-level targeting.
    ("*", "FilterRunOnce", "id"): Field("braced_guid"),
    **{("*", f, "bool"): _TOKEN_F for f in (
        "FilterCollection", "FilterComputerName", "FilterDate", "FilterDiskSpace",
        "FilterDomain", "FilterFile", "FilterFolder", "FilterGroup", "FilterIpRange",
        "FilterLanguage", "FilterOrgUnit", "FilterOs", "FilterRegistry", "FilterRunOnce",
        "FilterService", "FilterUser", "FilterVariable", "FilterWmi",
    )},
    **{("*", f, "not"): _FLAG_F for f in (
        "FilterCollection", "FilterComputerName", "FilterDate", "FilterDiskSpace",
        "FilterDomain", "FilterFile", "FilterFolder", "FilterGroup", "FilterIpRange",
        "FilterLanguage", "FilterOrgUnit", "FilterOs", "FilterRegistry", "FilterRunOnce",
        "FilterService", "FilterUser", "FilterVariable", "FilterWmi",
    )},
    ("*", "FilterCollection", "hidden"): _FLAG_F,
    ("*", "FilterRunOnce", "hidden"): _FLAG_F,
    ("*", "FilterOs", "hidden"): _FLAG_F,
    **_family("*", "FilterGroup", name="principal", sid="sid", localGroup="flag",
              primaryGroup="flag", userContext="flag"),
    **_family("*", "FilterUser", name="principal", sid="sid"),
    **_family("*", "FilterOrgUnit", name="ou", directMember="flag", userContext="flag"),
    **_family("*", "FilterDomain", name="name", userContext="flag"),
    **_family("*", "FilterComputerName", name="name", type="token"),
    **_family("*", "FilterDate", startDate="date", endDate="date", period="token"),
    **_family("*", "FilterDiskSpace", min="number", drive="token"),
    **_family("*", "FilterFile", path="line", type="token"),
    **_family("*", "FilterFolder", path="line"),
    **_family("*", "FilterIpRange", min="token", max="token", useIPv6="flag"),
    **_family("*", "FilterLanguage", language="number", default="flag", system="flag",
              native="flag"),
    **_family("*", "FilterOs", **_OS_CLASS, version="token", type="token",
              edition="token", sp="token"),
    **_family("*", "FilterRegistry", key="line", valueName="line", hive="token",
              type="token", valueType="token", valueData="line", subtype="token"),
    **_family("*", "FilterService", name="name"),
    **_family("*", "FilterVariable", variableName="line", value="line"),
    **_family("*", "FilterWmi", query="line", nameSpace="line", property="line",
              variableName="line"),
    # Families.
    **_family("Applications", "Properties", commandLine="line", path="line",
              runAs="principal", name="line"),
    **_family("DataSources", "Properties", attributes="line", description="text",
              driver="line", dsn="line", userDsn="flag"),
    **_family("Devices", "Properties", deviceAction="token", deviceClass="line",
              deviceName="line"),
    **_family("Drives", "Properties", allDrives="token", label="line", letter="token",
              path="line", persistent="flag", thisDrive="token", useLetter="flag",
              userName="name", username="name"),
    **_family("EnvironmentVariables", "Properties", name="line", partial="flag", user="flag",
              value="line"),
    **_family("Files", "Properties", archive="flag", hidden="flag", readOnly="flag",
              suppress="flag", fromPath="line", targetPath="line"),
    **_family("FolderOptions", "Properties", launchInSeparate="flag", showExtensions="flag",
              showFullPath="flag", showHidden="flag", showSuperHidden="flag"),
    **_family("Folders", "Properties", archive="flag", deleteFiles="flag", deleteFolder="flag",
              deleteIgnoreErrors="flag", deleteReadOnly="flag", deleteSubFolders="flag",
              hidden="flag", path="line", readOnly="flag"),
    **_family("Groups", "Member", action="token", name="principal", sid="sid"),
    **_family("Groups", "Properties", acctDisabled="flag", acctLockedOut="flag",
              passwordNeverExpires="flag", userCannotChangePassword="flag",
              deleteAllGroups="flag", deleteAllUsers="flag", removeAccounts="flag",
              description="text", fullName="line", groupName="name", groupSid="sid",
              newName="name", userAction="token", userName="name"),
    **_family("IniFiles", "Properties", path="line", property="line", section="line",
              value="line"),
    **_family("NetworkShareSettings", "Properties", comment="line", path="line",
              userLimit="number", name="line"),
    **_family("NTServices", "Properties", accountName="name", append="flag", args="line",
              firstFailure="token", secondFailure="token", thirdFailure="token",
              interact="flag", program="line", resetFailCountDelay="number",
              restartComputerDelay="number", restartServiceDelay="number",
              restartMessage="text", serviceAction="token", serviceName="name",
              startupType="token", timeout="number"),
    **_family("PowerOptions", "Properties", nameGuid="guid", schemeGuid="guid",
              schemeName="line", default="flag", acPowerSetting="token",
              dcPowerSetting="token",
              **_POWER_SETTINGS),
    **_family("Printers", "Properties", comment="line", default="flag", deleteAll="flag",
              deleteMaps="flag", location="line", path="line", persistent="flag",
              port="line", skipLocal="flag"),
    **_family("Regional", "Properties", userCurrency="line", userDate="line", userIME="line",
              userLocale="line", userNumber="line", userTime="line", userTimeZone="line"),
    **_family("RegistrySettings", "Properties", default="flag", displayDecimal="flag",
              hive="token", key="line", type="token", value="text", name="line"),
    ("RegistrySettings", "Value", "#text"): _LINE,
    **_family("ScheduledTasks", "Properties", arguments="line", enabled="flag",
              logonType="token", program="line", runAs="principal", startIn="line",
              triggerDays="line", triggerTime="time", triggerType="token", name="line"),
    **_family("Shortcuts", "Properties", arguments="line", comment="line", iconIndex="number",
              iconPath="line", pidl="token", shortcutKey="number", shortcutPath="line",
              startIn="line", targetPath="line", targetType="token", window="token"),
    # The Task Scheduler payload a TaskV2 or ImmediateTaskV2 carries.
    ("ScheduledTasks", "Task", "version"): _TOKEN_F,
    ("ScheduledTasks", "Actions", "Context"): _TOKEN_F,
    ("ScheduledTasks", "Principal", "id"): _TOKEN_F,
    ("ScheduledTasks", "StartBoundary", "#text"): Field("datetime", required=True),
    ("ScheduledTasks", "EndBoundary", "#text"): Field("datetime", required=True),
    ("ScheduledTasks", "UserId", "#text"): Field("principal", required=True),
    ("ScheduledTasks", "GroupId", "#text"): Field("principal", required=True),
    **{("ScheduledTasks", tag, "#text"): _TOKEN_F for tag in (
        "AllowHardTerminate", "AllowStartOnDemand", "DeleteExpiredTaskAfter",
        "DisallowStartIfOnBatteries", "Duration", "Enabled", "ExecutionTimeLimit",
        "Hidden", "Interval", "LogonType", "MultipleInstancesPolicy", "RestartOnIdle",
        "RunLevel", "RunOnlyIfIdle", "RunOnlyIfNetworkAvailable", "StartWhenAvailable",
        "StateChange", "StopIfGoingOnBatteries", "StopOnIdleEnd", "WaitTimeout",
        "WakeToRun",
    )},
    **{("ScheduledTasks", tag, "#text"): _NUMBER_F for tag in (
        "Count", "Day", "DaysInterval", "Priority", "WeeksInterval",
    )},
    **{("ScheduledTasks", tag, "#text"): _LINE for tag in (
        "Arguments", "Author", "Command", "From", "Server", "Subject", "Title", "To",
        "WorkingDirectory",
    )},
    ("ScheduledTasks", "Body", "#text"): _TEXT,
    ("ScheduledTasks", "Description", "#text"): _TEXT,
}

#: The elements Studio's typed writers produce, by GPP file root, and the
#: Microsoft names this repository sources: the 28 item-level targeting
#: elements of the Plan 021 inventory (docs/plan-021/capability-inventory.md,
#: from MS-GPPREF and the GPMC targeting reference) and the GPP item tags
#: `oracle_evidence._GPP_ITEM_TAGS` lists (``ImmediateTask``, ``LocalPrinter``,
#: ``PortPrinter``). It judges what the typed writers write. Retained content --
#: what an import stored, and only an import can add (the store's provenance
#: invariant, `store._retained_fragments`) -- is judged by that provenance at
#: egress instead: its elements are allowed because the GPO holds them
#: (`egress.retained_element_names`), so a valid Microsoft element the list
#: does not name is still exported as imported (Sol and DeepSeek final-r12).
#: ``tests/test_egress.py`` holds the list to the writers' output over the
#: corpus and to the sourced names.
MICROSOFT_FILTER_ELEMENTS = frozenset({
    "FilterCollection", "FilterBattery", "FilterComputer", "FilterCpu", "FilterDate",
    "FilterDisk", "FilterDomain", "FilterVariable", "FilterFile", "FilterIpRange",
    "FilterLanguage", "FilterLdap", "FilterMacRange", "FilterMsi", "FilterDialup",
    "FilterOs", "FilterOrgUnit", "FilterPcmcia", "FilterPortable", "FilterProcessingMode",
    "FilterRam", "FilterRegistry", "FilterGroup", "FilterSite", "FilterTerminalSession",
    "FilterTime", "FilterUser", "FilterWmi",
})
_FILTER_ELEMENTS = MICROSOFT_FILTER_ELEMENTS | frozenset({
    # What Studio's writers and GPMC's captures add: the container, the
    # apply-once filter, and the names Studio's typed predicates write.
    "Filters", "FilterComputerName", "FilterDiskSpace", "FilterFolder", "FilterRunOnce",
    "FilterService",
})
_TASK_PAYLOAD_ELEMENTS = frozenset({
    "Actions", "AllowHardTerminate", "AllowStartOnDemand", "April", "Arguments", "August",
    "Author", "Body", "CalendarTrigger", "Command", "Count", "Day", "DaysInterval",
    "DaysOfMonth", "DaysOfWeek", "December", "DeleteExpiredTaskAfter", "Description",
    "DisallowStartIfOnBatteries", "Duration", "Enabled", "EndBoundary", "Exec",
    "ExecutionTimeLimit", "February", "From", "HeaderFields", "Hidden", "IdleSettings",
    "Interval", "January", "July", "June", "LogonType", "March", "May", "Months",
    "MultipleInstancesPolicy", "November", "October", "Principal", "Principals", "Priority",
    "RegistrationInfo", "RestartOnFailure", "RestartOnIdle", "RunLevel", "RunOnlyIfIdle",
    "RunOnlyIfNetworkAvailable", "ScheduleByDay", "ScheduleByMonth", "ScheduleByWeek",
    "SendEmail", "September", "Server", "SessionStateChangeTrigger", "Settings",
    "ShowMessage", "StartBoundary", "StartWhenAvailable", "StateChange",
    "StopIfGoingOnBatteries", "StopOnIdleEnd", "Subject", "Task", "TimeTrigger", "Title",
    "To", "Triggers", "UserId", "WaitTimeout", "WakeToRun", "WeeksInterval",
    "WorkingDirectory",
    "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
})
GPP_ELEMENTS: Mapping[str, frozenset[str]] = {
    root: frozenset({root, "Properties", *_FILTER_ELEMENTS, *items})
    for root, items in {
        "Applications": {"Application"},
        "DataSources": {"DataSource"},
        "Devices": {"Device"},
        "Drives": {"Drive"},
        "EnvironmentVariables": {"EnvironmentVariable"},
        "Files": {"File"},
        "FolderOptions": {"GlobalFolderOptionsVista"},
        "Folders": {"Folder"},
        "Groups": {"Group", "Member", "Members", "User"},
        "IniFiles": {"Ini"},
        "NTServices": {"NTService"},
        "NetworkShareSettings": {"NetShare"},
        "PowerOptions": {"GlobalPowerOptionsV2", "PowerScheme"},
        "Printers": {"SharedPrinter", "LocalPrinter", "PortPrinter"},
        "Regional": {"RegionalOptions"},
        "RegistrySettings": {"Registry", "Value", "Values"},
        "ScheduledTasks": {
            "TaskV2", "ImmediateTaskV2", "Task", "ImmediateTask", *_TASK_PAYLOAD_ELEMENTS,
        },
        "Shortcuts": {"Shortcut"},
    }.items()
}


def gpp_element_problems(
    where: str, data: bytes, retained: frozenset[str] = frozenset()
) -> list[EmitProblem]:
    """Every element of a GPP file outside its root's vocabulary and the
    *retained* element names the GPO's imported content holds."""
    size = max(len(data), 1)
    root = parse_xml_bounded(data, max_size=size, max_attr_length=size, max_text_length=size)
    name = _local(root.tag)
    allowed = GPP_ELEMENTS.get(name)
    if allowed is None:
        return [EmitProblem(where, name, "", "is not a GPP file root Studio writes")]
    return [
        EmitProblem(where, _local(element.tag), "", f"is not an element of {name}")
        for element in root.iter()
        if _local(element.tag) not in allowed and _local(element.tag) not in retained
    ]


BACKUP_XML: XmlTable = {
    ("*", "*", "type"): _TOKEN_F,
    ("*", "*", "version"): _TOKEN_F,
    ("*", "GroupPolicyExtension", "ID"): Field("braced_guid"),
    ("*", "GroupPolicyExtension", "DescName"): _LINE,
    ("*", "FSObjectDir", "Location"): _LINE,
    ("*", "FSObjectDir", "Path"): _LINE,
    ("*", "FSObjectDir", "SourceExpandedPath"): _LINE,
    ("*", "FSObjectFile", "Location"): _LINE,
    ("*", "FSObjectFile", "Path"): _LINE,
    ("*", "FSObjectFile", "SourceExpandedPath"): _LINE,
    ("*", "ID", "#text"): Field("braced_guid", required=True),
    ("*", "GPOGuid", "#text"): Field("braced_guid", required=True),
    ("*", "GPODomainGuid", "#text"): Field("braced_guid", required=True),
    ("*", "DisplayName", "#text"): _LINE,
    ("*", "GPODisplayName", "#text"): _LINE,
    ("*", "Domain", "#text"): Field("name"),
    ("*", "GPODomain", "#text"): Field("name"),
    ("*", "GPODomainController", "#text"): Field("name"),
    ("*", "Comment", "#text"): _TEXT,
    ("*", "BackupTime", "#text"): Field("datetime"),
    ("*", "MachineExtensionGuids", "#text"): Field("guid_list"),
    ("*", "UserExtensionGuids", "#text"): Field("guid_list"),
    ("*", "MachineVersionNumber", "#text"): _NUMBER_F,
    ("*", "UserVersionNumber", "#text"): _NUMBER_F,
    ("*", "Options", "#text"): _NUMBER_F,
    ("*", "SecurityDescriptor", "#text"): Field("hex_bytes"),
    ("*", "WMIFilter", "#text"): _LINE,
}


def _local(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def classify_xml(table: XmlTable, root: str, element: str, attribute: str) -> Field | None:
    """The table's field for an emitted XML name, or ``None`` if it has none."""
    for key in ((root, element, attribute), ("*", element, attribute)):
        if key in table:
            return table[key]
    if attribute != "#text" and ("*", "*", attribute) in table:
        return table[("*", "*", attribute)]
    return None


def xml_names(data: bytes) -> list[tuple[str, str, str, str]]:
    """Every (root, element, attribute-or-#text, value) an XML document holds.

    Element text is listed for leaf elements (``#text``, possibly empty) and,
    for elements with children, only when it is not layout whitespace.
    """
    size = max(len(data), 1)
    root = parse_xml_bounded(
        data, max_size=size, max_attr_length=size, max_text_length=size
    )
    root_name = _local(root.tag)
    found: list[tuple[str, str, str, str]] = []
    for element in root.iter():
        tag = _local(element.tag)
        for attribute, value in element.attrib.items():
            found.append((root_name, tag, _local(attribute), value))
        text = element.text or ""
        if len(element) == 0 or text.strip(" \t\r\n"):
            found.append((root_name, tag, "#text", text))
        for child in element:
            tail = child.tail or ""
            if tail.strip(" \t\r\n"):
                found.append((root_name, tag, "#tail", tail))
    return found


def xml_problems(table: XmlTable, where: str, data: bytes) -> list[EmitProblem]:
    """Every emitted XML value its table (or the line rule) refuses.

    A name the table does not classify is held to :func:`line_problem`: a
    retained vendor attribute is written, but never with a line break in it.
    A leaf element no table names may be empty; a classified one only if its
    field allows it.
    """
    problems: list[EmitProblem] = []
    for root, element, attribute, value in xml_names(data):
        field = classify_xml(table, root, element, attribute)
        if field is None:
            if attribute == "#text" and value == "":
                continue
            problem = line_problem(value)
        else:
            problem = value_problem(field, value)
        if problem is not None:
            name = f"{element}" if attribute == "#text" else f"{element}@{attribute}"
            problems.append(EmitProblem(where, name, value, problem))
    return problems


def check_xml(table: XmlTable, where: str, data: bytes) -> bytes:
    problems = xml_problems(table, where, data)
    if problems:
        raise EmitError(problems)
    return data


# ---------------------------------------------------------------------------
# INF security templates (GptTmpl.inf)
# ---------------------------------------------------------------------------

#: Lower-cased section -> (key field, value field). Row sections write
#: ``"key",value``; the others ``key = value``.
INF_SECTIONS: Mapping[str, tuple[Field, Field]] = {
    "unicode": (_TOKEN_F, _TOKEN_F),
    "version": (_TOKEN_F, _LINE),
    "system access": (_TOKEN_F, _LINE),
    "kerberos policy": (_TOKEN_F, _NUMBER_F),
    "event audit": (_TOKEN_F, _NUMBER_F),
    "privilege rights": (Field("right"), Field("line")),
    "registry values": (_LINE, _LINE),
    "registry keys": (_LINE, _LINE),
    "file security": (_LINE, _LINE),
    "service general setting": (Field("name"), _LINE),
    "group membership": (_LINE, _LINE),
}
#: The keys each section may hold: a closed vocabulary where Windows defines
#: one, a pattern where the key names a right, path, service or principal. The
#: completeness test derives the keys the writers emit and requires each to be
#: here, so a writer gaining a setting is a decision, not a silent addition
#: (Sol final-r9 B2). The writer refuses a key outside its section's set too.
INF_KEYS: Mapping[str, frozenset[str] | re.Pattern[str]] = {
    "unicode": frozenset({"Unicode"}),
    "version": frozenset({"signature", "Revision"}),
    # The nine keys every Windows-written template in this repository carries,
    # and the rest of the [System Access] vocabulary of Microsoft's security
    # template reference (DeepSeek final-r10 N1): an unchanged native template
    # carrying them is written, not refused.
    "system access": frozenset({
        "MinimumPasswordAge", "MaximumPasswordAge", "MinimumPasswordLength",
        "PasswordComplexity", "PasswordHistorySize", "LockoutBadCount",
        "ResetLockoutCount", "LockoutDuration", "ClearTextPassword",
        "RequireLogonToChangePassword", "ForceLogoffWhenHourExpire",
        "NewAdministratorName", "NewGuestName", "LSAAnonymousNameLookup",
        "EnableAdminAccount", "EnableGuestAccount",
    }),
    "kerberos policy": frozenset({
        "MaxTicketAge", "MaxRenewAge", "MaxServiceAge", "MaxClockSkew",
        "TicketValidateClient",
    }),
    "event audit": frozenset({
        "AuditSystemEvents", "AuditLogonEvents", "AuditObjectAccess",
        "AuditPrivilegeUse", "AuditPolicyChange", "AuditAccountManage",
        "AuditProcessTracking", "AuditDSAccess", "AuditAccountLogon",
    }),
    "privilege rights": _RIGHT,
    "registry values": re.compile(r"(?:MACHINE|USER)\\.+"),
    "registry keys": re.compile(r"(?:MACHINE|USERS|CLASSES_ROOT|CURRENT_USER)\\.+"),
    "file security": re.compile(r".+"),
    "service general setting": re.compile(r".+"),
    "group membership": re.compile(r".+__Member(?:s|of)"),
}


_INF_SETTING = re.compile(r"[A-Za-z][A-Za-z0-9_]*", re.ASCII)


def inf_key_problem(section: str, key: str, *, vocabulary: bool = False) -> str | None:
    """Why *key* is not a key of *section*, or ``None``.

    A pattern section (rights, paths, principals) is enforced as written. A
    fixed-vocabulary section is enforced by shape -- a setting identifier --
    so a valid Windows key this table does not list is written, never refused
    (DeepSeek final-r10 N1); *vocabulary* holds a key to the listed names,
    which the completeness test does for every key Studio's writers emit.
    """
    allowed = INF_KEYS.get(section.casefold())
    if allowed is None:
        return f"is in [{section}], a section Studio does not write"
    if isinstance(allowed, frozenset):
        if not _INF_SETTING.fullmatch(key):
            return f"is not a [{section}] setting identifier"
        # INF keys are case-insensitive, as the reader treats them.
        if vocabulary and key.casefold() not in {name.casefold() for name in allowed}:
            return f"is not a [{section}] setting"
        return None
    return None if allowed.fullmatch(key) else f"is not a [{section}] key"


#: ``[Registry Keys]`` and its kin: the value after the quoted key -- a code and
#: a quoted, non-empty descriptor. ``secedit /validate`` requires the three
#: fields of ``"key",code,"descriptor"`` (R9), so neither a code alone nor a
#: descriptor alone is a row (final-r9 N4, final-r10).
_ROW_VALUE = re.compile(r'[0-9]+,"[^"]+"', re.ASCII)
#: A preserved row line in those sections, as Windows writes it.
ROW_LINE = re.compile(r'"[^"]*"\s*,\s*[0-9]+\s*,\s*"[^"]*"', re.ASCII)


def inf_problems(sections: tuple[tuple[str, tuple[tuple[str, str], ...], tuple[str, ...]], ...],
                 row_sections: frozenset[str]) -> list[EmitProblem]:
    """Every INF section name, key, value or preserved line that is not one line
    of its kind, or that the INF grammar would read back as something else."""
    problems: list[EmitProblem] = []

    def note(name: str, value: str, problem: str | None) -> None:
        if problem is not None:
            problems.append(EmitProblem("GptTmpl.inf", name, value, problem))

    for section, entries, unknown_lines in sections:
        folded = section.casefold()
        note(f"[{section}]", section, line_problem(section))
        if any(c in section for c in "[]"):
            note(f"[{section}]", section, "contains a bracket")
        key_field, value_field = INF_SECTIONS.get(folded, (_LINE, _LINE))
        row = folded in row_sections
        for key, value in entries:
            name = f"[{section}] {key}"
            note(name, key, value_problem(Field(key_field.kind, required=True), key))
            note(name, key, inf_key_problem(section, key))
            note(name, value, value_problem(value_field, value))
            if key != key.strip() or value != value.strip():
                note(name, f"{key}={value}", "has surrounding whitespace the INF reader strips")
            if key.startswith((";", "[")):
                note(name, key, "would read back as a comment or section header")
            if value.endswith("\\"):
                note(name, value, "ends in a line-continuation backslash")
            if row:
                if '"' in key:
                    note(name, key, "contains a double quote inside a quoted key")
                if not _ROW_VALUE.fullmatch(value):
                    note(name, value, 'is not a row value (code,"descriptor")')
            elif "=" in key:
                note(name, key, "contains '=', which the INF reader splits on")
            if folded in ("privilege rights", "group membership"):
                for principal in value.split(",") if value else ():
                    note(name, principal, security_principal_problem(principal))
            if folded == "group membership":
                group, marker, relation = key.rpartition("__")
                if not marker or relation not in ("Members", "Memberof"):
                    note(name, key, "is not <principal>__Members or __Memberof")
                else:
                    note(name, group, security_principal_problem(group))
        for line in unknown_lines:
            name = f"[{section}] (preserved line)"
            note(name, line, line_problem(line))
            stripped = line.strip()
            if stripped.startswith(";"):
                continue  # a comment stays a comment
            # A preserved line must read back as what it is, never as structure:
            # "[Privilege Rights]" then "SeDebugPrivilege = *S-1-5-18" became an
            # active grant (Sol final-r9 B2).
            if stripped.startswith("[") and stripped.endswith("]"):
                note(name, line, "would read back as a section header")
            elif row and ROW_LINE.fullmatch(stripped):
                pass  # a row; a quoted key may hold '=' (DeepSeek final-r11 N5)
            elif "=" in stripped:
                note(name, line, "would read back as a key = value entry")
            elif row:
                note(name, line, 'is not a row ("key",code,"descriptor")')
            elif not row:
                note(name, line, "is not a comment, and only row sections preserve rows")
            if stripped.endswith("\\"):
                note(name, line, "ends in a line-continuation backslash")
    return problems


def inf_text_problems(text: str) -> list[EmitProblem]:
    """What the parser would drop or strip from INF *text* without a check.

    Every physical line is held to the one-line rule (a vertical tab in a
    stripped margin, a format character in a comment outside any section), and
    a non-comment line outside every section -- which the parser discards --
    is refused. Used on the lossless path, where the original text is returned
    rather than rebuilt (Sol final-r10 B3), and at egress.
    """
    problems: list[EmitProblem] = []
    in_section = False
    for number, line in enumerate(re.split(r"\r\n|\n", text), start=1):
        body = line[:-1] if line.endswith("\r") else line
        problem = line_problem(body)
        if problem is not None:
            problems.append(EmitProblem("GptTmpl.inf", f"line {number}", body, problem))
        stripped = body.strip()
        if stripped.endswith("\\"):
            # The parser joins it with the next line, which the per-line check
            # then never sees whole (DeepSeek final-r11 N4).
            problems.append(
                EmitProblem("GptTmpl.inf", f"line {number}", body, "is a continuation line")
            )
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = True
        elif stripped and not in_section and not stripped.startswith(";"):
            problems.append(
                EmitProblem("GptTmpl.inf", f"line {number}", body, "is outside every section")
            )
    return problems


# ---------------------------------------------------------------------------
# scripts.ini / psscripts.ini
# ---------------------------------------------------------------------------

SCRIPTS_INI: Mapping[str, Field] = {
    "CmdLine": _LINE,
    "Parameters": _LINE,
    "StartExecutePSFirst": Field("token"),
    "EndExecutePSFirst": Field("token"),
}
_SCRIPT_KEY = re.compile(r"(?:[0-9]+)?([A-Za-z]+)", re.ASCII)
SCRIPTS_SECTIONS = frozenset({"Startup", "Shutdown", "Logon", "Logoff", "ScriptsConfig"})


def scripts_ini_problems(
    where: str, sections: list[tuple[str, list[tuple[str, str]]]]
) -> list[EmitProblem]:
    problems: list[EmitProblem] = []
    for section, pairs in sections:
        if section not in SCRIPTS_SECTIONS:
            problems.append(
                EmitProblem(where, f"[{section}]", section, "is not a Scripts section")
            )
        for key, value in pairs:
            match = _SCRIPT_KEY.fullmatch(key)
            field = SCRIPTS_INI.get(match.group(1)) if match else None
            if field is None:
                problems.append(
                    EmitProblem(where, f"[{section}] {key}", key, "is not a Scripts key")
                )
                continue
            problem = value_problem(field, value)
            if problem is None and value != value.strip(" "):
                problem = "has surrounding space, which the INI reader strips"
            if problem is not None:
                problems.append(EmitProblem(where, f"[{section}] {key}", value, problem))
    return problems


# ---------------------------------------------------------------------------
# PowerShell: the plan and the AD discovery scripts
# ---------------------------------------------------------------------------

#: The plan (`export.powershell_plan`): parameter or assignment -> what its
#: argument holds. ``text`` values may hold any character: each control, format
#: or separator character is written as ``[char]0xNNNN`` (see :func:`ps_literal`).
PS_PARAMETERS: Mapping[str, Field] = {
    "Guid": Field("guid", required=True),
    "Name": Field("line", required=True),
    "TargetName": Field("principal", required=True),
    "Comment": _TEXT,
    "Target": Field("dn", required=True),
    "Key": Field("line", required=True),
    "ValueName": _LINE,
    "Value": _TEXT,
    "Domain": Field("name", required=True),
    "Marker": Field("line", required=True),
}
#: The AD discovery scripts (`ad_discovery.discovery_script_*`): each
#: ``param()`` default. Empty means "not given" and is allowed.
DISCOVERY_PARAMETERS: Mapping[str, Field] = {
    "Domain": Field("name"),
    "SearchBase": Field("dn"),
    "SearchTerm": _LINE,
    "ObjectClass": _TOKEN_F,
}
#: Every character PowerShell reads as a single quote: each is doubled inside a
#: single-quoted literal, which is how PowerShell escapes any of them.
PS_SINGLE_QUOTES = "'\u2018\u2019\u201a\u201b"
#: Characters a ``text`` literal writes as ``[char]`` codes rather than inline.
_PS_ENCODED = frozenset({"Cc", "Cf", "Zl", "Zp", "Cs"})


def ps_problem(
    parameter: str, value: str, table: Mapping[str, Field] = PS_PARAMETERS
) -> str | None:
    field = table.get(parameter)
    if field is None:
        return f"is passed to {parameter}, which the script does not classify"
    if field.kind == "text":
        return None  # every character is representable; see ps_literal
    return value_problem(field, value)


def _ps_single_quoted(value: str) -> str:
    return "'" + "".join(c + c if c in PS_SINGLE_QUOTES else c for c in value) + "'"


def _ps_char(character: str) -> str:
    """One character as a PowerShell term. ``[char]`` is a single UTF-16 code
    unit, so a character above U+FFFF (U+1D173, the U+E0000 tag block, ...)
    cannot be one: ``[char]0x1D173`` fails at run time and the plan stopped at
    that statement, part-applied (Sol and DeepSeek final-r10 B1). Above U+FFFF
    the term is ``[char]::ConvertFromUtf32(...)``, which returns the string."""
    code = ord(character)
    if code > 0xFFFF:
        return f"[char]::ConvertFromUtf32(0x{code:X})"
    return f"[char]0x{code:04X}"


def ps_literal(
    value: str,
    parameter: str,
    *,
    table: Mapping[str, Field] = PS_PARAMETERS,
    where: str = "plan.ps1",
) -> str:
    """*value* as a PowerShell expression for *parameter*: the ONE quoting
    primitive every PowerShell writer uses (final-r9).

    The value is held to *table* first: a GUID must be a GUID, a link target a
    DN, a principal a principal, and nothing but ``text`` may hold a control,
    format or separator character. Then every character PowerShell reads as a
    single quote -- ``'`` and the typographic U+2018 to U+201B -- is doubled
    inside a single-quoted literal. A ``text`` value holding a control, format
    or separator character (a CRLF in registry data, say) is written as a
    concatenation, ``('Line one' + [char]0x000D + [char]0x000A + 'Line two')``,
    so the plan stays one physical line per statement and carries the value
    exactly whatever line endings the file is later given.
    """
    problem = ps_problem(parameter, value, table)
    if problem is None and any(unicodedata.category(c) == "Cs" for c in value):
        problem = "contains a lone surrogate, which is not a character"
    if problem is not None:
        raise EmitError([EmitProblem(where, f"-{parameter}", value, problem)])
    field = table[parameter]
    if field.kind != "text" or not any(
        unicodedata.category(c) in _PS_ENCODED for c in value
    ):
        return _ps_single_quoted(value)
    pieces: list[str] = []
    run: list[str] = []
    for character in value:
        if unicodedata.category(character) in _PS_ENCODED:
            if run:
                pieces.append(_ps_single_quoted("".join(run)))
                run = []
            pieces.append(_ps_char(character))
        else:
            run.append(character)
    if run:
        pieces.append(_ps_single_quoted("".join(run)))
    if not pieces[0].startswith("'"):
        pieces.insert(0, "''")  # a string first, so + concatenates strings
    return "(" + " + ".join(pieces) + ")"


# ---------------------------------------------------------------------------
# Registry.pol
# ---------------------------------------------------------------------------

REGISTRY_POL: Mapping[str, str] = {
    "key": "UTF-16LE, NUL-terminated, then ';': any character but NUL (MS-GPREG 2.2.1)",
    "value_name": "UTF-16LE, NUL-terminated, then ';': any character but NUL",
    "type": "a DWORD code from the type table",
    "data": "length-prefixed: every character but NUL is encoded",
}


def pol_name_problem(value: str) -> str | None:
    """A Registry.pol key or value name: anything but NUL, which ends the field.

    A ``;`` is data: the field is NUL-terminated and the separator follows the
    NUL (MS-GPREG 2.2.1; Sol and DeepSeek final-r9). Studio's store holds names
    to the one-line rule separately (`validation.validate_setting`), because
    the plan writes them as single-line PowerShell literals.
    """
    if "\0" in value:
        return "contains NUL, which ends a Registry.pol name"
    if any(unicodedata.category(c) == "Cs" for c in value):
        return "contains a lone surrogate, which UTF-16 cannot encode"
    return None


def pol_data_problem(value: str) -> str | None:
    """Registry.pol string data: anything the length prefix encodes, but no NUL."""
    if "\0" in value:
        return "contains NUL, which ends a registry string"
    if any(unicodedata.category(c) == "Cs" for c in value):
        return "contains a lone surrogate, which UTF-16 cannot encode"
    return None


# ---------------------------------------------------------------------------
# The plain-text report
# ---------------------------------------------------------------------------


def escape_line(value: str) -> str:
    """*value* with every control, format or separator character as ``\\uXXXX``,
    or ``\\UXXXXXXXX`` above U+FFFF, so an escape always reads back as one code
    point (DeepSeek final-r10 N4)."""
    return "".join(
        (f"\\U{ord(c):08x}" if ord(c) > 0xFFFF else f"\\u{ord(c):04x}")
        if unicodedata.category(c) in _LINE_BREAKING
        else c
        for c in value
    )
