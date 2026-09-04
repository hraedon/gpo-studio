"""Build reviewable, deterministic publication artifacts."""

from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from typing import Protocol, assert_never
from uuid import NAMESPACE_DNS, UUID, uuid5

from .canonical import (
    CANONICAL_SCHEMA_VERSION,
    policy_semantic_dict,
    policy_semantic_sha256,
    review_model_sha256,
)
from .gpp import contains_cpassword, serialize_gpp
from .model import GPO, RegistrySetting, ValidationError, ValidationIssue
from .registry_pol import PolRecord, serialize
from .script_policy import PowerShellScriptEntry, ScriptEntry, ScriptPolicy
from .validation import validate_gpo

_GPMC_NS = "http://www.microsoft.com/GroupPolicy/GPOOperations/Manifest"
_GPMC_BACKUP_NS = "http://www.microsoft.com/GroupPolicy/GPOOperations"
_REGISTRY_CSE_GUID = "{35378EAC-683F-11D2-A89A-00C04FBBCFA2}"
_REGISTRY_MACHINE_TOOL_GUID = "{D02B1F72-3407-48AE-BA88-E8213C6761F1}"
_REGISTRY_USER_TOOL_GUID = "{D02B1F73-3407-48AE-BA88-E8213C6761F1}"
_GPP_FILE_COPY_EXTENSION_GUID = "{F15C46CD-82A0-4C2D-A210-5D0D3182A418}"
_ZERO_GUID = "{00000000-0000-0000-0000-000000000000}"
_BACKUP_TIME = "1980-01-01T00:00:00"
_NATIVE_BACKUP_NAMESPACE = UUID("9f2492d8-f0d4-45f8-91db-7fc0c86ceae8")

# Pinned to genuine GPMC-authored WS2025 fixtures. Other GPP families remain
# blocked until their extension metadata is captured.
_GPP_EXTENSION_PROFILES: dict[str, tuple[str, str]] = {
    "Drives": (
        "{5794DAFD-BE60-433F-88A2-1A31939AC01F}",
        "{2EA1A81B-48E5-45E9-8BB7-A6E3AC170006}",
    ),
    "Groups": (
        "{17D89FEC-5C44-4972-B12D-241CAEF74509}",
        "{79F92669-4224-476C-9C5C-6EFB4D87DF4A}",
    ),
    "ScheduledTasks": (
        "{AADCED64-746C-4633-A97C-D61349046527}",
        "{CAB54552-DEEA-4691-817E-ED4A4D1AFC72}",
    ),
    "Services": (
        "{91FBB303-0CD5-4055-BF42-E512A681B325}",
        "{CC5746A9-9B74-4BE5-AE2E-64379C86E0E4}",
    ),
}

# Scripts CSE pair, measured on the wire (windows-console-driver claim-registry
# R2 re-run, 2026-09-03): the authoring transaction registered exactly
# ``[{42B5FAAE-6536-11D2-AE5A-0000F87571E3}{40B6664F-4972-11D1-A7CA-0000F87571E3}]``
# in ``gPCMachineExtensionNames``, so both halves below are captured, not
# recalled. scripts.ini (legacy) and psscripts.ini (PowerShell) are the payload
# files of this one extension.
_SCRIPTS_CSE_GUID = "{42B5FAAE-6536-11D2-AE5A-0000F87571E3}"
_SCRIPTS_TOOL_GUID = "{40B6664F-4972-11D1-A7CA-0000F87571E3}"

_DOMAIN_NEUTRAL_SECURITY_DESCRIPTOR = (
    "01 00 04 80 14 00 00 00 24 00 00 00 00 00 00 00 34 00 00 00 "
    "01 02 00 00 00 00 00 05 20 00 00 00 20 02 00 00 01 02 00 00 "
    "00 00 00 05 20 00 00 00 20 02 00 00 02 00 34 00 02 00 00 00 "
    "00 00 14 00 00 00 00 10 01 01 00 00 00 00 00 05 12 00 00 00 "
    "00 00 18 00 00 00 00 10 01 02 00 00 00 00 00 05 20 00 00 00 "
    "20 02 00 00"
)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _ps_sanitize_comment(text: str) -> str:
    return text.replace("\r", " ").replace("\n", " ").replace("`", " ")


def _ps_value(setting: RegistrySetting) -> str:
    if isinstance(setting.value, int):
        return str(setting.value)
    if isinstance(setting.value, list):
        return "@(" + ", ".join(_ps_quote(item) for item in setting.value) + ")"
    if setting.registry_type == "REG_BINARY":
        clean = setting.value.replace(" ", "")
        if not clean:
            return "([byte[]]@())"
        return (
            "([byte[]](" + ",".join(f"0x{clean[i : i + 2]}" for i in range(0, len(clean), 2)) + "))"
        )
    return _ps_quote(setting.value)


