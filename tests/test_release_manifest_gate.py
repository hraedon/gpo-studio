"""The release gate binds approval to the version being tagged, and fails closed.

Before this gate, ``release.yml`` grepped ``docs/release-evidence.md`` for an
approval string. That file is the 1.0.0 manifest, so a ``v1.1.0`` tag would
have published on 1.0.0's approval. These tests hold the replacement
(``scripts/check_release_manifest.py``) to refusing that, and hold the
workflow to running it, and the CI and identifier gates, before publishing.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
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
        f"{title}\n\n{status}\n\n{extra}\n\n## Schema and artifact identity\n\n"
        f"- Application version: {application}\n",
        encoding="utf-8",
    )
    (root / "docs" / f"release-evidence-report-{base}.json").write_text(
        json.dumps({"release_version": report_version or base}), encoding="utf-8"
    )


def _committed(root: Path, base: str = "1.1.0") -> Path:
    for name in (f"release-evidence-{base}.md", f"release-evidence-report-{base}.json"):
        shutil.copyfile(REPO_ROOT / "docs" / name, root / "docs" / name)
    return root / "docs" / f"release-evidence-{base}.md"


def _set_status(manifest: Path, status: str) -> None:
    text = manifest.read_text(encoding="utf-8")
    flipped, count = re.subn(r"^> \*\*Status:\*\*.*$", status, text, flags=re.MULTILINE)
    assert count == 1, "the committed manifest must have exactly one status line to flip"
    manifest.write_text(flipped, encoding="utf-8")


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
    with pytest.raises(gate.ReleaseGateError, match="level-1 heading"):
        gate.check(root, "v1.1.0")


def test_a_draft_manifest_cannot_release(tmp_path: Path) -> None:
    """Draft rejection, on an independent fixture.

    The committed manifest is not used here: it is meant to change to approved
    at the release cut, and a test that required it to stay a draft would fail
    the very CI run that publishes (Sol re-review, finding 3).
    """
    root = _root(tmp_path, "1.1.0")
    _manifest(root, "1.1.0", "> **Status:** DRAFT — not approved for release")
    with pytest.raises(gate.ReleaseGateError, match="the status line is"):
        gate.check(root, "v1.1.0")


@pytest.mark.parametrize(
    ("version", "tag", "status"),
    [("1.1.0", "v1.1.0", gate.APPROVED), ("1.1.0rc1", "v1.1.0-rc.1", gate.CANDIDATE)],
)
def test_the_committed_manifest_passes_once_its_status_is_flipped(
    tmp_path: Path, version: str, tag: str, status: str
) -> None:
    """The documented cut: change only the status line (and the version), and it passes.

    Holds whatever the committed status is today, so it keeps passing after the
    cut as well as before it.
    """
    root = _root(tmp_path, version)
    _set_status(_committed(root), status)
    result = gate.check(root, tag)
    assert result.manifest == "docs/release-evidence-1.1.0.md"
    assert result.is_candidate == ("rc" in version)


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


@pytest.mark.parametrize(
    "source",
    [
        # Sol re-review, finding 2: Hatchling built 1.2.0 from this; the gate read 1.1.0.
        '(__version__) = "1.1.0"\n__version__ = "1.2.0"\n',
        '__version__ = "1.1.0"\n__version__ = "1.2.0"\n',
        '__version__ = "1.1.0"\nif True:\n    __version__ = "1.2.0"\n',
        '__version__ = "1.1.0"\n__version__ += "-x"\n',
        '__version__ = "1.1.0"\nfor __version__ in ["1.2.0"]:\n    pass\n',
        '__version__ = "1.1.0"\nfrom os import sep as __version__\n',
        '__version__ = __version__ = "1.1.0"\n',
        '__version__: str = "1.1.0"\n',
        '__version__ = ("1.1.0")\n__version__ = "1.2.0"\n',
        '__version__ = "1." + "1.0"\n',
        # Hatchling's regex reads the first matching line, even inside a docstring.
        '"""\n__version__ = "1.2.0"\n"""\n__version__ = "1.1.0"\n',
        "__version__ = '1.1.0'  # fine\nVERSION = '1.2.0'\n",
    ],
)
def test_the_version_must_be_bound_exactly_once_and_unambiguously(
    tmp_path: Path, source: str
) -> None:
    root = _root(tmp_path, "1.1.0")
    (root / "src" / "gpo_studio" / "__init__.py").write_text(source, encoding="utf-8")
    _manifest(root, "1.1.0", gate.APPROVED)
    with pytest.raises(gate.ReleaseGateError, match="exactly once|Hatchling would read"):
        gate.check(root, "v1.1.0")


def test_the_committed_package_version_reads_cleanly() -> None:
    assert gate._VERSION.match(gate.package_version(REPO_ROOT)) is not None


def _wheel(path: Path, version: str, *, name_version: str | None = None) -> Path:
    wheel = path / f"gpo_studio-{name_version or version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            f"gpo_studio-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.4\nName: gpo-studio\nVersion: {version}\n\nbody\n",
        )
    return wheel


def _sdist(path: Path, version: str) -> Path:
    sdist = path / f"gpo_studio-{version}.tar.gz"
    data = f"Metadata-Version: 2.4\nName: gpo-studio\nVersion: {version}\n".encode()
    with tarfile.open(sdist, "w:gz") as archive:
        info = tarfile.TarInfo(f"gpo_studio-{version}/PKG-INFO")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    return sdist


def test_built_distributions_must_carry_the_approved_version(tmp_path: Path) -> None:
    root = _root(tmp_path / "repo", "1.1.0")
    _manifest(root, "1.1.0", gate.APPROVED)
    good_wheel, good_sdist = _wheel(tmp_path, "1.1.0"), _sdist(tmp_path, "1.1.0")
    base = ["--tag", "v1.1.0", "--root", str(root)]
    assert gate.main([*base, "--wheel", str(good_wheel), "--sdist", str(good_sdist)]) == 0

    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(gate.ReleaseGateError, match="do not carry the approved version"):
        gate.verify_distributions("1.1.0", _wheel(other, "1.2.0"), None)
    with pytest.raises(gate.ReleaseGateError, match="do not carry the approved version"):
        gate.verify_distributions("1.1.0", _wheel(other, "1.2.0", name_version="1.1.0"), None)
    with pytest.raises(gate.ReleaseGateError, match="do not carry the approved version"):
        gate.verify_distributions("1.1.0", None, _sdist(other, "1.2.0"))
    assert gate.main([*base, "--wheel", str(_wheel(other, "1.2.0"))]) == 1


def test_a_wheel_without_exactly_one_metadata_version_is_refused(tmp_path: Path) -> None:
    wheel = tmp_path / "gpo_studio-1.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("a.dist-info/METADATA", "Name: gpo-studio\nVersion: 1.1.0\n")
        archive.writestr("b.dist-info/METADATA", "Name: gpo-studio\nVersion: 1.2.0\n")
    with pytest.raises(gate.ReleaseGateError, match="METADATA files"):
        gate.verify_distributions("1.1.0", wheel, None)
    double = tmp_path / "double" / "gpo_studio-1.1.0-py3-none-any.whl"
    double.parent.mkdir()
    with zipfile.ZipFile(double, "w") as archive:
        archive.writestr("x.dist-info/METADATA", "Version: 1.1.0\nVersion: 1.2.0\n")
    with pytest.raises(gate.ReleaseGateError, match="Version headers"):
        gate.verify_distributions("1.1.0", double, None)


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
    with pytest.raises(gate.ReleaseGateError, match="the status line is"):
        gate.check(root, "v1.1.0-rc.2")


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("> **Status:** DRAFT — not approved", "the status line is"),
        ("> **Status:** release candidate; final approval pending", "the status line is"),
        ("> **Status:**  approved for release", "the status line is"),
        ("**Status:** approved for release", "not in a plain paragraph"),
        ("", "exactly one status line"),
    ],
)
def test_a_final_tag_needs_the_exact_approval_marker(
    tmp_path: Path, status: str, reason: str
) -> None:
    root = _root(tmp_path, "1.1.0")
    _manifest(root, "1.1.0", status)
    with pytest.raises(gate.ReleaseGateError, match=reason):
        gate.check(root, "v1.1.0")


# Each of these sat beside a valid approval line. The first three are the
# bypasses the first Sol review demonstrated against the prefix match.
@pytest.mark.parametrize(
    "second",
    [
        " > **Status:** DRAFT",
        "> **STATUS:** DRAFT",
        "    > **Status:** DRAFT",
        "Status: draft",
        "**Status**: draft",
        "> **Status** : draft",
        "> > **status:** draft",
        "- **Status:** DRAFT",
        "> **Status：** DRAFT",
        "> **Ѕtatus:** DRAFT",
        "_Status:_ draft",
        "`Status:` draft",
        "[x]: https://example.invalid 'Status: draft'",
    ],
)
def test_any_second_status_mention_is_refused(tmp_path: Path, second: str) -> None:
    root = _root(tmp_path, "1.1.0")
    _manifest(root, "1.1.0", gate.APPROVED, extra=second)
    with pytest.raises(gate.ReleaseGateError, match="exactly one status line"):
        gate.check(root, "v1.1.0")


def test_an_entity_encoded_second_status_is_counted_in_the_rendered_text(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    _manifest(root, "1.1.0", gate.APPROVED, extra="> **Sta&#116;us:** DRAFT")
    with pytest.raises(gate.ReleaseGateError, match=r"1 in the source .* 2 in the rendered"):
        gate.check(root, "v1.1.0")


@pytest.mark.parametrize(
    "heading",
    [
        "## Status\n\nDRAFT",
        # Third Sol review, finding 2: CommonMark renders these headings as "Status".
        "## Sta&#116;us\n\nDRAFT",
        "## Sta&#x74;us\n\nDRAFT",
        "Sta&#116;us\n------\n\nDRAFT",
        "## Sta&#116;us: DRAFT",
        "### **St&#97;tus**\n\nDRAFT",
    ],
)
def test_a_heading_that_reads_as_status_is_refused(tmp_path: Path, heading: str) -> None:
    root = _root(tmp_path, "1.1.0")
    _manifest(root, "1.1.0", gate.APPROVED, extra=heading)
    with pytest.raises(gate.ReleaseGateError, match="a heading reads"):
        gate.check(root, "v1.1.0")


@pytest.mark.parametrize(
    "char",
    ["​", "‍", "⁠", "﻿", "­", "‮", "⁦", "؜", "\x07"],
)
def test_invisible_and_control_characters_are_refused(tmp_path: Path, char: str) -> None:
    root = _root(tmp_path, "1.1.0")
    _manifest(root, "1.1.0", gate.APPROVED, extra=f"Ordinary prose{char} here.")
    with pytest.raises(gate.ReleaseGateError, match="invisible or control characters"):
        gate.check(root, "v1.1.0")


def test_the_original_document_decides_what_is_code_not_a_folded_copy(tmp_path: Path) -> None:
    """Third Sol review, finding 1: folding must not create a closing fence.

    The full-width backtick run is ordinary text to CommonMark, so the fence
    opened above it runs to the end of the file and swallows the approval and
    the version line. NFKC folds those characters to a real closing fence, so a
    gate that parsed the folded copy saw an approved manifest.
    """
    root = _root(tmp_path, "1.1.0")
    _manifest(root, "1.1.0", "```\nexample\n｀｀｀\n\n" + gate.APPROVED)
    with pytest.raises(gate.ReleaseGateError) as caught:
        gate.check(root, "v1.1.0")
    assert "not in a plain paragraph" in str(caught.value)
    assert "plain bullet-list item" in str(caught.value)


def test_a_zero_width_character_cannot_close_a_fence_either(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    _manifest(root, "1.1.0", "```\nexample\n`​``\n\n" + gate.APPROVED)
    with pytest.raises(gate.ReleaseGateError, match="invisible or control characters"):
        gate.check(root, "v1.1.0")


def test_full_width_punctuation_in_prose_is_still_matched_after_parsing(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    _manifest(root, "1.1.0", gate.APPROVED, extra="Ｓtatus： DRAFT")
    with pytest.raises(gate.ReleaseGateError, match="exactly one status line"):
        gate.check(root, "v1.1.0")


# The approval is the ONLY status mention in each of these, so the count passes;
# the parser must still see that it is not in the manifest's header paragraph.
@pytest.mark.parametrize(
    "status",
    [
        # Sol re-review, finding 1: an approval only inside a quoted code fence.
        "> ```\n> **Status:** approved for release\n> ```",
        "```\n> **Status:** approved for release\n```",
        "~~~\n> **Status:** approved for release\n~~~",
        "    > **Status:** approved for release",
        "> > **Status:** approved for release",
        "- > **Status:** approved for release",
        "> - **Status:** approved for release",
    ],
)
def test_an_approval_outside_the_header_paragraph_does_not_approve(
    tmp_path: Path, status: str
) -> None:
    root = _root(tmp_path, "1.1.0")
    _manifest(root, "1.1.0", status)
    with pytest.raises(gate.ReleaseGateError, match="not in a plain paragraph|the status line is"):
        gate.check(root, "v1.1.0")


@pytest.mark.parametrize(
    "status",
    [
        # Sol re-review, finding 1: approval inside HTML blocks.
        "<details>\n\n> **Status:** approved for release\n\n</details>",
        "<div>\n> **Status:** approved for release\n</div>",
        "<!--\n> **Status:** approved for release\n-->",
        "> **Status:** approved for release <!-- DRAFT -->",
    ],
)
def test_raw_html_is_refused_outright(tmp_path: Path, status: str) -> None:
    root = _root(tmp_path, "1.1.0")
    _manifest(root, "1.1.0", status)
    with pytest.raises(gate.ReleaseGateError, match="raw HTML is not allowed"):
        gate.check(root, "v1.1.0")


def test_a_listed_draft_beside_a_quoted_fence_approval_is_refused(tmp_path: Path) -> None:
    """Sol re-review, finding 1, as reported: a listed DRAFT plus a fenced approval."""
    root = _root(tmp_path, "1.1.0")
    _manifest(
        root,
        "1.1.0",
        "- **Status:** DRAFT",
        extra="> ```\n> > **Status:** approved for release\n> ```",
    )
    with pytest.raises(gate.ReleaseGateError, match="exactly one status line"):
        gate.check(root, "v1.1.0")


def test_an_unclosed_fence_cannot_swallow_the_version_line(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    _manifest(root, "1.1.0", gate.APPROVED, extra="```\nunterminated fence")
    with pytest.raises(gate.ReleaseGateError, match="plain bullet-list item"):
        gate.check(root, "v1.1.0")


def test_prose_that_mentions_status_is_not_a_declaration(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    prose = (
        "The gate reads the status line above.\n"
        "status line changes are recorded in the changelog.\n\n"
        "## Lab status\n\n"
        "```\nprint('no declaration here')\n```\n"
    )
    _manifest(root, "1.1.0", gate.APPROVED, extra=prose)
    assert gate.check(root, "v1.1.0").version == "1.1.0"


def test_the_title_must_name_the_version(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    _manifest(root, "1.1.0", gate.APPROVED, title="# Release evidence manifest — GPO Studio 1.0.0")
    with pytest.raises(gate.ReleaseGateError, match="level-1 heading"):
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
    assert text.count("python scripts/check_release_manifest.py") == 4


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


# --- the remote tag still names the built commit -------------------------------


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Release Gate Test",
        "GIT_AUTHOR_EMAIL": "gate@example.invalid",
        "GIT_COMMITTER_NAME": "Release Gate Test",
        "GIT_COMMITTER_EMAIL": "gate@example.invalid",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    command = [
        "git",
        "-c",
        "core.hooksPath=" + os.devnull,
        "-c",
        "commit.gpgsign=false",
        "-c",
        "tag.gpgsign=false",
        *args,
    ]
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture
def tagged_remote(tmp_path: Path) -> tuple[Path, str, str]:
    """A clone whose ``origin`` is a local bare repository with two commits."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare", "-q")
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q")
    _git(work, "remote", "add", "origin", str(remote))
    (work / "f").write_text("a", encoding="utf-8")
    _git(work, "add", "f")
    _git(work, "commit", "-q", "-m", "a")
    first = _git(work, "rev-parse", "HEAD")
    (work / "f").write_text("b", encoding="utf-8")
    _git(work, "commit", "-q", "-am", "b")
    second = _git(work, "rev-parse", "HEAD")
    _git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    return work, first, second


