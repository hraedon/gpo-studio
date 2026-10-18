"""Give stored GPP snapshots predicate ids and provenance records (final-r13).

Before final-r13 a workspace recorded no provenance: retained content was
trusted because it was present. This one-time data migration adds import
records to stored head and revision snapshots, and nothing else:

* each retained fragment gets an import record only under the conservative
  rule below. Everything else is unverified: kept and shown, but judged by
  the closed vocabulary at egress;
* a snapshot that gains no record is left byte-identical. Its targeting
  predicate ids and its legacy raw ``FilterRunOnce`` (normalised to the
  item's apply-once option and run-once id, as import holds it) are what
  every load derives (`store.gpo_from_dict`), and the first mutation writes
  them.

**The rule.** A fragment is recorded as imported only if the revision that
FIRST holds it was created by an import, or by a fork of a GPO that held it
recorded at the time of the fork. What the history records: each revision row
carries ``actor``, the operator's free-text ``reason``, ``created_at`` and the
full snapshot -- no operation name. A GPO's revision 1 is always its creation,
and every later revision a mutation, so only revision 1 can be an acquisition.
Its snapshot says which creation it was:

* a backup import (``POST /api/backups/import``) writes revision 1
  ``archived`` with the provenance line ``Imported from GPMC backup {GUID}``
  as its description (the same immutable facts the lifecycle restore plan
  relies on);
* an estate import writes revision 1 ``archived`` with ``source_guid`` equal
  to the GPO's own GUID (an estate carries no preferences, so this matters
  only for direct callers);
* a fork writes revision 1 ``draft`` with ``source_guid`` naming the parent
  workspace GPO: its fragments are recorded only if the parent held them
  recorded in the parent's revision current when the fork was made (by
  ``created_at``); a parent that no longer exists records nothing;
* anything else (``POST /api/gpos``, a starter derivation, a direct caller)
  records nothing.

So a fragment first stored by a mutation -- including a pre-final-r11 API
edit that supplied raw content -- or one restored into the head from such a
revision, stays unverified, and so does everything in a GPO whose creation
the history does not show to be an import.

The migration is keyed by ``workspace_meta.gpp_provenance`` rather than a
schema version: the rewritten snapshots stay readable by a schema-4 build,
which ignores the new keys, and a schema-4 build that writes drops records --
which can only leave content unverified, never verify it.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

META_KEY = "gpp_provenance"
META_VALUE = "1"

_IMPORT_PROVENANCE = re.compile(
    r"Imported from GPMC backup \{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}"
)


def _norm_guid(value: Any) -> str:
    return str(value or "").strip().strip("{}").lower()


def legacy_key(key: tuple[str, ...]) -> tuple[str, ...]:
    """A fragment key without its predicate id: before final-r13 a predicate had
    none, so a fragment is followed through history by its item and content."""
    if len(key) > 3 and key[2] == "ilt":
        return (*key[:3], *key[4:])
    return key


@dataclass
class _Snapshot:
    revision: int  # 0: the head
    created_at: str
    data: dict[str, Any]
    collections: list[Any] = field(default_factory=list)


def _load_collections(guid: str, data: dict[str, Any]) -> list[Any]:
    """The snapshot's collections as a load sees them: legacy item ids,
    normalised run-once, derived predicate ids."""
    from .store import gpo_from_dict

    minimal = {
        "guid": guid,
        "name": str(data.get("name", "")),
        "gpp_collections": data.get("gpp_collections", []),
    }
    return list(gpo_from_dict(minimal).gpp_collections)


def _legacy_keys(collections: Iterable[Any]) -> set[tuple[str, ...]]:
    from . import gpp_provenance

    return {
        (collection.scope, *legacy_key(key))
        for collection in collections
        for key, _text in gpp_provenance.fragments(collection).values()
    }


def _creation(guid: str, first: dict[str, Any]) -> str:
    status = first.get("status")
    if status == "archived" and _IMPORT_PROVENANCE.fullmatch(str(first.get("description", ""))):
        return "import"
    if status == "archived" and _norm_guid(first.get("source_guid")) == _norm_guid(guid):
        return "import"
    if status == "draft" and _norm_guid(first.get("source_guid")):
        return "fork"
    return "none"


class _History:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.snapshots: dict[str, list[_Snapshot]] = {}
        self.first_verified: dict[str, set[tuple[str, ...]]] = {}
        self.guids = {
            str(row[0]) for row in conn.execute("SELECT guid FROM gpos").fetchall()
        }

    def revisions(self, guid: str) -> list[_Snapshot]:
        if guid not in self.snapshots:
            rows = self.conn.execute(
                "SELECT revision, created_at, snapshot_json FROM revisions "
                "WHERE gpo_guid=? ORDER BY revision",
                (guid,),
            ).fetchall()
            snapshots = []
            for revision, created_at, payload in rows:
                data = json.loads(payload)
                snapshots.append(
                    _Snapshot(int(revision), str(created_at), data, _load_collections(guid, data))
                )
            self.snapshots[guid] = snapshots
        return self.snapshots[guid]

    def verified_at_creation(self, guid: str, visiting: frozenset[str] = frozenset()) -> set[
        tuple[str, ...]
    ]:
        """The legacy keys of revision 1 recorded as imported (see the module doc)."""
        if guid in self.first_verified:
            return self.first_verified[guid]
        verified: set[tuple[str, ...]] = set()
        history = self.revisions(guid)
        first = history[0] if history and history[0].revision == 1 else None
        if first is not None:
            keys = _legacy_keys(first.collections)
            kind = _creation(guid, first.data)
            if kind == "import":
                verified = keys
            elif kind == "fork":
                parent = _norm_guid(first.data.get("source_guid"))
                if parent in self.guids and parent != guid and parent not in visiting:
                    parent_verified = self.verified_at_creation(parent, visiting | {guid})
                    at_fork = [
                        snapshot
                        for snapshot in self.revisions(parent)
                        if snapshot.created_at <= first.created_at
                    ]
                    if at_fork:
                        verified = keys & _legacy_keys(at_fork[-1].collections) & parent_verified
        self.first_verified[guid] = verified
        return verified


def _record(
    collections: list[Any], verified: set[tuple[str, ...]], data: dict[str, Any]
) -> bool:
    """Add the import records of *data*'s collections; ``False`` if there are none.

    Only the ``retained_provenance`` key is added: a snapshot that gains no
    record is left byte-identical (stored history is evidence), and the
    predicate ids and the normalised run-once shape are what every load of the
    snapshot derives, so the records -- keyed by the loaded form -- name the
    same fragments on every later load."""
    from . import gpp_provenance

    changed = False
    for collection, collection_data in zip(
        collections, data.get("gpp_collections", []), strict=True
    ):
        recorded = sorted(
            key
            for key, (place, _text) in gpp_provenance.fragments(collection).items()
            if (collection.scope, *legacy_key(place)) in verified
        )
        if recorded:
            collection_data["retained_provenance"] = recorded
            changed = True
    return changed


def migrate_retained_provenance(conn: sqlite3.Connection) -> None:
    """Run once per workspace, inside the caller's transaction."""
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if not {"workspace_meta", "gpos", "revisions"} <= tables:
        return  # nothing stored to migrate (a schema-only database)
    done = conn.execute(
        "SELECT value FROM workspace_meta WHERE key=?", (META_KEY,)
    ).fetchone()
    if done is not None:
        return
    history = _History(conn)
    for guid in sorted(history.guids):
        verified = history.verified_at_creation(guid)
        if not verified:
            continue  # nothing recorded: every snapshot of this GPO stays as it is
        for snapshot in history.revisions(guid):
            if not _record(snapshot.collections, verified, snapshot.data):
                continue
            conn.execute(
                "UPDATE revisions SET snapshot_json=? WHERE gpo_guid=? AND revision=?",
                (
                    json.dumps(snapshot.data, separators=(",", ":"), sort_keys=True),
                    guid,
                    snapshot.revision,
                ),
            )
        row = conn.execute("SELECT snapshot_json FROM gpos WHERE guid=?", (guid,)).fetchone()
        head = json.loads(row[0])
        if _record(_load_collections(guid, head), verified, head):
            conn.execute(
                "UPDATE gpos SET snapshot_json=? WHERE guid=?",
                (json.dumps(head, separators=(",", ":"), sort_keys=True), guid),
            )
    conn.execute(
        "INSERT OR REPLACE INTO workspace_meta(key, value) VALUES (?, ?)", (META_KEY, META_VALUE)
    )
