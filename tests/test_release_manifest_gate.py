"""The release gate binds approval to the version being tagged, and fails closed.

Before this gate, ``release.yml`` grepped ``docs/release-evidence.md`` for an
approval string. That file is the 1.0.0 manifest, so a ``v1.1.0`` tag would
have published on 1.0.0's approval. These tests hold the replacement
(``scripts/check_release_manifest.py``) to refusing that, and hold the
workflow to running it, and the CI and identifier gates, before publishing.

Status and version come from the JSON report's strict schema; the Markdown
manifest is held to a lexical contract on its bytes. Every bypass found
against the earlier rendering-based gate is kept below as a probe.
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


def _release(
    root: Path,
    base: str,
    status: str = "approved",
    *,
    version: str | None = None,
    line5: str | None = None,
    extra: str = "",
    title: str | None = None,
    application: str | None = None,
    report: dict[str, object] | None = None,
) -> Path:
    """Write a manifest and JSON report that pass, unless told otherwise."""
    title = title if title is not None else f"# Release evidence manifest - GPO Studio {base}"
    line5 = line5 if line5 is not None else gate.STATUS_LINES[status]
    application = application if application is not None else base
    manifest = root / "docs" / f"release-evidence-{base}.md"
    manifest.write_bytes(
        (
            f"{title}\n\n> **Date:** 2026-10-31\n> **Source commit:** resolved by the "
            f"workflow\n{line5}\n\nBody text with a [link](other.md) in it.\n\n{extra}\n\n"
            f"## Schema and artifact identity\n\n- Application version: {application}\n"
        ).encode()
    )
    body: dict[str, object] = {
        "report_type": gate.REPORT_TYPE,
        "report_version": gate.REPORT_SCHEMA_VERSION,
        "version": version or base,
        "status": status,
        "manifest": f"docs/release-evidence-{base}.md",
        "workspace_schema_version": 4,
        "artifact_hashes": {key: "pending" for key in gate.ARTIFACT_HASH_KEYS},
        "evidence": {},
    }
    if report is not None:
        body = report
    (root / "docs" / f"release-evidence-report-{base}.json").write_text(
        json.dumps(body), encoding="utf-8"
    )
    return manifest


def _committed(root: Path, base: str = "1.1.0") -> tuple[Path, Path]:
    paths = []
    for name in (f"release-evidence-{base}.md", f"release-evidence-report-{base}.json"):
        shutil.copyfile(REPO_ROOT / "docs" / name, root / "docs" / name)
        paths.append(root / "docs" / name)
    return paths[0], paths[1]


def _approve(manifest: Path, report: Path, status: str, version: str) -> None:
    """The documented approval: the JSON status/version and line 5, nothing else."""
    data = json.loads(report.read_text(encoding="utf-8"))
    data["status"], data["version"] = status, version
    report.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    lines = manifest.read_text(encoding="utf-8").split("\n")
    lines[4] = gate.STATUS_LINES[status]
    manifest.write_text("\n".join(lines), encoding="utf-8")


# --- the stale-manifest hole and the documented transition -----------------


def test_the_base_fixture_passes(tmp_path: Path) -> None:
    """The control: every refusal below is a change to a fixture that passes."""
    root = _root(tmp_path, "1.1.0")
    _release(root, "1.1.0")
    assert gate.check(root, "v1.1.0").manifest == "docs/release-evidence-1.1.0.md"


def test_the_1_0_0_manifest_cannot_approve_a_1_1_0_tag(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    for name in ("release-evidence.md", "release-evidence-report.json"):
        shutil.copyfile(REPO_ROOT / "docs" / name, root / "docs" / name)
    with pytest.raises(gate.ReleaseGateError, match="no evidence manifest for 1.1.0"):
        gate.check(root, "v1.1.0")


def test_a_copied_1_0_0_manifest_is_refused(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    _release(root, "1.1.0")
    shutil.copyfile(
        REPO_ROOT / "docs" / "release-evidence.md", root / "docs" / "release-evidence-1.1.0.md"
    )
    with pytest.raises(gate.ReleaseGateError, match="printable ASCII"):
        gate.check(root, "v1.1.0")


def test_a_draft_cannot_release(tmp_path: Path) -> None:
    """Draft rejection on an independent fixture, never on the committed manifest."""
    root = _root(tmp_path, "1.1.0")
    _release(root, "1.1.0", "draft")
    with pytest.raises(gate.ReleaseGateError, match="status is 'draft'"):
        gate.check(root, "v1.1.0")


def test_the_committed_draft_holds_the_contract_and_is_refused_only_as_a_draft(
    tmp_path: Path,
) -> None:
    """Whatever the committed status is, the files are well formed for it."""
    report = json.loads(
        (REPO_ROOT / "docs" / "release-evidence-report-1.1.0.json").read_text("utf-8")
    )
    problems, _ = gate.report_problems(
        (REPO_ROOT / "docs" / "release-evidence-report-1.1.0.json").read_bytes(),
        "docs/release-evidence-1.1.0.md",
    )
    assert problems == []
    assert gate.manifest_problems(
        (REPO_ROOT / "docs" / "release-evidence-1.1.0.md").read_bytes(), "1.1.0", report["status"]
    ) == []


@pytest.mark.parametrize(
    ("version", "tag", "status"),
    [("1.1.0", "v1.1.0", "approved"), ("1.1.0rc1", "v1.1.0-rc.1", "candidate")],
)
def test_the_committed_files_pass_once_approved_as_documented(
    tmp_path: Path, version: str, tag: str, status: str
) -> None:
    root = _root(tmp_path, version)
    manifest, report = _committed(root)
    _approve(manifest, report, status, version)
    result = gate.check(root, tag)
    assert result.manifest == "docs/release-evidence-1.1.0.md"
    assert result.is_candidate == (status == "candidate")


def test_approving_only_one_of_the_two_files_is_refused(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    _release(root, "1.1.0", "approved", line5=gate.DRAFT)
    with pytest.raises(gate.ReleaseGateError, match="line 5 must be the status line"):
        gate.check(root, "v1.1.0")
    _release(root, "1.1.0", "draft", line5=gate.APPROVED)
    with pytest.raises(gate.ReleaseGateError, match="status is 'draft'"):
        gate.check(root, "v1.1.0")


def test_a_candidate_tag_needs_candidate_status_not_approval(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0rc2")
    _release(root, "1.1.0", "candidate", version="1.1.0rc2")
    assert gate.check(root, "v1.1.0-rc.2").is_candidate
    _release(root, "1.1.0", "approved", version="1.1.0rc2")
    with pytest.raises(gate.ReleaseGateError, match="requires 'candidate'"):
        gate.check(root, "v1.1.0-rc.2")


# --- identity ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "tag"),
    [
        ("1.2.0", "v1.1.0"),
        ("1.1.0", "v1.0.0"),
        ("1.1.0", "1.1.0"),
        ("1.1.0rc1", "v1.1.0"),
        ("1.1.0rc1", "v1.1.0rc1"),
        ("1.1.0", "v1.1.0-rc.1"),
    ],
)
def test_tag_must_equal_the_package_version(tmp_path: Path, version: str, tag: str) -> None:
    root = _root(tmp_path, version)
    _release(root, "1.1.0", "candidate" if "rc" in version else "approved", version=version)
    with pytest.raises(gate.ReleaseGateError, match="does not match package version"):
        gate.check(root, tag)


@pytest.mark.parametrize("version", ["1.1.0.dev1", "1.1.0+local", "1.1", "1.1.0a1", "1.1.0rc0"])
def test_unreleasable_versions_are_refused(tmp_path: Path, version: str) -> None:
    root = _root(tmp_path, version)
    with pytest.raises(gate.ReleaseGateError, match="refusing to release"):
        gate.check(root, f"v{version}")


def test_the_report_version_must_be_the_package_version(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0rc1")
    _release(root, "1.1.0", "candidate", version="1.1.0")
    with pytest.raises(gate.ReleaseGateError, match="but the package is '1.1.0rc1'"):
        gate.check(root, "v1.1.0-rc.1")


@pytest.mark.parametrize(
    "source",
    [
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
        '"""\n__version__ = "1.2.0"\n"""\n__version__ = "1.1.0"\n',
        "__version__ = '1.1.0'  # fine\nVERSION = '1.2.0'\n",
        # Hatchling's pattern is case-insensitive (DeepSeek review, finding 1):
        # it builds 9.9.9 from these although Python assigns 1.1.0.
        'Version = "9.9.9"\n__version__ = "1.1.0"\n',
        '__VERSION__ = "9.9.9"\n__version__ = "1.1.0"\n',
        'version = "9.9.9"\n__version__ = "1.1.0"\n',
        '__Version__ = "1.1.0"\n__version__ = "1.1.0"\n',
    ],
)
def test_the_version_must_be_bound_exactly_once_and_unambiguously(
    tmp_path: Path, source: str
) -> None:
    root = _root(tmp_path, "1.1.0")
    (root / "src" / "gpo_studio" / "__init__.py").write_text(source, encoding="utf-8")
    _release(root, "1.1.0")
    with pytest.raises(gate.ReleaseGateError, match="exactly once|Hatchling would read"):
        gate.check(root, "v1.1.0")


def test_the_hatchling_pattern_is_hatchlings_own() -> None:
    """Copied verbatim from hatchling ``version/core.py`` DEFAULT_PATTERN."""
    assert gate.HATCHLING_DEFAULT_PATTERN == (
        r"""(?i)^(__version__|VERSION) *= *(['"])v?(?P<version>.+?)\2"""
    )
    assert gate._HATCH_VERSION_LINE.flags & re.IGNORECASE


@pytest.mark.parametrize(
    "content",
    [b"__version__ = (\n", b'__version__ = "1.1.0"\n\xff\xfe\n', b"\x00__version__ = '1.1.0'\n"],
)
def test_an_unreadable_version_file_is_a_clean_gate_failure(
    tmp_path: Path, content: bytes, capsys: pytest.CaptureFixture[str]
) -> None:
    """DeepSeek review, finding 2: no traceback, a named gate failure."""
    root = _root(tmp_path, "1.1.0")
    _release(root, "1.1.0")
    (root / "src" / "gpo_studio" / "__init__.py").write_bytes(content)
    with pytest.raises(gate.ReleaseGateError, match="cannot read the package version"):
        gate.check(root, "v1.1.0")
    assert gate.main(["--tag", "v1.1.0", "--root", str(root)]) == 1
    error = capsys.readouterr().err
    assert error.startswith("release gate: FAIL: cannot read the package version")
    assert "Traceback" not in error


def test_a_missing_version_file_is_a_clean_gate_failure(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    (root / "src" / "gpo_studio" / "__init__.py").unlink()
    with pytest.raises(gate.ReleaseGateError, match="cannot read the package version"):
        gate.check(root, "v1.1.0")


def test_an_already_released_version_is_refused_with_its_reason(tmp_path: Path) -> None:
    """DeepSeek review, finding 3: 1.0.0 is refused explicitly, not by accident."""
    root = _root(tmp_path, "1.0.0")
    for name in ("release-evidence.md", "release-evidence-report.json"):
        shutil.copyfile(REPO_ROOT / "docs" / name, root / "docs" / name)
    with pytest.raises(gate.ReleaseGateError, match="already released 2026-07-18"):
        gate.check(root, "v1.0.0")
    assert gate.RELEASED == {"1.0.0": "released 2026-07-18 from docs/release-evidence.md"}


@pytest.mark.parametrize(
    ("extra", "rule"),
    [
        ("AT&amp;T", "rule no-entities"),
        ("the list[0] entry", "rule plain-links"),
        ("see <docs>", "rule no-html"),
        ("Status of X: open", "rule one-status"),
        ("a caf\u00e9", "rule ascii"),
    ],
)
def test_every_refusal_names_its_rule_and_a_rephrasing(
    tmp_path: Path, extra: str, rule: str
) -> None:
    """DeepSeek review, finding 4: strict, but the error says what to do."""
    root = _root(tmp_path, "1.1.0")
    _release(root, "1.1.0", extra=extra)
    with pytest.raises(gate.ReleaseGateError, match=rule) as caught:
        gate.check(root, "v1.1.0")
    hints = ("Use ", "Write ", "rephrase", "Replace", "Type ")
    assert any(hint in str(caught.value) for hint in hints)


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
    _release(root, "1.1.0")
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


# --- the JSON report's strict schema -------------------------------------------


def _report(**changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "report_type": gate.REPORT_TYPE,
        "report_version": gate.REPORT_SCHEMA_VERSION,
        "version": "1.1.0",
        "status": "approved",
        "manifest": "docs/release-evidence-1.1.0.md",
        "workspace_schema_version": 4,
        "artifact_hashes": {key: "pending" for key in gate.ARTIFACT_HASH_KEYS},
        "evidence": {},
    }
    for key, value in changes.items():
        if value is _DROP:
            body.pop(key)
        else:
            body[key] = value
    return body


_DROP = object()


@pytest.mark.parametrize(
    "report",
    [
        _report(status=_DROP),
        _report(extra="x"),
        _report(release_version="1.1.0"),
        _report(report_type="something else"),
        _report(report_version=True),
        _report(report_version=1),
        _report(version=110),
        _report(version="v1.1.0"),
        _report(status="Approved"),
        _report(status="final"),
        _report(manifest="docs/release-evidence.md"),
        _report(workspace_schema_version="4"),
        _report(workspace_schema_version=0),
        _report(artifact_hashes={"wheel_sha256": "x"}),
        _report(artifact_hashes={**{k: "x" for k in gate.ARTIFACT_HASH_KEYS}, "more": "x"}),
        _report(artifact_hashes={k: 1 for k in gate.ARTIFACT_HASH_KEYS}),
        _report(evidence=[]),
    ],
)
def test_the_report_schema_is_exact(tmp_path: Path, report: dict[str, object]) -> None:
    root = _root(tmp_path, "1.1.0")
    _release(root, "1.1.0", report=report)
    with pytest.raises(gate.ReleaseGateError, match="release-evidence-report-1.1.0.json"):
        gate.check(root, "v1.1.0")


@pytest.mark.parametrize(
    "raw",
    [
        "{not json",
        "[]",
        '{"status": "draft", "status": "approved"}',
        '{"workspace_schema_version": NaN}',
        b"\xff\xfe".decode("latin-1"),
    ],
)
def test_malformed_or_ambiguous_json_is_refused(tmp_path: Path, raw: str) -> None:
    root = _root(tmp_path, "1.1.0")
    _release(root, "1.1.0")
    (root / "docs" / "release-evidence-report-1.1.0.json").write_text(raw, encoding="utf-8")
    with pytest.raises(gate.ReleaseGateError):
        gate.check(root, "v1.1.0")


def test_a_duplicate_status_key_is_named(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    _release(root, "1.1.0")
    path = root / "docs" / "release-evidence-report-1.1.0.json"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('"status": "approved"', '"status": "draft", "status": "approved"'))
    with pytest.raises(gate.ReleaseGateError, match="duplicate JSON keys"):
        gate.check(root, "v1.1.0")


def test_a_missing_report_is_refused(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    _release(root, "1.1.0")
    (root / "docs" / "release-evidence-report-1.1.0.json").unlink()
    with pytest.raises(gate.ReleaseGateError, match="no evidence report"):
        gate.check(root, "v1.1.0")


# --- the manifest's lexical contract: every bypass from every review round ------
#
# Each probe is appended to a manifest that otherwise passes (approved JSON,
# approved line 5). Rounds: (1) prefix-match bypasses, (2) CommonMark context
# bypasses, (3) folding and entity headings, (4) linked headings and
# entity-encoded invisible characters.
BYPASS_PROBES = [
    # round 1
    " > **Status:** DRAFT",
    "> **STATUS:** DRAFT",
    "    > **Status:** DRAFT",
    "```\n> **Status:** approved for release\n```",
    # variants added with round 1
    "Status: draft",
    "**Status**: draft",
    "> **Status** : draft",
    "> > **status:** draft",
    "- **Status:** DRAFT",
    "> **Sta​tus:** DRAFT",
    "> **Status：** DRAFT",
    "> **Ѕtatus:** DRAFT",
    "_Status:_ draft",
    "`Status:` draft",
    "[x]: https://example.invalid 'Status: draft'",
    "> **Sta&#116;us:** DRAFT",
    "## Status\n\nDRAFT",
    "~~~markdown\n> **Status:** DRAFT\n~~~",
    "```yaml\nstatus: draft\n```",
    "<!-- > **Status:** DRAFT -->",
    "<!--\n> **Status:** DRAFT\n-->",
    "```\nunterminated fence",
    "<!-- unterminated comment",
    # round 2 (the quoted fence now carries ordinary text, so the probe tests
    # fence rejection itself rather than a second status declaration)
    "> ```\n> ordinary example\n> ```",
    "- **Status:** DRAFT\n\n> ```\n> > **Status:** approved for release\n> ```",
    "<details>\n\n> **Status:** approved for release\n\n</details>",
    "<div>\n> **Status:** approved for release\n</div>",
    "> **Status:** approved for release <!-- DRAFT -->",
    "- > **Status:** approved for release",
    "> - **Status:** approved for release",
    # round 3
    "```\nexample\n｀｀｀\n\n" + "> **Status:** approved for release",
    "```\nexample\n`​``\n",
    "## Sta&#116;us\n\nDRAFT",
    "## Sta&#x74;us\n\nDRAFT",
    "Sta&#116;us\n------\n\nDRAFT",
    "## Sta&#116;us: DRAFT",
    "### **St&#97;tus**\n\nDRAFT",
    "Ordinary prose‮ here.",
    "Ordinary prose\x07 here.",
    # round 4: linked headings
    "## [Status](https://example.invalid)\n\nDRAFT",
    "## [Status][r]\n\n[r]: https://example.invalid\n\nDRAFT",
    "## [Sta&#116;us](https://example.invalid)\n\nDRAFT",
    "[Status](https://example.invalid)\n------\n\nDRAFT",
    "## [S](https://example.invalid)tatus\n\nDRAFT",
    "## S[tatus](https://example.invalid)\n\nDRAFT",
    "## Sta[t](https://example.invalid)us\n\nDRAFT",
    "## [Status](https://example.invalid \"title\")\n\nDRAFT",
    "## [Status](<https://example.invalid>)\n\nDRAFT",
    # round 4: entity-encoded invisible characters
    "> **Sta&#8203;tus:** DRAFT",
    "> **Sta&#x200B;tus:** DRAFT",
    "> **Sta&shy;tus:** DRAFT",
    "> **Sta&ZeroWidthSpace;tus:** DRAFT",
    "## Sta&#x202E;tus\n\nDRAFT",
    "## Sta&#xE000;tus\n\nDRAFT",
    "Prose &#7; with a control character.",
    # and the class beyond: other ways to say it without a colon after the word
    "**Status** DRAFT",
    "Status of this release: DRAFT",
    "## Release status\n\nDRAFT",
    "> Stat**us**: DRAFT",
    "> Sta`t`us: DRAFT",
    "> Status\\: DRAFT",
    "Status\r\nDRAFT",
    "\tStatus: DRAFT",
]


@pytest.mark.parametrize("probe", BYPASS_PROBES)
def test_every_bypass_from_every_round_is_refused(tmp_path: Path, probe: str) -> None:
    root = _root(tmp_path, "1.1.0")
    _release(root, "1.1.0", extra=probe)
    with pytest.raises(gate.ReleaseGateError, match="release-evidence-1.1.0.md"):
        gate.check(root, "v1.1.0")


# Sol, 764fb29: code blocks inside containers. Ordinary text only, so the only
# rule that can refuse these is no-code-blocks.
NESTED_CODE_PROBES = [
    "> ```\n> ordinary example\n> ```",
    "> ~~~\n> ordinary example\n> ~~~",
    ">     ordinary example",
    "> >     ordinary example",
    "> > ```\n> > ordinary example\n> > ```",
    "- ```\n  ordinary example\n  ```",
    "-     ordinary example",
    "* ~~~\n  ordinary example\n  ~~~",
    "1. ```\n   ordinary example\n   ```",
    "1)     ordinary example",
    "> - ```\n>   ordinary example\n>   ```",
    "- > ```\n  > ordinary example\n  > ```",
    "   > ```\n   > ordinary example\n   > ```",
]


@pytest.mark.parametrize("probe", NESTED_CODE_PROBES)
def test_code_blocks_inside_quotes_and_lists_are_refused(tmp_path: Path, probe: str) -> None:
    root = _root(tmp_path, "1.1.0")
    _release(root, "1.1.0", extra=probe)
    with pytest.raises(gate.ReleaseGateError) as caught:
        gate.check(root, "v1.1.0")
    message = str(caught.value)
    assert "rule no-code-blocks" in message
    assert "one-status" not in message


@pytest.mark.parametrize(
    "prose",
    [
        "> An ordinary quoted paragraph with `inline code`.",
        "> > A nested quote.",
        "- A list item\n  with a continuation line.",
        "1. A numbered step\n   with a continuation.\n   - and a nested item",
        "**Bold** at the start of a line, and 1.5 million, and a rule:\n\n---",
        "*Emphasis* starting a line.",
    ],
)
def test_ordinary_quotes_and_lists_still_pass(tmp_path: Path, prose: str) -> None:
    root = _root(tmp_path, "1.1.0")
    _release(root, "1.1.0", extra=prose)
    assert gate.check(root, "v1.1.0").version == "1.1.0"


@pytest.mark.parametrize(
    "line5",
    [
        " > **Status:** approved for release",
        "> **Status:**  approved for release",
        "> **status:** approved for release",
        "> **Status:** approved for release ",
        "    > **Status:** approved for release",
        "> **Status:** release candidate; final approval pending",
        "",
    ],
)
def test_line_5_must_be_exactly_the_status_line(tmp_path: Path, line5: str) -> None:
    root = _root(tmp_path, "1.1.0")
    _release(root, "1.1.0", line5=line5)
    with pytest.raises(gate.ReleaseGateError, match="line 5 must be the status line"):
        gate.check(root, "v1.1.0")


def test_the_status_line_cannot_move_out_of_the_header(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    manifest = _release(root, "1.1.0")
    lines = manifest.read_text(encoding="utf-8").split("\n")
    lines.insert(4, "> **Owner:** release manager")
    manifest.write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises(gate.ReleaseGateError, match="line 5 must be the status line"):
        gate.check(root, "v1.1.0")


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"title": "# Release evidence manifest - GPO Studio 1.0.0"}, "the header must be"),
        ({"title": "# Release evidence manifest — GPO Studio 1.1.0"}, "printable ASCII"),
        ({"application": "1.0.0"}, "Application version"),
    ],
)
def test_the_header_and_version_line_are_fixed(
    tmp_path: Path, change: dict[str, str], reason: str
) -> None:
    root = _root(tmp_path, "1.1.0")
    _release(root, "1.1.0", **change)  # type: ignore[arg-type]
    with pytest.raises(gate.ReleaseGateError, match=reason):
        gate.check(root, "v1.1.0")


