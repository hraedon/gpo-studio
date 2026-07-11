from __future__ import annotations

from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.store import WorkspaceStore


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
