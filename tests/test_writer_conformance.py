"""Plan 033 WP-1B: writer-conformance summary and comparison tests."""

from __future__ import annotations

import io
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from gpo_studio.export import gpmc_backup_bundle
from gpo_studio.gpp import GppCollection, GppGroup, GppGroupMember
from gpo_studio.gpp_adapters import GppDrive, GppLocalUser, GppScheduledTask
from gpo_studio.ilt import IltFilter, IltPredicate
from gpo_studio.model import GPO, RegistrySetting
from gpo_studio.writer_conformance import (
    NATIVE_GPP_FAMILIES,
    compare_preferences,
    compare_summaries,
    native_shape_findings,
    summary_from_backup,
    summary_from_gpmc_report,
    summary_from_gpo,
)


def _gpo(
    *,
    drives: tuple[GppDrive, ...] = (),
    groups: tuple[GppGroup, ...] = (),
    local_users: tuple[GppLocalUser, ...] = (),
    scheduled_tasks: tuple[GppScheduledTask, ...] = (),
    settings: tuple[RegistrySetting, ...] = (),
) -> GPO:
    machine = GppCollection(
        scope="computer",
        groups=groups,
        local_users=local_users,
        scheduled_tasks=scheduled_tasks,
    )
    user = GppCollection(scope="user", drives=drives)
    return GPO(
        guid="11111111-2222-3333-4444-555555555556",
        name="WP1B Fixture",
        domain="synthetic.test",
        settings=settings,
        gpp_collections=(machine, user),
    )


def _extract(gpo: GPO, root: Path) -> Path:
    with zipfile.ZipFile(io.BytesIO(gpmc_backup_bundle(gpo))) as archive:
        archive.extractall(root)
    content_roots = list(root.glob("*/DomainSysvol/GPO"))
    assert len(content_roots) == 1
    return content_roots[0]


DRIVE = GppDrive(
    letter="P",
    path="\\\\lab.example\\share\\probe",
    label="Probe Share",
    action="update",
    id="{AAAAAAAA-0000-0000-0000-00000000A004}",
)
GROUP = GppGroup(
    name="Administrators",
    action="update",
    description="WP-1B fixture group",
    members=(
        GppGroupMember(
            name="LAB\\conformance-probe",
            sid="S-1-5-21-1111111111-2222222222-3333333333-1001",
            action="add",
        ),
    ),
    id="{AAAAAAAA-0000-0000-0000-00000000A001}",
)
LOCAL_USER = GppLocalUser(
    user_name="probeuser",
    full_name="WP-1B Probe User",
    account_disabled=True,
    action="update",
    id="{AAAAAAAA-0000-0000-0000-00000000A002}",
)
TASK = GppScheduledTask(
    name="WP1B Probe Task",
    program="C:\\Windows\\System32\\cmd.exe",
    arguments="/c exit 0",
    trigger_type="daily",
    trigger_time="03:00:00",
    action="update",
    id="{AAAAAAAA-0000-0000-0000-00000000A003}",
)
MACHINE_SETTING = RegistrySetting(
    id="machine",
    side="computer",
    hive="HKLM",
    key=r"Software\Policies\GPOStudio\WP1B",
    value_name="MachineValue",
    registry_type="REG_DWORD",
    value=7,
)
USER_SETTING = RegistrySetting(
    id="user",
    side="user",
    hive="HKCU",
    key=r"Software\Policies\GPOStudio\WP1B",
    value_name="UserValue",
    registry_type="REG_SZ",
    value="wp1b",
)


def test_summary_covers_every_native_family() -> None:
    summary = summary_from_gpo(
        _gpo(drives=(DRIVE,), groups=(GROUP,), local_users=(LOCAL_USER,), scheduled_tasks=(TASK,))
    )
    preferences = summary["preferences"]
    assert isinstance(preferences, dict)
    for scope in ("computer", "user"):
        assert set(preferences[scope]) == set(NATIVE_GPP_FAMILIES)


