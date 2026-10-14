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
"""

from __future__ import annotations

import fnmatch
import importlib.util
import json
import re
import subprocess
import sys
import tomllib
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import changed as changed_mod
from . import closure as closure_mod
from . import projection, replay, scan
from .lanes import Lane, live_lanes
from .repo import LISTS_DIR, REPO_ROOT, Wp0Error, git, rev_parse, sha256_bytes, sha256_file
from .repo import tracked_files as repo_tracked

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
    "minute.",
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
    "leaves Any; a value neither proves stays Any and fails unless declared.",
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
) -> dict[str, Any]:
    """Parts 1 and 2 for every live lane at *target*, and D-9's selection."""
    target = rev_parse(repo, target)
    lists = scan.Lists.load()
    exemptions = changed_mod.load_exemptions()
    clones = workdir / "clones"
    with replay.clone(repo, target, clones, "target-a", launcher=launcher) as clone_a, \
            replay.clone(repo, target, clones, "target-b", launcher=launcher) as clone_b:
        part1 = derive_part1(clone_a.path, python_version, lists, python=clone_a.python)
        lanes = [lane for lane in live_lanes(clone_a.path)
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

            def tables(at: replay.Clone, lane: Lane) -> dict[str, Any]:
                key = (at.commit, lane.spec.finalizer)
                if key not in tables_cache:
                    tables_cache[key] = projection.finalizer_tables(
                        at.python, at.path, lane.spec.finalizer, "psdirect"
                    )
                return tables_cache[key]

            for lane in lanes:
                results[lane.name] = _part2_lane(
                    repo, clone_a.path, lane, clone_a, clone_b, fidelity_clones[lane.commit],
                    workdir, tables,
                )
        selections = {}
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
            changes = {path: changed_mod.diff_file(repo, lane.commit, target, path)
                       for path in sorted(set(dependency) | opened)}
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
                changed_mod.child_repository_files(traces, clone_b.path),
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
    return {
        "target": target,
        "part1": part1,
        "lanes": results,
        "selection": {name: asdict(s) for name, s in selections.items()},
        "option2": sorted(name for name, s in selections.items() if s.selected),
        "option3": sorted(name for name, s in selections.items() if s.option3),
    }


def _part2_lane(
    repo: Path,
    root: Path,
    lane: Lane,
    clone_a: replay.Clone,
    clone_b: replay.Clone,
    fidelity: replay.Clone,
    workdir: Path,
    tables: Any,
) -> dict[str, Any]:
    if lane.has_candidate:
        replayed = replay.replay_builder(lane, root, clone_a, clone_b, workdir)
    else:
        replayed = {"applicable": False,
                    "reason": "no candidate: WP-0's fixture is authored on the guest"}
    banked = json.loads((root / lane.verdict).read_text(encoding="utf-8"))
    plain = replay.regrade(lane, root, clone_a, workdir, "plain", "target")
    traced = replay.regrade(lane, root, clone_b, workdir, "traced", "target")
    observed = replay.regrade(lane, root, clone_a, workdir, "observe", "target")
    own = replay.regrade(lane, root, fidelity, workdir, "plain", "verdict")

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

    banked_side = projection.Side(
        verdict=banked, commit=lane.commit, tables=tables(fidelity, lane),
        run_root="", candidate_root="", member_digest=lane.pack_files.get,
    )
    problems: list[str] = []
    for result in (plain, traced, observed, own):
        if result["verdict"] is None:
            problems.extend(result["problems"])
    at_target = projection.compare(banked_side, side(plain, clone_a), repo, lane.shape) \
        if plain["verdict"] is not None else {"equal": False}
    at_commit = projection.compare(banked_side, side(own, fidelity), repo, lane.shape) \
        if own["verdict"] is not None else {"equal": False}
    modes_agree = plain["verdict"] == traced["verdict"] == observed["verdict"]
    if not modes_agree:
        problems.append("plain, traced and observe re-grades wrote different verdicts")
    for result in (plain, traced, observed, own):
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
        "regrade_verdict_commit": {"commit": fidelity.commit, **at_commit},
        "regrade_problems": problems,
        "regrade_equal": regrade_equal,
        "fidelity_equal": bool(at_commit.get("equal")),
        "regrade_traced": {"trace": traced.get("trace"),
                           "trace_children": traced.get("trace_children", [])},
        "finalizer_equivalence_fields_differing": equivalence,
        "argv": {"finalizer": plain["argv"]},
    }


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
    return problems


def pin_from(
    successor: dict[str, Any], successor_bytes: bytes, part1: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema": 1,
        "successor_record": SUCCESSOR_RECORD,
        "successor_sha256": sha256_bytes(successor_bytes),
        "target_commit": successor["target_commit"],
        "analysis": successor["analysis"],
        "lists_sha256": part1["lists_sha256"],
        "exemptions_sha256": part1["exemptions_sha256"],
        "toolchain": part1["toolchain"],
        "lanes": {
            name: {
                "verdict": lane1["verdict"],
                "closure": lane1["closure"],
                "external": sorted({m for proc in lane1["processes"].values()
                                    for m in proc["external"]}),
                "resources": lane1["resources"],
                "memberships": lane1["memberships"],
                "floor": lane1["floor"],
                "edges": lane1["edges"],
            }
            for name, lane1 in sorted(part1["lanes"].items())
        },
    }


def write_records(repo: Path, result: dict[str, Any], environment: dict[str, str]) -> list[str]:
    """Write the successor record and the pin into *repo*, or refuse (returns refusals)."""
    problems = refusals(result)
    if problems:
        return problems
    successor = successor_record(result, environment)
    successor_bytes = _dump(successor)
    (repo / SUCCESSOR_RECORD).write_bytes(successor_bytes)
    (repo / PIN_RECORD).write_bytes(_dump(pin_from(successor, successor_bytes, result["part1"])))
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


def verify_pin(root: Path, pin: dict[str, Any]) -> list[str]:
    """Every way the tree at *root* departs from *pin* (empty: the gate is green)."""
    lists = scan.Lists.load()
    part1 = derive_part1(root, pin["analysis"]["python_version"], lists)
    return compare_pin(root, pin, part1, lists)


def compare_pin(
    root: Path, pin: dict[str, Any], part1: dict[str, Any], lists: scan.Lists
) -> list[str]:
    """`verify_pin` against an already derived part 1 (the tests reuse one derivation)."""
    problems: list[str] = []
    successor = root / pin["successor_record"]
    if not successor.is_file():
        return [f"the successor record {pin['successor_record']} is missing"]
    if sha256_file(successor) != pin["successor_sha256"]:
        problems.append(
            "the pin was not derived from the committed successor record (its digest differs): "
            "regenerate both with a full run of parts 1 and 2, never the pin alone"
        )
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
        if not set(pinned["floor"]) <= set(pinned["closure"]) | set(pinned["resources"]) | set(
            pinned["floor"]
        ):  # pragma: no cover - by construction
            problems.append(f"{name}: the floor is not inside the pinned dependency set")
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


def bank_problems(repo: Path, freeze: str, bank: str, pin: dict[str, Any]) -> list[str]:
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
    return problems


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()
