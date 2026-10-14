"""D-9 option 2: changed lines, the release-metadata exemption, and selection.

`plans/036-change-cost.md`, D-9 (decided by the owner on 2026-10-13, "option
2, as defined"):

- **Changed line**: a line on the new side of
  `git diff -U0 --diff-algorithm=histogram <verdict commit> <F> -- <file>`, for
  every file in the lane's dependency set (which includes the package's
  `__init__.py`). A file absent at the verdict's commit is changed on every
  line. A moved or replaced statement is a changed line.
- **Release-metadata exemption**: a changed line matching an entry of the
  reviewed table (`lists/exemptions.json`) is not counted for a lane only if
  (a) on both sides of the diff the line parses, by AST, as a single
  assignment of a plain string literal to that name; (b) the lane's two-clone
  replay at `F` reproduces the banked candidate byte for byte (WP-0, which has
  no candidate, skips (b)); (c) the lane's canonical re-grade at `F`
  reproduces the banked claim projection exactly. If any fails, it counts.
- **Executed**: reported by the line tracer during the builder run and the
  finalizer's re-grade at `F`, children included. A non-Python child that
  runs a repository file executes every changed line in it.
- **Rule**: a lane is selected iff some non-exempt changed line in its
  dependency set executed in either half, or a changed data file it reads
  (below), or its trace is incomplete; and, under every option, any lane
  whose replay or re-grade differs. A lane whose hand-bound file changed is
  listed too, with its own reason: that is not D-9 but the existing freshness
  gate, which already expires it.

The coordinator closed two gaps in the wording on 2026-10-14, strictly and
mechanically:

- **Pure deletions.** A deletion hunk has no new-side line, so the literal
  definition could not count it (a deleted guard clause would be invisible).
  Each deletion maps to the surviving statements on either side of the
  deletion point **within the same scope**: the scope that executed the
  deleted lines (the enclosing function, or the class or module body at top
  level; a `def` line belongs to the scope that executes it, not to its own
  body). The nearest statement at or before the point and the nearest at or
  after it, in that scope, are the deletion's adjacent lines, and if either
  executed, the deletion counts as an executed changed line. A scope is a
  function, not a block: a deletion at the end of an `if` body maps to the
  next statement in the function even if it follows the `if`, which can
  only select more. A file that does not parse, or any non-Python file, uses
  the raw lines either side of the point.
- **Data files.** A changed file that the lane reads, either declared (a
  resource or enumerated member in the dependency set) or observed (a
  repository file the trace saw opened that is not a Python module of the
  closure), counts as a changed dependency and selects the lane, exactly as
  an executed changed line does.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .repo import LISTS_DIR, Wp0Error, git, show

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Hunk:
    old_start: int
    old_lines: list[str]
    new_start: int
    new_lines: list[str]

    @property
    def new_numbers(self) -> list[int]:
        return list(range(self.new_start, self.new_start + len(self.new_lines)))


@dataclass
class FileChange:
    path: str
    status: str  # unchanged | modified | added | removed | binary
    hunks: list[Hunk] = field(default_factory=list)

    def new_lines(self) -> set[int]:
        return {number for hunk in self.hunks for number in hunk.new_numbers}

    #: New-side statements adjacent to each pure deletion, in the deleted
    #: lines' own scope (see the module docstring).
    deletion_lines: set[int] = field(default_factory=set)

    def deletion_points(self) -> set[int]:
        return set(self.deletion_lines)


def _scoped_statements(source: bytes) -> list[tuple[int, int, str]] | None:
    """(first line, last line, executing scope) for every statement, or None."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    found: list[tuple[int, int, str]] = []

    def visit(statements: list[ast.stmt], scope: str) -> None:
        for statement in statements:
            last = statement.end_lineno or statement.lineno
            found.append((statement.lineno, last, scope))
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(statement.body, f"{scope}.{statement.name}")
                continue
            for name in ("body", "orelse", "finalbody"):
                visit(getattr(statement, name, []) or [], scope)
            for handler in getattr(statement, "handlers", []) or []:
                found.append((handler.lineno, handler.end_lineno or handler.lineno, scope))
                visit(handler.body, scope)
            for case in getattr(statement, "cases", []) or []:
                visit(case.body, scope)

    visit(tree.body, "<module>")
    return found


def _scope_of(statements: list[tuple[int, int, str]], line: int) -> str | None:
    """The scope of the innermost statement spanning *line* (the last match wins:
    statements are listed outer before inner)."""
    scope = None
    for first, last, owner in statements:
        if first <= line <= last:
            scope = owner
    return scope


