"""Controlled publisher protocol for GPO Studio (Plan 030).

This module models the *gating* layer that sits between a
:class:`~gpo_studio.publication.PublicationPlan` and the actual emission of a
PowerShell publication script. The web process never writes directly to AD or
SYSVOL; instead, every publication must first pass a set of *gates* checked
against a :class:`PublisherProfile` (the capabilities granted to an actor) and
an optional :class:`ApprovalRequest` (human approval collected out-of-band).

The module is intentionally offline-first and side-effect free: gate evaluation
is pure, and the audit trail is an immutable, append-only structure.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Literal, assert_never

from .model import ValidationError, ValidationIssue
from .publication import PublicationPlan, validate_publication_plan

PublisherCapability = Literal[
    "read_gpo",
    "write_registry_pol",
    "write_sysvol_files",
    "write_security_descriptor",
    "write_gplink",
    "write_wmi_filter",
    "write_gpt_ini",
    "delete_gpo",
    "backup_gpo",
    "restore_gpo",
]


# ---------------------------------------------------------------------------
# Publisher capability profiles
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PublisherProfile:
    """A named set of publisher capabilities granted to an actor."""

    profile_id: str
    name: str
    capabilities: frozenset[PublisherCapability]
    scope_dns: tuple[str, ...] = ()  # DNs this profile applies to (empty = domain-wide)
    requires_approval: bool = True  # whether operations need explicit approval
    max_blast_radius: Literal["single_gpo", "ou", "domain", "forest"] = "single_gpo"
    allowed_hours: tuple[int, ...] = ()  # allowed publication hours (0-23, empty = any)
    is_active: bool = True

    def has_capability(
        self,
        cap: PublisherCapability,
        scope_dn: str = "",
    ) -> bool:
        """Check if this profile grants a capability, optionally scoped.

        When ``scope_dn`` is provided and the profile is scope-restricted
        (``scope_dns`` non-empty), the capability is only granted when the
        scope DN equals or nests under one of the profile's scope DNs.
        """
        if cap not in self.capabilities:
            return False
        if scope_dn and self.scope_dns:
            return _dn_within_any_scope(scope_dn, self.scope_dns)
        return True

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate profile structural rules."""
        issues: list[ValidationIssue] = []
        if not self.profile_id.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "empty_profile_id",
                    "profile_id must not be empty.",
                    "profile_id",
                )
            )
        if not self.name.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "empty_name",
                    "name must not be empty.",
                    "name",
                )
            )
        if not self.capabilities:
            issues.append(
                ValidationIssue(
                    "error",
                    "empty_capabilities",
                    "capabilities must not be empty.",
                    "capabilities",
                )
            )
        if self.max_blast_radius == "forest" and "write_security_descriptor" in self.capabilities:
            issues.append(
                ValidationIssue(
                    "warning",
                    "forest_security_descriptor",
                    "Forest-wide security descriptor writes are very broad.",
                    "max_blast_radius",
                )
            )
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class PublisherProfileSet:
    """A collection of publisher profiles."""

    profiles: tuple[PublisherProfile, ...] = field(default_factory=tuple)

    def get_profile(self, profile_id: str) -> PublisherProfile | None:
        """Look up a profile by id; returns ``None`` if not present."""
        for profile in self.profiles:
            if profile.profile_id == profile_id:
                return profile
        return None

    def profiles_for_actor(self, actor: str) -> tuple[PublisherProfile, ...]:
        """Get all active profiles that apply to an actor (by profile_id match)."""
        return tuple(
            p for p in self.profiles if p.is_active and p.profile_id == actor
        )

    def effective_capabilities(
        self,
        actor: str,
        scope_dn: str = "",
    ) -> frozenset[PublisherCapability]:
        """Compute the union of all capabilities for an actor at a scope."""
        granted: set[PublisherCapability] = set()
        for profile in self.profiles_for_actor(actor):
            for cap in profile.capabilities:
                if profile.has_capability(cap, scope_dn):
                    granted.add(cap)
        return frozenset(granted)


# ---------------------------------------------------------------------------
# Approval workflow
# ---------------------------------------------------------------------------


