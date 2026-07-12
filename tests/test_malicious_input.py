from __future__ import annotations

import io
import struct
import zipfile

import pytest

from gpo_studio.export import export_bundle
from gpo_studio.model import GPO, GPOLink, RegistrySetting
from gpo_studio.registry_pol import PolRecord, RegistryPolError, parse, serialize
from gpo_studio.validation import validate_gpo

_PREG_HEADER = b"PReg" + struct.pack("<I", 1)
_OPEN = "[".encode("utf-16le")
_SEP = ";".encode("utf-16le")


def _sample_gpo() -> GPO:
    return GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Synthetic Policy",
        description="Fixture only",
        revision=1,
        settings=(
            RegistrySetting(
                id="setting-1",
                side="computer",
                hive="HKLM",
                key=r"Software\Policies\Synthetic",
                value_name="Enabled",
                registry_type="REG_DWORD",
                value=1,
            ),
        ),
        links=(GPOLink(id="link-1", target="OU=Lab,DC=example,DC=test"),),
    )


def test_parse_truncated_header() -> None:
    with pytest.raises(RegistryPolError, match="header"):
        parse(b"PRe")


def test_parse_wrong_header_magic() -> None:
    with pytest.raises(RegistryPolError, match="header"):
        parse(b"XXXX" + struct.pack("<I", 1))


def test_parse_unsupported_version() -> None:
    with pytest.raises(RegistryPolError, match="version"):
        parse(b"PReg" + struct.pack("<I", 2))


def test_parse_truncated_record() -> None:
    valid = serialize([PolRecord(r"Software\Test", "Value", "REG_DWORD", 1)])
    with pytest.raises(RegistryPolError):
        parse(valid[:-3])


def test_parse_missing_separator() -> None:
    key = r"Software\Test".encode("utf-16le")
    name = "Value".encode("utf-16le")
    type_code = struct.pack("<I", 4)
    bad = _PREG_HEADER + _OPEN + key + _SEP + name + _SEP + type_code + b"\xFF\xFF"
    with pytest.raises(RegistryPolError, match="separator"):
        parse(bad)


def test_parse_oversized_data_size() -> None:
    key = r"Software\Test".encode("utf-16le")
    name = "Value".encode("utf-16le")
    type_code = struct.pack("<I", 4)
    huge_size = struct.pack("<I", 20 * 1024 * 1024)
    bad = (
        _PREG_HEADER
        + _OPEN
        + key
        + _SEP
        + name
        + _SEP
        + type_code
        + _SEP
        + huge_size
        + _SEP
    )
    with pytest.raises(RegistryPolError, match="truncated"):
        parse(bad)


def test_parse_empty_data() -> None:
    assert parse(_PREG_HEADER) == []


def test_parse_garbage_after_valid_records() -> None:
    valid = serialize([PolRecord(r"Software\Test", "Value", "REG_DWORD", 1)])
    with pytest.raises(RegistryPolError, match="expected record"):
        parse(valid + b"\x00\x00")


def test_parse_unicode_key_names() -> None:
    unicode_key = r"Software\Policies\Ünïcödé测试"
    record = PolRecord(unicode_key, "Value", "REG_SZ", "data")
    parsed = parse(serialize([record]))
    assert len(parsed) == 1
    assert parsed[0].key == unicode_key


def test_serialize_empty_key_allowed() -> None:
    # Documents current behavior: empty key strings are allowed, not rejected.
    record = PolRecord("", "Value", "REG_SZ", "data")
    result = serialize([record])
    assert parse(result)[0].key == ""


def test_dword_negative_rejected() -> None:
    record = PolRecord(r"Software\Test", "Value", "REG_DWORD", -1)
    with pytest.raises(RegistryPolError, match="range"):
        serialize([record])


def test_dword_overflow_rejected() -> None:
    record = PolRecord(r"Software\Test", "Value", "REG_DWORD", 0x100000000)
    with pytest.raises(RegistryPolError, match="range"):
        serialize([record])


def test_qword_overflow_rejected() -> None:
    record = PolRecord(r"Software\Test", "Value", "REG_QWORD", 0x10000000000000000)
    with pytest.raises(RegistryPolError, match="range"):
        serialize([record])


def test_binary_invalid_hex_rejected() -> None:
    record = PolRecord(r"Software\Test", "Value", "REG_BINARY", "ZZ")
    with pytest.raises(RegistryPolError, match="hexadecimal"):
        serialize([record])


def test_multi_sz_non_list_rejected() -> None:
    record = PolRecord(r"Software\Test", "Value", "REG_MULTI_SZ", "not-a-list")
    with pytest.raises(RegistryPolError, match="string list"):
        serialize([record])


def test_sz_non_string_rejected() -> None:
    record = PolRecord(r"Software\Test", "Value", "REG_SZ", 42)
    with pytest.raises(RegistryPolError, match="string"):
        serialize([record])


def test_export_no_path_traversal_in_entries() -> None:
    blob = export_bundle(_sample_gpo())
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for name in archive.namelist():
            assert ".." not in name
            assert not name.startswith("/")


def test_export_entries_are_fixed_names() -> None:
    blob = export_bundle(_sample_gpo())
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        assert archive.namelist() == [
            "manifest.json",
            "apply.ps1",
            "Machine/Registry.pol",
            "User/Registry.pol",
        ]


def test_export_deterministic_across_calls() -> None:
    assert export_bundle(_sample_gpo()) == export_bundle(_sample_gpo())


def test_export_empty_gpo_produces_valid_zip() -> None:
    gpo = GPO(guid="22222222-3333-4444-5555-666666666666", name="Empty Policy")
    blob = export_bundle(gpo)
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        assert archive.namelist() == [
            "manifest.json",
            "apply.ps1",
            "Machine/Registry.pol",
            "User/Registry.pol",
        ]
        assert parse(archive.read("Machine/Registry.pol")) == []
        assert parse(archive.read("User/Registry.pol")) == []


def test_validate_empty_name_error() -> None:
    gpo = GPO(guid="33333333-4444-5555-6666-777777777777", name="")
    issues = validate_gpo(gpo)
    assert any(
        issue.code == "name_required" and issue.severity == "error" for issue in issues
    )


def test_validate_long_key_name() -> None:
    setting = RegistrySetting(
        id="s1",
        side="computer",
        hive="HKLM",
        key="A" * 1000,
        value_name="Value",
        registry_type="REG_SZ",
        value="data",
    )
    gpo = GPO(
        guid="44444444-5555-6666-7777-888888888888",
        name="Long Key Policy",
        settings=(setting,),
    )
    issues = validate_gpo(gpo)
    assert not any(issue.code == "invalid_registry_key" for issue in issues)


def test_validate_special_chars_in_value() -> None:
    special = 'quotes" back\\slash new\nline'
    setting = RegistrySetting(
        id="s1",
        side="computer",
        hive="HKLM",
        key=r"Software\Policies\Test",
        value_name="Value",
        registry_type="REG_SZ",
        value=special,
    )
    gpo = GPO(
        guid="55555555-6666-7777-8888-999999999999",
        name="Special Chars Policy",
        settings=(setting,),
    )
    issues = validate_gpo(gpo)
    assert isinstance(issues, list)
    assert parse(serialize([setting]))[0].value == special
