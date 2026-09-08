#requires -Version 5.1
<#
.SYNOPSIS
    Capture WI-028's computer RSoP WMI and gpresult views without clearing them.
.DESCRIPTION
    Diagnostic only: this is not a conformance verdict or a precedence oracle.
    Use a new output directory for each before/during/after capture. ForceRefresh
    explicitly requests computer policy processing; the default only reads.
    Keep raw output private until reviewed for environment identifiers.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $OutputDirectory,
    [switch] $ForceRefresh
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (Test-Path -LiteralPath $OutputDirectory) {
    throw 'OutputDirectory already exists; retain it and choose a fresh directory.'
}
$null = New-Item -ItemType Directory -Path $OutputDirectory
$started = [DateTime]::UtcNow

function Get-SomSnapshot {
    @(Get-CimInstance -Namespace 'root/rsop/computer' -ClassName RSOP_SOM |
        Select-Object id, reason, type, SOMOrder, blocked, blocking |
        Sort-Object id, reason)
}

if ($ForceRefresh) {
    & gpupdate.exe /force /target:computer /wait:120 2>&1 |
        Out-File -LiteralPath (Join-Path $OutputDirectory 'gpupdate.txt') -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw "gpupdate failed with exit code $LASTEXITCODE" }
}
$before = @(Get-SomSnapshot)
$xmlPath = Join-Path $OutputDirectory 'gpresult.xml'
& gpresult.exe /x $xmlPath /scope:computer 2>&1 |
    Out-File -LiteralPath (Join-Path $OutputDirectory 'gpresult.txt') -Encoding utf8
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $xmlPath)) {
    throw "gpresult did not produce a successful capture (exit $LASTEXITCODE)"
}
$after = @(Get-SomSnapshot)
$os = Get-CimInstance Win32_OperatingSystem
$session = @(Get-CimInstance -Namespace 'root/rsop/computer' -ClassName RSOP_Session |
    Select-Object id, targetName, SOM, Site, creationTime)
$snapshot = [ordered]@{
    schema_version = 1
    started_utc = $started.ToString('o')
    completed_utc = [DateTime]::UtcNow.ToString('o')
    computer = $env:COMPUTERNAME
    os_caption = $os.Caption
    build = $os.BuildNumber
    last_boot_utc = $os.LastBootUpTime.ToUniversalTime().ToString('o')
    forced_refresh = [bool]$ForceRefresh
    namespace = 'root/rsop/computer'
    som_before_gpresult = $before
    som_after_gpresult = $after
    session = $session
}
$snapshot | ConvertTo-Json -Depth 6 |
    Out-File -LiteralPath (Join-Path $OutputDirectory 'snapshot.json') -Encoding utf8
$hashes = @(Get-ChildItem -LiteralPath $OutputDirectory -File | ForEach-Object {
    [ordered]@{ name = $_.Name; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
})
ConvertTo-Json -InputObject $hashes -Depth 3 |
    Out-File -LiteralPath (Join-Path $OutputDirectory 'hashes.json') -Encoding utf8
Write-Output "CAPTURE_DIRECTORY=$OutputDirectory"
