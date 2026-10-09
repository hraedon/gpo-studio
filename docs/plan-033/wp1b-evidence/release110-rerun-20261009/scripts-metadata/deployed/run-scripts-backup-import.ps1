#!/usr/bin/env pwsh
# Plan 034 R10: import Scripts metadata, report it, and back it up again.
param(
    [Parameter(Mandatory = $true)][string]$CandidateZip,
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [string]$Domain = $env:USERDNSDOMAIN
)
$ErrorActionPreference = 'Stop'
$runId = "scripts-r10-$(Get-Date -Format yyyyMMddHHmmss)-$(Get-Random -Minimum 1000 -Maximum 9999)"
$work = Join-Path $OutputDir $runId
$inputRoot = Join-Path $work 'input'
$rebackup = Join-Path $work 'rebackup'
$commands = Join-Path $work 'commands'
New-Item -ItemType Directory -Force -Path $work, $inputRoot, $rebackup, $commands | Out-Null
Copy-Item -LiteralPath $CandidateZip -Destination (Join-Path $work 'candidate.zip')
Expand-Archive -LiteralPath $CandidateZip -DestinationPath $inputRoot
$manifest = [xml](Get-Content (Join-Path $inputRoot 'manifest.xml') -Raw)
$ns = New-Object System.Xml.XmlNamespaceManager($manifest.NameTable)
$ns.AddNamespace('m', 'http://www.microsoft.com/GroupPolicy/GPOOperations/Manifest')
$backupId = [Guid]$manifest.SelectSingleNode('/m:Backups/m:BackupInst/m:ID', $ns).InnerText.Trim('{}')
$sourceId = $manifest.SelectSingleNode('/m:Backups/m:BackupInst/m:GPOGuid', $ns).InnerText
$target = "zz-studio-r10-$runId"
$ownedId = $null
$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem
$Domain = if ([string]::IsNullOrWhiteSpace($Domain)) { "$($cs.Domain)" } else { $Domain }
$gpModule = Get-Module -ListAvailable GroupPolicy | Select-Object -First 1
$result = [ordered]@{
    schema_version = 1; run_id = $runId; target_name = $target; domain = $Domain
    backup_id = "{$($backupId.ToString().ToUpperInvariant())}"; source_gpo_id = $sourceId
    owned_gpo_id = $null; import_succeeded = $false; report_succeeded = $false
    report_links_to_count = $null; rebackup_succeeded = $false
    cleanup_succeeded = $false; cleanup_state_restored = $false
    environment = [ordered]@{
        server_caption = "$($os.Caption)"; server_build = "$($os.BuildNumber)"
        computer_system_domain_role = [int]$cs.DomainRole
        powershell_edition = "$($PSVersionTable.PSEdition)"
        powershell_version = "$($PSVersionTable.PSVersion)"
        group_policy_module_version = if ($gpModule) { "$($gpModule.Version)" } else { 'unknown' }
        gpmc_version = 'built-in'; locale = (Get-Culture).Name
        computer_system_name = "$($cs.Name)"
        computer_system_domain = "$($cs.Domain)"
    }
    error = $null
}
try {
    $collisions = @(Get-GPO -All -Domain $Domain -ErrorAction Stop | Where-Object { $_.DisplayName -eq $target })
    if ($collisions.Count -ne 0) { throw 'disposable target already exists' }
    $owned = New-GPO -Name $target -Domain $Domain -Comment 'Plan 034 R10 disposable metadata oracle' -ErrorAction Stop
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
    $links = @($report.SelectNodes("//*[local-name()='LinksTo']/*"))
    $result.report_links_to_count = $links.Count
    if ($links.Count -ne 0) { throw 'disposable target unexpectedly has links' }
    $result.report_succeeded = $true
    Backup-GPO -Guid $ownedId -Domain $Domain -Path $rebackup `
        -Comment 'Plan 034 R10 metadata oracle' -ErrorAction Stop `
        2> (Join-Path $commands 'backup.stderr.txt') |
        Out-File (Join-Path $commands 'backup.stdout.txt')
    $result.rebackup_succeeded = $true
} catch { $result.error = "$($_.Exception.Message)" } finally {
    try {
        if ($ownedId) { Remove-GPO -Guid $ownedId -Domain $Domain -Confirm:$false -ErrorAction Stop }
        $remaining = @(Get-GPO -All -Domain $Domain -ErrorAction Stop | Where-Object {
            ($ownedId -and $_.Id -eq $ownedId) -or $_.DisplayName -eq $target
        })
        $result.cleanup_state_restored = $remaining.Count -eq 0
        $result.cleanup_succeeded = $result.cleanup_state_restored
    } catch { $result.error = (($result.error, "cleanup: $($_.Exception.Message)") -ne $null) -join '; ' }
    $result | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $work 'result.json') -Encoding UTF8
}
if (-not ($result.import_succeeded -and $result.report_succeeded -and
          $result.rebackup_succeeded -and $result.cleanup_succeeded -and
          $result.cleanup_state_restored)) { throw "R10 failed: $($result.error)" }
