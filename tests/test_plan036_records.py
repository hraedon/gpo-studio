"""Plan 036 WP-0 part 3: the successor record, the pin, and the gate's comparison.

The pin is written only after a full run of parts 1 and 2 with no difference,
carries the successor record's SHA-256, and the gate fails on any added or
removed edge, on any changed pinned byte or directory membership, on a changed
toolchain identity or classification list, and on a pin whose successor
digest does not match the committed successor record. These tests build a pin
from one real part 1 derivation of this tree and mutate it.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from plan036_support import REPO_ROOT, load

record = load("record")
scan = load("scan")


@pytest.fixture(scope="module")
def derived() -> tuple[dict[str, Any], Any]:
    lists = scan.Lists.load()
    return record.derive_part1(REPO_ROOT, "3.14", lists), lists


TARGET = record.head(REPO_ROOT)
LANES = {lane.name: lane for lane in record.live_lanes(REPO_ROOT)}


def _lock(commit: str) -> str:
    data = record.show(REPO_ROOT, commit, "uv.lock")
    assert data is not None
    return str(record.sha256_bytes(data))


def _environment(label: str, commit: str) -> dict[str, Any]:
    return {"method": "uv-locked", "commit": commit, "uv": "uv 0.12.0", "python": "3.14.6",
            "uv_lock_sha256": _lock(commit), "backend_source": "test",
            "backend_lock_sha256": _lock(TARGET), "backend_lock_commit": TARGET,
            "backend": ["hatchling==1.32.4"], "steps": [["uv", "sync"]],
            "installed": ["-e <clone>", "hatchling==1.32.4"], "label": label}


def _banked(name: str) -> tuple[dict[str, str], str]:
    """The banked candidate hashes and canonical payload digest of a lane, read from
    the committed verdict and pack (what an honest run's record carries)."""
    lane = LANES[name]
    verdict = json.loads((REPO_ROOT / lane.verdict).read_text(encoding="utf-8"))
    hashes = record.replay.banked_candidate_hashes(verdict, lane.spec.flat_delivery_file)
    side = record.projection.Side(
        verdict=verdict, commit=lane.commit, tables={},
        run_root=record._banked_root(REPO_ROOT, lane, "LOCAL_RUN_DIR"),
        candidate_root=record._banked_root(REPO_ROOT, lane, "CANDIDATE_DIR"),
        member_digest=lane.pack_files.get,
    )
    return hashes, str(record.projection.payload_digest(side, lane.shape))


def _complete_result(part1: dict[str, Any]) -> dict[str, Any]:
    """A run of parts 1 and 2 in which every replay and re-grade agreed, built from
    the repository's own evidence. The static half of the gate accepts it: it
    cannot tell it from a real run (the trust boundary); `reexecute_target` is
    what re-runs its target claims."""
    lanes, selection = {}, {}
    for name, lane1 in part1["lanes"].items():
        hashes, digest = _banked(name)
        payload = {"equal": True, "banked_payload_sha256": digest,
                   "regraded_payload_sha256": digest}
        lanes[name] = {
            "replay": ({"applicable": True, "identical": True, "problems": [],
                        "commit": TARGET, "recorded_hashes": hashes}
                       if lane1["has_candidate"] else {"applicable": False}),
            "regrade_target": {"commit": TARGET, **payload},
            "regrade_verdict_commit": {"commit": lane1["commit"], **payload},
            "regrade_problems": [],
            "regrade_equal": True,
            "fidelity_equal": True,
            "changes": {},
        }
        selection[name] = {"executed_changed": {}, "exempted": {}, "incomplete": [],
                           "selected": False, "reasons": []}
    environments = {label: _environment(label, TARGET) for label in ("target-a", "target-b")}
    for commit in {lane1["commit"] for lane1 in part1["lanes"].values()}:
        environments[f"verdict-{commit}"] = _environment("verdict", commit)
    return {"target": TARGET, "part1": part1, "environments": environments, "lanes": lanes,
            "selection": selection, "option2": [], "option3": []}


def _round1_forgery(part1: dict[str, Any]) -> dict[str, Any]:
    """Round 1's fixture (Sol round 2, B1): every lane said equal, with no commit on
    any replay or re-grade, no candidate hash, no payload digest and invented lock
    digests. It passed the gate at a8aa6f1; it must not now."""
    result = _complete_result(part1)
    for outcome in result["lanes"].values():
        outcome["replay"] = ({"applicable": True, "identical": True, "problems": []}
                             if outcome["replay"]["applicable"] else {"applicable": False})
        outcome["regrade_target"] = {"equal": True}
        outcome["regrade_verdict_commit"] = {
            "commit": outcome["regrade_verdict_commit"]["commit"], "equal": True}
    for built in result["environments"].values():
        built["uv_lock_sha256"] = built["backend_lock_sha256"] = "1" * 64
        del built["backend_lock_commit"]
    return result


@pytest.fixture(autouse=True)
def _standard_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The pin must name record.SUCCESSOR_RECORD (DeepSeek round 4, N1); these tests
    point that one standard path at a scratch file (absolute, so root / it is it)."""
    monkeypatch.setattr(record, "SUCCESSOR_RECORD", str(tmp_path / "successor.json"))


def _write(successor: dict[str, Any], tmp_path: Path) -> tuple[bytes, Path]:
    data = record._dump(successor)
    path = tmp_path / "successor.json"
    path.write_bytes(data)
    return data, path


@pytest.fixture
def complete(derived: tuple[dict[str, Any], Any]) -> dict[str, Any]:
    part1, _lists = derived
    return record.successor_record(_complete_result(part1), {})


@pytest.fixture
def pinned(complete: dict[str, Any], tmp_path: Path) -> dict[str, Any]:
    data, path = _write(complete, tmp_path)
    pin = record.pin_from(complete, data)
    return pin


def _baseline(part1: dict[str, Any]) -> list[str]:
    """Problems the derivation itself reports (the gate reports them too)."""
    problems = [f"completeness: {p}" for p in part1["completeness"]]
    problems += [f"declared site gone: {k}" for k in part1["unused_declarations"]]
    problems += [f"{name}: {len(lane['scan']['failing'])} scan sites fail"
                 for name, lane in sorted(part1["lanes"].items()) if lane["scan"]["failing"]]
    return problems


def _compare(pin: dict[str, Any], derived: tuple[dict[str, Any], Any]) -> list[str]:
    part1, lists = derived
    baseline = _baseline(part1)
    return [p for p in record.compare_pin(REPO_ROOT, pin, part1, lists) if p not in baseline]


def test_an_untouched_pin_matches_the_tree(pinned: dict[str, Any], derived: Any) -> None:
    assert _compare(pinned, derived) == []


def test_round_1s_complete_forgery_now_fails(
    derived: tuple[dict[str, Any], Any], tmp_path: Path
) -> None:
    part1, _lists = derived
    forged = record.successor_record(_round1_forgery(part1), {})
    data, path = _write(forged, tmp_path)
    with pytest.raises(record.Wp0Error):
        record.pin_from(forged, data)
    pin = record._pin_of(forged, data)
    problems = _compare(pin, derived)
    assert any("does not name the target commit" in p for p in problems), problems
    assert any("payload digests" in p for p in problems), problems
    assert any("does not record its build" in p for p in problems), problems


def test_a_record_that_disagrees_with_git_or_the_packs_is_refused(
    complete: dict[str, Any], derived: Any, tmp_path: Path
) -> None:
    """The historical attestations are bound to git (lock digests) and to the
    committed evidence (candidate hashes, banked payload digests)."""
    for mutate, expected in (
        (lambda s: s["environments"]["target-a"].__setitem__("uv_lock_sha256", "2" * 64),
         "uv_lock_sha256 is not the uv.lock"),
        (lambda s: s["lanes"]["wp2"]["replay"]["recorded_hashes"].__setitem__("x", "0" * 64),
         "candidate hashes are not the banked"),
        (lambda s: [s["lanes"]["wp2"]["regrade"][k].__setitem__(f, "3" * 64)
                    for k in ("at_target", "at_verdict_commit")
                    for f in ("banked_payload_sha256", "regraded_payload_sha256")],
         "not bound to the banked pack"),
    ):
        successor = copy.deepcopy(complete)
        mutate(successor)
        data, path = _write(successor, tmp_path)
        pin = record._pin_of(successor, data)
        problems = _compare(pin, derived)
        assert any(expected in p for p in problems), (expected, problems)


@pytest.mark.skipif(sys.platform != "linux", reason="the lanes' finalizers run on the controller")
def test_the_gate_re_executes_target_claims_and_refuses_a_false_one(
    complete: dict[str, Any], derived: tuple[dict[str, Any], Any], tmp_path: Path
) -> None:
    """A record that passes every static check (built from the repository's own
    evidence) still claims WP-0 re-grades equal at the target; WP-0's finalizer
    refuses to finalize at any commit but the one its harness inputs name, so
    the gate's offline re-execution must find the claim false."""
    part1, _lists = derived
    problems = record.reexecute_target(REPO_ROOT, complete, part1, tmp_path / "gate",
                                       lanes_wanted={"wp0"})
    assert any(p.startswith("wp0: the re-grade at the target differs") for p in problems), \
        problems
    assert any("D-9's selected" in p for p in problems), problems


def test_the_gate_refuses_to_run_under_another_interpreter() -> None:
    """Sol N2 / DeepSeek round 3 B1: the gate runs only under the lanes' interpreter,
    a constant of reviewed code; a pin naming any other interpreter fails."""
    pin = {"analysis": {"python_version": "2.7", "platform": "linux"}}
    assert any("2.7" in p for p in record.pinned_interpreter_problems(pin["analysis"]))
    if record.running_interpreter_problems():
        with pytest.raises(record.Wp0Error, match=f"only under Python {record.LANE_PYTHON}"):
            record.verify_pin(REPO_ROOT, pin)
    else:
        problems = record.verify_pin(REPO_ROOT, pin)
        assert problems and "2.7" in problems[0]


def test_a_fabricated_pin_with_no_part_2_evidence_is_refused(
    derived: tuple[dict[str, Any], Any], tmp_path: Path
) -> None:
    """Review round 1, Sol B1 (repro_pin_bypass.py): a successor record carrying no
    lanes, replays or re-grades yields no pin, and a pin hand-built over it fails."""
    successor = {"schema": 1, "target_commit": "0" * 40,
                 "analysis": {"python_version": "3.14", "platform": "linux"}}
    data, path = _write(successor, tmp_path)
    with pytest.raises(record.Wp0Error):
        record.pin_from(successor, data)
    part1, _lists = derived
    pin = {"schema": 1, "successor_record": record.SUCCESSOR_RECORD,
           "successor_sha256": record.sha256_bytes(data), "target_commit": "0" * 40,
           "analysis": successor["analysis"], "lists_sha256": part1["lists_sha256"],
           "exemptions_sha256": part1["exemptions_sha256"], "toolchain": part1["toolchain"],
           "lanes": {name: {"verdict": lane["verdict"], "verdict_commit": lane["commit"],
                            "closure": lane["closure"], "resources": lane["resources"],
                            "external": sorted({m for proc in lane["processes"].values()
                                                for m in proc["external"]}),
                            "memberships": lane["memberships"], "floor": lane["floor"],
                            "edges": lane["edges"]}
                     for name, lane in part1["lanes"].items()}}
    problems = _compare(pin, derived)
    assert any("names no lanes" in p for p in problems), problems


SUCCESSOR_DEFECTS = {
    "replay-differs": lambda s: s["lanes"]["wp2"]["replay"].__setitem__("identical", False),
    "replay-dropped": lambda s: s["lanes"]["wp2"].__setitem__("replay", {"applicable": False}),
    "regrade-differs": lambda s: s["lanes"]["wp2"]["regrade"].__setitem__("equal", False),
    "regrade-at-target-differs": lambda s: s["lanes"]["wp2"]["regrade"]["at_target"]
    .__setitem__("equal", False),
    "own-commit-regrade-differs": lambda s: s["lanes"]["wp2"]["regrade"]["at_verdict_commit"]
    .__setitem__("equal", False),
    "own-commit-regrade-elsewhere": lambda s: s["lanes"]["wp2"]["regrade"]["at_verdict_commit"]
    .__setitem__("commit", "e" * 40),
    "regrade-problem": lambda s: s["lanes"]["wp2"]["regrade"]["problems"].append("x"),
    "lane-dropped": lambda s: s["lanes"].pop("wp2"),
    "environment-current": lambda s: s["environments"]["target-a"].__setitem__(
        "method", "current-interpreter"),
    "environment-missing": lambda s: s["environments"].pop(
        f"verdict-{s['lanes']['wp2']['verdict_commit']}"),
    "closure-edited": lambda s: s["lanes"]["wp2"]["closure"].pop(
        sorted(s["lanes"]["wp2"]["closure"])[0]),
    "verdict-commit-edited": lambda s: s["lanes"]["wp2"].__setitem__("verdict_commit", "e" * 40),
    "scan-failing": lambda s: s["lanes"]["wp2"]["scan_counts"].__setitem__("unclassified", 1),
    # Review round 4, Sol B1: a record whose D-9 selection is not empty.
    "lane-selected": lambda s: (s["lanes"]["wp2"].__setitem__("selected", True),
                                s.__setitem__("option2", ["wp2"])),
}


@pytest.mark.parametrize("defect", sorted(SUCCESSOR_DEFECTS))
def test_a_pin_over_an_incomplete_or_unequal_successor_is_refused(
    complete: dict[str, Any], derived: Any, tmp_path: Path, defect: str
) -> None:
    """The gate re-checks the record the pin names: every replay identical, every
    re-grade equal, every environment built by the recorded uv method, and lane
    set, commits and closures equal to the re-derivation at the gated tree. The
    pin is re-derived consistently (digest and content), so only these checks
    can catch the defect."""
    successor = copy.deepcopy(complete)
    SUCCESSOR_DEFECTS[defect](successor)
    data, path = _write(successor, tmp_path)
    pin = record._pin_of(successor, data)
    assert _compare(pin, derived), defect
    if not defect.endswith("-edited") and defect != "lane-dropped":
        with pytest.raises(record.Wp0Error):
            record.pin_from(successor, data)


def test_a_hand_edited_pin_is_refused_even_when_the_tree_agrees(
    pinned: dict[str, Any], derived: Any
) -> None:
    pin = copy.deepcopy(pinned)
    pin["target_commit"] = "e" * 40
    assert any("determines" in p for p in _compare(pin, derived))


def test_the_toolchain_names_every_declared_process_executable(derived: Any) -> None:
    """Declared process edges (the finalizers' git launches) name their executable; the
    tracer's children are held to this list, so an empty one would select every lane."""
    part1, _lists = derived
    assert part1["toolchain"]["process_executables"] == ["git"]


def test_a_pin_not_derived_from_the_committed_successor_record_is_refused(
    pinned: dict[str, Any], derived: Any
) -> None:
    Path(record.SUCCESSOR_RECORD).write_bytes(b'{"edited": true}\n')
    assert any("successor record" in p for p in _compare(pinned, derived))


MUTATIONS = {
    "closure-edge-removed": lambda lane: lane["closure"].pop(sorted(lane["closure"])[0]),
    "closure-edge-added": lambda lane: lane["closure"].__setitem__("src/gpo_studio/x.py", "0"),
    "closure-digest-changed": lambda lane: lane["closure"].__setitem__(
        sorted(lane["closure"])[0], "0" * 64),
    "floor-changed": lambda lane: lane["floor"].pop(sorted(lane["floor"])[0]),
    "edge-added": lambda lane: lane["edges"].append("x::y::call::os.system::0"),
    "external-added": lambda lane: lane["external"].append("socket"),
    "verdict-changed": lambda lane: lane.__setitem__("verdict", "docs/plan-033/other.json"),
}


@pytest.mark.parametrize("mutation", sorted(MUTATIONS))
def test_any_change_to_a_lanes_pinned_closure_is_refused(
    pinned: dict[str, Any], derived: Any, mutation: str
) -> None:
    pin = copy.deepcopy(pinned)
    MUTATIONS[mutation](pin["lanes"]["wp2"])
    assert _compare(pin, derived), mutation


def test_a_changed_enumerated_membership_is_refused(pinned: dict[str, Any], derived: Any) -> None:
    pin = copy.deepcopy(pinned)
    memberships = pin["lanes"]["report-parity"]["memberships"]
    assert memberships, "report-parity enumerates the backup corpus"
    key = sorted(memberships)[0]
    memberships[key].pop(sorted(memberships[key])[0])
    assert any("membership" in p for p in _compare(pin, derived))


def test_a_changed_toolchain_list_or_lane_set_is_refused(
    pinned: dict[str, Any], derived: Any
) -> None:
    for change in (
        lambda p: p["toolchain"].__setitem__("uv_lock_sha256", "0" * 64),
        lambda p: p["toolchain"].__setitem__("process_executables", ["git", "curl"]),
        lambda p: p.__setitem__("lists_sha256", "0" * 64),
        lambda p: p.__setitem__("exemptions_sha256", "0" * 64),
        lambda p: p["lanes"].pop("wp0"),
    ):
        pin = copy.deepcopy(pinned)
        change(pin)
        assert _compare(pin, derived)


def test_the_records_are_refused_when_anything_differs() -> None:
    part1 = {"completeness": [], "unused_declarations": [],
             "lanes": {"a": {"scan": {"failing": []}}}}
    clean = {"replay": {"applicable": True, "identical": True}, "regrade_equal": True,
             "fidelity_equal": True}
    assert record.refusals({"part1": part1, "lanes": {"a": clean}}) == []
    for broken in (
        {**clean, "replay": {"applicable": True, "identical": False, "problems": ["x"]}},
        {**clean, "regrade_equal": False},
        {**clean, "fidelity_equal": False},
    ):
        assert record.refusals({"part1": part1, "lanes": {"a": broken}})
    failing = copy.deepcopy(part1)
    failing["lanes"]["a"]["scan"]["failing"] = [{"target": "os.system"}]
    assert record.refusals({"part1": failing, "lanes": {"a": clean}})


def test_every_pinned_file_has_platform_independent_checkout_bytes(
    derived: tuple[dict[str, Any], Any],
) -> None:
    """The gate digests checkout bytes, so a Windows checkout must not rewrite any
    pinned file (each is `text eol=lf` or `-text`)."""
    part1, _ = derived
    paths = sorted({p for lane in part1["lanes"].values()
                    for label in ("closure", "resources", "floor") for p in lane[label]}
                   | {"uv.lock"})
    assert record.eol_unpinned(REPO_ROOT, paths) == []


def test_the_successor_record_is_deterministic() -> None:
    data = {"b": 1, "a": [1, 2], "c": {"z": 1, "y": 2}}
    assert record._dump(data) == record._dump(json.loads(record._dump(data)))
    assert record._dump(data).endswith(b"\n") and b"\r" not in record._dump(data)


def test_the_banking_commit_may_not_touch_a_dependency(tmp_path: Path) -> None:
    """Step 8: B is F's child and changes evidence and records only."""
    import subprocess

    root = tmp_path / "repo"
    root.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              check=True).stdout.decode("utf-8").strip()

    git("init", "-q")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    (root / "dep.py").write_text("x = 1\n", encoding="utf-8")
    (root / "notes.md").write_text("a\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "F")
    freeze = git("rev-parse", "HEAD")
    pin = {"lanes": {"lane": {"closure": {"dep.py": "x"}, "resources": {}, "floor": {}}}}
    (root / "notes.md").write_text("b\n", encoding="utf-8")
    git("commit", "-qam", "B: records only")
    assert record.bank_problems(root, freeze, "HEAD", pin) == []
    (root / "dep.py").write_text("x = 2\n", encoding="utf-8")
    git("commit", "-qam", "B': touches a dependency")
    problems = record.bank_problems(root, freeze, "HEAD", pin)
    assert any("dep.py" in p for p in problems) and any("not a child" in p for p in problems)


def test_the_gate_holds_the_pin_to_the_gated_history(tmp_path: Path) -> None:
    """DeepSeek round 2, N6 and N2: the pin's target commit must be the gated commit
    or its ancestor, no dependency may change after it, and step 8 (the banking
    commit is F's child and changes no dependency) runs in the gate."""
    import subprocess

    root = tmp_path / "repo"
    root.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              check=True).stdout.decode("utf-8").strip()

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    (root / "dep.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "F")
    freeze = git("rev-parse", "HEAD")
    pin = {"target_commit": freeze,
           "lanes": {"lane": {"closure": {"dep.py": "x"}, "resources": {}, "floor": {}}}}
    assert any("step 8: no commit adds" in p for p in record.history_problems(root, pin))
    pin_path = root / record.PIN_RECORD
    pin_path.parent.mkdir(parents=True)
    pin_path.write_text("{}\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "B: the pin")
    assert record.history_problems(root, pin) == []
    (root / "dep.py").write_text("x = 2\n", encoding="utf-8")
    assert any("dep.py differs" in p for p in record.history_problems(root, pin))
    git("checkout", "-q", "--", "dep.py")
    git("checkout", "-q", "-b", "side", freeze)
    (root / "other.txt").write_text("side\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "side")
    side = git("rev-parse", "HEAD")
    git("checkout", "-q", "main")
    stale = {**pin, "target_commit": side}
    assert any("not the gated commit or an ancestor" in p
               for p in record.history_problems(root, stale))
    assert any("not in this repository" in p
               for p in record.history_problems(root, {**pin, "target_commit": "e" * 40}))
    git("reset", "-q", "--hard", freeze)
    (root / "dep.py").write_text("x = 3\n", encoding="utf-8")
    pin_path.parent.mkdir(parents=True, exist_ok=True)
    pin_path.write_text("{}\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "B': the pin and a dependency")
    problems = record.history_problems(root, pin)
    assert any(p.startswith("step 8:") and "dep.py" in p for p in problems), problems


def test_a_pin_naming_another_successor_record_is_refused(
    pinned: dict[str, Any], derived: Any, tmp_path: Path
) -> None:
    """DeepSeek round 4, N1: a hand-written pin pointing the gate at an alternate
    successor record (while the standard one goes unverified) fails."""
    alternate = tmp_path / "alternate.json"
    alternate.write_bytes(Path(record.SUCCESSOR_RECORD).read_bytes())
    pin = {**pinned, "successor_record": str(alternate)}
    assert any("names successor record" in p for p in _compare(pin, derived))
