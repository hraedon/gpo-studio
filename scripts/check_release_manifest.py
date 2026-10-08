"""Release gate: the pushed tag, the package version and its evidence manifest agree.

The 1.0.0 workflow grepped ``docs/release-evidence.md`` for an approval
string. That file is the 1.0.0 manifest, so any later tag would have passed on
1.0.0's approval. This gate binds the approval to the version being released:

1. ``src/gpo_studio/__init__.py``'s ``__version__`` is not a development
   version, and the tag is exactly ``v<version>`` (``1.1.0rc1`` tags as
   ``v1.1.0-rc.1``).
2. The manifest for that version exists: ``docs/release-evidence-<X.Y.Z>.md``
   beside ``docs/release-evidence-report-<X.Y.Z>.json``. Only 1.0.0, which
   predates the rule, uses the unversioned names.
3. The manifest names the version in its title and in its ``Application
   version`` line, and carries exactly one status line, which must be the
   approval marker for a final tag or the candidate marker for an RC tag. The
   manifest is parsed as CommonMark (see ``status_problems``): a draft, a
   stale copy, a second status mention anywhere, an approval in a code block,
   list or nested quote, or any raw HTML fails.
4. The JSON report names the same version in ``release_version`` (not checked
   for the legacy 1.0.0 report, which predates the field).
5. With ``--remote-tag-sha``, the remote tag still peels to the commit the run
   built, so a tag moved mid-run cannot receive another commit's artifacts.
6. With ``--wheel``/``--sdist``, the built artifacts' metadata versions equal
   the approved version, so what is published is what was approved.

Anything unexpected fails closed. With ``--github-output`` the resolved paths
are written for later workflow steps, so publication attaches the manifest
this gate read rather than a hard-coded file name.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import tarfile
import unicodedata
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from markdown_it.token import Token

APPROVED = "> **Status:** approved for release"
CANDIDATE = "> **Status:** release candidate; final approval pending"

# A status *mention* is the word "status" followed by a colon, however it is
# spelled: any case, emphasis or code markers between, after NFKC folding, with
# zero-width characters removed and common Cyrillic/Greek look-alikes folded to
# ASCII. Every mention in the manifest counts, wherever CommonMark puts it.
_STATUS_MENTION = re.compile(r"\bstatus\b[\s*_`~]*:", re.IGNORECASE)
_STATUS_WORD = re.compile(r"\bstatus\b", re.IGNORECASE)
_FOLD = str.maketrans(
    {
        **dict.fromkeys(map(ord, "\u00ad\u034f\u200b\u200c\u200d\u200e\u200f\u2060\ufeff")),
        "\u0405": "S", "\u0455": "s", "\u0422": "T", "\u0442": "t", "\u03a4": "T",
        "\u03c4": "t", "\u0410": "A", "\u0430": "a", "\u0391": "A", "\u03b1": "a",
        "\u03c5": "u", "\u057d": "u", "\u0446": "u",
    }
)


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_FOLD)


class _TextOnly(HTMLParser):
    """Collect the text a browser would show; comments and tags are dropped."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


_STATUS_HEADING = re.compile(r"^[\W_]*status[\W_]*$", re.IGNORECASE)
_HEADER_PARAGRAPH = ("blockquote_open", "paragraph_open")
_BULLET_ITEM = ("bullet_list_open", "list_item_open", "paragraph_open")
_TITLE = ("heading_open",)


def _inline_contexts(tokens: list[Token]) -> dict[int, tuple[str, ...]]:
    """Map each source line to the block context of the inline text on it.

    Lines that belong to code blocks, fences or HTML blocks get the leaf
    token's type (``("fence",)`` and so on), so they never look like prose.
    """
    contexts: dict[int, tuple[str, ...]] = {}
    stack: list[str] = []
    for token in tokens:
        if token.nesting == 1:
            stack.append(token.type)
            continue
        if token.nesting == -1:
            stack.pop()
            continue
        if token.map is None:
            continue
        context = (*stack, token.type) if token.type != "inline" else tuple(stack)
        for line in range(token.map[0], token.map[1]):
            contexts[line] = context
    return contexts


