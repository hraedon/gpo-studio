"""Every lane's transport, and the finalizer's binding set for each.

``harness_matches_source`` is only meaningful if the finalizer binds exactly the
files the lane deploys. That is a contract between a bash script and a Python
script with no shared type, so it is checked here rather than assumed -- the
same reason the Plan 021 WP-4 doc example is parsed by a test instead of read
by a reviewer.

This started as WP-1B's test, when WP-1B was the only lane with a choice of
transport. All four lanes now run over PowerShell Direct and the SSH path is
gone, so the contract applies to all of them and the checks are parametrised
over the lanes rather than written once for the lane that happened to have it.
"""

from __future__ import annotations

import re
import runpy
from pathlib import Path
from typing import cast

import pytest

REPO_ROOT = Path(__file__).parents[1]
ORACLE_DIR = REPO_ROOT / "scripts" / "windows-oracle"

#: lane driver -> finalizer, for every lane whose finalizer declares the tables.
LANES = {
    "run-wp1b-oracle.sh": "finalize_wp1b_run.py",
    "run-wp2-oracle.sh": "finalize_wp2_import_run.py",
    "run-wp3-oracle.sh": "finalize_wp3_run.py",
}


def _tables(finalizer: str) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    symbols = runpy.run_path(str(ORACLE_DIR / finalizer))
    return (
        cast(dict[str, dict[str, str]], symbols["TRANSPORT_DEPLOYED_FILES"]),
        cast(dict[str, dict[str, str]], symbols["TRANSPORT_LOCAL_FILES"]),
    )


def _lane_body(lane: str) -> str:
    """The lane's body with comments stripped.

    These assertions are about what a lane *does*. The comments legitimately
    name the mechanism the transport replaced, so a check against the raw text
    would report the explanation as a violation.
    """
    text = (ORACLE_DIR / lane).read_text(encoding="utf-8")
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


@pytest.mark.parametrize("lane,finalizer", sorted(LANES.items()))
def test_psdirect_is_the_only_transport(lane: str, finalizer: str) -> None:
    deployed, local = _tables(finalizer)
    assert set(deployed) == set(local) == {"psdirect"}
    assert "TRANSPORT=psdirect" in _lane_body(lane)


@pytest.mark.parametrize("lane,finalizer", sorted(LANES.items()))
def test_every_bound_source_path_exists(lane: str, finalizer: str) -> None:
    """A binding to a path that does not exist would fail every run, opaquely."""
    deployed, local = _tables(finalizer)
    for table in (deployed, local):
        for transport, files in table.items():
            for name, source in files.items():
                assert (REPO_ROOT / source).is_file(), f"{transport}:{name} -> {source}"


@pytest.mark.parametrize("lane,finalizer", sorted(LANES.items()))
def test_no_lane_binds_or_launches_a_scheduled_task(lane: str, finalizer: str) -> None:
    """The point of the transport: no launcher, so no schtasks /RP password.

    That argument put a privileged credential somewhere a privileged observer on
    the host could decode it. If a launcher is ever reintroduced, the
    credential-exposure argument in every lane header stops holding and this
    must be revisited rather than deleted.
    """
    deployed, local = _tables(finalizer)
    for table in (deployed, local):
        for files in table.values():
            assert not [name for name in files if "remote-run" in name]

    body = _lane_body(lane)
    assert "schtasks" not in body
    assert "remote-run.ps1" not in body
    assert "EncodedCommand" not in body
    # The two variables that selected a host and a transport are gone with it;
    # a lane silently honouring one again would re-point without a re-freeze.
    assert "GPO_STUDIO_ORACLE_HOST" not in body
    assert "GPO_STUDIO_ORACLE_TRANSPORT" not in body


@pytest.mark.parametrize("lane,finalizer", sorted(LANES.items()))
def test_the_transport_script_is_bound(lane: str, finalizer: str) -> None:
    """psdirect.ps1 runs on the controller, so it is a local binding, not a
    deployed one -- but it must be bound somewhere, or the script driving the
    whole run is outside the evidence pack."""
    deployed, local = _tables(finalizer)
    assert "psdirect.ps1" in local["psdirect"]
    assert "psdirect.ps1" not in deployed["psdirect"]


def _pull_calls(lane: str) -> list[tuple[str, str]]:
    """Every `psdirect -Action pull` in a lane, as (remote-path, local-path).

    Parsed rather than substring-matched. The earlier version of this test
    checked `name in body`, which every deployed file satisfies on its own
    *push* line -- so it passed with the retrieval deleted, and it did worse
    than nothing: a cross-lineage reviewer cleared the binding hazard partly
    because this test claimed to pin it.
    """
    body = _lane_body(lane)
    # Join continuation lines so one invocation is one string.
    joined = re.sub(r"\\\n\s*", " ", body)
    calls: list[tuple[str, str]] = []
    for line in joined.splitlines():
        if "-Action pull" not in line:
            continue
        remote = re.search(r"-RemotePath\s+(\S+)", line)
        local = re.search(r"-LocalPath\s+(\S+)", line)
        assert remote and local, f"{lane}: unparsable pull: {line}"
        calls.append((remote.group(1), local.group(1)))
    return calls


