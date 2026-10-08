#!/usr/bin/env pwsh
# Controller safety net, including when the authoring guest job times out.
param(
    [Parameter(Mandatory = $true)][string]$RunId,
    [string]$Domain = $env:USERDNSDOMAIN
)
$ErrorActionPreference = 'Stop'
if ($RunId -notmatch '^firewall-[0-9]{14}-[0-9]+$') { throw 'invalid controller run id' }
Import-Module GroupPolicy -ErrorAction Stop
$prefix = "StudioFwLane-$RunId-"

function Get-OwnedFirewallLaneGpo {
    Get-GPO -All -Domain $Domain -ErrorAction Stop | Where-Object {
        ([string]$_.DisplayName).StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
    }
}

foreach ($gpo in @(Get-OwnedFirewallLaneGpo)) {
    Remove-GPO -Guid $gpo.Id -Domain $Domain -Confirm:$false -ErrorAction Stop
}
if (@(Get-OwnedFirewallLaneGpo).Count -ne 0) { throw 'controller cleanup left owned GPOs' }
