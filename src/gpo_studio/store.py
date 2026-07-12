"""SQLite workspace with immutable revisions and optimistic concurrency."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

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
    WmiFilter,
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
    return SecurityFilter(
        id=str(data["id"]),
        principal=str(data["principal"]),
        permission=data.get("permission", "apply"),
        inheritable=bool(data.get("inheritable", True)),
    )


def _wmi_filter(data: dict[str, Any]) -> WmiFilter:
    return WmiFilter(
        id=str(data["id"]),
        name=str(data["name"]),
        description=str(data.get("description", "")),
        query=str(data.get("query", "")),
        language=str(data.get("language", "WQL")),
    )


def gpo_from_dict(data: dict[str, Any]) -> GPO:
    return GPO(
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
        domain=str(data.get("domain", "studio.local")),
        created_at=str(data.get("created_at", "")),
        updated_at=str(data.get("updated_at", "")),
    )


class WorkspaceStore:
    """Persist editable GPO snapshots and their audit history."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def close(self) -> None:
        self._connection.close()

    def _migrate(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS gpos (
                guid TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                revision INTEGER NOT NULL,
                snapshot_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_gpos_name_nocase
                ON gpos(name COLLATE NOCASE);
            CREATE TABLE IF NOT EXISTS revisions (
                gpo_guid TEXT NOT NULL REFERENCES gpos(guid) ON DELETE CASCADE,
                revision INTEGER NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                PRIMARY KEY (gpo_guid, revision)
            );
            """
        )
        self._connection.commit()

    def list_gpos(self) -> list[GPO]:
        rows = self._connection.execute(
            "SELECT snapshot_json FROM gpos ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return [gpo_from_dict(json.loads(row["snapshot_json"])) for row in rows]

    def get_gpo(self, guid: str) -> GPO:
        row = self._connection.execute(
            "SELECT snapshot_json FROM gpos WHERE guid = ?", (guid.lower(),)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"GPO {guid} was not found")
        return gpo_from_dict(json.loads(row["snapshot_json"]))

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
    ) -> GPO:
        actor = _resolve_actor(identity)
        timestamp = _now()
        gpo = GPO(
            guid=(guid or str(uuid.uuid4())).lower().strip("{}"),
            name=name.strip(),
            description=description.strip(),
            revision=1,
            settings=settings,
            links=links,
            source_guid=source_guid,
            cse_metadata=cse_metadata,
            created_at=timestamp,
            updated_at=timestamp,
        )
        payload = json.dumps(gpo.to_dict(), separators=(",", ":"), sort_keys=True)
        try:
            with self._connection:
                self._connection.execute(
                    """INSERT INTO gpos(guid, name, revision, snapshot_json, updated_at)
                       VALUES(?,?,?,?,?)""",
                    (gpo.guid, gpo.name, gpo.revision, payload, timestamp),
                )
                self._connection.execute(
                    "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
                    (gpo.guid, 1, actor, reason, timestamp, payload),
                )
        except sqlite3.IntegrityError as error:
            raise ConflictError("A GPO with that name or GUID already exists") from error
        return gpo

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
        current = self.get_gpo(guid)
        if current.revision != expected_revision:
            raise ConflictError(
                f"Expected revision {expected_revision}, "
                f"but the current revision is {current.revision}"
            )
        timestamp = _now()
        changed = mutation(current)
        updated = replace(changed, revision=current.revision + 1, updated_at=timestamp)
        payload = json.dumps(updated.to_dict(), separators=(",", ":"), sort_keys=True)
        with self._connection:
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
                raise ConflictError("The GPO changed while this request was being processed")
            self._connection.execute(
                "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
                (current.guid, updated.revision, actor, reason, timestamp, payload),
            )
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

        def mutate(gpo: GPO) -> GPO:
            return replace(gpo, **values)

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)

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
        new_ids = {s.id for s in new_settings}

        def mutate(gpo: GPO) -> GPO:
            existing = [s for s in gpo.settings if s.id not in new_ids]
            combined = existing + new_settings
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

    def revisions(self, guid: str) -> list[Revision]:
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

    def get_revision(self, guid: str, revision: int) -> Revision:
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

        def mutate(current: GPO) -> GPO:
            return replace(
                historical,
                revision=current.revision,
                created_at=current.created_at,
                updated_at=current.updated_at,
            )

        return self._mutate(guid, expected_revision, mutate, identity=identity, reason=reason)
