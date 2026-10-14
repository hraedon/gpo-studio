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
from pathlib import Path
from typing import Any

import pytest
from plan036_support import REPO_ROOT, load

record = load("record")
scan = load("scan")


@pytest.fixture(scope="module")
def derived() -> tuple[dict[str, Any], Any]:
    lists = scan.Lists.load()
    return record.derive_part1(REPO_ROOT, "3.13", lists), lists


@pytest.fixture
def pinned(derived: tuple[dict[str, Any], Any], tmp_path: Path) -> dict[str, Any]:
    part1, _lists = derived
    successor = {"schema": 1, "target_commit": "f" * 40,
                 "analysis": {"python_version": "3.13", "platform": "linux"}}
    data = record._dump(successor)
    path = tmp_path / "successor.json"
    path.write_bytes(data)
    pin = record.pin_from(successor, data, part1)
    pin["successor_record"] = str(path)  # absolute: compare_pin reads root / it
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


def test_the_toolchain_names_every_declared_process_executable(derived: Any) -> None:
    """Declared process edges (the finalizers' git launches) name their executable; the
    tracer's children are held to this list, so an empty one would select every lane."""
    part1, _lists = derived
    assert part1["toolchain"]["process_executables"] == ["git"]


def test_a_pin_not_derived_from_the_committed_successor_record_is_refused(
    pinned: dict[str, Any], derived: Any
) -> None:
    Path(pinned["successor_record"]).write_bytes(b'{"edited": true}\n')
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
