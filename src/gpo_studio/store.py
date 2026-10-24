"""SQLite workspace with immutable revisions and optimistic concurrency."""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import threading
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NoReturn, cast

from . import gpp_provenance as provenance
from .fdeploy import FdeployDocument, fdeploy_from_dict
from .gpp import (
    GppCollection,
    GppCommonOptions,
    GppError,
    GppGroup,
    GppGroupMember,
    GppRegistry,
    GppRegistryValue,
    GppScope,
    ensure_editor_ids,
    gpp_collection_from_dict_tolerant,
    gpp_collection_to_dict,
    registry_action_edit,
    serialize_gpp,
    with_refused_marks,
)
from .identity import Identity
from .ilt import IltError
from .model import (
    GPO,
    BackupInventory,
    ConflictError,
    CseFileEntry,
    CseMetadataEntry,
    CseSide,
    GPOLink,
    NotFoundError,
    RegistrySetting,
    Revision,
    SecurityFilter,
    StudioError,
    ValidationError,
    ValidationIssue,
    WmiFilter,
    WorkspaceError,
)
from .registry_repair import (
    RegistryRepairPreview,
    RegistryRepairSource,
    RepairChoice,
    repair_preview,
    repair_source,
    repaired_settings,
)
from .snapshot_documents import (
    RetainedDocument,
    SnapshotDocumentError,
    apply_snapshot_documents,
    extract_snapshot_documents,
    snapshot_digests,
)
from .stored_types import STORED_WRONG_TYPE, check_stored, problem_message, stored_flag
from .validation import (
    text_issues,
    validate_gpo,
    validate_gpp_collection,
    validate_ready_transition,
    validate_setting,
)
from .workspace_ops import (
    IntegrityResult,
    quick_check,
    release_workspace_lock,
    try_acquire_workspace_lock,
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _resolve_actor(identity: Identity | str) -> str:
    if isinstance(identity, str):
        return identity
    return identity.actor


def setting_id_issues(settings: Iterable[RegistrySetting]) -> list[ValidationIssue]:
    seen: set[str] = set()
    issues: list[ValidationIssue] = []
    for index, setting in enumerate(settings):
        if setting.id in seen:
            issues.append(ValidationIssue(
                "error", "duplicate_setting_id",
                "Registry setting editor IDs must be unique. Recover every intended "
                "registry record into a fresh draft, or reconfigure its uniquely owned policy.",
                f"settings/{index}/id",
            ))
        seen.add(setting.id)
    return issues


def _require_unambiguous_setting_id(gpo: GPO, setting_id: str) -> None:
    if sum(setting.id == setting_id for setting in gpo.settings) > 1:
        raise ValidationError([ValidationIssue(
            "error", "ambiguous_setting_id",
            "More than one registry record has this editor ID. This action cannot choose "
            "one safely; review all records and recover them into a fresh draft.",
            "setting_id",
        )])


def _setting(data: dict[str, Any]) -> RegistrySetting:
    return RegistrySetting(
        id=str(data["id"]),
        side=data["side"],
        hive=data["hive"],
        key=str(data["key"]),
        value_name=str(data["value_name"]),
        registry_type=data["registry_type"],
        value=data["value"],
        action=data.get("action", "set"),
        comment=str(data.get("comment", "")),
    )


def _link(data: dict[str, Any]) -> GPOLink:
    return GPOLink(
        id=str(data["id"]),
        target=str(data["target"]),
        enabled=stored_flag(data.get("enabled", True), True),
        enforced=stored_flag(data.get("enforced", False), False),
        order=int(data.get("order", 1)),
    )


def _cse_file_entry(data: dict[str, Any]) -> CseFileEntry:
    return CseFileEntry(
        relative_path=str(data["relative_path"]),
        content_hash=str(data["content_hash"]),
        size=int(data["size"]),
    )


def _cse_metadata_entry(data: dict[str, Any]) -> CseMetadataEntry:
    # A side outside its set is kept as stored and reported by validation
    # (`closed_sets`), never fatal on read (final-r17).
    side_raw = str(data["side"])
    return CseMetadataEntry(
        guid=str(data["guid"]),
        side=cast(CseSide, side_raw),
        files=tuple(_cse_file_entry(f) for f in data.get("files", [])),
    )


#: Validation codes that mean "this is not the identity the operator reviewed":
#: a link DN that is not exactly a DN, a security-filter SID that is malformed or
#: carries a control or format character, a principal that is padded or carries
#: one. A draft may hold other errors (a principal written as a bare group name,
#: say) until it is marked ready; these the store refuses outright, because a
#: stored value of this kind reaches the PowerShell plan and the GPO's ACL as a
#: different identity (Sol final-r6 B2b).
_IDENTITY_ERRORS = frozenset({
    "invalid_link_target",
    "control_character_in_principal",
    "principal_surrounding_whitespace",
    "control_character_in_sid",
    "invalid_sid_format",
})


def _issue_key(issue: ValidationIssue) -> tuple[str, str, str]:
    """An error's identity across an edit: item indices shift, the rest does not."""
    return (issue.code, re.sub(r"/[0-9]+(?=/|$)", "/#", issue.path or ""), issue.message)


def _refuse_identity_issues(gpo: GPO, prefix: str) -> None:
    errors = [
        issue
        for issue in validate_gpo(gpo)
        if issue.severity == "error"
        and issue.code in _IDENTITY_ERRORS
        and (issue.path or "").startswith(prefix)
    ]
    if errors:
        raise ValidationError(errors)


def _security_filter(data: dict[str, Any], *, strict: bool = True) -> SecurityFilter:
    """A security filter from a dict. Stored data (``strict=False``) keeps a
    value outside its closed set, for validation (`closed_sets`) to report;
    a mutation's values refuse it here (final-r17)."""
    permission = data.get("permission", "apply")
    if strict and permission not in ("apply", "read"):
        raise ValueError(f"Invalid permission: {permission!r}")
    target_type = data.get("target_type", "group")
    if strict and target_type not in ("user", "group", "computer"):
        raise ValueError(f"Invalid target_type: {target_type!r}")
    return SecurityFilter(
        id=str(data["id"]),
        principal=str(data["principal"]),
        permission=permission,
        inheritable=stored_flag(data.get("inheritable", True), True),
        target_type=target_type,
        sid=str(data.get("sid", "")),
        # Absent means allow, which is what every row written before WI-033
        # meant. Reading it is not optional: without this the field round-trips
        # to False, so a deny authored in one session came back an ALLOW in the
        # next -- the polarity silently inverted by persistence alone.
        deny=stored_flag(data.get("deny", False), False),
    )


def _wmi_filter(data: dict[str, Any]) -> WmiFilter:
    return WmiFilter(
        id=str(data["id"]),
        name=str(data["name"]),
        description=str(data.get("description", "")),
        query=str(data.get("query", "")),
        language=str(data.get("language", "WQL")),
    )


def _gpp_collection(data: dict[str, Any]) -> GppCollection:
    # Stored data is read tolerantly (final-r14): retained content a later
    # rule refuses is reported and refused at export, never fatal on read.
    return gpp_collection_from_dict_tolerant(data)


@dataclass
class _Grants:
    """What one mutation may add beyond what the GPO held (final-r13), per scope.

    ``fragments[scope]``: retained fragments (digests, `gpp_provenance`) the
    mutation may introduce into that scope's collection -- a clone's copy, a
    restored item or revision. ``records[scope]``: the provenance records it
    may carry with them there. Every other mutation adds neither: it can only
    keep or drop what is stored.

    Keyed by scope because a digest does not name its scope: a record pooled
    across scopes let a whole-revision restore give an unverified user-side
    fragment the record of an identical computer-side one (Sol final-r14 B1).
    """

    fragments: dict[str, set[str]] = field(default_factory=dict)
    records: dict[str, set[str]] = field(default_factory=dict)

    def grant(self, scope: str, fragments: Iterable[str], records: Iterable[str]) -> None:
        self.fragments.setdefault(scope, set()).update(fragments)
        self.records.setdefault(scope, set()).update(records)


def _provenance_refusal(added: list[provenance.Fragment]) -> ValidationError:
    places = sorted({"/".join(key[:3]) for key, _text in added})
    return ValidationError([
        ValidationIssue(
            severity="error",
            code="raw_content_not_accepted",
            message=(
                "Retained content (attributes, children, raw targeting, imported "
                "elements, run-once ids) comes only from an import, a fork, a clone "
                "or a restore; a mutation may keep or drop it, never add or change it: "
                + ", ".join(places[:5])
            ),
            path="gpp_collections",
        )
    ])


def _keep_provenance(before: GPO, after: GPO, grants: _Grants | None) -> GPO:
    """*after* with each collection's retained fragments checked and its
    provenance records kept, against *before* (the GPO the mutation started
    from). Refuses a fragment the mutation added without a grant; keeps only
    the records *before* held (and the grant's) of the fragments still there."""
    grants = grants or _Grants()
    old = {collection.scope: collection for collection in before.gpp_collections}
    collections: list[GppCollection] = []
    changed = False
    for collection in after.gpp_collections:
        previous = old.get(collection.scope)
        if collection is previous:
            collections.append(collection)
            continue
        scope = collection.scope
        added = provenance.new_fragments(
            collection, previous, grants.fragments.get(scope, set())
        )
        if added:
            raise _provenance_refusal(added)
        kept = provenance.keep_records(collection, previous, grants.records.get(scope, set()))
        if kept.load_problems:
            # What a tolerant load reported belongs to the stored data; after
            # this mutation only what the new collection still holds stands
            # (deleting the offending item clears it).
            kept = with_refused_marks(
                replace(
                    kept,
                    load_problems=gpp_collection_from_dict_tolerant(
                        gpp_collection_to_dict(replace(kept, load_problems=()))
                    ).load_problems,
                )
            )
        changed = changed or kept is not collection
        collections.append(kept)
    if not changed:
        return after
    return replace(after, gpp_collections=tuple(collections))


def _loaded_gpp(gpo: GPO) -> GPO:
    """Every loaded item with stable targeting-predicate ids and the legacy raw
    ``FilterRunOnce`` shape normalised (final-r13). A snapshot written before
    predicate ids existed gets ids derived from its item, so every load of it
    agrees until the schema migration writes them."""
    if not gpo.gpp_collections:
        return gpo

    def loader(scope: str) -> Callable[[str, Any], Any]:
        def load(family: str, item: Any) -> Any:
            item = provenance.normalize_run_once(item)
            return provenance.with_predicate_ids(
                item, seed=f"{gpo.guid}/{scope}/{family}/{getattr(item, 'id', '')}"
            )

        return load

    return replace(
        gpo,
        gpp_collections=tuple(
            provenance.map_items(collection, loader(collection.scope))
            for collection in gpo.gpp_collections
        ),
    )


def _keep_document_position[T: (GppGroup, GppRegistry)](edited: T, existing: T) -> T:
    """An edit replaces an item's content, not its place or its import record.

    The API's item payloads carry no ``document_position`` (WI-073), so an
    edited item inherits the slot of the item it replaces. A new item has no
    slot and is written after every positioned item (see ``gpp.py``,
    "Document order").

    Nor do they carry ``native_xml`` (WI-080), and the API never accepts one,
    so an edited item inherits the element the item it replaces was imported
    from. The writer reconciles it with the edit (``gpp_native``): every value
    the edit changed is written from the model, and only what the model does
    not type, or did not change, is written as imported.
    """
    if edited.document_position is None:
        edited = replace(edited, document_position=existing.document_position)
    if not edited.native_xml and existing.native_xml:
        edited = replace(edited, native_xml=existing.native_xml)
    return edited


def _with_common_edits[T: (GppGroup, GppRegistry)](
    edited: T, existing: T | None, common_edits: Mapping[str, bool] | None
) -> T:
    """Apply an API add/edit's common options to the item it writes (WI-082).

    ``None`` (a direct caller): the item's own options stand, as before.
    A mapping (the API): only the options it names change; every other one,
    and the run-once id, is carried over from the item being replaced, or is
    the default for a new item. The API's payloads never carried the options,
    so before WI-082 an edit reset them all.
    """
    if common_edits is None:
        return edited
    unknown = set(common_edits) - _EDITABLE_COMMON_OPTIONS
    if unknown:
        raise ValueError(f"not editable common options: {sorted(unknown)}")
    base = existing.common if existing is not None else GppCommonOptions()
    changes: dict[str, Any] = dict(common_edits)
    return replace(edited, common=replace(base, **changes))


#: What an add or edit may set; the run-once id is identity (WI-080), not an option.
_EDITABLE_COMMON_OPTIONS = frozenset({
    "apply_once", "remove_when_unapplied", "user_security_context", "disabled", "stop_on_error",
})


def _assign_legacy_gpp_ids(gpo: GPO) -> GPO:
    guid = gpo.guid
    new_collections: list[GppCollection] = []
    changed = False
    for collection in gpo.gpp_collections:
        scope = collection.scope
        new_groups: list[GppGroup] = []
        for g_idx, group in enumerate(collection.groups):
            new_group = group
            if not group.id:
                new_group = replace(
                    new_group,
                    id=str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL, f"{guid}/{scope}/group/{g_idx}"
                        )
                    ),
                )
                changed = True
            new_members: list[GppGroupMember] = []
            members_changed = False
            for m_idx, member in enumerate(new_group.members):
                if not member.id:
                    new_members.append(
                        replace(
                            member,
                            id=str(
                                uuid.uuid5(
                                    uuid.NAMESPACE_URL,
                                    f"{guid}/{scope}/group/{g_idx}/member/{m_idx}",
                                )
                            ),
                        )
                    )
                    members_changed = True
                    changed = True
                else:
                    new_members.append(member)
            if members_changed:
                new_group = replace(new_group, members=tuple(new_members))
            new_groups.append(new_group)
        new_registry: list[GppRegistry] = []
        for r_idx, registry in enumerate(collection.registry):
            new_reg = registry
            if not registry.id:
                new_reg = replace(
                    new_reg,
                    id=str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"{guid}/{scope}/registry/{r_idx}",
                        )
                    ),
                )
                changed = True
            if not new_reg.uid:
                new_reg = replace(
                    new_reg,
                    uid=str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"studio/registry/{new_reg.id}",
                        )
                    ),
                )
                changed = True
            new_value = new_reg.value
            if not new_value.id:
                new_value = replace(
                    new_value,
                    id=str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"{guid}/{scope}/registry/{r_idx}/value",
                        )
                    ),
                )
                new_reg = replace(new_reg, value=new_value)
                changed = True
            new_registry.append(new_reg)
        new_collections.append(
            replace(
                collection,
                groups=tuple(new_groups),
                registry=tuple(new_registry),
            )
        )
    if not changed:
        return gpo
    return replace(gpo, gpp_collections=tuple(new_collections))


