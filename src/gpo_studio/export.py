"""Build reviewable, deterministic publication artifacts."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Protocol, assert_never, cast
from uuid import NAMESPACE_DNS, UUID, uuid5

from .canonical import (
    CANONICAL_SCHEMA_VERSION,
    policy_semantic_dict,
    policy_semantic_sha256,
    review_model_sha256,
)
from .deterministic_zip import deterministic_zip
from .emit_integrity import (
    BACKUP_XML,
    EmitError,
    EmitProblem,
    check_xml,
    escape_line,
    ps_literal,
    scripts_ini_problems,
)
from .gpp import GppError, contains_cpassword, gpp_registry_unmeasured_shapes, serialize_gpp
from .identity_values import dn_problem, link_target_scope_problem
from .model import GPO, RegistrySetting, ValidationError, ValidationIssue
from .registry_pol import PolRecord, serialize
from .script_policy import PowerShellScriptEntry, ScriptEntry, ScriptPolicy
from .validation import text_issues, validate_gpo

_GPMC_NS = "http://www.microsoft.com/GroupPolicy/GPOOperations/Manifest"
_GPMC_BACKUP_NS = "http://www.microsoft.com/GroupPolicy/GPOOperations"
_REGISTRY_CSE_GUID = "{35378EAC-683F-11D2-A89A-00C04FBBCFA2}"
_REGISTRY_MACHINE_TOOL_GUID = "{D02B1F72-3407-48AE-BA88-E8213C6761F1}"
_REGISTRY_USER_TOOL_GUID = "{D02B1F73-3407-48AE-BA88-E8213C6761F1}"
_GPP_FILE_COPY_EXTENSION_GUID = "{F15C46CD-82A0-4C2D-A210-5D0D3182A418}"
# Windows Defender Firewall snap-in: the tool half GPMC pairs with the Registry
# CSE when the machine Registry.pol holds firewall policy. Measured 2026-10-08
# (tests/fixtures/native-firewall-gpmc): a GPO whose
# Registry.pol held only SOFTWARE\Policies\Microsoft\WindowsFirewall keys got
# gPCMachineExtensionNames = [{35378EAC-...}{B05566AC-...}] -- no {D02B1F72-...}.
_FIREWALL_TOOL_GUID = "{B05566AC-FE9C-4368-BE01-7A4CBB6CBA11}"
_FIREWALL_POLICY_KEY = "software\\policies\\microsoft\\windowsfirewall"
_ZERO_GUID = "{00000000-0000-0000-0000-000000000000}"
_BACKUP_TIME = "1980-01-01T00:00:00"
_NATIVE_BACKUP_NAMESPACE = UUID("9f2492d8-f0d4-45f8-91db-7fc0c86ceae8")

# (client-side extension, tool) per GPP family, keyed by the family's
# Preferences directory. Pinned to genuine WS2025 captures; other GPP families
# remain blocked until their extension metadata is captured. The ONE source of
# these pairs: the native backup, the publication planner
# (`extension_registration`) and `EMITTED_EXTENSION_GUIDS` all read this table.
_GPP_EXTENSION_PROFILES: dict[str, tuple[str, str]] = {
    "Drives": (
        "{5794DAFD-BE60-433F-88A2-1A31939AC01F}",
        "{2EA1A81B-48E5-45E9-8BB7-A6E3AC170006}",
    ),
    "Groups": (
        "{17D89FEC-5C44-4972-B12D-241CAEF74509}",
        "{79F92669-4224-476C-9C5C-6EFB4D87DF4A}",
    ),
    # Measured 2026-10-08 (tests/fixtures/native-gpp-registry-gpmc, every
    # capture.json): Set-GPPrefRegistryValue registered exactly
    # [{B087BE9D-...}{BEE07A6A-...}] in BOTH gPCMachineExtensionNames and
    # gPCUserExtensionNames, and Backup-GPO recorded the same lists. The
    # cmdlet wrote no zero-GUID group; `_extension_guids` lists the tool half in
    # one, as the GPMC editor does: the live census
    # (tests/fixtures/live-domain-census/r06-cse-census) records a production
    # machine list [{00000000-...}{BEE07A6A-...}]...[{B087BE9D-...}{BEE07A6A-...}].
    # The user-side zero group is the same pattern, not separately observed.
    "Registry": (
        "{B087BE9D-ED37-454F-AF9C-04291E351182}",
        "{BEE07A6A-EC9F-4659-B8C9-0B1937907C83}",
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

# Scripts CSE pair, measured on the wire. Machine side: windows-console-driver
# claim-registry R2 re-run, 2026-09-03 — the authoring transaction registered
# exactly ``[{42B5FAAE-6536-11D2-AE5A-0000F87571E3}{40B6664F-4972-11D1-A7CA-0000F87571E3}]``
# in ``gPCMachineExtensionNames``. User side: Plan 035 estate window 19
# (record w19-r2, re-verified w22-r1, 2026-10-21) — GPME writes a DIFFERENT
# tool GUID on the user side, ``40B66650`` where the machine side carries
# ``40B6664F``. scripts.ini (legacy) and psscripts.ini (PowerShell) are the
# payload files of this one extension.
_SCRIPTS_CSE_GUID = "{42B5FAAE-6536-11D2-AE5A-0000F87571E3}"
_SCRIPTS_TOOL_GUID = "{40B6664F-4972-11D1-A7CA-0000F87571E3}"
_SCRIPTS_USER_TOOL_GUID = "{40B66650-4972-11D1-A7CA-0000F87571E3}"

# Every GUID the native backup export can write into ``MachineExtensionGuids``
# / ``UserExtensionGuids``: both halves of every pair, plus the zero-GUID
# prefix of the GPP aggregation group. Inventory only: the known-CSE check
# that once consumed this set went with the ``gpmc_interop`` reduction, and
# ``_unknown_cse_refusal`` now refuses on any preserved ``cse_metadata``
# instead — but the set stays the answer to "which GUIDs can we emit?".
EMITTED_EXTENSION_GUIDS = frozenset({
    _REGISTRY_CSE_GUID,
    _REGISTRY_MACHINE_TOOL_GUID,
    _REGISTRY_USER_TOOL_GUID,
    _FIREWALL_TOOL_GUID,
    _SCRIPTS_CSE_GUID,
    _SCRIPTS_TOOL_GUID,
    _SCRIPTS_USER_TOOL_GUID,
    _ZERO_GUID,
    *(guid for pair in _GPP_EXTENSION_PROFILES.values() for guid in pair),
})

_DOMAIN_NEUTRAL_SECURITY_DESCRIPTOR = (
    "01 00 04 80 14 00 00 00 24 00 00 00 00 00 00 00 34 00 00 00 "
    "01 02 00 00 00 00 00 05 20 00 00 00 20 02 00 00 01 02 00 00 "
    "00 00 00 05 20 00 00 00 20 02 00 00 02 00 34 00 02 00 00 00 "
    "00 00 14 00 00 00 00 10 01 01 00 00 00 00 00 05 12 00 00 00 "
    "00 00 18 00 00 00 00 10 01 02 00 00 00 00 00 05 20 00 00 00 "
    "20 02 00 00"
)


def _ps_sanitize_comment(text: str) -> str:
    """Comment text: every control, format or separator character is written as
    a visible ``\\uXXXX`` (so none can end the comment line), and a backtick as
    a space."""
    return escape_line(text).replace("`", " ")


_HEX_PAIRS = re.compile(r"(?:[0-9A-Fa-f]{2})*", re.ASCII)


def _ps_value(setting: RegistrySetting) -> str:
    if isinstance(setting.value, int):
        return str(setting.value)
    if isinstance(setting.value, list):
        return "@(" + ", ".join(ps_literal(item, "Value") for item in setting.value) + ")"
    if setting.registry_type == "REG_BINARY":
        clean = setting.value.replace(" ", "")
        if not _HEX_PAIRS.fullmatch(clean):
            raise EmitError([
                EmitProblem("plan.ps1", "-Value", setting.value, "is not hexadecimal bytes")
            ])
        if not clean:
            return "([byte[]]@())"
        return (
            "([byte[]](" + ",".join(f"0x{clean[i : i + 2]}" for i in range(0, len(clean), 2)) + "))"
        )
    return ps_literal(setting.value, "Value")


def _text_refusal(gpo: GPO) -> ValidationIssue | None:
    """The first string no artifact can carry (batch-2 review), or ``None``.

    Every exporter asks this first: a stored legacy revision can still hold
    such text, and writing it would either crash (a lone surrogate cannot be
    encoded) or produce XML a parser rejects. It is a refusal with a code,
    reported where the capability is advertised.
    """
    issues = text_issues(gpo)
    return issues[0] if issues else None


def _writer_refusal(error: Exception, path: str) -> ValidationIssue:
    """A writer's refusal as the issue a capability reports (final-r9 DS B2):
    a read or a mutation response must never raise because stored data is
    something a writer will not emit."""
    if isinstance(error, ValidationError) and error.issues:
        return error.issues[0]
    return ValidationIssue(
        severity="error",
        code="unwritable_artifact_value",
        message=f"Studio will not write this artifact: {error}",
        path=path,
    )


def plan_refusal(gpo: GPO) -> ValidationIssue | None:
    """Why this GPO can get no PowerShell plan, or ``None`` if it can.

    The plan route's own function decides (`egress.plan_refusal`, which runs
    `egress.checked_plan`: the writer, then egress), so the capability and the
    download cannot disagree (WI-044; DeepSeek final-r11 B1). A GPO with a
    validation error is reported by `validate_gpo` (the caller's ``blocked``).
    """
    from .egress import plan_refusal as checked

    return cast(ValidationIssue | None, checked(gpo))


def studio_export_refusal(gpo: GPO) -> ValidationIssue | None:
    """Why this GPO can get no Studio bundle, or ``None``: `egress.checked_studio_bundle`
    decides, as the route does."""
    from .egress import studio_export_refusal as checked

    return cast(ValidationIssue | None, checked(gpo))


def _plan_precondition(gpo: GPO) -> ValidationIssue | None:
    """The first of `_plan_refusals`, or ``None``: what refuses a plan before
    any argument is written."""
    refusals = _plan_refusals(gpo)
    return refusals[0] if refusals else None


def _plan_refusals(gpo: GPO) -> list[ValidationIssue]:
    """Every reason a plan is refused before any argument is written.

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
    unwritable = _text_refusal(gpo)
    if unwritable is not None:
        return [unwritable]
    # All of them, not the first (pre-1.1.1 review, round 4): the bundle is
    # written without the plan only when EVERY reason is plan-only, so a deny
    # filter beside a WMI filter refuses the whole export, as it does alone.
    found: list[ValidationIssue] = []
    for link in gpo.links:
        # The plan names its domain on every call, but -Target may name a
        # container in any trusted domain (pre-1.1.1 review, round 2).
        if dn_problem(link.target) is None:
            scope = link_target_scope_problem(link.target, gpo.domain.strip())
            if scope is not None:
                found.append(ValidationIssue(
                    severity="error",
                    code="link_target_outside_domain",
                    message=(
                        f"Link target {link.target!r} {scope}. A PowerShell plan links "
                        "only to the GPO's declared domain and its OUs: set the domain, "
                        "or remove the link."
                    ),
                    path=f"links/{link.id}/target",
                ))
    if gpo.wmi_filter is not None:
        # The plan cannot assign a WMI filter, and it cannot verify an assigned
        # one: the GroupPolicy module exposes a filter's name and path, and its
        # query list's format is unmeasured (round 3). A name match alone let a
        # different filter with the same name -- deleted and recreated, or
        # broader -- satisfy the plan before it linked the GPO.
        found.append(ValidationIssue(
            severity="error",
            code="wmi_filter_not_verifiable",
            message=(
                f"The GPO has the WMI filter {gpo.wmi_filter.name!r}, which a PowerShell "
                "plan can neither assign nor verify, so no plan is generated. Apply this "
                "GPO through the GPMC backup and Import-GPO, and assign the filter in GPMC."
            ),
            path="wmi_filter",
        ))
    denied = [sf for sf in gpo.security_filters if sf.deny]
    if not denied:
        return sorted(found, key=lambda issue: (issue.code, issue.message))
    found.append(ValidationIssue(
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
    ))
    # A canonical order, so two models that differ only in the order of their
    # links -- the same semantic and review digests -- give the same record
    # (round 6). Ordinal: by code, then message (which names the target).
    return sorted(found, key=lambda issue: (issue.code, issue.message))


#: The text a plan writes into the comment of a GPO it creates, and the line it
#: looks for before it will modify an existing GPO without ``-AdoptExistingGpo``.
#: Accident prevention, not authentication: anyone who can edit a GPO's comment
#: can write it.
def plan_binding_marker(gpo: GPO) -> str:
    return f"GPO Studio binding: {gpo.guid}"


def _plan_comment(gpo: GPO) -> str:
    """The comment a plan gives a GPO it creates: the description (or a fixed
    line when there is none), then the binding marker on its own line."""
    return f"{gpo.description or 'Created by GPO Studio'}\n\n{plan_binding_marker(gpo)}"


_SID_PRINCIPAL = re.compile(r"[Ss]-\d")

#: Ordinal string comparisons, the plan's only kind for identities: PowerShell's
#: ``-eq``/``-ccontains`` compare with culture rules, under which a zero-width
#: space or a soft hyphen inside a value still matches (pre-1.1.1 review,
#: round 3).
_ORDINAL = "[StringComparison]::Ordinal"
_IGNORE_CASE = "[StringComparison]::OrdinalIgnoreCase"


def _guid_from_imported_data(gpo: GPO) -> bool:
    """Whether the workspace GUID came from imported data.

    An estate import keeps the GUID its file names as the workspace GUID and
    records it as ``source_guid`` too; every other way of making a workspace
    GPO mints a fresh one. Such a GUID proves nothing about which Studio
    workspace wrote a marker: a crafted estate can carry the GUID read from
    another Studio GPO's comment (comments are readable by every authenticated
    user), so its marker never binds without ``-AdoptExistingGpo``.
    """
    if not gpo.source_guid:
        return False
    try:
        return UUID(gpo.guid.strip("{}")) == UUID(gpo.source_guid.strip("{}"))
    except ValueError:
        return gpo.guid.strip("{}").casefold() == gpo.source_guid.strip("{}").casefold()


def _plan_principals(gpo: GPO) -> list[str]:
    """Resolve every authored filter principal to a SID before anything changes.

    A ``DOMAIN\\name`` or UPN principal is translated to its SID; a SID
    principal to the account name ``Set-GPPermission`` is given. Where the
    filter stores a SID (an import does), a different resolution stops the
    plan: a group deleted and recreated under the same name is not the group
    the filter was authored for. Two filters resolving to one SID stop it too.
    ``$authored`` holds ``(sid, name, level, type)`` per filter; ``$seen``
    maps each SID to its level, ordinally.
    """
    lines = ["$authored = @()"]
    for sf in sorted(gpo.security_filters, key=lambda s: s.principal.casefold()):
        match sf.permission:
            case "apply":
                level = "GpoApply"
            case "read":
                level = "GpoRead"
            case _:
                assert_never(sf.permission)
        if _SID_PRINCIPAL.match(sf.principal):
            sid = ps_literal(sf.principal, "Sid")
            lines += [
                f"try {{ $name = ([System.Security.Principal.SecurityIdentifier]{sid})"
                ".Translate([System.Security.Principal.NTAccount]).Value }",
                f"catch {{ throw ('Cannot resolve ' + {sid}"
                " + ', so nothing was changed: ' + $_.Exception.Message) }",
                f"$sid = ([System.Security.Principal.SecurityIdentifier]{sid}).Value",
            ]
        else:
            principal = ps_literal(sf.principal, "TargetName")
            lines += [
                f"try {{ $sid = ([System.Security.Principal.NTAccount]{principal})"
                ".Translate([System.Security.Principal.SecurityIdentifier]).Value }",
                f"catch {{ throw ('Cannot resolve ' + {principal}"
                " + ', so nothing was changed: ' + $_.Exception.Message) }",
                f"$name = {principal}",
            ]
        if sf.sid:
            stored = ps_literal(sf.sid, "Sid")
            lines.append(
                f"if (-not [string]::Equals($sid, {stored}, {_IGNORE_CASE})) {{"
                f" throw ($name + ' resolves to ' + $sid + ', not the stored ' + {stored}"
                " + ', so nothing was changed.') }"
            )
        lines.append(f"$authored += ,@($sid, $name, '{level}', '{sf.target_type.title()}')")
    lines += [
        "$seen = [System.Collections.Generic.Dictionary[string, string]]::new("
        "[StringComparer]::OrdinalIgnoreCase)",
        "foreach ($a in $authored) {",
        "    if ($seen.ContainsKey($a[0])) {",
        "        throw ('Two security filters resolve to ' + $a[0] + ', so nothing was changed.')",
        "    }",
        "    $seen[$a[0]] = $a[2]",
        "}",
    ]
    return lines


def _plan_new_gpo_editor_check() -> list[str]:
    """Before ``New-GPO``: stop if a filter names a principal every new GPO
    gives edit rights to, so a plan that would be refused creates nothing.

    The measured defaults (WS2025, wp0 permissions.txt) are SYSTEM, the
    declared domain's Domain Admins (RID 512) and the forest root's
    Enterprise Admins (RID 519). Both domain SIDs are read live, from the
    declared domain's RootDSE naming contexts and their ``objectSid`` -- never
    from the authored data, and never by RID alone (round 5: any trusted
    domain's RID-512 group was refused). Through ``psbase``, as the ADSI
    adapter needs (measured).
    """
    root = "[System.DirectoryServices.DirectoryEntry]::new('LDAP://' + $domain + '/RootDSE')"
    return [
        "    try {",
        f"        $rootDse = {root}",
        "        $domainNc = [string]$rootDse.psbase.Properties['defaultNamingContext'].Value",
        "        $rootNc = [string]$rootDse.psbase.Properties['rootDomainNamingContext'].Value",
        "        $domainSid = [System.Security.Principal.SecurityIdentifier]::new([byte[]]("
        "[System.DirectoryServices.DirectoryEntry]::new('LDAP://' + $domain + '/' + $domainNc)"
        ".psbase.Properties['objectSid'].Value), 0).Value",
        "        $rootSid = [System.Security.Principal.SecurityIdentifier]::new([byte[]]("
        "[System.DirectoryServices.DirectoryEntry]::new('LDAP://' + $domain + '/' + $rootNc)"
        ".psbase.Properties['objectSid'].Value), 0).Value",
        "    }",
        "    catch { throw ('Cannot read the SIDs of ' + $domain + ' and its forest root, so"
        " nothing was changed: ' + $_.Exception.Message) }",
        "    $editors = @('S-1-5-18', ($domainSid + '-512'), ($rootSid + '-519'))",
        "    foreach ($a in $authored) {",
        "        if (@($editors | Where-Object {"
        f" [string]::Equals($_, $a[0], {_IGNORE_CASE}) }}).Count -gt 0) {{",
        "            throw ($a[1] + ' has edit rights on every new GPO (SYSTEM, the Domain"
        " Admins of ' + $domain + ' or the Enterprise Admins of its forest), so a security"
        " filter cannot name it. Nothing was changed.')",
        "        }",
        "    }",
    ]


def _plan_new_gpo_owner_check() -> list[str]:
    """Right after ``New-GPO``, before any other change: stop if a filter names
    the new GPO's owner, whose rights ``-Replace`` would take.

    The owner is whom AD records -- read from the new GPO (``$gpo.Owner``, an
    account name, measured as ``LAB\\Domain Admins`` for an administrator
    creator) -- not the local token (``WindowsIdentity.GetCurrent()``), which
    differs under ``runas /netonly`` (round 5). This is the one check that can
    only run after the GPO exists: a stop here leaves it, unlinked, with the
    Windows default permissions.
    """
    return [
        "    try { $ownerSid = ([System.Security.Principal.NTAccount][string]$gpo.Owner)"
        ".Translate([System.Security.Principal.SecurityIdentifier]).Value }",
        "    catch { throw ('Cannot resolve ' + $gpo.Owner + ', the owner of the new GPO. The plan"
        " stops: the GPO exists, unlinked, with the Windows default permissions.') }",
        "    if ($seen.ContainsKey($ownerSid)) {",
        "        throw ($gpo.Owner + ' owns the new GPO, so a security filter cannot name it. The"
        " plan stops: the GPO exists, unlinked, with the Windows default permissions.')",
        "    }",
    ]


def _plan_permission_preflight() -> list[str]:
    """Stop, before any change to the GPO, on what the plan cannot reconcile.

    An authored principal holding an edit level, a custom level, an inherited
    entry or a deny: ``Set-GPPermission -Replace`` would silently remove edit
    rights, cannot change an inherited entry, and does not express a deny.
    Any other trustee holding an inherited ``GpoApply`` or a ``GpoCustom``
    level (which may include Apply): the plan cannot lower either. And every
    trustee the plan will lower must be one it can name: its account name must
    translate back to its own SID. An orphaned SID holding Apply used to stop
    the plan only when it was reached, after Authenticated Users had already
    been lowered -- an outage on a linked GPO that every re-run repeated
    (round 3). ``$lower`` holds ``(name, type)`` for each.
    """
    return [
        "$existing = @(Get-GPPermission -Guid $gpo.Id -All -Domain $domain)",
        "$lower = @()",
        "foreach ($perm in $existing) {",
        "    $held = [string]$perm.Trustee.Sid.Value",
        "    if ($seen.ContainsKey($held)) {",
        "        if ($perm.Denied -or $perm.Inherited -or",
        "            @('GpoApply', 'GpoRead') -notcontains $perm.Permission) {",
        "            throw ($perm.Trustee.Name + ' is named by a security filter but holds '"
        " + $perm.Permission + ' (denied: ' + $perm.Denied + ', inherited: '"
        " + $perm.Inherited + '). The plan will not replace that, so nothing was changed.')",
        "        }",
        "    }",
        "    elseif (-not $perm.Denied -and ($perm.Permission -eq 'GpoCustom' -or",
        "            ($perm.Permission -eq 'GpoApply' -and $perm.Inherited))) {",
        "        throw ('The plan cannot reduce ' + $perm.Permission + ' held by '"
        " + $perm.Trustee.Name + ' (inherited: ' + $perm.Inherited + '), so nothing was"
        " changed. Review it in GPMC, then run the plan again.')",
        "    }",
        "    if ($perm.Permission -eq 'GpoApply' -and -not $perm.Denied -and",
        "        -not ($seen.ContainsKey($held) -and $seen[$held] -eq 'GpoApply')) {",
        "        $who = [string]$perm.Trustee.Name",
        "        if ($perm.Trustee.Domain) {",
        "            $who = $perm.Trustee.Domain + '\\' + $perm.Trustee.Name",
        "        }",
        "        $named = ''",
        "        try { $named = ([System.Security.Principal.NTAccount]$who)"
        ".Translate([System.Security.Principal.SecurityIdentifier]).Value }",
        "        catch { $named = '' }",
        "        if (-not $held -or -not [string]::Equals($named, $held,"
        f" {_IGNORE_CASE})) {{",
        "            throw ('The plan would have to lower GpoApply held by ' + $held + ' ('"
        " + $who + '), but cannot name that trustee, so nothing was changed. Remove the"
        " entry in GPMC, then run the plan again.')",
        "        }",
        "        $type = 'Group'",
        "        if ($perm.Trustee.SidType -eq 'User') { $type = 'User' }",
        "        if ($perm.Trustee.SidType -eq 'Computer') { $type = 'Computer' }",
        "        $lower += ,@($who, $type)",
        "    }",
        "}",
    ]


def _plan_security_filtering(gpo: GPO) -> list[str]:
    """Reconcile filtering after the preflight: lower, then grant, then read back.

    The authored filters are the GPO's whole apply set (Daybreak finding 2).
    Every other non-deny ``GpoApply`` -- Authenticated Users included, which
    every new GPO carries -- is lowered to ``GpoRead`` first (Read stays: since
    MS16-072 the computer reads the GPO for both sides), then each authored
    filter is granted, then the permissions are read back: nothing outside the
    apply set may hold ``GpoApply``, and every authored principal must hold
    exactly its level. Everything is matched by SID. Under ``-WhatIf`` the
    cmdlets report what they would change and the read-back is skipped.

    No filters authored means "not authored", not "apply to nobody": the
    permissions are left as they are (a new GPO: the Windows default,
    Authenticated Users Apply).
    """
    if not gpo.security_filters:
        return [
            "",
            "# Security filtering: none authored. Permissions are left as they are; a new",
            "# GPO keeps the Windows default (Authenticated Users: GpoApply).",
        ]
    lines = [
        "",
        "# Security filtering: the authored filters are the GPO's whole apply set.",
        "# Any other trustee holding GpoApply, Authenticated Users included, is lowered to",
        "# GpoRead first: Read stays because, since MS16-072, the computer reads the GPO",
        "# for both sides. Then the filters are granted and the result is read back.",
    ]
    if not any(sf.permission == "apply" for sf in gpo.security_filters):
        lines.append(
            "# No authored filter grants Apply: after this section the GPO applies to no one."
        )
    lines += [
        "foreach ($l in $lower) {",
        "    Set-GPPermission -Guid $gpo.Id -PermissionLevel GpoRead -TargetName $l[0]"
        " -TargetType $l[1] -Replace -Domain $domain | Out-Null",
        "}",
        "foreach ($a in $authored) {",
        "    Set-GPPermission -Guid $gpo.Id -PermissionLevel $a[2] -TargetName $a[1]"
        " -TargetType $a[3] -Replace -Domain $domain | Out-Null",
        "}",
        "if ($WhatIfPreference) {",
        "    'What if: the security-filtering read-back runs only when the plan makes changes.'",
        "}",
        "else {",
        "    $now = @(Get-GPPermission -Guid $gpo.Id -All -Domain $domain)",
        "    foreach ($perm in $now) {",
        "        $held = [string]$perm.Trustee.Sid.Value",
        "        if ($perm.Permission -eq 'GpoApply' -and -not $perm.Denied -and",
        "            -not ($seen.ContainsKey($held) -and $seen[$held] -eq 'GpoApply')) {",
        "            throw ('After security filtering ' + $perm.Trustee.Name"
        " + ' still holds GpoApply. Stopping before links are written.')",
        "        }",
        "    }",
        "    foreach ($a in $authored) {",
        "        $held = @($now | Where-Object {",
        f"            [string]::Equals([string]$_.Trustee.Sid.Value, $a[0], {_IGNORE_CASE})"
        " -and -not $_.Denied })",
        "        if ($held.Count -ne 1 -or [string]$held[0].Permission -ne $a[2]) {",
        "            throw ('After security filtering ' + $a[1] + ' does not hold exactly '"
        " + $a[2] + '. Stopping before links are written.')",
        "        }",
        "    }",
        "}",
    ]
    return lines


def _link_target_checks(gpo: GPO) -> list[str]:
    """Check every link target before anything changes, against the directory.

    Lexically first: the target ends with the declared domain's DN and the part
    before it names no other domain component (`validation` refuses the same
    targets with a precise RDN parse, `link_target_scope_problem`). Then each
    is resolved with ``Get-GPInheritance`` (read-only), which must answer with
    a container of the expected type and the same path: a missing OU used to be
    found only when its link was written, after filtering, settings and status
    had changed (round 3). The answers are cached in ``$soms`` for the link
    step. ``Get-GPInheritance`` reads only domains and OUs (measured), which is
    one reason plans write no site links.
    """
    if not gpo.links:
        return []
    targets = ", ".join(ps_literal(link.target, "Target") for link in gpo.links)
    return [
        "$domainDn = 'DC=' + (($resolved.DomainName -split '\\.') -join ',DC=')",
        "$soms = [System.Collections.Generic.Dictionary[string, object]]::new("
        "[StringComparer]::OrdinalIgnoreCase)",
        "$objectGuids = [System.Collections.Generic.Dictionary[string, guid]]::new("
        "[StringComparer]::OrdinalIgnoreCase)",
        f"foreach ($target in @({targets})) {{",
        f"    $isDomain = [string]::Equals($target, $domainDn, {_IGNORE_CASE})",
        "    $outside = -not $isDomain -and",
        f"        -not $target.EndsWith(',' + $domainDn, {_IGNORE_CASE})",
        "    if ($outside -or $target.Substring(0, [Math]::Max(0,"
        " $target.Length - $domainDn.Length)) -imatch '(^|,)DC=') {",
        "        throw ('Link target ' + $target + ' is not in ' + $domain"
        " + ', so nothing was changed.')",
        "    }",
        "    try { $som = Get-GPInheritance -Target $target -Domain $domain }",
        "    catch { throw ('Link target ' + $target + ' cannot be read in ' + $domain"
        " + ', so nothing was changed: ' + $_.Exception.Message) }",
        "    $kind = 'OU'",
        "    if ($isDomain) { $kind = 'Domain' }",
        f"    if (-not [string]::Equals([string]$som.ContainerType, $kind, {_ORDINAL}) -or",
        f"        -not [string]::Equals([string]$som.Path, $target, {_IGNORE_CASE})) {{",
        "        throw ('Link target ' + $target + ' resolved to ' + $som.ContainerType + ' '"
        " + $som.Path + ', not the ' + $kind + ' named, so nothing was changed.')",
        "    }",
        "    $soms[$target] = $som",
        # A missing object's Guid reads as $null rather than throwing (measured).
        "    $read = $null",
        "    try { $read = [System.DirectoryServices.DirectoryEntry]::new("
        "'LDAP://' + $domain + '/' + $target.Replace('/', '\\/')).psbase.Guid }",
        "    catch { $read = $null }",
        "    if ($null -eq $read -or $read -eq [guid]::Empty) {",
        "        throw ('Link target ' + $target + ' has no readable objectGUID in ' + $domain"
        " + ', so nothing was changed.')",
        "    }",
        "    $objectGuids[$target] = $read",
        "}",
    ]


def powershell_plan(gpo: GPO) -> str:
    """Generate an idempotent, human-reviewable GroupPolicy module plan.

    Validates its own input (DeepSeek final-r8 N3): a GPO with a validation
    error gets no plan, whichever caller asks, and every quoted argument is
    checked as it is written (:func:`emit_integrity.ps_literal`).

    BOUND TO ONE DOMAIN AND ONE GPO (pre-1.1.1 review, three rounds). Every
    GroupPolicy call names ``-Domain`` with the declared domain, every identity
    comparison is ordinal, and the plan proceeds in this order:

    1. Preflight, changing nothing: the declared domain is reachable
       (``Microsoft.GroupPolicy.GPDomain``; no fallback to the session's
       domain); every link target is in it and resolves to the domain or OU it
       names (`_link_target_checks`); every filter principal resolves, to its
       stored SID where one is stored (`_plan_principals`).
    2. Bind the GPO from a complete ``Get-GPO -All`` listing, so a lookup error
       stops the plan instead of reading as "absent". The GPO with a comment
       line exactly :func:`plan_binding_marker` -- which the plan writes when
       it creates the GPO -- binds without ``-AdoptExistingGpo``, unless the
       workspace GUID came from imported data (`_guid_from_imported_data`) or
       the binding would rename the GPO. Any other existing GPO with the
       workspace GUID or the name needs the switch, and is never renamed.
       Duplicates stop the plan. Otherwise it creates the GPO.
    3. On an existing GPO, the permission preflight
       (`_plan_permission_preflight`), before any change to it.
    4. Security filtering (`_plan_security_filtering`), registry settings, side
       status (through ``ShouldProcess``, so ``-WhatIf`` changes nothing), and
       links last.

    A GPO with an authored WMI filter gets no plan (`_plan_precondition`).

    It is not transactional: what a stop after the preflight leaves behind is
    listed in the plan's header and in ``docs/capability-matrix.md``.
    ``source_guid`` never selects a GPO.
    """
    refused = plan_refusals(gpo)
    if refused.refused:
        raise ValidationError(refused.issues)
    name = ps_literal(gpo.name, "Name")
    trust_marker = "$false" if _guid_from_imported_data(gpo) else "$true"
    lines = [
        "# Generated by GPO Studio. Review before running with delegated GPO rights.",
        "# This plan intentionally does not run from the web application.",
        f"# Domain: {_ps_sanitize_comment(gpo.domain)}. Every GroupPolicy call names it,",
        "# and the plan stops before any change unless it is reachable, every link",
        "# target resolves in it (its objectGUID is read then, and again just before",
        "# its link is written), and every security filter principal resolves.",
        f"# GPO: {_ps_sanitize_comment(gpo.name)}"
        f" (workspace GUID {_ps_sanitize_comment(gpo.guid)}).",
        "# The plan creates the GPO, or modifies the existing GPO whose comment has a",
        "# line that is exactly this plan's binding marker, nothing else on the line",
        "# (written when the plan creates the GPO). That GPO still needs",
        "# -AdoptExistingGpo if the plan would",
        "# rename it or this workspace GPO's GUID came from imported data. Any other",
        "# existing GPO with the workspace GUID or this name needs -AdoptExistingGpo",
        "# and is never renamed. The marker prevents accidents and is",
        "# not authentication: anyone who can edit the comment can write it.",
        "# Run with -WhatIf to see the changes without making them.",
        "# Nothing changes before every check above has passed, except that a GPO the",
        "# plan creates is created before its owner and permissions can be read: the",
        "# owner is whom AD records (for an administrator, the Domain Admins group),",
        "# not necessarily the account running the plan.",
        "# The plan is not transactional. If it stops after its checks:",
        "# - after New-GPO: the GPO exists, unlinked, with Windows default permissions",
        "#   (it stops there if a filter names the new GPO's owner);",
        "# - while lowering Apply: some former Apply holders are at GpoRead and the",
        "#   filters are not granted yet, so the GPO may apply to no one;",
        "# - while granting: some filters hold their level, others do not;",
        "# - at the read-back, settings, status or links: everything before it is done.",
        "# Inspect with Get-GPPermission -All, Get-GPRegistryValue and Get-GPInheritance,",
        "# fix the cause the message names, and run the plan again: each step is",
        "# idempotent. Nothing is linked until the read-back passes.",
        "#Requires -Modules GroupPolicy",
        "[CmdletBinding(SupportsShouldProcess=$true)]",
        "param([switch]$AdoptExistingGpo)",
        "$ErrorActionPreference = 'Stop'",
        f"$domain = {ps_literal(gpo.domain, 'Domain')}",
        f"$marker = {ps_literal(plan_binding_marker(gpo), 'Marker')}",
        f"$trustMarker = {trust_marker}",
        "try { $resolved = [Microsoft.GroupPolicy.GPDomain]::new($domain) }",
        "catch { throw ('Domain ' + $domain + ' is not reachable, so nothing was changed: '"
        " + $_.Exception.Message) }",
        f"if (-not [string]::Equals($resolved.DomainName, $domain, {_IGNORE_CASE})) {{",
        "    throw ('Domain ' + $domain + ' resolved to ' + $resolved.DomainName"
        " + ', so nothing was changed. Declare the DNS name of the domain.')",
        "}",
    ]
    lines += _link_target_checks(gpo)
    lines += _plan_principals(gpo)
    lines += [
        "$all = @(Get-GPO -All -Domain $domain)",
        f"$byGuid = @($all | Where-Object {{ $_.Id -eq [guid]{ps_literal(gpo.guid, 'Guid')} }})",
        f"$byName = @($all | Where-Object {{ [string]::Equals($_.DisplayName, {name},"
        f" {_IGNORE_CASE}) }})",
        "$byMarker = @($all | Where-Object {",
        "    @(([string]$_.Description) -split '\\r?\\n' | Where-Object {",
        f"        [string]::Equals($_, $marker, {_ORDINAL}) }}).Count -gt 0 }})",
        "$gpo = $null",
        "$marked = $false",
        "if ($byMarker.Count -gt 1) {",
        "    throw ('More than one GPO in ' + $domain + ' carries the binding marker of this plan,"
        " so nothing was changed.')",
        "}",
        "if ($byMarker.Count -eq 1) {",
        "    $gpo = $byMarker[0]",
        "    $marked = $true",
        "}",
        "elseif ($byGuid.Count -eq 1) { $gpo = $byGuid[0] }",
        "elseif ($byName.Count -gt 1) {",
        f"    throw ('More than one GPO named ' + {name} + ' exists in ' + $domain"
        " + ', so nothing was changed.')",
        "}",
        "elseif ($byName.Count -eq 1) { $gpo = $byName[0] }",
        "$created = $false",
        "if ($gpo) {",
        "    if (@(@($byGuid) + @($byName) | Where-Object { $_.Id -ne $gpo.Id }).Count -gt 0) {",
        f"        throw ('Another GPO in ' + $domain + ' has the workspace GUID or the name '"
        f" + {name} + ', so nothing was changed.')",
        "    }",
        f"    $sameName = [string]::Equals($gpo.DisplayName, {name}, {_ORDINAL})",
        "    if (-not $marked -and -not $sameName) {",
        "        throw ('GPO ' + $gpo.Id + ' is named ' + $gpo.DisplayName + '. The plan renames"
        " only a GPO it created, so nothing was changed.')",
        "    }",
        "    if (-not ($marked -and $trustMarker -and $sameName) -and -not $AdoptExistingGpo) {",
        "        throw ('GPO ' + $gpo.DisplayName + ' (' + $gpo.Id + ') exists in ' + $domain"
        " + ' and nothing ties it to this plan without -AdoptExistingGpo (it is unmarked,"
        " would be renamed, or this workspace GUID came from imported data), so nothing was"
        " changed. To modify that GPO, run the plan with -AdoptExistingGpo.')",
        "    }",
        "}",
        "else {",
    ]
    if gpo.security_filters:
        lines += _plan_new_gpo_editor_check()
    lines += [
        f"    $gpo = New-GPO -Name {name} -Comment {ps_literal(_plan_comment(gpo), 'Comment')}"
        " -Domain $domain",
        "    if (-not $gpo) {",
        "        'What if: the GPO does not exist yet, so the plan stops after creating it.'",
        "        return",
        "    }",
        "    $created = $true",
    ]
    if gpo.security_filters:
        lines += _plan_new_gpo_owner_check()
    lines += [
        "}",
    ]
    if gpo.security_filters:
        lines += _plan_permission_preflight()
    lines += [
        f"if (-not [string]::Equals($gpo.DisplayName, {name}, {_ORDINAL})) {{",
        f"    Rename-GPO -Guid $gpo.Id -TargetName {name} -Domain $domain | Out-Null",
        "}",
    ]
    lines += _plan_security_filtering(gpo)
    lines.append("")
    for setting in gpo.settings:
        key = f"{setting.hive}\\{setting.key}"
        if setting.action == "delete":
            lines.append(
                "Remove-GPRegistryValue -Guid $gpo.Id"
                f" -Key {ps_literal(key, 'Key')}"
                f" -ValueName {ps_literal(setting.value_name, 'ValueName')}"
                " -Domain $domain -ErrorAction SilentlyContinue"
            )
        elif setting.action == "delete_all_values":
            lines.append(
                f"$existingValues = Get-GPRegistryValue -Guid $gpo.Id"
                f" -Key {ps_literal(key, 'Key')} -Domain $domain -ErrorAction SilentlyContinue"
            )
            lines.append(
                "if ($existingValues) { $existingValues | ForEach-Object"
                f" {{ Remove-GPRegistryValue -Guid $gpo.Id -Key {ps_literal(key, 'Key')}"
                " -ValueName $_.ValueName -Domain $domain -ErrorAction SilentlyContinue } }"
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
                f" -Key {ps_literal(key, 'Key')}"
                f" -ValueName {ps_literal(setting.value_name, 'ValueName')}"
                f" -Type {type_map[setting.registry_type]}"
                f" -Value {_ps_value(setting)} -Domain $domain"
            )
        else:
            assert_never(setting.action)
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
            "# Side enablement is explicit in the draft. GpoStatus is a writable .NET property,",
            "# not a cmdlet, so -WhatIf reaches it only through ShouldProcess.",
            f"if ($PSCmdlet.ShouldProcess($gpo.DisplayName, 'Set GpoStatus to {status_value}')) {{",
            f"    $gpo.GpoStatus = '{status_value}'",
            "}",
        ]
    )
    if gpo.links:
        lines.extend(
            [
                "",
                "# Link intent, last: nothing is linked before filtering and status are in place.",
                "# Each target was resolved in the preflight; New-GPLink updates an existing link.",
            ]
        )
    for link in gpo.links:
        target = ps_literal(link.target, "Target")
        common = (
            f"-Guid $gpo.Id -Target {target}"
            f" -LinkEnabled {'Yes' if link.enabled else 'No'}"
            f" -Enforced {'Yes' if link.enforced else 'No'} -Order {link.order} -Domain $domain"
        )
        lines.extend(
            [
                # The same object the preflight checked: its objectGUID, read
                # again just before the link is written (round 4: a same-DN
                # replacement in between linked a different object). Through
                # psbase: on Windows PowerShell's ADSI adapter ``.Guid`` is the
                # ADSI string property, not DirectoryEntry.Guid (measured).
                "$current = $null",
                f"try {{ $current = [System.DirectoryServices.DirectoryEntry]::new('LDAP://'"
                f" + $domain + '/' + ({target}).Replace('/', '\\/')).psbase.Guid }}",
                "catch { $current = $null }",
                f"if ($null -eq $current -or $current -ne $objectGuids[{target}]) {{",
                f"    throw ('Link target ' + {target} + ' is no longer the object checked"
                " before any change (objectGUID ' + $objectGuids["
                f"{target}] + ', now ' + $current + '), so no link was written.')",
                "}",
                f"$existingLink = $soms[{target}].GpoLinks |",
                "    Where-Object { $_.GpoId -eq $gpo.Id }",
                f"if ($existingLink) {{ Set-GPLink {common} | Out-Null }}",
                f"else {{ New-GPLink {common} | Out-Null }}",
            ]
        )
    return "\n".join(lines) + "\n"