def plan_refusal(gpo: GPO) -> ValidationIssue | None:
    """Why this GPO can get no PowerShell plan, or ``None`` if it can.

    ONE function so the refusal and the advertisement of it cannot disagree
    (WI-044). `powershell_plan` raises on whatever this returns, and the API
    reports the same artifact as unavailable for the same reason -- a caller is
    told up front what it would otherwise discover by pressing a button and
    getting a 422.

    The alternative was a second copy of the deny condition in the API's
    capability block. Two independent statements of "can this be exported" is
    how they drift apart, and this item exists because they already had:
    `validate_gpo` has no deny rule, so the capability said `enabled: true`
    while the download refused.
    """
    denied = [sf for sf in gpo.security_filters if sf.deny]
    if not denied:
        return None
    return ValidationIssue(
        severity="error",
        code="deny_filter_not_expressible",
        message=(
            "Security filter for "
            f"{', '.join(sorted(sf.principal for sf in denied))} "
            "is a DENY, which Set-GPPermission cannot express. "
            "A PowerShell plan cannot be generated for this GPO "
            "without changing what the deny means. Remove the "
            "deny, or apply it out of band as a DACL ACE."
        ),
        path="security_filters",
    )


def powershell_plan(gpo: GPO) -> str:
    """Generate an idempotent, human-reviewable GroupPolicy module plan."""
    lines = [
        "# Generated by GPO Studio. Review before running with delegated GPO rights.",
        "# This plan intentionally does not run from the web application.",
        "#Requires -Modules GroupPolicy",
        "[CmdletBinding(SupportsShouldProcess=$true)]",
        "param()",
        "$ErrorActionPreference = 'Stop'",
        f"$gpo = Get-GPO -Guid {_ps_quote(gpo.guid)} -ErrorAction SilentlyContinue",
        "if (-not $gpo) {",
        f"    $gpo = Get-GPO -Name {_ps_quote(gpo.name)} -ErrorAction SilentlyContinue",
        "}",
        "if (-not $gpo) {",
    ]
    if gpo.description:
        lines.append(
            f"    $gpo = New-GPO -Name {_ps_quote(gpo.name)}"
            f" -Comment {_ps_quote(gpo.description)}"
        )
    else:
        lines.append(
            f"    $gpo = New-GPO -Name {_ps_quote(gpo.name)}"
            " -Comment 'Created by GPO Studio'"
        )
    lines += [
        "}",
        "elseif ($gpo.DisplayName -ne " + _ps_quote(gpo.name) + ") {",
        f"    Rename-GPO -Guid $gpo.Id -TargetName {_ps_quote(gpo.name)} | Out-Null",
        "}",
        "",
    ]
    for setting in gpo.settings:
        key = f"{setting.hive}\\{setting.key}"
        if setting.action == "delete":
            lines.append(
                "Remove-GPRegistryValue -Guid $gpo.Id"
                f" -Key {_ps_quote(key)} -ValueName {_ps_quote(setting.value_name)}"
                " -ErrorAction SilentlyContinue"
            )
        elif setting.action == "delete_all_values":
            lines.append(
                f"$existing = Get-GPRegistryValue -Guid $gpo.Id"
                f" -Key {_ps_quote(key)} -ErrorAction SilentlyContinue"
            )
            lines.append(
                "if ($existing) { $existing | ForEach-Object"
                f" {{ Remove-GPRegistryValue -Guid $gpo.Id -Key {_ps_quote(key)}"
                " -ValueName $_.ValueName -ErrorAction SilentlyContinue } }"
            )
        elif setting.action == "set":
            type_map = {
                "REG_SZ": "String",
                "REG_EXPAND_SZ": "ExpandString",
                "REG_BINARY": "Binary",
                "REG_DWORD": "DWord",
                "REG_MULTI_SZ": "MultiString",
                "REG_QWORD": "QWord",
            }
            lines.append(
                "Set-GPRegistryValue -Guid $gpo.Id"
                f" -Key {_ps_quote(key)} -ValueName {_ps_quote(setting.value_name)}"
                f" -Type {type_map[setting.registry_type]}"
                f" -Value {_ps_value(setting)}"
            )
        else:
            assert_never(setting.action)
    if gpo.links:
        lines.extend(["", "# Link intent (New-GPLink updates an existing link when present)."])
    for link in gpo.links:
        target = _ps_quote(link.target)
        common = (
            f"-Guid $gpo.Id -Target {target}"
            f" -LinkEnabled {'Yes' if link.enabled else 'No'}"
            f" -Enforced {'Yes' if link.enforced else 'No'} -Order {link.order}"
        )
        lines.extend(
            [
                f"$existingLink = (Get-GPInheritance -Target {target}).GpoLinks |",
                "    Where-Object { $_.GpoId -eq $gpo.Id }",
                f"if ($existingLink) {{ Set-GPLink {common} | Out-Null }}",
                f"else {{ New-GPLink {common} | Out-Null }}",
            ]
        )
    if gpo.security_filters:
        # REFUSED, NOT APPROXIMATED (WI-041).
        #
        # `Set-GPPermission` cannot express a deny ACE at all -- that is WI-033's
        # own recorded finding about the cmdlet, and it is why the RSOP lane
        # writes denies straight onto the groupPolicyContainer's DACL. Every way
        # of emitting one anyway is wrong in the dangerous direction:
        #
        #   * emitting `-PermissionLevel GpoApply` INVERTS it, and this is what
        #     the module used to do -- a plan authored as "keep this GPO off
        #     these machines" GRANTED the permission instead, byte-identical to
        #     the allow plan;
        #   * silently dropping the row publishes a plan that leaves the deny
        #     unapplied while looking complete, which fails in the same
        #     direction one step later.
        #
        # A plan an operator reviews and runs has to mean what it says. So a GPO
        # carrying a deny does not get a partial plan; it gets no plan and a
        # reason. Same call as the cpassword refusal below, and WI-012's on
        # `explicitValue`: refuse what cannot be expressed rather than guess.
        refusal = plan_refusal(gpo)
        if refusal is not None:
            raise ValidationError([refusal])
        lines.append("")
        lines.append("# Security filtering — reconcile GpoApply permissions only.")
        lines.append("# GpoEdit, GpoRead, and other management permissions are preserved.")
        desired_targets = ", ".join(
            _ps_quote(sf.principal)
            for sf in gpo.security_filters
            if sf.permission == "apply"
        )
        lines.append(f"$desiredApply = @({desired_targets})")
        lines.append("# Trustees that must not be removed even if absent from desired set.")
        lines.append("$protected = @('Authenticated Users', 'Domain Admins',")
        lines.append("  'Enterprise Admins', 'SYSTEM', 'Administrators')")
        lines.append(
            "$existing = Get-GPPermission -Guid $gpo.Id -All -ErrorAction SilentlyContinue"
        )
        lines.append("foreach ($perm in $existing) {")
        lines.append("    if ($perm.Permission -eq 'GpoApply' -and")
        lines.append("        $desiredApply -notcontains $perm.Trustee.Name -and")
        lines.append("        $protected -notcontains $perm.Trustee.Name) {")
        lines.append(
            "        Set-GPPermission -Guid $gpo.Id -PermissionLevel None"
            " -TargetName $perm.Trustee.Name -TargetType $perm.Trustee.SidType"
            " -ErrorAction SilentlyContinue"
        )
        lines.append("    }")
        lines.append("}")
        for sf in sorted(gpo.security_filters, key=lambda s: s.principal.casefold()):
            match sf.permission:
                case "apply":
                    perm = "GpoApply"
                case "read":
                    perm = "GpoRead"
                case _:
                    assert_never(sf.permission)
            target_type_ps = sf.target_type.title()
            lines.append(
                f"Set-GPPermission -Guid $gpo.Id -PermissionLevel {perm}"
                f" -TargetName {_ps_quote(sf.principal)} -TargetType {target_type_ps} -Replace"
                " | Out-Null"
            )
    if gpo.wmi_filter is not None:
        wmi_name = _ps_sanitize_comment(gpo.wmi_filter.name)
        wmi_query = _ps_sanitize_comment(gpo.wmi_filter.query)
        lines.append("")
        lines.append(f"# WMI filter: {wmi_name} ({wmi_query})")
        lines.append(
            f"# Assign WMI filter '{wmi_name}' to this GPO via GPMC or the"
            f" GPMC COM API (domain: {_ps_sanitize_comment(gpo.domain)})."
        )
    status_value: str
    if gpo.computer_enabled and gpo.user_enabled:
        status_value = "AllSettingsEnabled"
    elif gpo.computer_enabled:
        status_value = "UserSettingsDisabled"
    elif gpo.user_enabled:
        status_value = "ComputerSettingsDisabled"
    else:
        status_value = "AllSettingsDisabled"

    lines.extend(
        [
            "",
            "# Side enablement is explicit in the draft.",
            "# GpoStatus is a writable .NET property on the GPO object, not a Set-GPO parameter.",
            f"$gpo.GpoStatus = '{status_value}'",
        ]
    )
    return "\n".join(lines) + "\n"


