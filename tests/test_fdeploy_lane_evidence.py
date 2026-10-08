"""The fdeploy lane's certifying run, banked: `fd-20261008102559-9746`.

One lane on its own commit (`6b76fad`), banked the way the firewall lane and
the Plan 034 object-security successor were: the controller's local run
directory verbatim, plus `controller-candidate/` (the builder's output) and
`controller.log`. The generic gates in `test_committed_evidence.py` cover the
registry, the manifest-form binding at the commit and the live-harness hashes.
This file pins what is specific to this pack:

* every byte is accounted for;
* the shipping finalizer, run over the banked bytes alone, writes the banked
  verdict again;
* the bound builder still produces the banked candidate byte for byte (the
  archive's container bytes on POSIX only; its members everywhere);
* the four claims the results doc makes are re-derived here from the raw
  Windows artifacts, not read back out of the verdict: SYSVOL and the
  re-export hold the candidate's bytes, Studio's `read_backup` over Windows'
  own backup agrees with Windows' fresh report row for row, and each report
  names the GPO the run owned;
* the option rendering per `Flags`, which the lane records and does not
  assert, is the table `docs/plan-033/fdeploy-results.md` publishes.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import runpy
import shutil
import sys
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest

from gpo_studio.backup import read_backup
from gpo_studio.fdeploy import native_digest, validate_fdeploy
from gpo_studio.fdeploy_parity import (
    folder_redirection_rendering,
    reader_claims,
    reader_report_differences,
    report_identity,
)

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "docs/plan-033/wp4-evidence/fdeploy"
CANDIDATE = PACK / "controller-candidate"
VERDICT_PATH = "wp4-evidence/fdeploy/verification.json"
RUN_ID = "fd-20261008102559-9746"
COMMIT = "6b76fad177183999cab7b2bdf35d16baf8fed014"
#: The exploratory pass that preceded the review hardening. History only: it
#: binds the guest, builder and finalizer as they were before the four review
#: fixes, and it was never banked or registered.
SUPERSEDED_EXPLORATORY_COMMIT = "379e59b"
CONTROLLER_LOG_SHA256 = "5421e76b81a8aa94657897a1ce727b700c204a735bc6de0ebd3d962d3909300a"
SETTINGS = "DomainSysvol/GPO/User/Documents & Settings"
FDEPLOY_FILES = ("fdeploy1.ini", "fdeploy.ini")
USER_EXTENSION_PAIR = (
    "[{25537BA6-77A8-11D2-9B6C-0000F8080861}{88E729D6-BDC1-11D1-BD2A-00C04FB9603F}]"
)
DOCUMENTS = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"
EVERYONE = "s-1-1-0"
FULL_PATH = r"\\zz-studio-fileserver\zzredir\%USERNAME%\Documents"

#: case id -> (case dir, Flags, verbatim R3 bytes)
CASES = {
    "r3-flags-1021": ("c1", 1021, True),
    "r3-flags-1020": ("c2", 1020, False),
    "r3-flags-1023": ("c3", 1023, False),
    "r3-flags-3069": ("c4", 3069, False),
}

#: Windows' option rendering per `Flags`, as recorded by this run. Recorded,
#: never asserted by the lane (WI-066): `fdeploy.py` decodes no bit, so there
#: is no Studio claim to grade. Only the elements that vary across the four
#: cases are listed; the rest are constant and listed in `CONSTANT_OPTIONS`.
VARYING_OPTIONS = {
    1021: {"MoveContents": "true", "FollowParent": "false", "RedirectToLocal": "false"},
    1020: {"MoveContents": "false", "FollowParent": "false", "RedirectToLocal": "false"},
    1023: {"MoveContents": "true", "FollowParent": "true", "RedirectToLocal": "false"},
    3069: {"MoveContents": "true", "FollowParent": "false", "RedirectToLocal": "true"},
}
CONSTANT_OPTIONS = {
    "ApplyToDownLevel": "false",
    "ConfigurationControl": "GP",
    "DoNotCare": "false",
    "GrantExclusiveRights": "false",
    "PolicyRemovalBehavior": "RestoreContents",
    "PrimaryComputerEvaluation": "PrimaryComputerPolicyDisabled",
}

FINALIZER_PATH = ROOT / "scripts/windows-oracle/finalize_fdeploy_run.py"
BUILDER = runpy.run_path(str(ROOT / "scripts/plan-033/build-fdeploy-candidate.py"))


def _load(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8-sig")))


VERDICT = _load(PACK / "verification.json")
RESULT = _load(PACK / "result.json")
RECORDS = {record["case_dir"]: record for record in RESULT["cases"]}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _guid(value: str) -> str:
    return value.strip("{}").casefold()


#: Why the archive's SHA-256 is exact only on POSIX, which the bound builder
#: does not control (the report-parity and firewall banks met the same thing;
#: the cross-lane deterministic-zip sweep, with lane re-runs, is scheduled
#: rather than done here, since the builder is bound). `zipfile.ZipInfo`
#: defaults `create_system` to 0 on Windows and 3 elsewhere, and that byte sits
#: in every central-directory entry; forcing it back to 3 is not enough,
#: because Windows CI on 3.14 also links a different zlib, so the deflated
#: member streams can differ too. The certified candidate is built on the
#: POSIX controller, so the banked hash is the POSIX one. Off POSIX the tests
#: below hold the archive to everything the guest consumes instead: member
#: names, order, timestamps, compression method, attributes and bytes.
#: `test_the_host_byte_changes_only_the_container` pins the explanation.
ARCHIVE = "fdeploy-cases.zip"
POSIX = os.name == "posix"
#: The finalizer's one check that compares the archive's container bytes.
CONTAINER_CHECK = "candidate_rebuilds_from_bound_builder"


def _archive_members(data: bytes) -> list[tuple[str, tuple[int, ...], int, int, bytes]]:
    """Everything in a ZIP the lane consumes, without the container's bytes."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return [
            (
                info.filename,
                tuple(info.date_time),
                info.compress_type,
                info.external_attr,
                archive.read(info),
            )
            for info in archive.infolist()
        ]


