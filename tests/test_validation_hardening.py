from __future__ import annotations

from gpo_studio.model import GPO, RegistrySetting, SecurityFilter, WmiFilter
from gpo_studio.validation import validate_gpo, validate_setting

_GUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _gpo(**kw: object) -> GPO:
    return GPO(guid=_GUID, name="Test", **kw)  # type: ignore[arg-type]


def _setting(key: str = r"Software\Policies\Test", **kw: object) -> RegistrySetting:
    return RegistrySetting(
        id="s1",
        side="computer",
        hive="HKLM",
        key=key,
        value_name="Value",
        registry_type="REG_SZ",
        value="data",
        **kw,  # type: ignore[arg-type]
    )


def test_security_filter_empty_principal_error() -> None:
    gpo = _gpo(security_filters=(SecurityFilter(id="sf-1", principal="   "),))
    issues = validate_gpo(gpo)
    assert any(
        i.code == "empty_principal"
        and i.severity == "error"
        and i.path == "security_filters/sf-1/principal"
        for i in issues
    )


def test_security_filter_duplicate_principal_different_case_error() -> None:
    gpo = _gpo(
        security_filters=(
            SecurityFilter(id="sf-1", principal="Domain Admins"),
            SecurityFilter(id="sf-2", principal="DOMAIN ADMINS"),
        ),
    )
    issues = validate_gpo(gpo)
    assert any(
        i.code == "duplicate_principal"
        and i.path == "security_filters/sf-2/principal"
        for i in issues
    )


def test_security_filter_valid_no_errors() -> None:
    gpo = _gpo(
        security_filters=(
            SecurityFilter(id="sf-1", principal="Domain Admins"),
            SecurityFilter(id="sf-2", principal="Help Desk", permission="read"),
        ),
    )
    assert validate_gpo(gpo) == []


def test_security_filter_control_character_in_principal_error() -> None:
    gpo = _gpo(
        security_filters=(SecurityFilter(id="sf-1", principal="Domain\x00Admins"),),
    )
    issues = validate_gpo(gpo)
    assert any(
        i.code == "control_character_in_principal"
        and i.path == "security_filters/sf-1/principal"
        for i in issues
    )


def test_security_filter_principal_too_long_error() -> None:
    gpo = _gpo(
        security_filters=(SecurityFilter(id="sf-1", principal="A" * 256),),
    )
    issues = validate_gpo(gpo)
    assert any(
        i.code == "principal_too_long"
        and i.path == "security_filters/sf-1/principal"
        for i in issues
    )


def test_wmi_filter_empty_query_warning() -> None:
    gpo = _gpo(wmi_filter=WmiFilter(id="wf-1", name="filter", query=""))
    issues = validate_gpo(gpo)
    assert any(
        i.code == "empty_wmi_query"
        and i.severity == "warning"
        and i.path == "wmi_filter/query"
        for i in issues
    )


def test_wmi_filter_query_missing_from_error() -> None:
    gpo = _gpo(wmi_filter=WmiFilter(id="wf-1", name="filter", query="SELECT *"))
    issues = validate_gpo(gpo)
    assert any(
        i.code == "invalid_wmi_query"
        and i.severity == "error"
        and i.path == "wmi_filter/query"
        for i in issues
    )


def test_wmi_filter_query_missing_select_error() -> None:
    gpo = _gpo(wmi_filter=WmiFilter(id="wf-1", name="filter", query="FROM Win32_OperatingSystem"))
    issues = validate_gpo(gpo)
    assert any(i.code == "invalid_wmi_query" and i.severity == "error" for i in issues)


def test_wmi_filter_valid_query_no_issues() -> None:
    gpo = _gpo(
        wmi_filter=WmiFilter(
            id="wf-1", name="filter", query="SELECT * FROM Win32_OperatingSystem"
        ),
    )
    assert validate_gpo(gpo) == []


def test_wmi_filter_empty_name_error() -> None:
    gpo = _gpo(
        wmi_filter=WmiFilter(
            id="wf-1", name="   ", query="SELECT * FROM Win32_OperatingSystem"
        ),
    )
    issues = validate_gpo(gpo)
    assert any(
        i.code == "empty_wmi_filter_name"
        and i.severity == "error"
        and i.path == "wmi_filter/name"
        for i in issues
    )


def test_registry_key_null_byte_error() -> None:
    issues = validate_setting(_setting(key="Software\x00Policies"))
    assert any(i.code == "control_character_in_key" for i in issues)


def test_registry_key_too_long_error() -> None:
    issues = validate_setting(_setting(key="A" * 256))
    assert any(i.code == "registry_key_too_long" for i in issues)


def test_registry_key_exactly_255_chars_passes() -> None:
    issues = validate_setting(_setting(key="A" * 255))
    assert not any(i.code == "registry_key_too_long" for i in issues)


def test_registry_key_consecutive_backslashes_error() -> None:
    issues = validate_setting(_setting(key=r"Software\\Policies"))
    assert any(i.code == "consecutive_backslashes_in_key" for i in issues)


def test_registry_valid_key_no_issues() -> None:
    assert validate_setting(_setting(key=r"Software\Policies\Test")) == []


def test_security_filter_duplicate_principal_whitespace_difference() -> None:
    gpo = _gpo(
        security_filters=(
            SecurityFilter(id="sf-1", principal="Domain Admins"),
            SecurityFilter(id="sf-2", principal=" Domain Admins "),
        ),
    )
    issues = validate_gpo(gpo)
    assert any(i.code == "duplicate_principal" for i in issues)


def test_wmi_query_substring_not_keyword() -> None:
    gpo = _gpo(
        wmi_filter=WmiFilter(id="wf-1", name="filter", query="selection fromage"),
    )
    issues = validate_gpo(gpo)
    assert any(i.code == "invalid_wmi_query" and i.severity == "error" for i in issues)


def test_registry_key_whitespace_only_error() -> None:
    issues = validate_setting(_setting(key="   "))
    assert any(i.code == "invalid_registry_key" for i in issues)
