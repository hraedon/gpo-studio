from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.estate import parse_estate
from gpo_studio.model import ValidationError
from gpo_studio.store import WorkspaceStore

_VALID_ESTATE = {
    "kind": "gpo-lens-estate",
    "domain": "corp.example.test",
    "exported_at": "2026-07-12T10:00:00Z",
    "gpos": [
        {
            "guid": "11111111-2222-3333-4444-555555555555",
            "display_name": "Workstation Baseline",
            "description": "Standard workstation policy",
            "domain": "corp.example.test",
            "computer_enabled": True,
            "user_enabled": True,
            "settings": [
                {
                    "id": "s1",
                    "side": "computer",
                    "hive": "HKLM",
                    "key": r"Software\Policies\App",
                    "value_name": "Enabled",
                    "registry_type": "REG_DWORD",
                    "value": 1,
                    "action": "set",
                    "comment": "",
                }
            ],
            "links": [
                {
                    "id": "l1",
                    "target": "OU=Workstations,DC=corp,DC=example,DC=test",
                    "enabled": True,
                    "enforced": False,
                    "order": 1,
                }
            ],
            "security_filters": [
                {
                    "id": "sf1",
                    "principal": r"CORP\DomainAdmins",
                    "permission": "apply",
                    "inheritable": True,
                    "target_type": "group",
                }
            ],
            "wmi_filter": None,
        }
    ],
}


def _estate_with_wmi() -> dict:
    estate = {
        "kind": "gpo-lens-estate",
        "domain": "corp.example.test",
        "gpos": [
            {
                "guid": "22222222-3333-4444-5555-666666666666",
                "display_name": "Server Baseline",
                "domain": "corp.example.test",
                "settings": [],
                "links": [],
                "security_filters": [],
                "wmi_filter": {
                    "id": "wf1",
                    "name": "ServerFilter",
                    "description": "Lab servers",
                    "query": "SELECT * FROM Win32_OperatingSystem",
                    "language": "WQL",
                },
            }
        ],
    }
    return estate


def test_parse_estate_valid() -> None:
    gpos = parse_estate(_VALID_ESTATE)
    assert len(gpos) == 1
    gpo = gpos[0]
    assert gpo.guid == "11111111-2222-3333-4444-555555555555"
    assert gpo.name == "Workstation Baseline"
    assert gpo.status == "archived"
    assert gpo.source_guid == "11111111-2222-3333-4444-555555555555"
    assert gpo.domain == "corp.example.test"
    assert len(gpo.settings) == 1
    assert len(gpo.links) == 1
    assert len(gpo.security_filters) == 1


def test_parse_estate_invalid_kind() -> None:
    bad = dict(_VALID_ESTATE, kind="something-else")
    with pytest.raises(ValidationError) as exc_info:
        parse_estate(bad)
    assert exc_info.value.issues[0].code == "invalid_estate_kind"


def test_parse_estate_missing_kind() -> None:
    bad = {"domain": "corp.example.test", "gpos": []}
    with pytest.raises(ValidationError) as exc_info:
        parse_estate(bad)
    assert exc_info.value.issues[0].code == "invalid_estate_kind"


def test_parse_estate_empty_gpos() -> None:
    estate = {"kind": "gpo-lens-estate", "domain": "corp.example.test", "gpos": []}
    gpos = parse_estate(estate)
    assert gpos == []


def test_parse_estate_validation_error() -> None:
    bad = {
        "kind": "gpo-lens-estate",
        "domain": "corp.example.test",
        "gpos": [
            {
                "guid": "22222222-3333-4444-5555-666666666666",
                "display_name": "Bad policy",
                "domain": "corp.example.test",
                "settings": [
                    {
                        "id": "s1",
                        "side": "user",
                        "hive": "HKLM",
                        "key": r"Software\Policies\App",
                        "value_name": "Enabled",
                        "registry_type": "REG_DWORD",
                        "value": 1,
                        "action": "set",
                        "comment": "",
                    }
                ],
            }
        ],
    }
    with pytest.raises(ValidationError) as exc_info:
        parse_estate(bad)
    codes = [i.code for i in exc_info.value.issues]
    assert "side_hive_mismatch" in codes


def test_parse_estate_with_wmi_filter() -> None:
    gpos = parse_estate(_estate_with_wmi())
    assert len(gpos) == 1
    assert gpos[0].wmi_filter is not None
    assert gpos[0].wmi_filter.name == "ServerFilter"