def deletion_adjacent(path: str, old: bytes, new: bytes, hunks: list[Hunk]) -> set[int]:
    """New-side lines adjacent to every pure deletion in *hunks* (module docstring)."""
    deletions = [h for h in hunks if h.old_lines and not h.new_lines]
    if not deletions:
        return set()
    new_count = new.count(b"\n") + (0 if new.endswith(b"\n") else 1)
    raw: set[int] = set()
    for hunk in deletions:
        raw.update(line for line in (hunk.new_start, hunk.new_start + 1) if 1 <= line <= new_count)
    if not path.endswith(".py"):
        return raw
    old_statements, new_statements = _scoped_statements(old), _scoped_statements(new)
    if old_statements is None or new_statements is None:
        return raw
    adjacent: set[int] = set()
    for hunk in deletions:
        point = hunk.new_start  # git: the deletion follows this new-side line
        deleted = range(hunk.old_start, hunk.old_start + len(hunk.old_lines))
        scopes = {_scope_of(old_statements, line) for line in deleted} - {None}
        for scope in scopes or {"<module>"}:
            lines = sorted({first for first, _last, owner in new_statements if owner == scope})
            if not lines:
                adjacent.update(line for line in (point, point + 1) if 1 <= line <= new_count)
                continue
            before = [line for line in lines if line <= point]
            after = [line for line in lines if line >= point + 1]
            if before:
                adjacent.add(before[-1])
            if after:
                adjacent.add(after[0])
    return adjacent


def diff_file(repo: Path, base: str, target: str, path: str) -> FileChange:
    """The histogram diff of *path* from *base* to *target*, parsed."""
    old, new = show(repo, base, path), show(repo, target, path)
    if old is None and new is None:
        raise Wp0Error(f"{path} exists at neither {base} nor {target}")
    if new is None:
        return FileChange(path, "removed")
    if old is None:
        text = new.decode("utf-8", errors="replace").splitlines()
        return FileChange(path, "added", [Hunk(0, [], 1, text)])
    if old == new:
        return FileChange(path, "unchanged")
    if b"\0" in old or b"\0" in new:
        return FileChange(path, "binary", [Hunk(0, [], 1, ["<binary>"] * max(1, new.count(b"\n")))])
    out = git(
        repo, "-c", "core.quotepath=off", "diff", "-U0", "--diff-algorithm=histogram",
        "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames", base, target, "--", path,
    )
    hunks: list[Hunk] = []
    current: Hunk | None = None
    for line in out.splitlines():
        match = _HUNK.match(line)
        if match:
            old_start = int(match.group(1))
            new_start = int(match.group(3))
            current = Hunk(old_start, [], new_start, [])
            hunks.append(current)
            continue
        if current is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            current.new_lines.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            current.old_lines.append(line[1:])
    if not hunks:
        raise Wp0Error(f"{path}: bytes differ but git reports no hunk (attributes?)")
    return FileChange(path, "modified", hunks, deletion_adjacent(path, old, new, hunks))


# --- the release-metadata exemption ---------------------------------------------------


@dataclass(frozen=True)
class Exemption:
    file: str
    name: str
    pattern: str
    reason: str


def load_exemptions(directory: Path = LISTS_DIR) -> list[Exemption]:
    data = json.loads((directory / "exemptions.json").read_text(encoding="utf-8"))
    return [Exemption(e["file"], e["name"], e["pattern"], e["reason"]) for e in data["entries"]]


def _plain_string_assignment(line: str, name: str) -> bool:
    """Gate (a), one side: the line alone is `<name> = "<literal>"`, by AST."""
    try:
        tree = ast.parse(line.strip())
    except SyntaxError:
        return False
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assign):
        return False
    statement = tree.body[0]
    return (
        len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Name)
        and statement.targets[0].id == name
        and isinstance(statement.value, ast.Constant)
        and type(statement.value.value) is str
    )


def exempt_candidates(change: FileChange, exemptions: Sequence[Exemption]) -> dict[int, str]:
    """New-side lines that pass gate (a) for an entry: {line number: entry name}.

    Both sides: the new line, and every old line of its hunk, must be a single
    plain string assignment to the entry's name (a replacement of one version
    literal by another, nothing else in the hunk).
    """
    eligible: dict[int, str] = {}
    entries = [e for e in exemptions if e.file == change.path]
    if not entries:
        return eligible
    for hunk in change.hunks:
        for entry in entries:
            if not hunk.old_lines or not hunk.new_lines:
                continue
            sides_ok = all(
                re.fullmatch(entry.pattern, text) and _plain_string_assignment(text, entry.name)
                for text in [*hunk.old_lines, *hunk.new_lines]
            )
            if sides_ok:
                for number in hunk.new_numbers:
                    eligible[number] = entry.name
    return eligible


