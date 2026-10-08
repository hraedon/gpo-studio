"""Tests for the same-domain lifecycle model: manifests, plans, scope survival.

Plan 028 WP-1, reworked for Plan 034 (2026-10-07). The state machine and the
duplicate migration table were deleted with their tests; the module docstring
says why.
"""

from __future__ import annotations

import base64
import shutil
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import get_args

import pytest

from gpo_studio.backup import (
    BackupError,
    BackupGpo,
    CseExtension,
    CseFile,
    GpmcBackup,
    read_backup,
)
from gpo_studio.lifecycle import (
    SCOPE_DIMENSIONS,
    SCOPE_SURVIVAL,
    WINDOWS_OPERATIONS,
    BackupFileEntry,
    BackupIndex,
    BackupManifest,
    RestoreMode,
    RestorePlan,
    ScopeDimension,
    Survival,
    WindowsOperation,
    cmdlet_for,
    generate_restore_plan,
    manifest_from_backup,
    target_identity_for,
)
from gpo_studio.model import ValidationError

_GUID = "{11111111-2222-3333-4444-555555555555}"
_NATIVE = Path(__file__).parent / "fixtures" / "native-gpp-gpmc"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _file(
    relative_path: str = "Machine/Registry.pol",
    content_hash: str = "a" * 64,
    size: int = 128,
) -> BackupFileEntry:
    return BackupFileEntry(
        relative_path=relative_path,
        content_hash=content_hash,
        size=size,
    )


def _manifest(**overrides: object) -> BackupManifest:
    fields: dict[str, object] = {
        "backup_id": "{AAAAAAAA-0000-0000-0000-000000000001}",
        "gpo_guid": _GUID,
        "gpo_display_name": "Test Policy",
        "domain": "studio.local",
        "created_at": "2026-01-01T00:00:00",
        "files": (_file(),),
    }
    fields.update(overrides)
    return BackupManifest(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# manifest_from_backup: the bridge from a real Backup-GPO directory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture",
    ["WI01A-DriveMaps-GPMC", "WI01A-Services-GPMC", "WI01A-MixedCSE-GPMC"],
)
def test_a_native_backup_yields_a_manifest(fixture: str) -> None:
    manifest = manifest_from_backup(read_backup(_NATIVE / fixture))
    assert manifest.validate() == ()
    assert manifest.gpo_display_name == fixture
    assert manifest.domain
    assert manifest.files
    assert all(len(f.content_hash) == 64 for f in manifest.files)


def test_the_backup_id_is_not_the_gpo_guid() -> None:
    """GPMC's ``ID`` names the backup instance; ``GPOGuid`` names the GPO.

    The pre-bridge model had nothing to stop a caller conflating them, and a
    restore aimed at the backup ID would aim at a GPO that does not exist.
    """
    manifest = manifest_from_backup(read_backup(_NATIVE / "WI01A-DriveMaps-GPMC"))
    backup_id = manifest.backup_id.strip("{}").casefold()
    gpo_guid = manifest.gpo_guid.strip("{}").casefold()
    assert backup_id == "e9f0a681-9b36-419e-a16e-c2c59dc44dad"
    assert gpo_guid == "f0197e25-3e19-4835-b296-3c35dc069635"
    assert backup_id != gpo_guid


def test_backup_time_is_kept_naive_rather_than_given_an_invented_offset() -> None:
    manifest = manifest_from_backup(read_backup(_NATIVE / "WI01A-DriveMaps-GPMC"))
    assert manifest.created_at == "2026-07-26T17:55:38"
    assert not manifest.created_at.endswith("Z")
    assert "+" not in manifest.created_at


def test_an_empty_wmi_filter_element_means_no_filter() -> None:
    manifest = manifest_from_backup(read_backup(_NATIVE / "WI01A-DriveMaps-GPMC"))
    assert manifest.has_wmi_filter is False
    assert manifest.wmi_filter_reference == ""


def _with_backup_xml(tmp_path: Path, transform: Callable[[bytes], bytes]) -> Path:
    source = _NATIVE / "WI01A-DriveMaps-GPMC"
    target = tmp_path / "backup"
    shutil.copytree(source, target)
    backup_xml = next(target.glob("{*}/Backup.xml"))
    original = backup_xml.read_bytes()
    changed = transform(original)
    assert changed != original, "the transform matched nothing"
    backup_xml.write_bytes(changed)
    return target


