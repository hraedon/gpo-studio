# ruff: noqa: E501 - the PowerShell stand-ins are kept one function per line
"""Execute the lifecycle guest script against mocked AD / GroupPolicy cmdlets.

Review findings 1 and 5 (2026-10-08) were found by running ``run-lifecycle.ps1``
under PowerShell 7 with the directory replaced by functions: a name collision
made cleanup delete a GPO the run did not create, and a create that committed
and then threw left an object nobody looked for. These probes keep both
scenarios executable. They need ``pwsh`` and are skipped without it.

They prove the script's *control flow* -- what it creates, records, deletes and
re-queries -- not Windows behaviour: every cmdlet here is a stand-in.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[1]
_GUEST = _ROOT / "scripts/windows-oracle/run-lifecycle.ps1"

pytestmark = pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is not installed")

#: A tiny in-memory directory. Objects are keyed by DN (AD) or display name
#: (GPOs); every mutating stand-in records what it did.
_PRELUDE = r"""
$ErrorActionPreference = 'Stop'
if (-not ('Microsoft.ActiveDirectory.Management.ADIdentityNotFoundException' -as [type])) {
    Add-Type 'namespace Microsoft.ActiveDirectory.Management { public class ADIdentityNotFoundException : System.Exception { public ADIdentityNotFoundException(string s) : base(s) {} } }'
}
$global:ad = @{}
$global:gpos = @{}
$global:deleted = @()
$global:failRemoveWmi = $false
$global:commitThenThrow = ''
function Get-Date { param([string]$Format) if ($Format) { return '20261008000000' }; return [datetime]'2026-10-08' }
function Get-Random { param($Minimum, $Maximum) return 4321 }
function Get-CimInstance { param($ClassName)
    if ($ClassName -eq 'Win32_OperatingSystem') { return [pscustomobject]@{ Caption = 'Synthetic OS'; BuildNumber = '26100' } }
    return [pscustomobject]@{ Domain = 'synthetic.test'; DomainRole = 3; Name = 'MEMBER' } }
function Get-Module { param([switch]$ListAvailable, $Name) return [pscustomobject]@{ Version = '1.0.0.0' } }
function Import-Module { param($Name, $ErrorAction) }
function Get-ADDomain { param($Server) return [pscustomobject]@{ PDCEmulator = 'dc.synthetic.test'; DistinguishedName = 'DC=synthetic,DC=test' } }
function Get-ADObject { param($Identity, $Server, $ErrorAction, $Properties, $LDAPFilter, $SearchBase)
    if ($LDAPFilter) { return @() }
    if ($Identity -eq 'DC=synthetic,DC=test') { return [pscustomobject]@{ gPLink = '' } }
    if ($global:ad.ContainsKey($Identity)) {
        $v = $global:ad[$Identity]; $m = if ($v -is [hashtable]) { $v.marker } else { '' }
        return [pscustomobject]@{ DistinguishedName = $Identity; description = $m; 'msWMI-Parm1' = $m } }
    throw [Microsoft.ActiveDirectory.Management.ADIdentityNotFoundException]::new('not found') }
function New-ADOrganizationalUnit { param($Name, $Path, $Server, $ProtectedFromAccidentalDeletion, $Description, $ErrorAction) $global:ad["OU=$Name,$Path"] = @{ kind = 'ou'; marker = $Description } }
function New-ADGroup { param($Name, $SamAccountName, $GroupScope, $GroupCategory, $Path, $Description, $Server, $ErrorAction) $global:ad["CN=$Name,$Path"] = @{ kind = 'group'; marker = $Description } }
function Get-ADGroup { param($Identity, $Server, $ErrorAction) return [pscustomobject]@{ SID = [pscustomobject]@{ Value = 'S-1-5-21-1-2-3-1101' } } }
function New-ADObject { param($Name, $Type, $Path, $Server, $OtherAttributes, $ErrorAction)
    $global:ad["CN=$Name,$Path"] = @{ kind = 'wmi'; marker = $OtherAttributes['msWMI-Parm1'] }
    if ($global:commitThenThrow -eq 'wmi') { throw 'synthetic: response lost after the server committed the WMI filter' } }
