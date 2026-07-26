"""Tests for the GPO lifecycle, backup/restore, and migration table model.

Plan 028 WP-1.
"""

from __future__ import annotations

import re

import pytest

from gpo_studio.lifecycle import (
    BackupFileEntry,
    BackupIndex,
    BackupManifest,
    LifecycleTransition,
    MigrationEntry,
    MigrationTable,
    RestorePlan,
    RestoreStep,
    apply_migration_table,
    generate_restore_plan,
    transition_lifecycle,
    valid_lifecycle_transitions,
)
from gpo_studio.model import ValidationError

_GUID = "{11111111-2222-3333-4444-555555555555}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _file(
    relative_path: str = "DomainSysvol/GPO/Machine/Registry.pol",
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
        "backup_id": "backup-001",
        "gpo_guid": _GUID,
        "gpo_display_name": "Test Policy",
        "domain": "studio.local",
        "created_at": "2026-01-01T00:00:00Z",
        "files": (_file(),),
    }
    fields.update(overrides)
    return BackupManifest(**fields)  # type: ignore[arg-type]


def _entry(
    source: str = "S-1-5-32-544",
    target: str = "S-1-12-544",
    *,
    is_resolved: bool = True,
    entry_type: str = "group",
) -> MigrationEntry:
    return MigrationEntry(
        source_principal=source,
        target_principal=target,
        entry_type=entry_type,  # type: ignore[arg-type]
        is_resolved=is_resolved,
    )


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
# RestorePlan
# ---------------------------------------------------------------------------


def test_restore_plan_valid_overwrite() -> None:
    plan = RestorePlan(
        backup_id="backup-001",
        mode="overwrite",
        target_gpo_guid=_GUID,
    )
    assert plan.validate() == ()


def test_restore_plan_valid_new_gpo() -> None:
    plan = RestorePlan(
        backup_id="backup-001",
        mode="new_gpo",
        target_name="Restored Policy",
    )
    assert plan.validate() == ()


def test_restore_plan_import_to_draft_needs_no_target() -> None:
    plan = RestorePlan(backup_id="backup-001", mode="import_to_draft")
    assert plan.validate() == ()


def test_restore_plan_overwrite_empty_target_is_error() -> None:
    plan = RestorePlan(backup_id="backup-001", mode="overwrite")
    issues = plan.validate()
    assert any(i.code == "empty_target_gpo_guid" and i.severity == "error" for i in issues)


def test_restore_plan_invalid_guid_error() -> None:
    plan = RestorePlan(
        backup_id="backup-001",
        mode="overwrite",
        target_gpo_guid="not-a-guid",
    )
    issues = plan.validate()
    assert any(i.code == "invalid_target_gpo_guid" and i.severity == "error" for i in issues)


def test_restore_plan_new_gpo_empty_name_is_error() -> None:
    plan = RestorePlan(backup_id="backup-001", mode="new_gpo")
    issues = plan.validate()
    assert any(i.code == "empty_target_name" and i.severity == "error" for i in issues)


def test_restore_plan_empty_backup_id_is_error() -> None:
    plan = RestorePlan(backup_id="", mode="import_to_draft")
    issues = plan.validate()
    assert any(i.code == "empty_backup_id" and i.severity == "error" for i in issues)


def test_restore_plan_conflicts_produce_warning() -> None:
    plan = RestorePlan(
        backup_id="backup-001",
        mode="overwrite",
        target_gpo_guid=_GUID,
        conflicts=("WMI filter 'X' not found in target domain",),
    )
    issues = plan.validate()
    conflict_issues = [i for i in issues if i.code == "restore_conflicts"]
    assert len(conflict_issues) == 1
    assert conflict_issues[0].severity == "warning"
    # The conflict warning does not block the plan.
    assert not any(i.severity == "error" for i in issues)


# ---------------------------------------------------------------------------
# generate_restore_plan
# ---------------------------------------------------------------------------


def test_generate_restore_plan_overwrite() -> None:
    manifest = _manifest()
    plan = generate_restore_plan(
        manifest,
        mode="overwrite",
        target_gpo_guid=_GUID,
        target_name="Overwritten Policy",
    )
    assert plan.backup_id == manifest.backup_id
    assert plan.mode == "overwrite"
    assert plan.target_gpo_guid == _GUID
    assert plan.target_name == "Overwritten Policy"


def test_generate_restore_plan_new_gpo_generates_guid() -> None:
    manifest = _manifest()
    plan = generate_restore_plan(
        manifest,
        mode="new_gpo",
        target_name="Restored Policy",
    )
    assert plan.mode == "new_gpo"
    assert plan.target_name == "Restored Policy"
    # A fresh GUID is generated and is a valid GUID format.
    assert re.fullmatch(
        r"\{?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
        r"[0-9A-Fa-f]{12}\}?",
        plan.target_gpo_guid,
    )


