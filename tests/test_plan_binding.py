"""The PowerShell plan is bound to its declared domain and to one GPO, and its
security filtering does not fail open (pre-1.1.1 security review: Daybreak
findings 1 and 2, then review round 2 of the fix).

Round 1. The plan called every GroupPolicy cmdlet without ``-Domain``, so it
modified whichever domain the session was in, and when the workspace GUID did
not resolve it silently adopted any GPO with the same display name.
``Authenticated Users`` sat in a ``$protected`` list, so the default
``GpoApply`` every new GPO carries was never reduced, and a filter authored for
a pilot group still applied to every authenticated principal.

Round 2. A GUID hit bound any GPO (an estate import names whatever GUID its
file says -- the Default Domain Policy's included); link targets could name
another domain; stored filter SIDs were ignored; a lookup error read as
"absent"; permissions were written before the checks that could refuse them;
``-WhatIf`` still wrote ``GpoStatus``; the marker matched as a substring; an
authored WMI filter was never assigned yet the GPO was linked; an authored
filter silently replaced a group's edit rights; and the read-back only looked
one way.

Two layers of test. The text tests read the generated plan. The execution
tests run it under ``pwsh`` against a STATEFUL stand-in for the GroupPolicy
module: a GPO table (``Id`` a ``[guid]``, compared as one, as the real module
does), a permission table keyed by SID, a stub
``Microsoft.GroupPolicy.GPDomain``, and stub SID/account types in place of
``System.Security.Principal`` (whose translation needs Windows). What is
asserted is what the plan does: which cmdlets ran, against which domain, and
what it left behind. Nothing here touches a directory. The stand-in's
behaviour is the measured one where the repository has a measurement (the
default DACL of a new GPO, ``docs/plan-033/wp0-evidence/*/wp0/permissions.txt``;
``-Replace`` lowering Authenticated Users to GpoRead, the lifecycle lane's
``Set-SecurityFilter``; ``Get-GPO`` not-found being a non-terminating error,
measured on the estate for this change); the rest is the live probe's job.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from gpo_studio import egress, export
from gpo_studio.model import (
    GPO,
    GPOLink,
    RegistrySetting,
    SecurityFilter,
    ValidationError,
    WmiFilter,
)
from gpo_studio.validation import validate_gpo

PWSH = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(PWSH is None, reason="pwsh is not installed on this host")

DOMAIN = "lab.synthetic.test"
DOMAIN_DN = "DC=lab,DC=synthetic,DC=test"
GUID = "{0B0D0080-0000-4000-8000-0000000000B1}"
#: The Default Domain Policy's well-known GUID: no secret, so neither an import
#: naming it as its source nor an estate import keeping it as its workspace
#: GUID may select it without the operator saying so.
WELL_KNOWN_GUID = "{31B2F340-016D-11D2-945F-00C04FB984F9}"
OTHER_ID = "{11111111-2222-4333-8444-555555555555}"
CREATED_ID = "{C0FFEE00-0000-4000-8000-0000000000C1}"
AU_SID = "S-1-5-11"
EVERYONE_SID = "S-1-1-0"
PILOT_SID = "S-1-5-21-1000-2000-3000-1101"
READERS_SID = "S-1-5-21-1000-2000-3000-1102"
OLD_PILOT_SID = "S-1-5-21-1000-2000-3000-1103"
EDITORS_SID = "S-1-5-21-1000-2000-3000-1104"
DA_SID = "S-1-5-21-1000-2000-3000-512"

#: Every GroupPolicy cmdlet the plan may call. Each accepts -Domain (measured
#: on Windows Server 2025, GroupPolicy 1.0.0.0: Get-/Set-GPPermission name it
#: -DomainName with alias -Domain, the rest -Domain with alias -DomainName).
GP_CMDLETS = (
    "Get-GPO",
    "New-GPO",
    "Rename-GPO",
    "Set-GPRegistryValue",
    "Get-GPRegistryValue",
    "Remove-GPRegistryValue",
    "New-GPLink",
    "Set-GPLink",
    "Get-GPInheritance",
    "Get-GPPermission",
    "Set-GPPermission",
)


def _gpo(**changes: Any) -> GPO:
    base = GPO(
        guid=GUID,
        name="Pilot Browser Policy",
        description="Pilot rollout",
        domain=DOMAIN,
        settings=(
            RegistrySetting(
                "s1", "computer", "HKLM", "Software\\Policies\\Example", "Enabled", "REG_DWORD", 1
            ),
        ),
        links=(GPOLink("l1", f"OU=Pilot,{DOMAIN_DN}"),),
        security_filters=(SecurityFilter("f1", "SYNTHETIC\\PilotGroup"),),
    )
    return replace(base, **changes)


def _marker(gpo: GPO) -> str:
    """The binding marker as the plan states it (``$marker = '...'``)."""
    plan = export.powershell_plan(gpo)
    found = re.search(r"^\$marker = '([^']*)'$", plan, re.MULTILINE)
    assert found is not None, "the plan states no binding marker"
    return found.group(1)


def _same_guid(a: str, b: str) -> bool:
    return uuid.UUID(a.strip("{}")) == uuid.UUID(b.strip("{}"))


# ---------------------------------------------------------------------------
# The plan text
# ---------------------------------------------------------------------------


def _code_lines(plan: str) -> list[str]:
    return [
        line for line in plan.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]


def test_every_grouppolicy_call_names_the_declared_domain() -> None:
    plan = export.powershell_plan(_gpo())
    assert f"$domain = '{DOMAIN}'" in plan
    calls = [
        line
        for line in _code_lines(plan)
        if any(re.search(rf"(?<![\w-]){cmd}(?![\w-])", line) for cmd in GP_CMDLETS)
    ]
    assert len(calls) >= 8
    for line in calls:
        assert "-Domain $domain" in line, line


def test_the_domain_is_asserted_before_any_grouppolicy_call() -> None:
    code = _code_lines(export.powershell_plan(_gpo()))
    resolve = next(i for i, line in enumerate(code) if "Microsoft.GroupPolicy.GPDomain" in line)
    compare = next(
        i for i, line in enumerate(code) if "[string]::Equals($resolved.DomainName, $domain" in line
    )
    first_call = next(i for i, line in enumerate(code) if any(cmd in line for cmd in GP_CMDLETS))
    assert resolve < compare < first_call
    assert "throw" in code[resolve + 1] and "throw" in code[compare + 1]


def test_an_existing_gpo_needs_the_exact_marker_line_or_the_switch() -> None:
    gpo = _gpo()
    plan = export.powershell_plan(gpo)
    marker = _marker(gpo)
    assert marker == f"GPO Studio binding: {GUID}"
    assert "param([switch]$AdoptExistingGpo)" in plan
    # A whole, trimmed line, compared ordinally -- not a substring, and not with
    # culture rules (under which a zero-width space still matches).
    assert (
        "        [string]::Equals($_.Trim(), $marker, [StringComparison]::Ordinal) })"
        ".Count -gt 0 })"
    ) in plan
    assert "-ccontains" not in plan
    assert ".Contains($marker)" not in plan
    assert (
        "if (-not ($marked -and $trustMarker -and $sameName) -and -not $AdoptExistingGpo) {"
    ) in plan
    assert "$trustMarker = $true" in plan
    # The plan writes the marker into the comment of a GPO it creates.
    assert (
        "-Comment ('Pilot rollout' + [char]0x000A + [char]0x000A"
        f" + 'GPO Studio binding: {GUID}')"
    ) in plan
    header = plan.split("#Requires")[0]
    assert "-AdoptExistingGpo" in header
    assert "not authentication" in header
    assert DOMAIN in header


def test_the_gpo_is_found_in_a_complete_listing() -> None:
    """No lookup is allowed to fail quietly: ``-ErrorAction SilentlyContinue``
    made access-denied or an unavailable DC read as "absent" and fall through
    to ``New-GPO``."""
    plan = export.powershell_plan(_gpo())
    assert "$all = @(Get-GPO -All -Domain $domain)" in plan
    assert "Get-GPO -Guid" not in plan and "Get-GPO -Name" not in plan
    lookups = [line for line in plan.splitlines() if "Get-GPO" in line]
    assert not any("SilentlyContinue" in line for line in lookups)


def test_source_guid_never_selects_a_gpo() -> None:
    plan = export.powershell_plan(_gpo(source_guid=WELL_KNOWN_GUID))
    assert WELL_KNOWN_GUID.casefold() not in plan.casefold()


def test_authenticated_users_is_not_protected_from_the_filter() -> None:
    plan = export.powershell_plan(_gpo())
    assert "$protected" not in plan
    assert (
        "Set-GPPermission -Guid $gpo.Id -PermissionLevel GpoRead -TargetName $l[0]"
        " -TargetType $l[1] -Replace -Domain $domain | Out-Null"
    ) in plan
    assert "-PermissionLevel None" not in plan


def test_a_stored_sid_is_checked_before_anything_changes() -> None:
    plan = export.powershell_plan(
        _gpo(security_filters=(SecurityFilter("f1", "SYNTHETIC\\PilotGroup", sid=PILOT_SID),))
    )
    check = (
        f"if (-not [string]::Equals($sid, '{PILOT_SID}',"
        " [StringComparison]::OrdinalIgnoreCase))"
    )
    assert check in plan
    assert plan.index(check) < plan.index("$all = @(Get-GPO -All")


def test_preflight_comes_before_the_first_permission_write() -> None:
    code = _code_lines(export.powershell_plan(_gpo()))
    preflight = next(i for i, line in enumerate(code) if "The plan cannot reduce" in line)
    first_write = next(i for i, line in enumerate(code) if "Set-GPPermission" in line)
    lower = next(
        i for i, line in enumerate(code) if "-PermissionLevel GpoRead -TargetName $l[0]" in line
    )
    grant = next(i for i, line in enumerate(code) if "-PermissionLevel $a[2]" in line)
    assert preflight < first_write == lower < grant


def test_filtering_and_status_come_before_links() -> None:
    code = _code_lines(export.powershell_plan(_gpo()))
    readback = max(i for i, line in enumerate(code) if "Stopping before links" in line)
    status = next(i for i, line in enumerate(code) if "$gpo.GpoStatus = " in line)
    first_link = next(i for i, line in enumerate(code) if "New-GPLink" in line)
    assert readback < first_link and status < first_link


def test_side_status_goes_through_should_process() -> None:
    plan = export.powershell_plan(_gpo())
    assert (
        "if ($PSCmdlet.ShouldProcess($gpo.DisplayName, 'Set GpoStatus to AllSettingsEnabled')) {\n"
        "    $gpo.GpoStatus = 'AllSettingsEnabled'\n}"
    ) in plan


def test_no_filters_authored_leaves_permissions_alone() -> None:
    plan = export.powershell_plan(_gpo(security_filters=()))
    assert not any("GPPermission" in line for line in _code_lines(plan))
    assert "none authored" in plan


def test_read_only_filters_say_the_gpo_applies_to_no_one() -> None:
    plan = export.powershell_plan(
        _gpo(security_filters=(SecurityFilter("r", "SYNTHETIC\\Readers", permission="read"),))
    )
    assert "applies to no one" in plan
    assert "$authored += ,@($sid, $name, 'GpoApply'" not in plan


@pytest.mark.parametrize(
    "target",
    [
        "OU=Victims,DC=trusted,DC=test",
        f"OU=Child,DC=sub,{DOMAIN_DN}",
        f"CN=Users,{DOMAIN_DN}",
        "CN=Default-First-Site-Name,CN=Sites,CN=Configuration,DC=root,DC=test",
        "DC=synthetic,DC=test",
        f"CN=Default-First-Site-Name,CN=Sites,CN=Configuration,{DOMAIN_DN}",
    ],
    ids=[
        "other-domain",
        "child-domain",
        "container",
        "other-forest-site",
        "parent-domain",
        "own-forest-site",
    ],
)
def test_a_link_target_outside_the_declared_domain_is_refused(target: str) -> None:
    gpo = _gpo(links=(GPOLink("l1", target),))
    # Flagged by validation (a warning: the GPMC backup carries no links, and a
    # real estate may link across domains) and refused by the plan.
    warnings = {issue.code for issue in validate_gpo(gpo) if issue.severity == "warning"}
    assert "link_target_outside_domain" in warnings
    refusal = export.plan_refusal(gpo)
    assert refusal is not None and refusal.code == "link_target_outside_domain"
    with pytest.raises(ValidationError) as caught:
        export.powershell_plan(gpo)
    assert caught.value.issues[0].code == "link_target_outside_domain"


@pytest.mark.parametrize(
    "target",
    [
        DOMAIN_DN,
        f"OU=Pilot,OU=Clients,{DOMAIN_DN}",
        "ou=pilot,dc=LAB,dc=Synthetic,dc=TEST",
    ],
    ids=["domain", "nested-ou", "case"],
)
def test_a_link_target_in_the_declared_domain_is_accepted(target: str) -> None:
    gpo = _gpo(links=(GPOLink("l1", target),))
    assert not [issue for issue in validate_gpo(gpo) if issue.code == "link_target_outside_domain"]
    assert export.plan_refusal(gpo) is None
    assert "$domainDn = 'DC=' + " in export.powershell_plan(gpo)


def test_the_bound_plan_passes_egress() -> None:
    for gpo in (
        _gpo(),
        _gpo(security_filters=()),
        _gpo(description=""),
        _gpo(security_filters=(SecurityFilter("r", "SYNTHETIC\\Readers", permission="read"),)),
        _gpo(security_filters=(SecurityFilter("au", AU_SID, sid=AU_SID),)),
    ):
        assert egress.powershell_plan_problems(export.powershell_plan(gpo)) == []


# ---------------------------------------------------------------------------
# The plan, executed against a stateful stand-in
# ---------------------------------------------------------------------------

#: The stand-in. ``$S`` is the scenario: ``Gpos`` (existing GPOs; ``WmiFilter``
#: the name of an assigned filter), ``Perms`` (the permission table of whichever
#: GPO the plan binds, keyed by SID), ``Names`` (account name -> SID, as the
#: domain resolves it; also read by the stub SID types through
#: ``STUB_NAMES``), ``IgnoreSid`` (a SID whose Set-GPPermission the stand-in
#: drops, to prove the read-back) and ``LookupFails``. Mutating stand-ins honour
#: ``$WhatIfPreference`` as the real cmdlets do. Every stand-in accepts
#: ``-ErrorAction`` and makes ``-Domain`` optional, so the round-1 plan runs
#: under it too (red-before-green). Every call is logged as
#: ``CALL=<base64 json>``, and the message of the error that stops the plan as
#: ``ERROR=<base64>``.
_STANDIN = r"""
trap {
  [Console]::Out.WriteLine('ERROR=' + [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes([string]$_.Exception.Message)))
  exit 1
}
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
namespace Microsoft.GroupPolicy {
  public class GPDomain {
    public string DomainName { get; private set; }
    public GPDomain(string name) {
      string to = Environment.GetEnvironmentVariable("STUB_RESOLVES_TO");
      if (string.IsNullOrEmpty(to)) {
        throw new Exception(
          "The specified domain either does not exist or could not be contacted.");
      }
      DomainName = to;
    }
  }
}
namespace StudioStub {
  static class Directory {
    public static Dictionary<string, string> Names() {
      var map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
      string raw = Environment.GetEnvironmentVariable("STUB_NAMES") ?? "";
      foreach (string line in raw.Split('\n')) {
        int tab = line.IndexOf('\t');
        if (tab > 0) { map[line.Substring(0, tab)] = line.Substring(tab + 1); }
      }
      return map;
    }
  }
  public class Account {
    public string Value { get; private set; }
    public Account(string value) { Value = value; }
    public Sid Translate(Type type) {
      string sid;
      if (!Directory.Names().TryGetValue(Value, out sid)) {
        throw new Exception("Some or all identity references could not be translated.");
      }
      return new Sid(sid);
    }
  }
  public class Sid {
    public string Value { get; private set; }
    public Sid(string value) {
      if (!value.StartsWith("S-")) { throw new ArgumentException("not a SID"); }
      Value = value;
    }
    public Account Translate(Type type) {
      foreach (var entry in Directory.Names()) {
        if (entry.Value == Value && !entry.Key.StartsWith("S-")) { return new Account(entry.Key); }
      }
      throw new Exception("Some or all identity references could not be translated.");
    }
  }
}
'@
$S = ConvertFrom-Json ([Text.Encoding]::UTF8.GetString(
  [Convert]::FromBase64String($env:STUB_SCENARIO)))
