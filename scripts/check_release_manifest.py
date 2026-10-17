"""Release gate: the pushed tag, the package version and its release evidence agree.

The 1.0.0 workflow grepped ``docs/release-evidence.md`` for an approval
string. That file is the 1.0.0 manifest, so any later tag would have passed on
1.0.0's approval. This gate binds approval to the version being released.

**The JSON report is the source of truth.** ``docs/release-evidence-report-
<X.Y.Z>.json`` must match a strict schema (exact keys, exact types, no
duplicate keys): ``version`` equals ``__version__`` (and so the tag and, with
``--wheel``/``--sdist``, the built artifacts), and ``status`` is ``approved``
for a final tag or ``candidate`` for an ``-rc.N`` tag (``draft`` never
releases).

**The Markdown manifest is held to a lexical contract, not rendered.** Four
review rounds found ways for Markdown rendering (entities, HTML, nested
fences, links, Unicode folding) to make what a reader sees differ from what a
parser sees. Rather than chase the renderer, the gate forbids every construct
that made that possible and checks the bytes:

* printable ASCII and LF only (no tabs, CR, control or non-ASCII characters);
* no ``<`` (so no raw HTML or autolinks) and no character references
  (``&#...``, ``&name;``);
* no code fences and no line indented four or more spaces, also after
  blockquote and list markers (so no code blocks, nested or not);
* links only in the plain forms ``[text](destination)`` and ``[text][ref]``,
  with no parentheses or whitespace in the destination and never glued to a
  letter or digit on either side (so a link cannot splice a word together);
* the header is fixed: line 1 the title, line 2 blank, lines 3 and 4 the Date
  and Source commit lines, line 5 the status line, line 6 blank. The status
  line must be the one for the JSON report's ``status``;
* after removing link destinations and emphasis/code markers, the word
  "status" followed by a colon appears exactly once (line 5), and no other
  line, heading or not, begins with the word "status" or is a heading
  containing it.

To approve a release: set ``"status": "approved"`` (or ``"candidate"`` for an
RC, with ``"version"`` set to the RC version) in the JSON report, and change
line 5 of the manifest to the matching line below. Nothing else changes. For a
final release the report's ``freeze`` was already set when the freeze batch
was banked (see below), and ``approved`` is refused without it.

Other checks: ``__version__`` is bound exactly once and Hatchling reads the
same value; with ``--remote-tag-sha`` the remote tag still peels to the built
commit; ``--github-output`` writes the resolved paths for later steps.

**A final tag is also held to its freeze commit** (owner decision 2026-10-17,
``plans/036-change-cost.md`` D-9 addendum). The report's ``freeze`` names the
commit ``F`` every lane was requalified at and the batch manifest that banked
the runs; an ``approved`` report must carry it. For a final tag ``vX.Y.Z``,
and only then, ``main`` runs :func:`verify_freeze` on the checkout, which
needs full history (``fetch-depth: 0``):

* ``F`` is in the clone and is an ancestor of the tagged commit (``HEAD``),
  and the clone is not shallow;
* ``git diff F HEAD`` touches only :data:`FREEZE_RECORDS` (modified, never
  added or deleted) and the files the batch manifest banks (added, never
  modified): its packs, its post-batch directory check, the manifest and its
  note. Everything else (``src/``, ``scripts/``, ``pyproject.toml``,
  ``uv.lock``, ``.github/``, ``.gitattributes``, every other test) must be
  byte-identical to ``F``, and every changed path must be a regular file
  (mode 100644);
* ``tests/test_committed_evidence.py`` may differ from ``F`` only in its
  verdict tables (:data:`REGISTRY_TABLES`), compared by AST;
* every live verdict that registry names, and the WP-0 manifest, was earned
  at ``F`` exactly (``source.commit``, a clean tree, a pass), is a run of the
  batch, whose files hash to what the batch manifest recorded; and the batch
  covers every lane live at ``F``, with no lane left out.

Release candidate tags are not subject to it.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any

APPROVED = "> **Status:** approved for release"
CANDIDATE = "> **Status:** release candidate; final approval pending"
DRAFT = "> **Status:** draft; not approved for release and not a release candidate"
STATUS_LINES = {"draft": DRAFT, "candidate": CANDIDATE, "approved": APPROVED}

REPORT_TYPE = "GPO Studio release evidence report"
# 3 (2026-10-17): adds ``freeze``, the freeze commit and its batch manifest.
REPORT_SCHEMA_VERSION = 3
REPORT_KEYS = frozenset(
    {
        "report_type",
        "report_version",
        "version",
        "status",
        "manifest",
        "workspace_schema_version",
        "artifact_hashes",
        "freeze",
        "evidence",
    }
)
ARTIFACT_HASH_KEYS = frozenset({"wheel_sha256", "sdist_sha256", "sbom_sha256"})
#: ``freeze`` is ``null`` until the batch at ``F`` is banked, then exactly these.
FREEZE_KEYS = frozenset({"commit", "batch_manifest"})
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_BATCH_MANIFEST = re.compile(r"^docs/plan-033/[a-z0-9][a-z0-9.-]*-batch\.json$")

_ALLOWED_BYTES = frozenset(range(0x20, 0x7F)) | {0x0A}
_ENTITY = re.compile(r"&(?:#|[A-Za-z][A-Za-z0-9]*;)")
_FENCE = re.compile(r"^ *(?:`{3,}|~{3,})")
_DEEP_INDENT = re.compile(r"^ {4,}\S")
# One container marker: a blockquote '>' or a list marker, with the spaces
# after it. Stripped repeatedly before looking for code, because
# "> ```" and ">     text" are code blocks inside a quote (Sol, 764fb29).
_CONTAINER = re.compile(r"^ {0,3}(>|[-*+]|\d{1,9}[.)])( *)")
_INLINE_LINK = re.compile(r"\]\(([^()\s]*)\)")
_REFERENCE_LINK = re.compile(r"\]\[([^\[\]]*)\]")
_GLUED_OPEN = re.compile(r"[A-Za-z0-9]!?\[")
_MARKUP = str.maketrans("", "", "*_`~\\[]!")
_STATUS_MENTION = re.compile(r"status *:", re.IGNORECASE)
_BLOCK_PREFIX = r"(?: {0,3}(?:>|[-*+]|\d{1,9}[.)])(?: |$))*"
_ATX_HEADING = re.compile(rf"^{_BLOCK_PREFIX} {{0,3}}#{{1,6}}(?: |$)")
_SETEXT_UNDERLINE = re.compile(rf"^{_BLOCK_PREFIX} {{0,3}}(?:=+|-+) *$")
_LEADING_MARKERS = re.compile(r"^[\s>#+\-*\d.)]*")

STATUS_LINE_INDEX = 4  # line 5


def _container_content(line: str) -> tuple[str, bool]:
    """Strip blockquote and list markers; report whether any opened a code block.

    A list marker counts only when a space or the end of the line follows it,
    so ``**bold**``, ``1.5`` and ``---`` are content, not containers. Four or
    more spaces after a marker are indented code in CommonMark (strictly, a
    blockquote marker eats one of them; refusing four is the stricter reading).
    """
    rest = line
    while True:
        match = _CONTAINER.match(rest)
        if match is None:
            return rest, False
        marker, spaces = match.groups()
        if marker != ">" and not spaces and match.end() < len(rest):
            return rest, False
        if len(spaces) >= 4:
            return rest, True
        rest = rest[match.end() :]


def _skeleton(line: str) -> str:
    """The line with link destinations and emphasis/code/escape markers removed."""
    return _REFERENCE_LINK.sub("", _INLINE_LINK.sub("", line)).translate(_MARKUP)


def manifest_problems(data: bytes, base: str, status: str) -> list[str]:
    """Why ``data`` breaks the manifest's lexical contract (empty list: it holds)."""
    problems: list[str] = []
    bad = sorted({f"0x{byte:02x}" for byte in data if byte not in _ALLOWED_BYTES})
    if bad:
        problems.append(
            f"rule ascii: only printable ASCII and LF are allowed; found bytes {bad}. "
            "Replace dashes, quotes and arrows with ASCII (-, ', ->), use spaces not "
            "tabs, and save with LF line endings"
        )
        return problems
    text = data.decode("ascii")
    lines = text.split("\n")
    if "<" in text:
        problems.append(
            "rule no-html: '<' is not allowed (no raw HTML or autolinks). Write 'less "
            "than', or 'X.Y.Z' instead of '<version>', and use [text](url) for links"
        )
    if _ENTITY.search(text):
        problems.append(
            "rule no-entities: character references ('&#...;', '&name;') are not allowed. "
            "Type the character itself (ASCII only) or spell it out, e.g. 'and' for '&amp;'"
        )
    for number, line in enumerate(lines, start=1):
        content, indented_code = _container_content(line)
        if _FENCE.match(line) or _FENCE.match(content):
            problems.append(
                f"line {number}: rule no-code-blocks: code fences are not allowed. "
                "Use `inline code` on ordinary lines instead"
            )
        if _DEEP_INDENT.match(line) or indented_code or _DEEP_INDENT.match(content):
            problems.append(
                f"line {number}: rule no-code-blocks: indentation of four or more spaces "
                "makes a code block. Indent list continuations by two or three spaces"
            )
        if line.count("](") != len(_INLINE_LINK.findall(line)):
            problems.append(
                f"line {number}: rule plain-links: a link destination contains '(', "
                "whitespace or a title. Use [text](path) with a bare path or URL"
            )
        for pattern in (_INLINE_LINK, _REFERENCE_LINK):
            for match in pattern.finditer(line):
                after = line[match.end() : match.end() + 1]
                if after.isalnum():
                    problems.append(
                        f"line {number}: rule plain-links: a link is glued to the "
                        "following word. Put a space or punctuation after the link"
                    )
        if _GLUED_OPEN.search(line):
            problems.append(
                f"line {number}: rule plain-links: '[' follows a letter or digit, "
                "which could splice a link into a word. Put a space before the '[' or "
                "rephrase, e.g. 'item 0 of the list' (this applies inside `code` too)"
            )

    title = f"# Release evidence manifest - GPO Studio {base}"
    header_ok = (
        len(lines) > 5
        and lines[0] == title
        and lines[1] == ""
        and lines[2].startswith("> **Date:** ")
        and lines[3].startswith("> **Source commit:** ")
        and lines[5] == ""
    )
    if not header_ok:
        problems.append(
            f"the header must be: {title!r}, a blank line, '> **Date:** ...', "
            "'> **Source commit:** ...', the status line, a blank line"
        )
    expected = STATUS_LINES[status]
    if len(lines) <= STATUS_LINE_INDEX or lines[STATUS_LINE_INDEX] != expected:
        found = lines[STATUS_LINE_INDEX] if len(lines) > STATUS_LINE_INDEX else None
        problems.append(
            f"line 5 must be the status line for report status {status!r}, "
            f"{expected!r}; found {found!r}"
        )

    mentions = [
        number
        for number, line in enumerate(lines, start=1)
        for _ in _STATUS_MENTION.finditer(_skeleton(line))
    ]
    if mentions != [STATUS_LINE_INDEX + 1]:
        problems.append(
            f"rule one-status: 'status:' must appear exactly once, on line 5; found it on "
            f"lines {mentions}. Rephrase elsewhere without a colon after the word, "
            "e.g. 'the state of X' or 'X is open'"
        )
    for index, line in enumerate(lines):
        if index == STATUS_LINE_INDEX:
            continue
        words = re.findall(r"[a-z]+", _skeleton(line).lower())
        heading = bool(_ATX_HEADING.match(line)) or (
            index + 1 < len(lines) and bool(_SETEXT_UNDERLINE.match(lines[index + 1]))
        )
        leading = re.findall(r"[a-z]+", _LEADING_MARKERS.sub("", _skeleton(line)).lower())
        if (heading and "status" in words) or leading[:1] == ["status"]:
            problems.append(
                f"line {index + 1}: rule one-status: reads as a status declaration: "
                f"{line!r}. Do not start a line or name a heading with 'status'; "
                "rephrase, e.g. 'Where X stands' or 'Open items'"
            )

    application = f"- Application version: {base}"
    if lines.count(application) != 1:
        problems.append(f"{application!r} must appear exactly once")
    return problems


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    duplicated = sorted({key for key in keys if keys.count(key) > 1})
    if duplicated:
        raise ReleaseGateError(f"duplicate JSON keys {duplicated}")
    return dict(pairs)


