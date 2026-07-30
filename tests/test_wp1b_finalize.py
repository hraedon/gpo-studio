from __future__ import annotations

import runpy
from collections.abc import Callable
from pathlib import Path
from typing import cast


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

    assert markers["services"] == ("ServiceSettings",)
    assert "ServiceSettings" in markers["mixed"]
    assert "ServiceSettings" in report_extensions(report)
