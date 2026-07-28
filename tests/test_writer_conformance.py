"""Plan 033 WP-1B: writer-conformance summary and comparison tests."""

from __future__ import annotations

import io
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from gpo_studio.export import gpmc_backup_bundle
from gpo_studio.gpp import (
    GppCollection,
    GppError,
    GppGroup,
    GppGroupMember,
    mark_edited,
    parse_gpp_collection,
    serialize_gpp,
)
from gpo_studio.gpp_adapters import GppDrive, GppLocalUser, GppScheduledTask
from gpo_studio.ilt import (
    OS_EDITION_PROSE_ONLY_VALUES,
    OS_EDITION_XSD_VALUES,
    IltFilter,
    IltPredicate,
)
from gpo_studio.import_export import collect_gpp_collections
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


#: Captures Studio's GPP parsers could not read. WI-019 is fixed, so this is
#: empty and all three now flow through the cross-validation set below. Kept as
#: the mechanism: a future capture that cannot be parsed is recorded here rather
#: than quietly dropped from validation.
CAPTURES_BLOCKED_BY_PARSER_DEFECTS: dict[str, str] = {}


def _native_captures() -> list[Path]:
    """Captures that exercise at least one family the writer summary covers.

    Families outside :data:`NATIVE_GPP_FAMILIES` summarize to empty on both
    sides, so including them would produce vacuous passes that look like
    validation.
    """
    captures: list[Path] = []
    for capture in sorted(NATIVE_CORPUS.glob("WI01A-*")):
        if not (capture / "gpreport-verify.xml").is_file():
            continue
        roots = list(capture.glob("*/DomainSysvol/GPO"))
        if not roots:
            continue
        if capture.name in CAPTURES_BLOCKED_BY_PARSER_DEFECTS:
            continue
        summary = summary_from_backup(roots[0])
        preferences = summary["preferences"]
        assert isinstance(preferences, dict)
        if any(items for families in preferences.values() for items in families.values()):
            captures.append(capture)
    return captures


@pytest.mark.parametrize(
    "capture",
    [pytest.param(NATIVE_CORPUS / name, id=name) for name in CAPTURES_BLOCKED_BY_PARSER_DEFECTS],
)
def test_known_parser_defects_still_block_native_captures(capture: Path) -> None:
    """Pin any capture Studio still cannot parse.

    Empty since WI-019 was fixed. The mechanism stays so that an unparseable
    capture has to be recorded deliberately rather than silently excluded from
    the cross-validation set.
    """
    content_root = next(iter(capture.glob("*/DomainSysvol/GPO")))
    with pytest.raises(GppError):
        summary_from_backup(content_root)


