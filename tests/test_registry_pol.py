from __future__ import annotations

import struct

import pytest

from gpo_studio.model import RegistrySetting
from gpo_studio.registry_pol import RegistryPolError, parse, serialize

_HEADER = b"PReg" + struct.pack("<I", 1)


def setting(name: str, registry_type: str, value: object, action: str = "set") -> RegistrySetting:
    return RegistrySetting(  # type: ignore[arg-type]
        id=name,
        side="computer",
        hive="HKLM",
        key=r"Software\Policies\Synthetic",
        value_name=name,
        registry_type=registry_type,
        value=value,
        action=action,
    )


@pytest.mark.parametrize(
    ("registry_type", "value"),
    [
        ("REG_SZ", "hello"),
        ("REG_EXPAND_SZ", r"%SystemRoot%\demo"),
        ("REG_BINARY", "00 FF A5"),
        ("REG_DWORD", 4_294_967_295),
        ("REG_QWORD", 18_446_744_073_709_551_615),
        ("REG_MULTI_SZ", ["alpha", "beta"]),
    ],
)
def test_round_trip_supported_types(registry_type: str, value: object) -> None:
    data = serialize([setting("Example", registry_type, value)])
    records = parse(data)
    assert len(records) == 1
    assert records[0].registry_type == registry_type
    if registry_type == "REG_BINARY":
        assert records[0].value == "00FFA5"
    else:
        assert records[0].value == value


def test_serialization_is_deterministic_and_sorted() -> None:
    first = setting("Zulu", "REG_DWORD", 1)
    second = setting("Alpha", "REG_DWORD", 2)
    assert serialize([first, second]) == serialize([second, first])
    assert [item.value_name for item in parse(serialize([first, second]))] == ["Alpha", "Zulu"]


def test_delete_marker_round_trip() -> None:
    record = parse(serialize([setting("Legacy", "REG_SZ", "ignored", "delete")]))[0]
    assert record.action == "delete"
    assert record.value_name == "Legacy"


def test_rejects_malformed_header() -> None:
    with pytest.raises(RegistryPolError, match="header"):
        parse(b"not-a-policy")


def test_rejects_excessive_record_count(monkeypatch) -> None:
    monkeypatch.setattr("gpo_studio.registry_pol._MAX_POL_RECORDS", 3)
    records = [setting(f"Val{i}", "REG_DWORD", i) for i in range(5)]
    data = serialize(records)
    with pytest.raises(RegistryPolError, match="record count exceeds"):
        parse(data)


def test_rejects_excessive_multi_sz_items(monkeypatch) -> None:
    monkeypatch.setattr("gpo_studio.registry_pol._MAX_MULTI_SZ_ITEMS", 3)
    rec = setting("Multi", "REG_MULTI_SZ", [f"item{i}" for i in range(5)])
    with pytest.raises(RegistryPolError, match="item count exceeds"):
        serialize([rec])


def test_preg_key_and_value_name_include_null_terminators() -> None:
    """Byte-level check: key and value_name must end with UTF-16LE null.

    Windows emits a null terminator on these fields.  The round-trip test
    cannot catch a regression because the parser strips trailing nulls for
    backward compatibility.
    """
    rec = setting("Val", "REG_DWORD", 1)
    data = serialize([rec])
    null_utf16 = "\0".encode("utf-16le")
    sep_utf16 = ";".encode("utf-16le")
    key_bytes = r"Software\Policies\Synthetic".encode("utf-16le")
    assert key_bytes + null_utf16 + sep_utf16 in data
    name_bytes = "Val".encode("utf-16le")
    assert name_bytes + null_utf16 + sep_utf16 in data


def test_preg_delete_marker_value_name_includes_null_terminator() -> None:
    """Delete-marker value_name (**del.X) must also carry the null terminator."""
    rec = setting("Legacy", "REG_SZ", "ignored", "delete")
    data = serialize([rec])
    null_utf16 = "\0".encode("utf-16le")
    sep_utf16 = ";".encode("utf-16le")
    del_name = "**del.Legacy".encode("utf-16le")
    assert del_name + null_utf16 + sep_utf16 in data