#: Plan refusals that concern only the PowerShell plan: the Studio bundle is
#: still written, without ``apply.ps1``, and its manifest says why
#: (``powershell_plan``). Pre-1.1.1 review, round 3: refusing the whole bundle
#: for a WMI filter or an out-of-domain link would also have withheld the
#: Registry.pol files a 1.0.0 workspace always exported.
PLAN_ONLY_REFUSALS = frozenset({"link_target_outside_domain", "wmi_filter_not_verifiable"})


@dataclass(frozen=True, slots=True)
class PlanRefusals:
    """Every reason a GPO's plan is refused: THE one answer every surface asks
    -- the plan writer, the bundle builder, egress (both ways), the plan and
    export routes and the API's capabilities (pre-1.1.1 review, round 5:
    egress asked a narrower question and accepted a plan for a deny filter).

    ``reasons`` are the plan's own refusals (`_plan_refusals`), ``errors`` the
    GPO's validation errors, which refuse everything."""

    reasons: tuple[ValidationIssue, ...]
    errors: tuple[ValidationIssue, ...]

    @property
    def issues(self) -> list[ValidationIssue]:
        """What a refused plan or bundle raises: the reasons, then the errors."""
        return [*self.reasons, *self.errors]

    @property
    def refused(self) -> bool:
        return bool(self.reasons or self.errors)

    @property
    def whole(self) -> tuple[ValidationIssue, ...]:
        """The reasons that refuse the whole bundle, not just its plan."""
        return tuple(r for r in self.reasons if r.code not in PLAN_ONLY_REFUSALS)

    @property
    def bundle_without_plan(self) -> bool:
        """The bundle is written, without ``apply.ps1``: every reason is
        plan-only and the GPO has no validation error."""
        return bool(self.reasons) and not self.whole and not self.errors

    def record(self) -> dict[str, object] | None:
        """The manifest's ``powershell_plan`` record, exactly, or ``None``."""
        return plan_record(self.reasons) if self.bundle_without_plan else None

    def summary(self) -> ValidationIssue | None:
        """One issue naming every reason (the API's capability ``reason``)."""
        issues = self.issues
        if not issues:
            return None
        if len(issues) == 1:
            return issues[0]
        return ValidationIssue(
            severity="error",
            code=issues[0].code,
            message=" ".join(f"({n}) {issue.message}" for n, issue in enumerate(issues, 1)),
            path=issues[0].path,
        )


