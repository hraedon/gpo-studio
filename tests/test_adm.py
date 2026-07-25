from __future__ import annotations

from fastapi.testclient import TestClient

from gpo_studio.adm import parse_adm
from gpo_studio.api import app
from gpo_studio.store import WorkspaceStore


def test_parse_machine_numeric_part() -> None:
    data = '''CLASS MACHINE
CATEGORY "Windows Components"
    POLICY "Test Policy"
        KEYNAME "Software\\Policies\\Test"
        VALUENAME "TestValue"
        PART "Setting value" NUMERIC
            DEFAULT 1
        END PART
    END POLICY
END CATEGORY
END CLASS'''
    settings, warnings = parse_adm(data)
    assert len(settings) == 1
    s = settings[0]
    assert s.id.startswith("legacy-adm-")
    assert s.side == "computer"
    assert s.hive == "HKLM"
    assert s.key == "Software\\Policies\\Test"
    assert s.value_name == "TestValue"
    assert s.registry_type == "REG_DWORD"
    assert s.value == 1
    assert s.action == "set"
    assert warnings == []


def test_parse_valueon_valueoff() -> None:
    data = '''CLASS MACHINE
POLICY "EnableFeature"
    KEYNAME "Software\\Policies\\Feature"
    VALUENAME "Enabled"
    VALUEON NUMERIC 1
    VALUEOFF NUMERIC 0
END POLICY
END CLASS'''
    settings, warnings = parse_adm(data)
    assert len(settings) == 1
    s = settings[0]
    assert s.value == 1
    assert s.registry_type == "REG_DWORD"
    assert warnings == []


def test_parse_user_policy() -> None:
    data = '''CLASS USER
POLICY "User Setting"
    KEYNAME "Software\\Policies\\UserSetting"
    VALUENAME "UserValue"
    PART "Label" TEXT
        DEFAULT "hello"
    END PART
END POLICY
END CLASS'''
    settings, warnings = parse_adm(data)
    assert len(settings) == 1
    s = settings[0]
    assert s.side == "user"
    assert s.hive == "HKCU"
    assert s.registry_type == "REG_SZ"
    assert s.value == "hello"
    assert warnings == []


def test_parse_actionliston_actionlistoff() -> None:
    data = '''CLASS MACHINE
POLICY "Action Policy"
    KEYNAME "Software\\Policies\\Action"
    ACTIONLISTON
        KEYNAME "Software\\Policies\\Action"
        VALUENAME "Enabled"
        VALUE NUMERIC 1
    END ACTIONLIST
    ACTIONLISTOFF
        KEYNAME "Software\\Policies\\Action"
        VALUENAME "Enabled"
        VALUE NUMERIC 0
    END ACTIONLIST
END POLICY
END CLASS'''
    settings, warnings = parse_adm(data)
    assert len(settings) == 1
    s = settings[0]
    assert s.value == 1
    assert s.registry_type == "REG_DWORD"
    assert warnings == []


def test_parse_missing_keyname() -> None:
    data = '''CLASS MACHINE
POLICY "Bad"
    VALUENAME "X"
    VALUEON NUMERIC 1
END POLICY'''
    settings, warnings = parse_adm(data)
    assert settings == []
    assert any("missing KEYNAME" in w for w in warnings)


def test_parse_unclosed_policy() -> None:
    data = '''CLASS MACHINE
POLICY "Unclosed"
    KEYNAME "Software\\Policies\\X"
    VALUENAME "Y"
    VALUEON NUMERIC 1'''
    settings, warnings = parse_adm(data)
    assert len(settings) == 1
    assert settings[0].value == 1
    assert any("unclosed policy" in w.lower() for w in warnings)


def test_parse_empty_input() -> None:
    settings, warnings = parse_adm("")
    assert settings == []
    assert warnings == []


def test_api_import_adm_success(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "adm.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "ADM import test"}).json()["gpo"]
        adm = '''CLASS MACHINE
    POLICY "Test"
        KEYNAME "Software\\Policies\\Test"
        VALUENAME "Value"
        VALUEON NUMERIC 1
        VALUEOFF NUMERIC 0
    END POLICY
END CLASS'''
        response = client.post(
            f"/api/gpos/{gpo['guid']}/import-adm",
            json={
                "adm_content": adm,
                "actor": "tester",
                "reason": "import adm",
                "expected_revision": gpo["revision"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["gpo"]["settings"]) == 1
        assert data["warnings"] == []
        assert data["gpo"]["revision"] == gpo["revision"] + 1


def test_api_import_adm_stale_revision(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "adm.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "Stale ADM"}).json()["gpo"]
        adm = '''CLASS MACHINE
POLICY "Test"
    KEYNAME "Software\\Policies\\Test"
    VALUENAME "Value"
    VALUEON NUMERIC 1
END POLICY'''
        response = client.post(
            f"/api/gpos/{gpo['guid']}/import-adm",
            json={
                "adm_content": adm,
                "actor": "tester",
                "reason": "import adm",
                "expected_revision": 999,
            },
        )
        assert response.status_code == 409


def test_api_import_adm_invalid(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "adm.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        gpo = client.post("/api/gpos", json={"name": "Invalid ADM"}).json()["gpo"]
        adm = '''CLASS MACHINE
POLICY "Bad"
    VALUENAME "Value"
    VALUEON NUMERIC 1
END POLICY'''
        response = client.post(
            f"/api/gpos/{gpo['guid']}/import-adm",
            json={
                "adm_content": adm,
                "actor": "tester",
                "reason": "import adm",
                "expected_revision": gpo["revision"],
            },
        )
        assert response.status_code == 422


def test_api_import_adm_not_found(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "adm.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        adm = '''CLASS MACHINE
POLICY "Test"
    KEYNAME "Software\\Policies\\Test"
    VALUENAME "Value"
    VALUEON NUMERIC 1
END POLICY'''
        response = client.post(
            "/api/gpos/00000000-0000-0000-0000-000000000000/import-adm",
            json={
                "adm_content": adm,
                "actor": "tester",
                "reason": "import adm",
                "expected_revision": 1,
            },
        )
        assert response.status_code == 404