def export_bundle(gpo: GPO) -> bytes:
    """Return a deterministic ZIP containing manifest, PReg files, and plan."""
    issues = validate_gpo(gpo)
    manifest = {
        "schema_version": 2,
        "kind": "gpo-studio-publication-bundle",
        "gpo": gpo.to_dict(),
        "validation": [asdict(issue) for issue in issues],
        "policy_semantic_sha256": policy_semantic_sha256(gpo),
        "review_model_sha256": review_model_sha256(gpo),
        "canonical_schema_version": CANONICAL_SCHEMA_VERSION,
        "canonical_model": policy_semantic_dict(gpo),
    }
    computer = [item for item in gpo.settings if item.side == "computer"]
    user = [item for item in gpo.settings if item.side == "user"]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        entries: dict[str, bytes] = {
            "manifest.json": json.dumps(manifest, indent=2, sort_keys=True).encode(),
            "apply.ps1": powershell_plan(gpo).encode("utf-8-sig"),
            "Machine/Registry.pol": serialize(computer),
            "User/Registry.pol": serialize(user),
        }
        for col in gpo.gpp_collections:
            side_dir = "Machine" if col.scope == "computer" else "User"
            for filename, content in serialize_gpp(col).items():
                if contains_cpassword(content):
                    raise ValidationError([
                        ValidationIssue(
                            severity="error",
                            code="cpassword_detected",
                            message=f"GPP file {filename} contains a cpassword attribute.",
                            path=f"gpp_collections/{filename}",
                        )
                    ])
                entries[f"{side_dir}/Preferences/{filename}"] = content
        for name, content in entries.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, content)
    return output.getvalue()


