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
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from gpo_studio.oracle_evidence import (  # noqa: E402
    OracleEvidenceError,
    assert_evidence_tag_writable,
    canonical_manifest_hash,
    finalize_oracle_run,
    tag_evidence_commit,
)


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
    # This finalizer writes its manifest inside the library call, so the tag
    # cannot be created first. Check upfront instead that a tag could be created
    # at all, which is the failure that would otherwise leave a written manifest
    # with nothing preserving its commit.
    if not args.no_tag:
        try:
            assert_evidence_tag_writable(repo_root)
        except OracleEvidenceError as exc:
            print(f"finalize refused: {exc}", file=sys.stderr)
            return 1
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
            outcome = tag_evidence_commit(
                repo_root, manifest.run_id, manifest.source.commit
            )
        except OracleEvidenceError as exc:
            print(f"evidence tag failed: {exc}", file=sys.stderr)
            return 1
        print(f"EVIDENCE_TAG={outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
