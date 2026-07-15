from __future__ import annotations

import logging
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.backup import BackupError
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


def test_origin_validation_rejects_mutation_from_evil_origin(
    tmp_path: Path, monkeypatch
) -> None:
    _setup_store(tmp_path)
    monkeypatch.delenv("GPO_STUDIO_UNSAFE_BIND")
    with TestClient(app) as client:
        resp = client.post(
            "/api/gpos",
            json={"name": "Test", "actor": "a", "reason": "b"},
            headers={"Host": "localhost", "Origin": "https://evil.example"},
        )
        assert resp.status_code == 403


def test_origin_validation_rejects_null_origin(
    tmp_path: Path, monkeypatch
) -> None:
    _setup_store(tmp_path)
    monkeypatch.delenv("GPO_STUDIO_UNSAFE_BIND")
    with TestClient(app) as client:
        resp = client.post(
            "/api/gpos",
            json={"name": "Test", "actor": "a", "reason": "b"},
            headers={"Host": "localhost", "Origin": "null"},
        )
        assert resp.status_code == 403


def test_origin_validation_allows_mutation_from_localhost_origin(
    tmp_path: Path, monkeypatch
) -> None:
    _setup_store(tmp_path)
    monkeypatch.delenv("GPO_STUDIO_UNSAFE_BIND")
    with TestClient(app) as client:
        resp = client.post(
            "/api/gpos",
            json={"name": "Test", "actor": "a", "reason": "b"},
            headers={"Host": "localhost", "Origin": "http://localhost:8765"},
        )
        assert resp.status_code == 201


def test_origin_validation_allows_mutation_without_origin_header(
    tmp_path: Path, monkeypatch
) -> None:
    _setup_store(tmp_path)
    monkeypatch.delenv("GPO_STUDIO_UNSAFE_BIND")
    with TestClient(app) as client:
        resp = client.post(
            "/api/gpos",
            json={"name": "Test", "actor": "a", "reason": "b"},
            headers={"Host": "localhost"},
        )
        assert resp.status_code == 201


def test_origin_validation_allows_get_from_any_origin(
    tmp_path: Path, monkeypatch
) -> None:
    _setup_store(tmp_path)
    monkeypatch.delenv("GPO_STUDIO_UNSAFE_BIND")
    with TestClient(app) as client:
        resp = client.get(
            "/api/health",
            headers={"Host": "localhost", "Origin": "https://evil.example"},
        )
        assert resp.status_code == 200


def test_origin_validation_unsafe_mode_allows_any_origin(tmp_path: Path) -> None:
    _setup_store(tmp_path)
    with TestClient(app) as client:
        resp = client.post(
            "/api/gpos",
            json={"name": "Test", "actor": "a", "reason": "b"},
            headers={"Origin": "https://evil.example"},
        )
        assert resp.status_code == 201


