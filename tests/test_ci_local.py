"""Fast tests for ``scripts/ci-local.py``; never run the product check suite."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

if sys.platform == "win32":
    pytest.skip(
        "ci-local uses a Linux controller for both its local and remote Windows legs",
        allow_module_level=True,
    )

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


def _wait_for_stopped_processes(pids: list[int]) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not all(_process_is_stopped(pid) for pid in pids):
        time.sleep(0.02)
    assert all(_process_is_stopped(pid) for pid in pids)


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(0o755)


def test_arguments_require_an_explicit_python(ci_local: ModuleType) -> None:
    with pytest.raises(SystemExit) as caught:
        ci_local.main([])
    assert caught.value.code == 2

    args = ci_local.parser().parse_args(["--python", "python3.14", "--commit", "main", "--keep"])
    assert args.python == "python3.14"
    assert args.commit == "main"
    assert args.keep is True
    assert args.windows is False

    windows = ci_local.parser().parse_args(
        ["--windows", "--windows-host", "builder@synthetic", "--windows-python", "3.13"]
    )
    assert windows.windows_host == "builder@synthetic"
    assert windows.windows_python == "3.13"


def test_windows_ssh_command_is_an_encoded_noninteractive_powershell_command(
    ci_local: ModuleType,
) -> None:
    script = "Write-Output 'quoted value'; Write-Output 'café'"

    command = ci_local.powershell_ssh_command("builder@synthetic", script)

    assert command[:-2] == [
        "ssh",
        *ci_local.SSH_OPTIONS,
        "builder@synthetic",
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
    ]
    import base64

    assert base64.b64decode(command[-2]).decode("utf-16-le") == script
    assert command[-1] == "; exit $LASTEXITCODE"


def test_powershell_literal_doubles_quotes_without_shell_interpolation(
    ci_local: ModuleType,
) -> None:
    assert ci_local._powershell_literal(r"C:\Synthetic User\owner's file") == (
        r"'C:\Synthetic User\owner''s file'"
    )


def test_windows_tool_digest_mismatch_is_refused(ci_local: ModuleType) -> None:
    ci_local.verify_windows_uv_digest(ci_local.WINDOWS_UV_SHA256.lower())
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        ci_local.verify_windows_uv_digest("0" * 64)
    ci_local.verify_windows_git_digest(ci_local.WINDOWS_GIT_SHA256.lower())
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        ci_local.verify_windows_git_digest("0" * 64)


@pytest.mark.parametrize("digest", ["0" * 64, ""])
def test_windows_preflight_refuses_an_untrusted_git_before_creating_a_run(
    ci_local: ModuleType, monkeypatch: pytest.MonkeyPatch, digest: str
) -> None:
    scripts: list[str] = []
    python = ci_local.WINDOWS_PYTHONS["3.14"]

    def remote(_host: str, script: str, *, timeout: float) -> str:
        scripts.append(script)
        return f"UV_SHA256={ci_local.WINDOWS_UV_SHA256}\nGIT_SHA256={digest}\nPYTHON={python}\n"

    monkeypatch.setattr(ci_local, "_run_remote_capture", remote)
    with pytest.raises(RuntimeError, match="refusing Windows Git: SHA-256 mismatch"):
        ci_local.windows_preflight("builder@synthetic", python, timeout=30)

    assert len(scripts) == 1
    assert ci_local.WINDOWS_GIT in scripts[0]
    assert "Get-Command git" not in scripts[0]
    assert "New-Item" not in scripts[0]


def test_windows_phase_hashes_tools_and_prepends_only_the_pinned_git_directory(
    ci_local: ModuleType,
) -> None:
    workspace = ci_local.WindowsWorkspace(
        run=r"C:\Users\synthetic\.ci-local\runs\gpo-studio-ci-local\abc",
        checkout=r"C:\Users\synthetic\.ci-local\runs\gpo-studio-ci-local\abc\checkout",
        archive=r"C:\Users\synthetic\.ci-local\runs\gpo-studio-ci-local\abc\source.zip",
        bundle=r"C:\Users\synthetic\.ci-local\runs\gpo-studio-ci-local\abc\source.bundle",
    )

    script = ci_local._windows_phase_script(
        workspace,
        ci_local.WINDOWS_PYTHONS["3.14"],
        ("run", "--locked", "pytest", "-q"),
    )

    assert f"$git='{ci_local.WINDOWS_GIT}'" in script
    assert ci_local.WINDOWS_GIT_SHA256 in script
    assert "$actualGit=(Get-FileHash -Algorithm SHA256 $git).Hash" in script
    assert "$env:PATH=$gitDir + [IO.Path]::PathSeparator + $env:PATH" in script
    assert "$process=[System.Diagnostics.Process]::new()" in script
    assert "$process.StartInfo.UseShellExecute=$false" in script
    assert "$process.StartInfo.Arguments='run --locked pytest -q'" in script
    assert f"$process.StartInfo.WorkingDirectory='{workspace.checkout}'" in script
    assert "$process.StartInfo.RedirectStandardOutput=$true" in script
    assert "$process.StartInfo.RedirectStandardError=$true" in script
    assert "if ($null -eq $code) { throw" in script
    assert "if ($process.HasExited)" in script
    assert "phase-owner" in script
    assert "[Environment]::SetEnvironmentVariable" not in script


def test_windows_setup_restores_git_metadata_and_cleanup_owns_the_phase_tree(
    ci_local: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = ci_local.WindowsWorkspace(
        run=r"C:\Users\synthetic\.ci-local\runs\gpo-studio-ci-local\abc",
        checkout=r"C:\Users\synthetic\.ci-local\runs\gpo-studio-ci-local\abc\checkout",
        archive=r"C:\Users\synthetic\.ci-local\runs\gpo-studio-ci-local\abc\source.zip",
        bundle=r"C:\Users\synthetic\.ci-local\runs\gpo-studio-ci-local\abc\source.bundle",
    )
    setup = ci_local._windows_setup_script(workspace, "a" * 40)
    captured: list[str] = []
    monkeypatch.setattr(
        ci_local,
        "_run_remote_capture",
        lambda _host, script, *, timeout: captured.append(script) or "",
    )

    ci_local.windows_cleanup("builder@synthetic", workspace)

    assert ci_local.WINDOWS_GIT_SHA256 in setup
    assert "config --local core.longpaths true" in setup
    assert "fetch --quiet --force $bundle" in setup
    assert "checkout --quiet --force --detach $commit" in setup
    assert setup.index("Set-Content -LiteralPath $owner") < setup.index("Expand-Archive")
    cleanup = captured[0]
    assert "StartTime.ToFileTimeUtc() -eq $started" in cleanup
    assert "taskkill.exe /PID $processId /T /F" in cleanup
    assert "$attempt -lt 20" in cleanup


def test_clixml_errors_are_decoded(ci_local: ModuleType) -> None:
    encoded = (
        '#< CLIXML\n<Objs xmlns="http://schemas.microsoft.com/powershell/2004/04">'
        '<S S="Error">first_x000D__x000A_second</S></Objs>'
    )
    assert ci_local.decode_powershell_output(encoded) == "first\r\nsecond"


@pytest.mark.parametrize(
    ("record", "age", "refused"),
    [
        ("live", 0, "live sibling"),
        ("live", 7000, "live sibling"),
        ("missing", 0, "recent unowned sibling"),
        ("missing", 5999, "recent unowned sibling"),
        ("missing", 7000, None),
        ("dead", 0, None),
        ("reused", 0, None),
        ("invalid", 7000, "invalid phase-owner"),
    ],
)
def test_windows_startup_behaviour_never_touches_a_live_or_recent_sibling(
    ci_local: ModuleType, tmp_path: Path, record: str, age: int, refused: str | None
) -> None:
    """Execute the production PowerShell, not a Python facsimile or substring check.

    PowerShell is available on this controller and the Linux CI image. No SSH,
    product checks, Windows-only tools or network access are used.
    """
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell is required for native ownership behaviour tests")
    root = tmp_path / ".ci-local" / "runs" / ci_local.WINDOWS_RUN_PREFIX
    sibling = root / ("a" * 24)
    sibling.mkdir(parents=True)
    sentinel = sibling / "do-not-touch"
    sentinel.write_text("original", encoding="ascii")
    target = ci_local._powershell_literal(str(sibling))
    fixture = f"$sibling={target}\n"
    if record != "missing":
        value = {
            "live": "$PID.ToString() + '|' + (Get-Process -Id $PID).StartTime.ToFileTimeUtc()",
            "dead": "'2147483647|1'",
            "reused": "$PID.ToString() + '|1'",
            "invalid": "'broken'",
        }[record]
        fixture += f"Set-Content (Join-Path $sibling 'phase-owner') -Value ({value})\n"
    fixture += (
        f"(Get-Item $sibling).LastWriteTimeUtc=[DateTime]::UtcNow.AddSeconds(-{age})\n"
        "function taskkill.exe { throw 'TEST: startup attempted to kill a sibling' }\n"
    )
    script = ci_local._windows_initialize_script("b" * 24).replace(
        "$HOME", ci_local._powershell_literal(str(tmp_path))
    )
    completed = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", fixture + script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if refused:
        assert completed.returncode != 0
        assert refused in completed.stderr
        assert sentinel.read_text(encoding="ascii") == "original"
        assert not (root / ("b" * 24)).exists()
    else:
        assert completed.returncode == 0, completed.stderr
        assert not sibling.exists()
        assert (root / ("b" * 24)).is_dir()


def test_windows_remove_tolerates_a_directory_disappearing_mid_delete(
    ci_local: ModuleType, tmp_path: Path
) -> None:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell is required for native ownership behaviour tests")
    target = tmp_path / "vanishes"
    target.mkdir()
    # Inject the race at the filesystem operation, execute the real catch/retry.
    script = ci_local._powershell_prelude() + "\n" + ci_local._windows_remove_owned_run_function()
    script += """
