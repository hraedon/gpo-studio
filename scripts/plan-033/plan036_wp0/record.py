"""Parts 1-3 together: derive, replay, re-grade, select, and write the records.

`derive_part1` is part 1 at a tree; `run` is parts 1 and 2 plus D-9's
selection at a target commit; `successor_record` assembles the successor
record; `write_records` writes the successor record and the pin, and only
after a full run that found no difference; `verify_pin` is the release-only
closure gate's re-derivation (`tests/test_release_closure_gate.py` runs it).

The successor record carries `DESIGN_NOTES`: the readings of the plan this
tooling makes (WP-0 always selected; the TYPE_CHECKING interpretation; the
D-9 deletion and data-file rules), so the record names the rules it was
produced under.

**The pin cannot be refreshed by regenerating it** (DeepSeek round-2 N2-9):
it carries the SHA-256 of the successor record it was derived from, and the
successor record carries the replay and re-grade results. `write_records`
refuses to write either if any lane's replay or re-grade differs, if the scan
fails anywhere, or if any declaration is unused; the gate refuses a pin whose
successor digest does not match the committed successor record.

**The pin is bound to the record's content, not only its digest** (review
round 1, Sol B1: a pin over a fabricated two-key successor record passed).
The gate requires the committed successor record to be complete and all-equal
(`successor_problems`: every applicable replay identical, every re-grade equal
at the target and at the verdict's own commit, no failing scan site, every
environment built by the recorded uv method), rebuilds the pin from it and
refuses any pin that differs (a pin is never edited by hand), and holds the
record's lane set, verdict commits, closures, resources, floor, memberships,
external modules and declared edges to the re-derivation at the gated tree.

**What the gate re-executes, and the trust boundary** (review round 2, Sol B1:
a hand-written "complete" record and its pin still passed, because mutual
consistency of two files a committer writes is not evidence). The record's
claims fall in two halves:

- *Target-side claims* are re-executed by the gate itself (`reexecute_target`),
  offline, in the CI job's own synced environment, at the record's target
  commit: every builder replay (against the record's and the banked candidate
  hashes), every canonical re-grade over the banked packs, and D-9's selection.
  Any difference fails. This is the claim-bearing half: whether the evidence
  is still true at the release commit is checked mechanically, not trusted.
- *Historical claims* (the re-grade at each verdict's own commit, and the
  environments those ran in) are recorded attestations; re-running them would
  need each commit's own environment, which CI does not build. They are bound
  to content the repository holds (`_bindings`): lock digests to
  `git show <commit>:uv.lock`, candidate hashes and banked payload digests to
  the committed verdicts and packs, and every replay and re-grade entry names
  its commit.

The gate also holds the pin to the gated history (`history_problems`): the
target commit is the gated commit or an ancestor, no dependency changed after
it, and the plan's step 8 holds for the commit that added the pin.

A committer who fabricates the historical attestations from the repository's
own evidence is outside the gate's threat model: the review at banking covers
that. The gate's job is that the release commit's claims are re-verified.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import changed as changed_mod
from . import closure as closure_mod
from . import projection, replay, scan
from .lanes import Lane, live_lanes
from .lanes import controller_log as controller_log_value
from .repo import (
    LISTS_DIR,
    REPO_ROOT,
    Wp0Error,
    detached_worktree,
    git,
    rev_parse,
    sha256_bytes,
    sha256_file,
    show,
)
from .repo import tracked_files as repo_tracked

#: The lanes' interpreter (uv picks it for every lane environment); the
#: analysis default, and the only interpreter the gate runs under.
LANE_PYTHON = "3.14"
SUCCESSOR_RECORD = "docs/plan-033/release110-dependency-audit.json"
PIN_RECORD = "docs/plan-033/release110-closure-pin.json"
#: Design notes carried in every successor record: the readings of the plan
#: the tooling makes, so the record says under which rules it was produced.
DESIGN_NOTES = (
    "WP-0 is always selected. Its shipping finalizer binds the harness-input "
    "record to the commit it was deployed at and refuses finalization at any "
    "other commit, so its canonical re-grade at F cannot run without altering a "
    "recorded input. Exemption gate (c) therefore fails, the release-metadata "
    "line counts, and D-9 selects WP-0: the rule working as written (coordinator "
    "ruling, 2026-10-14; the plan's rule is unchanged). Cost: about one estate "
    "minute. This is the selection at steps 3 and 5, against WP-0's banked verdict. "
    "Step 6 re-runs WP-0 on the estate at F, so its new verdict's harness inputs name "
    "F; at step 7 that verdict is live, WP-0's re-grade at F runs and is equal, and "
    "with nothing changed between its verdict commit and F it is not selected. The "
    "record written at step 7 is therefore all-equal (review round 3, DeepSeek N3).",
    "Imports only under `if TYPE_CHECKING:` are outside the run-time closure, and "
    "only when the guard resolves to typing.TYPE_CHECKING or "
    "typing_extensions.TYPE_CHECKING (aliases included); any other name is a "
    "run-time import (an interpretation, coordinator ruling 2026-10-14).",
    "A pure deletion counts through the surviving statements adjacent to the "
    "deletion point in the deleted lines' own scope; a changed data file a lane "
    "reads (declared, or observed opened in the trace) selects the lane "
    "(coordinator ruling 2026-10-14, closing two gaps in D-9's wording).",
    "A file the verdict already binds that changed selects its lane through the "
    "existing freshness gate, listed with its own reason.",
    "The scan's argparse plugin and JSON-provenance proof type values mypy "
    "leaves Any; a value neither proves stays Any and fails unless declared. "
    "The JSON proof holds only inside a JSON-clean function, one in which no "
    "value that may alias decoded JSON is stored into, mutated, or handed to "
    "code the proof does not follow (review round 1).",
    "Part 3 refinement (coordinator ruling, 2026-10-14, review round 2): the "
    "release gate re-executes the record's target-side claims offline in the CI "
    "environment (builder replays, canonical re-grades over the banked packs, "
    "D-9's selection) and fails on any difference; the historical claims (re-grades "
    "at each verdict commit, and their environments) stay recorded attestations, "
    "bound to git and the committed packs. Fabricated historical attestations are "
    "outside the gate's threat model; the review at banking covers them.",
    "Known scan limitation (accepted in review round 4, pending the owner): truth "
    "tests and comparisons of a value typed only object, and nothing else, run that "
    "value's methods without a scan site (any other protocol method of such a value, "
    "a call among them, fails); covering them adds about 400 sites, and in these "
    "lanes such values come from repository code or decoded JSON (scan.py's module "
    "docstring).",
    "Every clone environment is built by replay.uv_environment: the commit's own "
    "lock synced without building anything, the build backend from that lock or, "
    "for a lock that locks none (before 547626b), from the tool tree's lock by "
    "hash, and the project built offline without isolation. Each environment's "
    "method, pins and installed set are recorded under `environments` (review "
    "round 1).",
)
CAVEAT = (
    "These verdicts were earned on the estate at their own commits (de9736e or "
    "9940561). At the target commit their candidates and grading reproduce "
    "offline, as recorded here; the estate did not re-run them, except the lanes "
    "in the selected set, which ran at the target commit."
)


def _dump(data: Any) -> bytes:
    return (json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


# --- part 1 --------------------------------------------------------------------------------


def lane_processes(
    root: Path, lane: Lane, lists: scan.Lists
) -> dict[str, closure_mod.ProcessClosure]:
    """The builder's and the finalizer's process closures, with declared loads."""
    processes: dict[str, closure_mod.ProcessClosure] = {}
    for role, entry in (("builder", lane.spec.builder), ("finalizer", lane.spec.finalizer)):
        if entry is None:
            continue
        front: tuple[str, ...] = ()
        loaded: tuple[str, ...] = ()
        for _ in range(16):
            process = closure_mod.derive_process(root, entry, front=front, loaded=loaded)
            modules = set(process.modules)
            new_front = tuple(sorted({
                d["reaches"]["sys_path_front"] for d in lists.declarations_of(modules, "sys-path")
            }))
            new_loaded = tuple(sorted({
                d["reaches"]["module"] for d in lists.declarations_of(modules, "module-load")
            }))
            if (new_front, new_loaded) == (front, loaded):
                break
            front, loaded = new_front, new_loaded
        else:  # pragma: no cover - declarations would have to cycle
            raise Wp0Error(f"{lane.name} {role}: declared loads do not reach a fixpoint")
        processes[role] = process
    return processes


def _membership(root: Path, reaches: dict[str, Any]) -> dict[str, str]:
    """{path: sha256} of every repository file an enumeration edge reaches.

    `glob` (a `Path.glob` pattern under `repo_dir`, exactly as the code
    enumerates it) or `pattern` + `recursive` (a name pattern); with
    `copies_parent_tree`, every file under each match's directory too (the
    code copies or reads those whole trees).
    """
    directory = root / reaches["repo_dir"]
    if "glob" in reaches:
        matches = sorted(path for path in directory.glob(reaches["glob"]))
    else:
        pattern = reaches.get("pattern", "*")
        walker = directory.rglob("*") if reaches.get("recursive") else directory.glob("*")
        matches = sorted(path for path in walker if fnmatch.fnmatch(path.name, pattern))
    files: set[Path] = set()
    for match in matches:
        if reaches.get("copies_parent_tree"):
            files.update(path for path in match.parent.rglob("*") if path.is_file())
        elif match.is_file():
            files.add(match)
    return {path.relative_to(root).as_posix(): sha256_file(path) for path in sorted(files)}


def _all_reaches(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """A declaration's `reaches`, plus each item of an optional `repo_reaches` list."""
    return [entry["reaches"], *entry.get("repo_reaches", [])]


