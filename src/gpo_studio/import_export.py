"""Domain logic for GPMC backup import/export, extracted from the API layer."""

from __future__ import annotations

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
    GppGroup,
    GppRegistry,
    GppScope,
    contains_cpassword,
    ensure_editor_ids,
    parse_gpp_collection,
    parse_gpp_groups,
    parse_gpp_registry,
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


_HANDLED_GPP_FILES = frozenset({
    "Preferences/Groups/Groups.xml",
    "Preferences/Registry/Registry.xml",
})


def collect_cse_metadata(backup_gpo: BackupGpo) -> tuple[CseMetadataEntry, ...]:
    metadata: list[CseMetadataEntry] = []
    for ext in (*backup_gpo.machine_extensions, *backup_gpo.user_extensions):
        if ext.guid == _REGISTRY_CSE_GUID:
            continue
        if ext.guid == "unknown" and all(
            f.relative_path == "Registry.pol" for f in ext.files
        ):
            continue
        non_gpp_files = [
            f for f in ext.files
            if f.relative_path.replace("\\", "/") not in _HANDLED_GPP_FILES
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


def collect_gpp_collections(backup_dir: Path, gpo_guid: str) -> tuple[GppCollection, ...]:
    """Parse GPP XML files from a backup directory."""
    collections: list[GppCollection] = []
    sides: list[tuple[str, GppScope]] = [("Machine", "computer"), ("User", "user")]
    for side_name, scope in sides:
        side_dir = backup_dir / gpo_guid / side_name / "Preferences"
        if not side_dir.exists():
            continue
        groups_path = side_dir / "Groups" / "Groups.xml"
        registry_path = side_dir / "Registry" / "Registry.xml"
        groups: tuple[GppGroup, ...] = ()
        registry: tuple[GppRegistry, ...] = ()
        if groups_path.exists():
            groups_data = read_file_bytes(groups_path)
            if contains_cpassword(groups_data):
                raise BackupError("cpassword detected in Groups.xml")
            groups = parse_gpp_groups(groups_data)
        if registry_path.exists():
            registry_data = read_file_bytes(registry_path)
            if contains_cpassword(registry_data):
                raise BackupError("cpassword detected in Registry.xml")
            registry = parse_gpp_registry(registry_data)
        if groups or registry:
            files: dict[str, bytes] = {}
            if groups_path.exists():
                files["Groups/Groups.xml"] = read_file_bytes(groups_path)
            if registry_path.exists():
                files["Registry/Registry.xml"] = read_file_bytes(registry_path)
            parsed_collection = parse_gpp_collection(scope, files)
            collections.append(
                ensure_editor_ids(
                    GppCollection(
                        scope=scope, groups=groups, registry=registry,
                        groups_unknown_attrs=parsed_collection.groups_unknown_attrs,
                        groups_unknown_children=parsed_collection.groups_unknown_children,
                        registry_unknown_attrs=parsed_collection.registry_unknown_attrs,
                        registry_unknown_children=parsed_collection.registry_unknown_children,
                    )
                )
            )
    return tuple(collections)
