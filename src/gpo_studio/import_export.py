"""Domain logic for GPMC backup import/export, extracted from the API layer."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, cast

from .backup import (
    BackupError,
    BackupGpo,
    BackupSecurityFilter,
    BackupWmiFilter,
    read_file_bytes,
)
from .gpp import (
    GppCollection,
    GppScope,
    contains_cpassword,
    ensure_editor_ids,
    parse_gpp_collection,
)
from .gpp_adapters import ADAPTER_FILE_PATHS
from .model import (
    GPO,
    CseFileEntry,
    CseMetadataEntry,
    RegistrySetting,
    RegistryType,
    SecurityFilter,
    Side,
    StudioError,
    ValidationError,
    ValidationIssue,
    WmiFilter,
)
from .registry_pol import parse as parse_pol
from .safe_io import is_link_or_junction
from .store import WorkspaceStore, gpo_from_dict

_VALID_REGISTRY_TYPES = {
    "REG_SZ", "REG_EXPAND_SZ", "REG_BINARY",
    "REG_DWORD", "REG_MULTI_SZ", "REG_QWORD",
}
_VALID_ACTIONS = {"set", "delete"}
_REGISTRY_CSE_GUID = "{35378EAC-683F-11D2-A89A-00C04FBBCFA2}"


def extract_settings(pol_path: Path, side: Side) -> list[RegistrySetting]:
    if not pol_path.exists():
        return []
    data = read_file_bytes(pol_path)
    records = parse_pol(data)
    hive: Literal["HKLM", "HKCU"] = "HKLM" if side == "computer" else "HKCU"
    settings: list[RegistrySetting] = []
    for i, record in enumerate(records):
        key = record.key
        for prefix, prefix_hive in (
            ("HKLM\\", "HKLM"), ("HKCU\\", "HKCU"),
            ("HKLM/", "HKLM"), ("HKCU/", "HKCU"),
            ("HKEY_LOCAL_MACHINE\\", "HKLM"),
            ("HKEY_CURRENT_USER\\", "HKCU"),
            ("HKEY_LOCAL_MACHINE/", "HKLM"),
            ("HKEY_CURRENT_USER/", "HKCU"),
        ):
            if key.casefold().startswith(prefix.casefold()):
                if prefix_hive != hive:
                    raise ValidationError([
                        ValidationIssue(
                            severity="error",
                            code="registry_hive_side_mismatch",
                            message=(
                                f"Registry key hive {prefix_hive} conflicts with "
                                f"the {side} policy side."
                            ),
                            path=f"imported/{side}/{i}/key",
                        )
                    ])
                key = key[len(prefix):]
                break
        if record.registry_type not in _VALID_REGISTRY_TYPES:
            raise ValidationError([
                ValidationIssue(
                    severity="error",
                    code="invalid_registry_type",
                    message=f"Unknown registry type from PReg: {record.registry_type}",
                    path=f"imported/{side}/{i}",
                )
            ])
        if record.action not in _VALID_ACTIONS:
            raise ValidationError([
                ValidationIssue(
                    severity="error",
                    code="invalid_action",
                    message=f"Unknown action from PReg: {record.action}",
                    path=f"imported/{side}/{i}",
                )
            ])
        settings.append(
            RegistrySetting(
                id=f"imported-{side}-{i}",
                side=side,
                hive=hive,
                key=key,
                value_name=record.value_name,
                registry_type=cast(RegistryType, record.registry_type),
                value=record.value,
                action=cast(Literal["set", "delete"], record.action),
            )
        )
    return settings


_GPP_DISCOVERY_PATHS: frozenset[str] = frozenset(
    f"Preferences/{path}" for path in set(ADAPTER_FILE_PATHS.values())
) | frozenset({"Preferences/Registry/Registry.xml"})

_HANDLED_GPP_FILES = _GPP_DISCOVERY_PATHS


def collect_cse_metadata(backup_gpo: BackupGpo) -> tuple[CseMetadataEntry, ...]:
    metadata: list[CseMetadataEntry] = []
    for ext in (*backup_gpo.machine_extensions, *backup_gpo.user_extensions):
        if ext.guid == _REGISTRY_CSE_GUID:
            continue
        if ext.guid == "unknown" and all(
            f.relative_path.casefold() == "registry.pol" for f in ext.files
        ):
            continue
        non_gpp_files = [
            f for f in ext.files
            if f.relative_path.replace("\\", "/") not in _HANDLED_GPP_FILES
            and f.relative_path.casefold() != "registry.pol"
        ]
        if not non_gpp_files:
            continue
        metadata.append(
            CseMetadataEntry(
                guid=ext.guid,
                side=ext.side,
                files=tuple(
                    CseFileEntry(
                        relative_path=f.relative_path,
                        content_hash=f.content_hash,
                        size=f.size,
                    )
                    for f in non_gpp_files
                ),
            )
        )
    return tuple(metadata)


def resolve_gpo(store: WorkspaceStore, ref: str | dict[str, Any]) -> GPO:
    if isinstance(ref, str):
        return store.get_gpo(ref)
    try:
        return gpo_from_dict(ref)
    except (KeyError, TypeError, ValueError) as error:
        raise StudioError(f"Invalid inline GPO reference: {error}") from error


def backup_security_filters_to_model(
    filters: tuple[BackupSecurityFilter, ...],
) -> tuple[SecurityFilter, ...]:
    return tuple(
        SecurityFilter(
            id=f"imported-sf-{i}",
            principal=f.principal,
            permission=cast(Literal["apply", "read"], f.permission),
            inheritable=f.inheritable,
            target_type=cast(Literal["user", "group", "computer"], f.target_type),
            sid=f.sid,
        )
        for i, f in enumerate(filters)
    )


def backup_wmi_filter_to_model(wmi: BackupWmiFilter | None) -> WmiFilter | None:
    if wmi is None:
        return None
    return WmiFilter(
        id="imported-wmi-0",
        name=wmi.name,
        description=wmi.description,
        query=wmi.query,
        language=wmi.language,
    )


def _resolve_case_insensitive(base: Path, relative: str) -> Path | None:
    """Resolve *relative* under *base* case-insensitively.

    Returns the resolved path, or ``None`` if no match exists.  Raises
    :class:`BackupError` when multiple case variants collide (ambiguous),
    when path components contain traversal or NUL bytes, or when any
    component of the resolved path is a symlink.
    """
    parts = Path(relative).parts
    if any(p in ("", ".", "..") or "\x00" in p for p in parts):
        raise BackupError(f"Unsafe path component in {relative!r}")
    current = base
    for part in parts:
        if not current.is_dir():
            return None
        try:
            entries = os.listdir(current)
        except OSError:
            return None
        exact = [e for e in entries if e == part]
        if exact:
            current = current / exact[0]
        else:
            ci_matches = [e for e in entries if e.casefold() == part.casefold()]
            if len(ci_matches) > 1:
                raise BackupError(
                    f"Ambiguous case-insensitive match for {part!r} in {current}: "
                    f"{sorted(ci_matches)}"
                )
            if not ci_matches:
                return None
            current = current / ci_matches[0]
        if is_link_or_junction(current):
            raise BackupError(f"Symlink detected at intermediate path: {current}")
    if current.exists():
        return current
    return None


def _check_containment(base: Path, target: Path, relative: str) -> None:
    resolved = target.resolve()
    base_resolved = base.resolve()
    try:
        resolved.relative_to(base_resolved)
    except ValueError:
        raise BackupError(f"Path escapes content root: {relative}") from None


def extract_side_settings(content_root: Path, side: Side) -> list[RegistrySetting]:
    """Read a side's Registry.pol using Windows case-insensitive path rules."""
    side_name = "Machine" if side == "computer" else "User"
    resolved = _resolve_case_insensitive(content_root, f"{side_name}/Registry.pol")
    if resolved is None:
        return []
    _check_containment(content_root, resolved, f"{side_name}/Registry.pol")
    return extract_settings(resolved, side)


def collect_gpp_collections(content_root: Path) -> tuple[GppCollection, ...]:
    """Parse GPP XML files from a resolved backup content root."""
    collections: list[GppCollection] = []
    sides: list[tuple[str, GppScope]] = [("Machine", "computer"), ("User", "user")]
    for side_name, scope in sides:
        pref_dir = content_root / side_name / "Preferences"
        if not pref_dir.exists():
            continue
        files: dict[str, bytes] = {}
        for rel_path in sorted(_GPP_DISCOVERY_PATHS):
            sub_path = rel_path.removeprefix("Preferences/")
            resolved = _resolve_case_insensitive(pref_dir, sub_path)
            if resolved is None:
                continue
            if is_link_or_junction(resolved):
                raise BackupError(f"Symlink not allowed: {sub_path}")
            _check_containment(pref_dir, resolved, sub_path)
            data = read_file_bytes(resolved)
            if contains_cpassword(data):
                raise BackupError(f"cpassword detected in {sub_path}")
            files[sub_path] = data
        if files:
            parsed_collection = parse_gpp_collection(scope, files)
            collections.append(ensure_editor_ids(parsed_collection))
    return tuple(collections)
