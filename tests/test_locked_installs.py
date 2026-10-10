"""Every Python install the workflows and the operator run resolves from uv.lock.

Before this, only the Windows CI job used the lockfile; the Linux jobs, the
release gates and the published install instructions resolved ``pyproject.toml``
version ranges at run time, so what CI tested, what the release built and what
an operator installed could each be a different set of dependency versions.

PyYAML is not a dependency, so, like ``test_release_manifest_gate.py``, these
read the workflow text. They pin the rule line by line: a later edit that adds
an unlocked ``pip install`` fails here rather than in review.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
LOCKED_FILES = [
    WORKFLOWS / "ci.yml",
    WORKFLOWS / "release.yml",
    REPO_ROOT / "scripts" / "installed_package_smoke.sh",
]
QUICKSTART = REPO_ROOT / "docs" / "windows-quickstart.md"


def _lines(path: Path) -> list[tuple[int, str]]:
    """Logical lines: shell continuations (`\\` at end of line) are joined."""
    joined: list[tuple[int, str]] = []
    pending: tuple[int, str] | None = None
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if pending is not None:
            line = pending[1] + " " + line
            number = pending[0]
        if line.endswith("\\"):
            pending = (number, line[:-1].rstrip())
            continue
        pending = None
        if not line.startswith("#"):
            joined.append((number, line))
    return joined


def _job_block(text: str, job: str) -> str:
    match = re.search(rf"^  {re.escape(job)}:\n(?P<body>(?:^(?:    .*|\s*)\n)*)", text, re.M)
    assert match is not None, f"job {job!r} not found"
    return match["body"]


@pytest.mark.parametrize("path", LOCKED_FILES, ids=lambda path: path.name)
def test_every_pip_install_is_hash_checked_or_installs_only_the_built_artifact(
    path: Path,
) -> None:
    """`--require-hashes`, or `--no-deps --no-index` for the artifact under test."""
    installs = [
        (number, line) for number, line in _lines(path) if re.search(r'pip"?\s+install', line)
    ]
    assert installs, f"{path.name} installs nothing; this test is looking at the wrong file"
    for number, line in installs:
        hashed = "--require-hashes" in line and " -r " in line
        artifact_only = "--no-deps" in line and "--no-index" in line
        assert hashed or artifact_only, (
            f"{path.name}:{number} installs from version ranges or an index: {line!r}"
        )


@pytest.mark.parametrize("path", LOCKED_FILES, ids=lambda path: path.name)
def test_every_uv_resolution_is_locked(path: Path) -> None:
    for number, line in _lines(path):
        for command in ("uv sync", "uv run", "uv export"):
            if command in line:
                assert "--locked" in line, f"{path.name}:{number} {command} without --locked"


def test_no_workflow_installs_the_project_editable_from_ranges() -> None:
    for path in LOCKED_FILES:
        assert "pip install -e" not in path.read_text(encoding="utf-8"), path.name


def test_ci_checks_the_lockfile_matches_pyproject() -> None:
    ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "run: uv lock --check" in _job_block(ci, "test")


def test_the_published_requirements_are_runtime_only() -> None:
    """No dev extra and no dependency group may leak into the operator's lock."""
    release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    exports = [
        line
        for _number, line in _lines(WORKFLOWS / "release.yml")
        if "uv export" in line and "--output-file requirements-lock.txt" in line
    ]
    assert len(exports) == 1, exports
    (export,) = exports
    assert "--no-dev" in export and "--no-emit-project" in export
    assert "--extra" not in export and "--group" not in export and "--all" not in export
    assert "requirements-lock.txt" in _job_block(release, "publish")


def test_requirements_lock_is_checksummed_attested_and_published() -> None:
    publish = _job_block((WORKFLOWS / "release.yml").read_text(encoding="utf-8"), "publish")
    sums = re.search(r"sha256sum (?P<args>[^>]*)> SHA256SUMS", publish.replace("\\\n", " "))
    assert sums is not None
    assert "requirements-lock.txt" in sums["args"].split()

    provenance = publish.split("actions/attest-build-provenance", 1)[1].split("- name:", 1)[0]
    subjects = provenance.split("subject-path: |", 1)[1].split()
    assert "requirements-lock.txt" in subjects and "SHA256SUMS" in subjects

    create = publish.split('gh release create "$GITHUB_REF_NAME"', 1)[1].split("--verify-tag")[0]
    assert "requirements-lock.txt" in create.split()

    export = publish.index("--output-file requirements-lock.txt")
    assert publish.index("--no-isolation --outdir dist\n") < export, (
        "export after the build, or the file can be swept into the sdist"
    )
    assert export < publish.index("bash scripts/installed_package_smoke.sh")


def _powershell_blocks() -> list[list[str]]:
    text = QUICKSTART.read_text(encoding="utf-8")
    return [block.splitlines() for block in re.findall(r"```powershell\n(.*?)```", text, re.S)]


def test_the_quickstart_installs_the_wheel_from_the_lock() -> None:
    """Each wheel install is locked, or is the fallback for a release without the file."""
    wheel_installs = 0
    locked_installs = 0
    for block in _powershell_blocks():
        for index, line in enumerate(block):
            if "pip install" not in line:
                continue
            if "--require-hashes -r $Requirements.FullName" in line:
                locked_installs += 1
                continue
            if "$Wheel.FullName" not in line:
                assert line.strip() == "& $Python -m pip install --upgrade pip", line
                continue
            wheel_installs += 1
            if "--no-deps --no-index" in line:
                continue
            previous = next(text for text in reversed(block[:index]) if text.strip())
            assert previous.strip() == "} else {", (
                f"an unlocked wheel install outside the no-lockfile fallback: {line!r}"
            )
    # install, upgrade and rollback: one locked path and one fallback each.
    assert wheel_installs == 6
    assert locked_installs == 3


def test_the_quickstart_verifies_the_lockfile_and_the_attestations() -> None:
    text = QUICKSTART.read_text(encoding="utf-8")
    checksum = next(
        "\n".join(block)
        for block in _powershell_blocks()
        if any("Expected exactly one gpo_studio wheel" in line for line in block)
    )
    assert "requirements-lock\\.txt$" in checksum
    assert "foreach ($File in @($Wheel, $Requirements)" in checksum
    # The attestation instruction names the workflow release.yml attests with.
    assert "gh attestation verify $File.FullName --repo hraedon/gpo-studio" in text
    assert "--signer-workflow hraedon/gpo-studio/.github/workflows/release.yml" in text
    release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    assert "actions/attest-build-provenance" in _job_block(release, "publish")
