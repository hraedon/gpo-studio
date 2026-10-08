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
    assert_bound_source_bytes,
    manifest_bound_source,
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
    "finalize_endpoint_run.py": "scripts/windows-oracle/finalize_endpoint_run.py",
    "oracle_evidence.py": "src/gpo_studio/oracle_evidence.py",
    # Batch 2: the archive writer and product modules this lane's candidate
    # bytes flow through, so editing them stales the verdict (review P1).
    "export.py": "src/gpo_studio/export.py",
    "deterministic_zip.py": "src/gpo_studio/deterministic_zip.py",
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


#: The candidate artifacts this lane's verdict rests on, relative to
#: ``--candidate-root``.  ``expected.json`` is what the finalizer grades against
#: and ``candidate.zip`` is what the guest imported, so a verdict that names
#: neither hash asserts a comparison nobody can re-check (WI-025).  Named rather
#: than discovered so that a candidate root missing one of them is a lane
#: failure instead of a shorter hash block.
REQUIRED_CANDIDATE_FILES: tuple[str, ...] = ("candidate.zip", "expected.json")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate_hashes(candidate_root: Path) -> dict[str, str]:
    """SHA-256 of EVERY file under the candidate root, by relative path.

    WI-025. The endpoint lane always took ``--candidate-root``, so it never had
    the guest-supplied-expectation defect WP-6B was fixed for -- but it recorded
    no candidate hashes either, and a verdict that names the artifact it
    compared against without hashing it is a comparison nobody can re-check.

    Everything under the root is hashed rather than a fixed list, because the
    omission this closes is precisely the one nobody notices: a file the lane
    consumes and the verdict does not mention. ``REQUIRED_CANDIDATE_FILES`` then
    says which of them must be THERE, so an incomplete root fails the lane
    rather than producing a smaller block that still looks complete.
    """
    return {
        path.relative_to(candidate_root).as_posix(): _sha256(path)
        for path in sorted(candidate_root.rglob("*"))
        if path.is_file()
    }


def _candidate_problems(candidate_root: Path, hashes: dict[str, str]) -> list[str]:
    """Required candidate artifacts the root does not have.

    A REFUSAL rather than a recorded problem, because there is nothing to grade
    without them: `expected.json` supplies every expectation the comparison
    uses. Checked before that file is read, so the check can actually fail --
    reading it first would make this branch unreachable, which is the shape of
    guard this project has been bitten by before.
    """
    return [
        f"candidate root {candidate_root} has no {name}: the verdict would bind "
        "an artifact set that is missing the file it graded against"
        for name in REQUIRED_CANDIDATE_FILES
        if name not in hashes
    ]


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
    rows = observe.get("observed_tasks")
    if not isinstance(rows, list):
        return {}
    return {
        row["name"]: row
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    }


def _flag(record: dict[str, Any], key: str) -> bool | None:
    """*record[key]* if it is a real bool, else None.

    Every lane-validity field is read through this or an equivalent, so a
    MISSING field is never the same as an observed ``false``. The guest
    writes each of them explicitly; a record without one is truncated or
    from another harness, and that is a lane failure, not a negative.
    """
    value = record.get(key)
    return value if isinstance(value, bool) else None


