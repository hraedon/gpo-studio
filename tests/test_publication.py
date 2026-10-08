from __future__ import annotations

import io
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from gpo_studio.export import extension_registration, gpmc_backup_bundle
from gpo_studio.gpp import GppCollection, GppDrive, GppEnvironment, GppService
from gpo_studio.model import GPO, GPOLink, RegistrySetting, SecurityFilter
from gpo_studio.publication import (
    PublicationPlan,
    PublicationStep,
    PublicationTarget,
    _gpt_version_half,
    generate_publication_plan,
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


@pytest.mark.parametrize("target", ["sysvol", "both"])
def test_scripts_backup_metadata_makes_sysvol_publication_plan_incomplete(
    target: PublicationTarget,
) -> None:
    from gpo_studio.backup import read_backup
    from gpo_studio.import_export import collect_cse_metadata

    fixture = (Path(__file__).parents[1] / "docs" / "plan-033" / "wp1b-evidence" /
               "scripts-metadata" / "rebackup")
    backup_gpo = read_backup(fixture).gpos[0]
    metadata = collect_cse_metadata(backup_gpo)
    assert metadata and sum(len(entry.files) for entry in metadata) == 2
    gpo = GPO(
        guid=backup_gpo.guid,
        name=backup_gpo.display_name,
        domain=backup_gpo.domain,
        cse_metadata=metadata,
    )
    plan = generate_publication_plan(gpo, target=target)
    refusal = [step for step in plan.steps if step.operation == "unsupported_cse_content"]
    assert len(refusal) == 1
    assert "2 preserved file(s)" in refusal[0].detail
    assert any(
        issue.level == "error" and issue.check == "unsupported_cse_content"
        for issue in validate_publication_plan(plan)
    )


def test_ad_only_plan_does_not_claim_to_publish_sysvol_cse_content() -> None:
    from gpo_studio.model import CseMetadataEntry

    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Opaque metadata",
        cse_metadata=(CseMetadataEntry(guid="{opaque}", side="machine"),),
    )
    plan = generate_publication_plan(gpo, target="ad")
    assert not any(step.operation == "unsupported_cse_content" for step in plan.steps)


def test_metadata_without_preserved_files_still_refuses_sysvol_plan() -> None:
    from gpo_studio.model import CseMetadataEntry

    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Opaque metadata",
        cse_metadata=(CseMetadataEntry(guid="{opaque}", side="machine"),),
    )
    plan = generate_publication_plan(gpo, target="sysvol")
    refusal = [step for step in plan.steps if step.operation == "unsupported_cse_content"]
    assert len(refusal) == 1
    assert "1 preserved CSE metadata entry/entries" in refusal[0].detail
    assert "0 preserved file(s)" in refusal[0].detail
    assert any(
        issue.check == "unsupported_cse_content"
        for issue in validate_publication_plan(plan)
    )


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
# GPT.INI version half. Evidence (R5, measured live on Windows Server 2025,
# 2026-09-03): the packed value is user * 65536 + machine and the halves move
# independently, so a plan must say which half it moves. The completeness
# lane's finalizer checks the observed value against that declaration.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("has_machine", "has_user", "expected"),
    [
        (True, True, "both"),
        (True, False, "machine"),
        (False, True, "user"),
        (False, False, None),
    ],
)
def test_gpt_version_half_selection(
    has_machine: bool, has_user: bool, expected: str | None
) -> None:
    assert _gpt_version_half(has_machine, has_user) == expected


# ---------------------------------------------------------------------------
# Plan modeling: the update_gpt_ini step records which half it publishes.
# ---------------------------------------------------------------------------


def _gpo_with_user_registry() -> GPO:
    return GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="User Registry Policy",
        settings=(
            RegistrySetting(
                id="u1",
                side="user",
                hive="HKCU",
                key=r"Software\Policies\Synthetic",
                value_name="Enabled",
                registry_type="REG_DWORD",
                value=1,
            ),
        ),
    )


def _gpt_ini_step(plan: PublicationPlan) -> PublicationStep:
    return next(s for s in plan.steps if s.operation == "update_gpt_ini")


