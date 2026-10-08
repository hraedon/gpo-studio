"""The endpoint finalizer's refusals.

An absent scheduled task is the *expected* result for several rows of the
endpoint candidate, which makes this lane unusually easy to fool: anything that
quietly stops tasks being created looks identical to the defect the lane is
hunting. These tests pin the distinctions that keep that from happening --
lane failure, inconclusive control, and finding are three different outcomes and
must never collapse into one another.
"""

from __future__ import annotations

import json
import runpy
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

_ROOT = Path(__file__).parents[1]
_TARGET_GPO = "Endpoint-20260905-191402-7686"


def _symbols() -> dict[str, Any]:
    script = (
        Path(__file__).parents[1]
        / "scripts"
        / "windows-oracle"
        / "finalize_endpoint_run.py"
    )
    return runpy.run_path(str(script))


def _row(name: str, present: bool, expected: str = "present") -> dict[str, Any]:
    return {
        "name": name,
        "isolates": "test",
        "expected_if_defects_real": expected,
        "present": present,
        "state": "Ready" if present else None,
        "actions": [],
    }


def _expected() -> dict[str, Any]:
    return {
        "endpoint_role": "client",
        "match_os_version": "WINTHRESHOLD",
        "non_match_os_version": "WINTHRESHOLDSRV",
        "vocabulary_control_task": "GPOStudio-EP2-J-native-os-match",
    }


def _clean_author() -> dict[str, Any]:
    return {
        "run_id": "endpoint-author-1",
        "target_gpo": _TARGET_GPO,
        "setup_completed": True,
        "error": None,
        "cleanup": {
            "computer_restored": True,
            "link_removed": True,
            "gpo_removed": True,
            "ou_removed": True,
            "errors": [],
        },
    }


def _clean_observe(**overrides: Any) -> dict[str, Any]:
    observe: dict[str, Any] = {
        "run_id": "endpoint-observe-1",
        "target_gpo": _TARGET_GPO,
        "gpo_applied": True,
        "observation_settled": True,
        "cse_completed": True,
        "error": None,
        "cleanup": {"tasks_removed": True, "residual_tasks": [], "errors": []},
        "environment": {"build": "26200.1234", "locale": "en-US"},
        "observed_tasks": [],
    }
    observe.update(overrides)
    return observe


def _clean_verify(**overrides: Any) -> dict[str, Any]:
    verify: dict[str, Any] = {
        "computer": "client",
        "target_gpo": _TARGET_GPO,
        "gpupdate_exit_code": 0,
        "gpo_still_applied": False,
        "tasks_removed": True,
        "residual_tasks": [],
        "errors": [],
    }
    verify.update(overrides)
    return verify


def test_missing_post_teardown_verification_is_a_lane_failure() -> None:
    """The observation half's cleanup claim is provisional and must not stand alone.

    It unregisters tasks while the GPO is still linked -- the authoring half
    unlinks only afterwards. Any refresh in that window recreates every GPP
    ``Replace`` item, so an absence observed there says nothing durable. This is
    the bug the two-guest split introduced and the verify phase exists to close.
    """
    symbols = _symbols()
    lane_validity = cast(Callable[..., list[str]], symbols["_lane_validity"])

    problems = lane_validity(_clean_author(), _clean_observe(), None, True, False)

    assert any("provisional" in problem for problem in problems)


def test_tasks_surviving_teardown_is_a_lane_failure() -> None:
    symbols = _symbols()
    lane_validity = cast(Callable[..., list[str]], symbols["_lane_validity"])
    verify = _clean_verify(tasks_removed=False, residual_tasks=["GPOStudio-EP2-A-nofilter"])

    problems = lane_validity(_clean_author(), _clean_observe(), verify, True, False)

    assert any("survived teardown" in problem for problem in problems)


def test_gpo_still_applied_after_teardown_is_a_lane_failure() -> None:
    symbols = _symbols()
    lane_validity = cast(Callable[..., list[str]], symbols["_lane_validity"])

    problems = lane_validity(
        _clean_author(), _clean_observe(), _clean_verify(gpo_still_applied=True), True, False
    )

    assert any("still applied" in problem for problem in problems)