def _candidate_case_files(case_dir: str) -> dict[str, bytes]:
    with zipfile.ZipFile(CANDIDATE / "fdeploy-cases.zip") as archive:
        found = {}
        for name in archive.namelist():
            # cases/<case dir>/{backup id}/DomainSysvol/.../<file>
            parts = name.split("/")
            if parts[:2] == ["cases", case_dir] and "/".join(parts[3:-1]) == SETTINGS:
                found[parts[-1]] = archive.read(name)
    assert set(found) == set(FDEPLOY_FILES), case_dir
    return found


def _rebackup_root(record: dict[str, Any]) -> tuple[Path, str]:
    root = PACK / record["rebackup_dir"]
    return root, record["rebackup_id"]


def test_every_file_in_the_pack_is_accounted_for_and_intact() -> None:
    """The run directory's files are exactly the verdict's artifacts, plus three.

    The three are what the controller adds around the run directory:
    `verification.json` (the verdict itself, which cannot hash itself),
    `controller.log`, pinned here, and `controller-candidate/`, whose files
    the verdict's `candidate` block hashes.
    """
    on_disk = {p.relative_to(PACK).as_posix() for p in PACK.rglob("*") if p.is_file()}
    candidate = {"controller-candidate/" + name for name in VERDICT["candidate"]}
    expected = set(VERDICT["artifacts"]) | candidate | {"verification.json", "controller.log"}
    assert on_disk == expected
    for relative, digest in VERDICT["artifacts"].items():
        assert _sha((PACK / relative).read_bytes()) == digest, relative
    for name, digest in VERDICT["candidate"].items():
        assert _sha((CANDIDATE / name).read_bytes()) == digest, name
    assert _sha((PACK / "controller.log").read_bytes()) == CONTROLLER_LOG_SHA256
    # The guest received exactly the controller's candidate.
    assert (PACK / "candidate.zip").read_bytes() == (CANDIDATE / "fdeploy-cases.zip").read_bytes()
    assert (PACK / "builder.stdout.txt").read_bytes() == (
        CANDIDATE / "builder.stdout.txt"
    ).read_bytes()


