"""Directory-backed WMI filter catalogue for reusable filter definitions."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .model import StudioError

_MAX_FILE_SIZE = 50 * 1024 * 1024


class WmiCatalogueError(StudioError):
    """Malformed or unsupported WMI catalogue content."""


@dataclass(frozen=True, slots=True)
class WmiFilterEntry:
    id: str
    name: str
    query: str = ""
    language: str = "WQL"
    description: str = ""


@dataclass(frozen=True, slots=True)
class WmiCatalogue:
    filters: tuple[WmiFilterEntry, ...] = field(default_factory=tuple)


def _read_file_safe(path: Path) -> bytes:
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise WmiCatalogueError(
            f"Cannot open file (symlink or inaccessible): {path}"
        ) from None
    try:
        data = b""
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
            if len(data) > _MAX_FILE_SIZE:
                raise WmiCatalogueError(
                    f"File exceeds {_MAX_FILE_SIZE} bytes: {path}"
                )
        return data
    finally:
        os.close(fd)


def load_wmi_catalogue(path: Path) -> WmiCatalogue:
    """Load a WMI filter catalogue from a JSON file."""
    if not path.is_file():
        raise WmiCatalogueError(f"WMI catalogue file not found: {path}")
    raw = _read_file_safe(path)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise WmiCatalogueError(f"Invalid JSON in WMI catalogue: {error}") from error
    if not isinstance(data, dict):
        raise WmiCatalogueError("WMI catalogue root must be a JSON object")
    filters_raw = data.get("filters", [])
    if not isinstance(filters_raw, list):
        raise WmiCatalogueError("'filters' must be a list")
    entries: list[WmiFilterEntry] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(filters_raw):
        if not isinstance(item, dict):
            raise WmiCatalogueError(
                f"Filter at index {index} must be a JSON object"
            )
        entry_id = str(item.get("id") or "").strip()
        if not entry_id:
            raise WmiCatalogueError(
                f"Filter at index {index} is missing a non-empty 'id'"
            )
        if entry_id in seen_ids:
            raise WmiCatalogueError(
                f"Duplicate filter id at index {index}: {entry_id}"
            )
        seen_ids.add(entry_id)
        name = str(item.get("name") or "").strip()
        if not name:
            raise WmiCatalogueError(
                f"Filter at index {index} is missing a non-empty 'name'"
            )
        entries.append(
            WmiFilterEntry(
                id=entry_id,
                name=name,
                query=str(item.get("query") or ""),
                language=str(item.get("language") or "WQL"),
                description=str(item.get("description") or ""),
            )
        )
    return WmiCatalogue(filters=tuple(entries))
