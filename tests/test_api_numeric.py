from __future__ import annotations

from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.store import WorkspaceStore


def _post_setting(
    client: TestClient,
    guid: str,
    revision: int,
    registry_type: str,
    value: object,
):
    return client.post(
        f"/api/gpos/{guid}/settings",
        json={
            "actor": "tester",
            "reason": "numeric test",
            "expected_revision": revision,
            "setting": {
                "side": "computer",
                "hive": "HKLM",
                "key": r"Software\Policies\Numeric",
                "value_name": "Value",
                "registry_type": registry_type,
                "value": value,
            },
        },
    )


def test_qword_string_precision_boundary(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "QWORD test"}).json()["gpo"]
        resp = _post_setting(
            client, gpo["guid"], gpo["revision"], "REG_QWORD", "9007199254740993"
        )
        assert resp.status_code == 201
        setting = resp.json()["gpo"]["settings"][0]
        assert setting["value"] == "9007199254740993"
        assert isinstance(setting["value"], str)


def test_dword_string_max(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "DWORD max"}).json()["gpo"]
        resp = _post_setting(
            client, gpo["guid"], gpo["revision"], "REG_DWORD", "4294967295"
        )
        assert resp.status_code == 201
        assert resp.json()["gpo"]["settings"][0]["value"] == "4294967295"


def test_dword_string_zero(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "DWORD zero"}).json()["gpo"]
        resp = _post_setting(client, gpo["guid"], gpo["revision"], "REG_DWORD", "0")
        assert resp.status_code == 201
        assert resp.json()["gpo"]["settings"][0]["value"] == "0"


def test_qword_string_max(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "QWORD max"}).json()["gpo"]
        resp = _post_setting(
            client, gpo["guid"], gpo["revision"], "REG_QWORD", "18446744073709551615"
        )
        assert resp.status_code == 201
        setting = resp.json()["gpo"]["settings"][0]
        assert setting["value"] == "18446744073709551615"


def test_qword_max_round_trip_via_get(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "QWORD round trip"}).json()["gpo"]
        resp = _post_setting(
            client, gpo["guid"], gpo["revision"], "REG_QWORD", "18446744073709551615"
        )
        assert resp.status_code == 201
        fetched = client.get(f"/api/gpos/{gpo['guid']}").json()["gpo"]
        setting = fetched["settings"][0]
        assert setting["value"] == "18446744073709551615"
        assert isinstance(setting["value"], str)


def test_dword_string_overflow_rejected(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "DWORD overflow"}).json()["gpo"]
        resp = _post_setting(
            client, gpo["guid"], gpo["revision"], "REG_DWORD", "4294967296"
        )
        assert resp.status_code == 422


def test_dword_string_signed_rejected(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "DWORD signed"}).json()["gpo"]
        resp = _post_setting(client, gpo["guid"], gpo["revision"], "REG_DWORD", "-1")
        assert resp.status_code == 422


def test_dword_string_padded_rejected(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "DWORD padded"}).json()["gpo"]
        resp = _post_setting(client, gpo["guid"], gpo["revision"], "REG_DWORD", "007")
        assert resp.status_code == 422


def test_dword_string_fractional_rejected(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "DWORD frac"}).json()["gpo"]
        resp = _post_setting(client, gpo["guid"], gpo["revision"], "REG_DWORD", "1.5")
        assert resp.status_code == 422


def test_qword_int_precision_boundary(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "QWORD int"}).json()["gpo"]
        resp = _post_setting(
            client, gpo["guid"], gpo["revision"], "REG_QWORD", "9007199254740993"
        )
        assert resp.status_code == 201
        setting = resp.json()["gpo"]["settings"][0]
        assert setting["value"] == "9007199254740993"


def test_dword_int_overflow_rejected(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "DWORD int overflow"}).json()["gpo"]
        resp = _post_setting(
            client, gpo["guid"], gpo["revision"], "REG_DWORD", "4294967296"
        )
        assert resp.status_code == 422


def test_dword_int_rejected(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "DWORD int rejected"}).json()["gpo"]
        resp = _post_setting(
            client, gpo["guid"], gpo["revision"], "REG_DWORD", 42
        )
        assert resp.status_code == 422


def test_qword_string_fractional_rejected(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "QWORD frac"}).json()["gpo"]
        resp = _post_setting(
            client, gpo["guid"], gpo["revision"], "REG_QWORD", "1.5"
        )
        assert resp.status_code == 422


def test_dword_list_value_rejected(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "DWORD list"}).json()["gpo"]
        resp = _post_setting(
            client, gpo["guid"], gpo["revision"], "REG_DWORD", ["a", "b"]
        )
        assert resp.status_code == 422
