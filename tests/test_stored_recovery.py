"""Recovery from unreadable stored values, and the pre-commit answer (final-r20).

* Sol final-r20 B1: a GPO whose stored values cannot be read refuses every
  export and every edit, and its message promised "re-import it, or restore a
  revision without it". When the value was already in revision 1 neither
  existed: restoring revision 1 is refused like the head, and a re-import as a
  new GPO conflicts on its name. The recoveries are now a revision restore, a
  replacement of the GPO's content from a fresh import of its source backup
  (``replace_guid`` with ``expected_revision``), and deleting the GPO; the
  message names exactly these. The unreadable value here is the one that
  empties its collection (a named loader refusal, DeepSeek final-r20 N1).
* Sol final-r20 B2: the payload a mutation answers with was built before its
  commit and kept in a process-global dictionary; a failed creation could
  evict it, and the route then rebuilt it after the commit. It is now handed
  back through the committing request's own context, and answering a
  committed GPO without it raises.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gpo_studio import api
from gpo_studio import store as store_module
from gpo_studio.api import app
from gpo_studio.model import ConflictError
from gpo_studio.store import WorkspaceStore, _replaced_on_read, committed_answer
from gpo_studio.stored_types import RECOVERIES

NATIVE = Path(__file__).parent / "fixtures" / "native-gpp-gpmc"
AUDIT = {"actor": "t", "reason": "r20"}
#: A family the collection loader does not know: well-typed, so the typed load
#: passes it, and refused by the loader by name -- the collection reads empty.
UNREADABLE = [["Bogus", [0]]]


def _inbox(tmp_path: Path, capture: str) -> Path:
    inbox = tmp_path / "inbox" / capture
    shutil.copytree(NATIVE / capture, inbox)
    return inbox


@pytest.fixture
def workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, WorkspaceStore, Path]]:
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(tmp_path / "inbox"))
    (tmp_path / "inbox").mkdir()
    with closing(WorkspaceStore(tmp_path / "w.db")) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        yield TestClient(app, base_url="http://127.0.0.1"), store, tmp_path


#: The fixture backup's ``GPODomainGuid``.
DOMAIN_GUID = "67c09bd9-0326-42a4-a15f-7d1d878e469e"


def _variant(tmp_path: Path, capture: str, name: str, changes: dict[str, str]) -> Path:
    """A copy of *capture* presented, consistently, as another estate's: the
    same GPO GUID, its manifest and nested bkupInfo.xml both changed (they
    must agree, or the backup is refused before any identity check)."""
    inbox = tmp_path / "inbox" / name.replace(" ", "-")
    shutil.copytree(NATIVE / capture, inbox)
    for document in (inbox / "manifest.xml", *inbox.glob("*/bkupInfo.xml")):
        text = document.read_text(encoding="utf-8")
        for old, new in changes.items():
            assert old in text, (document.name, old)
            text = text.replace(old, new)
        document.write_text(text, encoding="utf-8")
    return inbox


def _import(client: TestClient, inbox: Path, **extra: Any) -> Any:
    return client.post(
        "/api/backups/import", json={"path": str(inbox), **AUDIT, **extra}
    )


def _unreadable_from_revision_1(store: WorkspaceStore, guid: str) -> None:
    """Make every stored revision of *guid* -- the first included -- hold a
    preference collection its loader refuses by name."""
    connection = store._connection
    for table, key in (("gpos", "guid"), ("revisions", "gpo_guid")):
        rows = connection.execute(
            f"SELECT rowid, snapshot_json FROM {table} WHERE {key}=?", (guid,)
        ).fetchall()
        for rowid, payload in rows:
            data = json.loads(payload)
            data["gpp_collections"][0]["root_unknown_positions"] = UNREADABLE
            connection.execute(
                f"UPDATE {table} SET snapshot_json=? WHERE rowid=?",
                (json.dumps(data, separators=(",", ":"), sort_keys=True), rowid),
            )
    connection.commit()


ARTIFACTS = (
    ("GET", "plan.ps1", None),
    ("GET", "export.zip", None),
    ("GET", "gpmc-backup", None),
    ("GET", "publication-plan", None),
    ("POST", "gpmc-backup-with-scripts", {"computer": {"startup": [{"command": "a.cmd"}]}}),
    (
        "POST",
        "gpmc-backup-with-scripts/preview",
        {"computer": {"startup": [{"command": "a.cmd"}]}},
    ),
)


def _blocked_everywhere(client: TestClient, guid: str) -> None:
    detail = client.get(f"/api/gpos/{guid}")
    assert detail.status_code == 200, detail.text[:300]
    issues = [i for i in detail.json()["validation"] if i["code"] == "stored_value_wrong_type"]
    assert issues and all(RECOVERIES in issue["message"] for issue in issues)
    collection = detail.json()["gpo"]["gpp_collections"][0]
    assert collection["drives"] == []  # the empty reading
    for method, route, body in ARTIFACTS:
        refused = client.request(method, f"/api/gpos/{guid}/{route}", json=body)
        assert refused.status_code == 422, (route, refused.text[:200])
    assert client.get(f"/api/gpos/{guid}/report.txt").status_code == 200


def test_an_unreadable_first_revision_is_recovered_by_a_replacing_import(
    workspace: Any,
) -> None:
    client, store, tmp_path = workspace
    inbox = _inbox(tmp_path, "WI01A-DriveMaps-GPMC")
    imported = _import(client, inbox)
    assert imported.status_code == 201, imported.text[:300]
    guid = imported.json()["gpo"]["guid"]
    _unreadable_from_revision_1(store, guid)
    _blocked_everywhere(client, guid)
    # No edit, no fork, no restore of the first revision, no second import.
    rename = client.patch(f"/api/gpos/{guid}", json={**AUDIT, "expected_revision": 1, "name": "X"})
    assert rename.status_code == 422
    assert client.post(f"/api/gpos/{guid}/fork", json={**AUDIT, "name": "F"}).status_code == 422
    restore = client.post(
        f"/api/gpos/{guid}/revisions/1/restore", json={**AUDIT, "expected_revision": 1}
    )
    assert restore.status_code == 422
    assert _import(client, inbox).status_code == 409
    # The import recorded the source GPO's full identity, in revision 1.
    recorded = store.get_revision(guid, 1).snapshot["backup_inventory"]
    assert recorded["source_gpo_guid"] == "f0197e25-3e19-4835-b296-3c35dc069635"
    assert recorded["source_domain"] == "ad.hraedon.com"
    assert recorded["source_domain_guid"] == DOMAIN_GUID
    # The replacement: expected revision, the very same source GPO only.
    assert _import(client, inbox, replace_guid=guid, expected_revision=7).status_code == 409
    for name, foreign in (
        # Sol final-r21 B1: same GPO GUID, another estate's DNS domain.
        ("another DNS domain", {"ad.hraedon.com": "foreign.example.test"}),
        # Same DNS name and GPO GUID, a different domain (a rebuilt forest).
        ("another domain GUID", {DOMAIN_GUID: "11111111-2222-4333-8444-555555555555"}),
    ):
        variant = _variant(tmp_path, "WI01A-DriveMaps-GPMC", name, foreign)
        refused = _import(client, variant, replace_guid=guid, expected_revision=1)
        assert refused.status_code == 422, (name, refused.text[:300])
        codes = {i["code"] for i in refused.json()["error"]["issues"]}
        assert codes == {"replace_source_mismatch"}, name
        assert store.get_gpo(guid).revision == 1, name
    other = _import(client, _inbox(tmp_path, "WI01A-EnvVars-GPMC"), replace_guid=guid,
                    expected_revision=1)
    assert other.status_code == 422, other.text[:300]
    assert {i["code"] for i in other.json()["error"]["issues"]} == {"replace_source_mismatch"}
    assert _import(client, inbox, replace_guid=guid).status_code == 422  # needs the revision
    replaced = _import(client, inbox, replace_guid=guid, expected_revision=1)
    assert replaced.status_code == 200, replaced.text[:300]
    payload = replaced.json()
    assert payload["gpo"]["guid"] == guid and payload["gpo"]["revision"] == 2
    assert not [i for i in payload["validation"] if i["severity"] == "error"]
    head = store.get_gpo(guid)
    assert not _replaced_on_read(head)
    assert head.gpp_collections[0].drives and head.gpp_collections[0].retained_provenance
    for route in ("plan.ps1", "export.zip", "gpmc-backup"):
        assert client.get(f"/api/gpos/{guid}/{route}").status_code == 200, route
    # The first revision is immutable: still as it was stored.
    assert store.get_revision(guid, 1).snapshot["gpp_collections"][0][
        "root_unknown_positions"
    ] == UNREADABLE
    # And now an ordinary edit works again.
    assert client.patch(
        f"/api/gpos/{guid}", json={**AUDIT, "expected_revision": 2, "name": "Edited"}
    ).status_code == 200


def _rows(store: WorkspaceStore, guid: str) -> dict[str, list[tuple[Any, ...]]]:
    """Every stored row of *guid*, as stored."""
    connection = store._connection
    return {
        "gpos": [
            tuple(row)
            for row in connection.execute(
                "SELECT guid, name, revision, snapshot_json, updated_at FROM gpos WHERE guid=?",
                (guid,),
            )
        ],
        "revisions": [
            tuple(row)
            for row in connection.execute(
                "SELECT gpo_guid, revision, actor, reason, created_at, snapshot_json "
                "FROM revisions WHERE gpo_guid=? ORDER BY revision",
                (guid,),
            )
        ],
        "snapshot_documents": [
            tuple(row)
            for row in connection.execute(
                "SELECT gpo_guid, revision, digest FROM snapshot_documents WHERE gpo_guid=? "
                "ORDER BY revision, digest",
                (guid,),
            )
        ],
        "retained_documents": [
            tuple(row)
            for row in connection.execute(
                "SELECT digest, content_base64, byte_length FROM retained_documents WHERE digest "
                "IN (SELECT digest FROM snapshot_documents WHERE gpo_guid=?) ORDER BY digest",
                (guid,),
            )
        ],
    }


def _archived(store: WorkspaceStore, guid: str) -> dict[str, list[tuple[Any, ...]]]:
    connection = store._connection
    return {
        "gpos": [
            tuple(row)
            for row in connection.execute(
                "SELECT guid, name, revision, snapshot_json, updated_at FROM deleted_gpos "
                "WHERE guid=?",
                (guid,),
            )
        ],
        "revisions": [
            tuple(row)
            for row in connection.execute(
                "SELECT gpo_guid, revision, actor, reason, created_at, snapshot_json "
                "FROM deleted_revisions WHERE gpo_guid=? ORDER BY revision",
                (guid,),
            )
        ],
        "snapshot_documents": [
            tuple(row)
            for row in connection.execute(
                "SELECT gpo_guid, revision, digest FROM deleted_snapshot_documents "
                "WHERE gpo_guid=? ORDER BY revision, digest",
                (guid,),
            )
        ],
        "retained_documents": [
            tuple(row)
            for row in connection.execute(
                "SELECT digest, content_base64, byte_length FROM deleted_retained_documents "
                "WHERE digest IN (SELECT digest FROM deleted_snapshot_documents "
                "WHERE gpo_guid=?) ORDER BY digest",
                (guid,),
            )
        ],
    }


def _restore_script() -> list[str]:
    """The restore script of docs/workspace-recovery.md, exactly as written."""
    text = (Path(__file__).resolve().parents[1] / "docs" / "workspace-recovery.md").read_text(
        encoding="utf-8"
    )
    section = text.split("### Putting a deleted GPO back", 1)[1]
    script = section.split("```sql\n", 1)[1].split("```", 1)[0]
    return [statement.strip() for statement in script.split(";") if statement.strip()]


def test_an_unreadable_gpo_is_deleted_only_confirmed_and_archived_whole(
    workspace: Any,
) -> None:
    """The delete recovery is not lossy (final-r20 ruling): it needs the guid
    repeated as ``confirm``, refuses a readable GPO, and archives every stored
    row -- head, every revision, the retained documents -- byte for byte; the
    runbook's restore script puts the GPO back exactly."""
    client, store, tmp_path = workspace
    imported = _import(client, _inbox(tmp_path, "WI01A-DriveMaps-GPMC"))
    guid = imported.json()["gpo"]["guid"]
    clean = client.post("/api/gpos", json={"name": "Clean", **AUDIT}).json()["gpo"]

    def delete(target: str, revision: int, confirm: str | None) -> Any:
        query = "" if confirm is None else f"?confirm={confirm}"
        return client.request(
            "DELETE",
            f"/api/gpos/{target}{query}",
            json={**AUDIT, "expected_revision": revision},
        )

    refused = delete(clean["guid"], 1, clean["guid"])
    assert refused.status_code == 422
    assert {i["code"] for i in refused.json()["error"]["issues"]} == {"gpo_delete_not_a_recovery"}
    _unreadable_from_revision_1(store, guid)
    for confirm in (None, "", clean["guid"], guid.upper()):
        unconfirmed = delete(guid, 1, confirm)
        assert unconfirmed.status_code == 422, confirm
        codes = {i["code"] for i in unconfirmed.json()["error"]["issues"]}
        assert codes == {"delete_confirmation_required"}, confirm
    assert delete(guid, 9, guid).status_code == 409
    before = _rows(store, guid)
    assert before["gpos"] and before["revisions"] and before["retained_documents"]
    assert delete(guid, 1, guid).status_code == 204
    assert client.get(f"/api/gpos/{guid}").status_code == 404
    assert _rows(store, guid) == {key: [] for key in before}
    assert _archived(store, guid) == before  # byte-equal: every column as stored
    ((deletion_id,),) = store._connection.execute(
        "SELECT deletion_id FROM deleted_gpos WHERE guid=?", (guid,)
    ).fetchall()
    # Every archive row carries the deletion's id and time (DeepSeek r21 N4).
    for table in (
        "deleted_gpos",
        "deleted_revisions",
        "deleted_snapshot_documents",
        "deleted_retained_documents",
    ):
        stamps = store._connection.execute(
            f"SELECT DISTINCT deletion_id, deleted_at FROM {table}"
        ).fetchall()
        assert [tuple(stamp) for stamp in stamps] == [
            (deletion_id, store.deletion_log()[0]["deleted_at"])
        ], table
    assert client.get(f"/api/gpos/{clean['guid']}").status_code == 200
    # The runbook's restore puts it back exactly as it was -- and again, as
    # a no-op (Sol r21 N1 / DeepSeek N5).
    for _attempt in range(2):
        _restore(store, deletion_id)
        assert _rows(store, guid) == before
        assert store.deletion_log() == []
    assert client.get(f"/api/gpos/{guid}").status_code == 200