@pytest.mark.parametrize("annotated", [True, False])
def test_the_remote_tag_must_peel_to_the_built_commit(
    tagged_remote: tuple[Path, str, str], annotated: bool
) -> None:
    work, first, second = tagged_remote
    if annotated:
        _git(work, "tag", "-a", "-m", "release", "v1.1.0", first)
    else:
        _git(work, "tag", "v1.1.0", first)
    _git(work, "push", "-q", "origin", "refs/tags/v1.1.0")
    gate.verify_remote_tag(work, "origin", "v1.1.0", first)

    # Sol's reproduction: the tag is moved to another commit after the run began.
    if annotated:
        _git(work, "tag", "-f", "-a", "-m", "moved", "v1.1.0", second)
    else:
        _git(work, "tag", "-f", "v1.1.0", second)
    _git(work, "push", "-q", "-f", "origin", "refs/tags/v1.1.0")
    with pytest.raises(gate.ReleaseGateError, match="tag moved"):
        gate.verify_remote_tag(work, "origin", "v1.1.0", first)
    gate.verify_remote_tag(work, "origin", "v1.1.0", second)


def test_a_missing_remote_tag_or_unreadable_remote_fails_closed(
    tagged_remote: tuple[Path, str, str],
) -> None:
    work, first, _second = tagged_remote
    with pytest.raises(gate.ReleaseGateError, match="has no refs/tags/v1.1.0"):
        gate.verify_remote_tag(work, "origin", "v1.1.0", first)
    with pytest.raises(gate.ReleaseGateError, match="ls-remote failed"):
        gate.verify_remote_tag(work, "no-such-remote", "v1.1.0", first)
    with pytest.raises(gate.ReleaseGateError, match="not a full SHA-1"):
        gate.verify_remote_tag(work, "origin", "v1.1.0", first[:12])


