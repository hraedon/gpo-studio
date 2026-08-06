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

import hashlib
import json
import runpy
import subprocess
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
    # WP-6B's first certification and the WI-031 enforcement arc. These were
    # committed but never mapped, so nothing checked them -- and the four
    # `finding` verdicts among them could not have been added at all, because
    # the consistency test had no branch for that state and demanded `passed`.
    "wp6-evidence/verdict-rsop-observe-20260804010341-7165.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804010551-9363.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804010738-5543.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804012618-5426.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804012803-7606.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804015016-5317.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804015258-1810.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804015447-4913.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804020109-7624.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260804020308-9752.json": "finalize_rsop_run.py",
    # WI-039: the one undeclared finding this lane has produced.
    "wp6-evidence/verdict-rsop-observe-20260804153726-7284.json": "finalize_rsop_run.py",
    # Re-certification 2026-08-05, after the finalizers' harness check was made
    # falsifiable (review finding 3). A certification binds the harness that
    # produced it, so changing the finalizer meant every earlier verdict
    # described code that no longer ships. Ten scenarios, all `pass`.
    "wp6-evidence/verdict-rsop-observe-20260805064008-9181.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805064155-8996.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805064351-9402.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805064540-1562.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805064725-4970.json": "finalize_rsop_run.py",
    # WI-040, both halves of the arc: the `expected-finding` that measured the
    # read-deny gap and the `pass` that certified the fix. Committed together
    # on purpose -- the gap and its closure are only readable as a pair.
    "wp6-evidence/verdict-rsop-observe-20260805045139-3731.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805045851-3883.json": "finalize_rsop_run.py",
    "wp9-evidence/verdict-rsop-user-observe-20260805065203-1562.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805065415-8622.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805065630-6815.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805065943-6615.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805070255-2473.json": (
        "finalize_rsop_user_run.py"
    ),
    # Re-certification 2026-08-05 under the WI-043 result contract. Making
    # `_gpo_filter_status` side-aware changed what a verdict MEANS -- the
    # prediction gained `unevaluable_gpos` and the finalizers stopped grading
    # those rows -- so every earlier verdict describes a harness that no longer
    # ships. Eleven scenarios at `a85736a`, all `pass`, all conclusive. This set
    # also re-earns the two WI-040 verdicts, whose `harness_matches_source` was
    # produced by the self-comparing check fixed in `d1eec72` hours after they
    # ran; those two are kept below as the historical record of the divergence,
    # not as live certifications.
    "wp6-evidence/verdict-rsop-observe-20260805194053-7180.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805194245-3734.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805194432-5944.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805194627-2633.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805194814-2731.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805195001-1590.json": "finalize_rsop_run.py",
    "wp9-evidence/verdict-rsop-user-observe-20260805195149-5629.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805195400-1809.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805195614-1767.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805195909-4033.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805200214-4370.json": (
        "finalize_rsop_user_run.py"
    ),
    # Re-certification 2026-08-05 (second round) at `faad341`, after review
    # round 3 added exhaustive dispatch to the candidate builder. That file is
    # bound BY HASH in `LOCAL_FILES`, so changing it invalidated the `a85736a`
    # set above even though the prediction output is byte-identical -- verified
    # by rebuilding the deny-read candidate and diffing all three artifacts.
    # "The output did not change" is not the rule; a certification binds the
    # harness. Eleven scenarios, all `pass`, all conclusive.
    "wp6-evidence/verdict-rsop-observe-20260805220819-4762.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805221004-8571.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805221150-4243.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805221335-1702.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805221522-1983.json": "finalize_rsop_run.py",
    "wp6-evidence/verdict-rsop-observe-20260805221707-4871.json": "finalize_rsop_run.py",
    "wp9-evidence/verdict-rsop-user-observe-20260805221856-6415.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805222106-2378.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805222317-3382.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805222624-9750.json": (
        "finalize_rsop_user_run.py"
    ),
    "wp9-evidence/verdict-rsop-user-observe-20260805222929-6350.json": (
        "finalize_rsop_user_run.py"
    ),
}