#: The deletion archive's tables (`WorkspaceStore._archive_rows`): name ->
#: (columns in order, each ``TEXT``/``INTEGER NOT NULL``; the primary key).
_ARCHIVE_TABLES: dict[str, tuple[tuple[tuple[str, str], ...], tuple[str, ...]]] = {
    "deleted_gpos": (
        (
            ("deletion_id", "TEXT"), ("deleted_at", "TEXT"), ("guid", "TEXT"),
            ("name", "TEXT"), ("revision", "INTEGER"), ("snapshot_json", "TEXT"),
            ("updated_at", "TEXT"),
        ),
        ("deletion_id",),
    ),
    "deleted_revisions": (
        (
            ("deletion_id", "TEXT"), ("deleted_at", "TEXT"), ("gpo_guid", "TEXT"),
            ("revision", "INTEGER"), ("actor", "TEXT"), ("reason", "TEXT"),
            ("created_at", "TEXT"), ("snapshot_json", "TEXT"),
        ),
        ("deletion_id", "revision"),
    ),
    "deleted_snapshot_documents": (
        (
            ("deletion_id", "TEXT"), ("deleted_at", "TEXT"), ("gpo_guid", "TEXT"),
            ("revision", "INTEGER"), ("digest", "TEXT"),
        ),
        ("deletion_id", "revision", "digest"),
    ),
    "deleted_retained_documents": (
        (
            ("deletion_id", "TEXT"), ("deleted_at", "TEXT"), ("digest", "TEXT"),
            ("content_base64", "TEXT"), ("byte_length", "INTEGER"),
        ),
        ("deletion_id", "digest"),
    ),
}


#: Checks run on a GPO before it is committed (`register_answer_check`).
_ANSWER_CHECKS: list[Callable[[GPO], object]] = []


def register_answer_check(check: Callable[[GPO], object]) -> None:
    """Run *check* on every GPO a mutation or creation is about to commit,
    before the commit (DeepSeek final-r19 N3). The API registers the build of
    the payload it answers with: if that cannot be built, the change is
    rolled back instead of committed and then answered with a 500."""
    if check not in _ANSWER_CHECKS:
        _ANSWER_CHECKS.append(check)


#: What each check built for a GPO committed in THIS context, by guid:
#: ``guid -> (the committed GPO object, {check: result})``. A context
#: variable, never mutated in place (each change sets a new mapping), so what
#: one request committed is visible to that request only (Sol final-r20 B2:
#: it was a process-global dictionary any failed creation could evict from).
_COMMITTED: ContextVar[Mapping[str, tuple[GPO, Mapping[Any, object]]] | None] = ContextVar(
    "store_committed_answers", default=None
)
#: Commits remembered per context. A request commits one or two GPOs; the
#: bound only keeps a long-lived context (a script driving the store
#: directly) from holding every payload it ever built.
_COMMITTED_MAX = 8


def _before_commit(gpo: GPO) -> dict[Any, object]:
    """Each registered check's result for *gpo*, built before its commit.

    Validation is run either way: with no check registered (the store used
    on its own) it is the crash pre-flight; with one, the check's payload
    carries its result."""
    if not _ANSWER_CHECKS:
        validate_gpo(gpo)
    return {check: check(gpo) for check in _ANSWER_CHECKS}


def _after_commit(gpo: GPO, answers: dict[Any, object]) -> None:
    current = dict(_COMMITTED.get() or {})
    current.pop(gpo.guid, None)
    while len(current) >= _COMMITTED_MAX:
        current.pop(next(iter(current)))
    current[gpo.guid] = (gpo, answers)
    _COMMITTED.set(current)


@contextlib.contextmanager
def answer_scope() -> Iterator[None]:
    """A scope of its own for the answers commits leave (`committed_answer`):
    the API opens one per request, so a request sees only what it committed,
    whatever context it was started from."""
    token = _COMMITTED.set(None)
    try:
        yield
    finally:
        _COMMITTED.reset(token)


def committed_answer(gpo: GPO, check: Callable[[GPO], object]) -> object | None:
    """What *check* built for *gpo* before this context committed it, taken
    (once), or ``None`` if this context committed no GPO with its guid.

    A committed GPO answered with another object than the one committed is a
    programming error -- the answer would be rebuilt after the commit, which
    is what building it first exists to rule out -- and raises."""
    current = _COMMITTED.get() or {}
    entry = current.get(gpo.guid)
    if entry is None:
        return None
    _COMMITTED.set({guid: value for guid, value in current.items() if guid != gpo.guid})
    committed, answers = entry
    if committed is not gpo or check not in answers:
        raise RuntimeError(
            f"GPO {gpo.guid} was committed in this request but is being answered "
            "without the answer built before its commit"
        )
    return answers[check]


def _replaced_on_read(gpo: GPO) -> list[ValidationIssue]:
    """The stored values *gpo*'s read replaced: of the wrong type, missing, or
    an unreadable import record (final-r18).

    The typed load replaces each with its default, never coercing it; the
    GPO is blocked. Storing the GPO again would store the default in its
    place -- an edit, a fork, a derived GPO or a copy would turn "false" into
    the default ``true`` and unblock it -- so each refuses such a GPO. The
    recoveries (`stored_types.RECOVERIES`, named in each issue) replace the
    content whole: a revision restore, a replacement from a fresh import
    (`replace_from_import`), or deleting the GPO (`delete_gpo`)."""
    from .validation import validate_gpo

    if not _has_replaced(gpo):
        return []
    return [
        issue
        for issue in validate_gpo(gpo)
        if issue.code == "stored_value_wrong_type" and issue.severity == "error"
    ]


def _has_replaced(gpo: GPO) -> bool:
    return bool(gpo.load_problems) or any(
        message.startswith(STORED_WRONG_TYPE)
        for collection in gpo.gpp_collections
        for _severity, message in collection.load_problems
    )


def _plain_guid(value: str) -> str:
    return value.strip().strip("{}").lower()


def require_unreplaced(gpo: GPO) -> None:
    issues = _replaced_on_read(gpo)
    if issues:
        raise ValidationError(issues)


def _stored_record[Record](
    loader: Callable[[object], Record],
    value: object,
    path: str,
    problems: list[tuple[str, str]],
) -> Record | None:
    """An import record (the native inventory, the fdeploy document) read back
    tolerantly: one its loader refuses is recorded as a problem -- an error,
    so the GPO is blocked -- and left out, never fatal on read (final-r18)."""
    if value is None:
        return None
    try:
        return loader(value)
    except StudioError as error:
        problems.append((path, f"the stored record is unreadable ({error})"))
        return None


def gpo_from_dict(data: dict[str, Any]) -> GPO:
    from .backup_inventory import inventory_from_dict

    # Stored JSON is held to the model's types first, never coerced: a value of
    # the wrong type is reported and replaced by its default (final-r18,
    # `stored_types`), the preferences and import records included.
    problems: list[tuple[str, str]] = []
    data = check_stored(
        GPO,
        data,
        "",
        problems,
        skip=frozenset({"load_problems"}),
    )
    gpo = GPO(
        guid=str(data["guid"]),
        name=str(data["name"]),
        description=str(data.get("description", "")),
        computer_enabled=stored_flag(data.get("computer_enabled", True), True),
        user_enabled=stored_flag(data.get("user_enabled", True), True),
        status=data.get("status", "draft"),
        revision=int(data.get("revision", 0)),
        settings=tuple(_setting(item) for item in data.get("settings", [])),
        links=tuple(_link(item) for item in data.get("links", [])),
        source_guid=str(data.get("source_guid", "")),
        cse_metadata=tuple(
            _cse_metadata_entry(item) for item in data.get("cse_metadata", [])
        ),
        backup_inventory=_stored_record(
            inventory_from_dict, data.get("backup_inventory"), "/backup_inventory", problems
        ),
        fdeploy=_stored_record(fdeploy_from_dict, data.get("fdeploy"), "/fdeploy", problems),
        security_filters=tuple(
            _security_filter(item, strict=False) for item in data.get("security_filters", [])
        ),
        wmi_filter=_wmi_filter(data["wmi_filter"]) if data.get("wmi_filter") is not None else None,
        gpp_collections=tuple(
            _gpp_collection(item) for item in data.get("gpp_collections", [])
        ),
        is_starter=stored_flag(data.get("is_starter", False), False),
        template_version=str(data.get("template_version", "")),
        domain=str(data.get("domain", "studio.local")),
        created_at=str(data.get("created_at", "")),
        updated_at=str(data.get("updated_at", "")),
        load_problems=tuple(("error", problem_message(path, what)) for path, what in problems),
    )
    return _loaded_gpp(_assign_legacy_gpp_ids(gpo))


def lf_line_breaks(text: Any) -> Any:
    """A GPMC comment's line breaks as LF (CRLF and bare CR alike).

    GPMC stores a GPO's comment, and a WMI filter's description, with Windows
    line breaks. Studio refuses a carriage return in any text written into XML
    -- an XML parser reads it back as LF, so the value would not survive a
    native export -- and these comments are written into XML. Their line breaks
    are line breaks, not data, so they are normalised here rather than
    refusing the GPO. `estate.parse_estate` applies it before its own
    validation, and `WorkspaceStore.import_baseline_gpos` for direct callers. Non-text values are
    returned unchanged for `gpo_from_dict` and validation to judge.
    """
    if not isinstance(text, str):
        return text
    return text.replace("\r\n", "\n").replace("\r", "\n")