def test_a_populated_wmi_filter_element_is_kept_verbatim(tmp_path: Path) -> None:
    """Synthetic: the populated shape has never been captured, so no parse."""
    target = _with_backup_xml(
        tmp_path,
        lambda data: data.replace(
            b"<WMIFilter/>", b"<WMIFilter>[synthetic;{F};0]</WMIFilter>"
        ),
    )
    manifest = manifest_from_backup(read_backup(target))
    assert manifest.has_wmi_filter is True
    assert manifest.wmi_filter_reference == "[synthetic;{F};0]"


def test_a_wmi_filter_element_with_children_counts_as_a_filter(tmp_path: Path) -> None:
    target = _with_backup_xml(
        tmp_path,
        lambda data: data.replace(b"<WMIFilter/>", b"<WMIFilter><Name>x</Name></WMIFilter>"),
    )
    assert manifest_from_backup(read_backup(target)).has_wmi_filter is True


def test_a_disabled_side_in_a_native_backup_maps_to_the_gpo_status(tmp_path: Path) -> None:
    target = _with_backup_xml(
        tmp_path,
        lambda data: data.replace(b"<Options><![CDATA[0]]>", b"<Options><![CDATA[1]]>"),
    )
    assert manifest_from_backup(read_backup(target)).gpo_status == "user_disabled"


def _gpo(**overrides: object) -> BackupGpo:
    fields: dict[str, object] = {
        "guid": "11111111-2222-3333-4444-555555555555",
        "display_name": "Legacy",
        "domain": "studio.local",
    }
    fields.update(overrides)
    return BackupGpo(**fields)  # type: ignore[arg-type]


def _backup(*gpos: BackupGpo, backup_id: str = "{AAAAAAAA-0000-0000-0000-000000000001}",
            backup_time: str = "2026-01-01T00:00:00") -> GpmcBackup:
    return GpmcBackup(backup_time=backup_time, backup_id=backup_id, gpos=gpos)


@pytest.mark.parametrize(
    "computer,user,status",
    [
        (True, True, "all_settings_enabled"),
        (True, False, "user_disabled"),
        (False, True, "computer_disabled"),
        (False, False, "all_disabled"),
    ],
)
def test_side_enablement_maps_to_every_gpo_status(
    computer: bool, user: bool, status: str
) -> None:
    backup = _backup(_gpo(computer_enabled=computer, user_enabled=user))
    assert manifest_from_backup(backup).gpo_status == status


def test_a_backup_without_retained_xml_lists_its_scanned_files() -> None:
    extension = CseExtension(
        guid="{35378EAC-683F-11D2-A89A-00C04FBBCFA2}",
        side="machine",
        files=(CseFile("Registry.pol", "b" * 64, 10),),
    )
    manifest = manifest_from_backup(_backup(_gpo(machine_extensions=(extension,))))
    assert manifest.files == (BackupFileEntry("Machine/Registry.pol", "b" * 64, 10),)
    assert manifest.has_wmi_filter is False


def test_a_multi_gpo_backup_is_refused() -> None:
    backup = _backup(_gpo(), _gpo(guid="22222222-2222-3333-4444-555555555555"))
    with pytest.raises(ValidationError) as excinfo:
        manifest_from_backup(backup)
    assert excinfo.value.issues[0].code == "multi_gpo_backup"


def test_a_backup_whose_manifest_would_be_invalid_is_refused() -> None:
    backup = _backup(_gpo(display_name=""), backup_id="", backup_time="")
    with pytest.raises(ValidationError) as excinfo:
        manifest_from_backup(backup)
    codes = {issue.code for issue in excinfo.value.issues}
    assert {"empty_backup_id", "empty_created_at", "empty_gpo_display_name"} <= codes


def _native_gpo_with_backup_xml(encoded: str) -> BackupGpo:
    gpo = read_backup(_NATIVE / "WI01A-DriveMaps-GPMC").gpos[0]
    assert gpo.backup_inventory is not None
    return replace(gpo, backup_inventory=replace(gpo.backup_inventory, backup_xml_base64=encoded))


