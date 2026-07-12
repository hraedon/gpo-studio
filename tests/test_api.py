from __future__ import annotations

import io
import zipfile

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
                    "key": r"Software\Policies\Synthetic",
                    "value_name": "Enabled",
                    "registry_type": "REG_DWORD",
                    "value": 1,
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
        assert "semantic_sha256" in data
        assert len(data["semantic_sha256"]) == 64
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
                    "value": 1,
                },
            },
        )
        assert resp.status_code == 201
        new_hash = resp.json()["semantic_sha256"]
        assert new_hash != data["semantic_sha256"]


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
                    "value": 1,
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


def test_backup_import(tmp_path) -> None:
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
        assert gpo["settings"][0]["value"] == 1


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


def test_gpmc_backup_roundtrip(tmp_path) -> None:
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
                    "value": 42,
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
        assert imported["settings"][0]["value"] == 42


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
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = store.create_gpo(
            "CSE test", identity="tester", reason="test",
            cse_metadata=({"guid": "{unknown-guid}", "side": "machine", "files": []},),
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


def test_backup_import_no_registry_pol(tmp_path) -> None:
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
