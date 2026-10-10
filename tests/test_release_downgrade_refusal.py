"""The released 1.0.0 refuses what this tree writes, without changing it.

What ``test_release_upgrade_from_1_0_0.py`` already proves, and this file does
not repeat: the 1.0.0-written workspace upgrades losslessly; 1.0.0 refused the
upgraded workspace and a backup of it when the fixture was generated (recorded
in ``provenance.json``); and 1.0.0's ``migrate``, loaded from the tag with
``git show``, still refuses today's upgrade output.

What it did not prove, and this file does:

* **Without a full clone.** The tag-loaded check skips in a shallow clone. The
  guards are vendored here, verbatim, in
  ``fixtures/released_code/v1.0.0-version-guards.py.txt``, and every vendored
  definition is compared with the tag whenever the tag is present.
* **Without writing.** A refusal that first stamped ``app_version`` or changed
  the journal mode would still "refuse". 1.0.0 opens a workspace by applying
  its PRAGMAs and then calling ``migrate``; both are run here against today's
  upgraded workspace, and its logical content and file bytes are unchanged.
* **The backup path, re-executed.** 1.0.0's restore reads the backup's sidecar
  before it writes anything, and refuses on its schema version. That refusal
  was only recorded; here 1.0.0's sidecar reader runs on a sidecar this tree's
  ``backup_workspace`` writes today, so a change to the sidecar format that
  1.0.0 would read differently fails here.
* **Every released schema has a release-written fixture.** Every ``v*`` tag
  (all three 1.0.0 candidates and 1.0.0) ships schema 1, and the only
  release-written workspace is 1.0.0's. When a schema bump leaves a released
  schema behind, this fails until a workspace written by that release exists.
* **This tree refuses a newer workspace without writing to it,** which is the
  property the next release will depend on when this one is the old one.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import types
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from gpo_studio.model import WorkspaceError
from gpo_studio.schema import SCHEMA_VERSION
from gpo_studio.store import WorkspaceStore
from gpo_studio.workspace_ops import backup_workspace

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
RELEASE_FIXTURE = FIXTURES / "release-1.0.0-workspace"
FROZEN = FIXTURES / "released_code" / "v1.0.0-version-guards.py.txt"
PROVENANCE: dict[str, Any] = json.loads(
    (RELEASE_FIXTURE / "provenance.json").read_text(encoding="utf-8")
)

# Where each vendored definition came from.
_ORIGIN = {
    "SchemaError": "src/gpo_studio/schema.py",
    "get_schema_version": "src/gpo_studio/schema.py",
    "migrate": "src/gpo_studio/schema.py",
    "BackupMetadata": "src/gpo_studio/workspace_ops.py",
    "_validate_checksum": "src/gpo_studio/workspace_ops.py",
    "_read_backup_metadata": "src/gpo_studio/workspace_ops.py",
}

# 1.0.0's WorkspaceStore._apply_pragmas, in order. It runs them before
# _migrate_schema, so they run even when the open is refused.
_V1_0_0_PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA synchronous = NORMAL",
)

# Release-written workspace fixtures, by the schema their writer shipped.
_RELEASE_WRITTEN_FIXTURES = {1: RELEASE_FIXTURE}


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )


def _tags_available() -> bool:
    shallow = _git("rev-parse", "--is-shallow-repository")
    return (
        shallow.returncode == 0
        and shallow.stdout.strip() == "false"
        and _git("rev-parse", "-q", "--verify", "refs/tags/v1.0.0").returncode == 0
    )


def _definitions(source: str) -> dict[str, str]:
    """Each top-level class/def, with its decorators, as written."""
    lines = source.splitlines()
    found = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef):
            start = min([node.lineno, *(item.lineno for item in node.decorator_list)])
            assert node.end_lineno is not None
            found[node.name] = "\n".join(lines[start - 1 : node.end_lineno])
    return found


def _open_regular_file(path: str | Path) -> int:
    # The one substitution: 1.0.0's safe_io.open_regular_file also refuses
    # links in the path, which has no bearing on the version check.
    return os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))


class _SafeOpenError(OSError):
    pass


def _v1_0_0() -> dict[str, Any]:
    """Execute the frozen guards with 1.0.0's module-level names."""
    module = types.ModuleType("gpo_studio_v1_0_0_frozen")
    module.__dict__.update(
        {
            "contextlib": contextlib,
            "json": json,
            "os": os,
            "sqlite3": sqlite3,
            "Path": Path,
            "cast": cast,
            "asdict": asdict,
            "dataclass": dataclass,
            "WorkspaceError": WorkspaceError,
            "open_regular_file": _open_regular_file,
            "SafeOpenError": _SafeOpenError,
            # v1.0.0:src/gpo_studio/schema.py and workspace_ops.py module values.
            "SCHEMA_VERSION": 1,
            "MIN_READ_VERSION": 0,
            "_MAX_BACKUP_METADATA_BYTES": 64 * 1024,
            "__version__": "1.0.0",
            "_MIGRATIONS": {},
            "_get_app_version": lambda: "1.0.0",
        }
    )
    # dataclass() resolves its class's module through sys.modules.
    sys.modules[module.__name__] = module
    try:
        source = FROZEN.read_text(encoding="utf-8")
        exec(compile(source, str(FROZEN), "exec"), module.__dict__)
    finally:
        del sys.modules[module.__name__]
    return module.__dict__