def test_unsettled_observation_is_a_lane_failure_not_a_negative_result() -> None:
    """The distinction the whole settle loop exists to preserve.

    A run whose CSE was never seen completing has not shown that an absent task
    is absent; it has shown nothing. Reporting that as a finding would let a
    slow endpoint manufacture a defect.
    """
    symbols = _symbols()
    lane_validity = cast(Callable[..., list[str]], symbols["_lane_validity"])

    problems = lane_validity(
        _clean_author(),
        _clean_observe(observation_settled=False, cse_completed=False),
        _clean_verify(),
        True,
        False,
    )

    assert any("did not settle" in problem for problem in problems)


def test_gpo_that_never_arrived_is_a_lane_failure() -> None:
    symbols = _symbols()
    lane_validity = cast(Callable[..., list[str]], symbols["_lane_validity"])

    problems = lane_validity(
        _clean_author(), _clean_observe(gpo_applied=False), _clean_verify(), True, False
    )

    assert any("never reported the GPO applied" in problem for problem in problems)


def test_displaced_computer_account_is_a_lane_failure() -> None:
    """Cleanup is not tidiness here: the lane moves a real computer account."""
    symbols = _symbols()
    lane_validity = cast(Callable[..., list[str]], symbols["_lane_validity"])
    author = _clean_author()
    author["cleanup"]["computer_restored"] = False

    problems = lane_validity(author, _clean_observe(), _clean_verify(), True, False)

    assert any("computer_restored" in problem for problem in problems)


def test_clean_run_has_no_lane_problems() -> None:
    symbols = _symbols()
    lane_validity = cast(Callable[..., list[str]], symbols["_lane_validity"])

    assert lane_validity(_clean_author(), _clean_observe(), _clean_verify(), True, False) == []


def test_client_build_sentinel_is_refused() -> None:
    """Environment-spec rule 6, enforced where the spec says it belongs.

    The manifest parser accepts ``not-tested`` because it cannot tell which lane
    produced a manifest. This lane applies policy to a client, so it may not.
    """
    symbols = _symbols()
    client_problems = cast(
        Callable[..., list[str]], symbols["_client_environment_problems"]
    )

    sentinel = {"build": "not-tested", "locale": "en-US"}
    problems = client_problems(_clean_observe(environment=sentinel))

    assert any("not-tested sentinel" in problem for problem in problems)


def test_server_build_masquerading_as_the_endpoint_is_refused() -> None:
    """Running the observation half on the member server would pass every other
    check while producing evidence about the wrong OS."""
    symbols = _symbols()
    client_problems = cast(
        Callable[..., list[str]], symbols["_client_environment_problems"]
    )

    server = {"build": "26100.5000", "locale": "en-US"}
    problems = client_problems(_clean_observe(environment=server))

    assert any("26100" in problem for problem in problems)


def test_frozen_client_family_is_accepted() -> None:
    symbols = _symbols()
    client_problems = cast(
        Callable[..., list[str]], symbols["_client_environment_problems"]
    )

    assert client_problems(_clean_observe()) == []


def test_absent_unfiltered_control_makes_everything_uninterpretable() -> None:
    symbols = _symbols()
    control_problems = cast(Callable[..., list[str]], symbols["_control_problems"])
    rows = {
        "GPOStudio-EP2-A-nofilter": _row("GPOStudio-EP2-A-nofilter", False),
        "GPOStudio-EP2-J-native-os-match": _row("GPOStudio-EP2-J-native-os-match", True),
    }

    problems = control_problems(rows, _expected())

    assert any("do not reach this endpoint at all" in problem for problem in problems)


def test_wrong_product_code_is_inconclusive_rather_than_a_studio_defect() -> None:
    """The reason the native matching row exists.

    ``WINTHRESHOLD`` covering Windows 11 is an *inference* from a dropdown that
    offered no Windows 11 entry. If it is wrong, Studio's matching filter is
    absent for a reason that has nothing to do with Studio -- and the native
    control is absent alongside it, which is what makes the difference visible.
    """
    symbols = _symbols()
    control_problems = cast(Callable[..., list[str]], symbols["_control_problems"])
    rows = {
        "GPOStudio-EP2-A-nofilter": _row("GPOStudio-EP2-A-nofilter", True),
        "GPOStudio-EP2-J-native-os-match": _row("GPOStudio-EP2-J-native-os-match", False),
    }

    problems = control_problems(rows, _expected())

    assert any("product code is wrong for this OS" in problem for problem in problems)


