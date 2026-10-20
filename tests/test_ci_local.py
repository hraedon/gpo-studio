"""Fast tests for ``scripts/ci-local.py``; never run the product check suite."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci-local.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_local", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ci_local() -> ModuleType:
    return _load()


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _wait_for_file(path: Path, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.is_file():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            pytest.fail(f"ci-local exited before {path.name}: {stdout}\n{stderr}")
        time.sleep(0.02)
    pytest.fail(f"timed out waiting for {path}")


def _process_is_stopped(pid: int) -> bool:
    stat = Path(f"/proc/{pid}/stat")
    if not stat.exists():
        return True
    return stat.read_text(encoding="utf-8").split()[2] == "Z"


def test_arguments_require_an_explicit_python(ci_local: ModuleType) -> None:
    with pytest.raises(SystemExit) as caught:
        ci_local.main([])
    assert caught.value.code == 2

    args = ci_local.parser().parse_args(
        ["--python", "python3.14", "--commit", "main", "--keep"]
    )
    assert args.python == "python3.14"
    assert args.commit == "main"
    assert args.keep is True
    assert args.windows is False


def test_commit_ref_beginning_with_dash_is_refused(
    ci_local: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as caught:
        ci_local.main(["--python", sys.executable, "--commit=-synthetic"])
    assert caught.value.code == 2
    assert "must not begin" in capsys.readouterr().err


def test_timeouts_are_derived_from_the_jobs_that_run_each_check(
    ci_local: ModuleType, tmp_path: Path
) -> None:
    workflow = tmp_path / "ci.yml"
    workflow.write_text(
        """
jobs:
  test:
    timeout-minutes: 7
  frontend:
    timeout-minutes: 11
  installed-package:
    timeout-minutes: 13
  static-safety:
    timeout-minutes: 17