$script:perms = [System.Collections.ArrayList]@()
foreach ($p in $S.Perms) { [void]$script:perms.Add($p) }
$script:bound = $null
function _Log($cmd, $fields) {
  $fields['cmd'] = $cmd
  $fields['whatif'] = [bool]$WhatIfPreference
  [Console]::Out.WriteLine('CALL=' + [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json $fields -Compress))))
}
function _Gpo($g) {
  $wmi = $null
  if ($g.WmiFilter) { $wmi = [pscustomobject]@{ Name = $g.WmiFilter } }
  [pscustomobject]@{ Id = [guid]$g.Id; DisplayName = $g.DisplayName;
    Description = $g.Description; GpoStatus = 'Unchanged'; WmiFilter = $wmi } }
function _Perm($p) { [pscustomobject]@{ Permission = $p.Permission; Denied = [bool]$p.Denied;
  Inherited = [bool]$p.Inherited; Trustee = [pscustomobject]@{ Domain = $p.Domain;
  Name = $p.Name; SidType = $p.SidType; Sid = [pscustomobject]@{ Value = $p.Sid } } } }
function _Sid($name) { $S.Names.$name }
function Get-GPO { [CmdletBinding()] param($Guid, $Name, [switch]$All, $Domain)
  _Log 'Get-GPO' @{ domain = $Domain; guid = [string]$Guid; name = $Name; all = [bool]$All }
  if ($S.LookupFails) { Write-Error 'stand-in: access is denied'; return }
  $objects = @(foreach ($g in $S.Gpos) { _Gpo $g })
  if ($All) { $script:listed = $objects; return $objects }
  $hits = @($objects | Where-Object {
    ($Guid -and $_.Id -eq [guid][string]$Guid) -or ($Name -and $_.DisplayName -eq $Name) })
  if ($hits.Count -eq 1) { return $hits[0] }
  Write-Error 'stand-in: GPO not found or not unique' }
