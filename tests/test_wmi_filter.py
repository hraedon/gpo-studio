from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.model import ValidationError
from gpo_studio.store import WorkspaceStore
from gpo_studio.wmi_filter import (
    LoopbackConfig,
    WmiFilterDetail,
    check_filter_deletion_safe,
    describe_loopback,
    lint_wql,
    parse_multi_query,
    serialize_multi_query,
    validate_loopback_config,
)

VALID_QUERY = (
    "SELECT Name FROM Win32_OperatingSystem WHERE ProductType = 1"
)


def test_lint_valid_query() -> None:
    issues = lint_wql(VALID_QUERY)
    assert issues == ()


def test_lint_empty_query() -> None:
    issues = lint_wql("   ")
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert issues[0].code == "empty_query"


def test_lint_unbalanced_parens() -> None:
    issues = lint_wql("SELECT * FROM Win32_Process WHERE (HandleCount > 10")
    assert any(i.code == "unbalanced_parens" for i in issues)
    assert any(i.severity == "error" for i in issues)


def test_lint_unbalanced_parens_closing_extra() -> None:
    issues = lint_wql("SELECT * FROM Win32_Process WHERE HandleCount > 10)")
    assert any(i.code == "unbalanced_parens" for i in issues)


def test_lint_missing_from() -> None:
    issues = lint_wql("SELECT * WHERE ProductType = 1")
    codes = {i.code for i in issues}
    assert "missing_from" in codes
    assert all(i.severity == "error" for i in issues if i.code == "missing_from")


def test_lint_missing_where() -> None:
    issues = lint_wql("SELECT * FROM Win32_OperatingSystem")
    assert any(i.code == "missing_where" for i in issues)
    assert any(i.severity == "warning" for i in issues)


def test_lint_select_star() -> None:
    issues = lint_wql("SELECT * FROM Win32_OperatingSystem WHERE ProductType = 1")
    assert any(i.code == "select_star" for i in issues)
    assert any(i.severity == "warning" for i in issues)


def test_lint_select_explicit_no_star() -> None:
    issues = lint_wql(
        "SELECT Name FROM Win32_OperatingSystem WHERE ProductType = 1"
    )
    assert not any(i.code == "select_star" for i in issues)


def test_lint_unterminated_string() -> None:
    issues = lint_wql('SELECT * FROM Win32_Service WHERE Name = "foo')
    assert any(i.code == "unterminated_string" for i in issues)
    assert any(i.severity == "error" for i in issues)


def test_lint_invalid_control_chars() -> None:
    issues = lint_wql("SELECT * FROM Win32_Process WHERE Name = \x00foo")
    assert any(i.code == "invalid_chars" for i in issues)
    assert any(i.severity == "error" for i in issues)


def test_lint_query_too_long() -> None:
    issues = lint_wql("SELECT * FROM Win32_Process WHERE " + "x" * 4100)
    assert any(i.code == "query_too_long" for i in issues)
    assert any(i.severity == "error" for i in issues)


def test_lint_respects_string_literals() -> None:
    # Semicolon, FROM, WHERE, and SELECT * inside a literal must not trigger.
    query = (
        'SELECT Name FROM Win32_OperatingSystem WHERE '
        'Caption = "SELECT * FROM FOO;BAR"'
    )
    issues = lint_wql(query)
    assert not any(i.code == "select_star" for i in issues)
    assert not any(i.code == "missing_from" for i in issues)
    assert not any(i.code == "missing_where" for i in issues)


def test_lint_terminated_string_at_end() -> None:
    issues = lint_wql('SELECT Name FROM Win32_Service WHERE Name = "foo"')
    assert not any(i.code == "unterminated_string" for i in issues)


def test_parse_multi_query_single() -> None:
    groups = parse_multi_query(VALID_QUERY)
    assert groups == ((VALID_QUERY,),)


def test_parse_multi_query_multiple() -> None:
    raw = (
        "SELECT * FROM Win32_Battery; "
        "SELECT * FROM Win32_OperatingSystem WHERE ProductType = 1"
    )
    groups = parse_multi_query(raw)
    assert len(groups) == 2
    assert groups[0] == ("SELECT * FROM Win32_Battery",)
    assert groups[1] == ("SELECT * FROM Win32_OperatingSystem WHERE ProductType = 1",)


def test_parse_multi_query_respects_string_literals() -> None:
    raw = 'SELECT * FROM Win32_Service WHERE Name = "a;b"; SELECT * FROM Win32_Process'
    groups = parse_multi_query(raw)
    assert len(groups) == 2
    assert groups[0] == ('SELECT * FROM Win32_Service WHERE Name = "a;b"',)


def test_parse_multi_query_ignores_empty() -> None:
    groups = parse_multi_query("SELECT 1;;SELECT 2;")
    assert groups == (("SELECT 1",), ("SELECT 2",))


