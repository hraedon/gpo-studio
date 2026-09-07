from __future__ import annotations

from dataclasses import replace

import pytest

from gpo_studio.model import GPO, GPOLink, RegistrySetting, ValidationError
from gpo_studio.publication import PublicationStep, generate_publication_plan
from gpo_studio.publisher import (
    ApprovalRequest,
    PublicationAuditEntry,
    PublicationAuditTrail,
    PublisherDecision,
    PublisherProfile,
    PublisherProfileSet,
    approve_request,
    create_approval_request,
    evaluate_publication,
    reject_request,
    run_publisher_gates,
)

_GUID = "11111111-2222-3333-4444-555555555555"


def _gpo_with_registry() -> GPO:
    return GPO(
        guid=_GUID,
        name="Registry Policy",
        settings=(
            RegistrySetting(
                id="s1",
                side="computer",
                hive="HKLM",
                key=r"Software\Policies\Synthetic",
                value_name="Enabled",
                registry_type="REG_DWORD",
                value=1,
            ),
        ),
    )


def _full_profile(**overrides: object) -> PublisherProfile:
    base: dict[str, object] = {
        "profile_id": "pub",
        "name": "Publisher",
        "capabilities": frozenset({
            "read_gpo",
            "write_gpt_ini",
            "write_registry_pol",
            "write_sysvol_files",
            "write_security_descriptor",
            "write_wmi_filter",
            "write_extension_lists",
            "write_gplink",
        }),
        "requires_approval": False,
        "max_blast_radius": "single_gpo",
        "allowed_hours": (),
    }
    base.update(overrides)
    return PublisherProfile(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# PublisherProfile
# ---------------------------------------------------------------------------


def test_publisher_profile_valid() -> None:
    profile = PublisherProfile(
        profile_id="p1",
        name="Publisher",
        capabilities=frozenset({"read_gpo", "write_registry_pol"}),
    )
    issues = profile.validate()
    assert not any(i.severity == "error" for i in issues)


def test_publisher_profile_empty_id_error() -> None:
    profile = PublisherProfile(
        profile_id="",
        name="P",
        capabilities=frozenset({"read_gpo"}),
    )
    issues = profile.validate()
    assert any(i.code == "empty_profile_id" and i.severity == "error" for i in issues)


def test_publisher_profile_empty_name_error() -> None:
    profile = PublisherProfile(
        profile_id="p1",
        name="",
        capabilities=frozenset({"read_gpo"}),
    )
    issues = profile.validate()
    assert any(i.code == "empty_name" and i.severity == "error" for i in issues)


def test_publisher_profile_empty_capabilities_error() -> None:
    profile = PublisherProfile(
        profile_id="p1",
        name="P",
        capabilities=frozenset(),
    )
    issues = profile.validate()
    assert any(i.code == "empty_capabilities" and i.severity == "error" for i in issues)


def test_publisher_profile_forest_security_warning() -> None:
    profile = PublisherProfile(
        profile_id="p1",
        name="P",
        capabilities=frozenset({"read_gpo", "write_security_descriptor"}),
        max_blast_radius="forest",
    )
    issues = profile.validate()
    assert any(
        i.code == "forest_security_descriptor" and i.severity == "warning"
        for i in issues
    )


def test_publisher_profile_has_capability_scoped() -> None:
    profile = PublisherProfile(
        profile_id="p1",
        name="P",
        capabilities=frozenset({"write_gplink"}),
        scope_dns=("OU=Servers,DC=example,DC=test",),
    )
    assert profile.has_capability("write_gplink", "OU=Servers,DC=example,DC=test")
    # Nested DN is within scope.
    assert profile.has_capability(
        "write_gplink", "OU=Child,OU=Servers,DC=example,DC=test"
    )
    # Out-of-scope DN is denied.
    assert not profile.has_capability(
        "write_gplink", "OU=Workstations,DC=example,DC=test"
    )
    # Capability not held.
    assert not profile.has_capability("read_gpo")
    # No scope argument -> granted regardless of scope_dns.
    assert profile.has_capability("write_gplink")


# ---------------------------------------------------------------------------
# PublisherProfileSet
# ---------------------------------------------------------------------------


def test_profile_set_get_profile() -> None:
    p1 = PublisherProfile(
        profile_id="p1", name="A", capabilities=frozenset({"read_gpo"})
    )
    p2 = PublisherProfile(
        profile_id="p2", name="B", capabilities=frozenset({"read_gpo"})
    )
    ps = PublisherProfileSet(profiles=(p1, p2))
    assert ps.get_profile("p1") is p1
    assert ps.get_profile("p2") is p2
    assert ps.get_profile("p3") is None


def test_profile_set_effective_capabilities_union() -> None:
    pa = PublisherProfile(
        profile_id="p-authoring",
        name="A",
        capabilities=frozenset({"read_gpo", "write_registry_pol"}),
        principals=("alice",),
    )
    pb = PublisherProfile(
        profile_id="p-linking",
        name="B",
        capabilities=frozenset({"read_gpo", "write_gplink"}),
        principals=("alice",),
    )
    ps = PublisherProfileSet(profiles=(pa, pb))
    caps = ps.effective_capabilities("alice")
    assert "read_gpo" in caps
    assert "write_registry_pol" in caps
    assert "write_gplink" in caps


def test_profile_set_effective_capabilities_scope_filtered() -> None:
    pa = PublisherProfile(
        profile_id="p-server-admin",
        name="A",
        capabilities=frozenset({"write_gplink"}),
        principals=("alice",),
        scope_dns=("OU=Servers,DC=example,DC=test",),
    )
    ps = PublisherProfileSet(profiles=(pa,))
    in_scope = ps.effective_capabilities(
        "alice", "OU=Servers,DC=example,DC=test"
    )
    assert "write_gplink" in in_scope
    out_of_scope = ps.effective_capabilities(
        "alice", "OU=Other,DC=example,DC=test"
    )
    assert "write_gplink" not in out_of_scope


def test_profiles_for_actor_resolves_principals_not_profile_ids() -> None:
    """WI-052's defect as a regression: a profile id is not an actor.

    ``profiles_for_actor`` matched ``profile_id == actor``, so a capability
    check against the real principal ``alice`` returned nothing while the
    literal string ``"p1"`` — nobody — received the profile's capabilities
    wholesale. Matching is against ``principals`` now, and a profile granted
    to nobody grants nobody anything.
    """
    p1 = PublisherProfile(
        profile_id="p1",
        name="Publisher",
        capabilities=frozenset({"read_gpo", "write_registry_pol"}),
    )
    ps = PublisherProfileSet(profiles=(p1,))
    assert ps.profiles_for_actor("p1") == ()
    assert ps.effective_capabilities("p1") == frozenset()

    granted = PublisherProfile(
        profile_id="p1",
        name="Publisher",
        capabilities=frozenset({"read_gpo", "write_registry_pol"}),
        principals=("carol",),
    )
    ps2 = PublisherProfileSet(profiles=(granted,))
    assert ps2.profiles_for_actor("p1") == ()
    assert ps2.profiles_for_actor("carol") == (granted,)
    assert "write_registry_pol" in ps2.effective_capabilities("carol")


def test_profiles_for_actor_skips_inactive_profiles() -> None:
    active = PublisherProfile(
        profile_id="p1",
        name="Active",
        capabilities=frozenset({"read_gpo"}),
        principals=("carol",),
    )
    inactive = PublisherProfile(
        profile_id="p2",
        name="Inactive",
        capabilities=frozenset({"delete_gpo"}),
        principals=("carol",),
        is_active=False,
    )
    ps = PublisherProfileSet(profiles=(active, inactive))
    assert ps.profiles_for_actor("carol") == (active,)


def test_publisher_profile_no_principals_warns() -> None:
    profile = PublisherProfile(
        profile_id="p1",
        name="P",
        capabilities=frozenset({"read_gpo"}),
    )
    issues = profile.validate()
    assert any(
        i.code == "no_principals_bound" and i.severity == "warning"
        for i in issues
    )


def test_publisher_profile_empty_principal_error() -> None:
    profile = PublisherProfile(
        profile_id="p1",
        name="P",
        capabilities=frozenset({"read_gpo"}),
        principals=("carol", ""),
    )
    issues = profile.validate()
    assert any(i.code == "empty_principal" and i.severity == "error" for i in issues)


# ---------------------------------------------------------------------------
# ApprovalRequest
# ---------------------------------------------------------------------------


def test_approval_request_valid() -> None:
    req = ApprovalRequest(
        request_id="r1",
        plan_id="plan-1",
        gpo_guid=_GUID,
        gpo_name="G",
        requested_by="alice",
        requested_at="2026-01-01T00:00:00+00:00",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    issues = req.validate()
    assert not any(i.severity == "error" for i in issues)


def test_approval_request_empty_request_id_error() -> None:
    req = ApprovalRequest(
        request_id="",
        plan_id="plan-1",
        gpo_guid=_GUID,
        gpo_name="G",
        requested_by="alice",
        requested_at="2026-01-01T00:00:00+00:00",
    )
    issues = req.validate()
    assert any(i.code == "empty_request_id" and i.severity == "error" for i in issues)


def test_approval_request_empty_plan_id_error() -> None:
    req = ApprovalRequest(
        request_id="r1",
        plan_id="",
        gpo_guid=_GUID,
        gpo_name="G",
        requested_by="alice",
        requested_at="2026-01-01T00:00:00+00:00",
    )
    issues = req.validate()
    assert any(i.code == "empty_plan_id" and i.severity == "error" for i in issues)


def test_approval_request_expired_warning() -> None:
    req = ApprovalRequest(
        request_id="r1",
        plan_id="plan-1",
        gpo_guid=_GUID,
        gpo_name="G",
        requested_by="alice",
        requested_at="2020-01-01T00:00:00+00:00",
        expires_at="2020-01-02T00:00:00+00:00",
    )
    issues = req.validate()
    assert any(
        i.code == "expired_approval" and i.severity == "warning" for i in issues
    )


def test_approval_request_approved_without_approver_error() -> None:
    req = ApprovalRequest(
        request_id="r1",
        plan_id="plan-1",
        gpo_guid=_GUID,
        gpo_name="G",
        requested_by="alice",
        requested_at="2026-01-01T00:00:00+00:00",
        state="approved",
        approved_by="",
    )
    issues = req.validate()
    assert any(
        i.code == "approved_without_approver" and i.severity == "error" for i in issues
    )


def test_approval_request_rejected_without_reason_error() -> None:
    req = ApprovalRequest(
        request_id="r1",
        plan_id="plan-1",
        gpo_guid=_GUID,
        gpo_name="G",
        requested_by="alice",
        requested_at="2026-01-01T00:00:00+00:00",
        state="rejected",
        rejection_reason="",
    )
    issues = req.validate()
    assert any(
        i.code == "rejected_without_reason" and i.severity == "error" for i in issues
    )


def test_approval_request_invalid_required_approvers_error() -> None:
    req = ApprovalRequest(
        request_id="r1",
        plan_id="plan-1",
        gpo_guid=_GUID,
        gpo_name="G",
        requested_by="alice",
        requested_at="2026-01-01T00:00:00+00:00",
        required_approvers=0,
    )
    issues = req.validate()
    assert any(
        i.code == "invalid_required_approvers" and i.severity == "error" for i in issues
    )


def test_approval_request_is_sufficiently_approved() -> None:
    approved = ApprovalRequest(
        request_id="r1",
        plan_id="plan-1",
        gpo_guid=_GUID,
        gpo_name="G",
        requested_by="alice",
        requested_at="2026-01-01T00:00:00+00:00",
        state="approved",
        approved_by="bob",
        required_approvers=2,
        current_approvals=2,
    )
    assert approved.is_sufficiently_approved()

    pending = ApprovalRequest(
        request_id="r2",
        plan_id="plan-1",
        gpo_guid=_GUID,
        gpo_name="G",
        requested_by="alice",
        requested_at="2026-01-01T00:00:00+00:00",
        state="pending",
        required_approvers=2,
        current_approvals=1,
    )
    assert not pending.is_sufficiently_approved()


# ---------------------------------------------------------------------------
# approve_request / reject_request
# ---------------------------------------------------------------------------


def test_approve_request_valid() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    req = create_approval_request(plan, requested_by="alice", required_approvers=1)
    assert req.state == "pending"
    assert req.current_approvals == 0

    approved = approve_request(req, "bob")
    assert approved.state == "approved"
    assert approved.current_approvals == 1
    assert approved.approved_by == "bob"
    assert approved.approved_at != ""
    assert approved.is_sufficiently_approved()


def test_approve_request_multi_approval() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    req = create_approval_request(plan, requested_by="alice", required_approvers=2)
    first = approve_request(req, "bob")
    assert first.state == "pending"
    assert first.current_approvals == 1
    assert not first.is_sufficiently_approved()

    second = approve_request(first, "carol")
    assert second.state == "approved"
    assert second.current_approvals == 2
    assert second.is_sufficiently_approved()


def test_approve_request_self_approval_raises() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    req = create_approval_request(plan, requested_by="alice")
    with pytest.raises(ValidationError):
        approve_request(req, "alice")


def test_approve_request_already_approved_raises() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    req = create_approval_request(plan, requested_by="alice")
    approved = approve_request(req, "bob")
    with pytest.raises(ValidationError):
        approve_request(approved, "carol")


def test_approve_request_expired_raises() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    req = create_approval_request(plan, requested_by="alice", expiry_hours=0)
    # expiry is now-ish; force a past expiry to guarantee expiration.
    import dataclasses

    req = dataclasses.replace(req, expires_at="2020-01-01T00:00:00+00:00")
    with pytest.raises(ValidationError):
        approve_request(req, "bob")


def test_reject_request_valid() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    req = create_approval_request(plan, requested_by="alice")
    rejected = reject_request(req, "bob", "Too risky")
    assert rejected.state == "rejected"
    assert rejected.rejection_reason == "Too risky"
    assert rejected.approved_by == "bob"  # holds the rejector


def test_reject_request_not_pending_raises() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    req = create_approval_request(plan, requested_by="alice")
    approved = approve_request(req, "bob")
    with pytest.raises(ValidationError) as exc_info:
        reject_request(approved, "carol", "Changed mind")
    assert any(i.code == "invalid_rejection_state" for i in exc_info.value.issues)


def test_approve_request_duplicate_approver_raises() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    req = create_approval_request(plan, requested_by="alice", required_approvers=2)
    first = approve_request(req, "bob")
    assert first.approvers == ("bob",)
    with pytest.raises(ValidationError) as exc_info:
        approve_request(first, "bob")
    assert any(i.code == "duplicate_approver" for i in exc_info.value.issues)


def test_approve_request_tracks_approvers() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    req = create_approval_request(plan, requested_by="alice", required_approvers=2)
    first = approve_request(req, "bob")
    assert first.approvers == ("bob",)
    second = approve_request(first, "carol")
    assert second.approvers == ("bob", "carol")
    assert second.state == "approved"


# ---------------------------------------------------------------------------
# run_publisher_gates
# ---------------------------------------------------------------------------


def test_run_publisher_gates_all_pass() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    profile = _full_profile()
    gates = run_publisher_gates(plan, profile, actor="alice")
    assert len(gates) == 8
    failed = [g for g in gates if not g.passed]
    assert failed == [], [g.detail for g in failed]


def test_run_publisher_gates_capability_fails() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    profile = _full_profile(
        capabilities=frozenset({"read_gpo", "write_gpt_ini"}),
    )
    gates = run_publisher_gates(plan, profile, actor="alice")
    cap_gate = next(g for g in gates if g.gate_id == "capability_gate")
    assert not cap_gate.passed
    assert "write_registry_pol" in cap_gate.detail


def test_run_publisher_gates_approval_fails() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    profile = _full_profile(requires_approval=True)
    gates = run_publisher_gates(plan, profile, approval=None, actor="alice")
    approval_gate = next(g for g in gates if g.gate_id == "approval_gate")
    assert not approval_gate.passed


def test_run_publisher_gates_approval_passes_with_sufficient_approval() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    profile = _full_profile(requires_approval=True)
    req = create_approval_request(plan, requested_by="alice", required_approvers=1)
    approved = approve_request(req, "bob")
    gates = run_publisher_gates(plan, profile, approval=approved, actor="alice")
    approval_gate = next(g for g in gates if g.gate_id == "approval_gate")
    assert approval_gate.passed


def test_run_publisher_gates_scope_fails() -> None:
    gpo = GPO(
        guid=_GUID,
        name="Linked Policy",
        links=(
            GPOLink(id="l1", target="OU=Workstations,DC=example,DC=test"),
        ),
    )
    plan = generate_publication_plan(gpo)
    profile = _full_profile(
        scope_dns=("OU=Servers,DC=example,DC=test",),
    )
    gates = run_publisher_gates(plan, profile, actor="alice")
    scope_gate = next(g for g in gates if g.gate_id == "scope_gate")
    assert not scope_gate.passed


def test_run_publisher_gates_scope_passes_when_in_scope() -> None:
    gpo = GPO(
        guid=_GUID,
        name="Linked Policy",
        links=(
            GPOLink(id="l1", target="OU=Servers,DC=example,DC=test"),
        ),
    )
    plan = generate_publication_plan(gpo)
    profile = _full_profile(
        scope_dns=("OU=Servers,DC=example,DC=test",),
    )
    gates = run_publisher_gates(plan, profile, actor="alice")
    scope_gate = next(g for g in gates if g.gate_id == "scope_gate")
    assert scope_gate.passed


def test_run_publisher_gates_blast_radius_fails() -> None:
    gpo = GPO(
        guid=_GUID,
        name="DC Policy",
        links=(
            GPOLink(id="l1", target="OU=Domain Controllers,DC=example,DC=test"),
        ),
    )
    plan = generate_publication_plan(gpo)
    assert plan.risk_level == "high"
    profile = _full_profile(max_blast_radius="single_gpo")
    gates = run_publisher_gates(plan, profile, actor="alice")
    radius_gate = next(g for g in gates if g.gate_id == "blast_radius_gate")
    assert not radius_gate.passed


# ---------------------------------------------------------------------------
# separation_of_duties_gate and the actor threading (WI-051)
# ---------------------------------------------------------------------------


def _approved_request(plan: object) -> object:
    """An approval produced by the legitimate path: alice asks, bob grants."""
    req = create_approval_request(plan, requested_by="alice", required_approvers=1)
    return approve_request(req, "bob")


def test_separation_of_duties_refuses_the_actor_who_approved() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    profile = _full_profile(requires_approval=True)
    approved = _approved_request(plan)
    gates = run_publisher_gates(plan, profile, approval=approved, actor="bob")
    sod_gate = next(g for g in gates if g.gate_id == "separation_of_duties_gate")
    assert not sod_gate.passed
    assert "bob" in sod_gate.detail


def test_separation_of_duties_refuses_a_self_approved_request() -> None:
    """WI-051's reproduction as a regression.

    ``approve_request`` refuses self-approval, but nothing stopped a
    directly-constructed request — the shape any persistence layer produces
    when it rehydrates stored state — from carrying one. The request below is
    structurally well-formed and binds the plan's content, so the approval
    gate passes it; only the separation-of-duties gate sees what it is.
    """
    plan = generate_publication_plan(_gpo_with_registry())
    profile = _full_profile(requires_approval=True)
    self_approved = ApprovalRequest(
        request_id="apr-selfapproved",
        plan_id=plan.plan_id,
        gpo_guid=plan.gpo_guid,
        gpo_name=plan.gpo_name,
        requested_by="alice",
        requested_at="2026-09-06T00:00:00+00:00",
        state="approved",
        approved_by="alice",
        approved_at="2026-09-06T00:00:00+00:00",
        current_approvals=1,
        plan_payload_digest=plan.payload_digest,
    )
    issues = self_approved.validate()
    assert any(
        i.code == "self_approved_request" and i.severity == "error"
        for i in issues
    ), [f"{i.severity}:{i.code}" for i in issues]
    gates = run_publisher_gates(
        plan, profile, approval=self_approved, actor="carol"
    )
    approval_gate = next(g for g in gates if g.gate_id == "approval_gate")
    assert approval_gate.passed, approval_gate.detail
    sod_gate = next(g for g in gates if g.gate_id == "separation_of_duties_gate")
    assert not sod_gate.passed
    assert "self-approval" in sod_gate.detail
    decision = evaluate_publication(
        plan, profile, approval=self_approved, actor="carol"
    )
    assert decision.approved is False
    assert any(
        g.gate_id == "separation_of_duties_gate" for g in decision.blocking_gates
    )


def test_separation_of_duties_requires_a_principal() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    profile = _full_profile(requires_approval=True)
    approved = _approved_request(plan)
    gates = run_publisher_gates(plan, profile, approval=approved, actor="  ")
    sod_gate = next(g for g in gates if g.gate_id == "separation_of_duties_gate")
    assert not sod_gate.passed
    assert "No principal supplied" in sod_gate.detail


def test_separation_of_duties_allows_the_normal_flow() -> None:
    """Alice requests, bob approves, alice publishes — the shape that must pass."""
    plan = generate_publication_plan(_gpo_with_registry())
    profile = _full_profile(requires_approval=True)
    approved = _approved_request(plan)
    gates = run_publisher_gates(plan, profile, approval=approved, actor="alice")
    sod_gate = next(g for g in gates if g.gate_id == "separation_of_duties_gate")
    assert sod_gate.passed, sod_gate.detail


def test_separation_of_duties_passes_without_required_approval() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    profile = _full_profile(requires_approval=False)
    gates = run_publisher_gates(plan, profile, approval=None, actor="alice")
    sod_gate = next(g for g in gates if g.gate_id == "separation_of_duties_gate")
    assert sod_gate.passed


# ---------------------------------------------------------------------------
# _approval_gate's refusal branches (WI-051's coverage debt)
# ---------------------------------------------------------------------------


def test_approval_gate_refuses_a_request_for_a_different_plan() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    other = generate_publication_plan(_gpo_with_registry())
    assert other.plan_id != plan.plan_id
    profile = _full_profile(requires_approval=True)
    approved = _approved_request(other)
    gates = run_publisher_gates(plan, profile, approval=approved, actor="alice")
    approval_gate = next(g for g in gates if g.gate_id == "approval_gate")
    assert not approval_gate.passed
    assert "does not match" in approval_gate.detail


def test_approval_gate_refuses_a_rejected_request() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    profile = _full_profile(requires_approval=True)
    req = create_approval_request(plan, requested_by="alice")
    rejected = reject_request(req, "bob", "Too risky")
    gates = run_publisher_gates(plan, profile, approval=rejected, actor="alice")
    approval_gate = next(g for g in gates if g.gate_id == "approval_gate")
    assert not approval_gate.passed
    assert "rejected" in approval_gate.detail


def test_approval_gate_refuses_an_expired_request() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    profile = _full_profile(requires_approval=True)
    req = create_approval_request(plan, requested_by="alice")
    expired = replace(req, state="expired")
    gates = run_publisher_gates(plan, profile, approval=expired, actor="alice")
    approval_gate = next(g for g in gates if g.gate_id == "approval_gate")
    assert not approval_gate.passed
    assert "expired" in approval_gate.detail


def test_approval_gate_refuses_insufficient_approvals() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    profile = _full_profile(requires_approval=True)
    req = create_approval_request(plan, requested_by="alice", required_approvers=2)
    once = approve_request(req, "bob")
    assert once.state == "pending"
    gates = run_publisher_gates(plan, profile, approval=once, actor="alice")
    approval_gate = next(g for g in gates if g.gate_id == "approval_gate")
    assert not approval_gate.passed
    assert "1/2" in approval_gate.detail


# ---------------------------------------------------------------------------
# evaluate_publication
# ---------------------------------------------------------------------------


def test_evaluate_publication_approved() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    profile = _full_profile()
    decision = evaluate_publication(plan, profile, actor="alice")
    assert isinstance(decision, PublisherDecision)
    assert decision.approved is True
    assert decision.blocking_gates == ()
    assert decision.plan_id == plan.plan_id
    assert decision.decision_at != ""
    assert decision.decided_by == "alice"
    assert len(decision.gates) == 8


def test_evaluate_publication_denied() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    profile = _full_profile(
        capabilities=frozenset({"read_gpo", "write_gpt_ini"}),
    )
    decision = evaluate_publication(plan, profile, actor="alice")
    assert decision.approved is False
    assert len(decision.blocking_gates) >= 1
    assert any(g.gate_id == "capability_gate" for g in decision.blocking_gates)


# ---------------------------------------------------------------------------
# PublicationAuditTrail
# ---------------------------------------------------------------------------


def test_audit_trail_append_immutability() -> None:
    trail = PublicationAuditEntry(
        entry_id="e1",
        plan_id="plan-1",
        action="plan_created",
        actor="alice",
        timestamp="2026-01-01T00:00:00+00:00",
    )
    empty = PublicationAuditTrail()
    one = empty.append(trail)
    assert len(empty.entries) == 0
    assert len(one.entries) == 1
    assert one.entries[0] is trail


def test_audit_trail_entries_for_plan() -> None:
    e1 = PublicationAuditEntry(
        entry_id="e1",
        plan_id="plan-1",
        action="plan_created",
        actor="alice",
        timestamp="2026-01-01T00:00:00+00:00",
    )
    e2 = PublicationAuditEntry(
        entry_id="e2",
        plan_id="plan-2",
        action="plan_submitted",
        actor="bob",
        timestamp="2026-01-02T00:00:00+00:00",
    )
    e3 = PublicationAuditEntry(
        entry_id="e3",
        plan_id="plan-1",
        action="approved",
        actor="carol",
        timestamp="2026-01-03T00:00:00+00:00",
    )
    trail = PublicationAuditTrail().append(e1).append(e2).append(e3)
    plan1 = trail.entries_for_plan("plan-1")
    assert plan1 == (e1, e3)
    plan2 = trail.entries_for_plan("plan-2")
    assert plan2 == (e2,)
    assert trail.entries_for_plan("plan-3") == ()


# ---------------------------------------------------------------------------
# WI-050: an approval binds the plan's content, not just its identifier
# ---------------------------------------------------------------------------


def _approval_gate_verdict(plan, profile, approval):  # type: ignore[no-untyped-def]
    gates = run_publisher_gates(plan, profile, approval=approval, actor="carol")
    return next(g for g in gates if g.gate_id == "approval_gate")


def test_payload_digest_ignores_plan_id() -> None:
    """`plan_id` is a random uuid4 prefix; binding to it is what WI-050 is about."""
    plan = generate_publication_plan(_gpo_with_registry())
    assert plan.payload_digest == replace(plan, plan_id="plan-somethingelse").payload_digest


def test_payload_digest_ignores_step_status() -> None:
    """A digest that moved as steps ran could not bind an approval taken before."""
    plan = generate_publication_plan(_gpo_with_registry())
    ran = replace(plan, steps=tuple(replace(s, status="completed") for s in plan.steps))
    assert plan.payload_digest == ran.payload_digest


def test_payload_digest_changes_when_an_operation_changes() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    swapped = replace(
        plan, steps=(replace(plan.steps[0], operation="update_gplink"),) + plan.steps[1:]
    )
    assert plan.payload_digest != swapped.payload_digest


def test_payload_digest_changes_when_risk_level_is_escalated() -> None:
    """Risk decides how much scrutiny a plan gets, so escalating it is a content change."""
    plan = generate_publication_plan(_gpo_with_registry())
    assert plan.payload_digest != replace(plan, risk_level="critical").payload_digest


def test_approval_does_not_carry_to_a_swapped_payload() -> None:
    """The WI-050 reproduction, as a regression test.

    Approve a plan whose one step writes a `registry.pol`, then swap the steps
    for an `update_gplink` retargeting the Domain Controllers OU, leaving
    `plan_id` untouched. Before the digest binding this returned passed=True.
    """
    plan = generate_publication_plan(_gpo_with_registry())
    profile = _full_profile(requires_approval=True)
    approved = approve_request(create_approval_request(plan, requested_by="alice"), "bob")
    assert _approval_gate_verdict(plan, profile, approved).passed

    swapped = replace(
        plan,
        steps=(
            PublicationStep(
                step_id="s1",
                operation="update_gplink",
                target="both",
                status="pending",
                detail="OU=Domain Controllers,DC=ad,DC=example,DC=test",
            ),
        ),
        risk_level="critical",
    )
    verdict = _approval_gate_verdict(swapped, profile, approved)
    assert not verdict.passed
    assert "content changed since approval" in verdict.detail


def test_approval_without_a_content_binding_is_refused() -> None:
    """Fail closed: an approval that binds nothing cannot attest to anything.

    This is the shape a persistence layer produces when it rehydrates a request
    stored before the digest existed.
    """
    plan = generate_publication_plan(_gpo_with_registry())
    profile = _full_profile(requires_approval=True)
    approved = approve_request(create_approval_request(plan, requested_by="alice"), "bob")
    unbound = replace(approved, plan_payload_digest="")
    verdict = _approval_gate_verdict(plan, profile, unbound)
    assert not verdict.passed
    assert "does not bind the plan's content" in verdict.detail


def test_create_approval_request_binds_the_plan_it_was_given() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    assert create_approval_request(plan, requested_by="alice").plan_payload_digest == (
        plan.payload_digest
    )