def _reject_constant(name: str) -> None:
    raise ReleaseGateError(f"JSON constant {name} is not allowed")


def report_problems(data: bytes, manifest_rel: str) -> tuple[list[str], dict[str, Any]]:
    """Validate the JSON report against the strict schema."""
    try:
        report = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_no_duplicates,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        return [f"not valid UTF-8 JSON: {error}"], {}
    except ReleaseGateError as error:
        return [str(error)], {}
    if not isinstance(report, dict):
        return ["the report must be a JSON object"], {}
    problems: list[str] = []
    if set(report) != REPORT_KEYS:
        problems.append(
            f"keys must be exactly {sorted(REPORT_KEYS)}; missing "
            f"{sorted(REPORT_KEYS - set(report))}, unexpected {sorted(set(report) - REPORT_KEYS)}"
        )
        return problems, report

    def is_str(value: Any) -> bool:
        return type(value) is str

    if report["report_type"] != REPORT_TYPE:
        problems.append(f"report_type must be {REPORT_TYPE!r}")
    if type(report["report_version"]) is not int or report["report_version"] != (
        REPORT_SCHEMA_VERSION
    ):
        problems.append(f"report_version must be the integer {REPORT_SCHEMA_VERSION}")
    if not is_str(report["version"]) or _VERSION.match(report["version"]) is None:
        problems.append("version must be a string 'X.Y.Z' or 'X.Y.ZrcN'")
    if not is_str(report["status"]) or report["status"] not in STATUS_LINES:
        problems.append(f"status must be one of {sorted(STATUS_LINES)}")
    if report["manifest"] != manifest_rel:
        problems.append(f"manifest must be {manifest_rel!r}")
    if type(report["workspace_schema_version"]) is not int or report[
        "workspace_schema_version"
    ] < 1:
        problems.append("workspace_schema_version must be a positive integer")
    hashes = report["artifact_hashes"]
    if (
        not isinstance(hashes, dict)
        or set(hashes) != ARTIFACT_HASH_KEYS
        or not all(is_str(value) for value in hashes.values())
    ):
        problems.append(
            f"artifact_hashes must have exactly the string keys {sorted(ARTIFACT_HASH_KEYS)}"
        )
    freeze = report["freeze"]
    if freeze is not None and (
        not isinstance(freeze, dict)
        or set(freeze) != FREEZE_KEYS
        or not is_str(freeze["commit"])
        or _FULL_SHA.match(freeze["commit"]) is None
        or not is_str(freeze["batch_manifest"])
        or _BATCH_MANIFEST.match(freeze["batch_manifest"]) is None
    ):
        problems.append(
            "freeze must be null or an object with exactly 'commit' (a full lower-case "
            "40-hex commit) and 'batch_manifest' (docs/plan-033/NAME-batch.json)"
        )
    if report["status"] == "approved" and freeze is None:
        problems.append(
            "an approved report must record its freeze: the commit F every lane was "
            "requalified at and the batch manifest that banked the runs"
        )
    if not isinstance(report["evidence"], dict):
        problems.append("evidence must be a JSON object")
    return problems, report


