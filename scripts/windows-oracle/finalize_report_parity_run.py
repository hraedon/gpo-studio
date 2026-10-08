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
(WI-025). It is not trusted on its hash: the bound builder rebuilds the whole
candidate and both files must match byte for byte, so every expectation (case
list, Studio and capture-time inventories, known divergences, authored spec)
comes from bound source. Every fresh report must name the GPO the run owned,
and a non-zero guest exit status can never pass or tag. The guest never sees
the expectation: the guest is the thing being measured.

Cases that once passed only on a pinned work-item divergence -- WI-072 (Power
Options' power plan dropped on write) and WI-073 (scheduled and immediate
tasks written grouped) -- are fixed, and the builder pins them
(``MUST_AGREE_CASE_IDS``): ``fixed_work_item_cases_agree_exactly`` requires each
to be in the run and equal to Windows' fresh report with no divergence at all.

Named exclusions (see ``docs/plan-033/report-parity-lane-design.md``):
ADMX-resolved ``<Policy>`` rendering, links / security filtering / WMI (the
lifecycle lane's scope), Scripts (not typed settings; the Scripts metadata lane
measures them), and preference ``Properties`` attributes beyond the action.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import re
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
    ReportIdentity,
    ReportParityError,
    classify,
    compare,
    inventory_from_json,
    known_divergence,
    report_identity,
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
    # Batch 2: the archive writer and product modules this lane's candidate
    # bytes flow through, so editing them stales the verdict (review P1).
    "deterministic_zip.py": "src/gpo_studio/deterministic_zip.py",
}

_RESULT_KEYS = frozenset({
    "schema_version", "run_id", "domain", "cases", "authored",
    "cleanup_state_restored", "environment", "error",
})
_CASE_KEYS = frozenset({
    "case_id", "case_dir", "target_name", "backup_id", "source_gpo_id", "owned_gpo_id",
    "import_succeeded", "report_file", "report_sha256", "report_links_to_count",
    "cleanup_succeeded", "absence_confirmed", "error",
})
_AUTHORED_KEYS = frozenset({
    "target_name", "owned_gpo_id", "values_set", "backup_succeeded", "backup_id",
    "backup_dir", "report_file", "report_sha256", "report_links_to_count",
    "cleanup_succeeded", "absence_confirmed", "error",
})


