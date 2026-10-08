"""N1: the parsed fdeploy document is stored once, not once per revision.

``GPO.to_dict()`` is ``asdict``, so every revision snapshot serialized the
whole parsed ``fdeploy`` document -- ``raw_text`` plus every view derived from
it -- which is the O(revisions x document) growth WI-061 removed for retained
XML. The store now reduces the document to its native bytes at the write
boundary, files them in ``retained_documents`` by the SHA-256 of those exact
bytes, and re-parses them at the read boundary. These tests pin that no
consumer of the store can tell: GPOs, revision snapshots, forks and restores
come back exactly as before, and snapshots written in the old inline form
still load.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from gpo_studio.fdeploy import encode_fdeploy, native_digest, read_fdeploy
from gpo_studio.model import GPO, WorkspaceError
from gpo_studio.snapshot_documents import (
    FDEPLOY_DIGEST_FIELD,
    SnapshotDocumentError,
    apply_fdeploy,
    apply_snapshot_documents,
    extract_documents,
    extract_fdeploy,
    extract_snapshot_documents,
    snapshot_digests,
)
from gpo_studio.store import WorkspaceStore, gpo_from_dict

ROOT = Path(__file__).resolve().parents[1]
_FIXTURES = ROOT / "tests" / "fixtures" / "native-folder-redirection-gpmc"
#: Banked in the R3 provenance record; the constant `test_fdeploy.py` binds.
_POLICY_SHA = "71f1026180c4a92ed5bcca3366e5d22b80a64931f663fa079ef5d49cd2450800"


def _policy_bytes() -> bytes:
    """R3's native ``fdeploy1.ini``, rebuilt from its banked transcript."""
    transcript = (_FIXTURES / "fdeploy1.ini.txt").read_text(encoding="ascii")
    _, counters, content = transcript.split("\n", 2)
    size_match = re.search(r"size: (\d+) bytes", counters)
    assert size_match is not None
    native = b"\xff\xfe" + content.replace("\n", "\r\n").encode("utf-16-le")
    assert len(native) == int(size_match.group(1))
    assert hashlib.sha256(native).hexdigest() == _POLICY_SHA
    return native


def _mixed_endings_bytes() -> bytes:
    """A file ``encode_fdeploy`` could not reproduce: LF, CRLF and a bare CR mixed.

    The stored form must keep the exact text, not a normalized spelling, or
    the digest and the bytes would drift from the file that was imported.
    """
    text = (
        "[version]\nversion=100\r\n[Folder_Redirection]\r"
        "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}=s-1-1-0;\r\n"
        "[{FDD39AD0-238F-46AF-ADB4-6C85480369C7}_s-1-1-0]\n"
        "FullPath=\\\\files\\home\\%USERNAME%\\Documents café\r\nFlags=1021"
    )
    return b"\xff\xfe" + text.encode("utf-16-le")


@pytest.fixture
def store(tmp_path: Path) -> Any:
    with closing(WorkspaceStore(tmp_path / "workspace.db")) as workspace:
        yield workspace