def test_main_runs_the_remote_tag_check_when_asked(
    tagged_remote: tuple[Path, str, str], tmp_path: Path
) -> None:
    work, first, second = tagged_remote
    root = _root(tmp_path / "manifest-root", "1.1.0")
    _manifest(root, "1.1.0", gate.APPROVED)
    _git(work, "tag", "v1.1.0", second)
    _git(work, "push", "-q", "origin", "refs/tags/v1.1.0")
    remote = str(tmp_path / "remote.git")
    base = ["--tag", "v1.1.0", "--root", str(root), "--remote", remote]
    assert gate.main([*base, "--remote-tag-sha", second]) == 0
    assert gate.main([*base, "--remote-tag-sha", first]) == 1


def test_publish_checks_the_remote_tag_immediately_before_creating_the_release() -> None:
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    publish = _job_block(text, "publish")
    check = publish.index('--remote-tag-sha "${GITHUB_SHA}"')
    create = publish.index('gh release create "$GITHUB_REF_NAME"')
    assert check < create
    assert publish[check:create].count("- name:") == 1, (
        "the tag check must be the step right before publication"
    )
    assert '--remote-tag-sha "${GITHUB_SHA}"' in _job_block(text, "release-identity")


def test_publish_checks_the_built_distributions_before_anything_is_attested() -> None:
    publish = _job_block((WORKFLOWS / "release.yml").read_text(encoding="utf-8"), "publish")
    build = publish.index("python -m build --outdir dist\n")
    check = publish.index('--wheel "${WHEELS[0]}" --sdist "${SDISTS[0]}"')
    attest = publish.index("actions/attest-build-provenance")
    assert build < check < attest


def test_both_gate_jobs_install_the_hash_pinned_parser_first() -> None:
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    install = "pip install --require-hashes -r scripts/release-gate-requirements.txt"
    for job in ("release-identity", "publish"):
        block = _job_block(text, job)
        assert block.index(install) < block.index("python scripts/check_release_manifest.py"), job


def test_the_gate_requirements_match_the_lockfile() -> None:
    import tomllib

    pinned = dict(
        re.findall(
            r"^([A-Za-z0-9_.-]+)==(\S+)",
            (REPO_ROOT / "scripts" / "release-gate-requirements.txt").read_text("utf-8"),
            re.MULTILINE,
        )
    )
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = {pkg["name"]: pkg for pkg in lock["package"]}
    assert set(pinned) == {"markdown-it-py", "mdurl"}
    requirements = (REPO_ROOT / "scripts" / "release-gate-requirements.txt").read_text("utf-8")
    for name, version in pinned.items():
        assert locked[name]["version"] == version, name
        hashes = [locked[name]["sdist"]["hash"]] + [w["hash"] for w in locked[name]["wheels"]]
        for digest in hashes:
            assert f"--hash={digest}" in requirements, (name, digest)
