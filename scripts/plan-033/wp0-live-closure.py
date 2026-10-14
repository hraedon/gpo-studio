#!/usr/bin/env python3
"""Plan 036 WP-0 (WI-083): live-lane dependency closure, replay, re-grade, records.

    python scripts/plan-033/wp0-live-closure.py derive --out part1.json
    python scripts/plan-033/wp0-live-closure.py run --target F --workdir DIR
    python scripts/plan-033/wp0-live-closure.py select DIR0/result.json DIR1/result.json
    python scripts/plan-033/wp0-live-closure.py write-records --workdir DIR --python-version 3.14
    python scripts/plan-033/wp0-live-closure.py check-pin

`run` works only in fresh `git worktree` clones under --workdir and writes
nothing into the repository; put --workdir on disk, not on a RAM-backed /tmp.
`write-records` is the freeze's step 7 (plans/036-change-cost.md, "Order"):
it refuses unless the tree is clean and every replay and re-grade reproduces.
The package is plan036_wp0/ beside this file; see its docstrings.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from plan036_wp0.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
