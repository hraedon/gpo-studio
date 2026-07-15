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