#: Verdicts committed BEFORE the transport was recorded, kept as history.
#:
#: They are listed rather than skipped by pattern so that adding to this set is
#: a deliberate act with a reason, not something a new file drifts into. The
#: psdirect assertions genuinely cannot apply to them; every other verdict must
#: be mapped above.
PRE_TRANSPORT_VERDICTS = {
    "wp1b-evidence/verification.json",
    "wp3-evidence/verification.json",
}

#: Verdicts whose harness has MOVED ON, kept as history rather than as claims.
#:
#: A certification binds the harness that produced it, so a verdict is a live
#: claim only while the files it names still hash to what it recorded. When a
#: harness file changes, every verdict bound to the old content stops being a
#: certification and becomes a record of something that happened once. Twice now
#: the RSOP lanes have been fully re-run for exactly this reason -- when the
#: finalizers' harness check was made falsifiable, and when review round 3
#: changed `build-rsop-candidate.py` -- and BOTH times the staleness was caught
#: by a person noticing rather than by a test (WI-045).
#:
#: These are kept deliberately. The operator ruled that `...045139-3731` retains
#: its value because the divergence it observed on a real client does not depend
#: on the harness check; the same argument covers the rest. History that is
#: supposed to be stale is why this cannot simply be "every verdict matches the
#: tree" -- that assertion would fail on day one and get switched off, which is
#: worse than no gate at all.
#:
#: ENUMERATED, never matched by pattern, for the reason `PRE_TRANSPORT_VERDICTS`
#: gives: retiring a certification should be a deliberate act with a reason, not
#: something a file drifts into. And it is not an escape hatch --
#: `test_retired_verdicts_are_genuinely_stale` fails if a verdict listed here
#: still matches the tree, so a live claim cannot be quietly parked in here to
#: silence the gate below.
RETIRED_VERDICTS = {
    # WP-6B's first certification and the WI-031 enforcement arc, superseded
    # when `run-rsop-author.ps1` and the candidate builder moved on.
    "wp6-evidence/verdict-rsop-observe-20260804010341-7165.json",
    "wp6-evidence/verdict-rsop-observe-20260804010551-9363.json",
    "wp6-evidence/verdict-rsop-observe-20260804010738-5543.json",
    "wp6-evidence/verdict-rsop-observe-20260804012618-5426.json",
    "wp6-evidence/verdict-rsop-observe-20260804012803-7606.json",
    "wp6-evidence/verdict-rsop-observe-20260804015016-5317.json",
    "wp6-evidence/verdict-rsop-observe-20260804015258-1810.json",
    "wp6-evidence/verdict-rsop-observe-20260804015447-4913.json",
    "wp6-evidence/verdict-rsop-observe-20260804020109-7624.json",
    "wp6-evidence/verdict-rsop-observe-20260804020308-9752.json",
    "wp6-evidence/verdict-rsop-observe-20260804020517-2089.json",
    "wp6-evidence/verdict-rsop-observe-20260804051032-8845.json",
    "wp6-evidence/verdict-rsop-observe-20260804051228-2926.json",
    "wp6-evidence/verdict-rsop-observe-20260804070708-6831.json",
    "wp6-evidence/verdict-rsop-observe-20260804151624-6393.json",
    "wp6-evidence/verdict-rsop-observe-20260804152957-1430.json",
    # WI-039, the one undeclared finding this lane has produced, and its fix.
    "wp6-evidence/verdict-rsop-observe-20260804153726-7284.json",
    "wp6-evidence/verdict-rsop-observe-20260804154241-9337.json",
    # WI-040's arc: the `expected-finding` that measured the read-deny gap and
    # the `pass` that certified the fix. Kept for the divergence they observed
    # on a real client, which does not depend on the harness binding.
    "wp6-evidence/verdict-rsop-observe-20260805045139-3731.json",
    "wp6-evidence/verdict-rsop-observe-20260805045851-3883.json",
    # Re-certification generation 1, superseded by the WI-043 contract change.
    "wp6-evidence/verdict-rsop-observe-20260805064008-9181.json",
    "wp6-evidence/verdict-rsop-observe-20260805064155-8996.json",
    "wp6-evidence/verdict-rsop-observe-20260805064351-9402.json",
    "wp6-evidence/verdict-rsop-observe-20260805064540-1562.json",
    "wp6-evidence/verdict-rsop-observe-20260805064725-4970.json",
    # Re-certification generation 2 (`a85736a`), superseded hours later by
    # review round 3's change to `build-rsop-candidate.py`. The prediction
    # output was byte-identical across that change -- which is worth knowing and
    # is NOT the standard. A certification binds the harness, not the output.
    "wp6-evidence/verdict-rsop-observe-20260805194053-7180.json",
    "wp6-evidence/verdict-rsop-observe-20260805194245-3734.json",
    "wp6-evidence/verdict-rsop-observe-20260805194432-5944.json",
    "wp6-evidence/verdict-rsop-observe-20260805194627-2633.json",
    "wp6-evidence/verdict-rsop-observe-20260805194814-2731.json",
    "wp6-evidence/verdict-rsop-observe-20260805195001-1590.json",
    # WP-9's own arc, same three generations.
    "wp9-evidence/verdict-rsop-user-observe-20260804045552-9148.json",
    "wp9-evidence/verdict-rsop-user-observe-20260804045809-8312.json",
    "wp9-evidence/verdict-rsop-user-observe-20260804050024-4383.json",
    "wp9-evidence/verdict-rsop-user-observe-20260804065146-4224.json",
    # WI-033: the deny a real client demonstrated the model could not express.
    "wp9-evidence/verdict-rsop-user-observe-20260804065525-9254.json",
    "wp9-evidence/verdict-rsop-user-observe-20260804150527-3868.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805065203-1562.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805065415-8622.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805065630-6815.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805065943-6615.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805070255-2473.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805195149-5629.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805195400-1809.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805195614-1767.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805195909-4033.json",
    "wp9-evidence/verdict-rsop-user-observe-20260805200214-4370.json",
}

