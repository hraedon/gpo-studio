"""A stored value outside its closed set never makes a route answer 500 (final-r17).

DeepSeek final-r17 B1: an estate import stored ``registry_type="REG_FOO"``,
and the GPO's detail, plan, Studio bundle and GPMC backup all answered 500 --
a writer assumed the set (``KeyError``) or refused it in a way no route
translated. Validation reports every value outside its field's closed set
as an error (`closed_sets`), so an import refuses one and a stored one blocks
the GPO, and no writer runs on it: the artifact routes refuse it by
validation first, and a preference collection holding one has its retained
records marked at load (final-r19; `test_stored_legacy_and_records` plants it
beside retained records, which this corpus has none of).

This fuzz finds every closed-set field of a GPO that holds every kind of
item -- from the annotations, as validation does -- plants a value outside
its set in a STORED GPO, and requests every GPO route: none may answer 500.
"""

from __future__ import annotations

import copy
import dataclasses
import json
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from writer_corpus import synthetic_collections

from gpo_studio.api import app
from gpo_studio.closed_sets import _field_sets
from gpo_studio.model import (
    CseFileEntry,
    CseMetadataEntry,
    GPOLink,
    RegistrySetting,
    SecurityFilter,
    WmiFilter,
)
from gpo_studio.store import WorkspaceStore

OUTSIDE = "OUT-OF-DOMAIN"


def _sample(store: WorkspaceStore) -> str:
    """A draft GPO holding one of every kind of item."""
    return store.create_gpo(
        "Closed sets",
        identity="t",
        reason="t",
        settings=(
            RegistrySetting(
                "s1", "computer", "HKLM", r"Software\Policies\Fuzz", "V", "REG_DWORD", 1
            ),
        ),
        links=(GPOLink(id="l1", target="OU=Fuzz,DC=studio,DC=local"),),
        security_filters=(SecurityFilter(id="f1", principal="STUDIO\\Fuzz", sid="S-1-5-32-545"),),
        wmi_filter=WmiFilter(id="w1", name="Fuzz", query="SELECT * FROM Win32_OperatingSystem"),
        cse_metadata=(
            CseMetadataEntry(
                guid="{00000000-0000-4000-8000-0000000000F1}",
                side="machine",
                files=(CseFileEntry("x.dat", "0" * 64, 1),),
            ),
        ),
        gpp_collections=tuple(synthetic_collections()),
    ).guid


