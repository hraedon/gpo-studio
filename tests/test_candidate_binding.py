"""WI-025: a lane's verdict must hash the candidate it was graded against.

A verdict that names an input artifact without hashing it asserts a comparison
nobody can re-check. WP-6B closed this in 2026-08 by recording SHA-256 for its
topology, prediction and expectation; the WP-1B and endpoint lanes were left
open, and their verdicts named seven candidates and one task corpus between them
while binding neither.

Two properties are pinned here, and the second is the one that rots:

1. the hashes the finalizers record are the hashes of the files on disk, keyed
   by a path a reviewer can resolve; and
2. the file names each finalizer REQUIRES are the file names its builder
   actually writes. A required-set that drifts from the builder turns the
   refusal below into either a permanent failure or -- worse -- a check on a
   file nobody produces, which passes by being vacuous.
"""

from __future__ import annotations

import hashlib
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ORACLE_DIR = _REPO_ROOT / "scripts" / "windows-oracle"
_BUILDER_DIR = _REPO_ROOT / "scripts" / "plan-033"


def _symbols(finalizer: str) -> dict[str, Any]:
    return runpy.run_path(str(_ORACLE_DIR / finalizer))


def _build(builder: str, output_dir: Path) -> None:
    subprocess.run(
        [sys.executable, str(_BUILDER_DIR / builder), str(output_dir)],
        check=True,
        capture_output=True,
    )


@pytest.mark.parametrize(
    "finalizer,builder",
    [
        ("finalize_endpoint_run.py", "build-endpoint-candidate.py"),
        ("finalize_wp1b_run.py", "build-wp1b-candidates.py"),
    ],
)
def test_every_file_under_the_candidate_root_is_hashed(
    finalizer: str, builder: str, tmp_path: Path
) -> None:
    """Everything, not a list -- the omission worth catching is the unlisted file."""
    root = tmp_path / "candidate"
    _build(builder, root)
    hashes = cast(
        dict[str, str], _symbols(finalizer)["_candidate_hashes"](root)
    )

    on_disk = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }
    assert hashes == on_disk
    assert hashes, "the builder produced nothing, so this test proves nothing"
    # Keyed by a path a reviewer can resolve, on either platform. The lane is
    # driven from a Windows controller and the verdicts are read anywhere.
    assert not any("\\" in name for name in hashes)


def test_the_endpoint_lane_requires_what_its_builder_writes(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    _build("build-endpoint-candidate.py", root)
    symbols = _symbols("finalize_endpoint_run.py")
    hashes = symbols["_candidate_hashes"](root)

    for name in cast(tuple[str, ...], symbols["REQUIRED_CANDIDATE_FILES"]):
        assert (root / name).is_file(), f"{name} is required and the builder does not write it"
    assert symbols["_candidate_problems"](root, hashes) == []


def test_the_wp1b_lane_requires_what_its_builder_writes(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    _build("build-wp1b-candidates.py", root)
    symbols = _symbols("finalize_wp1b_run.py")
    hashes = symbols["_candidate_hashes"](root)
    ids = [directory.name for directory in sorted(root.iterdir()) if directory.is_dir()]
    assert ids, "the builder produced no candidate directories"

    for name in cast(tuple[str, ...], symbols["REQUIRED_CANDIDATE_ROOT_FILES"]):
        assert (root / name).is_file(), f"{name} is required and the builder does not write it"
    for candidate_id in ids:
        for name in cast(tuple[str, ...], symbols["REQUIRED_CANDIDATE_FILES"]):
            assert (root / candidate_id / name).is_file()
    assert symbols["_candidate_problems"](root, hashes, ids) == []


@pytest.mark.parametrize(
    "finalizer,builder",
    [
        ("finalize_endpoint_run.py", "build-endpoint-candidate.py"),
        ("finalize_wp1b_run.py", "build-wp1b-candidates.py"),
    ],
)
def test_an_incomplete_candidate_root_is_refused(
    finalizer: str, builder: str, tmp_path: Path
) -> None:
    """The control: the refusal has to be reachable.

    A guard that cannot fire is indistinguishable from no guard, and this
    project has already shipped one -- `native_shape_matches_corpus` read as
    passing precisely when there was nothing to check. Each required file is
    removed in turn and the finalizer must name it.
    """
    root = tmp_path / "candidate"
    _build(builder, root)
    symbols = _symbols(finalizer)
    required = [
        root / name for name in cast(tuple[str, ...], symbols["REQUIRED_CANDIDATE_FILES"])
    ]
    ids: list[str] = []
    if "REQUIRED_CANDIDATE_ROOT_FILES" in symbols:
        ids = [d.name for d in sorted(root.iterdir()) if d.is_dir()]
        required = [
            root / name
            for name in cast(tuple[str, ...], symbols["REQUIRED_CANDIDATE_ROOT_FILES"])
        ] + [root / ids[0] / path.name for path in required]

    def problems(hashes: dict[str, str]) -> list[str]:
        if ids:
            return cast(list[str], symbols["_candidate_problems"](root, hashes, ids))
        return cast(list[str], symbols["_candidate_problems"](root, hashes))

    for path in required:
        kept = path.read_bytes()
        path.unlink()
        try:
            found = problems(symbols["_candidate_hashes"](root))
            assert found, f"removing {path.name} was not refused"
            assert any(path.name in problem for problem in found)
        finally:
            path.write_bytes(kept)

    assert problems(symbols["_candidate_hashes"](root)) == []