#: The verdicts that are still CLAIMS: everything mapped and not retired.
LIVE_VERDICTS = {
    relative: finalizer
    for relative, finalizer in LANE_VERDICTS.items()
    if relative not in RETIRED_VERDICTS
}


def _verdict(relative: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((EVIDENCE / relative).read_text(encoding="utf-8")))


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
def test_every_bound_file_still_exists_in_the_tree(relative: str, finalizer: str) -> None:
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

    if verdict.get("state") == "finding":
        # An UNDECLARED divergence: the lane predicted one thing, Windows did
        # another, and nobody saw it coming. WI-039 is the example and it is the
        # most valuable evidence this lane has produced -- reading the code
        # could not have found it.
        #
        # There was no branch for this state, so the assertions below demanded
        # `passed` and any attempt to commit such a verdict into the map failed.
        # The effect was that the one class of finding worth keeping was the one
        # class that could not be recorded here.
        assert verdict["passed"] is False, relative
        comparison = verdict["comparison"]
        assert comparison is not None, relative
        divergences = (
            comparison["value_findings"]
            + comparison["applied_only_predicted"]
            + comparison["applied_only_observed"]
        )
        assert divergences, f"{relative} is a finding and records no divergence"
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


def _recorded_vs_tree(relative: str, finalizer: str) -> list[tuple[str, str, str]]:
    """(name, recorded, on-disk) for every bound file whose hash has moved.

    Returns the empty list when the verdict still binds the shipping harness.
    Files the tables do not name are left to
    `test_source_files_holds_exactly_the_bound_repository_files`, which is the
    check that owns that failure.
    """
    verdict = _verdict(relative)
    paths = {name: source for table in _file_tables(finalizer) for name, source in table.items()}
    drifted: list[tuple[str, str, str]] = []
    for name, recorded in verdict["source"]["files"].items():
        source = paths.get(name)
        if source is None:
            continue
        actual = hashlib.sha256((REPO_ROOT / source).read_bytes()).hexdigest()
        if actual != recorded:
            drifted.append((name, recorded, actual))
    return drifted


