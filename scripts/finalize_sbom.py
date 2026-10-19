"""Add the deterministic release identity required for SBOM attestation."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any


def deterministic_serial(repository: str, source_commit: str) -> str:
    """Return a stable CycloneDX serial number for one repository commit."""
    identity = f"https://github.com/{repository}@{source_commit}"
    return f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, identity)}"


def finalize_sbom(path: Path, repository: str, source_commit: str) -> str:
    """Validate a CycloneDX JSON document and add its deterministic serial."""
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("bomFormat") != "CycloneDX":
        raise ValueError("SBOM must be a CycloneDX JSON object")
    if not isinstance(data.get("specVersion"), str):
        raise ValueError("CycloneDX SBOM must declare specVersion")

    serial = deterministic_serial(repository, source_commit)
    data["serialNumber"] = serial
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return serial


def resolve_commit(sha: str, cwd: Path) -> str:
    """The commit ``sha`` names in the checkout: itself, or an annotated tag's commit.

    ``GITHUB_SHA`` for a pushed annotated tag may be the tag object (DeepSeek
    final-r4 N1; not verified against GitHub's documentation here). The serial
    identifies the source commit, so it is derived from the commit either way.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"],
        cwd=cwd, capture_output=True, text=True, timeout=60, check=False,
    )
    commit = result.stdout.strip()
    if result.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError(f"--source-commit {sha!r} does not resolve to a commit here")
    return commit


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add a deterministic serial number to a CycloneDX SBOM.",
    )
    parser.add_argument("path", type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    try:
        commit = resolve_commit(args.source_commit, Path.cwd())
    except ValueError as error:
        print(f"finalize_sbom: FAIL: {error}", file=sys.stderr)
        return 1
    serial = finalize_sbom(args.path, args.repository, commit)
    print(f"Finalized CycloneDX SBOM {args.path} with {serial}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
