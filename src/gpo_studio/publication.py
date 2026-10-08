"""Planning/modeling layer for GPO publication.

This module does NOT publish to Active Directory or SYSVOL. It builds a typed,
review-only :class:`PublicationPlan` from a :class:`~gpo_studio.model.GPO`: an
account of what an administrator would have to do to publish it, which the
Plan 034 publication-completeness lane compares with what Windows produces.

It emits no script. The PowerShell script generator that used to live here
copied files straight into SYSVOL, which ``docs/live-publication.md`` forbids,
and was retired by the 2026-10-07 operator ruling
(``docs/direction-2026-10-07-plan-034-completion.md``). Publication goes
through the native GPMC backup and ``Import-GPO`` plus the reviewed
``apply.ps1``, and nothing else; the web process never writes to AD or SYSVOL.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from typing import Literal

from .canonical import canonical_json_bytes
from .export import extension_registration
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
# Measured on Windows Server 2025 (2026-09-03): a machine-side-only edit moved
# the value 0 -> 1; a user-side-only edit moved it 0 -> 65536 with the machine
# half untouched; a GPO published by Windows itself showed
# ``Version=0x0002000A`` (user 2, machine 10). The publication-completeness
# finalizer unpacks the observed value itself (``_unpack_version``).
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
    # publishes. The halves move independently (see GptVersionHalf), so the
    # half must be recorded explicitly rather than guessed at run time.
    version_half: GptVersionHalf | None = None
    # The GPO-relative SYSVOL path this step writes, for steps that write one.
    # Typed rather than left implicit in `detail`, so a completeness comparison
    # against a real SYSVOL tree reads data instead of parsing prose -- the
    # Plan 034 lane does exactly that, and a detail string is not an interface.
    sysvol_path: str | None = None


def _step_payload(step: PublicationStep) -> dict[str, object]:
    """The part of a step that determines its effect, for `payload_digest`.

    `status` is excluded: it moves from pending to completed as the plan runs,
    and a digest that changed under execution could not bind an approval taken
    before it.
    """
    return {
        "step_id": step.step_id,
        "operation": step.operation,
        "target": step.target,
        "detail": step.detail,
        "artifact_ids": list(step.artifact_ids),
        "version_half": step.version_half,
        "sysvol_path": step.sysvol_path,
    }


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

    @property
    def payload_digest(self) -> str:
        """SHA-256 over what this plan DOES, so an approval can bind to it.

        `plan_id` is a random `uuid4` prefix with no relationship to the plan's
        content, so an approval that names one attests to a name (WI-050). This
        digest covers the operative shape instead: the GPO addressed, every step
        in order, the rollback steps, and the two fields that decide how much
        scrutiny the plan gets -- `risk_level` and `requires_enhanced_approval`.
        Escalating a plan's risk after approval is itself a content change.

        Deliberately EXCLUDED, because they move while a plan is worked and
        would make the digest unstable exactly when it is being checked:
        `plan_id`, `state`, `approved_by`, `approved_at`, `published_at`, and
        each step's `status`. What remains is fixed for a given set of actions.

        Computed rather than stored: a stored digest is one more field that can
        be set to whatever the constructor is handed, which is the defect this
        closes rather than a fix for it.
        """
        payload = {
            "gpo_guid": self.gpo_guid,
            "gpo_name": self.gpo_name,
            "target": self.target,
            "risk_level": self.risk_level,
            "requires_enhanced_approval": self.requires_enhanced_approval,
            "steps": [_step_payload(step) for step in self.steps],
            "rollback_plan": [_step_payload(step) for step in self.rollback_plan],
        }
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()

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


# GPMC settings count threshold for medium risk.
_REGISTRY_SETTINGS_MEDIUM_RISK_THRESHOLD = 100


def _new_plan_id() -> str:
    return f"plan-{uuid.uuid4().hex[:12]}"


def _artifact_id_for(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _guid_without_braces(guid: str) -> str:
    return guid.strip("{}").lower()


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
        if gpo.cse_metadata:
            preserved_files = sum(len(entry.files) for entry in gpo.cse_metadata)
            steps.append(
                PublicationStep(
                    step_id="unsupported-preserved-cse-content",
                    operation="unsupported_cse_content",
                    target="sysvol",
                    status="pending",
                    detail=(
                        "Publication refused: the model contains "
                        f"{len(gpo.cse_metadata)} preserved CSE metadata entry/entries "
                        f"and {preserved_files} preserved file(s); this planner cannot "
                        "publish their opaque content without producing a partial GPO"
                    ),
                )
            )

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
                sysvol_path="GPT.INI" if version_half is not None else None,
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
                sysvol_path="Machine/Registry.pol",
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
                sysvol_path="User/Registry.pol",
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
                    sysvol_path=f"{side_dir}/Preferences/{filename}",
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

    # The GPO's comment lives in SYSVOL as GPO.cmt. `powershell_plan` has always
    # passed it to New-GPO -Comment; the typed step list never named the file,
    # so a plan read as an inventory of what publication writes was short by one
    # (WI-058). Measured: a GPO created with a comment has GPO.cmt, one created
    # without it has no such file, so the step is emitted on the same condition.
    if gpo.description and _is_sysvol_target(target):
        steps.append(
            PublicationStep(
                step_id="write-gpo-comment",
                operation="write_gpo_comment",
                target="sysvol",
                status="pending",
                detail="Write GPO.cmt",
                sysvol_path="GPO.cmt",
            )
        )
        rollback.append(
            PublicationStep(
                step_id="rollback-write-gpo-comment",
                operation="restore_gpo_comment",
                target="sysvol",
                status="pending",
                detail="Restore GPO.cmt from backup",
            )
        )

    # Register the client-side extensions the SYSVOL content needs, without
    # which every file above is inert (WI-057). The values come from export.py's
    # measured vocabulary rather than being restated here; the attributes are on
    # the directory object, which is why these steps target AD even though what
    # makes them necessary was written to SYSVOL.
    registration = extension_registration(gpo)
    if registration.unverified_families:
        steps.append(
            PublicationStep(
                step_id="unsupported-extension-registration",
                operation="unsupported_extension_registration",
                target=target,
                status="pending",
                detail=(
                    "Publication refused: extension metadata has never been "
                    "captured for "
                    f"{', '.join(registration.unverified_families)}, so the "
                    "extension list this content requires cannot be stated"
                ),
            )
        )
    else:
        for side, value in (
            ("machine", registration.machine),
            ("user", registration.user),
        ):
            if not value:
                continue
            attribute = (
                "gPCMachineExtensionNames" if side == "machine" else "gPCUserExtensionNames"
            )
            if _is_ad_target(target):
                steps.append(
                    PublicationStep(
                        step_id=f"register-{side}-extensions",
                        operation="update_extension_lists",
                        target="ad",
                        status="pending",
                        detail=f"Set {attribute} to {value}",
                    )
                )
                rollback.append(
                    PublicationStep(
                        step_id=f"rollback-register-{side}-extensions",
                        operation="restore_extension_lists",
                        target="ad",
                        status="pending",
                        detail=f"Restore {attribute} from backup",
                    )
                )
            else:
                # SYSVOL-only, with content that no extension will process
                # because the attribute lives somewhere this plan does not go.
                steps.append(
                    PublicationStep(
                        step_id=f"unreachable-{side}-extensions",
                        operation="extension_lists_unreachable",
                        target=target,
                        status="pending",
                        detail=(
                            f"Publication refused: {attribute} must be set to "
                            f"{value} for this content to apply, and a "
                            "SYSVOL-only plan cannot write a directory attribute"
                        ),
                    )
                )

    # A disabled side lives in the directory object's `flags` attribute, which
    # `apply.ps1` sets through `GpoStatus` and the native backup carries as
    # `Options`. No step here writes it, so a plan executed as written would
    # publish the side ENABLED -- the inverse of what the author set, and
    # silent, because every file it names would still be right (WI-070). The
    # planner refuses rather than inventing a step no lane has measured.
    disabled_sides = [
        side
        for side, enabled in (
            ("computer", gpo.computer_enabled),
            ("user", gpo.user_enabled),
        )
        if not enabled
    ]
    if disabled_sides:
        steps.append(
            PublicationStep(
                step_id="unsupported-side-status",
                operation="unsupported_side_status",
                target=target,
                status="pending",
                detail=(
                    "Publication refused: the "
                    f"{' and '.join(disabled_sides)} side"
                    f"{'s are' if len(disabled_sides) > 1 else ' is'} disabled, and "
                    "this planner has no step that sets the GPO's flags attribute, "
                    "so the plan as written would publish "
                    f"{'them' if len(disabled_sides) > 1 else 'it'} enabled"
                ),
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


def _is_valid_dn(value: str) -> bool:
    return bool(re.match(r"^(?:CN|OU|DC)=[^,=]+(?:,(?:CN|OU|DC)=[^,=]+)+$", value, re.IGNORECASE))


def _is_valid_guid(value: str) -> bool:
    return bool(re.match(
        r"^\{?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}?$",
        value,
    ))


def planned_sysvol_paths(plan: PublicationPlan) -> tuple[str, ...]:
    """Every GPO-relative SYSVOL path *plan* says it would write, sorted.

    The Plan 034 completeness lane compares this against the tree Windows
    actually materialises, so it is the plan's own claim about its output and
    must come from the steps rather than from a second derivation that could
    disagree with them.
    """
    return tuple(
        sorted(
            {step.sysvol_path for step in plan.steps if step.sysvol_path is not None},
            key=str.casefold,
        )
    )


def validate_publication_plan(plan: PublicationPlan) -> tuple[InteropIssue, ...]:
    """Validate a publication plan before execution.

    Structural only. The optional artifact-store cross-check that used to live
    here went with ``artifact_store.py`` (deleted by the 2026-10-07 ruling:
    delivering script or executable payloads is out of scope for 1.x); a step's
    ``artifact_ids`` are content digests of what the planner itself serialized,
    and ``payload_digest`` binds them.
    """
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

    if any(step.operation == "unsupported_cse_content" for step in plan.steps):
        issues.append(
            InteropIssue(
                level="error",
                check="unsupported_cse_content",
                message=(
                    "Plan contains preserved CSE metadata or files that the publication "
                    "planner cannot reproduce; publishing it would create a partial GPO."
                ),
                component="plan",
            )
        )

    if any(step.operation == "unsupported_extension_registration" for step in plan.steps):
        issues.append(
            InteropIssue(
                level="error",
                check="unsupported_extension_registration",
                message=(
                    "Plan carries GPP content whose extension metadata has never been "
                    "captured, so the extension list it requires cannot be stated; "
                    "publishing it would create a GPO that applies nothing."
                ),
                component="plan",
            )
        )

    if any(step.operation == "unsupported_side_status" for step in plan.steps):
        issues.append(
            InteropIssue(
                level="error",
                check="unsupported_side_status",
                message=(
                    "The GPO has a disabled computer or user side, and the plan has no "
                    "step that sets the directory object's flags attribute; publishing "
                    "it as written would leave that side enabled (WI-070)."
                ),
                component="plan",
            )
        )

    if any(step.operation == "extension_lists_unreachable" for step in plan.steps):
        issues.append(
            InteropIssue(
                level="error",
                check="extension_lists_unreachable",
                message=(
                    "Plan writes SYSVOL content that no client-side extension would "
                    "process, because the extension-list attributes live on the "
                    "directory object and this plan targets SYSVOL only."
                ),
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

    return tuple(issues)