function Remove-ADObject { param($Identity, $Server, $Confirm, $ErrorAction)
    if ($global:failRemoveWmi) { return }
    $global:ad.Remove($Identity); $global:deleted += $Identity }
function Remove-ADGroup { param($Identity, $Server, $Confirm, $ErrorAction) $global:ad.Remove($Identity); $global:deleted += $Identity }
function Remove-ADOrganizationalUnit { param($Identity, $Server, $Confirm, $Recursive, $ErrorAction) $global:ad.Remove($Identity); $global:deleted += $Identity }
function Get-GPInheritance { param($Target, $Domain, $Server, $ErrorAction) return [pscustomobject]@{ GpoLinks = @() } }
function Get-GPO { param([switch]$All, $Name, $Guid, $Domain, $Server, $ErrorAction)
    if ($Name) { if ($global:gpos.ContainsKey($Name)) { return $global:gpos[$Name] }; throw "GpoNotFound: $Name was not found" }
    foreach ($g in $global:gpos.Values) { if ("$($g.Id)" -eq "$Guid") { return $g } }
    throw "GpoNotFound: $Guid was not found" }
function New-GPO { param($Name, $Comment, $Domain, $Server, $ErrorAction)
    $g = [pscustomobject]@{ Id = [guid]::NewGuid(); DisplayName = $Name }
    $global:gpos[$Name] = $g
    if ($global:commitThenThrow -eq 'gpo') { throw 'synthetic: response lost after the server committed the GPO' }
    return $g }
function Remove-GPO { param($Guid, $Domain, $Server, $Confirm, $ErrorAction)
    foreach ($k in @($global:gpos.Keys)) { if ("$($global:gpos[$k].Id)" -eq "$Guid") { $global:gpos.Remove($k) } }
    $global:deleted += "$Guid" }
function Report($out) {
    $run = @(Get-ChildItem -LiteralPath $out -Directory)[0].FullName
    $r = Get-Content (Join-Path $run 'result.json') -Raw | ConvertFrom-Json
    [ordered]@{
        deleted               = @($global:deleted)
        ad_left               = @($global:ad.Keys)
        gpos_left             = @($global:gpos.Keys)
        ownership_established = $r.ownership_established
        created               = $r.created
        cleanup               = $r.cleanup
        cleanup_succeeded     = $r.cleanup_succeeded
        error                 = $r.error
    } | ConvertTo-Json -Depth 8 -Compress
}
"""


def _run_probe(tmp_path: Path, setup: str) -> dict[str, object]:
    out = tmp_path / "out"
    out.mkdir()
    script = tmp_path / "probe.ps1"
    script.write_text(
        _PRELUDE
        + setup
        + f"\ntry {{ & '{_GUEST}' -OutputDir '{out}' -Domain 'synthetic.test' }} catch {{ }}\n"
        + f"Report '{out}'\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return dict(json.loads(completed.stdout.strip().splitlines()[-1]))


def test_guest_probe_a_name_collision_deletes_nothing(tmp_path: Path) -> None:
    """Finding 1: a GPO already holding one of the run's names is never touched."""
    report = _run_probe(
        tmp_path,
        "$global:gpos['zz-studio-lifecycle-20261008000000-4321-copy'] = "
        "[pscustomobject]@{ Id = [guid]'aaaaaaaa-0000-0000-0000-000000000099'; "
        "DisplayName = 'zz-studio-lifecycle-20261008000000-4321-copy' }\n",
    )
    assert report["deleted"] == []
    assert report["gpos_left"] == ["zz-studio-lifecycle-20261008000000-4321-copy"]
    assert report["ad_left"] == []
    assert report["ownership_established"] is False
    assert "ownership guard" in str(report["error"])
    created = report["created"]
    assert isinstance(created, dict)
    assert all(created[k] in ([], None) for k in ("ous", "groups", "wmi_filters", "gpos"))


def test_guest_probe_a_committed_wmi_filter_whose_create_threw_is_cleaned(
    tmp_path: Path,
) -> None:
    """Finding 5: the filter was registered before New-ADObject, so it is found."""
    report = _run_probe(tmp_path, "$global:commitThenThrow = 'wmi'\n")
    created = report["created"]
    assert isinstance(created, dict)
    assert len(created["wmi_filters"]) == 1
    assert report["ad_left"] == []
    assert report["cleanup_succeeded"] is True


