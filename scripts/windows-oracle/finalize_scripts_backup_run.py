#!/usr/bin/env python3
"""Finalize the Plan 034 R10 Scripts metadata import/report/rebackup lane."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element

from gpo_studio.oracle_evidence import (
    OracleEvidenceError,
    assert_bound_source_bytes,
    lane_environment_violations,
    manifest_bound_source,
    tag_evidence_commit,
)
from gpo_studio.xml_safety import parse_xml_bounded

_NS = "http://www.microsoft.com/GroupPolicy/GPOOperations"
_MANIFEST_NS = "http://www.microsoft.com/GroupPolicy/GPOOperations/Manifest"
_PAIR = "[{42B5FAAE-6536-11D2-AE5A-0000F87571E3}{40B6664F-4972-11D1-A7CA-0000F87571E3}]"
_FILES = ("Machine/Scripts/scripts.ini", "Machine/Scripts/psscripts.ini")
_NATIVE_WILDCARDS = (
    r"%GPO_MACH_FSPATH%\Scripts\Startup\*",
    r"%GPO_MACH_FSPATH%\Scripts\Shutdown\*",
    r"%GPO_USER_FSPATH%\Scripts\Logon\*",
    r"%GPO_USER_FSPATH%\Scripts\Logoff\*",
)
DEPLOYED_FILES = {
    "run-scripts-backup-import.ps1": "scripts/windows-oracle/run-scripts-backup-import.ps1"
}
LOCAL_FILES = {
    "run-scripts-backup-oracle.sh": "scripts/windows-oracle/run-scripts-backup-oracle.sh",
    "finalize_scripts_backup_run.py": "scripts/windows-oracle/finalize_scripts_backup_run.py",
    "build-scripts-backup-candidate.py": "scripts/plan-033/build-scripts-backup-candidate.py",
    "psdirect.ps1": "scripts/windows-oracle/psdirect.ps1",
    "export.py": "src/gpo_studio/export.py",
    "script_policy.py": "src/gpo_studio/script_policy.py",
    "canonical.py": "src/gpo_studio/canonical.py",
    "gpp.py": "src/gpo_studio/gpp.py",
    "model.py": "src/gpo_studio/model.py",
    "registry_pol.py": "src/gpo_studio/registry_pol.py",
    "validation.py": "src/gpo_studio/validation.py",
    "oracle_evidence.py": "src/gpo_studio/oracle_evidence.py",
    "xml_safety.py": "src/gpo_studio/xml_safety.py",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate_projection(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len({name.casefold() for name in names}):
            raise ValueError("candidate repeats an archive member")
        roots = {n.split("/")[0] for n in names if n.endswith("/Backup.xml")}
        if len(roots) != 1:
            raise ValueError("candidate must contain exactly one Backup.xml")
        root = roots.pop()
        xml = parse_xml_bounded(archive.read(f"{root}/Backup.xml"), max_size=8_388_608)
        manifest = parse_xml_bounded(archive.read("manifest.xml"), max_size=1_048_576)
        instances = manifest.findall(f"{{{_MANIFEST_NS}}}BackupInst")
        if len(instances) != 1:
            raise ValueError("candidate manifest must contain exactly one backup")
        prefix = f"{root}/DomainSysvol/GPO/"
        script_files = sorted(
            name.removeprefix(prefix)
            for name in names
            if name.casefold().startswith(prefix.casefold())
            and "/scripts/" in name.casefold()
            and not name.endswith("/")
        )
        contents = {rel: archive.read(prefix + rel) for rel in _FILES}
    projection = _xml_projection(xml, contents, script_files)
    projection.update(
        backup_id=instances[0].findtext(f"{{{_MANIFEST_NS}}}ID"),
        source_gpo_id=instances[0].findtext(f"{{{_MANIFEST_NS}}}GPOGuid"),
    )
    if (
        str(projection["backup_id"]).strip("{}").casefold() != root.strip("{}").casefold()
        or str(projection["source_gpo_id"]).strip("{}").casefold()
        != str(projection["gpo_id"]).strip("{}").casefold()
    ):
        raise ValueError("candidate identity differs between manifest, directory and core")
    return projection


def _rebackup_projection(path: Path) -> dict[str, Any]:
    xmls = list(path.glob("*/Backup.xml"))
    if len(xmls) != 1:
        raise ValueError("rebackup must contain exactly one Backup.xml")
    base = xmls[0].parent / "DomainSysvol" / "GPO"
    script_files = sorted(
        item.relative_to(base).as_posix()
        for item in base.rglob("*")
        if item.is_file()
        and "scripts" in {part.casefold() for part in item.relative_to(base).parts[:-1]}
    )
    by_name = {name.casefold(): base / Path(name) for name in script_files}
    return _xml_projection(
        parse_xml_bounded(xmls[0].read_bytes(), max_size=8_388_608),
        {rel: by_name[rel.casefold()].read_bytes() for rel in _FILES},
        script_files,
    )


def _xml_projection(
    root: Element, contents: dict[str, bytes], script_files: list[str]
) -> dict[str, Any]:
    core = root.find(f".//{{{_NS}}}GroupPolicyCoreSettings")
    if core is None:
        raise ValueError("Backup.xml lacks core settings")
    pair = core.findtext(f"{{{_NS}}}MachineExtensionGuids")
    scripts_id = "{42B5FAAE-6536-11D2-AE5A-0000F87571E3}"
    extensions = [
        extension
        for extension in root.findall(f".//{{{_NS}}}GroupPolicyExtension")
        if extension.attrib.get(f"{{{_NS}}}ID", "").casefold() == scripts_id.casefold()
    ]
    if len(extensions) != 1:
        raise ValueError("Backup.xml must have exactly one Scripts extension")
    location_key = f"{{{_NS}}}Location"
    path_key = f"{{{_NS}}}Path"
    file_nodes = extensions[0].findall(f"{{{_NS}}}FSObjectFile")
    refs = [
        e.attrib[location_key].replace("\\", "/").removeprefix("DomainSysvol/GPO/")
        for e in file_nodes
        if location_key in e.attrib
    ]
    wildcards = [e.attrib.get(path_key) for e in file_nodes if location_key not in e.attrib]
    if any(path is None or path not in _NATIVE_WILDCARDS for path in wildcards) or len(
        wildcards
    ) != len(set(wildcards)):
        raise ValueError("Backup.xml has invalid location-less Scripts references")
    if len(refs) != len(set(refs)):
        raise ValueError("Backup.xml repeats a Scripts file reference")
    return {
        "machine_extension_pair": pair,
        "user_extension_pair": core.findtext(f"{{{_NS}}}UserExtensionGuids"),
        "file_references": sorted(refs),
        "script_files": script_files,
        "wildcard_references": sorted(wildcards),
        "gpo_id": core.findtext(f"{{{_NS}}}ID"),
        "domain": core.findtext(f"{{{_NS}}}Domain"),
        "display_name": core.findtext(f"{{{_NS}}}DisplayName"),
        "files": {k: hashlib.sha256(v).hexdigest() for k, v in contents.items()},
    }


def _report_matches(path: Path, owned_id: str, target: str, domain: str) -> bool:
    root = parse_xml_bounded(path.read_bytes(), max_size=8_388_608)
    scripts_ns = "http://www.microsoft.com/GroupPolicy/Settings/Scripts"
    settings_ns = "http://www.microsoft.com/GroupPolicy/Settings"
    types_ns = "http://www.microsoft.com/GroupPolicy/Types"
    identifier = root.find(f"{{{settings_ns}}}Identifier")
    if identifier is None:
        return False
    if (
        identifier.findtext(f"{{{types_ns}}}Identifier", "").strip("{}").casefold()
        != owned_id.strip("{}").casefold()
        or identifier.findtext(f"{{{types_ns}}}Domain", "").casefold() != domain.casefold()
        or root.findtext(f"{{{settings_ns}}}Name") != target
    ):
        return False
    if root.findall(f".//{{{settings_ns}}}LinksTo"):
        return False
    computer = root.find(f"{{{settings_ns}}}Computer")
    if computer is None:
        return False
    extensions = computer.findall(f"{{{settings_ns}}}ExtensionData/{{{settings_ns}}}Extension")
    owners = [extension for extension in extensions if extension.findall(f"{{{scripts_ns}}}Script")]
    if len(owners) != 1:
        return False
    scripts = owners[0].findall(f"{{{scripts_ns}}}Script")
    if len(root.findall(f".//{{{scripts_ns}}}Script")) != len(scripts):
        return False
    rows = []
    for script in scripts:
        rows.append(
            (
                script.findtext(f"{{{scripts_ns}}}Command"),
                script.findtext(f"{{{scripts_ns}}}Parameters"),
                script.findtext(f"{{{scripts_ns}}}Type"),
                script.findtext(f"{{{scripts_ns}}}Order"),
                script.findtext(f"{{{scripts_ns}}}RunOrder"),
            )
        )
    return rows == [
        ("zz-studio-marker.cmd", "/c alpha beta", "Startup", "1", "RunPSFirst"),
        ("zz-studio-second.cmd", None, "Startup", "2", "RunPSFirst"),
        ("zz-studio-marker.ps1", "-Mode Alpha", "Startup", "0", "RunPSFirst"),
    ]


def _member(environment: object) -> bool:
    return (
        isinstance(environment, dict)
        and type(environment.get("computer_system_domain_role")) is int
        and environment["computer_system_domain_role"] == 3
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--no-tag", action="store_true")
    args = parser.parse_args()
    run = args.run_dir.resolve()
    candidate = args.candidate_root.resolve() / "studio-scripts-backup.zip"
    repo = args.repo_root.resolve()
    try:
        assert_bound_source_bytes(repo, {**DEPLOYED_FILES, **LOCAL_FILES}.values())
    except OracleEvidenceError as exc:
        print(f"finalize refused: {exc}", file=sys.stderr)
        return 1
    result = json.loads((run / "result.json").read_text(encoding="utf-8-sig"))
    exact_keys = {
        "schema_version",
        "run_id",
        "target_name",
        "domain",
        "backup_id",
        "source_gpo_id",
        "owned_gpo_id",
        "import_succeeded",
        "report_succeeded",
        "report_links_to_count",
        "rebackup_succeeded",
        "cleanup_succeeded",
        "cleanup_state_restored",
        "environment",
        "error",
    }
    checks = {
        "result_schema_exact": set(result) == exact_keys
        and type(result.get("schema_version")) is int
        and result["schema_version"] == 1,
        "import_succeeded": result.get("import_succeeded") is True,
        "report_succeeded": result.get("report_succeeded") is True,
        "disposable_gpo_unlinked": type(result.get("report_links_to_count")) is int
        and result["report_links_to_count"] == 0,
        "rebackup_succeeded": result.get("rebackup_succeeded") is True,
        "cleanup_succeeded": result.get("cleanup_succeeded") is True,
        "cleanup_state_restored": result.get("cleanup_state_restored") is True,
        "harness_reported_no_error": result.get("error") is None,
        "member_server_host_role": _member(result.get("environment")),
        "raw_command_artifacts_complete": all(
            (run / "commands" / f"{n}.{s}.txt").is_file()
            for n in ("import", "report", "backup")
            for s in ("stdout", "stderr")
        )
        and (run / "builder.stdout.txt").is_file(),
    }
    violations = list(lane_environment_violations(result["environment"]))
    checks["environment_matches_frozen_spec"] = not violations
    error = None
    original = {}
    rebackup = {}
    try:
        original = _candidate_projection(candidate)
        rebackup = _rebackup_projection(run / "rebackup")
        checks["candidate_identity_matches_manifest"] = (
            result.get("backup_id") == original["backup_id"]
            and result.get("source_gpo_id") == original["source_gpo_id"]
        )
        expected_files = sorted(name.casefold() for name in _FILES)
        checks["candidate_metadata_exact"] = (
            original["machine_extension_pair"] == _PAIR
            and original["user_extension_pair"] in (None, "")
            and sorted(name.casefold() for name in original["file_references"]) == expected_files
            and sorted(name.casefold() for name in original["script_files"]) == expected_files
            and original["wildcard_references"] == []
        )
        checks["windows_rebackup_ini_bytes_exact"] = rebackup["files"] == original["files"]
        checks["windows_rebackup_metadata_exact"] = (
            rebackup["machine_extension_pair"] == _PAIR
            and rebackup["user_extension_pair"] in (None, "")
            and sorted(name.casefold() for name in rebackup["file_references"]) == expected_files
            and sorted(name.casefold() for name in rebackup["script_files"]) == expected_files
            and sorted(name.casefold() for name in rebackup["wildcard_references"])
            == sorted(name.casefold() for name in _NATIVE_WILDCARDS)
        )
        checks["rebackup_identity_matches_owned_target"] = (
            str(rebackup["gpo_id"]).strip("{}").casefold()
            == str(result.get("owned_gpo_id", "")).strip("{}").casefold()
            and rebackup["display_name"] == result.get("target_name")
            and str(rebackup["domain"]).casefold() == str(result.get("domain")).casefold()
        )
        checks["report_exposes_exact_scripts_metadata"] = _report_matches(
            run / "report.xml",
            str(result.get("owned_gpo_id", "")),
            str(result.get("target_name", "")),
            str(result.get("domain", "")),
        )
    except (KeyError, OSError, ValueError, zipfile.BadZipFile) as exc:
        error = str(exc)
        checks.update(
            candidate_metadata_exact=False,
            candidate_identity_matches_manifest=False,
            windows_rebackup_ini_bytes_exact=False,
            windows_rebackup_metadata_exact=False,
            rebackup_identity_matches_owned_target=False,
            report_exposes_exact_scripts_metadata=False,
        )
    # WI-062: controller-side files are bound by (commit, path, sha256) from
    # the source-tree copy that ran -- no byte copy rides in the pack, since
    # git at the commit holds the bytes and assert_bound_source_bytes proved
    # tree, index and HEAD agree.
    bound_local = manifest_bound_source(repo, LOCAL_FILES)
    source_hashes = {name: entry["sha256"] for name, entry in bound_local.items()}
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
        "comparison_error": error,
        "harness_error": result.get("error"),
        "candidate_delivery": {
            "controller_sha256": _sha(candidate),
            "guest_sha256": _sha(guest) if guest.is_file() else None,
        },
        "original_projection": original,
        "rebackup_projection": rebackup,
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
