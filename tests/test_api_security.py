from __future__ import annotations

import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.store import WorkspaceStore


def _setup_store(tmp_path: Path) -> None:
    app.state.store = WorkspaceStore(tmp_path / "api.db")
    app.state.owns_store = False


def test_security_headers_on_health(tmp_path: Path) -> None:
    _setup_store(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["Referrer-Policy"] == "no-referrer"
        assert resp.headers["Cache-Control"] == "no-store"
        csp = resp.headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp


def test_security_headers_on_404(tmp_path: Path) -> None:
    _setup_store(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/gpos/nonexistent-guid")
        assert resp.status_code == 404
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["Referrer-Policy"] == "no-referrer"
        assert resp.headers["Cache-Control"] == "no-store"


def test_security_headers_on_422(tmp_path: Path) -> None:
    _setup_store(tmp_path)
    with TestClient(app) as client:
        resp = client.post("/api/gpos", json={})
        assert resp.status_code == 422
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"


def test_security_headers_on_static_file(tmp_path: Path) -> None:
    _setup_store(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/assets/studio.css")
        assert resp.status_code == 200
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["Referrer-Policy"] == "no-referrer"


def test_security_headers_on_421_host_rejection(tmp_path: Path, monkeypatch) -> None:
    _setup_store(tmp_path)
    monkeypatch.delenv("GPO_STUDIO_UNSAFE_BIND")
    with TestClient(app) as client:
        resp = client.get("/api/health", headers={"Host": "evil.com"})
        assert resp.status_code == 421
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"


def test_security_headers_on_413_body_too_large(tmp_path: Path, monkeypatch) -> None:
    _setup_store(tmp_path)
    monkeypatch.setattr("gpo_studio.api.MAX_REQUEST_BODY_BYTES", 100)
    with TestClient(app) as client:
        resp = client.post(
            "/api/gpos",
            json={"name": "x" * 200, "actor": "a", "reason": "b"},
        )
        assert resp.status_code == 413
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"


def test_host_validation_allows_localhost(tmp_path: Path, monkeypatch) -> None:
    _setup_store(tmp_path)
    monkeypatch.delenv("GPO_STUDIO_UNSAFE_BIND")
    with TestClient(app) as client:
        resp = client.get("/api/health", headers={"Host": "localhost"})
        assert resp.status_code == 200


def test_host_validation_allows_127_0_0_1(tmp_path: Path, monkeypatch) -> None:
    _setup_store(tmp_path)
    monkeypatch.delenv("GPO_STUDIO_UNSAFE_BIND")
    with TestClient(app) as client:
        resp = client.get("/api/health", headers={"Host": "127.0.0.1"})
        assert resp.status_code == 200


def test_host_validation_allows_127_0_0_1_with_port(tmp_path: Path, monkeypatch) -> None:
    _setup_store(tmp_path)
    monkeypatch.delenv("GPO_STUDIO_UNSAFE_BIND")
    with TestClient(app) as client:
        resp = client.get("/api/health", headers={"Host": "127.0.0.1:8765"})
        assert resp.status_code == 200


def test_host_validation_allows_localhost_with_port(tmp_path: Path, monkeypatch) -> None:
    _setup_store(tmp_path)
    monkeypatch.delenv("GPO_STUDIO_UNSAFE_BIND")
    with TestClient(app) as client:
        resp = client.get("/api/health", headers={"Host": "localhost:8765"})
        assert resp.status_code == 200


def test_host_validation_allows_ipv6_loopback(tmp_path: Path, monkeypatch) -> None:
    _setup_store(tmp_path)
    monkeypatch.delenv("GPO_STUDIO_UNSAFE_BIND")
    with TestClient(app) as client:
        resp = client.get("/api/health", headers={"Host": "[::1]"})
        assert resp.status_code == 200


def test_host_validation_rejects_evil_com(tmp_path: Path, monkeypatch) -> None:
    _setup_store(tmp_path)
    monkeypatch.delenv("GPO_STUDIO_UNSAFE_BIND")
    with TestClient(app) as client:
        resp = client.get("/api/health", headers={"Host": "evil.com"})
        assert resp.status_code == 421
        assert resp.json()["error"]["message"] == "Host header not allowed"


def test_host_validation_rejects_private_ip(tmp_path: Path, monkeypatch) -> None:
    _setup_store(tmp_path)
    monkeypatch.delenv("GPO_STUDIO_UNSAFE_BIND")
    with TestClient(app) as client:
        resp = client.get("/api/health", headers={"Host": "192.168.1.1"})
        assert resp.status_code == 421


def test_host_validation_rejects_evil_com_with_port(tmp_path: Path, monkeypatch) -> None:
    _setup_store(tmp_path)
    monkeypatch.delenv("GPO_STUDIO_UNSAFE_BIND")
    with TestClient(app) as client:
        resp = client.get("/api/health", headers={"Host": "evil.com:8765"})
        assert resp.status_code == 421


def test_host_validation_unsafe_bind_allows_any_host(tmp_path: Path, monkeypatch) -> None:
    _setup_store(tmp_path)
    monkeypatch.setenv("GPO_STUDIO_UNSAFE_BIND", "1")
    with TestClient(app) as client:
        resp = client.get("/api/health", headers={"Host": "evil.com"})
        assert resp.status_code == 200


def test_host_validation_unsafe_bind_true_allows_any_host(
    tmp_path: Path, monkeypatch
) -> None:
    _setup_store(tmp_path)
    monkeypatch.setenv("GPO_STUDIO_UNSAFE_BIND", "true")
    with TestClient(app) as client:
        resp = client.get("/api/health", headers={"Host": "evil.com"})
        assert resp.status_code == 200


def test_body_size_limit_allows_normal_request(tmp_path: Path) -> None:
    _setup_store(tmp_path)
    with TestClient(app) as client:
        resp = client.post(
            "/api/gpos",
            json={"name": "Normal policy", "actor": "tester", "reason": "test"},
        )
        assert resp.status_code == 201


def test_body_size_limit_rejects_oversized_content_length(
    tmp_path: Path, monkeypatch
) -> None:
    _setup_store(tmp_path)
    monkeypatch.setattr("gpo_studio.api.MAX_REQUEST_BODY_BYTES", 100)
    with TestClient(app) as client:
        resp = client.post(
            "/api/gpos",
            json={"name": "x" * 200, "actor": "a", "reason": "b"},
        )
        assert resp.status_code == 413
        assert resp.json()["error"]["message"] == "Request body too large"


def test_body_size_limit_rejects_invalid_content_length(
    tmp_path: Path, monkeypatch
) -> None:
    _setup_store(tmp_path)
    monkeypatch.setattr("gpo_studio.api.MAX_REQUEST_BODY_BYTES", 100)
    with TestClient(app) as client:
        resp = client.post(
            "/api/gpos",
            content=b'{"name": "test"}',
            headers={"Content-Type": "application/json", "Content-Length": "abc"},
        )
        assert resp.status_code == 400


def test_request_id_header_present(tmp_path: Path) -> None:
    _setup_store(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert "X-Request-ID" in resp.headers


def test_request_id_is_valid_uuid(tmp_path: Path) -> None:
    _setup_store(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/health")
        request_id = resp.headers["X-Request-ID"]
        parsed = uuid.UUID(request_id)
        assert str(parsed) == request_id


def test_request_id_unique_per_request(tmp_path: Path) -> None:
    _setup_store(tmp_path)
    with TestClient(app) as client:
        resp1 = client.get("/api/health")
        resp2 = client.get("/api/health")
        id1 = resp1.headers["X-Request-ID"]
        id2 = resp2.headers["X-Request-ID"]
        assert id1 != id2


def test_request_id_present_on_error(tmp_path: Path) -> None:
    _setup_store(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/gpos/nonexistent-guid")
        assert resp.status_code == 404
        assert "X-Request-ID" in resp.headers


def test_health_includes_schema_version(tmp_path: Path) -> None:
    _setup_store(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "schema_version" in data
        assert data["schema_version"] != "unknown"


def test_health_schema_version_is_string(tmp_path: Path) -> None:
    _setup_store(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/health")
        data = resp.json()
        assert isinstance(data["schema_version"], str)


def test_health_retains_existing_fields(tmp_path: Path) -> None:
    _setup_store(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/health")
        data = resp.json()
        assert data["status"] == "ok"
        assert data["mode"] == "offline-workspace"
        assert "version" in data
        assert "admx_loaded" in data
        assert "wmi_catalogue_loaded" in data


def test_host_validation_rejects_empty_host(tmp_path: Path, monkeypatch) -> None:
    _setup_store(tmp_path)
    monkeypatch.delenv("GPO_STUDIO_UNSAFE_BIND")
    with TestClient(app) as client:
        resp = client.get("/api/health", headers={"Host": ""})
        assert resp.status_code == 421


def test_host_validation_rejects_0_0_0_0(tmp_path: Path, monkeypatch) -> None:
    _setup_store(tmp_path)
    monkeypatch.delenv("GPO_STUDIO_UNSAFE_BIND")
    with TestClient(app) as client:
        resp = client.get("/api/health", headers={"Host": "0.0.0.0"})
        assert resp.status_code == 421


def test_body_size_rejects_chunked_encoding(tmp_path: Path) -> None:
    _setup_store(tmp_path)
    with TestClient(app) as client:
        resp = client.post(
            "/api/gpos",
            content=b'{"name": "test", "actor": "a", "reason": "b"}',
            headers={
                "Content-Type": "application/json",
                "Transfer-Encoding": "chunked",
            },
        )
        assert resp.status_code == 400
        assert "Chunked" in resp.json()["error"]["message"]


def test_body_size_rejects_negative_content_length(tmp_path: Path) -> None:
    _setup_store(tmp_path)
    with TestClient(app) as client:
        resp = client.post(
            "/api/gpos",
            content=b'{"name": "test", "actor": "a", "reason": "b"}',
            headers={
                "Content-Type": "application/json",
                "Content-Length": "-1",
            },
        )
        assert resp.status_code == 413


def test_host_validation_case_insensitive(tmp_path: Path, monkeypatch) -> None:
    _setup_store(tmp_path)
    monkeypatch.delenv("GPO_STUDIO_UNSAFE_BIND")
    with TestClient(app) as client:
        resp = client.get("/api/health", headers={"Host": "LOCALHOST"})
        assert resp.status_code == 200


def test_workspace_error_returns_503(tmp_path: Path) -> None:
    from unittest.mock import patch

    from gpo_studio.model import WorkspaceError

    _setup_store(tmp_path)
    with TestClient(app) as client, patch.object(
        type(app.state.store),
        "list_gpos",
        side_effect=WorkspaceError("database is locked"),
    ):
        resp = client.get("/api/gpos")
        assert resp.status_code == 503
        assert "locked" in resp.json()["error"]["message"]