def test_native_excluding_filter_that_applies_invalidates_every_filter_row() -> None:
    symbols = _symbols()
    control_problems = cast(Callable[..., list[str]], symbols["_control_problems"])
    rows = {
        "GPOStudio-EP2-A-nofilter": _row("GPOStudio-EP2-A-nofilter", True),
        "GPOStudio-EP2-J-native-os-match": _row("GPOStudio-EP2-J-native-os-match", True),
        "GPOStudio-EP2-E-native-control": _row("GPOStudio-EP2-E-native-control", True, "absent"),
    }

    problems = control_problems(rows, _expected())

    assert any("filter evaluation itself is not working" in problem for problem in problems)


def test_controls_holding_yields_no_control_problems() -> None:
    symbols = _symbols()
    control_problems = cast(Callable[..., list[str]], symbols["_control_problems"])
    rows = {
        "GPOStudio-EP2-A-nofilter": _row("GPOStudio-EP2-A-nofilter", True),
        "GPOStudio-EP2-J-native-os-match": _row("GPOStudio-EP2-J-native-os-match", True),
        "GPOStudio-EP2-E-native-control": _row("GPOStudio-EP2-E-native-control", False, "absent"),
    }

    assert control_problems(rows, _expected()) == []


def test_filter_evaluated_requires_both_polarities() -> None:
    """Absent-in-both-polarities is the fails-closed signature, not a pass.

    Phase 1's excluding-only design could not tell "the CSE honoured the filter"
    from "the CSE could not parse it and failed closed". Both answers produce an
    absent task; only the matching row separates them.
    """
    symbols = _symbols()
    findings = cast(Callable[..., list[dict[str, Any]]], symbols["_findings"])
    rows = {
        "GPOStudio-EP2-B-os-match": _row("GPOStudio-EP2-B-os-match", False),
        "GPOStudio-EP2-C-os-exclude": _row("GPOStudio-EP2-C-os-exclude", False, "absent"),
        "GPOStudio-EP2-D-os-negated": _row("GPOStudio-EP2-D-os-negated", False),
    }

    wi021 = next(f for f in findings(rows, _expected()) if f["id"] == "WI-021")

    assert wi021["answer"] == "fails-closed"


def test_filter_ignored_when_both_polarities_apply() -> None:
    symbols = _symbols()
    findings = cast(Callable[..., list[dict[str, Any]]], symbols["_findings"])
    rows = {
        "GPOStudio-EP2-B-os-match": _row("GPOStudio-EP2-B-os-match", True),
        "GPOStudio-EP2-C-os-exclude": _row("GPOStudio-EP2-C-os-exclude", True, "absent"),
        "GPOStudio-EP2-D-os-negated": _row("GPOStudio-EP2-D-os-negated", True),
    }

    wi021 = next(f for f in findings(rows, _expected()) if f["id"] == "WI-021")

    assert wi021["answer"] == "ignored"


def test_filter_evaluated_when_the_split_is_clean() -> None:
    symbols = _symbols()
    findings = cast(Callable[..., list[dict[str, Any]]], symbols["_findings"])
    rows = {
        "GPOStudio-EP2-B-os-match": _row("GPOStudio-EP2-B-os-match", True),
        "GPOStudio-EP2-C-os-exclude": _row("GPOStudio-EP2-C-os-exclude", False, "absent"),
        "GPOStudio-EP2-D-os-negated": _row("GPOStudio-EP2-D-os-negated", True),
    }

    wi021 = next(f for f in findings(rows, _expected()) if f["id"] == "WI-021")

    assert wi021["answer"] == "evaluated"


def test_os_vocabulary_needs_the_server_code_to_miss() -> None:
    """A client code that matches proves nothing on its own.

    If the server code matched a client too, the product code would not be
    discriminating between them at all -- and the corpus matrix's claim that
    WINTHRESHOLD is the *client* value would be unsupported by this run.
    """
    symbols = _symbols()
    findings = cast(Callable[..., list[dict[str, Any]]], symbols["_findings"])
    rows = {
        "GPOStudio-EP2-J-native-os-match": _row("GPOStudio-EP2-J-native-os-match", True),
        "GPOStudio-EP2-K-os-server-code": _row("GPOStudio-EP2-K-os-server-code", True, "absent"),
    }

    vocabulary = next(f for f in findings(rows, _expected()) if f["id"] == "OS-VOCABULARY")

    assert vocabulary["answer"] == "product-code-not-discriminating"


