"""Enforce branch-coverage floors for release-critical subsystems."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_FLOORS = {
    "src/gpo_studio/api.py": 90.0,
    "src/gpo_studio/backup.py": 88.0,
    "src/gpo_studio/canonical.py": 90.0,
    "src/gpo_studio/export.py": 92.0,
    "src/gpo_studio/gpp.py": 88.0,
    "src/gpo_studio/ps_plan_validator.py": 94.0,
    "src/gpo_studio/registry_pol.py": 88.0,
    "src/gpo_studio/store.py": 85.0,
    "src/gpo_studio/validation.py": 82.0,
    "src/gpo_studio/workspace_ops.py": 80.0,
}
_TOTAL_FLOOR = 84.0


def _percent(entry: dict[str, Any]) -> float:
    summary = entry.get("summary", entry)
    return float(summary["percent_covered"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    args = parser.parse_args()

    data = json.loads(args.report.read_text(encoding="utf-8"))
    failures: list[str] = []
    files: dict[str, dict[str, Any]] = data["files"]
    for name, floor in _FLOORS.items():
        if name not in files:
            failures.append(f"missing coverage entry: {name}")
            continue
        actual = _percent(files[name])
        print(f"{name}: {actual:.2f}% (floor {floor:.2f}%)")
        if actual < floor:
            failures.append(f"{name}: {actual:.2f}% < {floor:.2f}%")

    total = _percent(data["totals"])
    print(f"total: {total:.2f}% (floor {_TOTAL_FLOOR:.2f}%)")
    if total < _TOTAL_FLOOR:
        failures.append(f"total: {total:.2f}% < {_TOTAL_FLOOR:.2f}%")

    if failures:
        print("Coverage gate failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
