"""Fast tests for ``scripts/ci-local.py``; never run the product check suite."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
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
