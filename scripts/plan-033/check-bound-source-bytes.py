#!/usr/bin/env python3
"""WI-059 preflight: refuse bound source bytes that Git will not reproduce.

Run before reserving the estate. This does not mint a verdict, replace the
finalizers' clean-tree checks. The finalizers enforce the same shared check
again before grading. Nothing is normalized or rewritten.
"""

from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path

from gpo_studio.oracle_evidence import SourceBytesError, assert_bound_source_bytes


def bound_source_paths(repo_root: Path) -> dict[str, tuple[str, ...]]:
    """Read the existing trusted finalizers' tables, including WP-0's distinct shape.

    These are the sets the current verdicts bind, not a claim that they include
    every Python dependency. No finalizer main function is executed.
    """
    lanes = {}
    directory = repo_root / "scripts" / "windows-oracle"
    for finalizer in sorted(directory.glob("finalize_*.py")):
        symbols = runpy.run_path(str(finalizer))
        if finalizer.name == "finalize_oracle_run.py":
            function = symbols["finalize_oracle_run"]
            table = function.__globals__["_HARNESS_INPUT_FILES"]["psdirect"]
            paths = {row[2] for row in table}
        else:
            paths = set()
            for keyed, flat in (
                ("TRANSPORT_DEPLOYED_FILES", "DEPLOYED_FILES"),
                ("TRANSPORT_LOCAL_FILES", "LOCAL_FILES"),
            ):
                table = symbols[keyed]["psdirect"] if keyed in symbols else symbols[flat]
                paths.update(table.values())
        if not paths:
            raise SourceBytesError(f"{finalizer.name}: no bound source paths")
        lanes[finalizer.name] = tuple(sorted(paths))
    if not lanes:
        raise SourceBytesError("no finalizers found")
    return lanes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args(argv)
    try:
        lanes = bound_source_paths(args.repo_root)
        paths = {path for lane in lanes.values() for path in lane}
        assert_bound_source_bytes(args.repo_root, paths)
    except (SourceBytesError, OSError, KeyError) as exc:
        print(f"preflight refused: {exc}", file=sys.stderr)
        return 1
    print(f"Bound source bytes match index and HEAD: {len(paths)} files, {len(lanes)} finalizers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