""".lstrip(),
        encoding="utf-8",
    )
    commands = [
        "uv run ruff check .",
        "npm run test:browser",
        "bash scripts/installed_package_smoke.sh",
        "uv run python scripts/check_safety.py",
    ]

    steps = ci_local.derive_steps(commands, workflow)

    assert [(step.job, step.timeout_seconds) for step in steps] == [
        ("test", 7 * 60),
        ("frontend", 11 * 60),
        ("installed-package", 13 * 60),
        ("static-safety", 17 * 60),
    ]


def test_unknown_required_check_is_refused(ci_local: ModuleType, tmp_path: Path) -> None:
    workflow = tmp_path / "ci.yml"
    workflow.write_text("jobs: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no ci.yml job mapping"):
        ci_local.derive_steps(["curl https://example.invalid"], workflow)


def test_only_top_level_pytest_gets_external_basetemp(
    ci_local: ModuleType, tmp_path: Path
) -> None:
    steps = [
        ci_local.Step("pytest", "uv run pytest -q", "test", 60),
        ci_local.Step("other", "uv run ruff check .", "test", 60),
    ]

    adjusted = ci_local.with_external_pytest_base(steps, tmp_path / "pytest")

    assert adjusted[0].command == f"uv run pytest -q --basetemp={tmp_path / 'pytest'}"
    assert adjusted[1] == steps[1]


def test_preparation_uses_ci_timeouts_and_named_python(ci_local: ModuleType) -> None:
    steps = ci_local._preparation_steps(
        {"test": 55 * 60, "frontend": 20 * 60}, Path("/synthetic/python")
    )

    assert [step.job for step in steps] == ["test", "frontend", "frontend"]
    assert [step.timeout_seconds for step in steps] == [55 * 60, 20 * 60, 20 * 60]
    assert "/synthetic/python" in steps[0].command
    assert steps[2].command.endswith("install --with-deps chromium firefox")


def test_step_environment_drops_parent_package_configuration(
    ci_local: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inherited = {
        "PYTHONPATH": "/synthetic/pythonpath",
        "UV_INDEX_URL": "https://uv.example.invalid/simple",
        "PIP_INDEX_URL": "https://pip.example.invalid/simple",
        "npm_config_registry": "https://npm.example.invalid/",
    }
    for key, value in inherited.items():
        monkeypatch.setenv(key, value)
    run_root = tmp_path / "run"
    checkout = tmp_path / "checkout"
    run_root.mkdir()
    checkout.mkdir()
    for name in ("tmp", "uv-cache", "npm-cache", "playwright-browsers"):
        (run_root / name).mkdir()
    output = tmp_path / "environment.json"
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import json, os, pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps(dict(os.environ)))\n",
        encoding="utf-8",
    )
    environment = ci_local.step_environment(
        run_root=run_root, checkout=checkout, python=Path(sys.executable)
    )
    command = " ".join(
        shlex.quote(value) for value in (sys.executable, str(probe), str(output))
    )

    passed, _duration = ci_local.run_step(
        ci_local.Step("environment probe", command, "test", 10),
        checkout=checkout,
        environment=environment,
    )

    assert passed
    child_environment = json.loads(output.read_text(encoding="utf-8"))
    assert not inherited.keys() & child_environment.keys()
    assert child_environment["PIP_CONFIG_FILE"] == os.devnull
    npm_userconfig = Path(child_environment["NPM_CONFIG_USERCONFIG"])
    assert npm_userconfig.read_bytes() == b""
    assert Path(child_environment["HOME"]).parent == run_root
    assert Path(child_environment["XDG_CONFIG_HOME"]).parent == run_root


@pytest.mark.parametrize("requested_signal", [signal.SIGINT, signal.SIGTERM])
def test_signal_stops_real_child_process_tree_and_cleans_run_directory(
    tmp_path: Path, requested_signal: signal.Signals
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Synthetic Test")
    _git(repository, "config", "user.email", "synthetic@example.invalid")
    (repository / ".github" / "workflows").mkdir(parents=True)
    (repository / "CONTRIBUTING.md").write_text(
        "# Synthetic\n\n## Required checks\n\n```bash\nuv run ruff check .\n```\n",
        encoding="utf-8",
    )
    (repository / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  test:\n    timeout-minutes: 1\n"
        "  frontend:\n    timeout-minutes: 1\n",
        encoding="utf-8",
    )
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "fixture")

    state = tmp_path / "state"
    state.mkdir()
    step_pid = state / "step.pid"
    grandchild_pid = state / "grandchild.pid"
    grandchild_code = (
        "import os, pathlib, signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(grandchild_pid)!r}).write_text(str(os.getpid())); "
        "time.sleep(60)"
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, subprocess, time\n"
        f"pathlib.Path({str(step_pid)!r}).write_text(str(os.getpid()))\n"
        f"subprocess.Popen([{sys.executable!r}, '-c', {grandchild_code!r}])\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "TMPDIR": str(temp_root),
    }
    process = subprocess.Popen(
        [sys.executable, str(SCRIPT), "--python", sys.executable],
        cwd=repository,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    child_pids: list[int] = []
    try:
        _wait_for_file(grandchild_pid, process)
        child_pids = [int(step_pid.read_text()), int(grandchild_pid.read_text())]
        os.kill(process.pid, requested_signal)
        stdout, stderr = process.communicate(timeout=10)

        assert process.returncode == 128 + requested_signal
        assert requested_signal.name in stderr
        assert "Run directory:" in stdout
        assert not list(temp_root.glob("gpo-studio-ci-local-*"))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not all(
            _process_is_stopped(pid) for pid in child_pids
        ):
            time.sleep(0.02)
        assert all(_process_is_stopped(pid) for pid in child_pids)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for pid in child_pids:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


def test_windows_leg_refuses_with_d7_reason(
    ci_local: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as caught:
        ci_local.main(["--windows"])
    assert caught.value.code == 2
    error = capsys.readouterr().err
    assert "D-7" in error
    assert "other users agree" in error


def test_fresh_clone_is_detached_unconverted_and_cleanup_is_external(
    ci_local: ModuleType, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-b", "main")
    _git(source, "config", "user.name", "Synthetic Test")
    _git(source, "config", "user.email", "synthetic@example.invalid")
    (source / "probe.txt").write_bytes(b"one\ntwo\n")
    _git(source, "add", "probe.txt")
    _git(source, "commit", "-m", "fixture")
    commit = _git(source, "rev-parse", "HEAD")
    run_root = tmp_path / "run"
    run_root.mkdir()

    clone = ci_local.fresh_clone(source, commit, run_root)

    assert (clone / "probe.txt").read_bytes() == b"one\ntwo\n"
    assert _git(clone, "rev-parse", "HEAD") == commit
    assert _git(clone, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    configured = subprocess.run(
        ["git", "config", "--local", "--get", "core.autocrlf"],
        cwd=clone,
        check=False,
        capture_output=True,
        text=True,
    )
    assert configured.returncode != 0
    assert run_root.parent == tmp_path


def test_main_removes_run_tree_when_clone_fails(
    ci_local: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root = tmp_path / "allocated"
    run_root.mkdir()
    monkeypatch.setattr(ci_local.tempfile, "mkdtemp", lambda **_kwargs: str(run_root))
    monkeypatch.setattr(ci_local, "resolve_interpreter", lambda _value: Path("/python"))
    monkeypatch.setattr(ci_local, "repository_root", lambda _cwd: tmp_path)
    monkeypatch.setattr(ci_local, "_run_git", lambda *_args, **_kwargs: "a" * 40)

    def fail_clone(*_args: object, **_kwargs: object) -> Path:
        raise RuntimeError("synthetic clone failure")

    monkeypatch.setattr(ci_local, "fresh_clone", fail_clone)

    assert ci_local.main(["--python", "fixture"]) == 2
    assert not run_root.exists()


def test_main_keeps_run_tree_on_request(
    ci_local: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root = tmp_path / "kept"
    run_root.mkdir()
    monkeypatch.setattr(ci_local.tempfile, "mkdtemp", lambda **_kwargs: str(run_root))
    monkeypatch.setattr(ci_local, "resolve_interpreter", lambda _value: Path("/python"))
    monkeypatch.setattr(ci_local, "repository_root", lambda _cwd: tmp_path)
    monkeypatch.setattr(ci_local, "_run_git", lambda *_args, **_kwargs: "a" * 40)
    monkeypatch.setattr(
        ci_local,
        "fresh_clone",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic failure")),
    )

    assert ci_local.main(["--python", "fixture", "--keep"]) == 2
    assert run_root.is_dir()
