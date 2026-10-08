"""Upgrading a workspace written by the released 1.0.0 application, and rolling back.

The fixture under ``tests/fixtures/release-1.0.0-workspace/`` was written by the
``v1.0.0`` tag's own code, installed from its own lockfile and driven through
its HTTP API by ``scripts/generate_release_workspace_fixture.py``. Unlike
``workspace_v0.db``/``workspace_v1.db``, which this repository wrote with SQL
it believes an old release would have written, every byte here came from the
release. ``provenance.json`` records what 1.0.0 served for the same workspace,
and what 1.0.0 did when pointed at the workspace after an upgrade.

The tests hold three claims:

* **Forward, losslessly.** Opening the workspace migrates schema 1 to the
  current schema. Every stored snapshot is byte-identical afterwards, and every
  field 1.0.0 served is served unchanged. Fields added since 1.0.0 appear only
  at their empty defaults: the upgrade invents no content.
* **Known differences are pinned, not hidden.** Three outputs legitimately
  differ from what 1.0.0 produced for the same content. Each has a test below
  that names it, so a change in either direction is a decision rather than a
  surprise.
* **Rollback is a restore, not a downgrade.** 1.0.0 refuses an upgraded
  workspace and a backup of one. The pre-upgrade backup that 1.0.0 wrote is
  the rollback path, and it restores.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import shutil
import sqlite3
import subprocess
import sys
import types
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gpo_studio import __version__, registry_pol
from gpo_studio.api import app
from gpo_studio.schema import SCHEMA_VERSION
from gpo_studio.store import WorkspaceStore
from gpo_studio.workspace_ops import restore_workspace

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "release-1.0.0-workspace"
WORKSPACE = FIXTURE_DIR / "workspace.db"
BACKUP = FIXTURE_DIR / "pre-upgrade-backup.db"
PROVENANCE: dict[str, Any] = json.loads(
    (FIXTURE_DIR / "provenance.json").read_text(encoding="utf-8")
)
BASELINE: dict[str, Any] = PROVENANCE["baseline"]["gpos"]


def _guid_named(fragment: str) -> str:
    matches = [
        guid for guid, entry in BASELINE.items() if fragment in entry["detail"]["gpo"]["name"]
    ]
    assert len(matches) == 1, fragment
    return matches[0]


REGISTRY_GPO = _guid_named("Registry Baseline")
PREFERENCES_GPO = _guid_named("Preferences")


def _snapshot_rows(path: Path) -> dict[str, list[tuple[Any, ...]]]:
    with contextlib.closing(sqlite3.connect(path)) as conn:
        return {
            "gpos": conn.execute(
                "SELECT guid, revision, snapshot_json FROM gpos ORDER BY guid"
            ).fetchall(),
            "revisions": conn.execute(
                "SELECT gpo_guid, revision, actor, reason, created_at, snapshot_json "
                "FROM revisions ORDER BY gpo_guid, revision"
            ).fetchall(),
        }


def _meta(path: Path) -> dict[str, str]:
    with contextlib.closing(sqlite3.connect(path)) as conn:
        return dict(conn.execute("SELECT key, value FROM workspace_meta").fetchall())


@pytest.fixture
def upgraded(tmp_path: Path) -> Iterator[tuple[Path, TestClient]]:
    """A copy of the 1.0.0 workspace, opened (and so migrated) by this tree."""
    path = tmp_path / "workspace.db"
    shutil.copyfile(WORKSPACE, path)
    store = WorkspaceStore(path)
    previous = getattr(app.state, "store", None)
    app.state.store = store
    app.state.owns_store = False
    try:
        with TestClient(app) as client:
            yield path, client
    finally:
        store.close()
        app.state.store = previous


def _is_empty(value: Any) -> bool:
    if isinstance(value, dict):
        return all(_is_empty(item) for item in value.values())
    return value in (None, "", [], False, 0)


def _assert_superset(legacy: Any, current: Any, path: str = "") -> None:
    """Every value 1.0.0 served is unchanged; anything new is an empty default."""
    if isinstance(legacy, dict):
        assert isinstance(current, dict), path
        for key, value in legacy.items():
            assert key in current, f"{path}/{key} was served by 1.0.0 and is gone"
            _assert_superset(value, current[key], f"{path}/{key}")
        for key in current.keys() - legacy.keys():
            assert _is_empty(current[key]), (
                f"{path}/{key} is new since 1.0.0 and holds {current[key]!r}: "
                "the upgrade invented content"
            )
    elif isinstance(legacy, list):
        assert isinstance(current, list) and len(current) == len(legacy), path
        for index, (old, new) in enumerate(zip(legacy, current, strict=True)):
            _assert_superset(old, new, f"{path}[{index}]")
    else:
        assert current == legacy, f"{path}: 1.0.0 served {legacy!r}, now {current!r}"


def _export_registry_pol(client: TestClient, guid: str) -> dict[str, bytes]:
    response = client.get(f"/api/gpos/{guid}/export.zip")
    assert response.status_code == 200, response.text
    bundle = zipfile.ZipFile(io.BytesIO(response.content))
    return {
        name: bundle.read(name)
        for name in sorted(bundle.namelist())
        if name.lower().endswith("registry.pol")
    }


# --- the fixture is what it says it is -------------------------------------


def test_fixture_files_match_their_recorded_digests() -> None:
    for name, digest in PROVENANCE["files"].items():
        assert hashlib.sha256((FIXTURE_DIR / name).read_bytes()).hexdigest() == digest, name


def test_fixture_was_written_by_1_0_0_at_schema_1() -> None:
    assert PROVENANCE["written_by_version"] == "1.0.0"
    meta = _meta(WORKSPACE)
    assert meta["schema_version"] == "1"
    # migrate() stamps app_version on every open, so this is the last writer.
    assert meta["app_version"] == "1.0.0"
    sidecar = json.loads((FIXTURE_DIR / f"{BACKUP.name}.meta.json").read_text("utf-8"))
    assert (sidecar["schema_version"], sidecar["app_version"]) == (1, "1.0.0")
    assert sidecar["backup_db_sha256"] == hashlib.sha256(BACKUP.read_bytes()).hexdigest()


def test_fixture_exercises_more_than_one_gpo_and_a_history() -> None:
    assert len(BASELINE) == 2
    revisions = sum(len(entry["revisions"]["items"]) for entry in BASELINE.values())
    assert revisions >= 10


# --- forward, losslessly --------------------------------------------------


def test_opening_migrates_to_the_current_schema(
    upgraded: tuple[Path, TestClient],
) -> None:
    path, _client = upgraded
    meta = _meta(path)
    assert meta["schema_version"] == str(SCHEMA_VERSION)
    assert meta["app_version"] == __version__


def test_every_stored_snapshot_is_byte_identical_after_the_upgrade(
    upgraded: tuple[Path, TestClient],
) -> None:
    path, _client = upgraded
    assert _snapshot_rows(path) == _snapshot_rows(WORKSPACE)


def test_every_field_1_0_0_served_is_served_unchanged(
    upgraded: tuple[Path, TestClient],
) -> None:
    _path, client = upgraded
    for guid, entry in BASELINE.items():
        detail = client.get(f"/api/gpos/{guid}").json()
        _assert_superset(entry["detail"]["gpo"], detail["gpo"], f"/{guid}/gpo")
        assert detail["validation"] == entry["detail"]["validation"]
        revisions = client.get(f"/api/gpos/{guid}/revisions").json()
        assert revisions == entry["revisions"]
        for item in revisions["items"]:
            response = client.get(f"/api/gpos/{guid}/revisions/{item['revision']}")
            assert response.status_code == 200, (guid, item["revision"])
    listing = client.get("/api/gpos").json()
    _assert_superset(PROVENANCE["baseline"]["list"], listing, "/list")


def test_a_gpo_without_preferences_keeps_its_review_digests(
    upgraded: tuple[Path, TestClient],
) -> None:
    _path, client = upgraded
    detail = client.get(f"/api/gpos/{REGISTRY_GPO}").json()
    legacy = BASELINE[REGISTRY_GPO]["detail"]
    assert detail["policy_semantic_sha256"] == legacy["policy_semantic_sha256"]
    assert detail["review_model_sha256"] == legacy["review_model_sha256"]


def test_exported_registry_pol_holds_exactly_the_records_1_0_0_exported(
    upgraded: tuple[Path, TestClient],
) -> None:
    _path, client = upgraded
    for guid, entry in BASELINE.items():
        legacy = {
            name: base64.b64decode(data)
            for name, data in entry["export_registry_pol_base64"].items()
        }
        current = _export_registry_pol(client, guid)
        assert current.keys() == legacy.keys()
        for name, data in current.items():
            # Same records; this serializer's order. See the pin below.
            assert data == registry_pol.serialize(registry_pol.parse(legacy[name])), (guid, name)


def test_the_workspace_accepts_new_revisions_after_the_upgrade(
    upgraded: tuple[Path, TestClient],
) -> None:
    path, client = upgraded
    revision = BASELINE[REGISTRY_GPO]["detail"]["gpo"]["revision"]
    response = client.post(
        f"/api/gpos/{REGISTRY_GPO}/settings",
        json={
            "actor": "upgrade-test",
            "reason": "first edit after the upgrade",
            "expected_revision": revision,
            "setting": {
                "side": "computer",
                "hive": "HKLM",
                "key": r"SOFTWARE\Policies\StudioFixture\Release",
                "value_name": "AfterUpgrade",
                "registry_type": "REG_DWORD",
                "value": "1",
            },
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["gpo"]["revision"] == revision + 1
    before = _snapshot_rows(WORKSPACE)["revisions"]
    after = _snapshot_rows(path)["revisions"]
    assert set(before) <= set(after)
    assert len(after) == len(before) + 1


# --- known differences from 1.0.0, pinned ----------------------------------


def test_pin_delete_records_now_precede_sets_within_a_key(
    upgraded: tuple[Path, TestClient],
) -> None:
    """6af849e (2026-07-21) orders PReg records delete-all, delete, set per key.

    1.0.0 sorted by value name alone, so its ``**del.Retired`` record sat between
    ``Quota`` and ``Servers``. The same records now open the key. Exactly one of
    the fixture's exported files has a delete action, so exactly one differs.
    """
    _path, client = upgraded
    differing = set()
    for guid, entry in BASELINE.items():
        current = _export_registry_pol(client, guid)
        for name, data in entry["export_registry_pol_base64"].items():
            if current[name] != base64.b64decode(data):
                differing.add((guid, name))
    assert differing == {(REGISTRY_GPO, "Machine/Registry.pol")}
    machine = registry_pol.parse(_export_registry_pol(client, REGISTRY_GPO)["Machine/Registry.pol"])
    assert machine[0].action == "delete"


def test_pin_preference_gpo_digests_change_with_the_canonical_form(
    upgraded: tuple[Path, TestClient],
) -> None:
    """The canonical form carries the Plan 024 preference families, empty.

    No value changes (the superset test above proves it), but the canonical
    document of a GPO with preference items gains empty keys for families 1.0.0
    did not model, so both digests differ from what 1.0.0 recorded.
    """
    _path, client = upgraded
    detail = client.get(f"/api/gpos/{PREFERENCES_GPO}").json()
    legacy = BASELINE[PREFERENCES_GPO]["detail"]
    assert detail["policy_semantic_sha256"] != legacy["policy_semantic_sha256"]
    assert detail["review_model_sha256"] != legacy["review_model_sha256"]


def test_pin_gpp_registry_no_longer_advertises_native_gpmc_export(
    upgraded: tuple[Path, TestClient],
) -> None:
    """1.0.0 offered GPMC backup export for GPP Registry; it is now refused.

    Plan 033 WP-2 made native GPP output an allowlist of four families whose
    extension metadata was captured, and GPP Registry is not one (WI-046). The
    Studio bundle still carries it.
    """
    _path, client = upgraded
    capabilities = client.get(f"/api/gpos/{PREFERENCES_GPO}").json()["artifact_capabilities"]
    legacy = BASELINE[PREFERENCES_GPO]["detail"]["artifact_capabilities"]
    assert legacy["gpmc_export"]["enabled"] is True
    assert capabilities["gpmc_export"]["enabled"] is False
    assert "Registry" in capabilities["gpmc_export"]["reason"]
    assert capabilities["studio_export"]["enabled"] is True


# --- rollback is a restore --------------------------------------------------


def test_the_observations_were_made_against_this_schema() -> None:
    """The recorded refusals describe a schema-N workspace; regenerate on a bump."""
    assert PROVENANCE["upgraded_schema_version"] == SCHEMA_VERSION


def test_1_0_0_refused_to_serve_the_upgraded_workspace() -> None:
    observed = PROVENANCE["rollback_observations"]["legacy_run_on_upgraded_workspace"]
    assert observed["exit_code"] != 0
    expected = (
        f"Workspace schema version {SCHEMA_VERSION} is newer than this version of "
        "GPO Studio supports (1). Upgrade GPO Studio."
    )
    assert any(line.endswith(expected) for line in observed["schema_lines"])


def test_1_0_0_refused_to_restore_a_backup_of_the_upgraded_workspace() -> None:
    observed = PROVENANCE["rollback_observations"]["legacy_restore_of_upgraded_backup"]
    assert observed["exit_code"] != 0
    assert observed["schema_lines"] == [
        f"error: Backup schema version {SCHEMA_VERSION} is newer than this version of "
        "GPO Studio supports (1). Upgrade GPO Studio."
    ]


def test_1_0_0_restored_its_pre_upgrade_backup_and_served_it() -> None:
    observed = PROVENANCE["rollback_observations"]["legacy_restore_of_pre_upgrade_backup"]
    assert observed == {"exit_code": 0, "served_baseline_again": True}


def _load_v1_0_0_schema_module() -> types.ModuleType:
    shallow = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if shallow.returncode != 0 or shallow.stdout.strip() != "false":
        pytest.skip("shallow or absent clone: the v1.0.0 tag is not fetched here")
    source = subprocess.run(
        ["git", "show", "v1.0.0:src/gpo_studio/schema.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    assert source.returncode == 0, (
        "the v1.0.0 tag does not resolve in a full clone: " + source.stderr.decode()
    )
    spec = importlib.util.spec_from_loader("gpo_studio_v1_0_0_schema", loader=None)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop(spec.name, None)
    exec(compile(source.stdout, "v1.0.0:src/gpo_studio/schema.py", "exec"), module.__dict__)
    return module


def test_1_0_0_schema_guard_refuses_the_upgraded_workspace_today(
    upgraded: tuple[Path, TestClient],
) -> None:
    """Re-run 1.0.0's own guard, from the tag, against today's upgrade output.

    The recorded observations above were made once; this re-executes the code
    that made them, so a later migration cannot quietly produce a workspace the
    old release would half-open.
    """
    path, _client = upgraded
    legacy_schema = _load_v1_0_0_schema_module()
    assert legacy_schema.SCHEMA_VERSION == 1
    conn = sqlite3.connect(path)
    try:
        with pytest.raises(legacy_schema.SchemaError, match="is newer than this version"):
            legacy_schema.migrate(conn)
    finally:
        conn.close()
    assert _meta(path)["schema_version"] == str(SCHEMA_VERSION)


def test_the_pre_upgrade_backup_restores_with_this_release_too(tmp_path: Path) -> None:
    """The 1.0.0-written backup and sidecar pass this release's restore checks.

    Restore does not migrate: the file stays at schema 1, so the same file can
    still be handed back to 1.0.0. Opening it is what upgrades it.
    """
    target = tmp_path / "restored.db"
    restore_workspace(BACKUP, target)
    assert _meta(target)["schema_version"] == "1"
    assert _snapshot_rows(target) == _snapshot_rows(WORKSPACE)
    store = WorkspaceStore(target)
    try:
        assert sorted(gpo.guid for gpo in store.list_gpos()) == sorted(BASELINE)
    finally:
        store.close()
    assert _meta(target)["schema_version"] == str(SCHEMA_VERSION)
