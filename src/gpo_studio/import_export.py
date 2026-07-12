"""Domain logic for GPMC backup import/export, extracted from the API layer."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, cast

from .backup import (
    BackupGpo,
    BackupSecurityFilter,
    BackupWmiFilter,
    read_file_bytes,
)
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
        for prefix in ("HKLM\\", "HKCU\\", "HKLM/", "HKCU/"):
            if key.casefold().startswith(prefix.casefold()):
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


def collect_cse_metadata(backup_gpo: BackupGpo) -> tuple[CseMetadataEntry, ...]:
    metadata: list[CseMetadataEntry] = []
    for ext in (*backup_gpo.machine_extensions, *backup_gpo.user_extensions):
        if ext.guid == _REGISTRY_CSE_GUID:
            continue
        if ext.guid == "unknown" and all(
            f.relative_path == "Registry.pol" for f in ext.files
        ):
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
                    for f in ext.files
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
