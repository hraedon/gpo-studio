"""The WP-2 finalizer's settings comparisons cannot pass on nothing."""

from __future__ import annotations

import runpy
from collections.abc import Callable
from pathlib import Path
from typing import cast

_SCRIPT = Path(__file__).parents[1] / "scripts" / "windows-oracle" / "finalize_wp2_import_run.py"
_FINALIZER = runpy.run_path(str(_SCRIPT))
_settings_match = cast(
    Callable[[list[tuple[object, ...]], list[tuple[object, ...]]], bool],
    _FINALIZER["_settings_match"],
)

_ROW = ("computer", "HKLM", r"software\policies\x", "flag", "REG_DWORD", 1)


def test_an_empty_expectation_never_matches_an_empty_readback() -> None:
    """Equality of two empty lists would certify a round trip of nothing."""
    assert _settings_match([], []) is False


def test_exact_non_empty_settings_match_and_any_difference_fails() -> None:
    assert _settings_match([_ROW], [_ROW]) is True
    assert _settings_match([_ROW], []) is False
    assert _settings_match([_ROW], [_ROW, _ROW]) is False


def test_both_settings_checks_use_the_non_empty_comparison() -> None:
    source = _SCRIPT.read_text(encoding="utf-8")
    assert (
        'checks["group_policy_readback_matches"] = _settings_match(expected_settings, '
        in source
    )
    assert 'checks["windows_rebackup_matches"] = _settings_match(expected_settings, ' in source
