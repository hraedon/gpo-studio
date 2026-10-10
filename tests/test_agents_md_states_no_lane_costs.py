"""AGENTS.md points at the generated cost table instead of restating it.

AGENTS.md once listed per-file lane costs by hand ("`gpp.py` ... five lanes",
"`export.py` ... three"). The generated `docs/plan-033/bound-source-cost.md`
said 6 and 6 by then, and listed `deterministic_zip.py` at 8, which the prose
never mentioned: the copy an agent reads first was the stale one. The table is
generated and guarded by `test_bound_source_cost.py`; this keeps the
duplication from coming back.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS = REPO_ROOT / "AGENTS.md"
TABLE = REPO_ROOT / "docs" / "plan-033" / "bound-source-cost.md"

_NUMBER = (
    r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty[\w-]*)"
)
# "five lanes", "26 lanes", "costs four", "every lane (26)"
_STATED_COST = re.compile(
    rf"\b{_NUMBER}\s+lanes?\b|\bcosts?\s+{_NUMBER}\b|\blanes?\s*\(\s*\d+\s*\)",
    re.IGNORECASE,
)


def _bound_files() -> list[str]:
    return re.findall(r"^\| `([^`]+)` \| \d+ \|", TABLE.read_text(encoding="utf-8"), re.M)


def test_agents_md_states_no_lane_count() -> None:
    text = AGENTS.read_text(encoding="utf-8")
    stated = [match.group(0) for match in _STATED_COST.finditer(text)]
    assert not stated, (
        f"AGENTS.md states lane costs {stated}; point at {TABLE.relative_to(REPO_ROOT)} "
        "instead -- it is generated from the live verdicts and this copy is not"
    )


def test_agents_md_names_no_bound_file_alongside_a_number() -> None:
    """The other shape of the same copy: a bound file and a count on one line."""
    bound = {Path(path).name for path in _bound_files()}
    assert "gpp.py" in bound and "oracle_evidence.py" in bound, "the table parse found nothing"
    for number, line in enumerate(AGENTS.read_text(encoding="utf-8").splitlines(), 1):
        named = [name for name in bound if f"`{name}`" in line]
        # A count, not an identifier's suffix such as WP-3 or WI-048.
        if named and re.search(rf"(?<![\w-]){_NUMBER}\b", line, re.IGNORECASE):
            raise AssertionError(f"AGENTS.md:{number} pairs {named} with a number: {line!r}")


def test_agents_md_points_at_the_generated_table() -> None:
    text = AGENTS.read_text(encoding="utf-8")
    assert "(docs/plan-033/bound-source-cost.md)" in text
    assert "tests/test_bound_source_cost.py" in text
