"""The PowerShell plan is bound to its declared domain and to one GPO, and its
security filtering does not fail open (Daybreak pre-1.1.1 findings 1 and 2).

Finding 1: the plan called every GroupPolicy cmdlet without ``-Domain``, so it
modified whichever domain the session was in, and when the workspace GUID did
not resolve it silently adopted any GPO with the same display name.

Finding 2: ``Authenticated Users`` sat in a ``$protected`` list, so the default
``GpoApply`` every new GPO carries was never reduced, and a filter authored for
a pilot group still applied to every authenticated principal.

Two layers of test. The text tests read the generated plan. The execution
tests run it under ``pwsh`` against a STATEFUL stand-in for the GroupPolicy
module (a GPO table, a permission table keyed by SID, and a stub
``Microsoft.GroupPolicy.GPDomain``), so what is asserted is what the plan does,
not what it says: which cmdlets ran, against which domain, and the permission
table it left behind. Nothing here touches a directory. The stand-in's
behaviour is the measured one where the repository has a measurement (the
default DACL of a new GPO, ``docs/plan-033/wp0-evidence/*/wp0/permissions.txt``;
``-Replace`` lowering Authenticated Users to GpoRead, the lifecycle lane's
``Set-SecurityFilter``); everything else about real Windows is the live probe's
job, not this file's.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from gpo_studio import egress, export
from gpo_studio.model import GPO, GPOLink, RegistrySetting, SecurityFilter

PWSH = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(PWSH is None, reason="pwsh is not installed on this host")

DOMAIN = "lab.synthetic.test"
GUID = "{0B0D0080-0000-4000-8000-0000000000B1}"
#: The Default Domain Policy's well-known GUID: no secret, so an import naming
#: it as its source must not be able to select it.
WELL_KNOWN_GUID = "{31B2F340-016D-11D2-945F-00C04FB984F9}"
AU_SID = "S-1-5-11"
PILOT_SID = "S-1-5-21-1000-2000-3000-1101"
READERS_SID = "S-1-5-21-1000-2000-3000-1102"
OLD_PILOT_SID = "S-1-5-21-1000-2000-3000-1103"
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
        links=(GPOLink("l1", "OU=Pilot,DC=lab,DC=synthetic,DC=test"),),
        security_filters=(SecurityFilter("f1", "SYNTHETIC\\PilotGroup"),),
    )
    return replace(base, **changes)


def _marker(gpo: GPO) -> str:
    """The binding marker as the plan states it (``$marker = '...'``)."""
    plan = export.powershell_plan(gpo)
    found = re.search(r"^\$marker = '([^']*)'$", plan, re.MULTILINE)
    assert found is not None, "the plan states no binding marker"
    return found.group(1)


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
        for cmd in GP_CMDLETS:
            for _ in re.finditer(rf"(?<![\w-]){cmd}(?![\w-])", line):
                assert "-Domain $domain" in line, line


def test_the_domain_is_asserted_before_any_grouppolicy_call() -> None:
    code = _code_lines(export.powershell_plan(_gpo()))
    resolve = next(i for i, line in enumerate(code) if "Microsoft.GroupPolicy.GPDomain" in line)
    compare = next(i for i, line in enumerate(code) if "$resolved.DomainName -ne $domain" in line)
    first_call = next(
        i for i, line in enumerate(code) if any(cmd in line for cmd in GP_CMDLETS)
    )
    assert resolve < compare < first_call
    assert "throw" in code[resolve + 1] and "throw" in code[compare + 1]


def test_a_same_named_gpo_needs_the_marker_or_the_switch() -> None:
    gpo = _gpo()
    plan = export.powershell_plan(gpo)
    marker = _marker(gpo)
    assert marker == f"GPO Studio binding: {GUID}"
    assert "param([switch]$AdoptExistingGpo)" in plan
    assert (
        "if ($gpo -and -not $AdoptExistingGpo -and"
        " -not ([string]$gpo.Description).Contains($marker)) {"
    ) in plan
    # The plan writes the marker into the comment of a GPO it creates.
    assert (
        "-Comment ('Pilot rollout' + [char]0x000A + [char]0x000A"
        f" + 'GPO Studio binding: {GUID}')"
    ) in plan
    # The header tells the reviewer the rule, the switch, and what the marker is not.
    header = plan.split("#Requires")[0]
    assert "-AdoptExistingGpo" in header
    assert "not authentication" in header
    assert DOMAIN in header


def test_source_guid_never_selects_a_gpo() -> None:
    plan = export.powershell_plan(_gpo(source_guid=WELL_KNOWN_GUID))
    assert WELL_KNOWN_GUID.casefold() not in plan.casefold()


def test_authenticated_users_is_not_protected_from_the_filter() -> None:
    plan = export.powershell_plan(_gpo())
    assert "$protected" not in plan
    # Every non-deny GpoApply outside the authored apply set is lowered to
    # GpoRead, with -Replace (a lower level without it changes nothing).
    assert (
        "Set-GPPermission -Guid $gpo.Id -PermissionLevel GpoRead"
        " -TargetName ($perm.Trustee.Domain + '\\' + $perm.Trustee.Name)"
        " -TargetType $type -Replace -Domain $domain | Out-Null"
    ) in plan
    assert "-PermissionLevel None" not in plan


def test_filtering_and_status_come_before_links() -> None:
    code = _code_lines(export.powershell_plan(_gpo()))
    readback = max(i for i, line in enumerate(code) if "still holds GpoApply" in line)
    status = next(i for i, line in enumerate(code) if line.startswith("$gpo.GpoStatus"))
    first_link = next(i for i, line in enumerate(code) if "Get-GPInheritance" in line)
    assert readback < first_link and status < first_link


def test_no_filters_authored_leaves_permissions_alone() -> None:
    plan = export.powershell_plan(_gpo(security_filters=()))
    assert "GPPermission" not in plan
    assert "none authored" in plan


def test_read_only_filters_say_the_gpo_applies_to_no_one() -> None:
    plan = export.powershell_plan(
        _gpo(security_filters=(SecurityFilter("r", "SYNTHETIC\\Readers", permission="read"),))
    )
    assert "applies to no one" in plan
    assert "$applySids += " not in plan


def test_the_bound_plan_passes_egress() -> None:
    for gpo in (
        _gpo(),
        _gpo(security_filters=()),
        _gpo(description=""),
        _gpo(security_filters=(SecurityFilter("r", "SYNTHETIC\\Readers", permission="read"),)),
    ):
        assert egress.powershell_plan_problems(export.powershell_plan(gpo)) == []


# ---------------------------------------------------------------------------
# The plan, executed against a stateful stand-in
# ---------------------------------------------------------------------------

#: The stand-in. ``$S`` is the scenario: ``Resolves`` (what the declared domain
#: resolves to; empty = unreachable), ``Gpos`` (existing GPOs), ``Perms`` (the
#: permission table of whichever GPO the plan binds, keyed by SID), ``Names``
#: (account name -> SID, as the domain would resolve it) and ``IgnoreDowngrade``
#: (a SID whose Set-GPPermission the stand-in drops, to prove the read-back).
#: Every stand-in accepts ``-ErrorAction`` and makes ``-Domain`` optional, so
#: the unbound plan runs under it too. Every call is logged as
#: ``CALL=<base64 json>``, and the message of the error that stops the plan as
#: ``ERROR=<base64>``.
_STANDIN = r"""
trap {
  [Console]::Out.WriteLine('ERROR=' + [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes([string]$_.Exception.Message)))
  exit 1
}
Add-Type -TypeDefinition @'
namespace Microsoft.GroupPolicy {
  public class GPDomain {
    public string DomainName { get; private set; }
    public GPDomain(string name) {
      string to = System.Environment.GetEnvironmentVariable("STUB_RESOLVES_TO");
      if (string.IsNullOrEmpty(to)) {
        throw new System.Exception(
          "The specified domain either does not exist or could not be contacted.");
      }
      DomainName = to;
    }
  }
}
'@
$S = ConvertFrom-Json ([Text.Encoding]::UTF8.GetString(
  [Convert]::FromBase64String($env:STUB_SCENARIO)))
