"""Which banked run is each lane's current certification.

Tests that ask "which run certifies lane X now?" read it here, never from a
batch manifest named in the test, so that banking a new batch changes no test.
That matters for the 1.1.0 final release: its batch is banked in a commit B
after the freeze commit F, and the final-tag release gate
(``scripts/check_release_manifest.py``) refuses any test edit after F other than
the registry's verdict tables (Plan 036, the D-9 addendum).

The answer comes from the release evidence report of the package's version:

* ``freeze`` is null (until the batch at F is banked): the release 1.1.0 batch,
  with its successor batch's six runs in place of the ones they replaced;
* ``freeze`` names a batch manifest: that batch, for every lane. The earlier
  batches are history; their packs and tests stay as they are.

The registry (``tests/test_committed_evidence.py``) stays the authority on what
is live; ``test_the_current_qualification_is_the_live_set`` in
``tests/test_current_qualification.py`` holds the two equal.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "plan-033"
#: The release 1.1.0rc1 qualification: a batch and the successor batch that
#: re-ran six of its lanes. History once a freeze batch is banked.
RC_BATCH = "docs/plan-033/release110-batch.json"
RC_SUCCESSORS = "docs/plan-033/release110-successors-batch.json"


def _load(relative: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    return data


def package_base_version() -> str:
    text = (ROOT / "src" / "gpo_studio" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "(\d+\.\d+\.\d+)', text, re.M)
    assert match is not None, "src/gpo_studio/__init__.py binds no release version"
    return match.group(1)


def release_report() -> dict[str, Any]:
    return _load(f"docs/release-evidence-report-{package_base_version()}.json")


def freeze() -> dict[str, str] | None:
    """The report's ``freeze`` (``commit`` and ``batch_manifest``), or None."""
    value = release_report()["freeze"]
    return None if value is None else dict(value)


@cache
def current_batches() -> tuple[str, ...]:
    """The batch manifests whose runs are current, later ones overriding earlier."""
    frozen = freeze()
    if frozen is None:
        return (RC_BATCH, RC_SUCCESSORS)
    return (frozen["batch_manifest"],)


@cache
def current_runs() -> dict[str, dict[str, Any]]:
    """Lane name -> its current run row (``verdict`` is relative to docs/plan-033)."""
    runs: dict[str, dict[str, Any]] = {}
    for batch in current_batches():
        for run in _load(batch)["runs"]:
            runs[run["name"]] = run
    return runs


def run(name: str) -> dict[str, Any]:
    return dict(current_runs()[name])


def verdict_path(name: str) -> Path:
    return EVIDENCE / run(name)["verdict"]


def verdict(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(verdict_path(name).read_text(encoding="utf-8-sig"))
    return data


def pack(name: str) -> Path:
    return verdict_path(name).parent
