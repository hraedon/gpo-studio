"""The coverage gate has to find the modules it grades, on either platform.

`coverage.json` keys files with the platform separator, so on win32 the floors
below are asked for `src\\gpo_studio\\api.py` while the gate spells them with
`/`. Every one of the eleven per-module floors missed, the gate reported them
as *missing coverage entries*, and the `test-windows` job died on a message
that named the symptom rather than the cause. It went unseen for as long as it
did because two things stood in front of it: the job was `continue-on-error`,
and once that came off the pytest step still failed first.

So the case that matters here is the Windows-shaped report. The POSIX one is
the control, and the two negative tests exist so the normalisation cannot buy
its pass by making the gate lenient.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "check_coverage.py"

_spec = importlib.util.spec_from_file_location("check_coverage", _SCRIPT_PATH)
assert _spec is not None
assert _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules["check_coverage"] = _mod
_spec.loader.exec_module(_mod)

FLOORS: dict[str, float] = _mod._FLOORS
TOTAL_FLOOR: float = _mod._TOTAL_FLOOR
main = _mod.main


def _report(separator: str, *, overrides: dict[str, float] | None = None) -> dict[str, Any]:
    """A report clearing every floor, keyed with `separator`."""
    overrides = overrides or {}
    files: dict[str, Any] = {}
    for name, floor in FLOORS.items():
        percent = overrides.get(name, min(100.0, floor + 1.0))
        files[name.replace("/", separator)] = {"summary": {"percent_covered": percent}}
    return {"files": files, "totals": {"percent_covered": TOTAL_FLOOR + 1.0}}


def _run(tmp_path: Path, report: dict[str, Any]) -> int:
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    argv = sys.argv
    sys.argv = ["check_coverage.py", str(path)]
    try:
        return int(main())
    finally:
        sys.argv = argv


def test_a_posix_keyed_report_passes(tmp_path: Path) -> None:
    assert _run(tmp_path, _report("/")) == 0


def test_a_windows_keyed_report_finds_the_same_modules(tmp_path: Path) -> None:
    """The regression. Without the normalisation this is eleven false misses."""
    assert _run(tmp_path, _report("\\")) == 0


@pytest.mark.parametrize("separator", ["/", "\\"])
def test_a_module_below_its_floor_still_fails(tmp_path: Path, separator: str) -> None:
    """Normalising must not buy its pass by making the gate lenient."""
    graded = next(iter(FLOORS))
    report = _report(separator, overrides={graded: FLOORS[graded] - 1.0})
    assert _run(tmp_path, report) == 1


@pytest.mark.parametrize("separator", ["/", "\\"])
def test_a_genuinely_absent_module_is_still_reported_missing(
    tmp_path: Path, separator: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """A module coverage never saw is a real miss, on either spelling."""
    absent = next(iter(FLOORS))
    report = _report(separator)
    del report["files"][absent.replace("/", separator)]

    assert _run(tmp_path, report) == 1
    assert f"missing coverage entry: {absent}" in capsys.readouterr().err
