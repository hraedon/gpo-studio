from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema
import pytest
from manifest_comparator import compare_fixture

from gpo_studio.gpp import parse_gpp_collection, parse_gpp_groups
from gpo_studio.gpp_adapters import parse_gpp_drives, parse_gpp_local_groups

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "native-gpp-gpmc"
SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent
    / "docs" / "plan-033" / "semantic-manifest-v1.schema.json"
)

ALL_FIXTURE_DIRS = [
    "WI01A-DriveMaps-GPMC",
    "WI01A-LocalGroups-GPMC",
    "WI01A-SchedTasks-GPMC",
    "WI01A-MixedCSE-GPMC",
]


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def _read_fixture_gpp(fixture_id: str, relative: str) -> bytes:
    base = FIXTURE_ROOT / fixture_id
    matches = list(base.glob(f"*/DomainSysvol/GPO/{relative}"))
    assert len(matches) == 1, f"Expected exactly one match for {relative} in {base}"
    return matches[0].read_bytes()


class TestGenuineDriveMaps:

    def test_parse_drives_xml_directly(self) -> None:
        data = _read_fixture_gpp(
            "WI01A-DriveMaps-GPMC", "User/Preferences/Drives/Drives.xml"
        )
        drives = parse_gpp_drives(data)
        assert len(drives) == 4

        m_drive = drives[0]
        assert m_drive.letter == "M"
        assert m_drive.path == "\\\\filesrv\\home"
        assert m_drive.label == "Home Drive"
        assert m_drive.persistent is True
        assert m_drive.use_letter is True
        assert m_drive.action == "update"
        assert m_drive.common.apply_once is True
        assert m_drive.common.remove_when_unapplied is False
        assert m_drive.common.user_security_context is True
        assert m_drive.common.stop_on_error is True
        assert m_drive.ilt_filter is not None
        assert len(m_drive.ilt_filter.items) == 1
        assert "FilterOs" in m_drive.ilt_filter.items[0]

        p_drive = drives[1]
        assert p_drive.letter == "P"
        assert p_drive.path == "\\\\filesrv\\projects"
        assert p_drive.label == "Projects"
        assert p_drive.action == "replace"
        assert p_drive.persistent is False
        assert p_drive.use_letter is True
        assert p_drive.common.remove_when_unapplied is True
        assert p_drive.common.stop_on_error is False

        x_drive = drives[2]
        assert x_drive.letter == "X"
        assert x_drive.action == "remove"
        assert x_drive.use_letter is True
        assert x_drive.common.stop_on_error is True

    def test_create_action_drive(self) -> None:
        data = _read_fixture_gpp(
            "WI01A-DriveMaps-GPMC", "User/Preferences/Drives/Drives.xml"
        )
        drives = parse_gpp_drives(data)
        h_drive = drives[3]
        assert h_drive.letter == "H"
        assert h_drive.action == "add"
        assert h_drive.path == "\\\\filesrv\\shäre-ünïcode"
        assert h_drive.label == 'Ünïcödé <"&> label'
        assert h_drive.persistent is False
        assert h_drive.use_letter is True
        assert h_drive.common.stop_on_error is False

    def test_unicode_drive_xml_entities(self) -> None:
        data = _read_fixture_gpp(
            "WI01A-DriveMaps-GPMC", "User/Preferences/Drives/Drives.xml"
        )
        drives = parse_gpp_drives(data)
        h_drive = drives[3]
        assert "ü" in h_drive.path
        assert "ä" in h_drive.path
        assert "<" in h_drive.label
        assert ">" in h_drive.label
        assert "&" in h_drive.label
        assert '"' in h_drive.label

    def test_drive_unknown_attrs_preserved(self) -> None:
        data = _read_fixture_gpp(
            "WI01A-DriveMaps-GPMC", "User/Preferences/Drives/Drives.xml"
        )
        drives = parse_gpp_drives(data)
        m_drive = drives[0]
        unknown_dict = dict(m_drive.unknown_attrs)
        assert unknown_dict["status"] == "M:"
        assert unknown_dict["image"] == "2"
        assert unknown_dict["changed"] == "2026-07-26 16:11:18"
        assert unknown_dict["uid"] == "{0C6877AF-4BF2-43F1-8615-851BCA128E87}"

    def test_drive_filter_os_ilt_after_run_once_promotion(self) -> None:
        data = _read_fixture_gpp(
            "WI01A-DriveMaps-GPMC", "User/Preferences/Drives/Drives.xml"
        )
        drives = parse_gpp_drives(data)
        m_drive = drives[0]
        assert m_drive.common.apply_once is True
        assert m_drive.ilt_filter is not None
        assert len(m_drive.ilt_filter.items) == 1
        assert "FilterOs" in m_drive.ilt_filter.items[0]
        assert 'version="WINTHRESHOLD"' in m_drive.ilt_filter.items[0]
        assert 'class="NT"' in m_drive.ilt_filter.items[0]

    def test_parse_via_collection(self) -> None:
        data = _read_fixture_gpp(
            "WI01A-DriveMaps-GPMC", "User/Preferences/Drives/Drives.xml"
        )
        collection = parse_gpp_collection("user", {"Drives/Drives.xml": data})
        assert len(collection.drives) == 4

    def test_gpmc_omits_false_common_options(self) -> None:
        data = _read_fixture_gpp(
            "WI01A-DriveMaps-GPMC", "User/Preferences/Drives/Drives.xml"
        )
        drives = parse_gpp_drives(data)
        m_drive = drives[0]
        assert m_drive.common.disabled is False
        assert m_drive.common.remove_when_unapplied is False

    def test_import_through_public_path(self) -> None:
        from gpo_studio.backup import read_backup
        from gpo_studio.import_export import collect_gpp_collections

        backup_dir = FIXTURE_ROOT / "WI01A-DriveMaps-GPMC"
        backup = read_backup(backup_dir)
        content_root = backup.gpos[0].content_root
        assert content_root is not None
        collections = collect_gpp_collections(content_root)
        all_drives = [d for c in collections for d in c.drives]
        assert len(all_drives) == 4


