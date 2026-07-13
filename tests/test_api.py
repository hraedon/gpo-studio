from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.registry_pol import PolRecord, serialize
from gpo_studio.store import WorkspaceStore

_ADMX_MINIMAL = b"""<?xml version="1.0" encoding="utf-8"?>
<policyDefinitions xmlns="http://www.microsoft.com/GroupPolicy/PolicyDefinitions">
  <categories>
    <category name="SyntheticCategory" displayName="$(string.SyntheticCategory)">
      <parentCategory ref="ParentCat" />
    </category>
  </categories>
  <supportedOn>
    <definition name="Supported_Synthetic" displayName="$(string.Supported_Synthetic)" />
  </supportedOn>
  <policies>
    <policy name="SyntheticPolicy" class="Machine" key="Software\\Policies\\Synthetic"
            displayName="$(string.SyntheticPolicy)" explainText="$(string.SyntheticPolicy_Explain)"
            supportedOn="Supported_Synthetic" presentation="$(presentation.SyntheticPolicy)">
      <parentCategory ref="SyntheticCategory" />
      <supportedOn ref="Supported_Synthetic" />
      <elements>
        <boolean id="Enabled" key="Software\\Policies\\Synthetic" valueName="Enabled" />
      </elements>
      <presentation>
        <checkBox id="Enabled" refId="Enabled" label="$(string.EnableLabel)" />
      </presentation>
    </policy>
    <policy name="UserPolicy" class="User" key="Software\\Policies\\UserSynthetic"
            displayName="$(string.UserPolicy)" explainText="$(string.UserPolicy_Explain)"
            supportedOn="Supported_Synthetic" presentation="$(presentation.UserPolicy)">
      <parentCategory ref="SyntheticCategory" />
      <supportedOn ref="Supported_Synthetic" />
      <elements>
        <text id="UserSetting" key="Software\\Policies\\UserSynthetic" valueName="UserSetting" />
      </elements>
      <presentation>
        <textBox id="UserSetting" refId="UserSetting" label="$(string.UserSettingLabel)" />
      </presentation>
    </policy>
  </policies>
</policyDefinitions>"""

_ADML_MINIMAL = b"""<?xml version="1.0" encoding="utf-8"?>
<policyDefinitionResources xmlns="http://www.microsoft.com/GroupPolicy/PolicyDefinitions">
  <resources>
    <stringTable>
      <string id="SyntheticCategory">Synthetic Category</string>
      <string id="Supported_Synthetic">Synthetic OS Support</string>
      <string id="SyntheticPolicy">Synthetic Policy</string>
      <string id="SyntheticPolicy_Explain">This is a synthetic policy for testing.</string>
      <string id="UserPolicy">User Policy</string>
      <string id="UserPolicy_Explain">User-side synthetic policy.</string>
      <string id="EnableLabel">Enable</string>
      <string id="UserSettingLabel">User Setting</string>
    </stringTable>
  </resources>
</policyDefinitionResources>"""

def test_full_api_authoring_flow(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        response = client.post(
            "/api/gpos",
            json={"name": "Synthetic browser policy", "actor": "tester", "reason": "test"},
        )
        assert response.status_code == 201
        gpo = response.json()["gpo"]
        response = client.post(
            f"/api/gpos/{gpo['guid']}/settings",
            json={
                "actor": "tester",
                "reason": "add setting",
                "expected_revision": gpo["revision"],
                "setting": {
                    "side": "computer",
                    "hive": "HKLM",
                    "key": r"Software\Policies\Test",
                    "value_name": "Enabled",
                    "registry_type": "REG_DWORD",
                    "value": "1",
                },
            },
        )
        assert response.status_code == 201
        gpo = response.json()["gpo"]
        assert gpo["revision"] == 2
        assert len(gpo["settings"]) == 1
        assert client.get(f"/api/gpos/{gpo['guid']}/export.zip").status_code == 200
        assert len(client.get(f"/api/gpos/{gpo['guid']}/revisions").json()["items"]) == 2


def test_api_rejects_side_hive_mismatch_and_stale_revision(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "Synthetic policy"}).json()["gpo"]
        invalid = client.post(
            f"/api/gpos/{gpo['guid']}/settings",
            json={
                "expected_revision": 1,
                "setting": {
                    "side": "user",
                    "hive": "HKLM",
                    "key": "Software\\Policies\\Synthetic",
                    "value_name": "X",
                    "registry_type": "REG_SZ",
                    "value": "x",
                },
            },
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["issues"][0]["code"] == "side_hive_mismatch"
        client.patch(
            f"/api/gpos/{gpo['guid']}",
            json={
                "expected_revision": 1,
                "name": gpo["name"],
                "description": "changed",
                "computer_enabled": True,
                "user_enabled": True,
                "status": "draft",
            },
        )
        stale = client.patch(
            f"/api/gpos/{gpo['guid']}",
            json={
                "expected_revision": 1,
                "name": gpo["name"],
                "description": "stale",
                "computer_enabled": True,
                "user_enabled": True,
                "status": "draft",
            },
        )
        assert stale.status_code == 409


def test_semantic_hash_in_response(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        resp = client.post("/api/gpos", json={"name": "Hash test policy"})
        assert resp.status_code == 201
        data = resp.json()
        assert "semantic_sha256" not in data
        assert "policy_semantic_sha256" in data
        assert "review_model_sha256" in data
        assert len(data["policy_semantic_sha256"]) == 64
        assert len(data["review_model_sha256"]) == 64
        guid = data["gpo"]["guid"]
        rev = data["gpo"]["revision"]
        resp = client.post(
            f"/api/gpos/{guid}/settings",
            json={
                "expected_revision": rev,
                "reason": "add setting",
                "setting": {
                    "side": "computer",
                    "hive": "HKLM",
                    "key": r"Software\Policies\Test",
                    "value_name": "Enabled",
                    "registry_type": "REG_DWORD",
                    "value": "1",
                },
            },
        )
        assert resp.status_code == 201
        new_hash = resp.json()["policy_semantic_sha256"]
        assert new_hash != data["policy_semantic_sha256"]


def test_two_way_diff(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "Diff test"}).json()["gpo"]
        client.post(
            f"/api/gpos/{gpo['guid']}/settings",
            json={
                "expected_revision": 1,
                "reason": "add setting",
                "setting": {
                    "side": "computer",
                    "hive": "HKLM",
                    "key": r"Software\Policies\Diff",
                    "value_name": "SettingA",
                    "registry_type": "REG_DWORD",
                    "value": "1",
                },
            },
        )
        diff = client.get(f"/api/gpos/{gpo['guid']}/diff?against_revision=1")
        assert diff.status_code == 200
        result = diff.json()
        assert len(result["settings"]) == 1
        assert result["settings"][0]["kind"] == "added"


def test_two_way_diff_identical(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "Diff identical"}).json()["gpo"]
        diff = client.get(f"/api/gpos/{gpo['guid']}/diff?against_revision=1")
        assert diff.status_code == 200
        result = diff.json()
        assert result["settings"] == []
        assert result["links"] == []