def test_native_round_trip_is_semantically_identical(tmp_path: Path) -> None:
    """The Studio→native-container→Studio path must be a no-op semantically.

    This is the control for the Windows lane: if it ever fails, a WP-1B lab
    difference cannot be attributed to Windows.
    """
    gpo = _gpo(
        drives=(DRIVE,),
        groups=(GROUP,),
        local_users=(LOCAL_USER,),
        scheduled_tasks=(TASK,),
        settings=(MACHINE_SETTING, USER_SETTING),
    )
    content_root = _extract(gpo, tmp_path)
    differences = compare_summaries(summary_from_gpo(gpo), summary_from_backup(content_root))
    assert differences == (), [difference.describe() for difference in differences]


@pytest.mark.parametrize(
    "isolated",
    [
        {"drives": (DRIVE,)},
        {"groups": (GROUP,)},
        {"local_users": (LOCAL_USER,)},
        {"scheduled_tasks": (TASK,)},
    ],
    ids=["drives", "groups", "local_users", "scheduled_tasks"],
)
def test_isolated_family_round_trips(tmp_path: Path, isolated: dict[str, object]) -> None:
    gpo = _gpo(**isolated)  # type: ignore[arg-type]
    content_root = _extract(gpo, tmp_path)
    differences = compare_summaries(summary_from_gpo(gpo), summary_from_backup(content_root))
    assert differences == (), [difference.describe() for difference in differences]


def test_comparison_is_order_insensitive_within_a_family() -> None:
    second = GppDrive(letter="Q", path="\\\\lab.example\\share\\other", action="update")
    forward = summary_from_gpo(_gpo(drives=(DRIVE, second)))
    reversed_order = summary_from_gpo(_gpo(drives=(second, DRIVE)))
    assert compare_summaries(forward, reversed_order) == ()


def test_item_identity_guids_are_normalized_out() -> None:
    """Item GUIDs are regenerated by GPMC and carry no policy meaning."""
    relabelled = GppDrive(
        letter=DRIVE.letter,
        path=DRIVE.path,
        label=DRIVE.label,
        action=DRIVE.action,
        id="{BBBBBBBB-0000-0000-0000-00000000B004}",
    )
    assert (
        compare_summaries(
            summary_from_gpo(_gpo(drives=(DRIVE,))), summary_from_gpo(_gpo(drives=(relabelled,)))
        )
        == ()
    )


@pytest.mark.parametrize(
    ("mutation", "expected_path"),
    [
        ({"path": "\\\\evil.example\\share"}, "path"),
        ({"action": "delete"}, "action"),
        ({"persistent": False}, "persistent"),
    ],
    ids=["path", "action", "persistent"],
)
def test_meaning_changes_are_reported(mutation: dict[str, object], expected_path: str) -> None:
    mutated = replace(DRIVE, **mutation)  # type: ignore[arg-type]
    differences = compare_summaries(
        summary_from_gpo(_gpo(drives=(DRIVE,))), summary_from_gpo(_gpo(drives=(mutated,)))
    )
    assert [difference.path.rsplit(".", 1)[-1] for difference in differences] == [expected_path]
    assert differences[0].kind == "changed"


def test_dropped_item_is_reported_as_missing() -> None:
    differences = compare_summaries(
        summary_from_gpo(_gpo(drives=(DRIVE,))), summary_from_gpo(_gpo())
    )
    assert [difference.kind for difference in differences] == ["missing"]
    assert differences[0].path.endswith("drives[0]")


def test_injected_item_is_reported_as_unexpected() -> None:
    differences = compare_summaries(
        summary_from_gpo(_gpo()), summary_from_gpo(_gpo(drives=(DRIVE,)))
    )
    assert [difference.kind for difference in differences] == ["unexpected"]


def test_ilt_filter_divergence_is_reported() -> None:
    filtered = replace(
        DRIVE,
        ilt_filter=IltFilter(items=(IltPredicate(type="FilterComputer", value="LAB1"),)),
    )
    differences = compare_summaries(
        summary_from_gpo(_gpo(drives=(DRIVE,))), summary_from_gpo(_gpo(drives=(filtered,)))
    )
    assert differences and differences[0].path.endswith("ilt")


def test_registry_value_change_is_reported() -> None:
    mutated = RegistrySetting(
        id="machine",
        side="computer",
        hive="HKLM",
        key=MACHINE_SETTING.key,
        value_name="MachineValue",
        registry_type="REG_DWORD",
        value=8,
    )
    differences = compare_summaries(
        summary_from_gpo(_gpo(settings=(MACHINE_SETTING,))),
        summary_from_gpo(_gpo(settings=(mutated,))),
    )
    assert [difference.path.rsplit(".", 1)[-1] for difference in differences] == ["value"]


