#!/usr/bin/env python3
"""Finalize the Plan 034 publication-completeness lane.

Grades one question: **is the publication plan's account of what it would
write the same as what Windows actually produced?** Both halves count -- a file
Windows wrote that no step names is content a publication would silently drop,
and a step naming a file Windows never produces is a plan asserting something
the directory does not want.

The expectation is the controller-built ``expected.json``, hash-bound below
(WI-025), never the guest's own output. That distinction carries more weight
here than in a round-trip lane: the guest is the thing being measured, so an
expectation it supplied would make this a comparison of Windows against itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from gpo_studio.oracle_evidence import (
    OracleEvidenceError,
    assert_bound_source_bytes,
    lane_environment_violations,
    manifest_bound_source,
    tag_evidence_commit,
)

CANDIDATE_ARCHIVE = "studio-publication-backup.zip"
CANDIDATE_EXPECTATION = "expected.json"

#: Every file the candidate builder writes. Named rather than globbed, so a
#: consumed artifact the verdict never mentions is refused at the door instead
#: of recorded as a shorter block (WI-025's worked argument).
REQUIRED_CANDIDATE_FILES = (CANDIDATE_ARCHIVE, CANDIDATE_EXPECTATION)

DEPLOYED_FILES = {
    "run-publication-import.ps1": "scripts/windows-oracle/run-publication-import.ps1"
}
LOCAL_FILES = {
    "run-publication-oracle.sh": "scripts/windows-oracle/run-publication-oracle.sh",
    "finalize_publication_run.py": "scripts/windows-oracle/finalize_publication_run.py",
    "build-publication-candidate.py": "scripts/plan-033/build-publication-candidate.py",
    "psdirect.ps1": "scripts/windows-oracle/psdirect.ps1",
    "publication.py": "src/gpo_studio/publication.py",
    "export.py": "src/gpo_studio/export.py",
    "canonical.py": "src/gpo_studio/canonical.py",
    "gpp.py": "src/gpo_studio/gpp.py",
    "model.py": "src/gpo_studio/model.py",
    "registry_pol.py": "src/gpo_studio/registry_pol.py",
    "validation.py": "src/gpo_studio/validation.py",
    "oracle_evidence.py": "src/gpo_studio/oracle_evidence.py",
    "xml_safety.py": "src/gpo_studio/xml_safety.py",
}

#: SYSVOL is case-insensitive and Windows does not preserve the plan's casing:
#: a step naming ``Machine/Registry.pol`` is satisfied by ``Machine/registry.pol``.
#: Comparison is casefolded for that reason and no other -- content differences
#: are never folded away.
def _fold(paths: object) -> list[str]:
    if not isinstance(paths, list):
        raise ValueError("expected a list of paths")
    return sorted(str(p).replace("\\", "/").casefold() for p in paths)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unpack_version(packed: int) -> tuple[int, int]:
    """Split the packed GPT.INI field into (machine, user) halves."""
    return packed & 0xFFFF, (packed >> 16) & 0xFFFF


def _version_half_matches(half: str, packed: int) -> bool:
    """Did exactly the declared half or halves move off zero?

    A fresh GPO starts at 0/0 and one import moves each side that has content
    to 1, so the declared half is checkable directly rather than by differencing
    against a prior read.
    """
    machine, user = _unpack_version(packed)
    if half == "both":
        return machine > 0 and user > 0
    if half == "machine":
        return machine > 0 and user == 0
    if half == "user":
        return user > 0 and machine == 0
    return False


def _member(environment: object) -> bool:
    return (
        isinstance(environment, dict)
        and type(environment.get("computer_system_domain_role")) is int
        and environment["computer_system_domain_role"] == 3
    )


def _observed_paths(result: dict[str, Any]) -> list[str]:
    files = result.get("sysvol_files")
    if not isinstance(files, list):
        raise ValueError("result carries no sysvol_files list")
    out: list[str] = []
    for entry in files:
        if not isinstance(entry, dict) or "relative_path" not in entry:
            raise ValueError("sysvol_files entry lacks relative_path")
        out.append(str(entry["relative_path"]))
    if len(out) != len({p.casefold() for p in out}):
        raise ValueError("Windows reported the same SYSVOL path twice")
    return out


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

    exact_keys = {
        "schema_version",
        "run_id",
        "target_name",
        "domain",
        "backup_id",
        "source_gpo_id",
        "owned_gpo_id",
        "import_succeeded",
        "report_links_to_count",
        "sysvol_path",
        "sysvol_files",
        "gpt_ini_text",
        "ad_attributes",
        "cleanup_succeeded",
        "cleanup_state_restored",
        "environment",
        "error",
    }
    checks: dict[str, bool] = {
        "result_schema_exact": set(result) == exact_keys
        and type(result.get("schema_version")) is int
        and result["schema_version"] == 1,
        "import_succeeded": result.get("import_succeeded") is True,
        "disposable_gpo_unlinked": type(result.get("report_links_to_count")) is int
        and result["report_links_to_count"] == 0,
        "cleanup_succeeded": result.get("cleanup_succeeded") is True,
        "cleanup_state_restored": result.get("cleanup_state_restored") is True,
        "harness_reported_no_error": result.get("error") is None,
        "member_server_host_role": _member(result.get("environment")),
        "raw_command_artifacts_complete": all(
            (run / "commands" / f"{n}.{s}.txt").is_file()
            for n in ("import", "report")
            for s in ("stdout", "stderr")
        )
        and (run / "builder.stdout.txt").is_file(),
    }
    violations = list(lane_environment_violations(result["environment"]))
    checks["environment_matches_frozen_spec"] = not violations

    error: str | None = None
    comparison: dict[str, Any] = {}
    try:
        observed = _observed_paths(result)
        planned = _fold(expected["sysvol_paths"])
        seen = _fold(observed)
        attributes = result.get("ad_attributes")
        if not isinstance(attributes, dict):
            raise ValueError("result carries no ad_attributes object")
        comparison = {
            "planned_sysvol_paths": planned,
            "observed_sysvol_paths": seen,
            "unplanned_files": [p for p in seen if p not in planned],
            "missing_files": [p for p in planned if p not in seen],
            "expected_machine_extension_names": expected["machine_extension_names"],
            "observed_machine_extension_names": attributes.get("gPCMachineExtensionNames"),
            "expected_user_extension_names": expected["user_extension_names"],
            "observed_user_extension_names": attributes.get("gPCUserExtensionNames"),
            "expected_version_half": expected["version_half"],
            "observed_version_number": attributes.get("versionNumber"),
        }
        # The two halves of completeness, kept as separate checks because they
        # fail for different reasons and a reader needs to know which happened.
        checks["plan_names_every_file_windows_wrote"] = not comparison["unplanned_files"]
        checks["windows_wrote_every_file_the_plan_names"] = not comparison["missing_files"]
        checks["machine_extension_names_exact"] = (
            attributes.get("gPCMachineExtensionNames") == expected["machine_extension_names"]
        )
        checks["user_extension_names_exact"] = (
            attributes.get("gPCUserExtensionNames") == expected["user_extension_names"]
        )
        packed = attributes.get("versionNumber")
        checks["gpt_version_moved_the_declared_half"] = type(packed) is int and (
            _version_half_matches(str(expected["version_half"]), packed)
        )
        # An undescribed GPO must produce no comment file: the negative half of
        # WI-058, which only an absence can state.
        checks["gpo_cmt_present_only_if_planned"] = (
            any(p.endswith("gpo.cmt") for p in seen) is bool(expected["expects_gpo_cmt"])
        )
        checks["candidate_identity_matches_manifest"] = (
            str(result.get("backup_id", "")).strip("{}").casefold()
            == str(expected["backup_id"]).strip("{}").casefold()
            and str(result.get("source_gpo_id", "")).strip("{}").casefold()
            == str(expected["gpo_id"]).strip("{}").casefold()
        )
        # The tree walked must be the directory's own idea of where this GPO
        # lives, and it must be the GPO this run owns.
        checks["sysvol_path_is_the_owned_gpos"] = (
            str(result.get("owned_gpo_id", "")).strip("{}").casefold()
            in str(result.get("sysvol_path", "")).casefold()
            and str(attributes.get("gPCFileSysPath", "")).casefold()
            == str(result.get("sysvol_path", "")).casefold()
        )
    except (KeyError, OSError, TypeError, ValueError) as exc:
        error = str(exc)
        for name in (
            "plan_names_every_file_windows_wrote",
            "windows_wrote_every_file_the_plan_names",
            "machine_extension_names_exact",
            "user_extension_names_exact",
            "gpt_version_moved_the_declared_half",
            "gpo_cmt_present_only_if_planned",
            "candidate_identity_matches_manifest",
            "sysvol_path_is_the_owned_gpos",
        ):
            checks[name] = False

    # WI-062: controller-side files are bound by (commit, path, sha256) from
    # the source-tree copy that ran -- no byte copy rides in the pack, since
    # git at the commit holds the bytes and assert_bound_source_bytes proved
    # tree, index and HEAD agree.
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
        "run_id": result["run_id"],
        "transport": "psdirect",
        "passed": all(checks.values()),
        "checks": checks,
        "comparison": comparison,
        "comparison_error": error,
        "harness_error": result.get("error"),
        "candidate": {
            path.relative_to(candidate_root).as_posix(): _sha(path)
            for path in sorted(candidate_root.rglob("*"))
            if path.is_file()
        },
        "candidate_delivery": {
            "controller_sha256": _sha(candidate),
            "guest_sha256": _sha(guest) if guest.is_file() else None,
        },
        "environment": result["environment"],
        "environment_violations": violations,
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
            p.relative_to(run).as_posix(): _sha(p)
            for p in sorted(run.rglob("*"))
            if p.is_file() and p.name != "verification.json"
        },
    }

    tag_outcome = None
    if verdict["passed"] and not args.no_tag:
        try:
            tag_outcome = tag_evidence_commit(repo, result["run_id"], commit)
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
