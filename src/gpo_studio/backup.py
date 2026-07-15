"""Read GPMC backup directories and preserve content including unknown CSEs."""

from __future__ import annotations

import hashlib
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .gpp import contains_cpassword
from .model import StudioError
from .safe_io import (
    SafeOpenError,
    is_link_or_junction,
    iter_directory,
    open_directory,
    open_regular_file,
)
from .xml_safety import parse_xml_bounded

_MAX_FILE_SIZE = 50 * 1024 * 1024
_MAX_DEPTH = 100
_MAX_TOTAL_BACKUP_BYTES = 500 * 1024 * 1024
_MAX_TOTAL_FILE_COUNT = 10000
_MAX_BACKUP_GPO_COUNT = 100
_MAX_XML_ELEMENT_COUNT = 100000
_MAX_XML_TEXT_LENGTH = 1024 * 1024
_MAX_XML_ATTR_LENGTH = 4096
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


@dataclass
class _BackupBudget:
    total_bytes: int = 0
    entry_count: int = 0

    def add_bytes(self, size: int) -> None:
        self.total_bytes += size
        if self.total_bytes > _MAX_TOTAL_BACKUP_BYTES:
            raise BackupError(
                f"Total backup size exceeds {_MAX_TOTAL_BACKUP_BYTES} bytes"
            )

    def add_entry(self) -> None:
        self.entry_count += 1
        if self.entry_count > _MAX_TOTAL_FILE_COUNT:
            raise BackupError(
                f"Total entry count exceeds {_MAX_TOTAL_FILE_COUNT}"
            )

    def add_file(self, size: int) -> None:
        self.add_entry()
        self.add_bytes(size)


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _safe_parse(data: bytes) -> ET.Element:
    return parse_xml_bounded(
        data,
        max_size=_MAX_FILE_SIZE,
        max_elements=_MAX_XML_ELEMENT_COUNT,
        max_depth=_MAX_DEPTH,
        max_text_length=_MAX_XML_TEXT_LENGTH,
        max_attr_length=_MAX_XML_ATTR_LENGTH,
        error_class=BackupError,
    )


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
        if is_link_or_junction(current):
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
        fd = open_regular_file(path)
    except SafeOpenError:
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
        fd = open_regular_file(path)
    except SafeOpenError:
        raise BackupError(f"Cannot open file (symlink or inaccessible): {path}") from None
    try:
        content_hash, size, _ = _inspect_open_file(fd, path, collect_content=False)
        return content_hash, size
    finally:
        os.close(fd)


def _inspect_open_file(
    fd: int, path: Path, *, collect_content: bool
) -> tuple[str, int, bytes | None]:
    hasher = hashlib.sha256()
    size = 0
    data = bytearray() if collect_content else None
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
        if data is not None:
            data.extend(chunk)
    return hasher.hexdigest(), size, bytes(data) if data is not None else None


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
    if is_link_or_junction(backup_dir):
        raise BackupError(f"Symlinks are not allowed: {backup_dir}")
    manifest_path = backup_dir / "manifest.xml"
    if is_link_or_junction(manifest_path):
        raise BackupError(f"Symlinks are not allowed: {manifest_path}")
    if not manifest_path.exists():
        raise BackupError(f"Missing manifest.xml in {backup_dir}")

    budget = _BackupBudget()
    manifest_data = read_file_bytes(manifest_path)
    budget.add_file(len(manifest_data))
    backup = parse_manifest(manifest_data)
    if len(backup.gpos) > _MAX_BACKUP_GPO_COUNT:
        raise BackupError(
            f"Backup contains {len(backup.gpos)} GPOs, "
            f"exceeding limit of {_MAX_BACKUP_GPO_COUNT}"
        )

    bkup_info_path = backup_dir / "bkupInfo.xml"
    if is_link_or_junction(bkup_info_path):
        raise BackupError(f"Symlinks are not allowed: {bkup_info_path}")
    if bkup_info_path.exists():
        bkup_data = read_file_bytes(bkup_info_path)
        budget.add_file(len(bkup_data))
        bkup = parse_bkup_info(bkup_data)
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
        if is_link_or_junction(gpo_dir):
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

        machine_exts = _scan_side(gpo_dir / "Machine", gpo.machine_extensions, budget)
        user_exts = _scan_side(gpo_dir / "User", gpo.user_extensions, budget)

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


def _scan_directory_fd(
    dir_fd: int,
    dir_path: Path,
    relative_dir: Path,
    depth: int,
    budget: _BackupBudget,
    results: dict[str, CseFile],
) -> None:
    if depth > _MAX_DEPTH:
        raise BackupError(f"Directory nesting depth exceeds {_MAX_DEPTH}")
    try:
        for entry in iter_directory(dir_fd):
            budget.add_entry()
            entry_path = dir_path / entry.name
            relative_path = relative_dir / entry.name
            if entry.is_directory:
                _scan_directory_fd(
                    entry.fd,
                    entry_path,
                    relative_path,
                    depth + 1,
                    budget,
                    results,
                )
                continue

            check_cpassword = bool(
                relative_path.parts
                and relative_path.parts[0].casefold() == "preferences"
            )
            content_hash, size, content = _inspect_open_file(
                entry.fd,
                entry_path,
                collect_content=check_cpassword,
            )
            budget.add_bytes(size)
            if content is not None and contains_cpassword(content):
                raise BackupError(
                    f"cpassword detected in backup file: {relative_path}"
                )
            rel = str(relative_path)
            results[rel] = CseFile(
                relative_path=rel,
                content_hash=content_hash,
                size=size,
            )
    except SafeOpenError as error:
        if "link" in str(error).casefold() or "reparse" in str(error).casefold():
            raise BackupError(
                f"Symlinks are not allowed in backup content: {dir_path}"
            ) from error
        raise BackupError(f"Cannot scan directory: {dir_path}") from error


def _scan_side(
    side_dir: Path, extensions: tuple[CseExtension, ...], budget: _BackupBudget
) -> tuple[CseExtension, ...]:
    if not side_dir.exists():
        return extensions
    if is_link_or_junction(side_dir):
        raise BackupError(f"Symlinks are not allowed in backup content: {side_dir}")

    all_files: dict[str, CseFile] = {}
    try:
        side_fd = open_directory(side_dir)
    except SafeOpenError:
        raise BackupError(
            f"Cannot open directory (symlink or inaccessible): {side_dir}"
        ) from None
    try:
        _scan_directory_fd(
            side_fd,
            side_dir,
            Path(),
            0,
            budget,
            all_files,
        )
    finally:
        os.close(side_fd)

    all_files = dict(sorted(all_files.items()))
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