@pytest.mark.parametrize("lane,finalizer", sorted(LANES.items()))
def test_every_deployed_file_is_retrieved_by_the_lane(lane: str, finalizer: str) -> None:
    """A deployed binding the lane never pulls back fails harness_matches_source
    at the end of a long estate run, rather than at review time.

    The finalizer looks for each deployed file by name under `deployed/`, so a
    pull only satisfies the binding if its remote leaf is that filename AND it
    lands in `$LOCAL_DIR/deployed`.
    """
    deployed, _ = _tables(finalizer)
    pulls = _pull_calls(lane)
    for name in deployed["psdirect"]:
        matching = [
            (remote, local)
            for remote, local in pulls
            if remote.rstrip('"').endswith(name) and "deployed" in local
        ]
        assert matching, (
            f"{lane} binds {name} but never pulls it into $LOCAL_DIR/deployed; "
            f"pulls seen: {pulls}"
        )


@pytest.mark.parametrize("lane,finalizer", sorted(LANES.items()))
def test_the_retrieval_check_can_fail(lane: str, finalizer: str) -> None:
    """The previous version of the check above could not fail. Prove this one can.

    Feeding the same matcher a pull set with the deployed file's retrieval
    removed must find nothing. Without this, a future simplification could
    quietly restore the substring match that made the check vacuous.
    """
    deployed, _ = _tables(finalizer)
    pulls = _pull_calls(lane)
    for name in deployed["psdirect"]:
        without = [
            (remote, local)
            for remote, local in pulls
            if not (remote.rstrip('"').endswith(name) and "deployed" in local)
        ]
        assert not [
            (remote, local)
            for remote, local in without
            if remote.rstrip('"').endswith(name) and "deployed" in local
        ]
        assert len(without) < len(pulls), (
            f"{lane}: removing {name}'s retrieval changed nothing, so the "
            "matcher is not selecting on it"
        )


def test_the_launcher_is_gone_from_the_tree() -> None:
    """Deleted, not merely unreferenced: it is what carried the password."""
    assert not (ORACLE_DIR / "remote-run.ps1").exists()


# --- WP-0 ------------------------------------------------------------------
#
# WP-0 is not in LANES: it has no TRANSPORT_* tables, because its binding lives
# in the library (`_HARNESS_INPUT_FILES`) and its file list is built inside a
# bash heredoc in the driver. That is the lane with the strictest binding
# machinery and it was the only one whose two halves agreed purely by hand --
# nothing read the heredoc, so a file added to the driver and executed but not
# added to `sources` would be absent from the record, absent from the table,
# and therefore absent from the "unexpected extra" check too.

WP0_LANE = ORACLE_DIR / "run-windows-oracle.sh"


def _wp0_recorded_paths() -> set[str]:
    """The deployed-relative paths the driver hashes into harness-inputs.json."""
    body = WP0_LANE.read_text(encoding="utf-8")
    block = re.search(r"^sources = \{$(.*?)^\}$", body, re.MULTILINE | re.DOTALL)
    assert block is not None, "run-windows-oracle.sh no longer builds a sources dict"
    return set(re.findall(r'^\s*"([^"]+)":', block.group(1), re.MULTILINE))


def _library_input_paths() -> set[str]:
    from gpo_studio import oracle_evidence

    return {
        relative_path
        for _artifact_id, relative_path, _repo_path in (
            oracle_evidence._HARNESS_INPUT_FILES["psdirect"]
        )
    }


def test_wp0_driver_records_exactly_what_the_library_binds() -> None:
    assert _wp0_recorded_paths() == _library_input_paths()


def test_wp0_guest_files_are_pushed_and_pulled_back() -> None:
    """Anything bound under `scripts/` ran on the guest, so it must round-trip.

    A file the driver pushes and executes but never pulls cannot be compared to
    its committed source, and one it records without pushing is a hash of
    something that never ran.
    """
    body = _lane_body("run-windows-oracle.sh")
    for path in sorted(_library_input_paths()):
        leaf = path.rsplit("/", 1)[-1]
        if path.startswith("scripts/"):
            assert f"-RemotePath \"$GUEST_SCRIPTS\\\\{leaf}\"" in body, (
                f"WP-0 binds {path} but never pushes it to the guest"
            )
            assert leaf in body.split("retrieving run dir")[-1], (
                f"WP-0 binds {path} but never pulls it back"
            )
        else:
            # Controller-side: the source-tree copy is the executed copy, and
            # the driver copies it into the run directory after the run.
            assert f'cp "$SCRIPT_DIR/{leaf}"' in body or f"/{leaf}\"" in body, (
                f"WP-0 binds {path} but never copies it into the run directory"
            )