$script:perms = [System.Collections.ArrayList]@()
foreach ($p in $S.Perms) { [void]$script:perms.Add($p) }
function _Log($cmd, $fields) {
  $fields['cmd'] = $cmd
  [Console]::Out.WriteLine('CALL=' + [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json $fields -Compress))))
}
function _Gpo($g) { [pscustomobject]@{ Id = $g.Id; DisplayName = $g.DisplayName;
  Description = $g.Description; GpoStatus = '' } }
function _Perm($p) { [pscustomobject]@{ Permission = $p.Permission; Denied = [bool]$p.Denied;
  Inherited = [bool]$p.Inherited; Trustee = [pscustomobject]@{ Domain = $p.Domain;
  Name = $p.Name; SidType = $p.SidType; Sid = [pscustomobject]@{ Value = $p.Sid } } } }
function _Sid($name) { $S.Names.$name }
function Get-GPO { param($Guid, $Name, $Domain, $ErrorAction)
  _Log 'Get-GPO' @{ domain = $Domain; guid = $Guid; name = $Name }
  foreach ($g in $S.Gpos) {
    if (($Guid -and $g.Id -eq $Guid) -or ($Name -and $g.DisplayName -eq $Name)) { return _Gpo $g }
  }
  $null }
function New-GPO { param($Name, $Comment, $Domain, $ErrorAction)
  _Log 'New-GPO' @{ domain = $Domain; name = $Name; comment = $Comment }
  [pscustomobject]@{ Id = 'created-id'; DisplayName = $Name; Description = $Comment;
    GpoStatus = '' } }
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
  [pscustomobject]@{ GpoLinks = @() } }
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
  $sid = _Sid $TargetName
  if (-not $sid) { throw ('stand-in: cannot resolve ' + $TargetName) }
  if ($sid -eq $S.IgnoreDowngrade) { return }
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
    "SYNTHETIC\\Domain Admins": DA_SID,
    "NT AUTHORITY\\Authenticated Users": AU_SID,
    AU_SID: AU_SID,
}


