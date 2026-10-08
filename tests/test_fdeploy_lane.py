"""Focused tests for the Plan 034 fdeploy lane (candidate, finalizer, guest).

The lane grades comparisons, so the tests that matter prove each check can
fail. A simulated run is assembled from the candidate itself: each case's
re-export is the candidate's own files in a backup of the owned GPO (what
Windows should write back), and each fresh report is a Windows-shaped
``FolderRedirectionSettings`` report rendering the provenance values. The
control asserts that run grades clean; every mutation after it breaks one
thing on purpose.
"""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import types
import uuid
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from gpo_studio.fdeploy import read_fdeploy
from gpo_studio.fdeploy_parity import (
    FdeployParityError,
    byte_differences,
    encoding_facts,
    folder_redirection_rendering,
    reader_claims,
    reader_report_differences,
    report_identity,
)

ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "scripts/plan-033/build-fdeploy-candidate.py"
FINALIZER_PATH = ROOT / "scripts/windows-oracle/finalize_fdeploy_run.py"
DRIVER_PATH = ROOT / "scripts/windows-oracle/run-fdeploy-oracle.sh"
GUEST_PATH = ROOT / "scripts/windows-oracle/run-fdeploy-lane.ps1"
PARITY_PATH = ROOT / "src/gpo_studio/fdeploy_parity.py"
ENVIRONMENT_SOURCE = ROOT / "docs/plan-033/wp1b-evidence/wi062-20260910/publication/result.json"
SETTINGS_NS = "http://www.microsoft.com/GroupPolicy/Settings"
TYPES_NS = "http://www.microsoft.com/GroupPolicy/Types"
FR_NS = "http://www.microsoft.com/GroupPolicy/Settings/FolderRedirection"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BUILDER = cast(Any, _load(BUILDER_PATH, "fdeploy_lane_builder"))
FINALIZER = cast(Any, _load(FINALIZER_PATH, "fdeploy_lane_finalizer"))


