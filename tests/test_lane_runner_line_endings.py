"""WI-063: a lane runner that does not parse is not a runnable lane.

`f5cad577` committed sixteen controller-side harness files with CRLF line
endings. The eight Python finalizers survive it -- CPython reads universal
newlines -- but the eight `run-*-oracle.sh` runners do not: `bash` reads the
trailing CR as part of the token and every one of them now fails to parse,
while the documented way to start a lane is `bash
scripts/windows-oracle/run-wp3-oracle.sh`.

Nothing caught it, and the reason is worth writing down next to the check.
`assert_bound_source_bytes` (WI-059) refuses to finalize when worktree, index
and HEAD disagree, which is exactly the shape a Windows-side CRLF edit takes --
*when the path is declared `text eol=lf`*. These paths are declared `-text`, so
there is no normalization for the working tree to disagree with: CRLF file,
CRLF blob, clean status, passing check, changed bytes. `.gitattributes` chose
`-text` to stop a Windows checkout smudging LF into CRLF; it also stops the
reverse, which is the half that was doing the guarding.

The sixteen files could not be renormalized while the WI-062 batch bound their
digests. The Plan 034 requalification batch renormalized them and re-earned
every verdict, so the exemption list this module used to carry is gone. What
remains is the rule itself and the declaration that enforces it:
`test_controller_trees_are_declared_lf` fails if `.gitattributes` goes back to
`-text` for these trees, which is the change that let WI-063 commit cleanly.
"""

from __future__ import annotations

import functools
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ORACLE_DIR = REPO_ROOT / "scripts" / "windows-oracle"
PLAN_033_DIR = REPO_ROOT / "scripts" / "plan-033"

@functools.cache
def _bash_can_check_a_file() -> bool:
    """Whether `bash -n <path>` on this host means anything.

    Not `shutil.which("bash")`: on a GitHub Windows runner that finds
    `C:\\Windows\\System32\\bash.exe`, the WSL launcher, which exists, is on
    PATH, and exits 1 for everything because no distribution is installed --
    so every runner "failed to parse" with empty stderr. The capability this
    test needs is `bash -n` against a path *this* interpreter wrote, so probe
    exactly that: a file that must parse, through the same call the assertion
    makes. A host where the probe fails cannot distinguish a broken runner
    from a broken bash, and skips.
    """
    with tempfile.TemporaryDirectory() as directory:
        probe = Path(directory) / "probe.sh"
        probe.write_bytes(b"#!/usr/bin/env bash\nprobe() { :; }\nprobe\n")
        try:
            return (
                subprocess.run(
                    ["bash", "-n", str(probe)], capture_output=True
                ).returncode
                == 0
            )
        except OSError:
            return False


def _controller_sources() -> list[Path]:
    """Every file the controller executes from the tree, not from a guest.

    `scripts/plan-033/build-*.py` is included because it carries the same
    `-text` rule and is only still LF by luck.
    """
    return sorted(
        [path for path in ORACLE_DIR.iterdir() if path.is_file()]
        + sorted(PLAN_033_DIR.glob("build-*.py"))
    )


@pytest.mark.parametrize(
    "runner",
    sorted(
        ORACLE_DIR.glob("run-*-oracle.sh")
    ),
    ids=lambda path: path.name,
)
def test_every_lane_runner_parses_under_bash(runner: Path) -> None:
    """The runbooks say `bash <runner>`; a runner that cannot be parsed is dead.

    `bash -n` and not a CR scan, because parseability is the property that
    actually matters and CRLF is only today's way of losing it.
    """
    if not _bash_can_check_a_file():  # pragma: no cover - depends on the host
        pytest.skip(
            "no bash here that can syntax-check a file; "
            "test_no_new_controller_source_carries_crlf still runs"
        )
    result = subprocess.run(
        ["bash", "-n", str(runner)], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"{runner.name} does not parse under bash, so the documented "
        f"`bash scripts/windows-oracle/{runner.name}` cannot start the lane:\n"
        f"{result.stderr.strip()}"
    )


def test_no_new_controller_source_carries_crlf() -> None:
    """The general rule the exemption list is carved out of.

    Both script trees are LF everywhere else -- including every `.ps1`, which
    Windows reads happily either way -- so LF is the convention and CRLF is the
    anomaly, not a per-file judgement call.
    """
    offenders = sorted(
        path.name
        for path in _controller_sources()
        if b"\r\n" in path.read_bytes()
    )
    assert not offenders, (
        f"These controller-side sources carry CRLF: {offenders}. They are "
        "declared `-text` in .gitattributes, so git will neither normalize "
        "them nor report drift -- the bytes commit exactly as an editor left "
        "them (WI-063). Convert to LF before committing."
    )


def test_controller_trees_are_declared_lf() -> None:
    """The declaration that keeps WI-063 from recurring.

    Under `-text`, a CRLF working tree and a CRLF blob agree, so
    `assert_bound_source_bytes` sees no drift and the broken bytes commit.
    Under `text eol=lf` the same edit shows up as a worktree/HEAD mismatch
    and the finalizer refuses it.
    """
    paths = [str(path.relative_to(REPO_ROOT)) for path in _controller_sources()]
    result = subprocess.run(
        ["git", "check-attr", "text", "eol", "--", *paths],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    attributes: dict[str, dict[str, str]] = {}
    for line in result.stdout.splitlines():
        path, name, value = (part.strip() for part in line.split(":", 2))
        attributes.setdefault(path, {})[name] = value
    wrong = sorted(
        path
        for path, values in attributes.items()
        if values.get("text") != "set" or values.get("eol") != "lf"
    )
    assert not wrong, (
        f"These controller-side sources are not declared `text eol=lf`: {wrong}"
    )
