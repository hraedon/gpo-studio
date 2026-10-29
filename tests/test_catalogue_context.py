"""Catalogue preconditions bind reviewed policy mappings to their mutations."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

import gpo_studio.api as delivery
import gpo_studio.template_store as templates
from gpo_studio.api import app
from gpo_studio.store import WorkspaceStore

POLICY = "Synthetic.CatalogueContext:SamePolicy"
POLICY_URL = f"/api/admx/policies/{quote(POLICY, safe='')}"


def write_pack(folder: Path, variant: str, namespace: str = "Synthetic.CatalogueContext") -> Path:
    folder.mkdir()
    number = 11 if variant == "A" else 22
    (folder / "synthetic.admx").write_text(
        f'''<policyDefinitions>
<policyNamespaces><target prefix="synthetic" namespace="{namespace}"/></policyNamespaces>
<categories><category name="SyntheticCategory" displayName="$(string.Category)"/></categories>
<policies><policy name="SamePolicy" class="Machine"
 key="Software\\Policies\\Synthetic\\Catalogue{variant}" valueName="Value{variant}"
 displayName="$(string.Policy)"><parentCategory ref="SyntheticCategory"/>
<enabledValue><decimal value="{number}"/></enabledValue>
<disabledValue><decimal value="0"/></disabledValue></policy></policies></policyDefinitions>''',
        encoding="utf-8",
    )
    (folder / "synthetic.adml").write_text(
        f"""<policyDefinitionResources><resources><stringTable>