def test_corrupt_retained_backup_xml_is_refused() -> None:
    with pytest.raises(BackupError, match="base64"):
        manifest_from_backup(_backup(_native_gpo_with_backup_xml("@@")))


def test_retained_backup_xml_without_core_settings_is_refused() -> None:
    encoded = base64.b64encode(b"<GroupPolicyBackupScheme/>").decode("ascii")
    with pytest.raises(BackupError, match="GroupPolicyCoreSettings"):
        manifest_from_backup(_backup(_native_gpo_with_backup_xml(encoded)))


def test_core_settings_without_a_wmi_element_means_no_filter() -> None:
    xml = b"<GroupPolicyBackupScheme><GroupPolicyCoreSettings/></GroupPolicyBackupScheme>"
    encoded = base64.b64encode(xml).decode("ascii")
    manifest = manifest_from_backup(_backup(_native_gpo_with_backup_xml(encoded)))
    assert manifest.has_wmi_filter is False


# ---------------------------------------------------------------------------
# BackupManifest
# ---------------------------------------------------------------------------


def test_backup_manifest_valid_has_no_issues() -> None:
    manifest = _manifest()
    assert manifest.validate() == ()


def test_backup_manifest_empty_guid_is_error() -> None:
    manifest = _manifest(gpo_guid="")
    issues = manifest.validate()
    codes = {i.code for i in issues}
    assert "empty_gpo_guid" in codes
    assert all(i.severity == "error" for i in issues if i.code == "empty_gpo_guid")


def test_backup_manifest_invalid_guid_is_error() -> None:
    manifest = _manifest(gpo_guid="not-a-guid")
    issues = manifest.validate()
    assert any(i.code == "invalid_gpo_guid" and i.severity == "error" for i in issues)


def test_backup_manifest_empty_backup_id_is_error() -> None:
    manifest = _manifest(backup_id="")
    issues = manifest.validate()
    assert any(i.code == "empty_backup_id" and i.severity == "error" for i in issues)


def test_backup_manifest_empty_files_is_warning() -> None:
    manifest = _manifest(files=())
    issues = manifest.validate()
    file_issues = [i for i in issues if i.code == "empty_files"]
    assert len(file_issues) == 1
    assert file_issues[0].severity == "warning"
    # No error-severity issues for an otherwise-complete manifest.
    assert not any(i.severity == "error" for i in issues)


def test_backup_manifest_file_with_empty_hash_is_error() -> None:
    manifest = _manifest(files=(_file(content_hash=""),))
    issues = manifest.validate()
    assert any(i.code == "empty_content_hash" and i.severity == "error" for i in issues)


def test_backup_manifest_all_required_fields_validated() -> None:
    manifest = _manifest(
        gpo_display_name="",
        domain="",
        created_at="",
    )
    codes = {i.code for i in manifest.validate()}
    assert "empty_gpo_display_name" in codes
    assert "empty_domain" in codes
    assert "empty_created_at" in codes


# ---------------------------------------------------------------------------
# BackupIndex
# ---------------------------------------------------------------------------


def _index_manifest(
    backup_id: str,
    created_at: str,
    gpo_guid: str = _GUID,
) -> BackupManifest:
    return _manifest(
        backup_id=backup_id,
        created_at=created_at,
        gpo_guid=gpo_guid,
    )


def test_backup_index_get_backup_found() -> None:
    index = BackupIndex(
        backups=(_index_manifest("b1", "2026-01-01T00:00:00Z"),)
    )
    assert index.get_backup("b1") is not None
    assert index.get_backup("b1").backup_id == "b1"


def test_backup_index_get_backup_missing_returns_none() -> None:
    index = BackupIndex(backups=(_index_manifest("b1", "2026-01-01T00:00:00Z"),))
    assert index.get_backup("nope") is None


def test_backup_index_backups_for_gpo_most_recent_first() -> None:
    index = BackupIndex(
        backups=(
            _index_manifest("old", "2026-01-01T00:00:00Z"),
            _index_manifest("newest", "2026-03-01T00:00:00Z"),
            _index_manifest("mid", "2026-02-01T00:00:00Z"),
        )
    )
    ordered = index.backups_for_gpo(_GUID)
    assert [b.backup_id for b in ordered] == ["newest", "mid", "old"]


