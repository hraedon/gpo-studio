"""The release gate binds approval to the version being tagged, and fails closed.

Before this gate, ``release.yml`` grepped ``docs/release-evidence.md`` for an
approval string. That file is the 1.0.0 manifest, so a ``v1.1.0`` tag would
have published on 1.0.0's approval. These tests hold the replacement
(``scripts/check_release_manifest.py``) to refusing that, and hold the
workflow to running it, and the CI and identifier gates, before publishing.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_release_manifest", REPO_ROOT / "scripts" / "check_release_manifest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


def _root(tmp_path: Path, version: str) -> Path:
    package = tmp_path / "src" / "gpo_studio"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        f'"""GPO Studio."""\n\n__version__ = "{version}"\n', encoding="utf-8"
    )
    (tmp_path / "docs").mkdir()
    return tmp_path


def _manifest(
    root: Path,
    base: str,
    status: str,
    *,
    title: str | None = None,
    application: str | None = None,
    report_version: str | None = None,
    extra: str = "",
) -> None:
    title = title or f"# Release evidence manifest — GPO Studio {base}"
    application = application or base
    (root / "docs" / f"release-evidence-{base}.md").write_text(
        f"{title}\n\n{status}\n{extra}\n## Schema and artifact identity\n\n"
        f"- Application version: {application}\n",
        encoding="utf-8",
    )
    (root / "docs" / f"release-evidence-report-{base}.json").write_text(
        json.dumps({"release_version": report_version or base}), encoding="utf-8"
    )


# --- the stale-manifest hole ------------------------------------------------


def test_the_1_0_0_manifest_cannot_approve_a_1_1_0_tag(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    shutil.copyfile(
        REPO_ROOT / "docs" / "release-evidence.md", root / "docs" / "release-evidence.md"
    )
    shutil.copyfile(
        REPO_ROOT / "docs" / "release-evidence-report.json",
        root / "docs" / "release-evidence-report.json",
    )
    with pytest.raises(gate.ReleaseGateError, match="no evidence manifest for 1.1.0"):
        gate.check(root, "v1.1.0")


def test_a_copied_manifest_still_naming_1_0_0_is_refused(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    shutil.copyfile(
        REPO_ROOT / "docs" / "release-evidence.md", root / "docs" / "release-evidence-1.1.0.md"
    )
    (root / "docs" / "release-evidence-report-1.1.0.json").write_text(
        json.dumps({"release_version": "1.1.0"}), encoding="utf-8"
    )
    with pytest.raises(gate.ReleaseGateError, match="must begin with"):
        gate.check(root, "v1.1.0")


def test_the_committed_1_1_0_draft_cannot_release(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    for name in ("release-evidence-1.1.0.md", "release-evidence-report-1.1.0.json"):
        shutil.copyfile(REPO_ROOT / "docs" / name, root / "docs" / name)
    with pytest.raises(gate.ReleaseGateError, match="exactly one status line"):
        gate.check(root, "v1.1.0")
    # It does name its own version correctly, so only approval is missing.
    (root / "docs" / "release-evidence-1.1.0.md").write_text(
        re.sub(
            r"^> \*\*Status:\*\*.*$",
            gate.APPROVED,
            (root / "docs" / "release-evidence-1.1.0.md").read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        ),
        encoding="utf-8",
    )
    assert gate.check(root, "v1.1.0").manifest == "docs/release-evidence-1.1.0.md"


def test_the_released_1_0_0_manifest_still_satisfies_its_own_tag(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.0.0")
    for name in ("release-evidence.md", "release-evidence-report.json"):
        shutil.copyfile(REPO_ROOT / "docs" / name, root / "docs" / name)
    result = gate.check(root, "v1.0.0")
    assert (result.manifest, result.report) == (
        "docs/release-evidence.md",
        "docs/release-evidence-report.json",
    )


# --- identity ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "tag"),
    [
        ("1.0.0", "v1.1.0"),
        ("1.1.0", "v1.0.0"),
        ("1.1.0", "1.1.0"),
        ("1.1.0rc1", "v1.1.0"),
        ("1.1.0rc1", "v1.1.0rc1"),
        ("1.1.0", "v1.1.0-rc.1"),
    ],
)
def test_tag_must_equal_the_package_version(tmp_path: Path, version: str, tag: str) -> None:
    root = _root(tmp_path, version)
    _manifest(root, "1.1.0", gate.CANDIDATE if "rc" in version else gate.APPROVED)
    with pytest.raises(gate.ReleaseGateError, match="does not match package version"):
        gate.check(root, tag)


@pytest.mark.parametrize("version", ["1.1.0.dev1", "1.1.0+local", "1.1", "1.1.0a1", "1.1.0rc0"])
def test_unreleasable_versions_are_refused(tmp_path: Path, version: str) -> None:
    root = _root(tmp_path, version)
    with pytest.raises(gate.ReleaseGateError, match="refusing to release"):
        gate.check(root, f"v{version}")


# --- the manifest's own claims -----------------------------------------------


def test_an_approved_manifest_for_the_tagged_version_passes(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    _manifest(root, "1.1.0", gate.APPROVED)
    result = gate.check(root, "v1.1.0")
    assert (result.version, result.is_candidate) == ("1.1.0", False)


def test_a_candidate_tag_needs_the_candidate_marker_not_approval(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0rc2")
    _manifest(root, "1.1.0", gate.CANDIDATE)
    assert gate.check(root, "v1.1.0-rc.2").is_candidate
    _manifest(root, "1.1.0", gate.APPROVED)
    with pytest.raises(gate.ReleaseGateError, match="exactly one status line"):
        gate.check(root, "v1.1.0-rc.2")


@pytest.mark.parametrize(
    "status",
    [
        "> **Status:** DRAFT — not approved",
        "> **Status:** release candidate; final approval pending",
        "> **Status:**  approved for release",
        "",
    ],
)
def test_a_final_tag_needs_the_exact_approval_marker(tmp_path: Path, status: str) -> None:
    root = _root(tmp_path, "1.1.0")
    _manifest(root, "1.1.0", status)
    with pytest.raises(gate.ReleaseGateError, match="exactly one status line"):
        gate.check(root, "v1.1.0")


def test_a_second_status_line_is_refused(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    _manifest(root, "1.1.0", gate.APPROVED, extra="> **Status:** DRAFT\n")
    with pytest.raises(gate.ReleaseGateError, match="exactly one status line"):
        gate.check(root, "v1.1.0")


def test_the_title_must_name_the_version(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    _manifest(root, "1.1.0", gate.APPROVED, title="# Release evidence manifest — GPO Studio 1.0.0")
    with pytest.raises(gate.ReleaseGateError, match="must begin with"):
        gate.check(root, "v1.1.0")


def test_the_application_version_line_must_name_the_version(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    _manifest(root, "1.1.0", gate.APPROVED, application="1.0.0")
    with pytest.raises(gate.ReleaseGateError, match="Application version"):
        gate.check(root, "v1.1.0")


def test_the_report_must_name_the_version(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    _manifest(root, "1.1.0", gate.APPROVED, report_version="1.0.0")
    with pytest.raises(gate.ReleaseGateError, match="release_version"):
        gate.check(root, "v1.1.0")


def test_a_missing_or_malformed_report_is_refused(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    _manifest(root, "1.1.0", gate.APPROVED)
    report = root / "docs" / "release-evidence-report-1.1.0.json"
    report.write_text("{not json", encoding="utf-8")
    with pytest.raises(gate.ReleaseGateError, match="not valid JSON"):
        gate.check(root, "v1.1.0")
    report.write_text("[]", encoding="utf-8")
    with pytest.raises(gate.ReleaseGateError, match="release_version"):
        gate.check(root, "v1.1.0")
    report.unlink()
    with pytest.raises(gate.ReleaseGateError, match="no evidence report"):
        gate.check(root, "v1.1.0")


def test_main_fails_closed_and_writes_outputs_only_on_success(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    outputs = tmp_path / "github-output"
    assert gate.main(["--tag", "v1.1.0", "--root", str(root), "--github-output", str(outputs)]) == 1
    assert not outputs.exists()
    _manifest(root, "1.1.0", gate.APPROVED)
    assert gate.main(["--tag", "v1.1.0", "--root", str(root), "--github-output", str(outputs)]) == 0
    assert outputs.read_text(encoding="utf-8").splitlines() == [
        "version=1.1.0",
        "manifest=docs/release-evidence-1.1.0.md",
        "report=docs/release-evidence-report-1.1.0.json",
        "prerelease=false",
    ]


def test_the_legacy_table_holds_only_the_release_that_predates_the_rule() -> None:
    assert gate.LEGACY_MANIFESTS == {
        "1.0.0": ("docs/release-evidence.md", "docs/release-evidence-report.json")
    }


# --- the workflow runs the gates ---------------------------------------------
#
# PyYAML is not a dependency, so these read the workflow text. They pin the
# structure a refactor is most likely to lose, not the YAML semantics.


def _job_block(text: str, job: str) -> str:
    match = re.search(rf"^  {re.escape(job)}:\n(?P<body>(?:^(?:    .*|\s*)\n)*)", text, re.M)
    assert match is not None, f"job {job!r} not found"
    return match["body"]


def test_release_workflow_no_longer_greps_the_unversioned_manifest() -> None:
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    assert "grep -q '^> " not in text
    assert "cp docs/release-evidence.md" not in text
    assert text.count("python scripts/check_release_manifest.py") == 2


def test_publish_needs_identity_ci_identifier_gate_and_verify() -> None:
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    publish = _job_block(text, "publish")
    needs = re.search(r"^    needs: \[(?P<needs>[^\]]*)\]", publish, re.M)
    assert needs is not None
    assert {item.strip() for item in needs["needs"].split(",")} == {
        "release-identity",
        "ci",
        "identifier-gate",
        "verify",
    }
    assert "uses: ./.github/workflows/ci.yml" in _job_block(text, "ci")
    identifier = _job_block(text, "identifier-gate")
    assert "uses: ./.github/workflows/identifier-gate.yml" in identifier
    assert "GPO_STUDIO_FORBIDDEN_IDENTIFIERS: ${{ secrets.GPO_STUDIO_FORBIDDEN_IDENTIFIERS }}" in (
        identifier
    )


def test_called_workflows_accept_workflow_call_and_keep_their_jobs() -> None:
    ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    on_block = ci.split("\nconcurrency:", 1)[0]
    assert "\n  workflow_call:" in on_block
    # The jobs a release must not skip are still defined in the called file.
    for job in ("test", "test-windows", "frontend", "installed-package", "static-safety"):
        assert re.search(rf"^  {re.escape(job)}:$", ci, re.M), job
    gate_workflow = (WORKFLOWS / "identifier-gate.yml").read_text(encoding="utf-8")
    assert "\n  workflow_call:\n    secrets:\n      GPO_STUDIO_FORBIDDEN_IDENTIFIERS:" in (
        gate_workflow
    )
    assert "--strict" in gate_workflow