function New-GPO { param($Name, $Comment, $Domain, $ErrorAction)
  _Log 'New-GPO' @{ domain = $Domain; name = $Name; comment = $Comment }
  if ($WhatIfPreference) { return }
  [pscustomobject]@{ Id = [guid]'C0FFEE00-0000-4000-8000-0000000000C1'; DisplayName = $Name;
    Description = $Comment; GpoStatus = 'Unchanged'; WmiFilter = $null } }
function Rename-GPO { param($Guid, $TargetName, $Domain, $ErrorAction)
  _Log 'Rename-GPO' @{ domain = $Domain; guid = [string]$Guid; name = $TargetName } }
function Set-GPRegistryValue { param($Guid, $Key, $ValueName, $Type, $Value, $Domain,
  $ErrorAction)
  _Log 'Set-GPRegistryValue' @{ domain = $Domain; guid = [string]$Guid; key = $Key } }
function Remove-GPRegistryValue { param($Guid, $Key, $ValueName, $Domain, $ErrorAction)
  _Log 'Remove-GPRegistryValue' @{ domain = $Domain; guid = [string]$Guid } }
function Get-GPRegistryValue { param($Guid, $Key, $Domain, $ErrorAction)
  _Log 'Get-GPRegistryValue' @{ domain = $Domain }; $null }
function Get-GPInheritance { param($Target, $Domain, $ErrorAction)
  _Log 'Get-GPInheritance' @{ domain = $Domain; target = $Target }
  # As measured: a missing OU throws "There is no such object on the server";
  # the path comes back lower-cased.
  if ($S.MissingTargets -contains $Target) {
    throw 'stand-in: There is no such object on the server.' }
  $type = 'OU'
  if ($Target -notmatch '(?i)^OU=') { $type = 'Domain' }
  if ($S.WrongType -contains $Target) { $type = 'Site' }
  [pscustomobject]@{ ContainerType = $type; Path = $Target.ToLowerInvariant();
    GpoLinks = @() } }
