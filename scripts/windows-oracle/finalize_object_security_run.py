#!/usr/bin/env python3
"""Finalize a non-applying Plan 034 object-security secedit round trip."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath
from typing import Any

from gpo_studio.oracle_evidence import (
    OracleEvidenceError,
    assert_bound_source_bytes,
    lane_environment_violations,
    manifest_bound_source,
    tag_evidence_commit,
)
from gpo_studio.security_template import (
    SecurityTemplateError,
    decode_security_template,
    parse_security_template,
)

_SECTIONS = frozenset({"Registry Keys", "File Security", "Service General Setting"})
_AREAS = frozenset({"regkeys", "filestore", "services"})
_NATIVE_ROW = re.compile(r'^"([^"]+)",\s*(\d+),\s*"([^"]*)"$')
_EXPORTED_ROW = re.compile(r'^"([^"]+)",\s*(\d+),\s*"([^"]*)"$')

DEPLOYED_FILES = {
    "run-object-security-template.ps1": "scripts/windows-oracle/run-object-security-template.ps1",
}
LOCAL_FILES = {
    "run-object-security-oracle.sh": "scripts/windows-oracle/run-object-security-oracle.sh",
    "build-object-security-candidate.py": "scripts/plan-033/build-object-security-candidate.py",
    "finalize_object_security_run.py": "scripts/windows-oracle/finalize_object_security_run.py",
    "psdirect.ps1": "scripts/windows-oracle/psdirect.ps1",
    "object_security.py": "src/gpo_studio/object_security.py",
    "security_template.py": "src/gpo_studio/security_template.py",
    "sddl.py": "src/gpo_studio/sddl.py",
    "oracle_evidence.py": "src/gpo_studio/oracle_evidence.py",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _expected_rows(raw: object) -> dict[tuple[str, str], tuple[int, str]]:
    if (
        not isinstance(raw, dict)
        or set(raw) != {"schema_version", "settings"}
        or type(raw.get("schema_version")) is not int
        or raw["schema_version"] != 1
    ):
        raise ValueError("expected schema_version must be integer 1 with exact top-level keys")
    settings = raw.get("settings")
    if not isinstance(settings, list) or not settings:
        raise ValueError("expected settings must be a non-empty array")
    rows: dict[tuple[str, str], tuple[int, str]] = {}
    for item in settings:
        if not isinstance(item, dict) or set(item) != {"section", "target", "code", "sddl"}:
            raise ValueError("each expected setting must have section, target, code, sddl")
        section, target, code, sddl = (item["section"], item["target"], item["code"], item["sddl"])
        if section not in _SECTIONS or not isinstance(target, str) or not target:
            raise ValueError("expected setting has an unsupported section or target")
        if type(code) is not int or not isinstance(sddl, str):
            raise ValueError("expected code must be int and sddl must be string")
        key = (section.casefold(), target.casefold())
        if key in rows:
            raise ValueError("expected settings repeat a section target")
        rows[key] = (code, sddl)
    return rows


def _template_rows(path: Path, *, exported: bool) -> dict[tuple[str, str], tuple[int, str]]:
    template = parse_security_template(decode_security_template(path.read_bytes()))
    rows: dict[tuple[str, str], tuple[int, str]] = {}
    observed_sections: set[str] = set()
    section_names = [section.name for section in template.sections]
    if len({name.casefold() for name in section_names}) != len(section_names):
        raise ValueError("object-security template repeats a section")
    allowed_sections = _SECTIONS | {"Unicode", "Version"}
    if set(section_names) != allowed_sections:
        raise ValueError("object-security template has missing or extra sections")
    for section in template.sections:
        if section.name not in _SECTIONS:
            continue
        observed_sections.add(section.name)
        raw_rows: list[str] = list(section.unknown_lines)
        if exported:
            if raw_rows:
                raise ValueError(f"{section.name} export is not ordinal row-shaped")
            ordinals: set[int] = set()
            for ordinal, value in section.entries:
                if not ordinal.isdecimal() or int(ordinal) in ordinals:
                    raise ValueError(f"{section.name} export has invalid ordinal")
                ordinals.add(int(ordinal))
                raw_rows.append(value)
        elif section.entries:
            raise ValueError(f"{section.name} candidate is not native row-shaped")
        for raw_row in raw_rows:
            match = (_EXPORTED_ROW if exported else _NATIVE_ROW).fullmatch(raw_row)
            if match is None:
                raise ValueError(f"{section.name} has an unparseable object-security row")
            target, code_text, sddl = match.groups()
            key = (section.name.casefold(), target.casefold())
            if key in rows:
                raise ValueError("object-security rows repeat a section target")
            rows[key] = (int(code_text), sddl)
    if observed_sections != _SECTIONS:
        raise ValueError("object-security template has missing or extra authored sections")
    return rows


def _differences(
    expected: Mapping[tuple[str, str], tuple[int, str]],
    actual: Mapping[tuple[str, str], tuple[int, str]],
) -> list[dict[str, object]]:
    differences: list[dict[str, object]] = []
    for key in sorted(set(expected) | set(actual)):
        if expected.get(key) != actual.get(key):
            differences.append(
                {
                    "section": key[0],
                    "target": key[1],
                    "expected": expected.get(key),
                    "actual": actual.get(key),
                }
            )
    return differences


def _areas(arguments: list[str]) -> frozenset[str] | None:
    folded = [argument.casefold() for argument in arguments]
    if "/areas" not in folded:
        return None
    start = folded.index("/areas") + 1
    values: list[str] = []
    for value in folded[start:]:
        if value.startswith("/"):
            break
        values.append(value)
    return frozenset(values)


def _operations_match(result: Mapping[str, Any]) -> bool:
    operations = result.get("invoked_operations")
    if not isinstance(operations, list) or len(operations) != 3:
        return False
    if [item.get("name") for item in operations if isinstance(item, dict)] != [
        "validate",
        "import",
        "export",
    ]:
        return False
    area_sets: list[frozenset[str] | None] = []
    database_path: str | None = None
    run_root: PureWindowsPath | None = None
    for expected_name, item in zip(("validate", "import", "export"), operations, strict=True):
        if not isinstance(item, dict) or item.get("name") != expected_name:
            return False
        arguments = item.get("arguments")
        if not isinstance(arguments, list) or not all(
            isinstance(value, str) for value in arguments
        ):
            return False
        if not arguments or arguments[0].casefold() != f"/{expected_name}":
            return False
        if any(value.casefold() == "/configure" for value in arguments):
            return False
        folded = [value.casefold() for value in arguments]
        if expected_name == "validate":
            if len(arguments) != 2:
                return False
            candidate = PureWindowsPath(arguments[1])
            if candidate.name.casefold() != "candidate.inf":
                return False
            run_root = candidate.parent
            continue
        expected_switches = (
            ["/import", "/db", "/cfg", "/overwrite", "/areas", "/log", "/quiet"]
            if expected_name == "import"
            else ["/export", "/db", "/cfg", "/areas", "/log", "/quiet"]
        )
        if [value for value in folded if value.startswith("/")] != expected_switches:
            return False
        if len(arguments) != (13 if expected_name == "import" else 12):
            return False
        if folded[1] != "/db" or folded[3] != "/cfg":
            return False
        if database_path is None:
            database_path = folded[2]
        elif folded[2] != database_path:
            return False
        expected_cfg = "\\candidate.inf" if expected_name == "import" else "\\exported.inf"
        if not folded[4].endswith(expected_cfg):
            return False
        if run_root is None or PureWindowsPath(arguments[2]).parent != run_root:
            return False
        if PureWindowsPath(arguments[4]).parent != run_root:
            return False
        if PureWindowsPath(arguments[2]).name.casefold() != "temporary-security-database.sdb":
            return False
        if expected_name != "validate":
            area_sets.append(_areas(arguments))
    return area_sets == [_AREAS, _AREAS]


def _member_server_environment(environment: object) -> bool:
    if not isinstance(environment, dict):
        return False
    role = environment.get("computer_system_domain_role")
    return type(role) is int and role == 3


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--no-tag", action="store_true")
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    candidate_root = args.candidate_root.resolve()
    repo_root = args.repo_root.resolve()
    try:
        assert_bound_source_bytes(repo_root, {**DEPLOYED_FILES, **LOCAL_FILES}.values())
    except OracleEvidenceError as exc:
        print(f"finalize refused: {exc}", file=sys.stderr)
        return 1
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8-sig"))
    expected_raw = json.loads((candidate_root / "expected.json").read_text(encoding="utf-8"))
    checks = {
        "validate_succeeded": type(result.get("validate_exit_code")) is int
        and result["validate_exit_code"] == 0,
        "import_succeeded": type(result.get("import_exit_code")) is int
        and result["import_exit_code"] == 0,
        "export_succeeded": type(result.get("export_exit_code")) is int
        and result["export_exit_code"] == 0,
        "export_created": result.get("export_created") is True,
        "cleanup_succeeded": result.get("cleanup_succeeded") is True,
        "database_absent_after_cleanup": result.get("database_absent_after_cleanup") is True,
        "database_residual_files_empty": result.get("database_residual_files") == [],
        "observed_secedit_operations_match": _operations_match(result),
        "harness_reported_no_error": result.get("error") is None,
        "member_server_host_role": _member_server_environment(result.get("environment")),
    }
    required_command_artifacts = (
        "validate.stdout.txt",
        "validate.stderr.txt",
        "import.stdout.txt",
        "import.stderr.txt",
        "export.stdout.txt",
        "export.stderr.txt",
        "import.log",
        "export.log",
    )
    checks["raw_command_artifacts_complete"] = all(
        (run_dir / "commands" / name).is_file() for name in required_command_artifacts
    )
    environment_violations = list(lane_environment_violations(result["environment"]))
    checks["environment_matches_frozen_spec"] = not environment_violations
    comparison_error: str | None = None
    candidate_differences: list[dict[str, object]] = []
    export_differences: list[dict[str, object]] = []
    try:
        expected = _expected_rows(expected_raw)
        candidate = _template_rows(candidate_root / "candidate.inf", exported=False)
        exported = _template_rows(run_dir / "exported.inf", exported=True)
        candidate_differences = _differences(expected, candidate)
        export_differences = _differences(expected, exported)
        checks["expected_schema_supported"] = True
        checks["candidate_exact"] = not candidate_differences
        checks["windows_export_exact"] = not export_differences
    except (KeyError, OSError, SecurityTemplateError, TypeError, ValueError) as exc:
        comparison_error = str(exc)
        checks.update(
            expected_schema_supported=False, candidate_exact=False, windows_export_exact=False
        )

    # WI-062: controller-side files are bound by (commit, path, sha256) from
    # the source-tree copy that ran -- no byte copy rides in the pack, since
    # git at the commit holds the bytes and assert_bound_source_bytes proved
    # tree, index and HEAD agree.
    bound_local = manifest_bound_source(repo_root, LOCAL_FILES)
    source_hashes: dict[str, str] = {
        name: entry["sha256"] for name, entry in bound_local.items()
    }
    deployed_intact = True
    for name, relative in DEPLOYED_FILES.items():
        source_hashes[name] = _sha256(repo_root / relative)
        evidence = run_dir / "deployed" / name
        deployed_intact &= evidence.is_file() and _sha256(evidence) == source_hashes[name]
    checks["deployed_harness_matches_source"] = deployed_intact
    delivery: dict[str, dict[str, object]] = {}
    for name in ("candidate.inf", "expected.json"):
        controller = candidate_root / name
        guest = run_dir / name
        intact = controller.is_file() and guest.is_file() and _sha256(controller) == _sha256(guest)
        delivery[name] = {
            "controller_sha256": _sha256(controller) if controller.is_file() else None,
            "guest_copy_matches": intact,
        }
    checks["candidate_delivered_intact"] = all(
        bool(item["guest_copy_matches"]) for item in delivery.values()
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True, capture_output=True, text=True
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
    checks["source_tree_clean"] = not dirty
    verdict = {
        "schema_version": 2,
        "run_id": result["run_id"],
        "passed": all(checks.values()),
        "checks": checks,
        "candidate_differences": candidate_differences,
        "export_differences": export_differences,
        "comparison_error": comparison_error,
        "harness_error": result.get("error"),
        "transport": "psdirect",
        "candidate_delivery": delivery,
        "environment": result["environment"],
        "environment_violations": environment_violations,
        "source": {
            "commit": commit,
            "dirty": dirty,
            "files": source_hashes,
            # WI-062: every bound file's repository path, and the names whose
            # bytes the pack actually carries. Controller-side files are
            # verified against git at the commit, not against pack copies.
            "paths": {**DEPLOYED_FILES, **LOCAL_FILES},
            "banked_copies": sorted(DEPLOYED_FILES),
        },
        "artifacts": {
            path.relative_to(run_dir).as_posix(): _sha256(path)
            for path in sorted(run_dir.rglob("*"))
            if path.is_file() and path.name != "verification.json"
        },
    }
    tag_outcome: str | None = None
    if verdict["passed"] and not args.no_tag:
        try:
            tag_outcome = tag_evidence_commit(repo_root, result["run_id"], commit)
        except OracleEvidenceError as exc:
            print(f"evidence tag failed: {exc}", file=sys.stderr)
            return 1
    (run_dir / "verification.json").write_text(
        json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(verdict, indent=2, sort_keys=True))
    if tag_outcome is not None:
        print(f"EVIDENCE_TAG={tag_outcome}")
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
