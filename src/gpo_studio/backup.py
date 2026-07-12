"""Read GPMC backup directories and preserve content including unknown CSEs."""

from __future__ import annotations

import hashlib
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .model import StudioError

_MAX_FILE_SIZE = 50 * 1024 * 1024
_MAX_DEPTH = 100
_ADMX_NS = "http://www.microsoft.com/GroupPolicy/Types"
_REGISTRY_CSE_GUID = "{35378EAC-683F-11D2-A89A-00C04FBBCFA2}"
_GUID_RE = re.compile(
    r"^\{?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}?$"
)


class BackupError(StudioError):
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
class BackupSecurityFilter:
    principal: str
    permission: str
    inheritable: bool
    target_type: str
    sid: str = ""


@dataclass(frozen=True, slots=True)
class BackupWmiFilter:
    name: str
    query: str
    language: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class BackupGpo:
    guid: str
    display_name: str
    domain: str
    machine_extensions: tuple[CseExtension, ...] = field(default_factory=tuple)
    user_extensions: tuple[CseExtension, ...] = field(default_factory=tuple)
    security_filters: tuple[BackupSecurityFilter, ...] = field(default_factory=tuple)
    wmi_filter: BackupWmiFilter | None = None


@dataclass(frozen=True, slots=True)
class GpmcBackup:
    backup_time: str
    backup_id: str
    backup_type: str = ""
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


_VALID_TARGET_TYPES = {"user", "group", "computer"}


def _safe_path(base: Path, relative: str) -> Path:
    if ".." in Path(relative).parts:
        raise BackupError(f"Path traversal detected: {relative}")
    if Path(relative).is_absolute():
        raise BackupError(f"Absolute path not allowed: {relative}")
    candidate = base / relative
    current = base
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise BackupError(f"Symlinks are not allowed: {current}")
    resolved = candidate.resolve()
    base_resolved = base.resolve()
    try:
        resolved.relative_to(base_resolved)
    except ValueError:
        raise BackupError(f"Path escapes base directory: {relative}") from None
    return resolved


def read_file_bytes(path: Path) -> bytes:
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise BackupError(f"Cannot open file (symlink or inaccessible): {path}") from None
    try:
        data = bytearray()
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                raise BackupError(f"Cannot read file: {path}") from None
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > _MAX_FILE_SIZE:
                raise BackupError(f"File exceeds {_MAX_FILE_SIZE} bytes: {path}")
        return bytes(data)
    finally:
        os.close(fd)


def _hash_file(path: Path) -> tuple[str, int]:
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise BackupError(f"Cannot open file (symlink or inaccessible): {path}") from None
    try:
        hasher = hashlib.sha256()
        size = 0
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                raise BackupError(f"Cannot read file: {path}") from None
            if not chunk:
                break
            size += len(chunk)
            if size > _MAX_FILE_SIZE:
                raise BackupError(f"File exceeds {_MAX_FILE_SIZE} bytes: {path}")
            hasher.update(chunk)
        return hasher.hexdigest(), size
    finally:
        os.close(fd)


def _parse_extension_guids(text: str | None) -> list[str]:
    if not text:
        return []
    guids: list[str] = []
    for part in text.replace("\n", " ").split():
        part = part.strip()
        if part.startswith("{") and part.endswith("}"):
            guids.append(part)
    return guids


def parse_bkup_info(data: bytes) -> GpmcBackup:
    """Parse bkupInfo.xml from a GPMC backup directory."""
    root = _safe_parse(data)

    backup_time = ""
    backup_id = ""
    backup_type = ""
    guid = ""
    display_name = ""
    domain = ""

    for child in root:
        local = _local_name(child.tag)
        if local == "BackupTime":
            backup_time = _text_or_empty(child)
        elif local == "ID":
            backup_id = _text_or_empty(child)
        elif local == "BackupType":
            backup_type = _text_or_empty(child)
        elif local == "GPO":
            guid_elem = child.find(f"./{{{_ADMX_NS}}}Identifier")
            guid = _text_or_empty(guid_elem).strip("{}").lower()
            name_elem = child.find(f"./{{{_ADMX_NS}}}DisplayName")
            display_name = _text_or_empty(name_elem)
            domain_elem = child.find(f"./{{{_ADMX_NS}}}Domain")
            domain = _text_or_empty(domain_elem)

    gpo = BackupGpo(guid=guid, display_name=display_name, domain=domain)
    return GpmcBackup(
        backup_time=backup_time,
        backup_id=backup_id,
        backup_type=backup_type,
        gpos=(gpo,),
    )


