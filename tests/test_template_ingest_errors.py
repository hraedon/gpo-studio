"""Filesystem failures during template setup remain actionable and non-destructive."""

from __future__ import annotations

import errno
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.store import WorkspaceStore


@pytest.fixture
def template_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    store = WorkspaceStore(tmp_path / "workspace.db")
    monkeypatch.setattr(app.state, "store", store, raising=False)
    monkeypatch.setattr(app.state, "owns_store", False, raising=False)
    monkeypatch.setenv("GPO_STUDIO_ADMX_DIR", str(Path(__file__).parent / "fixtures" / "admx"))
    monkeypatch.delenv("GPO_STUDIO_TEMPLATE_ROOT", raising=False)
    for name in ("admx_catalogue", "template_sources"):
        monkeypatch.delattr(app.state, name, raising=False)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        store.close()
        for name in ("admx_catalogue", "template_sources"):
            if hasattr(app.state, name):
                delattr(app.state, name)


@pytest.mark.parametrize(
    "failure",
    [PermissionError(errno.EACCES, "permission denied"), OSError(errno.EIO, "I/O failure")],
    ids=["permission-denied", "filesystem-error"],
)
@pytest.mark.parametrize(
    "folder_name",
    ["existing-unreadable", r"Synthetic\PolicyDefinitions"],
    ids=["plain-path", "backslash-path"],
)
def test_unreadable_template_folder_keeps_previous_catalogue(
    template_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: OSError,
    folder_name: str,
) -> None:
    folder = tmp_path / folder_name
    folder.mkdir(parents=True)
    folder = folder.resolve()
    sources = app.state.template_sources
    catalogue = app.state.admx_catalogue
    assert catalogue.policies
    before_sources = template_client.get("/api/templates").json()
    before_policies = template_client.get("/api/admx/search").json()
    original_iterdir = Path.iterdir

    def inaccessible_iterdir(path: Path) -> Iterator[Path]:
        if path == folder:
            raise failure
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", inaccessible_iterdir)
    response = template_client.post(
        "/api/templates/ingest",
        json={"name": sources[0].source.name, "kind": "local", "path": str(folder)},
    )

    assert response.status_code == 422
    message = response.json()["error"]["message"]
    assert "Cannot read ADMX folder" in message
    assert str(folder) in message
    assert "permission to read" in message
    assert app.state.template_sources is sources
    assert app.state.admx_catalogue is catalogue
    assert template_client.get("/api/templates").json() == before_sources
    assert template_client.get("/api/admx/search").json() == before_policies


def test_missing_template_folder_still_reports_existing_validation_error(
    template_client: TestClient, tmp_path: Path
) -> None:
    missing = tmp_path / "not-created"
    sources = app.state.template_sources
    catalogue = app.state.admx_catalogue
    response = template_client.post(
        "/api/templates/ingest",
        json={"name": "missing", "kind": "local", "path": str(missing)},
    )

    assert response.status_code == 422
    assert response.json()["error"]["message"] == f"Template directory does not exist: {missing}"
    assert app.state.template_sources is sources
    assert app.state.admx_catalogue is catalogue
