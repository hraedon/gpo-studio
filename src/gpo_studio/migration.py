"""GPMC migration table parsing and application.

Ground truth (measured live on Windows Server 2025 GPMC, 2026-09-03, by
authoring a table via the ``GPMgmt.GPM`` COM API): GPMC-authored migration
tables are ``MigrationTable`` documents in the default namespace
``http://www.microsoft.com/GroupPolicy/GPOOperations/MigrationTable`` where
each ``Mapping`` carries plain-text ``Type``, ``Source`` and ``Destination``
child elements.  ``Type`` is the GPM COM entry-type name (``User``,
``Computer``, ``LocalGroup``, ``GlobalGroup``, ``UniversalGroup``,
``UNCPath``, ``Unknown``).  "Same as source" is expressed by the destination
being the same account as the source; an empty destination is an error in
GPMC itself, so it is rejected here too.

An earlier revision of this module assumed a ``GroupPolicy/Types`` namespace
with ``Mapping / Source|Destination / Identifier / Sid|Name`` children.  That
shape has no real-world basis — every ``.migtable`` in the repository tree
was hand-written by this project, and no Microsoft documentation or
GPMC-authored artifact uses it (see ``docs/plans-025-032-oracle-survey.md``
§4.1).  It was a guess whose namespace mismatch made this parser silently
return an empty table on real GPMC output.  It is removed; unrecognized
formats now fail loud.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from enum import IntEnum
from pathlib import Path

from .backup import BackupError, _safe_parse, _text_or_empty
from .model import GPO, SecurityFilter
from .safe_io import SafeOpenError, regular_file_descriptor

_MIGRATION_TABLE_NS = (
    "http://www.microsoft.com/GroupPolicy/GPOOperations/MigrationTable"
)
_MAX_MIGRATION_TABLE_SIZE = 10 * 1024 * 1024

_SID_RE = re.compile(r"S-1-\d+(?:-\d+)*", re.IGNORECASE)


class MigrationEntryType(IntEnum):
    """GPM COM ``EntryType*`` constants.

    Values measured on Windows Server 2025 GPMC (2026-09-03): User 0,
    Computer 1, LocalGroup 2, GlobalGroup 3, UniversalGroup 4, UNCPath 5,
    Unknown 6.  There is no ``EntryTypeDomainLocalGroup``.
    """

    USER = 0
    COMPUTER = 1
    LOCAL_GROUP = 2
    GLOBAL_GROUP = 3
    UNIVERSAL_GROUP = 4
    UNC_PATH = 5
    UNKNOWN = 6


_ENTRY_TYPE_BY_TEXT: dict[str, MigrationEntryType] = {
    "user": MigrationEntryType.USER,
    "computer": MigrationEntryType.COMPUTER,
    "localgroup": MigrationEntryType.LOCAL_GROUP,
    "globalgroup": MigrationEntryType.GLOBAL_GROUP,
    "universalgroup": MigrationEntryType.UNIVERSAL_GROUP,
    "uncpath": MigrationEntryType.UNC_PATH,
    "unknown": MigrationEntryType.UNKNOWN,
}


@dataclass(frozen=True, slots=True)
class MigrationEntry:
    source_sid: str
    target_sid: str
    source_name: str
    target_name: str
    entry_type: MigrationEntryType = MigrationEntryType.UNKNOWN


@dataclass(frozen=True, slots=True)
class MigrationTable:
    entries: tuple[MigrationEntry, ...] = field(default_factory=tuple)
    domain: str = ""


def _principal_parts(text: str) -> tuple[str, str]:
    """Split plain-text GPMC mapping text into (sid, name).

    GPMC writes account names (``DOMAIN\\name``) or UNC paths as plain text;
    a raw SID string is also accepted so SID-keyed filters keep matching.
    """
    if _SID_RE.fullmatch(text):
        return text, ""
    return "", text


def parse_migration_table(path: Path) -> MigrationTable:
    """Parse a GPMC migration table XML file.

    Raises :class:`BackupError` for anything that is not a recognized
    GPMC-authored migration table — never returns a silently empty table
    for unrecognized input.
    """
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

    expected_root = f"{{{_MIGRATION_TABLE_NS}}}MigrationTable"
    if root.tag != expected_root:
        # root.tag carries the actual namespace for namespaced documents
        # (e.g. "{http://www.microsoft.com/GroupPolicy/Types}MigrationTable"),
        # so the error names what was actually found.
        raise BackupError(
            "Unrecognized migration table format: root element is "
            f"{root.tag!r}, expected {expected_root!r} (GPMC-authored "
            "migration table). Refusing to treat this as an empty table."
        )

    entries: list[MigrationEntry] = []
    for mapping in root.iter(f"{{{_MIGRATION_TABLE_NS}}}Mapping"):
        type_elem = mapping.find(f"./{{{_MIGRATION_TABLE_NS}}}Type")
        source_elem = mapping.find(f"./{{{_MIGRATION_TABLE_NS}}}Source")
        dest_elem = mapping.find(f"./{{{_MIGRATION_TABLE_NS}}}Destination")
        if type_elem is None or source_elem is None or dest_elem is None:
            missing = " and ".join(
                name
                for name, elem in (
                    ("Type", type_elem),
                    ("Source", source_elem),
                    ("Destination", dest_elem),
                )
                if elem is None
            )
            raise BackupError(f"Migration table Mapping missing {missing} element(s)")

        type_text = _text_or_empty(type_elem).strip()
        entry_type = _ENTRY_TYPE_BY_TEXT.get(type_text.casefold())
        if entry_type is None:
            raise BackupError(
                f"Migration table Mapping has unrecognized Type {type_text!r}"
            )

        source_text = _text_or_empty(source_elem).strip()
        if not source_text:
            raise BackupError("Migration table Mapping has empty Source")
        # "Same as source" is expressed by Destination == Source (an empty
        # destination is an error in GPMC itself), so equality is valid here.
        dest_text = _text_or_empty(dest_elem).strip()
        if not dest_text:
            raise BackupError("Migration table Mapping has empty Destination")

        source_sid, source_name = _principal_parts(source_text)
        target_sid, target_name = _principal_parts(dest_text)
        entries.append(
            MigrationEntry(
                source_sid=source_sid,
                target_sid=target_sid,
                source_name=source_name,
                target_name=target_name,
                entry_type=entry_type,
            )
        )

    # GPMC migration tables carry no Domain element; the field is kept for
    # model stability and remains empty.
    return MigrationTable(entries=tuple(entries), domain="")


def apply_migration(gpo: GPO, table: MigrationTable) -> GPO:
    """Apply migration table to a GPO's security filters, replacing SIDs and principals."""
    if not table.entries:
        return gpo

    sid_map: dict[str, MigrationEntry] = {}
    name_map: dict[str, MigrationEntry] = {}
    for mig_entry in table.entries:
        # UNC-path entries map shares, not security principals, and can never
        # legitimately match a security filter.
        if mig_entry.entry_type == MigrationEntryType.UNC_PATH:
            continue
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
