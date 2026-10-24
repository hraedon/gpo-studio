#!/usr/bin/env python3
"""Run GPO Studio's documented Linux checks or Windows CI test job.

The named commit supplies the documented shell commands, so run only commits
whose contents are trusted to execute with the invoking user's privileges.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import NoReturn
from xml.etree import ElementTree

import yaml  # type: ignore[import-untyped]  # no stubs in the dev lock; mypy gates src only

WINDOWS_UV = r"C:\Users\cw-admin\.ci-local\uv\0.12.0\uv.exe"
WINDOWS_UV_SHA256 = "268CD62B99395EB53825795518E067E4B27EC4B445175DF343824689F307C807"
WINDOWS_GIT = r"C:\Users\cw-admin\.ci-local\git\2.56.0.2\cmd\git.exe"
WINDOWS_GIT_SHA256 = "54BDA91C773C6BBA2B970424423CFCB80552D92DC6967D766F98931FE1251A54"
WINDOWS_PYTHONS = {
    "3.13": r"C:\Program Files\Python313\python.exe",
    "3.14": r"C:\Users\cw-admin\AppData\Local\Python\pythoncore-3.14-64\python.exe",
}
WINDOWS_JOB = "test-windows"
WINDOWS_RUN_PREFIX = "gpo-studio-ci-local"
WINDOWS_STALE_MARGIN = 600
SSH_OPTIONS = [
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=15",
    "-o",
    "ServerAliveInterval=15",
    "-o",
    "ServerAliveCountMax=3",
]
_REMOTE_PATH = re.compile(r"^[A-Za-z]:\\[A-Za-z0-9_.\\-]+$")

_JOB_BY_COMMAND = {
    "uv run ruff check .": "test",
    "uv run mypy src": "test",
    ("uv run pytest -q --cov=src/gpo_studio --cov-branch --cov-report=json:coverage.json"): "test",
    "uv run python scripts/check_coverage.py coverage.json": "test",
    "uv run python scripts/check_safety.py": "static-safety",
    "npm run check": "frontend",
    "npm run test:browser": "frontend",
    "bash scripts/installed_package_smoke.sh": "installed-package",
    # CONTRIBUTING runs this in the editable environment; the installed-package
    # job supplies its timeout even though CI rehearses against the wheel.
    "uv run python scripts/rehearse_upgrade_rollback.py": "installed-package",
}

_PASSTHROUGH_ENVIRONMENT = frozenset(
    {
        "ALL_PROXY",
        "CURL_CA_BUNDLE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "LANG",
        "NO_PROXY",
        "PATH",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TERM",
        "all_proxy",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)

# SIGHUP and SIGQUIT do not exist on Windows; main() refuses there anyway.
_INTERRUPT_SIGNALS = tuple(
    getattr(signal, name)
    for name in ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT")
    if hasattr(signal, name)
)


@dataclass(frozen=True)
class Step:
    name: str
    command: str
    job: str
    timeout_seconds: int


@dataclass(frozen=True)
class WindowsWorkspace:
    run: str
    checkout: str
    archive: str
    bundle: str


class _SignalInterruption(BaseException):
    def __init__(self, signum: int) -> None:
        self.signum = signum
        super().__init__(signum)


@contextmanager
def _interrupt_on_signals() -> Iterator[None]:
    previous = {signum: signal.getsignal(signum) for signum in _INTERRUPT_SIGNALS}

    def ignore_repeated_interrupt(_signum: int, _frame: FrameType | None) -> None:
        """Keep the first interruption authoritative while cleanup completes."""

    def interrupt(signum: int, _frame: FrameType | None) -> NoReturn:
        # Cleanup blocks these signals while stopping a process group and removing
        # its run tree. Different blocked signals are all delivered when the mask
        # is restored, so make every later delivery harmless before unwinding.
        for repeated in _INTERRUPT_SIGNALS:
            signal.signal(repeated, ignore_repeated_interrupt)
        raise _SignalInterruption(signum)

    for signum in previous:
        signal.signal(signum, interrupt)
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


@contextmanager
def _blocked_interrupt_signals() -> Iterator[None]:
    """Defer repeated termination signals until process and file cleanup finishes."""
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, _INTERRUPT_SIGNALS)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--commit",
        default="HEAD",
        help="commit or ref to test (default: HEAD)",
    )
    result.add_argument(
        "--python",
        help="Python interpreter for uv and the installed-package smoke test",
    )
    result.add_argument(
        "--windows",
        action="store_true",
        help="run the test-windows job on a remote Windows host",
    )
    result.add_argument(
        "--windows-host",
        default="cw-admin@mvmcitest01",
        help="SSH destination for --windows (default: cw-admin@mvmcitest01)",
    )
    result.add_argument(
        "--windows-python",
        choices=sorted(WINDOWS_PYTHONS),
        default="3.14",
        help="test-windows Python matrix version (default: 3.14)",
    )
    result.add_argument(
        "--keep",
        action="store_true",
        help="retain local run files (Windows remote files are always removed)",
    )
    result.add_argument(
        "--check-browser-os-dependencies",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return result


def _run_git(arguments: Sequence[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"git {' '.join(arguments)} failed: {detail}")
    return completed.stdout.strip()


def repository_root(cwd: Path) -> Path:
    return Path(_run_git(["rev-parse", "--show-toplevel"], cwd=cwd)).resolve()


def resolve_interpreter(value: str) -> Path:
    found = shutil.which(value)
    candidate = Path(found if found is not None else value).expanduser()
    if not candidate.is_file():
        raise ValueError(f"Python interpreter does not exist: {value}")
    completed = subprocess.run(
        [str(candidate), "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ValueError(f"Python interpreter could not run: {value}")
    return candidate.resolve()


def required_commands(contributing: Path) -> list[str]:
    """Read the first shell code block under CONTRIBUTING's Required checks."""
    lines = contributing.read_text(encoding="utf-8").splitlines()
    try:
        heading = lines.index("## Required checks")
        section_end = next(
            (index for index in range(heading + 1, len(lines)) if lines[index].startswith("## ")),
            len(lines),
        )
        opening = next(
            index for index in range(heading + 1, section_end) if lines[index] == "```bash"
        )
        closing = lines.index("```", opening + 1)
    except (ValueError, StopIteration) as error:
        raise ValueError("CONTRIBUTING.md has no bash block under Required checks") from error
    commands = [line.strip() for line in lines[opening + 1 : closing] if line.strip()]
    if not commands:
        raise ValueError("CONTRIBUTING.md's Required checks block is empty")
    return commands


def _canonical_command(command: str) -> str:
    words = shlex.split(command)
    if words[:2] == ["uv", "run"]:
        words = [word for word in words if word not in {"--locked", "-I"}]
    return " ".join(words)


def workflow_timeouts(workflow: Path) -> dict[str, int]:
    document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("jobs"), dict):
        raise ValueError(f"{workflow} has no jobs mapping")
    result: dict[str, int] = {}
    for name, body in document["jobs"].items():
        if isinstance(name, str) and isinstance(body, dict):
            minutes = body.get("timeout-minutes")
            if isinstance(minutes, int) and not isinstance(minutes, bool) and minutes > 0:
                result[name] = minutes * 60
    return result


def derive_steps(commands: Sequence[str], workflow: Path) -> list[Step]:
    timeouts = workflow_timeouts(workflow)
    steps: list[Step] = []
    for command in commands:
        canonical = _canonical_command(command)
        job = _JOB_BY_COMMAND.get(canonical)
        if job is None:
            raise ValueError(f"no ci.yml job mapping for required check: {command}")
        timeout = timeouts.get(job)
        if timeout is None:
            raise ValueError(f"ci.yml job {job!r} has no positive integer timeout-minutes")
        name = canonical.removeprefix("uv run ").removeprefix("npm run ")
        steps.append(Step(name=name, command=command, job=job, timeout_seconds=timeout))
    return steps