@pytest.mark.parametrize("relative,finalizer", sorted(LIVE_VERDICTS.items()))
def test_a_live_verdict_still_binds_the_harness_that_ships(
    relative: str, finalizer: str
) -> None:
    """A live certification's bound files must still hash to what it recorded.

    This is the check the project has been performing by hand. Twice the RSOP
    lanes were re-run because a harness file moved underneath their verdicts,
    and both times the discovery was somebody thinking to look -- while commit
    messages, the plan and the capability matrix all cite the binding as though
    something enforced it.

    The tests around this one are careful about everything adjacent and never
    hash a file: `source.files` KEYS are checked against the lane's tables, each
    bound path is checked to EXIST, and each verdict is checked against ITSELF.
    A verdict can satisfy all three while naming content the repository no
    longer has -- which is exactly the state the re-certifications existed to
    leave behind.
    """
    drifted = _recorded_vs_tree(relative, finalizer)
    assert not drifted, (
        f"{relative} is a live certification, but the harness it binds has "
        "changed: "
        + "; ".join(
            f"{name} recorded {rec[:12]}, tree has {act[:12]}" for name, rec, act in drifted
        )
        + ". Either re-run the lane so the verdict binds the shipping code, or "
        "move it to RETIRED_VERDICTS with a reason if it is now history."
    )


def test_retired_verdicts_are_genuinely_stale() -> None:
    """The control, and the thing that stops `RETIRED_VERDICTS` being a hatch.

    Two properties, and both matter:

    1. **The check above can fail.** A gate nobody has seen fail is a gate
       nobody should trust -- this project has already been burned by a test
       whose assertions were satisfied by the file's own contents, and a
       reviewer then cleared a hazard partly BECAUSE that test "pinned" it. Here
       the repository itself carries the negative case: 47 verdicts whose
       harness genuinely moved on. If the hashing logic ever silently stops
       hashing, this test goes red first.

    2. **A live claim cannot be parked here to silence the gate.** The cheap way
       out of a failing freshness check is to declare the verdict history. That
       only works if it really is history, because a retired verdict that still
       matches the tree fails right here.
    """
    not_stale = sorted(
        relative
        for relative in RETIRED_VERDICTS
        if not _recorded_vs_tree(relative, LANE_VERDICTS[relative])
    )
    assert not not_stale, (
        "These verdicts are listed as retired history but still bind the "
        f"shipping harness exactly: {not_stale}. A verdict that matches the "
        "tree is a live certification -- remove it from RETIRED_VERDICTS "
        "rather than retiring a claim that still holds."
    )


def test_the_live_set_is_not_empty_and_covers_every_lane() -> None:
    """Retiring everything would make the freshness check vacuous silently.

    `LIVE_VERDICTS` is a subtraction, so it degrades quietly: retire enough and
    the parametrised test above simply stops generating cases, reporting green
    for a repository whose every claim has expired. Each lane must keep at least
    one verdict that still binds the code it ships.
    """
    assert set(LANE_VERDICTS) >= RETIRED_VERDICTS, (
        "RETIRED_VERDICTS names verdicts that are not in LANE_VERDICTS: "
        f"{sorted(RETIRED_VERDICTS - set(LANE_VERDICTS))}"
    )
    lanes = {relative.split("/")[0] for relative in LIVE_VERDICTS}
    assert lanes == {relative.split("/")[0] for relative in LANE_VERDICTS}, (
        f"Some lane has no live certification left: {sorted(lanes)}"
    )


