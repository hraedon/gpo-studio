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
import types
import uuid
import xml.etree.ElementTree as ET
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


class _WindowsOrderedPath(type(Path())):  # type: ignore[misc]
    """A path that sorts the way ``WindowsPath`` does: case-insensitively.

    The first banked run's candidate rebuilt byte for byte on Linux and not on
    a Windows checkout, because the builder sorted ``Path`` objects and the two
    platforms order them differently (``bkupInfo.xml`` against ``DomainSysvol``).
    This reproduces the Windows comparator on any host, so the regression is
    caught here rather than only by the Windows CI job.
    """

    def _folded(self) -> str:
        return self.as_posix().casefold()

    def __lt__(self, other: object) -> bool:
        return self._folded() < Path(str(other)).as_posix().casefold()

    def __gt__(self, other: object) -> bool:
        return self._folded() > Path(str(other)).as_posix().casefold()

    def __le__(self, other: object) -> bool:
        return not self.__gt__(other)

    def __ge__(self, other: object) -> bool:
        return not self.__lt__(other)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "the shim stands in for WindowsPath on other hosts; on Windows the real "
        "comparator is native, and the two platform-independence tests below "
        "run against it directly"
    ),
)
def test_the_comparator_shim_really_orders_like_windows() -> None:
    """The control: without it the tests below prove nothing on Linux."""
    upper, lower = _WindowsOrderedPath("x/DomainSysvol"), _WindowsOrderedPath("x/bkupInfo.xml")
    assert sorted([upper, lower]) == [lower, upper]
    assert sorted([upper, lower], key=lambda p: p.parts) == [upper, lower]