class TestGenuineLocalGroups:

    def test_parse_machine_groups_xml(self) -> None:
        data = _read_fixture_gpp(
            "WI01A-LocalGroups-GPMC", "Machine/Preferences/Groups/Groups.xml"
        )
        groups = parse_gpp_local_groups(data)
        assert len(groups) == 3

        admins = groups[0]
        assert admins.group_name == "Administrators (built-in)"
        assert admins.description == "Managed by GPO Studio"
        assert admins.action == "update"
        assert admins.delete_all_users is False
        assert admins.delete_all_groups is False
        assert len(admins.members) == 2
        assert admins.members[0].name == "HRAENET\\svc-gpolens"
        assert admins.members[0].action == "add"
        assert admins.members[0].sid == "S-1-5-21-0000000000-0000000000-0000000000-5124"
        assert admins.members[1].name == "HRAENET\\lab-admins"
        assert admins.members[1].sid == "S-1-5-21-0000000000-0000000000-0000000000-4674"

        power_users = groups[1]
        assert power_users.group_name == "Power Users (built-in)"
        assert power_users.action == "replace"
        assert power_users.delete_all_users is True
        assert power_users.delete_all_groups is True
        assert power_users.common.remove_when_unapplied is True
        assert power_users.common.stop_on_error is True
        assert len(power_users.members) == 1
        assert power_users.members[0].name == "HRAENET\\dev-team"

        delete_group = groups[2]
        assert delete_group.group_name == "test-delete-group"
        assert delete_group.action == "remove"

    def test_parse_user_groups_xml(self) -> None:
        data = _read_fixture_gpp(
            "WI01A-LocalGroups-GPMC", "User/Preferences/Groups/Groups.xml"
        )
        groups = parse_gpp_local_groups(data)
        assert len(groups) == 1
        dev_team = groups[0]
        assert dev_team.group_name == "dev-team"
        assert dev_team.action == "add"
        assert dev_team.delete_all_users is False
        assert dev_team.delete_all_groups is False
        assert len(dev_team.members) == 1
        assert dev_team.members[0].name == "HRAENET\\ünïcode-test-group"
        assert dev_team.members[0].sid == "S-1-5-21-0000000000-0000000000-0000000000-4675"

    def test_user_group_filter_group_ilt(self) -> None:
        data = _read_fixture_gpp(
            "WI01A-LocalGroups-GPMC", "User/Preferences/Groups/Groups.xml"
        )
        groups = parse_gpp_local_groups(data)
        dev_team = groups[0]
        assert dev_team.common.apply_once is True
        assert dev_team.ilt_filter is not None
        assert len(dev_team.ilt_filter.predicates) == 1
        pred = dev_team.ilt_filter.predicates[0]
        assert pred.type == "group"
        assert pred.value == "S-1-5-21-0000000000-0000000000-0000000000-513"
        pred_unknown = dict(pred.unknown_attrs)
        assert pred_unknown["userContext"] == "1"

    def test_gpmc_common_option_emission(self) -> None:
        data = _read_fixture_gpp(
            "WI01A-LocalGroups-GPMC", "Machine/Preferences/Groups/Groups.xml"
        )
        groups = parse_gpp_local_groups(data)
        admins = groups[0]
        assert admins.common.user_security_context is False
        assert admins.common.disabled is False
        assert admins.common.stop_on_error is True

    def test_group_unknown_attrs_preserved(self) -> None:
        data = _read_fixture_gpp(
            "WI01A-LocalGroups-GPMC", "Machine/Preferences/Groups/Groups.xml"
        )
        groups = parse_gpp_local_groups(data)
        admins = groups[0]
        unknown_dict = dict(admins.unknown_attrs)
        assert unknown_dict["image"] == "2"
        assert unknown_dict["changed"] == "2026-07-26 16:17:00"
        assert unknown_dict["uid"] == "{A582B44E-6757-4CA0-AE25-03D28428C3E9}"

    def test_group_sid_not_in_unknown_attrs(self) -> None:
        data = _read_fixture_gpp(
            "WI01A-LocalGroups-GPMC", "Machine/Preferences/Groups/Groups.xml"
        )
        groups = parse_gpp_local_groups(data)
        admins = groups[0]
        unknown_dict = dict(admins.unknown_attrs)
        assert "groupSid" not in unknown_dict
        assert "groupName" not in unknown_dict
        assert admins.group_name == "Administrators (built-in)"

    def test_collection_produces_groups_via_canonical_path(self) -> None:
        data = _read_fixture_gpp(
            "WI01A-LocalGroups-GPMC", "Machine/Preferences/Groups/Groups.xml"
        )
        collection = parse_gpp_collection("computer", {"Groups/Groups.xml": data})
        assert len(collection.groups) == 3
        admins = collection.groups[0]
        assert admins.name == "Administrators (built-in)"
        assert admins.description == "Managed by GPO Studio"
        assert admins.remove_all_users is False
        assert admins.remove_all_groups is False
        assert len(admins.members) == 2
        assert admins.members[0].name == "HRAENET\\svc-gpolens"
        assert admins.members[0].action == "add"
        assert admins.members[0].sid == "S-1-5-21-0000000000-0000000000-0000000000-5124"
        assert admins.members[1].name == "HRAENET\\lab-admins"
        assert admins.members[1].sid == "S-1-5-21-0000000000-0000000000-0000000000-4674"
        assert admins.common.stop_on_error is True
        assert admins.common.user_security_context is False
        assert collection.local_groups == ()

    def test_import_through_public_path(self) -> None:
        from gpo_studio.backup import read_backup
        from gpo_studio.import_export import collect_gpp_collections

        backup_dir = FIXTURE_ROOT / "WI01A-LocalGroups-GPMC"
        backup = read_backup(backup_dir)
        content_root = backup.gpos[0].content_root
        assert content_root is not None
        collections = collect_gpp_collections(content_root)
        all_groups = [g for c in collections for g in c.groups]
        assert len(all_groups) == 4

    def test_group_sid_populated_from_native(self) -> None:
        data = _read_fixture_gpp(
            "WI01A-LocalGroups-GPMC", "Machine/Preferences/Groups/Groups.xml"
        )
        groups = parse_gpp_groups(data)
        assert groups[0].sid == "S-1-5-32-544"
        assert groups[1].sid == "S-1-5-32-547"
        assert groups[2].sid == ""

    def test_native_props_attrs_captured_in_unknown(self) -> None:
        data = _read_fixture_gpp(
            "WI01A-LocalGroups-GPMC", "Machine/Preferences/Groups/Groups.xml"
        )
        groups = parse_gpp_groups(data)
        admins_props = dict(groups[0].unknown_props_attrs)
        assert admins_props["newName"] == ""
        assert admins_props["removeAccounts"] == "0"

    def test_user_action_captured_in_unknown(self) -> None:
        data = _read_fixture_gpp(
            "WI01A-LocalGroups-GPMC", "User/Preferences/Groups/Groups.xml"
        )
        groups = parse_gpp_groups(data)
        dev_team_props = dict(groups[0].unknown_props_attrs)
        assert dev_team_props["userAction"] == "REMOVE"
        assert dev_team_props["removeAccounts"] == "0"