def test_serialize_multi_query() -> None:
    groups: tuple[tuple[str, ...], ...] = (
        ("SELECT * FROM Win32_Battery",),
        ("SELECT * FROM Win32_OperatingSystem WHERE ProductType = 1",),
    )
    serialized = serialize_multi_query(groups)
    assert serialized == (
        "SELECT * FROM Win32_Battery; "
        "SELECT * FROM Win32_OperatingSystem WHERE ProductType = 1"
    )


def test_multi_query_round_trip() -> None:
    raw = (
        "SELECT * FROM Win32_Battery; "
        "SELECT * FROM Win32_OperatingSystem WHERE ProductType = 1"
    )
    groups = parse_multi_query(raw)
    serialized = serialize_multi_query(groups)
    reparsed = parse_multi_query(serialized)
    assert reparsed == groups


def test_validate_loopback_config_valid() -> None:
    assert validate_loopback_config("disabled") == LoopbackConfig(mode="disabled")
    assert validate_loopback_config("merge") == LoopbackConfig(mode="merge")
    assert validate_loopback_config("replace") == LoopbackConfig(mode="replace")


def test_validate_loopback_config_invalid() -> None:
    with pytest.raises(ValidationError) as exc_info:
        validate_loopback_config("invalid")
    issues = exc_info.value.issues
    assert len(issues) == 1
    assert issues[0].code == "invalid_loopback_mode"
    assert issues[0].path == "mode"


def test_describe_loopback_disabled() -> None:
    description = describe_loopback(LoopbackConfig(mode="disabled"))
    assert "Loopback processing is disabled" in description
    assert "user's location in AD" in description


def test_describe_loopback_merge() -> None:
    description = describe_loopback(LoopbackConfig(mode="merge"))
    assert "Merge mode" in description
    assert "User's own GPOs take precedence" in description


def test_describe_loopback_replace() -> None:
    description = describe_loopback(LoopbackConfig(mode="replace"))
    assert "Replace mode" in description
    assert "user policies from the computer's GPOs" in description


def test_check_filter_deletion_safe_unreferenced() -> None:
    detail = WmiFilterDetail(id="f1", name="Filter 1")
    assert check_filter_deletion_safe(detail) == ()


def test_check_filter_deletion_safe_referenced() -> None:
    detail = WmiFilterDetail(
        id="f1",
        name="Filter 1",
        references=("{12345678-1234-1234-1234-123456789012}",),
    )
    warnings = check_filter_deletion_safe(detail)
    assert len(warnings) == 1
    assert "referenced by GPO {12345678-1234-1234-1234-123456789012}" in warnings[0]
    assert "Deleting will remove the WMI filter association" in warnings[0]


@pytest.fixture
def client(tmp_path):
    store = WorkspaceStore(tmp_path / "test.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as test_client:
        yield test_client


def test_api_wmi_filter_lint_valid(client) -> None:
    resp = client.post("/api/wmi-filters/lint", json={"query": VALID_QUERY})
    assert resp.status_code == 200
    data = resp.json()
    assert data["issues"] == []


def test_api_wmi_filter_lint_empty(client) -> None:
    resp = client.post("/api/wmi-filters/lint", json={"query": "   "})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["issues"]) == 1
    assert data["issues"][0]["code"] == "empty_query"


def test_api_wmi_filter_parse_multi_query(client) -> None:
    raw = "SELECT * FROM Win32_Battery; SELECT * FROM Win32_Process"
    resp = client.post("/api/wmi-filters/parse-multi-query", json={"raw": raw})
    assert resp.status_code == 200
    data = resp.json()
    assert data["query_groups"] == [
        ["SELECT * FROM Win32_Battery"],
        ["SELECT * FROM Win32_Process"],
    ]


def test_api_loopback_validate(client) -> None:
    resp = client.post("/api/loopback/validate", json={"mode": "merge"})
    assert resp.status_code == 200
    assert resp.json() == {"mode": "merge"}


def test_api_loopback_validate_invalid(client) -> None:
    resp = client.post("/api/loopback/validate", json={"mode": "invalid"})
    assert resp.status_code == 422
    assert resp.json()["error"]["issues"][0]["code"] == "invalid_loopback_mode"


def test_api_loopback_describe(client) -> None:
    resp = client.get("/api/loopback/describe/replace")
    assert resp.status_code == 200
    assert "Replace mode" in resp.json()["description"]


def test_api_loopback_describe_invalid(client) -> None:
    resp = client.get("/api/loopback/describe/invalid")
    assert resp.status_code == 422


def test_api_wmi_filter_check_deletion(client) -> None:
    detail = {
        "id": "f1",
        "name": "Filter 1",
        "namespace": "root\\cimv2",
        "query_groups": [],
        "description": "",
        "owner_sid": "",
        "references": ["{11111111-1111-1111-1111-111111111111}"],
    }
    resp = client.post("/api/wmi-filters/check-deletion", json=detail)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["warnings"]) == 1
    assert "referenced by GPO {11111111-1111-1111-1111-111111111111}" in data["warnings"][0]
