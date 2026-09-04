"""Planning/modeling layer for GPO publication.

This module does NOT publish to Active Directory or SYSVOL. It builds a typed
:class:`PublicationPlan` from a :class:`~gpo_studio.model.GPO` and generates an
administrator-review PowerShell script. The actual AD/SYSVOL writes require
manual administrator execution against the generated script; the web process
never writes directly to AD or SYSVOL.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from .artifact_store import ArtifactStore, detect_secrets
from .gpmc_interop import InteropIssue
from .gpp import serialize_gpp
from .model import GPO, RegistrySetting, ValidationIssue
from .registry_pol import PolRecord
from .registry_pol import serialize as serialize_registry_pol

PublicationState = Literal[
    "draft",           # being prepared
    "staged",          # staged for review
    "approved",        # approved by administrator
    "publishing",      # actively being published
    "published",       # successfully published
    "failed",          # publication failed
    "rolled_back",     # rolled back after failure
]

PublicationTarget = Literal["ad", "sysvol", "both"]

# Which half of the packed GPT.INI Version= field a publication increments.
# The field is user * 65536 + machine and its halves move independently.
GptVersionHalf = Literal["machine", "user", "both"]


@dataclass(frozen=True, slots=True)
class PublicationStep:
    step_id: str
    operation: str              # e.g. "write_registry_pol", "update_gpt_ini", "copy_gpp_xml"
    target: PublicationTarget
    status: Literal["pending", "running", "completed", "failed", "skipped"]
    detail: str = ""
    artifact_ids: tuple[str, ...] = ()  # artifacts involved in this step
    # update_gpt_ini only: which half of the packed Version= field this plan
    # publishes. The halves move independently (see _unpack_gpt_version), so
    # the half must be recorded explicitly rather than guessed at run time.
    version_half: GptVersionHalf | None = None


@dataclass(frozen=True, slots=True)
class PublicationPlan:
    plan_id: str
    gpo_guid: str
    gpo_name: str
    target: PublicationTarget = "both"
    state: PublicationState = "draft"
    steps: tuple[PublicationStep, ...] = field(default_factory=tuple)
    approved_by: str = ""
    approved_at: str = ""
    published_at: str = ""
    rollback_plan: tuple[PublicationStep, ...] = field(default_factory=tuple)
    requires_enhanced_approval: bool = False
    risk_level: Literal["low", "medium", "high", "critical"] = "low"

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate publication plan structural rules."""
        issues: list[ValidationIssue] = []
        if not self.gpo_guid.strip():
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_gpo_guid",
                    message="gpo_guid must not be empty.",
                    path="gpo_guid",
                )
            )
        if not self.steps:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_steps",
                    message="Publication plan must contain at least one step.",
                    path="steps",
                )
            )
        if self.state == "approved" and not self.approved_by.strip():
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="approved_without_approver",
                    message="Approved plan must record approved_by.",
                    path="approved_by",
                )
            )
        if self.state == "published" and not self.published_at.strip():
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="published_without_timestamp",
                    message="Published plan must record published_at.",
                    path="published_at",
                )
            )
        if self.requires_enhanced_approval and self.state == "approved":
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="requires_enhanced_approval",
                    message="Plan requires enhanced approval before approval.",
                    path="requires_enhanced_approval",
                )
            )
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class PowerShellPublicationScript:
    """Generated PowerShell script for administrator-driven publication."""
    script_text: str
    plan_id: str
    gpo_guid: str
    is_idempotent: bool = True  # True only if every step is implemented idempotently
    estimated_duration_seconds: int = 0


# Known CSE GUIDs for extension lists.
_REGISTRY_CSE_GUID = "{35378EAC-683F-11D2-A89A-00C04FBBCFA2}"
_GPP_GROUPS_CSE_GUID = "{3125E937-EB16-4b4c-9934-544FC6D24D26}"
_GPP_REGISTRY_CSE_GUID = "{A3CC7818-8A30-4e0c-91C5-A4EA4B5A8DAB}"

