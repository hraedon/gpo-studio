"""Invalid claimed actors are request failures, before any workspace write."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Literal, assert_never

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from gpo_studio.api import app
from gpo_studio.model import GPO
from gpo_studio.store import WorkspaceStore

Operation = Literal["create", "edit", "fork", "starter", "derive", "delete_starter", "estate"]
OPERATIONS: tuple[Operation, ...] = (
    "create",
    "edit",
    "fork",
    "starter",
    "derive",
    "delete_starter",
    "estate",
)
IMPORTED_GUID = "11111111-2222-4333-8444-555555555555"
REASON = "Reviewed synthetic operation"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple]:
    with closing(WorkspaceStore(tmp_path / "workspace.db")) as store:
        source = store.create_gpo("Synthetic seed", identity="seed-operator", reason="Seed source")
        starter = store.create_starter_gpo(
            "Synthetic starter seed", identity="seed-operator", reason="Seed starter"
        )
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        # A regression must produce an observable HTTP 500, not escape through
        # the test client's re-raising of server exceptions.
        client = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
        yield client, store, source, starter


def invoke(
    client: TestClient, source: GPO, starter: GPO, operation: Operation, actor: str
) -> Response:
    audit = {"actor": actor, "reason": REASON}
    match operation:
        case "create":
            return client.post("/api/gpos", json={**audit, "name": "Synthetic created"})
        case "edit":
            return client.patch(
                f"/api/gpos/{source.guid}",
                json={
                    **audit,
                    "name": "Synthetic edited",
                    "expected_revision": source.revision,
                },
            )
        case "fork":
            return client.post(
                f"/api/gpos/{source.guid}/fork",
                json={
                    **audit,
                    "name": "Synthetic fork",
                },
            )
        case "starter":
            return client.post("/api/starter-gpos", json={**audit, "name": "Synthetic starter"})
        case "derive":
            return client.post(
                f"/api/starter-gpos/{starter.guid}/derive",
                json={
                    **audit,
                    "name": "Synthetic derived",
                    "expected_revision": starter.revision,
                },
            )
        case "delete_starter":
            return client.request(
                "DELETE",
                f"/api/starter-gpos/{starter.guid}",
                json={
                    **audit,
                    "expected_revision": starter.revision,
                },
            )
        case "estate":
            return client.post(
                "/api/estate/import",
                params=audit,
                json={
                    "kind": "gpo-lens-estate",
                    "gpos": [
                        {
                            "guid": IMPORTED_GUID,
                            "display_name": "Synthetic imported",
                            "domain": "synthetic.test",
                            "settings": [],
                            "links": [],
                        }
                    ],
                },
            )
        case _:
            assert_never(operation)


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("actor", ["   ", "\t\r\n", "\u00a0", "\u2003"])
def test_blank_actor_returns_a_field_error_without_writes_and_valid_retry_works(
    workspace: tuple, operation: Operation, actor: str
) -> None:
    client, store, source, starter = workspace
    before = tuple(store._connection.iterdump())
    bad = invoke(client, source, starter, operation, actor)
    assert bad.status_code == 422, bad.text
    assert any(
        issue["code"] == "invalid_actor" and issue["path"] == "actor"
        for issue in bad.json()["error"]["issues"]
    )
    assert not store._connection.in_transaction
    assert tuple(store._connection.iterdump()) == before

    # Correcting only the actor must allow the original operation, with the
    # factory's established trimming still reflected in its immutable audit.
    good = invoke(client, source, starter, operation, "  synthetic-operator  ")
    if operation == "delete_starter":
        assert good.status_code == 204, good.text
        audit = store._connection.execute(
            "SELECT actor, reason FROM deletion_log WHERE guid=?", (starter.guid,)
        ).fetchone()
        assert tuple(audit) == ("synthetic-operator", REASON)
    else:
        assert good.status_code == (200 if operation in ("edit", "estate") else 201), good.text
        if operation == "estate":
            assert good.json()["imported"] == 1
            guid = IMPORTED_GUID
        else:
            guid = good.json()["gpo"]["guid"]
        revision = store.revisions(guid)[0]
        assert revision.actor == "synthetic-operator" and revision.reason == REASON


def test_unrelated_value_error_remains_an_internal_failure(
    workspace: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store, source, starter = workspace
    before = tuple(store._connection.iterdump())

    def fail(*args: object, **kwargs: object) -> None:
        raise ValueError("Synthetic unrelated store failure")

    monkeypatch.setattr(store, "create_gpo", fail)
    response = invoke(client, source, starter, "create", "synthetic-operator")
    assert response.status_code == 500
    assert tuple(store._connection.iterdump()) == before