_VERSION = re.compile(r"^(?P<base>\d+\.\d+\.\d+)(?:rc(?P<rc>[1-9]\d*))?$")
_TAG = re.compile(r"^v(?P<base>\d+\.\d+\.\d+)(?:-rc\.(?P<rc>[1-9]\d*))?$")


class ReleaseGateError(Exception):
    """The release must not be published."""


@dataclass(frozen=True)
class ReleaseManifest:
    version: str
    base_version: str
    is_candidate: bool
    manifest: str
    report: str
    #: The report's ``freeze``: the freeze commit and the batch manifest path.
    freeze_commit: str | None = None
    batch_manifest: str | None = None


# Hatchling's regex version source (``[tool.hatch.version] path``) reads the
# first line matching this, which need not be what Python executes: a line inside
# a docstring matches it, and a parenthesised assignment does not. Copied from
# hatchling's ``version/core.py`` DEFAULT_PATTERN, ``(?i)`` included, so
# ``Version = ...`` and ``__VERSION__ = ...`` count exactly as Hatchling counts them.
HATCHLING_DEFAULT_PATTERN = r"""(?i)^(__version__|VERSION) *= *(['"])v?(?P<version>.+?)\2"""
_HATCH_VERSION_LINE = re.compile(HATCHLING_DEFAULT_PATTERN, re.MULTILINE)


