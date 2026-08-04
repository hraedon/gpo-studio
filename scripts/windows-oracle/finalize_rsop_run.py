#!/usr/bin/env python3
"""Finalize a Plan 033 WP-6B RSOP run: does Windows agree with ``rsop.py``?

The lane compares a prediction committed *before* anything was applied against
what Windows actually resolved. This script is where the three outcomes are
kept apart, and keeping them apart is the entire job:

``lane-failure``   the experiment could not run or cannot be trusted -- cleanup
                   left state behind, the harness does not match its source,
                   the tree is dirty, gpresult produced nothing. No verdict
                   about Studio is available, in either direction.
``inconclusive``   the experiment ran but the control did not appear, so policy
                   demonstrably did not reach the endpoint. Every disagreement
                   is then explained by "nothing applied" and none of them is
                   evidence about the model.
``pass`` / finding the experiment ran, the control appeared, and the prediction
                   either matched Windows or did not. A mismatch is a FINDING
                   ABOUT STUDIO, which is the point of the lane -- not a lane
                   failure, and not something to retry until it agrees.

A collapsed version of these -- "did the values match?" -- would report a
client that never processed policy as a total model failure, and a broken
harness as a Studio defect. The endpoint lane already had to learn this;
WP-6 inherits the shape rather than rediscovering it.

## Name mapping

The authoring half stamps every GPO name per run (``Studio-RSOP-ChildA`` ->
``Studio-RSOP-ChildA-20260804...-1234``) so no previous run's residue can be
read as this run's evidence. The prediction speaks in symbolic names. Mapping
between them is by exact prefix against the *authoring half's own record* of
what it created -- never by fuzzy matching against whatever Windows reported,
which would let an unrelated GPO in the estate satisfy a row.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from gpo_studio.oracle_evidence import (  # noqa: E402
    CLIENT_NOT_TESTED,
    FROZEN_ENVIRONMENT,
    OracleEvidenceError,
    tag_evidence_commit,
)

DEPLOYED_FILES: dict[str, str] = {
    "run-rsop-author.ps1": "scripts/windows-oracle/run-rsop-author.ps1",
    "run-rsop-observe.ps1": "scripts/windows-oracle/run-rsop-observe.ps1",
}

LOCAL_FILES: dict[str, str] = {
    "run-rsop-oracle.sh": "scripts/windows-oracle/run-rsop-oracle.sh",
    "psdirect.ps1": "scripts/windows-oracle/psdirect.ps1",
    "build-rsop-candidate.py": "scripts/plan-033/build-rsop-candidate.py",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> Any:
    # utf-8-sig: PowerShell's Set-Content -Encoding UTF8 writes a BOM. Reading
    # these as plain utf-8 raises before the finalizer decides anything, which
    # presents as "the lane crashed" rather than as any of its four outcomes.
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _build_family(build: str) -> str:
    digits = "".join(character for character in build if character.isdigit())
    return digits[:5] if digits else ""


def _find_one(run_dir: Path, name: str) -> Path | None:
    matches = sorted(run_dir.rglob(name))
    return matches[0] if matches else None


def _symbolic_map(author: dict[str, Any]) -> dict[str, str]:
    """Real GPO name -> symbolic name, from the authoring half's own record."""
    mapping: dict[str, str] = {}
    for gpo in author.get("gpos") or []:
        real = str(gpo.get("name") or "")
        symbolic = str(gpo.get("symbolic_name") or "")
        if real and symbolic:
            mapping[real] = symbolic
    return mapping


def _lane_validity(
    author: dict[str, Any],
    cleanup: dict[str, Any] | None,
    observe: dict[str, Any],
    harness_ok: bool,
    dirty: bool,
) -> list[str]:
    """Reasons this run cannot produce a verdict about Studio at all."""
    problems: list[str] = []

    if not author.get("setup_completed"):
        problems.append("authoring half never completed setup")
    if not author.get("computer_moved"):
        problems.append("the endpoint's computer account was never moved into the target OU")

    # Cleanup is a lane-validity concern rather than housekeeping. This lane
    # links policy at the domain root and at the site, which reaches every
    # machine in the estate including the DC; a run that cannot prove it undid
    # that has not finished, whatever its comparison said.
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
        for link in residual.get("surviving_links") or []:
            problems.append(f"link survived teardown: {link}")
        for gpo in residual.get("surviving_gpos") or []:
            problems.append(f"GPO survived teardown: {gpo}")
        for ou in residual.get("surviving_ous") or []:
            problems.append(f"OU survived teardown: {ou}")

    for problem in observe.get("lane_problems") or []:
        problems.append(f"observation: {problem}")
    if observe.get("error"):
        problems.append(f"observation half threw: {observe['error']}")
    if not observe.get("rsop_captured"):
        problems.append(
            "no RSOP document was captured, so there is nothing to compare; "
            "gpresult's exit code is not evidence that it wrote a file"
        )
    if not observe.get("observation_settled"):
        problems.append(
            "the observation did not settle: neither the control value appeared nor "
            "did the Registry CSE complete a pass, so an absent value cannot be "
            "distinguished from one that has not been written yet"
        )

    # Residue from a previous run would let stale values satisfy the control and
    # be read as this run's evidence.
    residual_values = observe.get("pre_run_residual") or []
    if residual_values:
        names = ", ".join(str(row.get("value_name")) for row in residual_values)
        problems.append(
            f"the endpoint carried policy values before this run began ({names}); "
            "the observation cannot be attributed to this run"
        )

    if not harness_ok:
        problems.append("deployed harness does not match its committed source")
    if dirty:
        problems.append("source tree is dirty; certification evidence requires a clean tree")
    return problems


