"""SQLite workspace with immutable revisions and optimistic concurrency."""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NoReturn, cast

from .gpp import (
    GppCollection,
    GppGroup,
    GppGroupMember,
    GppRegistry,
    GppRegistryValue,
    GppScope,
    ensure_editor_ids,
    gpp_collection_from_dict,
)
from .identity import Identity
from .model import (
    GPO,
    ConflictError,
    CseFileEntry,
    CseMetadataEntry,
    CseSide,
    GPOLink,
    NotFoundError,
    RegistrySetting,
    Revision,
    SecurityFilter,
    ValidationError,
    ValidationIssue,
    WmiFilter,
    WorkspaceError,
)
from .validation import (
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
        enabled=bool(data.get("enabled", True)),
        enforced=bool(data.get("enforced", False)),
        order=int(data.get("order", 1)),
    )


def _cse_file_entry(data: dict[str, Any]) -> CseFileEntry:
    return CseFileEntry(
        relative_path=str(data["relative_path"]),
        content_hash=str(data["content_hash"]),
        size=int(data["size"]),
    )


def _cse_metadata_entry(data: dict[str, Any]) -> CseMetadataEntry:
    side_raw = str(data["side"])
    if side_raw not in ("machine", "user"):
        raise ValueError(f"Invalid CSE side: {side_raw!r}")
    return CseMetadataEntry(
        guid=str(data["guid"]),
        side=cast(CseSide, side_raw),
        files=tuple(_cse_file_entry(f) for f in data.get("files", [])),
    )


