#!/usr/bin/env python3
"""Validate a WP-2 Windows import run and write its reviewable verdict."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from gpo_studio.backup import BackupError, read_backup
from gpo_studio.import_export import extract_side_settings
from gpo_studio.model import ValidationError
from gpo_studio.oracle_evidence import OracleEvidenceError, tag_evidence_commit
from gpo_studio.registry_pol import RegistryPolError

_BACKUP_NS = "http://www.microsoft.com/GroupPolicy/GPOOperations"


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--no-tag",
        action="store_true",
        help=(
            "do not create the evidence/<run-id> tag for a passing run. The tag "
            "preserves the source commit that squash-merging would otherwise "
            "orphan (issue #22); skip it only when tagging is handled elsewhere."
        ),
    )
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    repo_root = args.repo_root.resolve()

    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8-sig"))
    expected = json.loads((run_dir / "expected.json").read_text(encoding="utf-8"))
    checks: dict[str, bool] = {
        "whatif_succeeded": result["whatif_succeeded"] is True,
        "whatif_target_absent": result["whatif_target_absent"] is True,
        "actual_import_succeeded": result["import_succeeded"] is True,
        "cleanup_succeeded": result["cleanup_succeeded"] is True,
        "cleanup_state_restored": result["cleanup_state_restored"] is True,
        "backup_id_matches": result["backup_id"] == expected["backup_id"],
        "backup_id_distinct": result["backup_id"] != result["source_gpo_id"],
    }

    versions = result["version_state"] or {}
    checks["computer_versions_synchronized"] = (
        int(versions.get("computer_dsa", -1))
        == int(versions.get("computer_sysvol", -2))
        > 0
    )
    checks["user_versions_synchronized"] = (
        int(versions.get("user_dsa", -1))
        == int(versions.get("user_sysvol", -2))
        > 0
    )

    expected_settings = sorted(_setting_projection(item) for item in expected["settings"])
    readback_settings = sorted(_setting_projection(item) for item in result["registry_readback"])
    checks["group_policy_readback_matches"] = readback_settings == expected_settings

    checks.update({
        "rebackup_has_one_gpo": False,
        "windows_rebackup_matches": False,
        "rebackup_machine_version_matches": False,
        "rebackup_user_version_matches": False,
    })
    rebackup_error: str | None = None
    try:
        rebackup = read_backup(run_dir / "rebackup")
        checks["rebackup_has_one_gpo"] = len(rebackup.gpos) == 1
        backup_gpo = rebackup.gpos[0]
        if backup_gpo.content_root is None:
            rebackup_settings: list[tuple[object, ...]] = []
        else:
            imported_settings = [
                *extract_side_settings(backup_gpo.content_root, "computer"),
                *extract_side_settings(backup_gpo.content_root, "user"),
            ]
            rebackup_settings = sorted(
                (
                    item.side,
                    item.hive,
                    item.key.casefold(),
                    item.value_name.casefold(),
                    item.registry_type,
                    item.value,
                )
                for item in imported_settings
            )
        checks["windows_rebackup_matches"] = rebackup_settings == expected_settings

        backup_xml = run_dir / "rebackup" / rebackup.backup_id / "Backup.xml"
        root = ET.fromstring(backup_xml.read_bytes())
        core = root.find(f".//{{{_BACKUP_NS}}}GroupPolicyCoreSettings")
        if core is None:
            raise ValueError("re-backup has no GroupPolicyCoreSettings")
        packed_machine = int(
            core.findtext(f"{{{_BACKUP_NS}}}MachineVersionNumber", "-1")
        )
        packed_user = int(core.findtext(f"{{{_BACKUP_NS}}}UserVersionNumber", "-1"))
        if versions:
            checks["rebackup_machine_version_matches"] = packed_machine == (
                (int(versions["computer_dsa"]) << 16)
                | int(versions["computer_sysvol"])
            )
            checks["rebackup_user_version_matches"] = packed_user == (
                (int(versions["user_dsa"]) << 16) | int(versions["user_sysvol"])
            )
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

    # Provenance: verify that the harness files which actually executed
    # match the committed source tree.  Files deployed to Windows are
    # retrieved post-run from the remote host into deployed/ and compared
    # against source.  Locally-executed scripts are compared from the
    # source-tree copy that ran.
    deployed_map = {
        "run-wp2-import.ps1": repo_root / "scripts/windows-oracle/run-wp2-import.ps1",
        "remote-run.ps1": repo_root / "scripts/windows-oracle/remote-run.ps1",
        "remote-run-launcher.ps1": repo_root / "scripts/windows-oracle/remote-run.ps1",
    }
    local_map = {
        "run-wp2-oracle.sh": repo_root / "scripts/windows-oracle/run-wp2-oracle.sh",
        "build-wp2-candidate.py": repo_root / "scripts/plan-033/build-wp2-candidate.py",
    }
    source_hashes: dict[str, str] = {}
    harness_ok = True
    for name, src_path in {**deployed_map, **local_map}.items():
        src_hash = _sha256(src_path)
        source_hashes[name] = src_hash
        evidence_path = run_dir / "deployed" / name if name in deployed_map else run_dir / name
        if not evidence_path.is_file() or _sha256(evidence_path) != src_hash:
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

    verdict = {
        "schema_version": 1,
        "run_id": result["run_id"],
        "passed": all(checks.values()),
        "checks": checks,
        "rebackup_error": rebackup_error,
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
    print(json.dumps(verdict, indent=2, sort_keys=True))

    # Preserve the commit this certification binds to (issue #22). Only WP-0's
    # finalizer tagged, which is how WP-0's and WP-2's own bindings were lost --
    # see docs/evidence-binding-audit-2026-08-03.md.
    if verdict["passed"] and not args.no_tag:
        try:
            print(f"EVIDENCE_TAG={tag_evidence_commit(repo_root, result['run_id'], commit)}")
        except OracleEvidenceError as exc:
            print(f"evidence tag failed: {exc}", file=sys.stderr)
            return 1
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