class WorkspaceStore:
    """Persist editable GPO snapshots and their audit history."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._lock_fd: int | None = None
        self._degraded = False
        lock_fd = try_acquire_workspace_lock(self.path)
        if lock_fd is None:
            raise WorkspaceError(
                "Cannot open workspace: it is in use by another process. "
                "Stop the other process and retry."
            )
        self._lock_fd = lock_fd
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        startup_qc = quick_check(self._connection)
        if not startup_qc.ok:
            self._degraded = True
            return
        self._apply_pragmas()
        self._migrate_schema()

    @property
    def is_degraded(self) -> bool:
        return self._degraded

    def _require_healthy(self) -> None:
        """Raise WorkspaceError if the store is closed or degraded."""
        if self._connection is None:
            raise WorkspaceError("Workspace is closed.")
        if self._degraded:
            raise WorkspaceError(
                "Workspace is degraded due to corruption — "
                "restart with a valid database or restore from backup."
            )

    def close(self) -> None:
        with contextlib.suppress(sqlite3.Error):
            self._connection.close()
        self._connection = None  # type: ignore[assignment]
        lock_fd = self._lock_fd
        self._lock_fd = None
        if lock_fd is not None:
            release_workspace_lock(lock_fd)

    def __del__(self) -> None:
        lock_fd = getattr(self, "_lock_fd", None)
        conn = getattr(self, "_connection", None)
        if lock_fd is not None:
            self._lock_fd = None
            with contextlib.suppress(Exception):
                release_workspace_lock(lock_fd)
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()

    def _apply_pragmas(self) -> None:
        try:
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 5000")
            self._connection.execute("PRAGMA synchronous = NORMAL")
        except sqlite3.Error as error:
            self._map_sqlite_error(error)

    def _migrate_schema(self) -> None:
        from .schema import SchemaError, migrate

        try:
            migrate(self._connection)
        except SchemaError as e:
            raise WorkspaceError(str(e)) from e
        except sqlite3.Error as e:
            self._map_sqlite_error(e)

    def quick_check(self) -> IntegrityResult:
        """Run PRAGMA quick_check — fast, suitable for startup."""
        from .workspace_ops import quick_check as _quick_check
        from .workspace_ops import record_integrity_check

        with self._lock:
            result = _quick_check(self._connection)
            with contextlib.suppress(sqlite3.Error):
                record_integrity_check(self._connection, result.ok, "quick")
            if not result.ok:
                self._degraded = True
            return result

    def full_integrity_check(self) -> IntegrityResult:
        """Run PRAGMA integrity_check — thorough, for operator-invoked checks."""
        from .workspace_ops import full_integrity_check as _full_check
        from .workspace_ops import record_integrity_check

        with self._lock:
            result = _full_check(self._connection)
            with contextlib.suppress(sqlite3.Error):
                record_integrity_check(self._connection, result.ok, "full")
            if not result.ok:
                self._degraded = True
            return result

    def _map_sqlite_error(self, error: sqlite3.Error) -> NoReturn:
        """Map SQLite errors to domain exceptions. Never returns; always raises."""
        if isinstance(error, sqlite3.IntegrityError):
            raise ConflictError("A GPO with that name or GUID already exists") from error
        if isinstance(error, sqlite3.OperationalError):
            msg = str(error).lower()
            if "locked" in msg or "busy" in msg:
                raise WorkspaceError("Workspace is busy. Try again.") from error
            if "read-only" in msg or "readonly" in msg:
                raise WorkspaceError("Workspace is read-only.") from error
            if "disk full" in msg or "database or disk is full" in msg or "database_full" in msg:
                raise WorkspaceError("Workspace disk is full.") from error
            if "corrupt" in msg or "malformed" in msg:
                self._degraded = True
                raise WorkspaceError("Workspace database is corrupt.") from error
        if isinstance(error, sqlite3.DatabaseError):
            self._degraded = True
            raise WorkspaceError("Workspace database error.") from error
        raise WorkspaceError(f"Workspace error: {error}") from error

    def workspace_meta(self) -> dict[str, str]:
        with self._lock:
            self._require_healthy()
            try:
                rows = self._connection.execute(
                    "SELECT key, value FROM workspace_meta"
                ).fetchall()
                return {row["key"]: row["value"] for row in rows}
            except sqlite3.Error as error:
                self._map_sqlite_error(error)

    DiscoveryKind = Literal[
        "forest", "domain", "site", "subnet", "ou", "principal", "gpos", "sites"
    ]

    def cache_discovery(
        self,
        kind: DiscoveryKind,
        key: str,
        data_json: str,
        source_dc: str = "",
        collected_at: str = "",
    ) -> None:
        """Cache a read-only AD topology snapshot, replacing any prior entry for kind+key."""
        with self._lock:
            self._require_healthy()
            try:
                self._connection.execute(
                    """
                    INSERT INTO estate_discovery(kind, key, data_json, source_dc, collected_at)
                    VALUES(?,?,?,?,?)
                    ON CONFLICT(kind, key) DO UPDATE SET
                        data_json=excluded.data_json,
                        source_dc=excluded.source_dc,
                        collected_at=excluded.collected_at
                    """,
                    (kind, key, data_json, source_dc, collected_at),
                )
            except sqlite3.Error as error:
                self._map_sqlite_error(error)

    def get_discovery(self, kind: str, key: str) -> dict[str, Any]:
        """Return a cached discovery snapshot by kind and key."""
        with self._lock:
            self._require_healthy()
            try:
                row = self._connection.execute(
                    "SELECT data_json FROM estate_discovery WHERE kind=? AND key=?",
                    (kind, key),
                ).fetchone()
                if row is None:
                    raise NotFoundError(
                        f"{kind} discovery entry {key} was not found"
                    )
                return cast(dict[str, Any], json.loads(row["data_json"]))
            except sqlite3.Error as error:
                self._map_sqlite_error(error)

    def list_discovery(self, kind: str) -> list[dict[str, Any]]:
        """Return all cached discovery snapshots of a given kind."""
        with self._lock:
            self._require_healthy()
            try:
                rows = self._connection.execute(
                    "SELECT data_json FROM estate_discovery WHERE kind=? ORDER BY key",
                    (kind,),
                ).fetchall()
                return [json.loads(row["data_json"]) for row in rows]
            except sqlite3.Error as error:
                self._map_sqlite_error(error)

    def search_principals(self, query: str, domain: str = "") -> list[dict[str, Any]]:
        """Search cached principals by name, SID, or distinguished name."""
        with self._lock:
            self._require_healthy()
            try:
                if domain:
                    rows = self._connection.execute(
                        """
                        SELECT data_json FROM estate_discovery
                        WHERE kind='principal' AND json_extract(data_json, '$.domain') = ?
                        ORDER BY key
                        """,
                        (domain,),
                    ).fetchall()
                else:
                    rows = self._connection.execute(
                        """
                        SELECT data_json FROM estate_discovery
                        WHERE kind='principal'
                        ORDER BY key
                        """
                    ).fetchall()
            except sqlite3.OperationalError as error:
                if "no such function: json_extract" in str(error).lower():
                    rows = self._connection.execute(
                        "SELECT data_json FROM estate_discovery WHERE kind='principal' ORDER BY key"
                    ).fetchall()
                else:
                    self._map_sqlite_error(error)
            except sqlite3.Error as error:
                self._map_sqlite_error(error)
            query_lower = query.casefold()
            domain_lower = domain.casefold() if domain else ""
            results: list[dict[str, Any]] = []
            for row in rows:
                data = json.loads(row["data_json"])
                if (
                    query_lower in str(data.get("sam_account_name", "")).casefold()
                    or query_lower in str(data.get("display_name", "")).casefold()
                    or query_lower in str(data.get("object_sid", "")).casefold()
                    or query_lower in str(data.get("distinguished_name", "")).casefold()
                ) and (
                    not domain_lower
                    or str(data.get("domain", "")).casefold() == domain_lower
                ):
                    results.append(data)
            return results

    def list_discovery_kinds(self) -> list[str]:
        """Return the distinct discovery kinds currently cached."""
        with self._lock:
            self._require_healthy()
            try:
                rows = self._connection.execute(
                    "SELECT DISTINCT kind FROM estate_discovery ORDER BY kind"
                ).fetchall()
                return [row["kind"] for row in rows]
            except sqlite3.Error as error:
                self._map_sqlite_error(error)

    def discovery_summary(self) -> dict[str, int]:
        """Return counts of cached discovery entries by kind."""
        with self._lock:
            self._require_healthy()
            try:
                rows = self._connection.execute(
                    "SELECT kind, COUNT(*) AS n FROM estate_discovery GROUP BY kind"
                ).fetchall()
                return {str(row["kind"]): int(row["n"]) for row in rows}
            except sqlite3.Error as error:
                self._map_sqlite_error(error)

    def _encode_snapshot_payload(self, gpo: GPO) -> tuple[str, list[RetainedDocument]]:
        """Serialize a snapshot with its retained documents moved to references.

        WI-061 moved the retained native XML; the parsed ``fdeploy`` document
        follows it, reduced to its native bytes, so N revisions of one import
        hold one copy of each. The returned documents must be filed with
        :meth:`_store_documents` inside the same transaction that writes the
        snapshot rows, or the payload's digests would reference nothing.
        """
        # No revision is written holding text XML cannot carry (batch-2
        # review): every create, import and mutation passes through here, so a
        # lone surrogate or a control character is refused BEFORE anything is
        # committed, whichever endpoint or field it arrived through.
        identity_issues = setting_id_issues(gpo.settings)
        if identity_issues:
            raise ValidationError(identity_issues)
        unwritable = text_issues(gpo)
        if unwritable:
            raise ValidationError(unwritable)
        data = gpo.to_stored_dict()
        for collection_data in data.get("gpp_collections", []):
            # Derived on every load, never stored (final-r14 `load_problems`).
            collection_data.pop("load_problems", None)
        try:
            documents = extract_snapshot_documents(data)
        except SnapshotDocumentError as error:
            raise WorkspaceError(str(error)) from error
        return json.dumps(data, separators=(",", ":"), sort_keys=True), documents

    def _store_documents(
        self, guid: str, revision: int, documents: list[RetainedDocument]
    ) -> None:
        """File a snapshot's retained documents; call inside the row's transaction.

        Revision 0 names the head snapshot in ``gpos``; revisions number from 1
        and name their ``revisions`` row. The reference rows cascade away with
        the GPO, which is what makes unreferenced-document cleanup an indexed
        query rather than a parse of every snapshot.
        """
        for document in documents:
            self._connection.execute(
                "INSERT OR IGNORE INTO retained_documents VALUES(?,?,?)",
                (document.digest, document.content_base64, document.byte_length),
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO snapshot_documents VALUES(?,?,?)",
                (guid, revision, document.digest),
            )

    def _load_snapshot(self, snapshot_json: str) -> dict[str, Any]:
        """Parse a stored snapshot and rehydrate its retained documents."""
        data: dict[str, Any] = json.loads(snapshot_json)
        if not snapshot_digests(data):
            return data

        def lookup(digest: str) -> str | None:
            row = self._connection.execute(
                "SELECT content_base64 FROM retained_documents WHERE digest=?",
                (digest,),
            ).fetchone()
            return None if row is None else str(row["content_base64"])

        try:
            apply_snapshot_documents(data, lookup)
        except SnapshotDocumentError as error:
            self._degraded = True
            raise WorkspaceError(str(error)) from error
        return data

    def list_gpos(self) -> list[GPO]:
        with self._lock:
            self._require_healthy()
            try:
                rows = self._connection.execute(
                    "SELECT snapshot_json FROM gpos ORDER BY name COLLATE NOCASE"
                ).fetchall()
                return [gpo_from_dict(self._load_snapshot(row["snapshot_json"])) for row in rows]
            except sqlite3.Error as error:
                self._map_sqlite_error(error)

    def get_gpo(self, guid: str) -> GPO:
        with self._lock:
            self._require_healthy()
            try:
                row = self._connection.execute(
                    "SELECT snapshot_json FROM gpos WHERE guid = ?", (guid.lower(),)
                ).fetchone()
                if row is None:
                    raise NotFoundError(f"GPO {guid} was not found")
                return gpo_from_dict(self._load_snapshot(row["snapshot_json"]))
            except sqlite3.Error as error:
                self._map_sqlite_error(error)

    def create_gpo(
        self,
        name: str,
        description: str = "",
        *,
        identity: Identity | str,
        reason: str,
        guid: str | None = None,
        settings: tuple[RegistrySetting, ...] = (),
        links: tuple[GPOLink, ...] = (),
        source_guid: str = "",
        cse_metadata: tuple[CseMetadataEntry, ...] = (),
        backup_inventory: BackupInventory | None = None,
        fdeploy: FdeployDocument | None = None,
        domain: str = "studio.local",
        computer_enabled: bool = True,
        user_enabled: bool = True,
        status: Literal["draft", "ready", "archived"] = "draft",
        is_starter: bool = False,
        template_version: str = "",
        security_filters: tuple[SecurityFilter, ...] = (),
        wmi_filter: WmiFilter | None = None,
        gpp_collections: tuple[GppCollection, ...] = (),
    ) -> GPO:
        actor = _resolve_actor(identity)
        timestamp = _now()
        # A creation keeps the provenance records its collections carry -- an
        # import's minted ones, a fork's carried ones -- for the fragments they
        # still hold; it mints none itself (final-r13).
        gpp_collections = tuple(
            provenance.with_records(collection, provenance.records(collection))
            for collection in gpp_collections
        )
        gpo = GPO(
            guid=(guid or str(uuid.uuid4())).lower().strip("{}"),
            name=name.strip(),
            description=description.strip(),
            computer_enabled=computer_enabled,
            user_enabled=user_enabled,
            status=status,
            revision=1,
            settings=settings,
            links=links,
            source_guid=source_guid,
            cse_metadata=cse_metadata,
            backup_inventory=backup_inventory,
            fdeploy=fdeploy,
            domain=domain,
            is_starter=is_starter,
            template_version=template_version,
            security_filters=security_filters,
            wmi_filter=wmi_filter,
            gpp_collections=gpp_collections,
            created_at=timestamp,
            updated_at=timestamp,
        )
        answers = _before_commit(gpo)
        payload, documents = self._encode_snapshot_payload(gpo)
        with self._lock:
            self._require_healthy()
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._connection.execute(
                    """INSERT INTO gpos(guid, name, revision, snapshot_json, updated_at)
                       VALUES(?,?,?,?,?)""",
                    (gpo.guid, gpo.name, gpo.revision, payload, timestamp),
                )
                self._store_documents(gpo.guid, 0, documents)
                self._store_documents(gpo.guid, 1, documents)
                self._connection.execute(
                    "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
                    (gpo.guid, 1, actor, reason, timestamp, payload),
                )
                self._connection.execute("COMMIT")
            except BaseException as error:
                # Audit-string encoding and cancellation can fail after SQL
                # wrote rows too; they must not leave a live transaction.
                with contextlib.suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                if isinstance(error, sqlite3.Error):
                    self._map_sqlite_error(error)
                raise
        _after_commit(gpo, answers)
        return gpo

    def list_starter_gpos(self) -> list[GPO]:
        with self._lock:
            self._require_healthy()
            try:
                rows = self._connection.execute(
                    """SELECT snapshot_json FROM gpos
                       WHERE json_extract(snapshot_json, '$.is_starter') = 1
                       ORDER BY name COLLATE NOCASE"""
                ).fetchall()
                return [
                    gpo_from_dict(self._load_snapshot(row["snapshot_json"]))
                    for row in rows
                ]
            except sqlite3.OperationalError as error:
                if "no such function: json_extract" in str(error).lower():
                    return [gpo for gpo in self.list_gpos() if gpo.is_starter]
                self._map_sqlite_error(error)
            except sqlite3.Error as error:
                self._map_sqlite_error(error)

    def create_starter_gpo(
        self,
        name: str,
        description: str = "",
        *,
        identity: Identity | str,
        reason: str,
        template_version: str = "",
        settings: tuple[RegistrySetting, ...] = (),
        domain: str = "studio.local",
        computer_enabled: bool = True,
        user_enabled: bool = True,
    ) -> GPO:
        return self.create_gpo(
            name=name,
            description=description,
            identity=identity,
            reason=reason,
            template_version=template_version,
            settings=settings,
            domain=domain,
            computer_enabled=computer_enabled,
            user_enabled=user_enabled,
            status="draft",
            is_starter=True,
        )

    def derive_gpo_from_starter(
        self,
        starter_guid: str,
        name: str,
        *,
        identity: Identity | str,
        reason: str,
        expected_revision: int | None = None,
    ) -> GPO:
        starter = self.get_gpo(starter_guid)
        if not starter.is_starter:
            raise NotFoundError(f"Starter GPO {starter_guid} was not found")
        require_unreplaced(starter)
        if expected_revision is not None and starter.revision != expected_revision:
            raise ConflictError(
                f"Expected revision {expected_revision}, "
                f"but the current revision is {starter.revision}",
                expected_revision=expected_revision,
                current_revision=starter.revision,
            )
        return self.create_gpo(
            name=name,
            description=starter.description,
            identity=identity,
            reason=reason,
            settings=starter.settings,
            domain=starter.domain,
            computer_enabled=starter.computer_enabled,
            user_enabled=starter.user_enabled,
            source_guid=starter.guid,
            status="draft",
        )

    def delete_gpo(
        self,
        guid: str,
        expected_revision: int,
        *,
        identity: Identity | str,
        reason: str,
        confirm: str | None,
    ) -> None:
        """Delete a GPO whose read replaced a stored value -- one of its
        recoveries (Sol final-r20 B1). Deleting persists nothing, so no default
        is stored in a value's place. A readable GPO is not deleted here:
        Studio has no general GPO deletion.

        Not lossy (final-r20 ruling): *confirm* must repeat the GPO's guid
        exactly, and every stored row of the GPO -- its head, every revision
        (each snapshot, the provenance records inside it) and the retained
        documents they reference -- is copied, byte for byte, into the
        ``deleted_*`` archive tables in the same transaction as the deletion,
        so an operator can audit it and put it back
        (docs/workspace-recovery.md)."""
        if confirm != guid:
            raise ValidationError([
                ValidationIssue(
                    severity="error",
                    code="delete_confirmation_required",
                    message=(
                        "Deleting a GPO needs its guid repeated as confirm=<guid>; the GPO "
                        "is archived, not lost (docs/workspace-recovery.md)."
                    ),
                    path="confirm",
                )
            ])
        self._delete(guid, expected_revision, identity=identity, reason=reason, starter=False)

    def delete_starter_gpo(
        self,
        guid: str,
        expected_revision: int,
        *,
        identity: Identity | str,
        reason: str,
    ) -> None:
        self._delete(guid, expected_revision, identity=identity, reason=reason, starter=True)

    def _delete(
        self,
        guid: str,
        expected_revision: int,
        *,
        identity: Identity | str,
        reason: str,
        starter: bool,
    ) -> None:
        actor = _resolve_actor(identity)
        with self._lock:
            self._require_healthy()
            current = self.get_gpo(guid)
            if starter and not current.is_starter:
                raise NotFoundError(f"Starter GPO {guid} was not found")
            if not starter and not _has_replaced(current):
                raise ValidationError([
                    ValidationIssue(
                        severity="error",
                        code="gpo_delete_not_a_recovery",
                        message=(
                            "Studio deletes a GPO only as the recovery for stored values it "
                            "cannot read; this GPO has none."
                        ),
                        path="",
                    )
                ])
            if current.revision != expected_revision:
                raise ConflictError(
                    f"Expected revision {expected_revision} but current is {current.revision}",
                    expected_revision=expected_revision,
                    current_revision=current.revision,
                )
            deleted_at = _now()
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                if not starter:
                    self._archive_rows(current.guid, deleted_at)
                self._connection.execute(
                    """INSERT INTO deletion_log(guid, name, actor, reason, revision, deleted_at)
                       VALUES(?,?,?,?,?,?)""",
                    (current.guid, current.name, actor, reason, current.revision, deleted_at),
                )
                self._connection.execute(
                    "DELETE FROM revisions WHERE gpo_guid = ?",
                    (current.guid,),
                )
                self._connection.execute(
                    "DELETE FROM gpos WHERE guid = ?",
                    (current.guid,),
                )
                # WI-061. The GPO's snapshot_documents rows cascade with its
                # gpos row; any retained document no snapshot references any
                # more is an orphan this deletion created, so it goes now
                # rather than accumulating behind the deletion log.
                self._connection.execute(
                    "DELETE FROM retained_documents WHERE digest NOT IN "
                    "(SELECT digest FROM snapshot_documents)"
                )
                self._connection.execute("COMMIT")
            except BaseException as exc:
                with contextlib.suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                if isinstance(exc, sqlite3.Error):
                    self._map_sqlite_error(exc)
                raise

    def _archive_rows(self, guid: str, deleted_at: str) -> None:
        """Copy every stored row of *guid* into the ``deleted_*`` archive
        tables; call inside the deletion's transaction.

        Each deletion is one event with its own ``deletion_id`` (a fresh UUID:
        two deletions of one GPO, even within a second, never collide --
        DeepSeek final-r21 N2), and every archive row -- the retained documents
        included, one row per document per deletion -- carries it and the
        deletion's ``deleted_at``. The tables are additive: created on first
        use and read by nothing in Studio, so the schema version is unchanged
        and an older build opens the workspace as before. Each copies its
        source table's columns as they are (``INSERT ... SELECT``)."""
        connection = self._connection
        for table, columns in _ARCHIVE_TABLES.items():
            self._archive_table(table, columns)
        event = (uuid.uuid4().hex, deleted_at)
        connection.execute(
            """INSERT INTO deleted_gpos
               SELECT ?, ?, guid, name, revision, snapshot_json, updated_at
               FROM gpos WHERE guid = ?""",
            (*event, guid),
        )
        connection.execute(
            """INSERT INTO deleted_revisions
               SELECT ?, ?, gpo_guid, revision, actor, reason, created_at, snapshot_json
               FROM revisions WHERE gpo_guid = ?""",
            (*event, guid),
        )
        connection.execute(
            """INSERT INTO deleted_snapshot_documents
               SELECT ?, ?, gpo_guid, revision, digest FROM snapshot_documents
               WHERE gpo_guid = ?""",
            (*event, guid),
        )
        connection.execute(
            """INSERT INTO deleted_retained_documents
               SELECT ?, ?, digest, content_base64, byte_length FROM retained_documents
               WHERE digest IN (SELECT digest FROM snapshot_documents WHERE gpo_guid = ?)""",
            (*event, guid),
        )

    def _archive_table(
        self, table: str, columns: tuple[tuple[tuple[str, str], ...], tuple[str, ...]]
    ) -> None:
        """Create archive *table* in its current shape. One that exists in
        another shape -- written by an earlier build -- is renamed aside, to
        ``<table>_preshape_<n>``, kept and never dropped, and a current one
        created (final-r22: ``CREATE TABLE IF NOT EXISTS`` left an old shape
        in place, and every later deletion failed on it)."""
        connection = self._connection
        names, key = columns
        expected = [name for name, _type in names]
        present = [
            str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        ]
        if present and present != expected:
            number = 1
            # Any schema object's name -- table, view, index, trigger -- is
            # taken (DeepSeek final-r23 N2), compared as SQLite compares them,
            # without regard to case.
            while connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = ? COLLATE NOCASE",
                (f"{table}_preshape_{number}",),
            ).fetchone():
                number += 1
            connection.execute(f"ALTER TABLE {table} RENAME TO {table}_preshape_{number}")
        definition = ", ".join(f"{name} {kind} NOT NULL" for name, kind in names)
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {table} "
            f"({definition}, PRIMARY KEY ({', '.join(key)}))"
        )

    def import_baseline_gpos(
        self,
        gpos: list[GPO],
        *,
        identity: Identity | str,
        reason: str,
    ) -> dict[str, int]:
        actor = _resolve_actor(identity)
        imported = 0
        skipped = 0
        conflicts = 0
        rejected = 0
        for gpo in gpos:
            normalized_guid = gpo.guid.lower().strip("{}")
            timestamp = _now()
            normalized = replace(
                gpo,
                guid=normalized_guid,
                name=gpo.name.strip(),
                # A GPMC comment arrives with Windows line endings; XML (where
                # the description is written) reads CR back as LF, so line
                # breaks are normalised to LF rather than refusing the GPO. The
                # same rule `estate.parse_estate` applies first on
                # the API path; this covers direct callers.
                description=lf_line_breaks(gpo.description).strip(),
                revision=1,
                created_at=timestamp,
                updated_at=timestamp,
                gpp_collections=tuple(
                    ensure_editor_ids(c) for c in gpo.gpp_collections
                ),
            )
            issues = validate_gpo(normalized) + setting_id_issues(normalized.settings)
            if any(issue.severity == "error" for issue in issues):
                rejected += 1
                continue
            if normalized.status == "ready":
                ready_issues = validate_ready_transition(normalized)
                if ready_issues:
                    rejected += 1
                    continue
            payload, documents = self._encode_snapshot_payload(normalized)
            try:
                with self._lock:
                    self._require_healthy()
                    try:
                        try:
                            self.get_gpo(normalized_guid)
                            skipped += 1
                            continue
                        except NotFoundError:
                            pass
                        self._connection.execute("BEGIN IMMEDIATE")
                        self._connection.execute(
                            """INSERT INTO gpos(guid, name, revision, snapshot_json, updated_at)
                               VALUES(?,?,?,?,?)""",
                            (
                                normalized.guid,
                                normalized.name,
                                normalized.revision,
                                payload,
                                timestamp,
                            ),
                        )
                        self._store_documents(normalized.guid, 0, documents)
                        self._store_documents(normalized.guid, 1, documents)
                        self._connection.execute(
                            "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
                            (normalized.guid, 1, actor, reason, timestamp, payload),
                        )
                        self._connection.execute("COMMIT")
                    except BaseException as error:
                        with contextlib.suppress(sqlite3.Error):
                            self._connection.execute("ROLLBACK")
                        if isinstance(error, sqlite3.Error):
                            if isinstance(error, sqlite3.IntegrityError):
                                raise
                            self._map_sqlite_error(error)
                        raise
            except sqlite3.IntegrityError:
                conflicts += 1
                continue
            imported += 1
        return {
            "imported": imported,
            "skipped": skipped,
            "conflicts": conflicts,
            "rejected": rejected,
            "total": imported + skipped + conflicts + rejected,
        }

    def _registry_repair_source(self, guid: str, expected_revision: int | None) -> GPO:
        """Call with the workspace lock held; never recover defaulted values."""
        source = self.get_gpo(guid)
        if expected_revision is not None and source.revision != expected_revision:
            raise ConflictError(
                f"Expected revision {expected_revision} but current is {source.revision}",
                expected_revision=expected_revision,
                current_revision=source.revision,
            )
        require_unreplaced(source)
        return source

    def inspect_registry_repair(self, guid: str) -> RegistryRepairSource:
        with self._lock:
            return repair_source(self._registry_repair_source(guid, None))

    def preview_registry_repair(
        self, guid: str, expected_revision: int, choices: tuple[RepairChoice, ...], *,
        name: str, identity: Identity | str, reason: str,
    ) -> RegistryRepairPreview:
        with self._lock:
            source = self._registry_repair_source(guid, expected_revision)
            return repair_preview(
                source, choices, name=name, actor=_resolve_actor(identity), reason=reason
            )

    def apply_registry_repair(
        self, guid: str, expected_revision: int, choices: tuple[RepairChoice, ...], *,
        name: str, identity: Identity | str, reason: str, review_sha256: str,
    ) -> GPO:
        with self._lock:
            source = self._registry_repair_source(guid, expected_revision)
            preview = repair_preview(
                source, choices, name=name, actor=_resolve_actor(identity), reason=reason
            )
            if preview.review_sha256 != review_sha256:
                raise ConflictError("Registry repair choices changed. Review a fresh preview.")
            # The exclusive workspace lock binds the source check and creation.
            # create_gpo owns its transaction; do not nest BEGIN statements.
            return self.create_gpo(
                preview.name, preview.description, identity=preview.actor, reason=preview.reason,
                settings=repaired_settings(source, choices), domain=source.domain,
                computer_enabled=source.computer_enabled, user_enabled=source.user_enabled,
            )

    def fork_gpo(
        self,
        guid: str,
        new_name: str,
        *,
        identity: Identity | str,
        reason: str,
    ) -> GPO:
        source = self.get_gpo(guid)
        require_unreplaced(source)
        forked_settings = tuple(
            replace(s, id=f"forked-{s.id}") for s in source.settings
        )
        forked_links = tuple(
            replace(link, id=f"forked-{link.id}") for link in source.links
        )
        forked_security_filters = tuple(
            replace(sf, id=f"forked-{sf.id}") for sf in source.security_filters
        )
        forked_wmi = (
            replace(source.wmi_filter, id=f"forked-{source.wmi_filter.id}")
            if source.wmi_filter
            else None
        )
        return self.create_gpo(
            name=new_name,
            description=source.description,
            identity=identity,
            reason=reason,
            settings=forked_settings,
            links=forked_links,
            source_guid=source.guid,
            cse_metadata=source.cse_metadata,
            backup_inventory=source.backup_inventory,
            fdeploy=source.fdeploy,
            domain=source.domain,
            computer_enabled=source.computer_enabled,
            user_enabled=source.user_enabled,
            security_filters=forked_security_filters,
            wmi_filter=forked_wmi,
            # The same items, so their provenance records carry over as they
            # are (final-r13). Their run-once ids stay too: a fork is how an
            # imported GPO is edited, and a new id would make every client
            # apply its apply-once items again (WI-080). A clone is a new item
            # and gets fresh ones (`gpp_provenance.fresh_item`).
            gpp_collections=source.gpp_collections,
        )

    def _unchanged(self, guid: str, expected_revision: int) -> GPO:
        """A mutation with nothing to change: the GPO as it is -- after the same
        checks `_mutate` makes, so an empty change is no way around them (Sol
        final-r19 B1): the expected revision, and no stored value the read
        replaced."""
        current = self.get_gpo(guid)
        if current.revision != expected_revision:
            raise ConflictError(
                f"Expected revision {expected_revision}, "
                f"but the current revision is {current.revision}",
                expected_revision=expected_revision,
                current_revision=current.revision,
            )
        require_unreplaced(current)
        return current

    def _mutate(
        self,
        guid: str,
        expected_revision: int,
        mutation: Callable[[GPO], GPO],
        *,
        identity: Identity | str,
        reason: str,
        grants: _Grants | None = None,
        replaces_all: bool = False,
    ) -> GPO:
        actor = _resolve_actor(identity)
        with self._lock:
            self._require_healthy()
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                current = self.get_gpo(guid)
                if current.revision != expected_revision:
                    raise ConflictError(
                        f"Expected revision {expected_revision}, "
                        f"but the current revision is {current.revision}",
                        expected_revision=expected_revision,
                        current_revision=current.revision,
                    )
                if not replaces_all:
                    require_unreplaced(current)
                timestamp = _now()
                # Every mutation passes the provenance check (final-r13): it may
                # keep or drop retained content, and add only what *grants* names.
                changed = _keep_provenance(current, mutation(current), grants)
                updated = replace(
                    changed, revision=current.revision + 1, updated_at=timestamp
                )
                # The answer is built before the commit (final-r18, r19 N3): a
                # failure building it raises here and rolls the mutation back,
                # so no durable revision is followed by a 500.
                answers = _before_commit(updated)
                payload, documents = self._encode_snapshot_payload(updated)
                self._store_documents(current.guid, 0, documents)
                self._store_documents(current.guid, updated.revision, documents)
                cursor = self._connection.execute(
                    """UPDATE gpos SET name=?, revision=?, snapshot_json=?, updated_at=?
                       WHERE guid=? AND revision=?""",
                    (
                        updated.name,
                        updated.revision,
                        payload,
                        timestamp,
                        current.guid,
                        expected_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ConflictError(
                        "The GPO changed while this request was being processed"
                    )
                self._connection.execute(
                    "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
                    (current.guid, updated.revision, actor, reason, timestamp, payload),
                )
                self._connection.execute("COMMIT")
            except BaseException as exc:
                with contextlib.suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                if isinstance(exc, sqlite3.Error):
                    self._map_sqlite_error(exc)
                raise
        _after_commit(updated, answers)
        return updated

    def update_metadata(
        self,
        guid: str,
        expected_revision: int,
        values: dict[str, Any],
        *,
        identity: Identity | str,
        reason: str,
    ) -> GPO:
        allowed = {"name", "description", "computer_enabled", "user_enabled", "status", "domain"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"unknown metadata fields: {', '.join(sorted(unknown))}")

        current = self.get_gpo(guid)
        target_status = values.get("status")
        if target_status == "ready" and current.status != "ready":
            target_gpo = replace(
                current, **{k: v for k, v in values.items() if k in allowed}
            )
            issues = validate_ready_transition(target_gpo)
            if issues:
                raise ValidationError(issues)

        def mutate(gpo: GPO) -> GPO:
            return replace(gpo, **values)

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)

    def _validate_settings(self, settings: Iterable[RegistrySetting]) -> None:
        settings = tuple(settings)
        errors = setting_id_issues(settings)
        for setting in settings:
            for issue in validate_setting(setting):
                if issue.severity == "error":
                    errors.append(issue)
        if errors:
            raise ValidationError(errors)

    def put_setting(
        self,
        guid: str,
        expected_revision: int,
        values: dict[str, Any],
        *,
        identity: Identity | str,
        reason: str,
        setting_id: str | None = None,
    ) -> GPO:
        new_setting = _setting(
            {"id": setting_id if setting_id is not None else str(uuid.uuid4()), **values}
        )
        self._validate_settings([new_setting])

        def mutate(gpo: GPO) -> GPO:
            _require_unambiguous_setting_id(gpo, new_setting.id)
            settings = [item for item in gpo.settings if item.id != new_setting.id]
            settings.append(new_setting)
            settings.sort(key=lambda item: item.identity())
            return replace(gpo, settings=tuple(settings))

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)

    def put_settings(
        self,
        guid: str,
        expected_revision: int,
        new_settings: list[RegistrySetting],
        *,
        identity: Identity | str,
        reason: str,
    ) -> GPO:
        if not new_settings:
            return self._unchanged(guid, expected_revision)
        self._validate_settings(new_settings)
        new_ids = {s.id for s in new_settings}

        def mutate(gpo: GPO) -> GPO:
            for setting_id in new_ids:
                _require_unambiguous_setting_id(gpo, setting_id)
            existing = [s for s in gpo.settings if s.id not in new_ids]
            combined = existing + new_settings
            combined.sort(key=lambda item: item.identity())
            return replace(gpo, settings=tuple(combined))

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)

    def replace_selected_settings(
        self,
        guid: str,
        expected_revision: int,
        positions: frozenset[int],
        new_settings: list[RegistrySetting],
        *,
        identity: Identity | str,
        reason: str,
    ) -> GPO:
        """Replace reviewed row positions, bound to the exact revision by _mutate's CAS.

        Selection contains positions rather than editor IDs: a legacy ID may
        name two different records, and selecting by it would erase both.
        """
        self._validate_settings(new_settings)

        def mutate(gpo: GPO) -> GPO:
            if any(index < 0 or index >= len(gpo.settings) for index in positions):
                raise ValueError("selected setting position is outside the reviewed revision")
            kept = [setting for index, setting in enumerate(gpo.settings) if index not in positions]
            combined = kept + new_settings
            combined.sort(key=lambda setting: setting.identity())
            return replace(gpo, settings=tuple(combined))

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)

    def replace_settings_with_prefix(
        self,
        guid: str,
        expected_revision: int,
        prefix: str,
        new_settings: list[RegistrySetting],
        *,
        identity: Identity | str,
        reason: str,
    ) -> GPO:
        """Swap every setting whose id starts with ``prefix`` for ``new_settings``.

        Unlike :meth:`put_settings`, an EMPTY ``new_settings`` is meaningful
        here: it removes the prefix's settings and leaves the rest of the GPO
        alone. That is what setting an Administrative Template policy to Not
        Configured requires — the policy must vanish from Registry.pol, not be
        written as a zero. ``put_settings`` cannot express this because it
        upserts by id and treats an empty list as a no-op.

        Always creates a revision, even when nothing matched, so that
        "I set this to Not Configured" is recorded rather than silently dropped.
        """
        if not prefix:
            raise ValueError("prefix must be non-empty")
        if any(not setting.id.startswith(prefix) for setting in new_settings):
            raise ValueError("every new setting id must start with prefix")
        self._validate_settings(new_settings)

        def mutate(gpo: GPO) -> GPO:
            kept = [s for s in gpo.settings if not s.id.startswith(prefix)]
            combined = kept + new_settings
            combined.sort(key=lambda item: item.identity())
            return replace(gpo, settings=tuple(combined))

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)

    def bulk_replace_settings_by_prefix(
        self,
        guid: str,
        expected_revision: int,
        replacements: list[tuple[str, list[RegistrySetting]]],
        *,
        identity: Identity | str,
        reason: str,
    ) -> GPO:
        if not replacements:
            return self._unchanged(guid, expected_revision)
        prefixes: list[str] = []
        all_new_settings: list[RegistrySetting] = []
        for prefix, settings in replacements:
            if not prefix:
                raise ValueError("prefix must be non-empty")
            for setting in settings:
                if not setting.id.startswith(prefix):
                    raise ValueError("every new setting id must start with prefix")
            prefixes.append(prefix)
            all_new_settings.extend(settings)
        self._validate_settings(all_new_settings)

        def mutate(gpo: GPO) -> GPO:
            kept = [s for s in gpo.settings if not any(s.id.startswith(p) for p in prefixes)]
            combined = kept + all_new_settings
            combined.sort(key=lambda item: item.identity())
            return replace(gpo, settings=tuple(combined))

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)

    def patch_setting_comment(
        self,
        guid: str,
        expected_revision: int,
        setting_id: str,
        comment: str,
        *,
        identity: Identity | str,
        reason: str,
    ) -> GPO:
        def mutate(gpo: GPO) -> GPO:
            _require_unambiguous_setting_id(gpo, setting_id)
            setting = next((s for s in gpo.settings if s.id == setting_id), None)
            if setting is None:
                raise NotFoundError(f"Setting {setting_id} was not found")
            new_setting = replace(setting, comment=comment)
            self._validate_settings([new_setting])
            settings = [s if s.id != setting_id else new_setting for s in gpo.settings]
            settings.sort(key=lambda item: item.identity())
            return replace(gpo, settings=tuple(settings))

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)

    def delete_setting(
        self,
        guid: str,
        setting_id: str,
        expected_revision: int,
        *,
        identity: Identity | str,
        reason: str,
    ) -> GPO:
        def mutate(gpo: GPO) -> GPO:
            _require_unambiguous_setting_id(gpo, setting_id)
            settings = tuple(item for item in gpo.settings if item.id != setting_id)
            if len(settings) == len(gpo.settings):
                raise NotFoundError(f"Setting {setting_id} was not found")
            return replace(gpo, settings=settings)

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)

    def put_link(
        self,
        guid: str,
        expected_revision: int,
        values: dict[str, Any],
        *,
        identity: Identity | str,
        reason: str,
        link_id: str | None = None,
    ) -> GPO:
        new_link = _link({"id": link_id or str(uuid.uuid4()), **values})

        def mutate(gpo: GPO) -> GPO:
            links = [item for item in gpo.links if item.id != new_link.id]
            links.append(new_link)
            links.sort(key=lambda item: (item.target.casefold(), item.order))
            updated = replace(gpo, links=tuple(links))
            _refuse_identity_issues(updated, f"links/{new_link.id}/")
            return updated

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)

    def delete_link(
        self,
        guid: str,
        link_id: str,
        expected_revision: int,
        *,
        identity: Identity | str,
        reason: str,
    ) -> GPO:
        def mutate(gpo: GPO) -> GPO:
            links = tuple(item for item in gpo.links if item.id != link_id)
            if len(links) == len(gpo.links):
                raise NotFoundError(f"Link {link_id} was not found")
            return replace(gpo, links=links)

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)

    def put_security_filter(
        self,
        guid: str,
        expected_revision: int,
        values: dict[str, Any],
        *,
        identity: Identity | str,
        reason: str,
        filter_id: str | None = None,
    ) -> GPO:
        new_filter = _security_filter({"id": filter_id or str(uuid.uuid4()), **values})

        def mutate(gpo: GPO) -> GPO:
            filters = [item for item in gpo.security_filters if item.id != new_filter.id]
            filters.append(new_filter)
            filters.sort(key=lambda item: item.principal.casefold())
            updated = replace(gpo, security_filters=tuple(filters))
            _refuse_identity_issues(updated, f"security_filters/{new_filter.id}/")
            return updated

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)

    def delete_security_filter(
        self,
        guid: str,
        filter_id: str,
        expected_revision: int,
        *,
        identity: Identity | str,
        reason: str,
    ) -> GPO:
        def mutate(gpo: GPO) -> GPO:
            filters = tuple(
                item for item in gpo.security_filters if item.id != filter_id
            )
            if len(filters) == len(gpo.security_filters):
                raise NotFoundError(f"Security filter {filter_id} was not found")
            return replace(gpo, security_filters=filters)

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)

    def set_wmi_filter(
        self,
        guid: str,
        expected_revision: int,
        values: dict[str, Any] | None,
        *,
        identity: Identity | str,
        reason: str,
    ) -> GPO:
        if values and "id" not in values:
            values = {"id": str(uuid.uuid4()), **values}
        new_wmi = _wmi_filter(values) if values else None

        def mutate(gpo: GPO) -> GPO:
            return replace(gpo, wmi_filter=new_wmi)

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)

    def _validate_gpp(
        self,
        collection: GppCollection,
        before: tuple[int, GppCollection] | None = None,
    ) -> None:
        """Refuse a preference mutation that leaves an error it did not find.

        Only NEW errors refuse it. A collection stored before a rule tightened
        (two group SIDs a 1.1.0rc1 workspace accepted, say) would otherwise refuse
        every edit and delete, including the one that repairs it (DeepSeek
        final-r7 N4). Errors are compared as a multiset keyed by code, path
        without item indices and message, so editing a bad item to a good one,
        or deleting one, always passes, and adding or keeping-and-adding a bad
        value does not.
        """
        errors = [
            issue
            for issue in validate_gpp_collection(collection)
            if issue.severity == "error"
        ]
        if before is not None:
            known = Counter(
                _issue_key(issue)
                for issue in validate_gpp_collection(before[1])
                if issue.severity == "error"
            )
            seen: Counter[tuple[str, str, str]] = Counter()
            fresh = []
            for issue in errors:
                key = _issue_key(issue)
                seen[key] += 1
                if seen[key] > known[key]:
                    fresh.append(issue)
            errors = fresh
        if errors:
            raise ValidationError(errors)
        if before is not None:
            # A collection that did not write before may still not write after a
            # repair of one item; what it must not do is stop writing because of
            # this mutation. validate_gpp_collection reports that case
            # (`unwritable_preferences`) as a new error above.
            return
        # Nothing is committed that Studio cannot write. Validation and the
        # writer are separate code; where they disagreed (a padded CIDR, a raw
        # predicate that does not parse) the mutation committed and every later
        # read or export of the GPO failed. Writing it here turns any such gap
        # into a 422 before the revision exists.
        try:
            serialize_gpp(collection)
        except (GppError, IltError, ValueError) as error:
            raise ValidationError([
                ValidationIssue(
                    severity="error",
                    code="unwritable_preferences",
                    message=f"Studio could not write these preferences: {error}",
                    path="gpp_collections",
                )
            ]) from error

    @staticmethod
    def _find_collection(
        gpo: GPO, scope: GppScope
    ) -> tuple[int, GppCollection] | None:
        for idx, c in enumerate(gpo.gpp_collections):
            if c.scope == scope:
                return idx, c
        return None

    @staticmethod
    def _replace_collection(
        gpo: GPO, idx: int, new_collection: GppCollection
    ) -> tuple[GppCollection, ...]:
        return tuple(
            new_collection if i == idx else c
            for i, c in enumerate(gpo.gpp_collections)
        )

    def reorder_gpp(
        self,
        guid: str,
        expected_revision: int,
        scope: GppScope,
        kind: Literal["groups", "registry"],
        ordered_ids: tuple[str, ...],
        *,
        identity: Identity | str,
        reason: str,
    ) -> GPO:
        """Atomically reorder one GPP collection without changing its items."""

        if kind not in ("groups", "registry"):
            raise ValidationError([
                ValidationIssue(
                    severity="error",
                    code="invalid_gpp_reorder_kind",
                    message="GPP reorder kind must be groups or registry.",
                    path="kind",
                )
            ])

        def mutate(gpo: GPO) -> GPO:
            found = self._find_collection(gpo, scope)
            if found is None:
                raise NotFoundError(f"GPP {scope} collection was not found")
            idx, existing = found
            current_ids = tuple(
                item.id
                for item in (
                    existing.groups if kind == "groups" else existing.registry
                )
            )
            if (
                len(ordered_ids) != len(current_ids)
                or len(set(ordered_ids)) != len(ordered_ids)
                or set(ordered_ids) != set(current_ids)
            ):
                raise ValidationError([
                    ValidationIssue(
                        severity="error",
                        code="invalid_gpp_reorder_ids",
                        message="ordered_ids must contain every current item exactly once.",
                        path="ordered_ids",
                    )
                ])
            if kind == "groups":
                groups_by_id = {item.id: item for item in existing.groups}
                reordered_groups = tuple(
                    groups_by_id[item_id] for item_id in ordered_ids
                )
                new_collection = replace(existing, groups=reordered_groups)
            else:
                registry_by_id = {item.id: item for item in existing.registry}
                reordered_registry = tuple(
                    registry_by_id[item_id] for item_id in ordered_ids
                )
                new_collection = replace(existing, registry=reordered_registry)
            self._validate_gpp(new_collection, before=self._find_collection(gpo, scope))
            return replace(
                gpo,
                gpp_collections=self._replace_collection(gpo, idx, new_collection),
            )

        return self._mutate(
            guid,
            expected_revision,
            mutate,
            identity=identity,
            reason=reason,
        )

    def clone_gpp_item(
        self,
        guid: str,
        expected_revision: int,
        scope: GppScope,
        kind: Literal["groups", "registry"],
        item_id: str,
        *,
        name: str | None = None,
        identity: Identity | str,
        reason: str,
    ) -> GPO:
        """Copy a stored GPP group or registry item, server-side and under the store
        lock, as a new item after the existing ones (Sol and DeepSeek final-r12).

        Retained content stays exactly as stored -- it is copied here, never
        supplied by the caller -- and so do its provenance records, re-keyed
        onto the copy's ids (final-r13). What makes the copy a new item is
        fresh: item, member and value ids, the run-once id, and what identifies
        the original to Windows (`gpp_provenance.fresh_item`). A group may take
        a new name.
        """
        grants = _Grants()

        def mutate(gpo: GPO) -> GPO:
            found = self._find_collection(gpo, scope)
            source = None
            if found is not None:
                source = next(
                    (item for item in getattr(found[1], kind) if item.id == item_id), None
                )
            if found is None or source is None:
                raise NotFoundError(f"GPP {kind} item {item_id} not found")
            idx, existing = found
            copy = provenance.fresh_item(source, clone=True)
            if isinstance(copy, GppGroup) and name is not None:
                copy = replace(copy, name=name)
            # A registry copy's uid is derived from its new id, as for any new item.
            copy = (
                ensure_editor_ids(GppCollection(scope=scope, registry=(copy,))).registry[0]
                if isinstance(copy, GppRegistry)
                else copy
            )
            grants.grant(
                scope,
                {provenance.digest(key) for key, _text in provenance.item_fragments(kind, copy)},
                provenance.carried_records(kind, source, copy, provenance.records(existing)),
            )
            if isinstance(copy, GppGroup):
                new_collection = replace(existing, groups=existing.groups + (copy,))
            else:
                new_collection = replace(existing, registry=existing.registry + (copy,))
            self._validate_gpp(new_collection, before=found)
            return replace(
                gpo, gpp_collections=self._replace_collection(gpo, idx, new_collection)
            )

        return self._mutate(
            guid, expected_revision, mutate, identity=identity, reason=reason, grants=grants
        )

    def restore_gpp_item(
        self,
        guid: str,
        expected_revision: int,
        scope: GppScope,
        kind: Literal["groups", "registry"],
        item_id: str,
        from_revision: int,
        *,
        identity: Identity | str,
        reason: str,
    ) -> GPO:
        """Put back one GPP item exactly as a named revision stored it.

        The item replaces the current item with the same id (or is appended if
        it has since been deleted). Its retained content comes from that
        revision, and so do its provenance records: the restore copies the
        records that revision holds for this item and creates none (final-r13).
        """
        historical = gpo_from_dict(self.get_revision(guid, from_revision).snapshot)
        require_unreplaced(historical)
        old_found = self._find_collection(historical, scope)
        restored = None
        if old_found is not None:
            restored = next(
                (item for item in getattr(old_found[1], kind) if item.id == item_id), None
            )
        if old_found is None or restored is None:
            raise NotFoundError(
                f"GPP {kind} item {item_id} not found in revision {from_revision}"
            )
        grants = _Grants()
        grants.grant(
            scope,
            {provenance.digest(key) for key, _text in provenance.item_fragments(kind, restored)},
            provenance.item_records(old_found[1], kind, restored),
        )

        def mutate(gpo: GPO) -> GPO:
            found = self._find_collection(gpo, scope)
            if found is None:
                new_collection = (
                    GppCollection(scope=scope, groups=(restored,))
                    if kind == "groups"
                    else GppCollection(scope=scope, registry=(restored,))
                )
                collections = gpo.gpp_collections + (new_collection,)
            else:
                idx, existing = found
                items = list(getattr(existing, kind))
                for index, item in enumerate(items):
                    if item.id == item_id:
                        items[index] = restored
                        break
                else:
                    items.append(restored)
                new_collection = (
                    replace(existing, groups=tuple(items))
                    if kind == "groups"
                    else replace(existing, registry=tuple(items))
                )
                collections = self._replace_collection(gpo, idx, new_collection)
            self._validate_gpp(new_collection, before=found)
            return replace(gpo, gpp_collections=collections)

        return self._mutate(
            guid, expected_revision, mutate, identity=identity, reason=reason, grants=grants
        )

    def put_gpp_group(
        self,
        guid: str,
        expected_revision: int,
        scope: GppScope,
        group: GppGroup,
        *,
        identity: Identity | str,
        reason: str,
        must_exist: bool = False,
        common_edits: Mapping[str, bool] | None = None,
        merge: Callable[[GppGroup], GppGroup] | None = None,
    ) -> GPO:
        """Add *group*, or replace the stored group with its id.

        With *merge* (the API's edits, final-r13) the replacement is
        ``merge(stored)``, computed under the store lock from the stored group:
        an edit carries typed fields and ids, never retained content, and the
        stored item supplies the rest.
        """
        processed = ensure_editor_ids(GppCollection(scope=scope, groups=(group,)))
        group = processed.groups[0]

        def mutate(gpo: GPO) -> GPO:
            found = self._find_collection(gpo, scope)
            if found is None:
                if must_exist:
                    raise NotFoundError(f"GPP group with id {group.id} not found")
                added = _with_common_edits(group, None, common_edits)
                new_collection = GppCollection(scope=scope, groups=(added,))
                new_collections = gpo.gpp_collections + (new_collection,)
            else:
                idx, existing = found
                groups_list = list(existing.groups)
                try:
                    gi = next(i for i, x in enumerate(groups_list) if x.id == group.id)
                    edited = group
                    if merge is not None:
                        edited = ensure_editor_ids(
                            GppCollection(scope=scope, groups=(merge(groups_list[gi]),))
                        ).groups[0]
                    groups_list[gi] = _keep_document_position(
                        _with_common_edits(edited, groups_list[gi], common_edits),
                        groups_list[gi],
                    )
                except StopIteration:
                    if must_exist:
                        raise NotFoundError(
                            f"GPP group with id {group.id} not found"
                        ) from None
                    groups_list.append(_with_common_edits(group, None, common_edits))
                new_collection = replace(existing, groups=tuple(groups_list))
                new_collections = self._replace_collection(gpo, idx, new_collection)
            self._validate_gpp(new_collection, before=self._find_collection(gpo, scope))
            return replace(gpo, gpp_collections=new_collections)

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)

    def delete_gpp_group(
        self,
        guid: str,
        expected_revision: int,
        scope: GppScope,
        group_id: str,
        *,
        identity: Identity | str,
        reason: str,
    ) -> GPO:
        if not group_id:
            raise ValidationError([
                ValidationIssue(
                    severity="error",
                    code="empty_gpp_group_id",
                    message="GPP group id is required.",
                    path="group_id",
                )
            ])

        def mutate(gpo: GPO) -> GPO:
            found = self._find_collection(gpo, scope)
            if found is None:
                raise NotFoundError(
                    f"GPP collection for scope '{scope}' was not found"
                )
            idx, existing = found
            groups_list = list(existing.groups)
            gi = next(
                (i for i, x in enumerate(groups_list) if x.id == group_id), None
            )
            if gi is None:
                raise NotFoundError(f"GPP group '{group_id}' was not found")
            groups = tuple(groups_list[:gi] + groups_list[gi + 1 :])
            new_collection = replace(existing, groups=groups)
            self._validate_gpp(new_collection, before=self._find_collection(gpo, scope))
            if (
                not new_collection.groups
                and not new_collection.registry
                and not new_collection.groups_unknown_attrs
                and not new_collection.groups_unknown_children
                and not new_collection.registry_unknown_attrs
                and not new_collection.registry_unknown_children
            ):
                new_collections = tuple(
                    c for i, c in enumerate(gpo.gpp_collections) if i != idx
                )
            else:
                new_collections = self._replace_collection(gpo, idx, new_collection)
            return replace(gpo, gpp_collections=new_collections)

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)

    def put_gpp_registry(
        self,
        guid: str,
        expected_revision: int,
        scope: GppScope,
        registry: GppRegistry,
        *,
        identity: Identity | str,
        reason: str,
        must_exist: bool = False,
        common_edits: Mapping[str, bool] | None = None,
        merge: Callable[[GppRegistry], GppRegistry] | None = None,
    ) -> GPO:
        """Add *registry*, or replace the stored item with its id; *merge* as
        for `put_gpp_group`."""
        processed = ensure_editor_ids(GppCollection(scope=scope, registry=(registry,)))
        registry = processed.registry[0]

        def mutate(gpo: GPO) -> GPO:
            found = self._find_collection(gpo, scope)
            if found is None:
                if must_exist:
                    raise NotFoundError(
                        f"GPP registry with id {registry.id} not found"
                    )
                added = _with_common_edits(registry, None, common_edits)
                new_collection = GppCollection(scope=scope, registry=(added,))
                new_collections = gpo.gpp_collections + (new_collection,)
            else:
                idx, existing = found
                items_list = list(existing.registry)
                try:
                    ri = next(i for i, x in enumerate(items_list) if x.id == registry.id)
                    edited = registry
                    if merge is not None:
                        edited = ensure_editor_ids(
                            GppCollection(scope=scope, registry=(merge(items_list[ri]),))
                        ).registry[0]
                    items_list[ri] = _keep_document_position(
                        registry_action_edit(
                            _with_common_edits(edited, items_list[ri], common_edits),
                            items_list[ri],
                        ),
                        items_list[ri],
                    )
                except StopIteration:
                    if must_exist:
                        raise NotFoundError(
                            f"GPP registry with id {registry.id} not found"
                        ) from None
                    items_list.append(_with_common_edits(registry, None, common_edits))
                new_collection = replace(existing, registry=tuple(items_list))
                new_collections = self._replace_collection(gpo, idx, new_collection)
            self._validate_gpp(new_collection, before=self._find_collection(gpo, scope))
            return replace(gpo, gpp_collections=new_collections)

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)

    def delete_gpp_registry(
        self,
        guid: str,
        expected_revision: int,
        scope: GppScope,
        registry_id: str,
        *,
        identity: Identity | str,
        reason: str,
    ) -> GPO:
        if not registry_id:
            raise ValidationError([
                ValidationIssue(
                    severity="error",
                    code="empty_gpp_registry_id",
                    message="GPP registry id is required.",
                    path="registry_id",
                )
            ])

        def mutate(gpo: GPO) -> GPO:
            found = self._find_collection(gpo, scope)
            if found is None:
                raise NotFoundError(
                    f"GPP collection for scope '{scope}' was not found"
                )
            idx, existing = found
            items_list = list(existing.registry)
            ri = next(
                (i for i, x in enumerate(items_list) if x.id == registry_id),
                None,
            )
            if ri is None:
                raise NotFoundError(f"GPP registry '{registry_id}' was not found")
            items = tuple(items_list[:ri] + items_list[ri + 1 :])
            new_collection = replace(existing, registry=items)
            self._validate_gpp(new_collection, before=self._find_collection(gpo, scope))
            if (
                not new_collection.groups
                and not new_collection.registry
                and not new_collection.groups_unknown_attrs
                and not new_collection.groups_unknown_children
                and not new_collection.registry_unknown_attrs
                and not new_collection.registry_unknown_children
            ):
                new_collections = tuple(
                    c for i, c in enumerate(gpo.gpp_collections) if i != idx
                )
            else:
                new_collections = self._replace_collection(gpo, idx, new_collection)
            return replace(gpo, gpp_collections=new_collections)

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)

    def put_gpp_member(
        self,
        guid: str,
        expected_revision: int,
        scope: GppScope,
        group_id: str,
        member: GppGroupMember,
        *,
        identity: Identity | str,
        reason: str,
        must_exist: bool = False,
        merge: Callable[[GppGroupMember], GppGroupMember] | None = None,
    ) -> GPO:
        if not member.id:
            member = replace(member, id=str(uuid.uuid4()))

        def mutate(gpo: GPO) -> GPO:
            found = self._find_collection(gpo, scope)
            if found is None:
                raise NotFoundError(
                    f"GPP collection for scope '{scope}' was not found"
                )
            idx, existing = found
            group_idx = None
            for gi, g in enumerate(existing.groups):
                if g.id == group_id:
                    group_idx = gi
                    break
            if group_idx is None:
                raise NotFoundError(f"GPP group '{group_id}' was not found")
            group = existing.groups[group_idx]
            members_list = list(group.members)
            try:
                mi = next(i for i, x in enumerate(members_list) if x.id == member.id)
                members_list[mi] = merge(members_list[mi]) if merge is not None else member
            except StopIteration:
                if must_exist:
                    raise NotFoundError(
                        f"GPP member with id {member.id} not found"
                    ) from None
                members_list.append(member)
            new_group = replace(group, members=tuple(members_list))
            new_groups = tuple(
                new_group if i == group_idx else g
                for i, g in enumerate(existing.groups)
            )
            new_collection = replace(existing, groups=new_groups)
            new_collections = self._replace_collection(gpo, idx, new_collection)
            self._validate_gpp(new_collection, before=self._find_collection(gpo, scope))
            return replace(gpo, gpp_collections=new_collections)

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)

    def delete_gpp_member(
        self,
        guid: str,
        expected_revision: int,
        scope: GppScope,
        group_id: str,
        member_id: str,
        *,
        identity: Identity | str,
        reason: str,
    ) -> GPO:
        if not member_id:
            raise ValidationError([
                ValidationIssue(
                    severity="error",
                    code="empty_gpp_member_id",
                    message="GPP member id is required.",
                    path="member_id",
                )
            ])

        def mutate(gpo: GPO) -> GPO:
            found = self._find_collection(gpo, scope)
            if found is None:
                raise NotFoundError(
                    f"GPP collection for scope '{scope}' was not found"
                )
            idx, existing = found
            group_idx = None
            for gi, g in enumerate(existing.groups):
                if g.id == group_id:
                    group_idx = gi
                    break
            if group_idx is None:
                raise NotFoundError(f"GPP group '{group_id}' was not found")
            group = existing.groups[group_idx]
            members_list = list(group.members)
            mi = next(
                (i for i, x in enumerate(members_list) if x.id == member_id),
                None,
            )
            if mi is None:
                raise NotFoundError(f"GPP member '{member_id}' was not found")
            members = tuple(members_list[:mi] + members_list[mi + 1 :])
            new_group = replace(group, members=members)
            new_groups = tuple(
                new_group if i == group_idx else g
                for i, g in enumerate(existing.groups)
            )
            new_collection = replace(existing, groups=new_groups)
            new_collections = self._replace_collection(gpo, idx, new_collection)
            self._validate_gpp(new_collection, before=self._find_collection(gpo, scope))
            return replace(gpo, gpp_collections=new_collections)

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)

    def put_gpp_registry_value(
        self,
        guid: str,
        expected_revision: int,
        scope: GppScope,
        registry_id: str,
        value: GppRegistryValue,
        *,
        identity: Identity | str,
        reason: str,
        must_exist: bool = False,
        merge: Callable[[GppRegistryValue], GppRegistryValue] | None = None,
    ) -> GPO:
        if not value.id:
            value = replace(value, id=str(uuid.uuid4()))

        def mutate(gpo: GPO) -> GPO:
            found = self._find_collection(gpo, scope)
            if found is None:
                raise NotFoundError(
                    f"GPP collection for scope '{scope}' was not found"
                )
            idx, existing = found
            reg_idx = None
            for ri, r in enumerate(existing.registry):
                if r.id == registry_id:
                    reg_idx = ri
                    break
            if reg_idx is None:
                raise NotFoundError(f"GPP registry '{registry_id}' was not found")
            registry = existing.registry[reg_idx]
            if must_exist:
                if not registry.value.id or registry.value.id != value.id:
                    raise NotFoundError(
                        f"GPP registry value with id {value.id} not found"
                    )
            elif registry.value.id:
                raise ValidationError([
                    ValidationIssue(
                        severity="error",
                        code="gpp_registry_value_already_exists",
                        message=(
                            "A value already exists for this registry item;"
                            " use PUT to update it."
                        ),
                        path="value_id",
                    )
                ])
            new_value = merge(registry.value) if merge is not None and must_exist else value
            new_registry = replace(registry, value=new_value)
            new_registries = tuple(
                new_registry if i == reg_idx else r
                for i, r in enumerate(existing.registry)
            )
            new_collection = replace(existing, registry=new_registries)
            new_collections = self._replace_collection(gpo, idx, new_collection)
            self._validate_gpp(new_collection, before=self._find_collection(gpo, scope))
            return replace(gpo, gpp_collections=new_collections)

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)

    def delete_gpp_registry_value(
        self,
        guid: str,
        expected_revision: int,
        scope: GppScope,
        registry_id: str,
        value_id: str,
        *,
        identity: Identity | str,
        reason: str,
    ) -> GPO:
        if not value_id:
            raise ValidationError([
                ValidationIssue(
                    severity="error",
                    code="empty_gpp_registry_value_id",
                    message="GPP registry value id is required.",
                    path="value_id",
                )
            ])

        def mutate(gpo: GPO) -> GPO:
            found = self._find_collection(gpo, scope)
            if found is None:
                raise NotFoundError(
                    f"GPP collection for scope '{scope}' was not found"
                )
            idx, existing = found
            reg_idx = None
            for ri, r in enumerate(existing.registry):
                if r.id == registry_id:
                    reg_idx = ri
                    break
            if reg_idx is None:
                raise NotFoundError(f"GPP registry '{registry_id}' was not found")
            registry = existing.registry[reg_idx]
            if registry.value.id != value_id:
                raise NotFoundError(f"GPP registry value '{value_id}' was not found")
            new_registries = tuple(
                r for i, r in enumerate(existing.registry) if i != reg_idx
            )
            new_collection = replace(existing, registry=new_registries)
            new_collections = self._replace_collection(gpo, idx, new_collection)
            if (
                not new_collection.groups
                and not new_collection.registry
                and not new_collection.groups_unknown_attrs
                and not new_collection.groups_unknown_children
                and not new_collection.registry_unknown_attrs
                and not new_collection.registry_unknown_children
            ):
                new_collections = tuple(
                    c for i, c in enumerate(gpo.gpp_collections) if i != idx
                )
            self._validate_gpp(new_collection, before=self._find_collection(gpo, scope))
            return replace(gpo, gpp_collections=new_collections)

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)

    def revisions(self, guid: str) -> list[Revision]:
        with self._lock:
            self._require_healthy()
            try:
                self.get_gpo(guid)
                rows = self._connection.execute(
                    """SELECT revision, actor, reason, created_at, snapshot_json FROM revisions
                       WHERE gpo_guid=? ORDER BY revision DESC""",
                    (guid.lower(),),
                ).fetchall()
                return [
                    Revision(
                        revision=int(row["revision"]),
                        actor=str(row["actor"]),
                        reason=str(row["reason"]),
                        created_at=str(row["created_at"]),
                        snapshot=self._load_snapshot(row["snapshot_json"]),
                    )
                    for row in rows
                ]
            except sqlite3.Error as error:
                self._map_sqlite_error(error)

    def get_revision(self, guid: str, revision: int) -> Revision:
        with self._lock:
            self._require_healthy()
            try:
                row = self._connection.execute(
                    """SELECT revision, actor, reason, created_at, snapshot_json FROM revisions
                       WHERE gpo_guid=? AND revision=?""",
                    (guid.lower(), revision),
                ).fetchone()
                if row is None:
                    raise NotFoundError(f"Revision {revision} was not found")
                return Revision(
                    revision=int(row["revision"]),
                    actor=str(row["actor"]),
                    reason=str(row["reason"]),
                    created_at=str(row["created_at"]),
                    snapshot=self._load_snapshot(row["snapshot_json"]),
                )
            except sqlite3.Error as error:
                self._map_sqlite_error(error)

    def restore_revision(
        self,
        guid: str,
        revision: int,
        expected_revision: int,
        *,
        identity: Identity | str,
        reason: str,
    ) -> GPO:
        historical = gpo_from_dict(self.get_revision(guid, revision).snapshot)
        issues = validate_gpo(historical)
        if any(issue.severity == "error" for issue in issues):
            raise ValidationError(issues)
        if historical.status == "ready":
            ready_issues = validate_ready_transition(historical)
            if ready_issues:
                raise ValidationError(ready_issues)

        # The revision's retained content and its provenance records come back
        # with it; the restore creates no record (final-r13).
        grants = _Grants()
        for collection in historical.gpp_collections:
            grants.grant(
                collection.scope,
                provenance.fragments(collection),
                provenance.records(collection),
            )

        def mutate(current: GPO) -> GPO:
            return replace(
                historical,
                revision=current.revision,
                created_at=current.created_at,
                updated_at=current.updated_at,
            )

        # The one change a GPO whose read replaced stored values may take: it
        # replaces everything, with a revision validated above.
        return self._mutate(
            guid,
            expected_revision,
            mutate,
            identity=identity,
            reason=reason,
            grants=grants,
            replaces_all=True,
        )

    def replace_from_import(
        self,
        guid: str,
        expected_revision: int,
        imported: GPO,
        *,
        identity: Identity | str,
        reason: str,
    ) -> GPO:
        """Replace a GPO's content whole with a fresh import of its source
        backup, as a new revision (Sol final-r20 B1).

        The recovery for a GPO whose stored values cannot be read, when no
        earlier revision is readable either (an import's first revision): a
        re-import as a new GPO conflicts on its name. *imported* is the GPO the
        import built (its guid and revision are this GPO's); it must be of the
        same source GPO, so an unrelated backup cannot overwrite this one. Its
        preferences carry the import's freshly minted provenance records,
        granted here as an import's are; nothing of the current content is
        kept. Every earlier revision stays as it was."""
        current = self.get_gpo(guid)
        if not _has_replaced(current):
            raise ValidationError([
                ValidationIssue(
                    severity="error",
                    code="replace_not_a_recovery",
                    message=(
                        "Studio replaces a GPO from an import only as the recovery for "
                        "stored values it cannot read; this GPO has none. To bring it in "
                        "line with its backup, rename it, import the backup as a new GPO, "
                        "and carry the differences across with the diff."
                    ),
                    path="replace_guid",
                )
            ])
        self._require_same_source(guid, imported)
        grants = _Grants()
        for collection in imported.gpp_collections:
            grants.grant(
                collection.scope,
                provenance.fragments(collection),
                provenance.records(collection),
            )

        def mutate(existing: GPO) -> GPO:
            return replace(
                imported,
                guid=existing.guid,
                revision=existing.revision,
                created_at=existing.created_at,
                updated_at=existing.updated_at,
            )

        return self._mutate(
            guid,
            expected_revision,
            mutate,
            identity=identity,
            reason=reason,
            grants=grants,
            replaces_all=True,
        )

    def _require_same_source(self, guid: str, imported: GPO) -> None:
        """Refuse a replacement unless *imported* is a backup of the very GPO
        *guid* was first imported from (Sol final-r21 B1).

        A GPO GUID is not unique across estates -- another forest's GPO, or a
        same-named domain rebuilt with a new domain GUID, can carry it -- so
        the identity is the GPO GUID, the domain's DNS name AND its GUID
        (``GPODomainGuid``), compared with what the FIRST revision's import
        record holds: revision 1 is immutable, the head is not. A GPO whose
        first import recorded no domain GUID (an import before 1.1.0, an
        estate entry, a Studio-format bundle) cannot be identified, so it is
        refused, not half-checked -- its other recoveries remain (a readable
        revision, or a deletion, which is archived, then a fresh import)."""
        first = gpo_from_dict(self.get_revision(guid, 1).snapshot)
        recorded = first.backup_inventory
        offered = imported.backup_inventory

        def identity(inventory: BackupInventory | None) -> tuple[str, str, str] | None:
            if inventory is None or not inventory.source_domain_guid:
                return None
            return (
                _plain_guid(inventory.source_gpo_guid),
                inventory.source_domain.strip().rstrip(".").casefold(),
                _plain_guid(inventory.source_domain_guid),
            )

        def shown(value: tuple[str, str, str] | None) -> str:
            if value is None:
                return "no recorded domain identity"
            return f"GPO {{{value[0]}}} in {value[1]} (domain {{{value[2]}}})"

        mine, theirs = identity(recorded), identity(offered)
        if mine is not None and _plain_guid(first.source_guid) != mine[0]:
            # Revision 1 carries an import record but is not the import: a
            # fork copies its parent's record, and its source is the parent
            # (DeepSeek final-r22 N1). A fork is its own GPO, never replaced by
            # its ancestor's backup.
            raise ValidationError([
                ValidationIssue(
                    severity="error",
                    code="replace_not_the_import",
                    message=(
                        "Only a GPO whose first revision is an import is replaced from a "
                        "backup; this GPO's first revision was made from another Studio "
                        f"GPO ({first.source_guid or 'none'}). Restore a readable revision, "
                        "or delete the GPO (archived) and import the backup as a new GPO."
                    ),
                    path="replace_guid",
                )
            ])
        if mine is None:
            raise ValidationError([
                ValidationIssue(
                    severity="error",
                    code="replace_source_unrecorded",
                    message=(
                        "This GPO's first revision recorded no domain identity -- it was "
                        "imported before 1.1.0, not from a native GPMC backup, or from a "
                        "backup whose manifest has no GPODomainGuid -- so Studio cannot "
                        "prove a backup is of the same GPO and will not replace it. "
                        "Restore a readable revision, or delete the GPO (archived) and "
                        "import the backup as a new GPO."
                    ),
                    path="replace_guid",
                )
            ])
        if theirs != mine or _plain_guid(imported.source_guid) != mine[0]:
            raise ValidationError([
                ValidationIssue(
                    severity="error",
                    code="replace_source_mismatch",
                    # The recorded identity is never served (final-r23), so the
                    # refusal names only what the offered backup holds.
                    message=(
                        "A GPO is replaced only by a backup of the GPO it was first "
                        f"imported from; this backup holds {shown(theirs)}, which is not "
                        "the GPO, domain and domain GUID recorded at that import."
                    ),
                    path="replace_guid",
                )
            ])

    def deletion_log(self) -> list[dict[str, Any]]:
        with self._lock:
            self._require_healthy()
            try:
                rows = self._connection.execute(
                    """SELECT guid, name, actor, reason, revision, deleted_at
                       FROM deletion_log ORDER BY deleted_at DESC"""
                ).fetchall()
                return [dict(row) for row in rows]
            except sqlite3.Error as error:
                self._map_sqlite_error(error)
