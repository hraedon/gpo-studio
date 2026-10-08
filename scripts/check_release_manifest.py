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
   version`` line, and carries exactly one ``Status`` line, which must be the
   approval marker for a final tag or the candidate marker for an RC tag. A
   draft, a stale copy or a second Status line fails.
4. The JSON report names the same version in ``release_version`` (not checked
   for the legacy 1.0.0 report, which predates the field).
5. With ``--remote-tag-sha``, the remote tag still peels to the commit the run
   built, so a tag moved mid-run cannot receive another commit's artifacts.

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
import unicodedata
from dataclasses import dataclass
from pathlib import Path

APPROVED = "> **Status:** approved for release"
CANDIDATE = "> **Status:** release candidate; final approval pending"

# A status *declaration* is any line a reader could take for one, however it is
# spelled: indented or not, in a blockquote or not, bold or not, any case,
# ``Status:`` or a ``## Status`` heading. The exact marker above is compared
# only after this broad match has found exactly one declaration, so a second,
# differently spelled draft line cannot sit unnoticed beside an approval.
_STATUS_LIKE = re.compile(
    r"""^[ \t]*(?:>[ \t]*)*          # indentation and blockquote markers
        (?:\#{1,6}[ \t]*)?             # or a heading
        [*_]*[ \t]*status[ \t]*[*_]*    # the word, optionally emphasised
        [ \t]*(?::|$)                   # then a colon, or nothing (a heading)
    """,
    re.IGNORECASE | re.VERBOSE,
)
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u200e\u200f\u2060\ufeff"))
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")


def status_declarations(text: str) -> tuple[list[str], list[str]]:
    """Return (visible status-like lines, status lines in hidden regions).

    Hidden regions are fenced code blocks and HTML comments. A renderer does not
    show them as the document's status, so an approval there must not count,
    and a declaration there is ambiguous enough to refuse outright: inside a
    fence any status-like line is refused, and inside a comment any mention of
    "status" is.
    """
    visible: list[str] = []
    hidden: list[str] = []
    fence: str | None = None
    in_comment = False
    for raw in text.splitlines():
        line = unicodedata.normalize("NFKC", raw).translate(_ZERO_WIDTH)
        if fence is not None:
            closing = _FENCE.match(line)
            if closing and closing.group(1)[0] == fence[0] and len(closing.group(1)) >= len(fence):
                fence = None
            elif _STATUS_LIKE.match(line):
                hidden.append(raw)
            continue
        if in_comment:
            if "status" in line.lower():
                hidden.append(raw)
            if "-->" in line:
                in_comment = False
            continue
        opening = _FENCE.match(line)
        if opening:
            fence = opening.group(1)
            continue
        if "<!--" in line:
            before, after = line.split("<!--", 1)
            if "status" in after.lower():
                hidden.append(raw)
            if "-->" not in after:
                in_comment = True
            if _STATUS_LIKE.match(before):
                visible.append(raw)
            continue
        if _STATUS_LIKE.match(line):
            visible.append(raw)
    if fence is not None:
        hidden.append(f"<unterminated {fence} fence>")
    if in_comment:
        hidden.append("<unterminated HTML comment>")
    return visible, hidden

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


def package_version(root: Path) -> str:
    """Read ``__version__`` without importing the package."""
    source = (root / "src" / "gpo_studio" / "__init__.py").read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "__version__"
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    raise ReleaseGateError("src/gpo_studio/__init__.py assigns no literal __version__")


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

    lines = manifest_path.read_text(encoding="utf-8").splitlines()
    title = f"# Release evidence manifest — GPO Studio {base}"
    if not lines or lines[0] != title:
        raise ReleaseGateError(f"{manifest_rel} must begin with {title!r}")
    if f"- Application version: {base}" not in lines:
        raise ReleaseGateError(f"{manifest_rel} has no '- Application version: {base}' line")
    visible, hidden = status_declarations("\n".join(lines))
    required = CANDIDATE if rc else APPROVED
    if hidden:
        raise ReleaseGateError(
            f"{manifest_rel} has status lines inside a code block or HTML comment, "
            f"which cannot approve and make the status ambiguous: {hidden!r}"
        )
    if visible != [required]:
        raise ReleaseGateError(
            f"{manifest_rel} must carry exactly one status line, {required!r}; "
            f"found {visible!r}"
        )

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
    args = parser.parse_args(argv)
    try:
        result = check(args.root, args.tag)
        if args.remote_tag_sha is not None:
            verify_remote_tag(args.root, args.remote, args.tag, args.remote_tag_sha)
    except (ReleaseGateError, OSError, subprocess.SubprocessError) as error:
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
