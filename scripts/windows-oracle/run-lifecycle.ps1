#!/usr/bin/env pwsh
# Plan 034: the same-domain lifecycle lane. Runs ON THE MEMBER SERVER.
#
# Measures what each GPMC lifecycle operation does to the scope around a GPO's
# settings -- GUID, security filtering, WMI filter association, links,
# description -- so the controller can grade gpo_studio.lifecycle's
# SCOPE_SURVIVAL predictions against it. This script never sees those
# predictions; it only authors, operates, and reads back.
#
# AUTHORING IS NATIVE, DELIBERATELY. The source GPO is made with New-GPO and
# Set-GPRegistryValue, not imported from a Studio backup, so a Studio writer
# defect cannot surface as a lifecycle finding. The link, WMI association and
# security-filter mechanics are copied from run-rsop-author.ps1 (which twelve
# lanes bind, so it is copied rather than shared).
#
# ## Sequence
#
#   1. Disposable OU tree (parent + 'src-link' + 'tgt-link'), two disposable
#      security groups, two WMI filters, and a CONTROL GPO left at New-GPO
#      defaults (its DACL is what "defaulted" means for a new GPO).
#   2. SOURCE GPO: registry value, description, link at src-link, WMI filter
#      src, security filter (Authenticated Users reduced to Read, the src group
#      given Apply).
#   3. Pre-existing TARGET GPO with its own value, description, link
#      (tgt-link), WMI filter (tgt) and security filter (tgt group).
#   4. Backup-GPO of the source, into this run's own directory so the pull
#      carries the real backup back to the controller.
#   5. Operations, each read back afterwards, in the order the controller's
#      expectation names: Copy-GPO; Copy-GPO -CopyAcl; Import-GPO
#      -CreateIfNeeded (new name); Import-GPO into the pre-existing target;
#      then PERTURB the source in every dimension and Restore-GPO it. Copies go
#      first because Copy-GPO reads the live source, which must still be as it
#      was backed up.
#   6. Remove everything created, then re-query each object for ABSENCE.
#
# ## Blast radius
#
# Every link is to an OU this script created, with no computer or user in it,
# so nothing created here applies to any machine. The registry value lives
# under HKLM\Software\Policies\StudioLab. The WMI filters live outside the OU
# tree (CN=SOM,CN=WMIPolicy) and are removed explicitly, after the GPOs that
# referenced them.
param(
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [string]$Domain = $env:USERDNSDOMAIN
)
$ErrorActionPreference = 'Stop'

$stamp = "$(Get-Date -Format yyyyMMddHHmmss)-$(Get-Random -Minimum 1000 -Maximum 9999)"
$runId = "lifecycle-$stamp"
$work = Join-Path $OutputDir $runId
$commands = Join-Path $work 'commands'
$backupRoot = Join-Path $work 'backup'
New-Item -ItemType Directory -Force -Path $work, $commands, $backupRoot | Out-Null

$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem
$Domain = if ([string]::IsNullOrWhiteSpace($Domain)) { "$($cs.Domain)" } else { $Domain }
$gpModule = Get-Module -ListAvailable GroupPolicy | Select-Object -First 1

$PolicyKey = 'HKLM\Software\Policies\StudioLab'
$ValueName = 'LifecycleMarker'
$prefix = "zz-studio-lifecycle-$stamp"
# sAMAccountName must stay short; the name and the account name are kept equal
# so Set-GPPermission -TargetName cannot resolve a different principal.
$short = '{0:D6}' -f (Get-Random -Minimum 0 -Maximum 999999)

$operationNames = @('copy', 'copy_with_acl', 'import_as_new', 'import_into_existing', 'restore_in_place')
$operations = [ordered]@{}
foreach ($name in $operationNames) {
    $operations[$name] = [ordered]@{
        succeeded         = $false
        error             = $null
        target_preexisted = ($name -eq 'import_into_existing' -or $name -eq 'restore_in_place')
        target_before     = $null
        target_after      = $null
    }
}