_GUID_RE = re.compile(
    r"\{?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}?"
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")

#: Checks graded from the reports and the rebuilt candidate. If grading
#: raises, every one of them is recorded as failed.
_GRADED_CHECKS = (
    "candidate_rebuilds_from_bound_builder",
    "every_case_studio_matches_fresh_report",
    "fixed_work_item_cases_agree_exactly",
    "fresh_reports_delivered_intact",
    "every_fresh_report_identifies_its_owned_gpo",
    "authored_steps_succeeded",
    "authored_report_lists_exactly_the_authored_values",
    "authored_studio_import_equals_report",
    "authored_fresh_report_matches_backup_report",
)

#: The complete check set. A verdict whose checks differ from this -- one
#: dropped by a refactor, say -- does not pass, however the rest read.
REQUIRED_CHECKS = frozenset({
    "guest_exited_zero",
    "result_schema_exact",
    "candidate_carries_the_required_corpus",
    "every_expected_case_ran_once",
    "every_case_imported",
    "every_case_identity_matches_candidate",
    "every_owned_gpo_is_this_runs",
    "every_disposable_gpo_unlinked",
    "every_gpo_removed_and_absent",
    "cleanup_state_restored",
    "harness_reported_no_error",
    "member_server_host_role",
    "raw_command_artifacts_complete",
    "environment_matches_frozen_spec",
    *_GRADED_CHECKS,
    "deployed_harness_matches_source",
    "candidate_delivered_intact",
    "source_tree_clean",
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


def agrees_exactly(fresh: Inventory, expected: dict[str, Any]) -> bool:
    """A fixed work item's case: no divergence of any kind, known or otherwise.

    The builder's ``MUST_AGREE_CASE_IDS`` are the cases that passed before
    1.1.0 only on a pinned work-item divergence (WI-072 on Power Options, WI-073
    on both scheduled-task captures). Both are fixed, so for these cases
    Studio's inventory and Windows' fresh report must be equal outright, and the
    expectation must name nothing for them. This holds independently of
    ``KNOWN_DIVERGENCES``: re-adding an allowance cannot let a regression pass.
    """
    studio = inventory_from_json(expected["studio_inventory"])
    return expected.get("expected_known") == [] and compare(fresh, studio).equal


def grade_authored(
    fresh: Inventory, studio: Inventory, backup_report: Inventory, spec: dict[str, Any],
    spec_values: int,
) -> tuple[dict[str, bool], dict[str, Any]]:
    """The guest-authored case: Windows wrote it, so nothing may be 'known'.

    ``spec_values`` is the number of values the bound builder authors; an
    expectation, report or import with any other count (an empty one included)
    fails rather than comparing equal to another empty inventory.
    """
    expected_windows = inventory_from_json(spec["windows_inventory"])
    result = compare(fresh, studio)

    def count(inventory: Inventory) -> int:
        return sum(len(f.items) for f in inventory.families)

    checks = {
        "authored_report_lists_exactly_the_authored_values": (
            spec_values > 0
            and count(expected_windows) == spec_values
            and count(fresh) == spec_values
            and _multiset(fresh) == _multiset(expected_windows)
        ),
        "authored_studio_import_equals_report": count(studio) == spec_values and result.equal,
        "authored_fresh_report_matches_backup_report": (
            count(backup_report) == spec_values and fresh == backup_report
        ),
    }
    summary = {
        "windows": _multiset(fresh),
        "expected": _multiset(expected_windows),
        "divergences": [d.describe() for d in result.divergences],
    }
    return checks, summary


def identifies(report: ReportIdentity, record: dict[str, Any], domain: object) -> bool:
    """Does this fresh report describe the GPO this record says the run owned?

    GUID and display name exactly (GUID case and braces aside); the domain
    case-insensitively, because it is a DNS name and the guest's
    ``USERDNSDOMAIN`` is upper case where the report's is not.
    """
    owned = record.get("owned_gpo_id")
    return (
        _is_guid(owned)
        and report.guid == _guid(owned)
        and isinstance(record.get("target_name"), str)
        and report.name == record["target_name"]
        and isinstance(domain, str)
        and bool(domain)
        and report.domain.casefold() == domain.casefold()
    )


def _is_guid(value: object) -> bool:
    return isinstance(value, str) and _GUID_RE.fullmatch(value.strip()) is not None


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _builder(repo: Path) -> Any:
    """The bound candidate builder, loaded from the repository under test."""
    path = repo / LOCAL_FILES["build-report-parity-candidate.py"]
    spec = importlib.util.spec_from_file_location("report_parity_bound_builder", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def candidate_rebuilds(builder: Any, candidate_root: Path, repo: Path) -> bool:
    """Rebuild the whole candidate with the bound builder; demand identical bytes.

    Every expectation the grading reads -- Studio's inventories, the
    capture-time Windows inventories, the expected known divergences, the
    authored spec and the case list -- then comes from bound source over the
    committed fixtures, not from a JSON file whose only credential is its hash.
    """
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        builder.build(out, repo)
        return all(
            (out / name).read_bytes() == (candidate_root / name).read_bytes()
            for name in REQUIRED_CANDIDATE_FILES
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--guest-status", type=int, default=None,
        help="exit status of the guest script; absent or non-zero can never pass or tag",
    )
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

    loaded = json.loads((run / "result.json").read_text(encoding="utf-8-sig"))
    result: dict[str, Any] = loaded if isinstance(loaded, dict) else {}
    expected = json.loads((candidate_root / CANDIDATE_EXPECTATION).read_text(encoding="utf-8"))
    builder = _builder(repo)

    raw_cases = result.get("cases")
    cases: list[dict[str, Any]] = [
        c for c in (raw_cases if isinstance(raw_cases, list) else []) if isinstance(c, dict)
    ]
    authored: dict[str, Any] = (
        result["authored"] if isinstance(result.get("authored"), dict) else {}
    )
    owned_records = [*cases, authored]
    run_id = result.get("run_id")
    prefix = f"zz-studio-rp-{run_id}-" if isinstance(run_id, str) and run_id else None
    expected_cases = [
        c for c in (expected.get("cases") if isinstance(expected.get("cases"), list) else [])
        if isinstance(c, dict)
    ]
    expected_ids = [c.get("case_id") for c in expected_cases]
    required_ids = list(builder.REQUIRED_CASE_IDS)
    dir_of = {c.get("case_id"): c.get("dir") for c in expected_cases}

    checks: dict[str, bool] = {
        "guest_exited_zero": args.guest_status == 0,
        "result_schema_exact": set(result) == _RESULT_KEYS
        and type(result.get("schema_version")) is int
        and result["schema_version"] == 1
        and isinstance(raw_cases, list)
        and len(cases) == len(raw_cases)
        and all(set(c) == _CASE_KEYS for c in cases)
        and set(authored) == _AUTHORED_KEYS,
        "candidate_carries_the_required_corpus": bool(required_ids)
        and expected_ids == required_ids
        and expected.get("excluded") == [],
        "every_expected_case_ran_once": bool(cases)
        and sorted(str(c.get("case_id")) for c in cases) == sorted(required_ids),
        "every_case_imported": bool(cases)
        and all(c.get("import_succeeded") is True for c in cases),
        "every_case_identity_matches_candidate": bool(cases) and all(
            any(
                _guid(c.get("backup_id")) == _guid(e.get("backup_id"))
                and _guid(c.get("source_gpo_id")) == _guid(e.get("source_gpo_id"))
                and isinstance(e.get("dir"), str)
                and c.get("case_dir") == e["dir"]
                for e in expected_cases if e.get("case_id") == c.get("case_id")
            )
            for c in cases
        ),
        "every_owned_gpo_is_this_runs": prefix is not None and bool(cases) and all(
            _is_guid(c.get("owned_gpo_id"))
            and isinstance(c.get("target_name"), str)
            and c["target_name"].startswith(prefix)
            for c in owned_records
        )
        and len({_guid(c.get("owned_gpo_id")) for c in owned_records}) == len(owned_records),
        "every_disposable_gpo_unlinked": all(
            type(c.get("report_links_to_count")) is int and c["report_links_to_count"] == 0
            for c in owned_records
        ),
        "every_gpo_removed_and_absent": all(
            c.get("cleanup_succeeded") is True and c.get("absence_confirmed") is True
            for c in owned_records
        ),
        "cleanup_state_restored": result.get("cleanup_state_restored") is True,
        "harness_reported_no_error": "error" in result and result["error"] is None
        and all("error" in c and c["error"] is None for c in owned_records),
        "member_server_host_role": _member(result.get("environment")),
        "raw_command_artifacts_complete": bool(cases) and all(
            # Keyed by the candidate's short directory (a MAX_PATH budget),
            # taken from the expectation, never from the guest's own claim.
            (run / "commands" / str(dir_of.get(c.get("case_id"))) / f"{n}.{s}.txt").is_file()
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
    per_case_ok = bool(cases)
    must_agree = frozenset(builder.MUST_AGREE_CASE_IDS)
    # Every pinned case must be in the run, so dropping one cannot pass.
    exact_ok = bool(must_agree) and must_agree <= {str(c.get("case_id")) for c in cases}
    delivered_ok = bool(cases)
    identity_ok = bool(cases)
    domain = result.get("domain")
    try:
        rebuilds = candidate_rebuilds(builder, candidate_root, repo)
        by_id = {c["case_id"]: c for c in expected_cases}
        for case in cases:
            case_id = str(case.get("case_id"))
            report_path = _run_file(run, case.get("report_file"))
            report_bytes = report_path.read_bytes()
            delivered_ok &= _is_sha256(case.get("report_sha256")) and (
                _sha(report_path) == case.get("report_sha256")
            )
            identity_ok &= identifies(report_identity(report_bytes), case, domain)
            fresh = windows_inventory(report_bytes)
            case_checks, summary = grade_case(fresh, by_id[case_id])
            per_case_ok &= all(case_checks.values())
            if case_id in must_agree:
                exact_ok &= agrees_exactly(fresh, by_id[case_id])
            comparison["cases"][case_id] = {"checks": case_checks, **summary}

        authored_report = _run_file(run, authored.get("report_file"))
        authored_bytes = authored_report.read_bytes()
        delivered_ok &= _is_sha256(authored.get("report_sha256")) and (
            _sha(authored_report) == authored.get("report_sha256")
        )
        identity_ok &= identifies(report_identity(authored_bytes), authored, domain)
        backup_dir = _run_file(run, authored.get("backup_dir"))
        readiness = builder.import_readiness(backup_dir)
        if isinstance(readiness, str):
            raise ValueError(f"authored backup is not a native backup: {readiness}")
        backup_id, backup_gpo = readiness
        authored_steps = (
            authored.get("values_set") is True
            and authored.get("backup_succeeded") is True
            and _is_guid(authored.get("backup_id"))
            and _guid(backup_id) == _guid(authored.get("backup_id"))
            and _guid(backup_gpo) == _guid(authored.get("owned_gpo_id"))
        )
        authored_gpo = studio_gpo_from_backup(backup_dir)
        if authored_gpo.backup_inventory is None:
            raise ValueError("authored backup carries no capture-time report")
        authored_checks, authored_summary = grade_authored(
            windows_inventory(authored_bytes),
            studio_inventory(authored_gpo),
            windows_inventory(base64.b64decode(authored_gpo.backup_inventory.report_xml_base64)),
            expected["authored"],
            len(builder.AUTHORED_VALUES),
        )
        comparison["authored"] = {"checks": authored_checks, **authored_summary}
        checks.update(authored_checks)
        checks["candidate_rebuilds_from_bound_builder"] = rebuilds
        checks["authored_steps_succeeded"] = authored_steps
        checks["every_case_studio_matches_fresh_report"] = per_case_ok
        checks["fixed_work_item_cases_agree_exactly"] = exact_ok
        checks["fresh_reports_delivered_intact"] = delivered_ok
        checks["every_fresh_report_identifies_its_owned_gpo"] = identity_ok
    except (
        AttributeError, KeyError, OSError, TypeError, ValueError, ReportParityError,
        zipfile.BadZipFile,
    ) as exc:
        error = f"{type(exc).__name__}: {exc}"
        for name in _GRADED_CHECKS:
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

    # A check that silently stopped being computed must not read as a pass.
    complete = set(checks) == REQUIRED_CHECKS
    verdict = {
        "schema_version": 2,
        "run_id": run_id,
        "transport": "psdirect",
        "passed": complete and all(checks.values()),
        "checks": checks,
        "checks_complete": complete,
        "guest_status": args.guest_status,
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
            tag_outcome = tag_evidence_commit(repo, str(run_id), commit)
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
