#!/usr/bin/env pwsh
# GPO Studio Windows Oracle Harness - Collect
# Plan 033 WP-0: capture evidence from Windows tooling

param(
    [Parameter(Mandatory=$true)]
    [string]$GpoGuid,
    
    [Parameter(Mandatory=$true)]
    [string]$OutputDir,
    
    [Parameter(Mandatory=$false)]
    [string]$Domain = $env:USERDNSDOMAIN
)

Import-Module (Join-Path $PSScriptRoot 'common.psm1') -Force

$ErrorActionPreference = 'Stop'

Write-HarnessLog "Starting evidence collection for GPO: $GpoGuid"

$collectDir = Join-Path $OutputDir 'collect'
New-Item -ItemType Directory -Force -Path $collectDir | Out-Null

$backupDir = Join-Path $collectDir 'backup'
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

$manifest = @{
    schema_version = 1
    run_id = "collect-$GpoGuid-$(Get-Date -Format 'yyyyMMddHHmmss')"
    started_at = Get-Date -Format 'o'
    gpo_guid = $GpoGuid
    domain = $Domain
    commands = @()
    artifacts = @()
}

try {
    Write-HarnessLog "Running Backup-GPO"
    $backupCmd = {
        Backup-GPO -Guid $GpoGuid -Path $backupDir -Domain $Domain
    }
    $evidence = Invoke-CommandWithEvidence -CommandId 'backup-gpo' -ScriptBlock $backupCmd -OutputDir $collectDir
    $manifest.commands += $evidence
    
    $backupFiles = Get-ChildItem -Path $backupDir -Recurse -File
    foreach ($file in $backupFiles) {
        $relativePath = $file.FullName.Substring($backupDir.Length).TrimStart('\')
        $manifest.artifacts += @{
            artifact_id = "backup-$($file.Name)"
            role = 'output'
            relative_path = "backup\$relativePath"
            sha256 = Get-FileSha256 -Path $file.FullName
            size_bytes = $file.Length
        }
    }
    
    Write-HarnessLog "Running Get-GPOReport"
    $reportPath = Join-Path $collectDir 'gpreport.xml'
    Get-GPOReport -Guid $GpoGuid -ReportType XML -Path $reportPath -Domain $Domain
    $manifest.artifacts += @{
        artifact_id = 'gpreport'
        role = 'output'
        relative_path = 'gpreport.xml'
        sha256 = Get-FileSha256 -Path $reportPath
        size_bytes = (Get-Item $reportPath).Length
    }
    
    $manifest.completed_at = Get-Date -Format 'o'
    $manifest.status = 'success'
    
    $manifestPath = Join-Path $collectDir 'collect-manifest.json'
    Export-ManifestJson -Manifest $manifest -OutputPath $manifestPath
    
    Write-HarnessLog "Collection completed successfully"
    
    return @{
        ManifestPath = $manifestPath
        BackupDir = $backupDir
    }
}
catch {
    $manifest.completed_at = Get-Date -Format 'o'
    $manifest.status = 'failed'
    $manifest.error = $_.Exception.Message
    
    $manifestPath = Join-Path $collectDir 'collect-manifest.json'
    Export-ManifestJson -Manifest $manifest -OutputPath $manifestPath
    
    Write-HarnessLog "Collection failed: $($_.Exception.Message)" -Level 'ERROR'
    throw
}