def _restore(store: WorkspaceStore, deletion_id: str) -> None:
    for statement in _restore_script():
        store._connection.execute(statement, {"deletion_id": deletion_id})
    store._connection.commit()


def test_a_restore_never_overwrites_a_different_gpo(workspace: Any) -> None:
    """Not blind replacement: a different row under a restored key fails the
    restore, which changes nothing."""
    client, store, tmp_path = workspace
    guid = _import(client, _inbox(tmp_path, "WI01A-DriveMaps-GPMC")).json()["gpo"]["guid"]
    _unreadable_from_revision_1(store, guid)
    assert client.request(
        "DELETE", f"/api/gpos/{guid}?confirm={guid}", json={**AUDIT, "expected_revision": 1}
    ).status_code == 204
    ((deletion_id,),) = store._connection.execute("SELECT deletion_id FROM deleted_gpos")
    # Another GPO now holds the deleted one's guid (a direct insert: the
    # API never reuses a guid), with other content.
    store._connection.execute(
        "INSERT INTO gpos VALUES(?,?,?,?,?)", (guid, "Other", 1, "{}", "2026-10-18T00:00:00Z")
    )
    store._connection.commit()
    with pytest.raises(sqlite3.IntegrityError):
        for statement in _restore_script():
            store._connection.execute(statement, {"deletion_id": deletion_id})
    store._connection.rollback()
    row = store._connection.execute("SELECT name FROM gpos WHERE guid=?", (guid,)).fetchone()
    assert tuple(row) == ("Other",)


