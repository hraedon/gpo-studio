from __future__ import annotations

import pytest

from gpo_studio.model import GPO, GPOLink, RegistrySetting, ValidationError
from gpo_studio.publication import generate_publication_plan
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
        profile_id="alice",
        name="A",
        capabilities=frozenset({"read_gpo", "write_registry_pol"}),
    )
    pb = PublisherProfile(
        profile_id="alice",
        name="B",
        capabilities=frozenset({"read_gpo", "write_gplink"}),
    )
    ps = PublisherProfileSet(profiles=(pa, pb))
    caps = ps.effective_capabilities("alice")
    assert "read_gpo" in caps
    assert "write_registry_pol" in caps
    assert "write_gplink" in caps


def test_profile_set_effective_capabilities_scope_filtered() -> None:
    pa = PublisherProfile(
        profile_id="alice",
        name="A",
        capabilities=frozenset({"write_gplink"}),
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
    gates = run_publisher_gates(plan, profile)
    assert len(gates) == 7
    failed = [g for g in gates if not g.passed]
    assert failed == [], [g.detail for g in failed]


def test_run_publisher_gates_capability_fails() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    profile = _full_profile(
        capabilities=frozenset({"read_gpo", "write_gpt_ini"}),
    )
    gates = run_publisher_gates(plan, profile)
    cap_gate = next(g for g in gates if g.gate_id == "capability_gate")
    assert not cap_gate.passed
    assert "write_registry_pol" in cap_gate.detail


def test_run_publisher_gates_approval_fails() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    profile = _full_profile(requires_approval=True)
    gates = run_publisher_gates(plan, profile, approval=None)
    approval_gate = next(g for g in gates if g.gate_id == "approval_gate")
    assert not approval_gate.passed


def test_run_publisher_gates_approval_passes_with_sufficient_approval() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    profile = _full_profile(requires_approval=True)
    req = create_approval_request(plan, requested_by="alice", required_approvers=1)
    approved = approve_request(req, "bob")
    gates = run_publisher_gates(plan, profile, approval=approved)
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
    gates = run_publisher_gates(plan, profile)
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
    gates = run_publisher_gates(plan, profile)
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
    gates = run_publisher_gates(plan, profile)
    radius_gate = next(g for g in gates if g.gate_id == "blast_radius_gate")
    assert not radius_gate.passed


# ---------------------------------------------------------------------------
# evaluate_publication
# ---------------------------------------------------------------------------


def test_evaluate_publication_approved() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    profile = _full_profile()
    decision = evaluate_publication(plan, profile)
    assert isinstance(decision, PublisherDecision)
    assert decision.approved is True
    assert decision.blocking_gates == ()
    assert decision.plan_id == plan.plan_id
    assert decision.decision_at != ""
    assert len(decision.gates) == 7


def test_evaluate_publication_denied() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    profile = _full_profile(
        capabilities=frozenset({"read_gpo", "write_gpt_ini"}),
    )
    decision = evaluate_publication(plan, profile)
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