def plan_refusals(
    gpo: GPO, validation: Sequence[ValidationIssue] | None = None
) -> PlanRefusals:
    """Every reason *gpo*'s plan is refused (`PlanRefusals`). *validation* is
    `validate_gpo`'s answer when the caller already has it."""
    issues = validate_gpo(gpo) if validation is None else validation
    return PlanRefusals(
        reasons=tuple(_plan_refusals(gpo)),
        errors=tuple(issue for issue in issues if issue.severity == "error"),
    )


def plan_only_refusals(gpo: GPO) -> tuple[ValidationIssue, ...]:
    """Every reason *gpo*'s plan is refused when ALL of them are plan-only (in
    `PLAN_ONLY_REFUSALS`) and the GPO has no validation error; otherwise ``()``
    -- no refusal, or one that refuses the whole bundle as before."""
    refused = plan_refusals(gpo)
    return refused.reasons if refused.bundle_without_plan else ()


def plan_record(refusals: Sequence[ValidationIssue]) -> dict[str, object]:
    """The manifest's ``powershell_plan`` record for a bundle without its plan."""
    return {
        "included": False,
        "refusals": [{"code": issue.code, "message": issue.message} for issue in refusals],
    }


def export_bundle(gpo: GPO) -> bytes:
    """Return a deterministic ZIP containing manifest, PReg files, and plan.

    Without the plan when it alone is refused (`plan_only_refusals`): the
    manifest then carries ``powershell_plan`` (`plan_record`) with every
    refusal's code and message, and egress accepts the missing ``apply.ps1``
    only on that record.
    """
    unwritable = _text_refusal(gpo)
    if unwritable is not None:
        raise ValidationError([unwritable])
    issues = validate_gpo(gpo)
    refused = plan_refusals(gpo)
    manifest: dict[str, object] = {
        "schema_version": 2,
        "kind": "gpo-studio-publication-bundle",
        "gpo": gpo.to_dict(),
        "validation": [asdict(issue) for issue in issues],
        "policy_semantic_sha256": policy_semantic_sha256(gpo),
        "review_model_sha256": review_model_sha256(gpo),
        "canonical_schema_version": CANONICAL_SCHEMA_VERSION,
        "canonical_model": policy_semantic_dict(gpo),
    }
    record = refused.record()
    if record is not None:
        manifest["powershell_plan"] = record
    computer = [item for item in gpo.settings if item.side == "computer"]
    user = [item for item in gpo.settings if item.side == "user"]
    entries: dict[str, bytes] = {
        "manifest.json": json.dumps(manifest, indent=2, sort_keys=True).encode(),
        "Machine/Registry.pol": serialize(computer),
        "User/Registry.pol": serialize(user),
    }
    if record is None:
        # Raises every reason when the whole bundle is refused.
        entries["apply.ps1"] = powershell_plan(gpo).encode("utf-8-sig")
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
    # Platform-identical bytes (members sorted, STORED, fixed metadata).
    return deterministic_zip(entries)


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
    problems = scripts_ini_problems("scripts.ini", sections)
    if problems:
        # A value holding a line break would start an INI line of its own
        # (final-r8: every writer checks what it writes).
        raise EmitError(problems)
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


