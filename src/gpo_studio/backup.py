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
_NATIVE_BACKUP_ID_RE = re.compile(
    r"^\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}$"
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
    content_root: Path | None = None
    computer_enabled: bool = True
    user_enabled: bool = True


@dataclass(frozen=True, slots=True)
class GpmcBackup:
    backup_time: str
    backup_id: str
    backup_type: str = ""
    gpos: tuple[BackupGpo, ...] = field(default_factory=tuple)
    is_native: bool = False


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


def _validate_native_backup_id(backup_id: str) -> str:
    """Validate the braced GUID used by native GPMC exports."""
    if not _NATIVE_BACKUP_ID_RE.fullmatch(backup_id):
        raise BackupError(f"Invalid native backup ID: {backup_id!r}")
    return backup_id


def parse_bkup_info(data: bytes) -> GpmcBackup:
    """Parse bkupInfo.xml from a GPMC backup directory."""
    root = _safe_parse(data)

    if _local_name(root.tag) == "BackupInst":
        native = _parse_native_manifest(root)
        return GpmcBackup(
            backup_time=native.backup_time,
            backup_id=native.backup_id,
            backup_type="GPO",
            is_native=True,
            gpos=native.gpos,
        )

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
    """Parse manifest.xml from a GPMC backup directory.

    Supports both the native GPMC manifest format (Backups/BackupInst) and the
    Studio export format (BackupInstances/BackupInstance/GPO).
    """
    root = _safe_parse(data)
    root_local = _local_name(root.tag)

    if root_local == "Backups":
        return _parse_native_manifest(root)
    return _parse_studio_manifest(root)


def _parse_native_manifest(root: ET.Element) -> GpmcBackup:
    backup_time = ""
    backup_id = ""
    gpos: list[BackupGpo] = []

    for inst in root.iter():
        if _local_name(inst.tag) != "BackupInst":
            continue
        guid = ""
        display_name = ""
        domain = ""
        for child in inst:
            local = _local_name(child.tag)
            if local == "GPOGuid":
                guid = _text_or_empty(child).strip("{}").lower()
            elif local == "GPODisplayName":
                display_name = _text_or_empty(child)
            elif local == "GPODomain":
                domain = _text_or_empty(child)
            elif local == "BackupTime":
                backup_time = _text_or_empty(child)
            elif local == "ID":
                backup_id = _text_or_empty(child)
        if guid:
            gpos.append(BackupGpo(guid=guid, display_name=display_name, domain=domain))

    if not gpos:
        raise BackupError("No GPO entries found in manifest")

    _validate_native_backup_id(backup_id)

    return GpmcBackup(
        backup_time=backup_time,
        backup_id=backup_id,
        backup_type="",
        is_native=True,
        gpos=tuple(gpos),
    )


def _parse_studio_manifest(root: ET.Element) -> GpmcBackup:

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


def _parse_native_backup_options(data: bytes, expected_guid: str) -> tuple[bool, bool]:
    root = _safe_parse(data)
    core: ET.Element | None = None
    for elem in root.iter():
        if _local_name(elem.tag) == "GroupPolicyCoreSettings":
            core = elem
            break
    if core is None:
        raise BackupError("Backup.xml has no GroupPolicyCoreSettings")

    core_guid = ""
    options = 0
    for child in core:
        local = _local_name(child.tag)
        if local == "ID":
            core_guid = _text_or_empty(child).strip("{}").casefold()
        elif local == "Options":
            raw_options = _text_or_empty(child)
            try:
                options = int(raw_options or "0")
            except ValueError:
                raise BackupError(f"Invalid Backup.xml Options value: {raw_options!r}") from None
    if core_guid != expected_guid.casefold():
        raise BackupError(
            f"Backup.xml GPO ID {core_guid!r} does not match manifest {expected_guid!r}"
        )
    if options & ~3:
        raise BackupError(f"Unsupported Backup.xml Options flags: {options}")
    return not bool(options & 2), not bool(options & 1)


def _resolve_content_root(
    backup_dir: Path, backup_id: str, gpo_guid: str, *, is_native: bool
) -> Path | None:
    """Detect native GPMC or legacy Studio layout and return the content root.

    Native GPMC layout: {backup_dir}/{BACKUP_ID}/DomainSysvol/GPO
    Legacy Studio layout: {backup_dir}/{GPO_GUID}
    """
    native_path = (
        _safe_path(backup_dir, f"{backup_id}/DomainSysvol/GPO")
        if is_native and backup_id
        else None
    )
    legacy_path = _safe_path(backup_dir, gpo_guid)

    native_exists = native_path is not None and native_path.is_dir()
    legacy_exists = legacy_path.is_dir()

    if native_exists and legacy_exists:
        raise BackupError(
            f"Ambiguous backup layout: both native ({native_path}) and "
            f"legacy ({legacy_path}) content roots exist"
        )
    if native_exists:
        assert native_path is not None
        if is_link_or_junction(native_path):
            raise BackupError(f"Symlinks are not allowed in backup content: {native_path}")
        return native_path
    if legacy_exists:
        if is_link_or_junction(legacy_path):
            raise BackupError(f"Symlinks are not allowed in backup content: {legacy_path}")
        return legacy_path
    return None


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

    manifest_backup_id = (
        _validate_native_backup_id(backup.backup_id) if backup.is_native else None
    )
    native_bkup_info_path = (
        _safe_path(backup_dir, f"{manifest_backup_id}/bkupInfo.xml")
        if manifest_backup_id is not None
        else None
    )
    bkup_info_path = native_bkup_info_path
    if bkup_info_path is None or not bkup_info_path.exists():
        bkup_info_path = backup_dir / "bkupInfo.xml"
    if is_link_or_junction(bkup_info_path):
        raise BackupError(f"Symlinks are not allowed: {bkup_info_path}")
    bkup_gpo: BackupGpo | None
    if bkup_info_path.exists():
        bkup_data = read_file_bytes(bkup_info_path)
        budget.add_file(len(bkup_data))
        bkup = parse_bkup_info(bkup_data)
        if manifest_backup_id is not None:
            nested_backup_id = _validate_native_backup_id(bkup.backup_id)
            if nested_backup_id.casefold() != manifest_backup_id.casefold():
                raise BackupError(
                    f"Nested bkupInfo.xml ID {bkup.backup_id!r} does not match "
                    f"manifest backup ID {backup.backup_id!r}"
                )
            manifest_gpo_guids = {gpo.guid.casefold() for gpo in backup.gpos}
            for bkup_gpo in bkup.gpos:
                if bkup_gpo.guid.casefold() not in manifest_gpo_guids:
                    raise BackupError(
                        f"Native bkupInfo.xml GPOGuid {bkup_gpo.guid!r} does not "
                        "match a manifest GPO identity"
                    )
        backup_time = bkup.backup_time or backup.backup_time
        backup_id = (
            backup.backup_id
            if manifest_backup_id is not None
            else bkup.backup_id or backup.backup_id
        )
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

        display_name = gpo.display_name
        domain = gpo.domain
        if bkup_gpo is not None and bkup_gpo.guid == gpo.guid:
            display_name = bkup_gpo.display_name or display_name
            domain = bkup_gpo.domain or domain

        content_root = _resolve_content_root(
            backup_dir, backup_id, gpo.guid, is_native=backup.is_native
        )

        if content_root is None:
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

        machine_exts = _scan_side(content_root / "Machine", gpo.machine_extensions, budget)
        user_exts = _scan_side(content_root / "User", gpo.user_extensions, budget)
        is_native_layout = (
            content_root.name.casefold() == "gpo"
            and content_root.parent.name.casefold() == "domainsysvol"
        )
        backup_xml_path = (
            content_root.parent.parent / "Backup.xml"
            if is_native_layout
            else content_root / "Backup.xml"
        )
        computer_enabled = True
        user_enabled = True
        if is_link_or_junction(backup_xml_path):
            raise BackupError(f"Symlinks are not allowed: {backup_xml_path}")
        if backup_xml_path.exists():
            backup_xml = read_file_bytes(backup_xml_path)
            budget.add_file(len(backup_xml))
            computer_enabled, user_enabled = _parse_native_backup_options(
                backup_xml, gpo.guid
            )

        enriched_gpos.append(
            BackupGpo(
                guid=gpo.guid,
                display_name=display_name,
                domain=domain,
                machine_extensions=machine_exts,
                user_extensions=user_exts,
                security_filters=gpo.security_filters,
                wmi_filter=gpo.wmi_filter,
                content_root=content_root,
                computer_enabled=computer_enabled,
                user_enabled=user_enabled,
            )
        )

    return GpmcBackup(
        backup_time=backup_time,
        backup_id=backup_id,
        backup_type=backup_type,
        is_native=backup.is_native,
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
        "machine" if side_dir.name.casefold() == "machine" else "user"
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
        if rel_path.name.casefold() == "registry.pol" and _REGISTRY_CSE_GUID in ext_map:
            files_by_ext[_REGISTRY_CSE_GUID].append(cse_file)
            continue
        first_part = rel_path.parts[0] if rel_path.parts else ""
        if first_part in ext_map:
            files_by_ext[first_part].append(cse_file)
            continue
        unknown_files.append(cse_file)

    result: list[CseExtension] = []
    for ext in extensions:
        result.append(
            CseExtension(
                guid=ext.guid, side=side_lit, files=tuple(files_by_ext[ext.guid])
            )
        )
    if unknown_files:
        result.append(
            CseExtension(guid="unknown", side=side_lit, files=tuple(unknown_files))
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
    backup = read_backup(backup_dir)
    matching = [gpo for gpo in backup.gpos if gpo.guid == gpo_guid.casefold()]
    if not matching or matching[0].content_root is None:
        raise BackupError(f"GPO content not found in backup: {gpo_guid}")
    side_dir_name = "Machine" if side == "machine" else "User"
    file_path = _safe_path(matching[0].content_root / side_dir_name, relative_path)
    if not file_path.exists():
        raise BackupError(f"File not found: {file_path}")
    return read_file_bytes(file_path)
