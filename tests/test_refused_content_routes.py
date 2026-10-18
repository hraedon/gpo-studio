"""Every artifact route over stored content a current rule refuses (final-r15).

A GPO read tolerantly can hold retained content a later rule refuses
(`gpp.gpp_collection_from_dict_tolerant`). Each content artifact -- the plan,
the Studio ZIP with its ``apply.ps1``, both GPMC backups, the scripts preview
and the publication preview, which digests the files it would copy -- must
refuse such a GPO with an actionable reason before anything is built. The
policy report is the one diagnostic exception: it renders, never with the
retained bytes. No response, refused or not, may carry the refused content.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.gpp import GppCollection, GppGroup, serialize_gpp
from gpo_studio.store import WorkspaceStore

#: A token in every planted fragment, and nowhere else: it must never leave.
MARKER = "RAW-MARKER-7f3e"


def _namespace(group: dict[str, Any]) -> None:
    group["unknown_children"] = [f'<p:foo xmlns:p="urn:example" m="{MARKER}"/>']


def _reserved(group: dict[str, Any]) -> None:
    group["unknown_attrs"] = [["name", MARKER]]


def _malformed(group: dict[str, Any]) -> None:
    group["unknown_children"] = [f'<Broken m="{MARKER}"']


def _native(group: dict[str, Any]) -> None:
    group["native_xml"] = f'<Registry m="{MARKER}"/>'


SHAPES: dict[str, Callable[[dict[str, Any]], None]] = {
    "namespace": _namespace,
    "reserved-name collision": _reserved,
    "malformed child": _malformed,
    "invalid native record": _native,
}

SCRIPTS = {"computer": {"startup": [{"command": "setup.cmd", "parameters": ""}]}}

#: (label, method, path, body). Every content artifact route, and the report.
ROUTES: list[tuple[str, str, str, dict[str, Any] | None]] = [
    ("report.txt", "GET", "/api/gpos/{guid}/report.txt", None),
    ("publication preview", "GET", "/api/gpos/{guid}/publication-plan", None),
    ("plan.ps1", "GET", "/api/gpos/{guid}/plan.ps1", None),
    ("Studio ZIP (apply.ps1)", "GET", "/api/gpos/{guid}/export.zip", None),
    ("GPMC backup", "GET", "/api/gpos/{guid}/gpmc-backup", None),
    ("scripted GPMC backup", "POST", "/api/gpos/{guid}/gpmc-backup-with-scripts", SCRIPTS),
    ("scripts preview", "POST", "/api/gpos/{guid}/gpmc-backup-with-scripts/preview", SCRIPTS),
]


@pytest.fixture
def planted(tmp_path: Path) -> Iterator[Callable[[str], tuple[TestClient, WorkspaceStore, str]]]:
    stores: list[WorkspaceStore] = []

    def plant(shape: str) -> tuple[TestClient, WorkspaceStore, str]:
        path = tmp_path / f"{shape.replace(' ', '-')}.db"
        store = WorkspaceStore(path)
        stores.append(store)
        gpo = store.create_gpo(
            "Refused content",
            identity="t",
            reason="t",
            gpp_collections=(
                GppCollection(
                    scope="computer",
                    groups=(GppGroup(name="G", sid="S-1-5-32-544", id="g"),),
                ),
            ),
        )
        connection = store._connection
        (payload,) = connection.execute(
            "SELECT snapshot_json FROM gpos WHERE guid=?", (gpo.guid,)
        ).fetchone()
        data = json.loads(payload)
        SHAPES[shape](data["gpp_collections"][0]["groups"][0])
        connection.execute(
            "UPDATE gpos SET snapshot_json=? WHERE guid=?",
            (json.dumps(data, separators=(",", ":"), sort_keys=True), gpo.guid),
        )
        connection.commit()
        app.state.store = store
        app.state.owns_store = False
        return TestClient(app, base_url="http://127.0.0.1"), store, gpo.guid

    yield plant
    for store in stores:
        store.close()


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_gpo_reads_and_says_what_is_refused(planted: Any, shape: str) -> None:
    client, _store, guid = planted(shape)
    detail = client.get(f"/api/gpos/{guid}")
    assert detail.status_code == 200, detail.text
    # The GPO payload shows retained content read-only, as it is stored; only
    # artifacts may not carry it.
    codes = {issue["code"] for issue in detail.json()["validation"]}
    assert "stored_preferences_refused" in codes
    capabilities = detail.json()["artifact_capabilities"]
    for name in ("studio_export", "gpmc_export", "powershell_plan", "scripts_export"):
        assert capabilities[name]["enabled"] is False, (shape, name)
    # The diagnostic exception, advertised as what it is.
    assert capabilities["policy_report"]["enabled"] is True


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize(("label", "method", "path", "body"), ROUTES, ids=[r[0] for r in ROUTES])
def test_every_artifact_route_refuses_refused_stored_content(
    planted: Any, shape: str, label: str, method: str, path: str, body: Any
) -> None:
    client, _store, guid = planted(shape)
    response = client.request(method, path.format(guid=guid), json=body)
    assert MARKER not in response.text, (shape, label)
    if label == "report.txt":
        assert response.status_code == 200, response.text
        assert "stored_preferences_refused" in response.text or "refused" in response.text
        return
    assert response.status_code == 422, (shape, label, response.status_code, response.text)
    codes = {issue.get("code") for issue in response.json()["error"].get("issues", [])}
    assert codes & {"stored_preferences_refused", "unwritable_preferences"}, (shape, label, codes)
    assert "artifact_ids" not in response.text and "copy_gpp_xml" not in response.text


def test_a_clean_gpo_still_gets_its_publication_plan(tmp_path: Path) -> None:
    with closing(WorkspaceStore(tmp_path / "clean.db")) as store:
        gpo = store.create_gpo(
            "Clean",
            identity="t",
            reason="t",
            gpp_collections=(
                GppCollection(scope="computer", groups=(GppGroup(name="G", sid="S-1-5-32-544"),)),
            ),
        )
        app.state.store = store
        app.state.owns_store = False
        client = TestClient(app, base_url="http://127.0.0.1")
        plan = client.get(f"/api/gpos/{gpo.guid}/publication-plan")
        assert plan.status_code == 200, plan.text
        assert any(step["operation"] == "copy_gpp_xml" for step in plan.json()["steps"])


def test_a_single_quoted_legacy_raw_or_keeps_its_join_through_a_move(tmp_path: Path) -> None:
    """Sol final-r15 B2, server side: the raw predicate is kept as stored, and
    written with its OR, whatever quoting 1.0.0 stored it with."""
    from gpp_payloads import keep_ilt

    with closing(WorkspaceStore(tmp_path / "quote.db")) as store:
        gpo = store.create_gpo("Quoted", identity="t", reason="t")
        connection = store._connection
        (payload,) = connection.execute(
            "SELECT snapshot_json FROM gpos WHERE guid=?", (gpo.guid,)
        ).fetchone()
        data = json.loads(payload)
        data["gpp_collections"] = [
            {
                "scope": "computer",
                "groups": [
                    {
                        "name": "G",
                        "sid": "S-1-5-32-544",
                        "members": [],
                        "id": "g",
                        "ilt_filter": {
                            "items": [
                                "<FilterBattery bool='OR' not='0'/>",
                                {"type": "ou", "negate": False, "value": "OU=A", "bool_op": "AND"},
                            ]
                        },
                    }
                ],
            }
        ]
        connection.execute(
            "UPDATE gpos SET snapshot_json=? WHERE guid=?",
            (json.dumps(data, separators=(",", ":"), sort_keys=True), gpo.guid),
        )
        connection.commit()
        app.state.store = store
        app.state.owns_store = False
        client = TestClient(app, base_url="http://127.0.0.1")
        served = client.get(f"/api/gpos/{gpo.guid}").json()["gpo"]
        group = served["gpp_collections"][0]["groups"][0]
        ids = keep_ilt(group["ilt_filter"])["items"]
        moved = client.put(
            f"/api/gpos/{gpo.guid}/preferences/groups/g",
            json={
                "actor": "t",
                "reason": "move",
                "scope": "computer",
                "expected_revision": served["revision"],
                "group": {
                    "name": "G",
                    "sid": "S-1-5-32-544",
                    "ilt_filter": {"items": [ids[1], ids[0]]},
                },
            },
        )
        assert moved.status_code == 200, moved.text
        written = serialize_gpp(store.get_gpo(gpo.guid).gpp_collections[0])["Groups/Groups.xml"]
        assert written.index(b"FilterOrgUnit") < written.index(b'FilterBattery bool="OR"')
