"""The registries that gate work must agree with the ones that record reality.

This is the mechanical form of a rule AGENTS.md states in prose after the same
failure recurred six times: plan status lines said ``proposed`` while
implemented, the capability matrix said ``failed`` while supported,
``environment-spec.md`` cited an orphaned commit, ``platforms.json`` said
``pending-qualification`` for two qualified hosts, ``work-items.md`` said
``open`` for a closed WI-048, and ``manual-evidence-requests.md`` pointed at a
"module-by-module record" that did not exist. Each time a human found it.

Nothing here checks whether a claim is *true* — no test can. These check that
every claim is *attributable*: that a cell asserting evidence names a request,
and that a named artifact is really in the tree. An unsupported citation is not
a weaker claim than a supported one, it is a different kind of thing, and it is
the kind that has cost this project six rediscoveries.
"""

from __future__ import annotations

import re
from pathlib import Path

_DOCS = Path(__file__).resolve().parent.parent / "docs"
_ROOT = Path(__file__).resolve().parent.parent

_MATRIX = _DOCS / "capability-matrix.md"
_EVIDENCE = _DOCS / "manual-evidence-requests.md"

#: ``capture-backed (R4, R9)`` -> the request ids inside the parentheses.
_CAPTURE_BACKED = re.compile(r"capture-backed\s*\(([^)]*)\)")
_REQUEST_ID = re.compile(r"\bR(\d{1,2})\b")


def _binding_table_rows() -> list[str]:
    """The rows of the 'Where each result lives' table in the evidence doc."""
    text = _EVIDENCE.read_text(encoding="utf-8")
    start = text.index("## Where each result lives")
    end = text.index("### Raw artifacts, and where they are", start)
    return [
        line
        for line in text[start:end].splitlines()
        if line.startswith("| R") and "---" not in line
    ]


def _bound_request_ids() -> set[str]:
    ids: set[str] = set()
    for row in _binding_table_rows():
        first_cell = row.split("|")[1].strip()
        match = _REQUEST_ID.fullmatch(first_cell)
        assert match is not None, f"binding row does not start with a request id: {row!r}"
        ids.add(f"R{int(match.group(1))}")
    return ids


def test_the_evidence_binding_table_is_present_and_covers_every_request() -> None:
    """The table exists and binds all eleven requests, not a prose summary."""
    bound = _bound_request_ids()
    assert bound == {f"R{n}" for n in range(1, 12)}, (
        f"the binding table must cover R1-R11; it covers {sorted(bound)}"
    )


def test_capture_backed_cells_cite_a_real_record() -> None:
    """Every ``capture-backed`` claim names requests the binding table knows.

    ``capability-matrix.md`` documents this test by name as the thing that
    fails the build for an uncited cell. If the assertion below is ever
    loosened, that sentence becomes false and must be removed in the same
    change.
    """
    matrix = _MATRIX.read_text(encoding="utf-8")
    cells = _CAPTURE_BACKED.findall(matrix)
    assert cells, "no capture-backed cells found; has the post-1.0 table changed shape?"

    bound = _bound_request_ids()
    for cell in cells:
        if cell.strip() == "Rn":
            continue  # the legend row's metavariable, not a claim
        cited = {f"R{int(n)}" for n in _REQUEST_ID.findall(cell)}
        assert cited, f"capture-backed cell cites no request id: 'capture-backed ({cell})'"
        unknown = cited - bound
        assert not unknown, (
            f"capture-backed ({cell}) cites {sorted(unknown)}, "
            "which the evidence binding table does not bind"
        )


def test_capture_backed_is_confined_to_the_post_10_table() -> None:
    """The 1.0 contract's verification column stays two-valued.

    A middle value there would weaken a shipped release claim, which is why
    the matrix says the value exists only in the post-1.0 section.
    """
    matrix = _MATRIX.read_text(encoding="utf-8")
    post_10 = matrix.index("## Post-1.0 domain layers — landed but not surfaced")
    assert "capture-backed" not in matrix[:post_10], (
        "capture-backed appears above the post-1.0 section; the 1.0 contract "
        "matrix must keep a two-valued verification column"
    )


def test_no_module_is_marked_windows_verified_yes() -> None:
    """`domain-layer-status.md`'s exit condition is still two-halved.

    A module reaches ``yes`` only via a re-runnable lane *and* a delivery
    surface. If one ever does, this test should be updated deliberately --
    with the lane's evidence manifest cited -- rather than deleted.
    """
    matrix = _MATRIX.read_text(encoding="utf-8")
    post_10 = matrix.index("## Post-1.0 domain layers — landed but not surfaced")
    section = matrix[post_10:]
    rows = [
        line
        for line in section.splitlines()
        if line.startswith("| 0") and line.count("|") >= 5
    ]
    assert rows, "post-1.0 module table not found"
    for row in rows:
        verified = row.split("|")[4].strip()
        assert verified.lower() != "yes", (
            f"{row.split('|')[2].strip()} is marked Windows-verified 'yes'; "
            "that requires a lane certification and a delivery surface"
        )


def test_every_fixture_the_binding_table_names_exists() -> None:
    """No citation dangles.

    The recurring defect this guards is a document naming an artifact that is
    not in the tree -- the reason R3's raw capture, cited by a committed
    record, was found living only in a gitignored directory.
    """
    for row in _binding_table_rows():
        cells = row.split("|")
        request, fixture_cell = cells[1].strip(), cells[3].strip()
        for path in re.findall(r"`(tests/fixtures/[^`]+)`", fixture_cell):
            assert (_ROOT / path).exists(), (
                f"{request} cites fixture {path!r}, which is not in the tree"
            )