def _row_problems(observe: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    """Every candidate task must be observed exactly once, and answered.

    The controls and findings read a handful of named rows; the rest of the
    candidate is the experiment. A row the observation dropped would leave
    its question silently unanswered while the controls still passed, so the
    observed row set must be EXACTLY the candidate's task set, each row
    carrying the candidate's own expectation and a real ``present`` bool.
    """
    problems: list[str] = []
    tasks = expected.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return ["the candidate names no tasks; there is no experiment to grade"]
    wanted: dict[str, dict[str, Any]] = {}
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("name"), str):
            return ["the candidate's task list has an entry with no name"]
        if task["name"] in wanted:
            return [f"the candidate names task {task['name']} twice"]
        wanted[task["name"]] = task

    rows = observe.get("observed_tasks")
    if not isinstance(rows, list):
        return ["the observation recorded no observed_tasks list"]
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            problems.append("an observed row has no name")
            continue
        name = row["name"]
        if name in seen:
            problems.append(f"task {name} was observed twice")
            continue
        seen.add(name)
        task = wanted.get(name)
        if task is None:
            problems.append(f"observed task {name} is not in the candidate")
            continue
        if not isinstance(row.get("present"), bool):
            problems.append(f"task {name} has no present/absent answer")
        for key in ("expected_if_defects_real", "isolates"):
            if row.get(key) != task.get(key):
                problems.append(
                    f"task {name} records {key}={row.get(key)!r}, but the candidate "
                    f"says {task.get(key)!r}"
                )
    for name in wanted:
        if name not in seen:
            problems.append(
                f"candidate task {name} was never observed, so the question it "
                "isolates is unanswered"
            )
    return problems


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
    target_gpo = observe.get("target_gpo")
    if not isinstance(target_gpo, str) or not target_gpo:
        problems.append("the observation half recorded no target GPO")

    if verify is None:
        problems.append(
            "no post-teardown verification: the observation half's cleanup claim is "
            "provisional (the GPO was still linked when it ran) and nothing has "
            "confirmed the endpoint is durably clean"
        )
    else:
        problems.extend(_verify_problems(verify, target_gpo))

    cleanup = author.get("cleanup")
    if not isinstance(cleanup, dict) or not cleanup:
        problems.append("authoring half recorded no cleanup: it never reached teardown")
    else:
        for key in ("computer_restored", "gpo_removed", "ou_removed"):
            flag = _flag(cleanup, key)
            if flag is None:
                problems.append(f"authoring cleanup did not record {key}")
            elif not flag:
                problems.append(f"authoring cleanup incomplete: {key} is false")
        problems.extend(_error_list_problems(cleanup, "authoring cleanup"))
    if _flag(author, "setup_completed") is not True:
        problems.append("the authoring half did not record a completed setup")
    if "error" not in author:
        problems.append("the authoring half recorded no error field")
    elif author["error"] is not None:
        problems.append(f"authoring half reported: {author['error']}")
    if author.get("target_gpo") != target_gpo:
        problems.append(
            f"the authoring half's target GPO {author.get('target_gpo')!r} is not the "
            f"one the observation half measured ({target_gpo!r})"
        )

    observe_cleanup = observe.get("cleanup")
    if not isinstance(observe_cleanup, dict):
        problems.append("observation half recorded no cleanup")
    else:
        removed = _flag(observe_cleanup, "tasks_removed")
        if removed is not True:
            residual = observe_cleanup.get("residual_tasks") or []
            problems.append(
                "observation left scheduled tasks behind: "
                f"{', '.join(map(str, residual)) or 'unknown'}"
                if removed is False
                else "observation cleanup did not record tasks_removed"
            )
        problems.extend(_error_list_problems(observe_cleanup, "observation cleanup"))

    if _flag(observe, "gpo_applied") is not True:
        problems.append(
            "the client never reported the GPO applied; nothing was measured, "
            "and every absent task is unexplained rather than negative"
        )
    if _flag(observe, "observation_settled") is not True:
        problems.append(
            "the observation did not settle: the Scheduled Tasks CSE was not seen "
            "completing a pass after the GPO arrived, so an absent task cannot be "
            "distinguished from one the CSE has not created yet"
        )
    if "error" not in observe:
        problems.append("the observation half recorded no error field")
    elif observe["error"] is not None:
        problems.append(f"observation half reported: {observe['error']}")

    if not harness_ok:
        problems.append("deployed harness does not match its committed source")
    if dirty:
        problems.append("source tree is dirty; certification evidence requires a clean tree")
    return problems


