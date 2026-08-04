#!/usr/bin/env pwsh
# Plan 033 WP-6B RSOP lane, AUTHORING half. Runs ON THE MEMBER SERVER.
#
# Builds the deterministic topology WP-6 demands -- site, domain, parent OU,
# child OU, with intentional conflicts so that every expected winner is known --
# and never observes anything. run-rsop-observe.ps1 runs on the client and never
# authors anything. Same split, same reasons, as the endpoint lane:
# docs/plan-033/endpoint-lane-design.md.
#
# AUTHORING IS NATIVE, DELIBERATELY. Every GPO here is created with New-GPO and
# Set-GPRegistryValue, not imported from a Studio backup. WP-6 asks whether
# rsop.py PREDICTS Windows correctly; WP-1B and WP-2 own whether Studio WRITES
# correctly. If this half authored through Studio's writer, a writer defect and
# a model defect would produce the same evidence.
#
# COMPUTER SCOPE ONLY (ruled 2026-08-03). Every value is HKLM. The estate has
# never had an interactive logon, so a user-scope assertion could not be
# observed even if it were authored. User scope and loopback are WP-9.
#
# ## Blast radius, stated plainly
#
# This lane links a GPO at the DOMAIN ROOT and another at the SITE. Both are
# wider than the disposable OU the endpoint lane confines itself to: they apply
# to every machine in the estate, including the domain controller. That is not
# incidental -- the S and the D in LSDOU cannot be tested any other way.
#
# What makes it acceptable here rather than reckless:
#
#   * every value written lives under HKLM\Software\Policies\StudioLab, which is
#     in the managed-policy branch. The CSE removes managed values when the GPO
#     stops applying, so teardown does not depend on this script remembering
#     individual values;
#   * the guests are disposable and checkpoint-backed;
#   * cleanup unlinks the site and domain links FIRST among the links, and the
#     run fails if any link survives.
#
# It would not be acceptable on a shared host, which is the other half of why
# the estate had to exist before this lane could be written.
#
# ## Two phases, because the client's run happens between them
#
#   -Phase setup    create the OU tree, create and link the six GPOs, move the
#                   CLIENT's computer account into the child OU.
#   -Phase cleanup  restore the computer account FIRST -- that is the step that
#                   stops policy applying to the endpoint -- then remove every
#                   link, delete every GPO, and remove the OU tree leaf-first.
#
# STATE IS WRITTEN TO DISK AS IT IS CREATED, not at the end. Every mutation is
# recorded before the next is attempted, so a setup that dies halfway leaves
# cleanup a complete record. A cleanup that cannot see a mutation cannot undo
# it, and this lane moves a real computer account and links policy at the domain
# root -- "we lost track of it" is not an acceptable failure mode even in a
# disposable estate.

