# GPO Studio Windows Oracle Harness - Common Module
# Plan 033 WP-0: setup/collect/cleanup harness for external-oracle validation

$ErrorActionPreference = 'Stop'

function New-DisposableName {
    param([string]$Prefix)
    $timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $random = Get-Random -Minimum 1000 -Maximum 9999
    return "${Prefix}-${timestamp}-${random}"
}

function Write-HarnessLog {
    param([string]$Message, [string]$Level = 'INFO')
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Write-Host "[$timestamp] [$Level] $Message"
}

function Test-CommandExists {
    param([string]$Command)
    return [bool](Get-Command $Command -ErrorAction SilentlyContinue)
}

function Invoke-CommandWithEvidence {
    param(
        [string]$CommandId,
        [scriptblock]$ScriptBlock,
        [string]$OutputDir
    )
    
    $stdoutPath = Join-Path $OutputDir "$CommandId.stdout.txt"
    $stderrPath = Join-Path $OutputDir "$CommandId.stderr.txt"
    
    try {
        $result = & $ScriptBlock 2>$stderrPath | Tee-Object -FilePath $stdoutPath
        $exitCode = 0
    }
    catch {
        $exitCode = 1
        $_.Exception.Message | Out-File -FilePath $stderrPath
        throw
    }
    
    return @{
        CommandId = $CommandId
        ExitCode = $exitCode
        StdoutPath = $stdoutPath
        StderrPath = $stderrPath
    }
}

function Get-FileSha256 {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    return (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLowerInvariant()
}

function Export-ManifestJson {
    param(
        [hashtable]$Manifest,
        [string]$OutputPath
    )
    $Manifest | ConvertTo-Json -Depth 100 | Out-File -FilePath $OutputPath -Encoding UTF8
    Write-HarnessLog "Manifest written to $OutputPath"
}
