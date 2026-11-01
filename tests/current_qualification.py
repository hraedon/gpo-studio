"""Which banked run is each lane's current certification.

Tests that ask "which run certifies lane X now?" read it here, never from a
batch manifest named in the test, so that banking a new batch changes no test.
That matters for every final release: its batch is banked in a commit B after
the freeze commit F, and the final-tag release gate
(``scripts/check_release_manifest.py``) refuses any test edit after F other than
the registry's verdict tables (Plan 036, the D-9 addendum).

The answer comes from the release evidence reports, read by version, so a new
release needs no edit here:

* the report of the package's version names a ``freeze`` batch manifest (from
  B on): that batch, for every lane. The earlier batches are history; their
  packs and tests stay as they are;
* its ``freeze`` is null (before B): the freeze batch of the latest approved
  release before it, the batch that release shipped on, with any run the
  verdict registry has made live since in place of the one it replaced. For
  1.2.0 that is the release 1.1.0 re-freeze batch.

The registry (``tests/test_committed_evidence.py``) stays the authority on what
is live; ``test_the_current_qualification_is_the_live_set`` in
``tests/test_current_qualification.py`` holds the two equal. The base batch is
read from the previous release's report, never from the registry, so that test
still says something while verdicts are pending requalification: every pending
verdict must be the previous release's run of its lane.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "plan-033"
DOCS = ROOT / "docs"
#: The verdict tables, as data (the same file tests/test_committed_evidence.py
#: loads and checks).
REGISTRY = EVIDENCE / "verdict-registry.json"
_REPORT_NAME = re.compile(r"release-evidence-report-(\d+)\.(\d+)\.(\d+)\.json")


def _load(relative: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    return data


def _version_key(base: str) -> tuple[int, int, int]:
    major, minor, patch = (int(part) for part in base.split("."))
    return major, minor, patch


def package_base_version() -> str:
    text = (ROOT / "src" / "gpo_studio" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "(\d+\.\d+\.\d+)', text, re.M)
    assert match is not None, "src/gpo_studio/__init__.py binds no release version"
    return match.group(1)


def release_report(base: str | None = None) -> dict[str, Any]:
    """The release evidence report of ``base`` (default: the package's version)."""
    return _load(f"docs/release-evidence-report-{base or package_base_version()}.json")


def freeze() -> dict[str, str] | None:
    """The report's ``freeze`` (``commit`` and ``batch_manifest``), or None."""
    value = release_report()["freeze"]
    return None if value is None else dict(value)


def latest_freeze() -> dict[str, str] | None:
    """The newest banked freeze: this release's, else the previous release's.

    None only before any release recorded a freeze. Tests that ask whether the
    release 1.1.0rc1 batches are history ask this, not :func:`freeze`, which is
    null again at the start of every release.
    """
    frozen = freeze()
    if frozen is not None:
        return frozen
    try:
        return previous_freeze()
    except AssertionError:
        return None


@cache
def previous_release() -> str:
    """The latest approved release before the package's version, with a freeze.

    Read from the committed reports (``docs/release-evidence-report-X.Y.Z.json``),
    highest version first; a report that never reached approval (an abandoned
    candidate) is skipped. 1.0.0 predates the report's ``freeze`` and is never
    the answer.
    """
    current = _version_key(package_base_version())
    earlier = []
    for path in DOCS.glob("release-evidence-report-*.json"):
        match = _REPORT_NAME.fullmatch(path.name)
        if match is None:
            continue
        base = ".".join(match.groups())
        if _version_key(base) < current:
            earlier.append(base)
    for base in sorted(earlier, key=_version_key, reverse=True):
        report = release_report(base)
        if report["status"] == "approved" and report["freeze"] is not None:
            return base
    raise AssertionError(
        f"no approved release with a freeze precedes {package_base_version()}, so no "
        "batch is the current qualification before this release's freeze"
    )


def previous_freeze() -> dict[str, str]:
    """The ``freeze`` of :func:`previous_release`: the batch it shipped on."""
    return dict(release_report(previous_release())["freeze"])


def _registry_live() -> set[str]:
    """Verdicts the registry treats as live: registered, not retired, not pending."""
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    parked = set(registry["retired_verdicts"]) | set(registry["pending_requalification"])
    live = {path for path in registry["lane_verdicts"] if path not in parked}
    return live | {registry["wp0_manifest"]}


@cache
def current_batches() -> tuple[str, ...]:
    """The batch manifests whose runs are current, later ones overriding earlier.

    From B on, the release's own freeze batch alone. Before it, the previous
    release's freeze batch, then (in path order) every other banked batch that
    holds a run the registry has made live since; a live verdict no batch
    manifest names is left out, and the live-set test fails on it.
    """
    frozen = freeze()
    if frozen is not None:
        return (frozen["batch_manifest"],)
    base = previous_freeze()["batch_manifest"]
    named = {run["verdict"] for run in _load(base)["runs"]}
    wanted = _registry_live() - named
    later = [
        relative
        for relative in sorted(
            path.relative_to(ROOT).as_posix() for path in EVIDENCE.glob("*-batch.json")
        )
        if relative != base and wanted & {run["verdict"] for run in _load(relative)["runs"]}
    ]
    return (base, *later)


@cache
def current_runs() -> dict[str, dict[str, Any]]:
    """Lane name -> its current run row (``verdict`` is relative to docs/plan-033)."""
    batches = current_batches()
    runs: dict[str, dict[str, Any]] = {run["name"]: run for run in _load(batches[0])["runs"]}
    live = _registry_live()
    for batch in batches[1:]:
        for run in _load(batch)["runs"]:
            # Only the runs that are live replace the base's; the rest of a later
            # batch is history (its other verdicts were retired or replaced).
            if run["verdict"] in live:
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
