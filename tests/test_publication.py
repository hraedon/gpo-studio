from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import pytest

from gpo_studio.artifact_store import ArtifactStore
from gpo_studio.model import GPO, GPOLink, RegistrySetting, SecurityFilter
from gpo_studio.publication import (
    PublicationPlan,
    PublicationStep,
    _bump_gpt_version,
    _gpt_ini_step_lines,
    _gpt_version_half,
    _pack_gpt_version,
    _unpack_gpt_version,
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


# ---------------------------------------------------------------------------
# Packed GPT.INI version field. Evidence (R5, measured live on Windows Server
# 2025, 2026-09-03): the value is user * 65536 + machine, the halves move
# independently, and a flat +1 on a user-side publication corrupts the field.
# R8 corroboration: a GPO published by Windows showed Version=0x0002000A,
# i.e. machine 10 in the LOW 16-bit half, user 2 in the HIGH half.
# ---------------------------------------------------------------------------

CORROBORATION_VERSION = 0x0002000A  # user 2, machine 10


@pytest.mark.parametrize(
    ("machine", "user"),
    [
        (0, 0),
        (1, 0),
        (0, 1),
        (10, 2),
        (42, 7),
        (65535, 0),
        (0, 65535),
        (65535, 65535),
    ],
)
def test_gpt_version_unpack_repack_round_trip(machine: int, user: int) -> None:
    packed = _pack_gpt_version(machine, user)
    assert _unpack_gpt_version(packed) == (machine, user)


def test_gpt_version_unpack_corroboration_fixture() -> None:
    assert _unpack_gpt_version(CORROBORATION_VERSION) == (10, 2)


def test_gpt_version_machine_bump_moves_only_the_low_half() -> None:
    # Measured: a machine-side-only edit moved the value 0 -> 1.
    assert _bump_gpt_version(0, "machine") == 1
    assert _bump_gpt_version(CORROBORATION_VERSION, "machine") == 0x0002000B


def test_gpt_version_user_bump_moves_only_the_high_half() -> None:
    # Measured: a user-side-only edit moved the value 0 -> 65536 with the
    # machine half untouched.
    assert _bump_gpt_version(0, "user") == 65536
    assert _bump_gpt_version(CORROBORATION_VERSION, "user") == 0x0003000A


def test_gpt_version_half_wrap_does_not_carry() -> None:
    assert _bump_gpt_version(65535, "machine") == 0
    assert _bump_gpt_version(0xFFFF0000, "user") == 0


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
# Emitted update_gpt_ini step lines: half-aware increment, per-half marker.
# The step block is only reachable once an operation is Windows-verified, so
# these pin the exact lines the verified path will emit.
# ---------------------------------------------------------------------------


def test_gpt_ini_lines_do_not_flat_increment() -> None:
    for half in ("machine", "user", "both"):
        text = "\n".join(_gpt_ini_step_lines(half))
        assert "[int]$currentVersion + 1" not in text
        assert "$expectedVersion" not in text
        assert "% 65536" in text
        assert "/ 65536" in text
        assert "($halfValues[$half] + 1) % 65536" in text


def test_gpt_ini_lines_publish_only_the_requested_half() -> None:
    assert (
        "$publishHalves = @('machine')"
        in "\n".join(_gpt_ini_step_lines("machine"))
    )
    assert "$publishHalves = @('user')" in "\n".join(_gpt_ini_step_lines("user"))
    assert (
        "$publishHalves = @('machine', 'user')"
        in "\n".join(_gpt_ini_step_lines("both"))
    )


def test_gpt_ini_lines_marker_is_per_half() -> None:
    text = "\n".join(_gpt_ini_step_lines("machine"))
    assert "gpt.ini.studio-marker" in text
    assert "'^\\s*(machine|user)\\s*=\\s*(\\d+)\\s*$'" in text
    # The marker records only the halves this script wrote, never the whole
    # packed version, so the other half's record survives.
    assert '$markerLines += "$half=$($markerHalfValues[$half])"' in text


def test_gpt_ini_lines_keep_review_gate_and_ps51_compatibility() -> None:
    text = "\n".join(_gpt_ini_step_lines("user"))
    assert "$PSCmdlet.ShouldProcess($gptIniPath, 'Update GPT.INI version')" in text
    assert "-Encoding ASCII" in text
    # Fail closed rather than guessing when Version= is missing or unreadable.
    assert "throw" in text
    assert "refusing to guess the packed version" in text


def test_gpt_ini_lines_without_half_do_not_touch_the_version() -> None:
    text = "\n".join(_gpt_ini_step_lines(None))
    assert "Set-Content" not in text
    assert "not incremented" in text


# ---------------------------------------------------------------------------
# Behavioral: run the exact emitted step lines under PowerShell against temp
# SYSVOL-shaped fixtures. Windows PowerShell 5.1 is preferred (the estate
# shell); pwsh 7+ parses the same constructs and keeps this runnable
# off-Windows.
# ---------------------------------------------------------------------------

_HARNESS_GPO_GUID = "{11111111-2222-3333-4444-555555555555}"


def _powershell_binary() -> str | None:
    for candidate in ("powershell.exe", "pwsh"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


_requires_powershell = pytest.mark.skipif(
    _powershell_binary() is None, reason="no PowerShell interpreter available"
)

# Each scenario seeds gpt.ini (and optionally the marker), runs the emitted
# step lines once, and pins the exact packed version and marker afterwards.
# "rerun" scenarios start from the state a previous identical run left behind;
# the moved-other-half scenarios simulate an independent editor bumping the
# other half between runs.
_GPT_PS_SCENARIOS: tuple[dict[str, object], ...] = (
    {
        "name": "machine-fresh-publish",
        "half": "machine",
        "initial_version": 0,
        "initial_marker": None,
        "expected_version": 1,
        "expected_marker": "machine=1",
    },
    {
        "name": "machine-rerun-idempotent",
        "half": "machine",
        "initial_version": 1,
        "initial_marker": "machine=1",
        "expected_version": 1,
        "expected_marker": "machine=1",
    },
    {
        "name": "machine-rerun-after-user-half-moved",
        # GPME moved the user half 0 -> 1 after our machine publish.
        "half": "machine",
        "initial_version": 0x00010001,
        "initial_marker": "machine=1",
        "expected_version": 0x00010001,
        "expected_marker": "machine=1",
    },
    {
        "name": "machine-rerun-after-own-half-moved",
        # Someone else bumped the machine half after our publish; re-run
        # bumps again rather than clobbering back to the marker value.
        "half": "machine",
        "initial_version": 5,
        "initial_marker": "machine=1",
        "expected_version": 6,
        "expected_marker": "machine=6",
    },
    {
        "name": "user-fresh-publish",
        # The measured case: user-side-only publication, 0 -> 65536.
        "half": "user",
        "initial_version": 0,
        "initial_marker": None,
        "expected_version": 65536,
        "expected_marker": "user=1",
    },
    {
        "name": "user-rerun-idempotent",
        "half": "user",
        "initial_version": 65536,
        "initial_marker": "user=1",
        "expected_version": 65536,
        "expected_marker": "user=1",
    },
    {
        "name": "user-rerun-after-machine-half-moved",
        # After our user publish (65536), GPME bumped the machine half to 10.
        "half": "user",
        "initial_version": 0x0001000A,
        "initial_marker": "user=1",
        "expected_version": 0x0001000A,
        "expected_marker": "user=1",
    },
    {
        "name": "corroboration-user-publish",
        # R8 fixture: Version=0x0002000A (user 2, machine 10); a user-side
        # publish must reach 0x0003000A (user 3, machine 10).
        "half": "user",
        "initial_version": CORROBORATION_VERSION,
        "initial_marker": None,
        "expected_version": 0x0003000A,
        "expected_marker": "user=3",
    },
    {
        "name": "corroboration-machine-publish",
        # R8 fixture: a machine-side publish must reach 0x0002000B.
        "half": "machine",
        "initial_version": CORROBORATION_VERSION,
        "initial_marker": None,
        "expected_version": 0x0002000B,
        "expected_marker": "machine=11",
    },
    {
        "name": "both-fresh-publish",
        "half": "both",
        "initial_version": 0,
        "initial_marker": None,
        "expected_version": 0x00010001,
        "expected_marker": "machine=1;user=1",
    },
    {
        "name": "both-partially-applied",
        # Machine half already applied by an earlier run; only the user half
        # still moves, and the machine marker line survives.
        "half": "both",
        "initial_version": 1,
        "initial_marker": "machine=1",
        "expected_version": 0x00010001,
        "expected_marker": "machine=1;user=1",
    },
)


def _write_gpt_harness(
    harness_path: os.PathLike[str] | str, base_dir: str
) -> None:
    lines: list[str] = [
        "$ErrorActionPreference = 'Stop'",
        f"$GpoGuid = '{_HARNESS_GPO_GUID}'",
        f"$BaseDir = '{base_dir}'",
        "function Write-PlanLog { param([string]$Message) }",
    ]
    for half in ("machine", "user", "both"):
        lines.append(
            f"function Invoke-GptStep{half.capitalize()} "
            "{ [CmdletBinding(SupportsShouldProcess=$true)] param()"
        )
        lines.extend(_gpt_ini_step_lines(half))  # type: ignore[arg-type]
        lines.append("}")
    for scenario in _GPT_PS_SCENARIOS:
        name = str(scenario["name"])
        half = str(scenario["half"])
        lines.append(f"$scenarioRoot = Join-Path $BaseDir '{name}'")
        lines.append("$env:SystemRoot = $scenarioRoot")
        lines.append(
            '$polDir = Join-Path $scenarioRoot "SYSVOL\\domain\\Policies\\$GpoGuid"'
        )
        lines.append("New-Item -ItemType Directory -Force -Path $polDir | Out-Null")
        lines.append("$gptFile = Join-Path $polDir 'gpt.ini'")
        lines.append("$markerFile = Join-Path $polDir 'gpt.ini.studio-marker'")
        lines.append(
            f'Set-Content -Path $gptFile -Value "[General]`r`nVersion='
            f'{scenario["initial_version"]}" -Encoding ASCII'
        )
        marker = scenario.get("initial_marker")
        if marker is not None:
            marker_args = ", ".join(f"'{part}'" for part in str(marker).split(";"))
            lines.append(
                f"Set-Content -Path $markerFile -Value @({marker_args}) "
                "-Encoding ASCII"
            )
        lines.append(f"Invoke-GptStep{half.capitalize()}")
        lines.append("$raw = Get-Content $gptFile -Raw")
        lines.append(
            "$ver = [int64][regex]::Match($raw, 'Version=(\\d+)').Groups[1].Value"
        )
        lines.append(
            "if (Test-Path $markerFile) "
            "{ $mk = (Get-Content $markerFile) -join ';' } else { $mk = '' }"
        )
        lines.append(f"Write-Output ('RESULT|{name}|' + $ver + '|' + $mk)")
    with open(harness_path, "w", encoding="ascii", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


@_requires_powershell
def test_gpt_ini_step_powershell_behavior(tmp_path: os.PathLike[str]) -> None:
    """The emitted step lines are half-aware and marker-idempotent on a real
    PowerShell: machine publish moves only the low half, user publish only the
    high half (0 -> 65536), and a re-run distinguishes 'already applied' from
    'the other half moved since' without clobbering either."""
    binary = _powershell_binary()
    assert binary is not None
    harness = os.path.join(str(tmp_path), "gpt-step-harness.ps1")
    _write_gpt_harness(harness, str(tmp_path))
    proc = subprocess.run(
        [
            binary,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            harness,
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"PowerShell harness failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    actual: dict[str, tuple[int, str]] = {}
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT|"):
            _, name, version, marker = line.split("|", 3)
            actual[name] = (int(version), marker)
    expected = {
        str(scenario["name"]): (
            int(scenario["expected_version"]),
            str(scenario["expected_marker"]),
        )
        for scenario in _GPT_PS_SCENARIOS
    }
    assert actual == expected