def test_backup_index_backups_for_gpo_excludes_other_gpos() -> None:
    other_guid = "{99999999-8888-7777-6666-555555555555}"
    index = BackupIndex(
        backups=(
            _index_manifest("mine", "2026-01-01T00:00:00Z", gpo_guid=_GUID),
            _index_manifest("theirs", "2026-02-01T00:00:00Z", gpo_guid=other_guid),
        )
    )
    ordered = index.backups_for_gpo(_GUID)
    assert [b.backup_id for b in ordered] == ["mine"]


def test_backup_index_latest_backup_returns_most_recent() -> None:
    index = BackupIndex(
        backups=(
            _index_manifest("old", "2026-01-01T00:00:00Z"),
            _index_manifest("newest", "2026-03-01T00:00:00Z"),
        )
    )
    latest = index.latest_backup(_GUID)
    assert latest is not None
    assert latest.backup_id == "newest"


def test_backup_index_latest_backup_missing_returns_none() -> None:
    index = BackupIndex(backups=())
    assert index.latest_backup(_GUID) is None


# ---------------------------------------------------------------------------
# Modes are named after the cmdlets they mean
# ---------------------------------------------------------------------------


def test_every_windows_operation_names_its_cmdlet() -> None:
    assert cmdlet_for("restore_in_place").startswith("Restore-GPO ")
    assert cmdlet_for("import_into_existing").startswith("Import-GPO ")
    assert "-TargetGuid" in cmdlet_for("import_into_existing")
    assert "-CreateIfNeeded" in cmdlet_for("import_as_new")
    assert "-CreateIfNeeded" not in cmdlet_for("import_into_existing")
    assert cmdlet_for("copy").startswith("Copy-GPO ")
    assert "-CopyAcl" not in cmdlet_for("copy")
    assert cmdlet_for("copy_with_acl").endswith("-CopyAcl")
    assert cmdlet_for("import_to_draft") == ""


def test_restore_modes_are_the_windows_operations_plus_the_studio_draft() -> None:
    modes: set[str] = set()
    for member in get_args(RestoreMode):
        modes.update(get_args(member))
    assert modes == set(WINDOWS_OPERATIONS) | {"import_to_draft"}


@pytest.mark.parametrize(
    "mode,identity",
    [
        ("restore_in_place", "source"),
        ("import_into_existing", "existing_target"),
        ("import_as_new", "windows_assigned"),
        ("copy", "windows_assigned"),
        ("copy_with_acl", "windows_assigned"),
        ("import_to_draft", "studio_draft"),
    ],
)
def test_target_identity_per_mode(mode: RestoreMode, identity: str) -> None:
    assert target_identity_for(mode) == identity


# ---------------------------------------------------------------------------
# SCOPE_SURVIVAL: predictions, complete, consistent with the plan's identity
# ---------------------------------------------------------------------------


def test_the_survival_table_covers_every_operation_and_dimension() -> None:
    assert set(SCOPE_SURVIVAL) == set(WINDOWS_OPERATIONS)
    outcomes = set(get_args(Survival))
    for operation, row in SCOPE_SURVIVAL.items():
        assert tuple(row) == SCOPE_DIMENSIONS, operation
        assert set(row.values()) <= outcomes, operation
    assert set(SCOPE_DIMENSIONS) == set(get_args(ScopeDimension))


def test_the_survival_table_cannot_be_corrected_at_run_time() -> None:
    with pytest.raises(TypeError):
        SCOPE_SURVIVAL["copy"]["links"] = "kept"  # type: ignore[index]


def test_every_operation_is_predicted_to_keep_the_settings() -> None:
    """The control row: an operation that lost the settings would not be one."""
    for operation in WINDOWS_OPERATIONS:
        assert SCOPE_SURVIVAL[operation]["settings"] == "kept", operation