function New-GPLink { param($Guid, $Target, $LinkEnabled, $Enforced, $Order, $Domain,
  $ErrorAction)
  _Log 'New-GPLink' @{ domain = $Domain; guid = [string]$Guid; target = $Target } }
function Set-GPLink { param($Guid, $Target, $LinkEnabled, $Enforced, $Order, $Domain,
  $ErrorAction)
  _Log 'Set-GPLink' @{ domain = $Domain; guid = [string]$Guid; target = $Target } }
function Get-GPPermission { param($Guid, [switch]$All, $TargetName, $TargetType, $Domain,
  $ErrorAction)
  _Log 'Get-GPPermission' @{ domain = $Domain; guid = [string]$Guid; target = $TargetName }
  if ($All) { return @($script:perms | ForEach-Object { _Perm $_ }) }
  $sid = _Sid $TargetName
  foreach ($p in $script:perms) { if ($p.Sid -eq $sid) { return _Perm $p } }
  $null }
function Set-GPPermission { param($Guid, $PermissionLevel, $TargetName, $TargetType,
  [switch]$Replace, $Domain, $ErrorAction)
  _Log 'Set-GPPermission' @{ domain = $Domain; guid = [string]$Guid; target = $TargetName;
    level = [string]$PermissionLevel; type = [string]$TargetType; replace = [bool]$Replace }
  if ($WhatIfPreference) { return }
  $sid = _Sid $TargetName
  if (-not $sid) { throw ('stand-in: cannot resolve ' + $TargetName) }
  if ($sid -eq $S.IgnoreSid) { return }
  foreach ($p in $script:perms) {
    if ($p.Sid -eq $sid -and -not $p.Denied) {
      if ($Replace) { $p.Permission = [string]$PermissionLevel }
      return
    }
  }
  $parts = $TargetName.Split('\')
  [void]$script:perms.Add([pscustomobject]@{ Sid = $sid; Domain = $parts[0]; Name = $parts[-1];
    SidType = [string]$TargetType; Permission = [string]$PermissionLevel; Denied = $false;
    Inherited = $false }) }
"""

_FOOTER = r"""
[Console]::Out.WriteLine('FINAL=' + [Convert]::ToBase64String(
  [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json @($script:perms) -Compress -Depth 4))))