def test_os_vocabulary_confirmed_by_a_clean_split() -> None:
    symbols = _symbols()
    findings = cast(Callable[..., list[dict[str, Any]]], symbols["_findings"])
    rows = {
        "GPOStudio-EP2-J-native-os-match": _row("GPOStudio-EP2-J-native-os-match", True),
        "GPOStudio-EP2-K-os-server-code": _row("GPOStudio-EP2-K-os-server-code", False, "absent"),
    }

    vocabulary = next(f for f in findings(rows, _expected()) if f["id"] == "OS-VOCABULARY")

    assert vocabulary["answer"] == "confirmed"


# ---------------------------------------------------------------------------
# Missing evidence is never a negative (review finding 7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["gpo_still_applied", "tasks_removed", "residual_tasks", "errors", "target_gpo",
     "gpupdate_exit_code"],
)
def test_a_verify_record_missing_any_field_is_a_lane_failure(field: str) -> None:
    """A truncated teardown record must not read as a clean one.

    ``verify.get("gpo_still_applied")`` used to treat a missing field exactly
    like an observed ``false`` -- the protection against tasks being recreated
    after the provisional cleanup, switched off by an absence.
    """
    symbols = _symbols()
    lane_validity = cast(Callable[..., list[str]], symbols["_lane_validity"])
    verify = _clean_verify()
    del verify[field]

    assert lane_validity(_clean_author(), _clean_observe(), verify, True, False) != []


@pytest.mark.parametrize("value", [None, "false", 0])
def test_a_non_bool_gpo_still_applied_is_a_lane_failure(value: object) -> None:
    symbols = _symbols()
    lane_validity = cast(Callable[..., list[str]], symbols["_lane_validity"])
    verify = _clean_verify(gpo_still_applied=value)

    problems = lane_validity(_clean_author(), _clean_observe(), verify, True, False)

    assert any("did not record gpo_still_applied" in problem for problem in problems)


@pytest.mark.parametrize("target", ["", None, "Some-Other-GPO"])
def test_a_verify_phase_that_checked_another_gpo_is_a_lane_failure(target: object) -> None:
    """The guest only measures still-applied when told which GPO to look for."""
    symbols = _symbols()
    lane_validity = cast(Callable[..., list[str]], symbols["_lane_validity"])
    verify = _clean_verify(target_gpo=target)

    problems = lane_validity(_clean_author(), _clean_observe(), verify, True, False)

    assert any("its still-applied answer is about nothing" in p for p in problems)


def test_a_failed_post_teardown_refresh_is_a_lane_failure() -> None:
    symbols = _symbols()
    lane_validity = cast(Callable[..., list[str]], symbols["_lane_validity"])
    verify = _clean_verify(gpupdate_exit_code=1)

    problems = lane_validity(_clean_author(), _clean_observe(), verify, True, False)

    assert any("refresh did not succeed" in problem for problem in problems)


@pytest.mark.parametrize(
    "field", ["computer_restored", "gpo_removed", "ou_removed", "errors"]
)
def test_an_author_cleanup_missing_any_field_is_a_lane_failure(field: str) -> None:
    symbols = _symbols()
    lane_validity = cast(Callable[..., list[str]], symbols["_lane_validity"])
    author = _clean_author()
    del author["cleanup"][field]

    assert lane_validity(author, _clean_observe(), _clean_verify(), True, False) != []


@pytest.mark.parametrize("field", ["setup_completed", "error", "target_gpo"])
def test_an_author_record_missing_any_field_is_a_lane_failure(field: str) -> None:
    symbols = _symbols()
    lane_validity = cast(Callable[..., list[str]], symbols["_lane_validity"])
    author = _clean_author()
    del author[field]

    assert lane_validity(author, _clean_observe(), _clean_verify(), True, False) != []


def test_an_author_error_is_a_lane_failure() -> None:
    symbols = _symbols()
    lane_validity = cast(Callable[..., list[str]], symbols["_lane_validity"])
    author = _clean_author()
    author["error"] = "link failed"

    problems = lane_validity(author, _clean_observe(), _clean_verify(), True, False)

    assert any("authoring half reported: link failed" in problem for problem in problems)


