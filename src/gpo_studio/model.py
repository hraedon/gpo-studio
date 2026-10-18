"""Domain types shared by storage, codecs, and delivery adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .fdeploy import FdeployDocument
    from .gpp import GppCollection

Side = Literal["computer", "user"]
RegistryType = Literal[
    "REG_SZ", "REG_EXPAND_SZ", "REG_BINARY", "REG_DWORD", "REG_MULTI_SZ", "REG_QWORD"
]

TrustDirection = Literal["inbound", "outbound", "bidirectional", "unknown"]
TrustType = Literal["parent-child", "cross-link", "external", "forest", "unknown"]
ResolutionState = Literal["resolved", "ambiguous", "deleted", "inaccessible", "stale"]


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


@dataclass(frozen=True, slots=True)
class BackupInventory:
    """Immutable import provenance, not instructions for publishing policy.

    The two native XML documents retain their exact bytes as base64. File
    entries inventory the source payload; they do not contain its bytes.
    """

    backup_xml_base64: str
    report_xml_base64: str = ""
    files: tuple[CseFileEntry, ...] = field(default_factory=tuple)
    #: The source GPO's identity as its backup's manifest states it, recorded
    #: at import (final-r21): its GUID, its domain's DNS name and the domain's
    #: GUID (``GPODomainGuid``). A GPO GUID alone is not unique across estates,
    #: so a replacement from an import must match all three
    #: (`WorkspaceStore.replace_from_import`). ``""``: not recorded -- an
    #: import before 1.1.0, or a backup whose manifest has no domain GUID.
    #: Provenance, not policy (final-r22): stored with the revision, never in
    #: a GPO's public dict, an artifact or a digest (`inventory_dict`).
    source_gpo_guid: str = ""
    source_domain: str = ""
    source_domain_guid: str = ""


#: `BackupInventory`'s source-identity fields: stored, never published.
INVENTORY_IDENTITY_FIELDS = ("source_gpo_guid", "source_domain", "source_domain_guid")


def inventory_dict(data: dict[str, Any], *, stored: bool = False) -> dict[str, Any]:
    """An inventory's dict (``asdict``) as it is published -- in a GPO's
    public dict, an export manifest, a canonical model -- or, *stored*, as the
    workspace keeps it.

    The source identity is import provenance, not policy (final-r22 ruling):
    the workspace keeps it, recorded, for `WorkspaceStore.replace_from_import`,
    and nothing published carries it, so a native import's dict, digests and
    export bytes are the ones they were before it was recorded."""
    return {
        key: value
        for key, value in data.items()
        if key not in INVENTORY_IDENTITY_FIELDS or (stored and value)
    }


TargetType = Literal["user", "group", "computer"]


@dataclass(frozen=True, slots=True)
class SecurityFilter:
    """One entry of a GPO's security filtering.

    ``deny`` carries the ACE's polarity, and its absence was a real defect:
    without it a GPO whose DACL held both an allow and a deny for the same
    principal was modelled as applying, so the model said a GPO would reach a
    machine that Windows keeps it off. Demonstrated against a real client
    (WI-033, run ``rsop-user-observe-20260804065525-9254``).

    Defaulting to ``False`` keeps every existing caller meaning what it meant:
    an entry with no polarity stated is an allow, which is what the readers
    that predate this field were producing.
    """

    id: str
    principal: str
    permission: Literal["apply", "read"] = "apply"
    inheritable: bool = True
    target_type: TargetType = "group"
    sid: str = ""
    deny: bool = False


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
    backup_inventory: BackupInventory | None = None
    #: The imported ``fdeploy1.ini`` (Folder Redirection), parsed. Import
    #: provenance like ``backup_inventory``: read-only, never published (no
    #: writer exists, WI-066), folded into the review digest and kept out of the
    #: policy-semantic one (WI-068).
    fdeploy: FdeployDocument | None = None
    security_filters: tuple[SecurityFilter, ...] = field(default_factory=tuple)
    wmi_filter: WmiFilter | None = None
    gpp_collections: tuple[GppCollection, ...] = field(default_factory=tuple)
    is_starter: bool = False
    template_version: str = ""
    domain: str = "studio.local"
    created_at: str = ""
    updated_at: str = ""
    #: ``(severity, message)`` for each stored value of the wrong JSON type a
    #: load replaced by its field's default (`stored_types`, final-r18).
    #: Validation reports them; never stored, served or exported.
    load_problems: tuple[tuple[str, str], ...] = field(default=(), compare=False)

    def to_dict(self) -> dict[str, Any]:
        """The GPO's public dict: what the API serves and artifacts embed."""
        return self._dict(stored=False)

    def to_stored_dict(self) -> dict[str, Any]:
        """The dict the workspace stores: the public one, plus the import
        record's recorded source identity (`inventory_dict`)."""
        return self._dict(stored=True)

    def _dict(self, *, stored: bool) -> dict[str, Any]:
        data = asdict(self)
        # Never part of a GPO's dict: the key did not exist before final-r18,
        # so a clean GPO's dict is the one it always was.
        data.pop("load_problems", None)
        if data.get("backup_inventory") is not None:
            data["backup_inventory"] = inventory_dict(data["backup_inventory"], stored=stored)
        for collection in data.get("gpp_collections", []):
            # A tolerant load's derived `load_problems` are never stored, served
            # or exported; emptied rather than dropped, so a GPO without any
            # writes exactly the dict it always did (DeepSeek final-r17 N3).
            if collection.get("load_problems"):
                collection["load_problems"] = []
        return data


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


@dataclass(frozen=True, slots=True)
class ForestInfo:
    name: str
    schema_master: str
    domain_naming_master: str
    domains: tuple[str, ...]
    global_catalogs: tuple[str, ...]
    trusts: tuple[TrustInfo, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TrustInfo:
    source: str
    target: str
    direction: TrustDirection
    trust_type: TrustType
    transitive: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DomainInfo:
    dns_name: str
    netbios_name: str
    domain_controllers: tuple[str, ...]
    pdc_emulator: str
    rid_master: str
    infrastructure_master: str
    functional_level: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SiteInfo:
    name: str
    description: str = ""
    subnets: tuple[str, ...] = field(default_factory=tuple)
    site_links: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SubnetInfo:
    cidr: str
    site_name: str
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OrganizationalUnit:
    distinguished_name: str
    name: str
    parent_dn: str = ""
    description: str = ""
    gpo_links: tuple[GPOLink, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PrincipalInfo:
    object_guid: str
    object_sid: str
    object_class: str
    sid_history: tuple[str, ...] = field(default_factory=tuple)
    sam_account_name: str = ""
    display_name: str = ""
    canonical_name: str = ""
    distinguished_name: str = ""
    domain: str = ""
    source_dc: str = ""
    collected_at: str = ""
    resolution_state: ResolutionState = "resolved"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