def _runnable(plan: str) -> str:
    lines = [line for line in plan.splitlines() if not line.startswith("#Requires")]
    # After the param block, which must stay the first statement. ``param()``
    # is the unbound plan's, so the same harness runs it (red-before-green).
    index = next(i for i, line in enumerate(lines) if line.startswith("param("))
    return "\n".join([*lines[: index + 1], _STANDIN, *lines[index + 1 :], _FOOTER]) + "\n"


class Run:
    def __init__(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.returncode = completed.returncode
        self.output = completed.stdout + completed.stderr
        #: The message of the terminating error that stopped the plan, if any.
        self.error = "".join(
            base64.b64decode(line.split("=", 1)[1]).decode("utf-8")
            for line in completed.stdout.splitlines()
            if line.startswith("ERROR=")
        )
        self.calls: list[dict[str, Any]] = [
            json.loads(base64.b64decode(line.split("=", 1)[1]))
            for line in completed.stdout.splitlines()
            if line.startswith("CALL=")
        ]
        final = [line for line in completed.stdout.splitlines() if line.startswith("FINAL=")]
        self.final: dict[str, dict[str, Any]] | None = (
            {p["Sid"]: p for p in json.loads(base64.b64decode(final[0].split("=", 1)[1]))}
            if final
            else None
        )

    def called(self, cmd: str) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["cmd"] == cmd]

    @property
    def mutations(self) -> list[dict[str, Any]]:
        return [
            call
            for call in self.calls
            if call["cmd"].split("-")[0] in {"New", "Set", "Rename", "Remove"}
        ]