def test_guest_probe_a_committed_wmi_filter_that_survives_is_reported(tmp_path: Path) -> None:
    report = _run_probe(
        tmp_path, "$global:commitThenThrow = 'wmi'\n$global:failRemoveWmi = $true\n"
    )
    cleanup = report["cleanup"]
    assert isinstance(cleanup, dict)
    assert len(cleanup["residual"]["surviving_wmi_filters"]) == 1
    assert report["cleanup_succeeded"] is False


def test_guest_probe_a_gpo_whose_create_threw_is_reported_not_deleted(tmp_path: Path) -> None:
    """Re-review P1: a GPO under an intended name whose create THREW is not provably ours.

    It may have been committed by this run (lost response) or by another
    creator that won the name after the guard; a GPO carries no marker the
    create could set without changing a measured dimension, so it is left in
    place and the run fails loudly instead of deleting it.
    """
    report = _run_probe(tmp_path, "$global:commitThenThrow = 'gpo'\n")
    created = report["created"]
    assert isinstance(created, dict)
    gpos = created["gpos"]
    gpos = gpos if isinstance(gpos, list) else [gpos]
    assert [(g["role"], g["owned"]) for g in gpos] == [("control", False)]
    assert report["gpos_left"] == ["zz-studio-lifecycle-20261008000000-4321-control"]
    assert report["ad_left"] == []
    assert report["cleanup_succeeded"] is False
    cleanup = report["cleanup"]
    assert isinstance(cleanup, dict)
    assert any("ownership unproven" in p for p in cleanup["problems"])


_FOREIGN_OU_RACE = r"""function New-ADOrganizationalUnit { param($Name, $Path, $Server, $ProtectedFromAccidentalDeletion, $Description, $ErrorAction)
    $global:ad["OU=$Name,$Path"] = 'foreign-ou'
    throw 'synthetic: another creator won after the absence check; object already exists' }
"""


def test_guest_probe_a_name_won_by_another_creator_is_not_deleted(tmp_path: Path) -> None:
    """Re-review P1 (Sol's ownership_race): intent alone is not ownership.

    The parent OU is taken by someone else between the guard and the create;
    this run's create throws. The foreign OU carries no marker of this run, so
    cleanup leaves it and reports the run as not cleanly torn down.
    """
    report = _run_probe(tmp_path, _FOREIGN_OU_RACE)
    parent = "OU=zz-studio-lifecycle-20261008000000-4321,DC=synthetic,DC=test"
    assert report["deleted"] == []
    assert report["ad_left"] == [parent]
    assert report["cleanup_succeeded"] is False
    cleanup = report["cleanup"]
    assert isinstance(cleanup, dict)
    assert any("without this run's marker" in p for p in cleanup["problems"])
    assert cleanup["residual"]["surviving_ous"] == []


_FOREIGN_WMI_RACE = r"""function New-ADObject { param($Name, $Type, $Path, $Server, $OtherAttributes, $ErrorAction)
    $global:ad["CN=$Name,$Path"] = @{ kind = 'wmi'; marker = 'someone else' }
    throw 'synthetic: object already exists' }
"""


def test_guest_probe_a_foreign_wmi_filter_is_not_deleted(tmp_path: Path) -> None:
    report = _run_probe(tmp_path, _FOREIGN_WMI_RACE)
    ad_left = report["ad_left"]
    assert isinstance(ad_left, list) and len(ad_left) == 1
    assert str(ad_left[0]).startswith("CN={")
    assert not any(str(d).startswith("CN={") for d in report["deleted"])  # type: ignore[union-attr]
    assert report["cleanup_succeeded"] is False


def test_guest_probe_objects_carrying_the_runs_marker_are_removed(tmp_path: Path) -> None:
    """The control for the two race probes: a clean early failure tears down fully."""
    report = _run_probe(tmp_path, "$global:commitThenThrow = 'gpo'\n")
    assert report["ad_left"] == []