def test_the_archive_member_order_does_not_depend_on_the_platform(tmp_path: Path) -> None:
    """The defect itself: the archive root is a temporary directory, so this is
    exercised through ``_zip`` directly, over the names that triggered it."""
    for relative in ("c01/{ID}/Backup.xml", "c01/{ID}/bkupInfo.xml",
                     "c01/{ID}/DomainSysvol/GPO/Machine/registry.pol",
                     "c01/{ID}/gpreport.xml", "c01/manifest.xml", "cases-index.tsv"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(relative.encode())
    native = BUILDER._zip(Path(tmp_path))
    windows = BUILDER._zip(_WindowsOrderedPath(tmp_path))
    with zipfile.ZipFile(io.BytesIO(native)) as zipped:
        names = zipped.namelist()
    assert names.index("c01/{ID}/DomainSysvol/GPO/Machine/registry.pol") < names.index(
        "c01/{ID}/bkupInfo.xml"
    )
    assert native == windows


def test_the_candidate_does_not_depend_on_the_platforms_path_order(tmp_path: Path) -> None:
    """The finalizer's rebuild check must hold on a controller of either OS."""
    native, windows = tmp_path / "native", tmp_path / "windows"
    native.mkdir()
    windows.mkdir()
    BUILDER.build(native, ROOT)
    BUILDER.build(windows, _WindowsOrderedPath(ROOT))
    for name in FINALIZER.REQUIRED_CANDIDATE_FILES:
        assert (native / name).read_bytes() == (windows / name).read_bytes(), name


def test_every_corpus_backup_is_import_ready_and_packaged(candidate: Path) -> None:
    expected = _expected(candidate)
    assert expected["excluded"] == []
    assert len(expected["cases"]) == len(BUILDER.corpus()) == 30
    with zipfile.ZipFile(candidate / "report-parity-cases.zip") as archive:
        names = archive.namelist()
        index = archive.read("cases/index.tsv").decode("ascii")
    dirs = {n.split("/")[1] for n in names if n.count("/") >= 2}
    assert dirs == {c["dir"] for c in expected["cases"]}
    assert all(n.startswith("cases/") for n in names)
    assert index == "".join(f"{c['dir']}\t{c['case_id']}\n" for c in expected["cases"])
    assert [c["dir"] for c in expected["cases"]] == [f"c{i:02d}" for i in range(1, 31)]


# ---------------------------------------------------------------------------
# The guest's MAX_PATH budget (first estate run, 2026-10-08)
# ---------------------------------------------------------------------------


def test_every_extracted_path_stays_under_the_guest_budget(candidate: Path) -> None:
    """The first estate run lost every case to MAX_PATH in Expand-Archive."""
    archive = (candidate / "report-parity-cases.zip").read_bytes()
    length, longest = BUILDER.longest_guest_path(archive)
    assert length <= BUILDER.MAX_GUEST_PATH, longest
    assert BUILDER.MAX_GUEST_PATH <= 200
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        for name in zipped.namelist():
            assert len(BUILDER.GUEST_EXTRACT_PREFIX + name) <= BUILDER.MAX_GUEST_PATH, name


def test_the_builder_refuses_a_path_over_the_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(BUILDER, "MAX_GUEST_PATH", 120)
    with pytest.raises(ValueError, match="exceeds 120"):
        BUILDER.build(tmp_path)


def test_the_driver_and_guest_use_the_budgeted_root() -> None:
    """GUEST_EXTRACT_PREFIX is a claim about the driver and guest; hold them to it."""
    driver = DRIVER_PATH.read_text(encoding="utf-8")
    guest = GUEST_PATH.read_text(encoding="utf-8")
    assert 'SHORT="$(date +%y%m%d%H%M%S)"' in driver
    assert 'GUEST_ROOT="C:\\gpo-studio\\rp\\\\$SHORT"' in driver
    assert 'GUEST_OUT="$GUEST_ROOT\\out"' in driver
    assert "$work = Join-Path $OutputDir 'run'" in guest
    assert "$inputRoot = Join-Path $work 'in'" in guest
    stamp = "000000000000"
    assert len(stamp) == len("261008092015")
    root = "C:\\gpo-studio\\rp\\" + stamp
    assert root + "\\out\\run\\in\\" == BUILDER.GUEST_EXTRACT_PREFIX


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
        native = case["source"].startswith(
            ("tests/fixtures/native-gpp-gpmc/", "tests/fixtures/native-gpp-registry-gpmc/")
        )
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
_RUN_ID = "report-parity-20261008000000-1234"
_PREFIX = f"zz-studio-rp-{_RUN_ID}"
_DOMAIN = "lab.test"
_SETTINGS_NS = "http://www.microsoft.com/GroupPolicy/Settings"
_TYPES_NS = "http://www.microsoft.com/GroupPolicy/Types"


def _identity_block(guid: str, name: str, domain: str = _DOMAIN) -> str:
    return (
        f'<Identifier><Identifier xmlns="{_TYPES_NS}">{{{guid.upper()}}}</Identifier>'
        f'<Domain xmlns="{_TYPES_NS}">{domain}</Domain></Identifier><Name>{name}</Name>'
    )


def _authored_report(
    values: list[tuple[str, str, str]], guid: str = _AUTHORED_GUID,
    name: str = f"{_PREFIX}-authored",
) -> bytes:
    def side(scope: str, rows: list[tuple[str, str]]) -> str:
        settings = "".join(
            f"<q:RegistrySetting><q:KeyPath>{BUILDER.AUTHORED_KEY}</q:KeyPath>"
            f"<q:AdmSetting>false</q:AdmSetting><q:Value><q:Name>{n}</q:Name>{v}</q:Value>"
            "</q:RegistrySetting>"
            for n, v in rows
        )
        return (
            f"<{scope}><ExtensionData><Extension "
            'xmlns:q="http://www.microsoft.com/GroupPolicy/Settings/Registry" '
            f'xsi:type="q:RegistrySettings">{settings}<q:Blocked>false</q:Blocked>'
            f"</Extension><Name>Registry</Name></ExtensionData></{scope}>"
        )

    computer = [(n, v) for s, n, v in values if s == "computer"]
    user = [(n, v) for s, n, v in values if s == "user"]
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<GPO xmlns="{_SETTINGS_NS}" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        + _identity_block(guid, name)
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


def _authored_backup(target: Path, report: bytes) -> str:
    """A native backup of the authored GPO, as Backup-GPO would leave it.

    Returns the backup ID its manifest names.
    """
    from gpo_studio.export import gpmc_backup_bundle

    settings = tuple(
        RegistrySetting(
            id=f"a{i}", side=side,
            hive="HKLM" if side == "computer" else "HKCU",  # type: ignore[arg-type]
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
    return backup_root.name


_XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"


def _parse_report(data: bytes) -> tuple[ET.Element, dict[int, tuple[str, str]]]:
    """A report tree plus each element's ``xsi:type`` QName.

    ElementTree drops ``xmlns:qN`` declarations and renames prefixes on output,
    while ``xsi:type="qN:..."`` keeps the old prefix in an attribute VALUE. A
    plain round trip therefore leaves every type QName unresolvable, which the
    report reader rightly treats as unmeasured. `_serialize_report` puts a
    matching declaration back.
    """
    from gpo_studio.report_parity import ReportParityError, _TypeTrackingBuilder
    from gpo_studio.xml_safety import parse_xml_bounded

    builder = _TypeTrackingBuilder(error_class=ReportParityError)
    root = parse_xml_bounded(
        data, max_size=64 * 1024 * 1024, error_class=ReportParityError, builder=builder
    )
    return root, builder.type_qnames


def _serialize_report(root: ET.Element, qnames: dict[int, tuple[str, str]]) -> bytes:
    for elem in root.iter():
        qname = qnames.get(id(elem))
        if qname is not None:
            elem.set("xmlns:qt", qname[0])
            elem.set(_XSI_TYPE, f"qt:{qname[1]}")
    return cast(bytes, ET.tostring(root, encoding="utf-8", xml_declaration=True))


def _as_fresh_report(capture: bytes, guid: str, name: str, domain: str = _DOMAIN) -> bytes:
    """A capture-time report re-identified as a fresh report of the owned GPO.

    Windows' fresh report of the disposable GPO carries the same settings but
    the owned GPO's identifier, name and domain; only those three change.
    """
    root, qnames = _parse_report(capture)
    ident = root.find(f"{{{_SETTINGS_NS}}}Identifier")
    assert ident is not None
    guid_elem = ident.find(f"{{{_TYPES_NS}}}Identifier")
    domain_elem = ident.find(f"{{{_TYPES_NS}}}Domain")
    name_elem = root.find(f"{{{_SETTINGS_NS}}}Name")
    assert guid_elem is not None and domain_elem is not None and name_elem is not None
    guid_elem.text = "{" + guid.upper() + "}"
    domain_elem.text = domain
    name_elem.text = name
    return _serialize_report(root, qnames)


def _simulated_run(candidate: Path, run: Path) -> dict[str, Any]:
    expected = _expected(candidate)
    environment = json.loads(ENVIRONMENT_SOURCE.read_text("utf-8-sig"))["environment"]
    (run / "reports").mkdir(parents=True)
    (run / "deployed").mkdir()
    shutil.copy(GUEST_PATH, run / "deployed" / GUEST_PATH.name)
    shutil.copy(candidate / "report-parity-cases.zip", run / "candidate.zip")
    (run / "builder.stdout.txt").write_text("log", encoding="utf-8")
    cases = []
    for index, case in enumerate(expected["cases"], 1):
        source = ROOT / case["source"] / case["backup_id"] / "gpreport.xml"
        owned = str(uuid.uuid5(uuid.NAMESPACE_URL, f"owned/{case['case_id']}"))
        target = f"{_PREFIX}-{index}"
        report = run / "reports" / f"{case['dir']}.xml"
        report.write_bytes(_as_fresh_report(source.read_bytes(), owned, target))
        commands = run / "commands" / case["dir"]
        commands.mkdir(parents=True)
        for name in ("import", "report"):
            for stream in ("stdout", "stderr"):
                (commands / f"{name}.{stream}.txt").write_text("", encoding="utf-8")
        cases.append({
            "case_id": case["case_id"], "case_dir": case["dir"], "target_name": target,
            "backup_id": case["backup_id"], "source_gpo_id": case["source_gpo_id"],
            "owned_gpo_id": owned, "import_succeeded": True,
            "report_file": f"reports/{case['dir']}.xml",
            "report_sha256": FINALIZER._sha(report), "report_links_to_count": 0,
            "cleanup_succeeded": True, "absence_confirmed": True, "error": None,
        })
    authored_report = _authored_report(_authored_values())
    (run / "reports" / "authored.xml").write_bytes(authored_report)
    backup_id = _authored_backup(run / "authored-backup", authored_report)
    commands = run / "commands" / "authored"
    commands.mkdir(parents=True)
    for name in ("set", "backup", "report"):
        for stream in ("stdout", "stderr"):
            (commands / f"{name}.{stream}.txt").write_text("", encoding="utf-8")
    result = {
        "schema_version": 1, "run_id": _RUN_ID,
        "domain": _DOMAIN.upper(), "cases": cases,
        "authored": {
            "target_name": f"{_PREFIX}-authored", "owned_gpo_id": _AUTHORED_GUID,
            "values_set": True, "backup_succeeded": True, "backup_id": backup_id,
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


def _read_result(run: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((run / "result.json").read_text("utf-8-sig")))


def _rehash(run: Path, result: dict[str, Any]) -> None:
    """Refresh every report hash, as a forger who controls the run would."""
    for record in [*result["cases"], result["authored"]]:
        path = run / record["report_file"]
        if path.is_file():
            record["report_sha256"] = FINALIZER._sha(path)
    _write_result(run, result)


def _verdict(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch, guest_status: int | None = 0,
) -> dict[str, Any]:
    # Bound-source bytes are the committed tree's concern, not this test's:
    # the grading is what is under test here.
    monkeypatch.setattr(FINALIZER, "assert_bound_source_bytes", lambda *_: None)
    # Likewise the working tree's cleanliness: git answers "clean" here, so the
    # control can assert a full pass and each mutation is the only failure.
    monkeypatch.setattr(FINALIZER, "subprocess", types.SimpleNamespace(run=_clean_git))
    status = [] if guest_status is None else ["--guest-status", str(guest_status)]
    monkeypatch.setattr(sys, "argv", [
        "finalize", str(run), "--candidate-root", str(candidate),
        "--repo-root", str(ROOT), *status, "--no-tag",
    ])
    FINALIZER.main()
    return cast(dict[str, Any], json.loads((run / "verification.json").read_text("utf-8")))


def _clean_git(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
    stdout = "0" * 40 + "\n" if args[:2] == ["git", "rev-parse"] else ""
    return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")


def _finalize(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch, guest_status: int = 0,
) -> dict[str, bool]:
    return cast(dict[str, bool], _verdict(run, candidate, monkeypatch, guest_status)["checks"])


@pytest.fixture()
def run(candidate: Path, tmp_path: Path) -> Path:
    path = tmp_path / "run"
    _simulated_run(candidate, path)
    return path


def test_the_simulated_run_passes_every_check(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control. Without it every mutation below could pass for the wrong reason."""
    verdict = _verdict(run, candidate, monkeypatch)
    checks = verdict["checks"]
    assert all(checks.values()), {k: v for k, v in checks.items() if not v}
    assert verdict["passed"] is True and verdict["checks_complete"] is True
    assert verdict["comparison_error"] is None
    power = verdict["comparison"]["cases"]["native-WI01A-Power-GPMC"]
    assert power["known"]["adapter-root-unknowns-dropped"]["work_item"] == "WI-072"


def _edit_report(run: Path, case_id: str, edit: Any) -> None:
    """Apply ``edit`` to one fresh report's XML tree, then refresh its hash."""
    report = run / "reports" / f"{BUILDER.case_dir(case_id)}.xml"
    root, qnames = _parse_report(report.read_bytes())
    edit(root)
    report.write_bytes(_serialize_report(root, qnames))
    _rehash(run, _read_result(run))


def test_a_setting_missing_from_the_fresh_report_fails_the_case(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def drop_first_drive(root: ET.Element) -> None:
        for container in root.iter():
            for child in list(container):
                if child.tag.endswith("}Drive"):
                    container.remove(child)
                    return

    _edit_report(run, "native-WI01A-DriveMaps-GPMC", drop_first_drive)
    checks = _finalize(run, candidate, monkeypatch)
    assert checks["every_case_studio_matches_fresh_report"] is False
    assert checks["fresh_reports_delivered_intact"] is True


# ---------------------------------------------------------------------------
# Review finding 1: every fresh report must name the GPO the run owned
# ---------------------------------------------------------------------------


def test_reports_naming_another_gpo_fail_even_with_matching_settings(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's mutation: every report re-identified as an unrelated GPO."""
    result = _read_result(run)
    for case in result["cases"]:
        path = run / case["report_file"]
        path.write_bytes(_as_fresh_report(
            path.read_bytes(), "11111111-2222-3333-4444-555555555555", "Unrelated GPO",
        ))
    _rehash(run, result)
    checks = _finalize(run, candidate, monkeypatch)
    assert checks["every_fresh_report_identifies_its_owned_gpo"] is False
    assert checks["every_case_studio_matches_fresh_report"] is True


@pytest.mark.parametrize(
    "guid,name,domain",
    [
        ("11111111-2222-3333-4444-555555555555", None, None),   # another GPO's id
        (None, "Some other name", None),                         # another name
        (None, None, "other.test"),                              # another domain
    ],
)
def test_each_identity_field_is_checked(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch,
    guid: str | None, name: str | None, domain: str | None,
) -> None:
    case = _read_result(run)["cases"][0]
    path = run / case["report_file"]
    path.write_bytes(_as_fresh_report(
        path.read_bytes(), guid or case["owned_gpo_id"], name or case["target_name"],
        domain or _DOMAIN,
    ))
    _rehash(run, _read_result(run))
    assert _finalize(run, candidate, monkeypatch)[
        "every_fresh_report_identifies_its_owned_gpo"
    ] is False


def test_the_capture_time_report_replayed_as_fresh_fails(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replaying gpreport.xml from the backup is not fresh Windows evidence."""
    result = _read_result(run)
    expected = {c["case_id"]: c for c in _expected(candidate)["cases"]}
    case = result["cases"][0]
    source = ROOT / expected[case["case_id"]]["source"] / case["backup_id"] / "gpreport.xml"
    shutil.copy(source, run / case["report_file"])
    _rehash(run, result)
    assert _finalize(run, candidate, monkeypatch)[
        "every_fresh_report_identifies_its_owned_gpo"
    ] is False


@pytest.mark.parametrize("owned", [None, "", "not-a-guid"])
def test_a_missing_owned_id_fails(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch, owned: str | None,
) -> None:
    result = _read_result(run)
    result["cases"][0]["owned_gpo_id"] = owned
    _write_result(run, result)
    checks = _finalize(run, candidate, monkeypatch)
    assert checks["every_owned_gpo_is_this_runs"] is False
    assert checks["every_fresh_report_identifies_its_owned_gpo"] is False


def test_a_target_name_outside_this_run_fails(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _read_result(run)
    result["cases"][0]["target_name"] = "zz-studio-rp-someone-else-1"
    _write_result(run, result)
    assert _finalize(run, candidate, monkeypatch)["every_owned_gpo_is_this_runs"] is False


# ---------------------------------------------------------------------------
# Review finding 2: every expectation comes from the bound builder
# ---------------------------------------------------------------------------


def _forge(candidate: Path, forged: Path, mutate: Any) -> None:
    shutil.copytree(candidate, forged)
    expected = _expected(forged)
    mutate(expected)
    (forged / "expected.json").write_text(json.dumps(expected), encoding="utf-8")


def test_a_tampered_studio_expectation_does_not_rebuild(
    run: Path, candidate: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forged = tmp_path / "forged"
    _forge(candidate, forged, lambda e: e["cases"][0]["studio_inventory"].update(families=[]))
    checks = _finalize(run, forged, monkeypatch)
    assert checks["candidate_rebuilds_from_bound_builder"] is False


def test_a_tampered_capture_time_expectation_does_not_rebuild(
    run: Path, candidate: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forged = tmp_path / "forged"
    _forge(
        candidate, forged,
        lambda e: e["cases"][0]["backup_report_inventory"].update(families=[]),
    )
    assert _finalize(run, forged, monkeypatch)["candidate_rebuilds_from_bound_builder"] is False


def test_an_empty_authored_expectation_and_empty_authored_run_fail(
    run: Path, candidate: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's mutation: empty spec, empty authored report and backup."""
    forged = tmp_path / "forged"
    _forge(
        candidate, forged,
        lambda e: e["authored"].update(windows_inventory={"families": [], "admx_policies": {}}),
    )
    empty = _authored_report([])
    (run / "reports" / "authored.xml").write_bytes(empty)
    shutil.rmtree(run / "authored-backup")
    _empty_authored_backup(run / "authored-backup", empty)
    result = _read_result(run)
    result["authored"]["backup_id"] = next(
        p.name for p in (run / "authored-backup").iterdir() if p.is_dir()
    )
    _rehash(run, result)
    checks = _finalize(run, forged, monkeypatch)
    assert checks["candidate_rebuilds_from_bound_builder"] is False
    assert checks["authored_report_lists_exactly_the_authored_values"] is False
    assert checks["authored_studio_import_equals_report"] is False
    assert checks["authored_fresh_report_matches_backup_report"] is False


def _empty_authored_backup(target: Path, report: bytes) -> None:
    from gpo_studio.export import gpmc_backup_bundle

    bundle = gpmc_backup_bundle(GPO(guid=_AUTHORED_GUID, name="authored"))
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        archive.extractall(target)
    (backup_root,) = [p for p in target.iterdir() if p.is_dir()]
    (backup_root / "gpreport.xml").write_bytes(report)


def test_grade_authored_refuses_empty_inventories_even_when_they_agree() -> None:
    empty = Inventory(families=())
    spec = {"windows_inventory": empty.to_json()}
    checks, _ = FINALIZER.grade_authored(empty, empty, empty, spec, len(BUILDER.AUTHORED_VALUES))
    assert not any(checks.values())


def test_a_case_dropped_from_archive_expectation_and_results_fails(
    run: Path, candidate: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's mutation: one case removed everywhere at once."""
    dropped = "native-WI01A-Services-GPMC"
    dropped_dir = BUILDER.case_dir(dropped)
    forged = tmp_path / "forged"
    _forge(candidate, forged, lambda e: e.update(
        cases=[c for c in e["cases"] if c["case_id"] != dropped]
    ))
    archive = forged / "report-parity-cases.zip"
    with zipfile.ZipFile(candidate / "report-parity-cases.zip") as source, zipfile.ZipFile(
        archive, "w"
    ) as target:
        for info in source.infolist():
            if f"/{dropped_dir}/" not in f"/{info.filename}":
                target.writestr(info, source.read(info))
    shutil.copy(archive, run / "candidate.zip")
    result = _read_result(run)
    result["cases"] = [c for c in result["cases"] if c["case_id"] != dropped]
    _write_result(run, result)
    checks = _finalize(run, forged, monkeypatch)
    assert checks["candidate_rebuilds_from_bound_builder"] is False
    assert checks["candidate_carries_the_required_corpus"] is False
    assert checks["every_expected_case_ran_once"] is False


def test_the_builder_refuses_a_corpus_missing_a_required_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    full = BUILDER.corpus()
    monkeypatch.setattr(BUILDER, "corpus", lambda repo=None: full[:-1])
    with pytest.raises(ValueError, match="missing"):
        BUILDER.build(tmp_path)


def test_the_required_corpus_is_the_whole_corpus() -> None:
    assert tuple(c for c, _ in BUILDER.corpus()) == BUILDER.REQUIRED_CASE_IDS
    # 27 at the report-parity bank; batch 2 added the three GPP Registry
    # captures (WI-075).
    assert len(BUILDER.REQUIRED_CASE_IDS) == 30


# ---------------------------------------------------------------------------
# Review finding 3: registry data is compared exactly
# ---------------------------------------------------------------------------


def test_whitespace_padding_an_authored_string_fails(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's mutation: '  report-parity-machine  ' in the fresh report."""
    values = [
        (s, n, v.replace("report-parity-machine", "  report-parity-machine  "))
        for s, n, v in _authored_values()
    ]
    (run / "reports" / "authored.xml").write_bytes(_authored_report(values))
    _rehash(run, _read_result(run))
    checks = _finalize(run, candidate, monkeypatch)
    assert checks["authored_report_lists_exactly_the_authored_values"] is False
    assert checks["authored_studio_import_equals_report"] is False
    assert checks["authored_fresh_report_matches_backup_report"] is False


# ---------------------------------------------------------------------------
# Review finding 4: WI-073 absorbs only the scheduled/immediate partition
# ---------------------------------------------------------------------------


def test_reordering_scheduled_tasks_within_their_type_fails(candidate: Path) -> None:
    """The reviewer's mutation: two TaskV2 items swapped in Windows' order."""
    case = next(
        c for c in _expected(candidate)["cases"]
        if c["case_id"] == "native-WI01A-SchedTasks-GPMC"
    )
    fresh = inventory_from_json(case["backup_report_inventory"]).to_json()
    for family in cast(list[dict[str, Any]], fresh["families"]):
        if family["family"] == "ScheduledTasksSettings" and family["side"] == "computer":
            items = family["items"]
            tasks = [i for i, item in enumerate(items) if item["element"] == "TaskV2"]
            items[tasks[0]], items[tasks[1]] = items[tasks[1]], items[tasks[0]]
    checks, summary = FINALIZER.grade_case(inventory_from_json(fresh), case)
    assert checks["no_unexplained_divergence"] is False
    assert any("order" in line for line in summary["unexplained"])


# ---------------------------------------------------------------------------
# Review finding 5: authored steps, backup id and guest exit status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("values_set", False),
        ("backup_succeeded", False),
        ("backup_id", None),
        ("backup_id", "not-a-guid"),
        ("backup_id", "{00000000-0000-0000-0000-000000000000}"),  # not the backup's id
    ],
)
def test_each_authored_step_is_required(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object,
) -> None:
    result = _read_result(run)
    result["authored"][field] = value
    _write_result(run, result)
    assert _finalize(run, candidate, monkeypatch)["authored_steps_succeeded"] is False


@pytest.mark.parametrize("status", [1, -1, None])
def test_a_failed_or_unreported_guest_can_never_pass(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch, status: int | None,
) -> None:
    verdict = _verdict(run, candidate, monkeypatch, guest_status=status)
    assert verdict["checks"]["guest_exited_zero"] is False
    assert verdict["passed"] is False


def test_the_driver_hands_the_guest_status_to_the_finalizer() -> None:
    driver = DRIVER_PATH.read_text(encoding="utf-8")
    assert '--guest-status "$GUEST_STATUS"' in driver
    assert driver.index("GUEST_STATUS=$?") < driver.index("finalize_report_parity_run.py")


# ---------------------------------------------------------------------------
# Sweep: missing data never reads as a pass
# ---------------------------------------------------------------------------


def test_a_verdict_missing_a_required_check_does_not_pass(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        FINALIZER, "REQUIRED_CHECKS", FINALIZER.REQUIRED_CHECKS | {"a_check_nobody_computes"}
    )
    verdict = _verdict(run, candidate, monkeypatch)
    assert verdict["checks_complete"] is False
    assert verdict["passed"] is False


def test_every_required_check_is_computed_by_the_control(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verdict = _verdict(run, candidate, monkeypatch)
    assert set(verdict["checks"]) == FINALIZER.REQUIRED_CHECKS
    assert verdict["checks_complete"] is True


@pytest.mark.parametrize(
    "mutate,check",
    [
        (lambda r: r.update(cases=[]), "every_case_imported"),
        (lambda r: r.update(cases="not a list"), "result_schema_exact"),
        (lambda r: r.update(authored=None), "authored_steps_succeeded"),
        (lambda r: r.pop("error"), "harness_reported_no_error"),
        (lambda r: r["cases"][0].pop("error"), "harness_reported_no_error"),
        (lambda r: r["cases"][0].update(report_sha256=None), "fresh_reports_delivered_intact"),
        (lambda r: r.update(run_id=None), "every_owned_gpo_is_this_runs"),
        (lambda r: r.update(domain=None), "every_fresh_report_identifies_its_owned_gpo"),
        (lambda r: r["cases"][1].update(owned_gpo_id=r["cases"][0]["owned_gpo_id"]),
         "every_owned_gpo_is_this_runs"),
    ],
)
def test_missing_or_duplicated_data_fails(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch, mutate: Any, check: str,
) -> None:
    result = _read_result(run)
    mutate(result)
    _write_result(run, result)
    verdict = _verdict(run, candidate, monkeypatch)
    assert verdict["checks"][check] is False
    assert verdict["passed"] is False


def test_a_report_altered_after_hashing_fails_delivery(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = run / "reports" / f"{BUILDER.case_dir('evidence-wi059-20260908-wp0-backup')}.xml"
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
            "finalize", str(tmp_path), "--candidate-root", str(root),
            "--guest-status", "0", "--no-tag",
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


# ---------------------------------------------------------------------------
# Review finding 6: the guest cleans up what it created, and only that
# ---------------------------------------------------------------------------

#: In-memory Group Policy cmdlets. Functions shadow cmdlets in PowerShell's
#: command resolution, so the real guest script runs unchanged against them.
_MOCK_HARNESS = r"""
param([string]$Script, [string]$Zip, [string]$Out, [string]$StatePath, [string]$Mode)
$ErrorActionPreference = 'Stop'
$global:Store = [ordered]@{}
$global:RemoveFailures = @{}
$global:Thrown = $false
$global:Store["$([guid]::NewGuid())"] = 'unrelated-gpo'
function New-GPO {
    [CmdletBinding()] param([string]$Name, [string]$Domain)
    $id = [guid]::NewGuid()
    $global:Store["$id"] = $Name
    if ($Mode -eq 'remove-fails-once') { $global:RemoveFailures["$id"] = 1 }
    if ($Mode -eq 'create-then-throw' -and -not $global:Thrown) {
        $global:Thrown = $true
        throw 'created, but the response was lost'
    }
    [pscustomobject]@{ Id = $id; DisplayName = $Name }
}
function Get-GPO {
    [CmdletBinding()] param([switch]$All, $Guid, [string]$Domain, [string]$Name)
    if ($All) {
        return @($global:Store.Keys | ForEach-Object {
            [pscustomobject]@{ Id = [guid]$_; DisplayName = $global:Store[$_] } })
    }
    $k = "$Guid"
    if (-not $global:Store.Contains($k)) { throw "GPO $k not found" }
    [pscustomobject]@{ Id = [guid]$k; DisplayName = $global:Store[$k] }
}
function Remove-GPO {
    [CmdletBinding(SupportsShouldProcess = $true)] param($Guid, [string]$Domain)
    $k = "$Guid"
    if ($global:RemoveFailures[$k] -gt 0) {
        $global:RemoveFailures[$k]--
        throw 'transient failure'
    }
    if (-not $global:Store.Contains($k)) { throw "GPO $k not found" }
    $global:Store.Remove($k)
}
function Import-GPO {
    [CmdletBinding(SupportsShouldProcess = $true)] param($BackupId, $Path, $TargetGuid, $Domain)
    [pscustomobject]@{ Id = $TargetGuid }
}
function Get-GPOReport {
    [CmdletBinding()] param($Guid, $Domain, $ReportType, $Path)
    $name = $global:Store["$Guid"]
    $settings = 'http://www.microsoft.com/GroupPolicy/Settings'
    $types = 'http://www.microsoft.com/GroupPolicy/Types'
    Set-Content -LiteralPath $Path -Value ("<GPO xmlns='$settings'><Identifier>" +
        "<Identifier xmlns='$types'>{$Guid}</Identifier></Identifier>" +
        "<Name>$name</Name></GPO>")
}
function Set-GPRegistryValue {
    [CmdletBinding()] param($Guid, $Domain, $Key, $ValueName, $Type, $Value)
}
function Backup-GPO {
    [CmdletBinding()] param($Guid, $Domain, $Path)
    [pscustomobject]@{ Id = [guid]::NewGuid(); GpoId = $Guid }
}
function Get-CimInstance {
    param([Parameter(Position = 0)]$ClassName)
    [pscustomobject]@{
        Caption = 'mock'; BuildNumber = '26100'; DomainRole = 3; Name = 'MOCK'; Domain = 'lab.test'
    }
}
function Start-Sleep { param($Seconds) }
$status = 0
try { & $Script -CandidateZip $Zip -OutputDir $Out -Domain 'lab.test' } catch { $status = 1 }
finally {
    @{ status = $status; remaining = @($global:Store.Values) } | ConvertTo-Json |
        Set-Content -LiteralPath $StatePath
}
"""


def _mock_candidate(path: Path, listed: tuple[str, ...] = ("c01", "c02"),
                    present: tuple[str, ...] = ("c01", "c02")) -> None:
    manifest = (
        '<Backups xmlns="http://www.microsoft.com/GroupPolicy/GPOOperations/Manifest">'
        "<BackupInst><GPOGuid>{AAAAAAAA-0000-0000-0000-000000000001}</GPOGuid>"
        "<ID>{BBBBBBBB-0000-0000-0000-000000000001}</ID></BackupInst></Backups>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "cases/index.tsv", "".join(f"{d}\tcase-{d}\n" for d in listed)
        )
        for case in present:
            archive.writestr(f"cases/{case}/manifest.xml", manifest)


def _run_mocked_guest(
    tmp_path: Path, mode: str, script: Path = GUEST_PATH,
    listed: tuple[str, ...] = ("c01", "c02"), present: tuple[str, ...] = ("c01", "c02"),
) -> dict[str, Any]:
    pwsh = shutil.which("pwsh") or shutil.which("powershell.exe")
    if pwsh is None:
        pytest.skip("no PowerShell interpreter available")
    harness = tmp_path / "harness.ps1"
    harness.write_text(_MOCK_HARNESS, encoding="utf-8")
    zip_path = tmp_path / "candidate.zip"
    _mock_candidate(zip_path, listed, present)
    out = tmp_path / "out"
    out.mkdir()
    state = tmp_path / "state.json"
    subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-File", str(harness), "-Script", str(script),
         "-Zip", str(zip_path), "-Out", str(out), "-StatePath", str(state), "-Mode", mode],
        capture_output=True, text=True, timeout=300, check=True,
    )
    final = cast(dict[str, Any], json.loads(state.read_text("utf-8-sig")))
    (run_dir,) = list(out.iterdir())
    final["result"] = json.loads((run_dir / "result.json").read_text("utf-8-sig"))
    return final


def _remaining(state: dict[str, Any]) -> list[str]:
    remaining = state["remaining"]
    return [remaining] if isinstance(remaining, str) else list(remaining)


def test_the_mocked_guest_run_leaves_only_what_it_found(tmp_path: Path) -> None:
    """The control: a clean run removes every GPO it made and nothing else."""
    state = _run_mocked_guest(tmp_path, "normal")
    assert state["status"] == 0
    assert _remaining(state) == ["unrelated-gpo"]
    result = state["result"]
    assert result["cleanup_state_restored"] is True
    assert all(c["cleanup_succeeded"] and c["absence_confirmed"] for c in result["cases"])
    run_id = result["run_id"]
    for record in [*result["cases"], result["authored"]]:
        assert record["target_name"].startswith(f"zz-studio-rp-{run_id}-")


def test_a_transient_removal_failure_is_retried(tmp_path: Path) -> None:
    """The reviewer's mutation: one Remove-GPO failure per GPO left a survivor."""
    state = _run_mocked_guest(tmp_path, "remove-fails-once")
    assert _remaining(state) == ["unrelated-gpo"]
    assert state["status"] == 0
    assert state["result"]["cleanup_state_restored"] is True


def test_a_gpo_created_without_a_returned_id_is_removed_by_name(tmp_path: Path) -> None:
    """The reviewer's mutation: New-GPO succeeded but threw; $ownedId stayed null."""
    state = _run_mocked_guest(tmp_path, "create-then-throw")
    assert _remaining(state) == ["unrelated-gpo"]
    assert state["status"] == 1  # the case still fails honestly
    first = state["result"]["cases"][0]
    assert first["import_succeeded"] is False
    assert first["owned_gpo_id"] is None
    assert first["absence_confirmed"] is True
    assert state["result"]["cleanup_state_restored"] is True


def test_the_guest_removes_only_names_it_registered() -> None:
    script = GUEST_PATH.read_text(encoding="utf-8")
    register = script.index("function Register-Target")
    assert script.index('StartsWith("$prefix-")', register) < script.index(
        "[void]$registeredNames.Add($name)", register
    )
    assert "if (-not $registeredNames.Contains($name)) { return $true }" in script
    # Registration precedes creation in both places a GPO is created.
    assert script.count("Register-Target $target\n") == 2
    for chunk in script.split("Register-Target $target\n")[1:]:
        assert chunk.lstrip().startswith("$owned = New-GPO")


# ---------------------------------------------------------------------------
# A case that did not extract is the run's stated error (first estate run)
# ---------------------------------------------------------------------------


def test_a_case_missing_after_extraction_is_the_recorded_error(tmp_path: Path) -> None:
    state = _run_mocked_guest(tmp_path, "normal", present=("c01",))
    result = state["result"]
    assert state["status"] == 1
    assert "missing after extraction: c02 (case-c02)" in (result["error"] or "")
    assert result["cases"] == []
    assert _remaining(state) == ["unrelated-gpo"]


def test_an_unlisted_case_directory_is_the_recorded_error(tmp_path: Path) -> None:
    state = _run_mocked_guest(tmp_path, "normal", listed=("c01",), present=("c01", "c02"))
    assert state["status"] == 1
    assert "unlisted case directories: c02" in (state["result"]["error"] or "")


def test_the_guest_records_each_case_directory(tmp_path: Path) -> None:
    state = _run_mocked_guest(tmp_path, "normal")
    cases = state["result"]["cases"]
    assert [(c["case_dir"], c["case_id"]) for c in cases] == [
        ("c01", "case-c01"), ("c02", "case-c02"),
    ]
    assert [c["report_file"] for c in cases] == ["reports/c01.xml", "reports/c02.xml"]


def test_a_case_directory_other_than_the_candidates_fails(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _read_result(run)
    result["cases"][0]["case_dir"] = "c99"
    _write_result(run, result)
    assert _finalize(run, candidate, monkeypatch)[
        "every_case_identity_matches_candidate"
    ] is False