[Console]::Out.WriteLine('STATUS=' + [string]$gpo.GpoStatus)
"""


def _perm(
    sid: str,
    domain: str,
    name: str,
    permission: str,
    sid_type: str = "Group",
    *,
    denied: bool = False,
    inherited: bool = False,
) -> dict[str, Any]:
    return {
        "Sid": sid,
        "Domain": domain,
        "Name": name,
        "SidType": sid_type,
        "Permission": permission,
        "Denied": denied,
        "Inherited": inherited,
    }


#: A new GPO's DACL as Windows Server 2025 reports it (wp0 permissions.txt):
#: Authenticated Users GpoApply, the admins GpoEditDeleteModifySecurity,
#: Enterprise Domain Controllers GpoRead.
def _default_perms() -> list[dict[str, Any]]:
    return [
        _perm(AU_SID, "NT AUTHORITY", "Authenticated Users", "GpoApply", "WellKnownGroup"),
        _perm(DA_SID, "SYNTHETIC", "Domain Admins", "GpoEditDeleteModifySecurity"),
        _perm(
            "S-1-5-21-1000-2000-3000-519",
            "SYNTHETIC",
            "Enterprise Admins",
            "GpoEditDeleteModifySecurity",
        ),
        _perm(
            "S-1-5-9", "NT AUTHORITY", "ENTERPRISE DOMAIN CONTROLLERS", "GpoRead", "WellKnownGroup"
        ),
        _perm(
            "S-1-5-18", "NT AUTHORITY", "SYSTEM", "GpoEditDeleteModifySecurity", "WellKnownGroup"
        ),
    ]


_NAMES = {
    "SYNTHETIC\\PilotGroup": PILOT_SID,
    "SYNTHETIC\\Readers": READERS_SID,
    "SYNTHETIC\\OldPilot": OLD_PILOT_SID,
    "SYNTHETIC\\Editors": EDITORS_SID,
    "SYNTHETIC\\Domain Admins": DA_SID,
    "NT AUTHORITY\\Authenticated Users": AU_SID,
    "Everyone": EVERYONE_SID,
    AU_SID: AU_SID,
}


def _runnable(plan: str) -> str:
    lines = [line for line in plan.splitlines() if not line.startswith("#Requires")]
    # After the param block, which must stay the first statement. ``param()``
    # is the round-0 plan's, so the same harness runs it (red-before-green).
    index = next(i for i, line in enumerate(lines) if line.startswith("param("))
    text = "\n".join([*lines[: index + 1], _STANDIN, *lines[index + 1 :], _FOOTER]) + "\n"
    # SID <-> account translation needs Windows: the stub types stand in.
    return text.replace("[System.Security.Principal.NTAccount]", "[StudioStub.Account]").replace(
        "[System.Security.Principal.SecurityIdentifier]", "[StudioStub.Sid]"
    )


class Run:
    def __init__(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.returncode = completed.returncode
        self.output = completed.stdout + completed.stderr
        lines = completed.stdout.splitlines()
        #: The message of the terminating error that stopped the plan, if any.
        self.error = "".join(
            base64.b64decode(line.split("=", 1)[1]).decode("utf-8")
            for line in lines
            if line.startswith("ERROR=")
        )
        self.calls: list[dict[str, Any]] = [
            json.loads(base64.b64decode(line.split("=", 1)[1]))
            for line in lines
            if line.startswith("CALL=")
        ]
        final = [line for line in lines if line.startswith("FINAL=")]
        self.final: dict[str, dict[str, Any]] | None = (
            {p["Sid"]: p for p in json.loads(base64.b64decode(final[0].split("=", 1)[1]))}
            if final
            else None
        )
        status = [line for line in lines if line.startswith("STATUS=")]
        self.status = status[0].split("=", 1)[1] if status else None

    def called(self, cmd: str) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["cmd"] == cmd]

    @property
    def mutations(self) -> list[dict[str, Any]]:
        """Mutating calls that would have changed something (not under -WhatIf)."""
        return [
            call
            for call in self.calls
            if call["cmd"].split("-")[0] in {"New", "Set", "Rename", "Remove"}
            and not call.get("whatif")
        ]


def _execute(
    tmp_path: Path,
    gpo: GPO,
    *,
    resolves: str = DOMAIN,
    gpos: list[dict[str, str]] | None = None,
    perms: list[dict[str, Any]] | None = None,
    names: dict[str, str] | None = None,
    adopt: bool = False,
    whatif: bool = False,
    ignore_sid: str = "",
    lookup_fails: bool = False,
    missing_targets: list[str] | None = None,
    wrong_type: list[str] | None = None,
    plan: str | None = None,
) -> Run:
    name_map = {**_NAMES, **(names or {})}
    scenario = {
        "Gpos": gpos or [],
        "Perms": _default_perms() if perms is None else perms,
        "Names": name_map,
        "IgnoreSid": ignore_sid,
        "LookupFails": lookup_fails,
        "MissingTargets": missing_targets or [],
        "WrongType": wrong_type or [],
    }
    path = tmp_path / "apply.ps1"
    path.write_text(
        _runnable(export.powershell_plan(gpo) if plan is None else plan), encoding="utf-8-sig"
    )
    assert PWSH is not None
    args = [PWSH, "-NoProfile", "-NonInteractive", "-File", str(path)]
    if adopt:
        args.append("-AdoptExistingGpo")
    if whatif:
        args.append("-WhatIf")
    completed = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={
            **os.environ,
            "STUB_RESOLVES_TO": resolves,
            "STUB_NAMES": "\n".join(f"{k}\t{v}" for k, v in name_map.items()),
            "STUB_SCENARIO": base64.b64encode(json.dumps(scenario).encode()).decode(),
        },
    )
    return Run(completed)


def _assert_domain_bound(run: Run) -> None:
    assert run.calls, run.output
    for call in run.calls:
        assert call["domain"] == DOMAIN, call


def _existing(gpo_id: str, name: str, description: str = "", wmi: str = "") -> dict[str, str]:
    return {"Id": gpo_id, "DisplayName": name, "Description": description, "WmiFilter": wmi}


# --- the domain ---------------------------------------------------------------


@needs_pwsh
def test_an_unreachable_domain_stops_the_plan_before_any_call(tmp_path: Path) -> None:
    run = _execute(tmp_path, _gpo(), resolves="")
    assert run.returncode != 0
    assert "not reachable, so nothing was changed" in run.error
    assert run.calls == []


@needs_pwsh
def test_a_domain_resolving_to_another_name_stops_the_plan(tmp_path: Path) -> None:
    run = _execute(tmp_path, _gpo(), resolves="corp.other.test")
    assert run.returncode != 0
    assert "resolved to corp.other.test, so nothing was changed" in run.error
    assert run.calls == []


@needs_pwsh
def test_the_plan_checks_link_targets_itself(tmp_path: Path) -> None:
    """Validation refuses an out-of-domain target; the plan's own check is the
    run-time half. A writer regression that let one through is stopped before
    any GroupPolicy call."""
    gpo = _gpo()
    plan = export.powershell_plan(gpo).replace(
        f"foreach ($target in @('OU=Pilot,{DOMAIN_DN}')) {{",
        "foreach ($target in @('OU=Victims,DC=trusted,DC=test')) {",
    )
    assert "OU=Victims" in plan
    run = _execute(tmp_path, gpo, plan=plan)
    assert run.returncode != 0
    assert "is not in lab.synthetic.test, so nothing was changed" in run.error
    assert run.calls == []
    child = export.powershell_plan(gpo).replace(
        f"foreach ($target in @('OU=Pilot,{DOMAIN_DN}')) {{",
        f"foreach ($target in @('OU=Pilot,DC=child,{DOMAIN_DN}')) {{",
    )
    assert _execute(tmp_path, gpo, plan=child).calls == []


# --- which GPO ----------------------------------------------------------------


@needs_pwsh
def test_a_new_gpo_is_created_in_the_declared_domain_with_the_marker(tmp_path: Path) -> None:
    gpo = _gpo()
    run = _execute(tmp_path, gpo)
    assert run.returncode == 0, run.output
    _assert_domain_bound(run)
    (created,) = run.called("New-GPO")
    assert created["comment"].endswith(_marker(gpo))
    assert run.called("New-GPLink")
    assert run.status == "AllSettingsEnabled"


@needs_pwsh
def test_an_estate_import_keeping_the_default_domain_policy_guid_is_refused(
    tmp_path: Path,
) -> None:
    """Round 2's blocker: estate import keeps the file's GUID as the workspace
    GUID, so a GUID hit alone bound -- and renamed, refiltered and linked -- the
    Default Domain Policy. The existing GPO is compared as a ``[guid]`` (braces
    and case do not matter), as real Get-GPO parses one."""
    gpo = _gpo(guid=WELL_KNOWN_GUID.strip("{}").lower())
    ddp = _existing(WELL_KNOWN_GUID, "Default Domain Policy")
    run = _execute(tmp_path, gpo, gpos=[ddp])
    assert run.returncode != 0
    assert "so nothing was changed" in run.error
    assert run.mutations == [], run.mutations


@needs_pwsh
def test_the_switch_never_renames_a_gpo_the_plan_did_not_create(tmp_path: Path) -> None:
    gpo = _gpo(guid=WELL_KNOWN_GUID)
    ddp = _existing(WELL_KNOWN_GUID, "Default Domain Policy")
    run = _execute(tmp_path, gpo, gpos=[ddp], adopt=True)
    assert run.returncode != 0
    assert "renames only a GPO it created" in run.error
    assert run.mutations == []


@needs_pwsh
def test_the_switch_adopts_an_unmarked_gpo_of_the_same_name(tmp_path: Path) -> None:
    existing = _existing(OTHER_ID, "Pilot Browser Policy")
    run = _execute(tmp_path, _gpo(), gpos=[existing], adopt=True)
    assert run.returncode == 0, run.output
    _assert_domain_bound(run)
    assert run.called("New-GPO") == [] and run.called("Rename-GPO") == []
    targets = {call["guid"] for call in run.called("Set-GPRegistryValue")}
    assert len(targets) == 1 and _same_guid(targets.pop(), OTHER_ID)


@needs_pwsh
def test_a_marked_gpo_is_renamed_only_with_the_switch(tmp_path: Path) -> None:
    """Round 3 (Opus S1): a marker binding that would also rename the GPO needs
    -AdoptExistingGpo -- a rename is how a harvested marker took over a GPO."""
    gpo = _gpo(guid=GUID.strip("{}").lower())
    existing = _existing(GUID, "Old name", f"Pilot rollout\n\n{_marker(gpo)}")
    run = _execute(tmp_path, gpo, gpos=[existing])
    assert run.returncode != 0
    assert "would be renamed" in run.error
    assert run.mutations == []
    run = _execute(tmp_path, gpo, gpos=[existing], adopt=True)
    assert run.returncode == 0, run.output
    (renamed,) = run.called("Rename-GPO")
    assert renamed["name"] == "Pilot Browser Policy"


@needs_pwsh
def test_the_gpo_the_plan_created_is_found_by_its_marker_after_a_rename(tmp_path: Path) -> None:
    """New-GPO assigns its own GUID, so a re-run after the workspace GPO was
    renamed finds the GPO by neither GUID nor name -- only by its marker. The
    rename back needs the switch (round 3)."""
    gpo = _gpo()
    mine = _existing(OTHER_ID, "Previous name", _marker(gpo))
    run = _execute(tmp_path, gpo, gpos=[mine], adopt=True)
    assert run.returncode == 0, run.output
    assert run.called("New-GPO") == []
    (renamed,) = run.called("Rename-GPO")
    assert _same_guid(renamed["guid"], OTHER_ID) and renamed["name"] == gpo.name


@needs_pwsh
def test_an_unrelated_same_named_gpo_is_refused(tmp_path: Path) -> None:
    existing = _existing(OTHER_ID, "Pilot Browser Policy", "Somebody else's policy")
    run = _execute(tmp_path, _gpo(), gpos=[existing])
    assert run.returncode != 0
    assert "nothing ties it to this plan without -AdoptExistingGpo" in run.error
    assert "-AdoptExistingGpo" in run.error
    assert run.mutations == [], run.mutations


@needs_pwsh
def test_a_rerun_finds_the_gpo_it_created_by_its_marker(tmp_path: Path) -> None:
    gpo = _gpo()
    existing = _existing(OTHER_ID, gpo.name, f"Pilot rollout\r\n\r\n  {_marker(gpo)}  ")
    run = _execute(tmp_path, gpo, gpos=[existing])
    assert run.returncode == 0, run.output
    assert run.called("New-GPO") == []
    targets = {call["guid"] for call in run.called("Set-GPRegistryValue")}
    assert len(targets) == 1 and _same_guid(targets.pop(), OTHER_ID)


@pytest.mark.parametrize(
    "description",
    [
        "Retired plan -- {marker} -- do not reuse",
        "{lower}",
        "{marker}x",
    ],
    ids=["inside-a-sentence", "other-case", "suffixed"],
)
@needs_pwsh
def test_the_marker_must_be_a_whole_line(tmp_path: Path, description: str) -> None:
    gpo = _gpo()
    marker = _marker(gpo)
    text = description.format(marker=marker, lower=marker.lower())
    run = _execute(tmp_path, gpo, gpos=[_existing(OTHER_ID, gpo.name, text)])
    assert run.returncode != 0
    assert run.mutations == []


@needs_pwsh
def test_another_workspace_gpos_marker_does_not_bind(tmp_path: Path) -> None:
    gpo = _gpo()
    other = replace(gpo, guid="{0B0D0080-0000-4000-8000-0000000000B2}")
    run = _execute(tmp_path, gpo, gpos=[_existing(OTHER_ID, gpo.name, _marker(other))])
    assert run.returncode != 0
    assert run.mutations == []


@needs_pwsh
def test_a_source_guid_naming_an_existing_gpo_selects_nothing(tmp_path: Path) -> None:
    existing = _existing(WELL_KNOWN_GUID, "Pilot Browser Policy")
    run = _execute(tmp_path, _gpo(source_guid=WELL_KNOWN_GUID), gpos=[existing])
    assert run.returncode != 0
    assert run.mutations == []


@needs_pwsh
def test_a_lookup_error_stops_the_plan_instead_of_reading_as_absent(tmp_path: Path) -> None:
    """Access denied or an unavailable DC used to read as "no such GPO", and the
    plan created (and linked) a replacement."""
    run = _execute(tmp_path, _gpo(), lookup_fails=True)
    assert run.returncode != 0
    assert "access is denied" in run.error
    assert run.called("New-GPO") == [] and run.mutations == []


@needs_pwsh
def test_two_gpos_with_the_name_stop_the_plan(tmp_path: Path) -> None:
    gpo = _gpo()
    twins = [
        _existing(OTHER_ID, gpo.name),
        _existing("{AAAAAAAA-0000-4000-8000-000000000002}", gpo.name),
    ]
    run = _execute(tmp_path, gpo, gpos=twins, adopt=True)
    assert run.returncode != 0
    assert "More than one GPO named" in run.error
    assert run.mutations == []
    marked_twins = [{**twin, "Description": _marker(gpo)} for twin in twins]
    run = _execute(tmp_path, gpo, gpos=marked_twins)
    assert run.returncode != 0
    assert "More than one GPO in lab.synthetic.test carries the binding marker" in run.error
    assert run.mutations == []


@needs_pwsh
def test_another_gpo_holding_the_name_beside_a_guid_hit_stops_the_plan(tmp_path: Path) -> None:
    gpo = _gpo()
    both = [
        _existing(GUID, "Old name", _marker(gpo)),
        _existing(OTHER_ID, gpo.name),
    ]
    run = _execute(tmp_path, gpo, gpos=both)
    assert run.returncode != 0
    assert "Another GPO in lab.synthetic.test has the workspace GUID or the name" in run.error
    assert run.mutations == []


# --- security filtering -------------------------------------------------------


@needs_pwsh
def test_a_pilot_filter_lowers_authenticated_users_to_read(tmp_path: Path) -> None:
    """Finding 2's scenario: authored for SYNTHETIC\\PilotGroup only."""
    run = _execute(tmp_path, _gpo())
    assert run.returncode == 0, run.output
    _assert_domain_bound(run)
    assert run.final is not None
    assert run.final[PILOT_SID]["Permission"] == "GpoApply"
    assert run.final[AU_SID]["Permission"] == "GpoRead"
    au_calls = [
        c for c in run.called("Set-GPPermission") if c["target"].endswith("Authenticated Users")
    ]
    assert [(c["target"], c["level"], c["type"], c["replace"]) for c in au_calls] == [
        ("NT AUTHORITY\\Authenticated Users", "GpoRead", "Group", True)
    ]
    assert run.final[DA_SID]["Permission"] == "GpoEditDeleteModifySecurity"
    assert run.final["S-1-5-18"]["Permission"] == "GpoEditDeleteModifySecurity"
    assert run.final["S-1-5-9"]["Permission"] == "GpoRead"
    applying = {sid for sid, p in run.final.items() if p["Permission"] == "GpoApply"}
    assert applying == {PILOT_SID}