# GPMC settings count threshold for medium risk.
_REGISTRY_SETTINGS_MEDIUM_RISK_THRESHOLD = 100

# Operations with Windows external-oracle evidence proving both execution and
# idempotency. Plan 033 intentionally starts empty: internally consistent
# PowerShell generation is not evidence that GPMC/SYSVOL assigns it the same
# meaning. Until an operation is promoted here, the generated script is a
# fail-closed review artifact and performs no mutation.
_WINDOWS_VERIFIED_OPERATIONS: frozenset[str] = frozenset()


def _new_plan_id() -> str:
    return f"plan-{uuid.uuid4().hex[:12]}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _artifact_id_for(content: bytes) -> str:
    return _content_hash(content)


def _guid_with_braces(guid: str) -> str:
    guid = guid.strip("{}")
    return "{" + guid.upper() + "}"


def _guid_without_braces(guid: str) -> str:
    return guid.strip("{}").lower()


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _ps_sanitize_comment(text: str) -> str:
    return text.replace("\r", " ").replace("\n", " ").replace("`", " ")


def _not_implemented_warning(operation: str) -> str:
    """PowerShell Write-Warning line for an unimplemented operation."""
    return (
        f'Write-Warning "NOT IMPLEMENTED: {operation} '
        "\u2014 requires manual execution\""
    )


def _unpack_gpt_version(packed: int) -> tuple[int, int]:
    """Split a packed GPT.INI ``Version=`` into its (machine, user) halves.

    Windows maintains the version as one packed 32-bit field,
    ``user * 65536 + machine``: the machine counter is the low 16-bit half and
    the user counter the high 16-bit half, and the halves move independently.
    Measured on Windows Server 2025 (2026-09-03): a machine-side-only edit
    moved the value 0 -> 1; a user-side-only edit moved it 0 -> 65536 with the
    machine half untouched. A GPO published by Windows itself showed
    ``Version=0x0002000A``: user 2, machine 10.
    """
    return packed % 65536, packed // 65536


def _pack_gpt_version(machine: int, user: int) -> int:
    """Pack independent 16-bit version halves into a GPT.INI ``Version=``."""
    return (user % 65536) * 65536 + (machine % 65536)


def _bump_gpt_version(packed: int, half: Literal["machine", "user"]) -> int:
    """Increment one half of a packed version and leave the other half alone.

    A wrapping half does not carry into its neighbour. Windows' own carry
    behaviour on wrap is unverified; the invariant this module commits to is
    that an increment never moves the half it does not target.
    """
    machine, user = _unpack_gpt_version(packed)
    if half == "machine":
        machine = (machine + 1) % 65536
    else:
        user = (user + 1) % 65536
    return _pack_gpt_version(machine, user)


def _gpt_version_half(
    has_machine_content: bool, has_user_content: bool
) -> GptVersionHalf | None:
    """Pick the GPT.INI half that a plan's SYSVOL content implies."""
    if has_machine_content and has_user_content:
        return "both"
    if has_machine_content:
        return "machine"
    if has_user_content:
        return "user"
    return None


_GPT_VERSION_HALF_LABELS: dict[GptVersionHalf, str] = {
    "machine": "machine half",
    "user": "user half",
    "both": "machine and user halves",
}


def _assess_risk(gpo: GPO) -> Literal["low", "medium", "high", "critical"]:
    for link in gpo.links:
        target = link.target.strip().casefold()
        if "ou=domain controllers" in target or target.endswith("dc=domain controllers"):
            return "high"
    if gpo.security_filters:
        return "medium"
    if len(gpo.settings) > _REGISTRY_SETTINGS_MEDIUM_RISK_THRESHOLD:
        return "medium"
    return "low"


def _build_registry_pol_content(settings: tuple[RegistrySetting, ...]) -> bytes:
    records = [
        PolRecord(
            key=s.key,
            value_name=s.value_name,
            registry_type=s.registry_type,
            value=s.value,
            action=s.action,
        )
        for s in settings
    ]
    return serialize_registry_pol(records)


