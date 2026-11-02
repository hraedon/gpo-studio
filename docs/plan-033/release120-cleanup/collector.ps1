$ErrorActionPreference = 'Stop'
# Post-batch directory check for the release 1.1.0 batch. The Plan 034
# collector (plan034-cleanup/collector.ps1), extended to the lanes that batch
# did not run:
#   lifecycle      OUs, GPOs and WMI filters named zz-studio-lifecycle-*,
#                  groups named zzlc-*
#   fdeploy        GPOs named zz-studio-fd-*
#   report-parity  GPOs named zz-studio-rp-*
#   firewall       GPOs named StudioFwLane-* (its rules live in the GPOs; the
#                  member's PersistentStore is checked by the lane itself)
# zz-studio* already covered the first three for OUs and GPOs; the group and
# WMI-filter patterns and the firewall GPO pattern are new.
Import-Module ActiveDirectory
Import-Module GroupPolicy
$domain = Get-ADDomain
$dc = $domain.PDCEmulator
$domainDn = $domain.DistinguishedName
$computer = Get-ADComputer LabCL01 -Server $dc
$user = Get-ADUser labauto1 -Server $dc
$ous = @(Get-ADOrganizationalUnit -Server $dc -Filter 'Name -like "StudioRsop*" -or Name -like "GPOStudioLab*" -or Name -like "WI028-*" -or Name -like "zz-studio*"' | Select-Object DistinguishedName)
$gpos = @(Get-GPO -All -Domain $domain.DNSRoot -Server $dc | Where-Object { $_.DisplayName -like 'Studio-RSOP-*' -or $_.DisplayName -like 'Endpoint-*' -or $_.DisplayName -like 'zz-studio-*' -or $_.DisplayName -like 'StudioFwLane*' } | Select-Object DisplayName, Id)
$groups = @(Get-ADGroup -Server $dc -Filter 'Name -like "StudioRsop*" -or Name -like "Studio-RSOP-*" -or Name -like "zzlc-*"' | Select-Object DistinguishedName)
$filters = @(Get-ADObject -Server $dc -LDAPFilter '(objectClass=msWMI-Som)' -SearchBase "CN=SOM,CN=WMIPolicy,CN=System,$domainDn" -Properties 'msWMI-Name' | Where-Object { $_.'msWMI-Name' -like 'StudioRsop*' -or $_.'msWMI-Name' -like 'zz-studio*' } | Select-Object DistinguishedName)
$result = [ordered]@{
    captured_utc = [DateTime]::UtcNow.ToString('o')
    directory_server = $dc
    computer_dn = $computer.DistinguishedName
    computer_restored = ($computer.DistinguishedName -eq "CN=LABCL01,CN=Computers,$domainDn")
    user_dn = $user.DistinguishedName
    user_restored = ($user.DistinguishedName -eq "CN=labauto1,CN=Users,$domainDn")
    residual_ous = $ous
    residual_gpos = $gpos
    residual_groups = $groups
    residual_wmi_filters = $filters
}
$result | ConvertTo-Json -Depth 5
if (-not $result.computer_restored -or -not $result.user_restored -or $ous.Count -or $gpos.Count -or $groups.Count -or $filters.Count) { throw 'Final directory state differs from the lab baseline.' }
