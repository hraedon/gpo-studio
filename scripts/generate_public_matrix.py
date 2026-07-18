#!/usr/bin/env python3
"""Derive the public GPMC capability matrix from signed evidence packs.

This is the Plan 021 WP-4 public matrix generator. It loads one or more
versioned evidence packs (see ``docs/plan-021/reference-estates-and-evidence.md``
for the schema), refuses to derive claims from packs whose redaction or
licensing gates are unsatisfied, and emits a public capability matrix that
contains *only* claims backed by passing evidence.

Usage::

    # Report pack signability without emitting a matrix
    uv run python scripts/generate_public_matrix.py --pack path/to/pack.json --check

    # Emit the public matrix (markdown) from signed packs
    uv run python scripts/generate_public_matrix.py --pack a.json --pack b.json --matrix

    # Derive the canonical hash of a single pack (for pinning)
    uv run python scripts/generate_public_matrix.py --pack path/to/pack.json --hash

    # Verify a pack's canonical hash against an expected value
    uv run python scripts/generate_public_matrix.py --pack path/to/pack.json --verify <sha256>

A non-signable pack (``redaction_verified`` or ``licensing_complete`` false) is
refused by default. ``--allow-unsigned`` admits unsigned packs for development
and stamps the output ``DRAFT``; unsigned packs MUST NOT be used as release
evidence.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gpo_studio.evidence import (
    PackError,
    canonical_pack_hash,
    derive_claims,
    load_pack,
    render_matrix_markdown,
    signability_report,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Derive the public GPMC capability matrix from evidence packs.",
    )
    parser.add_argument(
        "--pack",
        action="append",
        type=Path,
        default=[],
        metavar="PATH",
        help="Path to an evidence pack JSON file. Repeatable.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Load packs and report signability without emitting a matrix.",
    )
    parser.add_argument(
        "--matrix",
        action="store_true",
        help="Emit the public capability matrix (markdown) to stdout.",
    )
    parser.add_argument(
        "--hash",
        action="store_true",
        help="Print the canonical SHA-256 of a single pack.",
    )
    parser.add_argument(
        "--verify",
        metavar="SHA256",
        help="Verify a single pack's canonical hash matches the expected value.",
    )
    parser.add_argument(
        "--allow-unsigned",
        action="store_true",
        help="Admit packs that have not satisfied the redaction/licensing gates. "
        "The output is stamped DRAFT and MUST NOT be used as release evidence.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        metavar="PATH",
        help="Write the matrix to a file instead of stdout.",
    )
    args = parser.parse_args(argv)

    if not args.pack:
        parser.error("at least one --pack is required")

    packs = []
    for path in args.pack:
        try:
            packs.append(load_pack(path))
        except PackError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    if args.hash:
        if len(packs) != 1:
            print("ERROR: --hash requires exactly one --pack", file=sys.stderr)
            return 1
        print(canonical_pack_hash(packs[0]))
        return 0

    if args.verify:
        if len(packs) != 1:
            print("ERROR: --verify requires exactly one --pack", file=sys.stderr)
            return 1
        actual = canonical_pack_hash(packs[0])
        if actual != args.verify:
            print(
                f"ERROR: hash mismatch. expected {args.verify}, got {actual}",
                file=sys.stderr,
            )
            return 1
        print(f"verified: {actual}")
        return 0

    if args.check or not args.matrix:
        # Report signability.
        any_unsigned = False
        for pack in packs:
            reasons = signability_report(pack)
            status = "signable" if not reasons else "UNSIGNED"
            if reasons:
                any_unsigned = True
            print(f"{pack.pack_id}: {status}")
            for reason in reasons:
                print(f"  - {reason}")
            passing = sum(1 for _ in pack.passing())
            print(f"  records: {len(pack.records)} ({passing} passing)")
        if args.check and not args.matrix:
            return 1 if any_unsigned and not args.allow_unsigned else 0

    if args.matrix:
        result = derive_claims(packs, allow_unsigned=args.allow_unsigned)
        if result.unsigned_pack_ids and not args.allow_unsigned:
            print(
                "ERROR: refusing to derive claims from unsigned packs: "
                f"{', '.join(result.unsigned_pack_ids)}. "
                "Pass --allow-unsigned for a DRAFT derivation.",
                file=sys.stderr,
            )
            return 1
        output = render_matrix_markdown(result)
        if args.output:
            args.output.write_text(output, encoding="utf-8")
            print(f"wrote {args.output}")
        else:
            print(output, end="")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
