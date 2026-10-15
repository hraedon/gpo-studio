"""Plan 036 WP-0's line tracer (`plans/036-change-cost.md`, "How the trace runs").

The tracer is launched as `python <flags> tracer.py <out> -- <script> <args>`
and must make the process look as `python <flags> <script> <args>` would; a
Python script child is re-launched through it with its flags (`-I` included)
preserved; every other way of starting Python, and any shell, makes the trace
incomplete. These tests run real processes (no monkeypatching): the claim is
about what the interpreter does.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from plan036_support import PACKAGE, load

TRACER = PACKAGE / "tracer.py"
changed = load("changed")

PROBE = """\
import json, sys
print(json.dumps({
    "file": __file__, "argv": sys.argv, "path": sys.path, "spec": __spec__ is None,
    "loader": type(__loader__).__name__, "package": __package__, "name": __name__,
    "cached": __cached__, "main_is_globals": sys.modules["__main__"].__dict__ is globals(),
}))
"""


def _run(cwd: Path, argv: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(argv, cwd=cwd, capture_output=True, check=False, timeout=120)


def _trace(out: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((out / "trace.json").read_text(encoding="utf-8"))
    return data


def _all_traces(out: Path) -> list[dict[str, Any]]:
    found = [_trace(out)]
    for child in found[0]["children"]:
        found.extend(_all_traces(Path(child)))
    return found


@pytest.mark.parametrize("flags", [[], ["-I"], ["-P"], ["-I", "-B"]])
def test_the_launch_is_what_the_interpreter_would_make(tmp_path: Path, flags: list[str]) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "probe.py").write_text(PROBE, encoding="utf-8")
    script = os.path.join("sub", "probe.py")
    plain = _run(tmp_path, [sys.executable, *flags, script, "a", "b"])
    traced = _run(tmp_path, [sys.executable, *flags, str(TRACER), str(tmp_path / "t"), "--",
                             script, "a", "b"])
    assert plain.returncode == 0, plain.stderr
    assert traced.returncode == 0, traced.stderr
    expected, actual = json.loads(plain.stdout), json.loads(traced.stdout)
    assert actual == expected
    trace = _trace(tmp_path / "t")
    assert trace["incomplete"] == [] and trace["exit"] == 0


def test_lines_executed_at_import_and_in_bodies_are_recorded(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(
        "X = 1\n\n\ndef used():\n    return X + 1\n\n\ndef unused():\n    return 2\n",
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text("import mod\nmod.used()\n", encoding="utf-8")
    result = _run(tmp_path, [sys.executable, str(TRACER), str(tmp_path / "t"), "--", "main.py"])
    assert result.returncode == 0, result.stderr
    lines = _trace(tmp_path / "t")["lines"]
    mod = next(v for k, v in lines.items() if k.endswith("mod.py"))
    assert {1, 4, 5, 8} <= set(mod), mod  # definitions run at import, plus used()'s body
    assert 9 not in mod  # unused()'s body never ran


def test_a_script_child_started_with_dash_i_is_traced(tmp_path: Path) -> None:
    (tmp_path / "child.py").write_text("MARKED = 'changed line'\n", encoding="utf-8")
    (tmp_path / "builder.py").write_text(
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, '-I', 'child.py'], check=True)\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, [sys.executable, str(TRACER), str(tmp_path / "t"), "--", "builder.py"])
    assert result.returncode == 0, result.stderr
    traces = _all_traces(tmp_path / "t")
    assert all(not t["incomplete"] for t in traces)
    assert len(traces) == 2
    child = traces[1]
    assert child["flags"]["isolated"] == 1, "the child's -I was not preserved"
    child_lines = next(v for k, v in child["lines"].items() if k.endswith("child.py"))
    assert 1 in child_lines


UNTRACEABLE = {
    "python -c": "subprocess.run([sys.executable, '-c', 'pass'], check=True)",
    "python -m": "subprocess.run([sys.executable, '-m', 'json.tool', '--help'], check=True,"
                 " capture_output=True)",
    "stdin script": "subprocess.run([sys.executable, '-'], input=b'pass', check=True)",
    "os.system": "os.system(sys.executable + ' -c pass')",
}


@pytest.mark.parametrize("label", sorted(UNTRACEABLE))
def test_an_untraceable_python_child_makes_the_trace_incomplete(
    tmp_path: Path, label: str
) -> None:
    (tmp_path / "builder.py").write_text(
        "import os, subprocess, sys\n" + UNTRACEABLE[label] + "\n", encoding="utf-8"
    )
    result = _run(tmp_path, [sys.executable, str(TRACER), str(tmp_path / "t"), "--", "builder.py"])
    assert result.returncode == 0, result.stderr
    assert _trace(tmp_path / "t")["incomplete"], label


@pytest.mark.skipif(not hasattr(os, "posix_spawn"), reason="os.posix_spawn is POSIX-only")
def test_a_posix_spawn_python_child_makes_the_trace_incomplete(tmp_path: Path) -> None:
    (tmp_path / "child.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "builder.py").write_text(
        "import os, sys\n"
        "pid = os.posix_spawn(sys.executable, [sys.executable, 'child.py'], dict(os.environ))\n"
        "os.waitpid(pid, 0)\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, [sys.executable, str(TRACER), str(tmp_path / "t"), "--", "builder.py"])
    assert result.returncode == 0, result.stderr
    assert _trace(tmp_path / "t")["incomplete"]


@pytest.mark.skipif(shutil.which("sh") is None or os.name == "nt", reason="needs a POSIX sh")
def test_a_shell_child_makes_the_trace_incomplete(tmp_path: Path) -> None:
    (tmp_path / "child.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "builder.py").write_text(
        "import subprocess, sys\n"
        "subprocess.run(['sh', '-c', sys.executable + ' child.py'], check=True)\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, [sys.executable, str(TRACER), str(tmp_path / "t"), "--", "builder.py"])
    assert result.returncode == 0, result.stderr
    trace = _trace(tmp_path / "t")
    assert any(p["kind"] == "shell" for p in trace["processes"])
    # A shell is recorded for the harness to judge; the harness's rule
    # (changed.trace_incompleteness) counts it as incomplete.
    assert trace["processes"][0].get("needs_declaration") is True


def test_a_non_python_child_is_recorded_with_its_arguments(tmp_path: Path) -> None:
    (tmp_path / "builder.py").write_text(
        "import subprocess\n"
        "subprocess.run(['git', '--version'], check=True, capture_output=True)\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, [sys.executable, str(TRACER), str(tmp_path / "t"), "--", "builder.py"])
    assert result.returncode == 0, result.stderr
    trace = _trace(tmp_path / "t")
    assert trace["incomplete"] == []
    assert trace["processes"] == [
        {"kind": "process", "argv": ["git", "--version"], "route": "subprocess"}
    ]


def test_observe_mode_records_state_but_no_lines(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("X = 1\n", encoding="utf-8")
    result = _run(tmp_path, [sys.executable, str(TRACER), str(tmp_path / "t"), "--observe", "--",
                             "main.py"])
    assert result.returncode == 0, result.stderr
    trace = _trace(tmp_path / "t")
    assert trace["observe_only"] is True and trace["lines"] == {}
    assert trace["cwd"] == str(tmp_path) or Path(trace["cwd"]).samefile(tmp_path)


def test_the_exit_status_and_trace_survive_a_failing_script(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
    result = _run(tmp_path, [sys.executable, str(TRACER), str(tmp_path / "t"), "--", "main.py"])
    assert result.returncode == 3
    assert _trace(tmp_path / "t")["exit"] == 3


WRAPPED = {
    "env": "subprocess.run(['env', sys.executable, 'child.py'], check=True)",
    "nice": "subprocess.run(['nice', sys.executable, 'child.py'], check=True)",
    "timeout": "subprocess.run(['timeout', '30', sys.executable, 'child.py'], check=True)",
    "interpreter-path-arg": "subprocess.run(['git', '--version', sys.executable], check=True)",
}


@pytest.mark.skipif(os.name == "nt", reason="the wrappers are POSIX programs")
@pytest.mark.parametrize("variant", sorted(WRAPPED))
def test_a_wrapped_python_child_makes_the_trace_incomplete_and_joins_the_domain(
    tmp_path: Path, variant: str
) -> None:
    """Review round 4, Sol B2 (r4_env_wrapper_probe.py): `env <python> child.py` ran
    an untraced Python child even with `env` declared, and child.py stayed outside
    the selection domain. A wrapper, or any argv naming a Python interpreter or
    source, now makes the trace incomplete; repository files a child is handed
    are in the domain."""
    (tmp_path / "child.py").write_text("MARKED = 'changed and executed'\n", encoding="utf-8")
    (tmp_path / "builder.py").write_text(
        f"import subprocess, sys\n{WRAPPED[variant]}\n", encoding="utf-8")
    result = _run(tmp_path, [sys.executable, str(TRACER), str(tmp_path / "t"), "--", "builder.py"])
    assert result.returncode == 0, result.stderr
    trace = _trace(tmp_path / "t")
    declared = ["env", "nice", "timeout", "git"]
    incomplete = changed.trace_incompleteness([trace], declared)
    assert incomplete, trace["processes"]
    if variant != "interpreter-path-arg":
        children = changed.child_repository_files([trace], tmp_path)
        assert "child.py" in children
        change = changed.FileChange("child.py", "modified", [], set())
        selection = changed.select_lane(
            "probe", ["builder.py"], {"builder.py"}, set(),
            {"builder.py": changed.FileChange("builder.py", "unchanged"), "child.py": change},
            {}, set(), children, incomplete, True, True, [],
        )
        assert selection.selected and "child.py" in selection.considered


def test_a_python_path_handed_to_git_as_data_keeps_the_trace_complete(tmp_path: Path) -> None:
    """The finalizers hash bound files with `git show :scripts/x.py`: the path is data,
    not code to run, so it must not make every lane's trace incomplete."""
    trace = {"incomplete": [], "processes": [
        {"kind": "process", "argv": ["git", "show", ":scripts/plan-033/build-x.py"]},
    ]}
    assert changed.trace_incompleteness([trace], ["git"]) == []