# --- executed lines ---------------------------------------------------------------------------


def executed_lines(
    traces: Iterable[dict[str, Any] | None], clone_root: Path
) -> dict[str, set[int]]:
    """{repository path: executed lines} from traces made in *clone_root*."""
    prefix = str(clone_root.resolve())
    executed: dict[str, set[int]] = {}
    for trace in traces:
        if not trace:
            continue
        for filename, lines in trace.get("lines", {}).items():
            resolved = str(Path(filename).resolve())
            if resolved.startswith(prefix + "/") or resolved.startswith(prefix + "\\"):
                relative = Path(resolved).relative_to(prefix).as_posix()
                executed.setdefault(relative, set()).update(lines)
    return executed


def opened_files(traces: Iterable[dict[str, Any] | None], clone_root: Path) -> set[str]:
    prefix = str(clone_root.resolve())
    found: set[str] = set()
    for trace in traces:
        for filename, _mode in (trace or {}).get("opened", []):
            resolved = str(Path(filename).resolve())
            if resolved.startswith(prefix + "/") or resolved.startswith(prefix + "\\"):
                found.add(Path(resolved).relative_to(prefix).as_posix())
    return found


def child_repository_files(
    traces: Iterable[dict[str, Any] | None], clone_root: Path
) -> set[str]:
    """Repository files a non-Python child process was handed (`pwsh -File x`)."""
    prefix = clone_root.resolve()
    files: set[str] = set()
    for trace in traces:
        for process in (trace or {}).get("processes", []):
            if process.get("kind") not in ("process", "shell"):
                continue
            for argument in process.get("argv", [])[1:]:
                candidate = Path(str(argument))
                if not candidate.is_absolute():
                    candidate = prefix / candidate
                try:
                    resolved = candidate.resolve()
                    if resolved.is_file():
                        files.add(resolved.relative_to(prefix).as_posix())
                except (OSError, ValueError):
                    continue
    return files


def trace_incompleteness(
    traces: Iterable[dict[str, Any] | None], declared_executables: Iterable[str]
) -> list[str]:
    """Why the traces of one lane are incomplete (empty: complete).

    The tracer's own reasons (a Python child it could not follow, a fork, a
    shell started outside subprocess), plus the harness's two rules: a shell
    child is incomplete (no declaration today says a shell runs no
    interpreter, so none is accepted), and a non-Python child whose executable
    no declared process edge names means the declarations have fallen behind
    what ran.
    """
    allowed = {name.lower() for name in declared_executables}
    reasons: list[str] = []
    for trace in traces:
        if not trace:
            continue
        reasons.extend(trace.get("incomplete", []))
        for process in trace.get("processes", []):
            argv = [str(a) for a in process.get("argv", [])]
            if process.get("kind") == "shell":
                reasons.append(f"a shell child, which no declaration covers: {argv}")
            elif process.get("kind") == "process" and argv:
                name = Path(argv[0]).name.lower().removesuffix(".exe")
                if name not in allowed:
                    reasons.append(f"a child process no declared edge names: {argv[0]}")
    return reasons


@dataclass
class LaneSelection:
    lane: str
    selected: bool
    reasons: list[str]
    executed_changed: dict[str, list[int]]
    exempted: dict[str, list[int]]
    exemption_refused: dict[str, str]
    incomplete: list[str]
    changed_unexecuted: dict[str, int]
    deletion_adjacent: dict[str, list[int]]
    changed_data_read: list[str]
    option3: bool
    #: The scope this selection was made over: the dependency set plus the
    #: repository files the trace saw opened.
    considered: list[str] = field(default_factory=list)


