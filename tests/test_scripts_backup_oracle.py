"""Focused tests for the R10 Scripts metadata oracle."""

from __future__ import annotations

import importlib.util
import json
import runpy
import subprocess
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

_ROOT = Path(__file__).parents[1]
_FINALIZER = runpy.run_path(str(_ROOT / "scripts/windows-oracle/finalize_scripts_backup_run.py"))
_candidate_projection = cast(
    Callable[[Path], dict[str, object]], _FINALIZER["_candidate_projection"]
)
_rebackup_projection = cast(Callable[[Path], dict[str, object]], _FINALIZER["_rebackup_projection"])
_report_matches = cast(Callable[[Path, str, str, str], bool], _FINALIZER["_report_matches"])


def _builder() -> ModuleType:
    path = _ROOT / "scripts/plan-033/build-scripts-backup-candidate.py"
    spec = importlib.util.spec_from_file_location("r10_oracle_builder", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_candidate_and_rebackup_project_identically(tmp_path: Path) -> None:
    archive_path = tmp_path / "candidate.zip"
    archive_path.write_bytes(cast(Any, _builder()).build_bundle())
    extracted = tmp_path / "rebackup"
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(extracted)

    candidate = _candidate_projection(archive_path)
    rebackup = _rebackup_projection(extracted)
    assert candidate["files"] == rebackup["files"]
    assert candidate["machine_extension_pair"] == rebackup["machine_extension_pair"]
    assert candidate["machine_extension_pair"] == (
        "[{42B5FAAE-6536-11D2-AE5A-0000F87571E3}{40B6664F-4972-11D1-A7CA-0000F87571E3}]"
    )
    assert candidate["file_references"] == [
        "Machine/Scripts/psscripts.ini",
        "Machine/Scripts/scripts.ini",
    ]


def test_report_requires_names_and_parameters_in_expected_order(tmp_path: Path) -> None:
    report = tmp_path / "report.xml"
    report.write_text(
        '<GPO xmlns="http://www.microsoft.com/GroupPolicy/Settings">'
        '<Identifier><Identifier xmlns="http://www.microsoft.com/GroupPolicy/Types">{abc}</Identifier>'
        '<Domain xmlns="http://www.microsoft.com/GroupPolicy/Types">example.test</Domain></Identifier>'
        "<Name>target</Name><Computer><ExtensionData><Extension>"
        '<Script xmlns="http://www.microsoft.com/GroupPolicy/Settings/Scripts">'
        "<Command>zz-studio-marker.cmd</Command><Parameters>/c alpha beta</Parameters>"
        "<Type>Startup</Type><Order>1</Order><RunOrder>RunPSFirst</RunOrder></Script>"
        '<Script xmlns="http://www.microsoft.com/GroupPolicy/Settings/Scripts">'
        "<Command>zz-studio-second.cmd</Command><Type>Startup</Type><Order>2</Order><RunOrder>RunPSFirst</RunOrder></Script>"
        '<Script xmlns="http://www.microsoft.com/GroupPolicy/Settings/Scripts">'
        "<Command>zz-studio-marker.ps1</Command><Parameters>-Mode Alpha</Parameters>"
        "<Type>Startup</Type><Order>0</Order><RunOrder>RunPSFirst</RunOrder></Script>"
        "</Extension></ExtensionData></Computer></GPO>",
        encoding="utf-8",
    )
    assert _report_matches(report, "abc", "target", "example.test")
    assert not _report_matches(report, "def", "target", "example.test")


def test_lane_binds_executed_and_semantic_sources() -> None:
    assert set(_FINALIZER["DEPLOYED_FILES"]) == {"run-scripts-backup-import.ps1"}
    assert set(_FINALIZER["LOCAL_FILES"]) == {
        "run-scripts-backup-oracle.sh",
        "finalize_scripts_backup_run.py",
        "build-scripts-backup-candidate.py",
        "psdirect.ps1",
        "export.py",
        "script_policy.py",
        "canonical.py",
        "gpp.py",
        "model.py",
        "registry_pol.py",
        "validation.py",
        "oracle_evidence.py",
        "xml_safety.py",
    }


def test_guest_harness_has_no_endpoint_or_gui_execution_claim() -> None:
    harness = (_ROOT / "scripts/windows-oracle/run-scripts-backup-import.ps1").read_text(
        encoding="utf-8"
    )
    folded = harness.casefold()
    assert "get-gporeport" in folded
    assert "backup-gpo" in folded
    assert "remove-gpo" in folded
    assert "start-process" not in folded
    assert "invoke-expression" not in folded
    assert "mmc.exe" not in folded


def test_main_accepts_complete_synthetic_evidence_pack(tmp_path: Path, monkeypatch: Any) -> None:
    candidate_root = tmp_path / "candidate"
    candidate_root.mkdir()
    candidate_zip = candidate_root / "studio-scripts-backup.zip"
    candidate_zip.write_bytes(cast(Any, _builder()).build_bundle())
    candidate_projection = _candidate_projection(candidate_zip)
    run = tmp_path / "run"
    with zipfile.ZipFile(candidate_zip) as archive:
        archive.extractall(run / "rebackup")
    backup_xml = next((run / "rebackup").glob("*/Backup.xml"))
    native_wildcards = b"".join(
        f'<FSObjectFile bkp:Path="{path}" />'.encode() for path in _FINALIZER["_NATIVE_WILDCARDS"]
    )
    backup_xml.write_bytes(
        backup_xml.read_bytes()
        .replace(
            b"{22222222-3333-4444-5555-666666666666}", b"{aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}"
        )
        .replace(b"synthetic.test", b"example.test")
        .replace(b"zz-studio-evidence-10-scripts", b"target")
        .replace(
            b"</GroupPolicyExtension></GroupPolicyObject>",
            native_wildcards + b"</GroupPolicyExtension></GroupPolicyObject>",
        )
    )
    report = (
        '<GPO xmlns="http://www.microsoft.com/GroupPolicy/Settings">'
        '<Identifier><Identifier xmlns="http://www.microsoft.com/GroupPolicy/Types">'
        "{aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}</Identifier>"
        '<Domain xmlns="http://www.microsoft.com/GroupPolicy/Types">example.test</Domain>'
        "</Identifier><Name>target</Name><Computer><ExtensionData><Extension>"
        '<Script xmlns="http://www.microsoft.com/GroupPolicy/Settings/Scripts">'
        "<Command>zz-studio-marker.cmd</Command><Parameters>/c alpha beta</Parameters>"
        "<Type>Startup</Type><Order>1</Order><RunOrder>RunPSFirst</RunOrder></Script>"
        '<Script xmlns="http://www.microsoft.com/GroupPolicy/Settings/Scripts">'
        "<Command>zz-studio-second.cmd</Command><Type>Startup</Type><Order>2</Order>"
        "<RunOrder>RunPSFirst</RunOrder></Script>"
        '<Script xmlns="http://www.microsoft.com/GroupPolicy/Settings/Scripts">'
        "<Command>zz-studio-marker.ps1</Command><Parameters>-Mode Alpha</Parameters>"
        "<Type>Startup</Type><Order>0</Order><RunOrder>RunPSFirst</RunOrder></Script>"
        "</Extension></ExtensionData></Computer></GPO>"
    )
    (run / "report.xml").write_text(report, encoding="utf-8")
    (run / "candidate.zip").write_bytes(candidate_zip.read_bytes())
    (run / "builder.stdout.txt").write_text("synthetic builder output\n", encoding="utf-8")
    commands = run / "commands"
    commands.mkdir()
    for command in ("import", "report", "backup"):
        for stream in ("stdout", "stderr"):
            (commands / f"{command}.{stream}.txt").write_bytes(b"")
    result = {
        "schema_version": 1,
        "run_id": "scripts-r10-test",
        "target_name": "target",
        "domain": "example.test",
        "backup_id": candidate_projection["backup_id"],
        "source_gpo_id": candidate_projection["source_gpo_id"],
        "owned_gpo_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "import_succeeded": True,
        "report_succeeded": True,
        "report_links_to_count": 0,
        "rebackup_succeeded": True,
        "cleanup_succeeded": True,
        "cleanup_state_restored": True,
        "environment": {"computer_system_domain_role": 3},
        "error": None,
    }
    (run / "result.json").write_text(json.dumps(result), encoding="utf-8")
    (run / "deployed").mkdir()
    for name, relative in _FINALIZER["DEPLOYED_FILES"].items():
        (run / "deployed" / name).write_bytes((_ROOT / relative).read_bytes())
    for name, relative in _FINALIZER["LOCAL_FILES"].items():
        (run / name).write_bytes((_ROOT / relative).read_bytes())
    finalizer_globals = _FINALIZER["main"].__globals__
    # This semantic-pack test supplies synthetic Git; real byte refusals have subprocess tests.
    monkeypatch.setitem(finalizer_globals, "assert_bound_source_bytes", lambda *_: None)
    monkeypatch.setitem(finalizer_globals, "lane_environment_violations", lambda _: ())

    def fake_run(*args: Any, **kwargs: Any) -> Any:
        stdout = "" if "status" in args[0] else "abc123\n"
        return subprocess.CompletedProcess(args[0], 0, stdout=stdout, stderr="")

    monkeypatch.setitem(finalizer_globals, "subprocess", type("P", (), {"run": fake_run}))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "finalizer",
            str(run),
            "--candidate-root",
            str(candidate_root),
            "--repo-root",
            str(_ROOT),
            "--no-tag",
        ],
    )
    assert _FINALIZER["main"]() == 0
    assert json.loads((run / "verification.json").read_text())["passed"] is True
    assert json.loads((run / "verification.json").read_text())["transport"] == "psdirect"
    (run / "candidate.zip").write_bytes(candidate_zip.read_bytes() + b"corrupt")
    assert _FINALIZER["main"]() == 1
    assert (
        json.loads((run / "verification.json").read_text())["checks"]["candidate_delivered_intact"]
        is False
    )
    (run / "candidate.zip").write_bytes(candidate_zip.read_bytes())
    (run / "verification.json").unlink()

    def reject_tag(*args: Any) -> str:
        raise _FINALIZER["OracleEvidenceError"]("synthetic tag refusal")

    monkeypatch.setitem(finalizer_globals, "tag_evidence_commit", reject_tag)
    monkeypatch.setattr(sys, "argv", sys.argv[:-1])
    assert _FINALIZER["main"]() == 1
    assert not (run / "verification.json").exists()

    user_scripts = backup_xml.parent / "DomainSysvol/GPO/User/Scripts"
    user_scripts.mkdir(parents=True)
    (user_scripts / "unexpected.cmd").write_bytes(b"echo synthetic")
    assert _FINALIZER["main"]() == 1
    assert (
        json.loads((run / "verification.json").read_text())["checks"][
            "windows_rebackup_metadata_exact"
        ]
        is False
    )


def test_candidate_rejects_arbitrary_locationless_scripts_reference(tmp_path: Path) -> None:
    original = tmp_path / "original.zip"
    original.write_bytes(cast(Any, _builder()).build_bundle())
    corrupt = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(corrupt, "w") as destination:
        for info in source.infolist():
            data = source.read(info)
            if info.filename.endswith("/Backup.xml"):
                data = data.replace(
                    b"</GroupPolicyExtension></GroupPolicyObject>",
                    b'<FSObjectFile bkp:Path="unexpected" />'
                    b"</GroupPolicyExtension></GroupPolicyObject>",
                )
            destination.writestr(info, data)
    with pytest.raises(ValueError, match="location-less"):
        _candidate_projection(corrupt)


def test_candidate_rejects_duplicate_zip_members(tmp_path: Path) -> None:
    candidate = tmp_path / "duplicate.zip"
    candidate.write_bytes(cast(Any, _builder()).build_bundle())
    with zipfile.ZipFile(candidate, "a") as archive:
        payload = archive.read("manifest.xml")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("manifest.xml", payload)
    with pytest.raises(ValueError, match="repeats an archive member"):
        _candidate_projection(candidate)
