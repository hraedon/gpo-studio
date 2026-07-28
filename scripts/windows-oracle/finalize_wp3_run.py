#!/usr/bin/env python3
"""Validate a WP-3 secedit round trip and write its reviewable verdict."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from gpo_studio.security_template import (
    SecurityTemplate,
    SecurityTemplateError,
    decode_security_template,
    parse_security_template,
)

_EXPECTED_ENVIRONMENT = {
    "server_caption": "Microsoft Windows Server 2025 Standard",
    "server_build": "26100",
    "powershell_edition": "Desktop",
    "powershell_version": "5.1.26100.32860",
    "group_policy_module_version": "1.0.0.0",
    "gpmc_version": "built-in",
    "locale": "en-US",
    "lgpo_sha256": "0c97f29543418b30340c4ff5d930d31e6196dd59c2cc74b6b890fa7b90c910c7",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _setting_matches(
    template: SecurityTemplate,
    *,
    section: str,
    key: str,
    expected: str,
) -> bool:
    actual = template.get_value(section, key)
    if actual is None:
        return False
    if section.casefold() == "privilege rights":
        expected_principals = {
            principal.strip().casefold()
            for principal in expected.split(",")
            if principal.strip()
        }
        actual_principals = {
            principal.strip().casefold()
            for principal in actual.split(",")
            if principal.strip()
        }
        return actual_principals == expected_principals
    return actual.strip() == expected.strip()


def _matches_expected(
    template: SecurityTemplate,
    settings: list[dict[str, Any]],
) -> tuple[bool, list[dict[str, str | None]]]:
    differences: list[dict[str, str | None]] = []
    for setting in settings:
        section = str(setting["section"])
        key = str(setting["key"])
        expected = str(setting["value"])
        if not _setting_matches(
            template,
            section=section,
            key=key,
            expected=expected,
        ):
            differences.append(
                {
                    "section": section,
                    "key": key,
                    "expected": expected,
                    "actual": template.get_value(section, key),
                }
            )
    return not differences, differences


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    repo_root = args.repo_root.resolve()

    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8-sig"))
    expected = json.loads((run_dir / "expected.json").read_text(encoding="utf-8"))
    checks: dict[str, bool] = {
        "validate_succeeded": result["validate_exit_code"] == 0,
        "import_succeeded": result["import_exit_code"] == 0,
        "export_succeeded": result["export_exit_code"] == 0,
        "export_created": result["export_created"] is True,
        "cleanup_succeeded": result["cleanup_succeeded"] is True,
        "database_absent_after_cleanup": (
            result["database_absent_after_cleanup"] is True
        ),
        "database_residual_files_empty": result["database_residual_files"] == [],
        "non_applying_operations_only": result["invoked_operations"]
        == ["validate", "import", "export"],
        "expected_schema_supported": expected.get("schema_version") == 1,
        "environment_matches_frozen_spec": all(
            result["environment"].get(key) == value
            for key, value in _EXPECTED_ENVIRONMENT.items()
        ),
    }

    candidate_differences: list[dict[str, str | None]] = []
    export_differences: list[dict[str, str | None]] = []
    decode_error: str | None = None
    checks["candidate_decodes"] = False
    checks["candidate_has_required_preamble"] = False
    checks["candidate_semantics_match"] = False
    checks["windows_export_decodes"] = False
    checks["windows_export_semantics_match"] = False
    try:
        candidate = parse_security_template(
            decode_security_template((run_dir / "candidate.inf").read_bytes())
        )
        checks["candidate_decodes"] = True
        checks["candidate_has_required_preamble"] = (
            candidate.get_value("Unicode", "Unicode") == "yes"
            and candidate.get_value("Version", "signature") == '"$CHICAGO$"'
            and candidate.get_value("Version", "Revision") == "1"
        )
        (
            checks["candidate_semantics_match"],
            candidate_differences,
        ) = _matches_expected(candidate, expected["settings"])

        exported = parse_security_template(
            decode_security_template((run_dir / "exported.inf").read_bytes())
        )
        checks["windows_export_decodes"] = True
        (
            checks["windows_export_semantics_match"],
            export_differences,
        ) = _matches_expected(exported, expected["settings"])
    except (KeyError, OSError, SecurityTemplateError, TypeError, ValueError) as error:
        decode_error = str(error)

    deployed_map = {
        "run-wp3-security-template.ps1": (
            repo_root / "scripts/windows-oracle/run-wp3-security-template.ps1"
        ),
        "remote-run.ps1": repo_root / "scripts/windows-oracle/remote-run.ps1",
        "remote-run-launcher.ps1": (
            repo_root / "scripts/windows-oracle/remote-run.ps1"
        ),
    }
    local_map = {
        "run-wp3-oracle.sh": (
            repo_root / "scripts/windows-oracle/run-wp3-oracle.sh"
        ),
        "build-wp3-candidate.py": (
            repo_root / "scripts/plan-033/build-wp3-candidate.py"
        ),
        "finalize_wp3_run.py": (
            repo_root / "scripts/windows-oracle/finalize_wp3_run.py"
        ),
    }
    source_hashes: dict[str, str] = {}
    harness_ok = True
    for name, source_path in {**deployed_map, **local_map}.items():
        source_hash = _sha256(source_path)
        source_hashes[name] = source_hash
        evidence_path = (
            run_dir / "deployed" / name
            if name in deployed_map
            else run_dir / name
        )
        if not evidence_path.is_file() or _sha256(evidence_path) != source_hash:
            harness_ok = False
    checks["harness_matches_source"] = harness_ok

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
    checks["source_tree_clean"] = not dirty

    verdict = {
        "schema_version": 1,
        "run_id": result["run_id"],
        "passed": all(checks.values()),
        "checks": checks,
        "candidate_differences": candidate_differences,
        "export_differences": export_differences,
        "decode_error": decode_error,
        "harness_error": result["error"],
        "environment": result["environment"],
        "source": {"commit": commit, "dirty": dirty, "files": source_hashes},
        "artifacts": {
            str(path.relative_to(run_dir)): _sha256(path)
            for path in sorted(run_dir.rglob("*"))
            if path.is_file() and path.name != "verification.json"
        },
    }
    (run_dir / "verification.json").write_text(
        json.dumps(verdict, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(verdict, indent=2, sort_keys=True))
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