$created = [ordered]@{
    ous         = @()
    groups      = @()
    wmi_filters = @()
    gpos        = @()
}

$result = [ordered]@{
    schema_version         = 1
    run_id                 = $runId
    domain                 = $Domain
    fixture                = $null
    backup                 = $null
    control_state          = $null
    source_baseline        = $null
    target_baseline        = $null
    restore_perturbed      = $null
    operations             = $operations
    created                = $created
    cleanup                = $null
    cleanup_succeeded      = $false
    cleanup_state_restored = $false
    environment            = [ordered]@{
        server_caption              = "$($os.Caption)"
        server_build                = "$($os.BuildNumber)"
        computer_system_domain_role = [int]$cs.DomainRole
        powershell_edition          = "$($PSVersionTable.PSEdition)"
        powershell_version          = "$($PSVersionTable.PSVersion)"
        group_policy_module_version = if ($gpModule) { "$($gpModule.Version)" } else { 'unknown' }
        gpmc_version                = 'built-in'
        locale                      = (Get-Culture).Name
        computer_system_name        = "$($cs.Name)"
        computer_system_domain      = "$($cs.Domain)"
    }
    error                  = $null
}

# Get-ADObject hands back ADPropertyValueCollection, not primitives, and
# ConvertTo-Json hangs forever on one rather than failing (measured 2026-09-07
# by the publication lane). Everything read from the directory is flattened to
# a string or an int before it can reach the serializer.
function Flatten($value) {
    if ($null -eq $value) { return '' }
    return [string]::Join(';', @($value))
}

# Copied from run-rsop-author.ps1: -ErrorAction SilentlyContinue does not
# suppress Get-ADObject's terminating not-found, and "cannot tell" must not be
# read as "not there".
function Test-AdObjectExists {
    param([string]$Identity, [string]$Server)
    try {
        $null = Get-ADObject -Identity $Identity -Server $Server -ErrorAction Stop
        return $true
    } catch [Microsoft.ActiveDirectory.Management.ADIdentityNotFoundException] {
        return $false
    } catch {
        if ("$($_.Exception.Message)" -match 'not found|does not exist') { return $false }
        throw
    }
}

function Wait-ForAdObject {
    param([string]$Identity, [string]$Server, [int]$Attempts = 20)
    foreach ($attempt in 1..$Attempts) {
        if (Test-AdObjectExists -Identity $Identity -Server $Server) { return $true }
        Start-Sleep -Seconds 3
    }
    return $false
}

# Get-GPO throws GpoNotFound for a missing GPO. Absence is reported as $true;
# any other failure is re-thrown, for the same reason as above.
function Test-GpoAbsent {
    param([guid]$Id)
    try {
        $null = Get-GPO -Guid $Id -Domain $Domain -Server $dc -ErrorAction Stop
        return $false
    } catch {
        if ("$($_.Exception.Message)" -match 'not found|does not exist|GpoNotFound') { return $true }
        throw
    }
}

function Register-Gpo {
    param([string]$Role, $Gpo)
    $script:created.gpos += [ordered]@{ role = $Role; name = [string]$Gpo.DisplayName; id = [string]$Gpo.Id }
}

