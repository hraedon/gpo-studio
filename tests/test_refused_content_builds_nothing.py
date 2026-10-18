"""A GPO holding refused stored content builds no artifact on any payload path
(Sol final-r16 B1).

`GET /api/gpos/{guid}` and every GPO-returning mutation report artifact
capabilities. For a GPO whose stored content a current rule refuses, the
capabilities are already disabled by validation; building an artifact anyway
-- serializing the preferences, assembling a ZIP, digesting a file -- would put
the refused content into bytes, sent or not. A profile hook sees every call to
the writers, whatever name a module imported them under.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import CodeType, FrameType
from typing import Any

import pytest
from test_refused_content_routes import SHAPES, planted  # noqa: F401 (fixture)

from gpo_studio import deterministic_zip, export, gpp, publication

#: Every function that writes, assembles or digests preference content.
_BUILDERS = {
    "gpp.serialize_gpp": gpp.serialize_gpp,
    "gpp.serialize_gpp_unchecked": gpp.serialize_gpp_unchecked,
    "gpp._serialize_gpp_file": gpp._serialize_gpp_file,
    "gpp._family_elements": gpp._family_elements,
    "gpp._written_for": gpp._written_for,
    "export.export_bundle": export.export_bundle,
    "export.gpmc_backup_bundle": export.gpmc_backup_bundle,
    "export.powershell_plan": export.powershell_plan,
    "deterministic_zip.deterministic_zip": deterministic_zip.deterministic_zip,
    "publication.generate_publication_plan": publication.generate_publication_plan,
    "publication._artifact_id_for": publication._artifact_id_for,
}
_CODES: dict[CodeType, str] = {fn.__code__: name for name, fn in _BUILDERS.items()}


@contextmanager
def _calls() -> Iterator[list[str]]:
    seen: list[str] = []

    def profile(frame: FrameType, event: str, _arg: Any) -> None:
        if event == "call":
            name = _CODES.get(frame.f_code)
            if name is not None:
                seen.append(name)

    # TestClient runs the app on its own thread: profile every thread.
    threading.setprofile_all_threads(profile)
    try:
        yield seen
    finally:
        threading.setprofile_all_threads(None)


_AUDIT = {"actor": "t", "reason": "r17"}


def _step(
    client: Any,
    seen_by: str,
    method: str,
    path: str,
    body: Any = None,
    *,
    expect: tuple[int, ...] = (200, 201),
) -> Any:
    with _calls() as seen:
        response = client.request(method, path, json=body)
    assert response.status_code in expect, (
        seen_by,
        method,
        path,
        response.status_code,
        response.text[:300],
    )
    assert seen == [], (seen_by, method, path, seen)
    return response


def _current(client: Any, guid: str) -> dict[str, Any]:
    return client.get(f"/api/gpos/{guid}").json()["gpo"]


def _mutate(
    client: Any, shape: str, guid: str, method: str, path: str, body: dict[str, Any]
) -> Any:
    """One GPO-returning mutation at the current revision; the refused content stays."""
    revision = _current(client, guid)["revision"]
    response = _step(client, shape, method, path, {**_AUDIT, **body, "expected_revision": revision})
    assert "stored_preferences_refused" in response.text, (shape, path)
    return response.json()


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_no_route_builds_an_artifact_of_refused_content(
    planted: Any,  # noqa: F811
    shape: str,
) -> None:
    """Reads, diffs, the report, the refused artifact routes and every kind of
    GPO-returning mutation, while the refused content stays in the GPO."""
    client, store, guid = planted(shape)
    base = f"/api/gpos/{guid}"
    # Reads and the report (the diagnostic exception renders, building nothing).
    for path in (base, "/api/gpos", f"{base}/revisions", f"{base}/report.txt"):
        _step(client, shape, "GET", path)
    # The refused artifact routes refuse before building.
    for method, path, body in (
        ("GET", f"{base}/plan.ps1", None),
        ("GET", f"{base}/export.zip", None),
        ("GET", f"{base}/gpmc-backup", None),
        ("GET", f"{base}/publication-plan", None),
        (
            "POST",
            f"{base}/gpmc-backup-with-scripts",
            {"computer": {"startup": [{"command": "a.cmd"}]}},
        ),
        (
            "POST",
            f"{base}/gpmc-backup-with-scripts/preview",
            {"computer": {"startup": [{"command": "a.cmd"}]}},
        ),
    ):
        _step(client, shape, method, path, body, expect=(422,))
    # Every kind of GPO-returning mutation.
    _mutate(client, shape, guid, "PATCH", base, {"name": "Refused (renamed)"})
    setting = {
        "side": "computer",
        "hive": "HKLM",
        "key": r"Software\Policies\R17",
        "value_name": "V",
        "registry_type": "REG_DWORD",
        "value": "1",
    }
    added = _mutate(client, shape, guid, "POST", f"{base}/settings", {"setting": setting})
    setting_id = added["gpo"]["settings"][-1]["id"]
    _mutate(
        client,
        shape,
        guid,
        "PUT",
        f"{base}/settings/{setting_id}",
        {"setting": {**setting, "value": "2"}},
    )
    _mutate(client, shape, guid, "PATCH", f"{base}/settings/{setting_id}/comment", {"comment": "c"})
    _mutate(client, shape, guid, "DELETE", f"{base}/settings/{setting_id}", {})
    link = {"target": "OU=R17,DC=studio,DC=local"}
    linked = _mutate(client, shape, guid, "POST", f"{base}/links", {"link": link})
    link_id = linked["gpo"]["links"][-1]["id"]
    _mutate(
        client, shape, guid, "PUT", f"{base}/links/{link_id}", {"link": {**link, "enforced": True}}
    )
    _mutate(client, shape, guid, "DELETE", f"{base}/links/{link_id}", {})
    flt = {"principal": "STUDIO\\R17", "sid": "S-1-5-32-545"}
    filtered = _mutate(client, shape, guid, "POST", f"{base}/security-filters", {"filter": flt})
    filter_id = filtered["gpo"]["security_filters"][-1]["id"]
    _mutate(
        client,
        shape,
        guid,
        "PUT",
        f"{base}/security-filters/{filter_id}",
        {"filter": {**flt, "permission": "read"}},
    )
    _mutate(client, shape, guid, "DELETE", f"{base}/security-filters/{filter_id}", {})
    _mutate(
        client,
        shape,
        guid,
        "PUT",
        f"{base}/wmi-filter",
        {"wmi_filter": {"name": "R17", "query": "SELECT * FROM Win32_OperatingSystem"}},
    )
    _mutate(client, shape, guid, "DELETE", f"{base}/wmi-filter", {})
    prefs = f"{base}/preferences"
    grown = _mutate(
        client,
        shape,
        guid,
        "POST",
        f"{prefs}/groups",
        {"scope": "computer", "group": {"name": "Added", "sid": "S-1-5-32-545"}},
    )
    groups = grown["gpo"]["gpp_collections"][0]["groups"]
    added_id = groups[-1]["id"]
    _mutate(
        client,
        shape,
        guid,
        "PUT",
        f"{prefs}/groups/{added_id}",
        {"scope": "computer", "group": {"name": "Added", "sid": "S-1-5-32-546"}},
    )
    _mutate(
        client,
        shape,
        guid,
        "POST",
        f"{prefs}/groups/{added_id}/members",
        {"scope": "computer", "member": {"sid": "S-1-5-32-547", "name": "M"}},
    )
    _mutate(
        client,
        shape,
        guid,
        "POST",
        f"{prefs}/reorder",
        {
            "scope": "computer",
            "kind": "groups",
            "ordered_ids": [groups[-1]["id"], *[g["id"] for g in groups[:-1]]],
        },
    )
    _mutate(
        client,
        shape,
        guid,
        "POST",
        f"{prefs}/groups/g/clone",
        {"scope": "computer", "name": "G (copy)"},
    )
    _mutate(client, shape, guid, "DELETE", f"{prefs}/groups/{added_id}", {"scope": "computer"})
    registry = _mutate(
        client,
        shape,
        guid,
        "POST",
        f"{prefs}/registry",
        {"scope": "computer", "registry": {"key": r"Software\R17"}},
    )
    registry_id = registry["gpo"]["gpp_collections"][0]["registry"][-1]["id"]
    _mutate(client, shape, guid, "DELETE", f"{prefs}/registry/{registry_id}", {"scope": "computer"})
    current = _current(client, guid)["revision"]
    # Restoring a revision that holds refused content is itself refused --
    # by validation, building nothing.
    _step(
        client,
        shape,
        "POST",
        f"{base}/revisions/{current - 1}/restore",
        {**_AUDIT, "expected_revision": current},
        expect=(200, 422),
    )
    _step(
        client,
        shape,
        "POST",
        f"{prefs}/groups/g/restore",
        {
            **_AUDIT,
            "scope": "computer",
            "revision": current - 1,
            "expected_revision": _current(client, guid)["revision"],
        },
        expect=(200, 422),
    )
    # Diffs: current against a clean revision, two revisions, and the GUID-backed
    # three-way and estate diffs (Sol final-r17 B1).
    current = _current(client, guid)["revision"]
    for method, path, body in (
        ("GET", f"{base}/diff?against_revision=1", None),
        ("GET", f"{base}/revisions/diff?from_revision=1&to_revision={current}", None),
        ("GET", f"{base}/revisions/diff?from_revision={current - 1}&to_revision={current}", None),
        ("POST", "/api/diff", {"baseline": guid, "draft": guid, "observed": guid}),
        (
            "POST",
            "/api/estate/diff",
            {"baseline_guid": guid, "draft_guid": guid, "observed_guid": guid},
        ),
    ):
        _step(client, shape, method, path, body)
    # A fork carries the refused content, and builds nothing either.
    _step(client, shape, "POST", f"{base}/fork", {**_AUDIT, "name": "Refused fork"})
    assert store.get_gpo(guid).gpp_collections[0].load_problems


def test_the_spy_sees_a_build(planted: Any) -> None:  # noqa: F811
    """The hook is live: a clean GPO's payload does build its artifacts."""
    from fastapi.testclient import TestClient

    from gpo_studio.api import app
    from gpo_studio.gpp import GppCollection, GppGroup

    client, store, _guid = planted("namespace")
    clean = store.create_gpo(
        "Clean",
        identity="t",
        reason="t",
        gpp_collections=(GppCollection(scope="computer", groups=(GppGroup(name="G"),)),),
    )
    app.state.store = store
    with _calls() as seen:
        assert TestClient(app).get(f"/api/gpos/{clean.guid}").status_code == 200
    assert "gpp.serialize_gpp" in seen and "deterministic_zip.deterministic_zip" in seen
