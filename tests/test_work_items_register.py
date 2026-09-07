"""The work-item register must not contradict itself.

Status drift is this project's most persistent documentation failure: plans said
`proposed` while implemented, the capability matrix said `failed` while
supported, `platforms.json` said `pending` while qualified, and the register
said `open` for three items that recorded their own closure further down the
same entry (review finding 2 on PR #38).

Every previous remedy was a manual sweep, and a manual sweep is what keeps
failing. This is the mechanical version: an item that records a closure cannot
also claim to be open. It does not check that a status is *true* -- nothing can
-- only that the document agrees with itself, which is the specific way this
keeps going wrong.
"""

from __future__ import annotations

import re
from pathlib import Path

REGISTER = Path(__file__).resolve().parents[1] / "docs" / "work-items.md"

#: A FULL closure record, in the register's own vocabulary.
#:
#: CASE-SENSITIVE ON PURPOSE, and it is the whole reason this reads correctly.
#: The register writes a full closure as an all-caps banner (`**FIXED AND
#: CLOSED**`, `**CLOSED**`) and a PARTIAL one in ordinary case -- WI-025 is
#: "open for WP-1B and the endpoint lane. **Closed for WP-6B**", which is an
#: accurate description of a half-done item and must not read as a
#: contradiction. Matching case-insensitively flags it immediately; that was
#: tried, and this comment is what came back.
_CLOSED = re.compile(r"\*\*(FIXED AND CLOSED|CLOSED|FIXED)\b|\*\*Status:\*\*\s*closed")
_OPEN_STATUS = re.compile(r"\*\*Status:\*\*\s*open", re.IGNORECASE)


def _items() -> list[tuple[str, str]]:
    parts = re.split(r"\n## (WI-\d+)", REGISTER.read_text(encoding="utf-8"))
    return [(parts[i], parts[i + 1]) for i in range(1, len(parts), 2)]


def test_the_register_has_items_at_all() -> None:
    """The control: every assertion below is vacuous against an empty parse.

    If the register's heading format changes, `_items()` silently returns
    nothing and the contradiction check passes on zero items -- which is the
    failure mode this whole file exists to prevent, reproduced in the guard
    itself.
    """
    assert len(_items()) >= 5


def test_no_item_is_open_and_closed_at_once() -> None:
    contradictions = [
        number
        for number, body in _items()
        if _CLOSED.search(body) and _OPEN_STATUS.search(body)
    ]
    assert not contradictions, (
        "These items record a closure and still declare Status: open — "
        f"{', '.join(contradictions)}. Update the status line with the closure, "
        "not in a later sweep."
    )


def test_every_item_says_whether_it_is_open() -> None:
    """An item with no status at all is the same drift one step earlier.

    Closed items are kept in the register when how they hid is instructive, so
    absence of a status line is not evidence of either state — it just means a
    reader cannot tell, which is what the register exists to answer.
    """
    silent = [
        number
        for number, body in _items()
        if not _OPEN_STATUS.search(body) and not _CLOSED.search(body)
    ]
    assert not silent, (
        f"These items state neither open nor closed: {', '.join(silent)}. "
        "The register answers 'what is still open?' and cannot do that for an "
        "item that does not say."
    )


#: A line of the "Open right now" index: ``- [WI-042](#anchor) — title``.
_INDEX_ENTRY = re.compile(r"^- \[(WI-\d+)\]\(#", re.MULTILINE)


def test_the_open_index_matches_the_register() -> None:
    """The first screen must answer "what is open?" without reading 1900 lines.

    The register keeps every closed item's body -- deliberately, because how an
    item hid is usually the instructive part -- and grew past the point where a
    reader could tell open from closed by scrolling. An index fixes that only
    while it is accurate, and an index that drifts is this file's own subject
    matter one level up.
    """
    text = REGISTER.read_text(encoding="utf-8")
    listed = set(_INDEX_ENTRY.findall(text))
    actually_open = {number for number, body in _items() if not _CLOSED.search(body)}
    assert listed == actually_open, (
        f"the 'Open right now' index lists {sorted(listed)} but the register's "
        f"own status lines say {sorted(actually_open)} are open"
    )

