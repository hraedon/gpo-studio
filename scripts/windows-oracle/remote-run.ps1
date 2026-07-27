# Plan 033 WP-0/WP-2 - run an evidence harness as a privileged domain identity.
#
# GroupPolicy cmdlets need a full logon token; an SSH non-interactive session
# cannot delegate credentials to AD (the double-hop problem).  This script
# creates a one-shot scheduled task that runs run-evidence.ps1 as the supplied
# identity (e.g. svc-da) with a real logon token, waits for it to finish, and
# reports the result.  Deployed and invoked by run-windows-oracle.sh.
#
# The password is received as a parameter (never read from disk or environment
# on the host) and is only held in memory for the task registration.

param(
    [Parameter(Mandatory = $true)][string]$Upn,
    [Parameter(Mandatory = $true)][string]$Pw,
    [Parameter(Mandatory = $false)][string]$FailFlag = "",
    [Parameter(Mandatory = $false)][ValidateSet('wp0', 'wp2')][string]$Harness = 'wp0'
)

$taskName = "GPOStudioOracle-$Harness"
schtasks.exe /Delete /TN $taskName /F 2>$null | Out-Null

if ($Harness -eq 'wp2') {
    $tr = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:\gpo-studio\scripts\run-wp2-import.ps1 -CandidateZip C:\gpo-studio\scripts\candidate.zip -ExpectedPath C:\gpo-studio\scripts\expected.json -OutputDir C:\gpo-studio\out"
} else {
    $tr = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:\gpo-studio\scripts\run-evidence.ps1 -RecipePath C:\gpo-studio\scripts\recipe.json -OutputDir C:\gpo-studio\out $FailFlag"
}
schtasks.exe /Create /TN $taskName /TR $tr /SC ONCE /ST 23:59 /RU $Upn /RP $Pw /RL HIGHEST /F | Out-Null
schtasks.exe /Run /TN $taskName | Out-Null

Start-Sleep -Seconds 2
$deadline = (Get-Date).AddMinutes(6)
$state = "Running"
while ((Get-Date) -lt $deadline) {
    $state = (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue).State
    if ($state -ne "Running") { break }
    Start-Sleep -Seconds 3
}

$info = Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction SilentlyContinue
"TASK_LAST_RESULT=$($info.LastTaskResult)"
"TASK_STATE=$state"
schtasks.exe /Delete /TN $taskName /F 2>$null | Out-Null
