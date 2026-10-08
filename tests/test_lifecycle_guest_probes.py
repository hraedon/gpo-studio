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
import re
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
# Run names carry a nonce the probe cannot predict; a collision is modelled by
# answering any -Name lookup that ends with this suffix with a foreign GPO.
$global:collideSuffix = ''
$global:foreignGpo = $null
function Get-Date { param([string]$Format) if ($Format) { return '20261008000000' }; return [datetime]'2026-10-08' }
# The DOMAIN CONTROLLER's clock, which creation proof uses: RootDSE currentTime
# (as the raw generalized-time string) and every GPO's whenCreated.
$global:dcClock = [datetime]::SpecifyKind([datetime]'2026-10-08', 'Utc')
function Get-ADRootDSE { param($Server, $ErrorAction)
    return [pscustomobject]@{ currentTime = $global:dcClock.ToString('yyyyMMddHHmmss') + '.0Z' } }
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
function Start-Sleep { param($Seconds) }
function Get-GPO { param([switch]$All, $Name, $Guid, $Domain, $Server, $ErrorAction)
    if ($All) { return @($global:gpos.Values) }
    if ($Name -and $global:collideSuffix -and "$Name".EndsWith($global:collideSuffix)) { return $global:foreignGpo }
    if ($Name) { if ($global:gpos.ContainsKey($Name)) { return $global:gpos[$Name] }; throw "GpoNotFound: $Name was not found" }
    foreach ($g in $global:gpos.Values) { if ("$($g.Id)" -eq "$Guid") { return $g } }
    throw "GpoNotFound: $Guid was not found" }
function New-GPO { param($Name, $Comment, $Domain, $Server, $ErrorAction)
    $g = [pscustomobject]@{ Id = [guid]::NewGuid(); DisplayName = $Name; Description = $Comment
        GpoStatus = 'AllSettingsEnabled'; WmiFilter = $null; CreationTime = $global:dcClock }
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
        run_id                = $r.run_id
        import_as_new_error   = $r.operations.import_as_new.error
    } | ConvertTo-Json -Depth 8 -Compress
}
"""


def _run_probe(tmp_path: Path, setup: str) -> dict[str, object]:
    out = tmp_path / "out"
    out.mkdir(parents=True)
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


_RUN_ID = re.compile(r"^lifecycle-(\d{14}-\d{4}-[0-9a-f]{16})$")


def _prefix(report: dict[str, object]) -> str:
    """The run's name prefix, recovered from its run id (the nonce is random)."""
    match = _RUN_ID.match(str(report["run_id"]))
    assert match, report["run_id"]
    return f"zz-studio-lifecycle-{match.group(1)}"


def test_guest_probe_a_name_collision_deletes_nothing(tmp_path: Path) -> None:
    """Finding 1: a GPO already holding one of the run's names is never touched."""
    report = _run_probe(
        tmp_path,
        "$global:foreignGpo = [pscustomobject]@{ Id = [guid]'aaaaaaaa-0000-0000-0000-000000000099'; "
        "DisplayName = 'foreign-control' }\n"
        "$global:gpos['foreign-control'] = $global:foreignGpo\n"
        "$global:collideSuffix = '-control'\n",
    )
    assert report["deleted"] == []
    assert report["gpos_left"] == ["foreign-control"]
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
    assert report["gpos_left"] == [f"{_prefix(report)}-control"]
    assert report["ad_left"] == []
    assert report["cleanup_succeeded"] is False
    residual = report["cleanup"]["residual"]  # type: ignore[index]
    assert len(residual["surviving_gpos"]) == 1
    assert residual["surviving_gpos"][0].startswith(f"{_prefix(report)}-control (")
    assert residual["surviving_gpos"][0].endswith("left in place, ownership unproven")
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
    parent = f"OU={_prefix(report)},DC=synthetic,DC=test"
    assert report["deleted"] == []
    assert report["ad_left"] == [parent]
    assert report["cleanup_succeeded"] is False
    cleanup = report["cleanup"]
    assert isinstance(cleanup, dict)
    assert any("without this run's marker" in p for p in cleanup["problems"])
    # Reported as residue (estate run 1: never clean while run-named objects
    # survive), and labelled as not ours.
    assert cleanup["residual"]["surviving_ous"] == [
        f"{parent}: left in place, ownership unproven"
    ]


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


# ---------------------------------------------------------------------------
# Re-review 3: a GPO a creating operation RETURNS is not proof of creating it
# ---------------------------------------------------------------------------

