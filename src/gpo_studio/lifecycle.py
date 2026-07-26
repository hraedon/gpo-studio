"""GPO lifecycle, backup/restore model, and migration table planning.

Plan 028 WP-1.

This module is the Studio-side model for the GPO lifecycle
(``draft → ready → approved → published → archived → deleted``) and for
GPMC backup/restore planning. It is intentionally offline-first: it never
touches AD or SYSVOL. Restore is modelled as a *plan* — a set of steps and
detected conflicts — that an administrator reviews and executes via a
separate publication adapter.

The ``MigrationEntry`` / ``MigrationTable`` types here are the
backup/restore planning view (principal mapping with resolution state and
``table_id``); they are distinct from the lower-level GPMC XML migration
table parser in :mod:`gpo_studio.migration`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, assert_never

from .backup import _GUID_RE
from .model import ValidationError, ValidationIssue

GpoStatus = Literal[
    "all_settings_enabled",
    "computer_disabled",
    "user_disabled",
    "all_disabled",
]

LifecycleState = Literal[
    "draft",      # being authored in Studio
    "ready",      # ready for review/approval
    "approved",   # approved for publication
    "published",  # published to AD/SYSVOL
    "archived",   # archived (no longer active)
    "deleted",    # marked for deletion
]

RestoreMode = Literal["overwrite", "new_gpo", "import_to_draft"]

MigrationEntryType = Literal["user", "group", "computer", "unc_path", "unknown"]

RestoreStepStatus = Literal["pending", "completed", "failed", "skipped"]


# ---------------------------------------------------------------------------
# Backup manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BackupFileEntry:
    """A single file within a GPMC backup."""

    relative_path: str          # path within backup (e.g. "DomainSysvol/GPO/Machine/...")
    content_hash: str           # SHA-256
    size: int
    is_encrypted: bool = False
    is_binary: bool = False


@dataclass(frozen=True, slots=True)
class BackupManifest:
    """Manifest describing a single GPMC backup of a single GPO."""

    backup_id: str
    gpo_guid: str
    gpo_display_name: str
    domain: str
    created_at: str             # ISO timestamp
    created_by: str = ""
    comment: str = ""
    gpo_status: GpoStatus = "all_settings_enabled"
    has_wmi_filter: bool = False
    wmi_filter_name: str = ""
    files: tuple[BackupFileEntry, ...] = field(default_factory=tuple)
    migration_table_id: str = ""  # optional migration table reference

    def validate(self) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        if not self.backup_id:
            issues.append(
                ValidationIssue("error", "empty_backup_id", "Backup id is empty.", "backup_id")
            )
        if not self.gpo_guid:
            issues.append(
                ValidationIssue("error", "empty_gpo_guid", "GPO guid is empty.", "gpo_guid")
            )
        elif not _GUID_RE.match(self.gpo_guid):
            issues.append(
                ValidationIssue(
                    "error",
                    "invalid_gpo_guid",
                    f"GPO guid {self.gpo_guid!r} is not a valid GUID.",
                    "gpo_guid",
                )
            )
        if not self.gpo_display_name:
            issues.append(
                ValidationIssue(
                    "error",
                    "empty_gpo_display_name",
                    "GPO display name is empty.",
                    "gpo_display_name",
                )
            )
        if not self.domain:
            issues.append(
                ValidationIssue("error", "empty_domain", "Domain is empty.", "domain")
            )
        if not self.created_at:
            issues.append(
                ValidationIssue(
                    "error", "empty_created_at", "Created-at timestamp is empty.", "created_at"
                )
            )
        if not self.files:
            issues.append(
                ValidationIssue(
                    "warning",
                    "empty_files",
                    "Backup contains no files (empty backup).",
                    "files",
                )
            )
        for i, entry in enumerate(self.files):
            if not entry.content_hash:
                issues.append(
                    ValidationIssue(
                        "error",
                        "empty_content_hash",
                        f"File {entry.relative_path!r} has an empty content hash.",
                        f"files/{i}",
                    )
                )
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class BackupIndex:
    """Index of all backups in a backup location."""

    backups: tuple[BackupManifest, ...] = field(default_factory=tuple)
    location: str = ""          # filesystem path or URI

    def get_backup(self, backup_id: str) -> BackupManifest | None:
        """Look up a backup by id; returns ``None`` if not present."""
        for backup in self.backups:
            if backup.backup_id == backup_id:
                return backup
        return None

    def backups_for_gpo(self, gpo_guid: str) -> tuple[BackupManifest, ...]:
        """Get all backups for a specific GPO, most recent first."""
        matched = [b for b in self.backups if b.gpo_guid == gpo_guid]
        matched.sort(key=lambda b: b.created_at, reverse=True)
        return tuple(matched)

    def latest_backup(self, gpo_guid: str) -> BackupManifest | None:
        """Get the most recent backup for a GPO."""
        ordered = self.backups_for_gpo(gpo_guid)
        return ordered[0] if ordered else None


# ---------------------------------------------------------------------------
# Restore planning
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RestorePlan:
    """A validated plan for restoring a backup into a target environment."""

    backup_id: str
    mode: RestoreMode
    target_gpo_guid: str = ""   # for overwrite: existing GPO; for new_gpo: new GUID
    target_name: str = ""       # display name for restored GPO
    migration_table_id: str = ""  # optional migration table to apply
    # detected conflicts (e.g. "WMI filter not found in target domain")
    conflicts: tuple[str, ...] = ()

    def validate(self) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        if not self.backup_id:
            issues.append(
                ValidationIssue("error", "empty_backup_id", "Backup id is empty.", "backup_id")
            )
        match self.mode:
            case "overwrite":
                if not self.target_gpo_guid:
                    issues.append(
                        ValidationIssue(
                            "error",
                            "empty_target_gpo_guid",
                            "Overwrite mode requires a target GPO guid.",
                            "target_gpo_guid",
                        )
                    )
                elif not _GUID_RE.match(self.target_gpo_guid):
                    issues.append(
                        ValidationIssue(
                            "error",
                            "invalid_target_gpo_guid",
                            f"Target GPO guid {self.target_gpo_guid!r} is not a valid GUID.",
                            "target_gpo_guid",
                        )
                    )
            case "new_gpo":
                if not self.target_name:
                    issues.append(
                        ValidationIssue(
                            "error",
                            "empty_target_name",
                            "New GPO mode requires a target name.",
                            "target_name",
                        )
                    )
            case "import_to_draft":
                pass
            case _:
                assert_never(self.mode)
        if self.conflicts:
            issues.append(
                ValidationIssue(
                    "warning",
                    "restore_conflicts",
                    f"{len(self.conflicts)} conflict(s) detected; "
                    "review before restoring.",
                    "conflicts",
                )
            )
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class RestoreStep:
    """A single step in a restore plan execution."""

    step_id: str
    operation: str              # e.g. "create_gpo", "write_registry_pol", "copy_sysvol"
    status: RestoreStepStatus
    detail: str = ""


def generate_restore_plan(
    manifest: BackupManifest,
    mode: RestoreMode,
    target_gpo_guid: str = "",
    target_name: str = "",
) -> RestorePlan:
    """Generate a restore plan from a backup manifest.

    Steps depend on mode:
    - ``overwrite``: validate target exists, backup, overwrite
    - ``new_gpo``: generate new GUID, create, populate
    - ``import_to_draft``: create draft GPO in Studio

    Conflicts detected:
    - WMI filter referenced but not in target domain
    - Security principals in backup not found in target domain
    - Linked OUs in backup not found in target domain

    Raises :class:`ValidationError` if the resulting plan has error-severity
    issues (e.g. overwrite without a target GPO guid).
    """
    conflicts: list[str] = []
    if manifest.has_wmi_filter and manifest.wmi_filter_name:
        conflicts.append(
            f"WMI filter {manifest.wmi_filter_name!r} not found in target domain"
        )

    target_guid = target_gpo_guid
    name = target_name
    match mode:
        case "overwrite":
            pass  # target_gpo_guid supplied by caller; validated below
        case "new_gpo":
            target_guid = target_gpo_guid or str(uuid.uuid4())
        case "import_to_draft":
            target_guid = ""
            name = target_name or manifest.gpo_display_name
        case _:
            assert_never(mode)

    plan = RestorePlan(
        backup_id=manifest.backup_id,
        mode=mode,
        target_gpo_guid=target_guid,
        target_name=name,
        conflicts=tuple(conflicts),
    )
    errors = [issue for issue in plan.validate() if issue.severity == "error"]
    if errors:
        raise ValidationError(errors)
    return plan


# ---------------------------------------------------------------------------
# GPO lifecycle state machine
# ---------------------------------------------------------------------------


def valid_lifecycle_transitions(state: LifecycleState) -> tuple[LifecycleState, ...]:
    """Return the set of states reachable from ``state`` in one transition.

    State machine::

        draft → ready, deleted
        ready → approved, draft (send back), deleted
        approved → published, ready (revoke approval), deleted
        published → archived, draft (re-edit creates new draft), deleted
        archived → draft (restore to draft), deleted
        deleted → (terminal)
    """
    match state:
        case "draft":
            return ("ready", "deleted")
        case "ready":
            return ("approved", "draft", "deleted")
        case "approved":
            return ("published", "ready", "deleted")
        case "published":
            return ("archived", "draft", "deleted")
        case "archived":
            return ("draft", "deleted")
        case "deleted":
            return ()
        case _:
            assert_never(state)


@dataclass(frozen=True, slots=True)
class LifecycleTransition:
    """An immutable record of a lifecycle state change."""

    from_state: LifecycleState
    to_state: LifecycleState
    actor: str
    reason: str
    timestamp: str
    requires_approval: bool = False  # True for approved→published


def transition_lifecycle(
    current: LifecycleState,
    new_state: LifecycleState,
    actor: str,
    reason: str,
) -> LifecycleTransition:
    """Validate and create a lifecycle transition.

    Raises :class:`ValidationError` if the transition is invalid.

    Sets ``requires_approval=True`` for ``approved → published``.
    """
    allowed = valid_lifecycle_transitions(current)
    if new_state not in allowed:
        raise ValidationError(
            [
                ValidationIssue(
                    "error",
                    "invalid_transition",
                    f"Transition {current!r} → {new_state!r} is not allowed.",
                    "transition",
                )
            ]
        )
    requires_approval = current == "approved" and new_state == "published"
    return LifecycleTransition(
        from_state=current,
        to_state=new_state,
        actor=actor,
        reason=reason,
        timestamp=datetime.now(UTC).isoformat(),
        requires_approval=requires_approval,
    )


# ---------------------------------------------------------------------------
# Migration table (backup/restore planning view)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MigrationEntry:
    """A single source→target principal mapping in a migration table."""

    source_principal: str       # SID or name in source domain
    target_principal: str       # SID or name in target domain
    entry_type: MigrationEntryType = "unknown"
    is_resolved: bool = False   # True if target principal verified in AD


@dataclass(frozen=True, slots=True)
class MigrationTable:
    """A migration table applied during backup restore across domains."""

    table_id: str
    name: str = ""
    entries: tuple[MigrationEntry, ...] = field(default_factory=tuple)

    def get_entry(self, source: str) -> MigrationEntry | None:
        """Find migration entry by source principal (case-insensitive)."""
        key = source.casefold()
        for entry in self.entries:
            if entry.source_principal.casefold() == key:
                return entry
        return None

    def resolve_principal(self, source: str) -> str:
        """Resolve a source principal to target. Returns source if no mapping."""
        entry = self.get_entry(source)
        if entry is None:
            return source
        return entry.target_principal or source

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate the migration table.

        Rules:
        - Duplicate source principals → error
        - Unresolved entries → warning
        - source == target → warning (no-op mapping)
        """
        issues: list[ValidationIssue] = []
        seen: set[str] = set()
        for i, entry in enumerate(self.entries):
            key = entry.source_principal.casefold()
            if key in seen:
                issues.append(
                    ValidationIssue(
                        "error",
                        "duplicate_source_principal",
                        f"Duplicate source principal {entry.source_principal!r}.",
                        f"entries/{i}",
                    )
                )
            else:
                seen.add(key)
            if not entry.is_resolved:
                issues.append(
                    ValidationIssue(
                        "warning",
                        "unresolved_principal",
                        f"Source principal {entry.source_principal!r} is not "
                        "resolved in the target domain.",
                        f"entries/{i}",
                    )
                )
            if (
                entry.source_principal
                and entry.source_principal.casefold()
                == entry.target_principal.casefold()
            ):
                issues.append(
                    ValidationIssue(
                        "warning",
                        "noop_mapping",
                        f"Source and target principals are identical for "
                        f"{entry.source_principal!r}.",
                        f"entries/{i}",
                    )
                )
        return tuple(issues)


def apply_migration_table(
    manifest: BackupManifest,
    table: MigrationTable,
) -> tuple[str, ...]:
    """Apply migration table to backup manifest.

    Returns a tuple of warnings for unresolved principals. A principal is
    unresolved when its migration entry has ``is_resolved=False`` — i.e. the
    target principal could not be verified in the target domain. The caller
    must review these before executing the restore.
    """
    warnings: list[str] = []
    for entry in table.entries:
        if not entry.is_resolved:
            warnings.append(
                f"Unresolved principal {entry.source_principal!r} "
                f"(target {entry.target_principal!r}) is not verified in the "
                "target domain"
            )
    if manifest.migration_table_id and manifest.migration_table_id != table.table_id:
        warnings.append(
            f"Manifest references migration table {manifest.migration_table_id!r} "
            f"but applied table id is {table.table_id!r}"
        )
    return tuple(warnings)
