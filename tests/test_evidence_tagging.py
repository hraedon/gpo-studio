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


VERDICT_FINALIZERS = (
    "finalize_wp1b_run.py",
    "finalize_wp2_import_run.py",
    "finalize_wp3_run.py",
)


@pytest.mark.parametrize("name", VERDICT_FINALIZERS)
def test_the_tag_is_created_before_the_verdict_is_written(name: str) -> None:
    """Bind first, record second.

    The tag is what makes a verdict's ``source.commit`` checkable from a fresh
    clone. A finalizer that writes ``verification.json`` first and then fails to
    tag leaves a durable "passed" file claiming a binding it does not have --
    and re-finalizing a run directory after the tree moved on would rewrite the
    verdict to the new HEAD while the existing tag correctly refuses to move,
    leaving the two pointing at different commits permanently.

    This is a structural check on ordering, which no behavioural test in this
    suite covers: the lanes' real inputs are Windows evidence trees.
    """
    source = (REPO_ROOT / "scripts" / "windows-oracle" / name).read_text(encoding="utf-8")
    tag_at = source.index("tag_evidence_commit(repo_root")
    write_at = source.index('(run_dir / "verification.json").write_text')
    assert tag_at < write_at, f"{name} records a verdict before binding it to a commit"


def test_wp2_will_not_certify_a_dirty_tree() -> None:
    """WP-1B gates on it and WP-3 names it; WP-2 did neither.

    A certification names a commit, so an uncommitted working tree cannot be
    certified -- the commit recorded in ``source`` is not the tree that ran, and
    the tag would bind the wrong one.
    """
    source = (REPO_ROOT / "scripts" / "windows-oracle" / "finalize_wp2_import_run.py").read_text(
        encoding="utf-8"
    )
    assert 'checks["source_tree_clean"] = not dirty' in source
