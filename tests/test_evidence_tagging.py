"""Every lane finalizer must preserve the commit its certification binds to.

Issue #22's remedy landed only in the WP-0 finalizer, leaving WP-1B, WP-2, and
WP-3 certifying runs whose source commits nothing kept reachable. That is not a
hypothetical: WP-0's and WP-2's own bindings were lost exactly this way
(``docs/evidence-binding-audit-2026-08-03.md``). A per-lane omission is invisible
until someone tries to verify a run months later, so it is pinned here.
The behaviour of ``tag_evidence_commit`` itself -- creation, idempotency,
refusing to move a tag -- is pinned in ``tests/test_finalize_evidence_tag.py``.
What is checked here is that every lane actually calls it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[1]
FINALIZERS = (
    "finalize_oracle_run.py",
    "finalize_wp1b_run.py",
    "finalize_wp2_import_run.py",
    "finalize_wp3_run.py",
)


@pytest.mark.parametrize("name", FINALIZERS)
def test_every_finalizer_tags_passing_runs(name: str) -> None:
    source = (REPO_ROOT / "scripts" / "windows-oracle" / name).read_text(encoding="utf-8")
    assert "tag_evidence_commit(" in source, f"{name} never preserves its source commit"
    assert "--no-tag" in source, f"{name} offers no way to opt out"


@pytest.mark.parametrize("name", FINALIZERS)
def test_no_finalizer_reimplements_tagging(name: str) -> None:
    """One implementation, so a fix reaches every lane at once."""
    source = (REPO_ROOT / "scripts" / "windows-oracle" / name).read_text(encoding="utf-8")
    assert "def _tag_evidence_commit" not in source
