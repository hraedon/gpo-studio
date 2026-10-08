"""Focused tests for the Plan 034 report-parity lane (candidate and finalizer).

The lane grades comparisons, so the tests that matter prove each check can
fail. A simulated run is assembled from the candidate itself: each case's
"fresh" report is the capture-time ``gpreport.xml`` (what Windows should
regenerate), and the guest-authored case is a Studio-written native backup of
the four authored values with a hand-written report. The control asserts that
run grades clean; every mutation after it breaks exactly one thing.
"""

from __future__ import annotations

import importlib.util
import io
import json
import shutil
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from gpo_studio.model import GPO, RegistrySetting
from gpo_studio.report_parity import (
    Inventory,
    InventoryItem,
    inventory_from_json,
    windows_inventory,
)

ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "scripts/plan-033/build-report-parity-candidate.py"
FINALIZER_PATH = ROOT / "scripts/windows-oracle/finalize_report_parity_run.py"
DRIVER_PATH = ROOT / "scripts/windows-oracle/run-report-parity-oracle.sh"
GUEST_PATH = ROOT / "scripts/windows-oracle/run-report-parity.ps1"
ENVIRONMENT_SOURCE = ROOT / "docs/plan-033/wp1b-evidence/wi062-20260910/publication/result.json"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BUILDER = cast(Any, _load(BUILDER_PATH, "report_parity_builder"))
FINALIZER = cast(Any, _load(FINALIZER_PATH, "report_parity_finalizer"))