#: Stand-ins for the rest of the flow, so a run reaches the creating operations.
_FULL_FLOW = r"""
$global:groupCount = 0
function Get-ADGroup { param($Identity, $Server, $ErrorAction)
    $global:groupCount++
    return [pscustomobject]@{ SID = [pscustomobject]@{ Value = "S-1-5-21-1-2-3-$($global:groupCount + 1100)" } } }
function Set-GPRegistryValue { param($Guid, $Domain, $Server, $Key, $ValueName, $Type, $Value, $ErrorAction) }
function New-GPLink { param($Guid, $Target, $LinkEnabled, $Domain, $Server, $ErrorAction) }
function Remove-GPLink { param($Guid, $Target, $Domain, $Server, $ErrorAction) }
function Set-GPPermission { param($Guid, $Domain, $Server, $TargetName, $TargetType, $PermissionLevel, [switch]$Replace, $ErrorAction) }
function Set-ADObject { param($Identity, $Server, $Replace, $ErrorAction) }
function Get-GPRegistryValue { param($Guid, $Domain, $Server, $Key, $ValueName, $ErrorAction) return [pscustomobject]@{ Value = 'v' } }
function Get-GPPermission { param($Guid, [switch]$All, $Domain, $Server, $ErrorAction) }
function Get-Acl { param($Path) return [pscustomobject]@{ Sddl = '' } }
function Backup-GPO { param($Guid, $Path, $Comment, $Domain, $Server, $ErrorAction) return [pscustomobject]@{ Id = [guid]'dddddddd-0000-0000-0000-000000000001' } }
function Copy-GPO { param($SourceGuid, $TargetName, [switch]$CopyAcl, $SourceDomain, $TargetDomain, $SourceDomainController, $TargetDomainController, $ErrorAction)
    return New-GPO -Name $TargetName }
function Restore-GPO { param($BackupId, $Path, $Domain, $Server, $Confirm, $ErrorAction) }
function Get-ADObject { param($Identity, $Server, $ErrorAction, $Properties, $LDAPFilter, $SearchBase)
    if ($LDAPFilter) { return @() }
    if ($Identity -eq 'DC=synthetic,DC=test') { return [pscustomobject]@{ gPLink = '' } }
    if ("$Identity" -match '^CN=\{([^}]+)\},CN=Policies,') {
        $id = $Matches[1]; $when = $null
        foreach ($g in $global:gpos.Values) { if ("$($g.Id)" -eq $id) { $when = $g.CreationTime } }
        return [pscustomobject]@{ gPCWQLFilter = ''; gPCFileSysPath = ''; whenCreated = $when } }
    if ($global:ad.ContainsKey($Identity)) {
        $v = $global:ad[$Identity]; $m = if ($v -is [hashtable]) { $v.marker } else { '' }
        return [pscustomobject]@{ DistinguishedName = $Identity; description = $m; 'msWMI-Parm1' = $m } }
    throw [Microsoft.ActiveDirectory.Management.ADIdentityNotFoundException]::new('not found') }
"""

_FOREIGN_ID = "eeeeeeee-0000-0000-0000-000000000099"

#: Import-GPO -CreateIfNeeded hands back a GPO that already existed when the
#: operation began (it is in the pre-operation id snapshot).
_IMPORT_RETURNS_PREEXISTING = _FULL_FLOW + r"""
$global:gpos['someone-else'] = [pscustomobject]@{ Id = [guid]'eeeeeeee-0000-0000-0000-000000000099'
    DisplayName = 'someone-else'; Description = 'foreign'; GpoStatus = 'AllSettingsEnabled'; WmiFilter = $null
    CreationTime = [datetime]'2026-01-01' }
function Import-GPO { param($BackupId, $Path, $TargetName, $TargetGuid, [switch]$CreateIfNeeded, $Domain, $Server, $Confirm, $ErrorAction)
    if ($CreateIfNeeded) { return $global:gpos['someone-else'] }
    return Get-GPO -Guid $TargetGuid }
"""