@needs_pwsh
def test_extraneous_apply_is_lowered_before_any_grant(tmp_path: Path) -> None:
    run = _execute(tmp_path, _gpo())
    writes = run.called("Set-GPPermission")
    assert [(c["target"], c["level"]) for c in writes] == [
        ("NT AUTHORITY\\Authenticated Users", "GpoRead"),
        ("SYNTHETIC\\PilotGroup", "GpoApply"),
    ]


@needs_pwsh
def test_a_stale_apply_trustee_is_lowered_and_admins_are_not_exempt(tmp_path: Path) -> None:
    perms = [
        *_default_perms(),
        _perm(OLD_PILOT_SID, "SYNTHETIC", "OldPilot", "GpoApply"),
        _perm(EVERYONE_SID, "", "Everyone", "GpoApply", "WellKnownGroup"),
    ]
    perms[1] = _perm(DA_SID, "SYNTHETIC", "Domain Admins", "GpoApply")
    run = _execute(tmp_path, _gpo(), perms=perms)
    assert run.returncode == 0, run.output
    assert run.final is not None
    assert run.final[OLD_PILOT_SID]["Permission"] == "GpoRead"
    assert run.final[DA_SID]["Permission"] == "GpoRead"
    # A trustee with no domain is named without one, not '\Everyone' (Opus N5).
    assert run.final[EVERYONE_SID]["Permission"] == "GpoRead"
    assert "\\Everyone" not in {c["target"] for c in run.called("Set-GPPermission")}
    assert {s for s, p in run.final.items() if p["Permission"] == "GpoApply"} == {PILOT_SID}