class TestGenuineScheduledTasks:

    def test_parse_scheduled_tasks_xml_directly(self) -> None:
        from gpo_studio.gpp_adapters import parse_gpp_scheduled_tasks

        data = _read_fixture_gpp(
            "WI01A-SchedTasks-GPMC",
            "Machine/Preferences/ScheduledTasks/ScheduledTasks.xml",
        )
        tasks = parse_gpp_scheduled_tasks(data)
        assert len(tasks) == 2
        task = tasks[0]
        assert task.name == "GpoStudio-Cleanup"
        assert task.run_as == "NT AUTHORITY\\System"
        assert task.action == "update"
        assert task.element_variant == "TaskV2"

    def test_immediate_task_preserves_command(self) -> None:
        from gpo_studio.gpp_adapters import parse_gpp_immediate_tasks

        data = _read_fixture_gpp(
            "WI01A-SchedTasks-GPMC",
            "Machine/Preferences/ScheduledTasks/ScheduledTasks.xml",
        )
        tasks = parse_gpp_immediate_tasks(data)
        assert len(tasks) == 1
        task = tasks[0]
        assert task.name == "GpoStudio-Init"
        assert task.run_as == "NT AUTHORITY\\System"
        assert task.program == "C:\\Windows\\System32\\cmd.exe"
        assert task.arguments == "/c echo init"

    def test_immediate_task_found_by_element_name(self) -> None:
        from gpo_studio.gpp_adapters import parse_gpp_immediate_tasks

        data = _read_fixture_gpp(
            "WI01A-SchedTasks-GPMC",
            "Machine/Preferences/ScheduledTasks/ScheduledTasks.xml",
        )
        tasks = parse_gpp_immediate_tasks(data)
        assert len(tasks) == 1
        assert tasks[0].name == "GpoStudio-Init"
        assert tasks[0].action == "add"
        assert tasks[0].common.stop_on_error is True

    def test_import_through_public_path(self) -> None:
        from gpo_studio.backup import read_backup
        from gpo_studio.import_export import collect_gpp_collections

        backup_dir = FIXTURE_ROOT / "WI01A-SchedTasks-GPMC"
        backup = read_backup(backup_dir)
        content_root = backup.gpos[0].content_root
        assert content_root is not None
        collections = collect_gpp_collections(content_root)
        all_tasks = [t for c in collections for t in c.scheduled_tasks]
        all_imm = [t for c in collections for t in c.immediate_tasks]
        assert len(all_tasks) == 4
        assert len(all_imm) == 1


