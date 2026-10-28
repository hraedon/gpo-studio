"""History selectors outside SQLite's integer range are missing revisions."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.model import NotFoundError, WorkspaceError
from gpo_studio.store import WorkspaceStore

_MIN = -(2**63)
_MAX = 2**63 - 1
_OUTSIDE = [_MIN - 1, _MAX + 1, 2**128, 10**1000, 10**5000]


@pytest.fixture
def policy(tmp_path):
    store = WorkspaceStore(tmp_path / "workspace.db")
    try:
        gpo = store.create_gpo("Synthetic history", identity="analyst", reason="initial draft")
        yield store, gpo
    finally:
        if store._connection is not None:
            store.close()


@pytest.fixture
def client(policy, monkeypatch):
    store, _ = policy
    monkeypatch.setattr(app.state, "store", store, raising=False)
    monkeypatch.setattr(app.state, "owns_store", False, raising=False)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.mark.parametrize(
    "revision", _OUTSIDE, ids=["below-min", "above-max", "128bit", "1001digits", "5001digits"]
)
def test_core_treats_non_native_revision_as_missing(policy, revision):
    store, gpo = policy
    before = tuple(store._connection.iterdump())

    with pytest.raises(NotFoundError):
        store.get_revision(gpo.guid, revision)

    assert tuple(store._connection.iterdump()) == before


def _seed_extreme(store, guid, revision):
    store._connection.execute(
        """INSERT INTO revisions (gpo_guid, revision, actor, reason, created_at, snapshot_json)
           SELECT gpo_guid, ?, actor, ?, created_at, snapshot_json FROM revisions
           WHERE gpo_guid=? AND revision=1""",
        (revision, f"Synthetic native boundary {revision}", guid),
    )
    store._connection.commit()


@pytest.mark.parametrize("revision", [_MIN, _MAX], ids=["minimum", "maximum"])
def test_native_extreme_revision_is_read_exactly(policy, revision):
    store, gpo = policy
    _seed_extreme(store, gpo.guid, revision)

    result = store.get_revision(gpo.guid, revision)

    assert result.revision == revision
    assert result.reason == f"Synthetic native boundary {revision}"


@pytest.mark.parametrize(("outside", "edge"), [(_MIN - 1, _MIN), (_MAX + 1, _MAX)])
def test_non_native_revision_does_not_alias_a_native_boundary(policy, outside, edge):
    store, gpo = policy
    _seed_extreme(store, gpo.guid, edge)

    with pytest.raises(NotFoundError):
        store.get_revision(gpo.guid, outside)

    assert store.get_revision(gpo.guid, edge).revision == edge


@pytest.mark.parametrize("state", ["closed", "degraded"])
def test_workspace_health_guard_precedes_revision_lookup(policy, state):
    store, gpo = policy
    if state == "closed":
        store.close()
    else:
        store._degraded = True

    with pytest.raises(WorkspaceError):
        store.get_revision(gpo.guid, _MAX + 1)


@pytest.mark.parametrize(
    "revision", [_MAX + 1, 2**128, 10**1000], ids=["above-max", "128bit", "1001digits"]
)
@pytest.mark.parametrize("view", ["revision", "revision-diff", "current-diff"])
def test_api_readers_report_missing_revision(client, policy, revision, view):
    store, gpo = policy
    before = tuple(store._connection.iterdump())
    prefix = f"/api/gpos/{gpo.guid}"
    paths = {
        "revision": f"{prefix}/revisions/{revision}",
        "revision-diff": f"{prefix}/revisions/diff?from_revision=1&to_revision={revision}",
        "current-diff": f"{prefix}/diff?against_revision={revision}",
    }

    response = client.get(paths[view])

    assert response.status_code == 404
    assert "message" in response.json()["error"]
    assert tuple(store._connection.iterdump()) == before


@pytest.mark.parametrize(
    "revision",
    [_MIN - 1, _MAX + 1, 2**128, 10**1000],
    ids=["below-min", "above-max", "128bit", "1001digits"],
)
def test_failed_restore_preserves_history_and_a_valid_retry_works(client, policy, revision):
    store, gpo = policy
    before = tuple(store._connection.iterdump())
    body = {"expected_revision": 1, "actor": "analyst", "reason": "restore an existing revision"}

    response = client.post(f"/api/gpos/{gpo.guid}/revisions/{revision}/restore", json=body)

    assert response.status_code == 404
    assert tuple(store._connection.iterdump()) == before
    retry = client.post(f"/api/gpos/{gpo.guid}/revisions/1/restore", json=body)
    assert retry.status_code == 200
    assert store.get_gpo(gpo.guid).revision == 2
    history = store.revisions(gpo.guid)
    assert [item.revision for item in history] == [2, 1]
    assert history[0].actor == body["actor"]
    assert history[0].reason == body["reason"]


@pytest.mark.parametrize(
    "revision", [_MIN, -1, 0, _MAX], ids=["minimum", "negative", "zero", "maximum"]
)
def test_native_missing_revision_keeps_404(client, policy, revision):
    _, gpo = policy

    assert client.get(f"/api/gpos/{gpo.guid}/revisions/{revision}").status_code == 404


def test_valid_history_read_and_comparison_keep_working(client, policy):
    store, gpo = policy
    store.update_metadata(
        gpo.guid, 1, {"description": "Second draft"}, identity="editor", reason="describe policy"
    )

    response = client.get(f"/api/gpos/{gpo.guid}/revisions/2")
    comparison = client.get(f"/api/gpos/{gpo.guid}/revisions/diff?from_revision=1&to_revision=2")

    assert response.status_code == 200
    assert response.json()["revision"] == 2
    assert response.json()["actor"] == "editor"
    assert comparison.status_code == 200
