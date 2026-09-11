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

The sixteen files cannot simply be renormalized here, because the WI-062 batch
binds their digests: fixing them invalidates 19 of the 21 banked verdicts and
costs an estate requalification. So this module pins the damage instead. The
exemption list is the debt, written down where a seventeenth file cannot join
it silently, and `test_no_exempt_file_was_quietly_fixed` is the control that
stops the list from outliving the problem -- the same shape as
`ORPHANED_VERDICT_COMMITS` and `PENDING_REQUALIFICATION` in
`test_committed_evidence.py`, and for the same reason: an exemption with no
expiry is just a permission.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ORACLE_DIR = REPO_ROOT / "scripts" / "windows-oracle"
PLAN_033_DIR = REPO_ROOT / "scripts" / "plan-033"

#: Controller-side source committed with CRLF by `f5cad577`, hash-bound by the
#: WI-062 batch, and therefore not renormalizable until the estate re-runs.
#: Every entry closes with WI-063. Nothing else may be added: a new CRLF file
#: is a new defect, not a new row here.
CRLF_PENDING_RENORMALIZATION = frozenset(
    {
        "finalize_endpoint_run.py",
        "finalize_object_security_run.py",
        "finalize_publication_run.py",
        "finalize_rsop_run.py",
        "finalize_rsop_user_run.py",
        "finalize_scripts_backup_run.py",
        "finalize_wp2_import_run.py",
        "finalize_wp3_run.py",
        "run-endpoint-oracle.sh",
        "run-object-security-oracle.sh",
        "run-publication-oracle.sh",
        "run-rsop-oracle.sh",
        "run-rsop-user-oracle.sh",
        "run-scripts-backup-oracle.sh",
        "run-wp2-oracle.sh",
        "run-wp3-oracle.sh",
    }
)


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
        path for path in ORACLE_DIR.glob("run-*-oracle.sh")
        if path.name not in CRLF_PENDING_RENORMALIZATION
    ),
    ids=lambda path: path.name,
)
def test_every_lane_runner_parses_under_bash(runner: Path) -> None:
    """The runbooks say `bash <runner>`; a runner that cannot be parsed is dead.

    `bash -n` and not a CR scan, because parseability is the property that
    actually matters and CRLF is only today's way of losing it.
    """
    if shutil.which("bash") is None:  # pragma: no cover - depends on the host
        pytest.skip("no bash on this host; test_no_new_controller_source_carries_crlf still runs")
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
        if path.name not in CRLF_PENDING_RENORMALIZATION
        and b"\r\n" in path.read_bytes()
    )
    assert not offenders, (
        f"These controller-side sources carry CRLF: {offenders}. They are "
        "declared `-text` in .gitattributes, so git will neither normalize "
        "them nor report drift -- the bytes commit exactly as an editor left "
        "them (WI-063). Convert to LF before committing; do not add them to "
        "CRLF_PENDING_RENORMALIZATION, which records a debt that already "
        "exists rather than licensing a new one."
    )


def test_no_exempt_file_was_quietly_fixed() -> None:
    """The control: the list must expire, and only through a requalification.

    Renormalizing one of these changes a digest that 19 banked verdicts bind,
    so a fix that lands without re-running the lane leaves the evidence
    claiming a harness that no longer ships. Failing here is the reminder that
    the two have to move together.
    """
    stale = sorted(
        name
        for name in CRLF_PENDING_RENORMALIZATION
        if b"\r\n" not in (ORACLE_DIR / name).read_bytes()
    )
    assert not stale, (
        f"These files are no longer CRLF: {stale}. Their digests are bound by "
        "the WI-062 batch, so the lanes must be re-run and their verdicts "
        "re-banked before the entries come off this list -- see WI-063. If the "
        "requalification has happened, drop the entries and the "
        "`scripts/windows-oracle/** -text` rule together."
    )


def test_the_exemption_list_names_files_that_exist() -> None:
    """A list that has drifted off its files guards nothing and says nothing."""
    missing = sorted(
        name for name in CRLF_PENDING_RENORMALIZATION if not (ORACLE_DIR / name).is_file()
    )
    assert not missing, f"CRLF_PENDING_RENORMALIZATION names absent files: {missing}"