def hand_tables(root: Path, lane: Lane, python: list[str] | None = None) -> dict[str, str]:
    """{name: repository path} the lane's finalizer tables bind at *root* (the floor)."""
    transport = "psdirect"
    tables = projection.finalizer_tables(python or [sys.executable], root, lane.spec.finalizer,
                                         transport)
    if "wp0" in tables:
        return {row[0]: row[2] for row in tables["wp0"]}
    return {**tables.get("deployed", {}), **tables.get("local", {})}


def derive_part1(
    root: Path, python_version: str, lists: scan.Lists | None = None, *,
    python: list[str] | None = None,
) -> dict[str, Any]:
    """Part 1 at the tree *root*: closures, the scan, resources, the floor."""
    lists = lists or scan.Lists.load()
    lanes = live_lanes(root)
    processes = {lane.name: lane_processes(root, lane, lists) for lane in lanes}
    union = sorted(
        {p for procs in processes.values() for proc in procs.values() for p in proc.modules}
    )
    analysis = scan.analyse(root, union, python_version)
    completeness = scan.assert_complete(analysis)
    sites = scan.scan_all(analysis)
    classified = scan.classify(sites, lists)
    unused = scan.unused_declarations(sites, lists, union)
    per_lane: dict[str, Any] = {}
    for lane in lanes:
        procs = processes[lane.name]
        modules = sorted({p for proc in procs.values() for p in proc.modules})
        lane_sites = [c for c in classified if c.site.module in modules]
        failing = [c for c in lane_sites if c.status in scan.FAILING]
        resources: dict[str, str] = {}
        memberships: dict[str, dict[str, str]] = {}
        for entry in lists.declarations_of(modules, "file-read"):
            for reaches in _all_reaches(entry):
                for path in reaches.get("repo_files", []):
                    resources[path] = sha256_file(root / path)
                if "repo_dir" in reaches:
                    key = json.dumps({k: v for k, v in reaches.items() if k != "outside_tree"},
                                     sort_keys=True)
                    memberships[key] = _membership(root, reaches)
                    resources.update(memberships[key])
        for entry in lists.declarations_of(modules, "enumerate"):
            for reaches in _all_reaches(entry):
                if "repo_dir" in reaches:
                    key = json.dumps({k: v for k, v in reaches.items() if k != "outside_tree"},
                                     sort_keys=True)
                    memberships[key] = _membership(root, reaches)
                    resources.update(memberships[key])
        floor = hand_tables(root, lane, python)
        per_lane[lane.name] = {
            "verdict": lane.verdict,
            "commit": lane.commit,
            "shape": lane.shape,
            "has_candidate": lane.has_candidate,
            "processes": {
                role: {
                    "entry": proc.entry,
                    "path_entries": list(proc.path_entries),
                    "modules": dict(sorted(proc.modules.items())),
                    "external": sorted(proc.external),
                    "type_only_imports": sorted(
                        {f"{importer}:{line} {module}" for importer, line, module in proc.type_only}
                    ),
                }
                for role, proc in procs.items()
            },
            "closure": {path: sha256_file(root / path) for path in modules},
            "resources": dict(sorted(resources.items())),
            "memberships": memberships,
            "floor": {path: sha256_file(root / path) for path in sorted(set(floor.values()))},
            "edges": sorted({c.site.key for c in lane_sites if c.status == "edge"}),
            "scan": {
                "counts": dict(sorted(Counter(c.status for c in lane_sites).items())),
                "failing": [
                    {**asdict(c.site), "status": c.status, "why": c.reason} for c in failing
                ],
                "platform_unreachable": sorted(
                    {f"{c.site.module}:{c.site.line} {c.site.guard}" for c in lane_sites
                     if c.status == "platform-unreachable"}
                ),
            },
        }
    return {
        "root": str(root),
        "python_version": python_version,
        "platform": scan.CONTROLLER_PLATFORM,
        "lists_sha256": lists.digest,
        "exemptions_sha256": sha256_file(LISTS_DIR / "exemptions.json"),
        "completeness": completeness,
        "unused_declarations": unused,
        "analysed_modules": union,
        "toolchain": toolchain(root, lists),
        "lanes": per_lane,
    }


def toolchain(root: Path, lists: scan.Lists) -> dict[str, Any]:
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    executables = sorted({
        entry["reaches"]["executable"]
        for entry in [*lists.edges.values(), *lists.groups]
        if entry["kind"] == "process"
    })
    return {
        "uv_lock_sha256": sha256_file(root / "uv.lock"),
        "requires_python": pyproject["project"]["requires-python"],
        "process_executables": executables,
    }


# --- parts 1 and 2 at a target, with selection --------------------------------------------


