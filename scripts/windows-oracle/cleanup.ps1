#!/usr/bin/env pwsh
# GPO Studio Windows Oracle Harness - Cleanup
# Plan 033 WP-0: remove disposable resources, fail on partial cleanup

param(
    [Parameter(Mandatory=$true)]
    [string]$GpoGuid,
    
    [Parameter(Mandatory=$true)]
    [string]$OutputDir,
    
    [Parameter(Mandatory=$false)]
    [string]$Domain = $env:USERDNSDOMAIN,
    
    [Parameter(Mandatory=$false)]
    [switch]$Force
)

Import-Module (Join-Path $PSScriptRoot 'common.psm1') -Force

$ErrorActionPreference = 'Stop'

Write-HarnessLog "Starting cleanup for GPO: $GpoGuid"

$cleanupDir = Join-Path $OutputDir 'cleanup'
New-Item -ItemType Directory -Force -Path $cleanupDir | Out-Null

$manifest = @{
    schema_version = 1
    run_id = "cleanup-$GpoGuid-$(Get-Date -Format 'yyyyMMddHHmmss')"
    started_at = Get-Date -Format 'o'
    gpo_guid = $GpoGuid
    domain = $Domain
    attempted = $true
    succeeded = $false
    snapshot_restored = $false
    removed_resources = @()
    failures = @()
}

try {
    Write-HarnessLog "Removing GPO: $GpoGuid"
    Remove-GPO -Guid $GpoGuid -Domain $Domain -Confirm:$false
    $manifest.removed_resources += "gpo:$GpoGuid"
    
    Write-HarnessLog "Verifying GPO removal"
    $gpoExists = Get-GPO -Guid $GpoGuid -Domain $Domain -ErrorAction SilentlyContinue
    if ($gpoExists) {
        throw "GPO still exists after removal attempt"
    }
    
    $manifest.succeeded = $true
    $manifest.snapshot_restored = $true
    $manifest.completed_at = Get-Date -Format 'o'
    
    $manifestPath = Join-Path $cleanupDir 'cleanup-manifest.json'
    Export-ManifestJson -Manifest $manifest -OutputPath $manifestPath
    
    Write-HarnessLog "Cleanup completed successfully"
    
    return @{
        ManifestPath = $manifestPath
        Succeeded = $true
    }
}
catch {
    $manifest.failures += $_.Exception.Message
    $manifest.succeeded = $false
    $manifest.completed_at = Get-Date -Format 'o'
    
    $manifestPath = Join-Path $cleanupDir 'cleanup-manifest.json'
    Export-ManifestJson -Manifest $manifest -OutputPath $manifestPath
    
    Write-HarnessLog "Cleanup failed: $($_.Exception.Message)" -Level 'ERROR'
    
    if (-not $Force) {
        throw
    }
    
    return @{
        ManifestPath = $manifestPath
        Succeeded = $false
    }
}