def test_three_way_diff(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        base = client.post("/api/gpos", json={"name": "Baseline"}).json()["gpo"]
        draft = client.post("/api/gpos", json={"name": "Draft"}).json()["gpo"]
        observed = client.post("/api/gpos", json={"name": "Observed"}).json()["gpo"]
        resp = client.post(
            "/api/diff",
            json={
                "baseline": base["guid"],
                "draft": draft["guid"],
                "observed": observed["guid"],
            },
        )
        assert resp.status_code == 200
        result = resp.json()
        assert "settings" in result
        assert "links" in result
        assert "conflicts" in result


_MANIFEST_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<BackupInstances xmlns="http://www.microsoft.com/GroupPolicy/Types">
  <BackupInstance>
    <BackupTime>2026-01-01T00:00:00</BackupTime>
    <ID>backup-001</ID>
    <GPO>
      <Identifier>11111111-2222-3333-4444-555555555555</Identifier>
      <DisplayName>Imported Policy</DisplayName>
      <Domain>example.test</Domain>
    </GPO>
  </BackupInstance>
</BackupInstances>"""


def test_backup_import(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(tmp_path))
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / "11111111-2222-3333-4444-555555555555"
    machine_dir = gpo_dir / "Machine"
    user_dir = gpo_dir / "User"
    machine_dir.mkdir(parents=True)
    user_dir.mkdir(parents=True)
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_XML)
    machine_pol = serialize([
        PolRecord(key=r"Software\Policies\Test", value_name="Enabled",
                  registry_type="REG_DWORD", value=1),
    ])
    (machine_dir / "Registry.pol").write_bytes(machine_pol)
    (user_dir / "Registry.pol").write_bytes(b"PReg\x01\x00\x00\x00")

    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        resp = client.post("/api/backups/import", json={
            "path": str(backup_dir),
            "actor": "tester",
            "reason": "Import test backup",
        })
        assert resp.status_code == 201
        gpo = resp.json()["gpo"]
        assert gpo["name"] == "Imported Policy"
        assert gpo["source_guid"] == "11111111-2222-3333-4444-555555555555"
        assert len(gpo["settings"]) == 1
        assert gpo["settings"][0]["key"] == r"Software\Policies\Test"
        assert gpo["settings"][0]["value"] == "1"


def test_backup_import_nonexistent_dir(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        resp = client.post("/api/backups/import", json={
            "path": str(tmp_path / "nonexistent"),
        })
        assert resp.status_code == 422


def test_gpmc_backup_export(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "Export test"}).json()["gpo"]
        client.post(
            f"/api/gpos/{gpo['guid']}/settings",
            json={
                "expected_revision": 1,
                "reason": "add setting",
                "setting": {
                    "side": "computer",
                    "hive": "HKLM",
                    "key": r"Software\Policies\Test",
                    "value_name": "Enabled",
                    "registry_type": "REG_DWORD",
                    "value": 1,
                },
            },
        )
        resp = client.get(f"/api/gpos/{gpo['guid']}/gpmc-backup")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
            names = archive.namelist()
            assert "manifest.xml" in names
            assert "bkupInfo.xml" in names
            assert f"{gpo['guid']}/Machine/Registry.pol" in names
            assert f"{gpo['guid']}/User/Registry.pol" in names
            assert f"{gpo['guid']}/gpreport.xml" in names
            assert f"{gpo['guid']}/DomainController.xml" in names
            from gpo_studio.backup import parse_manifest
            backup = parse_manifest(archive.read("manifest.xml"))
            assert backup.gpos[0].display_name == "Export test"


def test_gpmc_backup_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(tmp_path))
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "Roundtrip test"}).json()["gpo"]
        client.post(
            f"/api/gpos/{gpo['guid']}/settings",
            json={
                "expected_revision": 1,
                "reason": "add setting",
                "setting": {
                    "side": "computer",
                    "hive": "HKLM",
                    "key": r"Software\Policies\Roundtrip",
                    "value_name": "SettingA",
                    "registry_type": "REG_DWORD",
                    "value": "42",
                },
            },
        )
        resp = client.get(f"/api/gpos/{gpo['guid']}/gpmc-backup")
        assert resp.status_code == 200
        backup_dir = tmp_path / "gpmc_backup"
        backup_dir.mkdir()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
            archive.extractall(backup_dir)
        store.close()
        app.state.store = WorkspaceStore(tmp_path / "api2.db")
        resp = client.post("/api/backups/import", json={
            "path": str(backup_dir),
            "actor": "tester",
            "reason": "Round-trip import",
        })
        assert resp.status_code == 201
        imported = resp.json()["gpo"]
        assert len(imported["settings"]) == 1
        assert imported["settings"][0]["key"] == r"Software\Policies\Roundtrip"
        assert imported["settings"][0]["value"] == "42"


def test_gpmc_backup_deterministic(tmp_path) -> None:
    from gpo_studio.export import gpmc_backup_bundle
    from gpo_studio.model import GPO, RegistrySetting
    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Deterministic test",
        settings=(
            RegistrySetting(
                id="s1", side="computer", hive="HKLM",
                key=r"Software\Policies\Test", value_name="V",
                registry_type="REG_DWORD", value=1,
            ),
        ),
    )
    assert gpmc_backup_bundle(gpo) == gpmc_backup_bundle(gpo)


def _setup_admx_env(tmp_path, monkeypatch, admx_dir_name: str = "admx"):
    admx_dir = tmp_path / admx_dir_name
    admx_dir.mkdir()
    (admx_dir / "test.admx").write_bytes(_ADMX_MINIMAL)
    (admx_dir / "test.adml").write_bytes(_ADML_MINIMAL)
    monkeypatch.setenv("GPO_STUDIO_ADMX_DIR", str(admx_dir))
    if hasattr(app.state, "admx_catalogue"):
        del app.state.admx_catalogue
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False


def test_admx_search_returns_results(tmp_path, monkeypatch) -> None:
    _setup_admx_env(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/api/admx/search?q=Synthetic")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] > 0
        assert any(p["id"] == "SyntheticPolicy" for p in data["items"])


def test_admx_search_empty_query(tmp_path, monkeypatch) -> None:
    _setup_admx_env(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/api/admx/search")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2


def test_admx_policy_detail(tmp_path, monkeypatch) -> None:
    _setup_admx_env(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/api/admx/policies/SyntheticPolicy")
        assert resp.status_code == 200
        policy = resp.json()
        assert policy["id"] == "SyntheticPolicy"
        assert policy["display_name"] == "Synthetic Policy"
        assert len(policy["elements"]) > 0
        assert len(policy["presentation"]) > 0
        not_found = client.get("/api/admx/policies/Nonexistent")
        assert not_found.status_code == 404


def test_admx_categories(tmp_path, monkeypatch) -> None:
    _setup_admx_env(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/api/admx/categories")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] > 0
        assert any(c["id"] == "SyntheticCategory" for c in data["items"])


def test_admx_no_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GPO_STUDIO_ADMX_DIR", str(tmp_path / "nonexistent"))
    if hasattr(app.state, "admx_catalogue"):
        del app.state.admx_catalogue
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.json()["admx_loaded"] is False
        search = client.get("/api/admx/search")
        assert search.status_code == 200
        assert search.json()["count"] == 0
        cats = client.get("/api/admx/categories")
        assert cats.status_code == 200
        assert cats.json()["count"] == 0


def test_gpmc_backup_rejected_with_cse_metadata(tmp_path) -> None:
    from gpo_studio.model import CseMetadataEntry
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = store.create_gpo(
            "CSE test", identity="tester", reason="test",
            cse_metadata=(CseMetadataEntry(guid="{unknown-guid}", side="machine"),),
        )
        resp = client.get(f"/api/gpos/{gpo.guid}/gpmc-backup")
        assert resp.status_code == 422
        assert resp.json()["error"]["issues"][0]["code"] == "unknown_cse_content"


def test_three_way_diff_identical(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "Identical 3-way"}).json()["gpo"]
        resp = client.post("/api/diff", json={
            "baseline": gpo["guid"],
            "draft": gpo["guid"],
            "observed": gpo["guid"],
        })
        assert resp.status_code == 200
        result = resp.json()
        assert result["settings"] == []
        assert result["conflicts"] == []


def test_ad_hoc_diff_malformed_dict(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        resp = client.post("/api/diff", json={
            "baseline": {"missing": "fields"},
            "draft": {"missing": "fields"},
            "observed": {"missing": "fields"},
        })
        assert resp.status_code == 422


def test_backup_import_no_registry_pol(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(tmp_path))
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / "11111111-2222-3333-4444-555555555555"
    gpo_dir.mkdir(parents=True)
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_XML)
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        resp = client.post("/api/backups/import", json={"path": str(backup_dir)})
        assert resp.status_code == 201
        gpo = resp.json()["gpo"]
        assert gpo["settings"] == []


def test_backup_import_not_a_directory(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        resp = client.post("/api/backups/import", json={"path": str(tmp_path / "nonexistent")})
        assert resp.status_code == 422


def test_configure_policy_boolean(tmp_path, monkeypatch) -> None:
    _setup_admx_env(tmp_path, monkeypatch)
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "Config target"}).json()["gpo"]
        resp = client.post("/api/admx/policies/SyntheticPolicy/configure", json={
            "gpo_guid": gpo["guid"],
            "side": "computer",
            "values": {"Enabled": True},
            "expected_revision": gpo["revision"],
            "actor": "tester",
            "reason": "Configure policy",
        })
        assert resp.status_code == 200
        updated = resp.json()["gpo"]
        assert len(updated["settings"]) == 1
        assert updated["settings"][0]["registry_type"] == "REG_DWORD"
        assert updated["settings"][0]["value"] == "1"


def test_configure_policy_unknown_policy_404(tmp_path, monkeypatch) -> None:
    _setup_admx_env(tmp_path, monkeypatch)
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "Target"}).json()["gpo"]
        resp = client.post("/api/admx/policies/Nonexistent/configure", json={
            "gpo_guid": gpo["guid"],
            "side": "computer",
            "values": {},
            "expected_revision": gpo["revision"],
        })
        assert resp.status_code == 404


def test_configure_policy_invalid_config_422(tmp_path, monkeypatch) -> None:
    _setup_admx_env(tmp_path, monkeypatch)
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "Target"}).json()["gpo"]
        resp = client.post("/api/admx/policies/SyntheticPolicy/configure", json={
            "gpo_guid": gpo["guid"],
            "side": "computer",
            "values": {},
            "expected_revision": gpo["revision"],
        })
        assert resp.status_code == 422


def test_configure_policy_side_mismatch_422(tmp_path, monkeypatch) -> None:
    _setup_admx_env(tmp_path, monkeypatch)
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "Target"}).json()["gpo"]
        resp = client.post("/api/admx/policies/SyntheticPolicy/configure", json={
            "gpo_guid": gpo["guid"],
            "side": "user",
            "values": {"Enabled": True},
            "expected_revision": gpo["revision"],
        })
        assert resp.status_code == 422


def test_configure_policy_unknown_gpo_404(tmp_path, monkeypatch) -> None:
    _setup_admx_env(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = client.post("/api/admx/policies/SyntheticPolicy/configure", json={
            "gpo_guid": "nonexistent-guid",
            "side": "computer",
            "values": {"Enabled": True},
            "expected_revision": 1,
        })
        assert resp.status_code == 404


def test_typed_cse_metadata_round_trip(tmp_path) -> None:
    from gpo_studio.model import CseFileEntry, CseMetadataEntry
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        cse = CseMetadataEntry(
            guid="{12345678-1234-1234-1234-123456789abc}",
            side="machine",
            files=(
                CseFileEntry(
                    relative_path="Custom/custom.xml",
                    content_hash="abc123",
                    size=100,
                ),
            ),
        )
        gpo = store.create_gpo(
            "Typed CSE test", identity="tester", reason="test",
            cse_metadata=(cse,),
        )
        resp = client.get(f"/api/gpos/{gpo.guid}")
        assert resp.status_code == 200
        cse_data = resp.json()["gpo"]["cse_metadata"]
        assert len(cse_data) == 1
        assert cse_data[0]["guid"] == "{12345678-1234-1234-1234-123456789abc}"
        assert cse_data[0]["side"] == "machine"
        assert len(cse_data[0]["files"]) == 1
        assert cse_data[0]["files"][0]["relative_path"] == "Custom/custom.xml"
        assert cse_data[0]["files"][0]["content_hash"] == "abc123"
        assert cse_data[0]["files"][0]["size"] == 100


def _create_minimal_backup(backup_dir: Path) -> None:
    gpo_dir = backup_dir / "11111111-2222-3333-4444-555555555555"
    machine_dir = gpo_dir / "Machine"
    user_dir = gpo_dir / "User"
    machine_dir.mkdir(parents=True)
    user_dir.mkdir(parents=True)
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_XML)
    machine_pol = serialize([
        PolRecord(key=r"Software\Policies\Test", value_name="Enabled",
                  registry_type="REG_DWORD", value=1),
    ])
    (machine_dir / "Registry.pol").write_bytes(machine_pol)
    (user_dir / "Registry.pol").write_bytes(b"PReg\x01\x00\x00\x00")


def test_backup_import_within_inbox(tmp_path: Path, monkeypatch) -> None:
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(inbox_dir))
    backup_dir = inbox_dir / "backup"
    _create_minimal_backup(backup_dir)
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        resp = client.post("/api/backups/import", json={
            "path": str(backup_dir),
            "actor": "tester",
            "reason": "Import from inbox",
        })
        assert resp.status_code == 201
        gpo = resp.json()["gpo"]
        assert gpo["name"] == "Imported Policy"
        assert gpo["source_guid"] == "11111111-2222-3333-4444-555555555555"


def test_backup_import_outside_inbox_rejected(tmp_path: Path, monkeypatch) -> None:
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(inbox_dir))
    backup_dir = tmp_path / "outside" / "backup"
    _create_minimal_backup(backup_dir)
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        resp = client.post("/api/backups/import", json={
            "path": str(backup_dir),
        })
        assert resp.status_code == 422
        issues = resp.json()["error"]["issues"]
        assert issues[0]["code"] == "path_outside_inbox"


def test_backup_import_no_inbox_configured(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GPO_STUDIO_INBOX_DIR", raising=False)
    backup_dir = tmp_path / "backup"
    _create_minimal_backup(backup_dir)
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    monkeypatch.chdir(tmp_path)
    with TestClient(app) as client:
        resp = client.post("/api/backups/import", json={
            "path": "backup",
        })
        assert resp.status_code == 201
        gpo = resp.json()["gpo"]
        assert gpo["name"] == "Imported Policy"


def test_backup_import_path_traversal_rejected(tmp_path: Path, monkeypatch) -> None:
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(inbox_dir))
    backup_dir = tmp_path / "outside" / "backup"
    _create_minimal_backup(backup_dir)
    traversal_path = str(inbox_dir / ".." / "outside" / "backup")
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        resp = client.post("/api/backups/import", json={
            "path": traversal_path,
        })
        assert resp.status_code == 422
        issues = resp.json()["error"]["issues"]
        assert issues[0]["code"] == "path_outside_inbox"


def test_import_backup_rejects_symlinked_manifest(tmp_path: Path, monkeypatch) -> None:
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(inbox_dir))
    backup_dir = inbox_dir / "backup"
    _create_minimal_backup(backup_dir)

    target = tmp_path / "fake_manifest.xml"
    target.write_bytes(b"evil")
    (backup_dir / "manifest.xml").unlink()
    (backup_dir / "manifest.xml").symlink_to(target)

    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        resp = client.post("/api/backups/import", json={
            "path": str(backup_dir),
            "actor": "tester",
            "reason": "Import symlinked backup",
        })
        assert resp.status_code == 422
        issues = resp.json()["error"]["issues"]
        assert issues[0]["code"] == "symlink_in_backup"


def test_security_filter_add_edit_delete(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post(
            "/api/gpos", json={"name": "Security filter policy"}
        ).json()["gpo"]
        resp = client.post(
            f"/api/gpos/{gpo['guid']}/security-filters",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "grant apply",
                "filter": {
                    "principal": "Domain Admins",
                    "permission": "apply",
                    "inheritable": True,
                },
            },
        )
        assert resp.status_code == 201
        gpo = resp.json()["gpo"]
        assert gpo["revision"] == 2
        assert len(gpo["security_filters"]) == 1
        assert gpo["security_filters"][0]["principal"] == "Domain Admins"
        assert gpo["security_filters"][0]["permission"] == "apply"

        filter_id = gpo["security_filters"][0]["id"]
        resp = client.put(
            f"/api/gpos/{gpo['guid']}/security-filters/{filter_id}",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "downgrade to read",
                "filter": {
                    "principal": "Domain Admins",
                    "permission": "read",
                    "inheritable": True,
                },
            },
        )
        assert resp.status_code == 200
        gpo = resp.json()["gpo"]
        assert gpo["revision"] == 3
        assert len(gpo["security_filters"]) == 1
        assert gpo["security_filters"][0]["permission"] == "read"

        resp = client.request(
            "DELETE",
            f"/api/gpos/{gpo['guid']}/security-filters/{filter_id}",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "remove filter",
            },
        )
        assert resp.status_code == 200
        gpo = resp.json()["gpo"]
        assert gpo["revision"] == 4
        assert len(gpo["security_filters"]) == 0


def test_security_filter_creates_revision(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post(
            "/api/gpos", json={"name": "Revision test"}
        ).json()["gpo"]
        initial_revision = gpo["revision"]
        resp = client.post(
            f"/api/gpos/{gpo['guid']}/security-filters",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "add filter",
                "filter": {"principal": "Domain Users"},
            },
        )
        assert resp.status_code == 201
        gpo = resp.json()["gpo"]
        assert gpo["revision"] == initial_revision + 1
        revisions = client.get(
            f"/api/gpos/{gpo['guid']}/revisions"
        ).json()["items"]
        assert len(revisions) == 2


def test_security_filter_stale_revision_returns_409(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post(
            "/api/gpos", json={"name": "Conflict test"}
        ).json()["gpo"]
        resp = client.post(
            f"/api/gpos/{gpo['guid']}/security-filters",
            json={
                "expected_revision": 999,
                "actor": "tester",
                "reason": "stale",
                "filter": {"principal": "Domain Admins"},
            },
        )
        assert resp.status_code == 409


def test_security_filter_with_target_type_user(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post(
            "/api/gpos", json={"name": "Target type policy"}
        ).json()["gpo"]
        resp = client.post(
            f"/api/gpos/{gpo['guid']}/security-filters",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "grant to user",
                "filter": {
                    "principal": "DOMAIN\\SvcAccount",
                    "permission": "apply",
                    "inheritable": True,
                    "target_type": "user",
                },
            },
        )
        assert resp.status_code == 201
        sf = resp.json()["gpo"]["security_filters"][0]
        assert sf["target_type"] == "user"


def test_wmi_filter_set_and_clear(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post(
            "/api/gpos", json={"name": "WMI filter policy"}
        ).json()["gpo"]
        resp = client.put(
            f"/api/gpos/{gpo['guid']}/wmi-filter",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "scope to workstations",
                "wmi_filter": {
                    "name": "Workstations",
                    "description": "Workstation filter",
                    "query": "SELECT * FROM Win32_ComputerSystem",
                    "language": "WQL",
                },
            },
        )
        assert resp.status_code == 200
        gpo = resp.json()["gpo"]
        assert gpo["revision"] == 2
        assert gpo["wmi_filter"] is not None
        assert gpo["wmi_filter"]["name"] == "Workstations"
        assert gpo["wmi_filter"]["query"] == "SELECT * FROM Win32_ComputerSystem"

        resp = client.request(
            "DELETE",
            f"/api/gpos/{gpo['guid']}/wmi-filter",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "clear filter",
            },
        )
        assert resp.status_code == 200
        gpo = resp.json()["gpo"]
        assert gpo["revision"] == 3
        assert gpo["wmi_filter"] is None


def test_domain_update_via_api(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "domain.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        resp = client.post(
            "/api/gpos",
            json={"name": "Domain test", "actor": "t", "reason": "test"},
        )
        gpo = resp.json()["gpo"]
        assert gpo["domain"] == "studio.local"
        resp = client.patch(
            f"/api/gpos/{gpo['guid']}",
            json={
                "name": "Domain test",
                "description": "",
                "computer_enabled": True,
                "user_enabled": True,
                "status": "draft",
                "domain": "corp.example.test",
                "actor": "t",
                "reason": "set domain",
                "expected_revision": gpo["revision"],
            },
        )
        assert resp.status_code == 200
        updated = resp.json()["gpo"]
        assert updated["domain"] == "corp.example.test"


def test_gpmc_backup_roundtrip_with_filters(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(tmp_path))
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "Filter roundtrip"}).json()["gpo"]
        client.post(
            f"/api/gpos/{gpo['guid']}/security-filters",
            json={
                "expected_revision": gpo["revision"],
                "actor": "t",
                "reason": "add filter",
                "filter": {
                    "principal": "DOMAIN\\Admins",
                    "permission": "apply",
                    "inheritable": True,
                    "target_type": "group",
                },
            },
        )
        gpo = client.get(f"/api/gpos/{gpo['guid']}").json()["gpo"]
        client.put(
            f"/api/gpos/{gpo['guid']}/wmi-filter",
            json={
                "expected_revision": gpo["revision"],
                "actor": "t",
                "reason": "set wmi",
                "wmi_filter": {
                    "name": "WorkstationFilter",
                    "query": "select * from Win32_OperatingSystem",
                    "language": "WQL",
                },
            },
        )
        resp = client.get(f"/api/gpos/{gpo['guid']}/gpmc-backup")
        assert resp.status_code == 200
        backup_dir = tmp_path / "gpmc_backup"
        backup_dir.mkdir()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
            archive.extractall(backup_dir)
        store.close()
        app.state.store = WorkspaceStore(tmp_path / "api2.db")
        resp = client.post("/api/backups/import", json={
            "path": str(backup_dir),
            "actor": "tester",
            "reason": "Round-trip with filters",
        })
        assert resp.status_code == 201
        imported = resp.json()["gpo"]
        assert len(imported["security_filters"]) == 1
        assert imported["security_filters"][0]["principal"] == "DOMAIN\\Admins"
        assert imported["security_filters"][0]["permission"] == "apply"
        assert imported["security_filters"][0]["target_type"] == "group"
        assert imported["wmi_filter"] is not None
        assert imported["wmi_filter"]["name"] == "WorkstationFilter"
        assert imported["wmi_filter"]["query"] == "select * from Win32_OperatingSystem"


def test_gpp_group_crud_via_api(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post(
            "/api/gpos", json={"name": "GPP group policy"}
        ).json()["gpo"]
        resp = client.post(
            f"/api/gpos/{gpo['guid']}/preferences/groups",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "add group",
                "scope": "computer",
                "group": {
                    "name": "Administrators",
                    "sid": "S-1-5-32-544",
                    "action": "update",
                    "members": [
                        {
                            "sid": "S-1-5-21-1-2-3-500",
                            "name": "DOMAIN\\Domain Admins",
                            "action": "add",
                        }
                    ],
                },
            },
        )
        assert resp.status_code == 201
        gpo = resp.json()["gpo"]
        assert len(gpo["gpp_collections"]) == 1
        assert gpo["gpp_collections"][0]["scope"] == "computer"
        assert len(gpo["gpp_collections"][0]["groups"]) == 1
        group_id = gpo["gpp_collections"][0]["groups"][0]["id"]
        assert group_id

        resp = client.put(
            f"/api/gpos/{gpo['guid']}/preferences/groups/{group_id}",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "rename group",
                "scope": "computer",
                "group": {
                    "name": "Admins",
                    "sid": "S-1-5-32-544",
                    "action": "update",
                    "id": group_id,
                },
            },
        )
        assert resp.status_code == 200
        gpo = resp.json()["gpo"]
        assert gpo["gpp_collections"][0]["groups"][0]["name"] == "Admins"

        resp = client.request(
            "DELETE",
            f"/api/gpos/{gpo['guid']}/preferences/groups/{group_id}",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "remove group",
                "scope": "computer",
            },
        )
        assert resp.status_code == 200
        gpo = resp.json()["gpo"]
        assert len(gpo["gpp_collections"]) == 0


def test_gpp_registry_crud_via_api(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post(
            "/api/gpos", json={"name": "GPP registry policy"}
        ).json()["gpo"]
        resp = client.post(
            f"/api/gpos/{gpo['guid']}/preferences/registry",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "add registry",
                "scope": "computer",
                "registry": {
                    "key": r"Software\Policies\Test",
                    "action": "update",
                    "values": [
                        {
                            "name": "Enabled",
                            "value": "42",
                            "registry_type": "REG_DWORD",
                            "action": "create",
                        }
                    ],
                },
            },
        )
        assert resp.status_code == 201
        gpo = resp.json()["gpo"]
        assert len(gpo["gpp_collections"][0]["registry"]) == 1
        reg_id = gpo["gpp_collections"][0]["registry"][0]["id"]
        assert reg_id
        assert gpo["gpp_collections"][0]["registry"][0]["values"][0]["value"] == "42"

        resp = client.request(
            "DELETE",
            f"/api/gpos/{gpo['guid']}/preferences/registry/{reg_id}",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "remove registry",
                "scope": "computer",
            },
        )
        assert resp.status_code == 200
        gpo = resp.json()["gpo"]
        assert len(gpo["gpp_collections"]) == 0


def test_gpp_member_crud_via_api(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post(
            "/api/gpos", json={"name": "GPP member policy"}
        ).json()["gpo"]
        resp = client.post(
            f"/api/gpos/{gpo['guid']}/preferences/groups",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "add group",
                "scope": "computer",
                "group": {
                    "name": "Administrators",
                    "sid": "S-1-5-32-544",
                    "action": "update",
                },
            },
        )
        assert resp.status_code == 201
        gpo = resp.json()["gpo"]
        group_id = gpo["gpp_collections"][0]["groups"][0]["id"]

        resp = client.post(
            f"/api/gpos/{gpo['guid']}/preferences/groups/{group_id}/members",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "add member",
                "scope": "computer",
                "member": {
                    "sid": "S-1-5-21-1-2-3-500",
                    "name": "DOMAIN\\Domain Admins",
                    "action": "add",
                },
            },
        )
        assert resp.status_code == 201
        gpo = resp.json()["gpo"]
        assert len(gpo["gpp_collections"][0]["groups"][0]["members"]) == 1
        member_id = gpo["gpp_collections"][0]["groups"][0]["members"][0]["id"]
        assert member_id

        resp = client.request(
            "DELETE",
            f"/api/gpos/{gpo['guid']}/preferences/groups/{group_id}/members/{member_id}",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "remove member",
                "scope": "computer",
            },
        )
        assert resp.status_code == 200
        gpo = resp.json()["gpo"]
        assert len(gpo["gpp_collections"][0]["groups"][0].get("members", [])) == 0


def test_gpp_dword_registry_value_round_trips(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post(
            "/api/gpos", json={"name": "GPP dword policy"}
        ).json()["gpo"]
        resp = client.post(
            f"/api/gpos/{gpo['guid']}/preferences/registry",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "add dword",
                "scope": "computer",
                "registry": {
                    "key": r"Software\Policies\Test",
                    "action": "update",
                    "values": [
                        {
                            "name": "MaxValue",
                            "value": "4294967295",
                            "registry_type": "REG_DWORD",
                            "action": "create",
                        }
                    ],
                },
            },
        )
        assert resp.status_code == 201
        gpo = resp.json()["gpo"]
        val = gpo["gpp_collections"][0]["registry"][0]["values"][0]
        assert val["registry_type"] == "REG_DWORD"
        assert val["value"] == "4294967295"


def test_gpp_invalid_group_name_returns_422(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post(
            "/api/gpos", json={"name": "GPP invalid policy"}
        ).json()["gpo"]
        resp = client.post(
            f"/api/gpos/{gpo['guid']}/preferences/groups",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "invalid group",
                "scope": "computer",
                "group": {
                    "name": "   ",
                    "sid": "S-1-5-32-544",
                    "action": "update",
                },
            },
        )
        assert resp.status_code == 422
        issues = resp.json()["error"]["issues"]
        assert any(i["code"] == "empty_gpp_group_name" for i in issues)


def test_gpp_stale_revision_returns_409(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post(
            "/api/gpos", json={"name": "GPP conflict policy"}
        ).json()["gpo"]
        resp = client.post(
            f"/api/gpos/{gpo['guid']}/preferences/groups",
            json={
                "expected_revision": 999,
                "actor": "tester",
                "reason": "stale",
                "scope": "computer",
                "group": {
                    "name": "Administrators",
                    "sid": "S-1-5-32-544",
                    "action": "update",
                },
            },
        )
        assert resp.status_code == 409


def test_gpp_group_with_ilt_filter(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post(
            "/api/gpos", json={"name": "GPP ILT policy"}
        ).json()["gpo"]
        resp = client.post(
            f"/api/gpos/{gpo['guid']}/preferences/groups",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "add group with filter",
                "scope": "computer",
                "group": {
                    "name": "Administrators",
                    "sid": "S-1-5-32-544",
                    "action": "update",
                    "ilt_filter": {
                        "predicates": [
                            {
                                "type": "ou",
                                "value": "OU=Workstations,DC=example,DC=test",
                            }
                        ]
                    },
                },
            },
        )
        assert resp.status_code == 201
        gpo = resp.json()["gpo"]
        group = gpo["gpp_collections"][0]["groups"][0]
        assert group["ilt_filter"] is not None
        assert len(group["ilt_filter"]["predicates"]) == 1
        assert group["ilt_filter"]["predicates"][0]["type"] == "ou"


def test_gpp_group_edit_uses_path_id_not_body_id(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post(
            "/api/gpos", json={"name": "GPP path id policy"}
        ).json()["gpo"]
        resp = client.post(
            f"/api/gpos/{gpo['guid']}/preferences/groups",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "add group",
                "scope": "computer",
                "group": {
                    "name": "Administrators",
                    "sid": "S-1-5-32-544",
                    "action": "update",
                },
            },
        )
        gpo = resp.json()["gpo"]
        group_id = gpo["gpp_collections"][0]["groups"][0]["id"]
        resp = client.put(
            f"/api/gpos/{gpo['guid']}/preferences/groups/{group_id}",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "edit",
                "scope": "computer",
                "group": {
                    "name": "Admins",
                    "sid": "S-1-5-32-544",
                    "action": "update",
                    "id": "divergent-body-id",
                },
            },
        )
        assert resp.status_code == 200
        gpo = resp.json()["gpo"]
        groups = gpo["gpp_collections"][0]["groups"]
        assert len(groups) == 1
        assert groups[0]["id"] == group_id
        assert groups[0]["name"] == "Admins"


def test_gpp_registry_edit_uses_path_id_not_body_id(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post(
            "/api/gpos", json={"name": "GPP path id reg policy"}
        ).json()["gpo"]
        resp = client.post(
            f"/api/gpos/{gpo['guid']}/preferences/registry",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "add registry",
                "scope": "computer",
                "registry": {
                    "key": r"Software\Policies\Test",
                    "action": "update",
                    "values": [],
                },
            },
        )
        gpo = resp.json()["gpo"]
        reg_id = gpo["gpp_collections"][0]["registry"][0]["id"]
        resp = client.put(
            f"/api/gpos/{gpo['guid']}/preferences/registry/{reg_id}",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "edit",
                "scope": "computer",
                "registry": {
                    "key": r"Software\Policies\Updated",
                    "action": "update",
                    "values": [],
                    "id": "divergent-body-id",
                },
            },
        )
        assert resp.status_code == 200
        gpo = resp.json()["gpo"]
        registry = gpo["gpp_collections"][0]["registry"]
        assert len(registry) == 1
        assert registry[0]["id"] == reg_id
        assert registry[0]["key"] == r"Software\Policies\Updated"


def test_gpp_member_edit_uses_path_id_not_body_id(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post(
            "/api/gpos", json={"name": "GPP path id member policy"}
        ).json()["gpo"]
        resp = client.post(
            f"/api/gpos/{gpo['guid']}/preferences/groups",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "add group",
                "scope": "computer",
                "group": {
                    "name": "Administrators",
                    "sid": "S-1-5-32-544",
                    "action": "update",
                },
            },
        )
        gpo = resp.json()["gpo"]
        group_id = gpo["gpp_collections"][0]["groups"][0]["id"]
        resp = client.post(
            f"/api/gpos/{gpo['guid']}/preferences/groups/{group_id}/members",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "add member",
                "scope": "computer",
                "member": {
                    "sid": "S-1-5-21-1-2-3-500",
                    "name": "DOMAIN\\Domain Admins",
                    "action": "add",
                },
            },
        )
        gpo = resp.json()["gpo"]
        member_id = gpo["gpp_collections"][0]["groups"][0]["members"][0]["id"]
        resp = client.put(
            f"/api/gpos/{gpo['guid']}/preferences/groups/{group_id}/members/{member_id}",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "edit member",
                "scope": "computer",
                "member": {
                    "sid": "S-1-5-21-1-2-3-500",
                    "name": "DOMAIN\\Admins",
                    "action": "add",
                    "id": "divergent-body-id",
                },
            },
        )
        assert resp.status_code == 200
        gpo = resp.json()["gpo"]
        members = gpo["gpp_collections"][0]["groups"][0]["members"]
        assert len(members) == 1
        assert members[0]["id"] == member_id
        assert members[0]["name"] == "DOMAIN\\Admins"