def _rows(store: WorkspaceStore, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
    connection = sqlite3.connect(store.path)
    try:
        return connection.execute(sql, params).fetchall()
    finally:
        connection.close()


def _redirected(store: WorkspaceStore, native: bytes, edits: int = 4) -> GPO:
    gpo = store.create_gpo(
        "Redirected", identity="n1", reason="import", fdeploy=read_fdeploy(native)
    )
    for index in range(edits):
        gpo = store.update_metadata(
            gpo.guid, gpo.revision, {"description": f"edit {index}"},
            identity="n1", reason="edit",
        )
    return gpo


def _inline_size(native: bytes) -> int:
    """What one revision used to spend on the document: its whole asdict form."""
    data = GPO(guid="g", name="n", fdeploy=read_fdeploy(native)).to_dict()
    return len(json.dumps(data["fdeploy"], separators=(",", ":"), sort_keys=True))


# ---------------------------------------------------------------------------
# Growth
# ---------------------------------------------------------------------------


def test_revisions_of_one_document_store_it_once(store: WorkspaceStore) -> None:
    native = _policy_bytes()
    gpo = _redirected(store, native, edits=7)

    snapshots = _rows(store, "SELECT snapshot_json FROM revisions ORDER BY revision")
    assert len(snapshots) == 8
    reference = json.dumps(
        {"fdeploy": {FDEPLOY_DIGEST_FIELD: _POLICY_SHA}},
        separators=(",", ":"), sort_keys=True,
    )
    for (snapshot,) in snapshots:
        stored = json.loads(snapshot)
        assert stored["fdeploy"] == {FDEPLOY_DIGEST_FIELD: _POLICY_SHA}
        # No revision carries the text or its views.
        assert "raw_text" not in snapshot
        assert "Folder_Redirection" not in snapshot
    head = _rows(store, "SELECT snapshot_json FROM gpos WHERE guid=?", (gpo.guid,))[0][0]
    assert json.loads(head)["fdeploy"] == {FDEPLOY_DIGEST_FIELD: _POLICY_SHA}

    documents = _rows(store, "SELECT digest, byte_length FROM retained_documents")
    assert documents == [(_POLICY_SHA, len(native))]
    # The reference costs a fixed few dozen bytes; the inline form cost the
    # document several times over (text plus its derived views).
    assert len(reference) < 100 < _inline_size(native) / 4


def test_the_database_does_not_grow_by_the_document_per_revision(tmp_path: Path) -> None:
    """Size scales with distinct documents, not with revisions.

    A synthetic document large enough that one inline copy per revision
    would dwarf page-level noise: forty further revisions must grow the file
    by less than one inline copy.
    """
    folders = "".join(
        f"[{{{index:08X}-0000-0000-0000-000000000000}}_s-1-1-0]\r\n"
        f"FullPath=\\\\files\\redirect\\{index}\\%USERNAME%\r\nFlags=1021\r\n"
        for index in range(200)
    )
    native = encode_fdeploy("[version]\r\nversion=100\r\n[Folder_Redirection]\r\n" + folders)
    inline = _inline_size(native)
    assert inline > 50_000

    db_path = tmp_path / "growth.db"
    with closing(WorkspaceStore(db_path)) as workspace:
        gpo = _redirected(workspace, native, edits=1)
    baseline = db_path.stat().st_size
    with closing(WorkspaceStore(db_path)) as workspace:
        for index in range(40):
            gpo = workspace.update_metadata(
                gpo.guid, gpo.revision, {"description": f"later {index}"},
                identity="n1", reason="edit",
            )
        assert _rows(workspace, "SELECT COUNT(*) FROM retained_documents")[0][0] == 1
    assert db_path.stat().st_size - baseline < inline


def test_forks_share_the_one_copy(store: WorkspaceStore) -> None:
    gpo = _redirected(store, _policy_bytes())
    store.fork_gpo(gpo.guid, "Fork A", identity="n1", reason="fork")
    store.fork_gpo(gpo.guid, "Fork B", identity="n1", reason="fork")
    assert _rows(store, "SELECT COUNT(*) FROM retained_documents")[0][0] == 1
    holders = _rows(
        store, "SELECT COUNT(DISTINCT gpo_guid) FROM snapshot_documents WHERE digest=?",
        (_POLICY_SHA,),
    )[0][0]
    assert holders == 3


# ---------------------------------------------------------------------------
# Exact round trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("native", [_policy_bytes(), _mixed_endings_bytes()],
                         ids=["native-capture", "mixed-line-endings"])
def test_the_stored_bytes_are_the_file_and_rehydrate_the_same_model(
    store: WorkspaceStore, native: bytes,
) -> None:
    expected = read_fdeploy(native)
    gpo = _redirected(store, native)

    (digest, content, length), = _rows(
        store, "SELECT digest, content_base64, byte_length FROM retained_documents"
    )
    # Byte for byte the file, keyed by its own SHA-256 -- the same digest the
    # backup inventory records for it and the report prints.
    assert base64.b64decode(content) == native
    assert length == len(native)
    assert digest == hashlib.sha256(native).hexdigest() == native_digest(expected)[0]

    for reader in (store.get_gpo(gpo.guid),
                   next(g for g in store.list_gpos() if g.guid == gpo.guid)):
        assert reader.fdeploy == expected
        assert reader.fdeploy is not None
        assert reader.fdeploy.raw_text == expected.raw_text
        assert b"\xff\xfe" + reader.fdeploy.raw_text.encode("utf-16-le") == native

    fork = store.fork_gpo(gpo.guid, "Forked", identity="n1", reason="fork")
    assert store.get_gpo(fork.guid).fdeploy == expected
    restored = store.restore_revision(
        gpo.guid, 1, store.get_gpo(gpo.guid).revision, identity="n1", reason="restore",
    )
    assert restored.fdeploy == expected


