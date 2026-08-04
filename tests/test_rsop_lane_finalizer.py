"""The WP-6B finalizer's three outcomes must stay distinguishable.

``lane-failure``, ``inconclusive``, ``pass`` and ``finding`` answer four
different questions, and collapsing any pair of them produces a confident wrong
answer rather than a visible gap:

* a client that never processed policy looks exactly like a total model failure
  unless the control row separates them;
* a broken harness looks exactly like a Studio defect unless lane validity is
  checked first;
* a wrong prediction is the *point* of the lane, so it must not be reported as
  a lane failure and quietly retried until it agrees.

Each test below drives the finalizer with one thing wrong and asserts the state
it lands in. The fixtures are synthetic because the discrimination logic is what
is under test -- a live run exercises one path through it, and the paths that
matter most are the ones a healthy estate never takes.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MODULE_PATH = _REPO_ROOT / "scripts" / "windows-oracle" / "finalize_rsop_run.py"

_spec = importlib.util.spec_from_file_location("finalize_rsop_run", _MODULE_PATH)
assert _spec and _spec.loader
finalize_rsop_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(finalize_rsop_run)


def _author_state() -> dict[str, Any]:
    return {
        "run_id": "rsop-author-20260804000000-1111",
        "setup_completed": True,
        "computer_moved": True,
        "environment": {"build": "26100", "locale": "en-US"},
        "gpos": [
            {"symbolic_name": "Studio-RSOP-Site", "name": "Studio-RSOP-Site-S"},
            {"symbolic_name": "Studio-RSOP-Domain", "name": "Studio-RSOP-Domain-S"},
            {"symbolic_name": "Studio-RSOP-Parent", "name": "Studio-RSOP-Parent-S"},
            {"symbolic_name": "Studio-RSOP-ChildA", "name": "Studio-RSOP-ChildA-S"},
            {"symbolic_name": "Studio-RSOP-ChildB", "name": "Studio-RSOP-ChildB-S"},
            {"symbolic_name": "Studio-RSOP-Control", "name": "Studio-RSOP-Control-S"},
        ],
    }


def _cleanup_result(**overrides: Any) -> dict[str, Any]:
    result = {
        "run_id": "rsop-author-20260804000000-1111",
        "cleanup_problems": [],
        "residual": {
            "computer_restored": True,
            "surviving_links": [],
            "surviving_gpos": [],
            "surviving_ous": [],
        },
    }
    result.update(overrides)
    return result


def _observation(**overrides: Any) -> dict[str, Any]:
    observation = {
        "run_id": "rsop-observe-20260804000000-2222",
        "scope": "computer",
        "observation_settled": True,
        "settle_attempts": 1,
        "cse_completed": True,
        "rsop_captured": True,
        "pre_run_residual": [],
        "control_present": True,
        "lane_problems": [],
        "error": None,
        "applied_gpos": [
            "Studio-RSOP-ChildA-S",
            "Studio-RSOP-ChildB-S",
            "Studio-RSOP-Control-S",
            "Studio-RSOP-Domain-S",
            "Studio-RSOP-Parent-S",
            "Studio-RSOP-Site-S",
        ],
        "denied_gpos": [],
        "observed_values": [
            {"value_name": "ChildBOnly", "value": "1"},
            {"value_name": "Control", "value": "present"},
            {"value_name": "Precedence", "value": "childA"},
            {"value_name": "SiteOnly", "value": "1"},
        ],
        "environment": {"build": "26200", "locale": "en-US"},
    }
    observation.update(overrides)
    return observation


def _prediction() -> dict[str, Any]:
    return {
        "query_id": "wp6b-lsdou-precedence",
        "applied_gpos": [
            "Studio-RSOP-ChildA",
            "Studio-RSOP-ChildB",
            "Studio-RSOP-Control",
            "Studio-RSOP-Domain",
            "Studio-RSOP-Parent",
            "Studio-RSOP-Site",
        ],
        "denied_gpos": [],
        "winners": [
            {"value_name": "ChildBOnly", "value": "1", "winning_gpo": "Studio-RSOP-ChildB"},
            {"value_name": "Control", "value": "present", "winning_gpo": "Studio-RSOP-Control"},
            {"value_name": "Precedence", "value": "childA", "winning_gpo": "Studio-RSOP-ChildA"},
            {"value_name": "SiteOnly", "value": "1", "winning_gpo": "Studio-RSOP-Site"},
        ],
    }


#: The authoring half copies its input into its own work dir, which the driver
#: pulls; the finalizer compares the two copies byte for byte. The content does
#: not matter to these tests -- only that the two copies agree.
_TOPOLOGY = '{"domain": "ad.labdomain.dev", "gpos": []}'


def _expected() -> dict[str, Any]:
    return {
        "scenario_id": "lsdou-precedence",
        "control_gpo": "Studio-RSOP-Control",
        "control_value_name": "Control",
    }


@pytest.fixture
def lane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A runnable lane whose harness binding and git state are forced clean.

    The finalizer hashes deployed harness files against the source tree and
    shells out to git. Neither is what these tests are about, so both are
    stubbed -- but note they are stubbed to the *passing* value, so a test that
    expects lane-failure has to earn it from the fixture it actually varies.
    """
    run_dir = tmp_path / "run"
    (run_dir / "author").mkdir(parents=True)
    (run_dir / "observe").mkdir(parents=True)
    candidate = tmp_path / "candidate"
    candidate.mkdir()

    monkeypatch.setattr(finalize_rsop_run, "DEPLOYED_FILES", {})
    monkeypatch.setattr(finalize_rsop_run, "LOCAL_FILES", {})

    def fake_run(args: list[str], **kwargs: Any):
        if args[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(args, 0, stdout="abc1234\n", stderr="")
        if args[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess call: {args}")

    monkeypatch.setattr(finalize_rsop_run.subprocess, "run", fake_run)
    monkeypatch.setattr(
        finalize_rsop_run, "tag_evidence_commit", lambda *a, **k: None
    )

    def write(
        *,
        author: dict[str, Any] | None = None,
        cleanup: dict[str, Any] | None = None,
        observation: dict[str, Any] | None = None,
        prediction: dict[str, Any] | None = None,
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
        (candidate / "expected.json").write_text(json.dumps(_expected()), encoding="utf-8")
        return run_dir, candidate

    return write


def _finalize(run_dir: Path, candidate: Path) -> dict[str, Any]:
    finalize_rsop_run.main(
        [str(run_dir), "--candidate-root", str(candidate), "--no-tag"]
    )
    return json.loads((run_dir / "rsop-verdict.json").read_text(encoding="utf-8"))


def test_agreeing_run_passes(lane) -> None:
    """The control: Windows agrees with the prediction."""
    verdict = _finalize(*lane())
    assert verdict["state"] == "pass"
    assert verdict["passed"] is True
    assert verdict["comparison"]["agrees"] is True


def test_absent_control_is_inconclusive_not_a_finding(lane) -> None:
    """Policy never reached the endpoint, so nothing is known about rsop.py.

    Every value is absent here, which is *identical* in the raw evidence to
    "Studio predicted six GPOs and Windows applied none". Reporting that as a
    finding would be the single most damaging thing this lane could do: it
    would manufacture a spectacular model defect out of a client that was
    simply not listening.
    """
    verdict = _finalize(
        *lane(
            observation=_observation(
                control_present=False,
                applied_gpos=[],
                observed_values=[],
            )
        )
    )
    assert verdict["state"] == "inconclusive"
    assert verdict["comparison"] is None
    assert any("control value" in problem for problem in verdict["control_problems"])


def test_wrong_winner_is_a_finding_not_a_lane_failure(lane) -> None:
    """The prediction was wrong. That is a result, and the lane must say so."""
    observation = _observation(
        observed_values=[
            {"value_name": "ChildBOnly", "value": "1"},
            {"value_name": "Control", "value": "present"},
            {"value_name": "Precedence", "value": "childB"},
            {"value_name": "SiteOnly", "value": "1"},
        ]
    )
    verdict = _finalize(*lane(observation=observation))
    assert verdict["state"] == "finding"
    assert verdict["passed"] is False
    assert verdict["lane_problems"] == []
    findings = verdict["comparison"]["value_findings"]
    assert [f["value_name"] for f in findings] == ["Precedence"]
    assert findings[0]["predicted"] == "childA"
    assert findings[0]["observed"] == "childB"
    assert findings[0]["kind"] == "wrong_value"


def test_unsettled_observation_is_a_lane_failure(lane) -> None:
    """An absent value that nobody waited for is not an absent value."""
    verdict = _finalize(
        *lane(observation=_observation(observation_settled=False, cse_completed=False))
    )
    assert verdict["state"] == "lane-failure"
    assert any("did not settle" in problem for problem in verdict["lane_problems"])


def test_missing_rsop_capture_is_a_lane_failure(lane) -> None:
    """gpresult exiting 0 while writing nothing must not become a verdict."""
    verdict = _finalize(*lane(observation=_observation(rsop_captured=False)))
    assert verdict["state"] == "lane-failure"
    assert any("nothing to compare" in problem for problem in verdict["lane_problems"])


def test_surviving_domain_link_is_a_lane_failure(lane) -> None:
    """This lane links at the domain root; a run that cannot prove it undid
    that has not finished, whatever its comparison said."""
    cleanup = _cleanup_result(
        residual={
            "computer_restored": True,
            "surviving_links": ["Studio-RSOP-Domain-S @ DC=ad,DC=labdomain,DC=dev"],
            "surviving_gpos": [],
            "surviving_ous": [],
        }
    )
    verdict = _finalize(*lane(cleanup=cleanup))
    assert verdict["state"] == "lane-failure"
    assert any("link survived teardown" in problem for problem in verdict["lane_problems"])


def test_displaced_computer_is_a_lane_failure(lane) -> None:
    cleanup = _cleanup_result(
        residual={
            "computer_restored": False,
            "surviving_links": [],
            "surviving_gpos": [],
            "surviving_ous": [],
        }
    )
    verdict = _finalize(*lane(cleanup=cleanup))
    assert verdict["state"] == "lane-failure"
    assert any("was not restored" in problem for problem in verdict["lane_problems"])


def test_pre_run_residual_is_a_lane_failure(lane) -> None:
    """Values already present cannot have been written by this run.

    Without this check a previous run's residue satisfies the control, the
    observation is attributed to this run, and a lane that measured nothing
    reports a pass.
    """
    verdict = _finalize(
        *lane(
            observation=_observation(
                pre_run_residual=[{"value_name": "Control", "value": "present"}]
            )
        )
    )
    assert verdict["state"] == "lane-failure"
    assert any("before this run began" in problem for problem in verdict["lane_problems"])


def test_server_build_on_the_client_is_a_lane_failure(lane) -> None:
    """Environment-spec rule 6: a lane applying policy to a client says so."""
    verdict = _finalize(
        *lane(observation=_observation(environment={"build": "26100", "locale": "en-US"}))
    )
    assert verdict["state"] == "lane-failure"
    assert any("build family" in problem for problem in verdict["lane_problems"])


def test_foreign_gpos_do_not_count_as_disagreement(lane) -> None:
    """The estate carries policy this lane did not create.

    The Default Domain Policy always applies. Counting it would report a
    disagreement that is really a scoping error in the harness.
    """
    observation = _observation(
        applied_gpos=[*_observation()["applied_gpos"], "Default Domain Policy"]
    )
    verdict = _finalize(*lane(observation=observation))
    assert verdict["state"] == "pass"
    assert verdict["comparison"]["observed_foreign_gpos"] == ["Default Domain Policy"]


def test_gpo_predicted_but_not_applied_is_a_finding(lane) -> None:
    observation = _observation(
        applied_gpos=[
            name for name in _observation()["applied_gpos"] if name != "Studio-RSOP-Site-S"
        ]
    )
    verdict = _finalize(*lane(observation=observation))
    assert verdict["state"] == "finding"
    assert verdict["comparison"]["applied_only_predicted"] == ["Studio-RSOP-Site"]


def test_lane_failure_suppresses_the_comparison_entirely(lane) -> None:
    """A run that cannot be trusted must not also publish a verdict about
    Studio -- in either direction. A 'pass' from a broken lane is worse than
    no result, because it is durable and looks like evidence."""
    verdict = _finalize(*lane(cleanup=_cleanup_result(cleanup_problems=["unlink failed"])))
    assert verdict["state"] == "lane-failure"
    assert verdict["comparison"] is None
    assert verdict["control_problems"] == []


def test_tagging_call_matches_the_real_signature(tmp_path: Path, monkeypatch) -> None:
    """The pass path must actually be able to tag.

    This is here because it was missed: the other tests pass --no-tag and stub
    tag_evidence_commit with a permissive lambda, so a wrong call signature
    sailed through every one of them and only surfaced against the live estate,
    on the pass path, after the experiment had already run. A stub that accepts
    anything tests nothing about the call.

    So this test binds the finalizer's call against the REAL function's
    signature and lets a mismatch raise, without tagging anything.
    """
    import inspect

    from gpo_studio.oracle_evidence import tag_evidence_commit as real

    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def recording(*args: Any, **kwargs: Any) -> str:
        # Raises TypeError if the finalizer's call does not fit the real
        # function -- which is exactly the defect this pins.
        inspect.signature(real).bind(*args, **kwargs)
        calls.append((args, kwargs))
        return "evidence/stub"

    run_dir = tmp_path / "run"
    (run_dir / "author").mkdir(parents=True)
    (run_dir / "observe").mkdir(parents=True)
    candidate = tmp_path / "candidate"
    candidate.mkdir()

    monkeypatch.setattr(finalize_rsop_run, "DEPLOYED_FILES", {})
    monkeypatch.setattr(finalize_rsop_run, "LOCAL_FILES", {})
    monkeypatch.setattr(finalize_rsop_run, "tag_evidence_commit", recording)

    def fake_run(args: list[str], **kwargs: Any):
        if args[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(args, 0, stdout="abc1234\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(finalize_rsop_run.subprocess, "run", fake_run)

    (run_dir / "author" / "author-state.json").write_text(
        json.dumps(_author_state()), encoding="utf-8"
    )
    (run_dir / "author" / "cleanup-result.json").write_text(
        json.dumps(_cleanup_result()), encoding="utf-8"
    )
    (run_dir / "observe" / "observation.json").write_text(
        json.dumps(_observation()), encoding="utf-8"
    )
    (candidate / "prediction.json").write_text(json.dumps(_prediction()), encoding="utf-8")
    (candidate / "expected.json").write_text(json.dumps(_expected()), encoding="utf-8")
    (candidate / "topology.json").write_text(_TOPOLOGY, encoding="utf-8")
    (run_dir / "author" / "topology.json").write_text(_TOPOLOGY, encoding="utf-8")

    # No --no-tag: the tagging path is the point.
    finalize_rsop_run.main([str(run_dir), "--candidate-root", str(candidate)])

    verdict = json.loads((run_dir / "rsop-verdict.json").read_text(encoding="utf-8"))
    assert verdict["state"] == "pass"
    assert len(calls) == 1


def test_guest_json_with_a_bom_still_loads(tmp_path: Path) -> None:
    """PowerShell's Set-Content -Encoding UTF8 writes a BOM.

    Reading it as plain utf-8 raises before the finalizer decides anything, so
    the lane presents as crashed rather than as any of its four outcomes. Also
    found live rather than here, which is why it is now pinned here.
    """
    path = tmp_path / "observation.json"
    path.write_text(json.dumps({"run_id": "x"}), encoding="utf-8-sig")
    assert finalize_rsop_run._load(path) == {"run_id": "x"}


def test_candidate_artifacts_are_hash_bound(lane) -> None:
    """WI-025 in this lane: the verdict must name what it compared against.

    The prediction is the input artifact this lane's entire claim rests on. A
    verdict that cites it without hashing it asserts a comparison nobody can
    re-check.
    """
    run_dir, candidate = lane()
    verdict = _finalize(run_dir, candidate)
    assert verdict["state"] == "pass"
    assert set(verdict["candidate"]) == {"topology.json", "prediction.json", "expected.json"}
    assert all(len(h) == 64 for h in verdict["candidate"].values())
    assert verdict["topology_delivered_intact"] is True


def test_guest_topology_mismatch_is_a_lane_failure(lane) -> None:
    """The guest built a different experiment from the one predicted.

    Without this check the verdict compares a forecast about one topology
    against results from another, and the mismatch would be reported as a model
    defect -- the lane blaming Studio for the harness's own drift.
    """
    verdict = _finalize(*lane(guest_topology='{"domain": "ad.labdomain.dev", "gpos": ["drift"]}'))
    assert verdict["state"] == "lane-failure"
    assert verdict["topology_delivered_intact"] is False
    assert any("different experiment" in p for p in verdict["lane_problems"])


def test_missing_guest_topology_is_a_lane_failure(lane) -> None:
    """Nothing binds the prediction to the experiment that ran."""
    verdict = _finalize(*lane(guest_topology=None))
    assert verdict["state"] == "lane-failure"
    assert verdict["topology_delivered_intact"] is None
    assert any("nothing" in p and "binds" in p for p in verdict["lane_problems"])