def _gpmc_preg_bytes(settings: list[RegistrySetting]) -> bytes:
    records = [
        PolRecord(
            key=s.key,
            value_name=s.value_name,
            registry_type=s.registry_type,
            value=s.value,
            action=s.action,
        )
        for s in settings
    ]
    return serialize(records)


# ---------------------------------------------------------------------------
# Native scripts.ini / psscripts.ini (Scripts CSE payload)
#
# The byte contract below is the banked R2 measurement (Windows Server 2025
# GPMC, 2026-09-03; artifacts in tests/fixtures/native-scripts-gpmc/), not a
# guess from the docs: GPMC writes both files as UTF-16LE with a BOM (FF FE),
# a leading blank line, and CRLF terminators on every line including the last.
# ---------------------------------------------------------------------------

_SCRIPT_SIDES: tuple[str, ...] = ("computer", "user")


def _native_ini_bytes(sections: list[tuple[str, list[tuple[str, str]]]]) -> bytes:
    """Encode INI *sections* into the measured native scripts-file byte shape.

    Why each element is load-bearing:

    * The BOM matters. The Scripts CSE sniffs encoding from the FF FE prefix;
      a UTF-8 or ANSI file decodes to mojibake and every entry is silently
      ignored — the inert failure the R10 import probe exists to catch.
    * The leading blank line is in the capture (byte 3..4 are 0D 00), as is a
      CRLF after the final entry, so both are reproduced exactly.
    * Only non-empty sections are emitted; the captures carry a ``[Startup]``
      section alone, never stubs for unconfigured triggers.
    """
    parts = ["\r\n"]
    for name, pairs in sections:
        if not pairs:
            continue
        parts.append(f"[{name}]\r\n")
        parts.extend(f"{key}={value}\r\n" for key, value in pairs)
    return b"\xff\xfe" + "".join(parts).encode("utf-16-le")


class _IniScriptEntry(Protocol):
    """The two attributes either entry type contributes to the INI shape."""

    @property
    def original_name(self) -> str: ...

    @property
    def parameters(self) -> str: ...


def _script_ini_pairs(entries: Sequence[_IniScriptEntry]) -> list[tuple[str, str]]:
    """Split-style ``NCmdLine``/``NParameters`` pairs, in tuple order.

    A zero-value parameter is written as an explicit empty key, never omitted:
    the R2 capture retains ``1Parameters=`` (nothing after the ``=``) for the
    parameterless second script, and the parser reads position off these
    numbered keys, so dropping the key would renumber every later entry.
    """
    pairs: list[tuple[str, str]] = []
    for idx, entry in enumerate(entries):
        pairs.append((f"{idx}CmdLine", entry.original_name))
        pairs.append((f"{idx}Parameters", entry.parameters))
    return pairs