def test_import_baseline_gpos(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "estate.db")
    gpos = parse_estate(_VALID_ESTATE)
    summary = store.import_baseline_gpos(gpos, identity="tester", reason="import baseline")
    assert summary == {
        "imported": 1,
        "skipped": 0,
        "conflicts": 0,
        "rejected": 0,
        "total": 1,
    }
    fetched = store.get_gpo("11111111-2222-3333-4444-555555555555")
    assert fetched.name == "Workstation Baseline"
    assert fetched.status == "archived"
    assert fetched.source_guid == "11111111-2222-3333-4444-555555555555"
    assert fetched.revision == 1


def test_import_baseline_gpos_skips_existing(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "estate.db")
    gpos = parse_estate(_VALID_ESTATE)
    store.import_baseline_gpos(gpos, identity="tester", reason="first import")
    summary = store.import_baseline_gpos(gpos, identity="tester", reason="second import")
    assert summary == {
        "imported": 0,
        "skipped": 1,
        "conflicts": 0,
        "rejected": 0,
        "total": 1,
    }


def test_import_baseline_gpos_empty(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "estate.db")
    summary = store.import_baseline_gpos([], identity="tester", reason="empty import")
    assert summary == {
        "imported": 0,
        "skipped": 0,
        "conflicts": 0,
        "rejected": 0,
        "total": 0,
    }


def test_import_baseline_gpos_creates_revision(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "estate.db")
    gpos = parse_estate(_VALID_ESTATE)
    store.import_baseline_gpos(gpos, identity="tester", reason="import baseline")
    revisions = store.revisions("11111111-2222-3333-4444-555555555555")
    assert len(revisions) == 1
    assert revisions[0].actor == "tester"
    assert revisions[0].reason == "import baseline"


def test_import_baseline_gpos_name_collision_counts_as_conflict(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "estate.db")
    store.create_gpo(
        "Workstation Baseline",
        "pre-existing",
        identity="tester",
        reason="seed",
    )
    estate = {
        "kind": "gpo-lens-estate",
        "domain": "corp.example.test",
        "gpos": [
            {
                "guid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "display_name": "Workstation Baseline",
                "domain": "corp.example.test",
                "settings": [],
                "links": [],
                "security_filters": [],
                "wmi_filter": None,
            },
            {
                "guid": "ffffffff-eeee-dddd-cccc-bbbbbbbbbbbb",
                "display_name": "Server Baseline",
                "domain": "corp.example.test",
                "settings": [],
                "links": [],
                "security_filters": [],
                "wmi_filter": None,
            },
        ],
    }
    gpos = parse_estate(estate)
    summary = store.import_baseline_gpos(
        gpos, identity="tester", reason="import with collision"
    )
    assert summary["conflicts"] == 1
    assert summary["imported"] == 1
    assert summary["skipped"] == 0
    assert summary["total"] == 2


def test_fork_gpo(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "estate.db")
    gpos = parse_estate(_VALID_ESTATE)
    store.import_baseline_gpos(gpos, identity="tester", reason="import baseline")
    forked = store.fork_gpo(
        "11111111-2222-3333-4444-555555555555",
        "Forked workstation policy",
        identity="tester",
        reason="fork for editing",
    )
    assert forked.guid != "11111111-2222-3333-4444-555555555555"
    assert forked.name == "Forked workstation policy"
    assert forked.status == "draft"
    assert forked.source_guid == "11111111-2222-3333-4444-555555555555"
    assert forked.domain == "corp.example.test"
    assert len(forked.settings) == 1
    assert forked.settings[0].id == "forked-s1"
    assert forked.settings[0].key == r"Software\Policies\App"
    assert len(forked.links) == 1
    assert forked.links[0].id == "forked-l1"
    assert len(forked.security_filters) == 1
    assert forked.security_filters[0].id == "forked-sf1"
    assert forked.cse_metadata == ()


def test_fork_gpo_with_wmi_filter(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "estate.db")
    gpos = parse_estate(_estate_with_wmi())
    store.import_baseline_gpos(gpos, identity="tester", reason="import")
    forked = store.fork_gpo(
        "22222222-3333-4444-5555-666666666666",
        "Forked with WMI",
        identity="tester",
        reason="fork",
    )
    assert forked.wmi_filter is not None
    assert forked.wmi_filter.id == "forked-wf1"
    assert forked.wmi_filter.name == "ServerFilter"