param(
    [Parameter(Mandatory = $true)][ValidateSet('setup', 'cleanup')][string]$Phase,
    [Parameter(Mandatory = $true)][string]$StatePath,

    # setup only
    [string]$TopologyPath,
    [string]$OutputDir,

    # The endpoint whose computer account is moved. This is the CLIENT, not this
    # machine.
    [string]$TargetComputer,

    [string]$Domain = $env:USERDNSDOMAIN
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

Import-Module ActiveDirectory -ErrorAction Stop
Import-Module GroupPolicy -ErrorAction Stop

function Save-State {
    param($State)
    $dir = Split-Path -Path $StatePath -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $State | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $StatePath -Encoding UTF8
}

# Does this object exist? Ask in a way that can actually answer "no".
#
# `Get-ADObject -Identity <missing>` throws ADIdentityNotFoundException, and
# -ErrorAction SilentlyContinue DOES NOT SUPPRESS IT -- that switch governs
# non-terminating errors, and this one is terminating. Every existence probe in
# this script is therefore an explicit try/catch.
#
# Found live on the estate 2026-08-04, twice over, and both faults were the
# resilience code failing in exactly the situation it was written for:
#
#   * the cleanup residual check probed the OUs it had just deleted, threw on
#     the first one, and killed the script BEFORE it wrote cleanup-result.json
#     -- so a teardown that fully succeeded left no record that it had, and the
#     lane could not finalize;
#   * Wait-ForAdObject's retry loop would have thrown on its first miss instead
#     of retrying. It only ever worked because the estate's single DC made every
#     object immediately readable. On a slower or multi-DC directory the retry
#     that exists to absorb replication lag would have been the thing that broke.
#
# Only genuine absence is swallowed. Any other failure -- an unreachable DC, a
# permissions error -- is re-thrown, because "cannot tell" and "not there" are
# different answers and this script decides teardown on the difference.
function Test-AdObjectExists {
    param([string]$Identity, [string]$Server)
    try {
        $null = Get-ADObject -Identity $Identity -Server $Server -ErrorAction Stop
        return $true
    } catch [Microsoft.ActiveDirectory.Management.ADIdentityNotFoundException] {
        return $false
    } catch {
        if ("$($_.Exception.Message)" -match 'not found|does not exist') { return $false }
        throw
    }
}

# AD writes are not necessarily readable immediately, even on the DC that
# accepted them. Inherited from the endpoint lane, where the first live attempt
# failed with "There is no such object on the server".
function Wait-ForAdObject {
    param([string]$Identity, [string]$Server, [int]$Attempts = 20)
    foreach ($attempt in 1..$Attempts) {
        if (Test-AdObjectExists -Identity $Identity -Server $Server) { return $true }
        Start-Sleep -Seconds 3
    }
    return $false
}

if ($Phase -eq 'setup') {
    foreach ($required in 'TopologyPath', 'OutputDir', 'TargetComputer') {
        if (-not (Get-Variable -Name $required -ValueOnly)) {
            throw "-$required is required for -Phase setup."
        }
    }

    $runId = "rsop-author-$(Get-Date -Format 'yyyyMMddHHmmss')-$(Get-Random -Minimum 1000 -Maximum 9999)"
    $workDir = Join-Path $OutputDir $runId
    $commandDir = Join-Path $workDir 'commands'
    New-Item -ItemType Directory -Force -Path $workDir, $commandDir | Out-Null
    Copy-Item $TopologyPath (Join-Path $workDir 'topology.json')

    $topology = Get-Content $TopologyPath -Raw | ConvertFrom-Json

    # Every AD and Group Policy operation is pinned to ONE domain controller,
    # for the same reason the endpoint lane pins them: a write landing on one DC
    # and a read hitting another is a race this lane has already lost once.
    $dc = (Get-ADDomain -Server $Domain).PDCEmulator
    $domainDn = (Get-ADDomain -Server $Domain).DistinguishedName

    $computer = Get-ADComputer -Identity $TargetComputer -Properties DistinguishedName -Server $dc
    $originalDn = $computer.DistinguishedName
    $originalParent = ($originalDn -split ',', 2)[1]

    # The site DN is resolved from the directory rather than taken from the
    # candidate. The candidate names a site; only the directory knows its DN,
    # and a hand-built CN=...,CN=Sites,CN=Configuration string that is subtly
    # wrong would fail as "link did not apply" -- which is a result this lane
    # would otherwise have to interpret.
    $configNc = (Get-ADRootDSE -Server $dc).configurationNamingContext
    $siteDn = "CN=$($topology.site_name),CN=Sites,$configNc"
    if (-not (Test-AdObjectExists -Identity $siteDn -Server $dc)) {
        throw "site '$($topology.site_name)' not found at $siteDn"
    }

    # Names are stamped per-run so a previous run's residue can never be picked
    # up as this run's evidence.
    $stamp = "$(Get-Date -Format 'yyyyMMddHHmmss')-$(Get-Random -Minimum 1000 -Maximum 9999)"

    $ouPlan = @()
    foreach ($ou in $topology.ous) {
        $ouPlan += [ordered]@{
            symbolic_name     = $ou.name
            key               = "$($ou.key)"
            parent_key        = "$($ou.parent_key)"
            name              = "$($ou.name)-$stamp"
            block_inheritance = [bool]$ou.block_inheritance
        }
    }
    # Resolve real DNs by PARENT KEY, not by position.
    #
    # This used to treat the list as a chain -- each OU parented to the one
    # before it -- which is true of the computer-scope tree and false of the
    # split tree the loopback scenarios need, where the user branch hangs off
    # the root beside the computer branch. A chain would have built the right
    # OUs in the wrong shape: every object would exist, every link would
    # succeed, and the estate would apply a topology the prediction does not
    # describe. That is the failure this lane most needs not to have, because
    # it surfaces as a finding about Studio.
    #
    # The candidate emits parents in dependency order, and this asserts it
    # rather than assuming it.
    $realOuDns = @{ 'domain' = $domainDn }
    foreach ($ou in $ouPlan) {
        if (-not $realOuDns.ContainsKey($ou.parent_key)) {
            throw "OU '$($ou.symbolic_name)' names parent '$($ou.parent_key)', which has not been resolved yet. The candidate must emit OUs parents-first."
        }
        $ou.parent_dn = $realOuDns[$ou.parent_key]
        $ou.dn = "OU=$($ou.name),$($ou.parent_dn)"
        $realOuDns[$ou.key] = $ou.dn
    }

    # Where the COMPUTER goes. Named by key rather than taken as "the last OU",
    # which stopped being the same thing once the tree branched.
    $targetOuKey = "$($topology.target_ou_key)"
    if (-not $realOuDns.ContainsKey($targetOuKey)) {
        throw "topology target_ou_key '$targetOuKey' does not name an OU in the tree."
    }
    $childOuDn = $realOuDns[$targetOuKey]

    # Where the USER goes, on user-scope scenarios only.
    $scope = "$($topology.scope)"
    if (-not $scope) { $scope = 'computer' }
    $targetUser = "$($topology.endpoint_user)"
    $userOuDn = ''
    if ($scope -eq 'user') {
        if (-not $targetUser) { throw "scope is 'user' but the topology names no endpoint_user." }
        $userOuKey = "$($topology.user_ou_key)"
        if (-not $realOuDns.ContainsKey($userOuKey)) {
            throw "topology user_ou_key '$userOuKey' does not name an OU in the tree."
        }
        $userOuDn = $realOuDns[$userOuKey]
    }

    $gpoPlan = @()
    foreach ($gpo in $topology.gpos) {
        $scopeDn = switch ($gpo.scope) {
            'site' { $siteDn }
            'domain' { $domainDn }
            'ou' {
                # The candidate's scope_dn is symbolic (it was built before the
                # stamped OUs existed); map it by suffix to the real OU.
                $matched = $null
                foreach ($ou in $ouPlan) {
                    if ($gpo.scope_dn -like "OU=$($ou.symbolic_name),*") { $matched = $ou.dn }
                }
                if (-not $matched) { throw "cannot map OU scope_dn '$($gpo.scope_dn)' for GPO $($gpo.name)" }
                $matched
            }
            default { throw "unknown scope '$($gpo.scope)' for GPO $($gpo.name)" }
        }
        $gpoPlan += [ordered]@{
            symbolic_name = $gpo.name
            name          = "$($gpo.name)-$stamp"
            scope         = $gpo.scope
            scope_dn      = $scopeDn
            order         = $gpo.order
            enforced      = [bool]$gpo.enforced
            link_enabled  = [bool]$gpo.link_enabled
            user_enabled  = [bool]$gpo.user_enabled
            values        = $gpo.values
            user_values   = $gpo.user_values
            raw_values    = $gpo.raw_values
            filters       = $gpo.filters
            guid          = $null
            created       = $false
            linked        = $false
        }
    }

    # The nesting case needs a group the principal belongs to. Created here per
    # run and deleted in cleanup: no pre-existing group is touched, so nothing
    # about the estate's own membership can leak into the result.
    $groupName = ''
    if ("$($topology.group_name)") { $groupName = "$($topology.group_name)-$stamp" }

    # The user object is READ before anything is created, for the same reason
    # the computer is: cleanup restores it to where the directory says it was,
    # and a record written after the first mutation is a record of a state that
    # already changed.
    $userOriginalDn = $null
    $userOriginalParent = $null
    $userSid = $null
    if ($scope -eq 'user') {
        $userObject = Get-ADUser -Identity $targetUser -Properties DistinguishedName -Server $dc
        $userOriginalDn = "$($userObject.DistinguishedName)"
        $userOriginalParent = ($userOriginalDn -split ',', 2)[1]
        $userSid = "$($userObject.SID)"
    }

    $state = [ordered]@{
        run_id            = $runId
        work_dir          = $workDir
        command_dir       = $commandDir
        phase             = 'setup'
        author_computer   = $env:COMPUTERNAME
        target_computer   = $TargetComputer
        domain            = "$Domain"
        domain_controller = $dc
        domain_dn         = $domainDn
        site_dn           = $siteDn
        policy_key        = $topology.policy_key
        original_dn       = $originalDn
        original_parent   = $originalParent
        child_ou_dn       = $childOuDn
        scope             = $scope
        loopback_mode     = "$($topology.loopback_mode)"
        target_user       = $targetUser
        user_ou_dn        = $userOuDn
        user_original_dn  = $userOriginalDn
        user_original_parent = $userOriginalParent
        user_sid          = $userSid
        group_name        = $groupName
        group_dn          = $null
        group_created     = $false
        group_sid         = $null
        user_moved        = $false
        ous               = $ouPlan
        gpos              = $gpoPlan
        computer_moved    = $false
        authored_problems = @()
        setup_completed   = $false
        replication_run   = $false
        environment       = [ordered]@{
            caption            = (Get-CimInstance Win32_OperatingSystem).Caption
            build              = (Get-CimInstance Win32_OperatingSystem).BuildNumber
            powershell_version = "$($PSVersionTable.PSVersion)"
            powershell_edition = "$($PSVersionTable.PSEdition)"
            locale             = (Get-Culture).Name
            gp_module_version  = "$((Get-Module GroupPolicy).Version)"
            domain             = "$Domain"
        }
        error             = $null
    }
    # Recorded before the first mutation.
    Save-State $state

    try {
        foreach ($ou in $state.ous) {
            New-ADOrganizationalUnit -Name $ou.name -Path $ou.parent_dn -Server $dc `
                -ProtectedFromAccidentalDeletion:$false -ErrorAction Stop
            $ou.created = $true
            Save-State $state
            if (-not (Wait-ForAdObject -Identity $ou.dn -Server $dc)) {
                throw "OU not readable on $dc after creation: $($ou.dn)"
            }
            if ($ou.block_inheritance) {
                Set-GPInheritance -Target $ou.dn -IsBlocked Yes -Domain $Domain -Server $dc `
                    -ErrorAction Stop | Out-Null
            }
        }

        if ($groupName) {
            New-ADGroup -Name $groupName -GroupScope Global -GroupCategory Security `
                -Path $childOuDn -Server $dc -ErrorAction Stop
            $state.group_created = $true
            $state.group_dn = "CN=$groupName,$childOuDn"
            Save-State $state
            if (-not (Wait-ForAdObject -Identity $state.group_dn -Server $dc)) {
                throw "group not readable on $dc after creation: $($state.group_dn)"
            }
            Add-ADGroupMember -Identity $state.group_dn -Members $targetUser -Server $dc -ErrorAction Stop
            $state.group_sid = "$((Get-ADGroup -Identity $state.group_dn -Server $dc).SID)"
            Save-State $state
        }

        Move-ADObject -Identity $originalDn -TargetPath $childOuDn -Server $dc -ErrorAction Stop
        $state.computer_moved = $true
        Save-State $state

        # The user goes into its own container on user-scope scenarios. On the
        # loopback scenarios that container is deliberately NOT the computer's:
        # merge and replace are both statements about preferring the computer's
        # location over the user's, so a user and a computer in one OU make
        # every loopback mode produce the same answer.
        if ($scope -eq 'user') {
            Move-ADObject -Identity $state.user_original_dn -TargetPath $userOuDn -Server $dc -ErrorAction Stop
            $state.user_moved = $true
            Save-State $state
        }

        foreach ($gpo in $state.gpos) {
            $created = New-GPO -Name $gpo.name -Domain $Domain -Server $dc -ErrorAction Stop
            $gpo.guid = "$($created.Id)"
            $gpo.created = $true
            Save-State $state

            foreach ($value in $gpo.values) {
                # -Key takes the full path including the hive. Every value in
                # this lane is a REG_SZ under the managed-policy branch.
                Set-GPRegistryValue -Guid $created.Id -Domain $Domain -Server $dc `
                    -Key "HKLM\$($state.policy_key)" `
                    -ValueName $value.value_name -Type String -Value $value.value `
                    -ErrorAction Stop | Out-Null
            }
            # User-side values are authored even though WP-6 never asserts on
            # them. A GPO whose user side is disabled but carries nothing on
            # that side would make "the computer side still applies" a claim
            # about an empty GPO, which tests nothing. The assertion about what
            # the user side does NOT contribute is WP-9's.
            foreach ($value in $gpo.user_values) {
                Set-GPRegistryValue -Guid $created.Id -Domain $Domain -Server $dc `
                    -Key "HKCU\$($state.policy_key)" `
                    -ValueName $value.value_name -Type String -Value $value.value `
                    -ErrorAction Stop | Out-Null
            }
            # Values outside the lane's own policy key, with their own type.
            # Loopback is the only user so far: 'Configure user Group Policy
            # loopback processing mode' is an ordinary machine registry policy,
            # and authoring it natively is what makes the mode real on the
            # client rather than an assertion in the candidate.
            foreach ($value in $gpo.raw_values) {
                Set-GPRegistryValue -Guid $created.Id -Domain $Domain -Server $dc `
                    -Key "HKLM\$($value.key)" `
                    -ValueName $value.value_name -Type $value.type -Value $value.value `
                    -ErrorAction Stop | Out-Null
            }
            # Side status is set AFTER the values are written: disabling a side
            # first would not prevent the write, but it makes the intent of the
            # sequence unreadable, and the CSE cares only about the final state.
            if (-not $gpo.user_enabled) {
                $gpoObject = Get-GPO -Guid $created.Id -Domain $Domain -Server $dc -ErrorAction Stop
                $gpoObject.GpoStatus = 'UserSettingsDisabled'
            }

            New-GPLink -Guid $created.Id -Target $gpo.scope_dn -Domain $Domain -Server $dc `
                -LinkEnabled ($(if ($gpo.link_enabled) { 'Yes' } else { 'No' })) `
                -Enforced ($(if ($gpo.enforced) { 'Yes' } else { 'No' })) `
                -ErrorAction Stop | Out-Null
            $gpo.linked = $true
            Save-State $state

            # SECURITY FILTERING, applied after the link so the GPO exists in
            # every sense before its DACL is touched.
            #
            # MS16-072 is why Authenticated Users keeps READ on every filtered
            # GPO. Since that update a USER's GPOs are retrieved in the
            # COMPUTER's security context, so a GPO the computer cannot read
            # does not reach the user however the user is filtered -- and the
            # lane would record a filtering result that is really a read
            # failure. Only Apply is moved.
            foreach ($planned in $gpo.filters) {
                $principal = switch ("$($planned.principal)") {
                    'user' { $state.target_user }
                    'group' { $groupName }
                    'authenticated-users' { 'Authenticated Users' }
                    default { throw "unknown filter principal '$($planned.principal)' on $($gpo.name)" }
                }
                $principalType = if ("$($planned.principal)" -eq 'user') { 'User' } else { 'Group' }

                switch ("$($planned.kind)") {
                    'read' {
                        # -Replace, so an existing Read+Apply for the same
                        # principal is reduced rather than added beside.
                        Set-GPPermission -Guid $created.Id -Domain $Domain -Server $dc `
                            -TargetName $principal -TargetType $principalType `
                            -PermissionLevel GpoRead -Replace -ErrorAction Stop | Out-Null
                    }
                    'apply' {
                        Set-GPPermission -Guid $created.Id -Domain $Domain -Server $dc `
                            -TargetName $principal -TargetType $principalType `
                            -PermissionLevel GpoApply -ErrorAction Stop | Out-Null
                    }
                    'deny' {
                        # No cmdlet writes a deny ACE, so this goes onto the
                        # groupPolicyContainer's DACL directly. The right is
                        # Apply Group Policy, a CONTROL-ACCESS right
                        # (ExtendedRight) rather than a property right, which is
                        # exactly the distinction Plan 033's preconditions
                        # single out.
                        $applyRight = [guid]'edacfd8f-ffb3-11d1-b41d-00a0c968f939'
                        $gpoDn = "CN={$($created.Id)},CN=Policies,CN=System,$domainDn"
                        $identity = if ("$($planned.principal)" -eq 'user') {
                            (Get-ADUser -Identity $principal -Server $dc).SID
                        } else {
                            (Get-ADGroup -Identity $principal -Server $dc).SID
                        }
                        $acl = Get-Acl -Path "AD:$gpoDn"
                        $ace = New-Object System.DirectoryServices.ActiveDirectoryAccessRule(
                            $identity,
                            [System.DirectoryServices.ActiveDirectoryRights]::ExtendedRight,
                            [System.Security.AccessControl.AccessControlType]::Deny,
                            $applyRight)
                        $acl.AddAccessRule($ace)
                        Set-Acl -Path "AD:$gpoDn" -AclObject $acl
                    }
                    default { throw "unknown filter kind '$($planned.kind)' on $($gpo.name)" }
                }
            }

            # Link order is set AFTER creation, because New-GPLink appends and
            # the resulting order depends on what is already linked to that
            # container. Setting it explicitly makes the topology independent of
            # creation sequence -- and link order is precisely what the ChildA /
            # ChildB pair exists to test, so leaving it implicit would test the
            # harness rather than Windows.
            Set-GPLink -Guid $created.Id -Target $gpo.scope_dn -Domain $Domain -Server $dc `
                -Order $gpo.order -ErrorAction Stop | Out-Null
        }

        # VERIFY WHAT WAS ACTUALLY AUTHORED, before handing the topology to the
        # client.
        #
        # Two of the mechanics here are assumptions until measured: that
        # assigning $gpo.GpoStatus persists a disabled user side, and that
        # Set-GPLink -Order preserves LinkEnabled rather than silently
        # re-enabling a link this scenario needs disabled. If either is wrong the
        # estate would apply a topology the prediction does not describe, and the
        # finalizer would report a FINDING ABOUT STUDIO for a harness defect --
        # the single most misleading outcome this lane can produce.
        #
        # So the directory is re-read and compared against intent. A mismatch is
        # a lane failure, recorded here rather than inferred later.
        $authoredProblems = @()
        foreach ($gpo in $state.gpos) {
            if (-not $gpo.created) { continue }
            $live = Get-GPO -Guid $gpo.guid -Domain $Domain -Server $dc -ErrorAction Stop
            $userDisabled = ("$($live.GpoStatus)" -match 'UserSettingsDisabled' -or
                             "$($live.GpoStatus)" -match 'AllSettingsDisabled')
            if ($gpo.user_enabled -and $userDisabled) {
                $authoredProblems += "$($gpo.name): user side disabled but intended enabled"
            }
            if (-not $gpo.user_enabled -and -not $userDisabled) {
                $authoredProblems += "$($gpo.name): user side is '$($live.GpoStatus)', intended UserSettingsDisabled"
            }

            # gPLink encodes both enablement and enforcement in a per-link
            # options bitmask: bit 0 = link DISABLED, bit 1 = enforced. Reading
            # the raw attribute avoids Get-GPInheritance, which cannot target a
            # site (found live 2026-08-04).
            $linkValue = "$((Get-ADObject -Identity $gpo.scope_dn -Properties gPLink -Server $dc -ErrorAction Stop).gPLink)"
            $guidBare = "$($gpo.guid)".Trim('{}')
            $matched = [regex]::Match($linkValue, '\[LDAP://[^;\]]*' + [regex]::Escape($guidBare) + '[^;\]]*;(\d+)\]', 'IgnoreCase')
            if (-not $matched.Success) {
                $authoredProblems += "$($gpo.name): no gPLink entry at $($gpo.scope_dn)"
            } else {
                $options = [int]$matched.Groups[1].Value
                $liveEnabled = -not ($options -band 1)
                $liveEnforced = [bool]($options -band 2)
                if ($liveEnabled -ne $gpo.link_enabled) {
                    $authoredProblems += "$($gpo.name): link enabled=$liveEnabled, intended $($gpo.link_enabled)"
                }
                if ($liveEnforced -ne $gpo.enforced) {
                    $authoredProblems += "$($gpo.name): link enforced=$liveEnforced, intended $($gpo.enforced)"
                }
            }
        }
        # Filtering is verified for the same reason link state is: if the DACL
        # does not say what the topology asked for, the estate is running a
        # different experiment and the prediction describes the wrong one. A
        # mismatch here would surface as a FINDING ABOUT STUDIO.
        foreach ($gpo in $state.gpos) {
            if (-not $gpo.created) { continue }
            if (@($gpo.filters).Count -eq 0) { continue }
            $permissions = @(Get-GPPermission -Guid $gpo.guid -All -Domain $Domain -Server $dc -ErrorAction Stop)
            $gpoDn = "CN={$($gpo.guid)},CN=Policies,CN=System,$domainDn"
            $dacl = (Get-Acl -Path "AD:$gpoDn").Access
            $applyRight = 'edacfd8f-ffb3-11d1-b41d-00a0c968f939'

            foreach ($planned in $gpo.filters) {
                $principal = switch ("$($planned.principal)") {
                    'user' { $state.target_user }
                    'group' { $groupName }
                    'authenticated-users' { 'Authenticated Users' }
                }
                $held = @($permissions | Where-Object {
                    "$($_.Trustee.Name)" -eq "$principal" -and "$($_.Permission)" -match 'GpoApply'
                })
                $denied = @($dacl | Where-Object {
                    "$($_.AccessControlType)" -eq 'Deny' -and
                    "$($_.ObjectType)" -eq $applyRight -and
                    "$($_.IdentityReference)" -match [regex]::Escape($principal)
                })
                switch ("$($planned.kind)") {
                    'apply' {
                        if ($held.Count -eq 0) {
                            $authoredProblems += "$($gpo.name): '$principal' was granted Apply and does not hold it"
                        }
                    }
                    'read' {
                        if ($held.Count -gt 0) {
                            $authoredProblems += "$($gpo.name): '$principal' should hold Read WITHOUT Apply and holds Apply"
                        }
                    }
                    'deny' {
                        if ($denied.Count -eq 0) {
                            $authoredProblems += "$($gpo.name): no deny ACE on Apply Group Policy for '$principal'"
                        }
                    }
                }
            }
        }

        foreach ($ou in $state.ous) {
            if (-not $ou.created) { continue }
            $inheritance = Get-GPInheritance -Target $ou.dn -Domain $Domain -Server $dc -ErrorAction Stop
            if ([bool]$inheritance.GpoInheritanceBlocked -ne [bool]$ou.block_inheritance) {
                $authoredProblems += "$($ou.name): inheritance blocked=$($inheritance.GpoInheritanceBlocked), intended $($ou.block_inheritance)"
            }
        }
        $state.authored_problems = $authoredProblems
        Save-State $state

        # Push replication from the machine that holds the tooling; the client
        # has no repadmin. This is a convergence push, not a guarantee -- the
        # observation half still polls until the client itself reports the GPOs.
        & repadmin.exe /syncall /Aeq 2>&1 |
            Out-File (Join-Path $commandDir 'repadmin-syncall.stdout.txt')
        $state.replication_run = $true

        $state.setup_completed = $true
        Save-State $state
    } catch {
        $state.error = "$($_.Exception.Message)"
        Save-State $state
        throw
    }

    # Key=value lines rather than JSON: the driver parses this with sed, and a
    # compressed JSON blob would need a parser on the controller for no gain.
    Write-Output "RUN_ID=$runId"
    Write-Output "WORK_DIR=$workDir"
    exit 0
}

# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------

if (-not (Test-Path -LiteralPath $StatePath)) {
    throw "no state file at $StatePath; cleanup cannot know what setup created."
}
$state = Get-Content $StatePath -Raw | ConvertFrom-Json
$dc = $state.domain_controller
$problems = @()

# ORDER MATTERS AND IT IS NOT THE REVERSE OF SETUP.
#
# The computer account goes back first. That is the single step that stops
# policy applying to the endpoint; doing it last would leave the machine
# receiving domain-root and site policy through every other teardown step.
if ($state.computer_moved) {
    # The object is wherever the directory says it is, which is not necessarily
    # where setup put it: a concurrent move, a partially-failed setup, or a
    # replication lag all produce a DN this script did not write down. Ask,
    # then move. Reconstructing the current DN by string surgery on the
    # original would be a guess dressed as a fact.
    try {
        $current = Get-ADComputer -Identity $state.target_computer -Server $dc `
            -Properties DistinguishedName -ErrorAction Stop
        if ($current.DistinguishedName -ne $state.original_dn) {
            Move-ADObject -Identity $current.DistinguishedName `
                -TargetPath $state.original_parent -Server $dc -ErrorAction Stop
        }
    } catch {
        $problems += "computer restore failed: $($_.Exception.Message)"
    }
}

