from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.store import WorkspaceStore
from gpo_studio.wmi_catalogue import (
    WmiCatalogue,
    WmiCatalogueError,
    WmiFilterEntry,
    load_wmi_catalogue,
)


def test_load_valid_catalogue(tmp_path: Path) -> None:
    catalogue_path = tmp_path / "wmi.json"
    catalogue_path.write_text(
        json.dumps(
            {
                "filters": [
                    {
                        "id": "workstations",
                        "name": "Workstations only",
                        "query": "SELECT * FROM Win32_OperatingSystem WHERE ProductType = 1",
                        "language": "WQL",
                        "description": "Matches workstation OS",
                    },
                    {
                        "id": "domain-controllers",
                        "name": "Domain Controllers",
                        "query": "SELECT * FROM Win32_ComputerSystem WHERE DomainRole = 5",
                    },
                ]
            }
        )
    )
    catalogue = load_wmi_catalogue(catalogue_path)
    assert len(catalogue.filters) == 2
    assert catalogue.filters[0].id == "workstations"
    assert catalogue.filters[0].name == "Workstations only"
    assert catalogue.filters[0].language == "WQL"
    assert catalogue.filters[0].description == "Matches workstation OS"
    assert catalogue.filters[1].language == "WQL"
    assert catalogue.filters[1].description == ""


def test_load_catalogue_empty_filters(tmp_path: Path) -> None:
    catalogue_path = tmp_path / "wmi.json"
    catalogue_path.write_text(json.dumps({"filters": []}))
    catalogue = load_wmi_catalogue(catalogue_path)
    assert len(catalogue.filters) == 0


def test_load_catalogue_missing_filters_key(tmp_path: Path) -> None:
    catalogue_path = tmp_path / "wmi.json"
    catalogue_path.write_text(json.dumps({}))
    catalogue = load_wmi_catalogue(catalogue_path)
    assert len(catalogue.filters) == 0


def test_load_catalogue_missing_id_raises(tmp_path: Path) -> None:
    catalogue_path = tmp_path / "wmi.json"
    catalogue_path.write_text(
        json.dumps({"filters": [{"name": "No ID", "query": "SELECT 1"}]})
    )
    with pytest.raises(WmiCatalogueError, match="non-empty 'id'"):
        load_wmi_catalogue(catalogue_path)


def test_load_catalogue_empty_id_raises(tmp_path: Path) -> None:
    catalogue_path = tmp_path / "wmi.json"
    catalogue_path.write_text(
        json.dumps({"filters": [{"id": "  ", "name": "Empty ID"}]})
    )
    with pytest.raises(WmiCatalogueError, match="non-empty 'id'"):
        load_wmi_catalogue(catalogue_path)


def test_load_catalogue_missing_name_raises(tmp_path: Path) -> None:
    catalogue_path = tmp_path / "wmi.json"
    catalogue_path.write_text(
        json.dumps({"filters": [{"id": "filter-1", "query": "SELECT 1"}]})
    )
    with pytest.raises(WmiCatalogueError, match="non-empty 'name'"):
        load_wmi_catalogue(catalogue_path)


def test_load_catalogue_nonexistent_file(tmp_path: Path) -> None:
    with pytest.raises(WmiCatalogueError, match="not found"):
        load_wmi_catalogue(tmp_path / "nonexistent.json")


def test_load_catalogue_invalid_json(tmp_path: Path) -> None:
    catalogue_path = tmp_path / "wmi.json"
    catalogue_path.write_text("{not valid json}")
    with pytest.raises(WmiCatalogueError, match="Invalid JSON"):
        load_wmi_catalogue(catalogue_path)


def test_load_catalogue_filters_not_list(tmp_path: Path) -> None:
    catalogue_path = tmp_path / "wmi.json"
    catalogue_path.write_text(json.dumps({"filters": "not a list"}))
    with pytest.raises(WmiCatalogueError, match="must be a list"):
        load_wmi_catalogue(catalogue_path)


def test_load_catalogue_root_not_object(tmp_path: Path) -> None:
    catalogue_path = tmp_path / "wmi.json"
    catalogue_path.write_text(json.dumps(["not", "an", "object"]))
    with pytest.raises(WmiCatalogueError, match="root must be a JSON object"):
        load_wmi_catalogue(catalogue_path)


def test_load_catalogue_filter_not_object(tmp_path: Path) -> None:
    catalogue_path = tmp_path / "wmi.json"
    catalogue_path.write_text(json.dumps({"filters": ["not an object"]}))
    with pytest.raises(WmiCatalogueError, match="must be a JSON object"):
        load_wmi_catalogue(catalogue_path)


def test_load_catalogue_rejects_symlink(tmp_path: Path) -> None:
    real_file = tmp_path / "real.json"
    real_file.write_text(json.dumps({"filters": []}))
    symlink_file = tmp_path / "link.json"
    symlink_file.symlink_to(real_file)
    with pytest.raises(WmiCatalogueError, match="symlink"):
        load_wmi_catalogue(symlink_file)