def test_gpt_ini_step_records_machine_half_for_computer_only_content() -> None:
    step = _gpt_ini_step(generate_publication_plan(_gpo_with_registry()))
    assert step.version_half == "machine"
    assert step.detail.endswith("(machine half)")


def test_gpt_ini_step_records_user_half_for_user_only_content() -> None:
    step = _gpt_ini_step(generate_publication_plan(_gpo_with_user_registry()))
    assert step.version_half == "user"
    assert step.detail.endswith("(user half)")


def test_gpt_ini_step_records_both_halves_for_mixed_content() -> None:
    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Mixed Sides Policy",
        settings=(
            RegistrySetting(
                id="c1",
                side="computer",
                hive="HKLM",
                key=r"Software\Policies\Synthetic",
                value_name="MachineValue",
                registry_type="REG_DWORD",
                value=1,
            ),
            RegistrySetting(
                id="u1",
                side="user",
                hive="HKCU",
                key=r"Software\Policies\Synthetic",
                value_name="UserValue",
                registry_type="REG_DWORD",
                value=1,
            ),
        ),
    )
    step = _gpt_ini_step(generate_publication_plan(gpo))
    assert step.version_half == "both"


def test_gpt_ini_step_user_half_from_user_gpp_collection() -> None:
    from gpo_studio.gpp import GppCollection, GppGroup

    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="User GPP Policy",
        gpp_collections=(
            GppCollection(scope="user", groups=(GppGroup(name="G1"),)),
        ),
    )
    step = _gpt_ini_step(generate_publication_plan(gpo))
    assert step.version_half == "user"


def test_gpt_ini_step_has_no_half_without_sysvol_content() -> None:
    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Unlinked Policy",
        links=(GPOLink(id="l1", target="OU=Servers,DC=example,DC=test"),),
    )
    step = _gpt_ini_step(generate_publication_plan(gpo, target="sysvol"))
    assert step.version_half is None
    assert "No GPT.INI version increment" in step.detail


# ---------------------------------------------------------------------------
# Extension-list registration (WI-057) and the GPO comment (WI-058)
#
# Both were found by the Plan 034 publication probe on LabMS01: the plan named
# every byte-bearing SYSVOL file Windows produced and nothing that would make
# any of them run. The assertions below are the measurement, not a design.
# ---------------------------------------------------------------------------


def _gpo_with_registry_and_verified_gpp() -> GPO:
    """The probe's GPO: both registry sides, plus one verified family per side."""
    return GPO(
        guid="31415926-5358-9793-2384-626433832795",
        name="zz-studio-evidence-pub-completeness",
        domain="synthetic.test",
        settings=(
            RegistrySetting(
                id="m1",
                side="computer",
                hive="HKLM",
                key=r"SOFTWARE\Policies\SyntheticApp",
                value_name="MachineFlag",
                registry_type="REG_DWORD",
                value=1,
            ),
            RegistrySetting(
                id="u1",
                side="user",
                hive="HKCU",
                key=r"SOFTWARE\Policies\SyntheticApp",
                value_name="UserFlag",
                registry_type="REG_SZ",
                value="on",
            ),
        ),
        gpp_collections=(
            GppCollection(
                scope="computer",
                services=(
                    GppService(service_name="SyntheticSvc", startup_type="automatic"),
                ),
            ),
            GppCollection(
                scope="user",
                drives=(
                    GppDrive(
                        letter="S", path=r"\\synthetic.test\share", label="Synthetic"
                    ),
                ),
            ),
        ),
    )


