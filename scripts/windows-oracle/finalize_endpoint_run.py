#!/usr/bin/env python3
"""Render the verdict for a Plan 033 two-guest endpoint run.

The Windows halves capture evidence; this renders the verdict, because the
verdict needs the git repository and the frozen qualification profile and
neither exists on a lab guest.

The endpoint lane exists to answer questions no round trip can answer: GPMC's
report echoes back what Studio wrote, so only the client-side extension's
behaviour on a real endpoint says whether a shape is *honoured*. That makes the
lane's own failure modes dangerous in a specific way — **an absent scheduled
task is the expected result for several rows**, so anything that silently
prevents tasks from being created reads exactly like the defect the lane is
looking for.

Three layers, and a run must clear each before the next means anything:

1. **Lane validity.** Both halves cleaned up, the GPO actually reached the
   client, the observation settled on evidence rather than on a deadline, the
   harness matches its source, and the tree is clean. A run failing any of
   these is a lane failure with no verdict at all.
2. **Controls.** The unfiltered row proves GPP scheduled tasks work on this
   endpoint at all; the native matching row proves the OS product code in the
   candidate actually matches this client; the native excluding row proves
   filters are evaluated. A control failure yields ``inconclusive`` — never a
   finding against Studio, because a broken control cannot distinguish "Studio
   wrote something wrong" from "the experiment did not run".
3. **Findings.** Only then are WI-018, WI-021, and the ``WINTHRESHOLD`` client
   collision read off the rows.

The client build is asserted here rather than left to the manifest parser. The
parser accepts the ``not-tested`` sentinel because it cannot tell which lane
produced a manifest; environment-spec rule 6 makes it the lane's job not to
claim endpoint evidence it did not gather. This lane applies policy to a client,
so a sentinel — or a client outside the frozen family — is a refusal.
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

#: Harness files deployed to the guests. Each retrieved copy is bound to the
#: committed source, so this set has to match what the lane actually deploys or
#: ``harness_matches_source`` means nothing. Both halves appear because both
#: execute on Windows; the driver and the transport execute on the controller.
DEPLOYED_FILES: dict[str, str] = {
    "run-endpoint-author.ps1": "scripts/windows-oracle/run-endpoint-author.ps1",
    "run-endpoint-observe.ps1": "scripts/windows-oracle/run-endpoint-observe.ps1",
}

#: Scripts that execute on the controller, where the source-tree copy *is* the
#: executed copy.
LOCAL_FILES: dict[str, str] = {
    "run-endpoint-oracle.sh": "scripts/windows-oracle/run-endpoint-oracle.sh",
    "psdirect.ps1": "scripts/windows-oracle/psdirect.ps1",
    "build-endpoint-candidate.py": "scripts/plan-033/build-endpoint-candidate.py",
}

#: The unfiltered row. If this task is absent, GPP scheduled tasks do not work
#: on this endpoint at all and every other row is uninterpretable.
CONTROL_UNFILTERED = "GPOStudio-EP2-A-nofilter"

#: The hand-written native excluding filter. Absent is correct; if it is
#: PRESENT, filters are not being evaluated and no filter row means anything.
CONTROL_NATIVE_EXCLUDING = "GPOStudio-EP2-E-native-control"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> Any:
    # utf-8-sig: PowerShell's Set-Content -Encoding UTF8 writes a BOM.
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _build_family(build: str) -> str:
    """The leading build number, which is what the frozen profile qualifies."""
    return build.strip().split(".")[0]


def _find_one(run_dir: Path, name: str) -> Path | None:
    matches = sorted(run_dir.rglob(name))
    return matches[0] if matches else None


def _row_map(observe: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["name"]: row for row in observe.get("observed_tasks", [])}


def _lane_validity(
    author: dict[str, Any],
    observe: dict[str, Any],
    verify: dict[str, Any] | None,
    harness_ok: bool,
    dirty: bool,
) -> list[str]:
    """Reasons this run cannot produce a verdict at all."""
    problems: list[str] = []

    # The observation half unregisters tasks while the GPO is still linked, so
    # its own absence claim is provisional -- any refresh before the authoring
    # half unlinks would recreate every GPP Replace item. Only the post-teardown
    # verify phase can say the endpoint is durably clean, so its absence is a
    # lane failure rather than a missing nicety.
    if verify is None:
        problems.append(
            "no post-teardown verification: the observation half's cleanup claim is "
            "provisional (the GPO was still linked when it ran) and nothing has "
            "confirmed the endpoint is durably clean"
        )
    else:
        if not verify.get("tasks_removed"):
            residual = verify.get("residual_tasks") or []
            problems.append(
                "tasks survived teardown on the endpoint: "
                f"{', '.join(residual) or 'unknown'}"
            )
        if verify.get("gpo_still_applied"):
            problems.append("the GPO is still applied to the endpoint after teardown")
        for error in verify.get("errors") or []:
            problems.append(f"post-teardown verification: {error}")

    cleanup = author.get("cleanup") or {}
    if not cleanup:
        problems.append("authoring half recorded no cleanup: it never reached teardown")
    else:
        for key in ("computer_restored", "gpo_removed", "ou_removed"):
            if not cleanup.get(key):
                problems.append(f"authoring cleanup incomplete: {key} is false")
        for error in cleanup.get("errors") or []:
            problems.append(f"authoring cleanup error: {error}")

    if not (observe.get("cleanup") or {}).get("tasks_removed"):
        residual = (observe.get("cleanup") or {}).get("residual_tasks") or []
        problems.append(
            f"observation left scheduled tasks behind: {', '.join(residual) or 'unknown'}"
        )

    if not observe.get("gpo_applied"):
        problems.append(
            "the client never reported the GPO applied; nothing was measured, "
            "and every absent task is unexplained rather than negative"
        )
    if not observe.get("observation_settled"):
        problems.append(
            "the observation did not settle: the Scheduled Tasks CSE was not seen "
            "completing a pass after the GPO arrived, so an absent task cannot be "
            "distinguished from one the CSE has not created yet"
        )
    if observe.get("error"):
        problems.append(f"observation half reported: {observe['error']}")

    if not harness_ok:
        problems.append("deployed harness does not match its committed source")
    if dirty:
        problems.append("source tree is dirty; certification evidence requires a clean tree")
    return problems


def _client_environment_problems(observe: dict[str, Any]) -> list[str]:
    """Environment-spec rule 6, enforced where the spec says it belongs."""
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


def _control_problems(rows: dict[str, dict[str, Any]], expected: dict[str, Any]) -> list[str]:
    """Reasons the experiment did not run, as distinct from Studio being wrong."""
    problems: list[str] = []

    unfiltered = rows.get(CONTROL_UNFILTERED)
    if unfiltered is None:
        problems.append(f"control row {CONTROL_UNFILTERED} was not observed")
    elif not unfiltered["present"]:
        problems.append(
            f"{CONTROL_UNFILTERED} is absent: GPP scheduled tasks do not reach this "
            "endpoint at all, so no other row is interpretable"
        )

    native_excluding = rows.get(CONTROL_NATIVE_EXCLUDING)
    if native_excluding is not None and native_excluding["present"]:
        problems.append(
            f"{CONTROL_NATIVE_EXCLUDING} is PRESENT: a hand-written native excluding "
            "filter was not honoured, so filter evaluation itself is not working and "
            "no filter row distinguishes Studio from the platform"
        )

    # The vocabulary control. Named in the candidate rather than hardcoded, so
    # the experiment and its verdict cannot drift apart.
    control_name = expected.get("vocabulary_control_task")
    if control_name:
        control = rows.get(control_name)
        if control is None:
            problems.append(f"vocabulary control row {control_name} was not observed")
        elif not control["present"]:
            match_version = expected.get("match_os_version", "the matching product code")
            problems.append(
                f"{control_name} is absent: a hand-written NATIVE filter for "
                f"{match_version!r} did not match this client either, so the product "
                "code is wrong for this OS and the matching-filter rows say nothing "
                "about Studio"
            )
    return problems


def _findings(rows: dict[str, dict[str, Any]], expected: dict[str, Any]) -> list[dict[str, Any]]:
    """The questions the lane exists to answer, read off the settled rows."""

    def state(name: str) -> bool | None:
        row = rows.get(name)
        return None if row is None else bool(row["present"])

    findings: list[dict[str, Any]] = []

    scalar = state("GPOStudio-EP2-F-scalar-shape")
    findings.append(
        {
            "id": "WI-018",
            "question": (
                "does a scalar-authored TaskV2 create a task, now that the writer "
                "synthesizes an embedded <Task> payload?"
            ),
            "observed": {"GPOStudio-EP2-F-scalar-shape": scalar},
            "answer": None if scalar is None else ("honoured" if scalar else "inert"),
        }
    )

    match = state("GPOStudio-EP2-B-os-match")
    exclude = state("GPOStudio-EP2-C-os-exclude")
    negated = state("GPOStudio-EP2-D-os-negated")
    if None in (match, exclude, negated):
        filter_answer = None
    elif match and not exclude and negated:
        filter_answer = "evaluated"
    elif not match and not exclude:
        # Absent in BOTH polarities is the phase-1 signature: a filter the CSE
        # cannot parse fails closed, which makes the item apply nowhere.
        filter_answer = "fails-closed"
    elif match and exclude:
        filter_answer = "ignored"
    else:
        filter_answer = "mixed"
    findings.append(
        {
            "id": "WI-021",
            "question": "is a Studio-authored OS filter actually evaluated by the CSE?",
            "observed": {
                "GPOStudio-EP2-B-os-match": match,
                "GPOStudio-EP2-C-os-exclude": exclude,
                "GPOStudio-EP2-D-os-negated": negated,
            },
            "answer": filter_answer,
        }
    )

    # The corpus matrix INFERS that WINTHRESHOLD covers Windows 11, from a
    # dropdown capture that offered no Windows 11 entry at all; the manual
    # evidence queue still carries it as wanting endpoint proof. The estate's
    # client is Windows 11, so this run can settle it. Both halves are needed: a
    # matching client code that matches, and a server code that does not.
    client_code = state(expected.get("vocabulary_control_task", ""))
    server_code = state("GPOStudio-EP2-K-os-server-code")
    if client_code is None or server_code is None:
        collision_answer = None
    elif client_code and not server_code:
        collision_answer = "confirmed"
    elif client_code and server_code:
        collision_answer = "product-code-not-discriminating"
    else:
        collision_answer = "refuted"
    findings.append(
        {
            "id": "OS-VOCABULARY",
            "question": (
                f"does {expected.get('match_os_version')!r} match a Windows 11 client "
                f"while {expected.get('non_match_os_version')!r} does not? "
                "(corpus-matrix inference, previously unproven at an endpoint)"
            ),
            "observed": {
                expected.get("vocabulary_control_task", "?"): client_code,
                "GPOStudio-EP2-K-os-server-code": server_code,
            },
            "answer": collision_answer,
        }
    )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=_REPO_ROOT)
    # Recorded rather than chosen: this lane is two-guest and PowerShell Direct
    # is the only transport that reaches an estate with no guest networking.
    # The argument exists so the verdict states how it was produced.
    parser.add_argument("--transport", choices=["psdirect"], default="psdirect")
    parser.add_argument(
        "--no-tag",
        action="store_true",
        help=(
            "do not create the evidence/<run-id> tag for a passing run. The tag "
            "preserves the source commit that squash-merging would otherwise "
            "orphan (issue #22); skip it only when tagging is handled elsewhere."
        ),
    )
    args = parser.parse_args(argv)
    run_dir = args.run_dir.resolve()
    repo_root = args.repo_root.resolve()

    author_path = _find_one(run_dir / "author", "author-result.json")
    observe_path = _find_one(run_dir / "observe", "observe-result.json")
    if author_path is None or observe_path is None:
        missing = "author-result.json" if author_path is None else "observe-result.json"
        print(f"finalize refused: {missing} is not in {run_dir}", file=sys.stderr)
        return 1
    author = _load(author_path)
    observe = _load(observe_path)
    verify_path = _find_one(run_dir / "verify", "verify-result.json")
    verify = _load(verify_path) if verify_path is not None else None
    expected = _load(args.candidate_root / "expected.json")

    source_hashes: dict[str, str] = {}
    harness_ok = True
    for name, source in {**DEPLOYED_FILES, **LOCAL_FILES}.items():
        src_hash = _sha256(repo_root / source)
        source_hashes[name] = src_hash
        evidence = run_dir / "deployed" / name if name in DEPLOYED_FILES else run_dir / name
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

    rows = _row_map(observe)
    lane_problems = _lane_validity(author, observe, verify, harness_ok, dirty)
    lane_problems += _client_environment_problems(observe)
    control_problems = _control_problems(rows, expected) if not lane_problems else []
    findings = _findings(rows, expected) if not (lane_problems or control_problems) else []

    if lane_problems:
        state = "lane-failure"
    elif control_problems:
        state = "inconclusive"
    else:
        state = "pass"

    # A row set that disagrees with its own expectations is a finding, not a
    # lane failure: that is what the lane is for. It is recorded so a reviewer
    # sees at a glance which rows moved.
    unexpected = [
        {
            "name": row["name"],
            "expected": row["expected_if_defects_real"],
            "observed": "present" if row["present"] else "absent",
            "isolates": row["isolates"],
        }
        for row in observe.get("observed_tasks", [])
        if (row["expected_if_defects_real"] == "present") != bool(row["present"])
    ]

    verdict = {
        "schema_version": 1,
        "work_package": "WP-6-endpoint",
        "run_id": observe.get("run_id"),
        "author_run_id": author.get("run_id"),
        "state": state,
        "passed": state == "pass",
        "transport": args.transport,
        "topology": {
            "author_guest": author.get("author_computer"),
            "endpoint_guest": observe.get("computer"),
            "domain_controller": author.get("domain_controller"),
            "target_gpo": observe.get("target_gpo"),
        },
        "environment": {
            "server": author.get("environment"),
            "client": observe.get("environment"),
        },
        "post_teardown_verification": verify,
        "settling": {
            "gpo_applied": observe.get("gpo_applied"),
            "apply_attempts": observe.get("apply_attempts"),
            "cse_completed": observe.get("cse_completed"),
            "settle_attempts": observe.get("settle_attempts"),
            "observation_settled": observe.get("observation_settled"),
        },
        "lane_problems": lane_problems,
        "control_problems": control_problems,
        "findings": findings,
        "rows": observe.get("observed_tasks", []),
        "unexpected_rows": unexpected,
        "harness_matches_source": harness_ok,
        "source": {"commit": commit, "dirty": dirty, "files": source_hashes},
        "artifacts": {
            str(path.relative_to(run_dir)): _sha256(path)
            for path in sorted(run_dir.rglob("*"))
            if path.is_file() and path.name != "verification.json"
        },
    }

    # Bind before recording, for the reason WP-1B's finalizer does: the tag is
    # what makes source.commit checkable from a fresh clone, so a run that
    # cannot be tagged must not leave a durable "passed" file claiming a binding
    # it does not have.
    tag_outcome: str | None = None
    if state == "pass" and not args.no_tag:
        try:
            tag_outcome = tag_evidence_commit(repo_root, str(observe["run_id"]), commit)
        except OracleEvidenceError as exc:
            print(f"evidence tag failed: {exc}", file=sys.stderr)
            return 1

    (run_dir / "verification.json").write_text(
        json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    for problem in lane_problems:
        print(f"LANE FAILURE  {problem}")
    for problem in control_problems:
        print(f"INCONCLUSIVE  {problem}")
    for row in observe.get("observed_tasks", []):
        mark = "present" if row["present"] else "absent "
        matched = (row["expected_if_defects_real"] == "present") == bool(row["present"])
        agree = "  " if matched else "!!"
        print(f"{agree} {mark}  {row['name']:34s} {row['isolates']}")
    for finding in findings:
        print(f"FINDING {finding['id']}: {finding['answer']}")
    print(f"\nrun {observe.get('run_id')}: state={state} (source {commit}, dirty={dirty})")
    if tag_outcome is not None:
        print(f"EVIDENCE_TAG={tag_outcome}")
    return 0 if state == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