function New-LaneWmiFilter {
    param([string]$Name)
    # msWMI-Parm2 is LENGTH-PREFIXED; see run-rsop-author.ps1 for why the
    # lengths are load-bearing. The query is true on every Windows host, which
    # is irrelevant here (nothing is linked where it could apply) but keeps the
    # filter well-formed.
    $filterId = "{$([guid]::NewGuid())}"
    $query = 'SELECT * FROM Win32_OperatingSystem'
    $namespace = 'root\CIMv2'
    $parm2 = "1;3;$($namespace.Length);$($query.Length);WQL;$namespace;$query;"
    $now = (Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss') + '.000000-000'
    New-ADObject -Name $filterId -Type 'msWMI-Som' -Path $somPath -Server $dc `
        -OtherAttributes @{
            'msWMI-Name'         = $Name
            'msWMI-Parm1'        = 'Studio lifecycle lane, disposable'
            'msWMI-Parm2'        = $parm2
            'msWMI-ID'           = $filterId
            'msWMI-Author'       = "$($env:USERNAME)@$Domain"
            'msWMI-ChangeDate'   = $now
            'msWMI-CreationDate' = $now
        } -ErrorAction Stop
    $script:created.wmi_filters += "CN=$filterId,$somPath"
    return $filterId
}

function Set-WmiAssociation {
    param([guid]$Id, [string]$FilterId)
    $gpoDn = "CN={$Id},CN=Policies,CN=System,$domainDn"
    if (-not (Wait-ForAdObject -Identity $gpoDn -Server $dc)) { throw "GPO not readable: $gpoDn" }
    Set-ADObject -Identity $gpoDn -Server $dc `
        -Replace @{ gPCWQLFilter = "[$Domain;$FilterId;0]" } -ErrorAction Stop
}

function Set-SecurityFilter {
    param([guid]$Id, [string]$GroupName)
    # MS16-072: Authenticated Users keeps READ; only Apply is moved.
    Set-GPPermission -Guid $Id -Domain $Domain -Server $dc -TargetName 'Authenticated Users' `
        -TargetType Group -PermissionLevel GpoRead -Replace -ErrorAction Stop | Out-Null
    Set-GPPermission -Guid $Id -Domain $Domain -Server $dc -TargetName $GroupName `
        -TargetType Group -PermissionLevel GpoApply -ErrorAction Stop | Out-Null
}

function New-AuthoredGpo {
    param([string]$Role, [string]$Name, [string]$Description, [string]$Value,
          [string]$LinkDn, [string]$FilterId, [string]$GroupName)
    $gpo = New-GPO -Name $Name -Comment $Description -Domain $Domain -Server $dc -ErrorAction Stop
    Register-Gpo -Role $Role -Gpo $gpo
    Set-GPRegistryValue -Guid $gpo.Id -Domain $Domain -Server $dc -Key $PolicyKey `
        -ValueName $ValueName -Type String -Value $Value -ErrorAction Stop | Out-Null
    New-GPLink -Guid $gpo.Id -Target $LinkDn -LinkEnabled Yes -Domain $Domain -Server $dc `
        -ErrorAction Stop | Out-Null
    Set-WmiAssociation -Id $gpo.Id -FilterId $FilterId
    Set-SecurityFilter -Id $gpo.Id -GroupName $GroupName
    return $gpo
}

# Everything the controller compares, read from the directory and SYSVOL as
# Windows reports them. Links are read with Get-GPInheritance over every scope
# this run could have linked to (its OUs) plus the domain root.
function Read-ScopeState {
    param([guid]$Id)
    $g = Get-GPO -Guid $Id -Domain $Domain -Server $dc -ErrorAction Stop
    $gpoDn = "CN={$Id},CN=Policies,CN=System,$domainDn"
    $ad = Get-ADObject -Identity $gpoDn -Server $dc -Properties gPCWQLFilter, gPCFileSysPath `
        -ErrorAction Stop
    $wql = [string](Flatten $ad.gPCWQLFilter)
    $filterId = ''
    $match = [regex]::Match($wql, '^\[[^;]*;(\{[0-9A-Fa-f-]+\});\d+\]$')
    if ($match.Success) { $filterId = $match.Groups[1].Value.ToLowerInvariant() }
    $sysvol = [string](Flatten $ad.gPCFileSysPath)

    $settingsValue = ''
    try {
        $reg = @(Get-GPRegistryValue -Guid $Id -Domain $Domain -Server $dc -Key $PolicyKey `
                -ValueName $ValueName -ErrorAction Stop)
        if ($reg.Count -gt 0) { $settingsValue = [string]$reg[0].Value }
    } catch {
        # Absent value: the cmdlet throws "...registry setting was not found".
        if ("$($_.Exception.Message)" -notmatch 'not found|does not exist') { throw }
    }

    $links = @()
    foreach ($scope in $linkScopes) {
        $inheritance = Get-GPInheritance -Target $scope -Domain $Domain -Server $dc -ErrorAction Stop
        foreach ($link in @($inheritance.GpoLinks)) {
            if ($link -and $link.GpoId -eq $Id) { $links += [string]$scope }
        }
    }

    $permissions = @()
    $permissionNames = @()
    foreach ($p in @(Get-GPPermission -Guid $Id -All -Domain $Domain -Server $dc -ErrorAction Stop)) {
        $sid = ''
        if ($p.Trustee -and $p.Trustee.Sid) { $sid = [string]$p.Trustee.Sid.Value }
        $permissions += [string]('{0}|{1}|{2}' -f $sid, $p.Permission, $p.Denied)
        $permissionNames += [string]('{0}|{1}|{2}|inherited={3}' -f $p.Trustee.Name, $p.Permission, $p.Denied, $p.Inherited)
    }

    $sddl = ''
    try { $sddl = [string](Get-Acl -Path "AD:$gpoDn").Sddl } catch { $sddl = "unreadable: $($_.Exception.Message)" }
    $cmtPresent = $false
    if ($sysvol) { $cmtPresent = [bool](Test-Path -LiteralPath (Join-Path $sysvol 'GPO.cmt')) }
    $wmiName = ''
    if ($g.WmiFilter) { $wmiName = [string]$g.WmiFilter.Name }

    return [ordered]@{
        gpo_id           = ([string]$g.Id).ToLowerInvariant()
        display_name     = [string]$g.DisplayName
        description      = [string]$g.Description
        gpo_status       = [string]$g.GpoStatus
        settings_value   = $settingsValue
        gpc_wql_filter   = $wql
        wmi_filter_id    = $filterId
        wmi_filter_name  = $wmiName
        links            = @($links | Sort-Object)
        permissions      = @($permissions | Sort-Object)
        permission_names = @($permissionNames | Sort-Object)
        dacl_sddl        = $sddl
        gpo_cmt_present  = $cmtPresent
    }
}