@needs_pwsh
def test_authored_authenticated_users_keeps_apply(tmp_path: Path) -> None:
    gpo = _gpo(
        security_filters=(
            # The model's principal formats are DOMAIN\\name, UPN or SID, and
            # "NT AUTHORITY" holds a space, so Authenticated Users is its SID.
            SecurityFilter("au", AU_SID, sid=AU_SID),
            SecurityFilter("p", "SYNTHETIC\\PilotGroup"),
        )
    )
    run = _execute(tmp_path, gpo)
    assert run.returncode == 0, run.output
    assert run.final is not None
    assert run.final[AU_SID]["Permission"] == "GpoApply"
    assert run.final[PILOT_SID]["Permission"] == "GpoApply"


@needs_pwsh
def test_read_only_filters_leave_no_apply_at_all(tmp_path: Path) -> None:
    gpo = _gpo(security_filters=(SecurityFilter("r", "SYNTHETIC\\Readers", permission="read"),))
    run = _execute(tmp_path, gpo)
    assert run.returncode == 0, run.output
    assert run.final is not None
    assert run.final[READERS_SID]["Permission"] == "GpoRead"
    assert run.final[AU_SID]["Permission"] == "GpoRead"
    assert not any(p["Permission"] == "GpoApply" for p in run.final.values())


@needs_pwsh
def test_no_filters_authored_leaves_the_default(tmp_path: Path) -> None:
    run = _execute(tmp_path, _gpo(security_filters=()))
    assert run.returncode == 0, run.output
    assert run.called("Get-GPPermission") == [] and run.called("Set-GPPermission") == []
    assert run.final is not None
    assert run.final[AU_SID]["Permission"] == "GpoApply"


@needs_pwsh
def test_a_deny_entry_is_left_as_it_is(tmp_path: Path) -> None:
    perms = [
        *_default_perms(),
        _perm(OLD_PILOT_SID, "SYNTHETIC", "OldPilot", "GpoApply", denied=True),
    ]
    run = _execute(tmp_path, _gpo(), perms=perms)
    assert run.returncode == 0, run.output
    assert not any(c["target"] == "SYNTHETIC\\OldPilot" for c in run.called("Set-GPPermission"))
    assert run.final is not None
    assert run.final[OLD_PILOT_SID]["Denied"] is True


@needs_pwsh
def test_a_stored_sid_that_no_longer_matches_stops_the_plan(tmp_path: Path) -> None:
    """A group deleted and recreated under the same name is not the group the
    filter was authored for (Daybreak round 2)."""
    gpo = _gpo(
        security_filters=(SecurityFilter("f1", "SYNTHETIC\\PilotGroup", sid=READERS_SID),)
    )
    run = _execute(tmp_path, gpo)
    assert run.returncode != 0
    assert f"not the stored {READERS_SID}, so nothing was changed" in run.error
    assert run.mutations == [] and run.called("Get-GPO") == []


@needs_pwsh
def test_an_unresolvable_principal_stops_the_plan_before_any_change(tmp_path: Path) -> None:
    """Round 2: A was granted before B failed to resolve, leaving both the old
    and the new Apply holders on a linked GPO."""
    gpo = _gpo(
        security_filters=(
            SecurityFilter("a", "SYNTHETIC\\PilotGroup"),
            SecurityFilter("b", "SYNTHETIC\\Missing"),
        )
    )
    marked = _existing(OTHER_ID, gpo.name, _marker(gpo))
    perms = [*_default_perms(), _perm(OLD_PILOT_SID, "SYNTHETIC", "OldPilot", "GpoApply")]
    run = _execute(tmp_path, gpo, gpos=[marked], perms=perms)
    assert run.returncode != 0
    assert "Cannot resolve SYNTHETIC\\Missing, so nothing was changed" in run.error
    assert run.mutations == [] and run.called("Get-GPO") == []


@pytest.mark.parametrize(
    "entry",
    [
        _perm(
            AU_SID,
            "NT AUTHORITY",
            "Authenticated Users",
            "GpoApply",
            "WellKnownGroup",
            inherited=True,
        ),
        _perm(OLD_PILOT_SID, "SYNTHETIC", "OldPilot", "GpoCustom"),
    ],
    ids=["inherited-apply", "custom"],
)
@needs_pwsh
def test_what_the_plan_cannot_reduce_stops_it_before_any_change(
    tmp_path: Path, entry: dict[str, Any]
) -> None:
    gpo = _gpo()
    marked = _existing(OTHER_ID, gpo.name, _marker(gpo))
    perms = [p for p in _default_perms() if p["Sid"] != entry["Sid"]] + [entry]
    run = _execute(tmp_path, gpo, gpos=[marked], perms=perms)
    assert run.returncode != 0
    assert "The plan cannot reduce" in run.error
    assert run.mutations == []


@pytest.mark.parametrize(
    ("permission", "denied"),
    [("GpoEditDeleteModifySecurity", False), ("GpoEdit", False), ("GpoApply", True)],
    ids=["edit-delete-modify", "edit", "deny"],
)
@needs_pwsh
def test_an_authored_principal_with_edit_rights_or_a_deny_stops_the_plan(
    tmp_path: Path, permission: str, denied: bool
) -> None:
    """``-Replace`` would silently turn an editor group into a reader (Opus N1)."""
    gpo = _gpo(security_filters=(SecurityFilter("e", "SYNTHETIC\\Editors"),))
    marked = _existing(OTHER_ID, gpo.name, _marker(gpo))
    editors = _perm(EDITORS_SID, "SYNTHETIC", "Editors", permission, denied=denied)
    perms = [*_default_perms(), editors]
    run = _execute(tmp_path, gpo, gpos=[marked], perms=perms)
    assert run.returncode != 0
    assert "is named by a security filter but holds" in run.error
    assert run.mutations == []


@needs_pwsh
def test_the_read_back_stops_the_plan_when_a_downgrade_did_not_land(tmp_path: Path) -> None:
    run = _execute(tmp_path, _gpo(), ignore_sid=AU_SID)
    assert run.returncode != 0
    assert "still holds GpoApply. Stopping before links are written." in run.error
    assert run.called("New-GPLink") == [] and run.called("Set-GPLink") == []


@needs_pwsh
def test_the_read_back_stops_the_plan_when_a_grant_did_not_land(tmp_path: Path) -> None:
    """Round 2 (Opus N2): the read-back also checks the authored side."""
    run = _execute(tmp_path, _gpo(), ignore_sid=PILOT_SID)
    assert run.returncode != 0
    assert "does not hold exactly GpoApply. Stopping before links are written." in run.error
    assert run.called("New-GPLink") == []


