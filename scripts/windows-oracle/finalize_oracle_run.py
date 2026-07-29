#!/usr/bin/env python3
"""Finalize a raw Windows oracle run into a validated evidence manifest.

The Windows harness (``run-evidence.ps1``) captures genuine raw evidence on the
domain-joined host and writes ``manifest.raw.json`` plus its artifacts into a run
directory.  This step runs on the machine that holds the git repository (the
Windows host has no git) and is the single authority for source provenance,
semantic normalization, comparison binding, and the final evidence state.

Usage::

    python finalize_oracle_run.py RUN_DIR [--repo-root PATH]

``RUN_DIR`` is the directory containing ``manifest.raw.json``.  ``--repo-root``
defaults to the repository this script lives in.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from gpo_studio.oracle_evidence import (  # noqa: E402
    OracleEvidenceError,
    canonical_manifest_hash,
    evidence_tag_name,
    finalize_oracle_run,
)


def _tag_evidence_commit(repo_root: Path, run_id: str, commit: str) -> str:
    """Tag the source commit a certified run binds itself to.

    Squash-merging orphans that commit: it becomes unreachable from ``main``
    and is collected eventually, after which the manifest's provenance claim
    cannot be checked from a fresh clone. The tag costs nothing, keeps the
    merge policy unchanged, and makes the binding permanent. See issue #22.

    Returns a human-readable outcome; never raises for an already-correct tag,
    because finalizing twice must stay idempotent.
    """
    tag = evidence_tag_name(run_id)

    existing = subprocess.run(
        ["git", "-C", str(repo_root), "rev-list", "-n", "1", tag],
        capture_output=True,
        text=True,
        check=False,
    )
    if existing.returncode == 0:
        found = existing.stdout.strip()
        if found == commit:
            return f"tag {tag} already points at {commit[:12]}"
        raise OracleEvidenceError(
            f"tag {tag} already exists at {found[:12]} but this run binds to "
            f"{commit[:12]}; refusing to move an evidence tag"
        )

    created = subprocess.run(
        [
            "git", "-C", str(repo_root), "tag", "-a", tag, commit,
            "-m", f"Source tree for certified evidence run {run_id} (issue #22).",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if created.returncode != 0:
        raise OracleEvidenceError(
            f"could not create evidence tag {tag}: {created.stderr.strip()}"
        )
    return f"created {tag} at {commit[:12]} — push it: git push origin {tag}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", help="directory containing manifest.raw.json")
    parser.add_argument(
        "--repo-root",
        default=str(_REPO_ROOT),
        help="git repository to derive source provenance from",
    )
    parser.add_argument(
        "--no-tag",
        action="store_true",
        help=(
            "do not create the evidence/<run-id> tag for a passing run. The tag "
            "preserves the source commit that squash-merging would otherwise "
            "orphan (issue #22); skip it only when tagging is handled elsewhere."
        ),
    )
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    repo_root = Path(args.repo_root)
    try:
        manifest = finalize_oracle_run(run_dir, repo_root)
    except OracleEvidenceError as exc:
        print(f"finalize failed: {exc}", file=sys.stderr)
        return 1
    print(f"MANIFEST_PATH={run_dir / 'manifest.json'}")
    print(f"EVIDENCE_STATE={manifest.capability.evidence_state}")
    print(f"CANONICAL_HASH={canonical_manifest_hash(manifest)}")

    # Only a passing run is a certification worth preserving a commit for.
    # A fail/inconclusive manifest is still evidence, but nothing later cites
    # its source tree as proof of anything.
    if manifest.capability.evidence_state == "pass" and not args.no_tag:
        try:
            outcome = _tag_evidence_commit(
                repo_root, manifest.run_id, manifest.source.commit
            )
        except OracleEvidenceError as exc:
            print(f"evidence tag failed: {exc}", file=sys.stderr)
            return 1
        print(f"EVIDENCE_TAG={outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