def _state(path: Path) -> tuple[list[str], str]:
    with contextlib.closing(sqlite3.connect(path)) as conn:
        dump = list(conn.iterdump())
    return dump, hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def upgraded(tmp_path: Path) -> Path:
    """The 1.0.0-written workspace after this tree has opened (upgraded) it."""
    path = tmp_path / "workspace.db"
    shutil.copyfile(RELEASE_FIXTURE / "workspace.db", path)
    WorkspaceStore(path).close()
    with contextlib.closing(sqlite3.connect(path)) as conn:
        version = conn.execute(
            "SELECT value FROM workspace_meta WHERE key = 'schema_version'"
        ).fetchone()
    assert version == (str(SCHEMA_VERSION),)
    return path


# --- the frozen copy is 1.0.0 ---------------------------------------------------


def test_the_frozen_guards_are_verbatim_from_the_v1_0_0_tag() -> None:
    if not _tags_available():
        pytest.skip("shallow or tagless clone: the frozen copy is compared where tags exist")
    frozen = _definitions(FROZEN.read_text(encoding="utf-8"))
    assert set(frozen) == set(_ORIGIN)
    for name, path in _ORIGIN.items():
        tagged = _git("show", f"v1.0.0:{path}")
        assert tagged.returncode == 0, tagged.stderr
        assert frozen[name] == _definitions(tagged.stdout)[name], (
            f"{name} differs from v1.0.0:{path}"
        )
    schema = _git("show", "v1.0.0:src/gpo_studio/schema.py").stdout
    assert "\nSCHEMA_VERSION = 1\nMIN_READ_VERSION = 0\n" in schema
    ops = _git("show", "v1.0.0:src/gpo_studio/workspace_ops.py").stdout
    assert "\n_MAX_BACKUP_METADATA_BYTES = 64 * 1024\n" in ops


def test_v1_0_0_opens_a_workspace_with_these_pragmas_before_migrating() -> None:
    if not _tags_available():
        pytest.skip("shallow or tagless clone")
    store = _git("show", "v1.0.0:src/gpo_studio/store.py").stdout
    init = store.split("    def __init__(self, path: str | Path) -> None:", 1)[1]
    init = init.split("\n    @property", 1)[0]
    assert init.index("self._apply_pragmas()") < init.index("self._migrate_schema()")
    pragmas = store.split("    def _apply_pragmas(self) -> None:", 1)[1].split("\n    def ", 1)[0]
    assert tuple(re.findall(r'execute\("(PRAGMA [^"]+)"\)', pragmas)) == _V1_0_0_PRAGMAS


# --- 1.0.0 refuses, and writes nothing ----------------------------------------


def test_v1_0_0_refuses_the_upgraded_workspace_without_writing(upgraded: Path) -> None:
    released = _v1_0_0()
    before = _state(upgraded)
    conn = sqlite3.connect(upgraded)
    try:
        for pragma in _V1_0_0_PRAGMAS:
            conn.execute(pragma)
        with pytest.raises(released["SchemaError"]) as refused:
            released["migrate"](conn)
    finally:
        conn.close()
    assert str(refused.value) == (
        f"Workspace schema version {SCHEMA_VERSION} is newer than this version of "
        "GPO Studio supports (1). Upgrade GPO Studio."
    )
    # The recorded run of the real 1.0.0 printed the same sentence.
    observed = PROVENANCE["rollback_observations"]["legacy_run_on_upgraded_workspace"]
    assert any(line.endswith(str(refused.value)) for line in observed["schema_lines"])
    assert _state(upgraded) == before, "1.0.0's refused open changed the workspace"