def _security_filter(data: dict[str, Any]) -> SecurityFilter:
    permission = data.get("permission", "apply")
    if permission not in ("apply", "read"):
        raise ValueError(f"Invalid permission: {permission!r}")
    target_type = data.get("target_type", "group")
    if target_type not in ("user", "group", "computer"):
        raise ValueError(f"Invalid target_type: {target_type!r}")
    return SecurityFilter(
        id=str(data["id"]),
        principal=str(data["principal"]),
        permission=permission,
        inheritable=bool(data.get("inheritable", True)),
        target_type=target_type,
        sid=str(data.get("sid", "")),
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
    return gpp_collection_from_dict(data)


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


def gpo_from_dict(data: dict[str, Any]) -> GPO:
    gpo = GPO(
        guid=str(data["guid"]),
        name=str(data["name"]),
        description=str(data.get("description", "")),
        computer_enabled=bool(data.get("computer_enabled", True)),
        user_enabled=bool(data.get("user_enabled", True)),
        status=data.get("status", "draft"),
        revision=int(data.get("revision", 0)),
        settings=tuple(_setting(item) for item in data.get("settings", [])),
        links=tuple(_link(item) for item in data.get("links", [])),
        source_guid=str(data.get("source_guid", "")),
        cse_metadata=tuple(
            _cse_metadata_entry(item) for item in data.get("cse_metadata", [])
        ),
        security_filters=tuple(
            _security_filter(item) for item in data.get("security_filters", [])
        ),
        wmi_filter=_wmi_filter(data["wmi_filter"]) if data.get("wmi_filter") else None,
        gpp_collections=tuple(
            _gpp_collection(item) for item in data.get("gpp_collections", [])
        ),
        domain=str(data.get("domain", "studio.local")),
        created_at=str(data.get("created_at", "")),
        updated_at=str(data.get("updated_at", "")),
    )
    return _assign_legacy_gpp_ids(gpo)


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
        """Raise WorkspaceError if the store is in degraded mode."""
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

    def list_gpos(self) -> list[GPO]:
        with self._lock:
            self._require_healthy()
            try:
                rows = self._connection.execute(
                    "SELECT snapshot_json FROM gpos ORDER BY name COLLATE NOCASE"
                ).fetchall()
                return [gpo_from_dict(json.loads(row["snapshot_json"])) for row in rows]
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
                return gpo_from_dict(json.loads(row["snapshot_json"]))
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
        domain: str = "studio.local",
        computer_enabled: bool = True,
        user_enabled: bool = True,
        status: Literal["draft", "ready", "archived"] = "draft",
        security_filters: tuple[SecurityFilter, ...] = (),
        wmi_filter: WmiFilter | None = None,
        gpp_collections: tuple[GppCollection, ...] = (),
    ) -> GPO:
        actor = _resolve_actor(identity)
        timestamp = _now()
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
            domain=domain,
            security_filters=security_filters,
            wmi_filter=wmi_filter,
            gpp_collections=gpp_collections,
            created_at=timestamp,
            updated_at=timestamp,
        )
        payload = json.dumps(gpo.to_dict(), separators=(",", ":"), sort_keys=True)
        with self._lock:
            self._require_healthy()
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._connection.execute(
                    """INSERT INTO gpos(guid, name, revision, snapshot_json, updated_at)
                       VALUES(?,?,?,?,?)""",
                    (gpo.guid, gpo.name, gpo.revision, payload, timestamp),
                )
                self._connection.execute(
                    "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
                    (gpo.guid, 1, actor, reason, timestamp, payload),
                )
                self._connection.execute("COMMIT")
            except sqlite3.Error as error:
                with contextlib.suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                self._map_sqlite_error(error)
        return gpo

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
                description=gpo.description.strip(),
                revision=1,
                created_at=timestamp,
                updated_at=timestamp,
                gpp_collections=tuple(
                    ensure_editor_ids(c) for c in gpo.gpp_collections
                ),
            )
            issues = validate_gpo(normalized)
            if any(issue.severity == "error" for issue in issues):
                rejected += 1
                continue
            if normalized.status == "ready":
                ready_issues = validate_ready_transition(normalized)
                if ready_issues:
                    rejected += 1
                    continue
            payload = json.dumps(
                normalized.to_dict(), separators=(",", ":"), sort_keys=True
            )
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
                        self._connection.execute(
                            "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
                            (normalized.guid, 1, actor, reason, timestamp, payload),
                        )
                        self._connection.execute("COMMIT")
                    except sqlite3.Error as error:
                        with contextlib.suppress(sqlite3.Error):
                            self._connection.execute("ROLLBACK")
                        if isinstance(error, sqlite3.IntegrityError):
                            raise
                        self._map_sqlite_error(error)
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

    def fork_gpo(
        self,
        guid: str,
        new_name: str,
        *,
        identity: Identity | str,
        reason: str,
    ) -> GPO:
        source = self.get_gpo(guid)
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
            domain=source.domain,
            computer_enabled=source.computer_enabled,
            user_enabled=source.user_enabled,
            security_filters=forked_security_filters,
            wmi_filter=forked_wmi,
            gpp_collections=source.gpp_collections,
        )

    def _mutate(
        self,
        guid: str,
        expected_revision: int,
        mutation: Callable[[GPO], GPO],
        *,
        identity: Identity | str,
        reason: str,
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
                timestamp = _now()
                changed = mutation(current)
                updated = replace(
                    changed, revision=current.revision + 1, updated_at=timestamp
                )
                payload = json.dumps(
                    updated.to_dict(), separators=(",", ":"), sort_keys=True
                )
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
            except Exception as exc:
                with contextlib.suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                if isinstance(exc, sqlite3.Error):
                    self._map_sqlite_error(exc)
                raise
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
        errors: list[ValidationIssue] = []
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
        new_setting = _setting({"id": setting_id or str(uuid.uuid4()), **values})
        self._validate_settings([new_setting])

        def mutate(gpo: GPO) -> GPO:
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
            return self.get_gpo(guid)
        self._validate_settings(new_settings)
        new_ids = {s.id for s in new_settings}

        def mutate(gpo: GPO) -> GPO:
            existing = [s for s in gpo.settings if s.id not in new_ids]
            combined = existing + new_settings
            combined.sort(key=lambda item: item.identity())
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
            return replace(gpo, links=tuple(links))

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
            return replace(gpo, security_filters=tuple(filters))

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

    def _validate_gpp(self, collection: GppCollection) -> None:
        errors = [
            issue
            for issue in validate_gpp_collection(collection)
            if issue.severity == "error"
        ]
        if errors:
            raise ValidationError(errors)

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
            self._validate_gpp(new_collection)
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
    ) -> GPO:
        processed = ensure_editor_ids(GppCollection(scope=scope, groups=(group,)))
        group = processed.groups[0]

        def mutate(gpo: GPO) -> GPO:
            found = self._find_collection(gpo, scope)
            if found is None:
                if must_exist:
                    raise NotFoundError(f"GPP group with id {group.id} not found")
                new_collection = GppCollection(scope=scope, groups=(group,))
                new_collections = gpo.gpp_collections + (new_collection,)
            else:
                idx, existing = found
                groups_list = list(existing.groups)
                try:
                    gi = next(i for i, x in enumerate(groups_list) if x.id == group.id)
                    groups_list[gi] = group
                except StopIteration:
                    if must_exist:
                        raise NotFoundError(
                            f"GPP group with id {group.id} not found"
                        ) from None
                    groups_list.append(group)
                new_collection = replace(existing, groups=tuple(groups_list))
                new_collections = self._replace_collection(gpo, idx, new_collection)
            self._validate_gpp(new_collection)
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
            self._validate_gpp(new_collection)
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
    ) -> GPO:
        processed = ensure_editor_ids(GppCollection(scope=scope, registry=(registry,)))
        registry = processed.registry[0]

        def mutate(gpo: GPO) -> GPO:
            found = self._find_collection(gpo, scope)
            if found is None:
                if must_exist:
                    raise NotFoundError(
                        f"GPP registry with id {registry.id} not found"
                    )
                new_collection = GppCollection(scope=scope, registry=(registry,))
                new_collections = gpo.gpp_collections + (new_collection,)
            else:
                idx, existing = found
                items_list = list(existing.registry)
                try:
                    ri = next(i for i, x in enumerate(items_list) if x.id == registry.id)
                    items_list[ri] = registry
                except StopIteration:
                    if must_exist:
                        raise NotFoundError(
                            f"GPP registry with id {registry.id} not found"
                        ) from None
                    items_list.append(registry)
                new_collection = replace(existing, registry=tuple(items_list))
                new_collections = self._replace_collection(gpo, idx, new_collection)
            self._validate_gpp(new_collection)
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
            self._validate_gpp(new_collection)
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
                members_list[mi] = member
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
            self._validate_gpp(new_collection)
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
            self._validate_gpp(new_collection)
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
            new_registry = replace(registry, value=value)
            new_registries = tuple(
                new_registry if i == reg_idx else r
                for i, r in enumerate(existing.registry)
            )
            new_collection = replace(existing, registry=new_registries)
            new_collections = self._replace_collection(gpo, idx, new_collection)
            self._validate_gpp(new_collection)
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
            self._validate_gpp(new_collection)
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
                        snapshot=json.loads(row["snapshot_json"]),
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
                    snapshot=json.loads(row["snapshot_json"]),
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

        def mutate(current: GPO) -> GPO:
            return replace(
                historical,
                revision=current.revision,
                created_at=current.created_at,
                updated_at=current.updated_at,
            )

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)