def _is_ad_target(target: PublicationTarget) -> bool:
    return target in ("ad", "both")


def _is_sysvol_target(target: PublicationTarget) -> bool:
    return target in ("sysvol", "both")


def generate_publication_plan(
    gpo: GPO,
    target: PublicationTarget = "both",
    actor: str = "",
) -> PublicationPlan:
    """Generate a publication plan for a GPO."""
    steps: list[PublicationStep] = []
    rollback: list[PublicationStep] = []

    gpo_guid = _guid_without_braces(gpo.guid)
    plan_id = _new_plan_id()

    computer_settings = [s for s in gpo.settings if s.side == "computer"]
    user_settings = [s for s in gpo.settings if s.side == "user"]

    # SYSVOL-targeted steps: only created when publishing to SYSVOL. When
    # target="ad" none of these are emitted.
    if _is_sysvol_target(target):
        # Update GPT.INI version counter. The version is a packed 32-bit field
        # whose halves move independently, so the step records which half this
        # plan publishes; incrementing the whole value would move the machine
        # half even for user-side-only content.
        has_machine_content = bool(computer_settings) or any(
            c.scope == "computer" for c in gpo.gpp_collections
        )
        has_user_content = bool(user_settings) or any(
            c.scope == "user" for c in gpo.gpp_collections
        )
        version_half = _gpt_version_half(has_machine_content, has_user_content)
        if version_half is None:
            detail = "No GPT.INI version increment (no machine or user SYSVOL content)"
        else:
            detail = (
                "Increment GPT.INI version counter "
                f"({_GPT_VERSION_HALF_LABELS[version_half]})"
            )
        steps.append(
            PublicationStep(
                step_id="update-gpt-ini",
                operation="update_gpt_ini",
                target="sysvol",
                status="pending",
                detail=detail,
                version_half=version_half,
            )
        )
        rollback.append(
            PublicationStep(
                step_id="rollback-update-gpt-ini",
                operation="restore_gpt_ini",
                target="sysvol",
                status="pending",
                detail="Restore previous GPT.INI version from backup",
            )
        )

        # If registry settings: write Registry.pol (computer and/or user).
        if computer_settings:
            content = _build_registry_pol_content(tuple(computer_settings))
            artifact_id = _artifact_id_for(content)
            step = PublicationStep(
                step_id="write-machine-registry-pol",
                operation="write_registry_pol",
                target="sysvol",
                status="pending",
                detail="Write Machine/Registry.pol",
                artifact_ids=(artifact_id,),
            )
            steps.append(step)
            rollback.append(
                PublicationStep(
                    step_id="rollback-write-machine-registry-pol",
                    operation="restore_registry_pol",
                    target="sysvol",
                    status="pending",
                    detail="Restore Machine/Registry.pol from backup",
                    artifact_ids=step.artifact_ids,
                )
            )

        if user_settings:
            content = _build_registry_pol_content(tuple(user_settings))
            artifact_id = _artifact_id_for(content)
            step = PublicationStep(
                step_id="write-user-registry-pol",
                operation="write_registry_pol",
                target="sysvol",
                status="pending",
                detail="Write User/Registry.pol",
                artifact_ids=(artifact_id,),
            )
            steps.append(step)
            rollback.append(
                PublicationStep(
                    step_id="rollback-write-user-registry-pol",
                    operation="restore_registry_pol",
                    target="sysvol",
                    status="pending",
                    detail="Restore User/Registry.pol from backup",
                    artifact_ids=step.artifact_ids,
                )
            )

        # If GPP collections: copy GPP XML files to SYSVOL.
        for collection in gpo.gpp_collections:
            files = serialize_gpp(collection)
            side_dir = "Machine" if collection.scope == "computer" else "User"
            for filename, content in files.items():
                artifact_id = _artifact_id_for(content)
                safe_name = filename.replace("/", "-").replace(" ", "")
                step = PublicationStep(
                    step_id=f"copy-gpp-{collection.scope}-{safe_name}",
                    operation="copy_gpp_xml",
                    target="sysvol",
                    status="pending",
                    detail=f"Copy {side_dir}/Preferences/{filename}",
                    artifact_ids=(artifact_id,),
                )
                steps.append(step)
                rollback.append(
                    PublicationStep(
                        step_id=f"rollback-{step.step_id}",
                        operation="remove_gpp_xml",
                        target="sysvol",
                        status="pending",
                        detail=f"Remove {side_dir}/Preferences/{filename}",
                        artifact_ids=step.artifact_ids,
                    )
                )

    # If security filters: update nTSecurityDescriptor.
    if gpo.security_filters and _is_ad_target(target):
        step = PublicationStep(
            step_id="update-security-filters",
            operation="update_nt_security_descriptor",
            target="ad",
            status="pending",
            detail="Update GPO security filtering",
        )
        steps.append(step)
        rollback.append(
            PublicationStep(
                step_id="rollback-update-security-filters",
                operation="restore_nt_security_descriptor",
                target="ad",
                status="pending",
                detail="Restore GPO security descriptor from backup",
            )
        )

    # If WMI filter: associate WMI filter.
    if gpo.wmi_filter is not None and _is_ad_target(target):
        step = PublicationStep(
            step_id="associate-wmi-filter",
            operation="associate_wmi_filter",
            target="ad",
            status="pending",
            detail=f"Associate WMI filter {gpo.wmi_filter.name!r}",
        )
        steps.append(step)
        rollback.append(
            PublicationStep(
                step_id="rollback-associate-wmi-filter",
                operation="disassociate_wmi_filter",
                target="ad",
                status="pending",
                detail="Disassociate WMI filter",
            )
        )

    # If links: update gPLink on target OUs.
    for link in gpo.links:
        if not _is_ad_target(target):
            continue
        step = PublicationStep(
            step_id=f"update-gplink-{link.id}",
            operation="update_gplink",
            target="ad",
            status="pending",
            detail=f"Update gPLink on {link.target}",
        )
        steps.append(step)
        rollback.append(
            PublicationStep(
                step_id=f"rollback-update-gplink-{link.id}",
                operation="restore_gplink",
                target="ad",
                status="pending",
                detail=f"Restore gPLink on {link.target}",
            )
        )

    return PublicationPlan(
        plan_id=plan_id,
        gpo_guid=gpo_guid,
        gpo_name=gpo.name,
        target=target,
        state="draft",
        steps=tuple(steps),
        rollback_plan=tuple(rollback),
        risk_level=_assess_risk(gpo),
    )