def parse_manifest(data: bytes) -> GpmcBackup:
    """Parse manifest.xml from a GPMC backup directory."""
    root = _safe_parse(data)

    backup_time = ""
    backup_id = ""
    gpos: list[BackupGpo] = []

    for inst in root.iter():
        if _local_name(inst.tag) == "BackupInstance":
            bt_elem = inst.find(f"./{{{_ADMX_NS}}}BackupTime")
            backup_time = _text_or_empty(bt_elem)
            id_elem = inst.find(f"./{{{_ADMX_NS}}}ID")
            backup_id = _text_or_empty(id_elem)

            for gpo_elem in inst.iter():
                if _local_name(gpo_elem.tag) != "GPO":
                    continue
                guid_elem = gpo_elem.find(f"./{{{_ADMX_NS}}}Identifier")
                guid = _text_or_empty(guid_elem).strip("{}").lower()
                if not guid:
                    guid_elem2 = gpo_elem.find(f"./{{{_ADMX_NS}}}Guid")
                    guid = _text_or_empty(guid_elem2).strip("{}").lower()

                name_elem = gpo_elem.find(f"./{{{_ADMX_NS}}}DisplayName")
                display_name = _text_or_empty(name_elem)

                domain_elem = gpo_elem.find(f"./{{{_ADMX_NS}}}Domain")
                domain = _text_or_empty(domain_elem)

                machine_ext_elem = gpo_elem.find(f"./{{{_ADMX_NS}}}MachineExtensionGuids")
                user_ext_elem = gpo_elem.find(f"./{{{_ADMX_NS}}}UserExtensionGuids")

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

                sf_list: list[BackupSecurityFilter] = []
                sf_container = gpo_elem.find(f"./{{{_ADMX_NS}}}SecurityFilters")
                if sf_container is not None:
                    for sf_elem in sf_container:
                        if _local_name(sf_elem.tag) != "SecurityFilter":
                            continue
                        trustee_elem = sf_elem.find(f"./{{{_ADMX_NS}}}Trustee")
                        if trustee_elem is not None:
                            sid_elem = trustee_elem.find(f"./{{{_ADMX_NS}}}Sid")
                            sid = _text_or_empty(sid_elem)
                            name_elem = trustee_elem.find(f"./{{{_ADMX_NS}}}Name")
                            principal = _text_or_empty(name_elem)
                            type_elem = trustee_elem.find(f"./{{{_ADMX_NS}}}Type")
                            target_type_raw = (
                                _text_or_empty(type_elem).lower()
                                if type_elem is not None
                                else "group"
                            )
                        else:
                            sid = ""
                            principal = sf_elem.get("principal", "")
                            target_type_raw = sf_elem.get("target_type", "group").lower()
                        perm_elem = sf_elem.find(f"./{{{_ADMX_NS}}}Permission")
                        if perm_elem is not None:
                            perm_raw = _text_or_empty(perm_elem).lower()
                        else:
                            perm_raw = sf_elem.get("permission", "GpoApply").lower()
                        if perm_raw == "gpoapply":
                            permission = "apply"
                        elif perm_raw == "gporead":
                            permission = "read"
                        else:
                            raise BackupError(
                                f"Unsupported permission in security filter: {perm_raw!r}"
                            )
                        inh_elem = sf_elem.find(f"./{{{_ADMX_NS}}}Inheritable")
                        if inh_elem is not None:
                            inheritable = _text_or_empty(inh_elem).lower() == "true"
                        else:
                            inheritable = sf_elem.get("inheritable", "true").lower() == "true"
                        target_type = target_type_raw
                        if target_type not in _VALID_TARGET_TYPES:
                            raise BackupError(
                                f"Unsupported target_type in security filter: {target_type!r}"
                            )
                        sf_list.append(
                            BackupSecurityFilter(
                                principal=principal,
                                permission=permission,
                                inheritable=inheritable,
                                target_type=target_type,
                                sid=sid,
                            )
                        )

                wmi: BackupWmiFilter | None = None
                wmi_elem = gpo_elem.find(f"./{{{_ADMX_NS}}}WmiFilter")
                if wmi_elem is not None:
                    wmi = BackupWmiFilter(
                        name=wmi_elem.get("name", ""),
                        query=wmi_elem.get("query", ""),
                        language=wmi_elem.get("language", "WQL"),
                        description=wmi_elem.get("description", ""),
                    )

                gpos.append(
                    BackupGpo(
                        guid=guid,
                        display_name=display_name,
                        domain=domain,
                        machine_extensions=machine_exts,
                        user_extensions=user_exts,
                        security_filters=tuple(sf_list),
                        wmi_filter=wmi,
                    )
                )

    if not gpos:
        raise BackupError("No GPO entries found in manifest")

    return GpmcBackup(
        backup_time=backup_time, backup_id=backup_id, backup_type="", gpos=tuple(gpos)
    )