def test_revision_snapshots_keep_the_shape_the_api_served_before(
    store: WorkspaceStore,
) -> None:
    """``GET /api/gpos/{guid}/revisions/{n}`` serves the snapshot dict as is.

    Rehydration writes the full ``asdict`` form back, so the dict equals what
    the inline encoding stored and read back -- views included.
    """
    native = _policy_bytes()
    gpo = _redirected(store, native)
    expected = json.loads(json.dumps(
        GPO(guid="g", name="n", fdeploy=read_fdeploy(native)).to_dict()["fdeploy"]
    ))
    for revision in store.revisions(gpo.guid):
        assert revision.snapshot["fdeploy"] == expected
        assert gpo_from_dict(revision.snapshot).fdeploy == read_fdeploy(native)
    assert store.get_revision(gpo.guid, 3).snapshot["fdeploy"] == expected


def test_a_gpo_without_a_document_stores_none_and_files_nothing(
    store: WorkspaceStore,
) -> None:
    gpo = store.create_gpo("Plain", identity="n1", reason="create")
    head = json.loads(
        _rows(store, "SELECT snapshot_json FROM gpos WHERE guid=?", (gpo.guid,))[0][0]
    )
    assert head["fdeploy"] is None
    assert _rows(store, "SELECT COUNT(*) FROM retained_documents")[0][0] == 0
    assert store.get_gpo(gpo.guid).fdeploy is None


# ---------------------------------------------------------------------------
# Legacy inline snapshots
# ---------------------------------------------------------------------------


def test_inline_snapshots_written_before_the_change_read_back_unchanged(
    store: WorkspaceStore,
) -> None:
    """A workspace written by the WI-068 batch holds the full asdict form inline.

    No migration rewrites it: the read path accepts both forms, and a
    snapshot with no digest reference is handed to ``gpo_from_dict`` as
    stored. Later revisions of the same GPO use the reference form.
    """
    native = _policy_bytes()
    expected = read_fdeploy(native)
    gpo = _redirected(store, native, edits=1)
    inline = json.loads(json.dumps(
        GPO(guid="g", name="n", fdeploy=expected).to_dict()["fdeploy"]
    ))
    connection = sqlite3.connect(store.path)
    try:
        for table, where in (("gpos", "guid=?"), ("revisions", "gpo_guid=?")):
            for rowid, snapshot in connection.execute(
                f"SELECT rowid, snapshot_json FROM {table} WHERE {where}", (gpo.guid,)
            ).fetchall():
                data = json.loads(snapshot)
                data["fdeploy"] = inline
                connection.execute(
                    f"UPDATE {table} SET snapshot_json=? WHERE rowid=?",
                    (json.dumps(data, separators=(",", ":"), sort_keys=True), rowid),
                )
        connection.execute("DELETE FROM snapshot_documents")
        connection.execute("DELETE FROM retained_documents")
        connection.commit()
    finally:
        connection.close()

    assert store.get_gpo(gpo.guid).fdeploy == expected
    for revision in store.revisions(gpo.guid):
        assert revision.snapshot["fdeploy"] == inline
    assert not store.is_degraded

    # The next write files the document and references it.
    later = store.update_metadata(
        gpo.guid, gpo.revision, {"description": "after"}, identity="n1", reason="edit",
    )
    assert later.fdeploy == expected
    newest = _rows(
        store, "SELECT snapshot_json FROM revisions WHERE gpo_guid=? AND revision=?",
        (gpo.guid, later.revision),
    )[0][0]
    assert json.loads(newest)["fdeploy"] == {FDEPLOY_DIGEST_FIELD: _POLICY_SHA}
    assert store.revisions(gpo.guid)[0].snapshot["fdeploy"] == inline


# ---------------------------------------------------------------------------
# Corruption
# ---------------------------------------------------------------------------


