#!/usr/bin/env pwsh
# Plan 034 WP-1: measure what Windows materialises for a GPO whose content
# Studio's publication planner claims to be able to write.
#
# Imports a Studio-produced GMPC backup into one disposable GPO, then reads
# back the two things a publication would have had to produce: the GPO's SYSVOL
# tree, and the directory attributes without which that tree is inert. The
# comparison against the plan's own claim happens on the controller, against a
# hash-bound expectation this script never sees.
param(
    [Parameter(Mandatory = $true)][string]$CandidateZip,
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [string]$Domain = $env:USERDNSDOMAIN
)
$ErrorActionPreference = 'Stop'
$runId = "publication-completeness-$(Get-Date -Format yyyyMMddHHmmss)-$(Get-Random -Minimum 1000 -Maximum 9999)"
$work = Join-Path $OutputDir $runId
$inputRoot = Join-Path $work 'input'
$commands = Join-Path $work 'commands'
New-Item -ItemType Directory -Force -Path $work, $inputRoot, $commands | Out-Null
Copy-Item -LiteralPath $CandidateZip -Destination (Join-Path $work 'candidate.zip')
Expand-Archive -LiteralPath $CandidateZip -DestinationPath $inputRoot

$manifest = [xml](Get-Content (Join-Path $inputRoot 'manifest.xml') -Raw)
$ns = New-Object System.Xml.XmlNamespaceManager($manifest.NameTable)
$ns.AddNamespace('m', 'http://www.microsoft.com/GroupPolicy/GPOOperations/Manifest')
$backupId = [Guid]$manifest.SelectSingleNode('/m:Backups/m:BackupInst/m:ID', $ns).InnerText.Trim('{}')
$sourceId = $manifest.SelectSingleNode('/m:Backups/m:BackupInst/m:GPOGuid', $ns).InnerText

$target = "zz-studio-pub-$runId"
$ownedId = $null
$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem
$Domain = if ([string]::IsNullOrWhiteSpace($Domain)) { "$($cs.Domain)" } else { $Domain }
$gpModule = Get-Module -ListAvailable GroupPolicy | Select-Object -First 1