def test_prose_that_mentions_status_without_declaring_one_passes(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    prose = (
        "The gate reads the status line above, and the [status table](table.md) too.\n"
        "Plan statuses are recorded elsewhere; see [the plan][p].\n\n"
        "[p]: plans/034.md\n\n"
        "## Lab results\n\n"
        "- A list item with `inline code` and **bold** text.\n"
    )
    _release(root, "1.1.0", extra=prose)
    assert gate.check(root, "v1.1.0").version == "1.1.0"


def test_main_fails_closed_and_writes_outputs_only_on_success(tmp_path: Path) -> None:
    root = _root(tmp_path, "1.1.0")
    outputs = tmp_path / "github-output"
    assert gate.main(["--tag", "v1.1.0", "--root", str(root), "--github-output", str(outputs)]) == 1
    assert not outputs.exists()
    _release(root, "1.1.0")
    assert gate.main(["--tag", "v1.1.0", "--root", str(root), "--github-output", str(outputs)]) == 0
    assert outputs.read_text(encoding="utf-8").splitlines() == [
        "version=1.1.0",
        "manifest=docs/release-evidence-1.1.0.md",
        "report=docs/release-evidence-report-1.1.0.json",
        "prerelease=false",
    ]


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
    _release(root, "1.1.0")
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
