"""The committed verdicts are the reviewable product; check they are coherent.

A verdict under `docs/plan-033/` is what a reviewer reads instead of re-running
a lane, so its internal claims have to hold up without the estate. These are the
checks a reviewer would otherwise have to do by eye, and one of them exists
because a reviewer did it by eye and reached a wrong conclusion: `source.files`
had acquired entries (`candidate/candidate.zip`) that are generated artifacts
rather than repository files, so resolving the block against `source.commit`
failed and the verdict looked malformed. The block now contains only what its
name promises, and this pins that.
"""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from typing import Any, cast

import pytest

REPO_ROOT = Path(__file__).parents[1]
ORACLE_DIR = REPO_ROOT / "scripts" / "windows-oracle"
EVIDENCE = REPO_ROOT / "docs" / "plan-033"

#: committed verdict -> the finalizer whose tables define its bound file set.
#:
#: The RSOP lanes were absent from this map until 2026-08-04, so their committed
#: verdicts -- the ones a reviewer reads instead of re-running the lane -- were
#: the only ones nothing checked. Their finalizers use flat file tables rather
#: than the transport-keyed shape the older lanes use, and that difference is
#: what kept them out; `_bound_names` now handles both rather than the map
#: quietly covering three lanes out of five.
LANE_VERDICTS = {
    "wp1b-evidence/verification-estate.json": "finalize_wp1b_run.py",
    "wp2-evidence/verification-estate.json": "finalize_wp2_import_run.py",
    "wp3-evidence/verification-estate.json": "finalize_wp3_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804020517-2089.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804051032-8845.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804051228-2926.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804070708-6831.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804151624-6393.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804152957-1430.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804154241-9337.json": "finalize_rsop_run.py",
    "wp9-evidence/verdict-rsop-user-observe-20260804050024-4383.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260804045552-9148.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260804045809-8312.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260804065146-4224.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260804065525-9254.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260804150527-3868.json": (
        "finalize_rsop_user_run.py"
    ),
}


def _verdict(relative: str) -> dict[str, Any]:
    return cast(
        dict[str, Any], json.loads((EVIDENCE / relative).read_text(encoding="utf-8"))
    )


def _file_tables(finalizer: str) -> list[dict[str, str]]:
    """The name -> repository path tables this finalizer binds, in either shape.

    Lanes that once supported two transports key their tables by transport;
    the RSOP lanes were written after the SSH path was deleted and key them
    directly. Both are read here so the checks below cover every lane rather
    than the ones that happen to share a shape.
    """
    symbols = runpy.run_path(str(ORACLE_DIR / finalizer))
    tables: list[dict[str, str]] = []
    for transport_keyed, flat in (
        ("TRANSPORT_DEPLOYED_FILES", "DEPLOYED_FILES"),
        ("TRANSPORT_LOCAL_FILES", "LOCAL_FILES"),
    ):
        if transport_keyed in symbols:
            tables.append(cast(dict[str, dict[str, str]], symbols[transport_keyed])["psdirect"])
        else:
            tables.append(cast(dict[str, str], symbols[flat]))
    return tables


def _bound_names(finalizer: str) -> set[str]:
    return {name for table in _file_tables(finalizer) for name in table}


@pytest.mark.parametrize("relative,finalizer", sorted(LANE_VERDICTS.items()))
def test_source_files_holds_exactly_the_bound_repository_files(
    relative: str, finalizer: str
) -> None:
    """Every `source.files` key names a file this lane binds, and nothing else.

    The block sits under `source: {commit, dirty, files}`, so each key has to be
    resolvable against that commit. A generated artifact filed there is not a
    small untidiness: it makes the verdict unverifiable by the obvious method.
    """
    verdict = _verdict(relative)
    assert set(verdict["source"]["files"]) == _bound_names(finalizer)


@pytest.mark.parametrize("relative,finalizer", sorted(LANE_VERDICTS.items()))
def test_every_bound_file_still_exists_in_the_tree(
    relative: str, finalizer: str
) -> None:
    """A verdict naming a file the repository no longer has cannot be re-checked."""
    for table in _file_tables(finalizer):
        for name, source in table.items():
            assert (REPO_ROOT / source).is_file(), f"{name} -> {source}"


@pytest.mark.parametrize("relative,finalizer", sorted(LANE_VERDICTS.items()))
def test_a_verdict_is_internally_consistent(relative: str, finalizer: str) -> None:
    """`passed` must follow from the state and the checks recorded beside it.

    Committed evidence is no longer all passes. A scenario may DECLARE that it
    expects to diverge -- the deny row and the WMI row exist to demonstrate
    capabilities the model does not have -- and its verdict is committed as
    evidence of the gap. Such a verdict must carry `expected-finding`, must not
    claim `passed`, and must actually contain the divergence: a declared
    divergence with an empty finding list would be a claim with nothing behind
    it.

    Everything else must be a pass whose checks agree with it. A verdict that
    said `passed` while carrying a false check would mean the file was edited
    after the fact, or assembled from more than one run.
    """
    verdict = _verdict(relative)
    if verdict.get("state") == "expected-finding":
        assert verdict["passed"] is False
        assert verdict["expected_finding"], relative
        comparison = verdict["comparison"]
        assert comparison is not None, relative
        divergences = (
            comparison["value_findings"]
            + comparison["applied_only_predicted"]
            + comparison["applied_only_observed"]
        )
        assert divergences, f"{relative} declares a divergence and records none"
        assert verdict["source"]["dirty"] is False
        assert verdict["transport"] == "psdirect"
        return

    assert verdict["passed"] is True
    if "checks" in verdict:
        failed = sorted(name for name, ok in verdict["checks"].items() if not ok)
        assert not failed, f"{relative} claims passed with failing checks: {failed}"
    for candidate in verdict.get("candidates", []):
        failed = sorted(name for name, ok in candidate["checks"].items() if not ok)
        assert not failed, f"{relative}:{candidate['candidate_id']} {failed}"
        assert candidate["state"] == "pass"
    assert verdict["source"]["dirty"] is False
    assert verdict["transport"] == "psdirect"


def test_wp0_manifest_is_a_pass_bound_to_a_resolvable_commit() -> None:
    manifest = _verdict("wp0-evidence/manifest-estate.json")
    assert manifest["capability"]["evidence_state"] == "pass"
    assert manifest["source"]["dirty"] is False