def _text_or_empty(elem: ET.Element | None) -> str:
    if elem is None:
        return ""
    return (elem.text or "").strip()


def read_backup(backup_dir: Path) -> GpmcBackup:
    """Read a complete GPMC backup directory."""
    if backup_dir.is_symlink():
        raise BackupError(f"Symlinks are not allowed: {backup_dir}")
    manifest_path = backup_dir / "manifest.xml"
    if manifest_path.is_symlink():
        raise BackupError(f"Symlinks are not allowed: {manifest_path}")
    if not manifest_path.exists():
        raise BackupError(f"Missing manifest.xml in {backup_dir}")

    manifest_data = read_file_bytes(manifest_path)
    backup = parse_manifest(manifest_data)

    bkup_info_path = backup_dir / "bkupInfo.xml"
    if bkup_info_path.is_symlink():
        raise BackupError(f"Symlinks are not allowed: {bkup_info_path}")
    if bkup_info_path.exists():
        bkup = parse_bkup_info(read_file_bytes(bkup_info_path))
        backup_time = bkup.backup_time or backup.backup_time
        backup_id = bkup.backup_id or backup.backup_id
        backup_type = bkup.backup_type or backup.backup_type
        bkup_gpo = bkup.gpos[0] if bkup.gpos else None
    else:
        backup_time = backup.backup_time
        backup_id = backup.backup_id
        backup_type = backup.backup_type
        bkup_gpo = None

    enriched_gpos: list[BackupGpo] = []
    for gpo in backup.gpos:
        if not _GUID_RE.match(gpo.guid):
            raise BackupError(f"Invalid GPO GUID in manifest: {gpo.guid!r}")
        gpo_dir = backup_dir / gpo.guid
        # A symlinked GPO directory would let a backup escape the inbox: the
        # per-side scan only rejects symlinks *within* the side dir, and checks
        # ``(gpo_dir / "Machine").is_symlink()`` — not gpo_dir itself. Guard it
        # here so the whole subtree (and the later Registry.pol read) stays
        # inside the validated backup directory.
        if gpo_dir.is_symlink():
            raise BackupError(f"Symlinks are not allowed in backup content: {gpo_dir}")

        display_name = gpo.display_name
        domain = gpo.domain
        if bkup_gpo is not None and bkup_gpo.guid == gpo.guid:
            display_name = bkup_gpo.display_name or display_name
            domain = bkup_gpo.domain or domain

        if not gpo_dir.exists():
            enriched_gpos.append(
                BackupGpo(
                    guid=gpo.guid,
                    display_name=display_name,
                    domain=domain,
                    machine_extensions=gpo.machine_extensions,
                    user_extensions=gpo.user_extensions,
                    security_filters=gpo.security_filters,
                    wmi_filter=gpo.wmi_filter,
                )
            )
            continue

        machine_exts = _scan_side(gpo_dir / "Machine", gpo.machine_extensions)
        user_exts = _scan_side(gpo_dir / "User", gpo.user_extensions)

        enriched_gpos.append(
            BackupGpo(
                guid=gpo.guid,
                display_name=display_name,
                domain=domain,
                machine_extensions=machine_exts,
                user_extensions=user_exts,
                security_filters=gpo.security_filters,
                wmi_filter=gpo.wmi_filter,
            )
        )

    return GpmcBackup(
        backup_time=backup_time,
        backup_id=backup_id,
        backup_type=backup_type,
        gpos=tuple(enriched_gpos),
    )