def _execute(
    tmp_path: Path,
    gpo: GPO,
    *,
    resolves: str = DOMAIN,
    gpos: list[dict[str, str]] | None = None,
    perms: list[dict[str, Any]] | None = None,
    adopt: bool = False,
    ignore_downgrade: str = "",
) -> Run:
    scenario = {
        "Gpos": gpos or [],
        "Perms": _default_perms() if perms is None else perms,
        "Names": _NAMES,
        "IgnoreDowngrade": ignore_downgrade,
    }
    path = tmp_path / "apply.ps1"
    path.write_text(_runnable(export.powershell_plan(gpo)), encoding="utf-8-sig")
    assert PWSH is not None
    args = [PWSH, "-NoProfile", "-NonInteractive", "-File", str(path)]
    if adopt:
        args.append("-AdoptExistingGpo")
    completed = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={
            **os.environ,
            "STUB_RESOLVES_TO": resolves,
            "STUB_SCENARIO": base64.b64encode(json.dumps(scenario).encode()).decode(),
        },
    )
    return Run(completed)


def _assert_domain_bound(run: Run) -> None:
    assert run.calls, run.output
    for call in run.calls:
        assert call["domain"] == DOMAIN, call


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
def test_a_new_gpo_is_created_in_the_declared_domain_with_the_marker(tmp_path: Path) -> None:
    gpo = _gpo()
    run = _execute(tmp_path, gpo)
    assert run.returncode == 0, run.output
    _assert_domain_bound(run)
    (created,) = run.called("New-GPO")
    assert created["comment"].endswith(_marker(gpo))
    assert run.called("New-GPLink")


@needs_pwsh
def test_an_unrelated_same_named_gpo_is_refused(tmp_path: Path) -> None:
    existing = {
        "Id": "{11111111-2222-4333-8444-555555555555}",
        "DisplayName": "Pilot Browser Policy",
        "Description": "Somebody else's policy",
    }
    run = _execute(tmp_path, _gpo(), gpos=[existing])
    assert run.returncode != 0
    assert "nothing ties it to this plan, so nothing was changed" in run.error
    assert "-AdoptExistingGpo" in run.error
    assert run.mutations == [], run.mutations


@needs_pwsh
def test_the_switch_adopts_a_same_named_gpo(tmp_path: Path) -> None:
    existing = {
        "Id": "{11111111-2222-4333-8444-555555555555}",
        "DisplayName": "Pilot Browser Policy",
        "Description": "",
    }
    run = _execute(tmp_path, _gpo(), gpos=[existing], adopt=True)
    assert run.returncode == 0, run.output
    _assert_domain_bound(run)
    assert run.called("New-GPO") == []
    assert {call["guid"] for call in run.called("Set-GPRegistryValue")} == {existing["Id"]}


@needs_pwsh
def test_a_rerun_finds_the_gpo_it_created_by_its_marker(tmp_path: Path) -> None:
    gpo = _gpo()
    existing = {
        "Id": "{11111111-2222-4333-8444-555555555555}",
        "DisplayName": gpo.name,
        "Description": f"Pilot rollout\n\n{_marker(gpo)}",
    }
    run = _execute(tmp_path, gpo, gpos=[existing])
    assert run.returncode == 0, run.output
    assert run.called("New-GPO") == []
    assert {call["guid"] for call in run.called("Set-GPRegistryValue")} == {existing["Id"]}


@needs_pwsh
def test_another_workspace_gpos_marker_does_not_bind(tmp_path: Path) -> None:
    gpo = _gpo()
    other = replace(gpo, guid="{0B0D0080-0000-4000-8000-0000000000B2}")
    existing = {
        "Id": "{11111111-2222-4333-8444-555555555555}",
        "DisplayName": gpo.name,
        "Description": _marker(other),
    }
    run = _execute(tmp_path, gpo, gpos=[existing])
    assert run.returncode != 0
    assert run.mutations == []