def _native_scripts_refusal(
    scope: str, policy: ScriptPolicy
) -> ValidationIssue | None:
    """Why *policy* has no native scripts-file encoding, or ``None``.

    REFUSED, NOT APPROXIMATED — the same call as the cpassword and
    ``explicitValue`` refusals above. The R2 captures pin the native entry
    shape to ``NCmdLine``/``NParameters`` plus the one ``[ScriptsConfig]``
    ordering key; the model's per-script PowerShell switches and the logon/
    logoff sync flags appear nowhere on the wire (the claim registry records
    their absence explicitly), so there is no measured encoding to emit and
    none is invented here.
    """
    path = f"scripts/{scope}"
    if policy.run_logon_scripts_sync or policy.run_logoff_scripts_sync:
        return ValidationIssue(
            severity="error",
            code="inexpressible_native_script_state",
            message=(
                "RunLogonScriptsSync/RunLogoffScriptsSync have no native "
                "scripts-file encoding (measured WS2025 GPMC: neither key "
                "exists on the wire). Use the Studio publication bundle "
                "instead."
            ),
            path=path,
        )
    for entry in (*policy.startup, *policy.shutdown, *policy.logon, *policy.logoff):
        if entry.execution != "synchronous":
            return ValidationIssue(
                severity="error",
                code="inexpressible_native_script_state",
                message=(
                    f"Script {entry.script_id} is asynchronous, which the "
                    "measured native scripts.ini shape (NCmdLine/NParameters "
                    "only) cannot express. Use the Studio publication bundle "
                    "instead."
                ),
                path=f"{path}/{entry.script_id}",
            )
    for ps_entry in (
        *policy.powershell_startup,
        *policy.powershell_shutdown,
        *policy.powershell_logon,
        *policy.powershell_logoff,
    ):
        if (
            ps_entry.execution != "synchronous"
            or ps_entry.no_profile
            or not ps_entry.non_interactive
        ):
            return ValidationIssue(
                severity="error",
                code="inexpressible_native_script_state",
                message=(
                    f"PowerShell script {ps_entry.script_id} sets no_profile, "
                    "non_interactive=False, or asynchronous execution; the "
                    "measured native psscripts.ini entry shape "
                    "(NCmdLine/NParameters only) cannot express it. Use the "
                    "Studio publication bundle instead."
                ),
                path=f"powershell_{path}/{ps_entry.script_id}",
            )
    return None


def _legacy_scripts_sections(
    policy: ScriptPolicy,
) -> list[tuple[str, list[tuple[str, str]]]]:
    legacy: dict[str, tuple[ScriptEntry, ...]] = {
        "Startup": policy.startup,
        "Shutdown": policy.shutdown,
        "Logon": policy.logon,
        "Logoff": policy.logoff,
    }
    return [
        (name, _script_ini_pairs(entries)) for name, entries in legacy.items() if entries
    ]


def _powershell_scripts_sections(
    policy: ScriptPolicy,
) -> list[tuple[str, list[tuple[str, str]]]]:
    powershell: dict[str, tuple[PowerShellScriptEntry, ...]] = {
        "Startup": policy.powershell_startup,
        "Shutdown": policy.powershell_shutdown,
        "Logon": policy.powershell_logon,
        "Logoff": policy.powershell_logoff,
    }
    sections: list[tuple[str, list[tuple[str, str]]]] = []
    has_entries = any(powershell.values())
    if has_entries:
        # Ordering is encoded as [ScriptsConfig] StartExecutePSFirst=true/false
        # (measured), placed before the trigger sections exactly as captured.
        # NotConfigured writes no section at all: the R2 engine re-run captured
        # a 140-byte psscripts.ini with no [ScriptsConfig] until the ordering
        # dropdown was explicitly set, so the default stays absent rather than
        # inventing a value for an unmeasured state.
        if policy.powershell_order == "run_windows_powershell_scripts_first":
            sections.append(("ScriptsConfig", [("StartExecutePSFirst", "true")]))
        elif policy.powershell_order == "run_windows_powershell_scripts_last":
            sections.append(("ScriptsConfig", [("StartExecutePSFirst", "false")]))
    sections.extend(
        (name, _script_ini_pairs(entries)) for name, entries in powershell.items() if entries
    )
    return sections


def _native_scripts_files(
    scope: str, policy: ScriptPolicy, files: dict[str, bytes]
) -> None:
    side_dir = "Machine" if scope == "computer" else "User"
    legacy_sections = _legacy_scripts_sections(policy)
    if legacy_sections:
        files[f"{side_dir}/Scripts/scripts.ini"] = _native_ini_bytes(legacy_sections)
    ps_sections = _powershell_scripts_sections(policy)
    if ps_sections:
        files[f"{side_dir}/Scripts/psscripts.ini"] = _native_ini_bytes(ps_sections)


def _xml_to_bytes(root: ET.Element, namespace: str) -> bytes:
    ET.register_namespace("", namespace)
    result = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    assert isinstance(result, bytes)
    return result


def _braced_guid(value: str, *, field: str) -> str:
    try:
        parsed = UUID(value.strip("{}"))
    except ValueError:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_native_backup_guid",
                message=f"{field} must be a valid GUID for native backup export.",
                path="guid",
            )
        ]) from None
    return "{" + str(parsed).upper() + "}"