def test_the_verdict_is_a_clean_29_check_pass_at_its_commit() -> None:
    assert VERDICT["run_id"] == RESULT["run_id"] == RUN_ID
    assert VERDICT["schema_version"] == 2
    assert VERDICT["transport"] == "psdirect"
    assert VERDICT["passed"] is True
    assert VERDICT["checks_complete"] is True
    assert VERDICT["guest_status"] == 0
    assert VERDICT["harness_error"] is None
    assert VERDICT["comparison_error"] is None
    assert VERDICT["environment_violations"] == []
    assert len(VERDICT["checks"]) == 29
    assert all(VERDICT["checks"].values())
    assert VERDICT["source"]["commit"] == COMMIT
    assert VERDICT["source"]["dirty"] is False
    assert not VERDICT["source"]["commit"].startswith(SUPERSEDED_EXPLORATORY_COMMIT)
    environment = VERDICT["environment"]
    assert environment["server_build"] == "26100"
    assert environment["server_caption"] == "Microsoft Windows Server 2025 Standard"
    assert environment["powershell_version"].startswith("5.1.")
    assert environment["computer_system_name"] == "LABMS01"
    assert environment["computer_system_domain_role"] == 3
    assert RESULT["residue"] == []
    assert RESULT["cleanup_state_restored"] is True


def test_the_controller_log_carries_the_verdict_and_its_evidence_tag() -> None:
    log = (PACK / "controller.log").read_text(encoding="utf-8")
    assert f"EVIDENCE_TAG=created evidence/{RUN_ID} at {COMMIT[:12]}" in log
    banked = (CANDIDATE / "builder.stdout.txt").read_text(encoding="utf-8")
    assert log.startswith(banked)
    # The finalizer prints the verdict it writes; the log holds it verbatim.
    assert (PACK / "verification.json").read_text(encoding="utf-8") in log


def test_the_shipping_finalizer_writes_the_banked_verdict_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-graded today, from the banked bytes alone, it is the recorded verdict.

    The finalizer is run end to end (`--no-tag`) over a copy of the run
    directory and the controller's candidate, so the rebuild of the candidate
    from the bound builder is part of the re-grade. Three fields are the
    controller's state at finalize time and cannot be repeated: the commit
    `git rev-parse` returns, whether the tree is dirty, and the
    `source_tree_clean` check that follows from it (and `passed`, which
    includes it). Everything else must come out identical.

    Off POSIX, `candidate_rebuilds_from_bound_builder` compares archive
    container bytes the host decides (see `ARCHIVE`), so it is held only where
    the controller runs; every other check must still pass, and
    `test_the_builder_still_produces_the_banked_candidate` holds the archive's
    members identical on every platform.
    """
    run = tmp_path / "run"
    for relative in VERDICT["artifacts"]:
        target = run / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PACK / relative, target)
    candidate = tmp_path / "candidate"
    shutil.copytree(CANDIDATE, candidate)

    finalizer = runpy.run_path(str(FINALIZER_PATH))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(FINALIZER_PATH),
            str(run),
            "--candidate-root", str(candidate),
            "--repo-root", str(ROOT),
            "--guest-status", "0",
            "--no-tag",
        ],
    )
    with redirect_stdout(io.StringIO()):
        finalizer["main"]()
    regraded = _load(run / "verification.json")

    recorded = json.loads(json.dumps(VERDICT))
    for verdict in (regraded, recorded):
        verdict["source"].pop("commit")
        verdict["source"].pop("dirty")
        verdict.pop("passed")
    assert recorded["checks"].pop("source_tree_clean") is True
    regraded["checks"].pop("source_tree_clean")
    if not POSIX:
        assert recorded["checks"].pop(CONTAINER_CHECK) is True
        regraded["checks"].pop(CONTAINER_CHECK)
    assert all(regraded["checks"].values()), [
        name for name, ok in regraded["checks"].items() if not ok
    ]
    assert regraded == recorded


def test_the_builder_still_produces_the_banked_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The certified request is reproducible: same bytes, same stdout.

    Every file is rebuilt byte for byte on POSIX, where the controller builds
    it. Off POSIX the archive's container bytes are the host's (see
    `ARCHIVE`), so it is held to identical members instead; every other file
    is still exact.
    """
    monkeypatch.setattr(sys, "argv", ["build-fdeploy-candidate.py", str(tmp_path)])
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert BUILDER["main"]() == 0
    for name, digest in VERDICT["candidate"].items():
        if name == "builder.stdout.txt":
            continue
        if name == ARCHIVE:
            assert _archive_members((tmp_path / name).read_bytes()) == _archive_members(
                (CANDIDATE / name).read_bytes()
            )
            if not POSIX:
                continue
        assert _sha((tmp_path / name).read_bytes()) == digest, name
    banked = (CANDIDATE / "builder.stdout.txt").read_text(encoding="utf-8")
    if not POSIX:
        # The builder prints the archive's hash; off POSIX that line names the
        # host's container bytes, and nothing else in the stdout may move.
        banked_line = f"{ARCHIVE} sha256={VERDICT['candidate'][ARCHIVE]}\n"
        assert banked.startswith(banked_line)
        rebuilt = _sha((tmp_path / ARCHIVE).read_bytes())
        banked = f"{ARCHIVE} sha256={rebuilt}\n" + banked.removeprefix(banked_line)
    assert stdout.getvalue() == banked


