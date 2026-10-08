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
line 5 of the manifest to the matching line below. Nothing else changes.

Other checks: ``__version__`` is bound exactly once and Hatchling reads the
same value; with ``--remote-tag-sha`` the remote tag still peels to the built
commit; ``--github-output`` writes the resolved paths for later steps.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path
from typing import Any

APPROVED = "> **Status:** approved for release"
CANDIDATE = "> **Status:** release candidate; final approval pending"
DRAFT = "> **Status:** draft; not approved for release and not a release candidate"
STATUS_LINES = {"draft": DRAFT, "candidate": CANDIDATE, "approved": APPROVED}

REPORT_TYPE = "GPO Studio release evidence report"
REPORT_SCHEMA_VERSION = 2
REPORT_KEYS = frozenset(
    {
        "report_type",
        "report_version",
        "version",
        "status",
        "manifest",
        "workspace_schema_version",
        "artifact_hashes",
        "evidence",
    }
)
ARTIFACT_HASH_KEYS = frozenset({"wheel_sha256", "sdist_sha256", "sbom_sha256"})

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

    return ReleaseManifest(
        version=version,
        base_version=base,
        is_candidate=rc is not None,
        manifest=manifest_rel,
        report=report_rel,
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
