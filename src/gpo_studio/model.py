"""Domain types shared by storage, codecs, and delivery adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .gpp import GppCollection

Side = Literal["computer", "user"]
RegistryType = Literal[
    "REG_SZ", "REG_EXPAND_SZ", "REG_BINARY", "REG_DWORD", "REG_MULTI_SZ", "REG_QWORD"
]


@dataclass(frozen=True, slots=True)
class RegistrySetting:
    id: str
    side: Side
    hive: Literal["HKLM", "HKCU"]
    key: str
    value_name: str
    registry_type: RegistryType
    value: str | int | list[str]
    action: Literal["set", "delete", "delete_all_values"] = "set"
    comment: str = ""

    def identity(self) -> tuple[str, str, str, str]:
        return (self.side, self.hive, self.key.casefold(), self.value_name.casefold())


@dataclass(frozen=True, slots=True)
class GPOLink:
    id: str
    target: str
    enabled: bool = True
    enforced: bool = False
    order: int = 1


@dataclass(frozen=True, slots=True)
class CseFileEntry:
    relative_path: str
    content_hash: str
    size: int


CseSide = Literal["machine", "user"]


@dataclass(frozen=True, slots=True)
class CseMetadataEntry:
    guid: str
    side: CseSide
    files: tuple[CseFileEntry, ...] = field(default_factory=tuple)


TargetType = Literal["user", "group", "computer"]


@dataclass(frozen=True, slots=True)
class SecurityFilter:
    id: str
    principal: str
    permission: Literal["apply", "read"] = "apply"
    inheritable: bool = True
    target_type: TargetType = "group"
    sid: str = ""


@dataclass(frozen=True, slots=True)
class WmiFilter:
    id: str
    name: str
    description: str = ""
    query: str = ""
    language: str = "WQL"


@dataclass(frozen=True, slots=True)
class GPO:
    guid: str
    name: str
    description: str = ""
    computer_enabled: bool = True
    user_enabled: bool = True
    status: Literal["draft", "ready", "archived"] = "draft"
    revision: int = 0
    settings: tuple[RegistrySetting, ...] = field(default_factory=tuple)
    links: tuple[GPOLink, ...] = field(default_factory=tuple)
    source_guid: str = ""
    cse_metadata: tuple[CseMetadataEntry, ...] = field(default_factory=tuple)
    security_filters: tuple[SecurityFilter, ...] = field(default_factory=tuple)
    wmi_filter: WmiFilter | None = None
    gpp_collections: tuple[GppCollection, ...] = field(default_factory=tuple)
    domain: str = "studio.local"
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    severity: Literal["error", "warning"]
    code: str
    message: str
    path: str


@dataclass(frozen=True, slots=True)
class Revision:
    revision: int
    actor: str
    reason: str
    created_at: str
    snapshot: dict[str, Any]


class StudioError(Exception):
    """Base class for expected domain errors."""


class NotFoundError(StudioError):
    """The requested object does not exist."""


class ConflictError(StudioError):
    """The expected revision is stale."""

    def __init__(
        self,
        message: str,
        *,
        expected_revision: int | None = None,
        current_revision: int | None = None,
    ) -> None:
        super().__init__(message)
        self.expected_revision = expected_revision
        self.current_revision = current_revision


class ValidationError(StudioError):
    """The requested mutation is invalid."""

    def __init__(self, issues: list[ValidationIssue]) -> None:
        super().__init__("validation failed")
        self.issues = issues


class WorkspaceError(StudioError):
    """Workspace-level error (corrupt database, schema mismatch, busy)."""