def test_generate_restore_plan_import_to_draft_defaults_name() -> None:
    manifest = _manifest(gpo_display_name="Studio Draft")
    plan = generate_restore_plan(manifest, mode="import_to_draft")
    assert plan.mode == "import_to_draft"
    assert plan.target_gpo_guid == ""
    assert plan.target_name == "Studio Draft"


def test_generate_restore_plan_overwrite_without_target_raises() -> None:
    manifest = _manifest()
    with pytest.raises(ValidationError) as excinfo:
        generate_restore_plan(manifest, mode="overwrite")
    assert any(i.code == "empty_target_gpo_guid" for i in excinfo.value.issues)


def test_generate_restore_plan_new_gpo_without_name_raises() -> None:
    manifest = _manifest()
    with pytest.raises(ValidationError) as excinfo:
        generate_restore_plan(manifest, mode="new_gpo")
    assert any(i.code == "empty_target_name" for i in excinfo.value.issues)


def test_generate_restore_plan_flags_wmi_filter_conflict() -> None:
    manifest = _manifest(has_wmi_filter=True, wmi_filter_name="WorkstationsOnly")
    plan = generate_restore_plan(manifest, mode="import_to_draft")
    assert any("WorkstationsOnly" in c for c in plan.conflicts)


# ---------------------------------------------------------------------------
# Lifecycle state machine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("draft", ("ready", "deleted")),
        ("ready", ("approved", "draft", "deleted")),
        ("approved", ("published", "ready", "deleted")),
        ("published", ("archived", "draft", "deleted")),
        ("archived", ("draft", "deleted")),
        ("deleted", ()),
    ],
)
def test_valid_lifecycle_transitions(state: str, expected: tuple[str, ...]) -> None:
    assert valid_lifecycle_transitions(state) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("frm", "to"),
    [
        ("draft", "ready"),
        ("ready", "approved"),
        ("ready", "draft"),
        ("approved", "published"),
        ("approved", "ready"),
        ("published", "archived"),
        ("published", "draft"),
        ("archived", "draft"),
        ("draft", "deleted"),
        ("published", "deleted"),
    ],
)
def test_transition_lifecycle_valid(frm: str, to: str) -> None:
    transition = transition_lifecycle(frm, to, "admin", "approved change")  # type: ignore[arg-type]
    assert transition.from_state == frm
    assert transition.to_state == to
    assert transition.actor == "admin"
    assert transition.reason == "approved change"
    assert transition.timestamp