class TestGenuineMixedCSE:

    def test_parse_drives_from_mixed(self) -> None:
        data = _read_fixture_gpp(
            "WI01A-MixedCSE-GPMC", "User/Preferences/Drives/Drives.xml"
        )
        drives = parse_gpp_drives(data)
        assert len(drives) == 1
        x_drive = drives[0]
        assert x_drive.letter == "X"
        assert x_drive.action == "replace"
        assert x_drive.path == "\\\\filesrv\\replace-mixed"
        assert x_drive.label == 'Ünïcödé <"&> label'
        assert x_drive.persistent is True
        assert x_drive.use_letter is True

    def test_parse_groups_from_mixed(self) -> None:
        data = _read_fixture_gpp(
            "WI01A-MixedCSE-GPMC", "Machine/Preferences/Groups/Groups.xml"
        )
        groups = parse_gpp_local_groups(data)
        assert len(groups) == 1
        admins = groups[0]
        assert admins.group_name == "Administrators (built-in)"
        assert admins.action == "update"
        assert admins.delete_all_users is False
        assert admins.delete_all_groups is False
        assert len(admins.members) == 1
        assert admins.members[0].name == "HRAENET\\lab-admins"
        assert admins.members[0].sid == "S-1-5-21-0000000000-0000000000-0000000000-4674"

    def test_parse_tasks_from_mixed(self) -> None:
        from gpo_studio.gpp_adapters import parse_gpp_scheduled_tasks

        data = _read_fixture_gpp(
            "WI01A-MixedCSE-GPMC",
            "Machine/Preferences/ScheduledTasks/ScheduledTasks.xml",
        )
        tasks = parse_gpp_scheduled_tasks(data)
        assert len(tasks) == 1
        assert tasks[0].name == "Mixed Task"
        assert tasks[0].element_variant == "TaskV2"

    def test_immediate_task_not_in_mixed(self) -> None:
        from gpo_studio.gpp_adapters import parse_gpp_immediate_tasks

        data = _read_fixture_gpp(
            "WI01A-MixedCSE-GPMC",
            "Machine/Preferences/ScheduledTasks/ScheduledTasks.xml",
        )
        tasks = parse_gpp_immediate_tasks(data)
        assert len(tasks) == 0


