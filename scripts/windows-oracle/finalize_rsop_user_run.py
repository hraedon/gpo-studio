#!/usr/bin/env python3
"""Finalize a Plan 033 WP-9 user-scope RSOP run: user side, and loopback.

WP-6B asked whether ``rsop.py`` predicts Windows on the computer side. This
asks the same question about the user side, where the model has three
behaviours nothing has ever checked against Windows: user-side resolution,
loopback merge, and loopback replace.

## Four outcomes, and the fourth is new

``lane-failure``   the experiment could not run or cannot be trusted.
``inconclusive``   the experiment ran but says nothing about the model.
``pass``           the prediction matched Windows.
``finding``        the prediction did not, and that is a result about Studio.

The inconclusive class carries a second member here, and it is the reason this
lane can be trusted at all. **Loopback either engaged or it did not**, and
Windows says which in event 5311. Under ``replace`` the expected observation is
that a whole GPO's values are ABSENT -- which is also exactly what a run where
loopback never took effect looks like. Without the control, the two are one
signature, and the lane would happily report "Studio predicted the user-location
values would be discarded, and they were" about a machine that never turned
loopback on.

So a mode mismatch is inconclusive, never a pass and never a finding.

## What is gated, and what is only recorded

The winners are gated: they are what the corpus scenarios assert, and they are
what an operator would act on.

The applied-GPO sets are RECORDED, not gated (WI-032). ``RsopResult`` carries
one ``is_applied`` per GPO meaning "applied on at least one side", while
``UserResults`` lists the GPOs that applied to the USER. On a scenario whose
GPOs also scope the computer those are different questions, and gating on a
comparison between them would manufacture findings out of a reporting gap in
the model's result shape. The difference is written into the verdict so it is
visible rather than quietly dropped.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from finalize_rsop_run import (  # noqa: E402
    _client_environment_problems,
    _find_one,
    _load,
    _sha256,
    _symbolic_map,
)

from gpo_studio.oracle_evidence import OracleEvidenceError, tag_evidence_commit  # noqa: E402

# The observation half is a different script from the computer lane's, and the
# authoring half is the same one. Both are bound by hash: a lane that certifies
# a harness it did not run is the failure this map exists to prevent.
DEPLOYED_FILES: dict[str, str] = {
    "run-rsop-author.ps1": "scripts/windows-oracle/run-rsop-author.ps1",
    "run-rsop-user-observe.ps1": "scripts/windows-oracle/run-rsop-user-observe.ps1",
}

LOCAL_FILES: dict[str, str] = {
    "run-rsop-user-oracle.sh": "scripts/windows-oracle/run-rsop-user-oracle.sh",
    "psdirect.ps1": "scripts/windows-oracle/psdirect.ps1",
    "build-rsop-candidate.py": "scripts/plan-033/build-rsop-candidate.py",
}


def _lane_validity(
    author: dict[str, Any],
    cleanup: dict[str, Any] | None,
    observe: dict[str, Any],
    harness_ok: bool,
    dirty: bool,
    topology_delivered_intact: bool | None,
) -> list[str]:
    """Reasons this run cannot produce a verdict about Studio at all."""
    problems: list[str] = []

    if not author.get("setup_completed"):
        problems.append("authoring half never completed setup")
    if not author.get("computer_moved"):
        problems.append("the endpoint's computer account was never moved into the target OU")
    if not author.get("user_moved"):
        # The user is the target of this lane. A run where it never left its
        # original container resolved policy for a principal the prediction is
        # not about, and every winner would legitimately differ.
        problems.append("the principal's user account was never moved into its target OU")
    for problem in author.get("authored_problems") or []:
        problems.append(f"authored topology does not match intent: {problem}")

    if cleanup is None:
        problems.append("authoring half recorded no cleanup: it never reached teardown")
    else:
        for problem in cleanup.get("cleanup_problems") or []:
            problems.append(f"authoring cleanup: {problem}")
        residual = cleanup.get("residual") or {}
        if not residual.get("computer_restored"):
            problems.append(
                "the endpoint's computer account was not restored to its original OU"
            )
        # None means the run never moved a user, which the check above has
        # already reported; False means it moved one and did not put it back.
        if residual.get("user_restored") is False:
            problems.append("the principal's user account was not restored to its original OU")
        for link in residual.get("surviving_links") or []:
            problems.append(f"link survived teardown: {link}")
        for gpo in residual.get("surviving_gpos") or []:
            problems.append(f"GPO survived teardown: {gpo}")
        for ou in residual.get("surviving_ous") or []:
            problems.append(f"OU survived teardown: {ou}")

    for problem in observe.get("lane_problems") or []:
        problems.append(f"observation: {problem}")
    if topology_delivered_intact is False:
        problems.append(
            "the topology the authoring half used does not match the candidate this "
            "controller built; the prediction describes a different experiment"
        )
    elif topology_delivered_intact is None:
        problems.append(
            "the authoring half returned no copy of the topology it used, so nothing "
            "binds the prediction to the experiment that ran"
        )
    if observe.get("error"):
        problems.append(f"observation half threw: {observe['error']}")
    if not observe.get("session_present"):
        # Without an interactive session there is no user hive and no RSoP data,
        # so every expected value reads as absent: a clean sweep of findings
        # manufactured by a missing precondition.
        problems.append(
            "no interactive session for the principal; restore the estate's "
            "user-logged-on checkpoint before running this lane"
        )
    if not observe.get("rsop_captured"):
        problems.append(
            "no RSOP document was captured, so there is nothing to compare; "
            "gpresult's exit code is not evidence that it wrote a file"
        )
    if not observe.get("observation_settled"):
        problems.append(
            "the observation did not settle: the control value and the user-side "
            "policy-completion event were not both seen, so an absent value cannot "
            "be distinguished from one that has not been written yet"
        )

    residual_values = observe.get("pre_run_residual") or []
    if residual_values:
        names = ", ".join(str(row.get("value_name")) for row in residual_values)
        problems.append(
            f"the principal's hive carried policy values before this run began ({names}); "
            "the observation cannot be attributed to this run"
        )

    if not harness_ok:
        problems.append("deployed harness does not match its committed source")
    if dirty:
        problems.append("source tree is dirty; certification evidence requires a clean tree")
    return problems


def _control_problems(observe: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    """Reasons the experiment says nothing about the model.

    Two controls, and the second is what makes a loopback verdict meaningful.
    """
    problems: list[str] = []
    if not observe.get("control_present"):
        problems.append(
            f"the control value {expected.get('control_value_name')!r} "
            f"(written by {expected.get('control_gpo')!r}, unconflicted and unfiltered) "
            "is absent: user policy did not reach the principal, so no disagreement "
            "in this run is attributable to rsop.py"
        )

    intended = str(expected.get("loopback_mode") or "disabled")
    observed = observe.get("observed_loopback_mode")
    if observed is None:
        problems.append(
            "Windows logged no loopback-mode event (5311) for this pass, so the mode "
            "it actually used is unknown; under replace, 'the user-location GPO was "
            "discarded' and 'loopback never engaged' are the same observation"
        )
    elif str(observed) != intended:
        problems.append(
            f"Windows processed the user side with loopback {observed!r}, not the "
            f"{intended!r} this topology authored. The prediction is about a mode "
            "that did not run, and reading the values as a finding would invent a "
            "defect out of a harness problem."
        )
    return problems


def _compare(
    prediction: dict[str, Any],
    observe: dict[str, Any],
    symbolic: dict[str, str],
) -> dict[str, Any]:
    """Diff the prediction against Windows.

    Winners decide the verdict. The applied sets are computed and reported but
    do not decide it -- see the module docstring and WI-032.
    """
    predicted_applied = sorted(prediction.get("applied_gpos") or [])
    observed_applied = sorted(
        symbolic[name] for name in (observe.get("applied_gpos") or []) if name in symbolic
    )
    observed_foreign = sorted(
        name for name in (observe.get("applied_gpos") or []) if name not in symbolic
    )

    predicted_winners = {str(row["value_name"]): row for row in (prediction.get("winners") or [])}
    observed_winners = {
        str(row["value_name"]): str(row["value"]) for row in (observe.get("observed_values") or [])
    }

    value_findings: list[dict[str, Any]] = []
    for value_name in sorted(set(predicted_winners) | set(observed_winners)):
        predicted_row = predicted_winners.get(value_name)
        predicted_value = str(predicted_row["value"]) if predicted_row else None
        observed_value = observed_winners.get(value_name)
        if predicted_value == observed_value:
            continue
        value_findings.append(
            {
                "value_name": value_name,
                "predicted": predicted_value,
                "observed": observed_value,
                "predicted_winning_gpo": (
                    predicted_row.get("winning_gpo") if predicted_row else None
                ),
                "kind": (
                    "predicted_but_absent"
                    if observed_value is None
                    else "observed_but_unpredicted"
                    if predicted_value is None
                    else "wrong_value"
                ),
            }
        )

    return {
        "predicted_applied_either_side": predicted_applied,
        "observed_applied_user_side": observed_applied,
        "observed_foreign_gpos": observed_foreign,
        "applied_set_difference_is_advisory": True,
        "applied_only_predicted": sorted(set(predicted_applied) - set(observed_applied)),
        "applied_only_observed": sorted(set(observed_applied) - set(predicted_applied)),
        "predicted_winners": {name: str(row["value"]) for name, row in predicted_winners.items()},
        "observed_winners": observed_winners,
        "value_findings": value_findings,
        "agrees": not value_findings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=_REPO_ROOT)
    parser.add_argument("--transport", choices=["psdirect"], default="psdirect")
    parser.add_argument("--no-tag", action="store_true")
    args = parser.parse_args(argv)
    run_dir = args.run_dir.resolve()
    repo_root = args.repo_root.resolve()

    author_path = _find_one(run_dir / "author", "author-state.json")
    observe_path = _find_one(run_dir / "observe", "observation.json")
    if author_path is None or observe_path is None:
        missing = "author-state.json" if author_path is None else "observation.json"
        print(f"finalize refused: {missing} is not in {run_dir}", file=sys.stderr)
        return 1
    author = _load(author_path)
    observe = _load(observe_path)
    cleanup_path = _find_one(run_dir / "author", "cleanup-result.json")
    cleanup = _load(cleanup_path) if cleanup_path is not None else None

    prediction = _load(args.candidate_root / "prediction.json")
    expected = _load(args.candidate_root / "expected.json")

    # A user-scope verdict must not be produced from a computer-scope candidate,
    # and vice versa. The scenario decides which side of the model was
    # predicted; if the two ever disagree the comparison is between different
    # experiments and every row of it is meaningless.
    if str(prediction.get("scope")) != "user" or str(expected.get("scope")) != "user":
        print(
            "finalize refused: this is the user-scope finalizer and the candidate "
            f"declares scope {prediction.get('scope')!r}",
            file=sys.stderr,
        )
        return 1
    if str(observe.get("scope")) != "user":
        print(
            f"finalize refused: the observation declares scope {observe.get('scope')!r}",
            file=sys.stderr,
        )
        return 1

    source_hashes: dict[str, str] = {}
    harness_ok = True
    for name, source in {**DEPLOYED_FILES, **LOCAL_FILES}.items():
        src_hash = _sha256(repo_root / source)
        source_hashes[name] = src_hash
        evidence = run_dir / "deployed" / name if name in DEPLOYED_FILES else repo_root / source
        if not evidence.is_file() or _sha256(evidence) != src_hash:
            harness_ok = False

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root, check=True, capture_output=True, text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root, check=True, capture_output=True, text=True,
        ).stdout
    )

    candidate_hashes = {
        name: _sha256(args.candidate_root / name)
        for name in ("topology.json", "prediction.json", "expected.json")
        if (args.candidate_root / name).is_file()
    }

    guest_topology = _find_one(run_dir / "author", "topology.json")
    topology_delivered_intact: bool | None = None
    if guest_topology is not None and (args.candidate_root / "topology.json").is_file():
        topology_delivered_intact = _sha256(guest_topology) == _sha256(
            args.candidate_root / "topology.json"
        )

    symbolic = _symbolic_map(author)
    lane_problems = _lane_validity(
        author, cleanup, observe, harness_ok, dirty, topology_delivered_intact
    )
    lane_problems += _client_environment_problems(observe)
    control_problems = _control_problems(observe, expected) if not lane_problems else []
    comparison = (
        _compare(prediction, observe, symbolic)
        if not (lane_problems or control_problems)
        else None
    )

    if lane_problems:
        state = "lane-failure"
    elif control_problems:
        state = "inconclusive"
    elif comparison and comparison["agrees"]:
        state = "pass"
    else:
        state = "finding"

    verdict = {
        "schema_version": 1,
        "work_package": "WP-9",
        "scope": "user",
        "run_id": observe.get("run_id"),
        "author_run_id": author.get("run_id"),
        "state": state,
        "passed": state == "pass",
        "transport": args.transport,
        "scenario_id": expected.get("scenario_id"),
        "principal": observe.get("principal"),
        "principal_sid": observe.get("principal_sid"),
        "loopback": {
            "intended": expected.get("loopback_mode"),
            "observed": observe.get("observed_loopback_mode"),
            "control_ok": observe.get("loopback_control_ok"),
        },
        "lane_problems": lane_problems,
        "control_problems": control_problems,
        "comparison": comparison,
        "settle_attempts": observe.get("settle_attempts"),
        "user_policy_completed": observe.get("user_policy_completed"),
        "environment": {
            "client": observe.get("environment"),
            "server": author.get("environment"),
        },
        "source": {"commit": commit, "dirty": dirty, "files": source_hashes},
        "harness_matches_source": harness_ok,
        "candidate": candidate_hashes,
        "topology_delivered_intact": topology_delivered_intact,
    }

    output = run_dir / "rsop-user-verdict.json"
    output.write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if state == "pass" and not args.no_tag:
        try:
            tag_evidence_commit(repo_root, str(observe.get("run_id")), commit)
        except OracleEvidenceError as error:
            verdict["state"] = "lane-failure"
            verdict["passed"] = False
            verdict["lane_problems"] = [*lane_problems, f"could not tag evidence commit: {error}"]
            output.write_text(
                json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(f"finalize: tagging failed, downgraded to lane-failure: {error}", file=sys.stderr)
            return 1

    print(f"\nrun {observe.get('run_id')}: state={state} (source {commit}, dirty={dirty})")
    print(
        f"  loopback intended={expected.get('loopback_mode')!r} "
        f"observed={observe.get('observed_loopback_mode')!r}"
    )
    for problem in lane_problems + control_problems:
        print(f"  ! {problem}")
    if comparison:
        for finding in comparison["value_findings"]:
            print(
                f"  FINDING {finding['value_name']}: predicted {finding['predicted']!r}, "
                f"observed {finding['observed']!r} ({finding['kind']})"
            )
        for name in comparison["applied_only_predicted"]:
            print(f"  note (advisory) {name}: predicted applied, not in UserResults")
        for name in comparison["applied_only_observed"]:
            print(f"  note (advisory) {name}: in UserResults, not predicted applied")
    return 0 if state == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