$result = [ordered]@{
    schema_version         = 1
    run_id                 = $runId
    target_name            = $target
    domain                 = $Domain
    backup_id              = "{$($backupId.ToString().ToUpperInvariant())}"
    source_gpo_id          = $sourceId
    owned_gpo_id           = $null
    import_succeeded       = $false
    report_links_to_count  = $null
    sysvol_path            = $null
    sysvol_files           = @()
    gpt_ini_text           = $null
    ad_attributes          = $null
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
# ConvertTo-Json hangs forever on one rather than failing. Measured 2026-09-07:
# the guest script completed in 2.1 s while the transport waited out its whole
# timeout three times over. Everything read from the directory is flattened to
# a string or an int before it can reach the serializer.
function Flatten($value) {
    if ($null -eq $value) { return '' }
    return [string]::Join(';', @($value))
}

try {
    $collisions = @(Get-GPO -All -Domain $Domain -ErrorAction Stop |
        Where-Object { $_.DisplayName -eq $target })
    if ($collisions.Count -ne 0) { throw 'disposable target already exists' }

    $owned = New-GPO -Name $target -Domain $Domain -ErrorAction Stop
    $ownedId = $owned.Id
    $result.owned_gpo_id = "$ownedId"

    Import-GPO -BackupId $backupId -Path $inputRoot -TargetGuid $ownedId -Domain $Domain `
        -Confirm:$false -ErrorAction Stop 2> (Join-Path $commands 'import.stderr.txt') |
        Tee-Object -FilePath (Join-Path $commands 'import.stdout.txt')
    $result.import_succeeded = $true

    $reportPath = Join-Path $work 'report.xml'
    Get-GPOReport -Guid $ownedId -Domain $Domain -ReportType XML -Path $reportPath `
        -ErrorAction Stop 2> (Join-Path $commands 'report.stderr.txt')
    New-Item -ItemType File -Force (Join-Path $commands 'report.stdout.txt') | Out-Null
    $report = [xml](Get-Content $reportPath -Raw)
    $result.report_links_to_count = @($report.SelectNodes("//*[local-name()='LinksTo']/*")).Count

    Import-Module ActiveDirectory -ErrorAction Stop
    $gpoObj = Get-GPO -Guid $ownedId -Domain $Domain -ErrorAction Stop
    $adObj = Get-ADObject -Identity "$($gpoObj.Path)" -Properties `
        versionNumber, gPCMachineExtensionNames, gPCUserExtensionNames, `
        gPCFileSysPath, gPCFunctionalityVersion, flags -ErrorAction Stop
    $result.ad_attributes = [ordered]@{
        versionNumber            = [int](Flatten $adObj.versionNumber)
        gPCMachineExtensionNames = [string](Flatten $adObj.gPCMachineExtensionNames)
        gPCUserExtensionNames    = [string](Flatten $adObj.gPCUserExtensionNames)
        gPCFileSysPath           = [string](Flatten $adObj.gPCFileSysPath)
        gPCFunctionalityVersion  = [string](Flatten $adObj.gPCFunctionalityVersion)
        flags                    = [string](Flatten $adObj.flags)
        # Get-GPO reports the packed versionNumber's halves separately, which
        # is exactly what update_gpt_ini's version_half claims to move.
        computer_ds_version      = [int]$gpoObj.Computer.DSVersion
        computer_sysvol_version  = [int]$gpoObj.Computer.SysvolVersion
        user_ds_version          = [int]$gpoObj.User.DSVersion
        user_sysvol_version      = [int]$gpoObj.User.SysvolVersion
    }

    # The GPO's SYSVOL path comes from the directory rather than being composed
    # here: a path this script built itself could agree with the plan for the
    # same wrong reason.
    $sysvol = [string](Flatten $adObj.gPCFileSysPath)
    $result.sysvol_path = $sysvol
    $files = @()
    foreach ($f in (Get-ChildItem -LiteralPath $sysvol -Recurse -File -Force -ErrorAction Stop)) {
        $relative = $f.FullName.Substring($sysvol.Length).TrimStart('\')
        $files += [ordered]@{
            relative_path = [string]$relative.Replace('\', '/')
            length        = [int]$f.Length
            sha256        = [string](Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    $result.sysvol_files = @($files | Sort-Object { $_.relative_path })

    $gptIni = Join-Path $sysvol 'GPT.INI'
    if (Test-Path -LiteralPath $gptIni) {
        # Cast: Get-Content decorates its string with PSPath/PSProvider notes,
        # and ConvertTo-Json will happily serialize the entire provider graph.
        $result.gpt_ini_text = [string](Get-Content -LiteralPath $gptIni -Raw)
    }
} catch {
    $result.error = "$($_.Exception.Message)"
} finally {
    try {
        if ($ownedId) { Remove-GPO -Guid $ownedId -Domain $Domain -Confirm:$false -ErrorAction Stop }
        $remaining = @(Get-GPO -All -Domain $Domain -ErrorAction Stop | Where-Object {
            ($ownedId -and $_.Id -eq $ownedId) -or $_.DisplayName -eq $target
        })
        $result.cleanup_state_restored = $remaining.Count -eq 0
        $result.cleanup_succeeded = $result.cleanup_state_restored
    } catch {
        $result.error = (($result.error, "cleanup: $($_.Exception.Message)") -ne $null) -join '; '
    }
    $json = $result | ConvertTo-Json -Depth 6
    Set-Content -LiteralPath (Join-Path $work 'result.json') -Value $json -Encoding UTF8
}
if (-not ($result.import_succeeded -and $result.cleanup_succeeded -and
          $result.cleanup_state_restored)) { throw "publication lane failed: $($result.error)" }
