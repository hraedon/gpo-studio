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

Anything unexpected fails closed. With ``--github-output`` the resolved paths
are written for later workflow steps, so publication attaches the manifest
this gate read rather than a hard-coded file name.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

APPROVED = "> **Status:** approved for release"
CANDIDATE = "> **Status:** release candidate; final approval pending"
_STATUS_PREFIX = "> **Status:**"

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
    statuses = [line for line in lines if line.startswith(_STATUS_PREFIX)]
    required = CANDIDATE if rc else APPROVED
    if statuses != [required]:
        raise ReleaseGateError(
            f"{manifest_rel} must carry exactly one status line, {required!r}; "
            f"found {statuses!r}"
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check release identity and evidence.")
    parser.add_argument("--tag", required=True, help="the pushed tag, e.g. v1.1.0")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--github-output", type=Path, help="append resolved paths here")
    args = parser.parse_args(argv)
    try:
        result = check(args.root, args.tag)
    except (ReleaseGateError, OSError) as error:
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