def select_lane(
    lane: str,
    dependency_set: Sequence[str],
    python_modules: set[str],
    floor: set[str],
    changes: dict[str, FileChange],
    executed: dict[str, set[int]],
    opened: set[str],
    child_files: set[str],
    incomplete: Sequence[str],
    replay_identical: bool | None,
    regrade_equal: bool,
    exemptions: Sequence[Exemption],
    resources: Iterable[str] = (),
) -> LaneSelection:
    """D-9 option 2 for one lane, as a Boolean over the inputs (no human input).

    *opened* are repository files the trace saw opened and *resources* the data
    files the declarations say the lane reads; a changed one of either that is
    not a Python module of the closure selects the lane. *changes* must hold a
    diff for every path in the dependency set and in *opened*.
    """
    declared_reads = set(resources)
    reasons: list[str] = []
    executed_changed: dict[str, list[int]] = {}
    exempted: dict[str, list[int]] = {}
    refused: dict[str, str] = {}
    unexecuted: dict[str, int] = {}
    deletion_adjacent: dict[str, list[int]] = {}
    changed_data: list[str] = []
    option3 = False
    gates_bc = (replay_identical is not False) and regrade_equal
    considered = sorted(set(dependency_set) | set(opened))
    for path in considered:
        change = changes[path]
        if change.status == "unchanged":
            continue
        new = change.new_lines()
        eligible = exempt_candidates(change, exemptions)
        counted = set(new)
        if eligible:
            if gates_bc:
                counted -= set(eligible)
                exempted[path] = sorted(eligible)
            else:
                refused[path] = (
                    "gate (b) failed: the replay differs" if replay_identical is False
                    else "gate (c) failed: the re-grade differs"
                )
        if path in python_modules and path not in floor and (counted or change.deletion_lines):
            option3 = True
        ran = executed.get(path, set())
        if path in child_files:
            ran = ran | new | change.deletion_lines  # a non-Python child ran the whole file
        adjacent = sorted(change.deletion_points() & ran)
        if adjacent:
            deletion_adjacent[path] = adjacent  # a deletion whose neighbours ran counts
        hits = sorted((counted & ran) | set(adjacent))
        if hits:
            executed_changed[path] = hits
        elif counted or change.deletion_lines:
            unexecuted[path] = len(counted | change.deletion_lines)
        if path not in python_modules and (path in opened or path in declared_reads):
            changed_data.append(path)  # a changed data file the lane reads
        if change.status == "removed":
            reasons.append(f"{path} was removed from the tree")
        if path in floor:
            # Not D-9's rule: the existing freshness gate already expires a
            # verdict whose hand-bound file changed, so the lane must run anyway.
            reasons.append(f"{path} is bound by the verdict and changed (the freshness gate)")
    if executed_changed:
        reasons.append(
            "changed lines executed: "
            + ", ".join(f"{p} ({len(v)})" for p, v in sorted(executed_changed.items()))
        )
    if deletion_adjacent:
        reasons.append(f"deletions whose adjacent lines executed: {sorted(deletion_adjacent)}")
    if changed_data:
        reasons.append(f"changed data files the lane reads: {changed_data}")
    if incomplete:
        reasons.append(f"trace incomplete: {list(incomplete)[:3]}")
    if replay_identical is False:
        reasons.append("the replay differs (selected under every option)")
    if not regrade_equal:
        reasons.append("the re-grade differs (selected under every option)")
    return LaneSelection(
        lane=lane,
        selected=bool(reasons),
        reasons=reasons,
        executed_changed=executed_changed,
        exempted=exempted,
        exemption_refused=refused,
        incomplete=list(incomplete),
        changed_unexecuted=unexecuted,
        deletion_adjacent=deletion_adjacent,
        changed_data_read=changed_data,
        option3=option3 or bool(changed_data) or replay_identical is False or not regrade_equal,
        considered=considered,
    )


def converge(passes: Sequence[set[str]], option3_at_final: set[str]) -> dict[str, Any]:
    """The plan's "Order", steps 3 and 5: provisional at F0, definitive at F.

    *passes* is [S0 (at F0), S1 (first pass at F), S2 (second pass at F)], as
    far as they have run. Returns the decision: `converged` with the set when
    a pass at F equals the set that F was made from; `another-pass` when the
    first pass at F differs (bring the tables into line, re-make F, run again);
    or, when the second pass at F differs too, the `conservative-fallback`:
    every lane any pass found plus option 3's lanes, which never shrinks.
    """
    if not passes:
        raise Wp0Error("no selection pass to converge")
    if len(passes) == 1:
        return {"decision": "provisional", "set": sorted(passes[0])}
    if len(passes) > 3:
        raise Wp0Error("at most two passes at F (the plan's bound)")
    if passes[-1] == passes[-2]:
        return {"decision": "converged", "set": sorted(passes[-1]), "passes": len(passes) - 1}
    if len(passes) == 2:
        return {"decision": "another-pass", "set": sorted(passes[-1])}
    union = set().union(*passes) | option3_at_final
    return {
        "decision": "conservative-fallback",
        "set": sorted(union),
        "label": "the conservative fallback set D-9 permits (not option 2's selection)",
    }
