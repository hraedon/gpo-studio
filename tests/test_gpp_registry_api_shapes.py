"""No accepted GPP Registry payload can make a GPO unreadable (batch-2 review).

A key-only item with ``value: []`` was accepted by the API and validation,
committed, and then crashed every later read of the GPO with a 500: the writer
refused the list, and the GPO payload runs the native-backup check on every
read. This sweeps every registry item shape the API can be handed and holds
two properties: a POST is accepted (201) or refused (422), never a 500; and a
GPO holding an accepted item can always be read, and its artifacts either
produced or refused with a 422.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.store import WorkspaceStore

TYPES = ["", "REG_SZ", "REG_EXPAND_SZ", "REG_BINARY", "REG_DWORD", "REG_QWORD", "REG_MULTI_SZ"]
NAMES = ["", "Value"]
VALUES: list[Any] = ["", [], "x", ["a", "b"], "42", "CAFE"]
DEFAULTS = [False, True]
ACTIONS = ["create", "delete"]


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = WorkspaceStore(tmp_path / "shapes.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as test_client:
        yield test_client
    store.close()


def test_no_accepted_registry_payload_breaks_a_later_read(client: TestClient) -> None:
    accepted = 0
    for index, (registry_type, name, value, default, action) in enumerate(
        itertools.product(TYPES, NAMES, VALUES, DEFAULTS, ACTIONS)
    ):
        shape = (registry_type, name, value, default, action)
        created = client.post("/api/gpos", json={"name": f"shape {index}"})
        assert created.status_code == 201, created.text
        gpo = created.json()["gpo"]
        response = client.post(
            f"/api/gpos/{gpo['guid']}/preferences/registry",
            json={
                "expected_revision": gpo["revision"],
                "actor": "tester",
                "reason": "shape sweep",
                "scope": "computer",
                "registry": {
                    "key": "Software\\GPOStudio\\Shapes",
                    "value": {
                        "name": name,
                        "value": value,
                        "registry_type": registry_type,
                        "default": default,
                        "action": action,
                    },
                },
            },
        )
        assert response.status_code in (201, 422), (shape, response.text)
        if response.status_code != 201:
            continue
        accepted += 1
        read = client.get(f"/api/gpos/{gpo['guid']}")
        assert read.status_code == 200, (shape, read.text)
        for artifact in ("gpmc-backup", "export.zip", "plan.ps1", "publication-plan"):
            produced = client.get(f"/api/gpos/{gpo['guid']}/{artifact}")
            assert produced.status_code in (200, 422), (shape, artifact, produced.text)
    # The control: the sweep reached shapes the API accepts, key-only ones included.
    assert accepted > 20


def test_a_key_only_empty_list_is_stored_as_the_empty_value(client: TestClient) -> None:
    gpo = client.post("/api/gpos", json={"name": "key only"}).json()["gpo"]
    response = client.post(
        f"/api/gpos/{gpo['guid']}/preferences/registry",
        json={
            "expected_revision": gpo["revision"],
            "actor": "tester",
            "reason": "key-only",
            "scope": "computer",
            "registry": {"key": "Software\\K", "value": {"name": "", "value": []}},
        },
    )
    assert response.status_code == 201, response.text
    stored = response.json()["gpo"]["gpp_collections"][0]["registry"][0]["value"]
    assert stored["value"] == ""
    assert client.get(f"/api/gpos/{gpo['guid']}/gpmc-backup").status_code == 200