def run(
    repo: Path,
    target: str,
    workdir: Path,
    python_version: str,
    *,
    lanes_wanted: set[str] | None = None,
    launcher: str = "uv",
    evidence: Path | None = None,
) -> dict[str, Any]:
    """Parts 1 and 2 for every live lane at *target*, and D-9's selection.

    With *evidence* (the plan's step 7, review round 3, Sol B2): a directory of
    staged evidence files at their repository paths (the new packs, batch
    manifest and registry entries, prose corrections: everything the banking
    commit B adds besides the successor record and the pin). They are made live
    in an *evidence view*, a worktree of *target* with the files laid over it,
    from which lanes, verdicts and packs are read; the builders and finalizers
    still run in clean clones of *target*, so the run binds *target* exactly as
    the estate runs did. Evidence that touches any lane's dependency set is
    refused.
    """
    target = rev_parse(repo, target)
    lists = scan.Lists.load()
    exemptions = changed_mod.load_exemptions()
    staged = staged_evidence(evidence) if evidence is not None else {}
    clones = workdir / "clones"
    with replay.clone(repo, target, clones, "target-a", launcher=launcher) as clone_a, \
            replay.clone(repo, target, clones, "target-b", launcher=launcher) as clone_b, \
            evidence_view(repo, target, evidence, clones) as view:
        environments = {"target-a": clone_a.environment, "target-b": clone_b.environment}
        root = view if view is not None else clone_a.path
        part1 = derive_part1(root, python_version, lists, python=clone_a.python)
        touched = sorted(set(staged) & (dependency_paths(part1) | {"uv.lock"}))
        if touched:
            raise Wp0Error(f"the staged evidence changes a dependency of a live lane: {touched}")
        lanes = [lane for lane in live_lanes(root)
                 if lanes_wanted is None or lane.name in lanes_wanted]
        tables_cache: dict[tuple[str, str], dict[str, Any]] = {}
        commits = sorted({lane.commit for lane in lanes})
        results: dict[str, Any] = {}
        fidelity_clones: dict[str, replay.Clone] = {}
        from contextlib import ExitStack

        with ExitStack() as stack:
            for commit in commits:
                fidelity_clones[commit] = stack.enter_context(
                    replay.clone(repo, commit, clones, f"verdict-{commit[:12]}", launcher=launcher)
                )
                environments[f"verdict-{commit}"] = fidelity_clones[commit].environment

            def tables(at: replay.Clone, lane: Lane) -> dict[str, Any]:
                key = (at.commit, lane.spec.finalizer)
                if key not in tables_cache:
                    tables_cache[key] = projection.finalizer_tables(
                        at.python, at.path, lane.spec.finalizer, "psdirect"
                    )
                return tables_cache[key]

            for lane in lanes:
                results[lane.name] = _part2_lane(
                    repo, root, lane, clone_a, clone_b, fidelity_clones[lane.commit],
                    workdir, tables,
                )
        selections = _select_all(repo, target, lanes, part1, results, clone_b, exemptions)
    return {
        "target": target,
        "part1": part1,
        "environments": environments,
        "evidence": staged,
        "lanes": results,
        "selection": {name: asdict(s) for name, s in selections.items()},
        "option2": sorted(name for name, s in selections.items() if s.selected),
        "option3": sorted(name for name, s in selections.items() if s.option3),
    }


#: Where staged evidence may live: the evidence packs, manifests, registry and
#: records under docs/, and prose under plans/. Anything else in B is refused.
EVIDENCE_PREFIXES = ("docs/", "plans/")


def staged_evidence(directory: Path) -> dict[str, str]:
    """{repository path: sha256} of every file staged under *directory*."""
    if not directory.is_dir():
        raise Wp0Error(f"no staged evidence directory at {directory}")
    files: dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory).as_posix()
        if path.is_symlink():
            raise Wp0Error(f"staged evidence {relative} is a symbolic link")
        if path.is_dir():
            continue
        if not relative.startswith(EVIDENCE_PREFIXES):
            raise Wp0Error(f"staged evidence {relative} is outside {EVIDENCE_PREFIXES}")
        if relative in (SUCCESSOR_RECORD, PIN_RECORD):
            raise Wp0Error(f"staged evidence {relative} is a record write-records produces")
        files[relative] = sha256_file(path)
    if not files:
        raise Wp0Error(f"the staged evidence directory {directory} is empty")
    return files


@contextmanager
def evidence_view(
    repo: Path, target: str, evidence: Path | None, parent: Path
) -> Iterator[Path | None]:
    """A worktree of *target* with the staged evidence laid over it (or None)."""
    if evidence is None:
        yield None
        return
    with detached_worktree(repo, target, parent, "evidence-view") as path:
        for relative in staged_evidence(evidence):
            destination = path / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(evidence / relative, destination)
        yield path


def dependency_paths(part1: dict[str, Any]) -> set[str]:
    """Every path any live lane depends on (closure, resources, floor)."""
    return {p for lane in part1["lanes"].values()
            for label in ("closure", "resources", "floor") for p in lane[label]}


def bank_commit(
    repo: Path, target: str, evidence: Path, records: Path, branch: str, message: str
) -> str:
    """Make the banking commit B: *target* plus the staged evidence, the successor
    record and the pin, as one commit whose sole parent is *target*, on a new
    branch *branch*. Returns B."""
    files = {relative: evidence / relative for relative in staged_evidence(evidence)}
    for relative in (SUCCESSOR_RECORD, PIN_RECORD):
        files[relative] = records / relative
    with tempfile.TemporaryDirectory(prefix="wp0-bank-") as scratch:
        path = Path(scratch) / "bank"
        git(repo, "worktree", "add", "-q", "-b", branch, str(path), target)
        try:
            for relative, source in sorted(files.items()):
                destination = path / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
            git(path, "add", "-A")
            git(path, "commit", "-q", "-m", message)
            bank = git(path, "rev-parse", "HEAD").strip()
        finally:
            git(repo, "worktree", "remove", "--force", str(path))
    return bank


def _select_all(
    repo: Path,
    target: str,
    lanes: list[Lane],
    part1: dict[str, Any],
    results: dict[str, Any],
    clone_b: replay.Clone,
    exemptions: Any,
) -> dict[str, changed_mod.LaneSelection]:
    """D-9's selection for every lane, from the traces part 2 recorded at *target*."""
    selections: dict[str, changed_mod.LaneSelection] = {}
    tracked: dict[str, set[str]] = {target: set(repo_tracked(repo, target))}
    for lane in lanes:
        lane1 = part1["lanes"][lane.name]
        outcome = results[lane.name]
        dependency = sorted(set(lane1["closure"]) | set(lane1["resources"]) |
                            set(lane1["floor"]))
        traces = [outcome["replay"].get("trace"), *outcome["replay"].get("trace_children", []),
                  outcome["regrade_traced"].get("trace"),
                  *outcome["regrade_traced"].get("trace_children", [])]
        # Repository files the trace saw opened (tracked at either commit:
        # bytecode caches and run outputs are not repository files).
        if lane.commit not in tracked:
            tracked[lane.commit] = set(repo_tracked(repo, lane.commit))
        opened = {
            path for path in changed_mod.opened_files(traces, clone_b.path)
            if path in tracked[target] or path in tracked[lane.commit]
        }
        children = {
            path for path in changed_mod.child_repository_files(traces, clone_b.path)
            if path in tracked[target] or path in tracked[lane.commit]
        }
        changes = {path: changed_mod.diff_file(repo, lane.commit, target, path)
                   for path in sorted(set(dependency) | opened | children)}
        executed = changed_mod.executed_lines(traces, clone_b.path)
        incomplete = changed_mod.trace_incompleteness(
            traces, part1["toolchain"]["process_executables"]
        )
        selection = changed_mod.select_lane(
            lane.name,
            dependency,
            set(lane1["closure"]),
            set(lane1["floor"]),
            changes,
            executed,
            opened,
            children,
            incomplete,
            outcome["replay"]["identical"] if outcome["replay"]["applicable"] else None,
            outcome["regrade_equal"],
            exemptions,
            resources=lane1["resources"],
        )
        selections[lane.name] = selection
        outcome["opened_repository_files"] = sorted(opened)
        outcome["changes"] = {
            path: {"status": c.status, "new_lines": len(c.new_lines()),
                   "hunks": len(c.hunks)}
            for path, c in changes.items() if c.status != "unchanged"
        }
    return selections


