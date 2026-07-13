"""FastAPI delivery layer for the local workspace."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import __version__
from .admx import AdmxCatalogue, AdmxError, load_catalogue
from .backup import read_backup
from .canonical import policy_semantic_sha256, review_model_sha256
from .diff import diff_gpos, three_way_diff
from .estate import parse_estate
from .export import export_bundle, gpmc_backup_bundle, powershell_plan
from .gpp import GppGroup, GppGroupMember, GppRegistry, GppRegistryValue
from .identity import ClaimedIdentity, claimed_identity
from .ilt import IltFilter, IltPredicate
from .import_export import (
    backup_security_filters_to_model,
    backup_wmi_filter_to_model,
    collect_cse_metadata,
    collect_gpp_collections,
    extract_settings,
    resolve_gpo,
)
from .model import (
    GPO,
    ConflictError,
    NotFoundError,
    StudioError,
    ValidationError,
    ValidationIssue,
)
from .numeric import coerce_dword_qword
from .policy_config import PolicyConfiguration, resolve_policy
from .store import WorkspaceStore, gpo_from_dict
from .validation import validate_gpo, validate_setting
from .wmi_catalogue import WmiCatalogue, WmiCatalogueError, load_wmi_catalogue

STATIC = Path(__file__).with_name("static")


class Audit(BaseModel):
    """Base model for audited mutation requests.

    Optimistic concurrency uses the JSON-body ``expected_revision`` field rather
    than HTTP ``If-Match`` headers.  This is a deliberate choice for the
    browser-first, single-operator deployment model where every mutation is an
    explicit, auditable request body rather than a side-effect of transport
    metadata.
    """

    actor: str = Field(default="local-operator", min_length=1, max_length=120)
    reason: str = Field(default="Interactive edit", min_length=1, max_length=500)
    expected_revision: int = Field(ge=1)


class CreateGPO(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    actor: str = Field(default="local-operator", min_length=1, max_length=120)
    reason: str = Field(default="Create draft", min_length=1, max_length=500)


class MetadataMutation(Audit):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    computer_enabled: bool = True
    user_enabled: bool = True
    status: Literal["draft", "ready", "archived"] = "draft"
    domain: str = Field(default="studio.local", max_length=255)


class SettingData(BaseModel):
    side: Literal["computer", "user"]
    hive: Literal["HKLM", "HKCU"]
    key: str = Field(min_length=1, max_length=1000)
    value_name: str = Field(max_length=255)
    registry_type: Literal[
        "REG_SZ", "REG_EXPAND_SZ", "REG_BINARY", "REG_DWORD", "REG_MULTI_SZ", "REG_QWORD"
    ]
    value: str | int | list[str]
    action: Literal["set", "delete"] = "set"
    comment: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def _normalize_numeric_value(self) -> SettingData:
        if self.registry_type in ("REG_DWORD", "REG_QWORD"):
            if not isinstance(self.value, str):
                raise ValueError(
                    f"{self.registry_type} requires a canonical decimal string value"
                )
            self.value = coerce_dword_qword(self.value, self.registry_type)
        return self


class SettingMutation(Audit):
    setting: SettingData


class LinkData(BaseModel):
    target: str = Field(min_length=1, max_length=1000)
    enabled: bool = True
    enforced: bool = False
    order: int = Field(default=1, ge=1, le=999)


class LinkMutation(Audit):
    link: LinkData


class SecurityFilterData(BaseModel):
    principal: str = Field(min_length=1, max_length=255)
    permission: Literal["apply", "read"] = "apply"
    inheritable: bool = True
    target_type: Literal["user", "group", "computer"] = "group"
    sid: str = Field(default="", max_length=255)


class SecurityFilterMutation(Audit):
    filter: SecurityFilterData


class WmiFilterData(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    query: str = Field(default="", max_length=4000)
    language: str = Field(default="WQL", max_length=50)


class WmiFilterMutation(Audit):
    wmi_filter: WmiFilterData


class DeleteMutation(Audit):
    pass


class RestoreMutation(Audit):
    pass


class IltPredicateData(BaseModel):
    type: Literal["ou", "group", "registry", "ip_range", "environment", "wmi_query"]
    negate: bool = False
    value: str = ""


class IltFilterData(BaseModel):
    predicates: list[IltPredicateData] = Field(default_factory=list)


class GppGroupMemberData(BaseModel):
    sid: str = Field(min_length=1, max_length=255)
    name: str = Field(default="", max_length=255)
    action: Literal["add", "replace", "remove", "update"] = "add"
    id: str = ""


class GppGroupData(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    sid: str = Field(default="", max_length=255)
    action: Literal["add", "replace", "remove", "update"] = "update"
    description: str = Field(default="", max_length=2000)
    remove_all_users: bool = False
    remove_all_groups: bool = False
    members: list[GppGroupMemberData] = Field(default_factory=list)
    id: str = ""
    ilt_filter: IltFilterData | None = None


_GPP_REGISTRY_TYPES = Literal[
    "REG_SZ", "REG_EXPAND_SZ", "REG_BINARY", "REG_DWORD", "REG_MULTI_SZ", "REG_QWORD"
]


class GppRegistryValueData(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    value: str | int | list[str]
    registry_type: _GPP_REGISTRY_TYPES = "REG_SZ"
    action: Literal["create", "replace", "update", "delete"] = "create"
    id: str = ""

    @model_validator(mode="after")
    def _normalize_numeric_value(self) -> GppRegistryValueData:
        if self.registry_type in ("REG_DWORD", "REG_QWORD"):
            if not isinstance(self.value, str):
                raise ValueError(
                    f"{self.registry_type} requires a canonical decimal string value"
                )
            self.value = coerce_dword_qword(self.value, self.registry_type)
        return self


class GppRegistryData(BaseModel):
    key: str = Field(min_length=1, max_length=1000)
    action: Literal["add", "replace", "remove", "update"] = "update"
    values: list[GppRegistryValueData] = Field(default_factory=list)
    id: str = ""
    ilt_filter: IltFilterData | None = None


class GppGroupMutation(Audit):
    scope: Literal["computer", "user"]
    group: GppGroupData

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "scope": "computer",
                    "group": {
                        "name": "Administrators",
                        "sid": "S-1-5-32-544",
                        "action": "add",
                        "members": [
                            {
                                "sid": "S-1-5-21-1-2-3-500",
                                "name": "STUDIO\\Domain Admins",
                                "action": "add",
                            }
                        ],
                    },
                    "actor": "local-operator",
                    "reason": "Add local admins group",
                    "expected_revision": 1,
                },
                {
                    "scope": "computer",
                    "group": {
                        "name": "Administrators",
                        "sid": "S-1-5-32-544",
                        "action": "replace",
                        "members": [
                            {
                                "sid": "S-1-5-21-1-2-3-500",
                                "name": "STUDIO\\Domain Admins",
                                "action": "add",
                            }
                        ],
                    },
                    "actor": "local-operator",
                    "reason": "Replace local admins group membership",
                    "expected_revision": 2,
                },
                {
                    "scope": "computer",
                    "group": {
                        "name": "Administrators",
                        "sid": "S-1-5-32-544",
                        "action": "update",
                        "description": "Grant Studio Domain Admins local admin rights",
                        "members": [
                            {
                                "sid": "S-1-5-21-1-2-3-500",
                                "name": "STUDIO\\Domain Admins",
                                "action": "add",
                            }
                        ],
                    },
                    "actor": "local-operator",
                    "reason": "Update local admins group",
                    "expected_revision": 3,
                },
                {
                    "scope": "computer",
                    "group": {
                        "name": "OldAdmins",
                        "sid": "S-1-5-32-547",
                        "action": "remove",
                    },
                    "actor": "local-operator",
                    "reason": "Remove obsolete local admins group",
                    "expected_revision": 4,
                },
                {
                    "scope": "computer",
                    "group": {
                        "name": "Administrators",
                        "sid": "S-1-5-32-544",
                        "action": "update",
                        "description": "Targeted local admins with ILT",
                        "members": [
                            {
                                "sid": "S-1-5-21-1-2-3-500",
                                "name": "STUDIO\\Domain Admins",
                                "action": "add",
                            }
                        ],
                        "ilt_filter": {
                            "predicates": [
                                {
                                    "type": "ou",
                                    "value": "OU=Servers,DC=studio,DC=local",
                                },
                                {
                                    "type": "group",
                                    "value": "S-1-5-21-1-2-3-1001",
                                },
                                {
                                    "type": "registry",
                                    "value": "Software\\Policies\\Test\\Enabled",
                                },
                                {
                                    "type": "ip_range",
                                    "value": "192.168.1.0/24",
                                },
                                {
                                    "type": "environment",
                                    "value": "COMPUTERNAME=WORKSTATION",
                                },
                                {
                                    "type": "wmi_query",
                                    "value": "SELECT * FROM Win32_OperatingSystem",
                                },
                            ]
                        },
                    },
                    "actor": "local-operator",
                    "reason": "Add group with item-level targeting",
                    "expected_revision": 5,
                },
            ]
        }
    )


class GppRegistryMutation(Audit):
    scope: Literal["computer", "user"]
    registry: GppRegistryData

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "scope": "computer",
                    "registry": {
                        "key": "Software\\Policies\\Studio",
                        "action": "update",
                        "values": [
                            {
                                "name": "Enabled",
                                "value": "1",
                                "registry_type": "REG_DWORD",
                                "action": "create",
                            },
                            {
                                "name": "InstallPath",
                                "value": "C:\\Program Files\\Studio",
                                "registry_type": "REG_SZ",
                                "action": "replace",
                            },
                            {
                                "name": "Servers",
                                "value": [
                                    "dc01.studio.local",
                                    "dc02.studio.local",
                                ],
                                "registry_type": "REG_MULTI_SZ",
                                "action": "update",
                            },
                            {
                                "name": "Blob",
                                "value": "deadbeef",
                                "registry_type": "REG_BINARY",
                                "action": "delete",
                            },
                            {
                                "name": "Counter",
                                "value": "18446744073709551615",
                                "registry_type": "REG_QWORD",
                                "action": "create",
                            },
                            {
                                "name": "SystemPath",
                                "value": "%SystemRoot%\\System32",
                                "registry_type": "REG_EXPAND_SZ",
                                "action": "create",
                            },
                        ],
                    },
                    "actor": "local-operator",
                    "reason": "Configure studio policy registry values",
                    "expected_revision": 1,
                },
                {
                    "scope": "user",
                    "registry": {
                        "key": "Software\\Policies\\Studio\\User",
                        "action": "add",
                        "values": [
                            {
                                "name": "Theme",
                                "value": "Dark",
                                "registry_type": "REG_SZ",
                                "action": "create",
                            }
                        ],
                    },
                    "actor": "local-operator",
                    "reason": "Add user registry preference",
                    "expected_revision": 2,
                },
                {
                    "scope": "computer",
                    "registry": {
                        "key": "Software\\Policies\\Studio",
                        "action": "replace",
                        "values": [
                            {
                                "name": "Enabled",
                                "value": "0",
                                "registry_type": "REG_DWORD",
                                "action": "replace",
                            }
                        ],
                    },
                    "actor": "local-operator",
                    "reason": "Replace studio registry preference",
                    "expected_revision": 3,
                },
                {
                    "scope": "computer",
                    "registry": {
                        "key": "Software\\Policies\\Studio\\Legacy",
                        "action": "remove",
                        "values": [],
                    },
                    "actor": "local-operator",
                    "reason": "Remove legacy studio registry preference",
                    "expected_revision": 4,
                },
            ]
        }
    )


class GppMemberMutation(Audit):
    scope: Literal["computer", "user"]
    member: GppGroupMemberData

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "scope": "computer",
                    "member": {
                        "sid": "S-1-5-21-1-2-3-500",
                        "name": "STUDIO\\Domain Admins",
                        "action": "add",
                    },
                    "actor": "local-operator",
                    "reason": "Add member to local admins group",
                    "expected_revision": 1,
                },
                {
                    "scope": "computer",
                    "member": {
                        "sid": "S-1-5-21-1-2-3-500",
                        "name": "STUDIO\\Domain Admins",
                        "action": "remove",
                    },
                    "actor": "local-operator",
                    "reason": "Remove member from local admins group",
                    "expected_revision": 2,
                },
                {
                    "scope": "computer",
                    "member": {
                        "sid": "S-1-5-21-1-2-3-513",
                        "name": "STUDIO\\Domain Users",
                        "action": "replace",
                    },
                    "actor": "local-operator",
                    "reason": "Replace member entry in local admins group",
                    "expected_revision": 3,
                },
                {
                    "scope": "computer",
                    "member": {
                        "sid": "S-1-5-21-1-2-3-513",
                        "name": "STUDIO\\Domain Users",
                        "action": "update",
                    },
                    "actor": "local-operator",
                    "reason": "Update member entry in local admins group",
                    "expected_revision": 4,
                },
            ]
        }
    )


class GppRegistryValueMutation(Audit):
    scope: Literal["computer", "user"]
    value: GppRegistryValueData

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "scope": "computer",
                    "value": {
                        "name": "InstallPath",
                        "value": "C:\\Program Files\\Studio",
                        "registry_type": "REG_SZ",
                        "action": "create",
                    },
                    "actor": "local-operator",
                    "reason": "Set REG_SZ registry value",
                    "expected_revision": 1,
                },
                {
                    "scope": "computer",
                    "value": {
                        "name": "Enabled",
                        "value": "1",
                        "registry_type": "REG_DWORD",
                        "action": "replace",
                    },
                    "actor": "local-operator",
                    "reason": "Set REG_DWORD registry value",
                    "expected_revision": 2,
                },
                {
                    "scope": "computer",
                    "value": {
                        "name": "Servers",
                        "value": ["dc01.studio.local", "dc02.studio.local"],
                        "registry_type": "REG_MULTI_SZ",
                        "action": "update",
                    },
                    "actor": "local-operator",
                    "reason": "Set REG_MULTI_SZ registry value",
                    "expected_revision": 3,
                },
                {
                    "scope": "computer",
                    "value": {
                        "name": "Blob",
                        "value": "deadbeef",
                        "registry_type": "REG_BINARY",
                        "action": "create",
                    },
                    "actor": "local-operator",
                    "reason": "Set REG_BINARY registry value",
                    "expected_revision": 4,
                },
                {
                    "scope": "computer",
                    "value": {
                        "name": "Counter",
                        "value": "18446744073709551615",
                        "registry_type": "REG_QWORD",
                        "action": "create",
                    },
                    "actor": "local-operator",
                    "reason": "Set REG_QWORD registry value",
                    "expected_revision": 5,
                },
                {
                    "scope": "computer",
                    "value": {
                        "name": "SystemPath",
                        "value": "%SystemRoot%\\System32",
                        "registry_type": "REG_EXPAND_SZ",
                        "action": "create",
                    },
                    "actor": "local-operator",
                    "reason": "Set REG_EXPAND_SZ registry value",
                    "expected_revision": 6,
                },
            ]
        }
    )


class GppDeleteMutation(Audit):
    scope: Literal["computer", "user"]

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "scope": "computer",
                    "actor": "local-operator",
                    "reason": "Delete GPP preference element",
                    "expected_revision": 1,
                },
                {
                    "scope": "user",
                    "actor": "local-operator",
                    "reason": "Remove stale user preference",
                    "expected_revision": 2,
                },
            ]
        }
    )


class ValidationIssueResponse(BaseModel):
    severity: str
    code: str
    message: str
    path: str


class RegistrySettingResponse(BaseModel):
    id: str
    side: str
    hive: str
    key: str
    value_name: str
    registry_type: str
    value: str | list[str]
    action: str
    comment: str

    model_config = ConfigDict(from_attributes=True)


class GpoLinkResponse(BaseModel):
    id: str
    target: str
    enabled: bool
    enforced: bool
    order: int

    model_config = ConfigDict(from_attributes=True)


class SecurityFilterResponse(BaseModel):
    id: str
    principal: str
    permission: str
    inheritable: bool
    target_type: str
    sid: str

    model_config = ConfigDict(from_attributes=True)


class WmiFilterResponse(BaseModel):
    id: str
    name: str
    description: str
    query: str
    language: str

    model_config = ConfigDict(from_attributes=True)


class CseFileEntryResponse(BaseModel):
    relative_path: str
    content_hash: str
    size: int

    model_config = ConfigDict(from_attributes=True)


class CseMetadataEntryResponse(BaseModel):
    guid: str
    side: str
    files: list[CseFileEntryResponse]

    model_config = ConfigDict(from_attributes=True)


class IltPredicateResponse(BaseModel):
    type: str
    negate: bool
    value: str

    model_config = ConfigDict(from_attributes=True)


class IltFilterResponse(BaseModel):
    predicates: list[IltPredicateResponse]

    model_config = ConfigDict(from_attributes=True)


class GppGroupMemberResponse(BaseModel):
    id: str
    sid: str
    name: str
    action: str

    model_config = ConfigDict(from_attributes=True)


class GppRegistryValueResponse(BaseModel):
    id: str
    name: str
    value: str | list[str]
    registry_type: str
    action: str

    model_config = ConfigDict(from_attributes=True)


class GppGroupResponse(BaseModel):
    id: str
    name: str
    sid: str
    action: str
    members: list[GppGroupMemberResponse]
    description: str
    remove_all_users: bool
    remove_all_groups: bool
    ilt_filter: IltFilterResponse | None

    model_config = ConfigDict(from_attributes=True)


class GppRegistryResponse(BaseModel):
    id: str
    key: str
    values: list[GppRegistryValueResponse]
    action: str
    ilt_filter: IltFilterResponse | None

    model_config = ConfigDict(from_attributes=True)


class GppCollectionResponse(BaseModel):
    scope: str
    groups: list[GppGroupResponse]
    registry: list[GppRegistryResponse]

    model_config = ConfigDict(from_attributes=True)


class GpoResponse(BaseModel):
    guid: str
    name: str
    description: str
    computer_enabled: bool
    user_enabled: bool
    status: str
    revision: int
    settings: list[RegistrySettingResponse]
    links: list[GpoLinkResponse]
    source_guid: str
    cse_metadata: list[CseMetadataEntryResponse]
    security_filters: list[SecurityFilterResponse]
    wmi_filter: WmiFilterResponse | None
    gpp_collections: list[GppCollectionResponse]
    domain: str
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class GpoPayloadResponse(BaseModel):
    gpo: GpoResponse
    validation: list[ValidationIssueResponse]
    policy_semantic_sha256: str
    review_model_sha256: str

    model_config = ConfigDict(from_attributes=True)


class ThreeWayDiffRequest(BaseModel):
    baseline: str | dict[str, Any]
    draft: str | dict[str, Any]
    observed: str | dict[str, Any]


class BackupImportRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    migration_table_path: str | None = Field(default=None, max_length=4096)
    actor: str = Field(default="local-operator", min_length=1, max_length=120)
    reason: str = Field(default="Import GPMC backup", min_length=1, max_length=500)


class ConfigurePolicyRequest(Audit):
    gpo_guid: str = Field(min_length=1, max_length=255)
    side: Literal["computer", "user"]
    values: dict[str, bool | int | str | list[str]]


class ForkGPO(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    actor: str = Field(default="local-operator", min_length=1, max_length=120)
    reason: str = Field(default="Fork from baseline", min_length=1, max_length=500)


class EstateDiffRequest(BaseModel):
    baseline_guid: str = Field(min_length=1, max_length=255)
    draft_guid: str = Field(min_length=1, max_length=255)
    observed_guid: str = Field(min_length=1, max_length=255)


def _store(request: Request) -> WorkspaceStore:
    return cast(WorkspaceStore, request.app.state.store)


def _catalogue(request: Request) -> AdmxCatalogue:
    return cast(AdmxCatalogue, getattr(request.app.state, "admx_catalogue", AdmxCatalogue()))


def _wmi_catalogue(request: Request) -> WmiCatalogue:
    return cast(WmiCatalogue, getattr(request.app.state, "wmi_catalogue", WmiCatalogue()))


def _identity(actor: str) -> ClaimedIdentity:
    return claimed_identity(actor)


def _ilt_filter_data_to_model(data: IltFilterData | None) -> IltFilter | None:
    if data is None:
        return None
    return IltFilter(
        predicates=tuple(
            IltPredicate(type=p.type, negate=p.negate, value=p.value)
            for p in data.predicates
        )
    )


def _gpp_member_data_to_model(data: GppGroupMemberData) -> GppGroupMember:
    return GppGroupMember(
        sid=data.sid,
        name=data.name,
        action=data.action,
        id=data.id,
    )


def _gpp_group_data_to_model(data: GppGroupData) -> GppGroup:
    return GppGroup(
        name=data.name,
        sid=data.sid,
        action=data.action,
        members=tuple(_gpp_member_data_to_model(m) for m in data.members),
        description=data.description,
        remove_all_users=data.remove_all_users,
        remove_all_groups=data.remove_all_groups,
        ilt_filter=_ilt_filter_data_to_model(data.ilt_filter),
        id=data.id,
    )


def _gpp_registry_value_data_to_model(data: GppRegistryValueData) -> GppRegistryValue:
    return GppRegistryValue(
        name=data.name,
        value=data.value,
        registry_type=data.registry_type,
        action=data.action,
        id=data.id,
    )


def _gpp_registry_data_to_model(data: GppRegistryData) -> GppRegistry:
    return GppRegistry(
        key=data.key,
        action=data.action,
        values=tuple(_gpp_registry_value_data_to_model(v) for v in data.values),
        ilt_filter=_ilt_filter_data_to_model(data.ilt_filter),
        id=data.id,
    )


def _stringify_numeric_settings(settings: list[dict[str, Any]]) -> None:
    for setting in settings:
        value = setting.get("value")
        if (
            setting.get("registry_type") in ("REG_DWORD", "REG_QWORD")
            and isinstance(value, int)
            and not isinstance(value, bool)
        ):
            setting["value"] = str(value)


def _stringify_gpp_numeric_values(collections: list[dict[str, Any]]) -> None:
    for collection in collections:
        for reg in collection.get("registry", []):
            for val in reg.get("values", []):
                raw = val.get("value")
                if (
                    val.get("registry_type") in ("REG_DWORD", "REG_QWORD")
                    and isinstance(raw, int)
                    and not isinstance(raw, bool)
                ):
                    val["value"] = str(raw)


def _gpo_to_api_dict(gpo: Any) -> dict[str, Any]:
    gpo_dict: dict[str, Any] = gpo.to_dict()
    _stringify_numeric_settings(gpo_dict["settings"])
    _stringify_gpp_numeric_values(gpo_dict["gpp_collections"])
    return gpo_dict


def _gpo_payload(gpo: Any) -> dict[str, Any]:
    return {
        "gpo": _gpo_to_api_dict(gpo),
        "validation": [asdict(item) for item in validate_gpo(gpo)],
        "policy_semantic_sha256": policy_semantic_sha256(gpo),
        "review_model_sha256": review_model_sha256(gpo),
    }


def _validate_inbox_path(path: str) -> Path:
    inbox = os.getenv("GPO_STUDIO_INBOX_DIR")
    if not inbox:
        p = Path(path)
        if p.is_absolute():
            raise ValidationError([
                ValidationIssue(
                    severity="error",
                    code="absolute_path_not_allowed",
                    message="Absolute paths are not allowed without inbox configuration.",
                    path="path",
                )
            ])
        if ".." in p.parts:
            raise ValidationError([
                ValidationIssue(
                    severity="error",
                    code="path_traversal_detected",
                    message="Path traversal is not allowed.",
                    path="path",
                )
            ])
        return p
    try:
        inbox_resolved = Path(inbox).resolve()
        requested_resolved = Path(path).resolve()
    except OSError:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="inbox_path_unresolvable",
                message=f"Cannot resolve backup path or inbox directory: {path}",
                path="path",
            )
        ]) from None
    if not requested_resolved.is_relative_to(inbox_resolved):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="path_outside_inbox",
                message=f"Backup path is outside the configured inbox directory: {path}",
                path="path",
            )
        ])
    return requested_resolved


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if not hasattr(app.state, "store"):
        app.state.store = WorkspaceStore(os.getenv("GPO_STUDIO_DB", "gpo-studio.db"))
        app.state.owns_store = True
    if not hasattr(app.state, "admx_catalogue"):
        admx_dir = os.getenv("GPO_STUDIO_ADMX_DIR", "./admx")
        try:
            app.state.admx_catalogue = load_catalogue(Path(admx_dir))
        except AdmxError as error:
            logging.getLogger("gpo_studio.api").warning(
                "Failed to load ADMX catalogue: %s", error
            )
            app.state.admx_catalogue = AdmxCatalogue()
    if not hasattr(app.state, "wmi_catalogue"):
        wmi_catalogue_path = os.getenv("GPO_STUDIO_WMI_CATALOGUE", "")
        if wmi_catalogue_path:
            try:
                app.state.wmi_catalogue = load_wmi_catalogue(Path(wmi_catalogue_path))
            except WmiCatalogueError as error:
                logging.getLogger("gpo_studio.api").warning(
                    "Failed to load WMI catalogue: %s", error
                )
                app.state.wmi_catalogue = WmiCatalogue()
        else:
            app.state.wmi_catalogue = WmiCatalogue()
    yield
    if getattr(app.state, "owns_store", False):
        app.state.store.close()


app = FastAPI(
    title="GPO Studio",
    version=__version__,
    description="Offline-first Group Policy authoring workspace",
    lifespan=lifespan,
)
app.mount("/assets", StaticFiles(directory=STATIC), name="assets")


@app.exception_handler(StudioError)
async def studio_error(_request: Request, error: StudioError) -> JSONResponse:
    status = (
        404
        if isinstance(error, NotFoundError)
        else 409
        if isinstance(error, ConflictError)
        else 422
    )
    detail: dict[str, Any] = {"message": str(error)}
    if isinstance(error, ValidationError):
        detail["issues"] = [asdict(item) for item in error.issues]
    return JSONResponse({"error": detail}, status_code=status)


def _json_safe_ctx(ctx: dict[str, Any]) -> dict[str, Any]:
    try:
        safe = json.loads(json.dumps(ctx, default=str))
    except (TypeError, ValueError):
        return {str(k): str(v) for k, v in ctx.items()}
    if isinstance(safe, dict):
        return cast(dict[str, Any], safe)
    return {str(k): str(v) for k, v in ctx.items()}


def _sanitize_validation_issues(issues: Sequence[Any]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for issue in issues:
        if not isinstance(issue, dict):
            sanitized.append({"issue": str(issue)})
            continue
        clean: dict[str, Any] = {}
        for key, value in issue.items():
            if key == "ctx" and isinstance(value, dict):
                clean[key] = _json_safe_ctx(value)
            else:
                clean[key] = value
        sanitized.append(clean)
    return sanitized


@app.exception_handler(RequestValidationError)
async def request_validation(_request: Request, error: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "message": "Invalid request",
                "issues": _sanitize_validation_issues(error.errors()),
            }
        },
        status_code=422,
    )


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health(request: Request) -> dict[str, Any]:
    catalogue = _catalogue(request)
    wmi_cat = _wmi_catalogue(request)
    return {
        "status": "ok",
        "version": __version__,
        "mode": "offline-workspace",
        "admx_loaded": len(catalogue.policies) > 0,
        "wmi_catalogue_loaded": len(wmi_cat.filters) > 0,
    }


@app.get("/api/admx/search")
def admx_search(request: Request, q: str = "") -> dict[str, Any]:
    catalogue = _catalogue(request)
    query = q.lower()
    if query:
        policies = [
            p
            for p in catalogue.policies
            if query in p.id.lower()
            or query in p.display_name.lower()
            or query in p.explain_text.lower()
            or query in p.parent_category.lower()
            or query in p.key.lower()
        ]
    else:
        policies = list(catalogue.policies)
    policies = policies[:50]
    items = [
        {
            "id": p.id,
            "class_": p.class_,
            "key": p.key,
            "display_name": p.display_name,
            "explain_text": p.explain_text,
            "supported_on": p.supported_on,
            "parent_category": p.parent_category,
        }
        for p in policies
    ]
    return {"items": items, "count": len(items)}


@app.get("/api/admx/policies/{policy_id}")
def admx_policy_detail(request: Request, policy_id: str) -> dict[str, Any]:
    catalogue = _catalogue(request)
    for policy in catalogue.policies:
        if policy.id == policy_id:
            return asdict(policy)
    raise NotFoundError(f"Policy '{policy_id}' not found")


@app.post("/api/admx/policies/{policy_id}/configure")
def configure_policy(
    request: Request, policy_id: str, body: ConfigurePolicyRequest
) -> dict[str, Any]:
    catalogue = _catalogue(request)
    policy = None
    for p in catalogue.policies:
        if p.id == policy_id:
            policy = p
            break
    if policy is None:
        raise NotFoundError(f"Policy '{policy_id}' not found")
    config = PolicyConfiguration(side=body.side, values=body.values)
    settings = resolve_policy(policy, config)
    issues: list[ValidationIssue] = []
    for s in settings:
        issues.extend(validate_setting(s))
    if any(i.severity == "error" for i in issues):
        raise ValidationError(issues)
    gpo = _store(request).put_settings(
        body.gpo_guid,
        body.expected_revision,
        settings,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.get("/api/admx/categories")
def admx_categories(request: Request) -> dict[str, Any]:
    catalogue = _catalogue(request)
    items = [
        {"id": c.id, "parent_id": c.parent_id, "display_name": c.display_name}
        for c in catalogue.categories
    ]
    return {"items": items, "count": len(items)}


@app.get("/api/gpos")
def list_gpos(request: Request) -> dict[str, Any]:
    gpos = _store(request).list_gpos()
    return {"items": [_gpo_to_api_dict(gpo) for gpo in gpos], "count": len(gpos)}


@app.post("/api/gpos", status_code=201)
def create_gpo(request: Request, body: CreateGPO) -> dict[str, Any]:
    gpo = _store(request).create_gpo(
        body.name, body.description, identity=_identity(body.actor), reason=body.reason
    )
    return _gpo_payload(gpo)


@app.get("/api/gpos/{guid}")
def get_gpo(request: Request, guid: str) -> dict[str, Any]:
    return _gpo_payload(_store(request).get_gpo(guid))


@app.patch("/api/gpos/{guid}")
def update_gpo(request: Request, guid: str, body: MetadataMutation) -> dict[str, Any]:
    values = body.model_dump(exclude={"actor", "reason", "expected_revision"})
    gpo = _store(request).update_metadata(
        guid,
        body.expected_revision,
        values,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


def _validated_setting(body: SettingMutation) -> dict[str, Any]:
    values = body.setting.model_dump()
    from .store import _setting

    issues = validate_setting(_setting({"id": "pending", **values}))
    if any(item.severity == "error" for item in issues):
        raise ValidationError(issues)
    return values


@app.post("/api/gpos/{guid}/settings", status_code=201)
def add_setting(request: Request, guid: str, body: SettingMutation) -> dict[str, Any]:
    gpo = _store(request).put_setting(
        guid,
        body.expected_revision,
        _validated_setting(body),
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.put("/api/gpos/{guid}/settings/{setting_id}")
def edit_setting(
    request: Request, guid: str, setting_id: str, body: SettingMutation
) -> dict[str, Any]:
    gpo = _store(request).put_setting(
        guid,
        body.expected_revision,
        _validated_setting(body),
        setting_id=setting_id,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.delete("/api/gpos/{guid}/settings/{setting_id}")
def delete_setting(
    request: Request, guid: str, setting_id: str, body: DeleteMutation
) -> dict[str, Any]:
    gpo = _store(request).delete_setting(
        guid,
        setting_id,
        body.expected_revision,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.post("/api/gpos/{guid}/links", status_code=201)
def add_link(request: Request, guid: str, body: LinkMutation) -> dict[str, Any]:
    gpo = _store(request).put_link(
        guid,
        body.expected_revision,
        body.link.model_dump(),
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.put("/api/gpos/{guid}/links/{link_id}")
def edit_link(request: Request, guid: str, link_id: str, body: LinkMutation) -> dict[str, Any]:
    gpo = _store(request).put_link(
        guid,
        body.expected_revision,
        body.link.model_dump(),
        link_id=link_id,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.delete("/api/gpos/{guid}/links/{link_id}")
def delete_link(request: Request, guid: str, link_id: str, body: DeleteMutation) -> dict[str, Any]:
    gpo = _store(request).delete_link(
        guid,
        link_id,
        body.expected_revision,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.post("/api/gpos/{guid}/security-filters", status_code=201)
def add_security_filter(
    request: Request, guid: str, body: SecurityFilterMutation
) -> dict[str, Any]:
    gpo = _store(request).put_security_filter(
        guid,
        body.expected_revision,
        body.filter.model_dump(),
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.put("/api/gpos/{guid}/security-filters/{filter_id}")
def edit_security_filter(
    request: Request, guid: str, filter_id: str, body: SecurityFilterMutation
) -> dict[str, Any]:
    gpo = _store(request).put_security_filter(
        guid,
        body.expected_revision,
        body.filter.model_dump(),
        filter_id=filter_id,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.delete("/api/gpos/{guid}/security-filters/{filter_id}")
def delete_security_filter(
    request: Request, guid: str, filter_id: str, body: DeleteMutation
) -> dict[str, Any]:
    gpo = _store(request).delete_security_filter(
        guid,
        filter_id,
        body.expected_revision,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.put("/api/gpos/{guid}/wmi-filter")
def set_wmi_filter(request: Request, guid: str, body: WmiFilterMutation) -> dict[str, Any]:
    gpo = _store(request).set_wmi_filter(
        guid,
        body.expected_revision,
        body.wmi_filter.model_dump(),
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.delete("/api/gpos/{guid}/wmi-filter")
def clear_wmi_filter(request: Request, guid: str, body: DeleteMutation) -> dict[str, Any]:
    gpo = _store(request).set_wmi_filter(
        guid,
        body.expected_revision,
        None,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.post("/api/gpos/{guid}/preferences/groups", status_code=201, response_model=GpoPayloadResponse)
def add_gpp_group(
    request: Request, guid: str, body: GppGroupMutation
) -> dict[str, Any]:
    group = _gpp_group_data_to_model(body.group)
    group = replace(group, id="")
    gpo = _store(request).put_gpp_group(
        guid,
        body.expected_revision,
        body.scope,
        group,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.put("/api/gpos/{guid}/preferences/groups/{group_id}", response_model=GpoPayloadResponse)
def edit_gpp_group(
    request: Request, guid: str, group_id: str, body: GppGroupMutation
) -> dict[str, Any]:
    group = _gpp_group_data_to_model(body.group)
    group = replace(group, id=group_id)
    gpo = _store(request).put_gpp_group(
        guid,
        body.expected_revision,
        body.scope,
        group,
        identity=_identity(body.actor),
        reason=body.reason,
        must_exist=True,
    )
    return _gpo_payload(gpo)


@app.delete("/api/gpos/{guid}/preferences/groups/{group_id}", response_model=GpoPayloadResponse)
def delete_gpp_group(
    request: Request, guid: str, group_id: str, body: GppDeleteMutation
) -> dict[str, Any]:
    gpo = _store(request).delete_gpp_group(
        guid,
        body.expected_revision,
        body.scope,
        group_id,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.post(
    "/api/gpos/{guid}/preferences/registry",
    status_code=201,
    response_model=GpoPayloadResponse,
)
def add_gpp_registry(
    request: Request, guid: str, body: GppRegistryMutation
) -> dict[str, Any]:
    registry = _gpp_registry_data_to_model(body.registry)
    registry = replace(registry, id="")
    gpo = _store(request).put_gpp_registry(
        guid,
        body.expected_revision,
        body.scope,
        registry,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.put("/api/gpos/{guid}/preferences/registry/{registry_id}", response_model=GpoPayloadResponse)
def edit_gpp_registry(
    request: Request, guid: str, registry_id: str, body: GppRegistryMutation
) -> dict[str, Any]:
    registry = _gpp_registry_data_to_model(body.registry)
    registry = replace(registry, id=registry_id)
    gpo = _store(request).put_gpp_registry(
        guid,
        body.expected_revision,
        body.scope,
        registry,
        identity=_identity(body.actor),
        reason=body.reason,
        must_exist=True,
    )
    return _gpo_payload(gpo)


@app.delete(
    "/api/gpos/{guid}/preferences/registry/{registry_id}",
    response_model=GpoPayloadResponse,
)
def delete_gpp_registry(
    request: Request, guid: str, registry_id: str, body: GppDeleteMutation
) -> dict[str, Any]:
    gpo = _store(request).delete_gpp_registry(
        guid,
        body.expected_revision,
        body.scope,
        registry_id,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.post(
    "/api/gpos/{guid}/preferences/groups/{group_id}/members",
    status_code=201,
    response_model=GpoPayloadResponse,
)
def add_gpp_member(
    request: Request,
    guid: str,
    group_id: str,
    body: GppMemberMutation,
) -> dict[str, Any]:
    member = _gpp_member_data_to_model(body.member)
    member = replace(member, id="")
    gpo = _store(request).put_gpp_member(
        guid,
        body.expected_revision,
        body.scope,
        group_id,
        member,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.put(
    "/api/gpos/{guid}/preferences/groups/{group_id}/members/{member_id}",
    response_model=GpoPayloadResponse,
)
def edit_gpp_member(
    request: Request,
    guid: str,
    group_id: str,
    member_id: str,
    body: GppMemberMutation,
) -> dict[str, Any]:
    member = _gpp_member_data_to_model(body.member)
    member = replace(member, id=member_id)
    gpo = _store(request).put_gpp_member(
        guid,
        body.expected_revision,
        body.scope,
        group_id,
        member,
        identity=_identity(body.actor),
        reason=body.reason,
        must_exist=True,
    )
    return _gpo_payload(gpo)


@app.delete(
    "/api/gpos/{guid}/preferences/groups/{group_id}/members/{member_id}",
    response_model=GpoPayloadResponse,
)
def delete_gpp_member(
    request: Request,
    guid: str,
    group_id: str,
    member_id: str,
    body: GppDeleteMutation,
) -> dict[str, Any]:
    gpo = _store(request).delete_gpp_member(
        guid,
        body.expected_revision,
        body.scope,
        group_id,
        member_id,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.post(
    "/api/gpos/{guid}/preferences/registry/{registry_id}/values",
    status_code=201,
    response_model=GpoPayloadResponse,
)
def add_gpp_registry_value(
    request: Request,
    guid: str,
    registry_id: str,
    body: GppRegistryValueMutation,
) -> dict[str, Any]:
    value = _gpp_registry_value_data_to_model(body.value)
    value = replace(value, id="")
    gpo = _store(request).put_gpp_registry_value(
        guid,
        body.expected_revision,
        body.scope,
        registry_id,
        value,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.put(
    "/api/gpos/{guid}/preferences/registry/{registry_id}/values/{value_id}",
    response_model=GpoPayloadResponse,
)
def edit_gpp_registry_value(
    request: Request,
    guid: str,
    registry_id: str,
    value_id: str,
    body: GppRegistryValueMutation,
) -> dict[str, Any]:
    value = _gpp_registry_value_data_to_model(body.value)
    value = replace(value, id=value_id)
    gpo = _store(request).put_gpp_registry_value(
        guid,
        body.expected_revision,
        body.scope,
        registry_id,
        value,
        identity=_identity(body.actor),
        reason=body.reason,
        must_exist=True,
    )
    return _gpo_payload(gpo)


@app.delete(
    "/api/gpos/{guid}/preferences/registry/{registry_id}/values/{value_id}",
    response_model=GpoPayloadResponse,
)
def delete_gpp_registry_value(
    request: Request,
    guid: str,
    registry_id: str,
    value_id: str,
    body: GppDeleteMutation,
) -> dict[str, Any]:
    gpo = _store(request).delete_gpp_registry_value(
        guid,
        body.expected_revision,
        body.scope,
        registry_id,
        value_id,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.get("/api/wmi-filters")
def list_wmi_filters(request: Request) -> dict[str, Any]:
    catalogue = _wmi_catalogue(request)
    items = [
        {
            "id": f.id,
            "name": f.name,
            "query": f.query,
            "language": f.language,
            "description": f.description,
        }
        for f in catalogue.filters
    ]
    return {"items": items, "count": len(items)}


@app.get("/api/wmi-filters/{filter_id}")
def get_wmi_filter(request: Request, filter_id: str) -> dict[str, Any]:
    catalogue = _wmi_catalogue(request)
    for f in catalogue.filters:
        if f.id == filter_id:
            return asdict(f)
    raise NotFoundError(f"WMI filter '{filter_id}' not found")


@app.get("/api/gpos/{guid}/revisions")
def revisions(request: Request, guid: str) -> dict[str, Any]:
    items = _store(request).revisions(guid)
    return {
        "items": [
            {
                "revision": item.revision,
                "actor": item.actor,
                "reason": item.reason,
                "created_at": item.created_at,
            }
            for item in items
        ]
    }


@app.get("/api/gpos/{guid}/revisions/{revision}")
def revision(request: Request, guid: str, revision: int) -> dict[str, Any]:
    item = _store(request).get_revision(guid, revision)
    return asdict(item)


@app.post("/api/gpos/{guid}/revisions/{revision}/restore")
def restore(request: Request, guid: str, revision: int, body: RestoreMutation) -> dict[str, Any]:
    gpo = _store(request).restore_revision(
        guid,
        revision,
        body.expected_revision,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo)


@app.get("/api/gpos/{guid}/export.zip")
def bundle(request: Request, guid: str) -> Response:
    gpo = _store(request).get_gpo(guid)
    errors = [item for item in validate_gpo(gpo) if item.severity == "error"]
    if errors:
        raise ValidationError(errors)
    headers = {"Content-Disposition": f'attachment; filename="{gpo.guid}-publication.zip"'}
    return Response(export_bundle(gpo), media_type="application/zip", headers=headers)


@app.get("/api/gpos/{guid}/plan.ps1")
def plan(request: Request, guid: str) -> Response:
    gpo = _store(request).get_gpo(guid)
    errors = [item for item in validate_gpo(gpo) if item.severity == "error"]
    if errors:
        raise ValidationError(errors)
    return Response(powershell_plan(gpo), media_type="text/plain; charset=utf-8")


def _resolve_gpo(request: Request, ref: str | dict[str, Any]) -> GPO:
    return resolve_gpo(_store(request), ref)


@app.get("/api/gpos/{guid}/diff")
def diff_gpo(request: Request, guid: str, against_revision: int) -> dict[str, Any]:
    current = _store(request).get_gpo(guid)
    old_revision = _store(request).get_revision(guid, against_revision)
    old_gpo = gpo_from_dict(old_revision.snapshot)
    result = diff_gpos(old_gpo, current)
    return asdict(result)


@app.post("/api/diff")
def ad_hoc_diff(request: Request, body: ThreeWayDiffRequest) -> dict[str, Any]:
    baseline = _resolve_gpo(request, body.baseline)
    draft = _resolve_gpo(request, body.draft)
    observed = _resolve_gpo(request, body.observed)
    result = three_way_diff(baseline, draft, observed)
    return asdict(result)


@app.post("/api/backups/import", status_code=201)
def import_backup(request: Request, body: BackupImportRequest) -> dict[str, Any]:
    backup_dir = _validate_inbox_path(body.path)
    if not backup_dir.is_dir():
        raise StudioError(f"Backup path is not a directory: {body.path}")
    for xml_file in ("manifest.xml", "bkupInfo.xml"):
        xml_path = backup_dir / xml_file
        if xml_path.is_symlink():
            raise ValidationError([
                ValidationIssue(
                    severity="error",
                    code="symlink_in_backup",
                    message=f"Symlinks are not allowed in backup content: {xml_file}",
                    path="path",
                )
            ])
    backup = read_backup(backup_dir)
    if not backup.gpos:
        raise StudioError("No GPOs found in backup")
    if len(backup.gpos) > 1:
        raise StudioError(
            f"Multi-GPO backups are not supported (found {len(backup.gpos)} GPOs)"
        )
    backup_gpo = backup.gpos[0]
    gpo_dir = backup_dir / backup_gpo.guid

    machine_settings = extract_settings(gpo_dir / "Machine" / "Registry.pol", "computer")
    user_settings = extract_settings(gpo_dir / "User" / "Registry.pol", "user")
    all_settings = tuple(machine_settings + user_settings)
    cse_metadata = collect_cse_metadata(backup_gpo)
    gpp_collections = collect_gpp_collections(backup_dir, backup_gpo.guid)

    security_filters = backup_security_filters_to_model(backup_gpo.security_filters)
    wmi_filter = backup_wmi_filter_to_model(backup_gpo.wmi_filter)

    if body.migration_table_path is not None:
        from .migration import apply_migration, parse_migration_table

        mig_path = _validate_inbox_path(body.migration_table_path)
        if not mig_path.is_file():
            raise StudioError(f"Migration table is not a file: {body.migration_table_path}")
        table = parse_migration_table(mig_path)
        temp_gpo_for_migration = GPO(
            guid="import-preview",
            name=backup_gpo.display_name or "Imported GPO",
            security_filters=security_filters,
            domain=backup_gpo.domain or "studio.local",
        )
        migrated_gpo = apply_migration(temp_gpo_for_migration, table)
        security_filters = migrated_gpo.security_filters

    temp_gpo = GPO(
        guid="import-preview",
        name=backup_gpo.display_name or "Imported GPO",
        settings=all_settings,
        security_filters=security_filters,
        wmi_filter=wmi_filter,
        domain=backup_gpo.domain or "studio.local",
    )
    gpo_issues = [i for i in validate_gpo(temp_gpo) if i.severity == "error"]
    if gpo_issues:
        raise ValidationError(gpo_issues)

    gpo = _store(request).create_gpo(
        name=backup_gpo.display_name or "Imported GPO",
        description=f"Imported from GPMC backup {backup.backup_id}",
        identity=_identity(body.actor),
        reason=body.reason,
        settings=all_settings,
        source_guid=backup_gpo.guid,
        cse_metadata=cse_metadata,
        domain=backup_gpo.domain or "studio.local",
        security_filters=security_filters,
        wmi_filter=wmi_filter,
        gpp_collections=gpp_collections,
    )
    return _gpo_payload(gpo)


@app.get("/api/gpos/{guid}/gpmc-backup")
def gpmc_backup(request: Request, guid: str) -> Response:
    gpo = _store(request).get_gpo(guid)
    errors = [item for item in validate_gpo(gpo) if item.severity == "error"]
    if errors:
        raise ValidationError(errors)
    if gpo.cse_metadata:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="unknown_cse_content",
                message="GPO has unknown CSE content and cannot be exported as a GPMC backup.",
                path="cse_metadata",
            )
        ])
    headers = {"Content-Disposition": f'attachment; filename="{gpo.guid}-gpmc-backup.zip"'}
    return Response(gpmc_backup_bundle(gpo), media_type="application/zip", headers=headers)


@app.post("/api/estate/import")
def import_estate(
    request: Request,
    body: dict[str, Any],
    actor: str = Query(default="local-operator", min_length=1, max_length=120),
    reason: str = Query(default="Import gpo-lens estate", min_length=1, max_length=500),
) -> dict[str, Any]:
    gpos = parse_estate(body)
    return _store(request).import_baseline_gpos(
        gpos, identity=_identity(actor), reason=reason
    )


@app.post("/api/gpos/{guid}/fork", status_code=201)
def fork_gpo(request: Request, guid: str, body: ForkGPO) -> dict[str, Any]:
    gpo = _store(request).fork_gpo(
        guid, body.name, identity=_identity(body.actor), reason=body.reason
    )
    return _gpo_payload(gpo)


@app.post("/api/estate/diff")
def estate_diff(request: Request, body: EstateDiffRequest) -> dict[str, Any]:
    store = _store(request)
    baseline = store.get_gpo(body.baseline_guid)
    draft = store.get_gpo(body.draft_guid)
    observed = store.get_gpo(body.observed_guid)
    result = three_way_diff(baseline, draft, observed)
    return asdict(result)
