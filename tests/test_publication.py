from __future__ import annotations

import os
import tempfile

from gpo_studio.artifact_store import ArtifactStore
from gpo_studio.model import GPO, GPOLink, RegistrySetting, SecurityFilter
from gpo_studio.publication import (
    PublicationPlan,
    PublicationStep,
    generate_publication_plan,
    generate_publication_script,
    validate_publication_plan,
)


def _gpo_with_registry() -> GPO:
    return GPO(
        guid="11111111-2222-3333-4444-555555555555",
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


def test_generate_publication_plan_includes_registry_pol_step() -> None:
    gpo = _gpo_with_registry()
    plan = generate_publication_plan(gpo)
    assert any(s.operation == "write_registry_pol" for s in plan.steps)
    assert plan.risk_level == "low"


def test_generate_publication_plan_includes_gpp_copy_step() -> None:
    from gpo_studio.gpp import GppCollection, GppGroup

    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="GPP Policy",
        gpp_collections=(
            GppCollection(scope="computer", groups=(GppGroup(name="G1"),)),
        ),
    )
    plan = generate_publication_plan(gpo)
    assert any(s.operation == "copy_gpp_xml" for s in plan.steps)


def test_generate_publication_script_copy_gpp_xml_fails_closed() -> None:
    from gpo_studio.gpp import GppCollection, GppGroup

    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="GPP Policy",
        gpp_collections=(
            GppCollection(scope="computer", groups=(GppGroup(name="G1"),)),
        ),
    )
    plan = generate_publication_plan(gpo)
    script = generate_publication_script(plan)
    assert "NOT WINDOWS-VERIFIED: copy_gpp_xml" in script.script_text
    assert "Copy-Item" not in script.script_text
    assert "SYSVOL" in script.script_text  # explanatory comment only
    assert "exit 1" in script.script_text


def test_generate_publication_script_update_gplink_fails_closed() -> None:
    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Linked Policy",
        links=(
            GPOLink(id="l1", target="OU=Servers,DC=example,DC=test"),
        ),
    )
    plan = generate_publication_plan(gpo)
    script = generate_publication_script(plan)
    assert "NOT WINDOWS-VERIFIED: update_gplink" in script.script_text
    assert "Set-ADObject" not in script.script_text
    assert "New-GPLink" not in script.script_text


def test_generate_publication_script_contains_expected_commands() -> None:
    gpo = _gpo_with_registry()
    plan = generate_publication_plan(gpo)
    script = generate_publication_script(plan)
    assert script.is_idempotent is False
    assert script.plan_id == plan.plan_id
    assert script.gpo_guid == plan.gpo_guid
    assert "fails closed before contacting AD or SYSVOL" in script.script_text
    assert "$ErrorActionPreference = 'Stop'" in script.script_text
    assert "Get-GPO" not in script.script_text


def test_publication_plan_validate_valid() -> None:
    gpo = _gpo_with_registry()
    plan = generate_publication_plan(gpo)
    issues = plan.validate()
    assert not any(i.severity == "error" for i in issues)


def test_publication_plan_validate_empty_steps_error() -> None:
    plan = PublicationPlan(
        plan_id="p1",
        gpo_guid="11111111-2222-3333-4444-555555555555",
        gpo_name="X",
    )
    issues = plan.validate()
    assert any(i.code == "empty_steps" and i.severity == "error" for i in issues)


def test_publication_plan_validate_approved_without_approver_error() -> None:
    gpo = _gpo_with_registry()
    plan = generate_publication_plan(gpo)
    plan = plan.__class__(
        **{
            k: getattr(plan, k)
            for k in plan.__dataclass_fields__
            if k != "state"
        },
        state="approved",
    )
    issues = plan.validate()
    assert any(i.code == "approved_without_approver" for i in issues)


def test_validate_publication_plan_valid_with_store() -> None:
    gpo = _gpo_with_registry()
    plan = generate_publication_plan(gpo)

    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(os.path.join(tmp, "artifacts.db"))
        # Find the content matching the artifact hash and store it.
        from gpo_studio.registry_pol import PolRecord, serialize
        content = serialize(
            [
                PolRecord(
                    key=s.key,
                    value_name=s.value_name,
                    registry_type=s.registry_type,
                    value=s.value,
                    action=s.action,
                )
                for s in gpo.settings
                if s.side == "computer"
            ]
        )
        store.store_artifact(content, "Machine-Registry.pol.txt", artifact_type="companion")
        issues = validate_publication_plan(plan, store=store)
        assert not any(i.level == "error" for i in issues)


def test_validate_publication_plan_missing_artifact_error() -> None:
    gpo = _gpo_with_registry()
    plan = generate_publication_plan(gpo)
    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(os.path.join(tmp, "artifacts.db"))
        issues = validate_publication_plan(plan, store=store)
        assert any(i.check == "artifact_exists" and i.level == "error" for i in issues)


def test_risk_assessment_domain_controllers_is_high() -> None:
    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="DC Policy",
        links=(GPOLink(id="l1", target="OU=Domain Controllers,DC=example,DC=test"),),
    )
    plan = generate_publication_plan(gpo)
    assert plan.risk_level == "high"


