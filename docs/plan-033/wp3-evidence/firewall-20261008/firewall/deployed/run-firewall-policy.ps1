#!/usr/bin/env pwsh
# Disposable, unlinked GPO author/read and Studio import/read. PowerShell 5.1.
param(
    [Parameter(Mandatory = $true)][string]$CandidateZip,
    [Parameter(Mandatory = $true)][string]$AuthoringJson,
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [Parameter(Mandatory = $true)][string]$RunId,
    [string]$Domain = $env:USERDNSDOMAIN
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ($RunId -notmatch '^firewall-[0-9]{14}-[0-9]+$') { throw 'invalid controller run id' }
$work = Join-Path $OutputDir $runId
$commands = Join-Path $work 'commands'
$inputRoot = Join-Path $work 'input'
New-Item -ItemType Directory -Force -Path $work, $commands, $inputRoot | Out-Null
$cs = Get-CimInstance Win32_ComputerSystem
$os = Get-CimInstance Win32_OperatingSystem
$Domain = if ([string]::IsNullOrWhiteSpace($Domain)) { "$($cs.Domain)" } else { $Domain }
$gpModule = Get-Module -ListAvailable GroupPolicy | Select-Object -First 1
$result = [ordered]@{
    schema_version = 1
    run_id = $runId
    domain = $Domain
    read_leg = $null
    write_leg = $null
    operations = @()
    persistent_before = $null
    persistent_after = $null
    cleanup_verified = $false
    cleanup_remaining = $null
    environment = [ordered]@{
        server_caption = "$($os.Caption)"
        server_build = "$($os.BuildNumber)"
        computer_system_domain_role = [int]$cs.DomainRole
        powershell_edition = "$($PSVersionTable.PSEdition)"
        powershell_version = "$($PSVersionTable.PSVersion)"
        group_policy_module_version = if ($gpModule) { "$($gpModule.Version)" } else { 'unknown' }
        gpmc_version = 'built-in'
        locale = (Get-Culture).Name
        computer_system_name = "$($cs.Name)"
        computer_system_domain = "$($cs.Domain)"
    }
    error = $null
}
$intended = @{}
$targets = @{ read = "StudioFwLane-$runId-read"; write = "StudioFwLane-$runId-write" }

function ConvertTo-FlatString($value) {
    if ($null -eq $value) { return '' }
    return [string]::Join(';', @($value | ForEach-Object { "$_" }))
}

# Each operation has separate stdout/stderr and a recorded effective PolicyStore.
# Filter cmdlets receive InputObject from the recorded store, not an implicit store.
function Invoke-LaneCommand($commandName, $operationLeg, $operationStore, $operationSubject, [scriptblock]$commandBlock) {
    # Unique local names preserve the caller's GPO $id/$name inside the block.
    $operationId = '{0:D4}-{1}' -f $result.operations.Count, $commandName
    $operationStdout = "$operationId.stdout.txt"
    $operationStderr = "$operationId.stderr.txt"
    $operationEntry = [ordered]@{ name = $commandName; leg = $operationLeg; policy_store = $operationStore;
        subject = $operationSubject; ok = $false; stdout = $operationStdout; stderr = $operationStderr }
    $result.operations += $operationEntry
    New-Item -ItemType File -Force -Path (Join-Path $commands $operationStdout), (Join-Path $commands $operationStderr) | Out-Null
    try {
        $commandOutput = @(& $commandBlock 2> (Join-Path $commands $operationStderr))
        $commandOutput | Out-String | Set-Content -LiteralPath (Join-Path $commands $operationStdout) -Encoding UTF8
        $operationEntry.ok = $true
        return $commandOutput
    } catch {
        [string]$_.Exception.Message | Add-Content -LiteralPath (Join-Path $commands $operationStderr)
        throw
    }
}

function Get-LaneObservation($leg, $id, $store, $legResult) {
    foreach ($rule in @(Invoke-LaneCommand 'Get-NetFirewallRule' $leg $store '' { Get-NetFirewallRule -PolicyStore $store -ErrorAction Stop })) {
        $port = Invoke-LaneCommand 'Get-NetFirewallPortFilter' $leg $store ([string]$rule.Name) { $rule | Get-NetFirewallPortFilter -ErrorAction Stop }
        $addr = Invoke-LaneCommand 'Get-NetFirewallAddressFilter' $leg $store ([string]$rule.Name) { $rule | Get-NetFirewallAddressFilter -ErrorAction Stop }
        $app = Invoke-LaneCommand 'Get-NetFirewallApplicationFilter' $leg $store ([string]$rule.Name) { $rule | Get-NetFirewallApplicationFilter -ErrorAction Stop }
        $svc = Invoke-LaneCommand 'Get-NetFirewallServiceFilter' $leg $store ([string]$rule.Name) { $rule | Get-NetFirewallServiceFilter -ErrorAction Stop }
        $ifType = Invoke-LaneCommand 'Get-NetFirewallInterfaceTypeFilter' $leg $store ([string]$rule.Name) { $rule | Get-NetFirewallInterfaceTypeFilter -ErrorAction Stop }
        $iface = Invoke-LaneCommand 'Get-NetFirewallInterfaceFilter' $leg $store ([string]$rule.Name) { $rule | Get-NetFirewallInterfaceFilter -ErrorAction Stop }
        $sec = Invoke-LaneCommand 'Get-NetFirewallSecurityFilter' $leg $store ([string]$rule.Name) { $rule | Get-NetFirewallSecurityFilter -ErrorAction Stop }
        $legResult.rules_readback += [ordered]@{
            name = "$($rule.Name)"; display_name = "$($rule.DisplayName)"
            description = "$($rule.Description)"; group = "$($rule.Group)"
            enabled = "$($rule.Enabled)"; direction = "$($rule.Direction)"
            action = "$($rule.Action)"; profile = "$($rule.Profile)"
            edge_traversal = "$($rule.EdgeTraversalPolicy)"
            protocol = "$($port.Protocol)"; local_port = (ConvertTo-FlatString $port.LocalPort)
            remote_port = (ConvertTo-FlatString $port.RemotePort); icmp_type = (ConvertTo-FlatString $port.IcmpType)
            local_address = (ConvertTo-FlatString $addr.LocalAddress); remote_address = (ConvertTo-FlatString $addr.RemoteAddress)
            program = "$($app.Program)"; service = "$($svc.Service)"
            interface_type = "$($ifType.InterfaceType)"
            interface_alias = (ConvertTo-FlatString $iface.InterfaceAlias)
            authentication = "$($sec.Authentication)"; encryption = "$($sec.Encryption)"
            override_block_rules = "$($sec.OverrideBlockRules)"; remote_machine = "$($sec.RemoteMachines)"
        }
    }
    foreach ($p in @(Invoke-LaneCommand 'Get-NetFirewallProfile' $leg $store '' { Get-NetFirewallProfile -PolicyStore $store -ErrorAction Stop })) {
        $legResult.profiles_readback += [ordered]@{
            name = "$($p.Name)"; enabled = "$($p.Enabled)"
            default_inbound = "$($p.DefaultInboundAction)"; default_outbound = "$($p.DefaultOutboundAction)"
            log_allowed = "$($p.LogAllowed)"; log_blocked = "$($p.LogBlocked)"
            log_ignored = "$($p.LogIgnored)"; log_file = "$($p.LogFileName)"
            log_max_kb = "$($p.LogMaxSizeKilobytes)"; notify_on_listen = "$($p.NotifyOnListen)"
            allow_local_rules = "$($p.AllowLocalFirewallRules)"
        }
    }

    $gpoObj = Invoke-LaneCommand 'Get-GPO' $leg $null "$id" { Get-GPO -Guid $id -Domain $Domain -ErrorAction Stop }
    $adObj = Invoke-LaneCommand 'Get-ADObject' $leg $null "$id" {
        Get-ADObject -Identity "$($gpoObj.Path)" -Properties gPCFileSysPath, gPCMachineExtensionNames, gPCUserExtensionNames -ErrorAction Stop
    }
    $sysvol = ConvertTo-FlatString $adObj.gPCFileSysPath
    $legResult.ad_attributes = [ordered]@{
        gPCMachineExtensionNames = ConvertTo-FlatString $adObj.gPCMachineExtensionNames
        gPCUserExtensionNames = ConvertTo-FlatString $adObj.gPCUserExtensionNames
        gPCFileSysPath = [string]$sysvol
    }
    $legResult.registry_pol_base64 = Invoke-LaneCommand 'Read-RegistryPol' $leg $null "$id" {
        [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $sysvol 'Machine\Registry.pol')))
    }
    $reportPath = Join-Path $work "$leg-report.xml"
    Invoke-LaneCommand 'Get-GPOReport' $leg $null "$id" {
        Get-GPOReport -Guid $id -Domain $Domain -ReportType XML -Path $reportPath -ErrorAction Stop
    } | Out-Null
    $reportText = [string](Get-Content -LiteralPath $reportPath -Raw)
    $report = [xml]$reportText
    $legResult.report_links_to_count = @($report.SelectNodes("//*[local-name()='LinksTo']")).Count
    $legResult.report_xml = $reportText
}

