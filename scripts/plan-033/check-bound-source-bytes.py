#!/usr/bin/env python3
"""WI-059 preflight: refuse bound source bytes that Git will not reproduce.

Run before reserving the estate. This does not mint a verdict, replace the
finalizers' clean-tree checks, or close WI-059: finalizers must also enforce
the invariant in the next harness batch. Nothing is normalized or rewritten.
"""

from __future__ import annotations

import argparse
import runpy
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path, PurePosixPath


class SourceBytesError(ValueError):
    """The source set cannot be reproduced from the recorded Git revision."""


def assert_bound_source_bytes(repo_root: Path, paths: Iterable[str]) -> None:
    """Compare raw worktree/index/HEAD bytes; Git's normalized status is insufficient.

    The index is the WI-059 comparison. HEAD is checked too: a staged edit
    cannot become evidence for the older commit a finalizer would name.
    Read in binary mode so Python cannot conceal CRLF differences on Windows.
    """
    problems = []
    unique_paths = sorted(set(paths))
    if not unique_paths:
        raise SourceBytesError("no bound source paths supplied")
    for relative in unique_paths:
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or "\\" in relative or ":" in relative:
            problems.append(f"{relative}: expected a repository-relative Git path")
            continue
        blobs = {}
        for label, revision in (("index", ""), ("HEAD", "HEAD")):
            try:
                blobs[label] = subprocess.run(
                    ["git", "show", f"{revision}:{relative}"],
                    cwd=repo_root,
                    capture_output=True,
                    check=True,
                    timeout=30,
                ).stdout
            except (OSError, subprocess.SubprocessError):
                problems.append(f"{relative}: cannot read {label} blob")
        try:
            working = (repo_root / relative).read_bytes()
        except OSError:
            problems.append(f"{relative}: cannot read working-tree file")
            continue
        if "index" in blobs and working != blobs["index"]:
            problems.append(f"{relative}: working-tree bytes differ from index")
        if "index" in blobs and "HEAD" in blobs and blobs["index"] != blobs["HEAD"]:
            problems.append(f"{relative}: index bytes differ from HEAD")
    if problems:
        raise SourceBytesError("bound source bytes refused:\n" + "\n".join(problems))


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