def test_a_missing_retained_document_is_a_degraded_workspace(
    store: WorkspaceStore,
) -> None:
    gpo = _redirected(store, _policy_bytes())
    connection = sqlite3.connect(store.path)
    try:
        connection.execute("DELETE FROM retained_documents")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(WorkspaceError, match=r"retained document .*\(fdeploy\)"):
        store.get_gpo(gpo.guid)
    assert store.is_degraded


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (base64.b64encode(b"\xff\xfe" + "[other]".encode("utf-16-le")).decode(),
         "does not hash to its digest"),
        ("not base64!!", "not valid base64"),
    ],
    ids=["altered-bytes", "invalid-base64"],
)
def test_an_altered_retained_document_is_refused(content: str, message: str) -> None:
    data: dict[str, Any] = {"fdeploy": {FDEPLOY_DIGEST_FIELD: _POLICY_SHA}}
    with pytest.raises(SnapshotDocumentError, match=message):
        apply_fdeploy(data, lambda digest: content)


def test_retained_bytes_that_do_not_decode_are_refused() -> None:
    no_bom = "[version]".encode("utf-16-le")
    digest = hashlib.sha256(no_bom).hexdigest()
    data: dict[str, Any] = {"fdeploy": {FDEPLOY_DIGEST_FIELD: digest}}
    with pytest.raises(SnapshotDocumentError, match="does not parse"):
        apply_fdeploy(data, lambda _: base64.b64encode(no_bom).decode())


@pytest.mark.parametrize(
    ("stored", "message"),
    [
        ({FDEPLOY_DIGEST_FIELD: _POLICY_SHA, "raw_text": "[x]"}, "mixes"),
        ({FDEPLOY_DIGEST_FIELD: ""}, "empty"),
    ],
    ids=["mixed-form", "empty-digest"],
)
def test_a_malformed_reference_is_refused_on_read_and_write(
    stored: dict[str, Any], message: str,
) -> None:
    with pytest.raises(SnapshotDocumentError, match=message):
        apply_fdeploy({"fdeploy": dict(stored)}, lambda _: None)
    with pytest.raises(SnapshotDocumentError, match=message):
        extract_fdeploy({"fdeploy": dict(stored)})


@pytest.mark.parametrize(
    ("stored", "message"),
    [
        ([], "not a serialized document"),
        ({"sections": []}, "no raw_text"),
        ({"raw_text": "\ud800"}, "cannot be encoded"),
    ],
    ids=["not-a-dict", "no-raw-text", "unpaired-surrogate"],
)
def test_extract_refuses_what_it_cannot_store_losslessly(
    stored: object, message: str,
) -> None:
    data: dict[str, Any] = {"fdeploy": stored}
    with pytest.raises(SnapshotDocumentError, match=message):
        extract_fdeploy(data)
    assert data["fdeploy"] == stored


# ---------------------------------------------------------------------------
# Codec
# ---------------------------------------------------------------------------


def test_extract_and_apply_are_inverse_and_idempotent() -> None:
    native = _policy_bytes()
    full = json.loads(json.dumps(
        GPO(guid="g", name="n", fdeploy=read_fdeploy(native)).to_dict()
    ))
    data = json.loads(json.dumps(full))
    documents = extract_snapshot_documents(data)
    assert [d.digest for d in documents] == [_POLICY_SHA]
    assert snapshot_digests(data) == [_POLICY_SHA]
    # Already in reference form: a second pass files nothing and changes nothing.
    assert extract_fdeploy(data) == []
    assert data["fdeploy"] == {FDEPLOY_DIGEST_FIELD: _POLICY_SHA}

    table = {d.digest: d.content_base64 for d in documents}
    apply_snapshot_documents(data, table.get)
    assert data == full
    # Inline documents are left as stored, whatever their views say.
    stale = {"raw_text": full["fdeploy"]["raw_text"], "sections": []}
    legacy: dict[str, Any] = {"fdeploy": dict(stale)}
    apply_fdeploy(legacy, table.get)
    assert legacy["fdeploy"] == stale


def test_the_xml_codec_the_v4_migration_uses_does_not_touch_fdeploy() -> None:
    """``schema._v3_to_v4`` calls ``extract_documents``; its behaviour is frozen."""
    data: dict[str, Any] = {"fdeploy": {"raw_text": "[version]"}}
    assert extract_documents(data) == []
    assert data == {"fdeploy": {"raw_text": "[version]"}}
