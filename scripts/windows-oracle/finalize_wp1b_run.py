#!/usr/bin/env python3
"""Validate a Plan 033 WP-1B writer-conformance run and write its verdict.

The Windows harness captures evidence; this script renders the verdict, because
semantic comparison needs the Studio import path and the git repository, and
neither exists on the lab host.

For every candidate the checks are:

* the mechanical import gates (``-WhatIf`` enumerates without creating, the real
  import succeeds, cleanup is confirmed by a strict absence re-query);
* registry readback through ``Get-GPRegistryValue`` matches the authored values
  *and types*;
* GPMC's own report declares an extension for every side that was authored, and
  the declared extension matches the family that was written;
* the decisive one: the Windows ``Backup-GPO`` re-export, re-imported through
  the ordinary Studio path, is semantically identical to the authoring model.

A candidate whose family has no recorded GPMC extension marker is reported
``inconclusive`` rather than ``pass``.  An unrecognized round trip must never
be able to certify itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from gpo_studio.backup import BackupError, read_backup
from gpo_studio.model import ValidationError
from gpo_studio.registry_pol import RegistryPolError
from gpo_studio.writer_conformance import (
    compare_preferences,
    compare_summaries,
    summary_from_backup,
    summary_from_gpmc_report,
)

_SETTINGS_NS = "http://www.microsoft.com/GroupPolicy/Settings"
_XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"

#: Namespace/local-name pairs GPMC uses in its XML report for each authored
#: family.  Populated from genuine Windows Server 2025 report captures; a family
#: absent here yields an ``inconclusive`` candidate rather than a pass.
_FAMILY_REPORT_MARKERS: dict[str, tuple[str, ...]] = {
    "registry": ("RegistrySettings",),
    "drives": ("DriveMapSettings",),
    "groups": ("LugsSettings",),
    "local_users": ("LugsSettings",),
    "scheduled_tasks": ("ScheduledTasksSettings",),
    "services": ("ServiceSettings",),
    "mixed": (
        "RegistrySettings",
        "DriveMapSettings",
        "LugsSettings",
        "ScheduledTasksSettings",
        "ServiceSettings",
    ),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _setting_projection(setting: dict[str, Any]) -> tuple[object, ...]:
    value = setting["value"]
    if setting["registry_type"] == "REG_DWORD":
        value = int(value)
    return (
        setting["side"],
        setting["hive"],
        setting["key"].casefold(),
        setting["value_name"].casefold(),
        setting["registry_type"],
        value,
    )


def _report_extensions(report_path: Path) -> list[str]:
    """Return the local names of every extension GPMC declared in its report."""
    root = ET.fromstring(report_path.read_bytes())
    observed: list[str] = []
    for side in ("Computer", "User"):
        side_element = root.find(f"{{{_SETTINGS_NS}}}{side}")
        if side_element is None:
            continue
        for extension in side_element.iter(f"{{{_SETTINGS_NS}}}ExtensionData"):
            for child in extension:
                type_attr = child.get(_XSI_TYPE)
                if type_attr is None:
                    continue
                observed.append(type_attr.split(":", 1)[-1])
    return sorted(set(observed))


def _finalize_candidate(
    case_dir: Path, candidate_dir: Path, result: dict[str, Any]
) -> dict[str, Any]:
    expected = json.loads((candidate_dir / "expected.json").read_text(encoding="utf-8"))
    family = result["family"]
    checks: dict[str, bool] = {
        "whatif_succeeded": result["whatif_succeeded"] is True,
        "whatif_target_absent": result["whatif_target_absent"] is True,
        "import_succeeded": result["import_succeeded"] is True,
        "report_captured": result["report_captured"] is True,
        "rebackup_succeeded": result["rebackup_succeeded"] is True,
        "cleanup_succeeded": result["cleanup_succeeded"] is True,
        "cleanup_state_restored": result["cleanup_state_restored"] is True,
        "backup_id_matches": result["backup_id"] == expected["backup_id"],
        "backup_id_distinct": result["backup_id"] != result["source_gpo_id"],
    }

    # Shape conformance against the captured native corpus.  Deliberately not
    # inferred from the round trip: GPMC echoes back attributes the CSE ignores,
    # so a synthetic shape can survive import, report, and re-export intact.
    shape_findings = expected.get("native_shape_findings", [])
    checks["native_shape_matches_corpus"] = not shape_findings

    expected_settings = sorted(_setting_projection(item) for item in expected["settings"])
    readback_settings = sorted(_setting_projection(item) for item in result["registry_readback"])
    checks["registry_readback_matches"] = readback_settings == expected_settings

    observed_extensions: list[str] = []
    report_path = case_dir / "gpreport-after-import.xml"
    report_error: str | None = None
    if report_path.is_file():
        try:
            observed_extensions = _report_extensions(report_path)
        except (ET.ParseError, OSError) as error:
            report_error = str(error)

    markers = _FAMILY_REPORT_MARKERS.get(family)
    if markers is None:
        checks["gpmc_report_declares_family"] = False
        marker_state = "unmapped"
    else:
        missing = [marker for marker in markers if marker not in observed_extensions]
        checks["gpmc_report_declares_family"] = not missing
        marker_state = "mapped"

    # GPMC's own parse of the imported policy, compared against the authoring
    # model.  Import-GPO copies GPP files to SYSVOL byte-for-byte, so the backup
    # round trip below proves survival only; this is the independent oracle that
    # proves GPMC assigns the same meaning.
    report_differences: list[dict[str, object]] = []
    checks["gpmc_report_semantics_match"] = False
    if report_path.is_file() and report_error is None:
        try:
            found_in_report = compare_preferences(
                expected["summary"], summary_from_gpmc_report(report_path)
            )
            report_differences = [difference.to_dict() for difference in found_in_report]
            checks["gpmc_report_semantics_match"] = not found_in_report
        except (ET.ParseError, OSError, ValidationError, ValueError) as error:
            report_error = str(error)

    differences: list[dict[str, object]] = []
    rebackup_error: str | None = None
    checks["rebackup_has_one_gpo"] = False
    checks["writer_semantics_preserved"] = False
    try:
        rebackup = read_backup(case_dir / "rebackup")
        checks["rebackup_has_one_gpo"] = len(rebackup.gpos) == 1
        content_root = rebackup.gpos[0].content_root
        if content_root is None:
            raise ValueError("re-backup GPO has no content root")
        actual_summary = summary_from_backup(content_root)
        found = compare_summaries(expected["summary"], actual_summary)
        differences = [difference.to_dict() for difference in found]
        checks["writer_semantics_preserved"] = not found
    except (
        BackupError,
        ET.ParseError,
        IndexError,
        KeyError,
        OSError,
        RegistryPolError,
        ValidationError,
        ValueError,
    ) as error:
        rebackup_error = str(error)

    if marker_state == "unmapped" and all(
        value for key, value in checks.items() if key != "gpmc_report_declares_family"
    ):
        state = "inconclusive"
    else:
        state = "pass" if all(checks.values()) else "fail"

    return {
        "candidate_id": result["candidate_id"],
        "family": family,
        "state": state,
        "checks": checks,
        "observed_report_extensions": observed_extensions,
        "expected_report_extensions": list(markers) if markers else [],
        "differences": differences,
        "report_differences": report_differences,
        "native_shape_findings": shape_findings,
        "harness_error": result["error"],
        "report_error": report_error,
        "rebackup_error": rebackup_error,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    candidate_root = args.candidate_root.resolve()
    repo_root = args.repo_root.resolve()

    run_result = json.loads((run_dir / "run-result.json").read_text(encoding="utf-8-sig"))
    candidates = [
        _finalize_candidate(
            run_dir / result["candidate_id"], candidate_root / result["candidate_id"], result
        )
        for result in run_result["candidates"]
    ]

    deployed_map = {
        "run-wp1b-writer.ps1": repo_root / "scripts/windows-oracle/run-wp1b-writer.ps1",
        "remote-run.ps1": repo_root / "scripts/windows-oracle/remote-run.ps1",
        "remote-run-launcher.ps1": repo_root / "scripts/windows-oracle/remote-run.ps1",
    }
    local_map = {
        "run-wp1b-oracle.sh": repo_root / "scripts/windows-oracle/run-wp1b-oracle.sh",
        "build-wp1b-candidates.py": repo_root / "scripts/plan-033/build-wp1b-candidates.py",
    }
    source_hashes: dict[str, str] = {}
    harness_ok = True
    for name, src_path in {**deployed_map, **local_map}.items():
        src_hash = _sha256(src_path)
        source_hashes[name] = src_hash
        evidence_path = run_dir / "deployed" / name if name in deployed_map else run_dir / name
        if not evidence_path.is_file() or _sha256(evidence_path) != src_hash:
            harness_ok = False

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )

    states = {candidate["state"] for candidate in candidates}
    passed = harness_ok and not dirty and states == {"pass"}
    verdict = {
        "schema_version": 1,
        "work_package": "WP-1B",
        "run_id": run_result["run_id"],
        "passed": passed,
        "harness_matches_source": harness_ok,
        "environment": run_result["environment"],
        "candidates": candidates,
        "source": {"commit": commit, "dirty": dirty, "files": source_hashes},
        "artifacts": {
            str(path.relative_to(run_dir)): _sha256(path)
            for path in sorted(run_dir.rglob("*"))
            if path.is_file() and path.name != "verification.json"
        },
    }
    (run_dir / "verification.json").write_text(
        json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    for candidate in candidates:
        marker = {"pass": "PASS", "fail": "FAIL", "inconclusive": "INCONCLUSIVE"}[
            candidate["state"]
        ]
        print(f"{marker:12s} {candidate['candidate_id']}")
        for name, value in sorted(candidate["checks"].items()):
            if not value:
                print(f"             failed check: {name}")
        for difference in candidate["differences"]:
            print(f"             backup: {difference['message']}")
        for difference in candidate["report_differences"]:
            print(f"             gpmc-report: {difference['message']}")
        for finding in candidate["native_shape_findings"]:
            print(f"             native-shape: {finding}")
        for key in ("harness_error", "report_error", "rebackup_error"):
            if candidate[key]:
                print(f"             {key}: {candidate[key]}")
    print(f"\nrun {run_result['run_id']}: passed={passed} (source {commit}, dirty={dirty})")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
