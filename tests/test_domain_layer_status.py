"""Drift guards for the domain-layer status ruling.

Plan status lines going stale is a *repeat* defect in this project — three
recurrences before AGENTS.md encoded the rule. The 2026-07-29 ruling that the
unsurfaced Plans 025-032 layers are unproven drafts is exactly the kind of
statement that rots quietly: it lives in prose, across nine files, and nothing
breaks when one of them stops agreeing.

These tests are cheap and structural. They do not check that the prose is
*right* — only that a plan cannot quietly drop the classification, and that a
layer cannot be called surfaced in one file and unsurfaced in another.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PLANS_DIR = REPO_ROOT / "plans"
RULING_DOC = REPO_ROOT / "docs" / "domain-layer-status.md"

#: Plans executed as domain layers that no delivery surface reaches.
#: Plans 023 and 024 are deliberately absent — they ARE surfaced.
DOMAIN_LAYER_PLANS: tuple[str, ...] = (
    "025",
    "026",
    "027",
    "028",
    "029",
    "030",
    "031",
    "032",
)


def _plan_path(number: str) -> pathlib.Path:
    matches = sorted(PLANS_DIR.glob(f"{number}-*.md"))
    assert len(matches) == 1, f"expected exactly one plan {number}, found {matches}"
    return matches[0]


def test_ruling_document_exists() -> None:
    assert RULING_DOC.is_file(), "the canonical ruling document is missing"


@pytest.mark.parametrize("number", DOMAIN_LAYER_PLANS)
def test_domain_layer_plan_cites_the_ruling(number: str) -> None:
    """Each domain-layer plan must point at the canonical ruling.

    Pointing rather than restating is deliberate: nine copies of a paragraph
    drift, one link does not.
    """
    text = _plan_path(number).read_text(encoding="utf-8")
    assert "domain-layer-status.md" in text, (
        f"plan {number} does not cite docs/domain-layer-status.md; "
        f"the unproven-draft classification was dropped or never applied"
    )


@pytest.mark.parametrize("number", DOMAIN_LAYER_PLANS)
def test_domain_layer_plan_still_declares_itself_unsurfaced(number: str) -> None:
    """A plan may not silently claim a delivery surface it does not have.

    If a layer genuinely gets surfaced, this test should fail — and the fix is
    to move it out of DOMAIN_LAYER_PLANS *and* into the capability matrix
    proper, not to soften the wording here.
    """
    text = _plan_path(number).read_text(encoding="utf-8")
    status = text.split("Scope:")[0]
    assert "not surfaced" in status.lower(), (
        f"plan {number} no longer says it is unsurfaced. If that is true, it "
        f"needs matrix promotion and removal from DOMAIN_LAYER_PLANS; if it is "
        f"not true, the status line is lying."
    )


def test_capability_matrix_carries_the_classification() -> None:
    matrix = (REPO_ROOT / "docs" / "capability-matrix.md").read_text(encoding="utf-8")
    assert "domain-layer-status.md" in matrix
    assert "unproven drafts" in matrix.lower()


def test_agents_guidance_carries_the_classification() -> None:
    """AGENTS.md is what a future session reads; the rule has to survive there."""
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "domain-layer-status.md" in agents
    assert "not proven" in agents.lower()


def test_ruling_names_its_evidence() -> None:
    """The ruling is only as good as the evidence it cites.

    Guards against the document degrading into an unsupported assertion, which
    would make it the same kind of claim it exists to reject.
    """
    text = RULING_DOC.read_text(encoding="utf-8")
    for token in ("security_template.py", "MS-GPSB", "WP-1B", "WP-3"):
        assert token in text, f"the ruling no longer cites {token}"
    assert re.search(r"\+547", text), "the ruling no longer cites the WP-1B correction size"