# Where a script refusal sends the reader. `export_bundle` carries no Scripts
# content, so "use the Studio publication bundle instead" -- what these
# refusals used to say -- pointed at an export that drops scripts silently.
_NO_SCRIPTS_EXPORT = (
    "No Studio export can carry this state: the GPMC backup (gpmc_backup_bundle "
    "with scripts) is the only export that carries scripts, in the measured "
    "shape only, and the Studio publication bundle carries no scripts at all."
)


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

    NO OTHER EXPORT CARRIES IT EITHER. The Studio publication bundle
    (`export_bundle`) writes no Scripts content at all, so the refusal must not
    point there; the GPMC backup with ``scripts=`` is the only export that
    carries scripts, and only in the measured shape. The message says so and
    names the change that makes the policy exportable.
    """
    path = f"scripts/{scope}"
    if policy.run_logon_scripts_sync or policy.run_logoff_scripts_sync:
        return ValidationIssue(
            severity="error",
            code="inexpressible_native_script_state",
            message=(
                "RunLogonScriptsSync/RunLogoffScriptsSync have no native "
                "scripts-file encoding (measured WS2025 GPMC: neither key "
                "exists on the wire). " + _NO_SCRIPTS_EXPORT
                + " Clear both flags to export this policy."
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
                    "only) cannot express. " + _NO_SCRIPTS_EXPORT
                    + " Make the script synchronous to export it."
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
                    "(NCmdLine/NParameters only) cannot express it. "
                    + _NO_SCRIPTS_EXPORT
                    + " Use the defaults (synchronous, profile loaded, "
                    "non-interactive) to export it."
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
    """A GPMC backup document (Backup.xml, bkupInfo.xml, manifest.xml), checked
    as it is written against `emit_integrity.BACKUP_XML` (final-r8)."""
    ET.register_namespace("", namespace)
    result = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    assert isinstance(result, bytes)
    return check_xml(BACKUP_XML, _local_tag(root.tag), result)


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


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
    unwritable = _text_refusal(gpo)
    if unwritable is not None:
        raise ValidationError([unwritable])
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


def _gpp_family_files(gpo: GPO) -> Iterator[tuple[str, str, str, bytes]]:
    """Yield ``(side_dir, family, filename, content)`` for every GPP file.

    One place derives an extension family from a serialized GPP path, so the
    native exporter and the publication planner cannot come to different
    conclusions about which extension a given file belongs to. That divergence
    is exactly what WI-057 measured: the exporter knew the Services pair and
    the planner had no idea the attribute existed.
    """
    for col in gpo.gpp_collections:
        side_dir = "Machine" if col.scope == "computer" else "User"
        try:
            files = serialize_gpp(col)
        except GppError as error:
            # A saved model the writer refuses is a refusal (422), never a
            # crash: `native_backup_refusal` runs on every GPO read, so an
            # exception here made the GPO unreadable (batch-2 review).
            raise ValidationError([
                ValidationIssue(
                    severity="error",
                    code="gpp_not_serializable",
                    message=f"{col.scope} preferences cannot be written: {error}",
                    path="gpp_collections",
                )
            ]) from error
        for filename, content in files.items():
            normalized = filename.replace("\\", "/")
            yield side_dir, normalized.split("/", 1)[0], normalized, content


@dataclass(frozen=True, slots=True)
class ExtensionRegistration:
    """The ``gPC*ExtensionNames`` values a GPO's SYSVOL content requires.

    Windows reads these two attributes to decide which client-side extensions
    to invoke. SYSVOL content whose extension list is unwritten is inert, and
    inert in the worst way -- every file a reviewer would check is present and
    correct. `unverified_families` names GPP families whose extension metadata
    has never been captured, for which no honest value can be produced.
    `unmeasured_shapes` names items of a REGISTERED family whose own wire form
    has no capture (`gpp.gpp_registry_unmeasured_shapes`): the extension list
    can be stated, the file content cannot be vouched for.
    """

    machine: str
    user: str
    unverified_families: tuple[str, ...]
    unmeasured_shapes: tuple[str, ...] = ()


def extension_registration(
    gpo: GPO,
    *,
    scripts: Mapping[str, ScriptPolicy] | None = None,
) -> ExtensionRegistration:
    """Return the extension-list values publishing *gpo* would have to write.

    Computed from the same vocabulary the native backup writes, rather than
    restated, so the two cannot drift. Unlike `_native_export_files` this
    reports unverified families instead of raising: a planner needs to describe
    a refusal, and raising would deny the caller the plan that explains why.
    """
    script_policies = scripts or {}
    profiles: dict[str, set[str]] = {"Machine": set(), "User": set()}
    unverified: set[str] = set()
    for side_dir, family, _filename, _content in _gpp_family_files(gpo):
        if family in _GPP_EXTENSION_PROFILES:
            profiles[side_dir].add(family)
        else:
            unverified.add(family)
    values: dict[str, str] = {}
    for side, scope in (("Machine", "computer"), ("User", "user")):
        values[side] = _extension_guids(
            side=side,
            has_registry=any(item.side == scope for item in gpo.settings),
            registry_tool=_registry_tool_guid(side, gpo.settings),
            gpp_profiles=profiles[side],
            has_scripts=script_policies.get(scope) is not None,
        )
    return ExtensionRegistration(
        machine=values["Machine"],
        user=values["User"],
        unverified_families=tuple(sorted(unverified)),
        unmeasured_shapes=_unmeasured_shapes(gpo),
    )


def _unmeasured_shapes(gpo: GPO) -> tuple[str, ...]:
    return tuple(
        shape
        for collection in gpo.gpp_collections
        for shape in gpp_registry_unmeasured_shapes(collection)
    )


def _native_export_files(
    gpo: GPO,
    scripts: Mapping[str, ScriptPolicy] | None = None,
) -> tuple[dict[str, bytes], dict[str, set[str]]]:
    """Build the native backup's SYSVOL files, or refuse with EVERY reason.

    Each check records its refusal and the function raises once, at the end,
    with the issues ordered most-relevant first: a cpassword (a secret would be
    shipped), then GPP content with no measured extension or wire form, then
    Scripts, then the Folder Redirection carry-over. Callers that show one
    reason (`native_backup_refusal`) show the first; the rest stay on the
    error. Raising at the first check used to report the fdeploy refusal for a
    GPO that also carried a cpassword (review N6).
    """
    computer = [item for item in gpo.settings if item.side == "computer"]
    user = [item for item in gpo.settings if item.side == "user"]
    files: dict[str, bytes] = {}
    profiles: dict[str, set[str]] = {"Machine": set(), "User": set()}
    if computer:
        files["Machine/registry.pol"] = _gpmc_preg_bytes(computer)
    if user:
        files["User/registry.pol"] = _gpmc_preg_bytes(user)

    issues: list[ValidationIssue] = []
    unsupported: set[str] = set()
    for side_dir, family, filename, content in _gpp_family_files(gpo):
        if contains_cpassword(content):
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="cpassword_detected",
                    message=f"GPP file {filename} contains a cpassword attribute.",
                    path=f"gpp_collections/{filename}",
                )
            )
            continue
        if family not in _GPP_EXTENSION_PROFILES:
            unsupported.add(family)
            continue
        profiles[side_dir].add(family)
        files[f"{side_dir}/Preferences/{filename}"] = content

    if unsupported:
        names = ", ".join(sorted(unsupported))
        issues.append(
            ValidationIssue(
                severity="error",
                code="unsupported_native_gpp_extension",
                message=(
                    "Native backup extension metadata has not been verified for: "
                    f"{names}. Use the Studio publication bundle instead."
                ),
                path="gpp_collections",
            )
        )
    shapes = _unmeasured_shapes(gpo)
    if shapes:
        issues.append(
            ValidationIssue(
                severity="error",
                code="unmeasured_gpp_registry_shape",
                message=(
                    "No Windows capture backs the native form of these GPP Registry "
                    f"items: {'; '.join(shapes)}. Use the Studio publication bundle "
                    "instead (WI-075)."
                ),
                path="gpp_collections",
            )
        )

    script_policies = scripts or {}
    for scope in _SCRIPT_SIDES:
        policy = script_policies.get(scope)
        if policy is None:
            continue
        refusal = _native_scripts_refusal(scope, policy)
        if refusal is not None:
            issues.append(refusal)
            continue
        _native_scripts_files(scope, policy, files)

    # WI-068. An imported fdeploy1.ini is carried on the model but there is no
    # writer for it (WI-066 owes the Flags encoding). Re-emitting the imported
    # text alone is not a measured write either: it would also need the Folder
    # Redirection extension registration, and whether Windows needs the empty
    # fdeploy.ini marker beside it is unmeasured. A backup without the file
    # would silently drop the redirection, so refuse. Checked here rather than
    # in the route so `native_backup_refusal` advertises the same refusal.
    # Recorded LAST: it is the least specific of the refusals (review N6).
    if gpo.fdeploy is not None:
        issues.append(
            ValidationIssue(
                severity="error",
                code="folder_redirection_not_exportable",
                message=(
                    "GPO carries an imported Folder Redirection file (fdeploy1.ini). "
                    "Studio reads it but cannot write it, so it cannot be emitted as "
                    "a GPMC backup. Keep the original backup."
                ),
                path="fdeploy",
            )
        )
    if issues:
        raise ValidationError(issues)
    return files, profiles


def _is_firewall_policy_key(key: str) -> bool:
    folded = key.casefold().strip("\\")
    return folded == _FIREWALL_POLICY_KEY or folded.startswith(_FIREWALL_POLICY_KEY + "\\")


def _registry_tool_guid(side: str, settings: Sequence[RegistrySetting]) -> str | None:
    """The tool half to pair with the Registry CSE on *side*, if any.

    Firewall-only machine policy gets the firewall snap-in's tool GUID, as
    measured. Anything else keeps the Administrative Templates tool. MIXED
    machine content (firewall keys and ordinary policy) also keeps it: no
    capture records which list GPMC writes for that combination. The live
    census (tests/fixtures/live-domain-census/r06-cse-census) shows a
    production group [{35378EAC}{B05566AC}{D02B1F72}], which suggests both
    tool halves in one group -- but the census holds no Registry.pol content,
    so what produced it is unmeasured and it is not written (WI-075). User-side
    firewall keys are unmeasured too (firewall policy is computer policy).
    """
    scope = "computer" if side == "Machine" else "user"
    keys = [item.key for item in settings if item.side == scope]
    if not keys:
        return None
    if side == "Machine" and all(_is_firewall_policy_key(key) for key in keys):
        return _FIREWALL_TOOL_GUID
    return _REGISTRY_MACHINE_TOOL_GUID if side == "Machine" else _REGISTRY_USER_TOOL_GUID


def _extension_guids(
    *,
    side: str,
    has_registry: bool,
    gpp_profiles: set[str],
    has_scripts: bool = False,
    registry_tool: str | None = None,
) -> str:
    groups: list[str] = []
    if has_registry:
        tool = registry_tool or (
            _REGISTRY_MACHINE_TOOL_GUID if side == "Machine" else _REGISTRY_USER_TOOL_GUID
        )
        groups.append(f"[{_REGISTRY_CSE_GUID}{tool}]")
    if has_scripts:
        # The pair GPMC itself registers when authoring scripts: one group,
        # CSE GUID then tool GUID, same bracket shape as the other profiles.
        # Both sides are now measured — machine side claim-registry R2 re-run
        # (2026-09-03), user side Plan 035 window 19 (w19-r2, re-verified
        # w22-r1): the user side carries its own tool GUID, not the machine
        # side's. Relative order against the registry/GPP groups was not
        # captured in any single transaction; this order is deterministic.
        tool = _SCRIPTS_TOOL_GUID if side == "Machine" else _SCRIPTS_USER_TOOL_GUID
        groups.append(f"[{_SCRIPTS_CSE_GUID}{tool}]")
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
                registry_tool=_registry_tool_guid("Machine", gpo.settings),
                gpp_profiles=profiles["Machine"],
                has_scripts=machine_scripts,
            ),
        ),
        (
            "UserExtensionGuids",
            _extension_guids(
                side="User",
                has_registry="User/registry.pol" in files,
                registry_tool=_registry_tool_guid("User", gpo.settings),
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
    A GPP Registry preference did it: `Registry` was authorable from 1.0 and
    absent from `_GPP_EXTENSION_PROFILES` until batch 2 measured its pair, so
    `_native_export_files` raised `unsupported_native_gpp_extension` while
    `validate_gpo` reported nothing and `preserved_files` stayed 0. (An
    unmeasured GPP Registry item shape still refuses the same way, WI-075.)

    RUNS THE REAL CODE rather than restating its conditions: it builds the
    backup and catches whatever the writers refuse (the backup documents' own
    emit check included, final-r9), so a new refusal is advertised the day it
    lands instead of the day someone remembers to mirror it. Restating the
    conditions is precisely how the deny case drifted (WI-044), so the same
    mistake is not repeated one capability along.
    """
    from .egress import gpmc_backup_refusal

    return cast(ValidationIssue | None, gpmc_backup_refusal(gpo, scripts))


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

    Validates its own input first, as `powershell_plan` does (Sol final-r18
    B2): a GPO with a validation error -- stored content a current rule
    refuses among them -- gets no backup, whichever caller asks, and nothing
    is serialized for it.
    """
    errors = [issue for issue in validate_gpo(gpo) if issue.severity == "error"]
    if errors:
        raise ValidationError(errors)
    backup_id = native_backup_id(gpo)
    files, profiles = _native_export_files(gpo, scripts)
    prefix = f"{backup_id}/DomainSysvol/GPO"
    entries: dict[str, bytes] = {
        "manifest.xml": _build_manifest_xml(gpo, backup_id),
        f"{backup_id}/Backup.xml": _build_backup_xml(gpo, files, profiles),
        f"{backup_id}/bkupInfo.xml": _build_bkup_info_xml(gpo, backup_id),
    }
    entries.update({f"{prefix}/{path}": content for path, content in files.items()})
    # Platform-identical bytes: every lane candidate built from this backup is
    # hash-bound, so a Windows controller must reproduce Linux's archive exactly.
    return deterministic_zip(entries)