@pytest.mark.parametrize(
    "capture",
    [
        pytest.param(NATIVE_CORPUS / name, id=name)
        for name in ("WI01A-Printers-GPMC", "WI01A-Services-GPMC", "WI01A-Shortcuts-GPMC")
    ],
)
def test_wi019_captures_now_import(capture: Path) -> None:
    """The three WI-019 captures parse, and are no longer merely 'not erroring'.

    Each previously raised GppError, which rejected the WHOLE backup at the
    supported import boundary -- not just the offending family.
    """
    content_root = next(iter(capture.glob("*/DomainSysvol/GPO")))
    collections = collect_gpp_collections(content_root)
    assert any(
        items
        for collection in collections
        for items in (collection.printers, collection.services, collection.shortcuts)
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


def test_taskv2_authoring_now_emits_the_native_shape() -> None:
    """WI-018 inverted: a scalar-authored TaskV2 emits a genuine payload.

    Studio used to emit the Task Scheduler 1.0 attributes on a TaskV2 element
    with no <Task> payload, which the CSE silently ignored -- the task was never
    created (endpoint-confirmed 2026-07-27). The writer now synthesizes the
    payload, and this gate checks the SERIALIZED output rather than the model,
    because those legitimately differ now.
    """
    assert native_shape_findings(_gpo(scheduled_tasks=(TASK,))) == ()


def test_explicit_task_payload_is_preserved_over_synthesis() -> None:
    """An operator-supplied payload wins; synthesis only fills a gap."""
    embedded = replace(
        TASK,
        task_xml=(
            '<Task version="1.2"><Actions Context="Author"><Exec>'
            "<Command>C:\\Windows\\System32\\notepad.exe</Command>"
            "</Exec></Actions></Task>"
        ),
    )
    assert native_shape_findings(_gpo(scheduled_tasks=(embedded,))) == ()
    emitted = serialize_gpp(
        GppCollection(scope="computer", scheduled_tasks=(embedded,))
    )["ScheduledTasks/ScheduledTasks.xml"]
    assert b"notepad.exe" in emitted
    assert b"cmd.exe" not in emitted


def test_task_scheduler_1_variant_is_not_flagged() -> None:
    """The v1 element is the schema those scalar properties belong to."""
    assert (
        native_shape_findings(_gpo(scheduled_tasks=(replace(TASK, element_variant="Task"),))) == ()
    )


def test_native_shape_findings_ignore_other_families() -> None:
    assert native_shape_findings(_gpo(drives=(DRIVE,), groups=(GROUP,))) == ()


NESTED_ILT_CAPTURE = NATIVE_CORPUS / "WI01A-NestedILT-GPMC"
ADMX_CORPUS = Path(__file__).parent / "fixtures" / "admx-real"


def _nested_ilt_drive() -> object:
    content_root = next(iter(NESTED_ILT_CAPTURE.glob("*/DomainSysvol/GPO")))
    collections = [c for c in collect_gpp_collections(content_root) if c.drives]
    assert len(collections) == 1
    return collections[0].drives[0]


def test_nested_ilt_collection_is_preserved_whole_and_in_order() -> None:
    """Plan 033 prediction P2, settled against a genuine GPMC capture.

    ``FilterCollection`` is in no tag map, so it must survive as one opaque
    item carrying its whole subtree. The fixture deliberately places a typed
    predicate on *either side* of the collection: that is the only arrangement
    that can catch a reordering, and nesting *mapped* predicate types inside
    the collection is the only way to distinguish "preserved as a group" from
    "silently flattened to top level".
    """
    ilt = _nested_ilt_drive().ilt_filter  # type: ignore[attr-defined]
    assert [type(item).__name__ for item in ilt.items] == [
        "IltPredicate",
        "str",
        "IltPredicate",
    ]
    assert [predicate.type for predicate in ilt.predicates] == ["group", "domain"]

    collection = ilt.unknown_predicates[0]
    assert collection.startswith("<FilterCollection")
    # Grouping changes meaning: A AND (B OR C) is not A AND B OR C.
    assert collection.count("FilterOrgUnit") == 2
    assert 'bool="OR"' in collection


def test_nested_ilt_survives_reserialization_from_the_typed_model() -> None:
    """Preservation is only real if it survives a rebuild, not just byte passthrough.

    ``mark_edited`` drops the verbatim source bytes, forcing serialization to
    reconstruct the XML from the typed model — the path an edited GPO takes.
    """
    content_root = next(iter(NESTED_ILT_CAPTURE.glob("*/DomainSysvol/GPO")))
    collection = [c for c in collect_gpp_collections(content_root) if c.drives][0]
    rebuilt = serialize_gpp(mark_edited(collection))["Drives/Drives.xml"]
    reparsed = parse_gpp_collection("user", {"Drives/Drives.xml": rebuilt})
    ilt = reparsed.drives[0].ilt_filter
    assert ilt is not None
    assert [predicate.type for predicate in ilt.predicates] == ["group", "domain"]
    assert len(ilt.unknown_predicates) == 1
    assert ilt.unknown_predicates[0].count("FilterOrgUnit") == 2
    assert 'bool="OR"' in ilt.unknown_predicates[0]


def test_genuine_gpmc_os_filter_is_typed() -> None:
    """WI-021 inverted: genuine FilterOs predicates are modelled, not opaque.

    Before the fix the tag map said "FilterOS" while GPMC writes "FilterOs", so
    every genuine OS predicate fell into the opaque branch. It was preserved --
    which is why no round-trip test caught it -- but never modelled, and the
    element Studio emitted was one the CSE could not parse at all.
    """
    content_root = next(iter((NATIVE_CORPUS / "WI01A-DriveMaps-GPMC").glob("*/DomainSysvol/GPO")))
    predicates = [
        predicate
        for collection in collect_gpp_collections(content_root)
        for drive in collection.drives
        if drive.ilt_filter is not None
        for predicate in drive.ilt_filter.predicates
        if predicate.type == "os"
    ]
    assert predicates, "no typed OS predicate found in the genuine capture"
    criteria = predicates[0].os_criteria
    assert criteria is not None
    assert (criteria.os_class, criteria.version) == ("NT", "WINTHRESHOLD")
    # No genuine OS predicate should carry a value outside the documented
    # enumerations; if one does, the model is behind the platform.
    assert all(
        predicate.os_criteria is not None and predicate.os_criteria.unrecognized() == ()
        for predicate in predicates
    )


def test_studio_emits_the_native_os_filter_element() -> None:
    """The emitted element must be one GPMC and the CSE actually recognize."""
    import xml.etree.ElementTree as ElementTree

    from gpo_studio.ilt import IltFilter, IltOsCriteria, IltPredicate, serialize_ilt

    emitted = ElementTree.tostring(
        serialize_ilt(
            IltFilter(
                items=(
                    IltPredicate(
                        type="os",
                        os_criteria=IltOsCriteria(os_class="NT", version="WINTHRESHOLD"),
                    ),
                )
            )
        ),
        encoding="unicode",
    )
    assert "<FilterOs " in emitted
    assert "FilterOS" not in emitted
    assert "osType" not in emitted
    for attribute in ("class=", "version=", "type=", "edition=", "sp="):
        assert attribute in emitted


def test_disabled_policy_deletes_every_element_value_name() -> None:
    """Pins WI-020 to the gpedit evidence that produced it.

    Disabling ``UserProfiles.admx``/``LimitSize`` through ``gpedit.msc`` wrote
    six deletion records: the policy's own ``valueName`` and all five
    ``<elements>`` value names. Studio emitted one. The counts and names below
    are transcribed from that capture
    (``~/gpo-studio-captures/part-b/WI-008/1/disabled``).
    """
    from gpo_studio.admx import build_catalogue
    from gpo_studio.policy_config import PolicyConfiguration, resolve_policy

    admx = ADMX_CORPUS / "UserProfiles.admx"
    adml = ADMX_CORPUS / "UserProfiles.adml"
    if not admx.is_file():
        pytest.skip("real UserProfiles ADMX/ADML not vendored")
    catalogue = build_catalogue(admx.read_bytes(), adml.read_bytes())
    policy = next(item for item in catalogue.policies if item.id == "LimitSize")

    settings = resolve_policy(policy, PolicyConfiguration(side="user", values={}, state="disabled"))
    assert {item.value_name for item in settings} == {
        "EnableProfileQuota",
        "ProfileQuotaMessage",
        "MaxProfileSize",
        "IncludeRegInProQuota",
        "WarnUser",
        "WarnUserTimeout",
    }
    assert all(item.action == "delete" for item in settings)


OS_ILT_CAPTURE = NATIVE_CORPUS / "WI01A-OS-ILT"


def _os_criteria_from_capture() -> list[object]:
    content_root = next(iter(OS_ILT_CAPTURE.glob("*/DomainSysvol/GPO")))
    return [
        predicate.os_criteria
        for collection in collect_gpp_collections(content_root)
        for drive in collection.drives
        if drive.ilt_filter is not None
        for predicate in drive.ilt_filter.predicates
        if predicate.type == "os"
    ]


def test_every_gpmc_os_entry_parses_with_recognized_values() -> None:
    """Every OS entry GPMC offers on Windows Server 2025 must model cleanly.

    The capture is one filter per Server 2025 entry and one per Windows 10
    entry, taken from the Targeting Editor dropdown. If any value is
    unrecognized the model is behind the platform.
    """
    criteria = _os_criteria_from_capture()
    assert len(criteria) >= 11
    unrecognized = {
        finding
        for item in criteria
        for finding in item.unrecognized()  # type: ignore[union-attr]
    }
    assert unrecognized == set()


def test_gpmc_os_vocabulary_stops_at_the_threshold_generation() -> None:
    """No version value exceeds WINTHRESHOLDSRV, which is why there is no Win11.

    GPMC's OS-filter vocabulary was never extended past the Threshold
    generation: Server 2025 reports WINTHRESHOLDSRV and Windows 10 reports
    WINTHRESHOLD, and newer products are distinguished by *edition* instead.
    The spec's version enumeration is therefore complete in practice.
    """
    versions = {item.version for item in _os_criteria_from_capture()}  # type: ignore[union-attr]
    assert versions == {"WINTHRESHOLD", "WINTHRESHOLDSRV"}


def test_capture_exercises_undocumented_and_prose_only_editions() -> None:
    """Guards the two ways the published XSD is incomplete.

    Four editions appear only in the spec's prose, and ``64PRO`` appears in
    neither prose nor XSD -- it is known solely because GPMC emitted it. If a
    future change narrowed the accepted set to the XSD, real captures would
    stop parsing; this pins that they must not.
    """
    editions = {item.edition for item in _os_criteria_from_capture()}  # type: ignore[union-attr]
    assert {"64STGSTD", "64STGWKGRP", "64MPPREM", "64ESSSOL"} <= editions
    assert "64PRO" in editions
    assert "64PRO" not in OS_EDITION_XSD_VALUES
    assert "64PRO" not in OS_EDITION_PROSE_ONLY_VALUES
