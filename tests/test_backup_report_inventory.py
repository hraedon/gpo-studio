"""Public import/report fidelity against independently produced Windows captures."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
import xml.etree.ElementTree as ET
import zipfile
from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.backup import BackupError, read_backup
from gpo_studio.backup_inventory import inventory_from_dict
from gpo_studio.canonical import policy_semantic_sha256, review_model_sha256
from gpo_studio.export import export_bundle
from gpo_studio.model import StudioError
from gpo_studio.store import WorkspaceStore, gpo_from_dict

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/plan-033"
NATIVE = ROOT / "tests/fixtures/native-gpp-gpmc"
SETTINGS = "{http://www.microsoft.com/GroupPolicy/Settings}"
BKP = "{http://www.microsoft.com/GroupPolicy/GPOOperations}"

# Only Windows-produced backups: exclude Studio input/controller candidate trees.
BACKUPS = sorted(NATIVE.glob("*/manifest.xml")) + sorted(
    p for p in EVIDENCE.glob("*-evidence/wi059-20260908/**/manifest.xml")
    if "rebackup" in p.parts or "backup" in p.parts
) + [EVIDENCE / "wp1b-evidence/backup-report-20260908/scripts-metadata/rebackup/manifest.xml"]
SCRIPTS = EVIDENCE / "wp1b-evidence/wi059-20260908/scripts-metadata/rebackup"


@pytest.mark.parametrize("manifest", BACKUPS, ids=lambda p: p.parent.relative_to(ROOT).as_posix())
def test_native_inventory_survives_public_import_and_report(
    manifest: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inbox = tmp_path / "inbox"
    shutil.copytree(manifest.parent, inbox)
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(inbox))
    original = read_backup(inbox).gpos[0]
    assert original.content_root is not None
    source = original.content_root.parent.parent
    with closing(WorkspaceStore(tmp_path / "inventory.db")) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        with TestClient(app) as client:
            response = client.post("/api/backups/import", json={
                "path": str(inbox), "actor": "inventory-test", "reason": "native fidelity",
            })
            assert response.status_code == 201, response.text
            gpo = gpo_from_dict(response.json()["gpo"])
            inventory = gpo.backup_inventory
            assert inventory is not None
            assert base64.b64decode(inventory.backup_xml_base64) == (
                source / "Backup.xml"
            ).read_bytes()
            report_file = source / "gpreport.xml"
            expected_report = report_file.read_bytes() if report_file.exists() else b""
            assert base64.b64decode(inventory.report_xml_base64) == expected_report
            expected_files = {
                p.relative_to(original.content_root).as_posix(): (
                    hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_size,
                )
                for p in original.content_root.rglob("*") if p.is_file()
            }
            actual = {f.relative_path: (f.content_hash, f.size) for f in inventory.files}
            assert actual == expected_files
            report = client.get(f"/api/gpos/{gpo.guid}/report.txt")
            assert report.status_code == 200
            assert report.headers["content-type"].startswith("text/plain")
            assert "Imported source snapshot" in report.text
            assert "not stored here" in report.text
            for path, (digest, _) in expected_files.items():
                assert path in report.text and digest in report.text
            # Inspect native declarations independently of the application parser.
            native_backup = ET.fromstring((source / "Backup.xml").read_bytes())
            for elem in native_backup.iter():
                if (
                    elem.tag in (f"{BKP}MachineExtensionGuids", f"{BKP}UserExtensionGuids")
                    and elem.text and elem.text.strip()
                ):
                    assert elem.text.strip() in report.text
                if elem.tag == f"{BKP}GroupPolicyExtension":
                    assert elem.attrib[f"{BKP}ID"] in report.text
            if expected_report:
                native_report = ET.fromstring(expected_report)
                for scope in ("Computer", "User"):
                    for ext in native_report.findall(
                        f"{SETTINGS}{scope}/{SETTINGS}ExtensionData/{SETTINGS}Extension"
                    ):
                        for elem in ext.iter():
                            if elem.text and elem.text.strip():
                                assert elem.text.strip() in report.text
                            for value in elem.attrib.values():
                                assert value in report.text
            else:
                assert "Native setting inventory unavailable" in report.text
            # Forks keep the imported observation explicitly historical.
            fork = client.post(f"/api/gpos/{gpo.guid}/fork", json={
                "name": "Inventory fork", "actor": "inventory-test", "reason": "provenance",
            })
            assert fork.status_code == 201, fork.text
            assert gpo_from_dict(fork.json()["gpo"]).backup_inventory == inventory


def test_scripts_are_identified_without_claiming_payload_preservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inbox = tmp_path / "inbox"
    shutil.copytree(SCRIPTS, inbox)
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(inbox))
    database = tmp_path / "inventory.db"
    with closing(WorkspaceStore(database)) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        with TestClient(app) as client:
            result = client.post("/api/backups/import", json={
                "path": str(inbox), "actor": "test", "reason": "native Scripts inventory",
            })
            assert result.status_code == 201, result.text
            guid = result.json()["gpo"]["guid"]
    assert inbox.resolve().is_relative_to(tmp_path.resolve())
    inbox.rename(tmp_path / "source-no-longer-at-import-path")
    with closing(WorkspaceStore(database)) as reopened:
        monkeypatch.setattr(app.state, "store", reopened, raising=False)
        with TestClient(app) as client:
            report = client.get(f"/api/gpos/{guid}/report.txt")
            assert report.status_code == 200
            for name in ("zz-studio-marker.cmd", "zz-studio-second.cmd", "zz-studio-marker.ps1"):
                assert name in report.text
            assert "Scripts {42B5FAAE-6536-11d2-AE5A-0000F87571E3}" in report.text
            assert "Unmodeled extension files (metadata only)" in report.text
            assert "original bytes not stored" in report.text
            assert "Keep the original backup" in report.text
            assert client.get(f"/api/gpos/{guid}/gpmc-backup").status_code == 422
            gpo = reopened.get_gpo(guid)
            without = replace(gpo, backup_inventory=None)
            assert policy_semantic_sha256(gpo) == policy_semantic_sha256(without)
            assert review_model_sha256(gpo) != review_model_sha256(without)


def test_native_report_identity_mismatch_is_refused(tmp_path: Path) -> None:
    shutil.copytree(SCRIPTS, tmp_path / "backup")
    report = next((tmp_path / "backup").glob("*/gpreport.xml"))
    tree = ET.fromstring(report.read_bytes())
    identifier = tree.find(f"{SETTINGS}Identifier/{{http://www.microsoft.com/GroupPolicy/Types}}Identifier")
    assert identifier is not None
    identifier.text = "{11111111-2222-3333-4444-555555555555}"
    report.write_bytes(ET.tostring(tree, encoding="utf-8"))
    with pytest.raises(StudioError, match="identity"):
        read_backup(tmp_path / "backup")


@pytest.mark.parametrize("invalid", [None, {}, {"backup_xml_base64": "not base64"}])
def test_invalid_retained_inventory_is_refused(invalid: object) -> None:
    with pytest.raises(StudioError):
        inventory_from_dict(invalid)


def test_inventory_replay_has_real_windows_coverage() -> None:
    assert len(BACKUPS) == 27
    assert all(p.exists() for p in BACKUPS)


def test_native_report_counts_toward_the_backup_byte_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gpo_studio import backup

    shutil.copytree(SCRIPTS, tmp_path / "backup")
    report = next((tmp_path / "backup").glob("*/gpreport.xml"))
    report_bytes = report.read_bytes()
    report.unlink()
    # Exact limit sufficient for every other scanned byte. Restoring the report
    # must tip the import over the same limit, not merely hit another large file.
    total = sum(p.stat().st_size for p in (tmp_path / "backup").rglob("*") if p.is_file())
    monkeypatch.setattr(backup, "_MAX_TOTAL_BACKUP_BYTES", total)
    from gpo_studio.backup_inventory import inventory_report_lines

    without_report = read_backup(tmp_path / "backup").gpos[0].backup_inventory
    assert without_report is not None
    assert any("Native setting inventory unavailable" in line
               for line in inventory_report_lines(without_report))
    report.write_bytes(report_bytes)
    with pytest.raises(BackupError, match="Total backup size"):
        read_backup(tmp_path / "backup")


def test_bundle_manifest_retains_inventory_without_changing_policy_semantics() -> None:
    from gpo_studio.model import GPO

    source = read_backup(SCRIPTS).gpos[0]
    # No modeled policy here: the archived observations must never be emitted
    # as current Scripts instructions by the publication bundle.
    gpo = GPO(guid=source.guid, name="Source inventory", backup_inventory=source.backup_inventory)
    with zipfile.ZipFile(io.BytesIO(export_bundle(gpo))) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        restored = gpo_from_dict(manifest["gpo"])
        assert restored.backup_inventory == gpo.backup_inventory
        assert not any("scripts.ini" in name.casefold() for name in archive.namelist())
    assert source.backup_inventory is not None
    changed = replace(source.backup_inventory, report_xml_base64="")
    other = replace(gpo, backup_inventory=changed)
    assert policy_semantic_sha256(other) == policy_semantic_sha256(gpo)
    assert review_model_sha256(other) != review_model_sha256(gpo)


@pytest.mark.parametrize("field,value", [
    ("relative_path", "../outside"), ("relative_path", "Machine//file"),
    ("relative_path", "Machine/./file"), ("relative_path", "C:/file"),
    ("content_hash", "bad"), ("size", -1), ("size", True),
])
def test_inventory_rejects_malformed_file_metadata(field: str, value: object) -> None:
    inventory = read_backup(SCRIPTS).gpos[0].backup_inventory
    assert inventory is not None
    data = asdict(inventory)
    data["files"][0][field] = value
    with pytest.raises(StudioError, match="file metadata"):
        inventory_from_dict(data)


def test_inventory_includes_root_payloads_and_future_native_settings(tmp_path: Path) -> None:
    from gpo_studio.backup_inventory import inventory_report_lines

    shutil.copytree(SCRIPTS, tmp_path / "backup")
    report = next((tmp_path / "backup").glob("*/gpreport.xml"))
    extra = report.parent / "DomainSysvol/GPO/Adm/example.adm"
    extra.parent.mkdir(exist_ok=True)
    extra.write_bytes(b"synthetic legacy template")
    tree = ET.fromstring(report.read_bytes())
    extension = tree.find(f"{SETTINGS}Computer/{SETTINGS}ExtensionData/{SETTINGS}Extension")
    assert extension is not None
    item = ET.SubElement(extension, "{urn:synthetic:future}Future", {"Mode": "observe-only"})
    item.text = "before"
    ET.SubElement(item, "Child").tail = "after"
    report.write_bytes(ET.tostring(tree, encoding="utf-8"))
    inventory = read_backup(tmp_path / "backup").gpos[0].backup_inventory
    assert inventory is not None
    assert "Adm/example.adm" in {f.relative_path for f in inventory.files}
    lines = "\n".join(inventory_report_lines(inventory))
    for value in ("Future", "observe-only", "before", "after"):
        assert value in lines


@pytest.mark.parametrize("xml", [
    b"<broken>", b'<!DOCTYPE x [<!ENTITY e "value">]><x>&e;</x>',
    b'<x xmlns:q="urn:test" q:CPassword="synthetic"/>',
])
def test_retained_native_xml_uses_the_import_safety_boundaries(xml: bytes) -> None:
    inventory = read_backup(SCRIPTS).gpos[0].backup_inventory
    assert inventory is not None
    data = asdict(inventory)
    data["report_xml_base64"] = base64.b64encode(xml).decode()
    with pytest.raises(StudioError):
        inventory_from_dict(data)
