"""Read GPMC backup directories and preserve content including unknown CSEs."""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

_MAX_FILE_SIZE = 50 * 1024 * 1024
_MAX_DEPTH = 100
_ADMX_NS = "http://www.microsoft.com/GroupPolicy/Types"
_GUID_RE = re.compile(
    r"^\{?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}?$"
)


class BackupError(ValueError):
    """Malformed or unsupported GPMC backup content."""


@dataclass(frozen=True, slots=True)
class CseFile:
    relative_path: str
    content_hash: str
    size: int


@dataclass(frozen=True, slots=True)
class CseExtension:
    guid: str
    side: Literal["machine", "user"]
    files: tuple[CseFile, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class BackupGpo:
    guid: str
    display_name: str
    domain: str
    machine_extensions: tuple[CseExtension, ...] = field(default_factory=tuple)
    user_extensions: tuple[CseExtension, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class GpmcBackup:
    backup_time: str
    backup_id: str
    gpos: tuple[BackupGpo, ...] = field(default_factory=tuple)


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _check_depth(elem: ET.Element, depth: int = 0) -> None:
    if depth > _MAX_DEPTH:
        raise BackupError(f"XML nesting depth exceeds {_MAX_DEPTH}")
    for child in elem:
        _check_depth(child, depth + 1)


def _safe_parse(data: bytes) -> ET.Element:
    if len(data) > _MAX_FILE_SIZE:
        raise BackupError(f"File exceeds {_MAX_FILE_SIZE} bytes")
    if b"<!ENTITY" in data:
        raise BackupError("XML entity declarations are not allowed")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise BackupError(f"Malformed XML: {error}") from error
    _check_depth(root)
    return root


def _safe_path(base: Path, relative: str) -> Path:
    if ".." in Path(relative).parts:
        raise BackupError(f"Path traversal detected: {relative}")
    if Path(relative).is_absolute():
        raise BackupError(f"Absolute path not allowed: {relative}")
    resolved = (base / relative).resolve()
    base_resolved = base.resolve()
    try:
        resolved.relative_to(base_resolved)
    except ValueError:
        raise BackupError(f"Path escapes base directory: {relative}") from None
    if resolved.is_symlink():
        raise BackupError(f"Symlinks are not allowed: {resolved}")
    return resolved


def _hash_file(path: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            size += len(chunk)
            if size > _MAX_FILE_SIZE:
                raise BackupError(f"File exceeds {_MAX_FILE_SIZE} bytes: {path}")
            hasher.update(chunk)
    return hasher.hexdigest(), size


def _parse_extension_guids(text: str | None) -> list[str]:
    if not text:
        return []
    guids: list[str] = []
    for part in text.replace("\n", " ").split():
        part = part.strip()
        if part.startswith("{") and part.endswith("}"):
            guids.append(part)
    return guids


def parse_manifest(data: bytes) -> GpmcBackup:
    """Parse manifest.xml from a GPMC backup directory."""
    root = _safe_parse(data)

    backup_time = ""
    backup_id = ""
    gpos: list[BackupGpo] = []

    for inst in root.iter():
        if _local_name(inst.tag) == "BackupInstance":
            bt_elem = inst.find(f".//{{{_ADMX_NS}}}BackupTime")
            backup_time = _text_or_empty(bt_elem)
            id_elem = inst.find(f".//{{{_ADMX_NS}}}ID")
            backup_id = _text_or_empty(id_elem)

            for gpo_elem in inst.iter():
                if _local_name(gpo_elem.tag) != "GPO":
                    continue
                guid_elem = gpo_elem.find(f".//{{{_ADMX_NS}}}Identifier")
                guid = _text_or_empty(guid_elem) if guid_elem is not None else ""
                if not guid:
                    guid_elem2 = gpo_elem.find(f".//{{{_ADMX_NS}}}Guid")
                    guid = _text_or_empty(guid_elem2) if guid_elem2 is not None else ""

                name_elem = gpo_elem.find(f".//{{{_ADMX_NS}}}DisplayName")
                display_name = _text_or_empty(name_elem) if name_elem is not None else ""

                domain_elem = gpo_elem.find(f".//{{{_ADMX_NS}}}Domain")
                domain = _text_or_empty(domain_elem) if domain_elem is not None else ""

                machine_ext_elem = gpo_elem.find(f".//{{{_ADMX_NS}}}MachineExtensionGuids")
                user_ext_elem = gpo_elem.find(f".//{{{_ADMX_NS}}}UserExtensionGuids")

                machine_guids = _parse_extension_guids(
                    machine_ext_elem.text if machine_ext_elem is not None else None
                )
                user_guids = _parse_extension_guids(
                    user_ext_elem.text if user_ext_elem is not None else None
                )

                machine_exts = tuple(
                    CseExtension(guid=g, side="machine", files=())
                    for g in machine_guids
                )
                user_exts = tuple(
                    CseExtension(guid=g, side="user", files=()) for g in user_guids
                )

                gpos.append(
                    BackupGpo(
                        guid=guid,
                        display_name=display_name,
                        domain=domain,
                        machine_extensions=machine_exts,
                        user_extensions=user_exts,
                    )
                )

    if not gpos:
        raise BackupError("No GPO entries found in manifest")

    return GpmcBackup(backup_time=backup_time, backup_id=backup_id, gpos=tuple(gpos))


def _text_or_empty(elem: ET.Element | None) -> str:
    if elem is None:
        return ""
    return (elem.text or "").strip()


def read_backup(backup_dir: Path) -> GpmcBackup:
    """Read a complete GPMC backup directory."""
    manifest_path = backup_dir / "manifest.xml"
    if not manifest_path.exists():
        raise BackupError(f"Missing manifest.xml in {backup_dir}")

    manifest_data = manifest_path.read_bytes()
    backup = parse_manifest(manifest_data)

    enriched_gpos: list[BackupGpo] = []
    for gpo in backup.gpos:
        if not _GUID_RE.match(gpo.guid):
            raise BackupError(f"Invalid GPO GUID in manifest: {gpo.guid!r}")
        gpo_dir = backup_dir / gpo.guid
        if not gpo_dir.exists():
            enriched_gpos.append(gpo)
            continue

        machine_exts = _scan_side(gpo_dir / "Machine", gpo.machine_extensions)
        user_exts = _scan_side(gpo_dir / "User", gpo.user_extensions)

        enriched_gpos.append(
            BackupGpo(
                guid=gpo.guid,
                display_name=gpo.display_name,
                domain=gpo.domain,
                machine_extensions=machine_exts,
                user_extensions=user_exts,
            )
        )

    return GpmcBackup(
        backup_time=backup.backup_time,
        backup_id=backup.backup_id,
        gpos=tuple(enriched_gpos),
    )


def _scan_side(side_dir: Path, extensions: tuple[CseExtension, ...]) -> tuple[CseExtension, ...]:
    if not side_dir.exists():
        return extensions

    all_files: list[CseFile] = []
    for path in sorted(side_dir.rglob("*")):
        if path.is_symlink():
            raise BackupError(f"Symlinks are not allowed in backup content: {path}")
        if not path.is_file():
            continue
        rel = str(path.relative_to(side_dir))
        if ".." in Path(rel).parts:
            raise BackupError(f"Path traversal detected: {rel}")
        content_hash, size = _hash_file(path)
        all_files.append(
            CseFile(relative_path=rel, content_hash=content_hash, size=size)
        )

    if not extensions:
        if all_files:
            side_lit: Literal["machine", "user"] = (
                "machine" if side_dir.name == "Machine" else "user"
            )
            return (
                CseExtension(
                    guid="unknown", side=side_lit, files=tuple(all_files)
                ),
            )
        return ()

    result: list[CseExtension] = []
    for ext in extensions:
        side_literal: Literal["machine", "user"] = (
            "machine" if side_dir.name == "Machine" else "user"
        )
        result.append(
            CseExtension(guid=ext.guid, side=side_literal, files=tuple(all_files))
        )
    return tuple(result)


def read_cse_content(
    backup_dir: Path,
    gpo_guid: str,
    side: Literal["machine", "user"],
    cse_guid: str,
    relative_path: str,
) -> bytes:
    """Read the raw bytes of a specific CSE file."""
    side_dir_name = "Machine" if side == "machine" else "User"
    file_path = _safe_path(backup_dir / gpo_guid / side_dir_name, relative_path)
    if not file_path.exists():
        raise BackupError(f"File not found: {file_path}")
    data = file_path.read_bytes()
    if len(data) > _MAX_FILE_SIZE:
        raise BackupError(f"File exceeds {_MAX_FILE_SIZE} bytes")
    return data