@pytest.mark.parametrize("operation", WINDOWS_OPERATIONS)
def test_the_guid_prediction_agrees_with_the_plans_target_identity(
    operation: WindowsOperation,
) -> None:
    expected = {
        "source": "kept",
        "existing_target": "replaced",
        "windows_assigned": "defaulted",
    }[target_identity_for(operation)]
    assert SCOPE_SURVIVAL[operation]["gpo_guid"] == expected


def test_replaced_is_predicted_only_where_a_target_pre_exists() -> None:
    """``replaced`` means the target's own prior value; a new GPO has none."""
    for operation in WINDOWS_OPERATIONS:
        if target_identity_for(operation) == "windows_assigned":
            assert "replaced" not in SCOPE_SURVIVAL[operation].values(), operation


def test_copy_acl_is_the_only_difference_between_the_two_copies() -> None:
    copy, with_acl = SCOPE_SURVIVAL["copy"], SCOPE_SURVIVAL["copy_with_acl"]
    differing: set[ScopeDimension] = {d for d in SCOPE_DIMENSIONS if copy[d] != with_acl[d]}
    assert differing == {"acl_security_filtering"}
    assert with_acl["acl_security_filtering"] == "kept"


def test_the_docstring_says_the_table_is_a_prediction() -> None:
    from gpo_studio import lifecycle

    assert lifecycle.__doc__ is not None and "predictions" in lifecycle.__doc__.casefold()
    assert lifecycle._predict.__doc__ is not None
    assert "PREDICTIONS" in lifecycle._predict.__doc__


# ---------------------------------------------------------------------------
# RestorePlan validation
# ---------------------------------------------------------------------------


def _plan(**overrides: object) -> RestorePlan:
    fields: dict[str, object] = {
        "backup_id": "{AAAAAAAA-0000-0000-0000-000000000001}",
        "mode": "restore_in_place",
        "source_gpo_guid": _GUID,
        "domain": "studio.local",
        "cmdlet": "",
        "target_identity": "source",
        "target_gpo_guid": _GUID,
    }
    fields.update(overrides)
    return RestorePlan(**fields)  # type: ignore[arg-type]


def _codes(plan: RestorePlan) -> set[str]:
    return {issue.code for issue in plan.validate() if issue.severity == "error"}


def test_restore_in_place_targets_only_the_backups_own_gpo() -> None:
    assert _codes(_plan()) == set()
    assert _codes(_plan(target_gpo_guid=_GUID.strip("{}").upper())) == set()
    other = "{99999999-8888-7777-6666-555555555555}"
    assert _codes(_plan(target_gpo_guid=other)) == {"restore_target_not_source"}


def test_import_into_existing_needs_a_guid_or_a_name() -> None:
    base = {"mode": "import_into_existing", "target_identity": "existing_target"}
    assert _codes(_plan(**base, target_gpo_guid="")) == {"empty_import_target"}
    assert _codes(_plan(**base, target_gpo_guid="", target_name="X")) == set()
    assert _codes(_plan(**base, target_gpo_guid="nope")) == {"invalid_target_gpo_guid"}


@pytest.mark.parametrize("mode", ["import_as_new", "copy", "copy_with_acl"])
def test_creating_modes_need_a_name_and_refuse_a_guid(mode: str) -> None:
    base = {"mode": mode, "target_identity": "windows_assigned"}
    assert _codes(_plan(**base, target_gpo_guid="", target_name="New")) == set()
    assert _codes(_plan(**base, target_gpo_guid="", target_name="")) == {"empty_target_name"}
    assert _codes(_plan(**base, target_name="New")) == {"target_guid_assigned_by_windows"}


def test_import_to_draft_needs_no_target() -> None:
    plan = _plan(mode="import_to_draft", target_identity="studio_draft", target_gpo_guid="")
    assert _codes(plan) == set()


def test_an_empty_backup_id_is_an_error() -> None:
    assert "empty_backup_id" in _codes(_plan(backup_id=""))


# ---------------------------------------------------------------------------
# generate_restore_plan
# ---------------------------------------------------------------------------


def test_restore_in_place_defaults_to_the_backups_gpo() -> None:
    plan = generate_restore_plan(_manifest(), "restore_in_place")
    assert plan.target_gpo_guid == _GUID
    assert plan.target_name == "Test Policy"
    assert plan.target_identity == "source"
    assert plan.cmdlet == cmdlet_for("restore_in_place")
    assert plan.domain == "studio.local"