class TestGenuineBackupLayout:

    @pytest.mark.parametrize("fixture_id", ALL_FIXTURE_DIRS)
    def test_native_layout_structure(self, fixture_id: str) -> None:
        base = FIXTURE_ROOT / fixture_id
        assert (base / "manifest.xml").exists(), f"{fixture_id}: missing manifest.xml"
        backup_dirs = [d for d in base.iterdir() if d.is_dir() and d.name.startswith("{")]
        assert len(backup_dirs) == 1, f"{fixture_id}: expected one backup-ID dir"
        backup_dir = backup_dirs[0]
        assert (backup_dir / "Backup.xml").exists()
        assert (backup_dir / "bkupInfo.xml").exists()
        assert (backup_dir / "gpreport.xml").exists()
        assert (backup_dir / "DomainSysvol" / "GPO").is_dir()


class TestSemanticManifests:

    def test_schema_is_valid_draft_2020_12(self) -> None:
        schema = _load_schema()
        jsonschema.Draft202012Validator.check_schema(schema)

    @pytest.mark.parametrize("fixture_id", ALL_FIXTURE_DIRS)
    def test_manifest_loads_and_validates(self, fixture_id: str) -> None:
        path = FIXTURE_ROOT / fixture_id / "semantic-manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))

        schema = _load_schema()
        jsonschema.validate(instance=data, schema=schema)

        assert data["classification"] == "native-gpmc-authored"

    @pytest.mark.parametrize("fixture_id", ALL_FIXTURE_DIRS)
    def test_manifest_fixture_id_matches_directory(self, fixture_id: str) -> None:
        path = FIXTURE_ROOT / fixture_id / "semantic-manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        expected = fixture_id.lower()
        actual = manifest["fixture_id"]
        assert actual == expected, (
            f"fixture_id {actual!r} does not match normalized directory name {expected!r}"
        )

    @pytest.mark.parametrize("fixture_id", ALL_FIXTURE_DIRS)
    def test_manifest_origin_fields_present(self, fixture_id: str) -> None:
        path = FIXTURE_ROOT / fixture_id / "semantic-manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        origin = manifest["origin"]
        assert origin["domain"] == "ad.hraedon.com"
        assert origin["method"].startswith("Backup-GPO")
        assert origin["captured_at"].endswith("Z")
        assert origin["backup_id"].startswith("{")
        assert origin["gpo_guid"].startswith("{")

    @pytest.mark.parametrize("fixture_id", ALL_FIXTURE_DIRS)
    def test_manifest_items_have_required_fields(self, fixture_id: str) -> None:
        path = FIXTURE_ROOT / fixture_id / "semantic-manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for item in manifest["items"]:
            assert item["uid"].startswith("{")
            assert item["windows_meaning"]
            assert item["element"]
            assert item["scope"] in ("computer", "user")
            assert item["action"] in ("U", "R", "D", "C")