@needs_pwsh
def test_the_workspace_guid_binds_and_renames(tmp_path: Path) -> None:
    existing = {"Id": GUID, "DisplayName": "Old name", "Description": ""}
    run = _execute(tmp_path, _gpo(), gpos=[existing])
    assert run.returncode == 0, run.output
    _assert_domain_bound(run)
    (renamed,) = run.called("Rename-GPO")
    assert renamed["name"] == "Pilot Browser Policy"


@needs_pwsh
def test_a_source_guid_naming_an_existing_gpo_selects_nothing(tmp_path: Path) -> None:
    existing = {
        "Id": WELL_KNOWN_GUID,
        "DisplayName": "Pilot Browser Policy",
        "Description": "",
    }
    run = _execute(tmp_path, _gpo(source_guid=WELL_KNOWN_GUID), gpos=[existing])
    assert run.returncode != 0
    assert run.mutations == []


@needs_pwsh
def test_a_pilot_filter_lowers_authenticated_users_to_read(tmp_path: Path) -> None:
    """Finding 2's scenario: authored for SYNTHETIC\\PilotGroup only."""
    run = _execute(tmp_path, _gpo())
    assert run.returncode == 0, run.output
    _assert_domain_bound(run)
    assert run.final is not None
    assert run.final[PILOT_SID]["Permission"] == "GpoApply"
    # Read kept (MS16-072), Apply gone.
    assert run.final[AU_SID]["Permission"] == "GpoRead"
    au_calls = [
        c for c in run.called("Set-GPPermission") if c["target"].endswith("Authenticated Users")
    ]
    assert au_calls == [
        {
            "cmd": "Set-GPPermission",
            "domain": DOMAIN,
            "guid": "created-id",
            "target": "NT AUTHORITY\\Authenticated Users",
            "level": "GpoRead",
            "type": "Group",
            "replace": True,
        }
    ]
    # Default edit rights are not apply rights and are untouched.
    assert run.final[DA_SID]["Permission"] == "GpoEditDeleteModifySecurity"
    assert run.final["S-1-5-18"]["Permission"] == "GpoEditDeleteModifySecurity"
    assert run.final["S-1-5-9"]["Permission"] == "GpoRead"
    applying = {sid for sid, p in run.final.items() if p["Permission"] == "GpoApply"}
    assert applying == {PILOT_SID}


@needs_pwsh
def test_a_stale_apply_trustee_is_lowered_and_admins_are_not_exempt(tmp_path: Path) -> None:
    """The old ``$protected`` list exempted the admin groups too: an admin
    group holding GpoApply outside the filter kept it. And the old removal
    passed ``-PermissionLevel None`` without ``-Replace``, which lowers
    nothing."""
    perms = [
        *_default_perms(),
        _perm(OLD_PILOT_SID, "SYNTHETIC", "OldPilot", "GpoApply"),
    ]
    perms[1] = _perm(DA_SID, "SYNTHETIC", "Domain Admins", "GpoApply")
    run = _execute(tmp_path, _gpo(), perms=perms)
    assert run.returncode == 0, run.output
    assert run.final is not None
    assert run.final[OLD_PILOT_SID]["Permission"] == "GpoRead"
    assert run.final[DA_SID]["Permission"] == "GpoRead"
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
def test_what_the_plan_cannot_reduce_stops_it_before_links(
    tmp_path: Path, entry: dict[str, Any]
) -> None:
    perms = [p for p in _default_perms() if p["Sid"] != entry["Sid"]] + [entry]
    run = _execute(tmp_path, _gpo(), perms=perms)
    assert run.returncode != 0
    assert "The plan cannot reduce" in run.error
    assert run.called("New-GPLink") == [] and run.called("Set-GPRegistryValue") == []


@needs_pwsh
def test_the_read_back_stops_the_plan_when_a_downgrade_did_not_land(tmp_path: Path) -> None:
    run = _execute(tmp_path, _gpo(), ignore_downgrade=AU_SID)
    assert run.returncode != 0
    assert "still holds GpoApply. Stopping before links are written." in run.error
    assert run.called("New-GPLink") == [] and run.called("Set-GPLink") == []
