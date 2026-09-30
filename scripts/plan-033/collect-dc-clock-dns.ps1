#requires -Version 5.1
<#
.SYNOPSIS
    Capture the DC's clock and DNS-record state around a time change.
.DESCRIPTION
    Diagnostic only: this is not a conformance verdict and not a lane. It
    writes nothing to the directory, to DNS, or to SYSVOL -- every call here
    reads. It exists because the WI-062 batch recorded a failure it could not
    explain ("setting the DC forward to real time ... kills domain-wide DC
    discovery ... the deletion mechanism was not identified") and nobody has
    since been able to tell a deleted record from an unanswered query.

    The decisive read is AD, not DNS. Dynamic DC-locator records in an
    AD-integrated zone are `dnsNode` objects; this reads them over LDAP, which
    answers whether they are gone, tombstoned, or present-but-unserved --
    three different failures that look identical to `nslookup`. Replication
    metadata is captured for the same objects, so a change can be attributed to
    an originating server and USN rather than inferred.

    Run once per phase and keep the phases in separate directories: `before`
    (frozen clock, healthy), `after-jump` (immediately after the time change),
    and `broken` (once discovery fails). Comparing the three is the point; a
    single capture settles nothing.

    Keep raw output private until reviewed for environment identifiers -- it
    contains host names, zone names and distinguished names.
.PARAMETER OutputDirectory
    A directory that does not exist yet. One per phase.
.PARAMETER Zone
    The AD-integrated forward zone to read. Defaults to the current domain.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $OutputDirectory,
    [string] $Zone
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (Test-Path -LiteralPath $OutputDirectory) {
    throw 'OutputDirectory already exists; retain it and choose a fresh directory.'
}
$null = New-Item -ItemType Directory -Path $OutputDirectory
$started = [DateTime]::UtcNow

Import-Module ActiveDirectory
$domain = Get-ADDomain
if (-not $Zone) { $Zone = $domain.DNSRoot }
$forest = Get-ADForest

function Save-Text {
    param([string] $Name, [scriptblock] $Command)
    $path = Join-Path $OutputDirectory $Name
    try {
        & $Command 2>&1 | Out-File -LiteralPath $path -Encoding utf8
    } catch {
        # A diagnostic that aborts on its first unavailable surface captures
        # nothing. Record the failure in place and keep going.
        "COLLECTOR_ERROR: $($_.Exception.Message)" |
            Out-File -LiteralPath $path -Encoding utf8
    }
}

function Invoke-Safely {
    param([scriptblock] $Command)
    try { & $Command } catch { @{ collector_error = $_.Exception.Message } }
}

# --- Clock -----------------------------------------------------------------
# w32tm's own view, verbatim: status, configuration and peer list. The
# question these answer is whether the DC believes it is authoritative and
# what, if anything, it is following.
Save-Text 'w32tm-status.txt'        { & w32tm.exe /query /status /verbose }
Save-Text 'w32tm-configuration.txt' { & w32tm.exe /query /configuration }
Save-Text 'w32tm-peers.txt'         { & w32tm.exe /query /peers }

# --- DNS server configuration ----------------------------------------------
# Scavenging and aging were reported disabled. Capture them rather than
# restate them, and capture them per zone as well as per server: the batch
# note says both were checked, and a claim nobody can re-read is not a check.
Save-Text 'dnsserver-scavenging.txt' { Get-DnsServerScavenging | Format-List * }
Save-Text 'dnsserver-setting.txt'    { Get-DnsServerSetting -All | Format-List * }
Save-Text 'dnsserver-zones.txt'      { Get-DnsServerZone | Format-List * }
Save-Text 'dnsserver-zone-aging.txt' {
    Get-DnsServerZone | ForEach-Object {
        if (-not $_.IsReverseLookupZone) {
            Get-DnsServerZoneAging -Name $_.ZoneName | Format-List *
        }
    }
}

# --- What Netlogon intends to register --------------------------------------
# netlogon.dns is Netlogon's own list of the records it owns. Host A records
# are NOT in it -- they belong to the DNS client -- which is why the batch
# note's "even the host A records" matters: a Netlogon-only mechanism does not
# reach them.
Save-Text 'netlogon.dns.txt' {
    Get-Content -LiteralPath (Join-Path $env:SystemRoot 'System32\config\netlogon.dns')
}

# --- The decisive read: dnsNode objects over LDAP ---------------------------
# Three states look the same through a resolver and different here:
#   absent        -- the object is gone from the directory
#   tombstoned    -- dNSTombstoned is TRUE; the object is still there
#   present       -- the record exists and the DNS service is not serving it
$zoneRoots = @(
    "DC=$Zone,CN=MicrosoftDNS,DC=DomainDnsZones,$($domain.DistinguishedName)",
    "DC=_msdcs.$($forest.RootDomain),CN=MicrosoftDNS,DC=ForestDnsZones,$($forest.RootDomainDN)"
)
$nodes = @()
foreach ($root in $zoneRoots) {
    $nodes += Invoke-Safely {
        Get-ADObject -SearchBase $root -LDAPFilter '(objectClass=dnsNode)' `
            -Properties name, dNSTombstoned, dsTombstoneTimestamp, whenCreated, whenChanged, uSNChanged |
            Select-Object @{n = 'searchBase'; e = { $root } },
                name, DistinguishedName, dNSTombstoned, dsTombstoneTimestamp,
                whenCreated, whenChanged, uSNChanged
    }
}

# --- Replication metadata for the locator records ---------------------------
# Attribute-level metadata names the originating server and USN for the last
# write to dnsRecord. If something deleted or tombstoned these, this says what
# and when, which is the half the batch note could not supply.
$locatorPattern = '^(_ldap|_kerberos|_gc|_kpasswd|@|' + [regex]::Escape($env:COMPUTERNAME) + ')'
$metadata = @()
foreach ($node in ($nodes | Where-Object { $_.DistinguishedName -and $_.name -match $locatorPattern })) {
    $metadata += Invoke-Safely {
        Get-ADReplicationAttributeMetadata -Object $node.DistinguishedName -Server $domain.PDCEmulator |
            Where-Object { $_.AttributeName -in @('dnsRecord', 'dNSTombstoned', 'isDeleted') } |
            Select-Object Object, AttributeName, LastOriginatingChangeTime,
                LastOriginatingChangeDirectoryServerIdentity, Version, LocalChangeUsn
    }
}

# --- Event logs -------------------------------------------------------------
# 2501/2502 are the scavenging pair; if scavenging removed records it says so
# here and the "scavenging is disabled" claim is wrong. W32Time 50/129/137/142
# bracket a clock change. 5774/5775 are Netlogon's dynamic-registration
# failures.
$since = (Get-Date).AddDays(-14)
Save-Text 'events-dns-server.txt' {
    Get-WinEvent -FilterHashtable @{ LogName = 'DNS Server'; StartTime = $since } -ErrorAction Stop |
        Select-Object TimeCreated, Id, LevelDisplayName, Message | Format-List *
}
Save-Text 'events-system-time-netlogon.txt' {
    Get-WinEvent -FilterHashtable @{
        LogName = 'System'; StartTime = $since
        ProviderName = @('Microsoft-Windows-Time-Service', 'NETLOGON', 'Microsoft-Windows-DNS-Client')
    } -ErrorAction Stop |
        Select-Object TimeCreated, Id, ProviderName, LevelDisplayName, Message | Format-List *
}
Save-Text 'events-directory-service.txt' {
    Get-WinEvent -FilterHashtable @{ LogName = 'Directory Service'; StartTime = $since } -ErrorAction Stop |
        Select-Object TimeCreated, Id, LevelDisplayName, Message | Format-List *
}

# --- Snapshot ---------------------------------------------------------------
$os = Get-CimInstance Win32_OperatingSystem
$snapshot = [ordered]@{
    kind              = 'diagnostic-observation-not-conformance-verdict'
    schema_version    = 1
    started_utc       = $started.ToString('o')
    completed_utc     = [DateTime]::UtcNow.ToString('o')
    computer          = $env:COMPUTERNAME
    os_caption        = $os.Caption
    build             = $os.BuildNumber
    last_boot_utc     = $os.LastBootUpTime.ToUniversalTime().ToString('o')
    local_time        = (Get-Date).ToString('o')
    utc_time          = [DateTime]::UtcNow.ToString('o')
    time_zone         = (Get-TimeZone).Id
    zone              = $Zone
    pdc_emulator      = $domain.PDCEmulator
    dns_node_count    = @($nodes).Count
    dns_nodes         = $nodes
    locator_metadata  = $metadata
}
$snapshot | ConvertTo-Json -Depth 6 |
    Out-File -LiteralPath (Join-Path $OutputDirectory 'snapshot.json') -Encoding utf8

$hashes = @(Get-ChildItem -LiteralPath $OutputDirectory -File | ForEach-Object {
    [ordered]@{ name = $_.Name; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
})
ConvertTo-Json -InputObject $hashes -Depth 3 |
    Out-File -LiteralPath (Join-Path $OutputDirectory 'hashes.json') -Encoding utf8
Write-Output "CAPTURE_DIRECTORY=$OutputDirectory"
