"""D-9 option 2 as defined (`plans/036-change-cost.md`, Owner decisions, D-9).

A lane is selected iff a non-exempt changed line in its dependency set
executed in either half, or its trace is incomplete; and, under every option,
any lane whose replay or re-grade differs. The release-metadata exemption
needs three gates: (a) a plain string assignment on both sides by AST, (b) an
identical replay, (c) an identical re-grade. The plan's planted test: a
builder that writes `__version__` into its candidate must make the line count.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from plan036_support import load

changed = load("changed")
repo_mod = load("repo")

EXEMPTIONS = changed.load_exemptions()
VERSION = "src/gpo_studio/__init__.py"


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)
    return result.stdout.decode("utf-8").strip()


@pytest.fixture
def history(tmp_path: Path) -> tuple[Path, str, str]:
    """A two-commit repository: base and target, with every kind of change."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "test")
    _git(root, "config", "core.autocrlf", "false")
    (root / "src" / "gpo_studio").mkdir(parents=True)
    (root / VERSION).write_text('"""Package."""\n\n__version__ = "1.0.0"\n', encoding="utf-8")
    (root / "mod.py").write_text("a = 1\nb = 2\nc = 3\nd = 4\ne = 5\n", encoding="utf-8")
    (root / "gone.py").write_text("x = 1\n", encoding="utf-8")
    (root / "mixed.py").write_text('__version__ = "1.0.0"\nflag = False\n', encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")
    (root / VERSION).write_text('"""Package."""\n\n__version__ = "1.1.0"\n', encoding="utf-8")
    # change b (a replacement), and drop d on its own (a pure deletion)
    (root / "mod.py").write_text("a = 1\nb = 20\nc = 3\ne = 5\n", encoding="utf-8")
    (root / "new.py").write_text("y = 1\nz = 2\n", encoding="utf-8")
    (root / "gone.py").unlink()
    (root / "mixed.py").write_text('__version__ = "1.1.0"\nflag = True\n', encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "target")
    return root, base, _git(root, "rev-parse", "HEAD")


def test_changed_lines_are_the_new_side_of_a_histogram_diff(history: tuple[Path, str, str]) -> None:
    root, base, target = history
    mod = changed.diff_file(root, base, target, "mod.py")
    assert mod.status == "modified" and mod.new_lines() == {2}
    # The pure deletion has no new-side line, so D-9 as worded cannot count it;
    # the lines bracketing it are reported separately (deletion_adjacent).
    assert mod.deletion_points() == {3, 4}
    assert changed.diff_file(root, base, target, "new.py").new_lines() == {1, 2}
    assert changed.diff_file(root, base, target, "gone.py").status == "removed"
    assert changed.diff_file(root, base, target, VERSION).new_lines() == {3}


def test_gate_a_admits_only_a_plain_string_version_assignment(
    history: tuple[Path, str, str],
) -> None:
    root, base, target = history
    version = changed.diff_file(root, base, target, VERSION)
    assert changed.exempt_candidates(version, EXEMPTIONS) == {3: "__version__"}
    for text in ('__version__ = compute()', '__version__ = "1" + "2"', '__version__ = b"1"',
                 'version = "1.1.0"', '__version__: str = "1.1.0"'):
        hunk = changed.Hunk(1, ['__version__ = "1.0.0"'], 1, [text])
        assert changed.exempt_candidates(changed.FileChange(VERSION, "modified", [hunk]),
                                         EXEMPTIONS) == {}, text


def test_a_version_line_sharing_a_hunk_with_another_change_is_not_exempt(
    history: tuple[Path, str, str],
) -> None:
    """Strict reading: gate (a) holds only for a hunk that replaces a version literal
    and nothing else (the table names __init__.py only; a hunk mixing the bump with a
    code change counts in full)."""
    hunk = changed.Hunk(3, ['__version__ = "1.0.0"', "flag = False"], 3,
                        ['__version__ = "1.1.0"', "flag = True"])
    assert changed.exempt_candidates(changed.FileChange(VERSION, "modified", [hunk]),
                                     EXEMPTIONS) == {}


def _select(change: object, *, replay: bool | None, regrade: bool, executed: set[int],
            incomplete: tuple[str, ...] = ()) -> object:
    return changed.select_lane(
        "lane", [VERSION], {VERSION}, set(), {VERSION: change}, {VERSION: executed}, set(), set(),
        list(incomplete), replay, regrade, EXEMPTIONS,
    )


def test_the_exempt_version_line_does_not_select_when_gates_b_and_c_hold(
    history: tuple[Path, str, str],
) -> None:
    root, base, target = history
    version = changed.diff_file(root, base, target, VERSION)
    selection = _select(version, replay=True, regrade=True, executed={3})
    assert not selection.selected and selection.exempted == {VERSION: [3]}


def test_a_builder_that_writes_the_version_into_its_candidate_makes_the_line_count(
    history: tuple[Path, str, str],
) -> None:
    """The plan's planted test: the version reaches the candidate, so the replay at F
    differs from the banked candidate (gate (b) fails), and the executed version line
    counts: the lane is selected."""
    root, base, target = history
    version = changed.diff_file(root, base, target, VERSION)
    selection = _select(version, replay=False, regrade=True, executed={3})
    assert selection.selected
    assert selection.exempted == {} and VERSION in selection.exemption_refused
    assert selection.executed_changed == {VERSION: [3]}


def test_wp0_has_no_replay_so_gate_b_is_the_regrade_alone(history: tuple[Path, str, str]) -> None:
    root, base, target = history
    version = changed.diff_file(root, base, target, VERSION)
    assert not _select(version, replay=None, regrade=True, executed={3}).selected
    assert _select(version, replay=None, regrade=False, executed={3}).selected


def test_selection_is_a_boolean_over_execution_and_incompleteness(
    history: tuple[Path, str, str],
) -> None:
    root, base, target = history
    mod = changed.diff_file(root, base, target, "mod.py")
    base_args = ("lane", ["mod.py"], {"mod.py"}, set(), {"mod.py": mod})
    ran = changed.select_lane(*base_args, {"mod.py": {2}}, set(), set(), [], True, True, EXEMPTIONS)
    idle = changed.select_lane(*base_args, {"mod.py": {1}}, set(), set(), [], True, True,
                               EXEMPTIONS)
    broken = changed.select_lane(*base_args, {}, set(), set(), ["a shell child"], True, True,
                                 EXEMPTIONS)
    assert ran.selected and not idle.selected and broken.selected
    assert idle.changed_unexecuted == {"mod.py": 1} and idle.option3


def test_a_non_python_child_running_a_changed_file_executes_all_of_it(
    history: tuple[Path, str, str],
) -> None:
    root, base, target = history
    new = changed.diff_file(root, base, target, "new.py")
    selection = changed.select_lane("lane", ["new.py"], set(), set(), {"new.py": new}, {}, set(),
                                    {"new.py"}, [], True, True, EXEMPTIONS)
    assert selection.selected and selection.executed_changed == {"new.py": [1, 2]}


def test_shells_and_undeclared_children_make_the_trace_incomplete() -> None:
    traces = [{"incomplete": [], "processes": [
        {"kind": "process", "argv": ["git", "rev-parse"]},
        {"kind": "shell", "argv": ["sh", "-c", "true"]},
        {"kind": "process", "argv": ["/usr/bin/curl", "x"]},
    ]}]
    reasons = changed.trace_incompleteness(traces, ["git"])
    assert len(reasons) == 2
    assert any("shell" in r for r in reasons) and any("curl" in r for r in reasons)
    assert changed.trace_incompleteness([{"incomplete": [], "processes": [
        {"kind": "process", "argv": ["git"]}]}], ["git"]) == []


def test_the_convergence_rule() -> None:
    s0, s1, s2 = {"a", "b"}, {"a", "b", "c"}, {"a", "d"}
    assert changed.converge([s0], set())["decision"] == "provisional"
    assert changed.converge([s0, s0], set()) == {"decision": "converged", "set": ["a", "b"],
                                                 "passes": 1}
    assert changed.converge([s0, s1], set())["decision"] == "another-pass"
    assert changed.converge([s0, s1, s1], set())["decision"] == "converged"
    fallback = changed.converge([s0, s1, s2], {"e"})
    assert fallback["decision"] == "conservative-fallback"
    assert fallback["set"] == ["a", "b", "c", "d", "e"]  # every pass, plus option 3: never shrinks
    with pytest.raises(repo_mod.Wp0Error):
        changed.converge([s0, s1, s2, s2], set())


def test_a_changed_hand_bound_file_selects_the_lane_through_the_freshness_gate(
    history: tuple[Path, str, str],
) -> None:
    root, base, target = history
    mod = changed.diff_file(root, base, target, "mod.py")
    selection = changed.select_lane("lane", ["mod.py"], set(), {"mod.py"}, {"mod.py": mod}, {},
                                    set(), set(), [], True, True, EXEMPTIONS)
    assert selection.selected and any("freshness gate" in r for r in selection.reasons)
