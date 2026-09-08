$ErrorActionPreference = 'Stop'
Import-Module ActiveDirectory
Import-Module GroupPolicy
$domain = Get-ADDomain
$dc = $domain.PDCEmulator
$domainDn = $domain.DistinguishedName
$computer = Get-ADComputer LabCL01 -Server $dc
$user = Get-ADUser labauto1 -Server $dc
$ous = @(Get-ADOrganizationalUnit -Server $dc -Filter 'Name -like "StudioRsop*" -or Name -like "GPOStudioLab*" -or Name -like "WI028-*"' | Select-Object DistinguishedName)
$gpos = @(Get-GPO -All -Domain $domain.DNSRoot -Server $dc | Where-Object { $_.DisplayName -like 'Studio-RSOP-*' -or $_.DisplayName -like 'Endpoint-*' } | Select-Object DisplayName, Id)
$groups = @(Get-ADGroup -Server $dc -Filter 'Name -like "StudioRsop*" -or Name -like "Studio-RSOP-*"' | Select-Object DistinguishedName)
$filters = @(Get-ADObject -Server $dc -LDAPFilter '(objectClass=msWMI-Som)' -SearchBase "CN=SOM,CN=WMIPolicy,CN=System,$domainDn" -Properties 'msWMI-Name' | Where-Object { $_.'msWMI-Name' -like 'StudioRsop*' } | Select-Object DistinguishedName)
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