def test_wp0_manifest_is_a_pass_bound_to_a_resolvable_commit() -> None:
    """WP-0 binds by COMMIT, not by file hashes — so it is checked differently.

    Its manifest carries `source: {commit, dirty}` and no `files` block at all,
    which is why it is absent from `LANE_VERDICTS` and outside the freshness
    gate above. Nothing is wrong with that; it is a different binding. But it
    means the gate must not be read as covering WP-0.

    Until now this test asserted the `pass` and the clean tree and **never
    checked that the commit resolves**, while its name said it did. That is not
    a hypothetical gap: the 2026-08-03 evidence-binding audit found FOUR cited
    commits that no longer resolve, all squash-merge orphans, and issue #22's
    remedy (auto-tagging passing runs) exists precisely to stop it recurring.
    A test named for the property, that does not test the property, is how the
    next reviewer gets talked out of checking by hand.

    SKIPPED ON A SHALLOW CLONE, and that is not a dodge. CI checks out with
    actions/checkout's default `fetch-depth: 1`, so the object is genuinely
    absent there and asserting would fail a healthy commit — the failure mode
    would be noise, and noisy gates get deleted. Where the history is present
    (any developer clone, and any CI job that deepens its checkout) the
    property is enforced for real.
    """
    manifest = _verdict("wp0-evidence/manifest-estate.json")
    assert manifest["capability"]["evidence_state"] == "pass"
    assert manifest["source"]["dirty"] is False
    assert "files" not in manifest["source"], (
        "The WP-0 manifest has grown a source.files block. It is not covered by "
        "the freshness gate — add it to LANE_VERDICTS so its hashes are checked."
    )

    shallow = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if shallow.stdout.strip() != "false":
        pytest.skip("shallow clone: the manifest's commit is not fetched here")

    commit = manifest["source"]["commit"]
    resolved = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    assert resolved.returncode == 0, (
        f"The WP-0 manifest binds commit {commit}, which this repository can no "
        "longer resolve — the certification is unverifiable. Squash-merge "
        "orphaning is the known cause; see docs/evidence-binding-audit-2026-08-03.md."
    )


def test_every_committed_verdict_is_covered() -> None:
    """The map must not be able to omit a verdict, which is how this failed.

    `LANE_VERDICTS` was hand-maintained, so a committed verdict was checked
    only if somebody remembered to add it. Twelve were not -- including all
    four `finding` verdicts, the ones carrying the most information. Nothing
    was wrong with the files; they were simply invisible to every test above.

    So coverage is now derived from the directory rather than asserted by
    memory. A new verdict is checked by default and can only escape by being
    named in `PRE_TRANSPORT_VERDICTS`, which is a deliberate act with a reason
    attached.
    """
    committed = {
        str(path.relative_to(EVIDENCE))
        for path in EVIDENCE.glob("wp*-evidence/*.json")
        if path.name.startswith(("verdict-", "verification"))
    }
    unmapped = sorted(committed - set(LANE_VERDICTS) - PRE_TRANSPORT_VERDICTS)
    assert not unmapped, (
        "These verdicts are committed but checked by nothing: "
        f"{unmapped}. Add them to LANE_VERDICTS, or to PRE_TRANSPORT_VERDICTS "
        "with a reason."
    )


def test_the_coverage_guard_is_looking_at_real_files() -> None:
    """The control. A glob that matches nothing makes the test above vacuous."""
    committed = {
        str(path.relative_to(EVIDENCE))
        for path in EVIDENCE.glob("wp*-evidence/*.json")
        if path.name.startswith(("verdict-", "verification"))
    }
    assert len(committed) >= len(LANE_VERDICTS)


