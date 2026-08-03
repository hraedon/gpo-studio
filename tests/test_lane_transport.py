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


@pytest.mark.parametrize("lane,finalizer", sorted(LANES.items()))
def test_every_deployed_file_is_retrieved_by_the_lane(lane: str, finalizer: str) -> None:
    """A deployed binding the lane never pulls back fails harness_matches_source
    at the end of a long run, rather than at review time."""
    deployed, _ = _tables(finalizer)
    body = _lane_body(lane)
    for name in deployed["psdirect"]:
        assert '-LocalPath "$LOCAL_DIR/deployed"' in body, lane
        assert name in body, f"{lane} never retrieves {name}"


def test_the_launcher_is_gone_from_the_tree() -> None:
    """Deleted, not merely unreferenced: it is what carried the password."""
    assert not (ORACLE_DIR / "remote-run.ps1").exists()
