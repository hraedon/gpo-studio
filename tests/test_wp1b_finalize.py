from __future__ import annotations

import json
import runpy
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

_PACK = Path(__file__).parents[1] / "docs/plan-033/wp1b-evidence/wi062-20260910/wp1b"


def _finalizer_symbols() -> dict[str, object]:
    script = (
        Path(__file__).parents[1]
        / "scripts"
        / "windows-oracle"
        / "finalize_wp1b_run.py"
    )
    return runpy.run_path(str(script))


def test_services_report_marker_is_capture_backed_and_required() -> None:
    symbols = _finalizer_symbols()
    markers = cast(dict[str, tuple[str, ...]], symbols["_FAMILY_REPORT_MARKERS"])
    report_extensions = cast(
        Callable[[Path], list[str]], symbols["_report_extensions"]
    )
    report = (
        Path(__file__).parent
        / "fixtures"
        / "native-gpp-gpmc"
        / "WI01A-Services-GPMC"
        / "gpreport-verify.xml"
    )

    assert markers["services"] == ("Services:ServiceSettings",)
    assert "Services:ServiceSettings" in markers["mixed"]
    assert "Services:ServiceSettings" in report_extensions(report)


_FIXTURES = Path(__file__).parent / "fixtures" / "native-gpp-gpmc"
_PLAN034 = Path(__file__).parents[1] / "docs/plan-033/wp1b-evidence/plan034-20261008/wp1b"


def test_gpp_registry_report_marker_is_capture_backed_and_namespace_qualified() -> None:
    """GPP Registry and Registry.pol share the local name ``RegistrySettings``.

    The native GPP Registry capture declares it under ``.../Windows/Registry``;
    the banked registry-only WP-1B report declares it under ``.../Registry``.
    Each candidate family must be satisfiable only by its own extension, so the
    marker is namespace-qualified -- by local name the mixed candidate's
    Registry.pol would satisfy the GPP Registry marker on its own.
    """
    symbols = _finalizer_symbols()
    markers = cast(dict[str, tuple[str, ...]], symbols["_FAMILY_REPORT_MARKERS"])
    report_extensions = cast(Callable[[Path], list[str]], symbols["_report_extensions"])

    gpp = report_extensions(
        _FIXTURES.parent / "native-gpp-registry-gpmc" / "WI01A-Registry-GPMC"
        / "gpreport-verify.xml"
    )
    policy = report_extensions(_PLAN034 / "registry-both" / "gpreport-after-import.xml")

    assert markers["gpp_registry"] == ("Windows/Registry:RegistrySettings",)
    assert gpp == ["Windows/Registry:RegistrySettings"]
    assert policy == ["Registry:RegistrySettings"]
    assert not set(markers["gpp_registry"]) & set(policy)
    assert set(markers["gpp_registry"]) <= set(markers["mixed"])


def test_every_banked_report_still_satisfies_its_family_markers() -> None:
    """Qualifying the markers must not break the families already certified."""
    symbols = _finalizer_symbols()
    markers = cast(dict[str, tuple[str, ...]], symbols["_FAMILY_REPORT_MARKERS"])
    report_extensions = cast(Callable[[Path], list[str]], symbols["_report_extensions"])
    index = json.loads((_PLAN034 / "controller-candidate/candidates.json").read_text())
    for entry in index["candidates"]:
        observed = report_extensions(_PLAN034 / entry["id"] / "gpreport-after-import.xml")
        wanted = set(markers[entry["family"]])
        if entry["family"] == "mixed":
            # The banked mixed run predates GPP Registry in the candidate.
            wanted.discard("Windows/Registry:RegistrySettings")
        assert wanted <= set(observed), entry["id"]


# ---------------------------------------------------------------------------
# The run must report the builder's whole candidate set (sweep, 2026-10-08)
# ---------------------------------------------------------------------------


def _index() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((_PACK / "controller-candidate/candidates.json").read_text(encoding="utf-8")),
    )


def _run_candidates() -> list[dict[str, Any]]:
    run = json.loads((_PACK / "run-result.json").read_text(encoding="utf-8-sig"))
    return cast(list[dict[str, Any]], run["candidates"])


def _set_problems(index: object, run: object) -> list[str]:
    symbols = _finalizer_symbols()
    return cast(Callable[[object, object], list[str]], symbols["_candidate_set_problems"])(
        index, run
    )


def test_the_banked_run_reports_the_whole_candidate_set() -> None:
    """The control: the last real run named all seven candidates, once each."""
    assert _set_problems(_index(), _run_candidates()) == []


def test_a_run_reporting_a_subset_of_candidates_fails() -> None:
    """One passing candidate out of seven used to certify the lane."""
    run = _run_candidates()[:1]
    problems = _set_problems(_index(), run)
    assert len(problems) == len(_index()["candidates"]) - 1
    assert all("never reported" in problem for problem in problems)


def test_a_run_reporting_no_candidates_fails() -> None:
    assert _set_problems(_index(), []) != []


def test_a_duplicated_or_foreign_candidate_fails() -> None:
    run = _run_candidates()
    assert any("twice" in p for p in _set_problems(_index(), [*run, run[0]]))
    foreign = [*run, dict(run[0], candidate_id="unknown-candidate")]
    assert any("never produced" in p for p in _set_problems(_index(), foreign))


def test_a_guest_reported_family_must_match_the_builder() -> None:
    """The family selects the report markers; the guest must not choose them."""
    run = [dict(c) for c in _run_candidates()]
    run[-1]["family"] = "registry"
    assert any("as family 'registry'" in p for p in _set_problems(_index(), run))


@pytest.mark.parametrize("index", [None, {}, {"candidates": []}, {"candidates": [{"id": "x"}]}])
def test_a_malformed_index_fails(index: object) -> None:
    assert _set_problems(index, _run_candidates()) != []


def test_a_candidate_set_problem_blocks_the_pass() -> None:
    symbols = _finalizer_symbols()
    passed = cast(Callable[..., bool], symbols["_passed"])
    clean: dict[str, Any] = {
        "harness_ok": True,
        "dirty": False,
        "environment_violations": [],
        "candidate_set_problems": [],
        "candidates": [{"state": "pass"}],
    }
    assert passed(**clean) is True
    assert passed(**{**clean, "candidate_set_problems": ["missing"]}) is False
    assert passed(**{**clean, "candidates": []}) is False
