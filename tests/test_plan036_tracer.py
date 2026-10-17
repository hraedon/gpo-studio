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
from plan036_support import PACKAGE, git_env, load

TRACER = PACKAGE / "tracer.py"
changed = load("changed")
repo_mod = load("repo")
replay = load("replay")

PROBE = """\
import json, sys
print(json.dumps({
    "file": __file__, "argv": sys.argv, "path": sys.path, "spec": __spec__ is None,
    "loader": type(__loader__).__name__, "package": __package__, "name": __name__,
    "cached": __cached__, "main_is_globals": sys.modules["__main__"].__dict__ is globals(),
}))
"""


def _run(
    cwd: Path, argv: list[str], env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(argv, cwd=cwd, capture_output=True, check=False, timeout=120, env=env)


def _lane_env(tmp_path: Path) -> dict[str, str]:
    """The environment the harness gives a lane run (replay.run_environment)."""
    private = tmp_path / "private-tmp"
    private.mkdir(exist_ok=True)
    return {**replay.run_environment(None, {}), "TMPDIR": str(private)}


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
        {"kind": "process", "argv": ["git", "--version"], "route": "subprocess",
         "cwd": trace["cwd"], "env_changed": []}
    ]
    assert trace["environ"] == dict(os.environ) | trace["environ"]  # every variable it was given


def test_a_childs_directory_and_environment_changes_are_recorded(tmp_path: Path) -> None:
    """Review round 6, Sol B2: what a child sees besides its argv."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "builder.py").write_text(
        "import os, subprocess\n"
        "subprocess.run(['git', '--version'], cwd='sub', capture_output=True,\n"
        "               env={**os.environ, 'GIT_DIR': '/elsewhere'})\n"
        "subprocess.run(['git', '--version'], capture_output=True, preexec_fn=lambda: None)\n",
        encoding="utf-8")
    result = _run(tmp_path, [sys.executable, str(TRACER), str(tmp_path / "t"), "--", "builder.py"])
    assert result.returncode == 0, result.stderr
    trace = _trace(tmp_path / "t")
    first, second = trace["processes"]
    assert Path(first["cwd"]) == (tmp_path / "sub").resolve() or Path(first["cwd"]).samefile(
        tmp_path / "sub")
    assert first["env_changed"] == ["GIT_DIR"] and second["env_changed"] == []
    assert any("preexec_fn" in reason for reason in trace["incomplete"])


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
    trace = {"incomplete": [], "environ": {**git_env(), **repo_mod.LANE_GIT_IDENTITY},
             "processes": [
                 {"kind": "process", "argv": ["git", "show", ":scripts/plan-033/build-x.py"],
                  "route": "subprocess", "cwd": "/clone", "env_changed": []},
             ]}
    assert changed.trace_incompleteness([trace], ["git"], Path("/clone")) == []


@pytest.mark.skipif(os.name == "nt" or shutil.which("git") is None, reason="needs POSIX git")
def test_a_python_grandchild_hidden_in_a_git_alias_makes_the_trace_incomplete(
    tmp_path: Path,
) -> None:
    """Review round 5, Sol B3 (r5_git_child_probe.py): `git -c alias.run=!python child.py
    run` starts a Python grandchild the tracer never sees, through the one declared
    executable. The embedded interpreter makes the trace incomplete, and child.py,
    named inside the argument, joins the selection domain."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=git_env())
    (tmp_path / "child.py").write_text("open('ran', 'w').close()\n", encoding="utf-8")
    (tmp_path / "builder.py").write_text(
        "import subprocess, sys\n"
        "subprocess.run(['git', '-c', f'alias.run=!{sys.executable} child.py', 'run'],"
        " check=True)\n", encoding="utf-8")
    # Hermetic (review round 6, Sol B2): no system or global configuration.
    result = _run(tmp_path, [sys.executable, str(TRACER), str(tmp_path / "t"), "--", "builder.py"],
                  _lane_env(tmp_path))
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "ran").exists()
    trace = _trace(tmp_path / "t")
    assert changed.trace_incompleteness([trace], ["git"]), trace["processes"]
    assert "child.py" in changed.child_repository_files([trace], tmp_path)


@pytest.mark.parametrize("argument", [
    "alias.x=!sh -c true", "core.sshCommand=ssh -i k", "a;b", "$(id)", "--upload-pack=x", "-c",
])
def test_an_embedded_command_in_a_declared_programs_argv_makes_the_trace_incomplete(
    argument: str,
) -> None:
    trace = {"incomplete": [], "processes": [{"kind": "process", "argv": ["git", argument]}]}
    assert changed.trace_incompleteness([trace], ["git"])