def _part2_lane(
    repo: Path,
    root: Path,
    lane: Lane,
    clone_a: replay.Clone,
    clone_b: replay.Clone,
    fidelity: replay.Clone,
    workdir: Path,
    tables: Any,
    *,
    own_commit: bool = True,
) -> dict[str, Any]:
    """Part 2 for one lane: the replay and the re-grades at the target, and (with
    *own_commit*) the re-grade at the verdict's own commit in *fidelity*; without
    it, *fidelity* only supplies the finalizer's tables at the verdict commit."""
    if lane.has_candidate:
        replayed = replay.replay_builder(lane, root, clone_a, clone_b, workdir)
    else:
        replayed = {"applicable": False,
                    "reason": "no candidate: WP-0's fixture is authored on the guest"}
    banked = json.loads((root / lane.verdict).read_text(encoding="utf-8"))
    plain = replay.regrade(lane, root, clone_a, workdir, "plain", "target")
    traced = replay.regrade(lane, root, clone_b, workdir, "traced", "target")
    observed = replay.regrade(lane, root, clone_a, workdir, "observe", "target")
    own = replay.regrade(lane, root, fidelity, workdir, "plain", "verdict") if own_commit else None
    runs = [r for r in (plain, traced, observed, own) if r is not None]

    def side(result: dict[str, Any], at: replay.Clone) -> projection.Side:
        run_root = str(result.get("run_root", ""))

        def digest(member: str) -> str | None:
            return _digest_in(run_root, member)

        return projection.Side(
            verdict=result["verdict"] or {},
            commit=at.commit,
            tables=tables(at, lane),
            run_root=run_root,
            candidate_root=result.get("candidate_root", ""),
            member_digest=digest,
        )

    # The banked verdict was written on the controller in LOCAL_RUN_DIR (with
    # the candidate in CANDIDATE_DIR), as its pack's controller.log records, so
    # the projection's path rewrite applies to the banked side too (review
    # round 1, DeepSeek N3). A pack that does not record one has no such root.
    banked_side = projection.Side(
        verdict=banked, commit=lane.commit, tables=tables(fidelity, lane),
        run_root=_banked_root(root, lane, "LOCAL_RUN_DIR"),
        candidate_root=_banked_root(root, lane, "CANDIDATE_DIR"),
        member_digest=lane.pack_files.get,
    )
    problems: list[str] = []
    for result in runs:
        if result["verdict"] is None:
            problems.extend(result["problems"])
    at_target: dict[str, Any] = {"commit": clone_a.commit, **(
        projection.compare(banked_side, side(plain, clone_a), repo, lane.shape)
        if plain["verdict"] is not None else {"equal": False})}
    at_commit: dict[str, Any] | None = None
    if own is not None:
        at_commit = {"commit": fidelity.commit, **(
            projection.compare(banked_side, side(own, fidelity), repo, lane.shape)
            if own["verdict"] is not None else {"equal": False})}
    modes_agree = plain["verdict"] == traced["verdict"] == observed["verdict"]
    if not modes_agree:
        problems.append("plain, traced and observe re-grades wrote different verdicts")
    for result in runs:
        problems.extend(result["problems"])
    equivalence = []
    if traced.get("trace") and observed.get("trace"):
        state_b = replay.normalise_state(traced["trace"], {
            str(clone_b.path): "<clone>",
            str(workdir / lane.name / "regrade-target-traced"): "<private>"})
        state_a = replay.normalise_state(observed["trace"], {
            str(clone_a.path): "<clone>",
            str(workdir / lane.name / "regrade-target-observe"): "<private>"})
        equivalence = [n for n in replay.EQUIVALENCE_FIELDS if state_a[n] != state_b[n]]
        if equivalence:
            problems.append(f"traced and untraced finalizer runs differ in {equivalence}")
    regrade_equal = bool(at_target.get("equal")) and modes_agree and not problems
    return {
        "replay": replayed,
        "regrade_target": at_target,
        "regrade_verdict_commit": at_commit,
        "regrade_problems": problems,
        "regrade_equal": regrade_equal,
        "fidelity_equal": None if at_commit is None else bool(at_commit.get("equal")),
        "regrade_traced": {"trace": traced.get("trace"),
                           "trace_children": traced.get("trace_children", [])},
        "finalizer_equivalence_fields_differing": equivalence,
        "argv": {"finalizer": plain["argv"]},
    }


def _banked_root(root: Path, lane: Lane, key: str) -> str:
    """The controller directory *key* names in the lane's pack ("" when absent)."""
    try:
        value = controller_log_value(root, lane, key)
    except (KeyError, OSError):
        return ""
    return value if value.startswith("/") and value.rstrip("/") else ""


def _digest_in(run_root: str, member: str) -> str | None:
    if not run_root:
        return None
    base = Path(run_root)
    path = base / member if not member.startswith("controller-candidate/") else (
        base.parent / "candidate" / member[len("controller-candidate/"):]
    )
    return sha256_file(path) if path.is_file() else None


# --- the records ------------------------------------------------------------------------------


def successor_record(result: dict[str, Any], environment: dict[str, str]) -> dict[str, Any]:
    """The successor record: deterministic content only (no timings, no raw traces)."""
    part1 = result["part1"]
    lanes: dict[str, Any] = {}
    for name, outcome in sorted(result["lanes"].items()):
        lane1 = part1["lanes"][name]
        replayed = {k: v for k, v in outcome["replay"].items()
                    if k not in ("trace", "trace_children", "seconds")}
        selection = result["selection"][name]
        lanes[name] = {
            "verdict": lane1["verdict"],
            "verdict_commit": lane1["commit"],
            "shape": lane1["shape"],
            "has_candidate": lane1["has_candidate"],
            "closure": lane1["closure"],
            "external_modules": sorted({m for proc in lane1["processes"].values()
                                        for m in proc["external"]}),
            "resources": lane1["resources"],
            "memberships": lane1["memberships"],
            "floor": lane1["floor"],
            "declared_edges": lane1["edges"],
            "scan_counts": lane1["scan"]["counts"],
            "replay": replayed,
            "regrade": {
                "at_target": outcome["regrade_target"],
                "at_verdict_commit": outcome["regrade_verdict_commit"],
                "problems": outcome["regrade_problems"],
                "equal": outcome["regrade_equal"],
            },
            "changed_files": outcome["changes"],
            "executed_changed_lines": selection["executed_changed"],
            "exempted_lines": selection["exempted"],
            "trace_incomplete": selection["incomplete"],
            "selected": selection["selected"],
            "selection_reasons": selection["reasons"],
            "caveat": CAVEAT,
        }
    return {
        "schema": 1,
        "plan": "plans/036-change-cost.md, WP-0 (WI-083)",
        "target_commit": result["target"],
        "analysis": {"python_version": part1["python_version"], "platform": part1["platform"]},
        "lists_sha256": part1["lists_sha256"],
        "exemptions_sha256": part1["exemptions_sha256"],
        "toolchain": part1["toolchain"],
        "environment": environment,
        "environments": result.get("environments", {}),
        "evidence": result.get("evidence", {}),
        "design_notes": list(DESIGN_NOTES),
        "option2": result["option2"],
        "option3": result["option3"],
        "lanes": lanes,
    }


def refusals(result: dict[str, Any]) -> list[str]:
    """Why the records may not be written from *result* (empty: they may)."""
    problems: list[str] = []
    part1 = result["part1"]
    problems.extend(f"completeness: {p}" for p in part1["completeness"])
    problems.extend(f"unused declaration: {k}" for k in part1["unused_declarations"])
    for name, lane1 in sorted(part1["lanes"].items()):
        if lane1["scan"]["failing"]:
            problems.append(f"{name}: {len(lane1['scan']['failing'])} scan sites fail")
    for name, outcome in sorted(result["lanes"].items()):
        replayed = outcome["replay"]
        if replayed.get("applicable") and not replayed.get("identical"):
            problems.append(f"{name}: replay differs: {replayed.get('problems')}")
        if not outcome["regrade_equal"]:
            problems.append(f"{name}: re-grade differs")
        if not outcome["fidelity_equal"]:
            problems.append(f"{name}: re-grade at the verdict's own commit differs")
    # Review round 4 (Sol B1): at step 7 every lane D-9 selected has been re-run at
    # F, so nothing may still be selected; a selected lane is a stale verdict.
    for name, selection in sorted(result.get("selection", {}).items()):
        if selection.get("selected"):
            problems.append(f"{name}: D-9 still selects it at the freeze "
                            f"({selection.get('reasons')}): re-run it on the estate at F "
                            "(step 6) before banking")
    return problems