function Remove-Item {
    param($LiteralPath, [switch]$Recurse, [switch]$Force, $ErrorAction)
    [IO.Directory]::Delete($LiteralPath, $true)
    throw 'directory disappeared during traversal'
}
"""
    script += f"Remove-OwnedRun {ci_local._powershell_literal(str(target))}\n"
    completed = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    assert not target.exists()


def test_windows_startup_lock_refuses_a_racing_initializer(
    ci_local: ModuleType, tmp_path: Path
) -> None:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell is required for native ownership behaviour tests")
    root = tmp_path / ".ci-local" / "runs" / ci_local.WINDOWS_RUN_PREFIX
    root.mkdir(parents=True)
    fixture = (
        f"$held=[IO.File]::Open({ci_local._powershell_literal(str(root) + '.startup-lock')}, "
        "'OpenOrCreate', 'ReadWrite', 'None')\n"
    )
    script = ci_local._windows_initialize_script("b" * 24).replace(
        "$HOME", ci_local._powershell_literal(str(tmp_path))
    )
    completed = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", fixture + script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode != 0
    assert "refusing concurrent startup" in completed.stderr
    assert not list(root.iterdir())


def test_windows_commands_and_matrix_come_from_the_tested_workflow(ci_local: ModuleType) -> None:
    import yaml

    job = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]["test-windows"]
    timeout, versions, phases = ci_local._windows_workflow_contract(ROOT, "HEAD")
    assert timeout == job["timeout-minutes"] * 60
    assert set(versions) == set(job["strategy"]["matrix"]["python-version"])
    assert set(versions) == set(ci_local.WINDOWS_PYTHONS)
    assert phases == tuple(
        (step["name"], tuple(shlex.split(step["run"])[1:]))
        for step in job["steps"]
        if "run" in step
    )
    # Historical workflow deliberately differs: don't silently run HEAD commands.
    _timeout, _versions, old_phases = ci_local._windows_workflow_contract(ROOT, "2f21ceb4")
    assert "-I" not in old_phases[-1][1]
    assert "-I" in phases[-1][1]


@pytest.mark.parametrize("mutation", ["matrix", "condition", "shell", "action"])
def test_windows_workflow_refuses_unsupported_contract_changes(
    ci_local: ModuleType, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    import yaml

    document = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    job = document["jobs"]["test-windows"]
    if mutation == "matrix":
        job["strategy"]["matrix"]["python-version"].append("3.99")
    elif mutation == "condition":
        job["steps"][-1]["if"] = "false"
    elif mutation == "shell":
        job["steps"][-1]["run"] += " && echo unexpected"
    else:
        job["steps"].append({"uses": "synthetic/action@v1"})
    monkeypatch.setattr(ci_local, "_run_git", lambda *_args, **_kwargs: yaml.safe_dump(document))
    with pytest.raises(ValueError):
        ci_local._windows_workflow_contract(ROOT, "a" * 40)


def test_windows_bundle_excludes_private_refs_and_never_writes_source_refs(
    ci_local: ModuleType, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.name", "Synthetic")
    _git(source, "config", "user.email", "synthetic@example.test")
    (source / "tracked").write_text("original", encoding="ascii")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "synthetic")
    commit = _git(source, "rev-parse", "HEAD")
    _git(source, "update-ref", "refs/remotes/origin/main", commit)
    _git(source, "tag", "synthetic-tag")
    _git(source, "update-ref", "refs/backup/private", commit)
    old_ref = "refs/ci-local/" + "a" * 24
    recent_ref = "refs/ci-local/" + "b" * 24
    for ref in (old_ref, recent_ref):
        _git(source, "update-ref", ref, commit)
    ref_file = source / _git(source, "rev-parse", "--git-path", old_ref)
    os.utime(ref_file, (time.time() - 7000, time.time() - 7000))
    ci_local.sweep_legacy_windows_refs(source)
    assert old_ref not in _git(source, "show-ref")
    assert recent_ref in _git(source, "show-ref")
    before = _git(source, "show-ref")
    ci_local.create_windows_bundle(source, commit, tmp_path / "source.bundle", "a" * 24)
    refs = _git(source, "bundle", "list-heads", str(tmp_path / "source.bundle"))
    assert "refs/heads/main" in refs
    assert "refs/tags/synthetic-tag" in refs
    assert "refs/backup/" not in refs
    assert recent_ref not in refs
    assert before == _git(source, "show-ref")


def test_windows_job_uses_one_decreasing_timeout_budget(
    ci_local: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = ci_local.WindowsWorkspace(
        run=r"C:\Users\synthetic\.ci-local\runs\gpo-studio-ci-local\abc",
        checkout=r"C:\Users\synthetic\.ci-local\runs\gpo-studio-ci-local\abc\checkout",
        archive=r"C:\Users\synthetic\.ci-local\runs\gpo-studio-ci-local\abc\source.zip",
        bundle=r"C:\Users\synthetic\.ci-local\runs\gpo-studio-ci-local\abc\source.bundle",
    )
    clock = iter(float(value) for value in range(100, 200))
    timeouts: list[float] = []
    cleaned: list[str] = []
    monkeypatch.setattr(ci_local.time, "monotonic", lambda: next(clock))
    contract = ci_local._windows_workflow_contract(ROOT, "HEAD")
    monkeypatch.setattr(ci_local, "_windows_workflow_contract", lambda *_: (90, *contract[1:]))
    monkeypatch.setattr(
        ci_local,
        "windows_preflight",
        lambda _host, _python, *, timeout: timeouts.append(timeout) or True,
    )
    monkeypatch.setattr(
        ci_local,
        "windows_initialize",
        lambda _host, _token, *, timeout: timeouts.append(timeout) or workspace,
    )
    monkeypatch.setattr(ci_local, "_run_git", lambda *_args, **_kwargs: "")

    def streamed(_arguments: object, *, timeout: float) -> int:
        timeouts.append(timeout)
        return 0

    monkeypatch.setattr(ci_local, "_run_streamed", streamed)
    monkeypatch.setattr(
        ci_local,
        "windows_cleanup",
        lambda _host, target, *, timeout=60: cleaned.append(target.run),
    )

    assert ci_local.run_windows_job(
        source=tmp_path,
        commit="a" * 40,
        host="builder@synthetic",
        version="3.14",
        local_root=tmp_path,
    )
    assert all(timeouts[index + 1] < timeouts[index] for index in range(len(timeouts) - 1))
    assert cleaned == [workspace.run]


@pytest.mark.parametrize("failure", ["return", "interrupt"])
def test_windows_job_attempts_remote_cleanup_on_failure_and_interruption(
    ci_local: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    workspace = ci_local.WindowsWorkspace(
        run=r"C:\Users\synthetic\.ci-local\runs\gpo-studio-ci-local\abc",
        checkout=r"C:\Users\synthetic\.ci-local\runs\gpo-studio-ci-local\abc\checkout",
        archive=r"C:\Users\synthetic\.ci-local\runs\gpo-studio-ci-local\abc\source.zip",
        bundle=r"C:\Users\synthetic\.ci-local\runs\gpo-studio-ci-local\abc\source.bundle",
    )
    cleaned: list[str] = []
    contract = ci_local._windows_workflow_contract(ROOT, "HEAD")
    monkeypatch.setattr(ci_local, "_windows_workflow_contract", lambda *_: (90, *contract[1:]))
    monkeypatch.setattr(ci_local, "windows_preflight", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ci_local, "windows_initialize", lambda *_args, **_kwargs: workspace)
    monkeypatch.setattr(ci_local, "_run_git", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        ci_local,
        "windows_cleanup",
        lambda _host, target, *, timeout=60: cleaned.append(target.run),
    )

    if failure == "return":
        monkeypatch.setattr(ci_local, "_run_streamed", lambda *_args, **_kwargs: 1)
        assert not ci_local.run_windows_job(
            source=tmp_path,
            commit="a" * 40,
            host="builder@synthetic",
            version="3.14",
            local_root=tmp_path,
        )
    else:

        def interrupt(*_args: object, **_kwargs: object) -> int:
            raise ci_local._SignalInterruption(signal.SIGTERM)

        monkeypatch.setattr(ci_local, "_run_streamed", interrupt)
        with pytest.raises(ci_local._SignalInterruption):
            ci_local.run_windows_job(
                source=tmp_path,
                commit="a" * 40,
                host="builder@synthetic",
                version="3.14",
                local_root=tmp_path,
            )

    assert cleaned == [workspace.run]


@pytest.mark.parametrize("interrupted", [False, True])
def test_windows_job_cleans_by_token_if_initialization_never_returns_a_workspace(
    ci_local: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupted: bool
) -> None:
    tokens: list[str] = []
    cleaned: list[tuple[object, str | None]] = []
    contract = ci_local._windows_workflow_contract(ROOT, "HEAD")
    monkeypatch.setattr(ci_local, "_windows_workflow_contract", lambda *_: (90, *contract[1:]))
    monkeypatch.setattr(ci_local, "windows_preflight", lambda *_args, **_kwargs: True)

    def initialize(_host: str, token: str, *, timeout: float) -> object:
        tokens.append(token)
        if interrupted:
            raise ci_local._SignalInterruption(signal.SIGTERM)
        raise RuntimeError("initialization response was lost")

    def cleanup(
        _host: str, workspace: object, *, token: str | None = None, timeout: float = 60
    ) -> None:
        cleaned.append((workspace, token))

    monkeypatch.setattr(ci_local, "windows_initialize", initialize)
    monkeypatch.setattr(ci_local, "windows_cleanup", cleanup)
    monkeypatch.setattr(ci_local, "_run_git", lambda *_args, **_kwargs: "")
    exception = ci_local._SignalInterruption if interrupted else RuntimeError
    with pytest.raises(exception):
        ci_local.run_windows_job(
            source=tmp_path,
            commit="a" * 40,
            host="builder@synthetic",
            version="3.14",
            local_root=tmp_path,
        )

    assert len(tokens) == 1
    assert cleaned == [(None, tokens[0])]


@pytest.mark.parametrize("token", [None, "", "../outside", "a" * 23, "A" * 24])
def test_windows_cleanup_without_a_workspace_refuses_invalid_tokens(
    ci_local: ModuleType, token: str | None
) -> None:
    with pytest.raises(ValueError, match="valid run token"):
        ci_local.windows_cleanup("builder@synthetic", None, token=token)


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


def test_required_checks_parser_does_not_search_later_sections(
    ci_local: ModuleType, tmp_path: Path
) -> None:
    contributing = tmp_path / "CONTRIBUTING.md"
    contributing.write_text(
        "## Required checks\n\nNo commands here.\n\n"
        "## Unrelated\n\n```bash\necho must-not-run\n```\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="no bash block"):
        ci_local.required_commands(contributing)


def test_only_top_level_pytest_gets_external_basetemp(ci_local: ModuleType, tmp_path: Path) -> None:
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

    assert [step.job for step in steps] == ["test", "frontend", "frontend", "frontend"]
    assert [step.timeout_seconds for step in steps] == [
        55 * 60,
        20 * 60,
        20 * 60,
        20 * 60,
    ]
    assert "/synthetic/python" in steps[0].command
    assert steps[2].command.endswith("install chromium firefox")
    assert "--with-deps" not in "\n".join(step.command for step in steps)
    assert "--check-browser-os-dependencies" in steps[3].command


@pytest.mark.parametrize(
    ("apt_output", "expected"),
    [
        ("Inst libmissing (1.2.3 synthetic [amd64])\n", ["libmissing"]),
        ("Inst libupgrade [1.2.2] (1.2.3 synthetic [amd64])\n", []),
        (
            "Inst libupgrade [1.2.2] (1.2.3 synthetic [amd64])\n"
            "Inst libmissing (4.5.6 synthetic [amd64])\n",
            ["libmissing"],
        ),
    ],
)
def test_apt_simulation_counts_new_installs_but_not_upgrades(
    ci_local: ModuleType, apt_output: str, expected: list[str]
) -> None:
    assert ci_local.missing_apt_packages(apt_output) == expected


def test_default_preparation_never_invokes_sudo_or_su(ci_local: ModuleType, tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    fake_bin = tmp_path / "bin"
    playwright_bin = checkout / "node_modules" / ".bin"
    fake_bin.mkdir()
    playwright_bin.mkdir(parents=True)
    invoked = tmp_path / "privilege-command-invoked"
    for command in ("uv", "npm"):
        _write_executable(fake_bin / command, "raise SystemExit(0)\n")
    for command in ("sudo", "su"):
        _write_executable(
            fake_bin / command,
            f"from pathlib import Path\nPath({str(invoked)!r}).write_text({command!r})\n",
        )
    _write_executable(
        playwright_bin / "playwright",
        "import subprocess, sys\n"
        "if sys.argv[1:2] == ['install-deps']:\n"
        "    raise SystemExit(subprocess.run(['apt-get', 'install', '-s', "
        "'--no-install-recommends', 'bash']).returncode)\n"
        "raise SystemExit(0)\n",
    )
    environment = {"PATH": f"{fake_bin}{os.pathsep}/usr/bin{os.pathsep}/bin"}

    for step in ci_local._preparation_steps(
        {"test": 60, "frontend": 60}, Path("/synthetic/python")
    ):
        passed, _duration = ci_local.run_step(step, checkout=checkout, environment=environment)
        assert passed

    assert not invoked.exists()


def test_missing_browser_dependencies_name_the_manual_command(
    ci_local: ModuleType, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    checkout = tmp_path / "checkout"
    playwright = checkout / "node_modules" / ".bin" / "playwright"
    playwright.parent.mkdir(parents=True)
    _write_executable(playwright, "raise SystemExit(1)\n")
    step = ci_local._preparation_steps({"test": 60, "frontend": 60}, Path("/synthetic/python"))[3]

    passed, _duration = ci_local.run_step(
        step,
        checkout=checkout,
        environment={"PATH": "/usr/bin:/bin"},
    )

    assert not passed
    output = capfd.readouterr()
    assert "browser dependency simulation failed" in output.err
    assert "playwright install-deps chromium firefox" in output.err


def test_steps_run_with_no_new_privileges(ci_local: ModuleType, tmp_path: Path) -> None:
    if shutil.which("setpriv") is None:
        pytest.skip("util-linux setpriv is unavailable")
    output = tmp_path / "status"
    command = f"awk '/^NoNewPrivs:/ {{print $2}}' /proc/self/status > {shlex.quote(str(output))}"

    passed, _duration = ci_local.run_step(
        ci_local.Step("no-new-privileges probe", command, "test", 10),
        checkout=tmp_path,
        environment=os.environ.copy(),
    )

    assert passed
    assert output.read_text(encoding="utf-8").strip() == "1"


def test_missing_setpriv_is_reported(
    ci_local: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    real_which = ci_local.shutil.which

    def without_setpriv(command: str, *args: object, **kwargs: object) -> str | None:
        if command == "setpriv":
            return None
        return real_which(command, *args, **kwargs)

    monkeypatch.setattr(ci_local.shutil, "which", without_setpriv)
    passed, _duration = ci_local.run_step(
        ci_local.Step("unprotected probe", "true", "test", 10),
        checkout=tmp_path,
        environment=os.environ.copy(),
    )

    assert passed
    assert "setpriv is unavailable" in capfd.readouterr().err


def test_job_timeout_is_a_cumulative_budget(
    ci_local: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remaining_seen: list[float] = []
    durations = iter((6.0, 3.0, 2.0))

    def fake_run_step(
        _step: object,
        *,
        checkout: Path,
        environment: dict[str, str],
        remaining_seconds: float,
    ) -> tuple[bool, float]:
        del checkout, environment
        remaining_seen.append(remaining_seconds)
        return True, next(durations)

    monkeypatch.setattr(ci_local, "run_step", fake_run_step)
    steps = [
        ci_local.Step("one", "true", "test", 10),
        ci_local.Step("two", "true", "test", 10),
        ci_local.Step("three", "true", "frontend", 20),
    ]

    assert ci_local.run_steps(steps, checkout=tmp_path, environment={})
    assert remaining_seen == [10.0, 4.0, 20.0]


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
    warm_venv = tmp_path / "warm" / ".venv"
    warm_bin = warm_venv / "bin"
    regular_bin = tmp_path / "regular-bin"
    warm_bin.mkdir(parents=True)
    regular_bin.mkdir()
    (warm_venv / "pyvenv.cfg").touch()
    monkeypatch.setenv(
        "PATH",
        f"{warm_bin}{os.pathsep}{regular_bin}{os.pathsep}/usr/bin{os.pathsep}/bin",
    )
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
    command = " ".join(shlex.quote(value) for value in (sys.executable, str(probe), str(output)))

    passed, _duration = ci_local.run_step(
        ci_local.Step("environment probe", command, "test", 10),
        checkout=checkout,
        environment=environment,
    )

    assert passed
    child_environment = json.loads(output.read_text(encoding="utf-8"))
    assert not inherited.keys() & child_environment.keys()
    assert child_environment["PIP_CONFIG_FILE"] == os.devnull
    assert child_environment["UV_NO_CONFIG"] == "1"
    npm_userconfig = Path(child_environment["NPM_CONFIG_USERCONFIG"])
    npm_globalconfig = Path(child_environment["NPM_CONFIG_GLOBALCONFIG"])
    assert npm_userconfig.read_bytes() == b""
    assert npm_globalconfig.read_bytes() == b""
    assert str(warm_bin) not in child_environment["PATH"].split(os.pathsep)
    assert str(regular_bin) in child_environment["PATH"].split(os.pathsep)
    assert Path(child_environment["HOME"]).parent == run_root
    assert Path(child_environment["XDG_CONFIG_HOME"]).parent == run_root


@pytest.mark.parametrize(
    "requested_signal", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT]
)
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
        "jobs:\n  test:\n    timeout-minutes: 1\n  frontend:\n    timeout-minutes: 1\n",
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
        _wait_for_stopped_processes(child_pids)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for pid in child_pids:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


def test_three_different_signals_cannot_interrupt_stop_and_cleanup(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Synthetic Test")
    _git(repository, "config", "user.email", "synthetic@example.invalid")
    (repository / ".github" / "workflows").mkdir(parents=True)
    (repository / "CONTRIBUTING.md").write_text(
        "## Required checks\n\n```bash\nuv run ruff check .\n```\n", encoding="utf-8"
    )
    (repository / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  test:\n    timeout-minutes: 1\n  frontend:\n    timeout-minutes: 1\n",
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
    _write_executable(
        fake_bin / "uv",
        "import os, pathlib, signal, subprocess, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"pathlib.Path({str(step_pid)!r}).write_text(str(os.getpid()))\n"
        f"subprocess.Popen([{sys.executable!r}, '-c', {grandchild_code!r}])\n"
        "time.sleep(60)\n",
    )
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    process = subprocess.Popen(
        [sys.executable, str(SCRIPT), "--python", sys.executable],
        cwd=repository,
        env={
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "TMPDIR": str(temp_root),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    child_pids: list[int] = []
    try:
        _wait_for_file(grandchild_pid, process)
        child_pids = [int(step_pid.read_text()), int(grandchild_pid.read_text())]
        os.kill(process.pid, signal.SIGINT)
        time.sleep(0.1)
        os.kill(process.pid, signal.SIGTERM)
        os.kill(process.pid, signal.SIGHUP)
        process.communicate(timeout=10)

        assert process.returncode == 128 + signal.SIGINT
        assert not list(temp_root.glob("gpo-studio-ci-local-*"))
        _wait_for_stopped_processes(child_pids)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for pid in child_pids:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


@pytest.mark.skipif(
    os.environ.get("GPO_STUDIO_WINDOWS_INTEGRATION") != "1",
    reason="set GPO_STUDIO_WINDOWS_INTEGRATION=1 for the opt-in SSH preflight",
)
def test_opt_in_windows_ssh_preflight(ci_local: ModuleType) -> None:
    """Exercise encoded SSH and the pinned tools without creating a remote run."""
    assert isinstance(
        ci_local.windows_preflight(
            "cw-admin@mvmcitest01", ci_local.WINDOWS_PYTHONS["3.14"], timeout=30
        ),
        bool,
    )


@pytest.mark.skipif(
    os.environ.get("GPO_STUDIO_WINDOWS_INTEGRATION") != "1",
    reason="set GPO_STUDIO_WINDOWS_INTEGRATION=1 for the opt-in SSH process probe",
)
def test_opt_in_windows_phase_preserves_exit_codes_and_quoted_arguments(
    ci_local: ModuleType,
) -> None:
    """A collection failure must fail the phase, including on PowerShell 5.1."""
    host = "cw-admin@mvmcitest01"
    python = ci_local.WINDOWS_PYTHONS["3.13"]
    ci_local.windows_preflight(host, python, timeout=30)
    workspace = ci_local.windows_initialize(host, secrets.token_hex(12), timeout=60)
    try:
        ci_local._run_remote_capture(
            host,
            ci_local._powershell_prelude()
            + "\nNew-Item -ItemType Directory -Path "
            + ci_local._powershell_literal(workspace.checkout)
            + " | Out-Null\n",
            timeout=30,
        )
        for code in (0, 7):
            arguments = (
                "run",
                "--no-project",
                "--python",
                python,
                "python",
                "-c",
                "import os, sys; "
                "assert os.path.basename(os.getcwd()) == 'checkout'; "
                "assert sys.argv[1] == 'café value'; "
                "assert os.environ['TEMP'] == os.environ['TMP']; "
                "assert os.environ['TEMP'].endswith('tmp'); "
                "print('café 日本語'); "
                "print('PROBE_STDOUT'); print('PROBE_STDERR', file=sys.stderr); "
                f"raise SystemExit({code})",
                "café value",
            )
            completed = ci_local._run_captured(
                ci_local.powershell_ssh_command(
                    host, ci_local._windows_phase_script(workspace, python, arguments)
                ),
                timeout=30,
            )
            assert completed.returncode == code, completed.stderr
            assert "PROBE_STDOUT" in completed.stdout
            assert "PROBE_STDERR" in completed.stderr
            assert "café 日本語" in completed.stdout
        with pytest.raises(RuntimeError, match="recent unowned sibling"):
            ci_local.windows_initialize(host, secrets.token_hex(12), timeout=30)
        # Remote deadline, not the controller's 30s timeout, must kill the child.
        completed = ci_local._run_captured(
            ci_local.powershell_ssh_command(
                host,
                ci_local._windows_phase_script(
                    workspace,
                    python,
                    (
                        "run",
                        "--no-project",
                        "--python",
                        python,
                        "python",
                        "-c",
                        "import time; print('WATCHDOG_READY', flush=True); time.sleep(60)",
                    ),
                    remaining=4,
                ),
            ),
            timeout=30,
        )
        assert completed.returncode == 124, completed.stderr
        assert "WATCHDOG_READY" in completed.stdout
    finally:
        ci_local.windows_cleanup(host, workspace)


@pytest.mark.skipif(
    os.environ.get("GPO_STUDIO_WINDOWS_INTEGRATION") != "1",
    reason="set GPO_STUDIO_WINDOWS_INTEGRATION=1 for the opt-in checkout probe",
)
def test_opt_in_windows_setup_bytes_and_live_sibling_refusal(
    ci_local: ModuleType, tmp_path: Path
) -> None:
    host = "cw-admin@mvmcitest01"
    token = secrets.token_hex(12)
    commit = _git(ROOT, "rev-parse", "HEAD")
    workspace = ci_local.windows_initialize(host, token, timeout=30)
    process = None
    try:
        archive = tmp_path / "source.zip"
        bundle = tmp_path / "source.bundle"
        _git(ROOT, "archive", "--format=zip", f"--output={archive}", commit)
        ci_local.create_windows_bundle(ROOT, commit, bundle, token)
        assert (
            ci_local._run_streamed(
                [
                    "scp",
                    *ci_local.SSH_OPTIONS,
                    str(archive),
                    str(bundle),
                    f"{host}:{workspace.run.replace(chr(92), '/')}/",
                ],
                timeout=120,
            )
            == 0
        )
        setup = ci_local._windows_setup_script(workspace, commit, 180)
        # Pause inside the actual setup wrapper, after its owner record exists
        # but before archive expansion. A second initializer must not touch it.
        setup = setup.replace(
            "Expand-Archive -LiteralPath",
            "Start-Sleep -Seconds 8\nExpand-Archive -LiteralPath",
        )
        process = subprocess.Popen(
            ci_local.powershell_ssh_command(host, setup),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        owner_probe = (
            ci_local._powershell_prelude()
            + "\n"
            + (
                f"$owner={ci_local._powershell_literal(workspace.run + r'\phase-owner')}\n"
                "for($i=0;$i -lt 80;$i++){\n"
                "if(Test-Path $owner){Write-Output 'SETUP_OWNER_READY'; break}; "
                "Start-Sleep -Milliseconds 100}\n"
            )
        )
        assert "SETUP_OWNER_READY" in ci_local._run_remote_capture(host, owner_probe, timeout=20)
        with pytest.raises(RuntimeError, match="live sibling"):
            ci_local.windows_initialize(host, secrets.token_hex(12), timeout=30)
        stdout, stderr = process.communicate(timeout=180)
        assert process.returncode == 0, stdout + stderr
        inspect = (
            ci_local._powershell_prelude()
            + "\n"
            + (
                f"$checkout={ci_local._powershell_literal(workspace.checkout)}\n"
                "$bytes=[IO.File]::ReadAllBytes((Join-Path $checkout 'CONTRIBUTING.md'))\n"
                "$text=[Text.Encoding]::UTF8.GetString($bytes)\n"
                "Write-Output ('BYTE_CRLF=' + [regex]::Matches($text, \"`r`n\").Count)\n"
                "Write-Output ('BYTE_LONE_LF=' + [regex]::Matches($text, \"(?<!`r)`n\").Count)\n"
                f"& {ci_local._powershell_literal(ci_local.WINDOWS_GIT)} "
                "-C $checkout config --show-origin --get core.autocrlf\n"
            )
        )
        observed = ci_local._run_remote_capture(host, inspect, timeout=30)
        print(observed)
        assert "BYTE_LONE_LF=0" in observed
        assert "BYTE_CRLF=0\n" not in observed
        assert "etc/gitconfig" in observed and "true" in observed
    finally:
        ci_local.windows_cleanup(host, workspace)
        if process is not None:
            process.communicate(timeout=30)


def test_normal_exit_kills_background_process_group(ci_local: ModuleType, tmp_path: Path) -> None:
    child_pid = tmp_path / "child.pid"
    child_code = (
        "import os, pathlib, signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(child_pid)!r}).write_text(str(os.getpid())); "
        "time.sleep(60)"
    )
    parent_code = (
        "import pathlib, subprocess; "
        f"child = subprocess.Popen([{sys.executable!r}, '-c', {child_code!r}]); "
        f"pathlib.Path({str(child_pid)!r}).write_text(str(child.pid))"
    )
    command = " ".join(shlex.quote(value) for value in (sys.executable, "-c", parent_code))

    passed, _duration = ci_local.run_step(
        ci_local.Step("background", command, "test", 10),
        checkout=tmp_path,
        environment=os.environ.copy(),
    )

    assert passed
    pid = int(child_pid.read_text())
    _wait_for_stopped_processes([pid])


def test_timeout_kills_real_child_process_tree(ci_local: ModuleType, tmp_path: Path) -> None:
    child_pid = tmp_path / "child.pid"
    child_code = (
        "import os, pathlib, signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(child_pid)!r}).write_text(str(os.getpid())); "
        "time.sleep(60)"
    )
    parent_code = (
        f"import subprocess, time; subprocess.Popen([{sys.executable!r}, '-c', "
        f"{child_code!r}]); time.sleep(60)"
    )
    command = " ".join(shlex.quote(value) for value in (sys.executable, "-c", parent_code))

    passed, _duration = ci_local.run_step(
        ci_local.Step("timeout", command, "test", 1),
        checkout=tmp_path,
        environment=os.environ.copy(),
    )

    assert not passed
    pid = int(child_pid.read_text())
    _wait_for_stopped_processes([pid])


def test_windows_leg_refuses_linux_python_option(
    ci_local: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as caught:
        ci_local.main(["--windows", "--python", sys.executable])
    assert caught.value.code == 2
    error = capsys.readouterr().err
    assert "use --windows-python" in error


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