@pytest.mark.skipif(not POSIX, reason="off POSIX the host is already the one that differs")
def test_the_host_byte_changes_only_the_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forcing Windows' `create_system` default moves the hash and nothing else."""
    original = zipfile.ZipInfo.__init__

    def windows_default(self: zipfile.ZipInfo, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        self.create_system = 0

    monkeypatch.setattr(zipfile.ZipInfo, "__init__", windows_default)
    monkeypatch.setattr(sys, "argv", ["build-fdeploy-candidate.py", str(tmp_path)])
    with redirect_stdout(io.StringIO()):
        assert BUILDER["main"]() == 0
    data = (tmp_path / ARCHIVE).read_bytes()
    banked = (CANDIDATE / ARCHIVE).read_bytes()
    assert _sha(data) != _sha(banked)
    assert _archive_members(data) == _archive_members(banked)
    assert _sha((tmp_path / "expected.json").read_bytes()) == VERDICT["candidate"]["expected.json"]


def test_the_cases_are_the_four_the_results_doc_names() -> None:
    expected = _load(CANDIDATE / "expected.json")
    assert [c["case_id"] for c in expected["cases"]] == list(CASES)
    for case in expected["cases"]:
        case_dir, flags, verbatim = CASES[case["case_id"]]
        assert (case["case_dir"], case["flags"], case["verbatim_r3"]) == (
            case_dir,
            flags,
            verbatim,
        )
    assert sorted(RECORDS) == sorted(d for d, _, _ in CASES.values())


@pytest.mark.parametrize("case_id", list(CASES))
def test_windows_kept_the_candidate_bytes_through_import_and_backup(case_id: str) -> None:
    """SYSVOL after `Import-GPO`, and the `Backup-GPO` re-export, are the candidate.

    Re-derived from the pulled backup directory and the guest's base64, not
    from the verdict's comparison block.
    """
    case_dir, flags, verbatim = CASES[case_id]
    record = RECORDS[case_dir]
    candidate = _candidate_case_files(case_dir)

    sysvol = {f["relative_path"]: (f["length"], f["sha256"]) for f in record["sysvol_files"]}
    assert sysvol == {
        name: (len(data), _sha(data)) for name, data in candidate.items()
    }

    root, backup_id = _rebackup_root(record)
    for name in FDEPLOY_FILES:
        pulled = (root / backup_id / SETTINGS / name).read_bytes()
        assert pulled == candidate[name], (case_id, name)
    for entry in record["rebackup_files"]:
        decoded = base64.b64decode(entry["base64"], validate=True)
        assert decoded == candidate[entry["name"]], (case_id, entry["name"])
        assert entry["sha256"] == _sha(decoded) and entry["length"] == len(decoded)

    policy = candidate["fdeploy1.ini"]
    assert f"Flags={flags}\r\n".encode("utf-16-le") in policy
    r3_policy = BUILDER["r3_bytes"]()[1]
    assert (policy == r3_policy) is verbatim
    assert record["user_extension_names"] == USER_EXTENSION_PAIR


@pytest.mark.parametrize("case_id", list(CASES))
def test_studio_reads_windows_backup_as_windows_reports_it(case_id: str) -> None:
    """`read_backup` over Windows' backup agrees with the fresh GPMC report.

    Row for row on (folder id, principal SID, destination), with no structural
    finding and no parse warning, and the report and the backup both name the
    GPO the run owned. An empty rendering cannot count as agreement.
    """
    case_dir, flags, _ = CASES[case_id]
    record = RECORDS[case_dir]
    owned = _guid(record["owned_gpo_id"])
    report_bytes = (PACK / record["report_file"]).read_bytes()
    assert _sha(report_bytes) == record["report_sha256"]

    identity = report_identity(report_bytes)
    assert _guid(identity.guid) == owned
    assert identity.name == record["target_name"]
    assert identity.domain.casefold() == RESULT["domain"].casefold()
    assert record["target_name"].startswith(f"zz-studio-fd-{RUN_ID}-")

    fresh = folder_redirection_rendering(report_bytes)
    assert fresh.extension_count == 1
    assert fresh.errors == ()
    assert fresh.computer_side_extensions == 0
    assert fresh.keys() == [(DOCUMENTS, EVERYONE, FULL_PATH)]

    root, backup_id = _rebackup_root(record)
    backup = read_backup(root)
    assert len(backup.gpos) == 1
    gpo = backup.gpos[0]
    assert _guid(gpo.guid) == owned
    document = gpo.fdeploy
    assert document is not None
    assert validate_fdeploy(document) == ()
    assert not document.parse_warnings
    windows_policy = (root / backup_id / SETTINGS / "fdeploy1.ini").read_bytes()
    assert native_digest(document)[0] == _sha(windows_policy)

    claims = reader_claims(document)
    assert [c.to_json() for c in claims] == [
        {
            "folder_guid": DOCUMENTS,
            "principal": EVERYONE,
            "full_path": FULL_PATH,
            "flags": flags,
            "flags_text": str(flags),
        }
    ]
    assert claims and reader_report_differences(claims, fresh) == []

    gpreport = (root / backup_id / "gpreport.xml").read_bytes()
    rebackup_identity = report_identity(gpreport)
    assert _guid(rebackup_identity.guid) == owned
    assert rebackup_identity.name == record["target_name"]
    assert folder_redirection_rendering(gpreport) == fresh


def test_the_option_rendering_per_flags_is_the_recorded_table() -> None:
    """Recorded, not asserted, by the lane: data for WI-066.

    Each case's rendering is re-read from its raw report, equals the table the
    results doc publishes, and matches the 2026-10-08 probe as the builder
    banked it. None of it is a Studio claim.
    """
    comparison = VERDICT["comparison"]["cases"]
    for case_id, (case_dir, flags, _) in CASES.items():
        report = (PACK / RECORDS[case_dir]["report_file"]).read_bytes()
        (row,) = folder_redirection_rendering(report).redirections
        options = dict(row.options)
        assert options == {**CONSTANT_OPTIONS, **VARYING_OPTIONS[flags]}, case_id
        assert options == BUILDER["PROBE_20261008_OPTIONS"][flags], case_id
        recorded = comparison[case_id]["recorded"]
        assert recorded["flags"] == flags
        assert recorded["windows_options"] == [options]
        assert recorded["matches_probe_20261008"] is True
    assert VERDICT["recorded_not_asserted"] == [
        "Windows option rendering per Flags value (WI-066 data)",
        "gPCUserExtensionNames after Import-GPO",
    ]
    results = (ROOT / "docs/plan-033/fdeploy-results.md").read_text(encoding="utf-8")
    assert RUN_ID in results and COMMIT[:7] in results
    for flags in VARYING_OPTIONS:
        assert f"| {flags} |" in results


def test_the_verdict_is_registered_live() -> None:
    registry = runpy.run_path(str(ROOT / "tests/test_committed_evidence.py"))
    assert registry["LANE_VERDICTS"][VERDICT_PATH] == "finalize_fdeploy_run.py"
    assert VERDICT_PATH in registry["LIVE_VERDICTS"]