def _error_list_problems(record: dict[str, Any], label: str) -> list[str]:
    """A record's ``errors`` must be a list that is present and empty."""
    errors = record.get("errors")
    if not isinstance(errors, list):
        return [f"{label} recorded no errors list"]
    return [f"{label} error: {error}" for error in errors]


def _verify_problems(verify: dict[str, Any], target_gpo: object) -> list[str]:
    """The post-teardown phase's durable-clean claim, field by field.

    Every field is required. A missing ``gpo_still_applied`` is not "not
    applied": the guest only measures it when it was told which GPO to look
    for, so the record must also name the same target GPO the observation
    measured, and the refresh that makes the claim durable must have run.
    """
    problems: list[str] = []
    if verify.get("target_gpo") != target_gpo or not verify.get("target_gpo"):
        problems.append(
            f"post-teardown verification checked target GPO {verify.get('target_gpo')!r}, "
            f"not the measured {target_gpo!r}; its still-applied answer is about "
            "nothing"
        )
    exit_code = verify.get("gpupdate_exit_code")
    if type(exit_code) is not int or exit_code != 0:
        problems.append(
            f"post-teardown policy refresh did not succeed (exit {exit_code!r}), so "
            "its absence claim is not durable"
        )
    removed = _flag(verify, "tasks_removed")
    residual = verify.get("residual_tasks")
    if removed is None:
        problems.append("post-teardown verification did not record tasks_removed")
    elif not removed or residual:
        problems.append(
            "tasks survived teardown on the endpoint: "
            f"{', '.join(map(str, residual or [])) or 'unknown'}"
        )
    if not isinstance(residual, list):
        problems.append("post-teardown verification recorded no residual_tasks list")
    applied = _flag(verify, "gpo_still_applied")
    if applied is None:
        problems.append(
            "post-teardown verification did not record gpo_still_applied, so nothing "
            "shows the removed policy stopped applying"
        )
    elif applied:
        problems.append("the GPO is still applied to the endpoint after teardown")
    problems.extend(_error_list_problems(verify, "post-teardown verification"))
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
    if native_excluding is None:
        problems.append(f"control row {CONTROL_NATIVE_EXCLUDING} was not observed")
    elif native_excluding["present"]:
        problems.append(
            f"{CONTROL_NATIVE_EXCLUDING} is PRESENT: a hand-written native excluding "
            "filter was not honoured, so filter evaluation itself is not working and "
            "no filter row distinguishes Studio from the platform"
        )

    # The vocabulary control. Named in the candidate rather than hardcoded, so
    # the experiment and its verdict cannot drift apart -- but REQUIRED, not
    # optional. It is the only thing standing between a wrong product code and a
    # fabricated Studio defect: without it, a matching filter that misses
    # because the code is wrong for this OS reads identically to one that misses
    # because Studio wrote it wrong. A candidate that does not name a control
    # cannot be interpreted, so its absence is a control problem in itself
    # rather than a check that quietly switches off.
    control_name = expected.get("vocabulary_control_task")
    if not control_name:
        problems.append(
            "the candidate names no vocabulary_control_task; the matching-filter "
            "rows cannot be attributed to Studio without a native control"
        )
    else:
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


