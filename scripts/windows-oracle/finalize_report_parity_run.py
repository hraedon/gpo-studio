#!/usr/bin/env python3
"""Finalize the Plan 034 report-parity lane.

Grades one question per case: **does Studio's import of this GPMC backup
inventory the same settings as Windows' fresh ``Get-GPOReport -ReportType Xml``
of a GPO the backup was imported into?** Per (side, report family), every
registry key/name/value and every preference item's element, name, uid and
action must agree, in order. The only divergences a case may show are the ones
the controller-built expectation names for it, each a ``KNOWN_DIVERGENCES``
entry (a named exclusion or a work item); anything else fails the lane.

The expectation is the controller-built ``expected.json``, hash-bound below
(WI-025) and recomputed from the candidate archive before it is trusted. The
guest never sees it: the guest is the thing being measured.

Named exclusions (see ``docs/plan-033/report-parity-lane-design.md``):
ADMX-resolved ``<Policy>`` rendering, links / security filtering / WMI (the
lifecycle lane's scope), Scripts (not typed settings; the Scripts metadata lane
measures them), and preference ``Properties`` attributes beyond the action.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from gpo_studio.oracle_evidence import (
    OracleEvidenceError,
    assert_bound_source_bytes,
    lane_environment_violations,
    manifest_bound_source,
    tag_evidence_commit,
)
from gpo_studio.report_parity import (
    Inventory,
    ParityResult,
    ReportParityError,
    classify,
    compare,
    inventory_from_json,
    known_divergence,
    studio_gpo_from_backup,
    studio_inventory,
    windows_inventory,
)

CANDIDATE_ARCHIVE = "report-parity-cases.zip"
CANDIDATE_EXPECTATION = "expected.json"

#: Every file the candidate builder writes. Named rather than globbed, so a
#: consumed artifact the verdict never mentions is refused at the door.
REQUIRED_CANDIDATE_FILES = (CANDIDATE_ARCHIVE, CANDIDATE_EXPECTATION)

DEPLOYED_FILES = {"run-report-parity.ps1": "scripts/windows-oracle/run-report-parity.ps1"}
LOCAL_FILES = {
    "run-report-parity-oracle.sh": "scripts/windows-oracle/run-report-parity-oracle.sh",
    "finalize_report_parity_run.py": "scripts/windows-oracle/finalize_report_parity_run.py",
    "build-report-parity-candidate.py": "scripts/plan-033/build-report-parity-candidate.py",
    "psdirect.ps1": "scripts/windows-oracle/psdirect.ps1",
    "report_parity.py": "src/gpo_studio/report_parity.py",
    "backup.py": "src/gpo_studio/backup.py",
    "backup_inventory.py": "src/gpo_studio/backup_inventory.py",
    "import_export.py": "src/gpo_studio/import_export.py",
    "gpp.py": "src/gpo_studio/gpp.py",
    "gpp_adapters.py": "src/gpo_studio/gpp_adapters.py",
    "registry_pol.py": "src/gpo_studio/registry_pol.py",
    "model.py": "src/gpo_studio/model.py",
    "xml_safety.py": "src/gpo_studio/xml_safety.py",
    "oracle_evidence.py": "src/gpo_studio/oracle_evidence.py",
}

_RESULT_KEYS = frozenset({
    "schema_version", "run_id", "domain", "cases", "authored",
    "cleanup_state_restored", "environment", "error",
})
_CASE_KEYS = frozenset({
    "case_id", "target_name", "backup_id", "source_gpo_id", "owned_gpo_id",
    "import_succeeded", "report_file", "report_sha256", "report_links_to_count",
    "cleanup_succeeded", "absence_confirmed", "error",
})
_AUTHORED_KEYS = frozenset({
    "target_name", "owned_gpo_id", "values_set", "backup_succeeded", "backup_id",
    "backup_dir", "report_file", "report_sha256", "report_links_to_count",
    "cleanup_succeeded", "absence_confirmed", "error",
})


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _member(environment: object) -> bool:
    return (
        isinstance(environment, dict)
        and type(environment.get("computer_system_domain_role")) is int
        and environment["computer_system_domain_role"] == 3
    )


def _guid(value: object) -> str:
    return str(value or "").strip().strip("{}").casefold()


def _run_file(run: Path, relative: object) -> Path:
    """Resolve a guest-reported relative path inside the pulled run only."""
    if not isinstance(relative, str) or not relative:
        raise ValueError("result names no report file")
    pure = PurePosixPath(relative.replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"report path escapes the run: {relative!r}")
    return run.joinpath(*pure.parts)


def _multiset(inventory: Inventory) -> dict[str, list[str]]:
    return {
        f"{f.side}/{f.family}": sorted(i.label() for i in f.items)
        for f in inventory.families if f.items
    }


def grade_case(
    fresh: Inventory, expected: dict[str, Any]
) -> tuple[dict[str, bool], dict[str, Any]]:
    """Grade one imported case's fresh report against the controller's claim.

    Returns the case's checks and a summary for the verdict. Kept separate from
    ``main`` so tests can drive every check without a run directory.
    """
    studio = inventory_from_json(expected["studio_inventory"])
    backup_report = inventory_from_json(expected["backup_report_inventory"])
    result: ParityResult = compare(fresh, studio)
    known, unexplained = classify(result)
    expected_known = sorted(expected["expected_known"])
    checks = {
        "no_unexplained_divergence": not unexplained,
        "known_divergences_exactly_as_expected": sorted(known) == expected_known,
        "fresh_report_matches_capture_time_report": fresh == backup_report,
        "fresh_report_lists_settings": any(f.items for f in fresh.families),
    }
    summary = {
        "families": [
            {
                "side": f.side, "family": f.family, "equal": f.equal,
                "windows_count": f.windows_count, "studio_count": f.studio_count,
            }
            for f in result.families
        ],
        "known": {
            name: {
                "work_item": known_divergence(name).work_item,
                "divergences": [d.describe() for d in items],
            }
            for name, items in known.items()
        },
        "unexplained": [d.describe() for d in unexplained],
    }
    return checks, summary


def grade_authored(
    fresh: Inventory, studio: Inventory, backup_report: Inventory, spec: dict[str, Any]
) -> tuple[dict[str, bool], dict[str, Any]]:
    """The guest-authored case: Windows wrote it, so nothing may be 'known'."""
    expected_windows = inventory_from_json(spec["windows_inventory"])
    result = compare(fresh, studio)
    checks = {
        "authored_report_lists_exactly_the_authored_values": (
            _multiset(fresh) == _multiset(expected_windows)
        ),
        "authored_studio_import_equals_report": result.equal,
        "authored_fresh_report_matches_backup_report": fresh == backup_report,
    }
    summary = {
        "windows": _multiset(fresh),
        "expected": _multiset(expected_windows),
        "divergences": [d.describe() for d in result.divergences],
    }
    return checks, summary


def _expected_reproduces(candidate: Path, expected: dict[str, Any]) -> bool:
    """Recompute Studio's side from the delivered archive, not trust the JSON."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with zipfile.ZipFile(candidate) as archive:
            for name in archive.namelist():
                pure = PurePosixPath(name)
                if pure.is_absolute() or ".." in pure.parts:
                    return False
            archive.extractall(root)
        cases_dir = root / "cases"
        listed = sorted(p.name for p in cases_dir.iterdir() if p.is_dir())
        if listed != sorted(c["case_id"] for c in expected["cases"]):
            return False
        for case in expected["cases"]:
            gpo = studio_gpo_from_backup(cases_dir / case["case_id"])
            if studio_inventory(gpo).to_json() != case["studio_inventory"]:
                return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--no-tag", action="store_true")
    args = parser.parse_args()
    run = args.run_dir.resolve()
    candidate_root = args.candidate_root.resolve()
    repo = args.repo_root.resolve()
    try:
        assert_bound_source_bytes(repo, {**DEPLOYED_FILES, **LOCAL_FILES}.values())
    except OracleEvidenceError as exc:
        print(f"finalize refused: {exc}", file=sys.stderr)
        return 1

    missing = [n for n in REQUIRED_CANDIDATE_FILES if not (candidate_root / n).is_file()]
    if missing:
        print(f"candidate root is missing {', '.join(missing)}", file=sys.stderr)
        return 1
    candidate = candidate_root / CANDIDATE_ARCHIVE

    result = json.loads((run / "result.json").read_text(encoding="utf-8-sig"))
    expected = json.loads((candidate_root / CANDIDATE_EXPECTATION).read_text(encoding="utf-8"))

    cases = result.get("cases") if isinstance(result.get("cases"), list) else []
    authored = result.get("authored") if isinstance(result.get("authored"), dict) else {}
    expected_ids = [c["case_id"] for c in expected["cases"]]
    checks: dict[str, bool] = {
        "result_schema_exact": set(result) == _RESULT_KEYS
        and type(result.get("schema_version")) is int
        and result["schema_version"] == 1
        and all(isinstance(c, dict) and set(c) == _CASE_KEYS for c in cases)
        and set(authored) == _AUTHORED_KEYS,
        "every_expected_case_ran_once": sorted(c.get("case_id") for c in cases)
        == sorted(expected_ids),
        "every_case_imported": bool(cases)
        and all(c.get("import_succeeded") is True for c in cases),
        "every_case_identity_matches_candidate": all(
            any(
                _guid(c.get("backup_id")) == _guid(e["backup_id"])
                and _guid(c.get("source_gpo_id")) == _guid(e["source_gpo_id"])
                for e in expected["cases"] if e["case_id"] == c.get("case_id")
            )
            for c in cases
        ),
        "every_disposable_gpo_unlinked": all(
            type(c.get("report_links_to_count")) is int and c["report_links_to_count"] == 0
            for c in [*cases, authored]
        ),
        "every_gpo_removed_and_absent": all(
            c.get("cleanup_succeeded") is True and c.get("absence_confirmed") is True
            for c in [*cases, authored]
        ),
        "cleanup_state_restored": result.get("cleanup_state_restored") is True,
        "harness_reported_no_error": result.get("error") is None
        and all(c.get("error") is None for c in [*cases, authored]),
        "member_server_host_role": _member(result.get("environment")),
        "raw_command_artifacts_complete": all(
            (run / "commands" / str(c.get("case_id")) / f"{n}.{s}.txt").is_file()
            for c in cases for n in ("import", "report") for s in ("stdout", "stderr")
        )
        and all(
            (run / "commands" / "authored" / f"{n}.{s}.txt").is_file()
            for n in ("set", "backup", "report") for s in ("stdout", "stderr")
        )
        and (run / "builder.stdout.txt").is_file(),
    }
    environment = result.get("environment")
    violations = list(
        lane_environment_violations(environment) if isinstance(environment, dict)
        else ["environment was not recorded"]
    )
    checks["environment_matches_frozen_spec"] = not violations

    comparison: dict[str, Any] = {"cases": {}, "authored": None}
    error: str | None = None
    per_case_ok = True
    delivered_ok = True
    try:
        by_id = {c["case_id"]: c for c in expected["cases"]}
        for case in cases:
            case_id = str(case.get("case_id"))
            report_path = _run_file(run, case.get("report_file"))
            delivered_ok &= report_path.is_file() and _sha(report_path) == case.get("report_sha256")
            case_checks, summary = grade_case(
                windows_inventory(report_path.read_bytes()), by_id[case_id]
            )
            per_case_ok &= all(case_checks.values())
            comparison["cases"][case_id] = {"checks": case_checks, **summary}

        authored_report = _run_file(run, authored.get("report_file"))
        delivered_ok &= authored_report.is_file() and _sha(authored_report) == authored.get(
            "report_sha256"
        )
        backup_dir = _run_file(run, authored.get("backup_dir"))
        authored_gpo = studio_gpo_from_backup(backup_dir)
        if _guid(authored_gpo.guid) != _guid(authored.get("owned_gpo_id")):
            raise ValueError("authored backup is not of the GPO the run owned")
        if authored_gpo.backup_inventory is None:
            raise ValueError("authored backup carries no capture-time report")
        authored_checks, authored_summary = grade_authored(
            windows_inventory(authored_report.read_bytes()),
            studio_inventory(authored_gpo),
            windows_inventory(base64.b64decode(authored_gpo.backup_inventory.report_xml_base64)),
            expected["authored"],
        )
        comparison["authored"] = {"checks": authored_checks, **authored_summary}
        checks.update(authored_checks)
        checks["every_case_studio_matches_fresh_report"] = per_case_ok and bool(cases)
        checks["fresh_reports_delivered_intact"] = delivered_ok
        checks["expected_inventories_reproduce_from_candidate"] = _expected_reproduces(
            candidate, expected
        )
    except (KeyError, OSError, TypeError, ValueError, ReportParityError, zipfile.BadZipFile) as exc:
        error = f"{type(exc).__name__}: {exc}"
        for name in (
            "every_case_studio_matches_fresh_report",
            "fresh_reports_delivered_intact",
            "expected_inventories_reproduce_from_candidate",
            "authored_report_lists_exactly_the_authored_values",
            "authored_studio_import_equals_report",
            "authored_fresh_report_matches_backup_report",
        ):
            checks[name] = False

    # WI-062: controller-side files are bound by (commit, path, sha256).
    bound_local = manifest_bound_source(repo, LOCAL_FILES)
    source_hashes: dict[str, str] = {
        name: entry["sha256"] for name, entry in bound_local.items()
    }
    deployed_ok = True
    for name, relative in DEPLOYED_FILES.items():
        source_hashes[name] = _sha(repo / relative)
        deployed_ok &= (run / "deployed" / name).is_file() and _sha(
            run / "deployed" / name
        ) == source_hashes[name]
    checks["deployed_harness_matches_source"] = deployed_ok

    guest = run / "candidate.zip"
    checks["candidate_delivered_intact"] = (
        candidate.is_file() and guest.is_file() and _sha(candidate) == _sha(guest)
    )

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout
    )
    checks["source_tree_clean"] = not dirty

    verdict = {
        "schema_version": 2,
        "run_id": result.get("run_id"),
        "transport": "psdirect",
        "passed": all(checks.values()),
        "checks": checks,
        "comparison": comparison,
        "comparison_error": error,
        "harness_error": result.get("error"),
        "exclusions": expected.get("exclusions"),
        "excluded_cases": expected.get("excluded"),
        "candidate": {
            path.relative_to(candidate_root).as_posix(): _sha(path)
            for path in sorted(candidate_root.rglob("*"))
            if path.is_file()
        },
        "candidate_delivery": {
            "controller_sha256": _sha(candidate),
            "guest_sha256": _sha(guest) if guest.is_file() else None,
        },
        "environment": environment,
        "environment_violations": violations,
        "source": {
            "commit": commit,
            "dirty": dirty,
            "files": source_hashes,
            "paths": {**DEPLOYED_FILES, **LOCAL_FILES},
            "banked_copies": sorted(DEPLOYED_FILES),
        },
        "artifacts": {
            p.relative_to(run).as_posix(): _sha(p)
            for p in sorted(run.rglob("*"))
            if p.is_file() and p.name != "verification.json"
        },
    }

    tag_outcome = None
    if verdict["passed"] and not args.no_tag:
        try:
            tag_outcome = tag_evidence_commit(repo, str(result["run_id"]), commit)
        except OracleEvidenceError as exc:
            print(f"evidence tag failed: {exc}", file=sys.stderr)
            return 1
    (run / "verification.json").write_text(
        json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(verdict, indent=2, sort_keys=True))
    if tag_outcome is not None:
        print(f"EVIDENCE_TAG={tag_outcome}")
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
