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
            SecurityFilter(id="sf-1", principal="DOMAIN\\Admins"),
            SecurityFilter(id="sf-2", principal="DOMAIN\\ADMINS"),
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
            SecurityFilter(id="sf-1", principal="DOMAIN\\Admins"),
            SecurityFilter(id="sf-2", principal="DOMAIN\\HelpDesk", permission="read"),
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
            SecurityFilter(id="sf-1", principal="DOMAIN\\Admins"),
            SecurityFilter(id="sf-2", principal=" DOMAIN\\Admins "),
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


def test_principal_format_domain_user_passes() -> None:
    gpo = _gpo(
        security_filters=(SecurityFilter(id="sf-1", principal="DOMAIN\\Admins"),),
    )
    assert not any(i.code == "invalid_principal_format" for i in validate_gpo(gpo))


def test_principal_format_upn_passes() -> None:
    gpo = _gpo(
        security_filters=(SecurityFilter(id="sf-1", principal="admin@example.com"),),
    )
    assert not any(i.code == "invalid_principal_format" for i in validate_gpo(gpo))


def test_principal_format_sid_passes() -> None:
    gpo = _gpo(
        security_filters=(SecurityFilter(id="sf-1", principal="S-1-5-32-544"),),
    )
    assert not any(i.code == "invalid_principal_format" for i in validate_gpo(gpo))


def test_principal_format_plain_name_rejected() -> None:
    gpo = _gpo(
        security_filters=(SecurityFilter(id="sf-1", principal="just a name"),),
    )
    issues = validate_gpo(gpo)
    assert any(
        i.code == "invalid_principal_format"
        and i.severity == "error"
        and i.path == "security_filters/sf-1/principal"
        for i in issues
    )


def test_principal_format_empty_user_part_rejected() -> None:
    gpo = _gpo(
        security_filters=(SecurityFilter(id="sf-1", principal="DOMAIN\\"),),
    )
    issues = validate_gpo(gpo)
    assert any(
        i.code == "invalid_principal_format"
        and i.path == "security_filters/sf-1/principal"
        for i in issues
    )


def test_principal_format_empty_domain_part_rejected() -> None:
    gpo = _gpo(
        security_filters=(SecurityFilter(id="sf-1", principal="\\user"),),
    )
    issues = validate_gpo(gpo)
    assert any(
        i.code == "invalid_principal_format"
        and i.path == "security_filters/sf-1/principal"
        for i in issues
    )


def test_domain_empty_error() -> None:
    gpo = _gpo(domain="   ")
    issues = validate_gpo(gpo)
    assert any(
        i.code == "empty_domain"
        and i.severity == "error"
        and i.path == "domain"
        for i in issues
    )


def test_domain_too_long_error() -> None:
    gpo = _gpo(domain="A" * 256)
    issues = validate_gpo(gpo)
    assert any(
        i.code == "domain_too_long"
        and i.severity == "error"
        and i.path == "domain"
        for i in issues
    )


def test_domain_control_character_error() -> None:
    gpo = _gpo(domain="studio\x00.local")
    issues = validate_gpo(gpo)
    assert any(
        i.code == "control_character_in_domain"
        and i.severity == "error"
        and i.path == "domain"
        for i in issues
    )


def test_domain_format_suspicious_warning() -> None:
    gpo = _gpo(domain="domain!")
    issues = validate_gpo(gpo)
    assert any(
        i.code == "domain_format_suspicious"
        and i.severity == "warning"
        and i.path == "domain"
        for i in issues
    )


def test_domain_valid_fqdn_no_issues() -> None:
    gpo = _gpo(domain="corp.example.com")
    assert not any(i.path == "domain" for i in validate_gpo(gpo))


def test_domain_default_studio_local_no_issues() -> None:
    gpo = _gpo()
    assert not any(i.path == "domain" for i in validate_gpo(gpo))
