"""WI-037: staging must not destroy the previous run's evidence.

Every long-running lane's ``PREPARE`` step used to remove all directories under
the guest's output root before it staged anything. That was harmless when a
failed run left nothing worth keeping, and stopped being harmless once a failure
left its observation, its ``commands/`` transcripts and its verify JSON on the
guest: the next run deleted exactly the evidence a human needed to explain why
the last one failed. It cost real time twice in one session.

Preserving those directories creates a second hazard in the same breath, and
these tests pin both halves together because fixing one without the other is
worse than fixing neither. Once old run directories survive, a fallback that
selects "the newest output directory" can pull the LAST run's observation and
hand it to the finalizer as this one's -- an unattributable failure turned into
a confidently mis-attributed pass.

Text assertions on a bash script, for the reason `test_lane_transport.py` gives
for its own: the contract is between a shell script and a Python finalizer with
no shared type, so it is checked here rather than assumed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[1]
ORACLE_DIR = REPO_ROOT / "scripts" / "windows-oracle"

#: The lanes whose output root is SHARED across runs, and the artifact that
#: identifies a directory as carrying an observation on each.
#:
#: The WP-1B, WP-2 and WP-3 lanes are deliberately absent: each mints a
#: per-invocation run root, refuses to reuse one, and selects its run directory
#: by asserting there is exactly one. They never had this defect, and the rule
#: they already follow is the one the lanes below now adopt.
SHARED_ROOT_LANES = {
    "run-endpoint-oracle.sh": "observe-result.json",
    "run-rsop-oracle.sh": "observation.json",
    "run-rsop-user-oracle.sh": "observation.json",
}


def _body(lane: str) -> str:
    """The lane's body with comments stripped.

    The comments legitimately quote the behaviour that was replaced, so a check
    against the raw text would report the explanation as the violation.
    """
    text = (ORACLE_DIR / lane).read_text(encoding="utf-8")
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _prepare(lane: str) -> str:
    for line in _body(lane).splitlines():
        if line.startswith("PREPARE="):
            return line
    raise AssertionError(f"{lane} defines no PREPARE step")


@pytest.mark.parametrize("lane", sorted(SHARED_ROOT_LANES))
def test_staging_keeps_at_least_the_previous_runs_directories(lane: str) -> None:
    """The item's closing condition, stated mechanically."""
    prepare = _prepare(lane)
    body = _body(lane)

    match = re.search(r"^KEEP_RUN_DIRS=(\d+)$", body, re.MULTILINE)
    assert match, f"{lane} does not say how many run directories staging keeps"
    assert int(match.group(1)) >= 1, (
        f"{lane} keeps {match.group(1)} run directories, so the next run still "
        "destroys the evidence of the last failure"
    )

    assert "-Skip $KEEP_RUN_DIRS" in prepare, (
        f"{lane}'s PREPARE does not skip the retained directories before deleting"
    )
    assert "Sort-Object CreationTimeUtc -Descending" in prepare, (
        f"{lane}'s PREPARE must order by creation time before skipping, or which "
        "directories it keeps is unspecified"
    )


@pytest.mark.parametrize("lane", sorted(SHARED_ROOT_LANES))
def test_staging_does_not_delete_every_run_directory(lane: str) -> None:
    """The control: the check above passes on a PREPARE that also wipes.

    A `Select-Object -Skip` clause elsewhere in the same line would satisfy
    every assertion above while an unguarded `Get-ChildItem -Directory |
    Remove-Item -Recurse` still removed everything, so the shape that caused
    WI-037 is refused by name.
    """
    prepare = _prepare(lane)
    unguarded = "-Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force"
    assert unguarded not in prepare, (
        f"{lane}'s PREPARE removes every directory under the output root, which "
        "is the WI-037 defect itself"
    )


@pytest.mark.parametrize("lane", sorted(SHARED_ROOT_LANES))
def test_staging_sweeps_the_scripts_directory(lane: str) -> None:
    """WI-037's second half: staging owns the guest's scripts directory.

    Everything in it is pushed by name straight after and hashed by the
    finalizer, so a file left there by hand is lab debris. Six accumulated
    across one session before anyone swept them up.
    """
    prepare = _prepare(lane)
    assert "Get-ChildItem '$GUEST_SCRIPTS' -File" in prepare, (
        f"{lane}'s PREPARE never touches the scripts directory, so a diagnostic "
        "pushed there by hand outlives the run that pushed it"
    )


@pytest.mark.parametrize("lane,artifact", sorted(SHARED_ROOT_LANES.items()))
def test_the_work_directory_fallback_cannot_select_another_runs_evidence(
    lane: str, artifact: str
) -> None:
    """Both constraints, because either alone is unsound.

    Without the artifact test the fallback can select a preflight or a verify
    directory -- every mode of an observation script mints its own. Without the
    creation-time test it can select the PREVIOUS run's observation, which is
    the hazard preserving the directories introduces.
    """
    body = _body(lane)
    assert "Sort-Object LastWriteTime -Descending | Select-Object -First 1" not in body, (
        f"{lane} still selects the newest output directory, which is now the "
        "previous run's evidence as often as it is this run's"
    )
    assert "OBSERVE_SINCE=" in body, f"{lane} takes no guest-side clock reading"
    assert f"Join-Path \\$_.FullName '{artifact}'" in body, (
        f"{lane}'s fallback does not require {artifact}, so it can select a "
        "directory that carries no observation at all"
    )
    assert "$_.CreationTimeUtc -ge \\$since" in body, (
        f"{lane}'s fallback does not bound the selection to this run"
    )
    assert "if (\\$found.Count -ne 1)" in body, (
        f"{lane}'s fallback resolves an ambiguous match instead of refusing it; "
        "'newest' is a guess and 'the only one' is a fact"
    )


def test_the_endpoint_verify_directory_is_per_invocation() -> None:
    """The fixed-name directory that preserving evidence would have poisoned.

    `verify-result.json` was written to `<out>\\verify` on every run, which was
    unambiguous only because staging deleted the output root first. Preserved,
    it would let a run whose own verification never executed pull the last
    run's -- and the finalizer reads a present, clean verify result as proof the
    endpoint is durably clean, so a lane failure would have certified as a pass.
    """
    observe = (ORACLE_DIR / "run-endpoint-observe.ps1").read_text(encoding="utf-8")
    driver = _body("run-endpoint-oracle.sh")

    assert "Join-Path $OutputDir 'verify'" not in observe, (
        "the verify phase still writes to a fixed directory name"
    )
    assert "$verifyId = \"endpoint-verify-" in observe
    assert "VERIFY_DIR=$verifyDir" in observe, "the phase must report the path it wrote"

    assert "-RemotePath \"$GUEST_OUT\\\\verify\"" not in driver, (
        "the driver still pulls a fixed verify path rather than the one the "
        "phase reported"
    )
    assert "s/^VERIFY_DIR=//p" in driver