#: Per-lane fields a successor record must carry, and the part 1 field each
#: must equal at the gated tree.
_REDERIVED = {
    "verdict": "verdict", "verdict_commit": "commit", "has_candidate": "has_candidate",
    "closure": "closure", "resources": "resources", "floor": "floor",
    "memberships": "memberships", "declared_edges": "edges",
}
_LANE_FIELDS = (*_REDERIVED, "external_modules", "replay", "regrade", "scan_counts", "selected")


def _commit_id(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def _sha(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def successor_problems(successor: Any) -> list[str]:
    """Why *successor* is not a complete, all-equal successor record (empty: it is).

    Review round 1 (Sol B1): the pin is only as good as the record it names.
    A record qualifies only if every applicable replay is identical, every
    re-grade (at the target and at the verdict's own commit) is equal with no
    problem, no scan site fails, and every clone environment was built by the
    recorded uv method (`replay.uv_environment`), one for the target and one
    for each verdict commit.
    """
    if not isinstance(successor, dict):
        return ["the successor record is not a JSON object"]
    problems: list[str] = []
    if successor.get("schema") != 1:
        problems.append(f"successor schema {successor.get('schema')!r} is not 1")
    target = successor.get("target_commit")
    if not (isinstance(target, str) and re.fullmatch(r"[0-9a-f]{40}", target)):
        problems.append(f"successor target_commit {target!r} is not a commit id")
    analysis = successor.get("analysis")
    problems.extend(f"successor: {p}" for p in pinned_interpreter_problems(analysis))
    for key in ("lists_sha256", "exemptions_sha256"):
        if not _sha(successor.get(key)):
            problems.append(f"successor {key} is missing")
    if not isinstance(successor.get("toolchain"), dict):
        problems.append("successor toolchain is missing")
    if successor.get("design_notes") != list(DESIGN_NOTES):
        problems.append("successor design notes are not this tooling's")
    evidence = successor.get("evidence")
    if not (isinstance(evidence, dict) and all(
        isinstance(path, str) and path.startswith(EVIDENCE_PREFIXES) and _sha(digest)
        for path, digest in evidence.items()
    )):
        problems.append("the successor record does not list the evidence it was run with")
    lanes = successor.get("lanes")
    if not isinstance(lanes, dict) or not lanes:
        return [*problems, "the successor record names no lanes"]
    environments = successor.get("environments")
    if not isinstance(environments, dict):
        environments = {}
        problems.append("the successor record does not say how its environments were built")
    for label, built in sorted(environments.items()):
        if not isinstance(built, dict) or built.get("method") != replay.UV_METHOD:
            problems.append(f"environment {label} was not built by {replay.UV_METHOD}")
        elif LANE_PYTHON not in (str(built.get("python", "")),
                                 str(built.get("python", "")).rsplit(".", 1)[0]):
            # DeepSeek round 3, N4: the environment ran the analysis interpreter.
            problems.append(f"environment {label} ran Python {built.get('python')!r}, not "
                            f"the lanes' {LANE_PYTHON}")
        elif not (built.get("installed") and built.get("backend") and built.get("steps")
                  and _sha(built.get("uv_lock_sha256")) and _sha(built.get("backend_lock_sha256"))
                  and _commit_id(built.get("backend_lock_commit"))):
            problems.append(f"environment {label} does not record its build")
    built_commits = {built.get("commit") for built in environments.values()
                     if isinstance(built, dict)}
    for label in ("target-a", "target-b"):
        if environments.get(label, {}).get("commit") != target:
            problems.append(f"no {label} environment at the target commit")
    selected = []
    for name, lane in sorted(lanes.items()):
        if not isinstance(lane, dict):
            problems.append(f"{name}: not an object")
            continue
        missing = [key for key in _LANE_FIELDS if key not in lane]
        if missing:
            problems.append(f"{name}: missing {missing}")
            continue
        if lane["verdict_commit"] not in built_commits:
            problems.append(f"{name}: no environment at its verdict commit")
        replayed = lane["replay"]
        if bool(replayed.get("applicable")) != bool(lane["has_candidate"]):
            problems.append(f"{name}: replay applicability does not match the lane")
        if replayed.get("applicable") and not (
            replayed.get("identical") is True and not replayed.get("problems")
        ):
            problems.append(f"{name}: the replay is not identical")
        if replayed.get("applicable") and replayed.get("commit") != target:
            problems.append(f"{name}: the replay record does not name the target commit")
        if replayed.get("applicable") and not replayed.get("recorded_hashes"):
            problems.append(f"{name}: the replay record carries no banked candidate hash")
        regrade = lane["regrade"]
        at_commit = regrade.get("at_verdict_commit") or {}
        if not (regrade.get("equal") is True and not regrade.get("problems")
                and (regrade.get("at_target") or {}).get("equal") is True
                and at_commit.get("equal") is True):
            problems.append(f"{name}: the re-grade is not equal")
        at_target = regrade.get("at_target") or {}
        if at_target.get("commit") != target:
            problems.append(f"{name}: the target re-grade record does not name the target commit")
        for label, comparison in (("target", at_target), ("own-commit", at_commit)):
            digests = {comparison.get("banked_payload_sha256"),
                       comparison.get("regraded_payload_sha256")}
            if len(digests) != 1 or not _sha(digests.pop()):
                problems.append(f"{name}: the {label} re-grade record carries no equal payload "
                                "digests")
        if at_commit.get("commit") != lane["verdict_commit"]:
            problems.append(f"{name}: the own-commit re-grade ran at another commit")
        failing = {k: v for k, v in (lane["scan_counts"] or {}).items() if k in scan.FAILING}
        if any(failing.values()):
            problems.append(f"{name}: scan sites fail {failing}")
        if lane["selected"]:
            selected.append(name)
    if successor.get("option2") != selected:
        problems.append("successor option2 is not the lanes it marks selected")
    if selected or successor.get("option2"):
        # Review round 4 (Sol B1): a record whose D-9 selection is not empty
        # certifies a verdict that must be re-run; it never qualifies.
        stale = sorted(set(selected) | set(successor.get("option2") or []))
        problems.append(f"D-9 still selects {stale} at the freeze: those verdicts are stale")
    return problems


def pin_from(successor: dict[str, Any], successor_bytes: bytes) -> dict[str, Any]:
    """The pin a successor record determines (refused for an incomplete record).

    Every field is copied from the successor record, so the gate can rebuild
    the pin from the committed record and refuse any pin that differs.
    """
    problems = successor_problems(successor)
    if problems:
        raise Wp0Error(f"no pin from an incomplete successor record: {problems[:5]}")
    return _pin_of(successor, successor_bytes)


def _pin_of(successor: dict[str, Any], successor_bytes: bytes) -> dict[str, Any]:
    return {
        "schema": 1,
        "successor_record": SUCCESSOR_RECORD,
        "successor_sha256": sha256_bytes(successor_bytes),
        "target_commit": successor["target_commit"],
        "analysis": successor["analysis"],
        "lists_sha256": successor["lists_sha256"],
        "exemptions_sha256": successor["exemptions_sha256"],
        "toolchain": successor["toolchain"],
        "lanes": {
            name: {
                "verdict": lane["verdict"],
                "verdict_commit": lane["verdict_commit"],
                "closure": lane["closure"],
                "external": lane["external_modules"],
                "resources": lane["resources"],
                "memberships": lane["memberships"],
                "floor": lane["floor"],
                "edges": lane["declared_edges"],
            }
            for name, lane in sorted(successor["lanes"].items())
        },
    }


def write_records(
    repo: Path, result: dict[str, Any], environment: dict[str, str], out: Path | None = None
) -> list[str]:
    """Write the successor record and the pin under *out* (default *repo*), at their
    repository paths, or refuse (returns refusals)."""
    out = out or repo
    problems = refusals(result)
    if problems:
        return problems
    successor = successor_record(result, environment)
    successor_bytes = _dump(successor)
    problems = successor_problems(successor)
    if problems:
        return problems
    pin = pin_from(successor, successor_bytes)
    for relative, data in ((SUCCESSOR_RECORD, successor_bytes), (PIN_RECORD, _dump(pin))):
        (out / relative).parent.mkdir(parents=True, exist_ok=True)
        (out / relative).write_bytes(data)
    return []


# --- the gate's re-derivation ---------------------------------------------------------------


def eol_unpinned(root: Path, paths: list[str]) -> list[str]:
    """Pinned paths whose checkout bytes a Windows clone could change (not `eol=lf`/`-text`)."""
    if not paths:
        return []
    out = subprocess.run(
        ["git", "check-attr", "text", "eol", "--", *paths],
        cwd=root, capture_output=True, check=True,
    ).stdout.decode("utf-8")
    attributes: dict[str, dict[str, str]] = {}
    for line in out.splitlines():
        path, attribute, value = line.rsplit(": ", 2)
        attributes.setdefault(path, {})[attribute] = value
    return sorted(
        path for path in paths
        if not (attributes.get(path, {}).get("text") == "unset"
                or attributes.get(path, {}).get("eol") == "lf")
    )


#: The gate's skip rule: only a version of the form N(.N)* with a PEP 440
#: pre-release (aN, bN, rcN) or development (.devN) suffix is not final. Any
#: other form, an unusual one included, is treated as final (strict: an odd
#: version string must not be what lets the gate skip).
_PRE_RELEASE = re.compile(r"^\d+(?:\.\d+)*(?P<pre>(?:a|b|rc)\d+)?(?:\.(?P<dev>dev\d+))?$")


def is_pre_release(version: str) -> bool:
    match = _PRE_RELEASE.match(version)
    return bool(match and (match.group("pre") or match.group("dev")))


def _package_version(root: Path) -> str:
    """The release gate's own strict reader (`check_release_manifest.package_version`)."""
    gate_path = Path(__file__).resolve().parents[2] / "check_release_manifest.py"
    spec = importlib.util.spec_from_file_location("_wp0_check_release_manifest", gate_path)
    if spec is None or spec.loader is None:  # pragma: no cover - the gate is in the tree
        raise Wp0Error(f"cannot load {gate_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        version: str = module.package_version(root)
    finally:
        sys.modules.pop(spec.name, None)
    return version


def gate_decision(root: Path) -> tuple[str, str]:
    """("run", "") when the pin exists; ("skip", why) or ("fail", why) when it does not.

    Plan 036, "Before the pin exists": while the pin record is absent the gate
    skips and the message names WI-083; the skip is refused, and the gate
    fails, once the package version is a final release.
    """
    if (root / PIN_RECORD).is_file():
        return "run", ""
    version = _package_version(root)
    if is_pre_release(version):
        return (
            "skip",
            f"WI-083: no closure pin yet ({PIN_RECORD} is written at the 1.1.0 final freeze, "
            f"Plan 036 WP-0); version {version} is a pre-release",
        )
    return (
        "fail",
        f"WI-083: version {version} is a final release but the closure pin {PIN_RECORD} is "
        "absent; a final release requires the release-only closure gate (Plan 036 WP-0)",
    )


def running_interpreter_problems() -> list[str]:
    """Why this interpreter may not run the gate (review rounds 2 and 3).

    The interpreter is the lanes' one, a constant of this reviewed code
    (`LANE_PYTHON`), never a value read from the pin: a pin naming another
    interpreter would otherwise switch the gate off (DeepSeek round 3, B1). A
    derivation or re-execution under another interpreter differs (typeshed's
    stubs differ by version) without anything being wrong, so the gate does not
    run there; the CI job on the lanes' interpreter runs it.
    """
    have = f"{sys.version_info.major}.{sys.version_info.minor}"
    problems = []
    if have != LANE_PYTHON:
        problems.append(f"the closure gate runs only under Python {LANE_PYTHON}, the lanes' "
                        f"interpreter; this is Python {have}")
    if not sys.platform.startswith(scan.CONTROLLER_PLATFORM):
        problems.append(f"the closure gate runs only on {scan.CONTROLLER_PLATFORM}, the lanes' "
                        f"controller platform; this is {sys.platform}")
    return problems


def pinned_interpreter_problems(analysis: Any) -> list[str]:
    """A pin or record must name the lanes' interpreter and platform, or it fails."""
    if not isinstance(analysis, dict):
        return ["the pin does not name its analysis interpreter"]
    problems = []
    if analysis.get("python_version") != LANE_PYTHON:
        problems.append(f"the pin names Python {analysis.get('python_version')!r}, not the "
                        f"lanes' {LANE_PYTHON}: such a pin cannot be gated, and fails")
    if analysis.get("platform") != scan.CONTROLLER_PLATFORM:
        problems.append(f"the pin names platform {analysis.get('platform')!r}, not "
                        f"{scan.CONTROLLER_PLATFORM}")
    return problems


def gate_outcome(root: Path) -> tuple[str, list[str]]:
    """What the release-only gate does at *root*: ("skip", why), ("fail", problems)
    or ("pass", []). The test in tests/test_release_closure_gate.py is this."""
    decision, reason = gate_decision(root)
    if decision == "skip":
        return "skip", [reason]
    if decision == "fail":
        return "fail", [reason]
    refused = running_interpreter_problems()
    if refused:
        return "skip", [f"WI-083: {'; '.join(refused)}"]
    try:
        pin = json.loads((root / PIN_RECORD).read_text(encoding="utf-8"))
    except ValueError as exc:
        return "fail", [f"the pin record is not JSON: {exc}"]
    problems = verify_pin(root, pin)
    return ("fail" if problems else "pass"), problems


def verify_pin(root: Path, pin: dict[str, Any], workdir: Path | None = None) -> list[str]:
    """Every way the tree at *root* departs from *pin* (empty: the gate is green).

    The static half (`compare_pin`, `history_problems`) holds the tree, the pin
    and the successor record to each other, to git history and to the committed
    packs; the dynamic half
    (`reexecute_target`) re-runs the record's target-side claims offline in
    this interpreter. Refused outright under any interpreter but the pinned
    one (`running_interpreter_problems`), and failed for a pin naming any other
    (`pinned_interpreter_problems`).
    """
    refused = running_interpreter_problems()
    if refused:
        raise Wp0Error("; ".join(refused))
    if not isinstance(pin, dict):
        return ["the pin record is not a JSON object"]
    wrong = pinned_interpreter_problems(pin.get("analysis"))
    if wrong:
        return wrong
    lists = scan.Lists.load()
    part1 = derive_part1(root, pin["analysis"]["python_version"], lists)
    problems = compare_pin(root, pin, part1, lists)
    problems.extend(history_problems(root, pin))
    successor = json.loads((root / SUCCESSOR_RECORD).read_bytes())
    if problems or successor_problems(successor):
        return problems
    if workdir is None:
        with tempfile.TemporaryDirectory(prefix="wp0-gate-") as scratch:
            return reexecute_target(root, successor, part1, Path(scratch))
    return reexecute_target(root, successor, part1, workdir)


def reexecute_target(
    repo: Path,
    successor: dict[str, Any],
    part1: dict[str, Any],
    workdir: Path,
    *,
    lanes_wanted: set[str] | None = None,
) -> list[str]:
    """Re-run the successor record's target-side claims and compare (review round 2).

    At the record's target commit (whose dependency files the static half holds
    byte-identical to the gated tree), in a fresh worktree run by this
    interpreter (the CI job's synced environment; nothing is installed or
    downloaded): every applicable builder replay, against the record's and the
    banked candidate hashes; every canonical re-grade over the banked packs;
    and D-9's selection, against the record's. The historical half (re-grades
    at each verdict commit and the environments they ran in) is not re-run: it
    is bound to git and the packs by `compare_pin`.
    """
    target = successor["target_commit"]
    lanes = [lane for lane in live_lanes(repo)
             if lanes_wanted is None or lane.name in lanes_wanted]
    exemptions = changed_mod.load_exemptions()
    problems: list[str] = []
    clones = workdir / "clones"
    with replay.clone(repo, target, clones, "gate", launcher="current") as clone:
        tables_cache: dict[tuple[str, str], dict[str, Any]] = {}
        from contextlib import ExitStack

        with ExitStack() as stack:
            verdict_trees = {
                commit: stack.enter_context(
                    replay.clone(repo, commit, clones, f"tables-{commit[:12]}",
                                 launcher="current"))
                for commit in sorted({lane.commit for lane in lanes})
            }

            def tables(at: replay.Clone, lane: Lane) -> dict[str, Any]:
                key = (at.commit, lane.spec.finalizer)
                if key not in tables_cache:
                    tables_cache[key] = projection.finalizer_tables(
                        at.python, at.path, lane.spec.finalizer, "psdirect"
                    )
                return tables_cache[key]

            results = {
                lane.name: _part2_lane(repo, repo, lane, clone, clone,
                                       verdict_trees[lane.commit], workdir, tables,
                                       own_commit=False)
                for lane in lanes
            }
        selections = _select_all(repo, target, lanes, part1, results, clone, exemptions)
    for lane in lanes:
        recorded = successor["lanes"][lane.name]
        outcome = results[lane.name]
        replayed = outcome["replay"]
        if replayed.get("applicable"):
            if not replayed.get("identical"):
                problems.append(f"{lane.name}: the replay at the target does not reproduce: "
                                f"{replayed.get('problems')}")
            if replayed.get("recorded_hashes") != recorded["replay"].get("recorded_hashes"):
                problems.append(f"{lane.name}: the replayed candidate hashes are not the "
                                "record's")
        if not outcome["regrade_equal"]:
            problems.append(f"{lane.name}: the re-grade at the target differs: "
                            f"{outcome['regrade_problems'][:3]}")
        digest = outcome["regrade_target"].get("regraded_payload_sha256")
        if digest != recorded["regrade"]["at_target"].get("regraded_payload_sha256"):
            problems.append(f"{lane.name}: the re-graded payload is not the record's")
        selection = asdict(selections[lane.name])
        if selection["selected"]:
            problems.append(f"{lane.name}: D-9 re-derived here selects it "
                            f"({selection['reasons'][:3]}): its verdict is stale")
        for mine, theirs in (("selected", "selected"), ("executed_changed",
                             "executed_changed_lines"), ("exempted", "exempted_lines"),
                             ("incomplete", "trace_incomplete"),
                             ("reasons", "selection_reasons")):
            if json.loads(json.dumps(selection[mine])) != recorded[theirs]:
                problems.append(f"{lane.name}: D-9's {theirs} re-derived here differs from the "
                                "record's")
    return problems


def compare_pin(
    root: Path, pin: dict[str, Any], part1: dict[str, Any], lists: scan.Lists
) -> list[str]:
    """`verify_pin` against an already derived part 1 (the tests reuse one derivation)."""
    problems: list[str] = []
    if pin.get("successor_record") != SUCCESSOR_RECORD:
        # Review round 4 (DeepSeek N1): the pin names the one standard successor
        # record; a pin naming another file would gate that file instead.
        return [f"the pin names successor record {pin.get('successor_record')!r}, not "
                f"{SUCCESSOR_RECORD}"]
    successor_path = root / SUCCESSOR_RECORD
    if not successor_path.is_file():
        return [f"the successor record {SUCCESSOR_RECORD} is missing"]
    successor_bytes = successor_path.read_bytes()
    if sha256_bytes(successor_bytes) != pin.get("successor_sha256"):
        problems.append(
            "the pin was not derived from the committed successor record (its digest differs): "
            "regenerate both with a full run of parts 1 and 2, never the pin alone"
        )
    try:
        successor = json.loads(successor_bytes)
    except ValueError:
        return [*problems, "the successor record is not JSON"]
    incomplete = successor_problems(successor)
    problems.extend(f"successor record: {p}" for p in incomplete)
    if not incomplete:
        if pin != _pin_of(successor, successor_bytes):
            problems.append("the pin is not the one its successor record determines "
                            "(a pin is never edited by hand)")
        problems.extend(_successor_against(successor, part1, lists))
        problems.extend(_bindings(root, successor))
    if lists.digest != pin["lists_sha256"]:
        problems.append("the classification lists changed since the pin (a tier-2 change)")
    if sha256_file(LISTS_DIR / "exemptions.json") != pin["exemptions_sha256"]:
        problems.append("the release-metadata exemption table changed since the pin")
    problems.extend(f"completeness: {p}" for p in part1["completeness"])
    problems.extend(f"declared site gone: {k}" for k in part1["unused_declarations"])
    if part1["toolchain"] != pin["toolchain"]:
        problems.append(f"toolchain identity changed: {part1['toolchain']} != {pin['toolchain']}")
    if sorted(part1["lanes"]) != sorted(pin["lanes"]):
        problems.append(
            f"the live lane set changed: {sorted(part1['lanes'])} != {sorted(pin['lanes'])}"
        )
    for name, pinned in sorted(pin["lanes"].items()):
        lane1 = part1["lanes"].get(name)
        if lane1 is None:
            continue
        if lane1["verdict"] != pinned["verdict"]:
            problems.append(f"{name}: live verdict {lane1['verdict']} is not {pinned['verdict']}")
        for label in ("closure", "resources", "floor"):
            added = sorted(set(lane1[label]) - set(pinned[label]))
            removed = sorted(set(pinned[label]) - set(lane1[label]))
            if added or removed:
                problems.append(f"{name}: {label} edge added {added} removed {removed}")
            for path in sorted(set(lane1[label]) & set(pinned[label])):
                if lane1[label][path] != pinned[label][path]:
                    problems.append(f"{name}: {path} changed since the pin")
        external = sorted({m for proc in lane1["processes"].values() for m in proc["external"]})
        if external != pinned["external"]:
            problems.append(f"{name}: external modules {external} != {pinned['external']}")
        if lane1["memberships"] != pinned["memberships"]:
            problems.append(f"{name}: an enumerated directory's membership changed")
        if lane1["edges"] != pinned["edges"]:
            added = sorted(set(lane1["edges"]) - set(pinned["edges"]))
            removed = sorted(set(pinned["edges"]) - set(lane1["edges"]))
            problems.append(f"{name}: declared edges added {added[:5]} removed {removed[:5]}")
        if lane1["scan"]["failing"]:
            problems.append(f"{name}: {len(lane1['scan']['failing'])} scan sites fail")
    pinned_paths = sorted({p for lane in pin["lanes"].values()
                           for label in ("closure", "resources", "floor") for p in lane[label]}
                          | {"uv.lock"})
    unpinned = eol_unpinned(root, pinned_paths)
    if unpinned:
        problems.append(f"pinned files a Windows checkout could rewrite (no eol=lf/-text): "
                        f"{unpinned}")
    return problems


def _bindings(root: Path, successor: dict[str, Any]) -> list[str]:
    """The record's historical attestations against git and the committed packs.

    Re-grades at each verdict commit, and the environments they ran in, are not
    re-run by the gate (`reexecute_target` re-runs the target side). They are
    bound instead to content the repository holds: each environment's lock
    digests to `git show <commit>:uv.lock`, and every comparison's banked
    payload digest, and each replay's banked candidate hashes, to the banked
    verdict and pack as committed. This does not stop a committer who writes
    the record by hand from the same repository: that is the trust boundary
    (see the module docstring); it stops a record that disagrees with git or
    the evidence.
    """
    problems: list[str] = []
    for label, built in sorted(successor["environments"].items()):
        for commit_key, digest_key in (("commit", "uv_lock_sha256"),
                                       ("backend_lock_commit", "backend_lock_sha256")):
            lock = show(root, str(built.get(commit_key)), "uv.lock")
            if lock is None or sha256_bytes(lock) != built.get(digest_key):
                problems.append(f"environment {label}: {digest_key} is not the uv.lock at "
                                f"{str(built.get(commit_key))[:12]}")
    for lane in live_lanes(root):
        recorded = successor["lanes"].get(lane.name)
        if recorded is None:
            continue
        verdict = json.loads((root / lane.verdict).read_text(encoding="utf-8"))
        if recorded["replay"].get("applicable"):
            banked = replay.banked_candidate_hashes(verdict, lane.spec.flat_delivery_file)
            if recorded["replay"].get("recorded_hashes") != banked:
                problems.append(f"{lane.name}: the record's candidate hashes are not the banked "
                                "verdict's")
        side = projection.Side(
            verdict=verdict, commit=lane.commit, tables={},
            run_root=_banked_root(root, lane, "LOCAL_RUN_DIR"),
            candidate_root=_banked_root(root, lane, "CANDIDATE_DIR"),
            member_digest=lane.pack_files.get,
        )
        digest = projection.payload_digest(side, lane.shape)
        for label in ("at_target", "at_verdict_commit"):
            if recorded["regrade"][label].get("banked_payload_sha256") != digest:
                problems.append(f"{lane.name}: the {label} comparison is not bound to the banked "
                                "pack's payload")
    return problems


def _successor_against(
    successor: dict[str, Any], part1: dict[str, Any], lists: scan.Lists
) -> list[str]:
    """The successor record against the re-derivation at the gated tree."""
    problems: list[str] = []
    if successor["lists_sha256"] != lists.digest:
        problems.append("successor record: the classification lists differ from the tree's")
    if successor["exemptions_sha256"] != part1["exemptions_sha256"]:
        problems.append("successor record: the exemption table differs from the tree's")
    if successor["toolchain"] != part1["toolchain"]:
        problems.append("successor record: the toolchain differs from the tree's")
    if sorted(successor["lanes"]) != sorted(part1["lanes"]):
        problems.append(f"successor record: lane set {sorted(successor['lanes'])} is not the "
                        f"live lane set {sorted(part1['lanes'])}")
    for name, lane in sorted(successor["lanes"].items()):
        lane1 = part1["lanes"].get(name)
        if lane1 is None:
            continue
        for field_name, part1_name in _REDERIVED.items():
            if lane[field_name] != lane1[part1_name]:
                problems.append(f"successor record: {name}: {field_name} differs from the "
                                "re-derivation at this tree")
        external = sorted({m for proc in lane1["processes"].values() for m in proc["external"]})
        if lane["external_modules"] != external:
            problems.append(f"successor record: {name}: external modules differ")
    return problems


def history_problems(root: Path, pin: dict[str, Any]) -> list[str]:
    """The pin's target commit against the gated tree's history (review round 2).

    DeepSeek N6: the record's `target_commit` (the freeze commit F) must be the
    gated commit or one of its ancestors, and nothing a lane depends on (any
    pinned closure, resource or floor path, `uv.lock`, the reviewed lists) may
    differ between F and the gated working tree. The per-file digests already
    catch a changed byte; this also refuses a pin naming a commit the tree did
    not descend from.

    DeepSeek N2: the plan's step 8 (`bank_problems`: the banking commit B is
    F's child and changes no dependency) runs here too, with B found in git as
    the commit that added the pin record, so the CI gate runs it, not only
    the `check-bank` command.
    """
    problems: list[str] = []
    target = str(pin.get("target_commit"))
    exists = subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"{target}^{{commit}}"],
        capture_output=True, check=False,
    )
    if not _commit_id(target) or exists.returncode != 0:
        return [f"the pin's target commit {target[:12]} is not in this repository"]
    ancestor = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", target, "HEAD"],
        capture_output=True, check=False,
    )
    if ancestor.returncode != 0:
        problems.append(f"the pin's target commit {target[:12]} is not the gated commit or an "
                        "ancestor of it")
    pinned = {p for lane in pin["lanes"].values()
              for label in ("closure", "resources", "floor") for p in lane[label]}
    lists_prefix = LISTS_DIR.relative_to(REPO_ROOT).as_posix() + "/"
    changed = [p for p in git(root, "diff", "--name-only", "-z", "--no-renames", target, "--")
               .split("\0") if p]
    for path in sorted(changed):
        if path in pinned or path == "uv.lock" or path.startswith(lists_prefix):
            problems.append(f"{path} differs between the pin's target commit and the gated "
                            "tree: a dependency of a live lane changed after the freeze")
    # The commit that last added the pin record is the banking commit B.
    # (`-m`: a merge that brings the pin in counts too, and step 8 refuses it.)
    added = list(dict.fromkeys(
        git(root, "log", "-m", "--format=%H", "--diff-filter=A", "--", PIN_RECORD).split()
    ))
    evidence: dict[str, str] | None = None
    successor_path = root / SUCCESSOR_RECORD
    if successor_path.is_file():
        try:
            listed = json.loads(successor_path.read_bytes()).get("evidence")
        except (ValueError, AttributeError):
            listed = None
        evidence = listed if isinstance(listed, dict) else None
    if added:
        problems.extend(f"step 8: {p}"
                        for p in bank_problems(root, target, added[0], pin, evidence))
    else:
        problems.append(f"step 8: no commit adds {PIN_RECORD}; the pin must be committed in "
                        "the banking commit, F's child")
    return problems


