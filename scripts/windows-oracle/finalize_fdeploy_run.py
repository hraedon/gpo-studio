#!/usr/bin/env python3
"""Finalize the Plan 034 fdeploy lane.

Grades one question per case: **does Studio's reader say about an
``fdeploy1.ini`` what Windows says about it, once Windows has imported it,
reported it and backed it up?** Concretely, for each case:

* Windows keeps the candidate's bytes: the files ``Import-GPO`` put in SYSVOL
  and the files ``Backup-GPO`` re-exported are byte-identical to the
  candidate's ``fdeploy1.ini`` and ``fdeploy.ini``. The 2026-10-08 probe's
  native backup already showed ``Backup-GPO`` copying these bytes verbatim, so
  identity is demanded, not parse equality; a rewrite fails the lane, and
  every difference is recorded (``byte_differences``) for whoever relaxes it.
* Studio's ``read_backup`` over Windows' own ``Backup-GPO`` output yields a
  ``GPO.fdeploy`` whose ``(folder, principal, FullPath, Flags)`` equal the
  R3 provenance values (rebuilt from bound source), with no structural finding.
* Windows' fresh ``Get-GPOReport`` renders exactly those redirections --
  ``Folder/Id``, ``SecurityGroup/SID``, ``DestinationPath`` -- and Studio's
  reading of Windows' backup agrees with that rendering row for row.
* The backup's own ``gpreport.xml`` renders the same as the fresh report.

Windows' option elements (``MoveContents``, ``FollowParent`` and the rest) are
**recorded, never asserted**: ``fdeploy.py`` decodes no ``Flags`` bit (WI-066),
so there is no Studio claim to grade them against. The verdict notes, per
case, whether they match the 2026-10-08 probe, as data for WI-066.

The expectation is the controller-built ``expected.json``. It is not trusted on
its hash: the bound builder rebuilds the whole candidate and both files must
match byte for byte. Every fresh report must name the GPO the run owned, every
re-export must be a backup of that GPO, and a non-zero guest exit status can
never pass or tag. Missing data never reads as a pass.
"""

from __future__ import annotations

import argparse
import base64
import binascii
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

from gpo_studio.backup import read_backup
from gpo_studio.fdeploy import FdeployError, native_digest, validate_fdeploy
from gpo_studio.fdeploy_parity import (
    FdeployParityError,
    FolderRedirectionRendering,
    ReportIdentity,
    byte_differences,
    encoding_facts,
    folder_redirection_rendering,
    reader_claims,
    reader_report_differences,
    report_identity,
)
from gpo_studio.model import StudioError
from gpo_studio.oracle_evidence import (
    OracleEvidenceError,
    assert_bound_source_bytes,
    lane_environment_violations,
    manifest_bound_source,
    tag_evidence_commit,
)

CANDIDATE_ARCHIVE = "fdeploy-cases.zip"
CANDIDATE_EXPECTATION = "expected.json"
REQUIRED_CANDIDATE_FILES = (CANDIDATE_ARCHIVE, CANDIDATE_EXPECTATION)
SETTINGS_PATH = "DomainSysvol/GPO/User/Documents & Settings"
FDEPLOY_FILES = ("fdeploy1.ini", "fdeploy.ini")