# The user object comes back next, and for the same reason as the computer: it
# is a step that stops policy applying, not a tidy-up. It is restored to where
# the DIRECTORY says it is rather than to where setup wrote it down, because a
# setup that died between the move and its Save-State leaves an object this
# script never recorded -- the same failure the computer restore was fixed for.
if ($state.PSObject.Properties.Name -contains 'user_moved' -and $state.user_moved) {
    try {
        $currentUser = Get-ADUser -Identity $state.target_user -Server $dc `
            -Properties DistinguishedName -ErrorAction Stop
        if ($currentUser.DistinguishedName -ne $state.user_original_dn) {
            Move-ADObject -Identity $currentUser.DistinguishedName `
                -TargetPath $state.user_original_parent -Server $dc -ErrorAction Stop
        }
    } catch {
        $problems += "user restore failed: $($_.Exception.Message)"
    }
}

# Links next, and the wide ones first: the site and domain links are the ones
# with a blast radius beyond the disposable OU tree, so they are the ones worth
# removing before anything else can go wrong.
$orderedGpos = @()
$orderedGpos += @($state.gpos | Where-Object { $_.scope -eq 'site' })
$orderedGpos += @($state.gpos | Where-Object { $_.scope -eq 'domain' })
$orderedGpos += @($state.gpos | Where-Object { $_.scope -eq 'ou' })

