#!/usr/bin/env python3
"""Run GPO Studio's documented Linux checks in a fresh, cold-cache clone.

The named commit supplies the documented shell commands, so run only commits
whose contents are trusted to execute with the invoking user's privileges.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import NoReturn

import yaml

WINDOWS_REFUSAL = (
    "the Windows leg is unavailable: Plan 036 D-7 leaves installing uv on "
    "mvmcitest01 open until the box's other users agree"
)

_JOB_BY_COMMAND = {
    "uv run ruff check .": "test",
    "uv run mypy src": "test",
    (
        "uv run pytest -q --cov=src/gpo_studio --cov-branch "
        "--cov-report=json:coverage.json"
    ): "test",
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


@dataclass(frozen=True)
class Step:
    name: str
    command: str
    job: str
    timeout_seconds: int


class _SignalInterruption(BaseException):
    def __init__(self, signum: int) -> None:
        self.signum = signum
        super().__init__(signum)


@contextmanager
def _interrupt_on_signals() -> Iterator[None]:
    previous = {
        signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)
    }

    def interrupt(signum: int, _frame: FrameType | None) -> NoReturn:
        raise _SignalInterruption(signum)

    for signum in previous:
        signal.signal(signum, interrupt)
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


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
        help="request the Windows leg (currently refused by Plan 036 D-7)",
    )
    result.add_argument(
        "--keep",
        action="store_true",
        help="retain the fresh clone and throwaway caches after the run",
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
        opening = next(
            index for index in range(heading + 1, len(lines)) if lines[index] == "```bash"
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


def run_step(step: Step, *, checkout: Path, environment: dict[str, str]) -> tuple[bool, float]:
    print(
        f"\n=== {step.name} (ci job {step.job}, timeout {step.timeout_seconds // 60} min) ===",
        flush=True,
    )
    print(f"$ {step.command}", flush=True)
    started = time.monotonic()
    process = subprocess.Popen(
        ["bash", "-o", "pipefail", "-c", step.command],
        cwd=checkout,
        env=environment,
        text=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        returncode = process.wait(timeout=step.timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _stop_process(process)
        returncode = process.returncode
    except (KeyboardInterrupt, _SignalInterruption):
        _stop_process(process)
        raise
    duration = time.monotonic() - started
    outcome = "TIMEOUT" if timed_out else "PASS" if returncode == 0 else "FAIL"
    print(
        f"[{outcome}] {step.name}: {duration:.1f}s / {step.timeout_seconds}s "
        f"({step.job} job timeout)",
        flush=True,
    )
    return returncode == 0 and not timed_out, duration


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
            command="node_modules/.bin/playwright install --with-deps chromium firefox",
            job="frontend",
            timeout_seconds=timeouts["frontend"],
        ),
    ]


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
    for directory in (isolated_home, isolated_config):
        directory.mkdir()
    npm_userconfig.touch()
    environment.update(
        {
            "CI": "true",
            "HOME": str(isolated_home),
            "XDG_CONFIG_HOME": str(isolated_config),
            "TMPDIR": str(run_root / "tmp"),
            "UV_CACHE_DIR": str(run_root / "uv-cache"),
            "UV_PYTHON_DOWNLOADS": "never",
            "NPM_CONFIG_CACHE": str(run_root / "npm-cache"),
            "NPM_CONFIG_USERCONFIG": str(npm_userconfig),
            "PIP_CONFIG_FILE": os.devnull,
            "PLAYWRIGHT_BROWSERS_PATH": str(run_root / "playwright-browsers"),
            "PYTHON": str(python),
            "GPO_STUDIO_TEST_PYTHON": str(checkout / ".venv" / "bin" / "python"),
        }
    )
    return environment


def _die(message: str) -> NoReturn:
    print(f"ci-local: {message}", file=sys.stderr)
    raise SystemExit(2)


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.windows:
        _die(WINDOWS_REFUSAL)
    if args.python is None:
        _die("--python is required for the Linux checks")
    if args.commit.startswith("-"):
        _die("--commit must not begin with '-'")
    try:
        python = resolve_interpreter(args.python)
        source = repository_root(Path.cwd())
        commit = _run_git(["rev-parse", "--verify", f"{args.commit}^{{commit}}"], cwd=source)
    except (RuntimeError, ValueError) as error:
        _die(str(error))

    run_root: Path | None = None
    try:
        with _interrupt_on_signals():
            run_root = Path(tempfile.mkdtemp(prefix="gpo-studio-ci-local-"))
            print(f"Run directory: {run_root}", flush=True)
            started = time.monotonic()
            try:
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
                environment = step_environment(
                    run_root=run_root, checkout=checkout, python=python
                )

                print(f"Commit: {commit}")
                print(f"Python: {python}")
                print(
                    "Scope: Linux cold-cache and fresh-install checks only. This pre-R1 mode "
                    "does not reproduce inline workflow shell or Windows-only failures."
                )
                total_steps = len(steps)
                for index, step in enumerate(steps, start=1):
                    print(f"\nStep {index}/{total_steps}", flush=True)
                    passed, _duration = run_step(
                        step, checkout=checkout, environment=environment
                    )
                    if not passed:
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
                    shutil.rmtree(run_root, ignore_errors=True)
    except (KeyboardInterrupt, _SignalInterruption) as interruption:
        signum = (
            signal.SIGINT
            if isinstance(interruption, KeyboardInterrupt)
            else interruption.signum
        )
        print(f"ci-local: interrupted by {signal.Signals(signum).name}", file=sys.stderr)
        return 128 + signum


if __name__ == "__main__":
    raise SystemExit(main())
