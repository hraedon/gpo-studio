#!/usr/bin/env python3
"""Report what each source file costs to edit, in lanes that must be re-run.

Every live lane verdict records the files it bound by `(commit, path, sha256)`.
Editing one of those files expires every verdict that binds it, and re-earning
a verdict means a run on the Windows estate --- WI-048's ordering argument,
which is why harness-touching work is batched. The information to price a
change has therefore always been present and never been in one place: it is
spread across twenty verdict files, and the question "what does touching
`model.py` cost?" is answered by reading all of them.

This is that answer, generated. `docs/plan-033/bound-source-cost.md` is the
committed output and `tests/test_bound_source_cost.py` fails when the two
disagree, on the same contract as the work-item register's open index: a table
that drifts is worse than no table, because it is consulted.

The live set comes from `LIVE_VERDICTS` in `tests/test_committed_evidence.py`
rather than from a directory glob. That registry is what the freshness gate
already parametrises over, and deriving a second notion of "live" here would
give the project two answers to a question it has exactly one of.

    python scripts/plan-033/report-bound-source-cost.py            # to stdout
    python scripts/plan-033/report-bound-source-cost.py --write    # to the doc
"""

from __future__ import annotations

import argparse
import json
import runpy
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = REPO_ROOT / "docs" / "plan-033"
REGISTRY = REPO_ROOT / "tests" / "test_committed_evidence.py"
DOC = EVIDENCE / "bound-source-cost.md"

#: Files under these prefixes are product source; the rest is harness. The
#: split is the one a reader actually wants, because it separates "this costs
#: an estate run because it ships" from "this costs one because it measures".
_PRODUCT_PREFIX = "src/gpo_studio/"


def live_verdicts() -> dict[str, str]:
    """The verdicts the freshness gate considers live, from its own registry."""
    namespace = runpy.run_path(str(REGISTRY))
    live: dict[str, str] = namespace["LIVE_VERDICTS"]
    return live


def bound_files(live: dict[str, str]) -> dict[str, list[str]]:
    """Map each bound repository path to the lanes that bind it.

    A lane is named by its verdict's directory, which is how the batch note and
    the runbooks refer to them. WP-0 has no `verification.json` --- it records
    its bound set in `manifest.json` under `source.bound` (WI-062) --- so it is
    read separately rather than left out of a cost table it belongs in.
    """
    bound: dict[str, list[str]] = defaultdict(list)
    commits: set[str] = set()
    for relative in live:
        path = EVIDENCE / relative
        verdict = json.loads(path.read_text(encoding="utf-8"))
        lane = path.parent.name
        source = verdict.get("source", {})
        commits.add(source.get("commit", ""))
        for repo_path in (source.get("paths") or {}).values():
            bound[repo_path].append(lane)
    for manifest_path in _live_wp0_manifests(commits):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest["source"]["bound"]:
            bound[entry["path"]].append("wp0")
    return {path: sorted(lanes) for path, lanes in bound.items()}


def _live_wp0_manifests(live_commits: set[str]) -> list[Path]:
    """The WP-0 manifests bound to a commit the live lane verdicts also bind.

    WP-0 is not in `LIVE_VERDICTS` --- it has no `verification.json` --- so it
    needs its own liveness test, and a directory glob is not one: every batch
    leaves a `wp0-evidence/<batch>/wp0/manifest.json` behind and the retired
    ones are still on disk. Matching on the commit is the same question the
    freshness gate asks of everything else, and it self-corrects at the next
    batch instead of naming a directory that will be wrong by then.

    Selecting on `source.bound` alone would also have picked exactly the right
    manifest today --- the WI-059 pack predates the manifest form and has no
    such block --- which is precisely why it is not the test used: it would
    stop discriminating the moment every pack on disk had one, and it would do
    so silently.
    """
    return [
        path
        for path in sorted(EVIDENCE.glob("wp0-evidence/*/wp0/manifest.json"))
        if json.loads(path.read_text(encoding="utf-8"))
        .get("source", {})
        .get("commit")
        in live_commits
    ]


def _table(rows: list[tuple[str, list[str]]]) -> list[str]:
    lines = ["| File | Lanes | Which |", "|---|---:|---|"]
    for path, lanes in rows:
        lines.append(f"| `{path}` | {len(lanes)} | {', '.join(lanes)} |")
    return lines


def render(live: dict[str, str], bound: dict[str, list[str]]) -> str:
    product = sorted(
        ((p, ls) for p, ls in bound.items() if p.startswith(_PRODUCT_PREFIX)),
        key=lambda row: (-len(row[1]), row[0]),
    )
    harness = sorted(
        ((p, ls) for p, ls in bound.items() if not p.startswith(_PRODUCT_PREFIX)),
        key=lambda row: (-len(row[1]), row[0]),
    )
    total = len(live) + 1  # the live lane verdicts, plus WP-0
    unbound = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "src" / "gpo_studio").glob("*.py")
        if path.relative_to(REPO_ROOT).as_posix() not in bound
    )
    return "\n".join([
        "# What each file costs to edit",
        "",
        "**Generated.** `python scripts/plan-033/report-bound-source-cost.py",
        "--write` rewrites it and `tests/test_bound_source_cost.py` fails when",
        "this file and the evidence disagree. Do not hand-edit the tables.",
        "",
        "Every live verdict binds its harness and source by `(commit, path,",
        "sha256)`. Editing a bound file expires every verdict that binds it, and",
        "a verdict is re-earned only by a run on the estate. So the cost of a",
        "change is not its diff --- it is the number below, in lanes that must",
        "be re-run before the evidence is honest again.",
        "",
        "That is WI-048's ordering argument, and this table is what makes it",
        "checkable before the edit instead of after. It exists because the",
        "information was already complete and unreadable: spread across",
        f"{total} packs, so pricing one file meant opening all of them.",
        "",
        "**A zero-cost file is not a safe file.** It means no lane measured it,",
        "which is a statement about coverage rather than about quality --- and",
        "for anything in `src/gpo_studio/`, usually the more interesting one.",
        "",
        f"Live set: {len(live)} lane verdicts plus WP-0. Retired and",
        "pending-requalification verdicts are excluded; they bind the commits",
        "they name and are not re-earned by an edit today.",
        "",
        "## Product source",
        "",
        *_table(product),
        "",
        "## Harness",
        "",
        *_table(harness),
        "",
        "## Bound by nothing",
        "",
        "Every other module in `src/gpo_studio/`. No lane reads these, so an",
        "edit costs no estate time --- and none of their behaviour has been",
        "measured against Windows by the oracle either.",
        "",
        *(f"- `{path}`" for path in unbound),
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--write", action="store_true", help=f"rewrite {DOC.relative_to(REPO_ROOT)}"
    )
    args = parser.parse_args()
    live = live_verdicts()
    text = render(live, bound_files(live))
    if args.write:
        # `newline=""` and not `write_text`: on Windows the default translates
        # every "\n" to "\r\n", and `docs/plan-033/**/*.md` is `-text`, so
        # those endings would commit exactly as written. That is WI-063's
        # mechanism -- a file whose bytes changed because of the platform it
        # was generated on, with git reporting no drift. The reader is
        # unaffected either way (`read_text` folds both), which is what makes
        # it the kind of difference nobody notices.
        with DOC.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        print(f"wrote {DOC.relative_to(REPO_ROOT)}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