def test_v1_0_0_refuses_a_backup_of_the_upgraded_workspace(upgraded: Path, tmp_path: Path) -> None:
    """1.0.0's restore reads this sidecar before it stages or writes anything."""
    released = _v1_0_0()
    backup = tmp_path / "upgraded-backup.db"
    backup_workspace(upgraded, backup)
    sidecar = Path(str(backup) + ".meta.json")  # the path 1.0.0's restore reads
    assert sidecar.is_file()
    with pytest.raises(WorkspaceError) as refused:
        released["_read_backup_metadata"](sidecar)
    message = (
        f"Backup schema version {SCHEMA_VERSION} is newer than this version of "
        "GPO Studio supports (1). Upgrade GPO Studio."
    )
    assert str(refused.value) == message
    observed = PROVENANCE["rollback_observations"]["legacy_restore_of_upgraded_backup"]
    assert observed["schema_lines"] == [f"error: {message}"]


def test_v1_0_0_still_reads_the_sidecar_it_wrote_itself() -> None:
    """The control: the frozen reader is not refusing everything."""
    released = _v1_0_0()
    metadata = released["_read_backup_metadata"](
        RELEASE_FIXTURE / "pre-upgrade-backup.db.meta.json"
    )
    assert (metadata.schema_version, metadata.app_version) == (1, "1.0.0")


def test_v1_0_0_still_accepts_the_workspace_it_wrote_itself(tmp_path: Path) -> None:
    """The control for the open: the frozen migrate runs to completion on schema 1."""
    released = _v1_0_0()
    path = tmp_path / "workspace.db"
    shutil.copyfile(RELEASE_FIXTURE / "workspace.db", path)
    with contextlib.closing(sqlite3.connect(path)) as conn:
        released["migrate"](conn)
        assert released["get_schema_version"](conn) == 1


# --- every released schema has a release-written workspace ---------------------


def _released_schema_versions() -> dict[str, int]:
    versions = {}
    for tag in _git("tag", "--list", "v[0-9]*").stdout.split():
        source = _git("show", f"{tag}:src/gpo_studio/schema.py")
        assert source.returncode == 0, (tag, source.stderr)
        match = re.search(r"^SCHEMA_VERSION = (\d+)$", source.stdout, re.M)
        assert match is not None, tag
        versions[tag] = int(match[1])
    return versions


def test_every_schema_a_release_left_behind_has_a_release_written_workspace() -> None:
    """A schema bump strands the last release's format; its upgrade needs real bytes.

    A released schema equal to the current one needs no upgrade. One below it
    does, and only a workspace that release wrote shows the upgrade works on
    what operators have -- not SQL this repository believes it would write.
    """
    if not _tags_available():
        pytest.skip("shallow or tagless clone: release tags are not fetched here")
    released = _released_schema_versions()
    assert {"v1.0.0", "v1.0.0-rc.1", "v1.0.0-rc.2", "v1.0.0-rc.3"} <= released.keys()
    stranded = {version for version in released.values() if version < SCHEMA_VERSION}
    assert stranded, "no release ships an older schema; this check found nothing to check"
    missing = stranded - _RELEASE_WRITTEN_FIXTURES.keys()
    assert not missing, (
        f"released schema(s) {sorted(missing)} have no workspace written by that "
        "release; generate one (scripts/generate_release_workspace_fixture.py)"
    )
    for version, directory in _RELEASE_WRITTEN_FIXTURES.items():
        provenance = json.loads((directory / "provenance.json").read_text(encoding="utf-8"))
        assert released[provenance["writer"]["tag"]] == version


# --- this tree refuses a newer workspace, and writes nothing --------------------


def test_this_release_refuses_a_newer_workspace_without_writing(upgraded: Path) -> None:
    with contextlib.closing(sqlite3.connect(upgraded)) as conn:
        conn.execute(
            "UPDATE workspace_meta SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION + 1),),
        )
        conn.commit()
    before = _state(upgraded)
    with pytest.raises(WorkspaceError, match=r"is newer than this version"):
        WorkspaceStore(upgraded)
    assert _state(upgraded) == before, "a refused open changed the newer workspace"