# Each GPMC command's objects and errors are kept as raw artifacts, as the
# publication lane keeps its import's.
function Initialize-CommandArtifacts {
    param([string]$Name)
    foreach ($stream in 'stdout', 'stderr') {
        New-Item -ItemType File -Force -Path (Join-Path $commands "$Name.$stream.txt") | Out-Null
    }
}
function Save-CommandOutput {
    param([string]$Name, $Output)
    $Output | Out-File -LiteralPath (Join-Path $commands "$Name.stdout.txt") -Encoding utf8
}

$dc = $null
$domainDn = $null
$somPath = $null
$linkScopes = @()
try {
    Import-Module ActiveDirectory -ErrorAction Stop
    Import-Module GroupPolicy -ErrorAction Stop
    # Every AD and Group Policy operation is pinned to ONE domain controller.
    $dc = (Get-ADDomain -Server $Domain).PDCEmulator
    $domainDn = (Get-ADDomain -Server $Domain).DistinguishedName
    $somPath = "CN=SOM,CN=WMIPolicy,CN=System,$domainDn"

    $collisions = @(Get-GPO -All -Domain $Domain -Server $dc -ErrorAction Stop |
        Where-Object { $_.DisplayName -like "$prefix*" })
    if ($collisions.Count -ne 0) { throw 'disposable names already exist' }

    $parentDn = "OU=$prefix,$domainDn"
    $ouSourceDn = "OU=src-link,$parentDn"
    $ouTargetDn = "OU=tgt-link,$parentDn"
    New-ADOrganizationalUnit -Name $prefix -Path $domainDn -Server $dc `
        -ProtectedFromAccidentalDeletion:$false -ErrorAction Stop
    $created.ous += $parentDn
    if (-not (Wait-ForAdObject -Identity $parentDn -Server $dc)) { throw "OU not readable: $parentDn" }
    foreach ($child in 'src-link', 'tgt-link') {
        New-ADOrganizationalUnit -Name $child -Path $parentDn -Server $dc `
            -ProtectedFromAccidentalDeletion:$false -ErrorAction Stop
        $created.ous += "OU=$child,$parentDn"
        if (-not (Wait-ForAdObject -Identity "OU=$child,$parentDn" -Server $dc)) {
            throw "OU not readable: OU=$child,$parentDn"
        }
    }
    $linkScopes = @($ouSourceDn, $ouTargetDn, $parentDn, $domainDn)

    $groupSids = @{}
    foreach ($side in 'src', 'tgt') {
        $groupName = "zzlc-$short-$side"
        New-ADGroup -Name $groupName -SamAccountName $groupName -GroupScope Global `
            -GroupCategory Security -Path $parentDn -Server $dc -ErrorAction Stop
        $groupDn = "CN=$groupName,$parentDn"
        $created.groups += $groupDn
        if (-not (Wait-ForAdObject -Identity $groupDn -Server $dc)) { throw "group not readable: $groupDn" }
        $groupSids[$side] = [string](Get-ADGroup -Identity $groupDn -Server $dc -ErrorAction Stop).SID.Value
    }
    $sourceFilter = New-LaneWmiFilter -Name "$prefix-src-wmi"
    $targetFilter = New-LaneWmiFilter -Name "$prefix-tgt-wmi"

    $fixture = [ordered]@{
        stamp                 = $stamp
        policy_key            = $PolicyKey
        value_name            = $ValueName
        source_value          = "source-$stamp"
        target_value          = "target-$stamp"
        perturbed_value       = "perturbed-$stamp"
        source_description    = "Studio lifecycle lane source $stamp"
        target_description    = "Studio lifecycle lane target $stamp"
        perturbed_description = "Studio lifecycle lane perturbed $stamp"
        ou_parent_dn          = $parentDn
        ou_source_dn          = $ouSourceDn
        ou_target_dn          = $ouTargetDn
        source_group_sid      = $groupSids['src']
        target_group_sid      = $groupSids['tgt']
        source_wmi_filter_id  = $sourceFilter.ToLowerInvariant()
        target_wmi_filter_id  = $targetFilter.ToLowerInvariant()
    }
    $result.fixture = $fixture

    $control = New-GPO -Name "$prefix-control" -Domain $Domain -Server $dc -ErrorAction Stop
    Register-Gpo -Role 'control' -Gpo $control
    $result.control_state = Read-ScopeState -Id $control.Id

    $source = New-AuthoredGpo -Role 'source' -Name "$prefix-source" `
        -Description $fixture.source_description -Value $fixture.source_value `
        -LinkDn $ouSourceDn -FilterId $sourceFilter -GroupName "zzlc-$short-src"
    $target = New-AuthoredGpo -Role 'target' -Name "$prefix-target" `
        -Description $fixture.target_description -Value $fixture.target_value `
        -LinkDn $ouTargetDn -FilterId $targetFilter -GroupName "zzlc-$short-tgt"
    $result.source_baseline = Read-ScopeState -Id $source.Id
    $result.target_baseline = Read-ScopeState -Id $target.Id

    Initialize-CommandArtifacts 'backup'
    $backup = Backup-GPO -Guid $source.Id -Path $backupRoot -Comment "Studio lifecycle lane $stamp" `
        -Domain $Domain -Server $dc -ErrorAction Stop 2> (Join-Path $commands 'backup.stderr.txt')
    Save-CommandOutput 'backup' $backup
    $backupId = [guid]$backup.Id
    $result.backup = [ordered]@{
        backup_id     = "{$($backupId.ToString().ToUpperInvariant())}"
        source_gpo_id = ([string]$source.Id).ToLowerInvariant()
        relative_path = 'backup'
    }

    # --- Copy-GPO, without and with -CopyAcl -------------------------------
    foreach ($name in 'copy', 'copy_with_acl') {
        $op = $operations[$name]
        try {
            Initialize-CommandArtifacts $name
            $stderr = Join-Path $commands "$name.stderr.txt"
            $targetName = "$prefix-$name"
            if ($name -eq 'copy_with_acl') {
                $copy = Copy-GPO -SourceGuid $source.Id -TargetName $targetName -CopyAcl `
                    -SourceDomain $Domain -TargetDomain $Domain `
                    -SourceDomainController $dc -TargetDomainController $dc -ErrorAction Stop 2> $stderr
            } else {
                $copy = Copy-GPO -SourceGuid $source.Id -TargetName $targetName `
                    -SourceDomain $Domain -TargetDomain $Domain `
                    -SourceDomainController $dc -TargetDomainController $dc -ErrorAction Stop 2> $stderr
            }
            Register-Gpo -Role $name -Gpo $copy
            Save-CommandOutput $name $copy
            $op.target_after = Read-ScopeState -Id $copy.Id
            $op.succeeded = $true
        } catch {
            $op.error = "$($_.Exception.Message)"
        }
    }

    # --- Import-GPO -CreateIfNeeded, to a name that does not exist ----------
    $op = $operations['import_as_new']
    try {
        Initialize-CommandArtifacts 'import_as_new'
        $imported = Import-GPO -BackupId $backupId -Path $backupRoot -TargetName "$prefix-imported" `
            -CreateIfNeeded -Domain $Domain -Server $dc -Confirm:$false -ErrorAction Stop `
            2> (Join-Path $commands 'import_as_new.stderr.txt')
        Register-Gpo -Role 'import_as_new' -Gpo $imported
        Save-CommandOutput 'import_as_new' $imported
        $op.target_after = Read-ScopeState -Id $imported.Id
        $op.succeeded = $true
    } catch {
        $op.error = "$($_.Exception.Message)"
    }

    # --- Import-GPO into the pre-existing target ---------------------------
    $op = $operations['import_into_existing']
    try {
        Initialize-CommandArtifacts 'import_into_existing'
        $op.target_before = Read-ScopeState -Id $target.Id
        $out = Import-GPO -BackupId $backupId -Path $backupRoot -TargetGuid $target.Id `
            -Domain $Domain -Server $dc -Confirm:$false -ErrorAction Stop `
            2> (Join-Path $commands 'import_into_existing.stderr.txt')
        Save-CommandOutput 'import_into_existing' $out
        $op.target_after = Read-ScopeState -Id $target.Id
        $op.succeeded = $true
    } catch {
        $op.error = "$($_.Exception.Message)"
    }

    # --- Perturb the source in every dimension, then Restore-GPO -----------
    # Every dimension is moved to a DIFFERENT NON-EMPTY value, so the
    # controller can tell "restore put it back" (kept) from "restore left it
    # alone" (replaced) from "restore cleared it" (lost).
    $op = $operations['restore_in_place']
    try {
        Initialize-CommandArtifacts 'restore_in_place'
        Set-GPRegistryValue -Guid $source.Id -Domain $Domain -Server $dc -Key $PolicyKey `
            -ValueName $ValueName -Type String -Value $fixture.perturbed_value -ErrorAction Stop | Out-Null
        $live = Get-GPO -Guid $source.Id -Domain $Domain -Server $dc -ErrorAction Stop
        $live.Description = $fixture.perturbed_description
        Remove-GPLink -Guid $source.Id -Target $ouSourceDn -Domain $Domain -Server $dc `
            -ErrorAction Stop | Out-Null
        New-GPLink -Guid $source.Id -Target $ouTargetDn -LinkEnabled Yes -Domain $Domain -Server $dc `
            -ErrorAction Stop | Out-Null
        Set-WmiAssociation -Id $source.Id -FilterId $targetFilter
        Set-GPPermission -Guid $source.Id -Domain $Domain -Server $dc -TargetName "zzlc-$short-src" `
            -TargetType Group -PermissionLevel None -Replace -ErrorAction Stop | Out-Null
        Set-GPPermission -Guid $source.Id -Domain $Domain -Server $dc -TargetName "zzlc-$short-tgt" `
            -TargetType Group -PermissionLevel GpoApply -ErrorAction Stop | Out-Null
        $op.target_before = Read-ScopeState -Id $source.Id
        $result.restore_perturbed = $op.target_before

        $out = Restore-GPO -BackupId $backupId -Path $backupRoot -Domain $Domain -Server $dc `
            -Confirm:$false -ErrorAction Stop 2> (Join-Path $commands 'restore_in_place.stderr.txt')
        Save-CommandOutput 'restore_in_place' $out
        $op.target_after = Read-ScopeState -Id $source.Id
        $op.succeeded = $true
    } catch {
        $op.error = "$($_.Exception.Message)"
    }
} catch {
    $result.error = "$($_.Exception.Message)"
} finally {
    $problems = @()
    $residual = [ordered]@{
        surviving_gpos        = @()
        surviving_links       = @()
        surviving_wmi_filters = @()
        surviving_groups      = @()
        surviving_ous         = @()
    }
    $ownIds = @($created.gpos | ForEach-Object { [guid]$_.id })

    if ($dc) {
        # Links first, explicitly: Remove-GPO is not relied on to unlink.
        foreach ($scope in $linkScopes) {
            try {
                $inheritance = Get-GPInheritance -Target $scope -Domain $Domain -Server $dc -ErrorAction Stop
                foreach ($link in @($inheritance.GpoLinks)) {
                    if ($link -and ($ownIds -contains $link.GpoId)) {
                        Remove-GPLink -Guid $link.GpoId -Target $scope -Domain $Domain -Server $dc `
                            -ErrorAction Stop | Out-Null
                    }
                }
            } catch {
                $problems += "unlink at ${scope}: $($_.Exception.Message)"
            }
        }

        # GPOs by recorded id, then anything else carrying this run's prefix (a
        # command that created a GPO and then threw leaves no recorded id).
        foreach ($gpo in $created.gpos) {
            try {
                if (-not (Test-GpoAbsent -Id ([guid]$gpo.id))) {
                    Remove-GPO -Guid ([guid]$gpo.id) -Domain $Domain -Server $dc -Confirm:$false `
                        -ErrorAction Stop | Out-Null
                }
            } catch {
                $problems += "GPO delete failed for $($gpo.name): $($_.Exception.Message)"
            }
        }
        try {
            foreach ($stray in @(Get-GPO -All -Domain $Domain -Server $dc -ErrorAction Stop |
                    Where-Object { $_.DisplayName -like "$prefix*" })) {
                Remove-GPO -Guid $stray.Id -Domain $Domain -Server $dc -Confirm:$false -ErrorAction Stop | Out-Null
            }
        } catch {
            $problems += "stray GPO sweep failed: $($_.Exception.Message)"
        }

        # WMI filters after the GPOs that referenced them.
        foreach ($filterDn in $created.wmi_filters) {
            try {
                Remove-ADObject -Identity $filterDn -Server $dc -Confirm:$false -ErrorAction Stop
            } catch {
                $problems += "WMI filter delete failed for ${filterDn}: $($_.Exception.Message)"
            }
        }
        # Groups live in the parent OU, so they go before it. No -Recursive
        # anywhere: a recursive delete would hide the leftovers the residual
        # check exists to find.
        foreach ($groupDn in $created.groups) {
            try {
                Remove-ADGroup -Identity $groupDn -Server $dc -Confirm:$false -ErrorAction Stop
            } catch {
                $problems += "group delete failed for ${groupDn}: $($_.Exception.Message)"
            }
        }
        $reversed = @($created.ous)
        [array]::Reverse($reversed)
        foreach ($ouDn in $reversed) {
            try {
                Remove-ADOrganizationalUnit -Identity $ouDn -Server $dc -Recursive:$false `
                    -Confirm:$false -ErrorAction Stop
            } catch {
                $problems += "OU delete failed for ${ouDn}: $($_.Exception.Message)"
            }
        }

        # Prove the teardown by re-query; "we issued the delete" is not cleanup.
        foreach ($gpo in $created.gpos) {
            try {
                if (-not (Test-GpoAbsent -Id ([guid]$gpo.id))) { $residual.surviving_gpos += [string]$gpo.name }
            } catch {
                $problems += "could not confirm $($gpo.name) was deleted: $($_.Exception.Message)"
            }
        }
        try {
            foreach ($stray in @(Get-GPO -All -Domain $Domain -Server $dc -ErrorAction Stop |
                    Where-Object { $_.DisplayName -like "$prefix*" })) {
                $residual.surviving_gpos += [string]$stray.DisplayName
            }
        } catch {
            $problems += "could not sweep for surviving GPOs: $($_.Exception.Message)"
        }
        foreach ($filterDn in $created.wmi_filters) {
            try {
                if (Test-AdObjectExists -Identity $filterDn -Server $dc) { $residual.surviving_wmi_filters += $filterDn }
            } catch { $problems += "could not re-query ${filterDn}: $($_.Exception.Message)" }
        }
        foreach ($groupDn in $created.groups) {
            try {
                if (Test-AdObjectExists -Identity $groupDn -Server $dc) { $residual.surviving_groups += $groupDn }
            } catch { $problems += "could not re-query ${groupDn}: $($_.Exception.Message)" }
        }
        foreach ($ouDn in $created.ous) {
            try {
                if (Test-AdObjectExists -Identity $ouDn -Server $dc) { $residual.surviving_ous += $ouDn }
            } catch { $problems += "could not re-query ${ouDn}: $($_.Exception.Message)" }
        }
        # The domain root is the one scope outside the disposable tree; read its
        # raw gPLink rather than infer from the GPOs being gone.
        if ($domainDn) {
            try {
                $rootLinks = [string](Flatten (Get-ADObject -Identity $domainDn -Properties gPLink `
                            -Server $dc -ErrorAction Stop).gPLink)
                foreach ($id in $ownIds) {
                    if ($rootLinks -match [regex]::Escape("$id")) { $residual.surviving_links += "$id @ $domainDn" }
                }
            } catch {
                $problems += "could not re-query links at ${domainDn}: $($_.Exception.Message)"
            }
        }
    } else {
        $problems += 'no domain controller resolved; nothing was created and nothing could be verified'
    }

    $survivors = 0
    foreach ($key in @($residual.Keys)) { $survivors += @($residual[$key]).Count }
    $result.cleanup = [ordered]@{ problems = @($problems); residual = $residual }
    $result.cleanup_state_restored = ($null -ne $dc) -and ($survivors -eq 0)
    $result.cleanup_succeeded = $result.cleanup_state_restored -and (@($problems).Count -eq 0)
    $json = $result | ConvertTo-Json -Depth 10
    Set-Content -LiteralPath (Join-Path $work 'result.json') -Value $json -Encoding UTF8
}

$allSucceeded = $true
foreach ($name in $operationNames) { if (-not $operations[$name].succeeded) { $allSucceeded = $false } }
if (-not ($allSucceeded -and $result.cleanup_succeeded -and $null -eq $result.error)) {
    throw "lifecycle lane failed: $($result.error)"
}