DEPLOYED_FILES = {"run-fdeploy-lane.ps1": "scripts/windows-oracle/run-fdeploy-lane.ps1"}
LOCAL_FILES = {
    "run-fdeploy-oracle.sh": "scripts/windows-oracle/run-fdeploy-oracle.sh",
    "finalize_fdeploy_run.py": "scripts/windows-oracle/finalize_fdeploy_run.py",
    "build-fdeploy-candidate.py": "scripts/plan-033/build-fdeploy-candidate.py",
    "psdirect.ps1": "scripts/windows-oracle/psdirect.ps1",
    "fdeploy_parity.py": "src/gpo_studio/fdeploy_parity.py",
    "fdeploy.py": "src/gpo_studio/fdeploy.py",
    "backup.py": "src/gpo_studio/backup.py",
    "backup_inventory.py": "src/gpo_studio/backup_inventory.py",
    "gpp.py": "src/gpo_studio/gpp.py",
    "model.py": "src/gpo_studio/model.py",
    "safe_io.py": "src/gpo_studio/safe_io.py",
    "xml_safety.py": "src/gpo_studio/xml_safety.py",
    "oracle_evidence.py": "src/gpo_studio/oracle_evidence.py",
    "r3-fdeploy1.ini.txt": "tests/fixtures/native-folder-redirection-gpmc/fdeploy1.ini.txt",
    "r3-fdeploy.ini.txt": "tests/fixtures/native-folder-redirection-gpmc/fdeploy.ini.txt",
    "r3-provenance.json": "tests/fixtures/native-folder-redirection-gpmc/provenance.json",
}

_RESULT_KEYS = frozenset({
    "schema_version", "run_id", "domain", "cases", "cleanup_state_restored",
    "environment", "error",
})
_CASE_KEYS = frozenset({
    "case_id", "target_name", "backup_id", "source_gpo_id", "owned_gpo_id",
    "import_succeeded", "user_extension_names", "sysvol_files", "report_file",
    "report_sha256", "report_links_to_count", "rebackup_succeeded", "rebackup_id",
    "rebackup_dir", "rebackup_files", "cleanup_succeeded", "absence_confirmed", "error",
})
_REBACKUP_FILE_KEYS = frozenset({"name", "present", "length", "sha256", "base64"})

