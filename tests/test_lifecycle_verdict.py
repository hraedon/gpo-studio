"""The banked lifecycle verdict binds `lifecycle.SCOPE_SURVIVAL` to Windows.

The lane's current certification is the release 1.1.0 batch's run, read from
`docs/plan-033/release110-batch.json` (its first certification,
`lifecycle-20261008093248-2000-c76d10eb3f2849fe` at `3513052`, is retired).
`test_committed_evidence.py` already checks it the way it
checks every verdict: the bound files still hash to what it recorded, the
manifest resolves at its commit, and `passed` follows from its checks.

What those checks cannot see is the claim this verdict exists to make, and the
claim the restore-plan surface repeats in every response: that each cell of
the survival table the product ships is a cell Windows was measured to agree
with. That is pinned here, from the committed bytes rather than from the
verdict's own summary of them. The committed `result.json` is re-graded by the
shipping finalizer against the committed expectation, the expectation is
re-derived from the shipping builder, and the committed `Backup-GPO` tree is
re-read through the shipping bridge. If someone edits `SCOPE_SURVIVAL`, the
live-verdict hash gate fails first; this file says what the edit would have
broken.
"""

from __future__ import annotations

import hashlib
import json
import runpy
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from gpo_studio.lifecycle import SCOPE_DIMENSIONS, SCOPE_SURVIVAL, WINDOWS_OPERATIONS

_ROOT = Path(__file__).parents[1]
#: The current run is whatever the batch manifest records for this lane.
_BATCH_RUN = next(
    run
    for run in json.loads(
        (_ROOT / "docs/plan-033/release110-batch.json").read_text(encoding="utf-8")
    )["runs"]
    if run["name"] == "lifecycle"
)
PACK = _ROOT / "docs/plan-033" / Path(_BATCH_RUN["verdict"]).parent
RUN_ID = _BATCH_RUN["run_id"]
COMMIT = _BATCH_RUN["commit"]

_FINALIZER = runpy.run_path(str(_ROOT / "scripts/windows-oracle/finalize_lifecycle_run.py"))
_grade = cast(
    Callable[[dict[str, Any], dict[str, Any]],
             tuple[dict[str, bool], dict[str, bool], dict[str, Any]]],
    _FINALIZER["grade"],
)
_bridge_check = cast(
    Callable[[Path, dict[str, Any]], tuple[bool, dict[str, Any]]], _FINALIZER["bridge_check"]
)
_expectation = cast(
    Callable[[], dict[str, Any]],
    runpy.run_path(str(_ROOT / "scripts/plan-033/build-lifecycle-candidate.py"))["expectation"],
)


def _json(relative: str) -> dict[str, Any]:
    # The guest writes result.json with a UTF-8 BOM (PowerShell 5.1); the
    # controller-side files have none. utf-8-sig reads both.
    return cast(dict[str, Any], json.loads((PACK / relative).read_text(encoding="utf-8-sig")))


def _table() -> dict[str, dict[str, str]]:
    return {op: dict(SCOPE_SURVIVAL[op]) for op in WINDOWS_OPERATIONS}


def test_the_verdict_is_the_clean_certifying_run() -> None:
    verdict = _json("verification.json")
    assert verdict["run_id"] == RUN_ID
    assert verdict["source"]["commit"] == COMMIT
    assert verdict["source"]["dirty"] is False
    assert verdict["passed"] is True
    assert verdict["harness_valid"] is True
    assert verdict["predictions_agree"] is True
    assert verdict["comparison"]["mismatches"] == []
    assert verdict["environment"]["computer_system_domain_role"] == 3
    assert verdict["environment"]["powershell_version"].startswith("5.1.26100")
    survival_checks = {
        name: ok for name, ok in verdict["checks"].items() if name.startswith("survival.")
    }
    assert len(survival_checks) == len(WINDOWS_OPERATIONS) * len(SCOPE_DIMENSIONS) == 30
    assert all(survival_checks.values())


def test_every_shipped_survival_cell_is_a_measured_cell() -> None:
    """The verdict observed exactly the table `lifecycle.py` ships, cell for cell."""
    comparison = _json("verification.json")["comparison"]
    assert comparison["observed_survival"] == _table()
    assert comparison["predicted_survival"] == _table()


def test_the_committed_result_regrades_clean_under_the_shipping_finalizer() -> None:
    """From the raw guest output, not the verdict's summary of it."""
    lane, claims, comparison = _grade(
        _json("result.json"), _json("controller-candidate/expected.json")
    )
    assert sorted(name for name, ok in lane.items() if not ok) == []
    assert sorted(name for name, ok in claims.items() if not ok) == []
    assert comparison["mismatches"] == []
    assert comparison["observed_survival"] == _table()


def test_the_committed_expectation_is_what_the_shipping_builder_derives() -> None:
    committed = _json("controller-candidate/expected.json")
    assert committed == json.loads(json.dumps(_expectation()))
    assert {op: claims["survival"] for op, claims in committed["operations"].items()} == _table()


def test_the_shipping_bridge_reads_the_committed_backup() -> None:
    """`manifest_from_backup(read_backup(...))` on the real Backup-GPO tree."""
    ok, data = _bridge_check(PACK, _json("result.json"))
    assert ok, data
    assert data["wmi_filter_reference"].startswith('MSFT_SomFilter.ID="{')
    assert data["wmi_reference_names_source_filter"] is True
    assert data["wmi_reference_names_target_filter"] is False


def test_the_pack_holds_exactly_the_hashed_files() -> None:
    verdict = _json("verification.json")
    on_disk = {p.relative_to(PACK).as_posix() for p in PACK.rglob("*") if p.is_file()}
    for relative, digest in verdict["artifacts"].items():
        assert hashlib.sha256((PACK / relative).read_bytes()).hexdigest() == digest, relative
    for relative, digest in verdict["candidate"].items():
        path = PACK / "controller-candidate" / relative
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, relative
    expected = (
        set(verdict["artifacts"])
        | {"controller-candidate/" + name for name in verdict["candidate"]}
        | {"verification.json", "controller.log"}
    )
    assert on_disk == expected