def test_risk_assessment_security_filter_is_medium() -> None:
    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Filtered Policy",
        security_filters=(
            SecurityFilter(id="sf1", principal="DOMAIN\\Users", permission="apply"),
        ),
    )
    plan = generate_publication_plan(gpo)
    assert plan.risk_level == "medium"


def test_risk_assessment_simple_is_low() -> None:
    gpo = _gpo_with_registry()
    plan = generate_publication_plan(gpo)
    assert plan.risk_level == "low"


def test_risk_assessment_many_registry_settings_is_medium() -> None:
    settings = tuple(
        RegistrySetting(
            id=f"s{i}",
            side="computer",
            hive="HKLM",
            key=r"Software\Policies\Synthetic",
            value_name=f"Value{i}",
            registry_type="REG_DWORD",
            value=1,
        )
        for i in range(101)
    )
    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Big Policy",
        settings=settings,
    )
    plan = generate_publication_plan(gpo)
    assert plan.risk_level == "medium"


def test_publication_state_machine_transitions() -> None:
    gpo = _gpo_with_registry()
    plan = generate_publication_plan(gpo)

    def transition(p: PublicationPlan, state: str) -> PublicationPlan:
        return p.__class__(
            **{k: getattr(p, k) for k in p.__dataclass_fields__ if k != "state"},
            state=state,  # type: ignore[arg-type]
        )

    plan = transition(plan, "staged")
    assert plan.state == "staged"
    plan = transition(plan, "approved")
    assert plan.state == "approved"
    plan = transition(plan, "publishing")
    assert plan.state == "publishing"
    plan = transition(plan, "published")
    assert plan.state == "published"


def test_publication_step_defaults() -> None:
    step = PublicationStep(
        step_id="s1", operation="write_registry_pol", target="sysvol", status="pending"
    )
    assert step.detail == ""
    assert step.artifact_ids == ()


# ---------------------------------------------------------------------------
# Issue A: steps filtered by target parameter
# ---------------------------------------------------------------------------


def _gpo_with_registry_and_link() -> GPO:
    return GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Mixed Policy",
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
        links=(
            GPOLink(id="l1", target="OU=Servers,DC=example,DC=test"),
        ),
    )


def _operations(plan: PublicationPlan) -> set[str]:
    return {s.operation for s in plan.steps}


def test_target_ad_excludes_sysvol_steps() -> None:
    plan = generate_publication_plan(_gpo_with_registry_and_link(), target="ad")
    ops = _operations(plan)
    assert "write_registry_pol" not in ops
    assert "update_gpt_ini" not in ops
    assert "update_gplink" in ops
    assert all(s.target == "ad" for s in plan.steps)


def test_target_sysvol_excludes_ad_steps() -> None:
    plan = generate_publication_plan(_gpo_with_registry_and_link(), target="sysvol")
    ops = _operations(plan)
    assert "update_gplink" not in ops
    assert "write_registry_pol" in ops
    assert "update_gpt_ini" in ops
    assert all(s.target == "sysvol" for s in plan.steps)


def test_target_both_includes_ad_and_sysvol_steps() -> None:
    plan = generate_publication_plan(_gpo_with_registry_and_link(), target="both")
    ops = _operations(plan)
    assert "write_registry_pol" in ops
    assert "update_gpt_ini" in ops
    assert "update_gplink" in ops


# ---------------------------------------------------------------------------
# Issue B: idempotency is withdrawn until Windows evidence exists
# ---------------------------------------------------------------------------


def test_gpt_ini_step_is_not_executable_before_windows_verification() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    script = generate_publication_script(plan)
    assert "NOT WINDOWS-VERIFIED: update_gpt_ini" in script.script_text
    assert "Set-Content" not in script.script_text


def test_is_idempotent_false_for_unverified_sysvol_plan() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    script = generate_publication_script(plan)
    assert script.is_idempotent is False


def test_is_idempotent_false_when_plan_has_unimplemented_ops() -> None:
    plan = generate_publication_plan(_gpo_with_registry_and_link())
    script = generate_publication_script(plan)
    assert script.is_idempotent is False


# ---------------------------------------------------------------------------
# Issue C: unverified operations warn and exit non-zero before mutation
# ---------------------------------------------------------------------------


def test_unverified_operations_emit_warnings() -> None:
    plan = generate_publication_plan(_gpo_with_registry_and_link())
    script = generate_publication_script(plan)
    assert "NOT WINDOWS-VERIFIED: update_gplink" in script.script_text
    assert "NOT WINDOWS-VERIFIED: write_registry_pol" in script.script_text


def test_script_exits_nonzero_when_incomplete() -> None:
    plan = generate_publication_plan(_gpo_with_registry_and_link())
    script = generate_publication_script(plan)
    assert "Publication refused" in script.script_text
    assert "exit 1" in script.script_text


def test_final_message_documents_refusal() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    script = generate_publication_script(plan)
    assert "Publication refused" in script.script_text
    assert "Publication plan executed." not in script.script_text


def test_sysvol_only_script_has_unverified_warnings() -> None:
    plan = generate_publication_plan(_gpo_with_registry())
    script = generate_publication_script(plan)
    assert "NOT WINDOWS-VERIFIED: update_gpt_ini" in script.script_text
    assert "NOT WINDOWS-VERIFIED: write_registry_pol" in script.script_text