foreach ($gpo in $orderedGpos) {
    if ($gpo.linked) {
        try {
            Remove-GPLink -Guid $gpo.guid -Target $gpo.scope_dn -Domain $state.domain -Server $dc `
                -ErrorAction Stop | Out-Null
        } catch {
            $problems += "unlink failed for $($gpo.name) at $($gpo.scope_dn): $($_.Exception.Message)"
        }
    }
}

foreach ($gpo in $orderedGpos) {
    if ($gpo.created) {
        try {
            Remove-GPO -Guid $gpo.guid -Domain $state.domain -Server $dc -ErrorAction Stop | Out-Null
        } catch {
            $problems += "GPO delete failed for $($gpo.name): $($_.Exception.Message)"
        }
    }
}

# The disposable group goes before the OUs that contain it: an OU with a child
# object cannot be removed, and -Recursive is deliberately not used anywhere in
# this teardown -- a recursive delete would hide exactly the leftovers the
# residual check below exists to find.
if ($state.PSObject.Properties.Name -contains 'group_created' -and $state.group_created) {
    try {
        Remove-ADGroup -Identity $state.group_dn -Server $dc -Confirm:$false -ErrorAction Stop
    } catch {
        $problems += "group delete failed for $($state.group_dn): $($_.Exception.Message)"
    }
}

# OUs leaf-first: a parent with children cannot be removed.
$reversed = @($state.ous)
[array]::Reverse($reversed)
foreach ($ou in $reversed) {
    if ($ou.created) {
        try {
            Remove-ADOrganizationalUnit -Identity $ou.dn -Server $dc -Recursive:$false `
                -Confirm:$false -ErrorAction Stop
        } catch {
            $problems += "OU delete failed for $($ou.dn): $($_.Exception.Message)"
        }
    }
}