@pytest.mark.parametrize(
    ("frm", "to"),
    [
        ("draft", "published"),
        ("draft", "approved"),
        ("ready", "published"),
        ("approved", "archived"),
        ("published", "ready"),
        ("archived", "published"),
        ("deleted", "draft"),
    ],
)
def test_transition_lifecycle_invalid_raises(frm: str, to: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        transition_lifecycle(frm, to, "admin", "bad move")  # type: ignore[arg-type]
    assert excinfo.value.issues[0].code == "invalid_transition"


def test_transition_lifecycle_approved_to_published_requires_approval() -> None:
    transition = transition_lifecycle("approved", "published", "admin", "ship it")
    assert transition.requires_approval is True


def test_transition_lifecycle_other_transitions_do_not_require_approval() -> None:
    transition = transition_lifecycle("ready", "approved", "admin", "approve")
    assert transition.requires_approval is False


def test_lifecycle_transition_is_immutable() -> None:
    transition = transition_lifecycle("draft", "ready", "admin", "go")
    with pytest.raises(AttributeError):
        transition.actor = "other"  # type: ignore[misc]


def test_lifecycle_transition_is_frozen_dataclass() -> None:
    transition = LifecycleTransition(
        from_state="draft",
        to_state="ready",
        actor="admin",
        reason="go",
        timestamp="2026-01-01T00:00:00Z",
    )
    # Frozen dataclass: re-assignment fails with AttributeError.
    with pytest.raises(AttributeError):
        transition.from_state = "ready"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# MigrationTable
# ---------------------------------------------------------------------------


def test_migration_table_get_entry_found() -> None:
    table = MigrationTable(
        table_id="mt-1",
        entries=(_entry("S-1-5-32-544", "S-1-12-544"),),
    )
    entry = table.get_entry("S-1-5-32-544")
    assert entry is not None
    assert entry.target_principal == "S-1-12-544"


def test_migration_table_get_entry_missing_returns_none() -> None:
    table = MigrationTable(table_id="mt-1", entries=())
    assert table.get_entry("S-1-5-32-544") is None


def test_migration_table_get_entry_is_case_insensitive() -> None:
    table = MigrationTable(
        table_id="mt-1",
        entries=(_entry("CONTOSO\\Admins", "FABRIKAM\\Admins"),),
    )
    entry = table.get_entry("contoso\\admins")
    assert entry is not None
    assert entry.target_principal == "FABRIKAM\\Admins"


def test_migration_table_resolve_principal_with_mapping() -> None:
    table = MigrationTable(
        table_id="mt-1",
        entries=(_entry("S-1-5-32-544", "S-1-12-544"),),
    )
    assert table.resolve_principal("S-1-5-32-544") == "S-1-12-544"


def test_migration_table_resolve_principal_without_mapping_returns_source() -> None:
    table = MigrationTable(table_id="mt-1", entries=())
    assert table.resolve_principal("S-1-5-32-999") == "S-1-5-32-999"


def test_migration_table_resolve_principal_empty_target_returns_source() -> None:
    table = MigrationTable(
        table_id="mt-1",
        entries=(_entry("S-1-5-32-544", ""),),
    )
    assert table.resolve_principal("S-1-5-32-544") == "S-1-5-32-544"


def test_migration_table_duplicate_source_is_error() -> None:
    table = MigrationTable(
        table_id="mt-1",
        entries=(
            _entry("S-1-5-32-544", "S-1-12-544"),
            _entry("S-1-5-32-544", "S-1-12-545"),
        ),
    )
    issues = table.validate()
    assert any(i.code == "duplicate_source_principal" and i.severity == "error" for i in issues)


def test_migration_table_noop_mapping_is_warning() -> None:
    table = MigrationTable(
        table_id="mt-1",
        entries=(_entry("S-1-5-32-544", "S-1-5-32-544"),),
    )
    issues = table.validate()
    noop = [i for i in issues if i.code == "noop_mapping"]
    assert len(noop) == 1
    assert noop[0].severity == "warning"


def test_migration_table_unresolved_entry_is_warning() -> None:
    table = MigrationTable(
        table_id="mt-1",
        entries=(_entry("S-1-5-32-544", "S-1-12-544", is_resolved=False),),
    )
    issues = table.validate()
    unresolved = [i for i in issues if i.code == "unresolved_principal"]
    assert len(unresolved) == 1
    assert unresolved[0].severity == "warning"


def test_migration_table_fully_resolved_has_no_warnings() -> None:
    table = MigrationTable(
        table_id="mt-1",
        entries=(_entry("S-1-5-32-544", "S-1-12-544", is_resolved=True),),
    )
    assert table.validate() == ()


# ---------------------------------------------------------------------------
# apply_migration_table
# ---------------------------------------------------------------------------


def test_apply_migration_table_resolved_principals_no_warnings() -> None:
    manifest = _manifest()
    table = MigrationTable(
        table_id="mt-1",
        entries=(_entry("S-1-5-32-544", "S-1-12-544", is_resolved=True),),
    )
    assert apply_migration_table(manifest, table) == ()


def test_apply_migration_table_unresolved_principals_warn() -> None:
    manifest = _manifest()
    table = MigrationTable(
        table_id="mt-1",
        entries=(
            _entry("S-1-5-32-544", "S-1-12-544", is_resolved=True),
            _entry("S-1-5-32-545", "S-1-12-545", is_resolved=False),
        ),
    )
    warnings = apply_migration_table(manifest, table)
    assert len(warnings) == 1
    assert "S-1-5-32-545" in warnings[0]


def test_apply_migration_table_mismatched_table_id_warns() -> None:
    manifest = _manifest(migration_table_id="mt-expected")
    table = MigrationTable(
        table_id="mt-actual",
        entries=(_entry("S-1-5-32-544", "S-1-12-544", is_resolved=True),),
    )
    warnings = apply_migration_table(manifest, table)
    assert len(warnings) == 1
    assert "mt-expected" in warnings[0]
    assert "mt-actual" in warnings[0]


def test_apply_migration_table_empty_table_no_warnings() -> None:
    manifest = _manifest()
    table = MigrationTable(table_id="mt-1", entries=())
    assert apply_migration_table(manifest, table) == ()


# ---------------------------------------------------------------------------
# RestoreStep (smoke)
# ---------------------------------------------------------------------------


def test_restore_step_defaults() -> None:
    step = RestoreStep(step_id="s1", operation="create_gpo", status="pending")
    assert step.detail == ""
    assert step.status == "pending"