#: Sol's race: another creator's GPO appears under the target name and the
#: import returns it. It is not in the snapshot, but it was created before the
#: operation began.
_IMPORT_RETURNS_OLDER_GPO = _FULL_FLOW + r"""
function Import-GPO { param($BackupId, $Path, $TargetName, $TargetGuid, [switch]$CreateIfNeeded, $Domain, $Server, $Confirm, $ErrorAction)
    if ($CreateIfNeeded) {
        $g = [pscustomobject]@{ Id = [guid]'eeeeeeee-0000-0000-0000-000000000099'; DisplayName = $TargetName
            Description = 'foreign'; GpoStatus = 'AllSettingsEnabled'; WmiFilter = $null; CreationTime = [datetime]'2026-01-01' }
        $global:gpos[$TargetName] = $g
        return $g }
    return Get-GPO -Guid $TargetGuid }
"""


def _import_entry(report: dict[str, object]) -> dict[str, object]:
    created = report["created"]
    assert isinstance(created, dict)
    gpos = created["gpos"]
    entries = [g for g in (gpos if isinstance(gpos, list) else [gpos]) if g["role"] == "import_as_new"]
    assert len(entries) == 1
    return dict(entries[0])


@pytest.mark.parametrize(
    "setup", [_IMPORT_RETURNS_PREEXISTING, _IMPORT_RETURNS_OLDER_GPO],
    ids=["returned_id_in_snapshot", "returned_gpo_older_than_operation"],
)
def test_guest_probe_a_returned_foreign_gpo_is_never_owned_or_deleted(
    tmp_path: Path, setup: str
) -> None:
    report = _run_probe(tmp_path, setup)
    entry = _import_entry(report)
    assert entry["owned"] is False
    assert entry["id"] == _FOREIGN_ID
    assert _FOREIGN_ID not in [str(d).lower() for d in report["deleted"]]  # type: ignore[union-attr]
    assert any(
        name == "someone-else" or re.search(r"-imported-[0-9a-f]{16}$", str(name))
        for name in report["gpos_left"]  # type: ignore[union-attr]
    )
    assert "not created by this run" in str(report["import_as_new_error"])
    assert report["cleanup_succeeded"] is False
    # Estate run 1: a surviving run-touched GPO must appear in the residual.
    residual = report["cleanup"]["residual"]  # type: ignore[index]
    assert any(_FOREIGN_ID in entry and "ownership unproven" in entry
               for entry in residual["surviving_gpos"])


def test_guest_probe_gpos_the_operations_did_create_are_owned_and_removed(
    tmp_path: Path,
) -> None:
    """The control for the probes above: fresh copies are owned and torn down."""
    report = _run_probe(tmp_path, _IMPORT_RETURNS_PREEXISTING)
    created = report["created"]
    assert isinstance(created, dict)
    owned = {g["role"]: g["owned"] for g in created["gpos"]}
    assert owned["copy"] is True and owned["copy_with_acl"] is True
    assert report["gpos_left"] == ["someone-else"]


#: Estate run 1 (2026-10-08): the DC's clock ran a few seconds BEHIND the
#: member's. Copy-GPO / Import-GPO create the GPO at DC time; the member's
#: clock already reads later. The proof must use the DC clock on both sides.
_DC_BEHIND_MEMBER = _FULL_FLOW + r"""
function Get-Date { param([string]$Format) if ($Format) { return '20261008000010' }; return [datetime]'2026-10-08 00:00:10' }
function Import-GPO { param($BackupId, $Path, $TargetName, $TargetGuid, [switch]$CreateIfNeeded, $Domain, $Server, $Confirm, $ErrorAction)
    if ($CreateIfNeeded) { return New-GPO -Name $TargetName }
    return Get-GPO -Guid $TargetGuid }
"""


def test_guest_probe_genuine_creations_are_owned_when_the_dc_lags_the_member(
    tmp_path: Path,
) -> None:
    report = _run_probe(tmp_path, _DC_BEHIND_MEMBER)
    created = report["created"]
    assert isinstance(created, dict)
    by_role = {g["role"]: g for g in created["gpos"]}
    for role in ("copy", "copy_with_acl", "import_as_new"):
        entry = by_role[role]
        assert entry["owned"] is True, (role, entry)
        assert str(entry["creation_evidence"]).startswith("in_snapshot=False;"), entry
        assert str(entry["id"]) in [str(d).lower() for d in report["deleted"]]  # type: ignore[union-attr]
    assert report["import_as_new_error"] is None
    assert report["gpos_left"] == []


#: Sol's round-4 cleanup case: the WMI filter survives teardown and its marker
#: has been changed, so it no longer reads as this run's.
_SURVIVING_WMI_MARKER_CHANGED = r"""
$global:commitThenThrow = 'wmi'
function Remove-ADObject { param($Identity, $Server, $Confirm, $ErrorAction)
    $global:ad[$Identity].marker = 'changed after the delete attempt' }
"""


