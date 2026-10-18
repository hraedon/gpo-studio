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

Stored data never stops the migration, or the workspace opening (DeepSeek
final-r14 B1): revision 1 is read first and nothing else for a GPO that was
not imported or forked; a snapshot the current model cannot load, or loads
only with problems (a 1.0.0-era namespaced child, say), records nothing, and
the reason is logged and listed in ``workspace_meta.gpp_provenance_unrecorded``.
Such content is refused at export with the reason (`validation`), not on read.

The migration is keyed by ``workspace_meta.gpp_provenance`` rather than a
schema version: the rewritten snapshots stay readable by a schema-4 build,
which ignores the new keys, and a schema-4 build that writes drops records --
which can only leave content unverified, never verify it.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections.abc import Iterable
from typing import Any

META_KEY = "gpp_provenance"
META_VALUE = "1"
#: Why snapshots got no import record (a JSON list), when any did not load.
UNRECORDED_KEY = "gpp_provenance_unrecorded"

_LOG = logging.getLogger(__name__)

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


class _Unloadable(Exception):
    """A stored snapshot the current model cannot load cleanly."""


def _load_collections(guid: str, data: dict[str, Any]) -> list[Any]:
    """The snapshot's collections as a load sees them: legacy item ids,
    normalised run-once, derived predicate ids. Raises `_Unloadable` for one
    the model refuses, or loads only with problems (`load_problems`): content a
    current rule refuses cannot be shown to be imported, so it records nothing
    -- and it must not stop the workspace opening (DeepSeek final-r14 B1)."""
    from .store import gpo_from_dict

    minimal = {
        "guid": guid,
        "name": str(data.get("name", "")),
        "gpp_collections": data.get("gpp_collections", []),
    }
    try:
        collections = list(gpo_from_dict(minimal).gpp_collections)
    except Exception as error:  # any stored shape the model refuses
        raise _Unloadable(f"{type(error).__name__}: {error}") from error
    problems = [
        message
        for collection in collections
        for severity, message in collection.load_problems
        if severity == "error"
    ]
    if problems:
        raise _Unloadable(problems[0])
    return collections


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
    """Revision history, read lazily: a snapshot is parsed only when the rule
    needs it, and one that does not load records nothing (`_Unloadable`)."""

    def __init__(self, conn: sqlite3.Connection, unrecorded: list[dict[str, Any]]) -> None:
        self.conn = conn
        self.unrecorded = unrecorded
        self.first_verified: dict[str, set[tuple[str, ...]]] = {}
        self.guids = {
            str(row[0]) for row in conn.execute("SELECT guid FROM gpos").fetchall()
        }

    def note(self, guid: str, revision: int, reason: str) -> None:
        """Record why a snapshot gets no import record (logged and kept in
        ``workspace_meta.gpp_provenance_unrecorded``)."""
        _LOG.warning(
            "gpp provenance: GPO %s revision %s records nothing: %s", guid, revision, reason
        )
        if len(self.unrecorded) < _MAX_NOTES:
            self.unrecorded.append(
                {"gpo": guid, "revision": revision, "reason": reason[:300]}
            )

    def snapshot(self, guid: str, revision: int) -> tuple[str, dict[str, Any]] | None:
        row = self.conn.execute(
            "SELECT created_at, snapshot_json FROM revisions WHERE gpo_guid=? AND revision=?",
            (guid, revision),
        ).fetchone()
        if row is None:
            return None
        try:
            return str(row[0]), json.loads(row[1])
        except ValueError as error:
            self.note(guid, revision, f"snapshot is not JSON: {error}")
            return None

    def keys_at(self, guid: str, revision: int) -> set[tuple[str, ...]] | None:
        loaded = self.snapshot(guid, revision)
        if loaded is None:
            return None
        try:
            return _legacy_keys(_load_collections(guid, loaded[1]))
        except _Unloadable as error:
            self.note(guid, revision, str(error))
            return None

    def verified_at_creation(
        self, guid: str, visiting: frozenset[str] = frozenset()
    ) -> set[tuple[str, ...]]:
        """The legacy keys of revision 1 recorded as imported (see the module doc).

        Revision 1 is read first, and nothing else unless it is an import or a
        fork: a GPO that was neither records nothing whatever its history holds.
        """
        if guid in self.first_verified:
            return self.first_verified[guid]
        verified: set[tuple[str, ...]] = set()
        first = self.snapshot(guid, 1)
        kind = _creation(guid, first[1]) if first is not None else "none"
        if first is not None and kind != "none":
            keys = self.keys_at(guid, 1)
            if keys and kind == "import":
                verified = keys
            elif keys and kind == "fork":
                parent = _norm_guid(first[1].get("source_guid"))
                if parent in self.guids and parent != guid and parent not in visiting:
                    parent_verified = self.verified_at_creation(parent, visiting | {guid})
                    row = self.conn.execute(
                        "SELECT MAX(revision) FROM revisions WHERE gpo_guid=? AND created_at<=?",
                        (parent, first[0]),
                    ).fetchone()
                    if parent_verified and row is not None and row[0] is not None:
                        at_fork = self.keys_at(parent, int(row[0]))
                        if at_fork is not None:
                            verified = keys & at_fork & parent_verified
        self.first_verified[guid] = verified
        return verified