def native_backup_id(gpo: GPO) -> str:
    """Return the deterministic backup-instance ID for a native export."""
    gpo_id = _braced_guid(gpo.guid, field="GPO ID")
    backup_content = replace(gpo, links=(), security_filters=(), wmi_filter=None)
    identity = gpo.name + "\n" + policy_semantic_sha256(backup_content)
    candidate = uuid5(_NATIVE_BACKUP_NAMESPACE, identity)
    if candidate == UUID(gpo_id.strip("{}")):
        candidate = uuid5(_NATIVE_BACKUP_NAMESPACE, "backup:" + identity)
    return "{" + str(candidate).upper() + "}"


def _backup_inst(gpo: GPO, backup_id: str) -> ET.Element:
    inst = ET.Element(f"{{{_GPMC_NS}}}BackupInst")
    values = (
        ("GPOGuid", _braced_guid(gpo.guid, field="GPO ID")),
        ("GPODomain", gpo.domain),
        ("GPODomainGuid", "{" + str(uuid5(NAMESPACE_DNS, gpo.domain.casefold())) + "}"),
        ("GPODomainController", "UNKNOWN"),
        ("BackupTime", _BACKUP_TIME),
        ("ID", backup_id),
        ("Comment", ""),
        ("GPODisplayName", gpo.name),
    )
    for name, text in values:
        ET.SubElement(inst, f"{{{_GPMC_NS}}}{name}").text = text
    return inst


def _build_manifest_xml(gpo: GPO, backup_id: str | None = None) -> bytes:
    resolved_id = backup_id or native_backup_id(gpo)
    root = ET.Element(
        f"{{{_GPMC_NS}}}Backups",
        {"xmlns:mfst": _GPMC_NS, "mfst:version": "1.0"},
    )
    root.append(_backup_inst(gpo, resolved_id))
    return _xml_to_bytes(root, _GPMC_NS)


def _build_bkup_info_xml(gpo: GPO, backup_id: str | None = None) -> bytes:
    return _xml_to_bytes(_backup_inst(gpo, backup_id or native_backup_id(gpo)), _GPMC_NS)


def _native_export_files(
    gpo: GPO,
    scripts: Mapping[str, ScriptPolicy] | None = None,
) -> tuple[dict[str, bytes], dict[str, set[str]]]:
    computer = [item for item in gpo.settings if item.side == "computer"]
    user = [item for item in gpo.settings if item.side == "user"]
    files: dict[str, bytes] = {}
    profiles: dict[str, set[str]] = {"Machine": set(), "User": set()}
    if computer:
        files["Machine/registry.pol"] = _gpmc_preg_bytes(computer)
    if user:
        files["User/registry.pol"] = _gpmc_preg_bytes(user)

    script_policies = scripts or {}
    for scope in _SCRIPT_SIDES:
        policy = script_policies.get(scope)
        if policy is None:
            continue
        refusal = _native_scripts_refusal(scope, policy)
        if refusal is not None:
            raise ValidationError([refusal])
        _native_scripts_files(scope, policy, files)

    unsupported: set[str] = set()
    for col in gpo.gpp_collections:
        side_dir = "Machine" if col.scope == "computer" else "User"
        for filename, content in serialize_gpp(col).items():
            if contains_cpassword(content):
                raise ValidationError([
                    ValidationIssue(
                        severity="error",
                        code="cpassword_detected",
                        message=f"GPP file {filename} contains a cpassword attribute.",
                        path=f"gpp_collections/{filename}",
                    )
                ])
            family = filename.replace("\\", "/").split("/", 1)[0]
            if family not in _GPP_EXTENSION_PROFILES:
                unsupported.add(family)
                continue
            profiles[side_dir].add(family)
            files[f"{side_dir}/Preferences/{filename.replace('\\', '/')}"] = content

    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="unsupported_native_gpp_extension",
                message=(
                    "Native backup extension metadata has not been verified for: "
                    f"{names}. Use the Studio publication bundle instead."
                ),
                path="gpp_collections",
            )
        ])
    return files, profiles


def _extension_guids(
    *,
    side: str,
    has_registry: bool,
    gpp_profiles: set[str],
    has_scripts: bool = False,
) -> str:
    groups: list[str] = []
    if has_registry:
        tool = (
            _REGISTRY_MACHINE_TOOL_GUID if side == "Machine" else _REGISTRY_USER_TOOL_GUID
        )
        groups.append(f"[{_REGISTRY_CSE_GUID}{tool}]")
    if has_scripts:
        # The pair GPMC itself registered when authoring scripts (claim-registry
        # R2 re-run): one group, CSE GUID then tool GUID, same bracket shape as
        # the other profiles. Relative order against the registry/GPP groups was
        # not captured in any single transaction; this order is deterministic.
        groups.append(f"[{_SCRIPTS_CSE_GUID}{_SCRIPTS_TOOL_GUID}]")
    if gpp_profiles:
        pairs = [_GPP_EXTENSION_PROFILES[name] for name in sorted(gpp_profiles)]
        groups.append("[" + _ZERO_GUID + "".join(tool for _, tool in pairs) + "]")
        groups.extend(f"[{client}{tool}]" for client, tool in pairs)
    return "".join(groups)