def test_guest_probe_a_survivor_whose_marker_changed_still_fails_cleanup(
    tmp_path: Path,
) -> None:
    report = _run_probe(tmp_path, _SURVIVING_WMI_MARKER_CHANGED)
    cleanup = report["cleanup"]
    assert isinstance(cleanup, dict)
    survivors = cleanup["residual"]["surviving_wmi_filters"]
    assert len(survivors) == 1
    assert survivors[0].endswith(": left in place, ownership unproven")
    assert report["cleanup_succeeded"] is False


def test_guest_probe_run_names_carry_an_unguessable_nonce(tmp_path: Path) -> None:
    """Re-review 4 P1: another creator cannot take a name it cannot know."""
    first = _run_probe(tmp_path / "a", "$global:commitThenThrow = 'gpo'\n")
    second = _run_probe(tmp_path / "b", "$global:commitThenThrow = 'gpo'\n")
    assert _prefix(first) != _prefix(second)


#: Sol's round-5 racer: it reads the run prefix from the first OU (public from
#: then on), derives what the import target "would" be called, and creates a
#: GPO under that name inside the window. Import-GPO -CreateIfNeeded adopts a
#: GPO of its TargetName if one exists.
_RACER_DERIVES_NAME_FROM_OU = _FULL_FLOW + r"""
function Import-GPO { param($BackupId, $Path, $TargetName, $TargetGuid, [switch]$CreateIfNeeded, $Domain, $Server, $Confirm, $ErrorAction)
    if (-not $CreateIfNeeded) { return Get-GPO -Guid $TargetGuid }
    $ou = @($global:ad.Keys | Where-Object { $_ -match '^OU=(zz-studio-lifecycle-[^,]+),DC=' })[0]
    $null = $ou -match '^OU=(zz-studio-lifecycle-[^,]+),DC='
    $derived = "$($Matches[1])-imported"
    $global:gpos[$derived] = [pscustomobject]@{ Id = [guid]'eeeeeeee-0000-0000-0000-000000000099'
        DisplayName = $derived; Description = 'racer'; GpoStatus = 'AllSettingsEnabled'; WmiFilter = $null
        CreationTime = $global:dcClock }
    if ($global:gpos.ContainsKey($TargetName)) { return $global:gpos[$TargetName] }
    return New-GPO -Name $TargetName }
"""


def test_guest_probe_a_racer_cannot_derive_the_import_target_from_the_ou(
    tmp_path: Path,
) -> None:
    """Re-review 5 P1: each creating target carries its own unpublished nonce."""
    report = _run_probe(tmp_path, _RACER_DERIVES_NAME_FROM_OU)
    entry = _import_entry(report)
    prefix = _prefix(report)
    assert re.fullmatch(rf"{re.escape(prefix)}-imported-[0-9a-f]{{16}}", str(entry["name"]))
    assert entry["owned"] is True
    assert entry["id"] != _FOREIGN_ID
    assert _FOREIGN_ID not in [str(d).lower() for d in report["deleted"]]  # type: ignore[union-attr]
    assert report["gpos_left"] == [f"{prefix}-imported"]
    # The racer's GPO carries the run prefix, so the report-only sweep lists it
    # and the post-run state is not reported clean.
    residual = report["cleanup"]["residual"]  # type: ignore[index]
    assert any(_FOREIGN_ID in item and "run-named" in item for item in residual["surviving_gpos"])
    assert report["cleanup_succeeded"] is False


def test_guest_probe_each_creating_operation_gets_a_distinct_nonce(tmp_path: Path) -> None:
    report = _run_probe(tmp_path, _DC_BEHIND_MEMBER)
    created = report["created"]
    assert isinstance(created, dict)
    prefix = _prefix(report)
    nonces = []
    for role, suffix in (("copy", "copy"), ("copy_with_acl", "copy_with_acl"),
                         ("import_as_new", "imported")):
        name = next(g["name"] for g in created["gpos"] if g["role"] == role)
        match = re.fullmatch(rf"{re.escape(prefix)}-{suffix}-([0-9a-f]{{16}})", name)
        assert match, name
        nonces.append(match.group(1))
    assert len(set(nonces)) == 3
    assert prefix.rsplit("-", 1)[-1] not in nonces