def test_cse_metadata_round_trip_through_parse_and_fork(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "estate.db")
    estate = {
        "kind": "gpo-lens-estate",
        "domain": "corp.example.test",
        "gpos": [
            {
                "guid": "33333333-4444-5555-6666-777777777777",
                "display_name": "CSE Policy",
                "domain": "corp.example.test",
                "settings": [],
                "links": [],
                "security_filters": [],
                "wmi_filter": None,
                "cse_metadata": [
                    {
                        "guid": "{35378EAC-683F-11D2-A89E-00C04FBBCFA2}",
                        "side": "machine",
                        "files": [
                            {
                                "relative_path": "Machine/Preferences/Settings/settings.xml",
                                "content_hash": "abc123",
                                "size": 1024,
                            }
                        ],
                    },
                    {
                        "guid": "{3265B299-5C44-49DD-B83D-9A79A9F9B9D2}",
                        "side": "user",
                        "files": [],
                    },
                ],
            }
        ],
    }
    gpos = parse_estate(estate)
    assert len(gpos) == 1
    gpo = gpos[0]
    assert len(gpo.cse_metadata) == 2
    assert gpo.cse_metadata[0].guid == "{35378EAC-683F-11D2-A89E-00C04FBBCFA2}"
    assert gpo.cse_metadata[0].side == "machine"
    assert len(gpo.cse_metadata[0].files) == 1
    assert gpo.cse_metadata[0].files[0].relative_path == "Machine/Preferences/Settings/settings.xml"
    assert gpo.cse_metadata[1].guid == "{3265B299-5C44-49DD-B83D-9A79A9F9B9D2}"
    assert gpo.cse_metadata[1].side == "user"

    store.import_baseline_gpos(gpos, identity="tester", reason="import")
    fetched = store.get_gpo("33333333-4444-5555-6666-777777777777")
    assert len(fetched.cse_metadata) == 2
    assert fetched.cse_metadata[0].guid == "{35378EAC-683F-11D2-A89E-00C04FBBCFA2}"
    assert fetched.cse_metadata[0].side == "machine"

    forked = store.fork_gpo(
        "33333333-4444-5555-6666-777777777777",
        "Forked CSE Policy",
        identity="tester",
        reason="fork for editing",
    )
    assert len(forked.cse_metadata) == 2
    assert forked.cse_metadata[0].guid == "{35378EAC-683F-11D2-A89E-00C04FBBCFA2}"
    assert forked.cse_metadata[0].side == "machine"
    assert len(forked.cse_metadata[0].files) == 1
    assert (
        forked.cse_metadata[0].files[0].relative_path
        == "Machine/Preferences/Settings/settings.xml"
    )
    assert forked.cse_metadata[1].guid == "{3265B299-5C44-49DD-B83D-9A79A9F9B9D2}"
    assert forked.cse_metadata[1].side == "user"


def test_fork_gpo_nonexistent(tmp_path) -> None:
    from gpo_studio.model import NotFoundError
    store = WorkspaceStore(tmp_path / "estate.db")
    with pytest.raises(NotFoundError):
        store.fork_gpo("nonexistent", "Fork", identity="tester", reason="fork")