def _binds_version(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "__version__" and isinstance(node.ctx, (ast.Store, ast.Del))
    if isinstance(node, ast.alias):
        return (node.asname or node.name) == "__version__"
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return "__version__" in node.names
    return False


def package_version(root: Path) -> str:
    """Read ``__version__`` without importing the package, refusing ambiguity.

    There must be exactly one binding of ``__version__`` anywhere in the module
    (assignment, annotated or augmented assignment, tuple target, ``for``,
    ``with``, walrus, import alias or ``global``), a plain module-level
    ``__version__ = "<literal>"``, and it must be the only line Hatchling's
    version regex matches. The built wheel's metadata is also checked against
    the approved version (``--wheel``), so this is the early half of the check.
    """
    path = root / "src" / "gpo_studio" / "__init__.py"
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as error:
        raise ReleaseGateError(
            f"cannot read the package version from {path.name}: "
            f"{type(error).__name__}: {error}"
        ) from error
    bindings = [node for node in ast.walk(tree) if _binds_version(node)]
    simple = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "__version__"
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    if len(bindings) != 1 or len(simple) != 1:
        raise ReleaseGateError(
            f"{path.name} must bind __version__ exactly once, as a plain string assignment; "
            f"found {len(bindings)} binding(s), {len(simple)} plain"
        )
    node = simple[0]
    assert isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
    version = node.value.value
    hatch = [match["version"] for match in _HATCH_VERSION_LINE.finditer(source)]
    if hatch != [version]:
        raise ReleaseGateError(
            f"{path.name}: Hatchling would read {hatch!r} as the version, "
            f"but Python assigns {version!r}"
        )
    return version


def distribution_versions(wheel: Path | None, sdist: Path | None) -> dict[str, str]:
    """The version recorded in a built wheel's METADATA and sdist's PKG-INFO."""
    found: dict[str, str] = {}
    if wheel is not None:
        with zipfile.ZipFile(wheel) as archive:
            names = [n for n in archive.namelist() if re.fullmatch(r"[^/]+\.dist-info/METADATA", n)]
            if len(names) != 1:
                raise ReleaseGateError(f"{wheel.name} has {len(names)} dist-info METADATA files")
            found[f"{wheel.name} METADATA"] = _metadata_version(archive.read(names[0]))
        match = re.fullmatch(r"gpo_studio-([^-]+)-.+\.whl", wheel.name)
        if match is None:
            raise ReleaseGateError(f"unexpected wheel file name {wheel.name!r}")
        found[f"{wheel.name} file name"] = match.group(1)
    if sdist is not None:
        with tarfile.open(sdist) as archive:
            names = [
                m.name for m in archive.getmembers() if re.fullmatch(r"[^/]+/PKG-INFO", m.name)
            ]
            if len(names) != 1:
                raise ReleaseGateError(f"{sdist.name} has {len(names)} top-level PKG-INFO files")
            member = archive.extractfile(names[0])
            if member is None:
                raise ReleaseGateError(f"{sdist.name}: PKG-INFO is not a regular file")
            found[f"{sdist.name} PKG-INFO"] = _metadata_version(member.read())
    return found


def _metadata_version(data: bytes) -> str:
    message = BytesParser().parsebytes(data, headersonly=True)
    versions = message.get_all("Version") or []
    if len(versions) != 1:
        raise ReleaseGateError(f"metadata carries {len(versions)} Version headers")
    return str(versions[0]).strip()


def verify_distributions(version: str, wheel: Path | None, sdist: Path | None) -> None:
    """Every built artifact must carry the version the manifest approved."""
    if wheel is None and sdist is None:
        return
    mismatched = {
        where: found
        for where, found in distribution_versions(wheel, sdist).items()
        if found != version
    }
    if mismatched:
        raise ReleaseGateError(
            f"built distributions do not carry the approved version {version!r}: {mismatched!r}"
        )


#: Versions already published. A tag for one of them is refused outright: the
#: release workflow never re-publishes, and these predate the current evidence
#: format (1.0.0's manifest is docs/release-evidence.md).
RELEASED: dict[str, str] = {
    "1.0.0": "released 2026-07-18 from docs/release-evidence.md",
}


def manifest_paths(base_version: str) -> tuple[str, str]:
    return (
        f"docs/release-evidence-{base_version}.md",
        f"docs/release-evidence-report-{base_version}.json",
    )


def check(root: Path, tag: str) -> ReleaseManifest:
    version = package_version(root)
    parsed = _VERSION.match(version)
    if parsed is None:
        raise ReleaseGateError(
            f"refusing to release version {version!r}: only X.Y.Z and X.Y.ZrcN are releasable"
        )
    base, rc = parsed["base"], parsed["rc"]
    if base in RELEASED:
        raise ReleaseGateError(
            f"{base} was already {RELEASED[base]}; the release workflow never "
            "re-publishes a version. Bump __version__ for a new release"
        )
    expected_tag = f"v{base}" + (f"-rc.{rc}" if rc else "")
    if _TAG.match(tag) is None or tag != expected_tag:
        raise ReleaseGateError(
            f"tag {tag!r} does not match package version {version!r} (expected {expected_tag!r})"
        )

    manifest_rel, report_rel = manifest_paths(base)
    manifest_path = root / manifest_rel
    report_path = root / report_rel
    if not manifest_path.is_file():
        raise ReleaseGateError(
            f"no evidence manifest for {base}: {manifest_rel} does not exist "
            "(docs/release-evidence.md is the 1.0.0 manifest and cannot approve another version)"
        )
    if not report_path.is_file():
        raise ReleaseGateError(f"no evidence report for {base}: {report_rel} does not exist")

    problems, report = report_problems(report_path.read_bytes(), manifest_rel)
    if problems:
        raise ReleaseGateError(f"{report_rel}: " + "; ".join(problems))
    required_status = "candidate" if rc else "approved"
    if report["version"] != version:
        raise ReleaseGateError(
            f"{report_rel}: version is {report['version']!r}, but the package is {version!r}"
        )
    if report["status"] != required_status:
        raise ReleaseGateError(
            f"{report_rel}: status is {report['status']!r}; tag {tag} requires "
            f"{required_status!r}"
        )
    problems = manifest_problems(manifest_path.read_bytes(), base, report["status"])
    if problems:
        raise ReleaseGateError(f"{manifest_rel}: " + "; ".join(problems))

    freeze = report["freeze"]
    return ReleaseManifest(
        version=version,
        base_version=base,
        is_candidate=rc is not None,
        manifest=manifest_rel,
        report=report_rel,
        freeze_commit=None if freeze is None else freeze["commit"],
        batch_manifest=None if freeze is None else freeze["batch_manifest"],
    )


_SHA = re.compile(r"^[0-9a-f]{40}$")


def remote_tag_commit(root: Path, remote: str, tag: str) -> str:
    """The commit the remote's ``refs/tags/<tag>`` peels to, read with ls-remote.

    An annotated tag is reported twice: the tag object, and ``^{}`` for the
    commit it points at. A lightweight tag is reported once, as the commit.
    """
    ref = f"refs/tags/{tag}"
    result = subprocess.run(
        ["git", "ls-remote", "--tags", remote, ref, f"{ref}^{{}}"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise ReleaseGateError(f"git ls-remote failed for {ref}: {result.stderr.strip()}")
    refs: dict[str, str] = {}
    for line in result.stdout.splitlines():
        sha, _, name = line.partition("\t")
        refs[name] = sha
    peeled = refs.get(f"{ref}^{{}}") or refs.get(ref)
    if peeled is None:
        raise ReleaseGateError(f"{remote} has no {ref}")
    if not _SHA.match(peeled):
        raise ReleaseGateError(f"{remote} reported an unreadable object name for {ref}: {peeled!r}")
    return peeled


def verify_remote_tag(root: Path, remote: str, tag: str, expected_sha: str) -> None:
    """Fail unless the remote tag still names the commit this run built.

    A tag-push run builds ``GITHUB_SHA``. If the tag has since been moved,
    ``gh release create --verify-tag`` would still find a tag of that name and
    attach this run's artifacts to a release for another commit.
    """
    if not _SHA.match(expected_sha):
        raise ReleaseGateError(f"expected commit {expected_sha!r} is not a full SHA-1")
    actual = remote_tag_commit(root, remote, tag)
    if actual != expected_sha:
        raise ReleaseGateError(
            f"{remote} refs/tags/{tag} now names {actual}, but this run built {expected_sha}; "
            "the tag moved after the run started"
        )


# --- a final tag and its freeze commit ------------------------------------------
#
# Owner decision 2026-10-17 (plans/036-change-cost.md, the D-9 addendum; WI-083):
# every lane is requalified on the estate at the freeze commit F, the runs are
# banked in an evidence-only commit B whose parent is F, the approval is a
# records-only commit A, and the tag goes on A. What makes that true by
# construction is the word "only", and this section checks it: the tag differs
# from F in evidence and records alone, and every live verdict names F.

EVIDENCE_ROOT = "docs/plan-033"
REGISTRY = "tests/test_committed_evidence.py"

#: Files the banking and approval commits may MODIFY after F (never add, delete
#: or change the mode of). The list is closed and reviewed: anything else that
#: differs from F refuses the tag. It was read off what banking the 1.1.0rc1
#: batches touched (PR #99's c16bc9f, PR #103's 1db00fa and 7a97458) and what
#: approval edits, keeping only records: none of these is imported, executed or
#: packaged as code, and no lane's builder, finalizer or runner reads any of them
#: (checked 2026-10-17 over scripts/windows-oracle/, scripts/plan-033/ and src/;
#: the bank tool, which is not a lane, retargets two of them).
#: ``{base}`` is the version being released. Deliberately left
#: out, although that banking touched them: ``.gitattributes`` (it decides the
#: bytes every checkout writes, src/ included, so the pack label's rule must be
#: in F); ``src/gpo_studio/api.py`` (it cites run ids, but it ships); every test
#: but the registry's verdict tables (they are code CI runs on the tag); the
#: bank and driver tools under scripts/. Packs, the batch manifest and its note,
#: and the post-batch directory check are not listed: they are derived from the
#: batch manifest the report names and may only be ADDED (see _check_changes).
FREEZE_RECORDS: dict[str, str] = {
    # The user-visible record of the banked batch and of the release itself.
    "CHANGELOG.md": "changelog",
    # Names the current qualification runs; a RETARGETABLE file of the bank tool.
    "README.md": "readme",
    # Cites each capability's certifying run.
    "docs/capability-matrix.md": "capability matrix",
    # The register: an item the batch closes changes its status line.
    "docs/work-items.md": "work-item register",
    # The release manifest: the batch's citations, and line 5 at approval.
    "docs/release-evidence-{base}.md": "release manifest",
    # The report: `freeze` in the banking commit, `status`/`version` at approval.
    "docs/release-evidence-report-{base}.json": "release report",
    # The estate registry; names the runs that qualified each environment.
    "docs/plan-033/environment-spec.md": "environment spec",
    # Generated from the live verdicts; tests/test_bound_source_cost.py holds it.
    "docs/plan-033/bound-source-cost.md": "bound-source cost table",
    # Platform registry data the bank tool retargets to the new run ids. Data, not
    # code: the tests that read it are byte-identical to F.
    "tests/fixtures/scenarios/platforms.json": "platform registry",
    # Only its verdict tables (REGISTRY_TABLES) may differ, compared by AST.
    REGISTRY: "verdict registry",
}
#: A lane's results document (``fdeploy-results.md`` and the like) names its
#: certifying run in its header. Modified only, like FREEZE_RECORDS.
_RESULTS_RECORD = re.compile(r"^docs/plan-033/[a-z0-9][a-z0-9-]*-results\.md$")
#: A run's verdict inside its pack, as ``bank-requal-batch.py`` writes it:
#: ``<family>-evidence/<label>/<lane>/verification.json`` (``manifest.json``
#: for WP-0), relative to docs/plan-033.
_PACK_VERDICT = re.compile(
    r"^(?:wp0|wp1b|wp2|wp3|wp4|wp6|wp7|wp9)-evidence/(?P<label>[a-z0-9][a-z0-9.-]*)/"
    r"(?P<lane>[a-z0-9][a-z0-9-]*)/(?P<file>verification|manifest)\.json$"
)
#: The post-batch directory check's two files, relative to docs/plan-033.
_CLEANUP_FILE = re.compile(r"^[a-z0-9][a-z0-9.-]*-cleanup/(?:collector\.ps1|directory\.json)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BATCH_KEYS = (
    "source_commit", "lanes_started", "runs", "not_passed", "successors", "post_batch_cleanup",
)

#: The module-level statements of the registry that are its verdict tables. In
#: ``tests/test_committed_evidence.py`` these, and nothing else, may change after F.
REGISTRY_TABLES = ("LANE_VERDICTS", "RETIRED_VERDICTS", "PENDING_REQUALIFICATION", "WP0_MANIFEST")
#: How pytest derives the live set. The gate computes the same subtraction, so the
#: registry must still say exactly this, after every table statement.
_LIVE_VERDICTS_SOURCE = """\
LIVE_VERDICTS = {
    relative: finalizer
    for relative, finalizer in LANE_VERDICTS.items()
    if relative not in RETIRED_VERDICTS
    and relative not in PENDING_REQUALIFICATION
}
"""
_TABLE_READS = frozenset({"items", "keys", "values", "get"})
_PURE_CALLS = frozenset({"set", "frozenset", "sorted", "len", "list", "tuple"})
_REFLECTION = frozenset(
    {"globals", "vars", "locals", "exec", "eval", "compile", "setattr", "delattr",
     "__import__", "__builtins__"}
)


@dataclass(frozen=True)
class Registry:
    """The verdict tables of ``tests/test_committed_evidence.py``, read statically."""

    lane_verdicts: dict[str, str]
    retired: frozenset[str]
    pending: frozenset[str]
    wp0_manifest: str

    @property
    def live(self) -> dict[str, str]:
        return {
            relative: finalizer
            for relative, finalizer in self.lane_verdicts.items()
            if relative not in self.retired and relative not in self.pending
        }


def _table_statement(node: ast.stmt) -> str | None:
    """The table a module-level statement assigns or updates, if it is one."""
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in REGISTRY_TABLES
    ):
        return node.targets[0].id
    if (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id in REGISTRY_TABLES
    ):
        return node.target.id
    if (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "update"
        and isinstance(node.value.func.value, ast.Name)
        and node.value.func.value.id in REGISTRY_TABLES
    ):
        return node.value.func.value.id
    return None


def _string(node: ast.expr | None, where: str) -> str:
    if isinstance(node, ast.Constant) and type(node.value) is str:
        return node.value
    raise ReleaseGateError(f"{where}: expected a string literal in a verdict table")


def _string_set(node: ast.expr | None, where: str) -> set[str]:
    if isinstance(node, ast.Set):
        return {_string(element, where) for element in node.elts}
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "set"
        and not node.args
        and not node.keywords
    ):
        return set()
    raise ReleaseGateError(f"{where}: expected a set literal of strings or set()")


def _string_dict(node: ast.expr | None, where: str) -> dict[str, str]:
    if not isinstance(node, ast.Dict) or any(key is None for key in node.keys):
        raise ReleaseGateError(f"{where}: expected a dict literal of strings")
    pairs = zip(node.keys, node.values, strict=True)
    return {_string(key, where): _string(value, where) for key, value in pairs}


def parse_registry(source: str, where: str) -> Registry:
    """Read the verdict tables without running the test module, refusing ambiguity.

    The gate cannot import the registry (it is a pytest module, and the release
    identity job has no dependencies), so it reads the tables from the AST and
    refuses every form it would have to guess at: a table bound other than by a
    literal, updated other than by ``.update(<literal>)`` at module level,
    rebound, deleted, subscript-assigned, passed to a call that could mutate it,
    touched through reflection, or updated after ``LIVE_VERDICTS`` is computed;
    and a ``LIVE_VERDICTS`` that is not :data:`_LIVE_VERDICTS_SOURCE`.
    """
    try:
        tree = ast.parse(source, filename=where)
    except (SyntaxError, ValueError) as error:
        raise ReleaseGateError(f"{where}: cannot parse: {error}") from error
    lanes: dict[str, str] | None = None
    retired: set[str] | None = None
    pending: set[str] | None = None
    wp0: str | None = None
    allowed: set[int] = set()
    live_seen = False
    canonical = ast.dump(ast.parse(_LIVE_VERDICTS_SOURCE).body[0])
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "LIVE_VERDICTS" for t in node.targets)
        ):
            if live_seen or ast.dump(node) != canonical:
                raise ReleaseGateError(
                    f"{where} line {node.lineno}: LIVE_VERDICTS must be assigned once, as "
                    "LANE_VERDICTS minus RETIRED_VERDICTS and PENDING_REQUALIFICATION"
                )
            live_seen = True
            allowed.add(id(node.targets[0]))
            continue
        table = _table_statement(node)
        if table is None:
            continue
        here = f"{where} line {node.lineno}"
        if table != "WP0_MANIFEST" and live_seen:
            raise ReleaseGateError(f"{here}: {table} changes after LIVE_VERDICTS is computed")
        if isinstance(node, ast.Expr):
            call = node.value
            assert isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            if len(call.args) != 1 or call.keywords or table == "WP0_MANIFEST":
                raise ReleaseGateError(f"{here}: {table}.update takes one literal")
            if table == "LANE_VERDICTS" and lanes is not None:
                lanes.update(_string_dict(call.args[0], here))
            elif table == "RETIRED_VERDICTS" and retired is not None:
                retired |= _string_set(call.args[0], here)
            elif table == "PENDING_REQUALIFICATION" and pending is not None:
                pending |= _string_set(call.args[0], here)
            else:
                raise ReleaseGateError(f"{here}: {table} is updated before it is assigned")
            allowed.add(id(call.func.value))
            continue
        assert isinstance(node, (ast.Assign, ast.AnnAssign))
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        already = {"LANE_VERDICTS": lanes, "RETIRED_VERDICTS": retired,
                   "PENDING_REQUALIFICATION": pending, "WP0_MANIFEST": wp0}[table]
        if already is not None:
            raise ReleaseGateError(f"{here}: {table} is assigned twice")
        if table == "LANE_VERDICTS":
            lanes = _string_dict(node.value, here)
        elif table == "RETIRED_VERDICTS":
            retired = _string_set(node.value, here)
        elif table == "PENDING_REQUALIFICATION":
            pending = _string_set(node.value, here)
        else:
            wp0 = _string(node.value, here)
        allowed.add(id(target))
    if lanes is None or retired is None or pending is None or wp0 is None or not live_seen:
        raise ReleaseGateError(
            f"{where}: must assign LANE_VERDICTS, RETIRED_VERDICTS, PENDING_REQUALIFICATION, "
            "WP0_MANIFEST and LIVE_VERDICTS at module level"
        )
    _refuse_other_table_writes(tree, where, allowed)
    return Registry(lanes, frozenset(retired), frozenset(pending), wp0)


