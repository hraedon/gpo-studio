"""The cost table must not drift from the evidence it summarises.

`docs/plan-033/bound-source-cost.md` answers "what does editing this file
cost?" in lanes that must be re-run. It is generated, and a generated document
that is allowed to drift is worse than no document, because it is consulted and
believed -- which is the same argument `test_work_items_register.py` makes for
the open index and `test_domain_layer_status.py` makes for the status lines.

These tests also cover the generator, not only its output. A table that
regenerates identically from a derivation that has quietly stopped finding
anything is the failure mode a comparison alone cannot see, so the controls
below assert that it still finds the things the project already knows are
there.
"""

from __future__ import annotations

import runpy
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = REPO_ROOT / "scripts" / "plan-033" / "report-bound-source-cost.py"
DOC = REPO_ROOT / "docs" / "plan-033" / "bound-source-cost.md"

_SYMBOLS = runpy.run_path(str(GENERATOR))
_live_verdicts = _SYMBOLS["live_verdicts"]
_bound_files = _SYMBOLS["bound_files"]
_render = _SYMBOLS["render"]


def _generated() -> str:
    live = _live_verdicts()
    return str(_render(live, _bound_files(live)))


def test_the_committed_table_is_what_the_evidence_produces() -> None:
    assert DOC.read_text(encoding="utf-8") == _generated(), (
        "docs/plan-033/bound-source-cost.md disagrees with the live verdicts. "
        "Regenerate it: python scripts/plan-033/report-bound-source-cost.py "
        "--write. Do not hand-edit the tables -- a cost table that is adjusted "
        "by hand is one that can be adjusted to say an edit was cheap."
    )


def test_the_derivation_still_finds_the_binding_everything_knows_about() -> None:
    """The control: a comparison alone passes on two identical empty tables.

    `oracle_evidence.py` is bound by every live lane -- WI-062's own register
    entry says so in prose -- so if the derivation stops seeing that, it has
    stopped seeing anything, and the doc would regenerate to a tidy and
    worthless file that matched itself.
    """
    live = _live_verdicts()
    bound = _bound_files(live)
    assert len(bound["src/gpo_studio/oracle_evidence.py"]) == len(live) + 1, (
        "oracle_evidence.py is bound by every live lane plus WP-0; the "
        "derivation now reports something else, so either the binding changed "
        "or the derivation stopped working"
    )


def test_exactly_one_wp0_manifest_counts_as_live() -> None:
    """WP-0 has no `verification.json`, so it needs its own liveness test.

    Every batch leaves a `wp0-evidence/<batch>/wp0/manifest.json` on disk and
    the retired ones stay there, so counting them all would inflate every
    orchestrator file's cost. The generator matches on the commit the live lane
    verdicts bind; this is what notices if that stops selecting exactly one --
    the case where it silently selects two is invisible in the rendered table,
    because the only symptom is a number being one too large.
    """
    live = _live_verdicts()
    bound = _bound_files(live)
    wp0_counts = {
        path: lanes.count("wp0") for path, lanes in bound.items() if "wp0" in lanes
    }
    assert wp0_counts, "no file is bound by WP-0; the manifest selection found nothing"
    assert set(wp0_counts.values()) == {1}, (
        f"WP-0 is counted more than once for {sorted(p for p, n in wp0_counts.items() if n > 1)}; "
        "more than one WP-0 manifest matched a live commit"
    )


def test_a_module_with_no_lane_is_listed_as_unmeasured() -> None:
    """The half of the table that is about coverage rather than about cost.

    `api.py` is bound by nothing, which is what made both Plan 034 WP-3
    surfaces free to build -- and it is also the statement that no lane has
    ever measured it. The document says both things about the same list; this
    keeps the list from silently becoming empty.
    """
    text = DOC.read_text(encoding="utf-8")
    unbound_section = text.split("## Bound by nothing", 1)[1]
    assert "`src/gpo_studio/api.py`" in unbound_section
    assert "`src/gpo_studio/store.py`" in unbound_section
    assert "`src/gpo_studio/rsop.py`" in unbound_section, (
        "rsop.py is certified against a real client by twelve scenarios and "
        "bound by no lane's file set -- if it has moved into the bound tables, "
        "the RSOP lanes started binding it and this expectation is stale"
    )


def test_the_generated_doc_has_no_carriage_returns() -> None:
    """WI-063's mechanism, refused at the generator rather than after the fact.

    `docs/plan-033/**/*.md` is `-text` in `.gitattributes`, so whatever bytes a
    generator writes are the bytes that commit, in both directions. Run
    `--write` on Windows with `Path.write_text` and every line ending becomes
    CRLF, git reports no drift because there is none to report, and no reader
    sees a difference because `read_text` folds both. The generator opens with
    `newline=""` to make the output platform-independent; this is what says so.
    """
    assert b"\r\n" not in DOC.read_bytes()