@pytest.mark.parametrize(
    "field", ["gpo_applied", "observation_settled", "error", "cleanup", "target_gpo"]
)
def test_an_observe_record_missing_any_field_is_a_lane_failure(field: str) -> None:
    symbols = _symbols()
    lane_validity = cast(Callable[..., list[str]], symbols["_lane_validity"])
    observe = _clean_observe()
    del observe[field]

    assert lane_validity(_clean_author(), observe, _clean_verify(), True, False) != []


@pytest.mark.parametrize("field", ["tasks_removed", "errors"])
def test_an_observe_cleanup_missing_any_field_is_a_lane_failure(field: str) -> None:
    symbols = _symbols()
    lane_validity = cast(Callable[..., list[str]], symbols["_lane_validity"])
    observe = _clean_observe()
    del observe["cleanup"][field]

    assert lane_validity(_clean_author(), observe, _clean_verify(), True, False) != []


def test_observation_cleanup_errors_are_lane_failures() -> None:
    """These were recorded by the guest and read by nothing."""
    symbols = _symbols()
    lane_validity = cast(Callable[..., list[str]], symbols["_lane_validity"])
    observe = _clean_observe()
    observe["cleanup"]["errors"] = ["remove-tasks: access denied"]

    problems = lane_validity(_clean_author(), observe, _clean_verify(), True, False)

    assert any("access denied" in problem for problem in problems)


def test_an_unobserved_native_excluding_control_is_a_control_problem() -> None:
    symbols = _symbols()
    control_problems = cast(Callable[..., list[str]], symbols["_control_problems"])
    rows = {
        "GPOStudio-EP2-A-nofilter": _row("GPOStudio-EP2-A-nofilter", True),
        "GPOStudio-EP2-J-native-os-match": _row("GPOStudio-EP2-J-native-os-match", True),
    }

    problems = control_problems(rows, _expected())

    assert any("GPOStudio-EP2-E-native-control was not observed" in p for p in problems)


# ---------------------------------------------------------------------------
# Every candidate row must be observed and answered (review finding 6),
# graded against a REAL generated candidate through the finalizer's own
# `_grade`, which is what `main` records.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_candidate(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    out = tmp_path_factory.mktemp("endpoint-candidate") / "candidate"
    subprocess.run(
        [sys.executable, str(_ROOT / "scripts/plan-033/build-endpoint-candidate.py"), str(out)],
        check=True,
        capture_output=True,
    )
    return cast(dict[str, Any], json.loads((out / "expected.json").read_text()))


def _all_rows(expected: dict[str, Any]) -> list[dict[str, Any]]:
    """Every candidate row, observed as the defects-real expectation says."""
    return [
        _row_from_task(task, task["expected_if_defects_real"] == "present")
        for task in expected["tasks"]
    ]


def _row_from_task(task: dict[str, Any], present: bool) -> dict[str, Any]:
    return {
        "name": task["name"],
        "isolates": task["isolates"],
        "expected_if_defects_real": task["expected_if_defects_real"],
        "present": present,
        "state": "Ready" if present else None,
        "actions": [],
    }


def _grade(
    expected: dict[str, Any],
    rows: list[dict[str, Any]],
    verify: dict[str, Any] | None = None,
) -> tuple[str, list[str], list[str], list[dict[str, Any]]]:
    grade = cast(Callable[..., tuple[str, list[str], list[str], list[dict[str, Any]]]],
                 _symbols()["_grade"])
    return grade(
        _clean_author(),
        _clean_observe(observed_tasks=rows),
        _clean_verify() if verify is None else verify,
        expected,
        True,
        False,
    )


def test_the_full_real_candidate_row_set_passes(real_candidate: dict[str, Any]) -> None:
    """The control for every mutation below."""
    state, lanes, controls, findings = _grade(real_candidate, _all_rows(real_candidate))

    assert (state, lanes, controls) == ("pass", [], [])
    assert all(finding["answer"] is not None for finding in findings)


def test_dropping_every_non_control_row_is_a_lane_failure(
    real_candidate: dict[str, Any],
) -> None:
    """The review's reproduction: two control rows used to make a taggable pass.

    With only the unfiltered and vocabulary controls observed, WI-018, WI-021
    and OS-VOCABULARY were all recorded as ``None`` under ``passed: true``.
    """
    keep = {"GPOStudio-EP2-A-nofilter", real_candidate["vocabulary_control_task"]}
    rows = [row for row in _all_rows(real_candidate) if row["name"] in keep]

    state, lanes, _, _ = _grade(real_candidate, rows)

    assert state == "lane-failure"
    unobserved = {t["name"] for t in real_candidate["tasks"]} - keep
    for name in unobserved:
        assert any(name in problem and "never observed" in problem for problem in lanes), name