#: Phrases that assert a lane's certification cannot be verified from the
#: repository. Each was true when written; what this guards is that they stayed
#: in the tree after the lane was re-certified.
UNVERIFIABLE_CLAIMS = (
    "evidence binding broken",
    "re-certification queued",
    "queued for re-certification",
    "committed no evidence manifest",
    "prose record, not a verifiable certification",
    "cannot be verified from the repository",
    "no longer independently checkable",
)

STATUS_DOC_ROOTS = ("docs", "plans")


def _certified_runs() -> dict[str, str]:
    """Lane -> run id, for every lane whose committed manifest is a clean pass.

    `passed` is absent on WP-0's manifest, which records `evidence_state`
    instead; both shapes are accepted rather than silently skipping WP-0, which
    is one of the two lanes this drift originally affected.
    """
    runs: dict[str, str] = {}
    for path in EVIDENCE.glob("wp*-evidence/*.json"):
        if not path.name.startswith(("verification", "manifest")):
            continue
        verdict = json.loads(path.read_text(encoding="utf-8"))
        source = verdict.get("source") or {}
        state = (verdict.get("capability") or {}).get("evidence_state")
        if source.get("dirty") is not False:
            continue
        if verdict.get("passed") is True or state == "pass":
            run_id = verdict.get("run_id")
            if isinstance(run_id, str) and run_id:
                runs[path.parent.name.removesuffix("-evidence")] = run_id
    return runs


def test_no_status_document_calls_a_certified_lane_unverifiable() -> None:
    """Status prose must not contradict a committed passing manifest.

    This is the seventh recurrence of status drift here and the first
    mechanical check on it. `test_domain_layer_status.py` gates the register
    against *itself*; it cannot see a document that is internally consistent
    and merely out of date with the evidence. WP-2 was exactly that:
    re-certified 2026-08-03 with a clean manifest bound to a resolvable commit,
    while two status documents went on saying its binding was broken and
    re-certification was queued.

    What this checks is narrow, and the narrowness is the point. It cannot
    verify that a status is *true*. It catches one specific repeated falsehood:
    prose calling a lane unverifiable when that lane's manifest is a committed,
    clean pass.

    The escape hatch is deliberately expensive. An earlier draft let a
    paragraph off for containing a word like "superseded" or "resolved", and a
    mutation test walked straight through it -- the stale WP-2 block quote was
    long enough to contain such a word further down, so the guard passed on the
    exact text it was written to catch. A vague marker is not evidence. So a
    paragraph may only speak of a certified lane in the past tense if it names
    **the run id that superseded the claim**, which cannot be satisfied without
    going and reading the manifest.
    """
    certified = _certified_runs()
    assert certified, "no lane has a clean manifest; this test would be vacuous"

    stale: list[str] = []
    for root in STATUS_DOC_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*.md")):
            for paragraph in path.read_text(encoding="utf-8").split("\n\n"):
                lowered = paragraph.lower()
                claim = next((c for c in UNVERIFIABLE_CLAIMS if c in lowered), None)
                if claim is None:
                    continue
                # A paragraph naming several lanes is reported once, against all
                # of them: which lane such a sentence is *about* is a question
                # only prose can answer, and guessing would trade this test's
                # precision for a plausible-looking attribution.
                unresolved = sorted(
                    f"{lane} ({run_id})"
                    for lane, run_id in certified.items()
                    if lane in lowered and run_id.lower() not in lowered
                )
                if unresolved:
                    stale.append(
                        f"{path.relative_to(REPO_ROOT)}: {claim!r}, "
                        f"but these are certified: {', '.join(unresolved)}"
                    )

    assert not stale, (
        "These paragraphs call a lane unverifiable when its committed manifest "
        "is a clean pass:\n  " + "\n  ".join(stale) + "\nReconcile the prose "
        "with the evidence, or cite the superseding run id in the same "
        "paragraph to mark the claim as history."
    )
