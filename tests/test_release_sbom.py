from __future__ import annotations

import json
import runpy
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

_SCRIPT = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts" / "finalize_sbom.py")
)
deterministic_serial = cast(Callable[[str, str], str], _SCRIPT["deterministic_serial"])
finalize_sbom = cast(
    Callable[[Path, str, str], str],
    _SCRIPT["finalize_sbom"],
)


def test_finalize_sbom_adds_stable_cyclonedx_serial(tmp_path: Path) -> None:
    sbom = tmp_path / "sbom.cdx.json"
    sbom.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "version": 1,
                "components": [],
            }
        ),
        encoding="utf-8",
    )

    serial = finalize_sbom(sbom, "example/gpo-studio", "a" * 40)
    first_bytes = sbom.read_bytes()
    assert serial == deterministic_serial("example/gpo-studio", "a" * 40)
    assert serial.startswith("urn:uuid:")
    uuid.UUID(serial.removeprefix("urn:uuid:"), version=5)
    assert json.loads(first_bytes)["serialNumber"] == serial

    assert finalize_sbom(sbom, "example/gpo-studio", "a" * 40) == serial
    assert sbom.read_bytes() == first_bytes


def test_finalize_sbom_rejects_unsupported_document(tmp_path: Path) -> None:
    sbom = tmp_path / "sbom.json"
    sbom.write_text('{"spdxVersion": "SPDX-2.3"}', encoding="utf-8")

    with pytest.raises(ValueError, match="CycloneDX"):
        finalize_sbom(sbom, "example/gpo-studio", "b" * 40)


def _git(cwd: Path, *args: str) -> str:
    import os
    import subprocess

    environ = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    environ.update({
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "SBOM Test", "GIT_AUTHOR_EMAIL": "sbom@example.invalid",
        "GIT_COMMITTER_NAME": "SBOM Test", "GIT_COMMITTER_EMAIL": "sbom@example.invalid",
    })
    return subprocess.run(
        ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "commit.gpgsign=false",
         "-c", "tag.gpgsign=false", *args],
        cwd=cwd, env=environ, capture_output=True, text=True, check=True,
    ).stdout.strip()


@pytest.mark.parametrize("annotated", [False, True], ids=["lightweight", "annotated"])
def test_the_serial_names_the_commit_whatever_the_tag_type(
    tmp_path: Path, annotated: bool
) -> None:
    """DeepSeek final-r4 N1: GITHUB_SHA may be an annotated tag object; peel it."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "f").write_text("x\n", encoding="utf-8")
    _git(tmp_path, "add", "f")
    _git(tmp_path, "commit", "-q", "-m", "c")
    commit = _git(tmp_path, "rev-parse", "HEAD")
    if annotated:
        _git(tmp_path, "tag", "-a", "-m", "t", "v1.1.0")
        sha = _git(tmp_path, "rev-parse", "refs/tags/v1.1.0")
        assert sha != commit
    else:
        sha = commit
    resolve = cast(Callable[[str, Path], str], _SCRIPT["resolve_commit"])
    assert resolve(sha, tmp_path) == commit
    with pytest.raises(ValueError, match="does not resolve to a commit"):
        resolve("b" * 40, tmp_path)