def test_plan_registers_the_extension_lists_windows_writes() -> None:
    """The exact values measured on LabMS01 after importing this GPO's backup.

    Three groups per side, not one, and the machine and user lists differ in
    their registry tool half -- ``{D02B1F72-...}`` against ``{D02B1F73-...}``.
    A fix that emitted one pair, or copied one side to the other, would satisfy
    a weaker assertion and still produce a GPO that applies nothing.
    """
    plan = generate_publication_plan(
        _gpo_with_registry_and_verified_gpp(), target="both"
    )
    details = {
        step.step_id: step.detail
        for step in plan.steps
        if step.operation == "update_extension_lists"
    }
    assert set(details) == {"register-machine-extensions", "register-user-extensions"}
    assert details["register-machine-extensions"] == (
        "Set gPCMachineExtensionNames to "
        "[{35378EAC-683F-11D2-A89A-00C04FBBCFA2}{D02B1F72-3407-48AE-BA88-E8213C6761F1}]"
        "[{00000000-0000-0000-0000-000000000000}{CC5746A9-9B74-4BE5-AE2E-64379C86E0E4}]"
        "[{91FBB303-0CD5-4055-BF42-E512A681B325}{CC5746A9-9B74-4BE5-AE2E-64379C86E0E4}]"
    )
    assert details["register-user-extensions"] == (
        "Set gPCUserExtensionNames to "
        "[{35378EAC-683F-11D2-A89A-00C04FBBCFA2}{D02B1F73-3407-48AE-BA88-E8213C6761F1}]"
        "[{00000000-0000-0000-0000-000000000000}{2EA1A81B-48E5-45E9-8BB7-A6E3AC170006}]"
        "[{5794DAFD-BE60-433F-88A2-1A31939AC01F}{2EA1A81B-48E5-45E9-8BB7-A6E3AC170006}]"
    )


def test_the_planner_and_the_exporter_cannot_disagree_about_extensions() -> None:
    """The planner's value must be the one the native backup actually writes.

    WI-057 was a divergence between two modules' beliefs about the same
    attribute, so the regression that matters is not the literal string above
    but that these two keep agreeing when the vocabulary changes.
    """
    gpo = _gpo_with_registry_and_verified_gpp()
    registration = extension_registration(gpo)
    with zipfile.ZipFile(io.BytesIO(gpmc_backup_bundle(gpo))) as archive:
        backup_xml = next(
            archive.read(name)
            for name in archive.namelist()
            if name.endswith("Backup.xml")
        ).decode("utf-8")
    assert registration.machine in backup_xml
    assert registration.user in backup_xml


def test_a_sysvol_only_plan_refuses_content_no_extension_would_process() -> None:
    """SYSVOL-only cannot reach a directory attribute, so it must not pretend to."""
    plan = generate_publication_plan(
        _gpo_with_registry_and_verified_gpp(), target="sysvol"
    )
    unreachable = [
        s.step_id for s in plan.steps if s.operation == "extension_lists_unreachable"
    ]
    assert unreachable == [
        "unreachable-machine-extensions",
        "unreachable-user-extensions",
    ]
    issues = validate_publication_plan(plan)
    assert any(issue.check == "extension_lists_unreachable" for issue in issues)
    assert any(
        issue.level == "error" and issue.check == "extension_lists_unreachable"
        for issue in issues
    )


def test_an_unverified_gpp_family_refuses_rather_than_guessing_a_value() -> None:
    """No captured metadata means no honest extension list, so the plan refuses.

    EnvironmentVariables is a real GPP family Studio serializes and whose
    extension pair has never been measured; guessing one is how a GPO ends up
    registering an extension that does not exist.
    """
    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Unverified Family",
        domain="synthetic.test",
        gpp_collections=(
            GppCollection(
                scope="user",
                environment=(
                    GppEnvironment(name="SYNTHETIC_HOME", value=r"C:\synthetic"),
                ),
            ),
        ),
    )
    plan = generate_publication_plan(gpo, target="both")
    refusals = [
        s for s in plan.steps if s.operation == "unsupported_extension_registration"
    ]
    assert len(refusals) == 1
    assert "EnvironmentVariables" in refusals[0].detail
    assert not any(s.operation == "update_extension_lists" for s in plan.steps)
    issues = validate_publication_plan(plan)
    assert any(
        issue.check == "unsupported_extension_registration" for issue in issues
    )