def test_delete_restore_delete_in_the_same_second(
    workspace: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DeepSeek final-r21 N2: the archive keyed by time collided."""
    client, store, tmp_path = workspace
    guid = _import(client, _inbox(tmp_path, "WI01A-DriveMaps-GPMC")).json()["gpo"]["guid"]
    _unreadable_from_revision_1(store, guid)
    before = _rows(store, guid)
    monkeypatch.setattr(store_module, "_now", lambda: "2026-10-18T12:00:00+00:00")
    deletions = []
    for _round in range(2):
        deleted = client.request(
            "DELETE", f"/api/gpos/{guid}?confirm={guid}", json={**AUDIT, "expected_revision": 1}
        )
        assert deleted.status_code == 204, deleted.text[:300]
        ids = {
            row[0]
            for row in store._connection.execute("SELECT deletion_id FROM deleted_gpos")
        }
        (latest,) = ids - set(deletions)
        deletions.append(latest)
        _restore(store, latest)
        assert _rows(store, guid) == before
    assert len(set(deletions)) == 2


def test_a_readable_gpo_is_never_replaced(workspace: Any) -> None:
    """DeepSeek final-r21 N1: replacement is a recovery, like the delete."""
    client, store, tmp_path = workspace
    inbox = _inbox(tmp_path, "WI01A-DriveMaps-GPMC")
    guid = _import(client, inbox).json()["gpo"]["guid"]
    refused = _import(client, inbox, replace_guid=guid, expected_revision=1)
    assert refused.status_code == 422
    assert {i["code"] for i in refused.json()["error"]["issues"]} == {"replace_not_a_recovery"}
    assert store.get_gpo(guid).revision == 1


def test_a_gpo_imported_without_a_recorded_identity_is_not_replaced(workspace: Any) -> None:
    """Strict, not partial: a first import that recorded no domain identity
    (before 1.1.0) cannot prove a backup is of the same GPO."""
    client, store, tmp_path = workspace
    inbox = _inbox(tmp_path, "WI01A-DriveMaps-GPMC")
    guid = _import(client, inbox).json()["gpo"]["guid"]
    _unreadable_from_revision_1(store, guid)
    connection = store._connection
    for table, key in (("gpos", "guid"), ("revisions", "gpo_guid")):
        for rowid, payload in connection.execute(
            f"SELECT rowid, snapshot_json FROM {table} WHERE {key}=?", (guid,)
        ).fetchall():
            data = json.loads(payload)
            for field in ("source_gpo_guid", "source_domain", "source_domain_guid"):
                data["backup_inventory"].pop(field)
            connection.execute(
                f"UPDATE {table} SET snapshot_json=? WHERE rowid=?",
                (json.dumps(data, separators=(",", ":"), sort_keys=True), rowid),
            )
    connection.commit()
    refused = _import(client, inbox, replace_guid=guid, expected_revision=1)
    assert refused.status_code == 422
    codes = {i["code"] for i in refused.json()["error"]["issues"]}
    assert codes == {"replace_source_unrecorded"}
    # Every way a first revision comes to record none is named (DeepSeek r22 N2).
    (message,) = [i["message"] for i in refused.json()["error"]["issues"]]
    assert "before 1.1.0" in message and "GPODomainGuid" in message
    assert store.get_gpo(guid).revision == 1


# ---------------------------------------------------------------------------
# Sol final-r20 B2: the answer is the one built before the commit
# ---------------------------------------------------------------------------


def _counting_builder(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """`_build_gpo_payload`, succeeding once per GPO revision and raising after:
    a second build of a committed answer fails the test."""
    real = api._build_gpo_payload
    calls: dict[str, int] = {}

    def build(gpo: Any) -> dict[str, Any]:
        key = f"{gpo.guid}@{gpo.revision}"
        calls[key] = calls.get(key, 0) + 1
        if calls[key] > 1:
            raise RuntimeError(f"rebuilt {key} after its commit")
        return real(gpo)

    monkeypatch.setattr(api, "_build_gpo_payload", build)
    return calls


def test_a_route_answers_with_the_payload_built_before_its_commit(
    workspace: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store, _tmp = workspace
    target = store.create_gpo("Target", identity="t", reason="t")
    calls = _counting_builder(monkeypatch)
    patched = client.patch(
        f"/api/gpos/{target.guid}", json={**AUDIT, "expected_revision": 1, "name": "Committed"}
    )
    assert patched.status_code == 200, patched.text[:300]
    assert patched.json()["gpo"]["revision"] == 2 and patched.json()["gpo"]["name"] == "Committed"
    assert calls == {f"{target.guid}@2": 1}


def test_failed_creations_cannot_evict_a_committed_answer(
    workspace: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sol's interleaving: the global cache filled with failed creations and
    cleared at 64, so a committed route rebuilt its answer and failed after
    the commit. Failed creations -- in this context and in others -- leave
    nothing behind, and the committed answer is the one built first."""
    _client, store, _tmp = workspace
    store.create_gpo("Taken", identity="t", reason="t")
    target = store.create_gpo("Target", identity="t", reason="t")
    calls = _counting_builder(monkeypatch)

    def fail_to_create(count: int) -> None:
        for _ in range(count):
            with pytest.raises(ConflictError):
                store.create_gpo("Taken", identity="t", reason="t")

    fail_to_create(70)
    committed = store.update_metadata(
        target.guid, 1, {"name": "Committed"}, identity="t", reason="r20"
    )
    fail_to_create(70)
    other = threading.Thread(target=fail_to_create, args=(70,))
    other.start()
    other.join()
    request = SimpleNamespace(state=SimpleNamespace())
    answer = api._gpo_payload(committed, request)  # type: ignore[arg-type]
    assert answer["gpo"]["name"] == "Committed" and answer["gpo"]["revision"] == 2
    assert calls[f"{target.guid}@2"] == 1
    assert store.get_gpo(target.guid).revision == 2


def test_answering_a_committed_gpo_without_its_answer_is_an_error(workspace: Any) -> None:
    _client, store, _tmp = workspace
    target = store.create_gpo("Target", identity="t", reason="t")
    store.update_metadata(target.guid, 1, {"name": "Committed"}, identity="t", reason="r20")
    reread = store.get_gpo(target.guid)  # the same GPO, another object
    with pytest.raises(RuntimeError, match="without the answer built before its commit"):
        committed_answer(reread, api._answer_check)


# ---------------------------------------------------------------------------
# Final-r22: the nested bkupInfo.xml, forks, archive tables of an earlier shape
# ---------------------------------------------------------------------------

INSTANCE = "{E9F0A681-9B36-419E-A16E-C2C59DC44DAD}"


def _nested_variant(tmp_path: Path, name: str, changes: dict[str, str]) -> Path:
    """A copy of the native capture with ONLY its nested
    ``{instance}/bkupInfo.xml`` changed -- the real layout of a native backup."""
    inbox = tmp_path / "inbox" / name.replace(" ", "-")
    shutil.copytree(NATIVE / "WI01A-DriveMaps-GPMC", inbox)
    nested = inbox / INSTANCE / "bkupInfo.xml"
    text = nested.read_text(encoding="utf-8")
    for old, new in changes.items():
        assert old in text, old
        text = text.replace(old, new)
    nested.write_text(text, encoding="utf-8")
    return inbox


NESTED_DISAGREEMENTS = {
    "another domain GUID": {DOMAIN_GUID: "11111111-2222-4333-8444-555555555555"},
    "another DNS domain": {"<![CDATA[ad.hraedon.com]]>": "<![CDATA[foreign.example.test]]>"},
    "another GPO GUID": {
        "F0197E25-3E19-4835-B296-3C35DC069635": "A0197E25-3E19-4835-B296-3C35DC069635"
    },
}


@pytest.mark.parametrize("name", sorted(NESTED_DISAGREEMENTS))
def test_a_backup_whose_nested_bkupinfo_disagrees_is_refused(
    workspace: Any, name: str
) -> None:
    """Sol final-r22 B1: the nested bkupInfo.xml was never read, so one naming
    another domain went unchecked. A backup whose identity documents disagree
    is neither imported nor used as a replacement."""
    client, store, tmp_path = workspace
    nested = (NATIVE / "WI01A-DriveMaps-GPMC" / INSTANCE / "bkupInfo.xml").read_text(
        encoding="utf-8"
    )
    assert "GPODomainGuid" in nested  # the real nested document, with an identity
    guid = _import(client, _inbox(tmp_path, "WI01A-DriveMaps-GPMC")).json()["gpo"]["guid"]
    _unreadable_from_revision_1(store, guid)
    variant = _nested_variant(tmp_path, name, NESTED_DISAGREEMENTS[name])
    store._connection.execute("UPDATE gpos SET name='Renamed' WHERE guid=?", (guid,))
    store._connection.commit()
    fresh = _import(client, variant)
    assert fresh.status_code == 422, (name, fresh.status_code, fresh.text[:300])
    replaced = _import(client, variant, replace_guid=guid, expected_revision=1)
    assert replaced.status_code == 422, (name, replaced.text[:300])
    assert store.get_gpo(guid).revision == 1
    assert len(store.list_gpos()) == 1


def test_a_backup_whose_nested_bkupinfo_agrees_imports() -> None:
    """Control: the capture as GPMC wrote it -- manifest and nested bkupInfo
    agreeing -- reads, with the identity both state."""
    from gpo_studio.backup import read_backup

    (source,) = read_backup(NATIVE / "WI01A-DriveMaps-GPMC").gpos
    assert source.domain_guid == DOMAIN_GUID


def test_a_fork_of_an_import_is_never_replaced_by_its_ancestors_backup(workspace: Any) -> None:
    """DeepSeek final-r22 N1: a fork copies its parent's import record, but its
    first revision is not the import."""
    client, store, tmp_path = workspace
    inbox = _inbox(tmp_path, "WI01A-DriveMaps-GPMC")
    parent = _import(client, inbox).json()["gpo"]["guid"]
    fork = client.post(f"/api/gpos/{parent}/fork", json={**AUDIT, "name": "Fork"})
    assert fork.status_code == 201, fork.text[:300]
    fork_guid = fork.json()["gpo"]["guid"]
    _unreadable_from_revision_1(store, fork_guid)
    # The parent's name is the backup's: rename the parent so a name clash is
    # not what refuses the replacement.
    store._connection.execute("UPDATE gpos SET name='Parent' WHERE guid=?", (parent,))
    store._connection.commit()
    refused = _import(client, inbox, replace_guid=fork_guid, expected_revision=1)
    assert refused.status_code == 422, refused.text[:300]
    assert {i["code"] for i in refused.json()["error"]["issues"]} == {"replace_not_the_import"}
    assert store.get_gpo(fork_guid).revision == 1


#: The archive tables as 5c6ea8a created them.
PRESHAPE = (
    """CREATE TABLE deleted_gpos (
        guid TEXT NOT NULL, name TEXT NOT NULL, revision INTEGER NOT NULL,
        snapshot_json TEXT NOT NULL, updated_at TEXT NOT NULL,
        deleted_at TEXT NOT NULL, PRIMARY KEY (guid, deleted_at))""",
    """CREATE TABLE deleted_revisions (
        gpo_guid TEXT NOT NULL, revision INTEGER NOT NULL, actor TEXT NOT NULL,
        reason TEXT NOT NULL, created_at TEXT NOT NULL, snapshot_json TEXT NOT NULL,
        deleted_at TEXT NOT NULL, PRIMARY KEY (gpo_guid, revision, deleted_at))""",
    """CREATE TABLE deleted_snapshot_documents (
        gpo_guid TEXT NOT NULL, revision INTEGER NOT NULL, digest TEXT NOT NULL,
        deleted_at TEXT NOT NULL, PRIMARY KEY (gpo_guid, revision, digest, deleted_at))""",
    """CREATE TABLE deleted_retained_documents (
        digest TEXT PRIMARY KEY, content_base64 TEXT NOT NULL,
        byte_length INTEGER NOT NULL)""",
)


def _preshape_delete(store: WorkspaceStore, guid: str, deleted_at: str) -> None:
    """Delete *guid* exactly as 5c6ea8a did: archive in the earlier shape,
    log, remove."""
    connection = store._connection
    connection.execute(
        "INSERT INTO deleted_gpos SELECT guid, name, revision, snapshot_json, updated_at, ? "
        "FROM gpos WHERE guid = ?",
        (deleted_at, guid),
    )
    connection.execute(
        "INSERT INTO deleted_revisions SELECT gpo_guid, revision, actor, reason, created_at, "
        "snapshot_json, ? FROM revisions WHERE gpo_guid = ?",
        (deleted_at, guid),
    )
    connection.execute(
        "INSERT INTO deleted_snapshot_documents SELECT gpo_guid, revision, digest, ? "
        "FROM snapshot_documents WHERE gpo_guid = ?",
        (deleted_at, guid),
    )
    connection.execute(
        "INSERT OR IGNORE INTO deleted_retained_documents SELECT digest, content_base64, "
        "byte_length FROM retained_documents WHERE digest IN "
        "(SELECT digest FROM snapshot_documents WHERE gpo_guid = ?)",
        (guid,),
    )
    name = connection.execute("SELECT name FROM gpos WHERE guid=?", (guid,)).fetchone()[0]
    connection.execute(
        "INSERT INTO deletion_log VALUES(?,?,?,?,?,?)", (guid, name, "t", "r", 1, deleted_at)
    )
    connection.execute("DELETE FROM revisions WHERE gpo_guid = ?", (guid,))
    connection.execute("DELETE FROM gpos WHERE guid = ?", (guid,))
    connection.commit()


def _preshape_restore_script() -> list[str]:
    text = (Path(__file__).resolve().parents[1] / "docs" / "workspace-recovery.md").read_text(
        encoding="utf-8"
    )
    section = text.split("### Archive tables set aside", 1)[1]
    script = section.split("```sql\n", 1)[1].split("```", 1)[0]
    return [statement.strip() for statement in script.split(";") if statement.strip()]


def test_archive_tables_of_an_earlier_shape_are_set_aside_and_kept(workspace: Any) -> None:
    """Sol B3 / DeepSeek B1 of round 22: a workspace whose archive tables
    5c6ea8a created could not delete (the old shape stayed, the insert
    failed). A deletion now sets them aside -- kept, never dropped -- and the
    runbook's script restores a GPO from them."""
    client, store, tmp_path = workspace
    old = _import(client, _inbox(tmp_path, "WI01A-DriveMaps-GPMC")).json()["gpo"]["guid"]
    new = _import(client, _inbox(tmp_path, "WI01A-EnvVars-GPMC")).json()["gpo"]["guid"]
    for statement in PRESHAPE:
        store._connection.execute(statement)
    before_old = _rows(store, old)
    _preshape_delete(store, old, "2026-10-17T00:00:00+00:00")
    old_archive = {
        table: [
            tuple(row) for row in store._connection.execute(f"SELECT * FROM {table} ORDER BY 1, 2")
        ]
        for table in (
            "deleted_gpos",
            "deleted_revisions",
            "deleted_snapshot_documents",
            "deleted_retained_documents",
        )
    }
    _unreadable_from_revision_1(store, new)
    deleted = client.request(
        "DELETE", f"/api/gpos/{new}?confirm={new}", json={**AUDIT, "expected_revision": 1}
    )
    assert deleted.status_code == 204, deleted.text[:300]
    for table, rows in old_archive.items():
        kept = [
            tuple(row)
            for row in store._connection.execute(f"SELECT * FROM {table}_preshape_1 ORDER BY 1, 2")
        ]
        assert kept == rows, table
    assert store._connection.execute(
        "SELECT guid FROM deleted_gpos WHERE guid=?", (new,)
    ).fetchone()
    # The set-aside archive still restores its GPO, by the runbook's script.
    for statement in _preshape_restore_script():
        store._connection.execute(
            statement, {"guid": old, "deleted_at": "2026-10-17T00:00:00+00:00"}
        )
    store._connection.commit()
    assert _rows(store, old) == before_old
    # A second set-aside never touches the first.
    for statement in PRESHAPE[:1]:
        store._connection.execute("DROP TABLE deleted_gpos")
        store._connection.execute(statement)
    store._connection.commit()
    _unreadable_from_revision_1(store, old)
    again = client.request(
        "DELETE", f"/api/gpos/{old}?confirm={old}", json={**AUDIT, "expected_revision": 1}
    )
    assert again.status_code == 204, again.text[:300]
    tables = {
        row[0]
        for row in store._connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"deleted_gpos_preshape_1", "deleted_gpos_preshape_2", "deleted_gpos"} <= tables


def test_a_set_aside_name_taken_by_any_schema_object_is_skipped(workspace: Any) -> None:
    """DeepSeek final-r23 N2: only tables were looked at, so an index or view
    holding the set-aside name failed the deletion."""
    client, store, tmp_path = workspace
    guid = _import(client, _inbox(tmp_path, "WI01A-DriveMaps-GPMC")).json()["gpo"]["guid"]
    for statement in PRESHAPE:
        store._connection.execute(statement)
    store._connection.execute(
        "CREATE VIEW Deleted_GPOs_Preshape_1 AS SELECT 1 AS one"  # another case, too
    )
    store._connection.execute(
        "CREATE INDEX deleted_revisions_preshape_1 ON gpos(updated_at)"
    )
    store._connection.commit()
    _unreadable_from_revision_1(store, guid)
    deleted = client.request(
        "DELETE", f"/api/gpos/{guid}?confirm={guid}", json={**AUDIT, "expected_revision": 1}
    )
    assert deleted.status_code == 204, deleted.text[:300]
    names = {
        str(row[0]).casefold()
        for row in store._connection.execute("SELECT name FROM sqlite_master")
    }
    assert {"deleted_gpos_preshape_2", "deleted_revisions_preshape_2"} <= names
    assert not store.is_degraded


def test_the_nested_bkupinfo_is_found_whatever_the_instance_directorys_case(
    workspace: Any,
) -> None:
    """DeepSeek final-r23 N3: a backup made on Windows can name its instance
    directory in another case than its manifest does."""
    client, store, tmp_path = workspace
    variant = _nested_variant(tmp_path, "lower case", NESTED_DISAGREEMENTS["another domain GUID"])
    (variant / INSTANCE).rename(variant / INSTANCE.lower())
    from gpo_studio.backup import BackupError, read_backup

    with pytest.raises(BackupError, match="different domain GUIDs"):
        read_backup(variant)
    agreeing = tmp_path / "inbox" / "agreeing"
    shutil.copytree(NATIVE / "WI01A-DriveMaps-GPMC", agreeing)
    (agreeing / INSTANCE).rename(agreeing / INSTANCE.lower())
    (source,) = read_backup(agreeing).gpos
    assert source.domain_guid == DOMAIN_GUID
    # And its content is found there too, as the import's own read does.
    assert source.content_root is not None and source.backup_inventory is not None