@pytest.fixture(scope="module")
def candidate(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("candidate")
    BUILDER.build(out)
    return out


def _expected(candidate: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((candidate / "expected.json").read_text("utf-8")))


# ---------------------------------------------------------------------------
# A Windows-shaped report
# ---------------------------------------------------------------------------


def _fr_report(
    guid: str,
    name: str,
    domain: str,
    rows: list[tuple[str, str, str]],
    options: dict[str, str] | None = None,
    error: str | None = None,
) -> bytes:
    """A ``Get-GPOReport`` document in the shape the 2026-10-08 probe recorded.

    UTF-16LE with a BOM and a ``q1`` prefix on the extension namespace, as
    Windows writes it, so the reader is tested against the real encoding.
    """
    opts = options if options is not None else dict(BUILDER.PROBE_20261008_OPTIONS[1021])
    folders = ""
    for folder_id, sid, destination in rows:
        dest = (
            f"<q1:DestinationPath>{destination}</q1:DestinationPath>"
            if destination else "<q1:DestinationPath />"
        )
        folders += (
            f"<q1:Folder><q1:Id>{folder_id}</q1:Id><q1:Location>{dest}<q1:SecurityGroup>"
            f'<SID xmlns="{TYPES_NS}">{sid}</SID><Name xmlns="{TYPES_NS}">Everyone</Name>'
            "</q1:SecurityGroup></q1:Location>"
            + "".join(f"<q1:{k}>{v}</q1:{k}>" for k, v in opts.items())
            + "</q1:Folder>"
        )
    if error is not None:
        data = f"<Error><Details>{error}</Details><Description>x</Description></Error>"
    else:
        data = (
            f'<Extension xmlns:q1="{FR_NS}" xsi:type="q1:FolderRedirectionSettings">'
            f"{folders}</Extension>"
        )
    xml = (
        '<?xml version="1.0" encoding="utf-16"?>'
        '<GPO xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        f'xmlns="{SETTINGS_NS}"><Identifier>'
        f'<Identifier xmlns="{TYPES_NS}">{{{guid}}}</Identifier>'
        f'<Domain xmlns="{TYPES_NS}">{domain}</Domain></Identifier><Name>{name}</Name>'
        "<Computer><VersionDirectory>0</VersionDirectory><Enabled>true</Enabled></Computer>"
        "<User><VersionDirectory>1</VersionDirectory><Enabled>true</Enabled>"
        f"<ExtensionData>{data}<Name>Folder Redirection</Name></ExtensionData></User></GPO>"
    )
    return b"\xff\xfe" + xml.encode("utf-16-le")


R3_ROW = (BUILDER.R3_FOLDER_GUID, BUILDER.R3_PRINCIPAL, BUILDER.R3_FULL_PATH)


# ---------------------------------------------------------------------------
# fdeploy_parity
# ---------------------------------------------------------------------------


def test_the_rendering_reads_folder_location_and_options() -> None:
    rendering = folder_redirection_rendering(_fr_report("a" * 8 + "-0000-0000-0000-" + "0" * 12,
                                                        "n", "d", [R3_ROW]))
    assert rendering.extension_count == 1 and rendering.errors == ()
    (row,) = rendering.redirections
    assert row.key() == R3_ROW
    assert row.principal_name == "Everyone"
    assert dict(row.options) == BUILDER.PROBE_20261008_OPTIONS[1021]


def test_every_location_is_a_row_and_a_folder_without_one_is_visible() -> None:
    """Advanced mode renders one Location per group; none of it may be dropped."""
    location = (
        "<q1:Location><q1:DestinationPath>{path}</q1:DestinationPath><q1:SecurityGroup>"
        f'<SID xmlns="{TYPES_NS}">{{sid}}</SID><Name xmlns="{TYPES_NS}">g</Name>'
        "</q1:SecurityGroup></q1:Location>"
    )
    xml = (
        f'<GPO xmlns="{SETTINGS_NS}" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<Identifier><Identifier xmlns="{TYPES_NS}">{{{uuid.uuid4()}}}</Identifier>'
        "</Identifier><User><ExtensionData>"
        f'<Extension xmlns:q1="{FR_NS}" xsi:type="q1:FolderRedirectionSettings">'
        "<q1:Folder><q1:Id>{A}</q1:Id>"
        + location.format(path="p1", sid="S-1-5-1")
        + location.format(path="p2", sid="S-1-5-2")
        + "<q1:MoveContents>true</q1:MoveContents></q1:Folder>"
        "<q1:Folder><q1:Id>{B}</q1:Id></q1:Folder>"
        "</Extension><Name>Folder Redirection</Name></ExtensionData></User></GPO>"
    ).encode()
    rendering = folder_redirection_rendering(xml)
    assert rendering.keys() == [("{A}", "S-1-5-1", "p1"), ("{A}", "S-1-5-2", "p2"), ("{B}", "", "")]
    assert rendering.redirections[0].options == (("MoveContents", "true"),)


def test_an_error_rendering_is_not_an_empty_agreement() -> None:
    rendering = folder_redirection_rendering(
        _fr_report(str(uuid.uuid4()), "n", "d", [], error="FRSettingRead failed with -2147467259")
    )
    assert rendering.extension_count == 0
    assert rendering.errors == ("FRSettingRead failed with -2147467259",)
    claims = reader_claims(read_fdeploy(BUILDER.r3_bytes()[1]))
    problems = reader_report_differences(claims, rendering)
    assert any("FRSettingRead" in p for p in problems)
    assert reader_report_differences((), rendering)


def test_an_empty_destination_disagrees_with_the_full_path() -> None:
    """The probe's Flags=765/2045 shape: Windows does not call FullPath the destination."""
    rendering = folder_redirection_rendering(
        _fr_report(str(uuid.uuid4()), "n", "d", [(R3_ROW[0], R3_ROW[1], "")])
    )
    claims = reader_claims(read_fdeploy(BUILDER.r3_bytes()[1]))
    assert reader_report_differences(claims, rendering)


def test_case_differences_are_disagreements() -> None:
    rendering = folder_redirection_rendering(
        _fr_report(str(uuid.uuid4()), "n", "d", [(R3_ROW[0], "S-1-1-0", R3_ROW[2])])
    )
    claims = reader_claims(read_fdeploy(BUILDER.r3_bytes()[1]))
    assert reader_report_differences(claims, rendering)


def test_report_identity_and_refusals() -> None:
    guid = "11111111-2222-3333-4444-555555555555"
    identity = report_identity(_fr_report(guid.upper(), "name", "Lab.Test", [R3_ROW]))
    assert (identity.guid, identity.name, identity.domain) == (guid, "name", "Lab.Test")
    with pytest.raises(FdeployParityError, match="not a GPMC settings report"):
        report_identity(b"<Other/>")
    with pytest.raises(FdeployParityError, match="no GPO identifier"):
        report_identity(f'<GPO xmlns="{SETTINGS_NS}"><Name>x</Name></GPO>'.encode())


def test_encoding_facts_and_byte_differences() -> None:
    marker, policy = BUILDER.r3_bytes()
    facts = encoding_facts(policy)
    assert facts["size"] == 458 and facts["cr_count"] == 9 and facts["crlf_only"] is True
    assert encoding_facts(b"plain")["utf16le_bom"] is False
    assert byte_differences(policy, policy) == {
        "identical": True, "candidate": facts, "windows": facts,
    }
    rewritten = policy.replace("s-1-1-0".encode("utf-16-le"), "S-1-1-0".encode("utf-16-le"))
    record = byte_differences(policy, rewritten)
    assert record["identical"] is False and record["windows_reads"] is True
    assert record["line_diff"]
    unreadable = byte_differences(policy, b"no bom")
    assert unreadable["windows_reads"] is False
    assert marker != policy


def test_the_parity_module_composes_no_fdeploy_bytes() -> None:
    text = PARITY_PATH.read_text(encoding="utf-8")
    assert "encode_fdeploy" not in text and "format_fdeploy" not in text


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

    The certifying run's candidate rebuilt byte for byte on Linux and not on a
    Windows checkout (PR #98, run 37772005773), because the builder sorted
    ``Path`` objects and the two platforms order them differently
    (``bkupInfo.xml`` against ``DomainSysvol``) -- the defect report-parity's
    builder had first. This reproduces the Windows comparator on any host, so
    the regression is caught here rather than only by the Windows CI job.
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
    for relative in ("cases/c1/{ID}/Backup.xml", "cases/c1/{ID}/bkupInfo.xml",
                     "cases/c1/{ID}/DomainSysvol/GPO/User/Documents & Settings/fdeploy1.ini",
                     "cases/c1/{ID}/gpreport.xml", "cases/c1/manifest.xml"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(relative.encode())
    native = BUILDER._zip(Path(tmp_path))
    windows = BUILDER._zip(_WindowsOrderedPath(tmp_path))
    with zipfile.ZipFile(io.BytesIO(native)) as zipped:
        names = zipped.namelist()
    assert names.index(
        "cases/c1/{ID}/DomainSysvol/GPO/User/Documents & Settings/fdeploy1.ini"
    ) < names.index("cases/c1/{ID}/bkupInfo.xml")
    assert native == windows


def test_the_candidate_does_not_depend_on_the_platforms_path_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The finalizer's rebuild check must hold on a controller of either OS.

    The builder stages into its own temporary directory, so the repo root
    alone does not carry the comparator in; the module's ``Path`` is replaced
    too, so the staging tree is Windows-ordered as well.
    """
    native, windows = tmp_path / "native", tmp_path / "windows"
    native.mkdir()
    windows.mkdir()
    BUILDER.build(native, ROOT)
    monkeypatch.setattr(BUILDER, "Path", _WindowsOrderedPath)
    BUILDER.build(windows, _WindowsOrderedPath(ROOT))
    for name in FINALIZER.REQUIRED_CANDIDATE_FILES:
        assert (native / name).read_bytes() == (windows / name).read_bytes(), name


def test_the_verbatim_case_is_r3_and_the_others_change_only_flags(candidate: Path) -> None:
    provenance = json.loads(
        (ROOT / BUILDER.FIXTURE_DIR / "provenance.json").read_text("utf-8")
    )["files"]
    _, r3 = BUILDER.r3_bytes()
    for case in _expected(candidate)["cases"]:
        files = FINALIZER.candidate_files(candidate / "fdeploy-cases.zip", case)
        assert encoding_facts(files["fdeploy.ini"])["sha256"] == provenance["fdeploy.ini.txt"][
            "raw_sha256"
        ]
        policy = files["fdeploy1.ini"]
        if case["case_id"] == BUILDER.VERBATIM_CASE:
            assert case["verbatim_r3"] is True
            assert encoding_facts(policy)["sha256"] == provenance["fdeploy1.ini.txt"]["raw_sha256"]
        else:
            assert case["verbatim_r3"] is False
            assert len(policy) == len(r3)
            changed = [i for i, (a, b) in enumerate(zip(policy, r3, strict=True)) if a != b]
            assert changed and all(b == 0 or chr(b).isdigit() for b in (policy[i] for i in changed))
            assert policy.decode("utf-16-le").count(f"Flags={case['flags']}\r\n") == 1
        assert case["expected_reader"] == [{
            "folder_guid": BUILDER.R3_FOLDER_GUID, "principal": BUILDER.R3_PRINCIPAL,
            "full_path": BUILDER.R3_FULL_PATH, "flags": case["flags"],
            "flags_text": str(case["flags"]),
        }]
        assert case["expected_report"] == [list(R3_ROW)]


def test_the_required_cases_are_the_four_the_ruling_names(candidate: Path) -> None:
    assert BUILDER.REQUIRED_CASE_IDS == (
        "r3-flags-1021", "r3-flags-1020", "r3-flags-1023", "r3-flags-3069",
    )
    assert [c["case_id"] for c in _expected(candidate)["cases"]] == list(BUILDER.REQUIRED_CASE_IDS)
    with zipfile.ZipFile(candidate / "fdeploy-cases.zip") as archive:
        tops = {name.split("/")[1] for name in archive.namelist()}
    assert tops == {"c1", "c2", "c3", "c4"}
    assert [c["case_dir"] for c in _expected(candidate)["cases"]] == ["c1", "c2", "c3", "c4"]


def test_the_builder_copies_the_domain_neutral_descriptor() -> None:
    from gpo_studio.export import _DOMAIN_NEUTRAL_SECURITY_DESCRIPTOR

    assert BUILDER.DOMAIN_NEUTRAL_SD == _DOMAIN_NEUTRAL_SECURITY_DESCRIPTOR


def test_the_backup_skeleton_follows_what_windows_wrote(candidate: Path) -> None:
    case = _expected(candidate)["cases"][0]
    with zipfile.ZipFile(candidate / "fdeploy-cases.zip") as archive:
        backup_xml = archive.read(f"cases/{case['case_dir']}/{case['backup_id']}/Backup.xml")
    text = backup_xml.decode("utf-8")
    assert 'bkp:ReEvaluateFunction="FRValidateSettings"' in text
    assert f"<![CDATA[{BUILDER.USER_EXTENSION_PAIR}]]>" in text
    assert "Documents &amp; Settings\\fdeploy1.ini" in text
    assert BUILDER.DOMAIN_NEUTRAL_SD in text
    # Synthetic identity only: no estate SID, domain or controller.
    assert "S-1-5-21" not in text and "synthetic.test" in text and "UNKNOWN" in text


def test_the_builder_refuses_a_tampered_transcript(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / BUILDER.FIXTURE_DIR, repo / BUILDER.FIXTURE_DIR)
    transcript = repo / BUILDER.FIXTURE_DIR / "fdeploy1.ini.txt"
    transcript.write_text(
        transcript.read_text("ascii").replace("Flags=1021", "Flags=1023"), encoding="ascii"
    )
    with pytest.raises(ValueError, match="disagree with the provenance record"):
        BUILDER.build(tmp_path / "out", repo)


def test_the_builder_refuses_constants_the_capture_contradicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(BUILDER, "R3_FULL_PATH", r"\\elsewhere\share")
    (tmp_path / "out").mkdir()
    with pytest.raises(ValueError, match="R3 constants disagree"):
        BUILDER.build(tmp_path / "out")


def test_the_probe_table_is_the_notes_reading() -> None:
    """Pins NOTES.md: each lane value differs from 1021 in exactly the element named."""
    base = BUILDER.PROBE_20261008_OPTIONS[1021]
    changes = {
        flags: {k: v for k, v in options.items() if base[k] != v}
        for flags, options in BUILDER.PROBE_20261008_OPTIONS.items()
    }
    assert changes == {
        1021: {}, 1020: {"MoveContents": "false"}, 1023: {"FollowParent": "true"},
        3069: {"RedirectToLocal": "true"},
    }
    assert set(BUILDER.PROBE_20261008_OPTIONS) == {flags for _, flags in BUILDER.CASES}


# ---------------------------------------------------------------------------
# A simulated run, graded by the real finalizer
# ---------------------------------------------------------------------------

_RUN_ID = "fd-20261008000000-1234"
_PREFIX = f"zz-studio-fd-{_RUN_ID}"
_DOMAIN = "lab.test"


def _owned(case_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"owned/{case_id}"))


def _first_case_files(candidate: Path) -> dict[str, bytes]:
    archive = candidate / "fdeploy-cases.zip"
    first = _expected(candidate)["cases"][0]
    return cast(dict[str, bytes], FINALIZER.candidate_files(archive, first))


def _write_backup(run: Path, record: dict[str, Any], files: dict[str, bytes],
                  report: bytes) -> None:
    """A native backup of the owned GPO, as Backup-GPO would leave it."""
    root = run / record["rebackup_dir"]
    if root.exists():
        shutil.rmtree(root)
    identity = {
        "source_gpo_id": "{" + record["owned_gpo_id"] + "}",
        "backup_id": record["rebackup_id"],
        "display_name": record["target_name"],
        "domain": _DOMAIN.upper(),
    }
    BUILDER.stage_case(root, identity, files["fdeploy.ini"], files["fdeploy1.ini"])
    (root / record["rebackup_id"] / "gpreport.xml").write_bytes(report)
    record["rebackup_files"] = [
        {
            "name": name, "present": True, "length": len(files[name]),
            "sha256": FINALIZER._sha_bytes(files[name]),
            "base64": base64.b64encode(files[name]).decode("ascii"),
        }
        for name in FINALIZER.FDEPLOY_FILES
    ]


def _simulated_run(candidate: Path, run: Path) -> dict[str, Any]:
    expected = _expected(candidate)
    environment = json.loads(ENVIRONMENT_SOURCE.read_text("utf-8-sig"))["environment"]
    (run / "reports").mkdir(parents=True)
    (run / "deployed").mkdir()
    shutil.copy(GUEST_PATH, run / "deployed" / GUEST_PATH.name)
    shutil.copy(candidate / "fdeploy-cases.zip", run / "candidate.zip")
    (run / "builder.stdout.txt").write_text("log", encoding="utf-8")
    cases = []
    for index, case in enumerate(expected["cases"], 1):
        case_id = case["case_id"]
        owned = _owned(case_id)
        target = f"{_PREFIX}-{index}"
        files = FINALIZER.candidate_files(candidate / "fdeploy-cases.zip", case)
        report = _fr_report(
            owned.upper(), target, _DOMAIN, [tuple(r) for r in case["expected_report"]],
            options=BUILDER.PROBE_20261008_OPTIONS[case["flags"]],
        )
        key = case["case_dir"]
        (run / "reports" / f"{key}.xml").write_bytes(report)
        commands = run / "commands" / key
        commands.mkdir(parents=True)
        for name in ("import", "report", "backup"):
            for stream in ("stdout", "stderr"):
                (commands / f"{name}.{stream}.txt").write_text("", encoding="utf-8")
        record: dict[str, Any] = {
            "case_dir": key, "target_name": target,
            "backup_id": case["backup_id"], "source_gpo_id": case["source_gpo_id"],
            "owned_gpo_id": owned, "import_succeeded": True,
            "user_extension_names": BUILDER.USER_EXTENSION_PAIR,
            "sysvol_files": [
                {"relative_path": name, "length": len(files[name]),
                 "sha256": FINALIZER._sha_bytes(files[name])}
                for name in sorted(files)
            ],
            "report_file": f"reports/{key}.xml",
            "report_sha256": FINALIZER._sha_bytes(report), "report_links_to_count": 0,
            "rebackup_succeeded": True,
            "rebackup_id": "{" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"rb/{case_id}")).upper() + "}",
            "rebackup_dir": f"backups/{key}", "rebackup_files": None,
            "cleanup_succeeded": True, "absence_confirmed": True, "foreign_residue": [],
            "error": None,
        }
        _write_backup(run, record, files, report)
        cases.append(record)
    result = {
        "schema_version": 2, "run_id": _RUN_ID, "domain": _DOMAIN.upper(), "cases": cases,
        "cleanup_state_restored": True, "residue": [], "environment": environment,
        "error": None,
    }
    _write_result(run, result)
    return result


def _write_result(run: Path, result: dict[str, Any]) -> None:
    (run / "result.json").write_text(json.dumps(result), encoding="utf-8-sig")


def _read_result(run: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((run / "result.json").read_text("utf-8-sig")))


def _clean_git(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
    stdout = "0" * 40 + "\n" if args[:2] == ["git", "rev-parse"] else ""
    return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")


def _verdict(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch, guest_status: int | None = 0,
) -> dict[str, Any]:
    # Bound-source bytes and tree cleanliness are the committed tree's concern;
    # the grading is what is under test here.
    monkeypatch.setattr(FINALIZER, "assert_bound_source_bytes", lambda *_: None)
    monkeypatch.setattr(FINALIZER, "subprocess", types.SimpleNamespace(run=_clean_git))
    status = [] if guest_status is None else ["--guest-status", str(guest_status)]
    monkeypatch.setattr(sys, "argv", [
        "finalize", str(run), "--candidate-root", str(candidate),
        "--repo-root", str(ROOT), *status, "--no-tag",
    ])
    FINALIZER.main()
    return cast(dict[str, Any], json.loads((run / "verification.json").read_text("utf-8")))


def _finalize(run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, bool]:
    return cast(dict[str, bool], _verdict(run, candidate, monkeypatch)["checks"])


@pytest.fixture()
def run(candidate: Path, tmp_path: Path) -> Path:
    path = tmp_path / "run"
    _simulated_run(candidate, path)
    return path


def _failing(checks: dict[str, bool]) -> set[str]:
    return {name for name, ok in checks.items() if not ok}


def test_the_simulated_run_passes_every_check(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control. Without it every mutation below could pass for the wrong reason."""
    verdict = _verdict(run, candidate, monkeypatch)
    assert _failing(verdict["checks"]) == set()
    assert verdict["passed"] is True and verdict["checks_complete"] is True
    assert verdict["comparison_error"] is None
    case = verdict["comparison"]["cases"]["r3-flags-1021"]
    assert case["recorded"]["matches_probe_20261008"] is True
    assert case["byte_differences"]["fdeploy1.ini"]["identical"] is True
    assert case["studio_claims"][0]["flags"] == 1021


def test_every_required_check_is_computed_by_the_control(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verdict = _verdict(run, candidate, monkeypatch)
    assert set(verdict["checks"]) == FINALIZER.REQUIRED_CHECKS


def test_a_verdict_missing_a_required_check_does_not_pass(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        FINALIZER, "REQUIRED_CHECKS", FINALIZER.REQUIRED_CHECKS | {"a_check_nobody_computes"}
    )
    verdict = _verdict(run, candidate, monkeypatch)
    assert verdict["checks_complete"] is False and verdict["passed"] is False


def _set_report(run: Path, case_index: int, report: bytes, rehash: bool = True) -> None:
    result = _read_result(run)
    record = result["cases"][case_index]
    (run / record["report_file"]).write_bytes(report)
    if rehash:
        record["report_sha256"] = FINALIZER._sha_bytes(report)
    _write_result(run, result)


def _case(run: Path, index: int = 0) -> dict[str, Any]:
    return cast(dict[str, Any], _read_result(run)["cases"][index])


def test_a_report_altered_after_hashing_fails_delivery(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _case(run)
    report = run / record["report_file"]
    report.write_bytes(report.read_bytes() + b" \x00")
    assert _failing(_finalize(run, candidate, monkeypatch)) == {
        "every_case_fresh_report_delivered_intact",
    }


@pytest.mark.parametrize("field", ["guid", "name", "domain"])
def test_each_identity_field_is_checked(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch, field: str,
) -> None:
    record = _case(run)
    values = {"guid": record["owned_gpo_id"], "name": record["target_name"], "domain": _DOMAIN}
    values[field] = {"guid": "11111111-2222-3333-4444-555555555555", "name": "Unrelated",
                     "domain": "other.test"}[field]
    _set_report(run, 0, _fr_report(values["guid"], values["name"], values["domain"], [R3_ROW]))
    assert _failing(_finalize(run, candidate, monkeypatch)) == {
        "every_case_fresh_report_identifies_owned_gpo",
    }


def test_a_rebackup_of_another_gpo_fails(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _read_result(run)
    record = result["cases"][0]
    other = dict(record, owned_gpo_id="11111111-2222-3333-4444-555555555555")
    files = _first_case_files(candidate)
    _write_backup(run, other, files, (run / record["report_file"]).read_bytes())
    record["rebackup_files"] = other["rebackup_files"]
    _write_result(run, result)
    failing = _failing(_finalize(run, candidate, monkeypatch))
    assert "every_case_rebackup_is_of_owned_gpo" in failing
    assert "every_case_studio_reads_windows_rebackup" in failing


def test_a_rebackup_id_other_than_the_guest_reported_fails(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _read_result(run)
    result["cases"][0]["rebackup_id"] = "{" + str(uuid.uuid4()).upper() + "}"
    _write_result(run, result)
    assert _failing(_finalize(run, candidate, monkeypatch)) == {
        "every_case_rebackup_is_of_owned_gpo",
    }


def test_base64_that_disagrees_with_the_pulled_file_fails_delivery(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _case(run)
    pulled = (run / record["rebackup_dir"] / record["rebackup_id"]
              / FINALIZER.SETTINGS_PATH / "fdeploy1.ini")
    pulled.write_bytes(pulled.read_bytes()[:-2])
    failing = _failing(_finalize(run, candidate, monkeypatch))
    assert "every_case_rebackup_bytes_delivered_intact" in failing


@pytest.mark.parametrize(
    "mutate",
    [
        lambda e: e.update(sha256="0" * 64),
        lambda e: e.update(length=1),
        lambda e: e.update(base64="not base64!"),
        lambda e: e.update(present=False),
        lambda e: e.pop("base64"),
    ],
)
def test_each_rebackup_file_field_is_required(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch, mutate: Any,
) -> None:
    result = _read_result(run)
    mutate(result["cases"][0]["rebackup_files"][0])
    _write_result(run, result)
    assert "every_case_rebackup_bytes_delivered_intact" in _failing(
        _finalize(run, candidate, monkeypatch)
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda files: files[0].update(sha256="0" * 64),
        lambda files: files.append({"relative_path": "extra.ini", "length": 1, "sha256": "0" * 64}),
        lambda files: files.pop(),
    ],
)
def test_sysvol_bytes_must_be_the_candidates(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch, mutate: Any,
) -> None:
    result = _read_result(run)
    mutate(result["cases"][0]["sysvol_files"])
    _write_result(run, result)
    assert _failing(_finalize(run, candidate, monkeypatch)) == {
        "every_case_imported_sysvol_holds_candidate_bytes",
    }


def test_windows_rewriting_the_file_fails_and_records_every_difference(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A consistent rewrite (SID upper-cased) delivered intact: identity fails."""
    result = _read_result(run)
    record = result["cases"][0]
    files = _first_case_files(candidate)
    rewritten = dict(files)
    rewritten["fdeploy1.ini"] = files["fdeploy1.ini"].replace(
        "s-1-1-0".encode("utf-16-le"), "S-1-1-0".encode("utf-16-le")
    )
    _write_backup(run, record, rewritten, (run / record["report_file"]).read_bytes())
    _write_result(run, result)
    verdict = _verdict(run, candidate, monkeypatch)
    failing = _failing(verdict["checks"])
    assert "every_case_rebackup_bytes_equal_candidate" in failing
    assert "every_case_studio_reads_windows_rebackup" in failing
    assert "every_case_rebackup_bytes_delivered_intact" not in failing
    diff = verdict["comparison"]["cases"]["r3-flags-1021"]["byte_differences"]["fdeploy1.ini"]
    assert diff["identical"] is False and diff["windows_reads"] is True
    # diff_fdeploy keys rows case-insensitively, so a case-only rewrite is no
    # reader-level change; the line diff is what records it.
    assert diff["reader_changes"] == []
    assert any("S-1-1-0" in line for line in diff["line_diff"])


def test_a_path_windows_and_studio_agree_on_but_provenance_does_not_fails(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows and Studio agreeing with each other is not enough: the expectation binds."""
    result = _read_result(run)
    record = result["cases"][0]
    files = _first_case_files(candidate)
    other_path = r"\\zz-studio-fileserver\zzredir\%USERNAME%\Documentz"
    moved = dict(files)
    moved["fdeploy1.ini"] = files["fdeploy1.ini"].replace(
        BUILDER.R3_FULL_PATH.encode("utf-16-le"), other_path.encode("utf-16-le")
    )
    report = _fr_report(record["owned_gpo_id"], record["target_name"], _DOMAIN,
                        [(R3_ROW[0], R3_ROW[1], other_path)])
    (run / record["report_file"]).write_bytes(report)
    record["report_sha256"] = FINALIZER._sha_bytes(report)
    _write_backup(run, record, moved, report)
    _write_result(run, result)
    failing = _failing(_finalize(run, candidate, monkeypatch))
    assert "every_case_studio_reads_windows_rebackup" in failing
    assert "every_case_windows_report_renders_expected_redirections" in failing
    assert "every_case_studio_reader_agrees_with_windows_report" not in failing


@pytest.mark.parametrize(
    "rows,error",
    [
        ([(R3_ROW[0], R3_ROW[1], "")], None),                       # Flags 765/2045 shape
        ([], "FRSettingRead failed with -2147467259"),               # Flags 0 shape
        ([], None),                                                  # extension, no folder
        ([R3_ROW, R3_ROW], None),                                    # rendered twice
    ],
)
def test_a_report_that_does_not_render_the_redirection_fails(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch,
    rows: list[tuple[str, str, str]], error: str | None,
) -> None:
    record = _case(run)
    _set_report(run, 0, _fr_report(record["owned_gpo_id"], record["target_name"], _DOMAIN,
                                   rows, error=error))
    failing = _failing(_finalize(run, candidate, monkeypatch))
    assert "every_case_windows_report_renders_expected_redirections" in failing
    assert "every_case_studio_reader_agrees_with_windows_report" in failing


def test_the_rebackup_report_must_render_the_same(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _case(run)
    gpreport = run / record["rebackup_dir"] / record["rebackup_id"] / "gpreport.xml"
    gpreport.write_bytes(_fr_report(
        record["owned_gpo_id"], record["target_name"], _DOMAIN, [R3_ROW],
        options={**BUILDER.PROBE_20261008_OPTIONS[1021], "MoveContents": "false"},
    ))
    assert _failing(_finalize(run, candidate, monkeypatch)) == {
        "every_case_rebackup_report_matches_fresh_report",
    }
    gpreport.unlink()
    assert _failing(_finalize(run, candidate, monkeypatch)) == {
        "every_case_rebackup_report_matches_fresh_report",
    }


def test_option_rendering_is_recorded_not_asserted(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Studio decodes no Flags bit, so a different option rendering fails nothing."""
    result = _read_result(run)
    record = result["cases"][0]
    changed = {**BUILDER.PROBE_20261008_OPTIONS[1021], "DoNotCare": "true"}
    report = _fr_report(record["owned_gpo_id"], record["target_name"], _DOMAIN, [R3_ROW],
                        options=changed)
    (run / record["report_file"]).write_bytes(report)
    record["report_sha256"] = FINALIZER._sha_bytes(report)
    gpreport = run / record["rebackup_dir"] / record["rebackup_id"] / "gpreport.xml"
    gpreport.write_bytes(report)
    _write_result(run, result)
    verdict = _verdict(run, candidate, monkeypatch)
    assert verdict["passed"] is True
    recorded = verdict["comparison"]["cases"]["r3-flags-1021"]["recorded"]
    assert recorded["matches_probe_20261008"] is False
    assert recorded["windows_options"] == [changed]


def test_an_unreadable_case_fails_only_that_case_and_says_why(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _case(run)
    shutil.rmtree(run / record["rebackup_dir"])
    verdict = _verdict(run, candidate, monkeypatch)
    assert verdict["passed"] is False
    assert "r3-flags-1021" in verdict["comparison_error"]
    assert set(verdict["comparison"]["cases"]["r3-flags-1021"]["checks"].values()) == {False}
    assert set(verdict["comparison"]["cases"]["r3-flags-1020"]["checks"].values()) == {True}


def test_a_tampered_expectation_does_not_rebuild(
    run: Path, candidate: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forged = tmp_path / "forged"
    shutil.copytree(candidate, forged)
    expected = _expected(forged)
    expected["cases"][0]["expected_reader"][0]["full_path"] = "anything"
    (forged / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
    checks = _finalize(run, forged, monkeypatch)
    assert checks["candidate_rebuilds_from_bound_builder"] is False
    assert checks["every_case_studio_reads_windows_rebackup"] is False


def test_a_short_candidate_fails(
    run: Path, candidate: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forged = tmp_path / "forged"
    shutil.copytree(candidate, forged)
    expected = _expected(forged)
    expected["cases"].pop()
    (forged / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
    result = _read_result(run)
    result["cases"].pop()
    _write_result(run, result)
    checks = _finalize(run, forged, monkeypatch)
    assert checks["candidate_carries_the_required_cases"] is False
    assert checks["every_expected_case_ran_once"] is False
    assert checks["candidate_rebuilds_from_bound_builder"] is False


@pytest.mark.parametrize(
    "mutate,check",
    [
        (lambda r: r["cases"][0].update(report_links_to_count=1), "every_disposable_gpo_unlinked"),
        (lambda r: r["cases"][0].update(report_links_to_count=None),
         "every_disposable_gpo_unlinked"),
        (lambda r: r["cases"][0].update(absence_confirmed=False), "every_gpo_removed_and_absent"),
        (lambda r: r["cases"][0].update(cleanup_succeeded=None), "every_gpo_removed_and_absent"),
        (lambda r: r.update(cleanup_state_restored=False), "cleanup_state_restored"),
        (lambda r: r["cases"][0].update(import_succeeded=False), "every_case_imported"),
        (lambda r: r["cases"].pop(), "every_expected_case_ran_once"),
        (lambda r: r.update(cases=[]), "every_case_imported"),
        (lambda r: r.update(cases="not a list"), "result_schema_exact"),
        (lambda r: r["cases"][0].update(error="boom"), "harness_reported_no_error"),
        (lambda r: r.pop("error"), "harness_reported_no_error"),
        (lambda r: r["cases"][0].pop("error"), "harness_reported_no_error"),
        (lambda r: r["cases"][0].update(source_gpo_id="{0}"),
         "every_case_identity_matches_candidate"),
        (lambda r: r["cases"][0].update(backup_id="{0}"),
         "every_case_identity_matches_candidate"),
        (lambda r: r["environment"].update(computer_system_domain_role=2),
         "member_server_host_role"),
        (lambda r: r["environment"].pop("server_build"), "environment_matches_frozen_spec"),
        (lambda r: r.update(extra=1), "result_schema_exact"),
        (lambda r: r["cases"][0].update(extra=1), "result_schema_exact"),
        (lambda r: r.update(run_id=None), "every_owned_gpo_is_this_runs"),
        (lambda r: r["cases"][0].update(target_name="zz-studio-fd-other-1"),
         "every_owned_gpo_is_this_runs"),
        (lambda r: r["cases"][1].update(owned_gpo_id=r["cases"][0]["owned_gpo_id"]),
         "every_owned_gpo_is_this_runs"),
        (lambda r: r["cases"][0].update(owned_gpo_id=None), "every_owned_gpo_is_this_runs"),
        (lambda r: r.update(domain=None), "every_case_fresh_report_identifies_owned_gpo"),
        (lambda r: r["cases"][0].update(report_sha256=None),
         "every_case_fresh_report_delivered_intact"),
        (lambda r: r["cases"][0].update(rebackup_succeeded=False),
         "every_case_rebackup_is_of_owned_gpo"),
        (lambda r: r["cases"][0].update(rebackup_files=None),
         "every_case_rebackup_bytes_equal_candidate"),
        (lambda r: r["cases"][0].update(sysvol_files=None),
         "every_case_imported_sysvol_holds_candidate_bytes"),
    ],
)
def test_each_harness_check_fires(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch, mutate: Any, check: str,
) -> None:
    result = _read_result(run)
    mutate(result)
    _write_result(run, result)
    verdict = _verdict(run, candidate, monkeypatch)
    assert verdict["checks"][check] is False
    assert verdict["passed"] is False


# ---------------------------------------------------------------------------
# Review findings 3 and 4 (2026-10-08): the re-export's own metadata
# ---------------------------------------------------------------------------


def _rebackup_file(run: Path, name: str, index: int = 0) -> Path:
    record = _case(run, index)
    return cast(Path, run / record["rebackup_dir"] / record["rebackup_id"] / name)


def _rebackup_identity(run: Path, index: int = 0, **changes: str) -> dict[str, str]:
    record = _case(run, index)
    return {
        "source_gpo_id": "{" + record["owned_gpo_id"] + "}",
        "backup_id": record["rebackup_id"],
        "display_name": record["target_name"],
        "domain": _DOMAIN.upper(),
        **changes,
    }


def test_an_empty_bkup_info_fails(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _rebackup_file(run, "bkupInfo.xml").write_bytes(b"")
    verdict = _verdict(run, candidate, monkeypatch)
    assert verdict["passed"] is False
    assert "every_case_rebackup_is_of_owned_gpo" in _failing(verdict["checks"])
    assert "bkupInfo.xml unreadable" in verdict["comparison_error"]


def test_a_bkup_info_naming_another_gpo_and_backup_fails(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's probe: metadata for an unrelated GPO and backup ID."""
    _rebackup_file(run, "bkupInfo.xml").write_bytes(BUILDER.bkup_info_xml({
        "source_gpo_id": "{11111111-2222-3333-4444-555555555555}",
        "backup_id": "{22222222-2222-3333-4444-555555555555}",
        "display_name": "unrelated-gpo",
    }))
    verdict = _verdict(run, candidate, monkeypatch)
    assert verdict["passed"] is False
    assert "every_case_rebackup_is_of_owned_gpo" in _failing(verdict["checks"])


@pytest.mark.parametrize(
    "field,value",
    [("display_name", "unrelated-gpo"), ("domain", "foreign.test")],
)
def test_backup_metadata_must_name_the_owned_gpo_in_every_field(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: str,
) -> None:
    """Manifest and bkupInfo.xml agreeing with each other is not enough."""
    identity = _rebackup_identity(run, **{field: value})
    root = run / _case(run)["rebackup_dir"]
    (root / "manifest.xml").write_bytes(BUILDER.manifest_xml(identity))
    _rebackup_file(run, "bkupInfo.xml").write_bytes(BUILDER.bkup_info_xml(identity))
    assert _failing(_finalize(run, candidate, monkeypatch)) == {
        "every_case_rebackup_is_of_owned_gpo",
    }


@pytest.mark.parametrize("field", ["GPOGuid", "GPODomain", "ID", "GPODisplayName"])
def test_a_bkup_info_missing_any_identity_field_fails(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch, field: str,
) -> None:
    path = _rebackup_file(run, "bkupInfo.xml")
    text = path.read_text("utf-8")
    path.write_text(re.sub(rf"<{field}>.*?</{field}>", f"<{field}></{field}>", text), "utf-8")
    verdict = _verdict(run, candidate, monkeypatch)
    assert verdict["passed"] is False
    assert f"lacks {field}" in verdict["comparison_error"]


def test_import_readiness_refuses_disagreeing_backup_metadata(tmp_path: Path) -> None:
    identity = BUILDER.case_identity("r3-flags-1021")
    marker, policy = BUILDER.r3_bytes()
    BUILDER.stage_case(tmp_path, identity, marker, policy)
    assert BUILDER.import_readiness(tmp_path) == (identity["backup_id"], identity["source_gpo_id"])
    info = tmp_path / identity["backup_id"] / "bkupInfo.xml"
    info.write_bytes(BUILDER.bkup_info_xml({**identity, "display_name": "other"}))
    assert "different GPO" in BUILDER.import_readiness(tmp_path)
    info.write_bytes(b"")
    assert "bkupInfo.xml unreadable" in BUILDER.import_readiness(tmp_path)


@pytest.mark.parametrize(
    "name,domain",
    [("unrelated-gpo", None), (None, "foreign.test"), ("unrelated-gpo", "foreign.test")],
)
def test_the_backup_report_must_name_the_owned_gpo(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch,
    name: str | None, domain: str | None,
) -> None:
    """Review finding 4: the same GUID under another name or domain fails."""
    record = _case(run)
    _rebackup_file(run, "gpreport.xml").write_bytes(_fr_report(
        record["owned_gpo_id"], name or record["target_name"], domain or _DOMAIN, [R3_ROW],
    ))
    assert _failing(_finalize(run, candidate, monkeypatch)) == {
        "every_case_rebackup_report_matches_fresh_report",
    }


def test_a_backup_report_with_no_name_or_domain_fails(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's probe: identity elements removed, GUID kept."""
    import xml.etree.ElementTree as ET

    path = _rebackup_file(run, "gpreport.xml")
    root = ET.fromstring(path.read_bytes())
    ident = root.find(f"{{{SETTINGS_NS}}}Identifier")
    assert ident is not None
    domain = ident.find(f"{{{TYPES_NS}}}Domain")
    name = root.find(f"{{{SETTINGS_NS}}}Name")
    assert domain is not None and name is not None
    ident.remove(domain)
    root.remove(name)
    path.write_bytes(ET.tostring(root, encoding="utf-16"))
    assert _failing(_finalize(run, candidate, monkeypatch)) == {
        "every_case_rebackup_report_matches_fresh_report",
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(residue=["11111111-2222-3333-4444-555555555555 x"]),
        lambda r: r.update(residue=None),
        lambda r: r["cases"][0].update(foreign_residue=["11111111-2222-3333-4444-555555555555"]),
        lambda r: r["cases"][0].update(foreign_residue=None),
    ],
)
def test_foreign_residue_or_its_absence_fails(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch, mutate: Any,
) -> None:
    result = _read_result(run)
    mutate(result)
    _write_result(run, result)
    assert _failing(_finalize(run, candidate, monkeypatch)) == {"no_foreign_residue"}


# ---------------------------------------------------------------------------
# MAX_PATH: the guest's longest path, measured from the real root
# ---------------------------------------------------------------------------


def test_the_longest_guest_path_is_bounded(candidate: Path) -> None:
    expected = _expected(candidate)
    assert expected["longest_guest_path"] <= BUILDER.GUEST_PATH_LIMIT == 200
    with zipfile.ZipFile(candidate / "fdeploy-cases.zip") as archive:
        names = archive.namelist()
    longest = BUILDER.longest_guest_path(names, ["c1", "c2", "c3", "c4"])
    assert len(longest) == expected["longest_guest_path"]
    assert "Documents & Settings\\fdeploy1.ini" in longest


def test_the_path_bound_is_composed_from_the_real_driver_and_guest() -> None:
    """The bound is only as good as its root: hold it to the scripts that use it."""
    driver = DRIVER_PATH.read_text(encoding="utf-8")
    guest = GUEST_PATH.read_text(encoding="utf-8")
    assert 'STAMP="$(date +%Y%m%d%H%M%S)-$$"' in driver
    assert f'GUEST_ROOT="{BUILDER.GUEST_ROOT_PREFIX}\\\\$STAMP"' in driver
    assert f'GUEST_OUT="$GUEST_ROOT\\{BUILDER.GUEST_OUT_LEAF}"' in driver
    assert "-OutputDir '$GUEST_OUT'" in driver
    run_id = (
        '$runId = "fd-$(Get-Date -Format yyyyMMddHHmmss)-'
        '$(Get-Random -Minimum 1000 -Maximum 9999)"'
    )
    assert run_id in guest
    assert len(BUILDER.GUEST_RUN_ID_WORST) == len("fd-") + 14 + 1 + 4
    assert len(BUILDER.GUEST_STAMP_WORST) == 14 + 1 + 7  # Linux pid_max is 2**22
    for leaf in ("input", "backups", "commands"):
        assert f"Join-Path $work '{leaf}'" in guest
    assert "Join-Path $inputRoot 'cases'" in guest


#: The driver lines that name the guest run root, evaluated by bash itself:
#: a source-text match cannot tell `\\$STAMP` (a separator, then the stamp)
#: from `\$STAMP` (a literal dollar), and the latter shipped in 258c195.
_ROOT_LINES = ("STAMP=", "GUEST_ROOT=", "GUEST_SCRIPTS=", "GUEST_OUT=", "PREPARE=")


def _evaluated_guest_paths() -> dict[str, str]:
    # The driver is a POSIX controller script. On a Windows runner `bash` may
    # be the WSL launcher with no distribution (test_lane_runner_line_endings).
    bash = shutil.which("bash")
    if bash is None or sys.platform == "win32":
        pytest.skip("the driver is a POSIX controller script")
    lines = [
        line for line in DRIVER_PATH.read_text(encoding="utf-8").splitlines()
        if line.startswith(_ROOT_LINES)
    ]
    assert [line.split("=", 1)[0] + "=" for line in lines] == list(_ROOT_LINES)
    names = [prefix.rstrip("=") for prefix in _ROOT_LINES]
    script = "\n".join(
        ["set -euo pipefail", *lines, *(f'printf "%s\\0" "${name}"' for name in names)]
    )
    out = subprocess.run([bash, "-c", script], check=True, capture_output=True).stdout
    values = out.decode("utf-8").split("\0")[:-1]
    return dict(zip(names, values, strict=True))


def test_the_guest_root_is_a_fresh_directory_per_run() -> None:
    """Evaluated, the root is the prefix, a separator, then this run's stamp.

    Two invocations (two shells, so two PIDs) must name different roots, or
    the second run's PREPARE refuses with "run root exists" -- which is how
    the estate found the `\\$STAMP` defect.
    """
    first, second = _evaluated_guest_paths(), _evaluated_guest_paths()
    for paths in (first, second):
        stamp = paths["STAMP"]
        assert re.fullmatch(r"\d{14}-\d{1,7}", stamp), stamp
        root = paths["GUEST_ROOT"]
        assert root == f"{BUILDER.GUEST_ROOT_PREFIX}\\{stamp}"
        assert "$" not in root
        assert paths["GUEST_SCRIPTS"] == f"{root}\\s"
        assert paths["GUEST_OUT"] == f"{root}\\{BUILDER.GUEST_OUT_LEAF}"
        assert f"Test-Path '{root}'" in paths["PREPARE"]
        # The builder's worst case is this same shape with the longest stamp,
        # so the budget it enforces covers the root the driver really uses.
        worst = "\\".join(
            (BUILDER.GUEST_ROOT_PREFIX, BUILDER.GUEST_STAMP_WORST, BUILDER.GUEST_OUT_LEAF)
        )
        assert BUILDER.GUEST_WORK_WORST.startswith(worst + "\\")
        assert len(paths["GUEST_OUT"]) <= len(worst)
    assert first["STAMP"] != second["STAMP"]
    assert first["GUEST_ROOT"] != second["GUEST_ROOT"]


def test_the_builder_refuses_a_candidate_past_the_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(BUILDER, "GUEST_PATH_LIMIT", 150)
    with pytest.raises(ValueError, match="exceeds 150"):
        BUILDER.build(tmp_path)


def test_the_old_layout_would_have_exceeded_the_bound(candidate: Path) -> None:
    """The reviewer measured 205 characters for the first layout."""
    with zipfile.ZipFile(candidate / "fdeploy-cases.zip") as archive:
        names = [n.replace("/c1/", "/r3-flags-1021/") for n in archive.namelist()]
    old_work = (
        "C:\\gpo-studio\\runs\\fdeploy-20261008123456-12345\\out"
        "\\fdeploy-lane-20261008123456-9998"
    )
    old = max((f"{old_work}\\input\\" + n.replace("/", "\\") for n in names), key=len)
    assert len(old) > BUILDER.GUEST_PATH_LIMIT


@pytest.mark.parametrize("status", [1, -1, None])
def test_a_failed_or_unreported_guest_can_never_pass(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch, status: int | None,
) -> None:
    verdict = _verdict(run, candidate, monkeypatch, guest_status=status)
    assert verdict["checks"]["guest_exited_zero"] is False
    assert verdict["passed"] is False


def test_missing_command_output_fails(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (run / "commands" / "c3" / "backup.stderr.txt").unlink()
    assert _failing(_finalize(run, candidate, monkeypatch)) == {"raw_command_artifacts_complete"}


def test_a_deployed_script_that_differs_from_source_fails(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deployed = run / "deployed" / GUEST_PATH.name
    deployed.write_bytes(deployed.read_bytes() + b"\n")
    assert _failing(_finalize(run, candidate, monkeypatch)) == {"deployed_harness_matches_source"}


def test_a_candidate_the_guest_did_not_receive_fails(
    run: Path, candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (run / "candidate.zip").write_bytes(b"other")
    assert _failing(_finalize(run, candidate, monkeypatch)) == {"candidate_delivered_intact"}


def test_a_path_cannot_escape_the_run(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="escapes"):
        FINALIZER._run_file(tmp_path, "../outside.xml")
    with pytest.raises(ValueError, match="no file"):
        FINALIZER._run_file(tmp_path, None)


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


def test_the_driver_hands_the_guest_status_to_the_finalizer() -> None:
    driver = DRIVER_PATH.read_text(encoding="utf-8")
    assert '--guest-status "$GUEST_STATUS"' in driver
    assert driver.index("GUEST_STATUS=$?") < driver.index("finalize_fdeploy_run.py")


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
    bound = set(FINALIZER.LOCAL_FILES.values())
    for module in ("fdeploy.py", "fdeploy_parity.py", "backup.py"):
        assert f"src/gpo_studio/{module}" in bound
    for name in ("fdeploy1.ini.txt", "fdeploy.ini.txt", "provenance.json"):
        assert f"{BUILDER.FIXTURE_DIR}/{name}" in bound


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
    "path", [BUILDER_PATH, FINALIZER_PATH, DRIVER_PATH, GUEST_PATH, PARITY_PATH],
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
    for number, line in enumerate(GUEST_PATH.read_text(encoding="utf-8").splitlines(), 1):
        code = line.split("#", 1)[0]
        assert "??" not in code and "?." not in code, number
        assert not re.search(r"\s(&&|\|\|)\s", code), number
        assert not re.search(r"\)\s*\?\s*[^\s]", code), number


def test_the_guest_script_never_interpolates_a_variable_before_a_colon() -> None:
    """``"$name:"`` parses as a drive-qualified variable; ``${name}:`` is required."""
    scopes = {"env", "global", "script", "local", "using", "private"}
    for number, line in enumerate(GUEST_PATH.read_text(encoding="utf-8").splitlines(), 1):
        for string in re.findall(r'"[^"]*"', line.split("#", 1)[0]):
            for name in re.findall(r"\$([A-Za-z_]\w*):", string):
                assert name.casefold() in scopes, (number, string)


def test_the_guest_removes_only_what_it_registered_and_owns() -> None:
    script = GUEST_PATH.read_text(encoding="utf-8")
    register = script.index("function Register-Target")
    assert script.index('StartsWith("$prefix-")', register) < script.index(
        "$registered[$name] = $null", register
    )
    assert "if (-not $registered.Contains($name)) { return $true }" in script
    # Registration precedes creation, and ownership is recorded the moment
    # New-GPO returns, so cleanup can go by GUID from then on.
    assert script.count("Register-Target $target\n") == 1
    chunk = script.split("Register-Target $target\n")[1].lstrip()
    assert chunk.startswith("$owned = New-GPO")
    assert chunk.index("Set-Owned $target $ownedId") < chunk.index("Import-GPO")
    # Removal by name happens only on the branch where no GUID is known.
    # Removal targets come from Get-Present, whose owned-GUID branch returns
    # before the name match is ever consulted.
    present = script[script.index("function Get-Present"):]
    present = present[: present.index("\n}\n")]
    by_id = 'if ($id) { return @($all | Where-Object { "$($_.Id)" -eq $id }'
    by_name = "return @($all | Where-Object { $_.DisplayName -eq $name }"
    assert by_id in present and by_name in present
    assert present.index(by_id) < present.index(by_name)
    removal = script[script.index("function Remove-Registered"):]
    removal = removal[: removal.index("\n}\n")]
    assert "$targets = @(Get-Present $name)" in removal and "DisplayName" not in removal


#: In-memory Group Policy and AD cmdlets. Functions shadow cmdlets in
#: PowerShell's command resolution, so the real guest script runs unchanged.
#: The modes after ``create-then-throw`` are the 2026-10-08 review's probes.
_MOCK_HARNESS = r"""
param([string]$Script, [string]$Zip, [string]$Out, [string]$StatePath, [string]$Mode)
$ErrorActionPreference = 'Stop'
$global:Store = [ordered]@{}
$global:RemoveFailures = @{}
$global:Thrown = $false
$global:Deleted = @()
$global:Reused = $false
$global:RenamedGuid = $null
$global:Backed = $false
$global:Store['31b2f340-016d-11d2-945f-00c04fb984f9'] = 'Default Domain Policy'
$global:Store["$([guid]::NewGuid())"] = 'unrelated-gpo'
$global:BadNetPath = 'The network path was not found.'
function Import-Module { [CmdletBinding()] param([Parameter(Position = 0)]$Name) }
function New-GPO {
    [CmdletBinding()] param([string]$Name, [string]$Domain)
    $id = [guid]::NewGuid()
    $global:Store["$id"] = $Name
    if ($Mode -eq 'remove-fails-once') { $global:RemoveFailures["$id"] = 1 }
    if ($Mode -eq 'create-then-throw' -and -not $global:Thrown) {
        $global:Thrown = $true
        throw 'created, but the response was lost'
    }
    [pscustomobject]@{ Id = $id; DisplayName = $Name; Path = "cn={$id}" }
}
function Get-GPO {
    [CmdletBinding()] param([switch]$All, $Guid, [string]$Domain, [string]$Name)
    if ($All) {
        # name-reused: another party creates a GPO under a name this run
        # registered, once the run's own GPO of that name has been removed.
        if ($Mode -eq 'name-reused' -and $global:Deleted.Count -gt 0 -and -not $global:Reused) {
            $global:Reused = $true
            $global:Store['11111111-2222-3333-4444-555555555555'] = $global:Deleted[0]
        }
        # enum-*: the enumeration itself fails, comes back empty, or comes
        # back without the domain's control GPO, once something was removed.
        $afterDelete = $global:Deleted.Count -gt 0
        if ($Mode -eq 'enum-fails' -and $afterDelete) { throw 'directory enumeration failed' }
        if ($Mode -eq 'enum-empty' -and $afterDelete) { return @() }
        $keys = @($global:Store.Keys)
        if ($Mode -eq 'enum-no-control' -and $afterDelete) {
            $keys = @($keys | Where-Object { $_ -ne '31b2f340-016d-11d2-945f-00c04fb984f9' })
        }
        return @($keys | ForEach-Object {
            [pscustomobject]@{ Id = [guid]$_; DisplayName = $global:Store[$_]; Path = "cn={$_}" } })
    }
    $k = "$Guid"
    $gone = $global:Deleted.Count -gt 0 -and -not $global:Store.Contains($k)
    if (($Mode -eq 'guid-query-fails' -and $gone) -or
        ($Mode -eq 'renamed-and-query-fails' -and $global:RenamedGuid -eq $k)) {
        throw 'directory query failed: access denied'
    }
    # The 258c195 review's probe: a COMException for ERROR_BAD_NETPATH
    # (0x80070035) whose message says "not found" about something else.
    if (($Mode -eq 'renamed-network-error' -and $global:RenamedGuid -eq $k) -or
        ($Mode -eq 'remove-network-error' -and $global:Deleted.Count -eq 0 -and $global:Backed)) {
        throw [System.Runtime.InteropServices.COMException]::new($global:BadNetPath, -2147024843)
    }
    if (-not $global:Store.Contains($k)) { throw "A GPO with ID {$k} was not found in lab.test." }
    [pscustomobject]@{ Id = [guid]$k; DisplayName = $global:Store[$k]; Path = "cn={$k}" }
}
function Remove-GPO {
    [CmdletBinding(SupportsShouldProcess = $true)] param($Guid, [string]$Domain)
    $k = "$Guid"
    if ($global:RemoveFailures[$k] -gt 0) {
        $global:RemoveFailures[$k]--
        throw 'transient failure'
    }
    if ($Mode -eq 'remove-network-error') {
        throw [System.Runtime.InteropServices.COMException]::new($global:BadNetPath, -2147024843)
    }
    if (-not $global:Store.Contains($k)) { throw "A GPO with ID {$k} was not found in lab.test." }
    $global:Deleted += $global:Store[$k]
    $global:Store.Remove($k)
}
function Import-GPO {
    [CmdletBinding(SupportsShouldProcess = $true)] param($BackupId, $Path, $TargetGuid, $Domain)
    [pscustomobject]@{ Id = $TargetGuid }
}
function Get-ADObject {
    [CmdletBinding()] param($Identity, $Properties)
    [pscustomobject]@{
        gPCUserExtensionNames = @('[{A}{B}]'); gPCFileSysPath = @('/nonexistent-sysvol')
    }
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
function Backup-GPO {
    [CmdletBinding()] param($Guid, $Domain, $Path)
    # renamed-and-query-fails: the owned GPO is renamed and its GUID becomes
    # unreadable (access denied) before cleanup.
    $renames = @('renamed-and-query-fails', 'renamed-network-error')
    if ($Mode -in $renames -and -not $global:RenamedGuid) {
        $global:RenamedGuid = "$Guid"
        $global:Store["$Guid"] = 'renamed-owned-gpo'
    }
    $global:Backed = $true
    $id = [guid]::NewGuid()
    New-Item -ItemType Directory -Force -Path (Join-Path $Path "{$id}") | Out-Null
    [pscustomobject]@{ Id = $id; GpoId = $Guid }
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
    @{ status = $status; remaining = @($global:Store.Values); deleted = @($global:Deleted) } |
        ConvertTo-Json | Set-Content -LiteralPath $StatePath
}
"""


def _mock_candidate(path: Path) -> None:
    manifest = (
        '<Backups xmlns="http://www.microsoft.com/GroupPolicy/GPOOperations/Manifest">'
        "<BackupInst><GPOGuid>{AAAAAAAA-0000-0000-0000-000000000001}</GPOGuid>"
        "<ID>{BBBBBBBB-0000-0000-0000-000000000001}</ID></BackupInst></Backups>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        for case in ("c1", "c2"):
            archive.writestr(f"cases/{case}/manifest.xml", manifest)


def _run_mocked_guest(tmp_path: Path, mode: str) -> dict[str, Any]:
    pwsh = shutil.which("pwsh") or shutil.which("powershell.exe")
    if pwsh is None:
        pytest.skip("no PowerShell interpreter available")
    harness = tmp_path / "harness.ps1"
    harness.write_text(_MOCK_HARNESS, encoding="utf-8")
    zip_path = tmp_path / "candidate.zip"
    _mock_candidate(zip_path)
    out = tmp_path / "out"
    out.mkdir()
    state = tmp_path / "state.json"
    subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-File", str(harness), "-Script", str(GUEST_PATH),
         "-Zip", str(zip_path), "-Out", str(out), "-StatePath", str(state), "-Mode", mode],
        capture_output=True, text=True, timeout=300, check=True,
    )
    final = cast(dict[str, Any], json.loads(state.read_text("utf-8-sig")))
    (run_dir,) = list(out.iterdir())
    final["result"] = json.loads((run_dir / "result.json").read_text("utf-8-sig"))
    return final


def _listed(value: object) -> list[Any]:
    """ConvertTo-Json writes a one-element array as its element; undo that."""
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


#: What the mocked domain holds before the run: the control GPO every domain
#: has, and one unrelated GPO the run must never touch.
_FOUND = ["Default Domain Policy", "unrelated-gpo"]


def test_the_mocked_guest_run_leaves_only_what_it_found(tmp_path: Path) -> None:
    """The control: a clean run removes every GPO it made and nothing else."""
    state = _run_mocked_guest(tmp_path, "normal")
    assert state["status"] == 0
    assert _listed(state["remaining"]) == _FOUND
    result = state["result"]
    assert set(result) == FINALIZER._RESULT_KEYS
    assert result["schema_version"] == FINALIZER._RESULT_SCHEMA_VERSION
    assert result["cleanup_state_restored"] is True and result["residue"] == []
    assert len(result["cases"]) == 2
    for record in result["cases"]:
        assert set(record) == FINALIZER._CASE_KEYS
        assert record["cleanup_succeeded"] and record["absence_confirmed"]
        assert record["foreign_residue"] == []
        assert record["target_name"].startswith(f"zz-studio-fd-{result['run_id']}-")
        assert record["user_extension_names"] == "[{A}{B}]"
        assert [f["name"] for f in record["rebackup_files"]] == list(FINALIZER.FDEPLOY_FILES)
        assert all(set(f) == FINALIZER._REBACKUP_FILE_KEYS for f in record["rebackup_files"])
    assert [c["case_dir"] for c in result["cases"]] == ["c1", "c2"]


def test_a_transient_removal_failure_is_retried(tmp_path: Path) -> None:
    state = _run_mocked_guest(tmp_path, "remove-fails-once")
    assert _listed(state["remaining"]) == _FOUND
    assert state["status"] == 0
    assert state["result"]["cleanup_state_restored"] is True


def test_a_gpo_created_without_a_returned_id_is_removed_by_name(tmp_path: Path) -> None:
    state = _run_mocked_guest(tmp_path, "create-then-throw")
    assert _listed(state["remaining"]) == _FOUND
    assert state["status"] == 1  # the case still fails honestly
    first = state["result"]["cases"][0]
    assert first["import_succeeded"] is False
    assert first["owned_gpo_id"] is None
    assert first["absence_confirmed"] is True
    assert state["result"]["cleanup_state_restored"] is True


def test_a_foreign_gpo_under_a_registered_name_is_reported_never_deleted(tmp_path: Path) -> None:
    """Review finding 1 (high): name reuse after removal must not be cleaned up."""
    state = _run_mocked_guest(tmp_path, "name-reused")
    result = state["result"]
    first = result["cases"][0]
    # The foreign GPO survives: only the two GPOs the run created were removed.
    assert len(_listed(state["deleted"])) == 2
    assert first["target_name"] in _listed(state["remaining"])
    assert _listed(first["foreign_residue"]) == ["11111111-2222-3333-4444-555555555555"]
    assert "foreign GPO(s) hold" in first["error"]
    # The run's own GPO is gone and that much is still reported truthfully.
    assert first["cleanup_succeeded"] is True and first["absence_confirmed"] is True
    assert result["cleanup_state_restored"] is False
    assert _listed(result["residue"]) == [
        f"11111111-2222-3333-4444-555555555555 {first['target_name']}"
    ]
    assert state["status"] == 1


def _reported_absent_but_alive(state: dict[str, Any]) -> list[str]:
    """Cases whose GPO the guest called gone while the domain still holds it."""
    alive = set(_listed(state["remaining"]))
    # The renamed-* modes rename the first case's GPO; it is alive under that name.
    renamed_alive = "renamed-owned-gpo" in alive
    return [
        c["target_name"] for index, c in enumerate(state["result"]["cases"])
        if c["absence_confirmed"] is True
        and (c["target_name"] in alive or (index == 0 and renamed_alive))
    ]


@pytest.mark.parametrize("mode", ["guid-query-fails", "renamed-and-query-fails"])
def test_guid_lookup_errors_cannot_decide_absence(tmp_path: Path, mode: str) -> None:
    """Absence comes from a complete enumeration, so a failing GUID lookup is moot.

    The owned GPO -- even renamed -- is removed by its GUID and confirmed gone
    by enumeration; nothing is left and nothing is misreported.
    """
    state = _run_mocked_guest(tmp_path, mode)
    assert _listed(state["remaining"]) == _FOUND
    assert state["status"] == 0
    assert _reported_absent_but_alive(state) == []
    assert all(c["cleanup_succeeded"] and c["absence_confirmed"]
               for c in state["result"]["cases"])


def test_a_network_not_found_error_on_the_guid_lookup_is_not_absence(tmp_path: Path) -> None:
    """The 258c195 review's probe: COMException 0x80070035 on the renamed GPO's lookup.

    Against 258c195 the renamed GPO survived while cleanup, absence and the end
    state all read true. Now the GUID lookup is never asked about absence: the
    renamed GPO is removed by its GUID and enumeration confirms it.
    """
    state = _run_mocked_guest(tmp_path, "renamed-network-error")
    assert "renamed-owned-gpo" not in _listed(state["remaining"])
    assert _listed(state["remaining"]) == _FOUND
    assert _reported_absent_but_alive(state) == []
    assert state["status"] == 0


def test_a_gpo_that_cannot_be_removed_is_never_reported_absent(tmp_path: Path) -> None:
    """Remove-GPO and the GUID lookup both fail with 'network path not found'."""
    state = _run_mocked_guest(tmp_path, "remove-network-error")
    result = state["result"]
    first = result["cases"][0]
    assert first["target_name"] in _listed(state["remaining"])
    assert first["cleanup_succeeded"] is False
    assert first["absence_confirmed"] is False
    assert "network path was not found" in first["error"]
    assert result["cleanup_state_restored"] is False
    assert first["target_name"] in " ".join(_listed(result["residue"]))
    assert _reported_absent_but_alive(state) == []
    assert state["status"] == 1


@pytest.mark.parametrize("mode", ["enum-fails", "enum-empty", "enum-no-control"])
def test_an_enumeration_that_fails_or_is_incomplete_fails_cleanup(
    tmp_path: Path, mode: str
) -> None:
    """Absence needs a complete enumeration; without one, cleanup fails."""
    state = _run_mocked_guest(tmp_path, mode)
    result = state["result"]
    first = result["cases"][0]
    assert first["absence_confirmed"] is False
    assert first["cleanup_succeeded"] is False
    assert result["cleanup_state_restored"] is False
    assert state["status"] == 1


def test_the_guest_never_infers_absence_from_an_exception() -> None:
    script = GUEST_PATH.read_text(encoding="utf-8")
    code = "\n".join(line.split("#", 1)[0] for line in script.splitlines())
    assert "Test-NotFound" not in code
    assert "not found" not in code.casefold() and "notfound" not in code.casefold()
    cleanup = code[code.index("$registered = "):code.index("function Add-RecordError")]
    assert "Get-GPO -Guid" not in cleanup
    # Every enumeration goes through the checked one, final scan included.
    assert script.count("Get-GPO -All") == 1
    assert "Get-GPO -All -Domain $Domain -ErrorAction Stop" in script
    assert "$controlGpoId = '31b2f340-016d-11d2-945f-00c04fb984f9'" in script
