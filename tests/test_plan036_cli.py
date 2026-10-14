"""Plan 036 WP-0: the command line and the uv environment builder.

Review round 1 (DeepSeek N5): the `run` and `write-records` paths were covered
only by a manual dry run, which is how the cold-cache environment failure
(DeepSeek B1) went unseen. These tests drive `cli.main` with the heavy
`record.run` replaced, asserting the launcher it is given, and drive
`replay.uv_environment` against a stub `uv` that records every invocation, so
the environment method (and its refusal to fall back) is exercised without a
network or a real sync.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from plan036_support import load

cli = load("cli")
record = load("record")
replay = load("replay")
repo_mod = load("repo")


class _Stop(Exception):
    pass


def _capture_run(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
        seen["args"], seen["kwargs"] = args, kwargs
        raise _Stop

    monkeypatch.setattr(record, "run", fake_run)
    return seen


@pytest.mark.parametrize("launcher", [None, "uv", "current"])
def test_write_records_hands_its_launcher_to_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, launcher: str | None
) -> None:
    seen = _capture_run(monkeypatch)
    monkeypatch.setattr(cli, "is_clean", lambda _root: True)
    argv = ["write-records", "--workdir", str(tmp_path / "w"), "--python-version", "3.14"]
    if launcher:
        argv += ["--launcher", launcher]
    with pytest.raises(_Stop):
        cli.main(argv)
    assert seen["kwargs"]["launcher"] == (launcher or "uv")


def test_write_records_refuses_a_dirty_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _capture_run(monkeypatch)
    monkeypatch.setattr(cli, "is_clean", lambda _root: False)
    code = cli.main(["write-records", "--workdir", str(tmp_path / "w"),
                     "--python-version", "3.14"])
    assert code == 2 and "clean tree" in capsys.readouterr().err


def test_run_hands_its_launcher_and_lanes_to_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _capture_run(monkeypatch)
    with pytest.raises(_Stop):
        cli.main(["run", "--target", "HEAD", "--workdir", str(tmp_path / "w"),
                  "--lanes", "wp2,endpoint"])
    assert seen["kwargs"]["launcher"] == "uv"
    assert seen["kwargs"]["lanes_wanted"] == {"wp2", "endpoint"}


def test_write_records_refuses_an_unequal_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    part1 = {"completeness": [], "unused_declarations": [],
             "lanes": {"a": {"scan": {"failing": []}}}}
    result = {"part1": part1, "lanes": {"a": {
        "replay": {"applicable": True, "identical": False, "problems": ["x"]},
        "regrade_equal": True, "fidelity_equal": True}}}
    monkeypatch.setattr(record, "run", lambda *a, **k: result)
    monkeypatch.setattr(cli, "is_clean", lambda _root: True)
    code = cli.main(["write-records", "--workdir", str(tmp_path / "w"),
                     "--python-version", "3.14"])
    assert code == 1 and "replay differs" in capsys.readouterr().err


def test_select_converges_over_its_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = []
    for index, option2 in enumerate((["a"], ["a"])):
        path = tmp_path / f"pass{index}.json"
        path.write_text(json.dumps({"option2": option2, "option3": ["a", "b"]}), "utf-8")
        paths.append(str(path))
    assert cli.main(["select", *paths]) == 0
    assert json.loads(capsys.readouterr().out)


def test_check_pin_and_check_bank_without_a_pin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    assert cli.main(["check-pin"]) == 1
    assert cli.main(["check-bank", "--freeze", "HEAD"]) == 1
    assert "no pin" in capsys.readouterr().out


# --- the uv environment builder, against a stub uv -----------------------------------------

STUB = r'''#!{python}
import json, os, sys
from pathlib import Path
log = Path(os.environ["STUB_UV_LOG"])
with log.open("a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd()}}) + "\n")
argv = sys.argv[1:]
if os.environ.get("STUB_UV_FAIL") and argv[:1] == [os.environ["STUB_UV_FAIL"]]:
    print("stub failure", file=sys.stderr)
    sys.exit(2)
if argv[:1] == ["--version"]:
    print("uv 0.12.0 (stub)")
elif argv[:1] == ["sync"]:
    bindir = Path(os.getcwd()) / ".venv" / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    (bindir / "python").symlink_to({python!r})
elif argv[:1] == ["export"]:
    print("hatchling==1.32.4 \\\n    --hash=sha256:" + "0" * 64)
elif argv[:2] == ["pip", "freeze"]:
    print("-e file://" + os.getcwd())
    print("hatchling==1.32.4")
    print("fastapi==0.139.0")
'''


@pytest.fixture
def stub_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bindir = tmp_path / "stub-bin"
    bindir.mkdir()
    uv = bindir / "uv"
    uv.write_text(STUB.format(python=sys.executable), encoding="utf-8")
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "uv-log.jsonl"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_UV_LOG", str(log))
    return log


def _tree(tmp_path: Path, name: str, lock: str, *, commit: bool = False) -> Path:
    path = tmp_path / name
    path.mkdir()
    (path / "uv.lock").write_text(lock, encoding="utf-8")
    if commit:  # the tool tree: its lock must be committed for the record to name it
        for args in (("init", "-q"), ("config", "user.email", "t@example.invalid"),
                     ("config", "user.name", "t"), ("add", "-A"), ("commit", "-q", "-m", "t")):
            subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    return path


def _calls(log: Path) -> list[list[str]]:
    return [json.loads(line)["argv"] for line in log.read_text("utf-8").splitlines()]


@pytest.mark.skipif(os.name == "nt", reason="the stub is a POSIX script")
def test_an_old_lock_gets_the_tool_trees_pinned_backend(stub_uv: Path, tmp_path: Path) -> None:
    """A commit whose lock locks no backend (before 547626b) syncs its own lock without
    building anything, installs the backend from the tool tree's lock by hash, then
    builds the project offline with no isolation; all of it is recorded."""
    clone = _tree(tmp_path, "clone", 'version = 1\n[[package]]\nname = "fastapi"\n')
    tool = _tree(tmp_path, "tool", '[[package]]\nname = "hatchling"\n', commit=True)
    python, built = replay.uv_environment(clone, "a" * 40, tool)
    calls = _calls(stub_uv)
    assert calls[0] == ["sync", "--locked", "--no-install-project", "--no-build", "--quiet"]
    assert calls[1][:5] == ["export", "--locked", "--only-group", "build-backend",
                            "--no-emit-project"]
    assert "--require-hashes" in calls[2] and "--no-build" in calls[2]
    assert calls[3][-2:] == ["-e", str(clone)] and "--offline" in calls[3]
    assert "--no-build-isolation" in calls[3]
    assert built["method"] == replay.UV_METHOD and built["commit"] == "a" * 40
    assert "tool tree" in built["backend_source"]
    assert built["backend_lock_sha256"] == repo_mod.sha256_file(tool / "uv.lock")
    assert built["backend_lock_commit"] == repo_mod.git(tool, "rev-parse", "HEAD").strip()
    assert built["backend"] == ["hatchling==1.32.4"]
    assert "-e <clone>" in built["installed"]
    assert not any(str(tmp_path) in " ".join(step) for step in built["steps"])
    assert python == [str(clone / ".venv" / "bin" / "python")]


@pytest.mark.skipif(os.name == "nt", reason="the stub is a POSIX script")
def test_an_uncommitted_tool_lock_is_refused(stub_uv: Path, tmp_path: Path) -> None:
    """The backend pins must come from a committed lock the record can name."""
    clone = _tree(tmp_path, "clone", "version = 1\n")
    tool = _tree(tmp_path, "tool", '[[package]]\nname = "hatchling"\n', commit=True)
    (tool / "uv.lock").write_text('[[package]]\nname = "hatchling"\n# edited\n', "utf-8")
    with pytest.raises(repo_mod.Wp0Error, match="differs from its HEAD"):
        replay.uv_environment(clone, "d" * 40, tool)


@pytest.mark.skipif(os.name == "nt", reason="the stub is a POSIX script")
def test_a_lock_with_its_own_backend_uses_it(stub_uv: Path, tmp_path: Path) -> None:
    clone = _tree(tmp_path, "clone", '[[package]]\nname = "hatchling"\n')
    tool = _tree(tmp_path, "tool", '[[package]]\nname = "hatchling"\n', commit=True)
    _python, built = replay.uv_environment(clone, "b" * 40, tool)
    assert not any(call[:1] == ["export"] for call in _calls(stub_uv))
    assert "own uv.lock" in built["backend_source"]
    assert built["backend_lock_sha256"] == repo_mod.sha256_file(clone / "uv.lock")


@pytest.mark.skipif(os.name == "nt", reason="the stub is a POSIX script")
@pytest.mark.parametrize("failing", ["sync", "export", "pip"])
def test_a_failed_uv_step_refuses_and_never_falls_back(
    stub_uv: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    monkeypatch.setenv("STUB_UV_FAIL", failing)
    clone = _tree(tmp_path, "clone", "version = 1\n")
    tool = _tree(tmp_path, "tool", '[[package]]\nname = "hatchling"\n', commit=True)
    with pytest.raises(repo_mod.Wp0Error, match="stub failure"):
        replay.uv_environment(clone, "c" * 40, tool)


@pytest.mark.skipif(os.name == "nt", reason="the stub is a POSIX script")
def test_a_uv_clone_carries_its_environment_record(stub_uv: Path, tmp_path: Path) -> None:
    """`replay.clone(launcher="uv")` builds through `uv_environment` and hands the record on."""
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              check=True).stdout.decode("utf-8").strip()

    git("init", "-q")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "c")
    commit = git("rev-parse", "HEAD")
    tool = _tree(tmp_path, "tool", '[[package]]\nname = "hatchling"\n', commit=True)
    with replay.clone(root, commit, tmp_path / "clones", "x", launcher="uv",
                      tool_root=tool) as made:
        assert made.environment["method"] == replay.UV_METHOD
        assert made.environment["commit"] == commit
        assert made.env["VIRTUAL_ENV"] == str(made.path / ".venv")


def test_the_analysis_defaults_to_the_lanes_interpreter() -> None:
    """DeepSeek round 2, N3: `derive` without --python-version analysed for the running
    interpreter and was red on Python 3.13; it now analyses for the lanes' one."""
    assert cli._python_version(None) == record.LANE_PYTHON == "3.14"
    assert cli._python_version("3.13") == "3.13"