_MAX_NOTES = 200


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
    """Run once per workspace, inside the caller's transaction.

    Never fatal for stored data (DeepSeek final-r14 B1): a snapshot the
    current model cannot load, or loads only with problems, records nothing
    and is noted with its reason; the head and history stay readable, and a
    refusal comes only at export (`validation` reports the load problems).
    """
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
    unrecorded: list[dict[str, Any]] = []
    history = _History(conn, unrecorded)
    for guid in sorted(history.guids):
        try:
            _migrate_gpo(conn, history, guid)
        except Exception as error:  # one GPO's history never blocks the workspace
            history.note(guid, 0, f"{type(error).__name__}: {error}")
    conn.execute(
        "INSERT OR REPLACE INTO workspace_meta(key, value) VALUES (?, ?)", (META_KEY, META_VALUE)
    )
    if unrecorded:
        conn.execute(
            "INSERT OR REPLACE INTO workspace_meta(key, value) VALUES (?, ?)",
            (UNRECORDED_KEY, json.dumps(unrecorded, sort_keys=True)),
        )


def _migrate_gpo(conn: sqlite3.Connection, history: _History, guid: str) -> None:
    verified = history.verified_at_creation(guid)
    if not verified:
        return  # nothing recorded: every snapshot of this GPO stays as it is
    rows = conn.execute(
        "SELECT revision FROM revisions WHERE gpo_guid=? ORDER BY revision", (guid,)
    ).fetchall()
    for (revision,) in rows:
        loaded = history.snapshot(guid, int(revision))
        if loaded is None:
            continue
        data = loaded[1]
        try:
            collections = _load_collections(guid, data)
        except _Unloadable as error:
            history.note(guid, int(revision), str(error))
            continue
        if _record(collections, verified, data):
            conn.execute(
                "UPDATE revisions SET snapshot_json=? WHERE gpo_guid=? AND revision=?",
                (json.dumps(data, separators=(",", ":"), sort_keys=True), guid, revision),
            )
    row = conn.execute("SELECT snapshot_json FROM gpos WHERE guid=?", (guid,)).fetchone()
    head = json.loads(row[0])
    try:
        collections = _load_collections(guid, head)
    except _Unloadable as error:
        history.note(guid, 0, str(error))
        return
    if _record(collections, verified, head):
        conn.execute(
            "UPDATE gpos SET snapshot_json=? WHERE guid=?",
            (json.dumps(head, separators=(",", ":"), sort_keys=True), guid),
        )
