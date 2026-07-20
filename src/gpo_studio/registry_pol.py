"""Read and write the documented Registry.pol PReg format.

The codec deliberately supports the common registry value types exposed by
GPO Studio. Delete operations use the conventional ``**del.<value>`` marker.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast

from .model import RegistrySetting

_HEADER = b"PReg" + struct.pack("<I", 1)
_OPEN = "[".encode("utf-16le")
_CLOSE = "]".encode("utf-16le")
_SEP = ";".encode("utf-16le")
_MAX_POL_RECORDS = 100000
_MAX_MULTI_SZ_ITEMS = 10000

_TYPE_TO_CODE = {
    "REG_SZ": 1,
    "REG_EXPAND_SZ": 2,
    "REG_BINARY": 3,
    "REG_DWORD": 4,
    "REG_MULTI_SZ": 7,
    "REG_QWORD": 11,
}
_CODE_TO_TYPE = {value: key for key, value in _TYPE_TO_CODE.items()}


@dataclass(frozen=True, slots=True)
class PolRecord:
    key: str
    value_name: str
    registry_type: str
    value: str | int | list[str]
    action: str = "set"


class RegistryPolError(ValueError):
    """Malformed or unsupported Registry.pol content."""


def _text(value: str) -> bytes:
    return value.encode("utf-16le")


def _encode_data(registry_type: str, value: str | int | list[str]) -> bytes:
    if registry_type in {"REG_SZ", "REG_EXPAND_SZ"}:
        if not isinstance(value, str):
            raise RegistryPolError(f"{registry_type} requires a string")
        return (value + "\0").encode("utf-16le")
    if registry_type == "REG_BINARY":
        if not isinstance(value, str):
            raise RegistryPolError("REG_BINARY requires a hexadecimal string")
        try:
            return bytes.fromhex(value.replace(" ", ""))
        except ValueError as error:
            raise RegistryPolError("REG_BINARY contains invalid hexadecimal data") from error
    if registry_type == "REG_DWORD":
        if not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
            raise RegistryPolError("REG_DWORD is outside its unsigned range")
        return struct.pack("<I", value)
    if registry_type == "REG_QWORD":
        if not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFFFFFFFFFF:
            raise RegistryPolError("REG_QWORD is outside its unsigned range")
        return struct.pack("<Q", value)
    if registry_type == "REG_MULTI_SZ":
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise RegistryPolError("REG_MULTI_SZ requires a string list")
        if len(value) > _MAX_MULTI_SZ_ITEMS:
            raise RegistryPolError(
                f"REG_MULTI_SZ item count exceeds {_MAX_MULTI_SZ_ITEMS}"
            )
        return ("\0".join(value) + "\0\0").encode("utf-16le")
    raise RegistryPolError(f"unsupported registry type: {registry_type}")


def _decode_data(registry_type: str, data: bytes) -> str | int | list[str]:
    if registry_type in {"REG_SZ", "REG_EXPAND_SZ"}:
        return data.decode("utf-16le").rstrip("\0")
    if registry_type == "REG_BINARY":
        return data.hex().upper()
    if registry_type == "REG_DWORD":
        if len(data) != 4:
            raise RegistryPolError("REG_DWORD payload is not four bytes")
        return cast(int, struct.unpack("<I", data)[0])
    if registry_type == "REG_QWORD":
        if len(data) != 8:
            raise RegistryPolError("REG_QWORD payload is not eight bytes")
        return cast(int, struct.unpack("<Q", data)[0])
    if registry_type == "REG_MULTI_SZ":
        decoded = data.decode("utf-16le").rstrip("\0")
        item_count = decoded.count("\0") + 1 if decoded else 0
        if item_count > _MAX_MULTI_SZ_ITEMS:
            raise RegistryPolError(
                f"REG_MULTI_SZ item count exceeds {_MAX_MULTI_SZ_ITEMS}"
            )
        return [item for item in decoded.split("\0") if item]
    raise RegistryPolError(f"unsupported registry type: {registry_type}")


def serialize(records: Iterable[RegistrySetting | PolRecord]) -> bytes:
    """Serialize deterministic PReg bytes sorted by registry identity."""
    ordered = sorted(
        records,
        key=lambda item: (item.key.casefold(), item.value_name.casefold()),
    )
    output = bytearray(_HEADER)
    for record in ordered:
        value_name = record.value_name
        registry_type = record.registry_type
        value = record.value
        # ``record.action`` is a runtime str here, not a closed Literal: serialize
        # also accepts PolRecord, the loosely-typed parse output for arbitrary
        # PReg (import_export validates and narrows it at the trust boundary). A
        # non-"delete" action is therefore treated as a plain set by design, so
        # there is no assert_never to add — the dispatch is over an open string.
        if record.action == "delete":
            value_name = f"**del.{value_name}"
            registry_type = "REG_SZ"
            value = ""
        type_code = _TYPE_TO_CODE.get(registry_type)
        if type_code is None:
            raise RegistryPolError(f"unsupported registry type: {registry_type}")
        data = _encode_data(registry_type, value)
        output.extend(_OPEN)
        output.extend(_text(record.key + "\0"))
        output.extend(_SEP)
        output.extend(_text(value_name + "\0"))
        output.extend(_SEP)
        output.extend(struct.pack("<I", type_code))
        output.extend(_SEP)
        output.extend(struct.pack("<I", len(data)))
        output.extend(_SEP)
        output.extend(data)
        output.extend(_CLOSE)
    return bytes(output)


def _read_until(data: bytes, offset: int, marker: bytes) -> tuple[bytes, int]:
    end = data.find(marker, offset)
    if end < 0:
        raise RegistryPolError("unterminated Registry.pol field")
    return data[offset:end], end + len(marker)


def parse(data: bytes) -> list[PolRecord]:
    """Parse PReg data produced by Windows or :func:`serialize`."""
    if len(data) < len(_HEADER) or data[:4] != b"PReg":
        raise RegistryPolError("missing PReg header")
    version = struct.unpack("<I", data[4:8])[0]
    if version != 1:
        raise RegistryPolError(f"unsupported PReg version: {version}")
    records: list[PolRecord] = []
    offset = 8
    while offset < len(data):
        if data[offset : offset + 2] != _OPEN:
            raise RegistryPolError(f"expected record at byte {offset}")
        offset += 2
        if len(records) >= _MAX_POL_RECORDS:
            raise RegistryPolError(
                f"PReg record count exceeds {_MAX_POL_RECORDS}"
            )
        key_bytes, offset = _read_until(data, offset, _SEP)
        name_bytes, offset = _read_until(data, offset, _SEP)
        if offset + 4 > len(data):
            raise RegistryPolError("truncated registry type")
        type_code = struct.unpack("<I", data[offset : offset + 4])[0]
        offset += 4
        if data[offset : offset + 2] != _SEP:
            raise RegistryPolError("missing separator after registry type")
        offset += 2
        if offset + 4 > len(data):
            raise RegistryPolError("truncated data size")
        size = struct.unpack("<I", data[offset : offset + 4])[0]
        offset += 4
        if data[offset : offset + 2] != _SEP:
            raise RegistryPolError("missing separator after data size")
        offset += 2
        if offset + size + 2 > len(data):
            raise RegistryPolError("truncated registry data")
        raw_value = data[offset : offset + size]
        offset += size
        if data[offset : offset + 2] != _CLOSE:
            raise RegistryPolError("missing record terminator")
        offset += 2
        try:
            key = key_bytes.decode("utf-16le").rstrip("\0")
            value_name = name_bytes.decode("utf-16le").rstrip("\0")
        except UnicodeDecodeError as error:
            raise RegistryPolError("Registry.pol contains invalid UTF-16 text") from error
        registry_type = _CODE_TO_TYPE.get(type_code)
        if registry_type is None:
            raise RegistryPolError(f"unsupported registry type code: {type_code}")
        action = "set"
        if value_name.casefold().startswith("**del."):
            value_name = value_name[6:]
            action = "delete"
        try:
            value = _decode_data(registry_type, raw_value)
        except UnicodeDecodeError as error:
            raise RegistryPolError("Registry.pol contains invalid UTF-16 data") from error
        records.append(PolRecord(key, value_name, registry_type, value, action))
    return records