def fresh_clone(source: Path, commit: str, run_root: Path) -> Path:
    clone = run_root / "checkout"
    _run_git(
        [
            "-c",
            "core.autocrlf=false",
            "clone",
            "--no-hardlinks",
            "--no-checkout",
            "--",
            str(source),
            str(clone),
        ],
        cwd=source,
    )
    unset = subprocess.run(
        ["git", "config", "--local", "--unset-all", "core.autocrlf"],
        cwd=clone,
        check=False,
        capture_output=True,
        text=True,
    )
    if unset.returncode not in {0, 5}:
        raise RuntimeError(f"could not unset core.autocrlf: {unset.stderr.strip()}")
    _run_git(["-c", "core.autocrlf=false", "checkout", "--detach", commit], cwd=clone)
    configured = subprocess.run(
        ["git", "config", "--local", "--get-all", "core.autocrlf"],
        cwd=clone,
        check=False,
        capture_output=True,
        text=True,
    )
    if configured.returncode not in {1, 5} or configured.stdout:
        raise RuntimeError("fresh clone unexpectedly has local core.autocrlf configuration")
    return clone


def _stop_process(process: subprocess.Popen[str]) -> None:
    with _blocked_interrupt_signals():
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.wait()
            return
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)
        # The group leader can exit before a descendant that ignores SIGTERM.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def run_step(
    step: Step,
    *,
    checkout: Path,
    environment: dict[str, str],
    remaining_seconds: float | None = None,
) -> tuple[bool, float]:
    timeout = step.timeout_seconds if remaining_seconds is None else remaining_seconds
    print(
        f"\n=== {step.name} (ci job {step.job}, {timeout:.1f}s remaining of "
        f"{step.timeout_seconds}s) ===",
        flush=True,
    )
    print(f"$ {step.command}", flush=True)
    started = time.monotonic()
    command = ["bash", "-o", "pipefail", "-c", step.command]
    setpriv = shutil.which("setpriv", path=environment.get("PATH", os.defpath))
    if setpriv is not None:
        command = [setpriv, "--no-new-privs", *command]
    else:
        print(
            "ci-local: setpriv is unavailable; this step runs without no-new-privileges protection",
            file=sys.stderr,
            flush=True,
        )
    process = subprocess.Popen(
        command,
        cwd=checkout,
        env=environment,
        text=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        returncode = 1
    finally:
        # A successful shell can still leave descendants in its process group.
        _stop_process(process)
    if process.returncode is not None:
        returncode = process.returncode
    duration = time.monotonic() - started
    outcome = "TIMEOUT" if timed_out else "PASS" if returncode == 0 else "FAIL"
    print(
        f"[{outcome}] {step.name}: {duration:.1f}s; {step.job} job limit {step.timeout_seconds}s",
        flush=True,
    )
    return returncode == 0 and not timed_out, duration


def run_steps(steps: Sequence[Step], *, checkout: Path, environment: dict[str, str]) -> bool:
    """Run steps against one cumulative timeout budget per workflow job."""
    elapsed_by_job: defaultdict[str, float] = defaultdict(float)
    total_steps = len(steps)
    for index, step in enumerate(steps, start=1):
        remaining = step.timeout_seconds - elapsed_by_job[step.job]
        print(f"\nStep {index}/{total_steps}", flush=True)
        if remaining <= 0:
            print(
                f"[TIMEOUT] {step.name}: {step.job} exhausted its cumulative "
                f"{step.timeout_seconds}s job budget",
                flush=True,
            )
            return False
        passed, duration = run_step(
            step,
            checkout=checkout,
            environment=environment,
            remaining_seconds=remaining,
        )
        elapsed_by_job[step.job] += duration
        print(
            f"[{step.job} budget] {elapsed_by_job[step.job]:.1f}s / "
            f"{step.timeout_seconds}s cumulative",
            flush=True,
        )
        if not passed:
            return False
    return True


def _preparation_steps(timeouts: dict[str, int], python: Path) -> list[Step]:
    return [
        Step(
            name="fresh Python install",
            command=f"uv sync --locked --extra dev --python {shlex.quote(str(python))}",
            job="test",
            timeout_seconds=timeouts["test"],
        ),
        Step(
            name="fresh frontend install",
            command="npm ci --ignore-scripts",
            job="frontend",
            timeout_seconds=timeouts["frontend"],
        ),
        Step(
            name="fresh browser install",
            command="node_modules/.bin/playwright install chromium firefox",
            job="frontend",
            timeout_seconds=timeouts["frontend"],
        ),
        Step(
            name="browser OS dependency check",
            command=(
                f"{shlex.quote(sys.executable)} "
                f"{shlex.quote(str(Path(__file__).resolve()))} "
                "--check-browser-os-dependencies"
            ),
            job="frontend",
            timeout_seconds=timeouts["frontend"],
        ),
    ]


def missing_apt_packages(output: str) -> list[str]:
    """Return packages apt would newly install, excluding upgrades.

    In ``apt-get install -s`` output, an installed package has its current
    version in square brackets: ``Inst pkg [old] (new ...)``. A package with no
    current version is not installed: ``Inst pkg (version ...)``.
    """
    missing: list[str] = []
    for line in output.splitlines():
        fields = line.split(maxsplit=2)
        if len(fields) != 3 or fields[0] != "Inst":
            continue
        if not fields[2].startswith("["):
            missing.append(fields[1])
    return sorted(set(missing))


def browser_dependencies_available(checkout: Path) -> bool:
    """Check Playwright's apt dependencies without trusting its upgrade parser."""
    playwright = checkout / "node_modules" / ".bin" / "playwright"
    apt_get = shutil.which("apt-get")
    if sys.platform != "linux" or apt_get is None:
        print(
            "ci-local: the browser OS dependency check supports Debian/Ubuntu "
            "hosts with apt-get only",
            file=sys.stderr,
        )
        return False
    if not playwright.is_file():
        print(f"ci-local: Playwright executable is missing: {playwright}", file=sys.stderr)
        return False

    with tempfile.TemporaryDirectory(prefix="gpo-studio-apt-probe-") as temporary:
        probe_root = Path(temporary)
        apt_output = probe_root / "apt-output"
        wrapper = probe_root / "apt-get"
        wrapper.write_text(
            f"#!{sys.executable}\n"
            "import os, pathlib, subprocess, sys\n"
            f"completed = subprocess.run([{apt_get!r}, *sys.argv[1:]], "
            "capture_output=True, text=True)\n"
            "pathlib.Path(os.environ['GPO_STUDIO_APT_OUTPUT']).write_text("
            "completed.stdout, encoding='utf-8')\n"
            "sys.stderr.write(completed.stderr)\n"
            "raise SystemExit(completed.returncode)\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        environment = os.environ.copy()
        environment["GPO_STUDIO_APT_OUTPUT"] = str(apt_output)
        environment["LC_ALL"] = "C"
        environment["PATH"] = os.pathsep.join((str(probe_root), environment.get("PATH", "")))
        completed = subprocess.run(
            [str(playwright), "install-deps", "--dry-run", "chromium", "firefox"],
            cwd=checkout,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

        if completed.returncode != 0 or not apt_output.is_file():
            detail = (completed.stderr or completed.stdout).strip()
            print(f"ci-local: browser dependency simulation failed: {detail}", file=sys.stderr)
            print(
                "Install the missing dependencies yourself with: "
                "node_modules/.bin/playwright install-deps chromium firefox",
                file=sys.stderr,
            )
            return False
        missing = missing_apt_packages(apt_output.read_text(encoding="utf-8"))

    if missing:
        print(
            f"ci-local: missing browser OS dependencies: {', '.join(missing)}",
            file=sys.stderr,
        )
        print(
            "Install the missing dependencies yourself with: "
            "node_modules/.bin/playwright install-deps chromium firefox",
            file=sys.stderr,
        )
        return False
    print("All browser OS dependencies are installed.")
    return True


def with_external_pytest_base(steps: Sequence[Step], pytest_base: Path) -> list[Step]:
    """Put only the top-level pytest's basetemp outside its disposable checkout."""
    result: list[Step] = []
    for step in steps:
        command = step.command
        if _canonical_command(command).startswith("uv run pytest "):
            command = f"{command} --basetemp={shlex.quote(str(pytest_base))}"
        result.append(
            Step(
                name=step.name,
                command=command,
                job=step.job,
                timeout_seconds=step.timeout_seconds,
            )
        )
    return result


def step_environment(*, run_root: Path, checkout: Path, python: Path) -> dict[str, str]:
    """Build a small, reproducible environment without user package settings."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in _PASSTHROUGH_ENVIRONMENT or key.startswith("LC_")
    }
    isolated_home = run_root / "home"
    isolated_config = run_root / "config"
    npm_userconfig = run_root / "empty-npmrc"
    npm_globalconfig = run_root / "empty-global-npmrc"
    privilege_guard = run_root / "privilege-guard"
    for directory in (isolated_home, isolated_config, privilege_guard):
        directory.mkdir()
    npm_userconfig.touch()
    npm_globalconfig.touch()
    inherited_path = os.pathsep.join(
        component
        for component in environment.get("PATH", "").split(os.pathsep)
        if component and not (Path(component).resolve().parent / "pyvenv.cfg").is_file()
    )
    for command in ("sudo", "su"):
        guard = privilege_guard / command
        guard.write_text(
            "#!/bin/sh\n"
            f"echo 'ci-local refuses to invoke {command}; install OS dependencies yourself.' >&2\n"
            "exit 126\n",
            encoding="utf-8",
        )
        guard.chmod(0o755)
    environment.update(
        {
            "CI": "true",
            "HOME": str(isolated_home),
            "XDG_CONFIG_HOME": str(isolated_config),
            "PATH": os.pathsep.join(
                component for component in (str(privilege_guard), inherited_path) if component
            ),
            "TMPDIR": str(run_root / "tmp"),
            "UV_CACHE_DIR": str(run_root / "uv-cache"),
            "UV_NO_CONFIG": "1",
            "UV_PYTHON_DOWNLOADS": "never",
            "NPM_CONFIG_CACHE": str(run_root / "npm-cache"),
            "NPM_CONFIG_GLOBALCONFIG": str(npm_globalconfig),
            "NPM_CONFIG_USERCONFIG": str(npm_userconfig),
            "PIP_CONFIG_FILE": os.devnull,
            "PLAYWRIGHT_BROWSERS_PATH": str(run_root / "playwright-browsers"),
            "PYTHON": str(python),
            "GPO_STUDIO_TEST_PYTHON": str(checkout / ".venv" / "bin" / "python"),
        }
    )
    return environment


def encode_powershell(script: str) -> str:
    """Encode a PowerShell script for Windows PowerShell's ``-EncodedCommand``."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def powershell_ssh_command(host: str, script: str) -> list[str]:
    if not host or host.startswith("-"):
        raise ValueError("--windows-host must be a non-option SSH destination")
    return [
        "ssh",
        *SSH_OPTIONS,
        host,
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        encode_powershell(script),
        # The SSH default shell is also PowerShell: preserve the child's code.
        "; exit $LASTEXITCODE",
    ]


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _powershell_prelude() -> str:
    return "\n".join(
        (
            "$ProgressPreference='SilentlyContinue'",
            "$ErrorActionPreference='Stop'",
            "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)",
        )
    )


def decode_powershell_output(output: str) -> str:
    """Turn PowerShell remoting CLIXML errors into readable text."""
    marker = "#< CLIXML"
    if marker not in output:
        return output.strip()
    prefix, xml = output.split(marker, 1)
    xml = xml.strip()
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return output.strip()

    def unescape(value: str) -> str:
        return re.sub(
            r"_x([0-9A-Fa-f]{4})_",
            lambda match: chr(int(match.group(1), 16)),
            value,
        )

    messages = [unescape(item.text or "") for item in root.iter() if item.tag.endswith("}S")]
    return "\n".join(part for part in (prefix.strip(), *messages) if part).strip()


def _stop_and_wait(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        _stop_process(process)


def _run_captured(arguments: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        list(arguments),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _stop_and_wait(process)
        raise RuntimeError(f"command timed out after {timeout:.1f}s: {arguments[0]}") from None
    except BaseException:
        _stop_and_wait(process)
        raise
    return subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)


def _run_remote_capture(host: str, script: str, *, timeout: float) -> str:
    completed = _run_captured(powershell_ssh_command(host, script), timeout=timeout)
    if completed.returncode != 0:
        detail = decode_powershell_output(completed.stderr or completed.stdout)
        raise RuntimeError(f"remote PowerShell failed: {detail or 'no diagnostic'}")
    if completed.stderr:
        # OpenSSH warnings are local diagnostics, not remote command output.
        print(completed.stderr.rstrip(), file=sys.stderr)
    return decode_powershell_output(completed.stdout)


def _run_streamed(arguments: Sequence[str], *, timeout: float) -> int:
    process = subprocess.Popen(list(arguments), text=True, start_new_session=True)
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _stop_and_wait(process)
        return 124
    except BaseException:
        _stop_and_wait(process)
        raise
    finally:
        _stop_and_wait(process)


def _parse_key_values(output: str, required: set[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in required:
            values[key] = value.strip()
    missing = required - values.keys()
    if missing:
        raise RuntimeError(f"remote preflight omitted: {', '.join(sorted(missing))}")
    return values


def verify_windows_uv_digest(actual: str) -> None:
    if actual.upper() != WINDOWS_UV_SHA256:
        raise RuntimeError(
            "refusing Windows uv: SHA-256 mismatch "
            f"(expected {WINDOWS_UV_SHA256}, got {actual or '<missing>'})"
        )


def verify_windows_git_digest(actual: str) -> None:
    if actual.upper() != WINDOWS_GIT_SHA256:
        raise RuntimeError(
            "refusing Windows Git: SHA-256 mismatch "
            f"(expected {WINDOWS_GIT_SHA256}, got {actual or '<missing>'})"
        )


def windows_preflight(host: str, python: str, *, timeout: float) -> bool:
    script = (
        _powershell_prelude()
        + "\n"
        + f"$uv={_powershell_literal(WINDOWS_UV)}\n"
        + f"$git={_powershell_literal(WINDOWS_GIT)}\n"
        + f"$python={_powershell_literal(python)}\n"
        + "if (-not (Test-Path -LiteralPath $uv -PathType Leaf)) { throw 'pinned uv is missing' }\n"
        + "if (-not (Test-Path -LiteralPath $git -PathType Leaf)) { "
        + "throw 'pinned MinGit is missing' }\n"
        + "if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { "
        + "throw 'selected Python is missing' }\n"
        + "Write-Output ('UV_SHA256=' + (Get-FileHash -Algorithm SHA256 $uv).Hash)\n"
        + "Write-Output ('GIT_SHA256=' + (Get-FileHash -Algorithm SHA256 $git).Hash)\n"
        + "Write-Output ('PYTHON=' + $python)\n"
    )
    values = _parse_key_values(
        _run_remote_capture(host, script, timeout=timeout),
        {"UV_SHA256", "GIT_SHA256", "PYTHON"},
    )
    verify_windows_uv_digest(values["UV_SHA256"])
    verify_windows_git_digest(values["GIT_SHA256"])
    if values["PYTHON"] != python:
        raise RuntimeError("remote preflight returned a different Python path")
    return True


def _validate_remote_path(value: str) -> str:
    if not _REMOTE_PATH.fullmatch(value) or any(p in {".", ".."} for p in value.split("\\")):
        raise RuntimeError(f"remote returned an unsafe run path: {value!r}")
    return value


def _windows_remove_owned_run_function() -> str:
    return (
        # Query adapters distinguish genuine absence from an unreadable state.
        # Both owner and run-age metadata must use the same missing-item rule.
        "function Get-ExistingItem([string]$path) {\n"
        "  try { return Get-Item -LiteralPath $path -Force -ErrorAction Stop }\n"
        "  catch [System.Management.Automation.ItemNotFoundException] { return $null }\n"
        "}\n"
        "function Get-ExistingProcess([int]$processId) {\n"
        "  try { return Get-Process -Id $processId -ErrorAction Stop }\n"
        "  catch {\n"
        "    if ($_.FullyQualifiedErrorId -ne "
        "'NoProcessFoundForGivenId,Microsoft.PowerShell.Commands.GetProcessCommand') { throw }\n"
        "    return $null\n"
        "  }\n"
        "}\n"
        "function Get-RunOwner([string]$target) {\n"
        "  $owner=Join-Path $target 'phase-owner'\n"
        "  $entry=Get-ExistingItem $owner\n"
        "  if ($entry -and ($entry.PSIsContainer -or "
        "($entry.Attributes -band [IO.FileAttributes]::ReparsePoint))) {\n"
        "    throw 'invalid phase-owner record: expected an ordinary file'\n"
        "  }\n"
        "  if (Test-Path -LiteralPath $owner -PathType Leaf) {\n"
        "    try {\n"
        "      $record=Get-Content -LiteralPath $owner -Raw\n"
        "      if ([string]::IsNullOrWhiteSpace($record)) { throw 'invalid phase-owner record' }\n"
        "      $parts=$record.Trim().Split('|')\n"
        "    }\n"
        "    catch { if (-not (Test-Path -LiteralPath $target)) { return }; throw }\n"
        "    if ($parts.Count -ne 2 -or\n"
        "        -not [regex]::IsMatch($parts[0], '\\A[0-9]+\\z') -or\n"
        "        -not [regex]::IsMatch($parts[1], '\\A[0-9]+\\z')) {\n"
        "      throw 'invalid phase-owner record'\n"
        "    }\n"
        "    try {\n"
        "      $processId=[int]$parts[0]; $started=[long]$parts[1]\n"
        "      $null=[DateTime]::FromFileTimeUtc($started)\n"
        "    } "
        "catch { throw 'invalid phase-owner record' }\n"
        "    if ($processId -le 0 -or $started -le 0) { throw 'invalid phase-owner record' }\n"
        "    $process=Get-ExistingProcess $processId\n"
        "    if ($process) {\n"
        "      $retain=$false\n"
        "      try {\n"
        # Holding the kernel process object reserves its PID until Dispose, even
        # if it exits between identity validation and taskkill's PID lookup.
        "        $pin=$process.Handle\n"
        "        if ($null -eq $pin -or $pin -eq [IntPtr]::Zero) {\n"
        "          throw 'cannot retain phase-owner process handle'\n"
        "        }\n"
        "        $exited=$process.HasExited\n"
        "        if ($null -eq $exited) { throw 'cannot read phase-owner process identity' }\n"
        "        if (-not $exited) {\n"
        "          $startTime=$process.StartTime\n"
        "          if ($null -eq $startTime) { throw 'cannot read phase-owner process identity' }\n"
        "          if ($startTime.ToFileTimeUtc() -eq $started) {\n"
        "            $retain=$true; return $process\n"
        "          }\n"
        "        }\n"
        "      } finally { if (-not $retain) { $process.Dispose() } }\n"
        "    }\n"
        "  }\n"
        "}\n"
        "function Assert-StaleRun([string]$target, [int]$staleAfter) {\n"
        "  $process=Get-RunOwner $target\n"
        "  if ($process) {\n"
        "    $process.Dispose()\n"
        "    throw ('refusing live sibling: ' + $target)\n"
        "  }\n"
        "  if (-not (Test-Path -LiteralPath (Join-Path $target 'phase-owner'))) {\n"
        "    $item=Get-ExistingItem $target\n"
        "    if ($item -and ([DateTime]::UtcNow - $item.LastWriteTimeUtc).TotalSeconds "
        "-le $staleAfter) { throw ('refusing recent unowned sibling: ' + $target) }\n"
        "  }\n"
        "}\n"
        "function Remove-OwnedRun([string]$target, [switch]$KillLive) {\n"
        "  $process=Get-RunOwner $target\n"
        "  if ($process) {\n"
        "    try {\n"
        "      if (-not $KillLive) { throw ('refusing live sibling: ' + $target) }\n"
        "      $processId=$process.Id\n"
        "      & taskkill.exe /PID $processId /T /F 2>$null | Out-Null\n"
        "      if ($LASTEXITCODE -ne 0 -and -not $process.HasExited) {\n"
        "        throw 'cannot terminate phase-owner process tree'\n"
        "      }\n"
        "    } finally { $process.Dispose() }\n"
        "  }\n"
        "  if (Test-Path -LiteralPath $target) {\n"
        "    for ($attempt=0; $attempt -lt 20; $attempt++) {\n"
        "      try {\n"
        "        Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction Stop\n"
        "        break\n"
        "      } catch {\n"
        "        if (-not (Test-Path -LiteralPath $target)) { return }\n"
        "        if ($attempt -eq 19) { throw }\n"
        "        Start-Sleep -Milliseconds 250\n"
        "      }\n"
        "    }\n"
        "  }\n"
        "}\n"
    )


def _windows_startup_lock_function() -> str:
    return (
        "function Enter-StartupLock([string]$owned, [int]$waitMilliseconds=0) {\n"
        "  $until=[DateTime]::UtcNow.AddMilliseconds($waitMilliseconds)\n"
        "  do {\n"
        "    try { return [IO.File]::Open(($owned + '.startup-lock'), "
        "'OpenOrCreate', 'ReadWrite', 'None') }\n"
        "    catch [IO.IOException] {\n"
        "      if ([DateTime]::UtcNow -ge $until) {\n"
        "        throw 'refusing concurrent startup: remote startup lock is held'\n"
        "      }\n"
        "      Start-Sleep -Milliseconds 100\n"
        "    }\n"
        "  } while ($true)\n"
        "}\n"
    )


def _windows_initialize_script(token: str, stale_after: int = 6000) -> str:
    if not re.fullmatch(r"[0-9a-f]{24}", token):
        raise ValueError("Windows run token is not 24 lowercase hex characters")
    return (
        _powershell_prelude()
        + "\n"
        + "$runs=Join-Path $HOME '.ci-local\\runs'\n"
        + f"$owned=Join-Path $runs {_powershell_literal(WINDOWS_RUN_PREFIX)}\n"
        + _windows_remove_owned_run_function()
        + _windows_startup_lock_function()
        + "New-Item -ItemType Directory -Force -Path $owned | Out-Null\n"
        # An OS file lock serializes inspection + creation across SSH sessions.
        # The empty lock file lives beside (not inside) the run root. A crash
        # releases its handle; it is never an authority to kill another run.
        + "$lock=Enter-StartupLock $owned\n"
        + "try {\n"
        + f"$cancelled=Join-Path ($owned + '.cancelled') {_powershell_literal(token)}\n"
        + "if (Test-Path -LiteralPath $cancelled) { "
        + "throw 'refusing cancelled Windows run token' }\n"
        + "$siblings=@(Get-ChildItem -LiteralPath $owned -Force)\n"
        + "$siblings | ForEach-Object {\n"
        + "  if (-not $_.PSIsContainer) {\n"
        + "    throw ('unknown file in owned run root: ' + $_.FullName)\n"
        + "  }\n"
        + "  if ($_.Name -notmatch '^[0-9a-f]{24}$' -or "
        + "($_.Attributes -band [IO.FileAttributes]::ReparsePoint)) { "
        + "throw 'refusing unknown or linked run directory' }\n"
        + f"  Assert-StaleRun $_.FullName {stale_after}\n"
        + "}\n"
        + "$siblings | ForEach-Object {\n"
        + f"  Assert-StaleRun $_.FullName {stale_after}\n"
        + "  Remove-OwnedRun $_.FullName\n"
        + "}\n"
        + f"$run=Join-Path $owned {_powershell_literal(token)}\n"
        + "$checkout=Join-Path $run 'checkout'\n"
        + "$archive=Join-Path $run 'source.zip'\n"
        + "$bundle=Join-Path $run 'source.bundle'\n"
        + "New-Item -ItemType Directory -Path $run | Out-Null\n"
        + _windows_owner_record("$run")
        + "Write-Output ('RUN=' + $run)\n"
        + "Write-Output ('CHECKOUT=' + $checkout)\n"
        + "Write-Output ('ARCHIVE=' + $archive)\n"
        + "Write-Output ('BUNDLE=' + $bundle)\n"
        + "Remove-Item -LiteralPath $owner -Force\n"
        + "} finally { $lock.Dispose() }\n"
    )


def windows_initialize(host: str, token: str, *, timeout: float) -> WindowsWorkspace:
    values = _parse_key_values(
        _run_remote_capture(host, _windows_initialize_script(token), timeout=timeout),
        {"RUN", "CHECKOUT", "ARCHIVE", "BUNDLE"},
    )
    return WindowsWorkspace(
        run=_validate_remote_path(values["RUN"]),
        checkout=_validate_remote_path(values["CHECKOUT"]),
        archive=_validate_remote_path(values["ARCHIVE"]),
        bundle=_validate_remote_path(values["BUNDLE"]),
    )


def windows_cleanup(
    host: str,
    workspace: WindowsWorkspace | None,
    *,
    token: str | None = None,
    timeout: float = 60,
) -> None:
    if workspace is None:
        if token is None or not re.fullmatch(r"[0-9a-f]{24}", token):
            raise ValueError("Windows cleanup needs a valid run token")
        target = (
            "(Join-Path (Join-Path $HOME "
            + _powershell_literal(".ci-local\\runs\\" + WINDOWS_RUN_PREFIX)
            + ") "
            + _powershell_literal(token)
            + ")"
        )
    else:
        target = _powershell_literal(workspace.run)
    script = (
        _powershell_prelude()
        + "\n"
        + f"$target=[IO.Path]::GetFullPath({target})\n"
        + "$owned=[IO.Path]::GetFullPath((Join-Path $HOME "
        + f"{_powershell_literal('.ci-local\\runs\\' + WINDOWS_RUN_PREFIX)}))\n"
        + "$separator=[IO.Path]::DirectorySeparatorChar\n"
        + "$prefix=$owned.TrimEnd($separator) + $separator\n"
        + "if (-not $target.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {\n"
        + "  throw 'refusing to remove a path outside the owned run root'\n"
        + "}\n"
        + "if ((Split-Path -Parent $target) -ne $owned -or "
        + "(Split-Path -Leaf $target) -notmatch '^[0-9a-f]{24}$') { "
        + "throw 'refusing a non-token run directory' }\n"
        + _windows_remove_owned_run_function()
        + _windows_startup_lock_function()
        + "New-Item -ItemType Directory -Force -Path $owned | Out-Null\n"
        # A lost initialization response needs a durable cancellation marker:
        # the initializer may not have acquired the lock or created RUN yet.
        # Keep markers beside the run root, only for interrupted initialization;
        # a delayed initializer must never be allowed to reuse its cancelled token.
        + (
            "$cancelled=$owned + '.cancelled'\n"
            "New-Item -ItemType Directory -Force -Path $cancelled | Out-Null\n"
            "$marker=Join-Path $cancelled (Split-Path -Leaf $target)\n"
            "[IO.File]::WriteAllText($marker, 'cancelled')\n"
            if workspace is None
            else ""
        )
        + f"$lock=Enter-StartupLock $owned {max(0, min(45000, int((timeout - 10) * 1000)))}\n"
        + "try { Remove-OwnedRun $target -KillLive } finally { $lock.Dispose() }\n"
    )
    _run_remote_capture(host, script, timeout=timeout)


def _windows_owner_record(run_expression: str) -> str:
    # Record the wrapper, not uv: it remains live while streams drain and setup
    # runs. Remove the record BEFORE the wrapper exits normally. Between remote
    # calls, the recent-unowned rule protects the entire cumulative job budget.
    return (
        f"$owner=Join-Path {run_expression} 'phase-owner'\n"
        "$wrapper=Get-Process -Id $PID\n"
        "$pending=$owner + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'\n"
        "try {\n"
        "  $record=$PID.ToString() + '|' + $wrapper.StartTime.ToFileTimeUtc().ToString()\n"
        "  [IO.File]::WriteAllText($pending, $record, [Text.Encoding]::ASCII)\n"
        "  [IO.File]::Move($pending, $owner)\n"
        "} finally {\n"
        "  if (Test-Path -LiteralPath $pending) {\n"
        "    Remove-Item -LiteralPath $pending -Force -ErrorAction SilentlyContinue\n"
        "  }\n"
        "}\n"
    )


def _windows_setup_script(workspace: WindowsWorkspace, commit: str, remaining: float = 5400) -> str:
    return (
        _powershell_prelude()
        + "\n"
        + _windows_owner_record(_powershell_literal(workspace.run))
        + f"$deadline=[DateTime]::UtcNow.AddMilliseconds({max(1, int(remaining * 1000))})\n"
        + _windows_temporary_environment(workspace)
        + _windows_process_guard()
        + "$watchdog=[CiLocalDeadline]::new((Get-Process -Id $PID), "
        + "[Math]::Max(1, [int]($deadline - [DateTime]::UtcNow).TotalMilliseconds))\n"
        + "try {\n"
        + f"$archive={_powershell_literal(workspace.archive)}\n"
        + f"$bundle={_powershell_literal(workspace.bundle)}\n"
        + f"$checkout={_powershell_literal(workspace.checkout)}\n"
        + f"$commit={_powershell_literal(commit)}\n"
        + f"$git={_powershell_literal(WINDOWS_GIT)}\n"
        + f"$expectedGit={_powershell_literal(WINDOWS_GIT_SHA256)}\n"
        + "if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) {\n"
        + "  throw 'archive is missing'\n"
        + "}\n"
        + "if (-not (Test-Path -LiteralPath $bundle -PathType Leaf)) {\n"
        + "  throw 'Git bundle is missing'\n"
        + "}\n"
        + "$actualGit=(Get-FileHash -Algorithm SHA256 $git).Hash\n"
        + "if ($actualGit -ne $expectedGit) { "
        + "throw ('refusing Git SHA-256 ' + $actualGit) }\n"
        + "$env:PATH=(Split-Path -Parent $git) + [IO.Path]::PathSeparator + $env:PATH\n"
        + "New-Item -ItemType Directory -Path $checkout | Out-Null\n"
        + "Expand-Archive -LiteralPath $archive -DestinationPath $checkout -Force\n"
        + "Remove-Item -LiteralPath $archive -Force\n"
        + "& $git -C $checkout init --quiet\n"
        + "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
        + "& $git -C $checkout config --local core.longpaths true\n"
        + "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
        + "& $git -C $checkout fetch --quiet --force $bundle "
        + "'+refs/heads/*:refs/remotes/ci-local/*' '+refs/tags/*:refs/tags/*' "
        + "'+refs/ci-local/*:refs/ci-local/*'\n"
        + "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
        + "& $git -C $checkout checkout --quiet --force --detach $commit\n"
        + "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
        + "Remove-Item -LiteralPath $bundle -Force\n"
        + "} finally {\n"
        + "  $watchdog.Dispose()\n"
        + "  Remove-Item -LiteralPath $owner -Force -ErrorAction SilentlyContinue\n"
        + "}\n"
    )


def _windows_phase_script(
    workspace: WindowsWorkspace,
    python: str,
    arguments: Sequence[str],
    remaining: float = 5400,
) -> str:
    # Start-Process -PassThru yielded a null ExitCode on PowerShell 5.1. Own the
    # .NET Process so WaitForExit/ExitCode cannot turn a failing phase green.
    invocation = _powershell_literal(subprocess.list2cmdline(arguments))
    return (
        _powershell_prelude()
        + "\n"
        + _windows_owner_record(_powershell_literal(workspace.run))
        + f"$deadline=[DateTime]::UtcNow.AddMilliseconds({max(1, int(remaining * 1000))})\n"
        + f"$uv={_powershell_literal(WINDOWS_UV)}\n"
        + f"$expectedUv={_powershell_literal(WINDOWS_UV_SHA256)}\n"
        + "$actualUv=(Get-FileHash -Algorithm SHA256 $uv).Hash\n"
        + "if ($actualUv -ne $expectedUv) { throw ('refusing uv SHA-256 ' + $actualUv) }\n"
        + f"$git={_powershell_literal(WINDOWS_GIT)}\n"
        + f"$expectedGit={_powershell_literal(WINDOWS_GIT_SHA256)}\n"
        + "if (-not (Test-Path -LiteralPath $git -PathType Leaf)) { "
        + "throw 'pinned MinGit is missing' }\n"
        + "$actualGit=(Get-FileHash -Algorithm SHA256 $git).Hash\n"
        + "if ($actualGit -ne $expectedGit) { "
        + "throw ('refusing Git SHA-256 ' + $actualGit) }\n"
        + "$gitDir=Split-Path -Parent $git\n"
        + "$env:PATH=$gitDir + [IO.Path]::PathSeparator + $env:PATH\n"
        # Git's SHA:path disambiguation also stats paths in temporary test
        # repositories. Pytest resolves DOS aliases back to physical names,
        # so the checkout-local setting alone cannot cover those children.
        + "$env:GIT_CONFIG_COUNT='1'\n"
        + "$env:GIT_CONFIG_KEY_0='core.longpaths'\n"
        + "$env:GIT_CONFIG_VALUE_0='true'\n"
        + f"$env:UV_PYTHON={_powershell_literal(python)}\n"
        + "$env:UV_PYTHON_DOWNLOADS='never'\n"
        + "$env:CI='true'\n"
        + f"$env:UV_CACHE_DIR={_powershell_literal(workspace.run + r'\uv-cache')}\n"
        + _windows_temporary_environment(workspace)
        + "$env:PYTHONIOENCODING='utf-8'\n"
        + f"Set-Location -LiteralPath {_powershell_literal(workspace.checkout)}\n"
        + "$process=[System.Diagnostics.Process]::new()\n"
        + "$process.StartInfo.FileName=$uv\n"
        + f"$process.StartInfo.Arguments={invocation}\n"
        + f"$process.StartInfo.WorkingDirectory={_powershell_literal(workspace.checkout)}\n"
        + "$process.StartInfo.UseShellExecute=$false\n"
        + "$process.StartInfo.CreateNoWindow=$true\n"
        # Redirecting stdin makes .NET Framework populate STARTF_USESTDHANDLES
        # even with CREATE_NO_WINDOW, so stdout/stderr inherit SSH's handles.
        # Close it immediately after start: CI commands receive EOF, not a prompt.
        + "$process.StartInfo.RedirectStandardInput=$true\n"
        # Inherit the SSH streams directly. Reading redirected pipes to EOF
        # before observing the leader's exit can hang on a leaked descendant
        # that inherited those pipes. The wrapper's private job kills any such
        # descendants when it exits, after preserving the leader's real code.
        + "$process.StartInfo.RedirectStandardOutput=$false\n"
        + "$process.StartInfo.RedirectStandardError=$false\n"
        + _windows_process_guard()
        + "if (-not $process.Start()) { throw 'could not start Windows phase' }\n"
        + "$process.StandardInput.Close()\n"
        + "$watchdog=[CiLocalDeadline]::new($process, "
        + "[Math]::Max(1, [int]($deadline - [DateTime]::UtcNow).TotalMilliseconds))\n"
        + "$code=1\n"
        + "try {\n"
        + "  $process.WaitForExit()\n"
        + "  $code=$process.ExitCode\n"
        + "  if ($watchdog.TimedOut) { $code=124 }\n"
        + "  if ($null -eq $code) { throw 'Windows phase exit code is unavailable' }\n"
        + "} finally {\n"
        + "  $watchdog.Dispose()\n"
        + "  if (-not $process.HasExited) {\n"
        + "    & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null\n"
        + "    $process.WaitForExit()\n"
        + "  }\n"
        + "  if ($process.HasExited) {\n"
        + "    Remove-Item -LiteralPath $owner -Force -ErrorAction SilentlyContinue\n"
        + "  }\n"
        + "  $process.Dispose()\n"
        + "}\n"
        + "exit $code\n"
    )


def _windows_temporary_environment(workspace: WindowsWorkspace) -> str:
    return (
        f"$env:TEMP={_powershell_literal(workspace.run + r'\tmp')}\n"
        "$env:TMP=$env:TEMP\n"
        "New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null\n"
        # The release-artifact tests intentionally require a short system temp
        # prefix. Use the directory's EXISTING DOS alias, not a shared external
        # directory, drive mapping, or persistent filesystem configuration.
        "Add-Type -TypeDefinition @'\n"
        "using System.Text;\n"
        "using System.Runtime.InteropServices;\n"
        "public static class CiLocalShortPath {\n"
        ' [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]\n'
        " public static extern uint GetShortPathName(string path, "
        "StringBuilder output, uint size);\n"
        "}\n"
        "'@\n"
        "$short=[Text.StringBuilder]::new(4096)\n"
        "$length=[CiLocalShortPath]::GetShortPathName($env:TEMP, $short, 4096)\n"
        "if ($length -eq 0 -or $length -ge 4096 -or $length -gt 60) {\n"
        "  throw 'run-local TEMP needs an existing short path (at most 60 characters)'\n"
        "}\n"
        "$env:TEMP=$short.ToString()\n"
        "$env:TMP=$env:TEMP\n"
    )


def _windows_process_guard() -> str:
    # The wrapper alone owns a non-inheritable, unnamed job handle. Assigning
    # itself before launching a phase also assigns all ordinary descendants.
    # Windows closes this handle on normal exit or abrupt termination, killing
    # remaining descendants without finding them by reusable PIDs. Retain the
    # raw handle until process exit: disposing it early would kill the wrapper.
    # Nested jobs are supported on the qualified modern Windows profile; refuse
    # a failed assignment rather than silently losing process ownership.
    return """Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;
public static class CiLocalProcessJob {
    static IntPtr ownedJob;
    [StructLayout(LayoutKind.Sequential)]
    struct BasicLimits {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass, SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct IoCounters {
        public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
        public ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct ExtendedLimits {
        public BasicLimits BasicLimitInformation;
        public IoCounters IoInfo;
        public UIntPtr ProcessMemoryLimit, JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
    static extern IntPtr CreateJobObjectW(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool SetInformationJobObject(IntPtr job, int informationClass,
        ref ExtendedLimits information, uint length);
    [DllImport("kernel32.dll", SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")]
    static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll", SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool CloseHandle(IntPtr handle);
    public static void Attach() {
        if (ownedJob != IntPtr.Zero) throw new InvalidOperationException("job already attached");
        // NULL security attributes make the handle non-inheritable; NULL name
        // prevents a second process opening it by name and extending its life.
        IntPtr job = CreateJobObjectW(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        try {
            var limits = new ExtendedLimits();
            limits.BasicLimitInformation.LimitFlags = 0x00002000; // KILL_ON_JOB_CLOSE
            if (!SetInformationJobObject(job, 9, ref limits,
                    (uint)Marshal.SizeOf(typeof(ExtendedLimits))))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            if (!AssignProcessToJobObject(job, GetCurrentProcess()))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            ownedJob = job;
            job = IntPtr.Zero;
        } finally { if (job != IntPtr.Zero) CloseHandle(job); }
    }
}
public sealed class CiLocalDeadline : IDisposable {
    readonly Process child;
    readonly Timer timer;
    public volatile bool TimedOut;
    public CiLocalDeadline(Process child, int milliseconds) {
        this.child = child;
        timer = new Timer(Expire, null, milliseconds, Timeout.Infinite);
    }
    void Expire(object state) {
        try {
            if (child.HasExited) return;
            TimedOut = true;
            var info = new ProcessStartInfo("taskkill.exe", "/PID " + child.Id + " /T /F");
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            using (var killer = Process.Start(info)) { killer.WaitForExit(); }
        } catch (InvalidOperationException) { }
    }
    public void Dispose() { using (var done = new ManualResetEvent(false)) {
        if (timer.Dispose(done)) done.WaitOne();
    } }
}
'@
[CiLocalProcessJob]::Attach()
"""


def _remaining_seconds(started: float, limit: int) -> float:
    remaining = limit - (time.monotonic() - started)
    if remaining <= 0:
        raise RuntimeError(f"{WINDOWS_JOB} exhausted its cumulative {limit}s job budget")
    return remaining


def _windows_workflow_contract(
    source: Path, commit: str
) -> tuple[int, tuple[str, ...], tuple[tuple[str, tuple[str, ...]], ...]]:
    yaml_text = _run_git(["show", f"{commit}:.github/workflows/ci.yml"], cwd=source)
    try:
        document = yaml.safe_load(yaml_text)
    except (
        yaml.YAMLError,
        ValueError,
        TypeError,
        LookupError,
        ArithmeticError,
        AttributeError,
        RecursionError,
    ) as error:
        # SafeLoader's explicit scalar constructors can raise built-in
        # conversion errors; excessive nesting raises RecursionError.
        mark = getattr(error, "problem_mark", None)
        location = "" if mark is None else f" at line {mark.line + 1}, column {mark.column + 1}"
        raise ValueError("ci.yml contains malformed or unsupported YAML" + location) from error
    # Both settings are inherited by every run step in Actions. Until the
    # remote leg implements them, reject their presence even when empty.
    if isinstance(document, dict) and {"env", "defaults"} & document.keys():
        raise ValueError("unsupported Windows workflow defaults/environment")
    try:
        job = document["jobs"][WINDOWS_JOB]
        minutes = job["timeout-minutes"]
        versions = job["strategy"]["matrix"]["python-version"]
        steps = job["steps"]
    except (KeyError, TypeError) as error:
        raise ValueError(f"ci.yml has no supported {WINDOWS_JOB} contract") from error
    if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes <= 0:
        raise ValueError(f"ci.yml {WINDOWS_JOB} timeout-minutes is not a positive integer")
    if (
        not isinstance(versions, list)
        or not versions
        or any(not isinstance(v, str) or v not in WINDOWS_PYTHONS for v in versions)
    ):
        raise ValueError("ci.yml Windows Python matrix has an unqualified interpreter")
    if set(job) - {"runs-on", "timeout-minutes", "strategy", "permissions", "steps"}:
        raise ValueError("unsupported Windows job conditions/environment")
    if set(job["strategy"]) != {"matrix"} or set(job["strategy"]["matrix"]) != {"python-version"}:
        raise ValueError("unsupported Windows matrix strategy")
    if not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps):
        raise ValueError("ci.yml Windows steps must be a list of mappings")
    setup_actions: tuple[tuple[str, dict[str, object]], ...] = (
        ("actions/checkout@", {"fetch-depth": 0}),
        (
            "astral-sh/setup-uv@",
            {
                "version": "0.12.0",
                "python-version": "${{ matrix.python-version }}",
                "enable-cache": True,
            },
        ),
    )
    setup_index = 0
    phases: list[tuple[str, tuple[str, ...]]] = []
    for step in steps:
        if "name" in step and not isinstance(step["name"], str):
            raise ValueError("unsupported Windows step name")
        if "uses" in step:
            if set(step) - {"uses", "name", "with"}:
                raise ValueError("unsupported Windows action conditions/environment")
            if setup_index == len(setup_actions) or phases:
                raise ValueError("unsupported Windows setup action order")
            action, inputs = setup_actions[setup_index]
            actual_inputs = step.get("with")
            # Python equality aliases False with 0 and 1 with True. These are
            # distinct action inputs, so match qualified literals by type too.
            if (
                not isinstance(step["uses"], str)
                or not step["uses"].startswith(action)
                or not isinstance(actual_inputs, dict)
                or actual_inputs.keys() != inputs.keys()
                or any(
                    type(actual_inputs[key]) is not type(value) or actual_inputs[key] != value
                    for key, value in inputs.items()
                )
            ):
                raise ValueError("unsupported Windows setup action")
            setup_index += 1
            continue
        if setup_index != len(setup_actions):
            raise ValueError("unsupported Windows setup action order: missing setup prefix")
        command = step.get("run", "")
        if (
            set(step) - {"name", "run"}
            or not isinstance(command, str)
            or not re.fullmatch(r"uv [A-Za-z0-9_./=: -]+\s*", command)
        ):
            raise ValueError("unsupported Windows phase shell/conditions/environment")
        phases.append((step.get("name", command), tuple(shlex.split(command)[1:])))
    if not phases:
        raise ValueError("ci.yml Windows job has no phases")
    return minutes * 60, tuple(versions), tuple(phases)


def run_windows_job(
    *,
    source: Path,
    commit: str,
    host: str,
    version: str,
    local_root: Path,
) -> bool:
    python = WINDOWS_PYTHONS[version]
    timeout_seconds, versions, phases = _windows_workflow_contract(source, commit)
    if version not in versions:
        raise ValueError(f"Python {version} is not in the tested commit's Windows matrix")
    if timeout_seconds + WINDOWS_STALE_MARGIN > 6000:
        raise ValueError("Windows job budget exceeds the qualified startup stale threshold")
    started = time.monotonic()
    workspace: WindowsWorkspace | None = None
    token: str | None = None
    unwinding = False
    try:
        sweep_legacy_windows_refs(source)
        windows_preflight(host, python, timeout=_remaining_seconds(started, timeout_seconds))
        token = secrets.token_hex(12)
        workspace = windows_initialize(
            host, token, timeout=_remaining_seconds(started, timeout_seconds)
        )
        archive = local_root / "source.zip"
        bundle = local_root / "source.bundle"
        _run_git(["archive", "--format=zip", f"--output={archive}", commit], cwd=source)
        create_windows_bundle(source, commit, bundle, token)
        remote_run = workspace.run.replace("\\", "/") + "/"
        print("\n=== Transfer git archive and metadata bundle ===", flush=True)
        if (
            _run_streamed(
                ["scp", *SSH_OPTIONS, str(archive), str(bundle), f"{host}:{remote_run}"],
                timeout=_remaining_seconds(started, timeout_seconds),
            )
            != 0
        ):
            return False
        print("\n=== Expand git archive ===", flush=True)
        remaining = _remaining_seconds(started, timeout_seconds)
        if (
            _run_streamed(
                powershell_ssh_command(host, _windows_setup_script(workspace, commit, remaining)),
                timeout=remaining,
            )
            != 0
        ):
            return False

        total = len(phases)
        for index, (name, arguments) in enumerate(phases, start=1):
            remaining = _remaining_seconds(started, timeout_seconds)
            print(
                f"\n=== Windows step {index}/{total}: {name} "
                f"({remaining:.1f}s remaining of {timeout_seconds}s) ===",
                flush=True,
            )
            phase_started = time.monotonic()
            returncode = _run_streamed(
                powershell_ssh_command(
                    host,
                    _windows_phase_script(workspace, python, arguments, remaining),
                ),
                timeout=remaining,
            )
            duration = time.monotonic() - phase_started
            outcome = "PASS" if returncode == 0 else "TIMEOUT" if returncode == 124 else "FAIL"
            print(f"[{outcome}] {name}: {duration:.1f}s", flush=True)
            if returncode != 0:
                return False
        print(
            f"\nci-local Windows {version} passed all {total} test-windows phases in "
            f"{time.monotonic() - started:.1f}s"
        )
        return True
    except BaseException:
        unwinding = True
        raise
    finally:
        if workspace is not None or token is not None:
            with _blocked_interrupt_signals():
                try:
                    if workspace is not None:
                        windows_cleanup(host, workspace)
                    else:
                        windows_cleanup(host, None, token=token)
                except RuntimeError as error:
                    print(f"ci-local: remote cleanup failed: {error}", file=sys.stderr)
                    if not unwinding:
                        raise


def create_windows_bundle(source: Path, commit: str, bundle: Path, token: str) -> None:
    # CI fetch-depth:0 sees origin's branches and tags, not local backup/probe
    # refs. Keep the synthetic commit ref in a private repository: SIGKILL can
    # no longer leak refs/ci-local/* into the user's source repository.
    metadata = bundle.parent / "metadata.git"
    _run_git(["init", "--bare", str(metadata)], cwd=source)
    _run_git(
        [
            "fetch",
            "--quiet",
            "--no-tags",
            str(source),
            commit,
            "+refs/remotes/origin/*:refs/heads/*",
            "+refs/tags/*:refs/tags/*",
        ],
        cwd=metadata,
    )
    _run_git(["update-ref", f"refs/ci-local/{token}", commit], cwd=metadata)
    _run_git(["bundle", "create", str(bundle), "--all"], cwd=metadata)


def sweep_legacy_windows_refs(source: Path) -> None:
    """Recover only old loose token refs left by the pre-private-bundle runner.

    Recent/packed/unrecognised refs have no sufficient age evidence and stay put.
    Compare-and-delete also refuses a concurrent update. New runs never create
    refs in this namespace in the source repository.
    """
    for line in _run_git(
        ["for-each-ref", "--format=%(refname) %(objectname)", "refs/ci-local/"], cwd=source
    ).splitlines():
        ref, sha = line.split()
        if not re.fullmatch(r"refs/ci-local/[0-9a-f]{24}", ref):
            continue
        path = Path(_run_git(["rev-parse", "--git-path", ref], cwd=source))
        if not path.is_absolute():
            path = source / path
        if not path.is_symlink() and path.is_file() and time.time() - path.stat().st_mtime > 6000:
            _run_git(["update-ref", "-d", ref, sha], cwd=source)
            print(f"Removed stale legacy local ref: {ref}", flush=True)


def _die(message: str) -> NoReturn:
    print(f"ci-local: {message}", file=sys.stderr)
    raise SystemExit(2)


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.check_browser_os_dependencies:
        return 0 if browser_dependencies_available(Path.cwd()) else 1
    if sys.platform != "linux":
        _die(f"ci-local needs a Linux controller host (this is {sys.platform})")
    if not args.windows and args.python is None:
        _die("--python is required for the Linux checks")
    if args.windows and args.python is not None:
        _die("--python is for the Linux checks; use --windows-python with --windows")
    if args.commit.startswith("-"):
        _die("--commit must not begin with '-'")
    try:
        source = repository_root(Path.cwd())
        commit = _run_git(["rev-parse", "--verify", f"{args.commit}^{{commit}}"], cwd=source)
        python = None if args.windows else resolve_interpreter(args.python)
    except (RuntimeError, ValueError) as error:
        _die(str(error))

    run_root: Path | None = None
    try:
        with _interrupt_on_signals():
            run_root = Path(tempfile.mkdtemp(prefix="gpo-studio-ci-local-"))
            print(f"Run directory: {run_root}", flush=True)
            started = time.monotonic()
            try:
                if args.windows:
                    print(f"Commit: {commit}")
                    print(f"Windows host: {args.windows_host}")
                    print(f"Windows Python: {args.windows_python}")
                    print(
                        "Scope: the test-windows job's install, Ruff, mypy, pytest and "
                        "coverage-floor commands, from a git archive. No AD or SYSVOL access, "
                        "frontend checks, Node tooling, or system installation is included."
                    )
                    return (
                        0
                        if run_windows_job(
                            source=source,
                            commit=commit,
                            host=args.windows_host,
                            version=args.windows_python,
                            local_root=run_root,
                        )
                        else 1
                    )
                assert python is not None
                checkout = fresh_clone(source, commit, run_root)
                commands = required_commands(checkout / "CONTRIBUTING.md")
                workflow = checkout / ".github" / "workflows" / "ci.yml"
                timeouts = workflow_timeouts(workflow)
                steps = [
                    *_preparation_steps(timeouts, python),
                    *derive_steps(commands, workflow),
                ]

                pytest_base = run_root / "pytest"
                for directory in (
                    run_root / "tmp",
                    run_root / "uv-cache",
                    run_root / "npm-cache",
                    run_root / "playwright-browsers",
                    pytest_base,
                ):
                    directory.mkdir()
                steps = with_external_pytest_base(steps, pytest_base)
                environment = step_environment(run_root=run_root, checkout=checkout, python=python)

                print(f"Commit: {commit}")
                print(f"Python: {python}")
                print(
                    "Scope: Linux cold-cache and fresh-install checks only. This pre-R1 mode "
                    "does not reproduce inline workflow shell or Windows-only failures."
                )
                total_steps = len(steps)
                if not run_steps(steps, checkout=checkout, environment=environment):
                    print(f"ci-local failed after {time.monotonic() - started:.1f}s")
                    return 1
                print(
                    f"\nci-local passed all {total_steps} steps in "
                    f"{time.monotonic() - started:.1f}s"
                )
                return 0
            except (RuntimeError, ValueError, KeyError) as error:
                print(f"ci-local: {error}", file=sys.stderr)
                return 2
            finally:
                if args.keep:
                    print(f"Kept run directory: {run_root}")
                else:
                    with _blocked_interrupt_signals():
                        shutil.rmtree(run_root, ignore_errors=True)
    except (KeyboardInterrupt, _SignalInterruption) as interruption:
        signum = (
            signal.SIGINT if isinstance(interruption, KeyboardInterrupt) else interruption.signum
        )
        print(f"ci-local: interrupted by {signal.Signals(signum).name}", file=sys.stderr)
        return 128 + signum


if __name__ == "__main__":
    raise SystemExit(main())
