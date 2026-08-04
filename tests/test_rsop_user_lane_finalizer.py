"""The WP-9 finalizer's outcomes must stay distinguishable -- especially loopback.

The user-scope lane inherits the computer lane's three-way split (lane failure /
inconclusive / verdict) and adds a fourth way to be wrong that is specific to
loopback, and worse than the others because it is invisible:

**Under ``replace``, the expected observation is that a whole GPO's values are
ABSENT.** That is also exactly what a run where loopback never engaged looks
like. Windows states the mode it used in event 5311, and the tests below pin
that the finalizer treats a mode mismatch as *inconclusive* -- never as a pass
it did not earn, and never as a finding it invented.

The fixtures are synthetic on purpose: a live run exercises one path, and the
paths that matter most here are the ones a healthy estate never takes.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MODULE_PATH = _REPO_ROOT / "scripts" / "windows-oracle" / "finalize_rsop_user_run.py"

_spec = importlib.util.spec_from_file_location("finalize_rsop_user_run", _MODULE_PATH)
assert _spec and _spec.loader
finalize_user = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(finalize_user)


def _author_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "run_id": "rsop-author-20260804000000-1111",
        "setup_completed": True,
        "computer_moved": True,
        "user_moved": True,
        "scope": "user",
        "environment": {"build": "26100", "locale": "en-US"},
        "gpos": [
            {"symbolic_name": "Studio-RSOP-Loopback", "name": "Studio-RSOP-Loopback-S"},
            {"symbolic_name": "Studio-RSOP-CompLocation", "name": "Studio-RSOP-CompLocation-S"},
            {"symbolic_name": "Studio-RSOP-UserLocation", "name": "Studio-RSOP-UserLocation-S"},
            {"symbolic_name": "Studio-RSOP-UserControl", "name": "Studio-RSOP-UserControl-S"},
        ],
    }
    state.update(overrides)
    return state


def _cleanup_result(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "run_id": "rsop-author-20260804000000-1111",
        "cleanup_problems": [],
        "residual": {
            "computer_restored": True,
            "user_restored": True,
            "surviving_links": [],
            "surviving_gpos": [],
            "surviving_ous": [],
        },
    }
    result.update(overrides)
    return result


def _observation(**overrides: Any) -> dict[str, Any]:
    observation: dict[str, Any] = {
        "run_id": "rsop-user-observe-20260804000000-2222",
        "scope": "user",
        "principal": "labauto1",
        "principal_sid": "S-1-5-21-1-2-3-1106",
        "session_present": True,
        "observation_settled": True,
        "settle_attempts": 1,
        "user_policy_completed": True,
        "rsop_captured": True,
        "pre_run_residual": [],
        "control_present": True,
        "intended_loopback_mode": "merge",
        "observed_loopback_mode": "merge",
        "loopback_control_ok": True,
        "applied_gpos": [
            "Studio-RSOP-CompLocation-S",
            "Studio-RSOP-UserControl-S",
            "Studio-RSOP-UserLocation-S",
        ],
        "denied_gpos": [],
        "observed_values": [
            {"value_name": "CompOnly", "value": "1"},
            {"value_name": "Control", "value": "present"},
            {"value_name": "Loop", "value": "computerLocation"},
            {"value_name": "UserOnly", "value": "1"},
        ],
        "lane_problems": [],
        "environment": {"build": "26200", "locale": "en-US"},
        "error": None,
    }
    observation.update(overrides)
    return observation


def _prediction(**overrides: Any) -> dict[str, Any]:
    prediction: dict[str, Any] = {
        "query_id": "wp6b-loopback-merge",
        "scope": "user",
        "loopback_mode": "merge",
        "applied_gpos": [
            "Studio-RSOP-CompLocation",
            "Studio-RSOP-Loopback",
            "Studio-RSOP-UserControl",
            "Studio-RSOP-UserLocation",
        ],
        "denied_gpos": [],
        "winners": [
            {"value_name": "CompOnly", "value": "1", "winning_gpo": "Studio-RSOP-CompLocation"},
            {"value_name": "Control", "value": "present", "winning_gpo": "Studio-RSOP-UserControl"},
            {
                "value_name": "Loop",
                "value": "computerLocation",
                "winning_gpo": "Studio-RSOP-CompLocation",
            },
            {"value_name": "UserOnly", "value": "1", "winning_gpo": "Studio-RSOP-UserLocation"},
        ],
    }
    prediction.update(overrides)
    return prediction


_TOPOLOGY = '{"scope": "user", "gpos": []}'


def _expected(**overrides: Any) -> dict[str, Any]:
    expected: dict[str, Any] = {
        "scenario_id": "loopback-merge",
        "scope": "user",
        "loopback_mode": "merge",
        "control_gpo": "Studio-RSOP-UserControl",
        "control_value_name": "Control",
        "endpoint_user": "labauto1",
    }
    expected.update(overrides)
    return expected


@pytest.fixture
def lane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A runnable lane whose harness binding and git state are forced clean.

    Both are stubbed to the *passing* value, so a test expecting lane-failure
    has to earn it from the fixture it actually varies.
    """
    run_dir = tmp_path / "run"
    (run_dir / "author").mkdir(parents=True)
    (run_dir / "observe").mkdir(parents=True)
    candidate = tmp_path / "candidate"
    candidate.mkdir()

    monkeypatch.setattr(finalize_user, "DEPLOYED_FILES", {})
    monkeypatch.setattr(finalize_user, "LOCAL_FILES", {})

    def fake_run(args: list[str], **kwargs: Any):
        if args[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(args, 0, stdout="abc1234\n", stderr="")
        if args[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess call: {args}")

    monkeypatch.setattr(finalize_user.subprocess, "run", fake_run)
    monkeypatch.setattr(finalize_user, "tag_evidence_commit", lambda *a, **k: None)

    def write(
        *,
        author: dict[str, Any] | None = None,
        cleanup: dict[str, Any] | None = None,
        observation: dict[str, Any] | None = None,
        prediction: dict[str, Any] | None = None,
        expected: dict[str, Any] | None = None,
        guest_topology: str | None = _TOPOLOGY,
    ) -> tuple[Path, Path]:
        (candidate / "topology.json").write_text(_TOPOLOGY, encoding="utf-8")
        if guest_topology is not None:
            (run_dir / "author" / "topology.json").write_text(guest_topology, encoding="utf-8")
        (run_dir / "author" / "author-state.json").write_text(
            json.dumps(author or _author_state()), encoding="utf-8"
        )
        (run_dir / "author" / "cleanup-result.json").write_text(
            json.dumps(cleanup if cleanup is not None else _cleanup_result()), encoding="utf-8"
        )
        (run_dir / "observe" / "observation.json").write_text(
            json.dumps(observation or _observation()), encoding="utf-8"
        )
        (candidate / "prediction.json").write_text(
            json.dumps(prediction or _prediction()), encoding="utf-8"
        )
        (candidate / "expected.json").write_text(
            json.dumps(expected or _expected()), encoding="utf-8"
        )
        return run_dir, candidate

    return write


def _finalize(run_dir: Path, candidate: Path) -> dict[str, Any]:
    finalize_user.main([str(run_dir), "--candidate-root", str(candidate), "--no-tag"])
    return json.loads((run_dir / "rsop-user-verdict.json").read_text(encoding="utf-8"))


def test_agreeing_merge_run_passes(lane) -> None:
    """The control: loopback merge engaged and the prediction matched."""
    verdict = _finalize(*lane())
    assert verdict["state"] == "pass"
    assert verdict["passed"] is True
    assert verdict["work_package"] == "WP-9"
    assert verdict["scope"] == "user"
    assert verdict["loopback"] == {"intended": "merge", "observed": "merge", "control_ok": True}


def test_loopback_mode_mismatch_is_inconclusive_not_a_pass(lane) -> None:
    """The lane's whole reason for existing, in its most dangerous form.

    A replace scenario whose values all match while Windows says loopback was
    DISABLED has not verified replace. Passing it would certify the model's
    loopback behaviour on the strength of a run in which loopback never ran.
    """
    verdict = _finalize(
        *lane(
            expected=_expected(scenario_id="loopback-replace", loopback_mode="replace"),
            prediction=_prediction(
                loopback_mode="replace",
                winners=[
                    {
                        "value_name": "CompOnly",
                        "value": "1",
                        "winning_gpo": "Studio-RSOP-CompLocation",
                    },
                    {
                        "value_name": "Control",
                        "value": "present",
                        "winning_gpo": "Studio-RSOP-UserControl",
                    },
                    {
                        "value_name": "Loop",
                        "value": "computerLocation",
                        "winning_gpo": "Studio-RSOP-CompLocation",
                    },
                ],
            ),
            observation=_observation(
                intended_loopback_mode="replace",
                observed_loopback_mode="disabled",
                loopback_control_ok=False,
                # The values a replace run is supposed to produce, exactly.
                observed_values=[
                    {"value_name": "CompOnly", "value": "1"},
                    {"value_name": "Control", "value": "present"},
                    {"value_name": "Loop", "value": "computerLocation"},
                ],
            ),
        )
    )
    assert verdict["state"] == "inconclusive"
    assert verdict["passed"] is False
    # No comparison at all: the run cannot speak about the model either way.
    assert verdict["comparison"] is None
    assert any("loopback" in problem for problem in verdict["control_problems"])


def test_missing_loopback_event_is_inconclusive(lane) -> None:
    """No 5311 at all means the mode Windows used is unknown, not assumed."""
    verdict = _finalize(
        *lane(observation=_observation(observed_loopback_mode=None, loopback_control_ok=False))
    )
    assert verdict["state"] == "inconclusive"
    assert any("5311" in problem for problem in verdict["control_problems"])


def test_absent_control_is_inconclusive_not_a_finding(lane) -> None:
    """User policy never reached the principal; nothing here is about the model."""
    verdict = _finalize(
        *lane(
            observation=_observation(
                control_present=False,
                observed_values=[{"value_name": "Loop", "value": "userLocation"}],
            )
        )
    )
    assert verdict["state"] == "inconclusive"
    assert verdict["comparison"] is None


def test_wrong_winner_is_a_finding_not_a_lane_failure(lane) -> None:
    """A wrong prediction is the point of the lane, and must survive as a result.

    Merge that resolved to the user-location value would mean the model has
    merge's precedence backwards -- exactly the class of defect WP-9 exists to
    find, and exactly the one that must not be retried until it agrees.
    """
    verdict = _finalize(
        *lane(
            observation=_observation(
                observed_values=[
                    {"value_name": "CompOnly", "value": "1"},
                    {"value_name": "Control", "value": "present"},
                    {"value_name": "Loop", "value": "userLocation"},
                    {"value_name": "UserOnly", "value": "1"},
                ]
            )
        )
    )
    assert verdict["state"] == "finding"
    findings = verdict["comparison"]["value_findings"]
    assert [f["value_name"] for f in findings] == ["Loop"]
    assert findings[0]["predicted"] == "computerLocation"
    assert findings[0]["observed"] == "userLocation"
    assert findings[0]["kind"] == "wrong_value"


def test_value_present_under_replace_is_a_finding(lane) -> None:
    """Replace must discard the user-location GPO entirely.

    With the loopback control satisfied, a surviving ``UserOnly`` is a real
    disagreement rather than a run that did not happen.
    """
    verdict = _finalize(
        *lane(
            expected=_expected(scenario_id="loopback-replace", loopback_mode="replace"),
            prediction=_prediction(
                loopback_mode="replace",
                winners=[
                    {
                        "value_name": "Control",
                        "value": "present",
                        "winning_gpo": "Studio-RSOP-UserControl",
                    },
                    {
                        "value_name": "Loop",
                        "value": "computerLocation",
                        "winning_gpo": "Studio-RSOP-CompLocation",
                    },
                ],
            ),
            observation=_observation(
                intended_loopback_mode="replace",
                observed_loopback_mode="replace",
                observed_values=[
                    {"value_name": "Control", "value": "present"},
                    {"value_name": "Loop", "value": "computerLocation"},
                    {"value_name": "UserOnly", "value": "1"},
                ],
            ),
        )
    )
    assert verdict["state"] == "finding"
    finding = verdict["comparison"]["value_findings"][0]
    assert finding["value_name"] == "UserOnly"
    assert finding["kind"] == "observed_but_unpredicted"


def test_no_interactive_session_is_a_lane_failure(lane) -> None:
    """The precondition that would otherwise manufacture a sweep of findings."""
    verdict = _finalize(*lane(observation=_observation(session_present=False)))
    assert verdict["state"] == "lane-failure"
    assert verdict["comparison"] is None
    assert any("interactive session" in problem for problem in verdict["lane_problems"])


def test_unmoved_user_is_a_lane_failure(lane) -> None:
    """A run that never moved the principal resolved a different experiment."""
    verdict = _finalize(*lane(author=_author_state(user_moved=False)))
    assert verdict["state"] == "lane-failure"
    assert any("user account was never moved" in p for p in verdict["lane_problems"])


def test_displaced_user_is_a_lane_failure(lane) -> None:
    """Teardown that leaves the principal in the disposable OU has not finished."""
    verdict = _finalize(
        *lane(
            cleanup=_cleanup_result(
                residual={
                    "computer_restored": True,
                    "user_restored": False,
                    "surviving_links": [],
                    "surviving_gpos": [],
                    "surviving_ous": [],
                }
            )
        )
    )
    assert verdict["state"] == "lane-failure"
    assert any("user account was not restored" in p for p in verdict["lane_problems"])


def test_pre_run_residual_in_the_user_hive_is_a_lane_failure(lane) -> None:
    """Values already in the principal's hive could satisfy the control."""
    verdict = _finalize(
        *lane(observation=_observation(pre_run_residual=[{"value_name": "Loop", "value": "stale"}]))
    )
    assert verdict["state"] == "lane-failure"
    assert any("carried policy values before" in p for p in verdict["lane_problems"])


def test_computer_scope_candidate_is_refused(lane, capsys) -> None:
    """A user-scope verdict must never be produced from a computer-scope prediction.

    They predict different sides of the model. Comparing one against the other
    would diff two different experiments and report the difference as a defect.
    """
    run_dir, candidate = lane(
        prediction=_prediction(scope="computer"),
        expected=_expected(scope="computer"),
    )
    assert finalize_user.main([str(run_dir), "--candidate-root", str(candidate), "--no-tag"]) == 1
    assert not (run_dir / "rsop-user-verdict.json").exists()
    assert "user-scope finalizer" in capsys.readouterr().err


def test_applied_set_difference_alone_does_not_decide_the_verdict(lane) -> None:
    """WI-032: the applied sets are recorded, not gated.

    ``RsopResult.is_applied`` means "applied on at least one side", while
    ``UserResults`` lists what applied to the USER. On a topology whose GPOs
    also scope the computer these are different questions, and gating on the
    difference would manufacture findings out of a reporting gap.

    Studio-RSOP-Loopback is the concrete case: it is a computer-side GPO that
    the model reports as applied and that never appears in ``UserResults``.
    """
    verdict = _finalize(*lane())
    assert verdict["state"] == "pass"
    assert verdict["comparison"]["applied_only_predicted"] == ["Studio-RSOP-Loopback"]
    assert verdict["comparison"]["applied_set_difference_is_advisory"] is True


def test_server_build_on_the_client_is_a_lane_failure(lane) -> None:
    """Shared environment gate: this lane observes a client, and says so."""
    verdict = _finalize(
        *lane(observation=_observation(environment={"build": "26100", "locale": "en-US"}))
    )
    assert verdict["state"] == "lane-failure"
    assert any("build family" in problem for problem in verdict["lane_problems"])


def test_lane_failure_suppresses_the_controls_and_the_comparison(lane) -> None:
    """Order matters: a broken run is not also asked whether Studio was right."""
    verdict = _finalize(
        *lane(
            observation=_observation(
                session_present=False,
                control_present=False,
                observed_loopback_mode="disabled",
            )
        )
    )
    assert verdict["state"] == "lane-failure"
    assert verdict["control_problems"] == []
    assert verdict["comparison"] is None