def test_load_catalogue_rejects_oversized_file(tmp_path: Path) -> None:
    catalogue_path = tmp_path / "wmi.json"
    catalogue_path.write_bytes(b"x" * (50 * 1024 * 1024 + 1))
    with pytest.raises(WmiCatalogueError, match="exceeds"):
        load_wmi_catalogue(catalogue_path)


def test_wmi_filter_entry_defaults() -> None:
    entry = WmiFilterEntry(id="test", name="Test")
    assert entry.query == ""
    assert entry.language == "WQL"
    assert entry.description == ""


def test_wmi_catalogue_default() -> None:
    catalogue = WmiCatalogue()
    assert catalogue.filters == ()


def test_load_catalogue_null_fields(tmp_path: Path) -> None:
    cat_path = tmp_path / "wmi.json"
    cat_path.write_text(
        json.dumps(
            {
                "filters": [
                    {
                        "id": "f1",
                        "name": "Filter 1",
                        "query": None,
                        "language": None,
                        "description": None,
                    }
                ]
            }
        )
    )
    cat = load_wmi_catalogue(cat_path)
    assert cat.filters[0].query == ""
    assert cat.filters[0].language == "WQL"
    assert cat.filters[0].description == ""


def test_load_catalogue_duplicate_id(tmp_path: Path) -> None:
    cat_path = tmp_path / "wmi.json"
    cat_path.write_text(
        json.dumps(
            {"filters": [{"id": "f1", "name": "A"}, {"id": "f1", "name": "B"}]}
        )
    )
    with pytest.raises(WmiCatalogueError, match="Duplicate filter id"):
        load_wmi_catalogue(cat_path)


def _setup_wmi_catalogue(
    tmp_path: Path, monkeypatch, catalogue_name: str = "wmi.json"
) -> Path:
    catalogue_path = tmp_path / catalogue_name
    catalogue_path.write_text(
        json.dumps(
            {
                "filters": [
                    {
                        "id": "laptops",
                        "name": "Laptop devices",
                        "query": "SELECT * FROM Win32_Battery",
                        "language": "WQL",
                        "description": "Matches laptops with batteries",
                    },
                    {
                        "id": "servers",
                        "name": "Server OS",
                        "query": "SELECT * FROM Win32_OperatingSystem WHERE ProductType = 3",
                    },
                ]
            }
        )
    )
    monkeypatch.setenv("GPO_STUDIO_WMI_CATALOGUE", str(catalogue_path))
    if hasattr(app.state, "wmi_catalogue"):
        del app.state.wmi_catalogue
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    return catalogue_path


def test_api_list_wmi_filters(tmp_path: Path, monkeypatch) -> None:
    _setup_wmi_catalogue(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/api/wmi-filters")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert data["items"][0]["id"] == "laptops"
        assert data["items"][0]["name"] == "Laptop devices"
        assert data["items"][0]["query"] == "SELECT * FROM Win32_Battery"
        assert data["items"][0]["description"] == "Matches laptops with batteries"


def test_api_get_wmi_filter(tmp_path: Path, monkeypatch) -> None:
    _setup_wmi_catalogue(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/api/wmi-filters/laptops")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "laptops"
        assert data["name"] == "Laptop devices"
        assert data["query"] == "SELECT * FROM Win32_Battery"


def test_api_get_wmi_filter_not_found(tmp_path: Path, monkeypatch) -> None:
    _setup_wmi_catalogue(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/api/wmi-filters/nonexistent")
        assert resp.status_code == 404


def test_api_health_includes_wmi_catalogue_loaded(tmp_path: Path, monkeypatch) -> None:
    _setup_wmi_catalogue(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["wmi_catalogue_loaded"] is True


def test_api_health_wmi_catalogue_not_loaded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GPO_STUDIO_WMI_CATALOGUE", raising=False)
    if hasattr(app.state, "wmi_catalogue"):
        del app.state.wmi_catalogue
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["wmi_catalogue_loaded"] is False
        list_resp = client.get("/api/wmi-filters")
        assert list_resp.status_code == 200
        assert list_resp.json()["count"] == 0


def test_api_wmi_filters_empty_when_no_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GPO_STUDIO_WMI_CATALOGUE", raising=False)
    if hasattr(app.state, "wmi_catalogue"):
        del app.state.wmi_catalogue
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        resp = client.get("/api/wmi-filters")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


def test_catalogue_rejects_symlinked_parent_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "wmi.json").write_text(json.dumps({"filters": []}))
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(outside, target_is_directory=True)

    with pytest.raises(WmiCatalogueError, match="symlink or inaccessible"):
        load_wmi_catalogue(linked_parent / "wmi.json")