def _gpt_ini_step_lines(version_half: GptVersionHalf | None) -> list[str]:
    """PowerShell lines for the ``update_gpt_ini`` step (Windows PowerShell 5.1).

    The packed version is read, unpacked, and only the half named by
    ``version_half`` is incremented in place, so a user-side publication can
    never move the machine counter (and vice versa) no matter how the value
    has moved since the plan was generated.

    Idempotence marker (``gpt.ini.studio-marker``) is per half: ``machine=<n>``
    and/or ``user=<n>`` lines record the half value this script last left on
    SYSVOL. A half sitting at its marker value is already applied and the
    script changes nothing; a half not at its marker value is incremented from
    the value read at run time. The untouched half is never rewritten, so
    independent movers between runs (GPMC authoring, or a different plan) are
    detected as "already applied" versus "needs the bump" without being
    clobbered.
    """
    if version_half is None:
        return [
            "# No machine or user SYSVOL content in this plan; the GPT.INI",
            "# version is not incremented (nothing for clients to reprocess).",
            (
                'Write-PlanLog "GPT.INI version not incremented: no machine or '
                'user content in this plan."'
            ),
            "",
        ]
    halves = ("machine", "user") if version_half == "both" else (version_half,)
    halves_ps = ", ".join(f"'{h}'" for h in halves)
    return [
        "# Increment GPT.INI version counter "
        f"({_GPT_VERSION_HALF_LABELS[version_half]}); idempotent per half.",
        (
            "$gptIniPath = Join-Path $env:SystemRoot "
            '"SYSVOL\\domain\\Policies\\$($GpoGuid)\\gpt.ini"'
        ),
        (
            "$gptMarkerPath = Join-Path $env:SystemRoot "
            '"SYSVOL\\domain\\Policies\\$($GpoGuid)\\gpt.ini.studio-marker"'
        ),
        f"$publishHalves = @({halves_ps})",
        "if (Test-Path $gptIniPath) {",
        "    $gptContent = Get-Content $gptIniPath -Raw",
        "    $versionMatch = [regex]::Match($gptContent, 'Version=(\\d+)')",
        "    if (-not $versionMatch.Success) {",
        (
            "        throw \"GPT.INI at $gptIniPath has no Version= line; "
            'refusing to guess the packed version."'
        ),
        "    }",
        "    $currentVersion = [int64]$versionMatch.Groups[1].Value",
        "    # Packed 32-bit field: machine is the low 16-bit half, user the",
        "    # high 16-bit half (measured: user-only edit 0 -> 65536;",
        "    # Version=0x0002000A is user 2, machine 10). Only the published",
        "    # half moves, in place, from the value read at run time.",
        "    $halfValues = @{",
        "        machine = [int]($currentVersion % 65536)",
        "        user    = [int][math]::Truncate($currentVersion / 65536)",
        "    }",
        "    $markerHalfValues = @{}",
        "    if (Test-Path $gptMarkerPath) {",
        "        foreach ($markerLine in (Get-Content $gptMarkerPath)) {",
        "            if ($markerLine -match '^\\s*(machine|user)\\s*=\\s*(\\d+)\\s*$') {",
        "                $markerHalfValues[$Matches[1]] = [int]$Matches[2]",
        "            }",
        "        }",
        "    }",
        "    $changedHalves = @()",
        "    foreach ($half in $publishHalves) {",
        "        $markerHasHalf = $markerHalfValues.ContainsKey($half)",
        (
            "        $halfAtMarkerValue = "
            "$markerHasHalf -and $halfValues[$half] -eq $markerHalfValues[$half]"
        ),
        "        if ($halfAtMarkerValue) {",
        (
            '            Write-PlanLog "GPT.INI $half half already at '
            '$($halfValues[$half]); no change."'
        ),
        "        } else {",
        "            $changedHalves += $half",
        "        }",
        "    }",
        "    if ($changedHalves.Count -gt 0) {",
        "        foreach ($half in $changedHalves) {",
        "            $halfValues[$half] = ($halfValues[$half] + 1) % 65536",
        "        }",
        "        $newVersion = $halfValues['user'] * 65536 + $halfValues['machine']",
        (
            "        if ($PSCmdlet.ShouldProcess($gptIniPath, "
            "'Update GPT.INI version')) {"
        ),
        (
            "            $gpt = $gptContent -replace 'Version=\\d+', "
            '"Version=$newVersion"'
        ),
        "            if (-not ($gpt -match '(?m)^Version=')) {",
        '                $gpt = "Version=$newVersion`r`n" + $gpt',
        "            }",
        "            Set-Content -Path $gptIniPath -Value $gpt -Encoding ASCII",
        "            foreach ($half in $changedHalves) {",
        "                $markerHalfValues[$half] = $halfValues[$half]",
        "            }",
        "            $markerLines = @()",
        "            foreach ($half in @('machine', 'user')) {",
        "                if ($markerHalfValues.ContainsKey($half)) {",
        '                    $markerLines += "$half=$($markerHalfValues[$half])"',
        "                }",
        "            }",
        (
            "            Set-Content -Path $gptMarkerPath -Value $markerLines "
            "-Encoding ASCII"
        ),
        "        }",
        "    }",
        "}",
        "",
    ]


