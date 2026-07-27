"""Plan 033 WP-1A: SYSVOL-injection diagnostic tests.

These tests exercise synthetic GPP XML that was hand-written and injected
directly into SYSVOL, then collected by ``Backup-GPO``.  GPMC did NOT
author these preference items — Backup.xml classifies them as "Unknown
Extension" and gpreport.xml contains no GPP ExtensionData.

They diagnose Studio parser behavior against plausible GPP shapes but
do NOT prove GPMC emits those shapes.  Genuine GPMC-authored fixtures
are in tests/fixtures/native-gpp-gpmc/.

Tests marked ``xfail(strict=True)`` assert the *desired* Windows-faithful
behavior.  They fail today because of known parser/model gaps and will
flip to passing when the gaps are resolved.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from gpo_studio.gpp import parse_gpp_collection
from gpo_studio.gpp_adapters import parse_gpp_drives, parse_gpp_local_groups

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "sysvol-injection-diagnostics"
SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent
    / "docs" / "plan-033" / "semantic-manifest-v1.schema.json"
)


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def _read_fixture_gpp(fixture_id: str, relative: str) -> bytes:
    base = FIXTURE_ROOT / fixture_id
    matches = list(base.glob(f"*/DomainSysvol/GPO/{relative}"))
    assert len(matches) == 1, f"Expected exactly one match for {relative} in {base}"
    return matches[0].read_bytes()


class TestDiagDriveMaps:
    """Drive Maps diagnostic — user scope, three items (synthetic SYSVOL injection)."""

    def test_parse_drives_xml_directly(self) -> None:
        data = _read_fixture_gpp("diag-drives", "User/Preferences/Drives/Drives.xml")
        drives = parse_gpp_drives(data)
        assert len(drives) == 3

        m_drive = drives[0]
        assert m_drive.letter == "M"
        assert m_drive.path == "\\\\filesrv\\home"
        assert m_drive.label == "Home Drive"
        assert m_drive.persistent is True
        assert m_drive.use_letter is True
        assert m_drive.action == "update"
        assert m_drive.common.apply_once is True
        assert m_drive.common.remove_when_unapplied is True
        assert m_drive.common.user_security_context is True
        assert m_drive.common.stop_on_error is True
        assert m_drive.ilt_filter is None

        p_drive = drives[1]
        assert p_drive.letter == "P"
        assert p_drive.action == "replace"
        assert p_drive.persistent is False

        x_drive = drives[2]
        assert x_drive.letter == "X"
        assert x_drive.action == "remove"

    def test_parse_via_collection(self) -> None:
        data = _read_fixture_gpp("diag-drives", "User/Preferences/Drives/Drives.xml")
        collection = parse_gpp_collection("user", {"Drives/Drives.xml": data})
        assert len(collection.drives) == 3

    def test_import_through_public_path(self) -> None:
        from gpo_studio.backup import read_backup
        from gpo_studio.import_export import collect_gpp_collections

        backup_dir = FIXTURE_ROOT / "diag-drives"
        backup = read_backup(backup_dir)
        content_root = backup.gpos[0].content_root
        assert content_root is not None
        collections = collect_gpp_collections(content_root)
        all_drives = [d for c in collections for d in c.drives]
        assert len(all_drives) == 3


class TestDiagLocalGroups:
    """Local Groups diagnostic — computer scope, two items (synthetic SYSVOL injection)."""

    def test_parse_groups_xml_directly(self) -> None:
        data = _read_fixture_gpp("diag-groups", "Machine/Preferences/Groups/Groups.xml")
        groups = parse_gpp_local_groups(data)
        assert len(groups) == 2

        admins = groups[0]
        assert admins.group_name == "Administrators (built-in)"
        assert admins.description == "Managed by GPO Studio"
        assert admins.action == "update"
        assert admins.delete_all_users is False
        assert admins.delete_all_groups is False
        assert len(admins.members) == 2
        assert admins.members[0].name == "HRAENET\\svc-gpolens"
        assert admins.members[0].action == "add"
        assert admins.members[1].name == "HRAENET\\lab-admins"

        power_users = groups[1]
        assert power_users.group_name == "Power Users (built-in)"
        assert power_users.action == "replace"
        assert power_users.delete_all_users is True
        assert power_users.delete_all_groups is True
        assert power_users.common.remove_when_unapplied is True
        assert power_users.common.stop_on_error is True
        assert len(power_users.members) == 1

    def test_collection_produces_groups_via_canonical_path(self) -> None:
        data = _read_fixture_gpp("diag-groups", "Machine/Preferences/Groups/Groups.xml")
        collection = parse_gpp_collection("computer", {"Groups/Groups.xml": data})
        assert len(collection.groups) == 2
        assert collection.groups[0].name == "Administrators (built-in)"
        assert len(collection.groups[0].members) == 2
        assert collection.local_groups == ()

    def test_import_through_public_path(self) -> None:
        from gpo_studio.backup import read_backup
        from gpo_studio.import_export import collect_gpp_collections

        backup_dir = FIXTURE_ROOT / "diag-groups"
        backup = read_backup(backup_dir)
        content_root = backup.gpos[0].content_root
        assert content_root is not None
        collections = collect_gpp_collections(content_root)
        all_groups = [g for c in collections for g in c.groups]
        assert len(all_groups) == 2


class TestDiagScheduledTasks:
    """Scheduled Tasks diagnostic — computer scope (synthetic SYSVOL injection)."""

    def test_parse_scheduled_tasks_xml_directly(self) -> None:
        from gpo_studio.gpp_adapters import parse_gpp_scheduled_tasks

        data = _read_fixture_gpp(
            "diag-scheduledtasks",
            "Machine/Preferences/ScheduledTasks/ScheduledTasks.xml",
        )
        tasks = parse_gpp_scheduled_tasks(data)
        assert len(tasks) == 1
        assert tasks[0].name == "GpoStudio-Cleanup"

    def test_immediate_task_preserves_command(self) -> None:
        from gpo_studio.gpp_adapters import parse_gpp_immediate_tasks

        data = _read_fixture_gpp(
            "diag-scheduledtasks",
            "Machine/Preferences/ScheduledTasks/ScheduledTasks.xml",
        )
        tasks = parse_gpp_immediate_tasks(data)
        assert len(tasks) == 1
        assert tasks[0].name == "GpoStudio-Init"
        assert tasks[0].run_as == "NT AUTHORITY\\SYSTEM"
        assert tasks[0].program == "C:\\Windows\\System32\\cmd.exe"
        assert tasks[0].arguments == "/c echo init"

    def test_import_through_public_path(self) -> None:
        from gpo_studio.backup import read_backup
        from gpo_studio.import_export import collect_gpp_collections

        backup_dir = FIXTURE_ROOT / "diag-scheduledtasks"
        backup = read_backup(backup_dir)
        content_root = backup.gpos[0].content_root
        assert content_root is not None
        collections = collect_gpp_collections(content_root)
        all_tasks = [t for c in collections for t in c.scheduled_tasks]
        all_imm = [t for c in collections for t in c.immediate_tasks]
        assert len(all_tasks) == 1
        assert len(all_imm) == 1


class TestDiagBackupLayout:
    """Document the native GPMC backup layout (useful WP-2 container specimens)."""

    def test_native_layout_structure(self) -> None:
        for fixture_id in ("diag-drives", "diag-groups", "diag-scheduledtasks"):
            base = FIXTURE_ROOT / fixture_id
            assert (base / "manifest.xml").exists(), f"{fixture_id}: missing manifest.xml"
            backup_dirs = [d for d in base.iterdir() if d.is_dir() and d.name.startswith("{")]
            assert len(backup_dirs) == 1, f"{fixture_id}: expected one backup-ID dir"
            backup_dir = backup_dirs[0]
            assert (backup_dir / "Backup.xml").exists()
            assert (backup_dir / "bkupInfo.xml").exists()
            assert (backup_dir / "gpreport.xml").exists()
            assert (backup_dir / "DomainSysvol" / "GPO").is_dir()

    def test_read_backup_accepts_native_layout(self) -> None:
        from gpo_studio.backup import read_backup

        backup_dir = FIXTURE_ROOT / "diag-drives"
        result = read_backup(backup_dir)
        assert len(result.gpos) == 1
        assert result.gpos[0].content_root is not None


SEMANTIC_MANIFEST_FIXTURES = ("diag-drives", "diag-groups", "diag-scheduledtasks")


class TestSemanticManifests:
    """Validate semantic-manifest.json files against the v1 schema contract."""

    def test_schema_is_valid_draft_2020_12(self) -> None:
        schema = _load_schema()
        jsonschema.Draft202012Validator.check_schema(schema)

    @pytest.mark.parametrize("fixture_id", SEMANTIC_MANIFEST_FIXTURES)
    def test_manifest_loads_and_validates(self, fixture_id: str) -> None:
        path = FIXTURE_ROOT / fixture_id / "semantic-manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))

        schema = _load_schema()
        jsonschema.validate(instance=data, schema=schema)

        assert data["classification"] == "synthetic-sysvol-injection"

    @pytest.mark.parametrize("fixture_id", SEMANTIC_MANIFEST_FIXTURES)
    def test_manifest_fixture_id_matches_directory(self, fixture_id: str) -> None:
        path = FIXTURE_ROOT / fixture_id / "semantic-manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["fixture_id"] == fixture_id