def test_a_described_gpo_publishes_its_comment_and_an_undescribed_one_does_not() -> None:
    """GPO.cmt exists only when the GPO has a comment -- measured with a control.

    The probe's first reading was that an unnamed GPO.cmt was a second
    completeness gap; a control run differing only in ``New-GPO -Comment``
    produced no such file. The pair below is that control, kept so the
    condition cannot quietly widen to "always emit".
    """
    described = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Described",
        domain="synthetic.test",
        description="Synthetic review note.",
        settings=_gpo_with_registry().settings,
    )
    plan = generate_publication_plan(described, target="both")
    comment_steps = [s for s in plan.steps if s.operation == "write_gpo_comment"]
    assert [s.detail for s in comment_steps] == ["Write GPO.cmt"]

    undescribed = replace(described, description="")
    plan = generate_publication_plan(undescribed, target="both")
    assert not [s for s in plan.steps if s.operation == "write_gpo_comment"]


# ---------------------------------------------------------------------------
# Side enablement (WI-070)
#
# A disabled side lives in the directory object's `flags` attribute. apply.ps1
# sets it through GpoStatus; the planner has no step that writes it, so a plan
# executed as written would publish the side enabled. Until a step exists and a
# lane measures it, the planner refuses, in the same shape as its other
# refusals.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("computer_enabled", "user_enabled", "named"),
    [
        (False, True, "the computer side is disabled"),
        (True, False, "the user side is disabled"),
        (False, False, "the computer and user sides are disabled"),
    ],
)
@pytest.mark.parametrize("target", ["ad", "sysvol", "both"])
def test_a_disabled_side_is_refused_rather_than_published_enabled(
    computer_enabled: bool,
    user_enabled: bool,
    named: str,
    target: PublicationTarget,
) -> None:
    gpo = replace(
        _gpo_with_registry_and_verified_gpp(),
        computer_enabled=computer_enabled,
        user_enabled=user_enabled,
    )
    plan = generate_publication_plan(gpo, target=target)
    refusals = [s for s in plan.steps if s.operation == "unsupported_side_status"]
    assert len(refusals) == 1
    assert refusals[0].step_id == "unsupported-side-status"
    assert refusals[0].target == target
    assert refusals[0].sysvol_path is None
    assert named in refusals[0].detail
    assert refusals[0].detail.startswith("Publication refused:")
    errors = [
        issue
        for issue in validate_publication_plan(plan)
        if issue.check == "unsupported_side_status"
    ]
    assert len(errors) == 1
    assert errors[0].level == "error"
    assert errors[0].component == "plan"


def test_an_enabled_gpo_carries_no_side_refusal() -> None:
    """The control: the refusal must not fire for the shape the lane measures."""
    plan = generate_publication_plan(_gpo_with_registry_and_verified_gpp(), target="both")
    assert not [s for s in plan.steps if s.operation == "unsupported_side_status"]
    assert not [
        issue
        for issue in validate_publication_plan(plan)
        if issue.check == "unsupported_side_status"
    ]


def test_the_side_refusal_is_excluded_from_the_planned_sysvol_file_set() -> None:
    """A refusal names no file, so the completeness comparison is unchanged."""
    from gpo_studio.publication import planned_sysvol_paths

    gpo = _gpo_with_registry_and_verified_gpp()
    disabled = replace(gpo, user_enabled=False)
    assert planned_sysvol_paths(generate_publication_plan(disabled)) == (
        planned_sysvol_paths(generate_publication_plan(gpo))
    )


def test_the_retired_script_branch_stays_retired() -> None:
    """2026-10-07 ruling: the SYSVOL-copying script generator is deleted.

    It copied files straight into SYSVOL, which docs/live-publication.md
    forbids. Publication goes through the native GPMC backup and Import-GPO
    plus apply.ps1. This pins the deletion so the branch cannot quietly return
    under the old names.
    """
    import importlib.util

    import gpo_studio.publication as publication

    for name in (
        "generate_publication_script",
        "PowerShellPublicationScript",
        "_WINDOWS_VERIFIED_OPERATIONS",
        "_gpt_ini_step_lines",
    ):
        assert not hasattr(publication, name), name
    assert importlib.util.find_spec("gpo_studio.artifact_store") is None