@pytest.mark.parametrize("name", ["python3.14-dbg", "pypy3", "/usr/bin/python3", "python"])
def test_a_python_interpreter_named_by_pattern_makes_the_trace_incomplete(name: str) -> None:
    """DeepSeek round 5, N2: by pattern, not a list."""
    trace = {"incomplete": [], "processes": [{"kind": "process", "argv": ["git", name, "x"]}]}
    assert changed.trace_incompleteness([trace], ["git"])


# --- review round 6, Sol B2: git configuration cannot start an untraced helper -----------

_FSMONITOR_BUILDER = (
    "import subprocess\n"
    "subprocess.run(['git', 'status', '--porcelain'], check=True, capture_output=True)\n"
)


def _fsmonitor_repository(root: Path) -> None:
    """Sol's r6_git_config_probe.py: a repository whose local configuration names
    an fsmonitor helper, which `git status --porcelain` starts."""
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=git_env())
    helper = root / "fsmonitor-helper"
    helper.write_text("#!/bin/sh\nprintf ran > fsmonitor-ran\nprintf '/\\n'\n", "utf-8")
    helper.chmod(0o755)
    subprocess.run(["git", "-C", str(root), "config", "core.fsmonitor", str(helper)],
                   check=True, env=git_env())
    (root / "builder.py").write_text(_FSMONITOR_BUILDER, encoding="utf-8")


@pytest.mark.skipif(os.name == "nt" or shutil.which("git") is None, reason="needs POSIX git")
def test_sols_fsmonitor_probe_starts_the_helper_only_outside_the_pinned_environment(
    tmp_path: Path,
) -> None:
    """Round 6, Sol B2 (r6_git_config_probe.py). Under the tool's own environment
    the declared `git status --porcelain` started the repository's fsmonitor
    helper, untraced, and the trace counted complete. Now (1) the trace of that
    run is incomplete, because it lacks the pinned environment; (2) under the
    lanes' environment the command-scope pin overrides the repository's setting,
    so the helper does not run; and (3) the clone is refused before any lane runs
    in it, because core.fsmonitor is not on the reviewed list."""
    _fsmonitor_repository(tmp_path)
    loose = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    result = _run(tmp_path, [sys.executable, str(TRACER), str(tmp_path / "loose"), "--",
                             "builder.py"], loose)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "fsmonitor-ran").is_file()  # the danger is real
    loose_trace = _trace(tmp_path / "loose")
    assert any("pinned hermetic environment" in reason for reason in
               changed.trace_incompleteness([loose_trace], ["git"], tmp_path))
    (tmp_path / "fsmonitor-ran").unlink()

    result = _run(tmp_path, [sys.executable, str(TRACER), str(tmp_path / "pinned"), "--",
                             "builder.py"], _lane_env(tmp_path))
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "fsmonitor-ran").exists()
    assert changed.trace_incompleteness([_trace(tmp_path / "pinned")], ["git"], tmp_path) == []

    problems = repo_mod.audit_repository(tmp_path)
    assert any("core.fsmonitor" in problem for problem in problems), problems


@pytest.mark.skipif(os.name == "nt" or shutil.which("git") is None, reason="needs POSIX git")
@pytest.mark.parametrize(("key", "value"), [
    ("filter.x.clean", "sh -c true"),
    ("filter.x.process", "helper"),
    ("diff.x.textconv", "helper"),
    ("diff.external", "helper"),
    ("core.pager", "helper"),
    ("core.sshCommand", "helper"),
    ("credential.helper", "helper"),
    ("alias.st", "!helper"),
    ("include.path", "/elsewhere/config"),
    ("includeIf.gitdir:/.path", "/elsewhere/config"),
    ("core.autocrlf", "true"),
    ("extensions.worktreeConfig", "true"),
    ("remote.origin.promisor", "true"),
    ("uploadpack.packObjectsHook", "helper"),
    ("core.gitProxy", "helper"),
])
def test_any_repository_configuration_off_the_reviewed_list_refuses_the_clone(
    tmp_path: Path, key: str, value: str
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=git_env())
    assert repo_mod.audit_repository(tmp_path) == []
    subprocess.run(["git", "-C", str(tmp_path), "config", key, value], check=True, env=git_env())
    problems = repo_mod.audit_repository(tmp_path)
    assert any(key.lower().split(".")[0] in problem.lower() for problem in problems), problems