def test_restore_in_place_into_another_gpo_is_refused() -> None:
    with pytest.raises(ValidationError):
        generate_restore_plan(
            _manifest(), "restore_in_place",
            target_gpo_guid="{99999999-8888-7777-6666-555555555555}",
        )


@pytest.mark.parametrize("mode", ["import_as_new", "copy", "copy_with_acl"])
def test_creating_modes_never_invent_a_target_guid(mode: RestoreMode) -> None:
    plan = generate_restore_plan(_manifest(), mode, target_name="New")
    assert plan.target_gpo_guid == ""
    assert plan.target_identity == "windows_assigned"


@pytest.mark.parametrize("mode", ["import_as_new", "copy", "copy_with_acl"])
def test_creating_modes_without_a_name_raise(mode: RestoreMode) -> None:
    with pytest.raises(ValidationError):
        generate_restore_plan(_manifest(), mode)


def test_import_into_existing_carries_the_callers_target() -> None:
    target = "{99999999-8888-7777-6666-555555555555}"
    plan = generate_restore_plan(_manifest(), "import_into_existing", target_gpo_guid=target)
    assert plan.target_gpo_guid == target
    assert plan.target_identity == "existing_target"


def test_import_to_draft_defaults_its_name_and_carries_no_scope() -> None:
    plan = generate_restore_plan(_manifest(), "import_to_draft")
    assert plan.target_name == "Test Policy"
    assert plan.target_gpo_guid == ""
    assert plan.scope == ()
    assert plan.warnings == ()


@pytest.mark.parametrize("operation", WINDOWS_OPERATIONS)
def test_each_plan_carries_its_operations_survival_row(operation: WindowsOperation) -> None:
    kwargs: dict[str, str] = {"target_name": "T"}
    if operation == "import_into_existing":
        kwargs = {"target_gpo_guid": "{99999999-8888-7777-6666-555555555555}"}
    if operation == "restore_in_place":
        kwargs = {}
    plan = generate_restore_plan(_manifest(), operation, **kwargs)
    assert {p.dimension: p.survival for p in plan.scope} == dict(SCOPE_SURVIVAL[operation])
    for prediction in plan.scope:
        rescoped = prediction.dimension not in ("settings", "gpo_guid")
        if rescoped and prediction.survival in ("lost", "defaulted"):
            assert any(w.startswith(prediction.dimension) for w in plan.warnings)


def test_a_wmi_link_is_flagged_as_unchecked_not_as_a_conflict() -> None:
    """The old plan reported 'not found in target domain' with no data to know.

    Now: if the operation is predicted to carry the link, the plan says the
    filter's existence was not checked. Nothing claims it is missing.
    """
    manifest = _manifest(has_wmi_filter=True, wmi_filter_reference="[d;{F};0]")
    plan = generate_restore_plan(manifest, "restore_in_place")
    assert any("not checked" in w for w in plan.warnings)
    assert not any("not found" in w for w in plan.warnings)
    no_filter = generate_restore_plan(_manifest(), "restore_in_place")
    assert not any("WMI" in w for w in no_filter.warnings)
    # import_as_new is predicted to lose the link, so existence is moot.
    imported = generate_restore_plan(manifest, "import_as_new", target_name="T")
    assert not any("not checked" in w for w in imported.warnings)


def test_a_cross_domain_plan_is_refused_by_the_ruling() -> None:
    with pytest.raises(ValidationError) as excinfo:
        generate_restore_plan(_manifest(), "import_as_new", target_name="T",
                              target_domain="other.local")
    assert excinfo.value.issues[0].code == "cross_domain_out_of_scope"
    same = generate_restore_plan(
        _manifest(), "import_as_new", target_name="T", target_domain="STUDIO.LOCAL"
    )
    assert same.domain == "STUDIO.LOCAL"


def test_the_index_matches_guids_regardless_of_braces_and_case() -> None:
    index = BackupIndex(backups=(_manifest(),))
    assert index.get_backup("aaaaaaaa-0000-0000-0000-000000000001") is not None
    assert index.backups_for_gpo(_GUID.strip("{}").upper()) != ()
