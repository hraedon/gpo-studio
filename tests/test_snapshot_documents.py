"""WI-061: retained native XML is stored once, not once per revision.

An import retains Backup.xml and gpreport.xml as exact base64 bytes, and
``GPO.to_dict()`` is ``asdict``, so every revision snapshot used to carry its
own full copy of both documents. These tests pin the replacement scheme: the
bytes live once in ``retained_documents`` keyed by the SHA-256 of the decoded
document, snapshots carry digest references, and every store read rehydrates
so no consumer of the API observes the encoding.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from gpo_studio.backup import read_backup
from gpo_studio.backup_inventory import inventory_from_dict
from gpo_studio.model import GPO, BackupInventory, WorkspaceError
from gpo_studio.snapshot_documents import (
    DOCUMENT_FIELDS,
    SnapshotDocumentError,
    apply_documents,
    extract_documents,
)
from gpo_studio.store import WorkspaceStore

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_REBACKUP = (
    ROOT / "docs/plan-033/wp1b-evidence/wi059-20260908/scripts-metadata/rebackup"
)


@pytest.fixture(scope="module")
def inventory() -> BackupInventory:
    """A real Windows-produced inventory: parseable native XML on both sides."""
    source = read_backup(SCRIPTS_REBACKUP).gpos[0]
    assert source.backup_inventory is not None
    assert source.backup_inventory.report_xml_base64, "fixture lost its report"
    return source.backup_inventory


@pytest.fixture
def store(tmp_path: Path) -> WorkspaceStore:
    with closing(WorkspaceStore(tmp_path / "workspace.db")) as workspace:
        yield workspace


def _imported(store: WorkspaceStore, inventory: BackupInventory) -> GPO:
    gpo = store.create_gpo(
        "Imported", identity="wi061", reason="import", backup_inventory=inventory
    )
    for index in range(4):
        gpo = store.update_metadata(
            gpo.guid,
            gpo.revision,
            {"description": f"edit {index}"},
            identity="wi061",
            reason="edit",
        )
    return gpo


def _rows(store: WorkspaceStore, sql: str, params: tuple = ()) -> list:
    connection = sqlite3.connect(store.path)
    try:
        return connection.execute(sql, params).fetchall()
    finally:
        connection.close()


def test_several_revisions_do_not_grow_the_workspace_by_the_inventory(
    inventory: BackupInventory, tmp_path: Path
) -> None:
    """The WI-061 close condition, stated exactly.

    Revisions of an import whose retained documents total ~30KB of base64
    must not each cost a copy. Every stored snapshot stays far smaller than
    one document, the documents table holds each document exactly once, and
    the database file itself grows by less than a single copy of the
    inventory across four further revisions.
    """
    inventory_bytes = len(inventory.backup_xml_base64) + len(
        inventory.report_xml_base64
    )
    db_path = tmp_path / "workspace.db"
    with closing(WorkspaceStore(db_path)) as workspace:
        gpo = _imported(workspace, inventory)
    baseline = db_path.stat().st_size

    with closing(WorkspaceStore(db_path)) as workspace:
        for index in range(4, 8):
            gpo = workspace.update_metadata(
                gpo.guid,
                gpo.revision,
                {"description": f"edit {index}"},
                identity="wi061",
                reason="edit",
            )
        sizes = [
            len(row[0])
            for row in _rows(
                workspace, "SELECT snapshot_json FROM revisions ORDER BY revision"
            )
        ]
        documents = _rows(
            workspace, "SELECT COUNT(*) FROM retained_documents"
        )[0][0]

    assert max(sizes) < inventory_bytes / 2
    assert documents == 2
    assert db_path.stat().st_size - baseline < inventory_bytes


def test_every_read_path_rehydrates_exact_bytes(
    store: WorkspaceStore, inventory: BackupInventory
) -> None:
    gpo = _imported(store, inventory)
    for reader in (
        store.get_gpo(gpo.guid),
        next(g for g in store.list_gpos() if g.guid == gpo.guid),
    ):
        assert reader.backup_inventory == inventory

    revisions = store.revisions(gpo.guid)
    assert len(revisions) == 5
    for revision in revisions:
        round_trip = json.loads(json.dumps(revision.snapshot))
        assert (
            inventory_from_dict(round_trip["backup_inventory"]) == inventory
        ), f"revision {revision.revision} lost its exact retained bytes"

    first = store.get_revision(gpo.guid, 1)
    assert inventory_from_dict(first.snapshot["backup_inventory"]) == inventory

    fork = store.fork_gpo(gpo.guid, "Forked", identity="wi061", reason="fork")
    assert store.get_gpo(fork.guid).backup_inventory == inventory

    restored = store.restore_revision(
        gpo.guid, 1, store.get_gpo(gpo.guid).revision,
        identity="wi061", reason="restore",
    )
    assert restored.backup_inventory == inventory


def test_forks_and_revisions_share_one_copy_of_each_document(
    store: WorkspaceStore, inventory: BackupInventory
) -> None:
    gpo = _imported(store, inventory)
    store.fork_gpo(gpo.guid, "Fork A", identity="wi061", reason="fork")
    store.fork_gpo(gpo.guid, "Fork B", identity="wi061", reason="fork")
    # Two documents, not two per fork or per revision.
    assert _rows(store, "SELECT COUNT(*) FROM retained_documents")[0][0] == 2
    referenced = _rows(
        store, "SELECT COUNT(DISTINCT digest) FROM snapshot_documents"
    )[0][0]
    assert referenced == 2


def test_stored_snapshots_carry_digests_not_bytes(
    store: WorkspaceStore, inventory: BackupInventory
) -> None:
    gpo = _imported(store, inventory)
    head = json.loads(
        _rows(
            store, "SELECT snapshot_json FROM gpos WHERE guid=?", (gpo.guid,)
        )[0][0]
    )
    assert head["backup_inventory"]["backup_xml_base64"] == ""
    digest = head["backup_inventory"]["backup_xml_digest"]
    expected = hashlib.sha256(
        base64.b64decode(inventory.backup_xml_base64)
    ).hexdigest()
    assert digest == expected


def test_deleting_the_only_holder_collects_the_documents(
    store: WorkspaceStore, inventory: BackupInventory
) -> None:
    """The deletion path cascades references and collects orphans.

    Inventory-bearing GPOs are not deletable through the public API today --
    only starter GPOs are -- so this drives the store method directly to pin
    the invariant the side tables exist to keep: a document no snapshot
    references does not outlive its GPOs.
    """
    gpo = _imported(store, inventory)
    # Mark the row as a starter so the store's deletion path accepts it.
    connection = sqlite3.connect(store.path)
    try:
        data = json.loads(
            connection.execute(
                "SELECT snapshot_json FROM gpos WHERE guid=?", (gpo.guid,)
            ).fetchone()[0]
        )
        data["is_starter"] = True
        connection.execute(
            "UPDATE gpos SET snapshot_json=? WHERE guid=?",
            (json.dumps(data, separators=(",", ":"), sort_keys=True), gpo.guid),
        )
        connection.commit()
    finally:
        connection.close()

    store.delete_starter_gpo(
        gpo.guid, store.get_gpo(gpo.guid).revision,
        identity="wi061", reason="delete",
    )
    assert _rows(store, "SELECT COUNT(*) FROM retained_documents")[0][0] == 0
    assert _rows(store, "SELECT COUNT(*) FROM snapshot_documents")[0][0] == 0


def test_missing_document_is_a_degraded_workspace(
    store: WorkspaceStore, inventory: BackupInventory
) -> None:
    gpo = _imported(store, inventory)
    connection = sqlite3.connect(store.path)
    try:
        connection.execute("DELETE FROM retained_documents")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(WorkspaceError, match="retained document"):
        store.get_gpo(gpo.guid)
    assert store.is_degraded


def _reinline_every_snapshot(connection: sqlite3.Connection, guid: str) -> None:
    """Undo the digest encoding on a v4 database, in place, for legacy tests."""
    documents = {
        digest: content
        for digest, content in connection.execute(
            "SELECT digest, content_base64 FROM retained_documents"
        ).fetchall()
    }
    data = json.loads(
        connection.execute(
            "SELECT snapshot_json FROM gpos WHERE guid=?", (guid,)
        ).fetchone()[0]
    )
    apply_documents(data, documents.get)
    connection.execute(
        "UPDATE gpos SET snapshot_json=? WHERE guid=?",
        (json.dumps(data, separators=(",", ":"), sort_keys=True), guid),
    )
    for revision, snapshot in connection.execute(
        "SELECT revision, snapshot_json FROM revisions WHERE gpo_guid=?", (guid,)
    ).fetchall():
        row = json.loads(snapshot)
        apply_documents(row, documents.get)
        connection.execute(
            "UPDATE revisions SET snapshot_json=? WHERE gpo_guid=? AND revision=?",
            (json.dumps(row, separators=(",", ":"), sort_keys=True), guid, revision),
        )


def _legacy_v3_database(path: Path, inventory: BackupInventory) -> str:
    """Write a schema-v3 workspace whose snapshots carry inline base64.

    Returns the snapshot JSON of a GPO with no inventory, which the migration
    must leave byte-identical.
    """
    with closing(WorkspaceStore(path)) as workspace:
        imported = _imported(workspace, inventory)
        plain = workspace.create_gpo("Plain", identity="wi061", reason="create")
        connection = sqlite3.connect(path)
        try:
            plain_snapshot = connection.execute(
                "SELECT snapshot_json FROM gpos WHERE guid=?", (plain.guid,)
            ).fetchone()[0]
            _reinline_every_snapshot(connection, imported.guid)
            connection.execute("DROP TABLE snapshot_documents")
            connection.execute("DROP TABLE retained_documents")
            connection.execute(
                "UPDATE workspace_meta SET value='3' WHERE key='schema_version'"
            )
            connection.commit()
        finally:
            connection.close()
    return plain_snapshot


def test_v3_workspace_migrates_inline_documents_to_the_side_table(
    inventory: BackupInventory, tmp_path: Path
) -> None:
    path = tmp_path / "legacy.db"
    plain_before = _legacy_v3_database(path, inventory)

    with closing(WorkspaceStore(path)) as workspace:
        migrated = next(g for g in workspace.list_gpos() if g.name == "Imported")
        assert migrated.backup_inventory == inventory

        connection = sqlite3.connect(path)
        try:
            version = connection.execute(
                "SELECT value FROM workspace_meta WHERE key='schema_version'"
            ).fetchone()[0]
            documents = connection.execute(
                "SELECT COUNT(*) FROM retained_documents"
            ).fetchone()[0]
            inline = connection.execute(
                "SELECT COUNT(*) FROM gpos WHERE "
                "json_extract(snapshot_json, "
                "'$.backup_inventory.backup_xml_base64') != ''"
            ).fetchall()
            plain_after = connection.execute(
                "SELECT snapshot_json FROM gpos WHERE name='Plain'"
            ).fetchone()[0]
        finally:
            connection.close()
        assert version == "4"
        assert documents == 2
        # No snapshot keeps inline bytes after migration.
        assert inline == [(0,)]
        assert plain_after == plain_before


def test_extract_and_apply_are_inverse_operations(inventory: BackupInventory) -> None:
    data = {
        "backup_inventory": {
            "backup_xml_base64": inventory.backup_xml_base64,
            "report_xml_base64": inventory.report_xml_base64,
            "files": [],
        }
    }
    extracted = extract_documents(data)
    assert len(extracted) == 2
    table = {document.digest: document.content_base64 for document in extracted}
    apply_documents(data, table.get)
    assert data["backup_inventory"]["backup_xml_base64"] == inventory.backup_xml_base64
    assert data["backup_inventory"]["report_xml_base64"] == inventory.report_xml_base64
    for _, digest_field in DOCUMENT_FIELDS:
        assert digest_field not in data["backup_inventory"]


def test_invalid_base64_is_refused_not_silently_emptied() -> None:
    data = {"backup_inventory": {"backup_xml_base64": "not base64!!"}}
    with pytest.raises(SnapshotDocumentError, match="not valid base64"):
        extract_documents(data)
    # The corrupt value is left in place, never swapped for a digest.
    assert data["backup_inventory"]["backup_xml_base64"] == "not base64!!"


def test_apply_refuses_a_digest_with_no_document() -> None:
    data = {
        "backup_inventory": {
            "backup_xml_base64": "",
            "backup_xml_digest": "0" * 64,
        }
    }
    with pytest.raises(SnapshotDocumentError, match="does not hold"):
        apply_documents(data, lambda digest: None)
