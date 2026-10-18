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


def _mutations(guid: str, gpo: dict[str, Any]) -> list[tuple[str, str, str, dict[str, Any]]]:
    audit = {"actor": "t", "reason": "r16"}
    return [
        ("rename", "PATCH", f"/api/gpos/{guid}", {**audit, "name": gpo["name"] + " (renamed)"}),
        (
            "add setting",
            "POST",
            f"/api/gpos/{guid}/settings",
            {**audit, "setting": {
                "side": "computer", "hive": "HKLM", "key": r"Software\Policies\R16",
                "value_name": "V", "registry_type": "REG_DWORD", "value": "1",
            }},
        ),
        (
            "add group",
            "POST",
            f"/api/gpos/{guid}/preferences/groups",
            {**audit, "scope": "computer", "group": {"name": "Added", "sid": "S-1-5-32-545"}},
        ),
        (
            "add link",
            "POST",
            f"/api/gpos/{guid}/links",
            {**audit, "link": {"target": "OU=R16,DC=studio,DC=local"}},
        ),
    ]


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_no_payload_path_builds_an_artifact_of_refused_content(
    planted: Any,  # noqa: F811
    shape: str,
) -> None:
    client, _store, guid = planted(shape)
    with _calls() as seen:
        detail = client.get(f"/api/gpos/{guid}")
    assert detail.status_code == 200, detail.text
    assert seen == [], (shape, "GET", seen)
    gpo = detail.json()["gpo"]
    for label, method, path, body in _mutations(guid, gpo):
        with _calls() as seen:
            response = client.request(
                method, path, json={**body, "expected_revision": gpo["revision"]}
            )
        assert response.status_code in (200, 201), (label, response.text)
        assert "stored_preferences_refused" in response.text, label
        assert seen == [], (shape, label, seen)
        gpo = response.json()["gpo"]
    with _calls() as seen:
        assert client.get("/api/gpos").status_code == 200
        assert client.get(f"/api/gpos/{guid}/revisions").status_code == 200
    assert seen == [], (shape, "list", seen)


def test_the_spy_sees_a_build(planted: Any) -> None:  # noqa: F811
    """The hook is live: a clean GPO's payload does build its artifacts."""
    from fastapi.testclient import TestClient

    from gpo_studio.api import app
    from gpo_studio.gpp import GppCollection, GppGroup

    client, store, _guid = planted("namespace")
    clean = store.create_gpo(
        "Clean", identity="t", reason="t",
        gpp_collections=(GppCollection(scope="computer", groups=(GppGroup(name="G"),)),),
    )
    app.state.store = store
    with _calls() as seen:
        assert TestClient(app).get(f"/api/gpos/{clean.guid}").status_code == 200
    assert "gpp.serialize_gpp" in seen and "deterministic_zip.deterministic_zip" in seen
