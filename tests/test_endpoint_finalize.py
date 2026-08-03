"""The endpoint finalizer's refusals.

An absent scheduled task is the *expected* result for several rows of the
endpoint candidate, which makes this lane unusually easy to fool: anything that
quietly stops tasks being created looks identical to the defect the lane is
hunting. These tests pin the distinctions that keep that from happening --
lane failure, inconclusive control, and finding are three different outcomes and
must never collapse into one another.
"""

from __future__ import annotations

import runpy
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast


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
