"""Retained native import observations, separate from the editable policy model."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import PurePosixPath

from .model import BackupInventory, CseFileEntry, StudioError
from .xml_safety import parse_xml_bounded

_MAX_XML_BYTES = 50 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_BKP = "{http://www.microsoft.com/GroupPolicy/GPOOperations}"
_SETTINGS = "{http://www.microsoft.com/GroupPolicy/Settings}"
_TYPES = "{http://www.microsoft.com/GroupPolicy/Types}"


def _xml(encoded: str) -> tuple[bytes, ET.Element]:
    if len(encoded) > 4 * ((_MAX_XML_BYTES + 2) // 3):
        raise StudioError("Native inventory XML exceeds the size limit")
    try:
        raw = base64.b64decode(encoded, validate=True)
        root = parse_xml_bounded(raw, max_size=_MAX_XML_BYTES)
    except (ValueError, binascii.Error) as error:
        raise StudioError("Invalid native inventory XML") from error
    if any(
        name.rsplit("}", 1)[-1].casefold() == "cpassword"
        for element in root.iter() for name in element.attrib
    ):
        raise StudioError("Native inventory XML contains a cpassword attribute")
    return raw, root


def inventory_from_dict(data: object) -> BackupInventory:
    """Validate snapshots read from the workspace or a Studio bundle."""
    if not isinstance(data, dict):
        raise StudioError("Invalid native backup inventory")
    backup_xml = data.get("backup_xml_base64")
    report_xml = data.get("report_xml_base64", "")
    files = data.get("files", [])
    if not isinstance(backup_xml, str) or not isinstance(report_xml, str):
        raise StudioError("Native inventory XML must be base64 text")
    _, backup = _xml(backup_xml)
    identifier = backup.find(
        f"{_BKP}GroupPolicyObject/{_BKP}GroupPolicyCoreSettings/{_BKP}ID"
    )
    if identifier is None or not (identifier.text or "").strip():
        raise StudioError("Native inventory has no backup GPO identity")
    if not isinstance(files, (list, tuple)) or len(files) > 10000:
        raise StudioError("Invalid native inventory file list")
    entries = []
    seen: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise StudioError("Invalid native inventory file entry")
        path, digest, size = (entry.get(key) for key in ("relative_path", "content_hash", "size"))
        if (
            not isinstance(path, str) or not path or "\\" in path or ":" in path
            or PurePosixPath(path).is_absolute()
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or path.casefold() in seen or "\x00" in path
            or not isinstance(digest, str) or not _SHA256.fullmatch(digest)
            or type(size) is not int or size < 0
        ):
            raise StudioError("Invalid native inventory file metadata")
        seen.add(path.casefold())
        entries.append(CseFileEntry(path, digest, size))
    inventory = BackupInventory(backup_xml, report_xml, tuple(entries))
    validate_report_identity(inventory, (identifier.text or "").strip().strip("{}"))
    return inventory


def validate_report_identity(inventory: BackupInventory, guid: str) -> None:
    if not inventory.report_xml_base64:
        return
    _, report = _xml(inventory.report_xml_base64)
    identifier = report.find(f"{_SETTINGS}Identifier/{_TYPES}Identifier")
    if (
        report.tag != f"{_SETTINGS}GPO" or identifier is None
        or (identifier.text or "").strip().strip("{}").casefold() != guid.casefold()
    ):
        raise StudioError("Native report identity does not match the imported GPO")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _observations(element: ET.Element, path: str) -> Iterator[str]:
    """Keep unknown native elements and attributes visible, without interpreting them."""
    for name, value in sorted(element.attrib.items()):
        yield f"{path}/@{_local(name)}: {value}"
    if element.text and element.text.strip():
        yield f"{path}: {element.text.strip()}"
    for index, child in enumerate(element, start=1):
        yield from _observations(child, f"{path}/{_local(child.tag)}[{index}]")
        if child.tail and child.tail.strip():
            yield f"{path}/tail[{index}]: {child.tail.strip()}"
    if len(element) == 0 and not element.attrib and not (element.text or "").strip():
        yield f"{path}: (empty)"


def inventory_report_lines(inventory: BackupInventory) -> Iterator[str]:
    yield "Imported source snapshot; later edits do not update these observations."
    yield "Payload files are inventoried by hash, not stored here. Keep the original backup."
    raw, backup = _xml(inventory.backup_xml_base64)
    yield f"Backup.xml SHA-256: {hashlib.sha256(raw).hexdigest()}"
    core = backup.find(f"{_BKP}GroupPolicyObject/{_BKP}GroupPolicyCoreSettings")
    if core is not None:
        for side in ("Machine", "User"):
            registrations = core.find(f"{_BKP}{side}ExtensionGuids")
            value = (registrations.text or "").strip() if registrations is not None else ""
            yield f"{side} extension registrations: {value or '(none)'}"
    yield "Backup handlers (these are not counts of active policy settings):"
    for handler in backup.iter(f"{_BKP}GroupPolicyExtension"):
        name = handler.get(f"{_BKP}DescName", "Unnamed handler")
        yield f"  {name} {handler.get(f'{_BKP}ID', '(no GUID)')}"
        for reference in handler:
            yield from (f"    {line}" for line in _observations(reference, _local(reference.tag)))
    yield "Captured payload inventory (all paths relative to DomainSysvol/GPO):"
    for file in inventory.files:
        yield f"  {file.relative_path}: {file.size} bytes; SHA-256 {file.content_hash}"
    if not inventory.report_xml_base64:
        yield "Native setting inventory unavailable: this backup has no gpreport.xml."
        return
    raw, report = _xml(inventory.report_xml_base64)
    yield f"gpreport.xml SHA-256: {hashlib.sha256(raw).hexdigest()}"
    yield "Native source setting inventory (includes unmodeled settings):"
    for scope in ("Computer", "User"):
        scope_element = report.find(f"{_SETTINGS}{scope}")
        if scope_element is None:
            continue
        for group in scope_element.findall(f"{_SETTINGS}ExtensionData"):
            name = group.findtext(f"{_SETTINGS}Name", default="Unnamed extension")
            extension = group.find(f"{_SETTINGS}Extension")
            if extension is not None:
                yield from _observations(extension, f"{scope}/{name}")