def _reads_only(node: ast.Name, parent: ast.AST | None, grandparent: ast.AST | None) -> bool:
    """Whether this use of a mutable table can only read it.

    A closed list of contexts, everything else refused: an alias (``x = TABLE``),
    a return, a container display or an arbitrary call could hand the object to
    code that mutates it before pytest computes the live set.
    """
    if isinstance(parent, ast.Attribute) and parent.value is node:
        return parent.attr in _TABLE_READS
    if isinstance(parent, ast.Subscript) and parent.value is node:
        return isinstance(parent.ctx, ast.Load)
    if isinstance(parent, ast.Call) and node in parent.args:
        return isinstance(parent.func, ast.Name) and parent.func.id in _PURE_CALLS
    if isinstance(parent, ast.Compare):
        return True
    if isinstance(parent, (ast.comprehension, ast.For)) and parent.iter is node:
        return True
    return isinstance(parent, (ast.BinOp, ast.FormattedValue)) and grandparent is not None


def _refuse_other_table_writes(tree: ast.Module, where: str, allowed: set[int]) -> None:
    parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    names = {*REGISTRY_TABLES, "LIVE_VERDICTS"}
    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        if isinstance(node, (ast.Global, ast.Nonlocal)) and names & set(node.names):
            raise ReleaseGateError(f"{where} line {line}: a verdict table is declared global")
        if isinstance(node, ast.Name) and node.id in _REFLECTION:
            raise ReleaseGateError(f"{where} line {line}: {node.id} could rewrite a verdict table")
        if isinstance(node, ast.Attribute) and (node.attr == "__dict__" or node.attr in names):
            raise ReleaseGateError(
                f"{where} line {line}: .{node.attr} could rewrite a verdict table"
            )
        if not isinstance(node, ast.Name) or node.id not in names or id(node) in allowed:
            continue
        parent = parents.get(id(node))
        grandparent = parents.get(id(parent)) if parent is not None else None
        if not isinstance(node.ctx, ast.Load) or (
            # WP0_MANIFEST is a string: no read of it can change it.
            node.id != "WP0_MANIFEST" and not _reads_only(node, parent, grandparent)
        ):
            raise ReleaseGateError(
                f"{where} line {line}: {node.id} is written, aliased or passed where it could "
                "be changed, outside its module-level literal and .update(<literal>) statements"
            )