_GUID_RE = re.compile(
    r"\{?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}?"
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")

#: Checks graded per case. Each becomes ``every_case_<name>`` in the verdict.
PER_CASE_CHECKS = (
    "fresh_report_delivered_intact",
    "fresh_report_identifies_owned_gpo",
    "rebackup_is_of_owned_gpo",
    "rebackup_bytes_delivered_intact",
    "imported_sysvol_holds_candidate_bytes",
    "rebackup_bytes_equal_candidate",
    "studio_reads_windows_rebackup",
    "windows_report_renders_expected_redirections",
    "studio_reader_agrees_with_windows_report",
    "rebackup_report_matches_fresh_report",
)

#: Checks graded from the reports and the rebuilt candidate.
_GRADED_CHECKS = (
    "candidate_rebuilds_from_bound_builder",
    *(f"every_case_{name}" for name in PER_CASE_CHECKS),
)

#: The complete check set. A verdict whose checks differ from this does not
#: pass, however the rest read.
REQUIRED_CHECKS = frozenset({
    "guest_exited_zero",
    "result_schema_exact",
    "candidate_carries_the_required_cases",
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


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _member(environment: object) -> bool:
    return (
        isinstance(environment, dict)
        and type(environment.get("computer_system_domain_role")) is int
        and environment["computer_system_domain_role"] == 3
    )


def _guid(value: object) -> str:
    return str(value or "").strip().strip("{}").casefold()


def _is_guid(value: object) -> bool:
    return isinstance(value, str) and _GUID_RE.fullmatch(value.strip()) is not None


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _run_file(run: Path, relative: object) -> Path:
    """Resolve a guest-reported relative path inside the pulled run only."""
    if not isinstance(relative, str) or not relative:
        raise ValueError("result names no file")
    pure = PurePosixPath(relative.replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"path escapes the run: {relative!r}")
    return run.joinpath(*pure.parts)


def identifies(report: ReportIdentity, record: dict[str, Any], domain: object) -> bool:
    """Does this fresh report describe the GPO this record says the run owned?"""
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


def candidate_files(archive: Path, case: dict[str, Any]) -> dict[str, bytes]:
    """The fdeploy bytes the candidate carried for one case, from the archive."""
    with zipfile.ZipFile(archive) as bundle:
        return {
            name: bundle.read(
                f"cases/{case['case_id']}/{case['backup_id']}/{SETTINGS_PATH}/{name}"
            )
            for name in FDEPLOY_FILES
        }


def _rebackup_bytes(record: dict[str, Any]) -> dict[str, bytes]:
    """Decode the guest's base64 copies; only well-formed, present entries."""
    files = record.get("rebackup_files")
    out: dict[str, bytes] = {}
    for entry in files if isinstance(files, list) else []:
        if not isinstance(entry, dict) or set(entry) != _REBACKUP_FILE_KEYS:
            continue
        if entry["present"] is not True or not isinstance(entry["base64"], str):
            continue
        try:
            out[str(entry["name"])] = base64.b64decode(entry["base64"], validate=True)
        except (binascii.Error, ValueError):
            continue
    return out


def _delivered(record: dict[str, Any], backup_root: Path, backup_id: str) -> bool:
    """Guest hash == decoded bytes == pulled file, for both fdeploy files."""
    files = record.get("rebackup_files")
    if not isinstance(files, list) or len(files) != len(FDEPLOY_FILES):
        return False
    decoded = _rebackup_bytes(record)
    by_name = {e.get("name"): e for e in files if isinstance(e, dict)}
    for name in FDEPLOY_FILES:
        entry = by_name.get(name)
        data = decoded.get(name)
        if entry is None or data is None:
            return False
        pulled = backup_root / backup_id / SETTINGS_PATH / name
        if not (
            _is_sha256(entry["sha256"])
            and entry["sha256"] == _sha_bytes(data)
            and type(entry["length"]) is int
            and entry["length"] == len(data)
            and pulled.is_file()
            and pulled.read_bytes() == data
        ):
            return False
    return True


def _sysvol_holds(record: dict[str, Any], expected: dict[str, bytes]) -> bool:
    files = record.get("sysvol_files")
    if not isinstance(files, list):
        return False
    seen = {
        e.get("relative_path"): (e.get("length"), e.get("sha256"))
        for e in files if isinstance(e, dict)
    }
    want = {name: (len(data), _sha_bytes(data)) for name, data in expected.items()}
    return len(files) == len(want) and seen == want


def grade_case(
    run: Path,
    record: dict[str, Any],
    expected: dict[str, Any],
    candidate: dict[str, bytes],
    domain: object,
    builder: Any,
) -> tuple[dict[str, bool], dict[str, Any]]:
    """Grade one case. Kept apart from ``main`` so tests drive every check."""
    checks = dict.fromkeys(PER_CASE_CHECKS, False)
    summary: dict[str, Any] = {}

    report_path = _run_file(run, record.get("report_file"))
    report_bytes = report_path.read_bytes()
    checks["fresh_report_delivered_intact"] = _is_sha256(record.get("report_sha256")) and (
        _sha_bytes(report_bytes) == record.get("report_sha256")
    )
    checks["fresh_report_identifies_owned_gpo"] = identifies(
        report_identity(report_bytes), record, domain
    )
    fresh: FolderRedirectionRendering = folder_redirection_rendering(report_bytes)
    summary["fresh_report"] = fresh.to_json()
    expected_rows = sorted(tuple(row) for row in expected["expected_report"])
    checks["windows_report_renders_expected_redirections"] = (
        bool(expected_rows)
        and fresh.extension_count == 1
        and not fresh.errors
        and fresh.computer_side_extensions == 0
        and fresh.keys() == expected_rows
    )

    checks["imported_sysvol_holds_candidate_bytes"] = _sysvol_holds(record, candidate)

    backup_root = _run_file(run, record.get("rebackup_dir"))
    readiness = builder.import_readiness(backup_root)
    if isinstance(readiness, str):
        raise ValueError(f"re-export is not a native backup: {readiness}")
    backup_id, backup_gpo = readiness
    checks["rebackup_is_of_owned_gpo"] = (
        record.get("rebackup_succeeded") is True
        and _is_guid(record.get("rebackup_id"))
        and _guid(backup_id) == _guid(record.get("rebackup_id"))
        and _is_guid(record.get("owned_gpo_id"))
        and _guid(backup_gpo) == _guid(record.get("owned_gpo_id"))
    )
    checks["rebackup_bytes_delivered_intact"] = _delivered(record, backup_root, backup_id)

    windows = _rebackup_bytes(record)
    summary["byte_differences"] = {
        name: byte_differences(candidate[name], windows[name]) if name in windows else None
        for name in FDEPLOY_FILES
    }
    checks["rebackup_bytes_equal_candidate"] = all(
        name in windows and windows[name] == candidate[name] for name in FDEPLOY_FILES
    ) and all(
        _sha_bytes(candidate[name]) == expected[key]["sha256"]
        for name, key in (("fdeploy1.ini", "fdeploy1"), ("fdeploy.ini", "marker"))
    )
    summary["windows_encoding"] = {name: encoding_facts(data) for name, data in windows.items()}

    backup = read_backup(backup_root)
    claims: tuple[Any, ...] = ()
    if len(backup.gpos) == 1 and backup.gpos[0].fdeploy is not None:
        gpo = backup.gpos[0]
        document = gpo.fdeploy
        assert document is not None
        claims = reader_claims(document)
        issues = validate_fdeploy(document)
        summary["studio_claims"] = [claim.to_json() for claim in claims]
        summary["studio_findings"] = [issue.code for issue in issues]
        checks["studio_reads_windows_rebackup"] = (
            _guid(gpo.guid) == _guid(record.get("owned_gpo_id"))
            and bool(expected["expected_reader"])
            and summary["studio_claims"] == expected["expected_reader"]
            and not issues
            and not document.parse_warnings
            and "fdeploy1.ini" in windows
            and native_digest(document)[0] == _sha_bytes(windows["fdeploy1.ini"])
        )
    else:
        summary["studio_claims"] = None
    disagreements = reader_report_differences(claims, fresh)
    summary["reader_report_differences"] = disagreements
    checks["studio_reader_agrees_with_windows_report"] = bool(claims) and not disagreements

    gpreport = backup_root / backup_id / "gpreport.xml"
    summary["rebackup_report"] = None
    if gpreport.is_file():
        rebackup_rendering = folder_redirection_rendering(gpreport.read_bytes())
        summary["rebackup_report"] = rebackup_rendering.to_json()
        checks["rebackup_report_matches_fresh_report"] = (
            bool(fresh.redirections) and rebackup_rendering == fresh
        )

    # Recorded, never asserted: Windows' option rendering, for WI-066.
    options = [dict(r.options) for r in fresh.redirections]
    probe = expected.get("probe_20261008_options")
    summary["recorded"] = {
        "flags": expected.get("flags"),
        "windows_options": options,
        "matches_probe_20261008": bool(options) and all(o == probe for o in options),
        "user_extension_names": record.get("user_extension_names"),
    }
    return checks, summary


def _builder(repo: Path) -> Any:
    """The bound candidate builder, loaded from the repository under test."""
    path = repo / LOCAL_FILES["build-fdeploy-candidate.py"]
    spec = importlib.util.spec_from_file_location("fdeploy_bound_builder", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def candidate_rebuilds(builder: Any, candidate_root: Path, repo: Path) -> bool:
    """Rebuild the whole candidate with the bound builder; demand identical bytes."""
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
    run_id = result.get("run_id")
    prefix = f"zz-studio-fd-{run_id}-" if isinstance(run_id, str) and run_id else None
    expected_cases = [
        c for c in (expected.get("cases") if isinstance(expected.get("cases"), list) else [])
        if isinstance(c, dict)
    ]
    expected_ids = [c.get("case_id") for c in expected_cases]
    required_ids = list(builder.REQUIRED_CASE_IDS)

    checks: dict[str, bool] = {
        "guest_exited_zero": args.guest_status == 0,
        "result_schema_exact": set(result) == _RESULT_KEYS
        and type(result.get("schema_version")) is int
        and result["schema_version"] == 1
        and isinstance(raw_cases, list)
        and len(cases) == len(raw_cases)
        and all(set(c) == _CASE_KEYS for c in cases),
        "candidate_carries_the_required_cases": bool(required_ids)
        and expected_ids == required_ids,
        "every_expected_case_ran_once": bool(cases)
        and sorted(str(c.get("case_id")) for c in cases) == sorted(required_ids),
        "every_case_imported": bool(cases)
        and all(c.get("import_succeeded") is True for c in cases),
        "every_case_identity_matches_candidate": bool(cases) and all(
            any(
                _guid(c.get("backup_id")) == _guid(e.get("backup_id"))
                and _guid(c.get("source_gpo_id")) == _guid(e.get("source_gpo_id"))
                for e in expected_cases if e.get("case_id") == c.get("case_id")
            )
            for c in cases
        ),
        "every_owned_gpo_is_this_runs": prefix is not None and bool(cases) and all(
            _is_guid(c.get("owned_gpo_id"))
            and isinstance(c.get("target_name"), str)
            and c["target_name"].startswith(prefix)
            for c in cases
        )
        and len({_guid(c.get("owned_gpo_id")) for c in cases}) == len(cases),
        "every_disposable_gpo_unlinked": bool(cases) and all(
            type(c.get("report_links_to_count")) is int and c["report_links_to_count"] == 0
            for c in cases
        ),
        "every_gpo_removed_and_absent": bool(cases) and all(
            c.get("cleanup_succeeded") is True and c.get("absence_confirmed") is True
            for c in cases
        ),
        "cleanup_state_restored": result.get("cleanup_state_restored") is True,
        "harness_reported_no_error": "error" in result and result["error"] is None
        and all("error" in c and c["error"] is None for c in cases),
        "member_server_host_role": _member(result.get("environment")),
        "raw_command_artifacts_complete": bool(cases) and all(
            (run / "commands" / str(c.get("case_id")) / f"{n}.{s}.txt").is_file()
            for c in cases for n in ("import", "report", "backup") for s in ("stdout", "stderr")
        )
        and (run / "builder.stdout.txt").is_file(),
    }
    environment = result.get("environment")
    violations = list(
        lane_environment_violations(environment) if isinstance(environment, dict)
        else ["environment was not recorded"]
    )
    checks["environment_matches_frozen_spec"] = not violations

    comparison: dict[str, Any] = {"cases": {}}
    errors: list[str] = []
    per_case: dict[str, bool] = dict.fromkeys(PER_CASE_CHECKS, bool(cases))
    try:
        checks["candidate_rebuilds_from_bound_builder"] = candidate_rebuilds(
            builder, candidate_root, repo
        )
    except (AttributeError, KeyError, OSError, TypeError, ValueError) as exc:
        checks["candidate_rebuilds_from_bound_builder"] = False
        errors.append(f"rebuild: {type(exc).__name__}: {exc}")
    by_id = {c.get("case_id"): c for c in expected_cases}
    for case in cases:
        case_id = str(case.get("case_id"))
        try:
            expectation = by_id[case_id]
            case_checks, summary = grade_case(
                run, case, expectation, candidate_files(candidate, expectation),
                result.get("domain"), builder,
            )
        except (
            AttributeError, KeyError, OSError, TypeError, ValueError, StudioError,
            FdeployError, FdeployParityError, zipfile.BadZipFile,
        ) as exc:
            case_checks = dict.fromkeys(PER_CASE_CHECKS, False)
            summary = {"error": f"{type(exc).__name__}: {exc}"}
            errors.append(f"{case_id}: {type(exc).__name__}: {exc}")
        for name in PER_CASE_CHECKS:
            per_case[name] = per_case[name] and case_checks[name]
        comparison["cases"][case_id] = {"checks": case_checks, **summary}
    for name in PER_CASE_CHECKS:
        checks[f"every_case_{name}"] = per_case[name]

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
        "comparison_error": "; ".join(errors) or None,
        "harness_error": result.get("error"),
        "excluded_flags": expected.get("excluded_flags"),
        "recorded_not_asserted": expected.get("recorded_not_asserted"),
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