def test_dropping_any_single_row_is_a_lane_failure(real_candidate: dict[str, Any]) -> None:
    for task in real_candidate["tasks"]:
        rows = [row for row in _all_rows(real_candidate) if row["name"] != task["name"]]
        state, lanes, _, _ = _grade(real_candidate, rows)
        assert state == "lane-failure", task["name"]
        assert any(task["name"] in problem for problem in lanes), task["name"]


def test_an_unanswered_row_is_a_lane_failure(real_candidate: dict[str, Any]) -> None:
    rows = _all_rows(real_candidate)
    del rows[-1]["present"]

    state, lanes, _, _ = _grade(real_candidate, rows)

    assert state == "lane-failure"
    assert any("has no present/absent answer" in problem for problem in lanes)


def test_a_duplicated_or_foreign_row_is_a_lane_failure(real_candidate: dict[str, Any]) -> None:
    rows = _all_rows(real_candidate)
    duplicated = rows + [dict(rows[0])]
    assert _grade(real_candidate, duplicated)[0] == "lane-failure"

    foreign = rows + [dict(rows[0], name="GPOStudio-EP2-Z-unknown")]
    assert _grade(real_candidate, foreign)[0] == "lane-failure"


def test_a_row_relabelled_against_the_candidate_is_a_lane_failure(
    real_candidate: dict[str, Any],
) -> None:
    """The guest echoes the candidate's labels; a row that disagrees is not this experiment."""
    rows = _all_rows(real_candidate)
    rows[1]["expected_if_defects_real"] = "absent"

    state, lanes, _, _ = _grade(real_candidate, rows)

    assert state == "lane-failure"
    assert any("expected_if_defects_real" in problem for problem in lanes)


def test_a_missing_teardown_field_fails_the_real_candidate_grade(
    real_candidate: dict[str, Any],
) -> None:
    """Finding 7 end to end: the review deleted gpo_still_applied and got a pass."""
    verify = _clean_verify()
    del verify["gpo_still_applied"]

    state, lanes, _, _ = _grade(real_candidate, _all_rows(real_candidate), verify)

    assert state == "lane-failure"
    assert any("gpo_still_applied" in problem for problem in lanes)


def test_a_candidate_with_no_tasks_is_a_lane_failure(real_candidate: dict[str, Any]) -> None:
    empty = dict(real_candidate, tasks=[])

    assert _grade(empty, [])[0] == "lane-failure"


def test_the_banked_endpoint_evidence_still_grades_pass() -> None:
    """The last real run, regraded by today's finalizer: tightened, not broken.

    Every field this file now requires was in the record Windows actually
    wrote on 2026-09-05, so a run of the same harness can still pass.
    """
    pack = _ROOT / "docs/plan-033/wp6-evidence/wi062-20260910/endpoint"

    def load(relative: str) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads((pack / relative).read_text(encoding="utf-8-sig")))

    grade = cast(Callable[..., tuple[str, list[str], list[str], list[dict[str, Any]]]],
                 _symbols()["_grade"])
    state, lanes, controls, findings = grade(
        load("author/author-result.json"),
        load("observe/observe-result.json"),
        load("verify/verify-result.json"),
        load("controller-candidate/expected.json"),
        True,
        False,
    )

    assert (state, lanes, controls) == ("pass", [], [])
    assert [f["answer"] for f in findings] == ["honoured", "evaluated", "confirmed"]


def test_deployed_file_set_matches_what_the_lane_pushes() -> None:
    """``harness_matches_source`` is meaningless if this set drifts."""
    symbols = _symbols()
    deployed = cast(dict[str, str], symbols["DEPLOYED_FILES"])
    local = cast(dict[str, str], symbols["LOCAL_FILES"])
    repo_root = Path(__file__).parents[1]
    driver = (repo_root / "scripts" / "windows-oracle" / "run-endpoint-oracle.sh").read_text()

    for name in deployed:
        assert f"-LocalPath \"$SCRIPT_DIR/{name}\"" in driver, f"{name} is never pushed"
    for source in {**deployed, **local}.values():
        assert (repo_root / source).is_file(), f"{source} does not exist"