def bank_problems(
    repo: Path, freeze: str, bank: str, pin: dict[str, Any],
    evidence: dict[str, str] | None = None,
) -> list[str]:
    """The plan's step 8: `B` is a child of `F` and changes no dependency file.

    Every path any lane's pinned closure, resources or floor names, and the
    reviewed lists, must be byte-identical between `F` and `B`; `B` carries only
    evidence and records (packs, batch manifest, registry, successor record,
    pin, prose).
    """
    problems: list[str] = []
    freeze, bank = rev_parse(repo, freeze), rev_parse(repo, bank)
    parents = git(repo, "rev-list", "--parents", "-n", "1", bank).split()[1:]
    if parents != [freeze]:
        problems.append(f"{bank[:12]} is not a child of the freeze commit {freeze[:12]}: {parents}")
    pinned = {p for lane in pin["lanes"].values()
              for label in ("closure", "resources", "floor") for p in lane[label]}
    changed = [p for p in git(repo, "diff", "--name-only", "-z", "--no-renames", freeze, bank)
               .split("\0") if p]
    lists_prefix = LISTS_DIR.relative_to(REPO_ROOT).as_posix() + "/"
    for path in sorted(changed):
        if path in pinned or path == "uv.lock" or path.startswith(lists_prefix):
            problems.append(f"{path}: the banking commit changes a dependency of a live lane")
    if evidence is not None:
        # Review round 3 (Sol B2): B is exactly F + the evidence the record was
        # run with + the successor record + the pin, byte for byte.
        allowed = set(evidence) | {SUCCESSOR_RECORD, PIN_RECORD}
        extra = sorted(set(changed) - allowed)
        if extra:
            problems.append(f"the banking commit changes files the record was not run with: "
                            f"{extra[:10]}")
        for record_path in (SUCCESSOR_RECORD, PIN_RECORD):
            if record_path not in changed:
                problems.append(f"the banking commit does not add {record_path}")
        for path, digest in sorted(evidence.items()):
            blob = show(repo, bank, path)
            if blob is None or sha256_bytes(blob) != digest:
                problems.append(f"{path}: the banking commit does not carry the evidence the "
                                "record was run with")
    return problems


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()
