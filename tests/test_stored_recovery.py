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
import threading
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gpo_studio import api
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
    # The replacement: expected revision, same source GPO only.
    assert _import(client, inbox, replace_guid=guid, expected_revision=7).status_code == 409
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


def test_an_unreadable_gpo_can_be_deleted_and_only_such_a_gpo(workspace: Any) -> None:
    client, store, tmp_path = workspace
    imported = _import(client, _inbox(tmp_path, "WI01A-DriveMaps-GPMC"))
    guid = imported.json()["gpo"]["guid"]
    clean = client.post("/api/gpos", json={"name": "Clean", **AUDIT}).json()["gpo"]
    refused = client.request(
        "DELETE", f"/api/gpos/{clean['guid']}", json={**AUDIT, "expected_revision": 1}
    )
    assert refused.status_code == 422
    assert {i["code"] for i in refused.json()["error"]["issues"]} == {"gpo_delete_not_a_recovery"}
    _unreadable_from_revision_1(store, guid)
    stale = client.request("DELETE", f"/api/gpos/{guid}", json={**AUDIT, "expected_revision": 9})
    assert stale.status_code == 409
    deleted = client.request("DELETE", f"/api/gpos/{guid}", json={**AUDIT, "expected_revision": 1})
    assert deleted.status_code == 204
    assert client.get(f"/api/gpos/{guid}").status_code == 404
    assert [entry["guid"] for entry in store.deletion_log()] == [guid]
    assert client.get(f"/api/gpos/{clean['guid']}").status_code == 200


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
