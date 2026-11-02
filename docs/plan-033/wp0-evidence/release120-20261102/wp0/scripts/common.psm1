# GPO Studio Windows Oracle Harness - Common Module
# Plan 033 WP-0: setup/collect/cleanup harness for external-oracle validation
#
# The harness captures genuine command evidence (real stdout/stderr/exit codes)
# and never fabricates hashes.  Source provenance and semantic comparison are
# NOT computed here; they are owned by finalize_oracle_run.py, which runs where
# the git repository and the XML normalizer live.

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

function Get-FileSha256 {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    return (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLowerInvariant()
}

function New-CommandEvidence {
    <#
        Build a command-evidence record from the stdout/stderr files a command
        tee'd during its (single) real execution.  The harness writes each
        command's genuine output to "<id>.stdout.txt" / "<id>.stderr.txt" via
        Tee-Object; this function hashes them honestly.  It never re-runs the
        command and never fabricates a hash.
    #>
    param(
        [Parameter(Mandatory)][string]$CommandId,
        [Parameter(Mandatory)][string]$CommandLine,
        [Parameter(Mandatory)][int]$ExitCode,
        [Parameter(Mandatory)][string]$CommandDir
    )

    $stdoutPath = Join-Path $CommandDir "$CommandId.stdout.txt"
    $stderrPath = Join-Path $CommandDir "$CommandId.stderr.txt"
    if (-not (Test-Path $stdoutPath)) { New-Item -ItemType File -Force -Path $stdoutPath | Out-Null }
    if (-not (Test-Path $stderrPath)) { New-Item -ItemType File -Force -Path $stderrPath | Out-Null }

    return @{
        command_id         = $CommandId
        command_line       = $CommandLine
        exit_code          = $ExitCode
        stdout_sha256      = (Get-FileSha256 -Path $stdoutPath)
        stderr_sha256      = (Get-FileSha256 -Path $stderrPath)
        relevant_event_ids = @()
    }
}

function Export-ManifestJson {
    param(
        [hashtable]$Manifest,
        [string]$OutputPath
    )
    $Manifest | ConvertTo-Json -Depth 100 | Out-File -FilePath $OutputPath -Encoding UTF8
    Write-HarnessLog "Manifest written to $OutputPath"
}