def _closed_paths(
    value: Any, data: Any, path: tuple[Any, ...] = ()
) -> Iterator[tuple[str, tuple[Any, ...]]]:
    """(class.field, path into the stored dict) for every closed-set field."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for name, _closed in _field_sets(type(value)):
            if isinstance(data, dict) and name in data:
                yield f"{type(value).__name__}.{name}", (*path, name)
        for item in dataclasses.fields(value):
            if isinstance(data, dict) and item.name in data:
                yield from _closed_paths(
                    getattr(value, item.name), data[item.name], (*path, item.name)
                )
    elif isinstance(value, (tuple, list)) and isinstance(data, list):
        for index, (entry, entry_data) in enumerate(zip(value, data, strict=False)):
            yield from _closed_paths(entry, entry_data, (*path, index))


@pytest.fixture(scope="module")
def workspace(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[TestClient, WorkspaceStore, str, dict[str, Any], dict[str, tuple[Any, ...]]]]:
    path = Path(tmp_path_factory.mktemp("closed-sets")) / "fuzz.db"
    with closing(WorkspaceStore(path)) as store:
        guid = _sample(store)
        (payload,) = store._connection.execute(
            "SELECT snapshot_json FROM gpos WHERE guid=?", (guid,)
        ).fetchone()
        clean = json.loads(payload)
        fields: dict[str, tuple[Any, ...]] = {}
        for label, field_path in _closed_paths(store.get_gpo(guid), clean):
            fields.setdefault(label, field_path)  # one place per class field
        app.state.store = store
        app.state.owns_store = False
        with TestClient(app, base_url="http://127.0.0.1") as client:
            yield client, store, guid, clean, fields


def _plant(
    store: WorkspaceStore, guid: str, clean: dict[str, Any], field_path: tuple[Any, ...]
) -> int:
    """Store *clean* with OUTSIDE at *field_path* as a new head revision."""
    connection = store._connection
    (revision,) = connection.execute("SELECT revision FROM gpos WHERE guid=?", (guid,)).fetchone()
    data = copy.deepcopy(clean)
    target = data
    for step in field_path[:-1]:
        target = target[step]
    target[field_path[-1]] = OUTSIDE
    data["revision"] = revision + 1
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True)
    connection.execute(
        "UPDATE gpos SET snapshot_json=?, revision=? WHERE guid=?", (payload, revision + 1, guid)
    )
    connection.execute(
        "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
        (guid, revision + 1, "fuzz", "plant", "2026-10-18T00:00:00+00:00", payload),
    )
    connection.commit()
    return revision + 1


def _scratch_store() -> WorkspaceStore:
    """A throwaway workspace under TMPDIR (a ":memory:" store leaves a lock
    file in the working directory)."""
    import tempfile

    return WorkspaceStore(Path(tempfile.mkdtemp(prefix="closed-sets-")) / "w.db")


def _labels() -> list[str]:
    with closing(_scratch_store()) as store:
        guid = _sample(store)
        (payload,) = store._connection.execute(
            "SELECT snapshot_json FROM gpos WHERE guid=?", (guid,)
        ).fetchone()
        labels = {label for label, _ in _closed_paths(store.get_gpo(guid), json.loads(payload))}
    return sorted(labels)


LABELS = _labels()
SCRIPTS = {"computer": {"startup": [{"command": "setup.cmd"}]}}


def test_the_clean_sample_is_valid() -> None:
    """A control: every refusal the fuzz sees comes from the planted value."""
    from gpo_studio.validation import validate_gpo

    with closing(_scratch_store()) as store:
        gpo = store.get_gpo(_sample(store))
        assert [i for i in validate_gpo(gpo) if i.severity == "error"] == []


def test_the_fuzz_reaches_every_closed_set_field() -> None:
    assert len(LABELS) >= 40
    for expected in (
        "RegistrySetting.registry_type",
        "RegistrySetting.action",
        "GppGroup.action",
        "IltPredicate.type",
        "GppCollection.scope",
        "GPO.status",
    ):
        assert expected in LABELS


@pytest.mark.parametrize("label", LABELS)
def test_a_stored_value_outside_its_closed_set_never_answers_500(
    workspace: Any, label: str
) -> None:
    client, store, guid, clean, fields = workspace
    revision = _plant(store, guid, clean, fields[label])
    requests: list[tuple[str, str, Any]] = [
        ("GET", f"/api/gpos/{guid}", None),
        ("GET", "/api/gpos", None),
        ("GET", f"/api/gpos/{guid}/plan.ps1", None),
        ("GET", f"/api/gpos/{guid}/export.zip", None),
        ("GET", f"/api/gpos/{guid}/gpmc-backup", None),
        ("POST", f"/api/gpos/{guid}/gpmc-backup-with-scripts", SCRIPTS),
        ("POST", f"/api/gpos/{guid}/gpmc-backup-with-scripts/preview", SCRIPTS),
        ("GET", f"/api/gpos/{guid}/report.txt", None),
        ("GET", f"/api/gpos/{guid}/publication-plan", None),
        ("GET", f"/api/gpos/{guid}/diff?against_revision=1", None),
        ("GET", f"/api/gpos/{guid}/revisions/diff?from_revision=1&to_revision={revision}", None),
        ("GET", f"/api/gpos/{guid}/revisions/{revision}", None),
        ("POST", "/api/diff", {"baseline": guid, "draft": guid, "observed": guid}),
        (
            "POST",
            "/api/estate/diff",
            {"baseline_guid": guid, "draft_guid": guid, "observed_guid": guid},
        ),
        (
            "PATCH",
            f"/api/gpos/{guid}",
            {
                "actor": "fuzz",
                "reason": "rename",
                "expected_revision": revision,
                "name": f"Closed sets {label}",
            },
        ),
    ]
    detail = client.get(f"/api/gpos/{guid}").json()
    codes = {issue["code"] for issue in detail["validation"]}
    assert codes & {"value_outside_closed_set", "stored_preferences_refused"}, (label, codes)
    for name in ("studio_export", "gpmc_export", "powershell_plan"):
        assert detail["artifact_capabilities"][name]["enabled"] is False, (label, name)
    for method, path, body in requests:
        response = client.request(method, path, json=body)
        assert response.status_code != 500, (label, method, path, response.text[:300])


def test_an_estate_import_refuses_an_unsupported_registry_type(tmp_path: Path) -> None:
    """The way DeepSeek found in: `/api/estate/import` read settings verbatim."""
    with closing(WorkspaceStore(tmp_path / "estate.db")) as store:
        app.state.store = store
        app.state.owns_store = False
        client = TestClient(app, base_url="http://127.0.0.1")
        body = {
            "kind": "gpo-lens-estate",
            "domain": "studio.local",
            "exported_at": "2026-07-12T10:00:00Z",
            "gpos": [
                {
                    "guid": "11111111-2222-3333-4444-555555555555",
                    "display_name": "Baseline",
                    "domain": "studio.local",
                    "computer_enabled": True,
                    "user_enabled": True,
                    "settings": [
                        {
                            "id": "s1",
                            "side": "computer",
                            "hive": "HKLM",
                            "key": "Software\\Policies\\App",
                            "value_name": "Enabled",
                            "registry_type": "REG_FOO",
                            "value": "1",
                            "action": "set",
                            "comment": "",
                        }
                    ],
                    "links": [],
                    "security_filters": [],
                    "wmi_filter": None,
                }
            ],
        }
        response = client.post("/api/estate/import", json=body)
        assert response.status_code == 422, response.text
        assert "value_outside_closed_set" in response.text
        assert store.list_gpos() == []


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("links", [{"id": "l", "target": "OU=A,DC=studio,DC=local", "order": "x"}]),
        ("security_filters", [{"id": "f", "principal": "A", "permission": "bogus"}]),
        (
            "cse_metadata",
            [{"guid": "{00000000-0000-4000-8000-000000000001}", "side": "bogus", "files": "x"}],
        ),
        (
            "settings",
            [
                {
                    "id": "s",
                    "side": "computer",
                    "hive": "HKLM",
                    "key": "K",
                    "value_name": "V",
                    "registry_type": "REG_SZ",
                    "value": "x",
                    "action": "bogus",
                }
            ],
        ),
    ],
)
def test_a_malformed_estate_member_is_a_422(tmp_path: Path, field: str, bad: Any) -> None:
    """DeepSeek final-r17 N1."""
    with closing(WorkspaceStore(tmp_path / "estate.db")) as store:
        app.state.store = store
        app.state.owns_store = False
        client = TestClient(app, base_url="http://127.0.0.1")
        gpo: dict[str, Any] = {
            "guid": "11111111-2222-3333-4444-555555555556",
            "display_name": "Malformed",
            "domain": "studio.local",
            "settings": [],
            "links": [],
            "security_filters": [],
            "wmi_filter": None,
        }
        gpo[field] = bad
        response = client.post(
            "/api/estate/import",
            json={
                "kind": "gpo-lens-estate",
                "domain": "studio.local",
                "exported_at": "2026-07-12T10:00:00Z",
                "gpos": [gpo],
            },
        )
        assert response.status_code == 422, (field, response.status_code, response.text)
        assert store.list_gpos() == []
