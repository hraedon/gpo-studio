"""FastAPI delivery layer for the local workspace."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid as uuid_module
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Literal, cast, get_args
from urllib.parse import urlparse

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response as StarletteResponse
from starlette.types import Scope

from . import __version__
from .admx import (
    AdmxCatalogue,
    AmbiguousPolicyError,
    PolicyDefinition,
    find_policy,
)
from .backup import BackupError, read_backup
from .canonical import policy_semantic_sha256, review_model_sha256
from .diff import diff_gpos, three_way_diff
from .estate import parse_estate
from .export import export_bundle, gpmc_backup_bundle, powershell_plan
from .gpp import (
    _GROUP_KNOWN_CHILDREN,
    _GROUP_PROPS_KNOWN_ATTRS,
    _GROUP_RESERVED_ATTRS,
    _MEMBER_RESERVED_ATTRS,
    _REGISTRY_KNOWN_CHILDREN,
    _REGISTRY_RESERVED_ATTRS,
    _REGISTRY_VALUE_RESERVED_ATTRS,
    GppError,
    GppGroup,
    GppGroupMember,
    GppRegistry,
    GppRegistryValue,
    _normalize_hive,
    _validate_unknown_attrs,
    _validate_unknown_children,
)
from .identity import ClaimedIdentity, claimed_identity
from .ilt import IltError, IltFilter, IltPredicate, validate_predicate_unknown_attrs
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
    WorkspaceError,
)
from .numeric import coerce_dword_qword
from .policy_config import (
    PolicyConfiguration,
    PolicyState,
    policy_setting_prefix,
    resolve_policy,
)
from .registry_pol import RegistryPolError
from .report import policy_report
from .store import WorkspaceStore, gpo_from_dict
from .validation import validate_gpo, validate_setting
from .wmi_catalogue import WmiCatalogue, WmiCatalogueError, load_wmi_catalogue

STATIC = Path(__file__).with_name("static")


class StudioStaticFiles(StaticFiles):
    """Serve browser modules with a MIME type independent of the host registry."""

    def file_response(
        self,
        full_path: str | os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> StarletteResponse:
        response = super().file_response(full_path, stat_result, scope, status_code)
        if Path(full_path).suffix.casefold() == ".mjs":
            response.headers["Content-Type"] = "text/javascript; charset=utf-8"
        return response


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


class GppReorderMutation(Audit):
    scope: Literal["computer", "user"]
    kind: Literal["groups", "registry"]
    ordered_ids: list[str] = Field(min_length=1)


class RestoreMutation(Audit):
    pass


class IltPredicateData(BaseModel):
    type: Literal["ou", "group", "registry", "ip_range", "environment", "wmi_query"]
    negate: bool = False
    value: str = ""
    bool_op: Literal["AND", "OR"] = "AND"
    unknown_attrs: list[tuple[str, str]] = Field(default_factory=list)


class IltFilterData(BaseModel):
    items: list[IltPredicateData | str] | None = None
    predicates: list[IltPredicateData] = Field(default_factory=list)
    unknown_predicates: list[str] = Field(default_factory=list)


class GppGroupMemberData(BaseModel):
    sid: str = Field(min_length=1, max_length=255)
    name: str = Field(default="", max_length=255)
    action: Literal["add", "replace", "remove", "update"] = "add"
    id: str = ""
    unknown_attrs: list[tuple[str, str]] = Field(default_factory=list)


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
    unknown_attrs: list[tuple[str, str]] = Field(default_factory=list)
    unknown_props_attrs: list[tuple[str, str]] = Field(default_factory=list)
    unknown_children: list[str] = Field(default_factory=list)


_GPP_REGISTRY_TYPES = Literal[
    "REG_SZ", "REG_EXPAND_SZ", "REG_BINARY", "REG_DWORD", "REG_MULTI_SZ", "REG_QWORD"
]
_VALID_REGISTRY_TYPE_STRINGS: frozenset[str] = frozenset(
    t for t in get_args(_GPP_REGISTRY_TYPES)
)


class GppRegistryValueData(BaseModel):
    name: str = Field(default="", max_length=255)
    value: str | int | list[str] = ""
    registry_type: str = "REG_SZ"
    action: Literal["create", "replace", "update", "delete"] = "create"
    default: bool = False
    id: str = ""
    unknown_attrs: list[tuple[str, str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_registry_value(self) -> GppRegistryValueData:
        if self.default:
            if self.name != "":
                raise ValueError(
                    "Default registry entry must have empty name"
                )
            if self.registry_type not in ("", "REG_SZ", "REG_EXPAND_SZ",
                                          "REG_BINARY", "REG_DWORD",
                                          "REG_MULTI_SZ", "REG_QWORD"):
                raise ValueError("Invalid registry type for default entry")
            if self.registry_type in ("REG_DWORD", "REG_QWORD"):
                if not isinstance(self.value, str):
                    raise ValueError(
                        f"{self.registry_type} requires a canonical "
                        "decimal string value"
                    )
                self.value = coerce_dword_qword(self.value, self.registry_type)
        elif self.name == "":
            if self.registry_type not in ("", "REG_SZ"):
                raise ValueError("Key-only registry entry must have empty type")
            if self.value != "" and self.value != []:
                raise ValueError("Key-only registry entry must have empty value")
        else:
            if self.registry_type not in _VALID_REGISTRY_TYPE_STRINGS:
                raise ValueError(f"Invalid registry type: {self.registry_type}")
            if self.registry_type in ("REG_DWORD", "REG_QWORD"):
                if not isinstance(self.value, str):
                    raise ValueError(
                        f"{self.registry_type} requires a canonical decimal string value"
                    )
                self.value = coerce_dword_qword(self.value, self.registry_type)
        return self


class GppRegistryData(BaseModel):
    key: str = Field(min_length=1, max_length=1000)
    hive: str = Field(default="HKEY_LOCAL_MACHINE", max_length=50)
    action: Literal["add", "replace", "remove", "update"] = "update"
    value: GppRegistryValueData = Field(default_factory=lambda: GppRegistryValueData())
    id: str = ""
    uid: str = ""
    ilt_filter: IltFilterData | None = None
    unknown_attrs: list[tuple[str, str]] = Field(default_factory=list)
    unknown_children: list[str] = Field(default_factory=list)


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
                        "value": {
                            "name": "Enabled",
                            "value": "1",
                            "registry_type": "REG_DWORD",
                            "action": "create",
                        },
                    },
                    "actor": "local-operator",
                    "reason": "Configure studio policy registry value",
                    "expected_revision": 1,
                },
                {
                    "scope": "user",
                    "registry": {
                        "key": "Software\\Policies\\Studio\\User",
                        "action": "add",
                        "value": {
                            "name": "Theme",
                            "value": "Dark",
                            "registry_type": "REG_SZ",
                            "action": "create",
                        },
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
                        "value": {
                            "name": "Enabled",
                            "value": "0",
                            "registry_type": "REG_DWORD",
                            "action": "replace",
                        },
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
                        "value": {
                            "name": "",
                            "value": "",
                            "registry_type": "",
                            "action": "create",
                        },
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
    bool_op: str = "AND"
    unknown_attrs: list[tuple[str, str]] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class IltFilterResponse(BaseModel):
    items: list[IltPredicateResponse | str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class GppGroupMemberResponse(BaseModel):
    id: str
    sid: str
    name: str
    action: str
    unknown_attrs: list[tuple[str, str]] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class GppRegistryValueResponse(BaseModel):
    id: str
    name: str
    value: str | list[str]
    registry_type: str
    action: str
    default: bool = False
    unknown_attrs: list[tuple[str, str]] = Field(default_factory=list)

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
    unknown_attrs: list[tuple[str, str]] = Field(default_factory=list)
    unknown_props_attrs: list[tuple[str, str]] = Field(default_factory=list)
    unknown_children: list[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class GppRegistryResponse(BaseModel):
    id: str
    key: str
    hive: str
    value: GppRegistryValueResponse
    action: str
    uid: str = ""
    ilt_filter: IltFilterResponse | None = None
    unknown_attrs: list[tuple[str, str]] = Field(default_factory=list)
    unknown_children: list[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class GppCollectionResponse(BaseModel):
    scope: str
    groups: list[GppGroupResponse]
    registry: list[GppRegistryResponse]
    groups_unknown_attrs: list[tuple[str, str]] = Field(default_factory=list)
    groups_unknown_children: list[str] = Field(default_factory=list)
    registry_unknown_attrs: list[tuple[str, str]] = Field(default_factory=list)
    registry_unknown_children: list[str] = Field(default_factory=list)

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
    artifact_capabilities: dict[str, Any]

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
    values: dict[str, bool | int | str | list[str]] = Field(default_factory=dict)
    # Defaults to "enabled" so existing clients keep their behaviour; disabled
    # and not_configured are the states GPMC offers that had no representation.
    state: PolicyState = "enabled"


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


def _resolve_policy(request: Request, policy_id: str) -> PolicyDefinition:
    """Look up a policy by qualified id or bare name, 404 when absent.

    An ambiguous bare name propagates as :class:`AmbiguousPolicyError` and is
    answered 409 by its handler — never resolved to an arbitrary candidate.
    """
    policy = find_policy(_catalogue(request), policy_id)
    if policy is None:
        raise NotFoundError(f"Policy '{policy_id}' not found")
    return policy


def _wmi_catalogue(request: Request) -> WmiCatalogue:
    return cast(WmiCatalogue, getattr(request.app.state, "wmi_catalogue", WmiCatalogue()))


def _identity(actor: str) -> ClaimedIdentity:
    return claimed_identity(actor)


def _ilt_filter_data_to_model(data: IltFilterData | None) -> IltFilter | None:
    if data is None:
        return None
    items: list[IltPredicate | str] = []
    if data.items:
        for item in data.items:
            if isinstance(item, IltPredicateData):
                pred = IltPredicate(
                    type=item.type,
                    negate=item.negate,
                    value=item.value,
                    bool_op=item.bool_op,
                    unknown_attrs=tuple((pair[0], pair[1]) for pair in item.unknown_attrs),
                )
                _validate_ilt_predicate_attrs(pred)
                items.append(pred)
            else:
                items.append(item)
    else:
        for p in data.predicates:
            pred = IltPredicate(
                type=p.type,
                negate=p.negate,
                value=p.value,
                bool_op=p.bool_op,
                unknown_attrs=tuple((pair[0], pair[1]) for pair in p.unknown_attrs),
            )
            _validate_ilt_predicate_attrs(pred)
            items.append(pred)
        for raw in data.unknown_predicates:
            items.append(raw)
    return IltFilter(items=tuple(items))


def _validate_ilt_predicate_attrs(pred: IltPredicate) -> None:
    try:
        validate_predicate_unknown_attrs(pred)
    except IltError as error:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="reserved_attribute_collision",
                message=str(error),
                path="unknown_attrs",
            )
        ]) from error


def _validate_gpp_unknown_attrs(
    unknown: tuple[tuple[str, str], ...],
    reserved: frozenset[str],
    context: str,
) -> None:
    try:
        _validate_unknown_attrs(unknown, reserved, context)
    except GppError as error:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="reserved_attribute_collision",
                message=str(error),
                path="unknown_attrs",
            )
        ]) from error


def _validate_gpp_unknown_children(
    unknown: tuple[str, ...],
    reserved: frozenset[str],
    context: str,
) -> None:
    try:
        _validate_unknown_children(unknown, reserved, context)
    except GppError as error:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="reserved_child_collision",
                message=str(error),
                path="unknown_children",
            )
        ]) from error


def _gpp_member_data_to_model(data: GppGroupMemberData) -> GppGroupMember:
    member = GppGroupMember(
        sid=data.sid,
        name=data.name,
        action=data.action,
        id=data.id,
        unknown_attrs=tuple((pair[0], pair[1]) for pair in data.unknown_attrs),
    )
    _validate_gpp_unknown_attrs(
        member.unknown_attrs, _MEMBER_RESERVED_ATTRS, f"member {member.name!r}"
    )
    return member


def _gpp_group_data_to_model(data: GppGroupData) -> GppGroup:
    group = GppGroup(
        name=data.name,
        sid=data.sid,
        action=data.action,
        members=tuple(_gpp_member_data_to_model(m) for m in data.members),
        description=data.description,
        remove_all_users=data.remove_all_users,
        remove_all_groups=data.remove_all_groups,
        ilt_filter=_ilt_filter_data_to_model(data.ilt_filter),
        id=data.id,
        unknown_attrs=tuple((pair[0], pair[1]) for pair in data.unknown_attrs),
        unknown_props_attrs=tuple(
            (pair[0], pair[1]) for pair in data.unknown_props_attrs
        ),
        unknown_children=tuple(data.unknown_children),
    )
    _validate_gpp_unknown_attrs(
        group.unknown_attrs, _GROUP_RESERVED_ATTRS, f"group {group.name!r}"
    )
    _validate_gpp_unknown_attrs(
        group.unknown_props_attrs,
        _GROUP_PROPS_KNOWN_ATTRS,
        f"group {group.name!r} properties",
    )
    _validate_gpp_unknown_children(
        group.unknown_children, _GROUP_KNOWN_CHILDREN, f"group {group.name!r}"
    )
    return group


def _gpp_registry_value_data_to_model(data: GppRegistryValueData) -> GppRegistryValue:
    value = GppRegistryValue(
        name=data.name,
        value=data.value,
        registry_type=data.registry_type,
        action=data.action,
        default=data.default,
        id=data.id,
        unknown_attrs=tuple((pair[0], pair[1]) for pair in data.unknown_attrs),
    )
    _validate_gpp_unknown_attrs(
        value.unknown_attrs,
        _REGISTRY_VALUE_RESERVED_ATTRS,
        f"registry value {value.name!r}",
    )
    return value


def _gpp_registry_data_to_model(data: GppRegistryData) -> GppRegistry:
    try:
        hive = _normalize_hive(data.hive)
    except GppError as error:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_hive",
                message=str(error),
                path="hive",
            )
        ]) from error
    reg_ilt = _ilt_filter_data_to_model(data.ilt_filter)
    value = _gpp_registry_value_data_to_model(data.value)
    registry = GppRegistry(
        key=data.key,
        hive=hive,
        action=data.action,
        uid=data.uid,
        value=value,
        id=data.id,
        ilt_filter=reg_ilt,
        unknown_attrs=tuple((pair[0], pair[1]) for pair in data.unknown_attrs),
        unknown_children=tuple(data.unknown_children),
    )
    _validate_gpp_unknown_attrs(
        registry.unknown_attrs,
        _REGISTRY_RESERVED_ATTRS,
        f"registry {registry.key!r}",
    )
    _validate_gpp_unknown_children(
        registry.unknown_children,
        _REGISTRY_KNOWN_CHILDREN,
        f"registry {registry.key!r}",
    )
    return registry


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
            val = reg.get("value")
            if not isinstance(val, dict):
                continue
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


def _gpo_payload(gpo: Any, request: Request | None = None) -> dict[str, Any]:
    if request is not None:
        request.state.gpo_guid = gpo.guid
        request.state.gpo_revision = gpo.revision
    validation = validate_gpo(gpo)
    blocked = any(item.severity == "error" for item in validation)
    preserved_files = sum(len(entry.files) for entry in gpo.cse_metadata)
    return {
        "gpo": _gpo_to_api_dict(gpo),
        "validation": [asdict(item) for item in validation],
        "policy_semantic_sha256": policy_semantic_sha256(gpo),
        "review_model_sha256": review_model_sha256(gpo),
        "artifact_capabilities": {
            "studio_export": {"enabled": not blocked, "format": "zip"},
            "gpmc_export": {
                "enabled": not blocked and preserved_files == 0,
                "format": "zip",
                "reason": (
                    "Preserved extension content cannot be emitted as a GPMC backup."
                    if preserved_files
                    else ""
                ),
            },
            "powershell_plan": {"enabled": not blocked, "format": "text"},
            "policy_report": {"enabled": True, "format": "text"},
            "preserved_content": {
                "present": preserved_files > 0,
                "file_count": preserved_files,
            },
        },
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
        requested = Path(path)
        requested_resolved = (
            requested if requested.is_absolute() else inbox_resolved / requested
        ).resolve()
    except OSError:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="inbox_path_unresolvable",
                message="Cannot resolve the requested import path.",
                path="path",
            )
        ]) from None
    if not requested_resolved.is_relative_to(inbox_resolved):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="path_outside_inbox",
                message="Import path is outside the configured inbox directory.",
                path="path",
            )
        ])
    return requested_resolved


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
MAX_REQUEST_BODY_BYTES = 10 * 1024 * 1024

_logger = logging.getLogger("gpo_studio.api")

_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _is_unsafe_mode() -> bool:
    return os.getenv("GPO_STUDIO_UNSAFE_BIND", "").lower() in ("1", "true", "yes")


def _sanitize_log_value(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in value)


def _sanitize_log_path(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_/" else "_" for c in value)


def _is_loopback_host(host: str) -> bool:
    host = host.lower()
    if host in _LOOPBACK_HOSTS:
        return True
    return any(host.startswith(known + ":") for known in _LOOPBACK_HOSTS)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> StarletteResponse:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )
        return response


class HostValidationMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> StarletteResponse:
        if not _is_unsafe_mode():
            host = request.headers.get("host", "")
            if not _is_loopback_host(host):
                return JSONResponse(
                    {"error": {"message": "Host header not allowed"}},
                    status_code=421,
                )
        return await call_next(request)


class OriginValidationMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> StarletteResponse:
        if not _is_unsafe_mode() and request.method in _MUTATION_METHODS:
            origin = request.headers.get("origin", "")
            if origin:
                if origin == "null":
                    return JSONResponse(
                        {"error": {"message": "Origin not allowed"}},
                        status_code=403,
                    )
                try:
                    parsed = urlparse(origin)
                except ValueError:
                    return JSONResponse(
                        {"error": {"message": "Origin not allowed"}},
                        status_code=403,
                    )
                if parsed.scheme not in ("http", "https"):
                    return JSONResponse(
                        {"error": {"message": "Origin not allowed"}},
                        status_code=403,
                    )
                origin_host = parsed.hostname or ""
                if not origin_host or not _is_loopback_host(origin_host):
                    return JSONResponse(
                        {"error": {"message": "Origin not allowed"}},
                        status_code=403,
                    )
        return await call_next(request)


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> StarletteResponse:
        te = request.headers.get("transfer-encoding", "").lower()
        if "chunked" in te:
            return JSONResponse(
                {"error": {"message": "Chunked transfer encoding not supported"}},
                status_code=400,
            )
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                cl = int(content_length)
            except ValueError:
                return JSONResponse(
                    {"error": {"message": "Invalid Content-Length"}},
                    status_code=400,
                )
            if cl < 0 or cl > MAX_REQUEST_BODY_BYTES:
                return JSONResponse(
                    {"error": {"message": "Request body too large"}},
                    status_code=413,
                )

        if request.method in _MUTATION_METHODS:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > MAX_REQUEST_BODY_BYTES:
                    return JSONResponse(
                        {"error": {"message": "Request body too large"}},
                        status_code=413,
                    )
            request._body = bytes(body)

        return await call_next(request)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> StarletteResponse:
        request_id = str(uuid_module.uuid4())
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        path_params = request.scope.get("path_params") or {}
        guid = str(path_params.get("guid", "")) or getattr(request.state, "gpo_guid", "")
        revision = (
            str(path_params.get("revision", ""))
            or getattr(request.state, "gpo_revision", "")
        )

        safe_guid = _sanitize_log_value(guid) if guid else ""
        safe_revision = _sanitize_log_value(str(revision)) if revision else ""

        route = request.scope.get("route")
        route_path = getattr(route, "path", None)
        path = request.url.path
        safe_path = _sanitize_log_path(path)

        if route_path and safe_path.startswith("/api/"):
            operation = f"{request.method} {route_path}"
        elif safe_path.startswith("/api/"):
            route_template = safe_path
            if safe_guid:
                route_template = route_template.replace(safe_guid, "{guid}")
            operation = f"{request.method} {route_template}"
        else:
            operation = request.method

        outcome = "success" if response.status_code < 400 else "error"
        _logger.info(
            "request request_id=%s operation=%s method=%s status=%d "
            "outcome=%s duration_ms=%.1f%s%s",
            request_id,
            operation,
            request.method,
            response.status_code,
            outcome,
            duration_ms,
            f" gpo_guid={safe_guid}" if safe_guid else "",
            f" revision={safe_revision}" if safe_revision else "",
        )
        response.headers["X-Request-ID"] = request_id
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if not hasattr(app.state, "store"):
        db_path = os.getenv("GPO_STUDIO_DB", "gpo-studio.db")
        app.state.store = WorkspaceStore(db_path)
        app.state.owns_store = True
        _logger.info("workspace_opened")
    if not hasattr(app.state, "admx_catalogue"):
        admx_dir = os.getenv("GPO_STUDIO_ADMX_DIR", "./admx")
        try:
            from .template_store import ingest_source

            result = ingest_source("default", "local", Path(admx_dir))
            app.state.template_sources = [result]
            app.state.admx_catalogue = result.catalogue
        except Exception as error:
            _logger.warning("Failed to load ADMX catalogue: %s", error)
            app.state.template_sources = []
            app.state.admx_catalogue = AdmxCatalogue()
    if not hasattr(app.state, "template_sources"):
        app.state.template_sources = []
    if not hasattr(app.state, "wmi_catalogue"):
        wmi_catalogue_path = os.getenv("GPO_STUDIO_WMI_CATALOGUE", "")
        if wmi_catalogue_path:
            try:
                app.state.wmi_catalogue = load_wmi_catalogue(Path(wmi_catalogue_path))
            except WmiCatalogueError:
                _logger.warning("Failed to load WMI catalogue")
                app.state.wmi_catalogue = WmiCatalogue()
        else:
            app.state.wmi_catalogue = WmiCatalogue()
    store = app.state.store
    if store.is_degraded:
        _logger.error(
            "startup_quick_check=fail workspace is degraded — "
            "pragmas and migrations skipped"
        )
    else:
        startup_qc = store.quick_check()
        if startup_qc.ok:
            _logger.info("startup_quick_check=ok")
        else:
            _logger.error("startup_quick_check=fail errors=%s", "; ".join(startup_qc.errors))
    try:
        meta = store.workspace_meta()
    except Exception:
        meta = {}
    admx_cat = cast(AdmxCatalogue, getattr(app.state, "admx_catalogue", AdmxCatalogue()))
    wmi_cat = cast(WmiCatalogue, getattr(app.state, "wmi_catalogue", WmiCatalogue()))
    _logger.info(
        "startup_complete schema_version=%s app_version=%s admx_policies=%d wmi_filters=%d",
        meta.get("schema_version", "unknown"),
        __version__,
        len(admx_cat.policies),
        len(wmi_cat.filters),
    )
    yield
    if getattr(app.state, "owns_store", False):
        app.state.store.close()
        _logger.info("workspace_closed")


app = FastAPI(
    title="GPO Studio",
    version=__version__,
    description="Offline-first Group Policy authoring workspace",
    lifespan=lifespan,
)
app.add_middleware(HostValidationMiddleware)
app.add_middleware(OriginValidationMiddleware)
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.mount("/assets", StudioStaticFiles(directory=STATIC), name="assets")


@app.exception_handler(StudioError)
async def studio_error(_request: Request, error: StudioError) -> JSONResponse:
    status = (
        404
        if isinstance(error, NotFoundError)
        else 409
        if isinstance(error, ConflictError)
        else 503
        if isinstance(error, WorkspaceError)
        else 422
    )
    detail: dict[str, Any] = {"message": str(error)}
    if isinstance(error, ValidationError):
        detail["issues"] = [asdict(item) for item in error.issues]
    if isinstance(error, ConflictError):
        detail["code"] = "revision_conflict"
        detail["expected_revision"] = error.expected_revision
        detail["current_revision"] = error.current_revision
    return JSONResponse({"error": detail}, status_code=status)


@app.exception_handler(AmbiguousPolicyError)
async def ambiguous_policy(
    _request: Request, error: AmbiguousPolicyError
) -> JSONResponse:
    """409: the caller must qualify the policy name with its namespace."""
    return JSONResponse(
        {
            "error": {
                "message": str(error),
                "code": "ambiguous_policy",
                "policy_id": error.policy_id,
                "candidates": list(error.candidates),
            }
        },
        status_code=409,
    )


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
    store = _store(request)
    catalogue = _catalogue(request)
    wmi_cat = _wmi_catalogue(request)
    healthy = not store.is_degraded
    try:
        meta = store.workspace_meta()
    except Exception:
        meta = {}
        healthy = False
    return {
        "status": "ok" if healthy else "degraded",
        "version": __version__,
        "schema_version": meta.get("schema_version", "unknown"),
        "mode": "offline-workspace",
        "admx_loaded": len(catalogue.policies) > 0,
        "wmi_catalogue_loaded": len(wmi_cat.filters) > 0,
    }


@app.post("/api/workspace/integrity")
def workspace_integrity(request: Request, full: bool = False) -> dict[str, Any]:
    """Run an integrity check on the workspace database.

    By default runs a quick check (fast, suitable for routine use).
    Pass ``?full=true`` for a thorough integrity check.

    This is a POST endpoint so that Origin validation middleware protects
    it from cross-origin requests.  The check writes integrity metadata
    to the workspace, making it a state-changing operation.
    """
    store = _store(request)
    result = store.full_integrity_check() if full else store.quick_check()
    return result.to_dict()


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
            or query in p.namespace.lower()
        ]
    else:
        policies = list(catalogue.policies)
    policies = policies[:50]
    items = [
        {
            "id": p.id,
            # Clients must address policies by qualified_id: `id` alone is not
            # unique across a multi-namespace central store.
            "qualified_id": p.qualified_id,
            "namespace": p.namespace,
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
    policy = _resolve_policy(request, policy_id)
    payload = asdict(policy)
    payload["qualified_id"] = policy.qualified_id
    return payload


@app.post("/api/admx/policies/{policy_id}/configure")
def configure_policy(
    request: Request, policy_id: str, body: ConfigurePolicyRequest
) -> dict[str, Any]:
    policy = _resolve_policy(request, policy_id)
    config = PolicyConfiguration(side=body.side, values=body.values, state=body.state)
    settings = resolve_policy(policy, config)
    issues: list[ValidationIssue] = []
    for s in settings:
        issues.extend(validate_setting(s))
    if any(i.severity == "error" for i in issues):
        raise ValidationError(issues)
    # Replace by prefix rather than upsert: re-configuring a policy must drop the
    # settings its PREVIOUS state wrote. Enabled -> Disabled would otherwise
    # leave the Enabled element values behind alongside the Disabled value, and
    # Not Configured (no settings at all) would be a silent no-op.
    gpo = _store(request).replace_settings_with_prefix(
        body.gpo_guid,
        body.expected_revision,
        policy_setting_prefix(policy, body.side),
        settings,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo, request)


@app.get("/api/admx/categories")
def admx_categories(request: Request) -> dict[str, Any]:
    catalogue = _catalogue(request)
    items = [
        {"id": c.id, "parent_id": c.parent_id, "display_name": c.display_name}
        for c in catalogue.categories
    ]
    return {"items": items, "count": len(items)}


class IngestTemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["local", "central-store", "vendor-pack", "curated"] = "local"
    path: str = Field(min_length=1)


@app.get("/api/templates")
def list_template_sources(request: Request) -> dict[str, Any]:
    sources: list[Any] = getattr(request.app.state, "template_sources", [])
    items = []
    for result in sources:
        items.append({
            "name": result.source.name,
            "kind": result.source.kind,
            "path": result.source.path,
            "file_count": len(result.source.files),
            "policy_count": len(result.catalogue.policies),
            "error_count": len(result.errors),
        })
    return {"items": items, "count": len(items)}


@app.post("/api/templates/ingest", status_code=201)
def ingest_template_source(request: Request, body: IngestTemplateRequest) -> dict[str, Any]:
    from .template_store import TemplateError, ingest_source

    directory = Path(body.path).resolve()
    template_root = os.getenv("GPO_STUDIO_TEMPLATE_ROOT", "")
    if template_root:
        root = Path(template_root).resolve()
        if not directory.is_relative_to(root):
            raise StudioError(
                f"Path escapes template root: {directory}"
            )
    try:
        result = ingest_source(body.name, body.kind, directory)
    except TemplateError as exc:
        raise StudioError(str(exc)) from exc
    sources: list[Any] = getattr(request.app.state, "template_sources", [])
    sources = [s for s in sources if s.source.name != body.name]
    sources.append(result)
    request.app.state.template_sources = sources
    merged = _merge_template_catalogues(sources)
    request.app.state.admx_catalogue = merged
    return {
        "name": result.source.name,
        "kind": result.source.kind,
        "path": result.source.path,
        "file_count": len(result.source.files),
        "policy_count": len(result.catalogue.policies),
        "errors": [{"path": e.relative_path, "message": e.message} for e in result.errors],
    }


@app.get("/api/templates/collisions")
def template_collisions(request: Request) -> dict[str, Any]:
    from .template_store import detect_collisions

    sources: list[Any] = getattr(request.app.state, "template_sources", [])
    if not sources:
        return {
            "has_issues": False,
            "namespace_collisions": [],
            "file_collisions": [],
            "policy_drift": [],
            "missing_adml": [],
        }
    report = detect_collisions(tuple(sources))
    return {
        "has_issues": report.has_issues,
        "namespace_collisions": [
            {"namespace": c.namespace, "sources": list(c.sources)}
            for c in report.namespace_collisions
        ],
        "file_collisions": [
            {
                "relative_path": c.relative_path,
                "sources": list(c.sources),
                "hashes_differ": c.hashes_differ,
            }
            for c in report.file_collisions
        ],
        "policy_drift": [
            {"qualified_id": d.qualified_id, "sources": list(d.sources), "reason": d.reason}
            for d in report.policy_drift
        ],
        "missing_adml": [
            {"admx_path": m.admx_path, "source": m.source}
            for m in report.missing_adml
        ],
    }


@app.post("/api/templates/lock")
def build_template_lock(request: Request) -> dict[str, Any]:
    from .template_store import build_lock

    sources: list[Any] = getattr(request.app.state, "template_sources", [])
    lock = build_lock(tuple(sources))
    return {"source_hashes": [{"name": n, "sha256": h} for n, h in lock.source_hashes]}


class ValidateLockRequest(BaseModel):
    source_hashes: list[dict[str, str]]


@app.post("/api/templates/lock/validate")
def validate_template_lock(request: Request, body: ValidateLockRequest) -> dict[str, Any]:
    from .template_store import TemplateLock, validate_lock

    sources: list[Any] = getattr(request.app.state, "template_sources", [])
    lock = TemplateLock(
        source_hashes=tuple((h["name"], h["sha256"]) for h in body.source_hashes)
    )
    violations = validate_lock(lock, tuple(sources))
    return {"valid": len(violations) == 0, "violations": violations}


def _merge_template_catalogues(sources: list[Any]) -> AdmxCatalogue:
    from .template_store import merge_catalogues

    if not sources:
        return AdmxCatalogue()
    return merge_catalogues(tuple(sources))


@app.get("/api/gpos")
def list_gpos(request: Request) -> dict[str, Any]:
    gpos = _store(request).list_gpos()
    return {"items": [_gpo_to_api_dict(gpo) for gpo in gpos], "count": len(gpos)}


@app.post("/api/gpos", status_code=201)
def create_gpo(request: Request, body: CreateGPO) -> dict[str, Any]:
    gpo = _store(request).create_gpo(
        body.name, body.description, identity=_identity(body.actor), reason=body.reason
    )
    return _gpo_payload(gpo, request)


@app.get("/api/gpos/{guid}")
def get_gpo(request: Request, guid: str) -> dict[str, Any]:
    return _gpo_payload(_store(request).get_gpo(guid), request)


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
    return _gpo_payload(gpo, request)


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
    return _gpo_payload(gpo, request)


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
    return _gpo_payload(gpo, request)


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
    return _gpo_payload(gpo, request)


@app.post("/api/gpos/{guid}/links", status_code=201)
def add_link(request: Request, guid: str, body: LinkMutation) -> dict[str, Any]:
    gpo = _store(request).put_link(
        guid,
        body.expected_revision,
        body.link.model_dump(),
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo, request)


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
    return _gpo_payload(gpo, request)


@app.delete("/api/gpos/{guid}/links/{link_id}")
def delete_link(request: Request, guid: str, link_id: str, body: DeleteMutation) -> dict[str, Any]:
    gpo = _store(request).delete_link(
        guid,
        link_id,
        body.expected_revision,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo, request)


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
    return _gpo_payload(gpo, request)


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
    return _gpo_payload(gpo, request)


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
    return _gpo_payload(gpo, request)


@app.put("/api/gpos/{guid}/wmi-filter")
def set_wmi_filter(request: Request, guid: str, body: WmiFilterMutation) -> dict[str, Any]:
    gpo = _store(request).set_wmi_filter(
        guid,
        body.expected_revision,
        body.wmi_filter.model_dump(),
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo, request)


@app.delete("/api/gpos/{guid}/wmi-filter")
def clear_wmi_filter(request: Request, guid: str, body: DeleteMutation) -> dict[str, Any]:
    gpo = _store(request).set_wmi_filter(
        guid,
        body.expected_revision,
        None,
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo, request)


@app.post(
    "/api/gpos/{guid}/preferences/reorder",
    response_model=GpoPayloadResponse,
)
def reorder_gpp(
    request: Request, guid: str, body: GppReorderMutation
) -> dict[str, Any]:
    gpo = _store(request).reorder_gpp(
        guid,
        body.expected_revision,
        body.scope,
        body.kind,
        tuple(body.ordered_ids),
        identity=_identity(body.actor),
        reason=body.reason,
    )
    return _gpo_payload(gpo, request)


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
    return _gpo_payload(gpo, request)


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
    return _gpo_payload(gpo, request)


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
    return _gpo_payload(gpo, request)


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
    return _gpo_payload(gpo, request)


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
    return _gpo_payload(gpo, request)


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
    return _gpo_payload(gpo, request)


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
    return _gpo_payload(gpo, request)


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
    return _gpo_payload(gpo, request)


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
    return _gpo_payload(gpo, request)


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
    return _gpo_payload(gpo, request)


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
    return _gpo_payload(gpo, request)


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
    return _gpo_payload(gpo, request)


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


@app.get("/api/gpos/{guid}/revisions/diff")
def revision_diff(
    request: Request,
    guid: str,
    from_revision: int = Query(ge=1),
    to_revision: int = Query(ge=1),
) -> dict[str, Any]:
    if from_revision == to_revision:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="identical_revisions",
                message="Choose two different revisions to compare.",
                path="to_revision",
            )
        ])
    old = gpo_from_dict(_store(request).get_revision(guid, from_revision).snapshot)
    new = gpo_from_dict(_store(request).get_revision(guid, to_revision).snapshot)
    return asdict(diff_gpos(old, new))


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
    return _gpo_payload(gpo, request)


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


@app.get("/api/gpos/{guid}/report.txt")
def report(request: Request, guid: str) -> Response:
    gpo = _store(request).get_gpo(guid)
    headers = {"Content-Disposition": f'attachment; filename="{gpo.guid}-report.txt"'}
    return Response(
        policy_report(gpo),
        media_type="text/plain; charset=utf-8",
        headers=headers,
    )


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


@app.get("/api/imports/capabilities")
def import_capabilities() -> dict[str, Any]:
    inbox = os.getenv("GPO_STUDIO_INBOX_DIR")
    return {
        "gpmc_backup": {
            "enabled": bool(inbox),
            "requires_inbox": True,
            "inbox_configured": bool(inbox),
            "path_mode": "relative-to-inbox" if inbox else "relative-working-directory",
        }
    }


@app.post("/api/backups/import", status_code=201)
def import_backup(request: Request, body: BackupImportRequest) -> dict[str, Any]:
    backup_dir = _validate_inbox_path(body.path)

    try:
        if not backup_dir.is_dir():
            raise StudioError("Backup path is not a directory")
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
                raise StudioError("Migration table is not a file")
            table = parse_migration_table(mig_path)
            temp_gpo_for_migration = GPO(
                guid="import-preview",
                name=backup_gpo.display_name or "Imported GPO",
                security_filters=security_filters,
                domain=backup_gpo.domain or "studio.local",
            )
            migrated_gpo = apply_migration(temp_gpo_for_migration, table)
            security_filters = migrated_gpo.security_filters
    except BackupError as error:
        # Backup parser errors may contain untrusted policy fields (for
        # example an invalid permission or target type).  Keep those details
        # in the exception chain for local diagnostics, but never reflect
        # them into the HTTP response.
        raise StudioError("Invalid or unsafe backup content") from error
    except StudioError:
        raise
    except (RegistryPolError, GppError) as error:
        raise StudioError("Invalid or malformed policy data in backup") from error
    except OSError as error:
        raise StudioError("Filesystem error during backup import") from error
    except Exception as error:
        raise StudioError("Backup import failed") from error

    temp_gpo = GPO(
        guid="import-preview",
        name=backup_gpo.display_name or "Imported GPO",
        settings=all_settings,
        security_filters=security_filters,
        wmi_filter=wmi_filter,
        domain=backup_gpo.domain or "studio.local",
        gpp_collections=gpp_collections,
        status="archived",
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
        # Imports are immutable evidence by default. Editing requires the
        # explicit fork endpoint so the source snapshot remains reviewable.
        status="archived",
    )
    return _gpo_payload(gpo, request)


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
    return _gpo_payload(gpo, request)


@app.post("/api/estate/diff")
def estate_diff(request: Request, body: EstateDiffRequest) -> dict[str, Any]:
    store = _store(request)
    baseline = store.get_gpo(body.baseline_guid)
    draft = store.get_gpo(body.draft_guid)
    observed = store.get_gpo(body.observed_guid)
    result = three_way_diff(baseline, draft, observed)
    return asdict(result)