def manifest_problems(text: str, title: str, application: str, required: str) -> list[str]:
    """Why ``text`` is not an unambiguous manifest carrying ``required``.

    The manifest is parsed as CommonMark (markdown-it-py), so code blocks,
    blockquote-nested fences, HTML blocks and lazy continuations are decided
    the way a renderer decides them, not by a line heuristic. The rules:

    * the manifest contains no raw HTML at all: an HTML block can hide a
      status (``<details>``) or show one Markdown does not;
    * the title is the level-1 heading on the first line, and the
      ``- Application version`` line is a plain bullet-list item;
    * no heading is named "Status";
    * exactly one status mention exists, counted on the source lines and again
      on the rendered text (which catches entity-encoded spellings);
    * that mention is in a plain paragraph directly inside a top-level
      blockquote (the manifest's header block), not in a list, table, code
      block or nested quote, and its source line is exactly ``required``.

    An empty list means the manifest passes.
    """
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark")
    raw_lines = text.splitlines()
    folded = _fold(text)
    folded_lines = folded.splitlines()
    tokens = md.parse(folded)
    contexts = _inline_contexts(tokens)
    problems: list[str] = []

    html_lines = sorted(
        {
            line
            for token in tokens
            if token.map is not None
            and (
                token.type == "html_block"
                or any(child.type == "html_inline" for child in (token.children or []))
            )
            for line in range(token.map[0], token.map[1])
        }
    )
    if html_lines:
        problems.append(f"raw HTML is not allowed in a manifest (source lines {html_lines})")

    if not raw_lines or raw_lines[0] != title or contexts.get(0) != _TITLE:
        problems.append(f"the first line must be the level-1 heading {title!r}")
    application_lines = [i for i, line in enumerate(raw_lines) if line == application]
    if len(application_lines) != 1 or contexts.get(application_lines[0]) != _BULLET_ITEM:
        problems.append(f"{application!r} must appear once, as a plain bullet-list item")

    problems.extend(
        f"a heading is named {token.content!r}"
        for token in tokens
        if token.type == "inline"
        and token.map is not None
        and contexts.get(token.map[0], ())[-1:] == ("heading_open",)
        and _STATUS_HEADING.match(token.content)
    )

    mentions = [index for index, line in enumerate(folded_lines) if _STATUS_MENTION.search(line)]
    renderer = _TextOnly()
    renderer.feed(md.render(folded))
    rendered_mentions = len(_STATUS_MENTION.findall(_fold("".join(renderer.parts))))
    if len(mentions) != 1 or rendered_mentions != 1:
        problems.append(
            f"exactly one status line is allowed; found {len(mentions)} in the source "
            f"({[raw_lines[i] for i in mentions]!r}) and {rendered_mentions} in the "
            "rendered text"
        )
    elif contexts.get(mentions[0]) != _HEADER_PARAGRAPH:
        problems.append(
            f"the status line {raw_lines[mentions[0]]!r} is not in a plain paragraph of a "
            f"top-level blockquote (its context is {contexts.get(mentions[0])!r})"
        )
    elif raw_lines[mentions[0]] != required:
        problems.append(f"the status line is {raw_lines[mentions[0]]!r}, not {required!r}")
    return problems


#: Manifests written before versioned names existed. Never add to this.
LEGACY_MANIFESTS: dict[str, tuple[str, str]] = {
    "1.0.0": ("docs/release-evidence.md", "docs/release-evidence-report.json"),
}

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
# a docstring matches it, and a parenthesised assignment does not.
_HATCH_VERSION_LINE = re.compile(
    r"""^(__version__|VERSION) *= *(['"])v?(?P<version>.+?)\2""", re.MULTILINE
)


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
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
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


def manifest_paths(base_version: str) -> tuple[str, str]:
    if base_version in LEGACY_MANIFESTS:
        return LEGACY_MANIFESTS[base_version]
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
    expected_tag = f"v{base}" + (f"-rc.{rc}" if rc else "")
    tag_match = _TAG.match(tag)
    if tag_match is None or tag != expected_tag:
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

    text = manifest_path.read_text(encoding="utf-8")
    problems = manifest_problems(
        text,
        title=f"# Release evidence manifest — GPO Studio {base}",
        application=f"- Application version: {base}",
        required=CANDIDATE if rc else APPROVED,
    )
    if problems:
        raise ReleaseGateError(f"{manifest_rel}: " + "; ".join(problems))

    if base not in LEGACY_MANIFESTS:
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ReleaseGateError(f"{report_rel} is not valid JSON: {error}") from error
        if not isinstance(report, dict) or report.get("release_version") != base:
            found = report.get("release_version") if isinstance(report, dict) else None
            raise ReleaseGateError(
                f"{report_rel} must declare release_version {base!r}; found {found!r}"
            )

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