def test_body_size_counts_actual_bytes_no_content_length(
    tmp_path: Path, monkeypatch
) -> None:
    _setup_store(tmp_path)
    monkeypatch.setattr("gpo_studio.api.MAX_REQUEST_BODY_BYTES", 100)
    with TestClient(app) as client:
        resp = client.post(
            "/api/gpos",
            content=b'{"name": "' + b'x' * 200 + b'", "actor": "a", "reason": "b"}',
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 413


def test_request_log_includes_request_id(tmp_path: Path, caplog) -> None:
    _setup_store(tmp_path)
    caplog.set_level(logging.INFO)
    with TestClient(app) as client:
        client.get("/api/health")
    assert any("request_id=" in r.getMessage() for r in caplog.records)


def test_request_log_includes_outcome(tmp_path: Path, caplog) -> None:
    _setup_store(tmp_path)
    caplog.set_level(logging.INFO)
    with TestClient(app) as client:
        client.get("/api/health")
        client.get("/api/gpos/nonexistent-guid")
    messages = [
        r.getMessage() for r in caplog.records if "request_id=" in r.getMessage()
    ]
    assert any("outcome=success" in m for m in messages)
    assert any("outcome=error" in m for m in messages)


def test_lifespan_log_does_not_contain_path(
    tmp_path: Path, caplog, monkeypatch
) -> None:
    if hasattr(app.state, "store"):
        del app.state.store
    monkeypatch.setenv("GPO_STUDIO_DB", str(tmp_path / "api.db"))
    caplog.set_level(logging.INFO)
    with TestClient(app):
        pass
    workspace_logs = [
        r for r in caplog.records if "workspace_opened" in r.getMessage()
    ]
    assert len(workspace_logs) > 0
    for record in workspace_logs:
        assert "path=" not in record.getMessage()


def test_origin_validation_rejects_hostless_origin(
    tmp_path: Path, monkeypatch
) -> None:
    _setup_store(tmp_path)
    monkeypatch.delenv("GPO_STUDIO_UNSAFE_BIND")
    with TestClient(app) as client:
        resp = client.post(
            "/api/gpos",
            json={"name": "Test", "actor": "a", "reason": "b"},
            headers={"Host": "localhost", "Origin": "://bad"},
        )
        assert resp.status_code == 403


def test_origin_validation_rejects_file_origin(
    tmp_path: Path, monkeypatch
) -> None:
    _setup_store(tmp_path)
    monkeypatch.delenv("GPO_STUDIO_UNSAFE_BIND")
    with TestClient(app) as client:
        resp = client.post(
            "/api/gpos",
            json={"name": "Test", "actor": "a", "reason": "b"},
            headers={"Host": "localhost", "Origin": "file:///etc/passwd"},
        )
        assert resp.status_code == 403


def test_origin_validation_rejects_malformed_origin(
    tmp_path: Path, monkeypatch
) -> None:
    _setup_store(tmp_path)
    monkeypatch.delenv("GPO_STUDIO_UNSAFE_BIND")
    with TestClient(app) as client:
        resp = client.post(
            "/api/gpos",
            json={"name": "Test", "actor": "a", "reason": "b"},
            headers={"Host": "localhost", "Origin": "http://["},
        )
        assert resp.status_code == 403


def test_origin_validation_rejects_ftp_origin(
    tmp_path: Path, monkeypatch
) -> None:
    _setup_store(tmp_path)
    monkeypatch.delenv("GPO_STUDIO_UNSAFE_BIND")
    with TestClient(app) as client:
        resp = client.post(
            "/api/gpos",
            json={"name": "Test", "actor": "a", "reason": "b"},
            headers={"Host": "localhost", "Origin": "ftp://localhost"},
        )
        assert resp.status_code == 403


def test_origin_validation_allows_http_localhost(
    tmp_path: Path, monkeypatch
) -> None:
    _setup_store(tmp_path)
    monkeypatch.delenv("GPO_STUDIO_UNSAFE_BIND")
    with TestClient(app) as client:
        resp = client.post(
            "/api/gpos",
            json={"name": "Test", "actor": "a", "reason": "b"},
            headers={"Host": "localhost", "Origin": "http://localhost:8765"},
        )
        assert resp.status_code == 201


def test_request_log_includes_guid_from_route_for_revisions_endpoint(
    tmp_path: Path, caplog
) -> None:
    _setup_store(tmp_path)
    caplog.set_level(logging.INFO)
    with TestClient(app) as client:
        create_resp = client.post(
            "/api/gpos",
            json={"name": "Test", "actor": "a", "reason": "b"},
        )
        guid = create_resp.json()["gpo"]["guid"]
        client.get(f"/api/gpos/{guid}/revisions")
    messages = [
        r.getMessage() for r in caplog.records if "request_id=" in r.getMessage()
    ]
    assert any(f"gpo_guid={guid}" in m for m in messages)
    assert any("operation=GET /api/gpos/{guid}/revisions" in m for m in messages)


def test_request_log_includes_guid_for_404_on_gpo_route(
    tmp_path: Path, caplog
) -> None:
    _setup_store(tmp_path)
    caplog.set_level(logging.INFO)
    nonexistent_guid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    with TestClient(app) as client:
        client.get(f"/api/gpos/{nonexistent_guid}")
    messages = [
        r.getMessage() for r in caplog.records if "request_id=" in r.getMessage()
    ]
    assert any(f"gpo_guid={nonexistent_guid}" in m for m in messages)


def test_request_log_includes_revision_from_route(
    tmp_path: Path, caplog
) -> None:
    _setup_store(tmp_path)
    caplog.set_level(logging.INFO)
    with TestClient(app) as client:
        create_resp = client.post(
            "/api/gpos",
            json={"name": "Test", "actor": "a", "reason": "b"},
        )
        guid = create_resp.json()["gpo"]["guid"]
        client.get(f"/api/gpos/{guid}/revisions/1")
    messages = [
        r.getMessage() for r in caplog.records if "request_id=" in r.getMessage()
    ]
    assert any("revision=" in m for m in messages)


def test_request_log_sanitizes_forged_route_params(
    tmp_path: Path, caplog
) -> None:
    _setup_store(tmp_path)
    caplog.set_level(logging.INFO)
    forged = "fake%20outcome=success%20revision=999"
    with TestClient(app) as client:
        client.get(f"/api/gpos/{forged}")
    messages = [
        r.getMessage() for r in caplog.records if "request_id=" in r.getMessage()
    ]
    assert messages, "Expected at least one request log"
    for msg in messages:
        assert "outcome=success" not in msg.split("gpo_guid=")[-1] if "gpo_guid=" in msg else True
        assert "revision=999" not in msg.split("gpo_guid=")[-1] if "gpo_guid=" in msg else True


def test_import_error_does_not_expose_policy_values(tmp_path: Path, monkeypatch) -> None:
    _setup_store(tmp_path)
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / "11111111-2222-3333-4444-555555555555"
    machine_dir = gpo_dir / "Machine"
    machine_dir.mkdir(parents=True)
    pol_data = bytearray(b"PReg\x01\x00\x00\x00")
    open_sep = "[".encode("utf-16le")
    sep = ";".encode("utf-16le")
    close = "]".encode("utf-16le")
    key = "Software\\Policies\\Test".encode("utf-16le")
    val_name = "TestValue".encode("utf-16le")
    raw_value = b"\x00\x00"
    pol_data.extend(open_sep + key + sep + val_name + sep)
    pol_data.extend((99).to_bytes(4, "little"))
    pol_data.extend(sep)
    pol_data.extend(len(raw_value).to_bytes(4, "little"))
    pol_data.extend(sep)
    pol_data.extend(raw_value)
    pol_data.extend(close)
    (machine_dir / "Registry.pol").write_bytes(bytes(pol_data))
    manifest = b"""<?xml version="1.0" encoding="utf-8"?>
<BackupInstances xmlns="http://www.microsoft.com/GroupPolicy/Types">
  <BackupInstance>
    <BackupTime>2026-01-01T00:00:00</BackupTime>
    <ID>backup-001</ID>
    <GPO>
      <Identifier>11111111-2222-3333-4444-555555555555</Identifier>
      <DisplayName>Synthetic Policy</DisplayName>
      <Domain>example.test</Domain>
      <MachineExtensionGuids>{35378EAC-683F-11D2-A89A-00C04FBBCFA2}</MachineExtensionGuids>
    </GPO>
  </BackupInstance>
</BackupInstances>"""
    (backup_dir / "manifest.xml").write_bytes(manifest)
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(tmp_path))

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.post("/api/backups/import", json={"path": str(backup_dir)})
        assert resp.status_code == 422
        msg = resp.json()["error"]["message"]
        assert "Software" not in msg
        assert "TestValue" not in msg
        assert "99" not in msg
        assert "policy data" in msg.lower()


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("synthetic-secret"),
        OSError("synthetic-secret"),
        BackupError("synthetic-secret"),
    ],
)
def test_import_unexpected_error_is_generic(
    tmp_path: Path, monkeypatch, error: Exception
) -> None:
    _setup_store(tmp_path)
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(tmp_path))

    def fail_read(_path: Path) -> None:
        raise error

    monkeypatch.setattr("gpo_studio.api.read_backup", fail_read)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.post("/api/backups/import", json={"path": str(backup_dir)})

    assert resp.status_code == 422
    message = resp.json()["error"]["message"]
    assert "synthetic-secret" not in message
    assert message in {
        "Backup import failed",
        "Filesystem error during backup import",
        "Invalid or unsafe backup content",
    }
