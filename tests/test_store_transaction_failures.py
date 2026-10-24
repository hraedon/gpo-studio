"""A failed write must leave no partial rows or live transaction (WI-089)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Literal, assert_never
from uuid import uuid4

import pytest

from gpo_studio.model import GPO
from gpo_studio.snapshot_documents import RetainedDocument
from gpo_studio.store import WorkspaceStore

Operation = Literal["create", "mutate", "import", "delete"]
OPERATIONS: tuple[Operation, ...] = ("create", "mutate", "import", "delete")


class SyntheticCancellation(BaseException):
    """Exercise cancellation cleanup without interrupting the test process."""


def seed(store: WorkspaceStore, operation: Operation) -> GPO:
    source = store.create_gpo(
        "Synthetic source", identity="synthetic-operator", reason="Seed history"
    )
    if operation == "delete":
        # Ordinary policy deletion is reserved for unreadable stored content.
        data = source.to_stored_dict()
        data["computer_enabled"] = "not a boolean"
        store._connection.execute(
            "UPDATE gpos SET snapshot_json=? WHERE guid=?",
            (json.dumps(data), source.guid),
        )
        store._connection.commit()
        assert store.get_gpo(source.guid).load_problems
    return source


def invoke(
    store: WorkspaceStore,
    source: GPO,
    operation: Operation,
    *,
    identity: str = "synthetic-operator",
    reason: str = "Synthetic write",
) -> None:
    match operation:
        case "create":
            store.create_gpo("Synthetic created", identity=identity, reason=reason)
        case "mutate":
            store.update_metadata(
                source.guid, source.revision, {"description": "Changed"},
                identity=identity, reason=reason,
            )
        case "import":
            result = store.import_baseline_gpos(
                [GPO(guid=str(uuid4()), name="Synthetic imported")],
                identity=identity, reason=reason,
            )
            assert result["imported"] == 1
        case "delete":
            store.delete_gpo(
                source.guid, source.revision, confirm=source.guid, identity=identity, reason=reason
            )
        case _:
            assert_never(operation)


def snapshot(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(connection.iterdump())


def assert_rolled_back(
    store: WorkspaceStore, path: Path, before: tuple[str, ...]
) -> None:
    assert not store._connection.in_transaction
    assert snapshot(store._connection) == before
    with closing(sqlite3.connect(path)) as independent:
        assert snapshot(independent) == before
    following = store.create_gpo(
        "Synthetic next write", identity="synthetic-operator", reason="Valid follow-up"
    )
    assert following.revision == 1
    assert len(store.revisions(following.guid)) == 1


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("failure_type", [OSError, SyntheticCancellation])
def test_python_failures_roll_back_partial_writes_and_allow_the_next_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: Operation,
    failure_type: type[BaseException],
) -> None:
    path = tmp_path / "workspace.db"
    with closing(WorkspaceStore(path)) as store:
        source = seed(store, operation)
        store._connection.execute("CREATE TABLE synthetic_partial_write (marker TEXT)")
        before = snapshot(store._connection)
        error = failure_type("Synthetic failure after a write")
        name = "_archive_rows" if operation == "delete" else "_store_documents"
        original: Callable[..., object] = getattr(store, name)

        def fail_after_write(*args: object, **kwargs: object) -> None:
            original(*args, **kwargs)
            assert store._connection.in_transaction
            store._connection.execute(
                "INSERT INTO synthetic_partial_write VALUES(?)", ("written before failure",)
            )
            raise error

        with monkeypatch.context() as fault:
            fault.setattr(store, name, fail_after_write)
            with pytest.raises(failure_type) as caught:
                invoke(store, source, operation)
        assert caught.value is error
        assert_rolled_back(store, path, before)


@pytest.mark.parametrize("operation", ("create", "import", "delete"))
@pytest.mark.parametrize("field", ("identity", "reason"))
def test_unencodable_audit_text_does_not_poison_store_calls(
    tmp_path: Path, operation: Operation, field: str
) -> None:
    path = tmp_path / "workspace.db"
    with closing(WorkspaceStore(path)) as store:
        source = seed(store, operation)
        before = snapshot(store._connection)
        audit = {"identity": "synthetic-operator", "reason": "Synthetic write"}
        audit[field] = "unencodable-" + chr(0xD800)
        # HTTP models already reject these strings. Direct store callers must
        # still get their original error without leaving a half-written GPO.
        with pytest.raises(UnicodeEncodeError):
            invoke(store, source, operation, **audit)
        assert_rolled_back(store, path, before)


def test_a_later_import_failure_keeps_committed_items_and_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with closing(WorkspaceStore(tmp_path / "workspace.db")) as store:
        first = GPO(guid=str(uuid4()), name="Synthetic first import")
        second = GPO(guid=str(uuid4()), name="Synthetic second import")
        original = store._store_documents

        def interrupt_second(
            guid: str, revision: int, documents: list[RetainedDocument]
        ) -> None:
            original(guid, revision, documents)
            if guid == second.guid:
                raise OSError("Synthetic second-item failure")

        with monkeypatch.context() as fault:
            fault.setattr(store, "_store_documents", interrupt_second)
            with pytest.raises(OSError, match="second-item failure"):
                store.import_baseline_gpos(
                    [first, second], identity="first-operator", reason="First attempt"
                )

        assert not store._connection.in_transaction
        assert [g.guid for g in store.list_gpos()] == [first.guid]
        original_revision = store.revisions(first.guid)
        result = store.import_baseline_gpos(
            [first, second], identity="retry-operator", reason="Retry failed item"
        )
        assert result == {"imported": 1, "skipped": 1, "conflicts": 0, "rejected": 0, "total": 2}
        assert store.revisions(first.guid) == original_revision
        assert store.revisions(second.guid)[0].actor == "retry-operator"