def test_estate_import_api(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        resp = client.post("/api/estate/import", json=_VALID_ESTATE)
        assert resp.status_code == 200
        summary = resp.json()
        assert summary["imported"] == 1
        assert summary["skipped"] == 0
        assert summary["total"] == 1
        gpos = client.get("/api/gpos").json()["items"]
        assert any(g["guid"] == "11111111-2222-3333-4444-555555555555" for g in gpos)
        fetched = client.get("/api/gpos/11111111-2222-3333-4444-555555555555").json()["gpo"]
        assert fetched["status"] == "archived"


def test_estate_import_api_skip_existing(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        client.post("/api/estate/import", json=_VALID_ESTATE)
        resp = client.post("/api/estate/import", json=_VALID_ESTATE)
        assert resp.status_code == 200
        summary = resp.json()
        assert summary["imported"] == 0
        assert summary["skipped"] == 1


def test_estate_import_api_invalid_kind(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        resp = client.post("/api/estate/import", json={"kind": "wrong", "gpos": []})
        assert resp.status_code == 422
        issues = resp.json()["error"]["issues"]
        assert issues[0]["code"] == "invalid_estate_kind"


def test_estate_import_api_validation_error(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    bad_estate = {
        "kind": "gpo-lens-estate",
        "domain": "corp.example.test",
        "gpos": [
            {
                "guid": "33333333-4444-5555-6666-777777777777",
                "display_name": "Bad",
                "domain": "corp.example.test",
                "settings": [
                    {
                        "id": "s1",
                        "side": "user",
                        "hive": "HKLM",
                        "key": r"Software\Policies\Bad",
                        "value_name": "X",
                        "registry_type": "REG_DWORD",
                        "value": 1,
                        "action": "set",
                        "comment": "",
                    }
                ],
            }
        ],
    }
    with TestClient(app) as client:
        resp = client.post("/api/estate/import", json=bad_estate)
        assert resp.status_code == 422
        issues = resp.json()["error"]["issues"]
        assert any(i["code"] == "side_hive_mismatch" for i in issues)


def test_fork_api(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        client.post("/api/estate/import", json=_VALID_ESTATE)
        resp = client.post(
            "/api/gpos/11111111-2222-3333-4444-555555555555/fork",
            json={"name": "Forked policy", "actor": "tester", "reason": "fork"},
        )
        assert resp.status_code == 201
        data = resp.json()
        gpo = data["gpo"]
        assert gpo["name"] == "Forked policy"
        assert gpo["status"] == "draft"
        assert gpo["source_guid"] == "11111111-2222-3333-4444-555555555555"
        assert len(gpo["settings"]) == 1
        assert gpo["settings"][0]["id"] == "forked-s1"
        assert gpo["settings"][0]["key"] == r"Software\Policies\App"
        assert len(gpo["links"]) == 1
        assert gpo["links"][0]["id"] == "forked-l1"
        assert len(gpo["security_filters"]) == 1
        assert gpo["security_filters"][0]["id"] == "forked-sf1"
        assert gpo["cse_metadata"] == []


def test_fork_api_nonexistent(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        resp = client.post(
            "/api/gpos/nonexistent-guid/fork",
            json={"name": "Fork", "actor": "tester", "reason": "fork"},
        )
        assert resp.status_code == 404


def test_estate_diff_api(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        client.post("/api/estate/import", json=_VALID_ESTATE)
        forked = client.post(
            "/api/gpos/11111111-2222-3333-4444-555555555555/fork",
            json={"name": "Draft fork", "actor": "tester", "reason": "fork"},
        ).json()["gpo"]
        resp = client.post(
            "/api/estate/diff",
            json={
                "baseline_guid": "11111111-2222-3333-4444-555555555555",
                "draft_guid": forked["guid"],
                "observed_guid": "11111111-2222-3333-4444-555555555555",
            },
        )
        assert resp.status_code == 200
        result = resp.json()
        assert "settings" in result
        assert "conflicts" in result
        assert result["settings"] == []
        assert result["conflicts"] == []


def test_estate_diff_api_missing_guid(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        resp = client.post(
            "/api/estate/diff",
            json={
                "baseline_guid": "nonexistent",
                "draft_guid": "nonexistent",
                "observed_guid": "nonexistent",
            },
        )
        assert resp.status_code == 404


def test_estate_diff_api_detects_changes(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        client.post("/api/estate/import", json=_VALID_ESTATE)
        forked = client.post(
            "/api/gpos/11111111-2222-3333-4444-555555555555/fork",
            json={"name": "Modified fork", "actor": "tester", "reason": "fork"},
        ).json()["gpo"]
        client.post(
            f"/api/gpos/{forked['guid']}/settings",
            json={
                "expected_revision": forked["revision"],
                "reason": "add setting",
                "setting": {
                    "side": "computer",
                    "hive": "HKLM",
                    "key": r"Software\Policies\New",
                    "value_name": "SettingB",
                    "registry_type": "REG_DWORD",
                    "value": 1,
                },
            },
        )
        resp = client.post(
            "/api/estate/diff",
            json={
                "baseline_guid": "11111111-2222-3333-4444-555555555555",
                "draft_guid": forked["guid"],
                "observed_guid": "11111111-2222-3333-4444-555555555555",
            },
        )
        assert resp.status_code == 200
        result = resp.json()
        assert len(result["settings"]) == 1
        assert result["settings"][0]["kind"] == "added"
