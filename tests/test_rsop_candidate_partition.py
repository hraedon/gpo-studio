"""The candidate builder must not silently drop a GPO from its prediction.

`prediction_document` partitions `result.gpo_results` into `applied_gpos`,
`denied_gpos` and `unevaluable_gpos`. It was three filtered comprehensions, so a
status matching none of them produced a GPO that appears in NO list -- and a GPO
missing from the prediction entirely is the one shape the finalizer cannot
notice: it has nothing to compare, so it reports agreement.

The partition now dispatches exhaustively and ends in `assert_never`. That guard
is a *static* construct, and `mypy` in CI covers `src` only -- this script is not
type-checked by anything. So the invariant is pinned here at runtime instead,
where the test suite enforces it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from gpo_studio.rsop import RsopGpoResult, RsopResult, RsopTarget

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MODULE_PATH = _REPO_ROOT / "scripts" / "plan-033" / "build-rsop-candidate.py"

_spec = importlib.util.spec_from_file_location("build_rsop_candidate", _MODULE_PATH)
assert _spec and _spec.loader
build_rsop_candidate = importlib.util.module_from_spec(_spec)
# Registered before execution because the module defines dataclasses, and
# `dataclasses` resolves annotations through `sys.modules[cls.__module__]`.
sys.modules["build_rsop_candidate"] = build_rsop_candidate
_spec.loader.exec_module(build_rsop_candidate)


def _result_with_status(status: str) -> RsopResult:
    return RsopResult(
        query_id="q",
        mode="planning",
        target=RsopTarget(computer_name="C", domain="x"),
        gpo_results=(
            RsopGpoResult(
                gpo_guid="g1",
                gpo_name="Studio-RSOP-Thing",
                # Deliberately outside the Literal. `Literal` is not enforced at
                # runtime, which is what lets this test exist at all.
                status=cast(Any, status),
            ),
        ),
    )


def _predict(monkeypatch: pytest.MonkeyPatch, status: str) -> dict[str, Any]:
    monkeypatch.setattr(
        build_rsop_candidate, "compute_rsop", lambda _query: _result_with_status(status)
    )
    scenario = build_rsop_candidate.SCENARIOS["lsdou-precedence"]
    return cast(
        dict[str, Any],
        build_rsop_candidate.prediction_document(scenario, "ad.example.test", "S", "C"),
    )


@pytest.mark.parametrize(
    "status,bucket",
    [
        ("applied", "applied_gpos"),
        ("blocked", "denied_gpos"),
        ("unevaluable", "unevaluable_gpos"),
    ],
)
def test_every_known_status_lands_in_exactly_one_bucket(
    monkeypatch: pytest.MonkeyPatch, status: str, bucket: str
) -> None:
    """The control: without it, a builder that emitted nothing would pass below."""
    document = _predict(monkeypatch, status)
    buckets = ("applied_gpos", "denied_gpos", "unevaluable_gpos")
    populated = [name for name in buckets if document[name]]
    assert populated == [bucket]


def test_an_unknown_status_raises_rather_than_vanishing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A GPO in no bucket is invisible to the finalizer, which reads that as agreement.

    Loud failure is the only safe behaviour here: the lane's whole claim is that
    the prediction describes every GPO in the topology.
    """
    with pytest.raises(AssertionError):
        _predict(monkeypatch, "deferred")
