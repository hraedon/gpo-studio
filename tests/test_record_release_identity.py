"""The tagged run records exactly the automated evidence the approval says it does.

Sol final-r3 B1: the approval commit closes the "automated evidence" item as
recorded by the tagged run, but the run recorded only the commit, the tag and
three hashes, and published a manifest still saying "To be resolved". The
writer (``scripts/record_release_identity.py``) now resolves that placeholder in
the published copies with the run's identity, the jobs gating publication, the
coverage check and the hashes, and refuses to run without any of them.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import current_qualification
import pytest

ROOT = Path(__file__).resolve().parents[1]
#: The committed records the next tag publishes: the package version's.
BASE = current_qualification.package_base_version()
SCRIPT = ROOT / "scripts" / "record_release_identity.py"
ENV = {
    "GITHUB_SHA": "set by the publish_dir fixture to the repository's commit",
    "GITHUB_REF_NAME": "v1.1.0",
    "GITHUB_RUN_ID": "123456789",
    "GITHUB_RUN_ATTEMPT": "2",
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REPOSITORY": "hraedon/gpo-studio",
    "GITHUB_WORKFLOW_REF": "hraedon/gpo-studio/.github/workflows/release.yml@refs/tags/v1.1.0",
}
RUN_URL = "https://github.com/hraedon/gpo-studio/actions/runs/123456789/attempts/2"


_GIT = {
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_AUTHOR_NAME": "Writer Test", "GIT_AUTHOR_EMAIL": "writer@example.invalid",
    "GIT_COMMITTER_NAME": "Writer Test", "GIT_COMMITTER_EMAIL": "writer@example.invalid",
}


def _git(cwd: Path, *args: str) -> str:
    environ = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "commit.gpgsign=false",
         "-c", "tag.gpgsign=false", *args],
        cwd=cwd, env={**environ, **_GIT}, capture_output=True, text=True, check=True,
    ).stdout.strip()


@pytest.fixture
def publish_dir(tmp_path: Path) -> Path:
    """The publish job's working directory just before the writer runs.

    A git checkout (the publish job checks out full history), with the release
    commit tagged both ways: ``v1.1.0`` lightweight and ``v1.1.0-annotated``.
    """
    _git(tmp_path, "init", "-q")
    (tmp_path / "source.txt").write_text("source\n", encoding="utf-8")
    _git(tmp_path, "add", "source.txt")
    _git(tmp_path, "commit", "-q", "-m", "release commit")
    _git(tmp_path, "tag", "v1.1.0")
    _git(tmp_path, "tag", "-a", "-m", "annotated", "v1.1.0-annotated")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "gpo_studio-1.1.0-py3-none-any.whl").write_bytes(b"wheel")
    (tmp_path / "dist" / "gpo_studio-1.1.0.tar.gz").write_bytes(b"sdist")
    (tmp_path / "sbom.cdx.json").write_bytes(b"{}")
    (tmp_path / "requirements-lock.txt").write_bytes(b"x==1\n")
    shutil.copyfile(ROOT / "docs" / f"release-evidence-{BASE}.md", tmp_path / "release-evidence.md")
    shutil.copyfile(
        ROOT / "docs" / f"release-evidence-report-{BASE}.json",
        tmp_path / "release-evidence-report.json",
    )
    return tmp_path


def _run(cwd: Path, **env: str | None) -> subprocess.CompletedProcess[str]:
    environ = {
        k: v for k, v in os.environ.items() if not k.startswith(("GITHUB_", "GIT_"))
    }
    environ.update(_GIT)
    commit = _git(cwd, "rev-parse", "HEAD") if (cwd / ".git").exists() else "a" * 40
    for key, value in {**ENV, "GITHUB_SHA": commit, **env}.items():
        if value is not None:
            environ[key] = value
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT)], cwd=cwd, env=environ,
        capture_output=True, text=True, check=False,
    )


def _files(directory: Path) -> dict[str, bytes]:
    return {
        name: (directory / name).read_bytes()
        for name in ("release-evidence.md", "release-evidence-report.json")
    }


def test_the_placeholder_is_resolved_with_the_run_identity(publish_dir: Path) -> None:
    result = _run(publish_dir)
    assert result.returncode == 0, result.stderr
    manifest = (publish_dir / "release-evidence.md").read_text(encoding="utf-8")
    section = manifest.split("## Automated evidence\n", 1)[1].split("\n## ", 1)[0]
    assert "To be resolved" not in section
    assert f"[run 123456789]({RUN_URL}), attempt" in section
    for job in ("release-identity", "ci", "identifier-gate", "verify"):
        assert f"`{job}`" in section
    assert "checked branch coverage against the floor" in section
    assert "is not recorded here" in section
    assert "Local pre-release observation" in section  # the rest is untouched
    assert f"- Workflow run: {RUN_URL}" in manifest
    commit = _git(publish_dir, "rev-parse", "HEAD")
    assert manifest.split("\n")[3] == f"> **Source commit:** `{commit}`"
    assert "To be resolved" not in manifest
    assert "resolved by the tagged release workflow" not in manifest
    report = json.loads((publish_dir / "release-evidence-report.json").read_text("utf-8"))
    assert report["release_identity"] == {
        "source_commit": commit,
        "tag": "v1.1.0",
        "workflow_ref": ENV["GITHUB_WORKFLOW_REF"],
        "run_id": "123456789",
        "run_attempt": "2",
        "run_url": RUN_URL,
    }
    automated = report["evidence"]["automated_evidence"]
    assert automated["run_url"] == RUN_URL
    assert automated["jobs_passed_before_publish"] == [
        "release-identity", "ci", "identifier-gate", "verify"
    ]
    assert "total not recorded" in automated["coverage"]
    assert automated["artifact_hashes"] == {
        k: v for k, v in report["artifact_hashes"].items()
    }
    assert "resolved by the tagged release workflow" not in json.dumps(report["artifact_hashes"])


@pytest.mark.parametrize(
    "missing",
    ["GITHUB_SHA", "GITHUB_REF_NAME", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
     "GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_WORKFLOW_REF"],
)
def test_a_missing_run_variable_refuses_and_writes_nothing(
    publish_dir: Path, missing: str
) -> None:
    before = _files(publish_dir)
    result = _run(publish_dir, **{missing: None})
    assert result.returncode == 1
    assert result.stderr.startswith("record release identity: FAIL: ") and missing in result.stderr
    assert "Traceback" not in result.stderr
    assert _files(publish_dir) == before


@pytest.mark.parametrize(
    ("name", "value"),
    [("GITHUB_RUN_ID", "12a"), ("GITHUB_SERVER_URL", "http://github.com"),
     ("GITHUB_REF_NAME", "main"), ("GITHUB_SHA", "a" * 39),
     ("GITHUB_REPOSITORY", "hraedon"), ("GITHUB_RUN_ATTEMPT", "0"),
     ("GITHUB_WORKFLOW_REF", "hraedon/gpo-studio/release.yml@refs/tags/v1.1.0")],
)
def test_a_malformed_run_variable_refuses(publish_dir: Path, name: str, value: str) -> None:
    before = _files(publish_dir)
    result = _run(publish_dir, **{name: value})
    assert result.returncode == 1 and name in result.stderr
    assert _files(publish_dir) == before


@pytest.mark.parametrize("name", sorted(ENV))
def test_a_trailing_newline_is_malformed(publish_dir: Path, name: str) -> None:
    """Sol final-r4 B1: `$` with match() accepted "value\\n"; fullmatch does not."""
    value = _git(publish_dir, "rev-parse", "HEAD") if name == "GITHUB_SHA" else ENV[name]
    before = _files(publish_dir)
    result = _run(publish_dir, **{name: value + "\n"})
    assert result.returncode == 1 and name in result.stderr
    assert _files(publish_dir) == before


@pytest.mark.parametrize("annotated", [False, True], ids=["lightweight", "annotated"])
def test_the_source_commit_is_the_commit_whatever_the_tag_type(
    publish_dir: Path, annotated: bool
) -> None:
    """DeepSeek final-r4 N1: GITHUB_SHA may be an annotated tag object."""
    commit = _git(publish_dir, "rev-parse", "HEAD")
    tag = "v1.1.0-annotated" if annotated else "v1.1.0"
    sha = _git(publish_dir, "rev-parse", f"refs/tags/{tag}")
    assert (sha != commit) is annotated
    result = _run(publish_dir, GITHUB_SHA=sha)
    assert result.returncode == 0, result.stderr
    report = json.loads((publish_dir / "release-evidence-report.json").read_text("utf-8"))
    identity = report["release_identity"]
    assert identity["source_commit"] == commit
    assert identity.get("tag_object") == (sha if annotated else None)
    manifest = (publish_dir / "release-evidence.md").read_text(encoding="utf-8")
    assert f"at commit `{commit}`" in manifest and f"- Source commit: `{commit}`" in manifest


def test_a_sha_that_is_not_in_the_checkout_refuses(publish_dir: Path) -> None:
    before = _files(publish_dir)
    result = _run(publish_dir, GITHUB_SHA="b" * 40)
    assert result.returncode == 1 and "does not resolve to a commit" in result.stderr
    assert _files(publish_dir) == before


@pytest.mark.parametrize("damage", ["absent", "twice", "outside-the-section"])
def test_the_placeholder_must_be_there_exactly_once(publish_dir: Path, damage: str) -> None:
    path = publish_dir / "release-evidence.md"
    text = path.read_text(encoding="utf-8")
    marker = "To be resolved from the tagged release workflow run:"
    if damage == "absent":
        text = text.replace(marker, "Resolved by hand:")
    elif damage == "twice":
        text = text.replace(marker, f"{marker} x\n\n{marker}")
    else:
        text = text.replace(marker, "Moved:").replace("## Accessibility evidence\n",
                                                      f"## Accessibility evidence\n\n{marker} x\n")
    path.write_text(text, encoding="utf-8", newline="\n")
    before = _files(publish_dir)
    result = _run(publish_dir)
    assert result.returncode == 1 and "placeholder" in result.stderr
    if damage == "outside-the-section":
        # DeepSeek final-r4 N3: the diagnostic names the failing (in-section) count.
        assert "found 0 in the section and 1 in the whole manifest" in result.stderr
    assert _files(publish_dir) == before


@pytest.mark.parametrize(
    ("anchor", "where"),
    [
        ("## Accessibility evidence\n", "inside the section"),
        ("## Upgrade and rollback\n", "elsewhere in the manifest"),
    ],
)
def test_placeholder_text_left_anywhere_in_the_manifest_refuses(
    publish_dir: Path, anchor: str, where: str
) -> None:
    """DeepSeek final-r4 N2: the whole published manifest is scanned, not one section."""
    path = publish_dir / "release-evidence.md"
    text = path.read_text(encoding="utf-8")
    assert anchor in text, where
    path.write_text(
        text.replace(anchor, f"To be resolved later, by hand.\n\n{anchor}", 1),
        encoding="utf-8", newline="\n",
    )
    before = _files(publish_dir)
    result = _run(publish_dir)
    assert result.returncode == 1 and "survived" in result.stderr
    assert _files(publish_dir) == before


def test_placeholder_text_left_in_the_report_refuses(publish_dir: Path) -> None:
    path = publish_dir / "release-evidence-report.json"
    report = json.loads(path.read_text("utf-8"))
    report["evidence"]["note"] = "To be resolved from the tagged release workflow run: x"
    path.write_text(json.dumps(report), encoding="utf-8")
    before = _files(publish_dir)
    result = _run(publish_dir)
    assert result.returncode == 1 and "survived in the published report" in result.stderr
    assert _files(publish_dir) == before


def test_a_manifest_without_the_source_commit_header_refuses(publish_dir: Path) -> None:
    path = publish_dir / "release-evidence.md"
    lines = path.read_text(encoding="utf-8").split("\n")
    lines[3] = "> Commit: unknown"
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    result = _run(publish_dir)
    assert result.returncode == 1 and "Source commit" in result.stderr


def test_a_report_already_holding_automated_evidence_refuses(publish_dir: Path) -> None:
    path = publish_dir / "release-evidence-report.json"
    report = json.loads(path.read_text("utf-8"))
    report["evidence"]["automated_evidence"] = {"run_url": "hand-written"}
    path.write_text(json.dumps(report), encoding="utf-8")
    result = _run(publish_dir)
    assert result.returncode == 1 and "automated_evidence" in result.stderr


def test_the_writer_runs_before_the_published_files_are_hashed_attested_and_released() -> None:
    """DeepSeek final-r4 N5: a reorder would publish stale hashes of the committed copies."""
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    publish = text.split("\n  publish:\n", 1)[1]
    order = [
        publish.index('cp "${EVIDENCE_MANIFEST}" release-evidence.md'),
        publish.index("python -I scripts/record_release_identity.py"),
        publish.index("sha256sum dist/* requirements-lock.txt sbom.cdx.json release-evidence.md"),
        publish.index("uses: actions/attest-build-provenance@"),
        publish.index('gh release create "$GITHUB_REF_NAME"'),
    ]
    assert order == sorted(order), order


def test_the_publish_job_runs_only_after_the_jobs_the_record_names() -> None:
    """The record says those jobs passed because publish `needs:` all of them."""
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "  publish:\n    needs: [release-identity, ci, identifier-gate, verify]\n" in text
    publish = text.split("\n  publish:\n", 1)[1]
    assert "\n    if:" not in publish.split("\n    steps:", 1)[0]
    assert "python -I scripts/record_release_identity.py" in publish
