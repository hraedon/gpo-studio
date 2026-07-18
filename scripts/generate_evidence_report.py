#!/usr/bin/env python3
"""Generate and verify the sanitized Windows lab evidence report.

This script computes the SHA-256 of ``docs/release-evidence-report.json``
and can verify it against an expected hash pinned in the repository.

The report itself is populated from the results of the Windows lab
validation sessions. The lab steps are documented in the report's
``conformance_corpus`` entries and the ``import_gpo_diagnosis`` section.
The artifact hashes in the report are computed by this script's
``--generate-hashes`` mode, which runs the conformance corpus through
the Studio export pipeline and records the SHA-256 of each artifact.

Usage::

    # Print the current report hash and summary
    uv run python scripts/generate_evidence_report.py --check

    # Regenerate artifact hashes from the conformance corpus
    uv run python scripts/generate_evidence_report.py --generate-hashes

    # Verify the report hash matches the pinned expected value
    uv run python scripts/generate_evidence_report.py --verify

"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPORT_PATH = Path(__file__).resolve().parent.parent / "docs" / "release-evidence-report.json"
EXPECTED_HASH_PATH = Path(__file__).resolve().parent.parent / "docs" / ".evidence-report-sha256"


def compute_sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def generate_artifact_hashes() -> list[dict[str, str]]:
    """Compute SHA-256 hashes for all conformance corpus artifacts."""
    from gpo_studio.conformance import corpus
    from gpo_studio.export import export_bundle, gpmc_backup_bundle, powershell_plan
    from gpo_studio.registry_pol import serialize

    results: list[dict[str, str]] = []
    for name, gpo in corpus():
        computer = [s for s in gpo.settings if s.side == "computer"]
        user = [s for s in gpo.settings if s.side == "user"]

        ps_plan = powershell_plan(gpo).encode("utf-8-sig")
        machine_pol = serialize(computer)
        user_pol = serialize(user)
        backup = gpmc_backup_bundle(gpo)
        bundle = export_bundle(gpo)

        results.append(
            {
                "fixture": name,
                "powershell_plan_sha256": hashlib.sha256(ps_plan).hexdigest(),
                "machine_registry_pol_sha256": hashlib.sha256(machine_pol).hexdigest(),
                "user_registry_pol_sha256": hashlib.sha256(user_pol).hexdigest(),
                "gpmc_backup_sha256": hashlib.sha256(backup).hexdigest(),
                "export_bundle_sha256": hashlib.sha256(bundle).hexdigest(),
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="GPO Studio evidence report generator")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Print the current report hash and summary.",
    )
    parser.add_argument(
        "--generate-hashes",
        action="store_true",
        help="Regenerate artifact hashes from the conformance corpus and print them.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify the report hash matches the pinned expected value.",
    )
    args = parser.parse_args()

    if args.generate_hashes:
        hashes = generate_artifact_hashes()
        print(json.dumps(hashes, indent=2))
        return 0

    if not REPORT_PATH.exists():
        print(f"ERROR: Report not found at {REPORT_PATH}", file=sys.stderr)
        return 1

    report_hash = compute_sha256(REPORT_PATH)
    print(f"Report: {REPORT_PATH}")
    print(f"SHA-256: {report_hash}")

    if args.check:
        try:
            data = json.loads(REPORT_PATH.read_text())
            print(f"Report type: {data.get('report_type')}")
            print(f"Report date: {data.get('report_date')}")
            print(f"Source commit: {data.get('source_commit', 'not specified')}")
            print(f"Fixtures: {len(data.get('conformance_corpus', []))}")
            print(f"Bugs found: {len(data.get('bugs_found_and_fixed', []))}")
            print(
                f"Unvalidated capabilities: "
                f"{len(data.get('capabilities_not_validated_by_windows_tooling', []))}"
            )
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON: {e}", file=sys.stderr)
            return 1

    if args.verify:
        if not EXPECTED_HASH_PATH.exists():
            print(f"ERROR: Expected hash file not found at {EXPECTED_HASH_PATH}", file=sys.stderr)
            print("Run with --check to get the current hash, then pin it:", file=sys.stderr)
            print(f"  echo '{report_hash}' > {EXPECTED_HASH_PATH}", file=sys.stderr)
            return 1
        expected = EXPECTED_HASH_PATH.read_text().strip()
        if report_hash != expected:
            print(
                f"ERROR: Hash mismatch! Expected: {expected}, Got: {report_hash}",
                file=sys.stderr,
            )
            return 1
        print("Hash verified: report matches pinned expected value.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