# Prove the teardown rather than asserting it. Every check below is a re-query
# against the directory, because "we issued the delete" and "it is gone" are
# different claims and only the second one is cleanup.
$residual = [ordered]@{
    computer_dn      = $null
    computer_restored = $false
    user_dn          = $null
    user_restored    = $null
    surviving_group  = $null
    surviving_links  = @()
    surviving_gpos   = @()
    surviving_ous    = @()
}
try {
    $current = Get-ADComputer -Identity $state.target_computer -Server $dc `
        -Properties DistinguishedName -ErrorAction Stop
    $residual.computer_dn = "$($current.DistinguishedName)"
    $residual.computer_restored = ($current.DistinguishedName -eq $state.original_dn)
    if (-not $residual.computer_restored) {
        $problems += "computer is at $($current.DistinguishedName), expected $($state.original_dn)"
    }
} catch {
    $problems += "could not re-query computer: $($_.Exception.Message)"
}

if ($state.PSObject.Properties.Name -contains 'user_moved' -and $state.user_moved) {
    try {
        $currentUser = Get-ADUser -Identity $state.target_user -Server $dc `
            -Properties DistinguishedName -ErrorAction Stop
        $residual.user_dn = "$($currentUser.DistinguishedName)"
        $residual.user_restored = ($currentUser.DistinguishedName -eq $state.user_original_dn)
        if (-not $residual.user_restored) {
            $problems += "user is at $($currentUser.DistinguishedName), expected $($state.user_original_dn)"
        }
    } catch {
        $problems += "could not re-query user: $($_.Exception.Message)"
    }
}