def generate_publication_script(plan: PublicationPlan) -> PowerShellPublicationScript:
    """Generate an administrator-review PowerShell script for ``plan``.

    The returned script is a *review artifact*, not an automated publisher.
    Any operation without Plan 033 Windows evidence causes an early, non-zero
    refusal before AD or SYSVOL is contacted. Verified operation generators
    remain behind the explicit allowlist for later promotion.
    """
    unverified = tuple(
        step
        for step in plan.steps
        if step.operation not in _WINDOWS_VERIFIED_OPERATIONS
    )
    if unverified:
        review_lines = [
            "# Generated by GPO Studio for administrator review.",
            "# No publication operation in this plan is Windows-verified.",
            "# This script fails closed before contacting AD or SYSVOL.",
            "[CmdletBinding()]",
            "param()",
            "$ErrorActionPreference = 'Stop'",
            f"$PlanId = {_ps_quote(plan.plan_id)}",
            f"$GpoGuid = {_ps_quote(_guid_with_braces(plan.gpo_guid))}",
            f"$GpoName = {_ps_quote(plan.gpo_name)}",
            "",
        ]
        for step in unverified:
            detail = _ps_sanitize_comment(step.detail)
            review_lines.append(
                f"Write-Warning {_ps_quote(f'NOT WINDOWS-VERIFIED: {step.operation} — {detail}')}"
            )
        review_lines.extend([
            "",
            "Write-Error 'Publication refused: this plan contains unverified operations.'",
            "exit 1",
            "",
        ])
        return PowerShellPublicationScript(
            script_text="\n".join(review_lines),
            plan_id=plan.plan_id,
            gpo_guid=plan.gpo_guid,
            is_idempotent=False,
            estimated_duration_seconds=0,
        )

    lines: list[str] = [
        "# Generated by GPO Studio. Review before running with delegated GPO rights.",
        "# This script is produced for administrator review and does not write directly.",
        "#Requires -Modules GroupPolicy, ActiveDirectory",
        "[CmdletBinding(SupportsShouldProcess=$true)]",
        "param()",
        "$ErrorActionPreference = 'Stop'",
        f"$PlanId = {_ps_quote(plan.plan_id)}",
        f"$GpoGuid = {_ps_quote(_guid_with_braces(plan.gpo_guid))}",
        f"$GpoName = {_ps_quote(plan.gpo_name)}",
        "",
        "function Write-PlanLog {",
        "    param([string]$Message)",
        "    Write-Information \"[GPO Studio $PlanId] $Message\" -InformationAction Continue",
        "}",
        "",
        "$gpo = Get-GPO -Guid $GpoGuid -ErrorAction SilentlyContinue",
        "if (-not $gpo) {",
        "    Write-PlanLog \"GPO not found by GUID; attempting by name.\"",
        "    $gpo = Get-GPO -Name $GpoName -ErrorAction SilentlyContinue",
        "}",
        "if (-not $gpo) {",
        "    throw \"GPO $GpoName ($GpoGuid) was not found.\"",
        "}",
        "$incompleteSteps = 0",
        "",
    ]

    estimated_seconds = 30
    write_steps = [s for s in plan.steps if s.operation != "update_gpt_ini"]

    for step in plan.steps:
        match step.operation:
            case "update_gpt_ini":
                # Half-aware increment; see _gpt_ini_step_lines for the marker
                # semantics and the evidence behind the packing.
                lines.extend(_gpt_ini_step_lines(step.version_half))
            case "write_registry_pol":
                side = "Machine" if "machine" in step.step_id else "User"
                lines.extend([
                    f"# {step.detail}",
                    (
                        "$polPath = Join-Path $env:SystemRoot "
                        f'"SYSVOL\\domain\\Policies\\$($GpoGuid)\\{side}\\Registry.pol"'
                    ),
                    "# Rollback: restore previous Registry.pol from backup.",
                    f"Write-PlanLog \"{_ps_sanitize_comment(step.detail)}\"",
                    "if ($PSCmdlet.ShouldProcess($polPath, 'Write Registry.pol')) {",
                    "    # Stage artifact next to script or pull from Studio store.",
                    (
                        f'    Copy-Item -Path "$PSScriptRoot\\$($GpoGuid)\\{side}\\Registry.pol" '
                        "-Destination $polPath -Force"
                    ),
                    "}",
                    "",
                ])
            case "copy_gpp_xml":
                # detail is "Copy <Side>/Preferences/<filename>"
                if step.detail.startswith("Copy "):
                    rel_path = step.detail[len("Copy "):].strip()
                else:
                    rel_path = step.detail.strip()
                rel_path_ps = rel_path.replace("/", "\\")
                lines.extend([
                    f"# {step.detail}",
                    f"Write-PlanLog \"{_ps_sanitize_comment(step.detail)}\"",
                    "# Rollback: remove the copied GPP XML file.",
                    f"$sourcePath = \"$PSScriptRoot\\$($GpoGuid)\\{rel_path_ps}\"",
                    (
                        f"$targetPath = Join-Path $env:SystemRoot "
                        f"\"SYSVOL\\domain\\Policies\\$($GpoGuid)\\{rel_path_ps}\""
                    ),
                    "if ($PSCmdlet.ShouldProcess($targetPath, 'Copy GPP XML')) {",
                    "    Copy-Item -Path $sourcePath -Destination $targetPath -Force",
                    "}",
                    "",
                ])
            case "update_nt_security_descriptor":
                lines.extend([
                    "# Update GPO security filtering.",
                    "# Rollback: capture current ACL with Get-GPPermission -All before applying.",
                    _not_implemented_warning(step.operation),
                    "$incompleteSteps++",
                    "if ($PSCmdlet.ShouldProcess($GpoGuid, 'Update security filters')) {",
                    "    # Reconcile security filters via Set-GPPermission (manual review)",
                    "}",
                    "",
                ])
            case "associate_wmi_filter":
                lines.extend([
                    "# Associate WMI filter with GPO.",
                    "# Rollback: remove WMI filter association.",
                    _not_implemented_warning(step.operation),
                    "$incompleteSteps++",
                    "if ($PSCmdlet.ShouldProcess($GpoGuid, 'Associate WMI filter')) {",
                    "    # Associate via Set-ADObject or GPMC COM API (manual review)",
                    "}",
                    "",
                ])
            case "update_gplink":
                # detail is "Update gPLink on <DN>"
                parts = step.detail.split(" on ", 1)
                target_dn = parts[1].strip() if len(parts) == 2 else step.detail.strip()
                lines.extend([
                    f"# {step.detail}",
                    "# Rollback: remove the added GPLink if this script created it.",
                    f"$targetDn = {_ps_quote(target_dn)}",
                    _not_implemented_warning(step.operation),
                    "$incompleteSteps++",
                    "if ($PSCmdlet.ShouldProcess($targetDn, 'Update gPLink')) {",
                    "    # Use Set-ADObject to update gPLink attribute (manual review required)",
                    "}",
                    "",
                ])
            case _:
                raise AssertionError(
                    f"Expected code to be unreachable, but got: {step.operation!r}"
                )

    estimated_seconds = max(30, len(write_steps) * 15)

    lines.extend([
        "if ($incompleteSteps -gt 0) {",
        (
            '    Write-Warning "$incompleteSteps operation(s) require manual '
            'execution. Review warnings above."'
        ),
        "    exit 1",
        "}",
        (
            'Write-PlanLog "Publication script completed. Review warnings above '
            'for operations requiring manual execution."'
        ),
        "",
    ])

    # The script is idempotent only when every step is implemented with an
    # idempotent check. AD operations (security descriptor, WMI filter, gPLink)
    # are not implemented, so their presence makes the script non-idempotent.
    is_idempotent = bool(plan.steps) and all(
        s.operation in _WINDOWS_VERIFIED_OPERATIONS for s in plan.steps
    )

    return PowerShellPublicationScript(
        script_text="\n".join(lines),
        plan_id=plan.plan_id,
        gpo_guid=plan.gpo_guid,
        is_idempotent=is_idempotent,
        estimated_duration_seconds=estimated_seconds,
    )