try {
    Import-Module NetSecurity, GroupPolicy, ActiveDirectory -ErrorAction Stop
    if ([int]$cs.DomainRole -ne 3 -or "$($cs.Name)" -ine 'LabMS01') { throw 'requires LabMS01 member server' }
    Copy-Item -LiteralPath $CandidateZip -Destination (Join-Path $work 'candidate.zip')
    Copy-Item -LiteralPath $AuthoringJson -Destination (Join-Path $work 'authoring.json')
    $author = Get-Content -LiteralPath $AuthoringJson -Raw | ConvertFrom-Json
    if ($author.schema_version -ne 1 -or @($author.rules).Count -ne 13 -or @($author.profiles).Count -ne 2) {
        throw 'unexpected authoring schema/tranche'
    }
    Expand-Archive -LiteralPath $CandidateZip -DestinationPath $inputRoot
    $manifest = [xml](Get-Content (Join-Path $inputRoot 'manifest.xml') -Raw)
    $ns = New-Object System.Xml.XmlNamespaceManager($manifest.NameTable)
    $ns.AddNamespace('m', 'http://www.microsoft.com/GroupPolicy/GPOOperations/Manifest')
    $backupId = [Guid]$manifest.SelectSingleNode('/m:Backups/m:BackupInst/m:ID', $ns).InnerText.Trim('{}')
    $result.persistent_before = @(Invoke-LaneCommand 'Get-NetFirewallRule' 'before' 'PersistentStore' '' {
        Get-NetFirewallRule -PolicyStore PersistentStore -ErrorAction Stop | Where-Object {
            $_.Name -like 'StudioFwLane*' -or $_.DisplayName -like 'StudioFwLane*'
        } | ForEach-Object { [string]$_.Name }
    })
    if ($result.persistent_before.Count -ne 0) { throw 'PersistentStore already holds lane rules' }
    foreach ($leg in @('read', 'write')) {
        $name = $targets[$leg]
        $store = "$Domain\$name"
        $collisions = @(Invoke-LaneCommand 'Get-GPO' $leg $null $name {
            Get-GPO -All -Domain $Domain -ErrorAction Stop | Where-Object { $_.DisplayName -eq $name }
        })
        if ($collisions.Count -ne 0) { throw 'disposable target already exists' }
        # Persist intent first: New-GPO may succeed before logging throws.
        $intended[$leg] = $name
        $intended | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $work 'intended-gpos.json') -Encoding UTF8
        $gpo = Invoke-LaneCommand 'New-GPO' $leg $null $name { New-GPO -Name $name -Domain $Domain -ErrorAction Stop }
        $legResult = [ordered]@{
            target_name = $name; owned_gpo_id = [string]$gpo.Id; policy_store = $store
            authoring_succeeded = $false; import_succeeded = $false
            rules_readback = @(); profiles_readback = @(); registry_pol_base64 = $null
            ad_attributes = $null; report_xml = $null; report_links_to_count = $null
        }
        $result["${leg}_leg"] = $legResult
        if ($leg -eq 'read') {
            foreach ($r in $author.rules) {
                $splat = @{}
                foreach ($property in $r.PSObject.Properties) { $splat[$property.Name] = $property.Value }
                if ($splat.Name -notlike 'StudioFwLane*' -or $splat.ContainsKey('PolicyStore')) { throw 'invalid rule authoring scope' }
                $splat.PolicyStore = $store
                Invoke-LaneCommand 'New-NetFirewallRule' $leg $store ([string]$r.Name) { New-NetFirewallRule @splat -ErrorAction Stop } | Out-Null
            }
            foreach ($p in $author.profiles) {
                $splat = @{}
                foreach ($property in $p.PSObject.Properties) { $splat[$property.Name] = $property.Value }
                if ($splat.Name -notin @('Domain', 'Private') -or $splat.ContainsKey('PolicyStore')) { throw 'invalid profile authoring scope' }
                $splat.PolicyStore = $store
                Invoke-LaneCommand 'Set-NetFirewallProfile' $leg $store ([string]$p.Name) { Set-NetFirewallProfile @splat -ErrorAction Stop } | Out-Null
            }
            $legResult.authoring_succeeded = $true
        } else {
            Invoke-LaneCommand 'Import-GPO' $leg $null ([string]$gpo.Id) {
                Import-GPO -BackupId $backupId -Path $inputRoot -TargetGuid $gpo.Id -Domain $Domain -Confirm:$false -ErrorAction Stop
            } | Out-Null
            $legResult.import_succeeded = $true
        }
        Get-LaneObservation $leg $gpo.Id $store $legResult
    }
} catch {
    $result.error = [string]$_.Exception.Message
} finally {
    $cleanupOk = $true
    foreach ($leg in @('read', 'write')) {
        try {
            if ($intended.ContainsKey($leg)) {
                $cleanupName = $intended[$leg]
                $cleanupGpos = @(Invoke-LaneCommand 'Get-GPO' "cleanup-$leg" $null $cleanupName {
                    Get-GPO -All -Domain $Domain -ErrorAction Stop | Where-Object {
                        $_.DisplayName -eq $cleanupName -and
                        ([string]$_.DisplayName).StartsWith("StudioFwLane-$runId-", [StringComparison]::OrdinalIgnoreCase)
                    }
                })
                foreach ($cleanupGpo in $cleanupGpos) {
                    $id = $cleanupGpo.Id
                    Invoke-LaneCommand 'Remove-GPO' $leg $null ([string]$id) {
                        Remove-GPO -Guid $id -Domain $Domain -Confirm:$false -ErrorAction Stop
                    } | Out-Null
                }
            }
        } catch {
            $cleanupOk = $false
            $result.error = (@($result.error, "cleanup: $($_.Exception.Message)") |
                Where-Object { -not [string]::IsNullOrEmpty($_) }) -join '; '
        }
    }
    try {
        # Query all with Stop: errors do not masquerade as an absent resource.
        $result.cleanup_remaining = @(Invoke-LaneCommand 'Get-GPO' 'cleanup' $null '' {
            Get-GPO -All -Domain $Domain -ErrorAction Stop | Where-Object {
                ([string]$_.DisplayName).StartsWith("StudioFwLane-$runId-", [StringComparison]::OrdinalIgnoreCase)
            } | ForEach-Object { [string]$_.Id }
        })
        $result.persistent_after = @(Invoke-LaneCommand 'Get-NetFirewallRule' 'after' 'PersistentStore' '' {
            Get-NetFirewallRule -PolicyStore PersistentStore -ErrorAction Stop | Where-Object {
                $_.Name -like 'StudioFwLane*' -or $_.DisplayName -like 'StudioFwLane*'
            } | ForEach-Object { [string]$_.Name }
        })
        $result.cleanup_verified = $cleanupOk -and $result.cleanup_remaining.Count -eq 0
    } catch {
        $result.error = (@($result.error, "verification: $($_.Exception.Message)") |
            Where-Object { -not [string]::IsNullOrEmpty($_) }) -join '; '
    }
    $result | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $work 'result.json') -Encoding UTF8
    Write-Output $work
}
if ($result.error -or -not $result.cleanup_verified) { throw "firewall lane failed: $($result.error)" }