def _backup_attr(name: str) -> str:
    return f"bkp:{name}"


def _source_path(gpo: GPO, relative: str) -> str:
    gpo_id = _braced_guid(gpo.guid, field="GPO ID")
    return rf"\\UNKNOWN\SYSVOL\{gpo.domain}\Policies\{gpo_id}\{relative}"


def _append_file_reference(
    parent: ET.Element, gpo: GPO, relative: str, *, is_dir: bool = False
) -> None:
    normalized = relative.replace("/", "\\")
    side, _, side_relative = normalized.partition("\\")
    variable = "%GPO_MACH_FSPATH%" if side == "Machine" else "%GPO_USER_FSPATH%"
    tag = "FSObjectDir" if is_dir else "FSObjectFile"
    ET.SubElement(
        parent,
        f"{{{_GPMC_BACKUP_NS}}}{tag}",
        {
            _backup_attr("Path"): f"{variable}\\{side_relative}",
            _backup_attr("SourceExpandedPath"): _source_path(gpo, normalized),
            _backup_attr("Location"): f"DomainSysvol\\GPO\\{normalized}",
        },
    )


def _build_backup_xml(
    gpo: GPO, files: dict[str, bytes], profiles: dict[str, set[str]]
) -> bytes:
    root = ET.Element(
        f"{{{_GPMC_BACKUP_NS}}}GroupPolicyBackupScheme",
        {
            "xmlns:bkp": _GPMC_BACKUP_NS,
            "bkp:version": "2.0",
            "bkp:type": "GroupPolicyBackupTemplate",
        },
    )
    obj = ET.SubElement(root, f"{{{_GPMC_BACKUP_NS}}}GroupPolicyObject")
    ET.SubElement(obj, f"{{{_GPMC_BACKUP_NS}}}SecurityGroups")
    ET.SubElement(obj, f"{{{_GPMC_BACKUP_NS}}}FilePaths")
    core = ET.SubElement(obj, f"{{{_GPMC_BACKUP_NS}}}GroupPolicyCoreSettings")
    options = (0 if gpo.user_enabled else 1) | (0 if gpo.computer_enabled else 2)
    machine_files = any(path.startswith("Machine/") for path in files)
    user_files = any(path.startswith("User/") for path in files)
    machine_scripts = any(path.startswith("Machine/Scripts/") for path in files)
    user_scripts = any(path.startswith("User/Scripts/") for path in files)
    core_values = (
        ("ID", _braced_guid(gpo.guid, field="GPO ID")),
        ("Domain", gpo.domain),
        ("SecurityDescriptor", _DOMAIN_NEUTRAL_SECURITY_DESCRIPTOR),
        ("DisplayName", gpo.name),
        ("Options", str(options)),
        ("UserVersionNumber", "65537" if user_files else "0"),
        ("MachineVersionNumber", "65537" if machine_files else "0"),
        (
            "MachineExtensionGuids",
            _extension_guids(
                side="Machine",
                has_registry="Machine/registry.pol" in files,
                gpp_profiles=profiles["Machine"],
                has_scripts=machine_scripts,
            ),
        ),
        (
            "UserExtensionGuids",
            _extension_guids(
                side="User",
                has_registry="User/registry.pol" in files,
                gpp_profiles=profiles["User"],
                has_scripts=user_scripts,
            ),
        ),
        ("WMIFilter", ""),
    )
    for name, text in core_values:
        ET.SubElement(core, f"{{{_GPMC_BACKUP_NS}}}{name}").text = text

    registry_ext = ET.SubElement(
        obj,
        f"{{{_GPMC_BACKUP_NS}}}GroupPolicyExtension",
        {
            _backup_attr("ID"): _REGISTRY_CSE_GUID,
            _backup_attr("DescName"): "Registry",
        },
    )
    for path in sorted(path for path in files if path.endswith("/registry.pol")):
        _append_file_reference(registry_ext, gpo, path)
    ET.SubElement(
        registry_ext,
        f"{{{_GPMC_BACKUP_NS}}}FSObjectFile",
        {
            _backup_attr("Path"): r"%GPO_FSPATH%\Adm\*.*",
            _backup_attr("SourceExpandedPath"): _source_path(gpo, r"Adm\*.*"),
        },
    )

    gpp_paths = sorted(path for path in files if "/Preferences/" in path)
    if gpp_paths:
        gpp_ext = ET.SubElement(
            obj,
            f"{{{_GPMC_BACKUP_NS}}}GroupPolicyExtension",
            {
                _backup_attr("ID"): _GPP_FILE_COPY_EXTENSION_GUID,
                _backup_attr("DescName"): "Unknown Extension",
            },
        )
        directories: set[str] = set()
        for path in gpp_paths:
            parts = path.split("/")[:-1]
            directories.update("/".join(parts[:i]) for i in range(2, len(parts) + 1))
        for path in sorted(directories):
            _append_file_reference(gpp_ext, gpo, path, is_dir=True)
        for path in gpp_paths:
            _append_file_reference(gpp_ext, gpo, path)

    scripts_paths = sorted(path for path in files if "/Scripts/" in path)
    if scripts_paths:
        scripts_ext = ET.SubElement(
            obj,
            f"{{{_GPMC_BACKUP_NS}}}GroupPolicyExtension",
            {
                _backup_attr("ID"): _SCRIPTS_CSE_GUID,
                _backup_attr("DescName"): "Scripts",
            },
        )
        directories = {
            "/".join(path.split("/")[:-1]) for path in scripts_paths
        }
        # GPMC lays the trigger subdirectories down alongside the INIs — the R2
        # SYSVOL tree shows Machine/Scripts, Machine/Scripts/Startup and
        # Machine/Scripts/Shutdown with the two INIs — so the backup references
        # them for every side that carries a scripts file, even where Studio
        # ships no payload inside. User sides get the logon/logoff analogues by
        # the same convention (unmeasured; the R2 capture was machine-side
        # only).
        trigger_names = {"Machine": ("Startup", "Shutdown"), "User": ("Logon", "Logoff")}
        for side_dir in sorted({path.split("/", 1)[0] for path in scripts_paths}):
            side_scripts = f"{side_dir}/Scripts"
            directories.update(
                f"{side_scripts}/{trigger}" for trigger in trigger_names[side_dir]
            )
        for path in sorted(directories):
            _append_file_reference(scripts_ext, gpo, path, is_dir=True)
        for path in scripts_paths:
            _append_file_reference(scripts_ext, gpo, path)
    return _xml_to_bytes(root, _GPMC_BACKUP_NS)