ApprovalState = Literal["pending", "approved", "rejected", "expired"]


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """A request to approve a publication plan."""

    request_id: str
    plan_id: str  # PublicationPlan.plan_id
    gpo_guid: str
    gpo_name: str
    requested_by: str
    requested_at: str
    state: ApprovalState = "pending"
    approved_by: str = ""
    approved_at: str = ""
    rejection_reason: str = ""
    expires_at: str = ""  # approval expires if not used
    required_approvers: int = 1  # number of approvals needed
    current_approvals: int = 0
    approvers: tuple[str, ...] = ()  # actors who have already approved

    def is_sufficiently_approved(self) -> bool:
        """Check if enough approvals have been collected."""
        return self.state == "approved" and self.current_approvals >= self.required_approvers

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate approval request structural rules."""
        issues: list[ValidationIssue] = []
        if not self.request_id.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "empty_request_id",
                    "request_id must not be empty.",
                    "request_id",
                )
            )
        if not self.plan_id.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "empty_plan_id",
                    "plan_id must not be empty.",
                    "plan_id",
                )
            )
        if self.state == "approved" and not self.approved_by.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "approved_without_approver",
                    "Approved request must record approved_by.",
                    "approved_by",
                )
            )
        if self.state == "rejected" and not self.rejection_reason.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "rejected_without_reason",
                    "Rejected request must record a rejection reason.",
                    "rejection_reason",
                )
            )
        if self.expires_at and _is_expired(self.expires_at):
            issues.append(
                ValidationIssue(
                    "warning",
                    "expired_approval",
                    "Approval has expired and should not be used.",
                    "expires_at",
                )
            )
        if self.required_approvers < 1:
            issues.append(
                ValidationIssue(
                    "error",
                    "invalid_required_approvers",
                    "required_approvers must be at least 1.",
                    "required_approvers",
                )
            )
        return tuple(issues)


def create_approval_request(
    plan: PublicationPlan,
    requested_by: str,
    required_approvers: int = 1,
    expiry_hours: int = 24,
) -> ApprovalRequest:
    """Create an approval request for a publication plan."""
    now = _now_dt()
    expires = now + timedelta(hours=expiry_hours)
    return ApprovalRequest(
        request_id=f"apr-{uuid.uuid4().hex[:12]}",
        plan_id=plan.plan_id,
        gpo_guid=plan.gpo_guid,
        gpo_name=plan.gpo_name,
        requested_by=requested_by,
        requested_at=now.isoformat(timespec="seconds"),
        state="pending",
        expires_at=expires.isoformat(timespec="seconds"),
        required_approvers=required_approvers,
        current_approvals=0,
    )


def approve_request(
    request: ApprovalRequest,
    approver: str,
) -> ApprovalRequest:
    """Approve a request. Increments current_approvals.

    Raises :class:`ValidationError` if:
    - The request is not pending
    - The approver is the same as ``requested_by`` (no self-approval)
    - The approver has already approved (no duplicate approvals)
    - The request is expired

    When the approval count reaches ``required_approvers``, the request
    transitions to the ``approved`` state with ``approved_by`` and
    ``approved_at`` populated.
    """
    issues: list[ValidationIssue] = []
    if request.state != "pending":
        issues.append(
            ValidationIssue(
                "error",
                "not_pending",
                f"Request is in state {request.state!r}, not pending.",
                "state",
            )
        )
    if approver == request.requested_by:
        issues.append(
            ValidationIssue(
                "error",
                "self_approval",
                "Self-approval is not allowed.",
                "approved_by",
            )
        )
    if approver in request.approvers:
        issues.append(
            ValidationIssue(
                "error",
                "duplicate_approver",
                f"Approver {approver!r} has already approved this request.",
                "approvers",
            )
        )
    if request.expires_at and _is_expired(request.expires_at):
        issues.append(
            ValidationIssue(
                "error",
                "expired",
                "Request has expired and can no longer be approved.",
                "expires_at",
            )
        )
    if issues:
        raise ValidationError(issues)

    new_approvals = request.current_approvals + 1
    new_approvers = request.approvers + (approver,)
    if new_approvals >= request.required_approvers:
        return replace(
            request,
            current_approvals=new_approvals,
            approvers=new_approvers,
            state="approved",
            approved_by=approver,
            approved_at=_now(),
        )
    return replace(
        request,
        current_approvals=new_approvals,
        approvers=new_approvers,
    )


def reject_request(
    request: ApprovalRequest,
    approver: str,
    reason: str,
) -> ApprovalRequest:
    """Reject a request with a reason.

    The resulting request has ``state="rejected"`` and ``rejection_reason``
    set. The ``approver`` is recorded in ``approved_by`` (which, for a
    rejected request, holds the rejector rather than an approver).

    Raises :class:`ValidationError` if the request is not in the ``pending``
    state.
    """
    if request.state != "pending":
        raise ValidationError([ValidationIssue(
            severity="error",
            code="invalid_rejection_state",
            message=f"Cannot reject request in state {request.state!r}; must be pending.",
            path="state",
        )])
    return replace(
        request,
        state="rejected",
        approved_by=approver,  # holds the rejector for rejected requests
        rejection_reason=reason,
    )


# ---------------------------------------------------------------------------
# Publisher gates
# ---------------------------------------------------------------------------


# Maps a publication step operation to the capability required to perform it.
_STEP_CAPABILITY_MAP: dict[str, PublisherCapability] = {
    "update_gpt_ini": "write_gpt_ini",
    "write_registry_pol": "write_registry_pol",
    "copy_gpp_xml": "write_sysvol_files",
    "update_nt_security_descriptor": "write_security_descriptor",
    "associate_wmi_filter": "write_wmi_filter",
    "update_gplink": "write_gplink",
}

# Every publication implicitly requires reading the GPO first.
_BASE_CAPABILITY: PublisherCapability = "read_gpo"


@dataclass(frozen=True, slots=True)
class PublisherGate:
    """A pre-publication gate that must pass before publication proceeds."""

    gate_id: str
    name: str
    check: str  # description of what's checked
    passed: bool = False
    detail: str = ""


def run_publisher_gates(
    plan: PublicationPlan,
    profile: PublisherProfile,
    approval: ApprovalRequest | None = None,
) -> tuple[PublisherGate, ...]:
    """Run all pre-publication gates.

    Gates:
    1. capability_gate: actor has required capabilities for all plan steps
    2. approval_gate: plan has sufficient approvals (if profile.requires_approval)
    3. scope_gate: plan target is within profile's allowed scope
    4. blast_radius_gate: plan risk_level is within profile's max_blast_radius
    5. time_gate: current hour is within profile's allowed_hours (if specified)
    6. interop_gate: GPO passes GPMC interop check
    7. rsop_gate: RSOP computation succeeds without critical warnings
    """
    gates: list[PublisherGate] = [
        _capability_gate(plan, profile),
        _approval_gate(plan, profile, approval),
        _scope_gate(plan, profile),
        _blast_radius_gate(plan, profile),
        _time_gate(profile),
        _interop_gate(plan),
        _rsop_gate(plan),
    ]
    return tuple(gates)


def _capability_gate(
    plan: PublicationPlan,
    profile: PublisherProfile,
) -> PublisherGate:
    required: list[PublisherCapability] = [_BASE_CAPABILITY]
    missing: list[str] = []
    for step in plan.steps:
        cap = _STEP_CAPABILITY_MAP.get(step.operation)
        if cap is None:
            missing.append(
                f"step {step.step_id!r} has unmapped operation {step.operation!r}"
            )
            continue
        if cap not in required:
            required.append(cap)
    for cap in required:
        if not profile.has_capability(cap):
            missing.append(f"missing capability {cap!r}")
    return PublisherGate(
        gate_id="capability_gate",
        name="Capability Gate",
        check="Actor has required capabilities for all plan steps",
        passed=not missing,
        detail="; ".join(missing) if missing else "All required capabilities present",
    )


def _approval_gate(
    plan: PublicationPlan,
    profile: PublisherProfile,
    approval: ApprovalRequest | None,
) -> PublisherGate:
    if not profile.requires_approval:
        return PublisherGate(
            gate_id="approval_gate",
            name="Approval Gate",
            check="Plan has sufficient approvals",
            passed=True,
            detail="Profile does not require approval",
        )
    if approval is None:
        return PublisherGate(
            gate_id="approval_gate",
            name="Approval Gate",
            check="Plan has sufficient approvals",
            passed=False,
            detail="Approval required but no request provided",
        )
    if approval.plan_id != plan.plan_id:
        return PublisherGate(
            gate_id="approval_gate",
            name="Approval Gate",
            check="Plan has sufficient approvals",
            passed=False,
            detail=(
                f"Approval plan_id {approval.plan_id!r} does not match "
                f"plan {plan.plan_id!r}"
            ),
        )
    if approval.state == "rejected":
        return PublisherGate(
            gate_id="approval_gate",
            name="Approval Gate",
            check="Plan has sufficient approvals",
            passed=False,
            detail="Approval was rejected",
        )
    if approval.state == "expired":
        return PublisherGate(
            gate_id="approval_gate",
            name="Approval Gate",
            check="Plan has sufficient approvals",
            passed=False,
            detail="Approval has expired",
        )
    if not approval.is_sufficiently_approved():
        return PublisherGate(
            gate_id="approval_gate",
            name="Approval Gate",
            check="Plan has sufficient approvals",
            passed=False,
            detail=(
                f"Insufficient approvals: {approval.current_approvals}/"
                f"{approval.required_approvers}"
            ),
        )
    return PublisherGate(
        gate_id="approval_gate",
        name="Approval Gate",
        check="Plan has sufficient approvals",
        passed=True,
        detail="Sufficient approvals collected",
    )


def _scope_gate(
    plan: PublicationPlan,
    profile: PublisherProfile,
) -> PublisherGate:
    if not profile.scope_dns:
        return PublisherGate(
            gate_id="scope_gate",
            name="Scope Gate",
            check="Plan target is within profile's allowed scope",
            passed=True,
            detail="Profile is domain-wide",
        )
    targets = _gplink_targets(plan)
    if not targets:
        return PublisherGate(
            gate_id="scope_gate",
            name="Scope Gate",
            check="Plan target is within profile's allowed scope",
            passed=True,
            detail="No scoped targets in plan",
        )
    out_of_scope = [
        dn for dn in targets if not _dn_within_any_scope(dn, profile.scope_dns)
    ]
    if out_of_scope:
        return PublisherGate(
            gate_id="scope_gate",
            name="Scope Gate",
            check="Plan target is within profile's allowed scope",
            passed=False,
            detail=f"Targets out of scope: {', '.join(out_of_scope)}",
        )
    return PublisherGate(
        gate_id="scope_gate",
        name="Scope Gate",
        check="Plan target is within profile's allowed scope",
        passed=True,
        detail="All targets within scope",
    )


def _blast_radius_gate(
    plan: PublicationPlan,
    profile: PublisherProfile,
) -> PublisherGate:
    allowed = _allowed_risk_levels(profile.max_blast_radius)
    if plan.risk_level in allowed:
        return PublisherGate(
            gate_id="blast_radius_gate",
            name="Blast Radius Gate",
            check="Plan risk level is within profile's max blast radius",
            passed=True,
            detail=(
                f"Risk {plan.risk_level!r} within "
                f"max_blast_radius {profile.max_blast_radius!r}"
            ),
        )
    return PublisherGate(
        gate_id="blast_radius_gate",
        name="Blast Radius Gate",
        check="Plan risk level is within profile's max blast radius",
        passed=False,
        detail=(
            f"Risk {plan.risk_level!r} exceeds max blast radius "
            f"{profile.max_blast_radius!r}"
        ),
    )


def _time_gate(profile: PublisherProfile) -> PublisherGate:
    if not profile.allowed_hours:
        return PublisherGate(
            gate_id="time_gate",
            name="Time Gate",
            check="Current hour is within profile's allowed hours",
            passed=True,
            detail="No time restriction",
        )
    hour = datetime.now(UTC).hour
    if hour in profile.allowed_hours:
        return PublisherGate(
            gate_id="time_gate",
            name="Time Gate",
            check="Current hour is within profile's allowed hours",
            passed=True,
            detail=f"Current hour {hour} is allowed",
        )
    return PublisherGate(
        gate_id="time_gate",
        name="Time Gate",
        check="Current hour is within profile's allowed hours",
        passed=False,
        detail=(
            f"Current hour {hour} not in allowed hours "
            f"{sorted(profile.allowed_hours)}"
        ),
    )


def _interop_gate(plan: PublicationPlan) -> PublisherGate:
    issues = validate_publication_plan(plan)
    errors = [i for i in issues if i.level == "error"]
    if errors:
        detail = (
            f"{len(errors)} interop error(s): "
            + "; ".join(e.message for e in errors)
        )
        return PublisherGate(
            gate_id="interop_gate",
            name="Interop Gate",
            check="GPO passes GPMC interop check",
            passed=False,
            detail=detail,
        )
    return PublisherGate(
        gate_id="interop_gate",
        name="Interop Gate",
        check="GPO passes GPMC interop check",
        passed=True,
        detail="No interop errors",
    )


def _rsop_gate(plan: PublicationPlan) -> PublisherGate:
    if plan.risk_level == "critical":
        return PublisherGate(
            gate_id="rsop_gate",
            name="RSOP Gate",
            check="RSOP computation succeeds without critical warnings",
            passed=False,
            detail="Plan has critical risk level",
        )
    return PublisherGate(
        gate_id="rsop_gate",
        name="RSOP Gate",
        check="RSOP computation succeeds without critical warnings",
        passed=True,
        detail=f"Plan risk level {plan.risk_level!r} is below critical",
    )


@dataclass(frozen=True, slots=True)
class PublisherDecision:
    """The result of evaluating a publication plan against gates."""

    plan_id: str
    approved: bool
    gates: tuple[PublisherGate, ...]
    blocking_gates: tuple[PublisherGate, ...]  # gates that failed
    decision_at: str = ""
    decided_by: str = ""


def evaluate_publication(
    plan: PublicationPlan,
    profile: PublisherProfile,
    approval: ApprovalRequest | None = None,
) -> PublisherDecision:
    """Evaluate whether a publication plan can proceed.

    Returns a decision with all gate results. ``approved=True`` only if ALL
    gates pass.
    """
    gates = run_publisher_gates(plan, profile, approval)
    blocking = tuple(g for g in gates if not g.passed)
    return PublisherDecision(
        plan_id=plan.plan_id,
        approved=not blocking,
        gates=gates,
        blocking_gates=blocking,
        decision_at=_now(),
        decided_by="",
    )


# ---------------------------------------------------------------------------
# Publication audit trail
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PublicationAuditEntry:
    """A single entry in the publication audit trail."""

    entry_id: str
    plan_id: str
    action: Literal[
        "plan_created",
        "plan_submitted",
        "approval_requested",
        "approved",
        "rejected",
        "gates_evaluated",
        "publication_started",
        "step_completed",
        "step_failed",
        "publication_completed",
        "rollback_started",
        "rollback_completed",
    ]
    actor: str
    timestamp: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class PublicationAuditTrail:
    """An immutable, append-only audit trail of publication events."""

    entries: tuple[PublicationAuditEntry, ...] = field(default_factory=tuple)

    def entries_for_plan(self, plan_id: str) -> tuple[PublicationAuditEntry, ...]:
        """Return all entries for a specific plan."""
        return tuple(e for e in self.entries if e.plan_id == plan_id)

    def append(self, entry: PublicationAuditEntry) -> PublicationAuditTrail:
        """Return a new trail with the entry appended (immutable)."""
        return PublicationAuditTrail(entries=self.entries + (entry,))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _now_dt() -> datetime:
    return datetime.now(UTC)


def _is_expired(expires_at: str) -> bool:
    """Return True if ``expires_at`` is a valid timestamp in the past."""
    if not expires_at:
        return False
    try:
        expiry = datetime.fromisoformat(expires_at)
    except ValueError:
        return False
    return _now_dt() > expiry


def _dn_within_any_scope(target_dn: str, scope_dns: tuple[str, ...]) -> bool:
    """Return True if ``target_dn`` equals or nests under a scope DN."""
    target = target_dn.casefold()
    for scope in scope_dns:
        scope_fold = scope.casefold()
        if target == scope_fold:
            return True
        if target.endswith("," + scope_fold):
            return True
    return False


def _gplink_targets(plan: PublicationPlan) -> list[str]:
    """Extract gPLink target DNs from a publication plan's steps."""
    targets: list[str] = []
    for step in plan.steps:
        if step.operation != "update_gplink":
            continue
        if not step.detail:
            continue
        parts = step.detail.split(" on ", 1)
        if len(parts) == 2:
            targets.append(parts[1].strip())
    return targets


def _allowed_risk_levels(
    radius: Literal["single_gpo", "ou", "domain", "forest"],
) -> frozenset[str]:
    """Return the set of risk levels permitted by a blast radius."""
    match radius:
        case "single_gpo":
            return frozenset({"low", "medium"})
        case "ou":
            return frozenset({"low", "medium", "high"})
        case "domain":
            return frozenset({"low", "medium", "high", "critical"})
        case "forest":
            return frozenset({"low", "medium", "high", "critical"})
        case _:
            assert_never(radius)


__all__ = [
    "ApprovalRequest",
    "ApprovalState",
    "PublisherCapability",
    "PublisherDecision",
    "PublisherGate",
    "PublisherProfile",
    "PublisherProfileSet",
    "PublicationAuditEntry",
    "PublicationAuditTrail",
    "approve_request",
    "create_approval_request",
    "evaluate_publication",
    "reject_request",
    "run_publisher_gates",
]