# --- -WhatIf ------------------------------------------------------------------


@needs_pwsh
def test_whatif_changes_nothing_on_an_existing_gpo(tmp_path: Path) -> None:
    """``$gpo.GpoStatus = ...`` is a .NET property set, which ``-WhatIf`` does
    not reach: it went through regardless (Daybreak round 2)."""
    gpo = _gpo(computer_enabled=False)
    marked = _existing(OTHER_ID, gpo.name, _marker(gpo))
    run = _execute(tmp_path, gpo, gpos=[marked], whatif=True)
    assert run.returncode == 0, run.output
    assert run.mutations == []
    assert run.status == "Unchanged"
    assert "What if: the security-filtering read-back runs only" in run.output
    assert run.final is not None
    assert run.final[AU_SID]["Permission"] == "GpoApply"


@needs_pwsh
def test_whatif_on_a_new_gpo_stops_after_reporting_the_creation(tmp_path: Path) -> None:
    run = _execute(tmp_path, _gpo(), whatif=True)
    assert run.returncode == 0, run.output
    reads = {"Get-GPO", "Get-GPInheritance"}
    assert [c["cmd"] for c in run.calls if c["cmd"] not in reads] == ["New-GPO"]
    assert run.mutations == []
    assert "What if: the GPO does not exist yet" in run.output


# --- WMI filter ---------------------------------------------------------------


def test_a_wmi_filtered_gpo_gets_no_plan() -> None:
    """Round 3 (Daybreak): the plan checked an assigned filter by display name
    only, so a different filter with the same name -- deleted and recreated, or
    broader -- let it link the GPO. The GroupPolicy module exposes a filter's
    name and path and an unmeasured query list, so the plan can verify neither
    the filter nor assign one: it is not generated."""
    wmi = WmiFilter("w", "Servers only", query="SELECT * FROM Win32_OperatingSystem")
    gpo = _gpo(wmi_filter=wmi)
    refusal = export.plan_refusal(gpo)
    assert refusal is not None and refusal.code == "wmi_filter_not_verifiable"
    with pytest.raises(ValidationError) as caught:
        export.powershell_plan(gpo)
    assert caught.value.issues[0].code == "wmi_filter_not_verifiable"
    assert export.plan_refusal(_gpo(links=())) is None


# --- round 3 ------------------------------------------------------------------


@needs_pwsh
def test_a_missing_link_target_stops_the_plan_before_any_change(tmp_path: Path) -> None:
    """Round 3 (Daybreak): the first directory lookup of a target was the link
    step, after filtering, settings and status had changed."""
    gpo = _gpo(links=(GPOLink("l1", f"OU=Missing,{DOMAIN_DN}"),))
    marked = _existing(OTHER_ID, gpo.name, _marker(gpo))
    run = _execute(tmp_path, gpo, gpos=[marked], missing_targets=[f"OU=Missing,{DOMAIN_DN}"])
    assert run.returncode != 0
    assert "cannot be read in lab.synthetic.test, so nothing was changed" in run.error
    assert run.mutations == []
    assert run.called("Get-GPO") == []


@needs_pwsh
def test_a_link_target_of_the_wrong_type_stops_the_plan(tmp_path: Path) -> None:
    gpo = _gpo()
    target = f"OU=Pilot,{DOMAIN_DN}"
    run = _execute(tmp_path, gpo, wrong_type=[target])
    assert run.returncode != 0
    assert "not the OU named, so nothing was changed" in run.error
    assert run.mutations == []


@needs_pwsh
def test_each_target_is_read_once_before_anything_changes(tmp_path: Path) -> None:
    gpo = _gpo(links=(GPOLink("l1", f"OU=Pilot,{DOMAIN_DN}"), GPOLink("l2", DOMAIN_DN, order=2)))
    run = _execute(tmp_path, gpo)
    assert run.returncode == 0, run.output
    names = [c["cmd"] for c in run.calls]
    reads = [i for i, cmd in enumerate(names) if cmd == "Get-GPInheritance"]
    first_change = next(i for i, cmd in enumerate(names) if cmd.split("-")[0] in {"New", "Set"})
    assert len(reads) == 2 and max(reads) < first_change
    assert len(run.called("New-GPLink")) == 2


@needs_pwsh
def test_a_harvested_marker_in_a_crafted_estate_import_does_not_bind(tmp_path: Path) -> None:
    """Round 3 (Opus S1): GPO comments are readable by any authenticated user,
    so a crafted estate can carry another Studio GPO's marker GUID as its own
    workspace GUID. Such a GUID came from imported data, so its marker does not
    bind without the switch -- even when the names agree."""
    victim = _gpo(name="Payroll Lockdown")
    crafted = _gpo(name="Payroll Lockdown", source_guid=victim.guid)
    existing = _existing(OTHER_ID, "Payroll Lockdown", _marker(victim))
    plan = export.powershell_plan(crafted)
    assert "$trustMarker = $false" in plan
    run = _execute(tmp_path, crafted, gpos=[existing])
    assert run.returncode != 0
    assert "came from imported data" in run.error
    assert run.mutations == []
    renamed = replace(crafted, name="Harmless Pilot")
    run = _execute(tmp_path, renamed, gpos=[existing])
    assert run.returncode != 0
    assert run.mutations == []


@needs_pwsh
def test_an_orphaned_apply_trustee_stops_the_plan_before_any_change(tmp_path: Path) -> None:
    """Round 3 (Opus S2): an unresolvable SID holding Apply stopped the plan
    only when it was reached -- after Authenticated Users had been lowered, an
    outage on a linked GPO that every re-run repeated."""
    gpo = _gpo()
    marked = _existing(OTHER_ID, gpo.name, _marker(gpo))
    orphan = "S-1-5-21-1000-2000-3000-9999"
    perms = [*_default_perms(), _perm(orphan, "", orphan, "GpoApply", "Unknown")]
    run = _execute(tmp_path, gpo, gpos=[marked], perms=perms)
    assert run.returncode != 0
    assert f"lower GpoApply held by {orphan}" in run.error
    assert run.mutations == []


@pytest.mark.parametrize("hidden", ["\u200b", "\u00ad"], ids=["zero-width-space", "soft-hyphen"])
@needs_pwsh
def test_the_marker_is_compared_ordinally(tmp_path: Path, hidden: str) -> None:
    """Round 3 (Opus): ``-ccontains`` compares with culture rules, under which a
    zero-width space or a soft hyphen inside the line still matches."""
    gpo = _gpo()
    marker = _marker(gpo)
    near = marker[:10] + hidden + marker[10:]
    run = _execute(tmp_path, gpo, gpos=[_existing(OTHER_ID, gpo.name, near)])
    assert run.returncode != 0
    assert run.mutations == []
