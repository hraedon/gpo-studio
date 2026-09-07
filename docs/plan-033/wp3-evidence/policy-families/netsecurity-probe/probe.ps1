$ErrorActionPreference = 'Stop'
Import-Module GroupPolicy
Import-Module NetSecurity
$name = 'GPOStudioLab-NetSecurity-' + [guid]::NewGuid().ToString('N').Substring(0, 12)
$gpo = $null
$result = [ordered]@{
    captured_utc = [DateTime]::UtcNow.ToString('o')
    module_version = (Get-Module NetSecurity).Version.ToString()
    computer = $env:COMPUTERNAME
    firewall_query_succeeded = $false
    ipsec_query_succeeded = $false
    authored_rule_read_back = $false
    linked = $false
    cleanup_verified = $false
}
try {
    $gpo = New-GPO -Name $name
    $store = $gpo.DomainName + '\' + $name
    $initial = @(Get-NetFirewallRule -PolicyStore $store -ErrorAction Stop)
    $result.firewall_query_succeeded = $true
    $result.initial_rule_count = $initial.Count
    $ipsec = @(Get-NetIPsecRule -PolicyStore $store -ErrorAction Stop)
    $result.ipsec_query_succeeded = $true
    $result.initial_ipsec_count = $ipsec.Count
    $null = New-NetFirewallRule -PolicyStore $store -Name 'Studio-NetSecurity-Probe' -DisplayName 'Studio isolated store probe' -Direction Outbound -Action Block -Profile Domain -Protocol TCP -RemoteAddress '192.0.2.1' -RemotePort 65000
    $rule = Get-NetFirewallRule -PolicyStore $store -Name 'Studio-NetSecurity-Probe' -ErrorAction Stop
    $port = $rule | Get-NetFirewallPortFilter
    $address = $rule | Get-NetFirewallAddressFilter
    $result.rule = [ordered]@{direction="$($rule.Direction)";action="$($rule.Action)";profile="$($rule.Profile)";protocol="$($port.Protocol)";remote_port=@($port.RemotePort);remote_address=@($address.RemoteAddress)}
    $result.authored_rule_read_back = ($rule.Direction -eq 'Outbound' -and $rule.Action -eq 'Block' -and $port.Protocol -eq 'TCP' -and @($port.RemotePort) -contains '65000' -and @($address.RemoteAddress) -contains '192.0.2.1')
} finally {
    if ($null -ne $gpo) { Remove-GPO -Guid $gpo.Id -Confirm:$false }
    $remaining = @(Get-GPO -All | Where-Object DisplayName -eq $name)
    $result.cleanup_verified = $remaining.Count -eq 0
    $result | ConvertTo-Json -Depth 6
    if (-not $result.cleanup_verified) { throw 'Probe GPO remains after cleanup' }
}