@pytest.mark.skipif(os.name == "nt" or shutil.which("git") is None, reason="needs POSIX git")
@pytest.mark.parametrize(("where", "line"), [
    (".gitattributes", "*.py filter=x\n"),
    ("sub/.gitattributes", "*.json diff=x\n"),
    (".gitattributes", "[attr]m merge=x\n"),
    ("info", "* filter=x\n"),
])
def test_an_attributes_file_naming_a_driver_refuses_the_clone(
    tmp_path: Path, where: str, line: str
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=git_env())
    target = tmp_path / ".git" / "info" / "attributes" if where == "info" else tmp_path / where
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(line, encoding="utf-8")
    if where != "info":
        subprocess.run(["git", "-C", str(tmp_path), "add", where], check=True, env=git_env())
    problems = repo_mod.audit_repository(tmp_path)
    assert any("driver" in problem for problem in problems), problems


@pytest.mark.skipif(os.name == "nt" or shutil.which("git") is None, reason="needs POSIX git")
def test_a_clone_of_a_repository_with_a_helper_is_refused(tmp_path: Path) -> None:
    """detached_worktree audits every clone before a lane can run in it."""
    root = tmp_path / "repo"
    _fsmonitor_repository(root)
    for args in (("config", "user.email", "t@example.invalid"), ("config", "user.name", "t"),
                 ("add", "builder.py"), ("commit", "-q", "-m", "c")):
        subprocess.run(["git", "-C", str(root), *args], check=True, env=git_env())
    with (
        pytest.raises(repo_mod.Wp0Error, match="core.fsmonitor"),
        repo_mod.detached_worktree(root, "HEAD", tmp_path / "clones", "c"),
    ):
        pass


@pytest.mark.skipif(os.name == "nt" or shutil.which("git") is None, reason="needs POSIX git")
def test_the_ci_checkouts_persisted_header_passes_the_audit(tmp_path: Path) -> None:
    """actions/checkout persists its token as http.<url>.extraheader; the gate must
    still run in CI. Its value never appears in a refusal."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=git_env())
    subprocess.run(["git", "-C", str(tmp_path), "config", "http.https://github.com/.extraheader",
                    "AUTHORIZATION: basic secret-value"], check=True, env=git_env())
    assert repo_mod.audit_repository(tmp_path) == []
    subprocess.run(["git", "-C", str(tmp_path), "config", "core.pager", "secret-value"],
                   check=True, env=git_env())
    assert not any("secret-value" in p for p in repo_mod.audit_repository(tmp_path))


def test_the_live_repository_passes_the_audit() -> None:
    """The repository the tool runs from: its own configuration (hooksPath for the
    identifier gate, remotes, branches) is on the reviewed list, and its
    attributes name no driver."""
    assert repo_mod.audit_repository(PACKAGE.parents[2]) == []


_SEED_MAIN = (
    "import importlib\n"
    "importlib.import_module('dep_' + next(iter({'a', 'b'})))\n"
    "print('constant-output')\n"
)


def _seed_lines(root: Path, out: str, env: dict[str, str]) -> set[str]:
    result = _run(root, [sys.executable, str(TRACER), str(root / out), "--", "main.py"], env)
    assert result.returncode == 0 and result.stdout.strip() == b"constant-output", result.stderr
    return {Path(name).name for name in _trace(root / out)["lines"] if name.startswith(str(root))}


def test_the_pinned_hash_seed_makes_set_order_branches_repeatable(tmp_path: Path) -> None:
    """Round 6, Sol's Part B counterexample (r6_trace_probe.py): two hash seeds gave
    identical output but executed different dependency files. Every lane run
    now has PYTHONHASHSEED=0, and the trace records it."""
    for name in ("a", "b"):
        (tmp_path / f"dep_{name}.py").write_text(f"DEP_{name.upper()} = 1\n", "utf-8")
    (tmp_path / "main.py").write_text(_SEED_MAIN, "utf-8")
    base = {k: v for k, v in os.environ.items() if k != "PYTHONHASHSEED"}
    one = _seed_lines(tmp_path, "seed1", {**base, "PYTHONHASHSEED": "1"})
    two = _seed_lines(tmp_path, "seed2", {**base, "PYTHONHASHSEED": "2"})
    assert one != two  # the counterexample: same output, different files executed
    env = _lane_env(tmp_path)
    assert env["PYTHONHASHSEED"] == "0"
    first = _seed_lines(tmp_path, "pinned1", env)
    second = _seed_lines(tmp_path, "pinned2", env)
    assert first == second
    assert _trace(tmp_path / "pinned1")["environ"]["PYTHONHASHSEED"] == "0"
