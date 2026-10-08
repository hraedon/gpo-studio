#!/usr/bin/env pwsh
# Plan 034, network_security exit: a one-off NATIVE capture of Windows Firewall
# policy authored into a GPO. This is a capture, not a lane: it exists so the
# firewall codec is written against bytes Windows wrote, and so the lane that
# follows knows what to assert. Nothing here is a verdict.
#
# Authors a representative tranche of firewall rules and per-profile settings
# into ONE disposable, unlinked GPO with the NetSecurity cmdlets'
# -PolicyStore parameter, reads it back through the same cmdlets, takes a
# Backup-GPO and a Get-GPOReport, copies the GPO's Registry.pol bytes and
# extension lists, and removes the GPO. The local persistent store is checked
# before and after so the capture provably never touched the host's own rules.
#
# All addresses, ports, names and paths are synthetic (RFC 5737 / RFC 3849).
param(
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [string]$Domain = $env:USERDNSDOMAIN
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$runId = "firewall-capture-$(Get-Date -Format yyyyMMddHHmmss)-$(Get-Random -Minimum 1000 -Maximum 9999)"
$work = Join-Path $OutputDir $runId
New-Item -ItemType Directory -Force -Path $work | Out-Null
$cs = Get-CimInstance Win32_ComputerSystem
$os = Get-CimInstance Win32_OperatingSystem
$Domain = if ([string]::IsNullOrWhiteSpace($Domain)) { "$($cs.Domain)" } else { $Domain }
$target = "zz-studio-fw-$runId"
$store = "$Domain\$target"
$prefix = 'StudioFwProbe'

function Flatten($value) {
    if ($null -eq $value) { return '' }
    return [string]::Join(';', @($value | ForEach-Object { "$_" }))
}

$result = [ordered]@{
    schema_version = 1
    run_id = $runId
    target_name = $target
    environment = [ordered]@{
        server_build = "$($os.BuildNumber)"
        computer_system_domain_role = [int]$cs.DomainRole
        powershell_version = "$($PSVersionTable.PSVersion)"
    }
    authoring = @()
    authoring_errors = @()
    profiles_authored = @()
    rules_readback = @()
    profiles_readback = @()
    registry_pol_base64 = $null
    ad_attributes = $null
    persistent_store_matches_before = $null
    persistent_store_matches_after = $null
    cleanup_state_restored = $false
    error = $null
}

# Each rule is authored with the cmdlet vocabulary an operator would use. The
# set deliberately spans every enum value the model offers plus the ones it
# does not (keyword ports, LocalSubnet, ICMP type:code, edge traversal,
# interface type, OverrideBlockRules, disabled rules).
$rules = @(
    @{ Name = "$prefix-01"; DisplayName = "$prefix in tcp allow"; Direction = 'Inbound'; Action = 'Allow'; Protocol = 'TCP'; LocalPort = '65001'; Profile = 'Domain' },
    @{ Name = "$prefix-02"; DisplayName = "$prefix out udp block range"; Direction = 'Outbound'; Action = 'Block'; Protocol = 'UDP'; RemotePort = @('65002-65003', '65010'); Profile = @('Domain', 'Private') },
    @{ Name = "$prefix-03"; DisplayName = "$prefix icmpv4 echo"; Direction = 'Inbound'; Action = 'Allow'; Protocol = 'ICMPv4'; IcmpType = '8:0'; Profile = 'Any' },
    @{ Name = "$prefix-04"; DisplayName = "$prefix icmpv6 any"; Direction = 'Inbound'; Action = 'Allow'; Protocol = 'ICMPv6' },
    @{ Name = "$prefix-05"; DisplayName = "$prefix any protocol addresses"; Direction = 'Inbound'; Action = 'Block'; RemoteAddress = @('192.0.2.0/24', '2001:db8::/32'); LocalAddress = '198.51.100.7' },
    @{ Name = "$prefix-06"; DisplayName = "$prefix program"; Direction = 'Outbound'; Action = 'Allow'; Program = '%ProgramFiles%\StudioProbe\probe.exe'; Protocol = 'TCP'; RemotePort = '443' },
    @{ Name = "$prefix-07"; DisplayName = "$prefix service"; Direction = 'Inbound'; Action = 'Allow'; Service = 'GPOStudioProbe'; Protocol = 'TCP'; LocalPort = '65004' },
    @{ Name = "$prefix-08"; DisplayName = "$prefix rpc keyword"; Direction = 'Inbound'; Action = 'Allow'; Protocol = 'TCP'; LocalPort = 'RPC'; RemoteAddress = 'LocalSubnet' },
    @{ Name = "$prefix-09"; DisplayName = "$prefix rpc epmap"; Direction = 'Inbound'; Action = 'Allow'; Protocol = 'TCP'; LocalPort = 'RPCEPMap' },
    @{ Name = "$prefix-10"; DisplayName = "$prefix disabled grouped"; Direction = 'Inbound'; Action = 'Allow'; Protocol = 'TCP'; LocalPort = '65005'; Enabled = 'False'; Group = 'Studio Probe Group'; Description = 'synthetic probe rule' },
    @{ Name = "$prefix-11"; DisplayName = "$prefix edge and interface"; Direction = 'Inbound'; Action = 'Allow'; Protocol = 'UDP'; LocalPort = '65006'; EdgeTraversalPolicy = 'Allow'; InterfaceType = 'Wired' },
    @{ Name = "$prefix-12"; DisplayName = "$prefix protocol number"; Direction = 'Outbound'; Action = 'Block'; Protocol = '47' },
    @{ Name = "$prefix-13"; DisplayName = "$prefix override block"; Direction = 'Inbound'; Action = 'Allow'; Protocol = 'TCP'; LocalPort = '65007'; Authentication = 'Required'; OverrideBlockRules = $true; RemoteMachine = 'D:(A;;CC;;;WD)' }
)

# Per-profile settings use a distinct value in each profile so a codec that
# collapses them into one global setting cannot pass. Public is deliberately
# left unconfigured: its readback is the "not configured" control.
$profiles = @(
    @{ Name = 'Domain'; Enabled = 'True'; DefaultInboundAction = 'Block'; DefaultOutboundAction = 'Allow'; LogBlocked = 'True'; LogAllowed = 'False'; LogFileName = '%systemroot%\system32\LogFiles\Firewall\studio-domain.log'; LogMaxSizeKilobytes = 8192 },
    @{ Name = 'Private'; Enabled = 'True'; DefaultInboundAction = 'Block'; LogAllowed = 'True'; NotifyOnListen = 'False' }
)

try {
    Import-Module NetSecurity, GroupPolicy, ActiveDirectory -ErrorAction Stop
    $result.persistent_store_matches_before = @(Get-NetFirewallRule -PolicyStore PersistentStore -ErrorAction Stop |
        Where-Object { $_.Name -like "$prefix*" -or $_.DisplayName -like "$prefix*" }).Count
    if (@(Get-GPO -All -Domain $Domain | Where-Object { $_.DisplayName -eq $target }).Count -ne 0) {
        throw 'disposable target already exists'
    }
    $gpo = New-GPO -Name $target -Domain $Domain -ErrorAction Stop
    $result.owned_gpo_id = "$($gpo.Id)"

    foreach ($r in $rules) {
        $splat = @{} + $r
        $splat["PolicyStore"] = $store
        try {
            New-NetFirewallRule @splat -ErrorAction Stop | Out-Null
            $result.authoring += [ordered]@{ name = $r.Name; ok = $true }
        } catch {
            $result.authoring += [ordered]@{ name = $r.Name; ok = $false }
            $result.authoring_errors += [ordered]@{ name = $r.Name; error = "$($_.Exception.Message)" }
        }
    }
    foreach ($p in $profiles) {
        $splat = @{} + $p
        $splat["PolicyStore"] = $store
        try {
            Set-NetFirewallProfile @splat -ErrorAction Stop
            $result.profiles_authored += [ordered]@{ name = $p.Name; ok = $true }
        } catch {
            $result.profiles_authored += [ordered]@{ name = $p.Name; ok = $false; error = "$($_.Exception.Message)" }
        }
    }

    foreach ($rule in @(Get-NetFirewallRule -PolicyStore $store -ErrorAction Stop)) {
        $port = $rule | Get-NetFirewallPortFilter
        $addr = $rule | Get-NetFirewallAddressFilter
        $app = $rule | Get-NetFirewallApplicationFilter
        $svc = $rule | Get-NetFirewallServiceFilter
        $ifType = $rule | Get-NetFirewallInterfaceTypeFilter
        $sec = $rule | Get-NetFirewallSecurityFilter
        $result.rules_readback += [ordered]@{
            name = "$($rule.Name)"; display_name = "$($rule.DisplayName)"
            description = "$($rule.Description)"; group = "$($rule.Group)"
            enabled = "$($rule.Enabled)"; direction = "$($rule.Direction)"
            action = "$($rule.Action)"; profile = "$($rule.Profile)"
            edge_traversal = "$($rule.EdgeTraversalPolicy)"
            protocol = "$($port.Protocol)"; local_port = (Flatten $port.LocalPort)
            remote_port = (Flatten $port.RemotePort); icmp_type = (Flatten $port.IcmpType)
            local_address = (Flatten $addr.LocalAddress); remote_address = (Flatten $addr.RemoteAddress)
            program = "$($app.Program)"; service = "$($svc.Service)"
            interface_type = "$($ifType.InterfaceType)"
            authentication = "$($sec.Authentication)"; encryption = "$($sec.Encryption)"
            override_block_rules = "$($sec.OverrideBlockRules)"; remote_machine = "$($sec.RemoteMachines)"
        }
    }
    foreach ($p in @(Get-NetFirewallProfile -PolicyStore $store -ErrorAction Stop)) {
        $result.profiles_readback += [ordered]@{
            name = "$($p.Name)"; enabled = "$($p.Enabled)"
            default_inbound = "$($p.DefaultInboundAction)"; default_outbound = "$($p.DefaultOutboundAction)"
            log_allowed = "$($p.LogAllowed)"; log_blocked = "$($p.LogBlocked)"
            log_ignored = "$($p.LogIgnored)"; log_file = "$($p.LogFileName)"
            log_max_kb = "$($p.LogMaxSizeKilobytes)"; notify_on_listen = "$($p.NotifyOnListen)"
            allow_local_rules = "$($p.AllowLocalFirewallRules)"
        }
    }

    $gpoObj = Get-GPO -Guid $gpo.Id -Domain $Domain
    $adObj = Get-ADObject -Identity "$($gpoObj.Path)" -Properties versionNumber, gPCMachineExtensionNames, gPCUserExtensionNames, gPCFileSysPath
    $sysvol = [string](Flatten $adObj.gPCFileSysPath)
    $result.ad_attributes = [ordered]@{
        versionNumber = [int](Flatten $adObj.versionNumber)
        gPCMachineExtensionNames = [string](Flatten $adObj.gPCMachineExtensionNames)
        gPCUserExtensionNames = [string](Flatten $adObj.gPCUserExtensionNames)
    }
    $files = @()
    foreach ($f in (Get-ChildItem -LiteralPath $sysvol -Recurse -File -Force)) {
        $files += [ordered]@{
            relative_path = [string]$f.FullName.Substring($sysvol.Length).TrimStart('\').Replace('\', '/')
            length = [int]$f.Length
        }
    }
    $result.sysvol_files = @($files)
    $pol = Join-Path $sysvol 'Machine\Registry.pol'
    if (Test-Path -LiteralPath $pol) {
        $result.registry_pol_base64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($pol))
    }
    Get-GPOReport -Guid $gpo.Id -Domain $Domain -ReportType XML -Path (Join-Path $work 'report.xml')
    $backupRoot = Join-Path $work 'backup'
    New-Item -ItemType Directory -Force $backupRoot | Out-Null
    Backup-GPO -Guid $gpo.Id -Domain $Domain -Path $backupRoot | Out-Null
} catch {
    $result.error = "$($_.Exception.Message)"
} finally {
    try {
        if ($result.owned_gpo_id) { Remove-GPO -Guid $result.owned_gpo_id -Domain $Domain -Confirm:$false }
        $remaining = @(Get-GPO -All -Domain $Domain | Where-Object { $_.DisplayName -eq $target })
        $result.persistent_store_matches_after = @(Get-NetFirewallRule -PolicyStore PersistentStore |
            Where-Object { $_.Name -like "$prefix*" -or $_.DisplayName -like "$prefix*" }).Count
        $result.cleanup_state_restored = ($remaining.Count -eq 0)
    } catch {
        $result.error = "$($result.error) | cleanup: $($_.Exception.Message)"
    }
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $work 'capture.json') -Encoding UTF8
    Write-Output $work
}
