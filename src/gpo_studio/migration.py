"""GPMC migration table parsing and application."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from .backup import BackupError, _safe_parse, _text_or_empty
from .model import GPO, SecurityFilter
from .safe_io import SafeOpenError, regular_file_descriptor

_GPMC_NS = "http://www.microsoft.com/GroupPolicy/Types"
_MAX_MIGRATION_TABLE_SIZE = 10 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class MigrationEntry:
    source_sid: str
    target_sid: str
    source_name: str
    target_name: str


@dataclass(frozen=True, slots=True)
class MigrationTable:
    entries: tuple[MigrationEntry, ...] = field(default_factory=tuple)
    domain: str = ""


def parse_migration_table(path: Path) -> MigrationTable:
    """Parse a GPMC migration table XML file."""
    try:
        with regular_file_descriptor(path) as fd:
            data = bytearray()
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > _MAX_MIGRATION_TABLE_SIZE:
                    raise BackupError(
                        f"Migration table exceeds {_MAX_MIGRATION_TABLE_SIZE} bytes"
                    )
    except SafeOpenError as error:
        raise BackupError(
            f"Cannot open migration table (symlink or inaccessible): {error}"
        ) from None
    root = _safe_parse(bytes(data))

    domain = ""
    domain_elem = root.find(f"./{{{_GPMC_NS}}}Domain")
    if domain_elem is not None:
        domain = _text_or_empty(domain_elem)

    entries: list[MigrationEntry] = []
    for mapping in root.iter(f"{{{_GPMC_NS}}}Mapping"):
        source = mapping.find(f"./{{{_GPMC_NS}}}Source")
        dest = mapping.find(f"./{{{_GPMC_NS}}}Destination")
        if source is None or dest is None:
            raise BackupError("Migration table Mapping missing Source or Destination")

        src_identifier = source.find(f"./{{{_GPMC_NS}}}Identifier")
        dst_identifier = dest.find(f"./{{{_GPMC_NS}}}Identifier")
        if src_identifier is None or dst_identifier is None:
            raise BackupError(
                "Migration table Mapping missing Identifier in Source or Destination"
            )

        src_sid_elem = src_identifier.find(f"./{{{_GPMC_NS}}}Sid")
        src_name_elem = src_identifier.find(f"./{{{_GPMC_NS}}}Name")
        dst_sid_elem = dst_identifier.find(f"./{{{_GPMC_NS}}}Sid")
        dst_name_elem = dst_identifier.find(f"./{{{_GPMC_NS}}}Name")

        source_sid = _text_or_empty(src_sid_elem) if src_sid_elem is not None else ""
        source_name = _text_or_empty(src_name_elem) if src_name_elem is not None else ""
        target_sid = _text_or_empty(dst_sid_elem) if dst_sid_elem is not None else ""
        target_name = _text_or_empty(dst_name_elem) if dst_name_elem is not None else ""

        if not source_sid and not source_name:
            raise BackupError(
                "Migration table Mapping has empty Source (no Sid or Name)"
            )
        if not target_sid and not target_name:
            raise BackupError(
                "Migration table Mapping has empty Destination (no Sid or Name)"
            )

        entries.append(
            MigrationEntry(
                source_sid=source_sid,
                target_sid=target_sid,
                source_name=source_name,
                target_name=target_name,
            )
        )

    return MigrationTable(entries=tuple(entries), domain=domain)


def apply_migration(gpo: GPO, table: MigrationTable) -> GPO:
    """Apply migration table to a GPO's security filters, replacing SIDs and principals."""
    if not table.entries:
        return gpo

    sid_map: dict[str, MigrationEntry] = {}
    name_map: dict[str, MigrationEntry] = {}
    for mig_entry in table.entries:
        if mig_entry.source_sid:
            sid_map[mig_entry.source_sid.casefold()] = mig_entry
        if mig_entry.source_name:
            name_map[mig_entry.source_name.casefold()] = mig_entry

    new_filters: list[SecurityFilter] = []
    for sf in gpo.security_filters:
        sid_entry = sid_map.get(sf.sid.casefold()) if sf.sid else None
        entry: MigrationEntry | None = (
            sid_entry if sid_entry is not None
            else name_map.get(sf.principal.casefold())
        )
        if entry is not None:
            if not entry.target_sid and not entry.target_name:
                new_filters.append(sf)
            else:
                new_filters.append(
                    replace(
                        sf,
                        sid=entry.target_sid or sf.sid,
                        principal=entry.target_name or sf.principal,
                    )
                )
        else:
            new_filters.append(sf)

    return replace(gpo, security_filters=tuple(new_filters))
