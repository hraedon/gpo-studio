#!/usr/bin/env pwsh
# GPO Studio Windows Oracle Harness - Setup
# Plan 033 WP-0: create disposable GPOs and test environment

param(
    [Parameter(Mandatory=$true)]
    [string]$RecipePath,
    
    [Parameter(Mandatory=$true)]
    [string]$OutputDir,
    
    [Parameter(Mandatory=$false)]
    [string]$Domain = $env:USERDNSDOMAIN
)

Import-Module (Join-Path $PSScriptRoot 'common.psm1') -Force

$ErrorActionPreference = 'Stop'

Write-HarnessLog "Starting setup from recipe: $RecipePath"

$recipe = Get-Content $RecipePath -Raw | ConvertFrom-Json
$fixtureId = $recipe.fixture_id
$gpoName = New-DisposableName -Prefix $recipe.gpo_name_prefix

Write-HarnessLog "Fixture ID: $fixtureId"
Write-HarnessLog "Disposable GPO name: $gpoName"

$setupDir = Join-Path $OutputDir 'setup'
New-Item -ItemType Directory -Force -Path $setupDir | Out-Null

$manifest = @{
    schema_version = 1
    run_id = "setup-$fixtureId-$(Get-Date -Format 'yyyyMMddHHmmss')"
    started_at = (Get-Date -Format 'o')
    fixture_id = $fixtureId
    gpo_name = $gpoName
    domain = $Domain
    steps = @()
}

try {
    Write-HarnessLog "Creating GPO: $gpoName"
    $gpo = New-GPO -Name $gpoName -Domain $Domain -Comment "Plan 033 WP-0 disposable fixture"
    
    $manifest.steps += @{
        action = 'create_gpo'
        gpo_guid = $gpo.Id.ToString()
        status = 'success'
    }
    
    Write-HarnessLog "GPO created with GUID: $($gpo.Id)"
    
    $manifest.completed_at = Get-Date -Format 'o'
    $manifest.status = 'success'
    
    $manifestPath = Join-Path $setupDir 'setup-manifest.json'
    Export-ManifestJson -Manifest $manifest -OutputPath $manifestPath
    
    Write-HarnessLog "Setup completed successfully"
    Write-HarnessLog "GPO GUID: $($gpo.Id)"
    
    return @{
        GpoName = $gpoName
        GpoGuid = $gpo.Id.ToString()
        ManifestPath = $manifestPath
    }
}
catch {
    $manifest.completed_at = Get-Date -Format 'o'
    $manifest.status = 'failed'
    $manifest.error = $_.Exception.Message
    
    $manifestPath = Join-Path $setupDir 'setup-manifest.json'
    Export-ManifestJson -Manifest $manifest -OutputPath $manifestPath
    
    Write-HarnessLog "Setup failed: $($_.Exception.Message)" -Level 'ERROR'
    throw
}
