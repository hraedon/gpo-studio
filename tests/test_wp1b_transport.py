"""The WP-1B lane's transports, and the finalizer's binding set for each.

``harness_matches_source`` is only meaningful if the finalizer binds exactly the
files the lane deploys. That is a contract between a bash script and a Python
script with no shared type, so it is checked here rather than assumed -- the
same reason the Plan 021 WP-4 doc example is parsed by a test instead of read
by a reviewer.
"""

from __future__ import annotations

import re
import runpy
from pathlib import Path
from typing import cast

REPO_ROOT = Path(__file__).parents[1]
LANE = REPO_ROOT / "scripts" / "windows-oracle" / "run-wp1b-oracle.sh"


def _finalizer_symbols() -> dict[str, object]:
    return runpy.run_path(
        str(REPO_ROOT / "scripts" / "windows-oracle" / "finalize_wp1b_run.py")
    )


def _tables() -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    symbols = _finalizer_symbols()
    return (
        cast(dict[str, dict[str, str]], symbols["TRANSPORT_DEPLOYED_FILES"]),
        cast(dict[str, dict[str, str]], symbols["TRANSPORT_LOCAL_FILES"]),
    )


#: The lane branches on ``$TRANSPORT`` twice, in this order.
INVOKE, RETRIEVE = 0, 1


def _lane_branch(transport: str, half: int | None = None) -> str:
    """The lane's body for one transport.

    ``half`` selects one of the two branches (``INVOKE`` or ``RETRIEVE``);
    omitted, both are joined. Scoping matters for retrieval assertions: a
    harness file is named in the invoke half too (it is pushed and executed
    there), so a check against the joined body cannot tell "pulled back" from
    "merely mentioned" and would pass with the pull deleted.

    Comments are stripped: these assertions are about what the lane *does*, and
    the psdirect branch legitimately explains itself by naming the mechanism it
    replaced.
    """
    text = LANE.read_text(encoding="utf-8")
    blocks: list[tuple[str, str]] = [
        (match.group(1), match.group(2))
        for match in re.finditer(
            r'if \[\[ "\$TRANSPORT" == "ssh" \]\]; then\n(.*?)\nelse\n(.*?)\nfi\n',
            text,
            re.DOTALL,
        )
    ]
    assert len(blocks) == 2, "lane no longer branches on $TRANSPORT twice as expected"
    chosen = blocks if half is None else [blocks[half]]
    body = "\n".join(pair[0 if transport == "ssh" else 1] for pair in chosen)
    return "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("#")
    )


def test_both_transports_are_defined_consistently() -> None:
    deployed, local = _tables()
    assert set(deployed) == set(local) == {"ssh", "psdirect"}


def test_every_bound_source_path_exists() -> None:
    """A binding to a path that does not exist would fail every run, opaquely."""
    deployed, local = _tables()
    for table in (deployed, local):
        for transport, files in table.items():
            for name, source in files.items():
                assert (REPO_ROOT / source).is_file(), f"{transport}:{name} -> {source}"


def test_lane_accepts_exactly_the_transports_the_finalizer_knows() -> None:
    deployed, _ = _tables()
    case_arms = re.search(r"case \"\$TRANSPORT\" in\n(.*?)\nesac", LANE.read_text(), re.DOTALL)
    assert case_arms is not None
    accepted = set(re.findall(r"^\s{4}(\w+)\)", case_arms.group(1), re.MULTILINE))
    assert accepted == set(deployed)


def test_psdirect_binds_no_scheduled_task_launcher() -> None:
    """The point of the transport: no launcher, so no schtasks /RP password.

    If a launcher is ever reintroduced on this path, the credential-exposure
    argument in the lane header stops holding and this must be revisited.
    """
    deployed, _ = _tables()
    assert "remote-run.ps1" not in deployed["psdirect"]
    assert "remote-run-launcher.ps1" not in deployed["psdirect"]
    assert set(deployed["psdirect"]) == {"run-wp1b-writer.ps1"}

    body = _lane_branch("psdirect")
    assert "schtasks" not in body
    assert "remote-run.ps1" not in body
    assert "EncodedCommand" not in body


def test_ssh_transport_still_binds_its_launcher() -> None:
    """The historical lane is unchanged; certified runs stay reproducible."""
    deployed, _ = _tables()
    assert set(deployed["ssh"]) == {
        "run-wp1b-writer.ps1",
        "remote-run.ps1",
        "remote-run-launcher.ps1",
    }
    body = _lane_branch("ssh")
    assert "remote-run.ps1" in body
    assert "EncodedCommand" in body


def test_psdirect_binds_the_transport_script_it_executes() -> None:
    """psdirect.ps1 runs on the controller, so it is a local binding, not a
    deployed one -- but it must be bound somewhere, or the script driving the
    whole run is outside the evidence pack."""
    deployed, local = _tables()
    assert "psdirect.ps1" in local["psdirect"]
    assert "psdirect.ps1" not in deployed["psdirect"]
    assert "psdirect.ps1" not in local["ssh"]


def test_every_deployed_file_is_retrieved_by_the_lane() -> None:
    """A deployed binding the lane never pulls back fails harness_matches_source
    at the end of a long run, rather than at review time.

    Checked against the retrieval branch only -- see ``_lane_branch``."""
    deployed, _ = _tables()
    for transport, files in deployed.items():
        body = _lane_branch(transport, RETRIEVE)
        for name in files:
            assert name in body, f"{transport} lane never retrieves {name}"
