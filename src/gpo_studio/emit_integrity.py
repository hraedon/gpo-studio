"""Emit-time integrity: what every writer refuses to write, and why.

Final-r8 found a principal holding a line feed written into ``GptTmpl.inf`` as
a new privilege grant, and a service name doing the same in the
object-security template -- writers that trusted their input. Field
inventories kept missing fields (an item ``uid``, ``FilterRunOnce@id``, a
power scheme GUID), so the rule now lives in the writers: each one checks
every name and value it emits as it emits it, against one table per format
here, and refuses what does not fit. A refusal is an error everywhere: the
store refuses the mutation, an export or render answers 422.

The writers (``tests/test_emit_integrity.py`` runs each over the native
captures and synthetic inputs and fails on any emitted name not classified
below):

* ``gpp.serialize_gpp`` -- GPP XML, typed and retained items and imported
  files alike: :data:`GPP_XML`;
* ``export._xml_to_bytes`` -- the GPMC backup documents ``Backup.xml``,
  ``bkupInfo.xml`` and ``manifest.xml``: :data:`BACKUP_XML`;
* ``security_template.format_security_template`` -- ``GptTmpl.inf`` for the
  policy-family and object-security render endpoints: :data:`INF_SECTIONS`
  and :func:`inf_problems`;
* ``export._native_ini_bytes`` -- ``scripts.ini`` / ``psscripts.ini``:
  :data:`SCRIPTS_INI`;
* ``export.powershell_plan`` (``plan.ps1`` and ``apply.ps1``) -- every quoted
  argument through ``export._ps_quote``: :data:`PS_PARAMETERS`; comments
  through :func:`escape_line`;
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
* PowerShell single-quoted literals: TAB and LF are part of the literal and
  read back exactly; allowed only for ``-Comment`` and registry data.
* ``Registry.pol`` value data: length-prefixed UTF-16, so every character but
  the terminating NUL is encoded; key and value names are NUL-terminated and
  ``;``-separated, so they refuse both.
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
from typing import Literal

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
    "security_principal",
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

CHECKS: Mapping[Kind, Callable[[str], str | None]] = {
    "sid": sid_problem,
    "principal": principal_problem,
    "security_principal": security_principal_problem,
    "name": name_problem,
    "ou": lambda value: dn_problem(value, min_rdns=1),
    "dn": dn_problem,
    "guid": guid_problem,
    "braced_guid": lambda value: guid_problem(value, braces="required"),
    "guid_list": _pattern(_GUID_LIST, "a list of bracketed GUID pairs"),
    "time": time_problem,
    "datetime": datetime_problem,
    "date": date_problem,
    "timestamp": _pattern(_TIMESTAMP, "a timestamp (YYYY-MM-DD H:MM:SS)"),
    "flag": _pattern(_FLAG, "0 or 1"),
    "token": _pattern(_TOKEN, "a token (letters, digits and _.:+-)"),
    "number": _pattern(_NUMBER, "an integer"),
    "hex_bytes": _pattern(_HEX_BYTES, "space-separated hexadecimal bytes"),
    "right": _pattern(_RIGHT, "a user right constant (Se...)"),
    "line": line_problem,
    "text": text_problem,
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
#: ``[Registry Keys]`` and its kin: the value after the quoted key.
_ROW_VALUE = re.compile(r'(?:[0-9]+,)?"[^"]*"|', re.ASCII)


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
            note(f"[{section}] (preserved line)", line, line_problem(line))
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
# The PowerShell plan
# ---------------------------------------------------------------------------

#: Parameter (or assignment) -> what its quoted argument holds.
PS_PARAMETERS: Mapping[str, Field] = {
    "Guid": Field("guid", required=True),
    "Name": Field("line", required=True),
    "TargetName": Field("principal", required=True),
    "Comment": _TEXT,
    "Target": Field("dn", required=True),
    "Key": Field("line", required=True),
    "ValueName": _LINE,
    "Value": _TEXT,
    "desiredApply": Field("principal", required=True),
}
#: Every character PowerShell reads as a single quote: each is doubled inside a
#: single-quoted literal, which is how PowerShell escapes any of them.
PS_SINGLE_QUOTES = "'‘’‚‛"


def ps_problem(parameter: str, value: str) -> str | None:
    field = PS_PARAMETERS.get(parameter)
    if field is None:
        return f"is passed to {parameter}, which the plan does not classify"
    return value_problem(field, value)


# ---------------------------------------------------------------------------
# Registry.pol
# ---------------------------------------------------------------------------

REGISTRY_POL: Mapping[str, str] = {
    "key": "NUL-terminated, ';'-separated: one line, no ';'",
    "value_name": "NUL-terminated, ';'-separated: one line, no ';'",
    "type": "a DWORD code from the type table",
    "data": "length-prefixed: every character but NUL is encoded",
}


def pol_name_problem(value: str) -> str | None:
    """A Registry.pol key or value name: one line, no ``;`` (the field separator)."""
    problem = line_problem(value)
    if problem is None and ";" in value:
        problem = "contains ';', the Registry.pol field separator"
    return problem


def pol_data_problem(value: str) -> str | None:
    """Registry.pol string data: anything the length prefix encodes, but no NUL."""
    if "\0" in value:
        return "contains NUL, which ends a registry string"
    return None


# ---------------------------------------------------------------------------
# The plain-text report
# ---------------------------------------------------------------------------


def escape_line(value: str) -> str:
    """*value* with every control, format or separator character as ``\\uXXXX``."""
    return "".join(
        f"\\u{ord(c):04x}" if unicodedata.category(c) in _LINE_BREAKING else c for c in value
    )