def _is_valid_dn(value: str) -> bool:
    return bool(re.match(r"^(?:CN|OU|DC)=[^,=]+(?:,(?:CN|OU|DC)=[^,=]+)+$", value, re.IGNORECASE))


def _is_valid_guid(value: str) -> bool:
    return bool(re.match(
        r"^\{?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}?$",
        value,
    ))


def validate_publication_plan(
    plan: PublicationPlan,
    *,
    store: ArtifactStore | None = None,
) -> tuple[InteropIssue, ...]:
    """Validate a publication plan before execution."""
    issues: list[InteropIssue] = []

    if not plan.gpo_guid.strip():
        issues.append(
            InteropIssue(
                level="error",
                check="gpo_guid",
                message="GPO GUID is empty.",
                component="plan",
            )
        )
    elif not _is_valid_guid(plan.gpo_guid):
        issues.append(
            InteropIssue(
                level="error",
                check="gpo_guid",
                message=f"GPO GUID {plan.gpo_guid!r} is not valid.",
                component="plan",
            )
        )

    if not plan.steps:
        issues.append(
            InteropIssue(
                level="error",
                check="steps",
                message="Plan has no steps.",
                component="plan",
            )
        )

    write_operations = {
        "write_registry_pol",
        "copy_gpp_xml",
        "update_nt_security_descriptor",
        "associate_wmi_filter",
        "update_gplink",
    }
    write_steps = [s for s in plan.steps if s.operation in write_operations]
    rollback_step_ids = {s.step_id for s in plan.rollback_plan}
    uncovered = [
        s.step_id
        for s in write_steps
        if f"rollback-{s.step_id}" not in rollback_step_ids
    ]
    if uncovered:
        issues.append(
            InteropIssue(
                level="error",
                check="rollback_coverage",
                message=f"Rollback plan does not cover write steps: {', '.join(uncovered)}.",
                component="rollback_plan",
            )
        )

    for step in plan.steps:
        if step.target not in ("ad", "sysvol", "both"):
            issues.append(
                InteropIssue(
                    level="error",
                    check="target",
                    message=f"Step {step.step_id!r} has invalid target {step.target!r}.",
                    component=f"steps/{step.step_id}",
                )
            )
        if step.operation == "update_gplink" and step.detail:
            # Extract DN from detail text like "Update gPLink on OU=...,DC=...".
            parts = step.detail.split(" on ", 1)
            if len(parts) == 2 and not _is_valid_dn(parts[1]):
                issues.append(
                    InteropIssue(
                        level="error",
                        check="link_target",
                        message=f"Step {step.step_id!r} targets an invalid DN.",
                        component=f"steps/{step.step_id}",
                    )
                )

    if store is not None:
        for step in plan.steps:
            for artifact_id in step.artifact_ids:
                artifact = store.get_artifact(artifact_id, include_content=True)
                if artifact is None:
                    issues.append(
                        InteropIssue(
                            level="error",
                            check="artifact_exists",
                            message=f"Artifact {artifact_id!r} referenced by step "
                                    f"{step.step_id!r} was not found in the store.",
                            component=f"steps/{step.step_id}",
                        )
                    )
                    continue
                if detect_secrets(artifact.content):
                    issues.append(
                        InteropIssue(
                            level="error",
                            check="artifact_secrets",
                            message=f"Artifact {artifact_id!r} contains potential secrets.",
                            component=f"steps/{step.step_id}",
                        )
                    )

    return tuple(issues)