def test_registry_type_change_is_reported() -> None:
    """A REG_DWORD silently read back as REG_SZ is a conformance failure."""
    mutated = RegistrySetting(
        id="machine",
        side="computer",
        hive="HKLM",
        key=MACHINE_SETTING.key,
        value_name="MachineValue",
        registry_type="REG_SZ",
        value=7,
    )
    differences = compare_summaries(
        summary_from_gpo(_gpo(settings=(MACHINE_SETTING,))),
        summary_from_gpo(_gpo(settings=(mutated,))),
    )
    assert [difference.path.rsplit(".", 1)[-1] for difference in differences] == ["registry_type"]


NATIVE_CORPUS = Path(__file__).parent / "fixtures" / "native-gpp-gpmc"


def _native_captures() -> list[Path]:
    return sorted(
        capture
        for capture in NATIVE_CORPUS.glob("WI01A-*")
        if (capture / "gpreport-verify.xml").is_file() and list(capture.glob("*/DomainSysvol/GPO"))
    )


@pytest.mark.parametrize("capture", _native_captures(), ids=lambda path: path.name)
def test_gpmc_report_agrees_with_gpmc_backup(capture: Path) -> None:
    """Two independent GPMC outputs must agree when read through Studio.

    The report and the backup are produced by different GPMC code paths from the
    same policy.  Comparing them validates the report reader against genuine
    Windows output rather than against Studio's own writer -- which is what
    caught the report reader dropping element text, invisible to any
    Studio-authored fixture because Studio writes scalar attributes.
    """
    content_root = next(iter(capture.glob("*/DomainSysvol/GPO")))
    differences = compare_preferences(
        summary_from_backup(content_root), summary_from_gpmc_report(capture / "gpreport-verify.xml")
    )
    assert differences == (), [difference.describe() for difference in differences]


def test_report_reader_preserves_task_scheduler_2_command() -> None:
    """A TaskV2 command lives in element text, not an attribute."""
    capture = NATIVE_CORPUS / "WI01A-SchedTasks-GPMC"
    summary = summary_from_gpmc_report(capture / "gpreport-verify.xml")
    preferences = summary["preferences"]
    assert isinstance(preferences, dict)
    tasks = preferences["computer"]["scheduled_tasks"]  # type: ignore[index]
    assert [task["program"] for task in tasks if task["program"]]


def test_native_shape_findings_flag_taskv2_without_embedded_payload() -> None:
    """Studio's scalar TaskV2 authoring has no genuine GPMC precedent.

    GPMC echoes the scalar attributes back in its report, so a round trip cannot
    detect this; the shape is therefore checked against the native corpus.
    """
    findings = native_shape_findings(_gpo(scheduled_tasks=(TASK,)))
    assert len(findings) == 2
    assert any("no embedded <Task> payload" in finding for finding in findings)
    assert any("Task Scheduler 1.0 scalar properties" in finding for finding in findings)


def test_embedded_task_payload_clears_only_the_missing_payload_finding() -> None:
    """The scalar-attribute finding is unconditional for TaskV2.

    Studio's serializer writes the whole v1 attribute set on every TaskV2
    element, defaults included, so supplying an embedded payload does not make
    the emitted shape native.
    """
    embedded = replace(
        TASK,
        task_xml=(
            '<Task version="1.2"><Actions Context="Author"><Exec>'
            "<Command>C:\\Windows\\System32\\cmd.exe</Command>"
            "</Exec></Actions></Task>"
        ),
    )
    findings = native_shape_findings(_gpo(scheduled_tasks=(embedded,)))
    assert len(findings) == 1
    assert "Task Scheduler 1.0 scalar properties" in findings[0]


def test_task_scheduler_1_variant_is_not_flagged() -> None:
    """The v1 element is the schema those scalar properties belong to."""
    assert (
        native_shape_findings(_gpo(scheduled_tasks=(replace(TASK, element_variant="Task"),))) == ()
    )


def test_native_shape_findings_ignore_other_families() -> None:
    assert native_shape_findings(_gpo(drives=(DRIVE,), groups=(GROUP,))) == ()