class TestManifestDrivenComparison:

    @pytest.mark.parametrize("fixture_id", ALL_FIXTURE_DIRS)
    def test_manifest_vs_parser(self, fixture_id: str) -> None:
        result = compare_fixture(FIXTURE_ROOT / fixture_id)
        assert result.ok, f"Mismatches: {result.mismatches}"


class TestSanitizationIntegrity:

    def _load_record(self) -> dict:
        path = FIXTURE_ROOT / "sanitization-record.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_sanitized_hashes_match_disk(self) -> None:
        record = self._load_record()
        for entry in record["files"]:
            file_path = FIXTURE_ROOT / entry["relative_path"]
            assert file_path.exists(), f"Missing: {entry['relative_path']}"
            actual = hashlib.sha256(file_path.read_bytes()).hexdigest()
            assert actual == entry["sanitized_sha256"], (
                f"Hash mismatch for {entry['relative_path']}: "
                f"expected {entry['sanitized_sha256']}, got {actual}"
            )

    def test_record_covers_all_fixture_files(self) -> None:
        record = self._load_record()
        tracked = {entry["relative_path"] for entry in record["files"]}
        exempt = {"semantic-manifest.json", "sanitization-record.json"}
        all_files = {
            str(p.relative_to(FIXTURE_ROOT))
            for p in FIXTURE_ROOT.rglob("*")
            if p.is_file() and p.name not in exempt
        }
        untracked = all_files - tracked
        assert not untracked, f"Untracked files: {sorted(untracked)}"


class TestDiscoveryRegistry:

    def test_all_fixture_gpp_files_discovered(self) -> None:
        from gpo_studio.backup import read_backup
        from gpo_studio.import_export import collect_gpp_collections

        expected: dict[str, dict[str, int]] = {
            "WI01A-DriveMaps-GPMC": {"drives": 4},
            "WI01A-LocalGroups-GPMC": {"groups": 4},
            "WI01A-MixedCSE-GPMC": {"drives": 1, "groups": 1},
        }
        for fixture_id, counts in expected.items():
            backup_dir = FIXTURE_ROOT / fixture_id
            backup = read_backup(backup_dir)
            content_root = backup.gpos[0].content_root
            assert content_root is not None
            collections = collect_gpp_collections(content_root)
            for field_name, expected_count in counts.items():
                actual = sum(
                    len(getattr(c, field_name)) for c in collections
                )
                assert actual == expected_count, (
                    f"{fixture_id}: expected {expected_count} {field_name}, got {actual}"
                )

    def test_case_insensitive_resolution(self, tmp_path: Path) -> None:
        from gpo_studio.import_export import collect_gpp_collections

        drives_dir = tmp_path / "Machine" / "Preferences" / "drives"
        drives_dir.mkdir(parents=True)
        xml = (
            b'<?xml version="1.0" encoding="utf-8"?>'
            b'<Drives clsid="{8FDDCC1A-0C3C-43cd-A6B4-71A6DF20DA8C}">'
            b'<Drive clsid="{935D1B74-9CB8-4e3c-9914-7DD559B7A417}" name="Z:">'
            b'<Properties action="U" letter="Z" path="\\\\srv\\share" />'
            b"</Drive></Drives>"
        )
        (drives_dir / "drives.xml").write_bytes(xml)
        collections = collect_gpp_collections(tmp_path)
        assert len(collections) == 1
        assert len(collections[0].drives) == 1
        assert collections[0].drives[0].letter == "Z"

    def test_case_insensitive_ambiguity_rejected(self, tmp_path: Path) -> None:
        from gpo_studio.backup import BackupError
        from gpo_studio.import_export import _resolve_case_insensitive

        base = tmp_path / "base"
        (base / "DRIVES").mkdir(parents=True)
        (base / "drives").mkdir(parents=True)
        with pytest.raises(BackupError, match="Ambiguous case-insensitive"):
            _resolve_case_insensitive(base, "Drives/Drives.xml")
