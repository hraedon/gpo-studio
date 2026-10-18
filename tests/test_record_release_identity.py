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

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "record_release_identity.py"
ENV = {
    "GITHUB_SHA": "a" * 40,
    "GITHUB_REF_NAME": "v1.1.0",
    "GITHUB_RUN_ID": "123456789",
    "GITHUB_RUN_ATTEMPT": "2",
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REPOSITORY": "hraedon/gpo-studio",
    "GITHUB_WORKFLOW_REF": "hraedon/gpo-studio/.github/workflows/release.yml@refs/tags/v1.1.0",
}
RUN_URL = "https://github.com/hraedon/gpo-studio/actions/runs/123456789/attempts/2"


@pytest.fixture
def publish_dir(tmp_path: Path) -> Path:
    """The publish job's working directory just before the writer runs."""
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "gpo_studio-1.1.0-py3-none-any.whl").write_bytes(b"wheel")
    (tmp_path / "dist" / "gpo_studio-1.1.0.tar.gz").write_bytes(b"sdist")
    (tmp_path / "sbom.cdx.json").write_bytes(b"{}")
    (tmp_path / "requirements-lock.txt").write_bytes(b"x==1\n")
    shutil.copyfile(ROOT / "docs" / "release-evidence-1.1.0.md", tmp_path / "release-evidence.md")
    shutil.copyfile(
        ROOT / "docs" / "release-evidence-report-1.1.0.json",
        tmp_path / "release-evidence-report.json",
    )
    return tmp_path


def _run(cwd: Path, **env: str | None) -> subprocess.CompletedProcess[str]:
    environ = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}
    for key, value in {**ENV, **env}.items():
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
    report = json.loads((publish_dir / "release-evidence-report.json").read_text("utf-8"))
    assert report["release_identity"] == {
        "source_commit": "a" * 40,
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
     ("GITHUB_REF_NAME", "main"), ("GITHUB_SHA", "a" * 39)],
)
def test_a_malformed_run_variable_refuses(publish_dir: Path, name: str, value: str) -> None:
    result = _run(publish_dir, **{name: value})
    assert result.returncode == 1 and name in result.stderr


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
    assert _files(publish_dir) == before


def test_a_placeholder_that_survives_the_rewrite_refuses(publish_dir: Path) -> None:
    path = publish_dir / "release-evidence.md"
    text = path.read_text(encoding="utf-8").replace(
        "## Accessibility evidence\n",
        "To be resolved later, by hand.\n\n## Accessibility evidence\n",
    )
    path.write_text(text, encoding="utf-8", newline="\n")
    result = _run(publish_dir)
    assert result.returncode == 1 and "survived" in result.stderr


def test_a_report_already_holding_automated_evidence_refuses(publish_dir: Path) -> None:
    path = publish_dir / "release-evidence-report.json"
    report = json.loads(path.read_text("utf-8"))
    report["evidence"]["automated_evidence"] = {"run_url": "hand-written"}
    path.write_text(json.dumps(report), encoding="utf-8")
    result = _run(publish_dir)
    assert result.returncode == 1 and "automated_evidence" in result.stderr


def test_the_publish_job_runs_only_after_the_jobs_the_record_names() -> None:
    """The record says those jobs passed because publish `needs:` all of them."""
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "  publish:\n    needs: [release-identity, ci, identifier-gate, verify]\n" in text
    publish = text.split("\n  publish:\n", 1)[1]
    assert "\n    if:" not in publish.split("\n    steps:", 1)[0]
    assert "python -I scripts/record_release_identity.py" in publish