foreach ($gpo in $orderedGpos) {
    if (-not $gpo.created) { continue }
    # Get-GPO does not return $null for a missing GPO on every build -- it
    # throws GpoNotFound. Absence is the outcome cleanup wants, so the throw is
    # a PASS here, not an error. It is recorded rather than swallowed: an empty
    # catch would also swallow "the DC is unreachable", which is a very
    # different thing from "the GPO is gone" and would silently certify a
    # teardown nobody verified.
    $stillPresent = $null
    try {
        $stillPresent = Get-GPO -Guid $gpo.guid -Domain $state.domain -Server $dc -ErrorAction Stop
    } catch {
        if ("$($_.Exception.Message)" -match 'not found|does not exist|GpoNotFound') {
            $stillPresent = $null
        } else {
            $problems += "could not confirm $($gpo.name) was deleted: $($_.Exception.Message)"
        }
    }
    if ($stillPresent) {
        $residual.surviving_gpos += $gpo.name
        $problems += "GPO still present after delete: $($gpo.name)"
    }
}

if ($state.PSObject.Properties.Name -contains 'group_created' -and $state.group_created) {
    if (Test-AdObjectExists -Identity $state.group_dn -Server $dc) {
        $residual.surviving_group = "$($state.group_dn)"
        $problems += "group still present after delete: $($state.group_dn)"
    }
}