def _client_environment_problems(observe: dict[str, Any]) -> list[str]:
    """Environment-spec rule 6: a lane that applies policy to a client says so."""
    environment = observe.get("environment") or {}
    build = str(environment.get("build") or "")
    problems: list[str] = []
    if not build or build == CLIENT_NOT_TESTED:
        problems.append(
            "the observation half recorded no real client build; this lane applies "
            "policy to a client and must not fall back to the not-tested sentinel"
        )
    elif _build_family(build) != FROZEN_ENVIRONMENT.client_build_family:
        problems.append(
            f"client build family {_build_family(build)!r} is not the frozen "
            f"{FROZEN_ENVIRONMENT.client_build_family!r}"
        )
    locale = str(environment.get("locale") or "")
    if locale != FROZEN_ENVIRONMENT.locale:
        problems.append(f"client locale {locale!r} is not the frozen {FROZEN_ENVIRONMENT.locale!r}")
    return problems


def _control_problems(observe: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    """Reasons the experiment did not run, as distinct from Studio being wrong.

    The control value is written by exactly one GPO, conflicts with nothing and
    is filtered by nothing. Its absence means policy did not reach the endpoint;
    every other row is then explained by that and says nothing about the model.
    """
    problems: list[str] = []
    if not observe.get("control_present"):
        problems.append(
            f"the control value {expected.get('control_value_name')!r} "
            f"(written by {expected.get('control_gpo')!r}, unconflicted and unfiltered) "
            "is absent: policy did not reach the endpoint, so no disagreement in this "
            "run is attributable to rsop.py"
        )
    return problems


def _compare(
    prediction: dict[str, Any],
    observe: dict[str, Any],
    symbolic: dict[str, str],
) -> dict[str, Any]:
    """Diff the prediction against Windows. Every difference is a finding."""
    predicted_applied = sorted(prediction.get("applied_gpos") or [])

    # Only GPOs this run created are considered. The estate may carry unrelated
    # policy (the Default Domain Policy always applies), and a lane that counted
    # it would report a disagreement that is really a scoping error.
    observed_applied = sorted(
        symbolic[name] for name in (observe.get("applied_gpos") or []) if name in symbolic
    )
    observed_foreign = sorted(
        name for name in (observe.get("applied_gpos") or []) if name not in symbolic
    )

    applied_only_predicted = sorted(set(predicted_applied) - set(observed_applied))
    applied_only_observed = sorted(set(observed_applied) - set(predicted_applied))

    predicted_winners = {
        str(row["value_name"]): row for row in (prediction.get("winners") or [])
    }
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
        "predicted_applied": predicted_applied,
        "observed_applied": observed_applied,
        "observed_foreign_gpos": observed_foreign,
        "applied_only_predicted": applied_only_predicted,
        "applied_only_observed": applied_only_observed,
        "predicted_winners": {name: str(row["value"]) for name, row in predicted_winners.items()},
        "observed_winners": observed_winners,
        "value_findings": value_findings,
        "agrees": (
            not applied_only_predicted and not applied_only_observed and not value_findings
        ),
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

    # The prediction is read from the candidate root -- the copy built before
    # the run -- and never from anything the guests returned. A prediction the
    # experiment could have influenced is not a prediction.
    prediction = _load(args.candidate_root / "prediction.json")
    expected = _load(args.candidate_root / "expected.json")

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

    symbolic = _symbolic_map(author)
    lane_problems = _lane_validity(author, cleanup, observe, harness_ok, dirty)
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
        # The prediction was wrong. That is a result, not an error: this lane
        # exists to find out whether rsop.py is right, and "no" is an answer.
        state = "finding"

    verdict = {
        "schema_version": 1,
        "work_package": "WP-6B",
        "scope": "computer",
        "run_id": observe.get("run_id"),
        "author_run_id": author.get("run_id"),
        "state": state,
        "passed": state == "pass",
        "transport": args.transport,
        "scenario_id": expected.get("scenario_id"),
        "lane_problems": lane_problems,
        "control_problems": control_problems,
        "comparison": comparison,
        "settle_attempts": observe.get("settle_attempts"),
        "cse_completed": observe.get("cse_completed"),
        "environment": {
            "client": observe.get("environment"),
            "server": author.get("environment"),
        },
        "source": {"commit": commit, "dirty": dirty, "files": source_hashes},
        "harness_matches_source": harness_ok,
    }

    output = run_dir / "rsop-verdict.json"
    output.write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if state == "pass" and not args.no_tag:
        try:
            tag_evidence_commit(repo_root, str(observe.get("run_id")), commit)
        except OracleEvidenceError as error:
            # A pass that cannot be tagged must not leave a durable verdict
            # claiming a binding that does not exist.
            verdict["state"] = "lane-failure"
            verdict["passed"] = False
            verdict["lane_problems"] = [*lane_problems, f"could not tag evidence commit: {error}"]
            output.write_text(
                json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(f"finalize: tagging failed, downgraded to lane-failure: {error}", file=sys.stderr)
            return 1

    print(f"\nrun {observe.get('run_id')}: state={state} (source {commit}, dirty={dirty})")
    for problem in lane_problems + control_problems:
        print(f"  ! {problem}")
    if comparison:
        for finding in comparison["value_findings"]:
            print(
                f"  FINDING {finding['value_name']}: predicted {finding['predicted']!r}, "
                f"observed {finding['observed']!r} ({finding['kind']})"
            )
        for name in comparison["applied_only_predicted"]:
            print(f"  FINDING {name}: predicted applied, Windows did not apply it")
        for name in comparison["applied_only_observed"]:
            print(f"  FINDING {name}: Windows applied it, prediction did not")
    return 0 if state == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