def native_backup_refusal(
    gpo: GPO,
    *,
    scripts: Mapping[str, ScriptPolicy] | None = None,
) -> ValidationIssue | None:
    """Why this GPO can get no GMPC-native backup, or ``None`` if it can.

    The companion to `plan_refusal`, and it exists because WI-044 fixed an
    INSTANCE rather than the CLASS. `gpmc_export` was the entry WI-044 held up
    as the correct template — it already reported a `reason` — and it was
    advertising `enabled: true` for GPOs whose backup then refused with 422.
    A GPP Registry preference does it: `Registry` is authorable and has been
    since 1.0, and it is not one of the four families `_GPP_EXTENSION_PROFILES`
    covers, so `_native_export_files` raises `unsupported_native_gpp_extension`
    while `validate_gpo` reports nothing and `preserved_files` stays 0.

    RUNS THE REAL CODE rather than restating its conditions.
    `gpmc_backup_bundle` refuses only inside `native_backup_id` and
    `_native_export_files`; calling both and catching is therefore exact by
    construction, and a new refusal added to either is advertised the day it
    lands instead of the day someone remembers to mirror it. Restating the
    conditions is precisely how the deny case drifted (WI-044), so the same
    mistake is not repeated one capability along.
    """
    try:
        native_backup_id(gpo)
        _native_export_files(gpo, scripts)
    except ValidationError as error:
        return error.issues[0]
    return None


def gpmc_backup_bundle(
    gpo: GPO,
    *,
    scripts: Mapping[str, ScriptPolicy] | None = None,
) -> bytes:
    """Return a deterministic native Backup-GPO-compatible ZIP.

    *scripts* maps ``"computer"``/``"user"`` to that side's `ScriptPolicy`;
    each side with configured entries contributes ``Scripts/scripts.ini``
    (legacy) and/or ``Scripts/psscripts.ini`` (PowerShell) in the measured
    native byte shape, and registers the measured Scripts CSE pair in
    ``MachineExtensionGuids``/``UserExtensionGuids``. States with no measured
    native encoding are refused, not approximated — see
    `_native_scripts_refusal`.
    """
    backup_id = native_backup_id(gpo)
    files, profiles = _native_export_files(gpo, scripts)
    prefix = f"{backup_id}/DomainSysvol/GPO"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        entries: dict[str, bytes] = {
            "manifest.xml": _build_manifest_xml(gpo, backup_id),
            f"{backup_id}/Backup.xml": _build_backup_xml(gpo, files, profiles),
            f"{backup_id}/bkupInfo.xml": _build_bkup_info_xml(gpo, backup_id),
        }
        entries.update({f"{prefix}/{path}": content for path, content in files.items()})
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, entries[name])
    return output.getvalue()