foreach ($ou in $state.ous) {
    if (-not $ou.created) { continue }
    if (Test-AdObjectExists -Identity $ou.dn -Server $dc) {
        $residual.surviving_ous += $ou.dn
        $problems += "OU still present after delete: $($ou.dn)"
    }
}

# The site and domain containers are re-read directly: these are the two scopes
# whose residue would affect machines outside the disposable tree, so a link
# leak there is worth detecting explicitly rather than inferring from the GPO
# having been deleted.
#
# Read the raw gPLink attribute rather than calling Get-GPInheritance.
# GET-GPINHERITANCE CANNOT TARGET A SITE -- it accepts only a domain or an OU
# and throws "The target specified is invalid" on anything else. Found live on
# the estate 2026-08-04, and it presented in the worst available way: the
# teardown had actually succeeded, the verification threw, and the lane reported
# a cleanup failure for an estate that was already clean. A check that cannot
# run against half the scopes it is given is not a check.
#
# gPLink is the attribute Get-GPInheritance summarizes, it exists on every SOM
# type, and it names linked GPOs by GUID -- so one code path now covers site,
# domain and OU instead of two behaviours with a silent hole between them.
foreach ($scopeDn in @($state.site_dn, $state.domain_dn)) {
    try {
        $linkValue = "$((Get-ADObject -Identity $scopeDn -Properties gPLink -Server $dc -ErrorAction Stop).gPLink)"
        foreach ($gpo in $orderedGpos) {
            if (-not $gpo.guid) { continue }
            $guid = "$($gpo.guid)".Trim('{}')
            if ($linkValue -match [regex]::Escape($guid)) {
                $residual.surviving_links += "$($gpo.name) @ $scopeDn"
                $problems += "link survives at $scopeDn for $($gpo.name)"
            }
        }
    } catch {
        $problems += "could not re-query links at ${scopeDn}: $($_.Exception.Message)"
    }
}

$result = [ordered]@{
    run_id           = $state.run_id
    phase            = 'cleanup'
    cleanup_problems = $problems
    residual         = $residual
}
# Written into the run's own work dir so the driver's evidence pull carries it,
# and echoed so a human watching the run sees the teardown outcome without
# waiting for the pull.
if ($state.work_dir -and (Test-Path -LiteralPath $state.work_dir)) {
    $result | ConvertTo-Json -Depth 20 |
        Set-Content -LiteralPath (Join-Path $state.work_dir 'cleanup-result.json') -Encoding UTF8
}
Write-Output (ConvertTo-Json $result -Depth 20 -Compress)
if ($problems.Count -gt 0) { exit 1 }
exit 0