def _grade(
    author: dict[str, Any],
    observe: dict[str, Any],
    verify: dict[str, Any] | None,
    expected: dict[str, Any],
    harness_ok: bool,
    dirty: bool,
) -> tuple[str, list[str], list[str], list[dict[str, Any]]]:
    """The verdict's three layers, exactly as `main` records them.

    Returns (state, lane_problems, control_problems, findings).
    """
    rows = _row_map(observe)
    lane_problems = _lane_validity(author, observe, verify, harness_ok, dirty)
    lane_problems += _client_environment_problems(observe)
    lane_problems += _row_problems(observe, expected)
    control_problems = _control_problems(rows, expected) if not lane_problems else []
    findings = _findings(rows, expected) if not (lane_problems or control_problems) else []
    # A finding the rows could not answer is a question the run did not settle.
    # With the row set complete this cannot happen for the current candidate,
    # but a candidate that dropped a task a finding reads would otherwise pass
    # with that question recorded as None.
    for finding in findings:
        if finding["answer"] is None:
            lane_problems.append(f"finding {finding['id']} is unanswered by the observed rows")
    if lane_problems:
        state = "lane-failure"
    elif control_problems:
        state = "inconclusive"
    else:
        state = "pass"
    return state, lane_problems, control_problems, findings


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
    try:
        assert_bound_source_bytes(repo_root, {**DEPLOYED_FILES, **LOCAL_FILES}.values())
    except OracleEvidenceError as exc:
        print(f"finalize refused: {exc}", file=sys.stderr)
        return 1

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

    # WI-025. Bind the candidate BEFORE anything is graded against it, and
    # before `expected.json` is read -- see `_candidate_problems`.
    candidate_hashes = _candidate_hashes(args.candidate_root)
    candidate_problems = _candidate_problems(args.candidate_root, candidate_hashes)
    if candidate_problems:
        for problem in candidate_problems:
            print(f"finalize refused: {problem}", file=sys.stderr)
        return 1
    expected = _load(args.candidate_root / "expected.json")

    # WI-062: controller-side files are bound by (commit, path, sha256) from
    # the source-tree copy that ran -- no byte copy rides in the pack, since
    # git at the commit holds the bytes and assert_bound_source_bytes proved
    # tree, index and HEAD agree.
    bound_local = manifest_bound_source(repo_root, LOCAL_FILES)
    source_hashes: dict[str, str] = {
        name: entry["sha256"] for name, entry in bound_local.items()
    }
    harness_ok = True
    for name, source in DEPLOYED_FILES.items():
        src_hash = _sha256(repo_root / source)
        source_hashes[name] = src_hash
        evidence = run_dir / "deployed" / name
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

    state, lane_problems, control_problems, findings = _grade(
        author, observe, verify, expected, harness_ok, dirty
    )
    observed_rows = [
        row for row in (observe.get("observed_tasks") or []) if isinstance(row, dict)
    ]

    # A row set that disagrees with its own expectations is a finding, not a
    # lane failure: that is what the lane is for. It is recorded so a reviewer
    # sees at a glance which rows moved.
    unexpected = [
        {
            "name": row.get("name"),
            "expected": row.get("expected_if_defects_real"),
            "observed": "present" if row.get("present") else "absent",
            "isolates": row.get("isolates"),
        }
        for row in observed_rows
        if (row.get("expected_if_defects_real") == "present") != bool(row.get("present"))
    ]

    verdict = {
        "schema_version": 2,
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
        "rows": observe.get("observed_tasks"),
        "unexpected_rows": unexpected,
        "harness_matches_source": harness_ok,
        "source": {
            "commit": commit,
            "dirty": dirty,
            "files": source_hashes,
            # WI-062: every bound file's repository path, and the names whose
            # bytes the pack actually carries. Controller-side files are
            # verified against git at the commit, not against pack copies.
            "paths": {**DEPLOYED_FILES, **LOCAL_FILES},
            "banked_copies": sorted(DEPLOYED_FILES),
        },
        # The INPUT side of the comparison, hashed. `artifacts` below covers
        # what the run produced; without this block the thing it was graded
        # against was the one unhashed input in the verdict (WI-025).
        "candidate": candidate_hashes,
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
    for row in observed_rows:
        mark = "present" if row.get("present") else "absent "
        matched = (row.get("expected_if_defects_real") == "present") == bool(row.get("present"))
        agree = "  " if matched else "!!"
        print(f"{agree} {mark}  {row.get('name')!s:34s} {row.get('isolates')}")
    for finding in findings:
        print(f"FINDING {finding['id']}: {finding['answer']}")
    print(f"\nrun {observe.get('run_id')}: state={state} (source {commit}, dirty={dirty})")
    if tag_outcome is not None:
        print(f"EVIDENCE_TAG={tag_outcome}")
    return 0 if state == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