def _scan_directory_for_files(
    dir_path: Path, base: Path, depth: int = 0
) -> list[tuple[Path, str]]:
    if depth > _MAX_DEPTH:
        raise BackupError(f"Directory nesting depth exceeds {_MAX_DEPTH}")
    results: list[tuple[Path, str]] = []
    try:
        entries = list(os.scandir(dir_path))
    except OSError as error:
        raise BackupError(f"Cannot scan directory: {dir_path}") from error
    for entry in entries:
        if entry.is_symlink():
            raise BackupError(f"Symlinks are not allowed in backup content: {entry.path}")
        if entry.is_dir(follow_symlinks=False):
            results.extend(
                _scan_directory_for_files(Path(entry.path), base, depth + 1)
            )
        elif entry.is_file(follow_symlinks=False):
            rel = str(Path(entry.path).relative_to(base))
            results.append((Path(entry.path), rel))
    return sorted(results)


def _scan_side(side_dir: Path, extensions: tuple[CseExtension, ...]) -> tuple[CseExtension, ...]:
    if not side_dir.exists():
        return extensions
    if side_dir.is_symlink():
        raise BackupError(f"Symlinks are not allowed in backup content: {side_dir}")

    all_files: dict[str, CseFile] = {}
    for path, rel in _scan_directory_for_files(side_dir, side_dir):
        if ".." in Path(rel).parts:
            raise BackupError(f"Path traversal detected: {rel}")
        content_hash, size = _hash_file(path)
        all_files[rel] = CseFile(
            relative_path=rel, content_hash=content_hash, size=size
        )

    if not all_files:
        return extensions

    side_lit: Literal["machine", "user"] = (
        "machine" if side_dir.name == "Machine" else "user"
    )

    if not extensions:
        return (
            CseExtension(
                guid="unknown", side=side_lit, files=tuple(all_files.values())
            ),
        )

    ext_map = {ext.guid: ext for ext in extensions}
    files_by_ext: dict[str, list[CseFile]] = {ext.guid: [] for ext in extensions}
    unknown_files: list[CseFile] = []

    for rel, cse_file in all_files.items():
        rel_path = Path(rel)
        if rel_path.name == "Registry.pol" and _REGISTRY_CSE_GUID in ext_map:
            files_by_ext[_REGISTRY_CSE_GUID].append(cse_file)
            continue
        first_part = rel_path.parts[0] if rel_path.parts else ""
        if first_part in ext_map:
            files_by_ext[first_part].append(cse_file)
            continue
        unknown_files.append(cse_file)

    if unknown_files:
        first_guid = extensions[0].guid
        files_by_ext[first_guid].extend(unknown_files)

    result: list[CseExtension] = []
    for ext in extensions:
        result.append(
            CseExtension(
                guid=ext.guid, side=side_lit, files=tuple(files_by_ext[ext.guid])
            )
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
    if not _GUID_RE.match(gpo_guid):
        raise BackupError(f"Invalid GPO GUID: {gpo_guid!r}")
    side_dir_name = "Machine" if side == "machine" else "User"
    file_path = _safe_path(backup_dir / gpo_guid / side_dir_name, relative_path)
    if not file_path.exists():
        raise BackupError(f"File not found: {file_path}")
    return read_file_bytes(file_path)