def test_preg_multi_sz_data_includes_double_null_terminator() -> None:
    """REG_MULTI_SZ payload must end with \\0\\0 (double-null) per MS-GPREG."""
    rec = setting("Multi", "REG_MULTI_SZ", ["alpha", "beta"])
    data = serialize([rec])
    close_utf16 = "]".encode("utf-16le")
    payload = ("alpha\0beta\0\0").encode("utf-16le")
    assert payload + close_utf16 in data


def test_parse_rejects_excessive_multi_sz_items(monkeypatch) -> None:
    monkeypatch.setattr("gpo_studio.registry_pol._MAX_MULTI_SZ_ITEMS", 3)
    items = [f"item{i}" for i in range(5)]
    raw_value = ("\0".join(items) + "\0\0").encode("utf-16le")
    record_bytes = (
        "[".encode("utf-16le")
        + "K".encode("utf-16le")
        + ";".encode("utf-16le")
        + "V".encode("utf-16le")
        + ";".encode("utf-16le")
        + struct.pack("<I", 7)
        + ";".encode("utf-16le")
        + struct.pack("<I", len(raw_value))
        + ";".encode("utf-16le")
        + raw_value
        + "]".encode("utf-16le")
    )
    data = _HEADER + record_bytes
    with pytest.raises(RegistryPolError, match="item count exceeds"):
        parse(data)


def test_multi_sz_round_trip_preserves_empty_strings() -> None:
    """Empty strings within a REG_MULTI_SZ list must survive round-trip."""
    rec = setting("Multi", "REG_MULTI_SZ", ["alpha", "", "beta"])
    data = serialize([rec])
    records = parse(data)
    assert records[0].value == ["alpha", "", "beta"]


def test_multi_sz_single_empty_string_collapses_to_empty_list() -> None:
    """REG_MULTI_SZ [""] is indistinguishable from [] at the byte level.

    Both encode to a bare double-null terminator. This is a format
    limitation, not a codec bug.
    """
    rec = setting("Multi", "REG_MULTI_SZ", [""])
    data = serialize([rec])
    records = parse(data)
    assert records[0].value == []


def test_multi_sz_empty_list_round_trip() -> None:
    """An empty REG_MULTI_SZ list must round-trip to an empty list."""
    rec = setting("Multi", "REG_MULTI_SZ", [])
    data = serialize([rec])
    records = parse(data)
    assert records[0].value == []


def test_delete_all_values_round_trip() -> None:
    record = parse(serialize([setting("Ignored", "REG_SZ", "", "delete_all_values")]))[0]
    assert record.action == "delete_all_values"
    assert record.value_name == ""


def test_delete_all_values_serializes_before_set_records() -> None:
    dav = setting("Ignored", "REG_SZ", "", "delete_all_values")
    val = setting("Alpha", "REG_DWORD", 1)
    records = parse(serialize([val, dav]))
    assert records[0].action == "delete_all_values"
    assert records[1].action == "set"
    assert records[1].value_name == "Alpha"


def test_delete_all_values_byte_format() -> None:
    rec = setting("Ignored", "REG_SZ", "", "delete_all_values")
    data = serialize([rec])
    null_utf16 = "\0".encode("utf-16le")
    sep_utf16 = ";".encode("utf-16le")
    delvals_name = "**delvals.".encode("utf-16le")
    assert delvals_name + null_utf16 + sep_utf16 in data
    assert struct.pack("<I", 1) + sep_utf16 + struct.pack("<I", 0) in data


def test_delete_all_values_and_delete_ordering() -> None:
    dav = setting("Ignored", "REG_SZ", "", "delete_all_values")
    dele = setting("Old", "REG_SZ", "x", "delete")
    val = setting("New", "REG_DWORD", 42)
    records = parse(serialize([val, dele, dav]))
    assert [r.action for r in records] == ["delete_all_values", "delete", "set"]