<string id="Category">Synthetic Category {variant}</string>
<string id="Policy">Synthetic catalogue policy</string>
</stringTable></resources></policyDefinitionResources>""",
        encoding="utf-8",
    )
    return folder


@pytest.fixture
def harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, Path, WorkspaceStore]]:
    first = write_pack(tmp_path / "A", "A")
    write_pack(tmp_path / "B", "B")
    store = WorkspaceStore(tmp_path / "workspace.db")
    monkeypatch.setattr(app.state, "store", store, raising=False)
    monkeypatch.setattr(app.state, "owns_store", False, raising=False)
    monkeypatch.setenv("GPO_STUDIO_ADMX_DIR", str(first))
    monkeypatch.delenv("GPO_STUDIO_TEMPLATE_ROOT", raising=False)
    names = ("admx_catalogue", "template_sources", "catalogue_snapshot")
    for name in names:
        monkeypatch.delattr(app.state, name, raising=False)
    try:
        with TestClient(app) as client:
            yield client, tmp_path, store
    finally:
        store.close()
        for name in names:
            if hasattr(app.state, name):
                delattr(app.state, name)


def token(client: TestClient) -> str:
    revision = client.get("/api/health").json()["catalogue_revision"]
    assert str(UUID(revision)) == revision
    return str(revision)


def activate(client: TestClient, folder: Path, name: str = "default") -> dict[str, Any]:
    response = client.post(
        "/api/templates/ingest", json={"name": name, "kind": "local", "path": str(folder)}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def draft(client: TestClient) -> dict[str, Any]:
    response = client.post("/api/gpos", json={"name": "Synthetic context target"})
    assert response.status_code == 201
    return dict(response.json()["gpo"])


def mutation(client: TestClient, gpo: dict[str, Any], kind: str, **context: Any) -> Any:
    body = {
        "actor": "synthetic-test",
        "reason": "Apply reviewed synthetic policy",
        "expected_revision": gpo["revision"],
        "side": "computer",
        **context,
    }
    if kind == "configure":
        return client.post(POLICY_URL + "/configure", json={**body, "gpo_guid": gpo["guid"]})
    return client.post(
        f"/api/gpos/{gpo['guid']}/bulk-policy-state",
        json={**body, "policy_ids": [POLICY], "target_state": "enabled"},
    )


@pytest.mark.parametrize("kind", ["configure", "bulk"])
def test_stale_catalogue_rejected_without_revision_or_history_change(
    harness: Any, kind: str
) -> None:
    client, folder, _store = harness
    gpo = draft(client)
    previous = token(client)
    active = activate(client, folder / "B")["catalogue_revision"]
    before = client.get(f"/api/gpos/{gpo['guid']}").json()
    history = client.get(f"/api/gpos/{gpo['guid']}/revisions").json()

    response = mutation(client, gpo, kind, expected_catalogue_revision=previous)

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "catalogue_conflict"
    assert error["expected_catalogue_revision"] == previous
    assert error["current_catalogue_revision"] == active
    assert "expected_revision" not in error and "current_revision" not in error
    assert client.get(f"/api/gpos/{gpo['guid']}").json() == before
    assert client.get(f"/api/gpos/{gpo['guid']}/revisions").json() == history


@pytest.mark.parametrize("kind", ["configure", "bulk"])
@pytest.mark.parametrize("send_token", [True, False], ids=["guarded", "legacy"])
def test_current_catalogue_and_legacy_callers_apply_same_mapping(
    harness: Any, kind: str, send_token: bool
) -> None:
    client, _folder, _store = harness
    gpo = draft(client)
    current = token(client)
    context = {"expected_catalogue_revision": current} if send_token else {}
    response = mutation(client, gpo, kind, **context)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["catalogue_revision"] == current
    assert data["gpo"]["revision"] == 2
    setting = data["gpo"]["settings"][0]
    assert setting["key"].endswith("CatalogueA")
    assert setting["value_name"] == "ValueA" and setting["value"] == "11"


def test_all_catalogue_reads_share_token_and_configured_actual_gpo_revision(harness: Any) -> None:
    client, folder, _store = harness
    gpo = draft(client)
    first = token(client)
    paths = [
        "/api/health",
        "/api/admx/search",
        POLICY_URL,
        "/api/admx/categories",
        "/api/admx/categories/tree",
        "/api/templates",
        f"/api/gpos/{gpo['guid']}/configured-settings",
    ]
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)
        assert response.json()["catalogue_revision"] == first
    changed = mutation(client, gpo, "configure", expected_catalogue_revision=first).json()["gpo"]
    configured = client.get(f"/api/gpos/{gpo['guid']}/configured-settings").json()
    assert configured["gpo_revision"] == changed["revision"] == 2
    current = activate(client, folder / "B")["catalogue_revision"]
    assert current != first
    for path in paths:
        assert client.get(path).json()["catalogue_revision"] == current


def test_failed_load_or_merge_does_not_activate_or_rotate_token(
    harness: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, folder, _store = harness
    previous = token(client)
    snapshot = app.state.catalogue_snapshot
    missing = client.post(
        "/api/templates/ingest", json={"name": "default", "path": str(folder / "missing")}
    )
    assert missing.status_code == 422
    assert token(client) == previous
    sources = client.get("/api/templates").json()

    def fail_merge(_sources: Any) -> Any:
        raise RuntimeError("synthetic merge failure")

    monkeypatch.setattr(delivery, "_merge_template_catalogues", fail_merge)
    with pytest.raises(RuntimeError, match="synthetic merge failure"):
        activate(client, folder / "B")
    assert token(client) == previous
    assert app.state.catalogue_snapshot is snapshot
    assert client.get("/api/templates").json() == sources
    assert client.get(POLICY_URL).json()["key"].endswith("CatalogueA")


def test_legacy_state_injection_cannot_be_masked_by_cached_snapshot(
    harness: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _folder, _store = harness
    previous = token(client)
    old = app.state.admx_catalogue
    monkeypatch.setattr(app.state, "admx_catalogue", replace(old, policies=()))
    health = client.get("/api/health").json()
    assert not health["admx_loaded"]
    assert health["catalogue_revision"] != previous
    assert client.get("/api/admx/search").json()["items"] == []


def test_new_lifespan_cannot_reuse_a_previous_startup_token(harness: Any) -> None:
    client, _folder, _store = harness
    previous = token(client)
    # A second lifespan with the same injected state models a process restart
    # without relying on a counter that could reset to the same value.
    with TestClient(app) as restarted:
        current = token(restarted)
    assert current != previous
    assert client.get("/api/health").json()["catalogue_revision"] == current


def test_concurrent_source_additions_merge_latest_complete_sources(
    harness: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, folder, _store = harness
    one = write_pack(folder / "extra-one", "A", "Synthetic.ExtraOne")
    two = write_pack(folder / "extra-two", "B", "Synthetic.ExtraTwo")
    original = templates.ingest_source
    # One ingest runs at a time and a second is refused (409), never queued:
    # an ingest may hold up to the aggregate byte limit. Additions made one
    # after the other must still all end up in the active catalogue.
    started = threading.Event()
    release = threading.Event()

    def load(*args: Any, **kwargs: Any) -> Any:
        started.set()
        assert release.wait(timeout=10)
        return original(*args, **kwargs)

    monkeypatch.setattr(templates, "ingest_source", load)
    with ThreadPoolExecutor(max_workers=1) as executor:
        a = executor.submit(activate, client, one, "extra-one")
        assert started.wait(timeout=5)
        refused = client.post(
            "/api/templates/ingest",
            json={"name": "extra-two", "kind": "local", "path": str(two)},
        )
        release.set()
        first = a.result(timeout=10)["catalogue_revision"]
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "template_ingest_in_progress"
    versions = {first, activate(client, two, "extra-two")["catalogue_revision"]}
    listed = client.get("/api/templates").json()
    policies = client.get("/api/admx/search").json()
    assert listed["count"] == 3 and policies["count"] == 3
    assert {item["name"] for item in listed["items"]} == {"default", "extra-one", "extra-two"}
    assert listed["catalogue_revision"] == policies["catalogue_revision"] == token(client)
    assert listed["catalogue_revision"] in versions
    assert len(versions) == 2


@pytest.mark.parametrize("kind", ["configure", "bulk"])
def test_activation_cannot_cross_guard_resolution_and_store_commit(
    harness: Any, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    client, folder, store = harness
    gpo = draft(client)
    previous = token(client)
    mutation_at_store = threading.Event()
    finish_mutation = threading.Event()
    activation_waiting = threading.Event()
    role = threading.local()
    actual_lock = threading.RLock()

    class ObservedLock:
        def __enter__(self) -> None:
            if getattr(role, "activation", False) and not actual_lock.acquire(blocking=False):
                activation_waiting.set()
                actual_lock.acquire()
            elif not getattr(role, "activation", False):
                actual_lock.acquire()

        def __exit__(self, *_args: Any) -> None:
            actual_lock.release()

    original_load = templates.ingest_source

    def load(*args: Any, **kwargs: Any) -> Any:
        result = original_load(*args, **kwargs)
        role.activation = True
        return result

    method = "replace_selected_settings"
    original_mutation = getattr(store, method)

    def pause_before_commit(*args: Any, **kwargs: Any) -> Any:
        mutation_at_store.set()
        assert finish_mutation.wait(timeout=10)
        return original_mutation(*args, **kwargs)

    monkeypatch.setattr(delivery, "_CATALOGUE_LOCK", ObservedLock())
    monkeypatch.setattr(templates, "ingest_source", load)
    monkeypatch.setattr(store, method, pause_before_commit)
    with ThreadPoolExecutor(max_workers=2) as executor:
        changing = executor.submit(
            mutation, client, gpo, kind, expected_catalogue_revision=previous
        )
        try:
            assert mutation_at_store.wait(timeout=5)
            reloading = executor.submit(activate, client, folder / "B")
            # This event is emitted by a failed nonblocking lock acquisition,
            # proving actual contention rather than assuming ordering by sleep.
            assert activation_waiting.wait(timeout=5)
            assert not reloading.done()
        finally:
            finish_mutation.set()
        saved = changing.result(timeout=10)
        activated = reloading.result(timeout=10)
    assert saved.status_code == 200, saved.text
    assert saved.json()["catalogue_revision"] == previous
    assert saved.json()["gpo"]["settings"][0]["key"].endswith("CatalogueA")
    assert activated["catalogue_revision"] != previous
    assert client.get(POLICY_URL).json()["key"].endswith("CatalogueB")
