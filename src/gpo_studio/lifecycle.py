"""Same-domain GPO lifecycle: backup manifests, restore plans, scope survival.

Plan 028 WP-1, reworked for Plan 034 (operator ruling 2026-10-07: lifecycle
exits through a same-domain lane over ``Backup-GPO`` / ``Restore-GPO`` /
``Import-GPO`` / ``Copy-GPO``, then a restore-plan surface; the cross-domain
half is out of scope until the estate has a second domain or trust).

The module is offline: it never touches AD or SYSVOL. It answers three
questions about a GPMC backup an administrator already holds:

* **What is in it?** :func:`manifest_from_backup` turns the output of
  :func:`gpo_studio.backup.read_backup` on a real ``Backup-GPO`` directory into
  a :class:`BackupManifest`. Until this bridge existed nothing in the tree
  built a manifest from anything Windows produced; the only caller was a test.
* **Which GPMC operation, aimed where?** :func:`generate_restore_plan` names the
  cmdlet each :data:`RestoreMode` means and the identity the result will have.
* **What happens to the scope around the settings?** :data:`SCOPE_SURVIVAL`
  states, per operation, whether the GUID, the security filtering, the WMI
  filter association, the links and the description survive. **These are
  predictions.** They are written from Microsoft's documentation and have not
  been measured; ``scripts/windows-oracle/run-lifecycle-oracle.sh`` measures
  every cell, and the table is corrected to what that lane observes.

What was removed, and why (2026-10-07)
--------------------------------------

* **The six-state lifecycle machine** (``draft -> ready -> approved ->
  published -> archived -> deleted``). It had no consumer, and the oracle
  survey classified it (c): a product process whose oracle is an operator, not
  Windows. Keeping it here dressed a process decision up as a Windows model.
  If the product needs approval states, they belong with the workspace's
  revision model, decided by an operator.
* **The second ``MigrationTable``.** :mod:`gpo_studio.migration` is the real
  one -- measured against GPMC-authored tables. The copy here was a
  "planning view" with its own invented ``is_resolved`` flag, and two types of
  the same name for the same file format is how a reader picks the wrong one.
  Migration tables are a cross-domain instrument, which is out of scope.
* **``RestoreStep`` and the invented conflict list.** No code produced a step,
  and ``generate_restore_plan`` claimed to detect missing principals and OUs in
  the target domain without any target-domain data to detect them from. The
  only conflict it actually emitted -- "WMI filter not found in target domain"
  -- fired whenever the backup had a filter, including when the filter
  demonstrably exists. Plans now carry the survival prediction and honest
  warnings about what was *not* checked.
* **The ``uuid4`` target GUID.** For ``Import-GPO -CreateIfNeeded`` and
  ``Copy-GPO`` Windows assigns the new GPO's GUID. A plan that names one it
  made up names a GPO that will never exist.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, assert_never, get_args

from .backup import _GUID_RE, BackupError, GpmcBackup, _local_name, _safe_parse
from .model import ValidationError, ValidationIssue

GpoStatus = Literal[
    "all_settings_enabled",
    "computer_disabled",
    "user_disabled",
    "all_disabled",
]

#: The GPMC operations the same-domain lane measures, named after what they
#: mean on Windows:
#:
#: ``restore_in_place``      ``Restore-GPO`` -- back into the GPO the backup
#:                           was taken from (same GUID, same domain).
#: ``import_into_existing``  ``Import-GPO -TargetGuid|-TargetName`` into a GPO
#:                           that already exists.
#: ``import_as_new``         ``Import-GPO -TargetName -CreateIfNeeded`` where
#:                           the target does not exist yet.
#: ``copy``                  ``Copy-GPO`` without ``-CopyAcl``.
#: ``copy_with_acl``         ``Copy-GPO -CopyAcl``.
WindowsOperation = Literal[
    "restore_in_place",
    "import_into_existing",
    "import_as_new",
    "copy",
    "copy_with_acl",
]

#: Every mode a plan can take: the Windows operations plus ``import_to_draft``,
#: which is Studio-only (the backup becomes a workspace draft) and touches no
#: directory at all.
RestoreMode = WindowsOperation | Literal["import_to_draft"]

#: The scope a GPO carries besides its settings, plus the settings themselves
#: as the control: an operation that loses the settings is not a restore.
ScopeDimension = Literal[
    "settings",
    "gpo_guid",
    "acl_security_filtering",
    "wmi_association",
    "links",
    "description",
]

#: How a dimension fares, judged by comparing the target after the operation
#: with the source as it was when backed up (or copied):
#:
#: ``kept``       the target carries the source's value.
#: ``replaced``   the target carries the value it already had before the
#:                operation (only possible when the target pre-existed;
#:                for ``restore_in_place`` that is the GPO's state just before
#:                the restore).
#: ``lost``       the target carries no value where the source had one
#:                (no links, no WMI filter, no description).
#: ``defaulted``  the target carries what Windows gives a freshly created GPO
#:                and the source's value is gone: a Windows-assigned GUID, or
#:                the default DACL of ``New-GPO``.
Survival = Literal["kept", "lost", "defaulted", "replaced"]

#: Who decides the identity of the GPO an operation produces.
TargetIdentity = Literal["source", "existing_target", "windows_assigned", "studio_draft"]

WINDOWS_OPERATIONS: tuple[WindowsOperation, ...] = get_args(WindowsOperation)
SCOPE_DIMENSIONS: tuple[ScopeDimension, ...] = get_args(ScopeDimension)


def _predict(operation: WindowsOperation) -> Mapping[ScopeDimension, Survival]:
    """One operation's row of :data:`SCOPE_SURVIVAL`. PREDICTIONS, not evidence.

    Sources and confidence, so the lane's corrections can be read against
    them: GPMC's documented rules are that a backup holds the settings, the
    DACL and the WMI filter *link* but not SOM links; that restore puts back
    settings, DACL and WMI link into the original GPO; that import transfers
    settings only; and that copy makes a new GPO with the default DACL unless
    asked to copy the source's. The description lives in ``GPO.cmt``, which
    ``Backup-GPO`` captures as root SYSVOL content (seen in banked
    ``Backup.xml`` files), so every operation that moves SYSVOL content is
    predicted to move it. **The description cells for both imports are the
    least certain** -- whether ``Import-GPO`` writes ``GPO.cmt`` (and whether
    ``Get-GPO`` reads the description from it) has not been observed here. The
    ``copy`` WMI cell (same-domain copy keeps the link) is the next least
    certain.
    """
    match operation:
        case "restore_in_place":
            return {
                "settings": "kept",
                "gpo_guid": "kept",
                "acl_security_filtering": "kept",
                "wmi_association": "kept",
                # Restore does not touch SOM links: whatever the GPO is linked
                # to just before the restore is what it is linked to after.
                "links": "replaced",
                "description": "kept",
            }
        case "import_into_existing":
            return {
                "settings": "kept",
                "gpo_guid": "replaced",
                "acl_security_filtering": "replaced",
                "wmi_association": "replaced",
                "links": "replaced",
                "description": "kept",
            }
        case "import_as_new":
            return {
                "settings": "kept",
                "gpo_guid": "defaulted",
                "acl_security_filtering": "defaulted",
                "wmi_association": "lost",
                "links": "lost",
                "description": "kept",
            }
        case "copy":
            return {
                "settings": "kept",
                "gpo_guid": "defaulted",
                "acl_security_filtering": "defaulted",
                "wmi_association": "kept",
                "links": "lost",
                "description": "kept",
            }
        case "copy_with_acl":
            return {
                "settings": "kept",
                "gpo_guid": "defaulted",
                "acl_security_filtering": "kept",
                "wmi_association": "kept",
                "links": "lost",
                "description": "kept",
            }
        case _:
            assert_never(operation)


#: operation x dimension -> predicted survival. Read :func:`_predict` for what
#: each prediction rests on. Immutable, so a caller cannot "correct" a cell at
#: run time and have a plan agree with a lane for the wrong reason.
SCOPE_SURVIVAL: Mapping[WindowsOperation, Mapping[ScopeDimension, Survival]] = (
    MappingProxyType(
        {op: MappingProxyType(dict(_predict(op))) for op in WINDOWS_OPERATIONS}
    )
)


def cmdlet_for(mode: RestoreMode) -> str:
    """The GPMC cmdlet invocation shape a mode means (no arguments bound)."""
    match mode:
        case "restore_in_place":
            return "Restore-GPO -BackupId <id> -Path <dir>"
        case "import_into_existing":
            return "Import-GPO -BackupId <id> -Path <dir> -TargetGuid <guid> | -TargetName <name>"
        case "import_as_new":
            return "Import-GPO -BackupId <id> -Path <dir> -TargetName <name> -CreateIfNeeded"
        case "copy":
            return "Copy-GPO -SourceGuid <guid> -TargetName <name>"
        case "copy_with_acl":
            return "Copy-GPO -SourceGuid <guid> -TargetName <name> -CopyAcl"
        case "import_to_draft":
            return ""
        case _:
            assert_never(mode)


def target_identity_for(mode: RestoreMode) -> TargetIdentity:
    """Whose GUID the GPO an operation produces carries."""
    match mode:
        case "restore_in_place":
            return "source"
        case "import_into_existing":
            return "existing_target"
        case "import_as_new" | "copy" | "copy_with_acl":
            return "windows_assigned"
        case "import_to_draft":
            return "studio_draft"
        case _:
            assert_never(mode)


# ---------------------------------------------------------------------------
# Backup manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BackupFileEntry:
    """A single file within a GPMC backup, relative to ``DomainSysvol/GPO``."""

    relative_path: str
    content_hash: str           # SHA-256
    size: int


@dataclass(frozen=True, slots=True)
class BackupManifest:
    """Manifest describing a single GPMC backup of a single GPO.

    ``backup_id`` is the backup instance's own GUID (``manifest.xml`` ``ID``),
    which is **not** the GPO's GUID. ``created_at`` is ``BackupTime`` verbatim:
    GPMC writes it without an offset, so it is a naive timestamp in whatever
    clock the backing-up host kept, and no offset is invented for it here.
    """

    backup_id: str
    gpo_guid: str
    gpo_display_name: str
    domain: str
    created_at: str
    comment: str = ""
    gpo_status: GpoStatus = "all_settings_enabled"
    has_wmi_filter: bool = False
    #: The raw ``Backup.xml`` ``WMIFilter`` text. Its shape for a linked filter
    #: has not been captured yet (every banked backup has an empty element), so
    #: it is kept verbatim rather than parsed into a name.
    wmi_filter_reference: str = ""
    files: tuple[BackupFileEntry, ...] = field(default_factory=tuple)

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


def _gpo_status(computer_enabled: bool, user_enabled: bool) -> GpoStatus:
    if computer_enabled and user_enabled:
        return "all_settings_enabled"
    if computer_enabled:
        return "user_disabled"
    if user_enabled:
        return "computer_disabled"
    return "all_disabled"


def _wmi_filter_reference(backup_xml: bytes) -> tuple[bool, str]:
    """Read ``GroupPolicyCoreSettings/WMIFilter`` from ``Backup.xml``.

    An empty element means no filter -- that much is observed in every banked
    backup. Anything else (text or children) counts as a filter link, kept
    verbatim, because the populated shape has not been captured yet.
    """
    root = _safe_parse(backup_xml)
    for elem in root.iter():
        if _local_name(elem.tag) != "GroupPolicyCoreSettings":
            continue
        for child in elem:
            if _local_name(child.tag) != "WMIFilter":
                continue
            text = (child.text or "").strip()
            return (bool(text) or len(child) > 0), text
        return False, ""
    raise BackupError("Backup.xml has no GroupPolicyCoreSettings")


def manifest_from_backup(backup: GpmcBackup) -> BackupManifest:
    """Build a :class:`BackupManifest` from :func:`~gpo_studio.backup.read_backup`.

    One GPO per backup: ``read_backup`` keeps a single backup ID for the whole
    directory, so in a multi-GPO native backup every GPO but one would be
    attributed the wrong instance ID. That is refused here rather than
    reproduced.

    ``comment`` (the *backup's* comment, not the GPO description) is left
    empty: ``read_backup`` does not surface ``bkupInfo.xml``'s ``Comment``.
    """
    if len(backup.gpos) != 1:
        raise ValidationError(
            [
                ValidationIssue(
                    "error",
                    "multi_gpo_backup",
                    f"Backup holds {len(backup.gpos)} GPOs; a manifest describes exactly one "
                    "(read_backup keeps one backup ID per directory).",
                    "gpos",
                )
            ]
        )
    gpo = backup.gpos[0]
    has_wmi_filter = False
    wmi_reference = ""
    files: tuple[BackupFileEntry, ...]
    if gpo.backup_inventory is not None:
        try:
            backup_xml = base64.b64decode(gpo.backup_inventory.backup_xml_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise BackupError("retained Backup.xml is not valid base64") from exc
        has_wmi_filter, wmi_reference = _wmi_filter_reference(backup_xml)
        files = tuple(
            BackupFileEntry(f.relative_path, f.content_hash, f.size)
            for f in gpo.backup_inventory.files
        )
    else:
        files = tuple(
            BackupFileEntry(
                f"{side}/{f.relative_path.replace(chr(92), '/')}", f.content_hash, f.size
            )
            for side, extensions in (
                ("Machine", gpo.machine_extensions),
                ("User", gpo.user_extensions),
            )
            for extension in extensions
            for f in extension.files
        )
        if gpo.wmi_filter is not None:
            has_wmi_filter, wmi_reference = True, gpo.wmi_filter.name
    manifest = BackupManifest(
        backup_id=backup.backup_id,
        gpo_guid=gpo.guid,
        gpo_display_name=gpo.display_name,
        domain=gpo.domain,
        created_at=backup.backup_time,
        gpo_status=_gpo_status(gpo.computer_enabled, gpo.user_enabled),
        has_wmi_filter=has_wmi_filter,
        wmi_filter_reference=wmi_reference,
        files=files,
    )
    errors = [issue for issue in manifest.validate() if issue.severity == "error"]
    if errors:
        raise ValidationError(errors)
    return manifest


@dataclass(frozen=True, slots=True)
class BackupIndex:
    """Index of all backups in a backup location."""

    backups: tuple[BackupManifest, ...] = field(default_factory=tuple)
    location: str = ""          # filesystem path or URI

    def get_backup(self, backup_id: str) -> BackupManifest | None:
        """Look up a backup by id; returns ``None`` if not present."""
        key = _bare_guid(backup_id)
        for backup in self.backups:
            if _bare_guid(backup.backup_id) == key:
                return backup
        return None

    def backups_for_gpo(self, gpo_guid: str) -> tuple[BackupManifest, ...]:
        """Get all backups for a specific GPO, most recent first.

        ``created_at`` is GPMC's naive ``BackupTime``; ordering compares the
        strings, which is chronological only for backups taken under one clock.
        """
        key = _bare_guid(gpo_guid)
        matched = [b for b in self.backups if _bare_guid(b.gpo_guid) == key]
        matched.sort(key=lambda b: b.created_at, reverse=True)
        return tuple(matched)

    def latest_backup(self, gpo_guid: str) -> BackupManifest | None:
        """Get the most recent backup for a GPO."""
        ordered = self.backups_for_gpo(gpo_guid)
        return ordered[0] if ordered else None


def _bare_guid(value: str) -> str:
    return value.strip().strip("{}").casefold()


# ---------------------------------------------------------------------------
# Restore planning
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScopePrediction:
    """One predicted cell of :data:`SCOPE_SURVIVAL`, carried by a plan."""

    dimension: ScopeDimension
    survival: Survival


@dataclass(frozen=True, slots=True)
class RestorePlan:
    """A validated plan for applying a backup (or copying its GPO) in one domain.

    ``target_gpo_guid`` is set only when the GUID is known before the
    operation runs: the backup's own GPO for ``restore_in_place``, or the
    caller's existing target for ``import_into_existing``. For
    ``import_as_new`` / ``copy`` / ``copy_with_acl`` Windows assigns it, and
    the plan says so through ``target_identity`` instead of inventing one.
    """

    backup_id: str
    mode: RestoreMode
    source_gpo_guid: str
    domain: str
    cmdlet: str
    target_identity: TargetIdentity
    target_gpo_guid: str = ""
    target_name: str = ""
    #: Predicted survival per dimension (empty for ``import_to_draft``).
    scope: tuple[ScopePrediction, ...] = ()
    #: What the plan could not check and the operator must.
    warnings: tuple[str, ...] = ()

    def validate(self) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        if not self.backup_id:
            issues.append(
                ValidationIssue("error", "empty_backup_id", "Backup id is empty.", "backup_id")
            )
        match self.mode:
            case "restore_in_place":
                if _bare_guid(self.target_gpo_guid) != _bare_guid(self.source_gpo_guid):
                    issues.append(
                        ValidationIssue(
                            "error",
                            "restore_target_not_source",
                            "Restore-GPO restores only into the GPO the backup was taken "
                            "from; use import_into_existing for any other target.",
                            "target_gpo_guid",
                        )
                    )
            case "import_into_existing":
                if not self.target_gpo_guid and not self.target_name:
                    issues.append(
                        ValidationIssue(
                            "error",
                            "empty_import_target",
                            "Import-GPO into an existing GPO needs -TargetGuid or -TargetName.",
                            "target_gpo_guid",
                        )
                    )
                elif self.target_gpo_guid and not _GUID_RE.match(self.target_gpo_guid):
                    issues.append(
                        ValidationIssue(
                            "error",
                            "invalid_target_gpo_guid",
                            f"Target GPO guid {self.target_gpo_guid!r} is not a valid GUID.",
                            "target_gpo_guid",
                        )
                    )
            case "import_as_new" | "copy" | "copy_with_acl":
                if not self.target_name:
                    issues.append(
                        ValidationIssue(
                            "error",
                            "empty_target_name",
                            f"{self.mode} creates a GPO and needs a target name.",
                            "target_name",
                        )
                    )
                if self.target_gpo_guid:
                    issues.append(
                        ValidationIssue(
                            "error",
                            "target_guid_assigned_by_windows",
                            f"{self.mode} creates a GPO whose GUID Windows assigns; "
                            "a plan cannot name it in advance.",
                            "target_gpo_guid",
                        )
                    )
            case "import_to_draft":
                pass
            case _:
                assert_never(self.mode)
        return tuple(issues)


_RESCOPED_DIMENSIONS: tuple[ScopeDimension, ...] = (
    "links",
    "wmi_association",
    "acl_security_filtering",
    "description",
)


def _scope_and_warnings(
    manifest: BackupManifest, operation: WindowsOperation
) -> tuple[tuple[ScopePrediction, ...], tuple[str, ...]]:
    row = SCOPE_SURVIVAL[operation]
    scope = tuple(ScopePrediction(dim, row[dim]) for dim in SCOPE_DIMENSIONS)
    warnings: list[str] = []
    if manifest.has_wmi_filter and row["wmi_association"] == "kept":
        warnings.append(
            "the backup links a WMI filter; whether that filter still exists in "
            "the domain is not checked"
        )
    for dim in _RESCOPED_DIMENSIONS:
        outcome = row[dim]
        if outcome in ("lost", "defaulted"):
            warnings.append(f"{dim} is predicted {outcome}: re-establish it after {operation}")
    return scope, tuple(warnings)


def generate_restore_plan(
    manifest: BackupManifest,
    mode: RestoreMode,
    target_gpo_guid: str = "",
    target_name: str = "",
    target_domain: str = "",
) -> RestorePlan:
    """Plan one same-domain lifecycle operation over a backup.

    What the plan contains: the cmdlet the mode means, whose GUID the result
    carries, and the :data:`SCOPE_SURVIVAL` prediction for that operation.

    What it **does not** detect, because it has no target-domain data: whether
    the WMI filter the backup links to exists, whether the principals in the
    backup's DACL resolve, and which SOMs the result will be linked to. Each
    of those is stated as a warning when the backup or the prediction makes it
    relevant, rather than asserted as a conflict the plan cannot see.

    Cross-domain plans are refused (ruling 2026-10-07): ``target_domain``
    defaults to the backup's domain and must equal it.

    Raises :class:`ValidationError` if the resulting plan has error-severity
    issues.
    """
    domain = target_domain or manifest.domain
    if domain.casefold() != manifest.domain.casefold():
        raise ValidationError(
            [
                ValidationIssue(
                    "error",
                    "cross_domain_out_of_scope",
                    f"Backup is from {manifest.domain!r}; restoring into {domain!r} is "
                    "cross-domain, which is out of scope until the estate has a second "
                    "domain or trust (ruling 2026-10-07).",
                    "target_domain",
                )
            ]
        )

    target_guid = target_gpo_guid
    name = target_name
    scope: tuple[ScopePrediction, ...] = ()
    warnings: tuple[str, ...] = ()
    match mode:
        case "restore_in_place":
            target_guid = target_gpo_guid or manifest.gpo_guid
            name = target_name or manifest.gpo_display_name
            scope, warnings = _scope_and_warnings(manifest, mode)
        case "import_into_existing" | "import_as_new" | "copy" | "copy_with_acl":
            scope, warnings = _scope_and_warnings(manifest, mode)
        case "import_to_draft":
            target_guid = ""
            name = target_name or manifest.gpo_display_name
        case _:
            assert_never(mode)

    plan = RestorePlan(
        backup_id=manifest.backup_id,
        mode=mode,
        source_gpo_guid=manifest.gpo_guid,
        domain=domain,
        cmdlet=cmdlet_for(mode),
        target_identity=target_identity_for(mode),
        target_gpo_guid=target_guid,
        target_name=name,
        scope=scope,
        warnings=warnings,
    )
    errors = [issue for issue in plan.validate() if issue.severity == "error"]
    if errors:
        raise ValidationError(errors)
    return plan