def _without_tables(source: str, where: str) -> str:
    try:
        tree = ast.parse(source, filename=where)
    except (SyntaxError, ValueError) as error:
        raise ReleaseGateError(f"{where}: cannot parse: {error}") from error
    body = [node for node in tree.body if _table_statement(node) is None]
    return ast.dump(ast.Module(body=body, type_ignores=[]))


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-c", "core.quotepath=false", *args],
        cwd=root,
        capture_output=True,
        timeout=300,
        check=False,
    )


def _git_out(root: Path, *args: str) -> bytes:
    result = _git(root, *args)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace").strip()
        raise ReleaseGateError(f"git {' '.join(args)} failed: {stderr}")
    return result.stdout


def _repo_file(root: Path, relative: str) -> Path:
    return root.joinpath(*relative.split("/"))


def _json_file(path: Path, where: str, *, bom: bool = False) -> Any:
    try:
        return json.loads(
            path.read_bytes().decode("utf-8-sig" if bom else "utf-8"),
            object_pairs_hook=_no_duplicates,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseGateError(f"{where}: cannot read it as JSON: {error}") from error


def _safe_relative(relative: object) -> bool:
    if type(relative) is not str or not relative or "\\" in relative:
        return False
    parts = PurePosixPath(relative).parts
    return not relative.startswith("/") and all(p not in ("", ".", "..") for p in parts)


@dataclass(frozen=True)
class _Batch:
    path: str
    runs: dict[str, dict[str, Any]]
    cleanup: dict[str, str]

    def pack(self, lane: str) -> str:
        """The lane's pack directory, relative to the repository root."""
        return f"{EVIDENCE_ROOT}/{PurePosixPath(self.runs[lane]['verdict']).parent}"


def _batch(root: Path, relative: str, freeze: str) -> _Batch:
    """The batch manifest the report names, held to one passing run per lane at F."""
    batch = _json_file(_repo_file(root, relative), relative)
    if not isinstance(batch, dict) or any(key not in batch for key in _BATCH_KEYS):
        raise ReleaseGateError(f"{relative}: a batch manifest must have {list(_BATCH_KEYS)}")
    if batch["source_commit"] != freeze:
        raise ReleaseGateError(
            f"{relative}: the batch ran at {batch['source_commit']!r}, not at the freeze "
            f"commit {freeze}"
        )
    if batch["not_passed"] != []:
        raise ReleaseGateError(f"{relative}: not every lane passed: {batch['not_passed']!r}")
    if batch["successors"] != []:
        raise ReleaseGateError(
            f"{relative}: records successor runs, so its verdicts span more than one commit"
        )
    runs = batch["runs"]
    if not isinstance(runs, list) or not runs or batch["lanes_started"] != len(runs) or (
        type(batch["lanes_started"]) is not int
    ):
        raise ReleaseGateError(f"{relative}: runs must be the lanes_started passing runs")
    cleanup = batch["post_batch_cleanup"]
    if (
        not isinstance(cleanup, dict)
        or not cleanup
        or not all(type(k) is str and _CLEANUP_FILE.match(k) for k in cleanup)
        or not all(type(v) is str and _SHA256.match(v) for v in cleanup.values())
    ):
        raise ReleaseGateError(
            f"{relative}: post_batch_cleanup must hash the batch's post-batch directory check"
        )
    by_lane: dict[str, dict[str, Any]] = {}
    labels: set[str] = set()
    for run in runs:
        name = run.get("name") if isinstance(run, dict) else None
        if type(name) is not str or name in by_lane:
            raise ReleaseGateError(f"{relative}: every run needs a distinct lane name: {name!r}")
        assert isinstance(run, dict)
        verdict = run.get("verdict")
        match = _PACK_VERDICT.match(verdict) if type(verdict) is str else None
        if (
            match is None
            or match["lane"] != name
            or (match["file"] == "manifest") != (name == "wp0")
        ):
            raise ReleaseGateError(
                f"lane {name!r}: {verdict!r} is not where the bank tool banks it"
            )
        if run.get("commit") != freeze:
            raise ReleaseGateError(
                f"lane {name!r}: the batch records its run at {run.get('commit')!r}, not at the "
                f"freeze commit {freeze}"
            )
        files = run.get("files")
        assert type(verdict) is str
        if (
            not isinstance(files, dict)
            or PurePosixPath(verdict).name not in files
            or not all(_safe_relative(k) for k in files)
            or not all(type(v) is str and _SHA256.match(v) for v in files.values())
        ):
            raise ReleaseGateError(f"lane {name!r}: the batch does not hash its pack's files")
        labels.add(match["label"])
        by_lane[name] = run
    if len(labels) != 1:
        raise ReleaseGateError(
            f"{relative}: one batch banks under one label; found {sorted(labels)}"
        )
    return _Batch(relative, by_lane, cleanup)


def _changes(root: Path, freeze: str, tagged: str) -> list[tuple[str, str, str]]:
    """``(status, new mode, path)`` for every path that differs between F and the tag."""
    raw = _git_out(
        root, "diff", "--raw", "-z", "--no-renames", "--no-ext-diff", "--abbrev=40",
        "--ignore-submodules=none", freeze, tagged,
    )
    tokens = raw.split(b"\0")
    if tokens and tokens[-1] == b"":
        tokens.pop()
    if len(tokens) % 2:
        raise ReleaseGateError("cannot read git diff --raw output")
    changes = []
    for header, path in zip(tokens[0::2], tokens[1::2], strict=True):
        fields = header.decode("ascii").lstrip(":").split()
        if len(fields) != 5:
            raise ReleaseGateError(f"cannot read git diff --raw entry {header!r}")
        changes.append((fields[4], fields[1], path.decode("utf-8")))
    return changes


def _check_changes(
    root: Path, base: str, freeze: str, tagged: str, batch: _Batch
) -> list[tuple[str, str, str]]:
    """Refuse any difference from F that is not evidence the batch banks or a record."""
    changes = _changes(root, freeze, tagged)
    records = {path.format(base=base) for path in FREEZE_RECORDS}
    packs = tuple(batch.pack(lane) + "/" for lane in batch.runs)
    added_files = {
        batch.path,
        batch.path.removesuffix(".json") + ".md",
        *(f"{EVIDENCE_ROOT}/{path}" for path in batch.cleanup),
    }
    refused = []
    for status, mode, path in changes:
        banked = status == "A" and (path in added_files or path.startswith(packs))
        recorded = status == "M" and (path in records or bool(_RESULTS_RECORD.match(path)))
        if not (banked or recorded) or mode != "100644":
            refused.append(f"{status} {path} (mode {mode})")
    if refused:
        raise ReleaseGateError(
            f"the tagged commit {tagged} differs from the freeze commit {freeze} outside the "
            "batch's evidence and the release records: " + "; ".join(refused[:20])
            + (f"; and {len(refused) - 20} more" if len(refused) > 20 else "")
            + ". Only the batch's packs, manifest, note and post-batch check may be added, "
            "and only FREEZE_RECORDS modified; src/, scripts/, pyproject.toml, uv.lock, "
            ".github/, .gitattributes and every other test must be byte-identical to F"
        )
    added = {path for status, _, path in changes if status == "A"}
    owed = [
        f"{EVIDENCE_ROOT}/{path}" for path in batch.cleanup
        if f"{EVIDENCE_ROOT}/{path}" not in added
    ]
    owed += [batch.path] if batch.path not in added else []
    for lane, run in batch.runs.items():
        owed += [
            f"{batch.pack(lane)}/{name}" for name in run["files"]
            if f"{batch.pack(lane)}/{name}" not in added
        ]
    if owed:
        raise ReleaseGateError(
            "the batch's evidence must be added after the freeze commit, never carried from "
            f"it or before it: {owed[:10]}"
        )
    if any(path == REGISTRY for _, _, path in changes):
        before = _git_out(root, "cat-file", "blob", f"{freeze}:{REGISTRY}").decode("utf-8")
        after = _git_out(root, "cat-file", "blob", f"{tagged}:{REGISTRY}").decode("utf-8")
        if _without_tables(before, f"{REGISTRY} at F") != _without_tables(after, REGISTRY):
            raise ReleaseGateError(
                f"{REGISTRY} differs from the freeze commit outside its verdict tables "
                f"{list(REGISTRY_TABLES)}; only those may change after F"
            )
    return changes


def _tree_digests(directory: Path, where: str) -> dict[str, str]:
    if directory.is_symlink() or not directory.is_dir():
        raise ReleaseGateError(f"{where}: not a directory")
    digests: dict[str, str] = {}
    for current, dirnames, filenames in os.walk(directory, followlinks=False):
        for name in [*dirnames, *filenames]:
            path = Path(current) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ReleaseGateError(f"{where}: {path.name} is not a regular file or directory")
            if stat.S_ISREG(mode):
                relative = path.relative_to(directory).as_posix()
                digests[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def _lane(relative: str) -> str:
    return PurePosixPath(relative).parent.name


def _check_live_verdicts(root: Path, freeze: str, batch: _Batch) -> None:
    """Every live verdict is the batch's run of its lane, earned at F; no lane is missing."""
    try:
        at_tag = parse_registry(_repo_file(root, REGISTRY).read_text(encoding="utf-8"), REGISTRY)
    except (OSError, UnicodeDecodeError) as error:
        raise ReleaseGateError(f"{REGISTRY}: cannot read it: {error}") from error
    at_freeze = parse_registry(
        _git_out(root, "cat-file", "blob", f"{freeze}:{REGISTRY}").decode("utf-8"),
        f"{REGISTRY} at F",
    )
    if at_tag.pending:
        raise ReleaseGateError(
            f"{REGISTRY}: PENDING_REQUALIFICATION is not empty ({sorted(at_tag.pending)}); "
            "a final release owes no requalification"
        )
    live = {*at_tag.live, at_tag.wp0_manifest}
    lanes = [_lane(relative) for relative in live]
    doubled = sorted({lane for lane in lanes if lanes.count(lane) > 1})
    if doubled:
        raise ReleaseGateError(f"{REGISTRY}: more than one live verdict for lanes {doubled}")
    expected = {_lane(relative) for relative in (*at_freeze.live, at_freeze.wp0_manifest)}
    missing = sorted(expected - set(batch.runs))
    extra = sorted(set(batch.runs) - expected)
    if missing or extra:
        raise ReleaseGateError(
            f"{batch.path}: the batch must re-run exactly the lanes live at the freeze commit; "
            f"not re-run: {missing}; not live at F: {extra}"
        )
    cited = {run["verdict"]: lane for lane, run in batch.runs.items()}
    stale = sorted(live - set(cited))
    if stale:
        raise ReleaseGateError(
            f"lane {_lane(stale[0])!r}: the live verdict {stale[0]} is not this batch's run, so "
            f"it was not earned at the freeze commit {freeze}; retire it in {REGISTRY}"
        )
    unregistered = sorted(set(cited) - live)
    if unregistered:
        raise ReleaseGateError(
            f"lane {cited[unregistered[0]]!r}: the batch's verdict {unregistered[0]} is not "
            f"live in {REGISTRY}"
        )
    for lane, run in sorted(batch.runs.items()):
        where = f"lane {lane!r} ({EVIDENCE_ROOT}/{run['verdict']})"
        verdict = _json_file(_repo_file(root, f"{EVIDENCE_ROOT}/{run['verdict']}"), where, bom=True)
        source = verdict.get("source") if isinstance(verdict, dict) else None
        if not isinstance(source, dict) or source.get("commit") != freeze:
            earned = source.get("commit") if isinstance(source, dict) else None
            raise ReleaseGateError(
                f"{where}: earned at {earned!r}, not at the freeze commit {freeze}"
            )
        assert isinstance(verdict, dict)
        if source.get("dirty") is not False:
            raise ReleaseGateError(f"{where}: not earned from a clean tree (source.dirty)")
        if lane == "wp0":
            capability = verdict.get("capability")
            passed = isinstance(capability, dict) and capability.get("evidence_state") == "pass"
        else:
            passed = verdict.get("passed") is True
        if not passed:
            raise ReleaseGateError(f"{where}: not a pass")
        if verdict.get("run_id") != run.get("run_id"):
            raise ReleaseGateError(f"{where}: run_id is not the batch's {run.get('run_id')!r}")
        pack = batch.pack(lane)
        if _tree_digests(_repo_file(root, pack), where) != run["files"]:
            raise ReleaseGateError(f"{where}: the pack's files are not what the batch banked")
    for relative, digest in sorted(batch.cleanup.items()):
        path = _repo_file(root, f"{EVIDENCE_ROOT}/{relative}")
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ReleaseGateError(f"{batch.path}: {relative} is not what the batch hashed")


def verify_freeze(root: Path, result: ReleaseManifest) -> str:
    """Hold a final tag to its freeze commit F (see the module docstring)."""
    freeze, batch_path = result.freeze_commit, result.batch_manifest
    if freeze is None or batch_path is None:
        raise ReleaseGateError(f"{result.report}: a final tag needs the report's freeze")
    top = _git_out(root, "rev-parse", "--show-toplevel").decode("utf-8").strip()
    if not Path(top).samefile(root):
        raise ReleaseGateError(f"{root} is not the top of its git work tree ({top})")
    if _git_out(root, "rev-parse", "--is-shallow-repository").strip() != b"false":
        raise ReleaseGateError(
            "a final tag is checked against its freeze commit, which needs the whole history, "
            "and this clone is shallow; check out with fetch-depth: 0"
        )
    if _git(root, "cat-file", "-e", f"{freeze}^{{commit}}").returncode != 0:
        raise ReleaseGateError(
            f"the freeze commit {freeze} that {result.report} records is not in this clone; "
            "it must be on the tagged commit's history (and the checkout must not be shallow)"
        )
    tagged = _git_out(root, "rev-parse", "--verify", "HEAD^{commit}").decode("ascii").strip()
    if tagged == freeze:
        raise ReleaseGateError(
            f"the tagged commit is the freeze commit {freeze} itself; the batch is banked in a "
            "child of F and approved after that, and the tag goes on the approval commit"
        )
    ancestry = _git(root, "merge-base", "--is-ancestor", freeze, tagged)
    if ancestry.returncode == 1:
        raise ReleaseGateError(
            f"the freeze commit {freeze} is not an ancestor of the tagged commit {tagged}"
        )
    if ancestry.returncode != 0:
        raise ReleaseGateError(
            "git merge-base --is-ancestor failed: "
            + ancestry.stderr.decode("utf-8", "replace").strip()
        )
    if _git_out(root, "status", "--porcelain=v1", "-z", "--untracked-files=no"):
        raise ReleaseGateError(
            "the checkout has uncommitted changes to tracked files; the gate checks the "
            "tagged commit, so it runs on a clean checkout"
        )
    batch = _batch(root, batch_path, freeze)
    changes = _check_changes(root, result.base_version, freeze, tagged, batch)
    _check_live_verdicts(root, freeze, batch)
    return (
        f"freeze {freeze[:12]}: {len(batch.runs)} lanes earned at F ({batch_path}); "
        f"{len(changes)} paths changed since F, all the batch's evidence or release records"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check release identity and evidence.")
    parser.add_argument("--tag", required=True, help="the pushed tag, e.g. v1.1.0")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--github-output", type=Path, help="append resolved paths here")
    parser.add_argument(
        "--remote-tag-sha",
        help="also require the remote tag to peel to this commit (GITHUB_SHA)",
    )
    parser.add_argument("--remote", default="origin", help="remote for --remote-tag-sha")
    parser.add_argument("--wheel", type=Path, help="require this built wheel's version to match")
    parser.add_argument("--sdist", type=Path, help="require this built sdist's version to match")
    args = parser.parse_args(argv)
    try:
        result = check(args.root, args.tag)
        # A final tag, and only a final tag, is held to its freeze commit. It runs
        # on every final-tag invocation, so dropping a flag cannot skip it.
        frozen = None if result.is_candidate else verify_freeze(args.root, result)
        verify_distributions(result.version, args.wheel, args.sdist)
        if args.remote_tag_sha is not None:
            verify_remote_tag(args.root, args.remote, args.tag, args.remote_tag_sha)
    except (
        ReleaseGateError,
        OSError,
        subprocess.SubprocessError,
        zipfile.BadZipFile,
        tarfile.TarError,
    ) as error:
        print(f"release gate: FAIL: {error}", file=sys.stderr)
        return 1
    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as stream:
            stream.write(f"version={result.version}\n")
            stream.write(f"manifest={result.manifest}\n")
            stream.write(f"report={result.report}\n")
            stream.write(f"prerelease={'true' if result.is_candidate else 'false'}\n")
    kind = "release candidate" if result.is_candidate else "final release"
    print(f"release gate: {args.tag} is a {kind} of {result.version}; evidence {result.manifest}")
    if frozen is not None:
        print(f"release gate: {frozen}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