@pytest.fixture(scope="module")
def candidate(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("candidate")
    BUILDER.build(out)
    return out


def _expected(candidate: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((candidate / "expected.json").read_text("utf-8")))


# ---------------------------------------------------------------------------
# The candidate
# ---------------------------------------------------------------------------


def test_the_builder_is_deterministic(tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    for out in (first, second):
        subprocess.run([sys.executable, str(BUILDER_PATH), str(out)], check=True,
                       capture_output=True)
    for name in FINALIZER.REQUIRED_CANDIDATE_FILES:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_every_corpus_backup_is_import_ready_and_packaged(candidate: Path) -> None:
    expected = _expected(candidate)
    assert expected["excluded"] == []
    assert len(expected["cases"]) == len(BUILDER.corpus()) == 27
    with zipfile.ZipFile(candidate / "report-parity-cases.zip") as archive:
        tops = {PathParts(n) for n in archive.namelist()}
    assert tops == {c["case_id"] for c in expected["cases"]}


def PathParts(name: str) -> str:  # noqa: N802 - tiny local helper
    parts = name.split("/")
    assert parts[0] == "cases"
    return parts[1]


def test_the_expectation_is_the_offline_differs_own_answer(candidate: Path) -> None:
    pins = cast(Any, _load(ROOT / "tests/test_report_parity.py", "report_parity_pins"))
    EXPECTED_KNOWN = pins.EXPECTED_KNOWN  # noqa: N806 - the pinned table's own name

    by_source = {c["source"]: c for c in _expected(candidate)["cases"]}
    assert set(by_source) == set(EXPECTED_KNOWN)
    for source, case in by_source.items():
        assert set(case["expected_known"]) == EXPECTED_KNOWN[source], source


def test_only_the_sanitized_placeholder_descriptor_is_replaced(candidate: Path) -> None:
    from gpo_studio.export import _DOMAIN_NEUTRAL_SECURITY_DESCRIPTOR

    assert BUILDER.DOMAIN_NEUTRAL_SD == _DOMAIN_NEUTRAL_SECURITY_DESCRIPTOR
    for case in _expected(candidate)["cases"]:
        native = case["source"].startswith("tests/fixtures/native-gpp-gpmc/")
        assert case["transformations"] == (
            ["restore-importable-security-descriptor"] if native else []
        ), case["case_id"]


def test_import_readiness_names_the_missing_piece(tmp_path: Path) -> None:
    source = ROOT / "tests/fixtures/native-gpp-gpmc/WI01A-DriveMaps-GPMC"
    case = tmp_path / "case"
    shutil.copytree(source, case)
    assert BUILDER.import_readiness(case) == (
        "{E9F0A681-9B36-419E-A16E-C2C59DC44DAD}", "{F0197E25-3E19-4835-B296-3C35DC069635}",
    )
    (case / "{E9F0A681-9B36-419E-A16E-C2C59DC44DAD}" / "bkupInfo.xml").unlink()
    assert "bkupInfo.xml is missing" in BUILDER.import_readiness(case)
    (case / "manifest.xml").write_text("<Backups/>", encoding="utf-8")
    assert "native single-backup" in BUILDER.import_readiness(case)
    (case / "manifest.xml").write_text("<", encoding="utf-8")
    assert "unreadable" in BUILDER.import_readiness(case)


def test_a_backup_whose_core_id_disagrees_is_not_import_ready(tmp_path: Path) -> None:
    source = ROOT / "tests/fixtures/native-gpp-gpmc/WI01A-DriveMaps-GPMC"
    case = tmp_path / "case"
    shutil.copytree(source, case)
    backup_xml = case / "{E9F0A681-9B36-419E-A16E-C2C59DC44DAD}" / "Backup.xml"
    backup_xml.write_bytes(backup_xml.read_bytes().replace(
        b"{F0197E25-3E19-4835-B296-3C35DC069635}", b"{00000000-0000-0000-0000-000000000000}"
    ))
    assert "does not match" in BUILDER.import_readiness(case)


def test_the_authored_spec_matches_the_guest_script() -> None:
    """Two independent copies of the four values; they must agree."""
    script = GUEST_PATH.read_text(encoding="utf-8")
    assert f"$authoredKey = '{BUILDER.AUTHORED_KEY}'" in script
    for side, name, kind, value in BUILDER.AUTHORED_VALUES:
        hive = "HKLM" if side == "computer" else "HKCU"
        ps_type = "String" if kind == "String" else "DWord"
        rendered = f"'{value}'" if kind == "String" else value
        assert f"Hive = '{hive}'; Name = '{name}'" in script
        line = next(line for line in script.splitlines() if f"Name = '{name}'" in line)
        assert f"Type = '{ps_type}'" in line and f"Value = {rendered}" in line


# ---------------------------------------------------------------------------
# A simulated run, graded by the real finalizer
# ---------------------------------------------------------------------------

_AUTHORED_GUID = "6a0e5d2c-6c1b-4f43-9a55-1c8f2d7e4b10"


def _authored_report(values: list[tuple[str, str, str]]) -> bytes:
    def side(name: str, rows: list[tuple[str, str]]) -> str:
        settings = "".join(
            f"<q:RegistrySetting><q:KeyPath>{BUILDER.AUTHORED_KEY}</q:KeyPath>"
            f"<q:AdmSetting>false</q:AdmSetting><q:Value><q:Name>{n}</q:Name>{v}</q:Value>"
            "</q:RegistrySetting>"
            for n, v in rows
        )
        return (
            f"<{name}><ExtensionData><Extension "
            'xmlns:q="http://www.microsoft.com/GroupPolicy/Settings/Registry" '
            f'xsi:type="q:RegistrySettings">{settings}<q:Blocked>false</q:Blocked>'
            f"</Extension><Name>Registry</Name></ExtensionData></{name}>"
        )

    computer = [(n, v) for s, n, v in values if s == "computer"]
    user = [(n, v) for s, n, v in values if s == "user"]
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<GPO xmlns="http://www.microsoft.com/GroupPolicy/Settings" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        '<Identifier><Identifier xmlns="http://www.microsoft.com/GroupPolicy/Types">'
        f"{{{_AUTHORED_GUID.upper()}}}</Identifier></Identifier>"
        + side("Computer", computer) + side("User", user) + "</GPO>"
    ).encode()


def _authored_values() -> list[tuple[str, str, str]]:
    # In the order the simulated backup's Registry.pol holds them: Studio's
    # export writes it, and it sorts. On the estate Windows writes both the
    # file and the report, and the corpus shows the report follows file order.
    return sorted(
        (side, name, f"<q:{kind}>{value}</q:{kind}>")
        for side, name, kind, value in BUILDER.AUTHORED_VALUES
    )


def _authored_backup(target: Path, report: bytes) -> None:
    """A native backup of the authored GPO, as Backup-GPO would leave it."""
    from gpo_studio.export import gpmc_backup_bundle

    settings = tuple(
        RegistrySetting(
            id=f"a{i}", side=side, hive="HKLM" if side == "computer" else "HKCU",  # type: ignore[arg-type]
            key=BUILDER.AUTHORED_KEY, value_name=name,
            registry_type="REG_SZ" if kind == "String" else "REG_DWORD",
            value=value if kind == "String" else int(value),
        )
        for i, (side, name, kind, value) in enumerate(BUILDER.AUTHORED_VALUES)
    )
    bundle = gpmc_backup_bundle(GPO(guid=_AUTHORED_GUID, name="authored", settings=settings))
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        archive.extractall(target)
    (backup_root,) = [p for p in target.iterdir() if p.is_dir()]
    (backup_root / "gpreport.xml").write_bytes(report)


def _simulated_run(candidate: Path, run: Path) -> dict[str, Any]:
    expected = _expected(candidate)
    environment = json.loads(ENVIRONMENT_SOURCE.read_text("utf-8-sig"))["environment"]
    (run / "reports").mkdir(parents=True)
    (run / "deployed").mkdir()
    shutil.copy(GUEST_PATH, run / "deployed" / GUEST_PATH.name)
    shutil.copy(candidate / "report-parity-cases.zip", run / "candidate.zip")
    (run / "builder.stdout.txt").write_text("log", encoding="utf-8")
    cases = []
    for case in expected["cases"]:
        source = ROOT / case["source"] / case["backup_id"] / "gpreport.xml"
        report = run / "reports" / f"{case['case_id']}.xml"
        shutil.copy(source, report)
        commands = run / "commands" / case["case_id"]
        commands.mkdir(parents=True)
        for name in ("import", "report"):
            for stream in ("stdout", "stderr"):
                (commands / f"{name}.{stream}.txt").write_text("", encoding="utf-8")
        cases.append({
            "case_id": case["case_id"], "target_name": "zz-studio-rp-x",
            "backup_id": case["backup_id"], "source_gpo_id": case["source_gpo_id"],
            "owned_gpo_id": str(uuid.uuid4()), "import_succeeded": True,
            "report_file": f"reports/{case['case_id']}.xml",
            "report_sha256": FINALIZER._sha(report), "report_links_to_count": 0,
            "cleanup_succeeded": True, "absence_confirmed": True, "error": None,
        })
    authored_report = _authored_report(_authored_values())
    (run / "reports" / "authored.xml").write_bytes(authored_report)
    _authored_backup(run / "authored-backup", authored_report)
    commands = run / "commands" / "authored"
    commands.mkdir(parents=True)
    for name in ("set", "backup", "report"):
        for stream in ("stdout", "stderr"):
            (commands / f"{name}.{stream}.txt").write_text("", encoding="utf-8")
    result = {
        "schema_version": 1, "run_id": "report-parity-20261008000000-1234",
        "domain": "lab.test", "cases": cases,
        "authored": {
            "target_name": "zz-studio-rp-x-authored", "owned_gpo_id": _AUTHORED_GUID,
            "values_set": True, "backup_succeeded": True, "backup_id": "{X}",
            "backup_dir": "authored-backup", "report_file": "reports/authored.xml",
            "report_sha256": FINALIZER._sha(run / "reports" / "authored.xml"),
            "report_links_to_count": 0, "cleanup_succeeded": True,
            "absence_confirmed": True, "error": None,
        },
        "cleanup_state_restored": True, "environment": environment, "error": None,
    }
    _write_result(run, result)
    return result


def _write_result(run: Path, result: dict[str, Any]) -> None:
    (run / "result.json").write_text(json.dumps(result), encoding="utf-8-sig")


def _finalize(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, bool]:
    # Bound-source bytes are the committed tree's concern, not this test's:
    # the grading is what is under test here.
    monkeypatch.setattr(FINALIZER, "assert_bound_source_bytes", lambda *_: None)
    monkeypatch.setattr(sys, "argv", [
        "finalize", str(run), "--candidate-root", str(candidate),
        "--repo-root", str(ROOT), "--no-tag",
    ])
    FINALIZER.main()
    verdict = json.loads((run / "verification.json").read_text("utf-8"))
    checks = cast(dict[str, bool], verdict["checks"])
    # The working tree's cleanliness is not a property of the simulated run.
    checks.pop("source_tree_clean")
    return checks


@pytest.fixture()
def run(candidate: Path, tmp_path: Path) -> Path:
    path = tmp_path / "run"
    _simulated_run(candidate, path)
    return path


def test_the_simulated_run_passes_every_check(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control. Without it every mutation below could pass for the wrong reason."""
    checks = _finalize(run, candidate, monkeypatch)
    assert all(checks.values()), {k: v for k, v in checks.items() if not v}
    verdict = json.loads((run / "verification.json").read_text("utf-8"))
    assert verdict["comparison_error"] is None
    power = verdict["comparison"]["cases"]["native-WI01A-Power-GPMC"]
    assert power["known"]["adapter-root-unknowns-dropped"]["work_item"] == "WI-072"


def test_a_setting_missing_from_the_fresh_report_fails_the_case(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = run / "reports" / "native-WI01A-DriveMaps-GPMC.xml"
    text = report.read_bytes().decode("utf-16")
    start = text.index("<q1:Drive ")
    end = text.index("</q1:Drive>", start) + len("</q1:Drive>")
    report.write_bytes((text[:start] + text[end:]).encode("utf-16"))
    result = json.loads((run / "result.json").read_text("utf-8-sig"))
    for case in result["cases"]:
        if case["case_id"] == "native-WI01A-DriveMaps-GPMC":
            case["report_sha256"] = FINALIZER._sha(report)
    _write_result(run, result)
    checks = _finalize(run, candidate, monkeypatch)
    assert checks["every_case_studio_matches_fresh_report"] is False
    assert checks["fresh_reports_delivered_intact"] is True


def test_a_report_altered_after_hashing_fails_delivery(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = run / "reports" / "evidence-wi059-20260908-wp0-backup.xml"
    report.write_bytes(report.read_bytes() + b" ")
    checks = _finalize(run, candidate, monkeypatch)
    assert checks["fresh_reports_delivered_intact"] is False


def test_an_authored_value_windows_did_not_report_fails(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = _authored_values()[:-1]
    (run / "reports" / "authored.xml").write_bytes(_authored_report(values))
    result = json.loads((run / "result.json").read_text("utf-8-sig"))
    result["authored"]["report_sha256"] = FINALIZER._sha(run / "reports" / "authored.xml")
    _write_result(run, result)
    checks = _finalize(run, candidate, monkeypatch)
    assert checks["authored_report_lists_exactly_the_authored_values"] is False
    assert checks["authored_studio_import_equals_report"] is False
    assert checks["authored_fresh_report_matches_backup_report"] is False


@pytest.mark.parametrize(
    "mutate,check",
    [
        (lambda r: r["cases"][0].update(report_links_to_count=1), "every_disposable_gpo_unlinked"),
        (lambda r: r["cases"][0].update(absence_confirmed=False), "every_gpo_removed_and_absent"),
        (lambda r: r["authored"].update(cleanup_succeeded=False), "every_gpo_removed_and_absent"),
        (lambda r: r.update(cleanup_state_restored=False), "cleanup_state_restored"),
        (lambda r: r["cases"][0].update(import_succeeded=False), "every_case_imported"),
        (lambda r: r["cases"].pop(), "every_expected_case_ran_once"),
        (lambda r: r["cases"][0].update(error="boom"), "harness_reported_no_error"),
        (lambda r: r["cases"][0].update(source_gpo_id="{0}"),
         "every_case_identity_matches_candidate"),
        (lambda r: r["environment"].update(computer_system_domain_role=2),
         "member_server_host_role"),
        (lambda r: r.update(extra=1), "result_schema_exact"),
    ],
)
def test_each_harness_check_fires(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch,
    mutate: Any, check: str,
) -> None:
    result = json.loads((run / "result.json").read_text("utf-8-sig"))
    mutate(result)
    _write_result(run, result)
    assert _finalize(run, candidate, monkeypatch)[check] is False


def test_missing_command_output_fails(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (run / "commands" / "authored" / "set.stderr.txt").unlink()
    assert _finalize(run, candidate, monkeypatch)["raw_command_artifacts_complete"] is False


def test_a_tampered_expectation_does_not_reproduce(
    run: Path, candidate: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forged = tmp_path / "forged"
    shutil.copytree(candidate, forged)
    expected = _expected(forged)
    expected["cases"][0]["studio_inventory"]["families"] = []
    (forged / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
    checks = _finalize(run, forged, monkeypatch)
    assert checks["expected_inventories_reproduce_from_candidate"] is False


def test_a_deployed_script_that_differs_from_source_fails(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deployed = run / "deployed" / GUEST_PATH.name
    deployed.write_bytes(deployed.read_bytes() + b"\n")
    assert _finalize(run, candidate, monkeypatch)["deployed_harness_matches_source"] is False


def test_a_report_path_cannot_escape_the_run(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="escapes"):
        FINALIZER._run_file(tmp_path, "../outside.xml")
    with pytest.raises(ValueError, match="no report file"):
        FINALIZER._run_file(tmp_path, None)


def test_grade_case_requires_the_known_set_exactly(candidate: Path) -> None:
    """A known divergence that vanishes is a change too: the pin must move."""
    case = next(
        c for c in _expected(candidate)["cases"] if c["case_id"] == "native-WI01A-Power-GPMC"
    )
    fresh = inventory_from_json(case["backup_report_inventory"])
    checks, _ = FINALIZER.grade_case(fresh, case)
    assert all(checks.values())
    fixed = dict(case, expected_known=[])
    checks, _ = FINALIZER.grade_case(fresh, fixed)
    assert checks["known_divergences_exactly_as_expected"] is False


def test_grade_case_refuses_an_empty_report(candidate: Path) -> None:
    case = _expected(candidate)["cases"][0]
    checks, _ = FINALIZER.grade_case(Inventory(families=()), case)
    assert checks["fresh_report_lists_settings"] is False
    assert checks["no_unexplained_divergence"] is False


def test_the_authored_report_shape_is_what_the_differ_reads() -> None:
    inventory = windows_inventory(_authored_report(_authored_values()))
    assert InventoryItem(
        "RegistrySetting", key=BUILDER.AUTHORED_KEY, name="MachineString",
        value="String:report-parity-machine",
    ) in inventory.family("computer", "RegistrySettings")
    assert InventoryItem(
        "RegistrySetting", key=BUILDER.AUTHORED_KEY, name="UserDword", value="Number:2424",
    ) in inventory.family("user", "RegistrySettings")


def test_the_finalizer_refuses_a_candidate_root_missing_a_required_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(FINALIZER, "assert_bound_source_bytes", lambda *_: None)
    for omitted in FINALIZER.REQUIRED_CANDIDATE_FILES:
        root = tmp_path / f"without-{omitted}"
        root.mkdir()
        for name in FINALIZER.REQUIRED_CANDIDATE_FILES:
            if name != omitted:
                (root / name).write_bytes(b"{}")
        monkeypatch.setattr(sys, "argv", [
            "finalize", str(tmp_path), "--candidate-root", str(root), "--no-tag",
        ])
        assert FINALIZER.main() == 1
        assert omitted in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The driver and guest script
# ---------------------------------------------------------------------------


def test_the_lane_binds_every_source_file_it_uses() -> None:
    driver = DRIVER_PATH.read_text(encoding="utf-8")
    for name in FINALIZER.DEPLOYED_FILES:
        assert name in driver, f"{name} is deployed but the driver never moves it"
    for name in FINALIZER.LOCAL_FILES:
        assert not any(
            line.startswith("cp ") and name in line for line in driver.splitlines()
        ), f"{name} is manifest-bound (WI-062); the driver must not bank a copy"
    for relative in {**FINALIZER.LOCAL_FILES, **FINALIZER.DEPLOYED_FILES}.values():
        assert (ROOT / relative).is_file(), relative


def test_the_expectation_never_travels_to_the_guest() -> None:
    driver = DRIVER_PATH.read_text(encoding="utf-8")
    pushes = [
        line for line in driver.splitlines() if "-Action push" in line or "-LocalPath" in line
    ]
    assert pushes
    assert not any("expected.json" in line for line in pushes)
    assert "expected.json" not in GUEST_PATH.read_text(encoding="utf-8")


def test_the_driver_prints_the_run_and_candidate_directories() -> None:
    driver = DRIVER_PATH.read_text(encoding="utf-8")
    assert 'echo "LOCAL_RUN_DIR=$LOCAL_DIR"' in driver
    assert 'echo "CANDIDATE_DIR=$CANDIDATE_DIR"' in driver


@pytest.mark.parametrize(
    "path",
    [BUILDER_PATH, FINALIZER_PATH, DRIVER_PATH, GUEST_PATH],
    ids=lambda p: p.name,
)
def test_new_lane_files_are_lf_only(path: Path) -> None:
    """WI-063: eight runners were committed with CRLF and stopped parsing."""
    assert b"\r" not in path.read_bytes()


def _parse_errors(pwsh: str, script: Path) -> list[str]:
    command = (
        "$e=$null;$t=$null;"
        f"[void][System.Management.Automation.Language.Parser]::ParseFile('{script}',"
        "[ref]$t,[ref]$e); @($e).Count; $e | ForEach-Object { $_.ToString() }"
    )
    out = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, timeout=120, check=True,
    ).stdout.splitlines()
    return out[1:] if out and out[0].strip() != "0" else []


def test_the_guest_script_parses(tmp_path: Path) -> None:
    pwsh = shutil.which("powershell.exe") or shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("no PowerShell interpreter available")
    broken = tmp_path / "broken.ps1"
    broken.write_text("if ($true) { 'unclosed'\n", encoding="utf-8")
    assert _parse_errors(pwsh, broken), "the parse check cannot fail; it proves nothing"
    assert _parse_errors(pwsh, GUEST_PATH) == []


def test_the_guest_script_avoids_powershell_7_only_syntax() -> None:
    """LabMS01 runs Windows PowerShell 5.1: no ?:, ??, ?. or && / || chains."""
    import re

    for number, line in enumerate(GUEST_PATH.read_text(encoding="utf-8").splitlines(), 1):
        code = line.split("#", 1)[0]
        assert "??" not in code and "?." not in code, number
        assert not re.search(r"\s(&&|\|\|)\s", code), number
        assert not re.search(r"\)\s*\?\s*[^\s]", code), number
