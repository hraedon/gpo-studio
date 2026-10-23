"""Reviewed exports bind their preconditions and bytes to one immutable read."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Iterator
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import gpo_studio.api as delivery
from gpo_studio.model import GPO
from gpo_studio.registry_pol import parse
from gpo_studio.store import WorkspaceStore

KINDS = ("export.zip", "plan.ps1", "gpmc-backup")
BUILDERS = dict(
    zip(KINDS, ("checked_studio_bundle", "checked_plan", "checked_gpmc_backup"), strict=True)
)


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[tuple[TestClient, WorkspaceStore, dict]]:
    store = WorkspaceStore(tmp_path / "workspace.db")
    delivery.app.state.store = store
    delivery.app.state.owns_store = False
    with TestClient(delivery.app) as client:
        created = client.post("/api/gpos", json={"name": "Synthetic reviewed export"}).json()
        guid = created["gpo"]["guid"]
        result = client.post(
            f"/api/gpos/{guid}/settings",
            json={
                "expected_revision": 1,
                "setting": {
                    "side": "computer",
                    "hive": "HKLM",
                    "key": r"Software\Policies\SyntheticExportReview",
                    "value_name": "Reviewed",
                    "registry_type": "REG_SZ",
                    "value": "before",
                },
            },
        )
        assert result.status_code == 201
        yield client, store, result.json()
    store.close()


def tokens(payload: dict) -> dict:
    return {
        "expected_revision": payload["gpo"]["revision"],
        "expected_review_hash": payload["review_model_sha256"],
    }


def change(client: TestClient, payload: dict) -> dict:
    setting = dict(payload["gpo"]["settings"][0])
    setting["value"] = "after"
    result = client.put(
        f"/api/gpos/{payload['gpo']['guid']}/settings/{setting['id']}",
        json={"expected_revision": payload["gpo"]["revision"], "setting": setting},
    )
    assert result.status_code == 200
    return result.json()


def assert_artifact(data: bytes, kind: str, expected_revision: int) -> None:
    if kind == "plan.ps1":
        assert "before" in data.decode("utf-8-sig")
        return
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if kind == "export.zip":
            assert json.loads(archive.read("manifest.json"))["gpo"]["revision"] == expected_revision
        pols = [name for name in archive.namelist() if name.lower().endswith("registry.pol")]
        assert pols
        assert any(
            record.value == "before" for name in pols for record in parse(archive.read(name))
        )


@pytest.mark.parametrize("kind", KINDS)
def test_current_review_and_legacy_download_actual_bytes(workspace, kind):
    client, _, payload = workspace
    path = f"/api/gpos/{payload['gpo']['guid']}/{kind}"
    response = client.get(path, params=tokens(payload))
    assert response.status_code == 200
    assert response.headers["x-gpo-studio-guid"] == payload["gpo"]["guid"]
    assert response.headers["x-gpo-studio-revision"] == "2"
    assert response.headers["x-gpo-studio-review-hash"] == payload["review_model_sha256"]
    assert (
        response.headers["x-gpo-studio-artifact-sha256"]
        == hashlib.sha256(response.content).hexdigest()
    )
    assert response.headers["cache-control"] == "no-store"
    assert_artifact(response.content, kind, 2)
    legacy = client.get(path)
    assert legacy.status_code == 200
    assert legacy.content == response.content
    if kind == "plan.ps1":
        assert "content-disposition" not in legacy.headers
    else:
        assert "attachment" in legacy.headers["content-disposition"]


@pytest.mark.parametrize("kind", KINDS)
def test_stale_review_refuses_without_writing_head_or_history(workspace, kind):
    client, store, payload = workspace
    current = change(client, payload)
    guid = payload["gpo"]["guid"]
    before = store._connection.iterdump()
    before = tuple(before)
    response = client.get(f"/api/gpos/{guid}/{kind}", params=tokens(payload))
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "export_review_conflict"
    assert error["current_revision"] == 3
    assert error["current_review_hash"] == current["review_model_sha256"]
    assert tuple(store._connection.iterdump()) == before
    legacy = client.get(f"/api/gpos/{guid}/{kind}")
    assert legacy.status_code == 200
    assert legacy.headers["x-gpo-studio-revision"] == "3"


@pytest.mark.parametrize("kind", KINDS)
def test_matching_content_hash_does_not_bypass_revision_guard(workspace, kind):
    client, _, payload = workspace
    query = tokens(payload)
    query["expected_revision"] = 1
    response = client.get(f"/api/gpos/{payload['gpo']['guid']}/{kind}", params=query)
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["expected_revision"] == 1
    assert error["current_revision"] == 2
    assert error["expected_review_hash"] == error["current_review_hash"]


@pytest.mark.parametrize("kind", KINDS)
def test_same_revision_different_content_is_not_same_review(workspace, tmp_path, monkeypatch, kind):
    client, store, payload = workspace
    original = store.get_gpo(payload["gpo"]["guid"])
    # A replacement workspace really reuses GUID and integer revision, but not
    # content. This exercises persisted ABA without weakening deletion rules.
    replacement = WorkspaceStore(tmp_path / "replacement.db")
    try:
        created = replacement.create_gpo(
            original.name,
            "Synthetic recreated policy",
            guid=original.guid,
            settings=original.settings,
            identity="synthetic-test",
            reason="Recreate fixture",
        )
        replacement.put_setting(
            created.guid,
            1,
            asdict(replace(original.settings[0], value="replacement")),
            setting_id=original.settings[0].id,
            identity="synthetic-test",
            reason="Match old integer revision",
        )
        monkeypatch.setattr(delivery.app.state, "store", replacement)
        response = client.get(f"/api/gpos/{original.guid}/{kind}", params=tokens(payload))
        assert response.status_code == 409
        assert response.json()["error"]["current_revision"] == original.revision
        assert response.json()["error"]["current_review_hash"] != payload["review_model_sha256"]
    finally:
        delivery.app.state.store = store
        replacement.close()


@pytest.mark.parametrize("kind", KINDS)
def test_mutation_between_guard_and_serialization_keeps_reviewed_snapshot(
    workspace, monkeypatch, kind
):
    client, store, payload = workspace
    builder_name = BUILDERS[kind]
    builder = getattr(delivery, builder_name)
    captured: list[GPO] = []
    validate = delivery.validate_gpo
    mutated = False

    def concurrent_validation(gpo):
        nonlocal mutated
        if gpo.guid == payload["gpo"]["guid"] and gpo.revision == 2 and not mutated:
            mutated = True
            # The nested request commits on another worker after the export
            # guard, before validation and serialization have finished.
            change(client, payload)
        return validate(gpo)

    def captured_builder(gpo):
        captured.append(gpo)
        assert mutated
        assert store.get_gpo(gpo.guid).revision == 3
        assert store.get_gpo(gpo.guid).settings[0].value == "after"
        return builder(gpo)

    monkeypatch.setattr(delivery, "validate_gpo", concurrent_validation)
    monkeypatch.setattr(delivery, builder_name, captured_builder)
    response = client.get(f"/api/gpos/{payload['gpo']['guid']}/{kind}", params=tokens(payload))
    assert response.status_code == 200
    assert response.headers["x-gpo-studio-revision"] == "2"
    assert response.headers["x-gpo-studio-review-hash"] == payload["review_model_sha256"]
    assert captured[0].revision == 2
    assert_artifact(response.content, kind, 2)


@pytest.mark.parametrize(
    "query",
    [
        {"expected_revision": 2},
        {"expected_review_hash": "0" * 64},
        {"expected_revision": 0, "expected_review_hash": "0" * 64},
        {"expected_revision": 2, "expected_review_hash": "bad"},
    ],
)
@pytest.mark.parametrize("kind", KINDS)
def test_malformed_or_incomplete_tokens_refuse(workspace, kind, query):
    client, _, payload = workspace
    assert client.get(f"/api/gpos/{payload['gpo']['guid']}/{kind}", params=query).status_code == 422


@pytest.mark.parametrize("kind", KINDS)
def test_current_validation_refusals_remain(workspace, kind):
    client, _, payload = workspace
    setting = dict(payload["gpo"]["settings"][0])
    setting.pop("id")
    duplicate = client.post(
        f"/api/gpos/{payload['gpo']['guid']}/settings",
        json={
            "expected_revision": 2,
            "setting": setting,
        },
    )
    assert duplicate.status_code == 201
    response = client.get(
        f"/api/gpos/{payload['gpo']['guid']}/{kind}", params=tokens(duplicate.json())
    )
    assert response.status_code == 422
    assert "duplicate_setting" in response.text


@pytest.mark.parametrize("kind", ["export.zip", "plan.ps1"])
def test_current_capability_refusals_remain(workspace, kind):
    client, _, payload = workspace
    changed = client.post(
        f"/api/gpos/{payload['gpo']['guid']}/security-filters",
        json={
            "expected_revision": 2,
            "filter": {"principal": r"SYNTHETIC\Workstations", "permission": "read", "deny": True},
        },
    )
    assert changed.status_code == 201
    current = changed.json()
    assert not current["artifact_capabilities"]["studio_export"]["enabled"]
    assert (
        client.get(f"/api/gpos/{payload['gpo']['guid']}/{kind}", params=tokens(current)).status_code
        == 422
    )


@pytest.mark.parametrize("guarded", [False, True], ids=["legacy", "reviewed"])
@pytest.mark.parametrize("kind", KINDS)
def test_unhashable_legacy_text_keeps_actionable_refusal_and_stored_records(
    workspace, kind, guarded
):
    client, store, payload = workspace
    guid = payload["gpo"]["guid"]
    path = f"/api/gpos/{guid}/{kind}"
    query = tokens(payload) if guarded else {}
    assert client.get(path, params=query).status_code == 200

    # Simulate a head written before the text gate. Reads remain tolerant; an
    # export must report the actual field rather than mask it with a hash error.
    row = store._connection.execute(
        "SELECT snapshot_json FROM gpos WHERE guid = ?", (guid,)
    ).fetchone()
    snapshot = json.loads(row["snapshot_json"])
    snapshot["description"] = "legacy\ud800text"
    store._connection.execute(
        "UPDATE gpos SET snapshot_json = ? WHERE guid = ?", (json.dumps(snapshot), guid)
    )
    store._connection.commit()
    before = tuple(store._connection.iterdump())
    read = client.get(f"/api/gpos/{guid}")
    assert read.status_code == 200
    assert read.json()["review_model_sha256"] is None
    assert read.json()["gpo"]["description"] == "legacy\ud800text"
    assert client.get("/api/gpos").status_code == 200

    response = client.get(path, params=query)
    assert response.status_code == 422
    issues = response.json()["error"]["issues"]
    assert any(
        issue["code"] == "text_not_xml_writable" and issue["path"] == "description"
        for issue in issues
    )
    assert "cannot be hashed" not in response.text
    assert tuple(store._connection.iterdump()) == before
