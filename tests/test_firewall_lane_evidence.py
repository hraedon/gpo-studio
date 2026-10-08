"""The firewall lane's certifying run, banked: `firewall-20261008094055-2092337`.

One lane on its own commit (`a6e0002`), banked the way the Plan 034
object-security successor was: the controller's local run directory verbatim,
plus `controller-candidate/` (the builder's output) and `controller.log`. The
generic gates in `test_committed_evidence.py` cover the registry, the
manifest-form binding at the commit and the live-harness hashes. This file pins
what is specific to this pack: every byte is accounted for, the shipping
finalizer still grades the banked record a pass, the builder still produces the
banked candidate, and the two observations the verdict records without
asserting (the tool GUIDs) say what the results doc says they say.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import runpy
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest

from gpo_studio.firewall_policy import FIREWALL_TOOL_GUID, from_registry_records
from gpo_studio.registry_pol import parse

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "docs/plan-033/wp3-evidence/firewall-20261008/firewall"
VERDICT_PATH = "wp3-evidence/firewall-20261008/firewall/verification.json"
RUN_ID = "firewall-20261008094055-2092337"
COMMIT = "a6e0002dac0d65d6ae2b969a23636bf284061da1"
CONTROLLER_LOG_SHA256 = "a60d70aba6ae507fd06144cd4576f621e5c881e7ddc62f4817ba80322f0995a5"
REGISTRY_CSE_GUID = "{35378EAC-683F-11D2-A89A-00C04FBBCFA2}"
ADMIN_TEMPLATES_TOOL_GUID = "{D02B1F72-3407-48AE-BA88-E8213C6761F1}"

FINALIZER = runpy.run_path(str(ROOT / "scripts/windows-oracle/finalize_firewall_run.py"))
BUILDER = runpy.run_path(str(ROOT / "scripts/plan-033/build-firewall-candidate.py"))


def _load(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8-sig")))


VERDICT = _load(PACK / "verification.json")
RESULT = _load(PACK / "result.json")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
        assert _sha((PACK / "controller-candidate" / name).read_bytes()) == digest, name
    assert _sha((PACK / "controller.log").read_bytes()) == CONTROLLER_LOG_SHA256


def test_the_verdict_is_a_clean_36_check_pass_at_its_commit() -> None:
    assert VERDICT["run_id"] == RESULT["run_id"] == RUN_ID
    assert VERDICT["schema_version"] == 2
    assert VERDICT["transport"] == "psdirect"
    assert VERDICT["passed"] is True
    assert VERDICT["harness_error"] is None
    assert VERDICT["environment_violations"] == []
    assert len(VERDICT["checks"]) == 36
    assert all(VERDICT["checks"].values())
    assert VERDICT["source"]["commit"] == COMMIT
    assert VERDICT["source"]["dirty"] is False
    environment = VERDICT["environment"]
    assert environment["server_build"] == "26100"
    assert environment["server_caption"] == "Microsoft Windows Server 2025 Standard"
    assert environment["powershell_version"].startswith("5.1.")
    assert environment["computer_system_name"] == "LABMS01"
    assert environment["computer_system_domain_role"] == 3


def test_the_controller_log_records_the_run_and_its_evidence_tag() -> None:
    log = (PACK / "controller.log").read_text(encoding="utf-8")
    assert f"EVIDENCE_TAG=created evidence/{RUN_ID} at {COMMIT[:12]}" in log
    for name, digest in VERDICT["candidate"].items():
        if name != "builder.stdout.txt":
            assert f"{name} sha256={digest}" in log


def test_the_shipping_finalizer_still_grades_the_banked_record_a_pass() -> None:
    """Regraded today, from the banked bytes alone, it is the recorded verdict.

    `source_tree_clean` is the one check `grade` does not compute: it is the
    controller's `git status` at finalize time, which a regrade cannot repeat.
    """
    expected = _load(PACK / "controller-candidate/expected.json")
    checks, comparison = FINALIZER["grade"](
        RESULT, expected, PACK, ROOT, PACK / "controller-candidate"
    )
    recorded = dict(VERDICT["checks"])
    assert recorded.pop("source_tree_clean") is True
    assert checks == recorded
    assert comparison == VERDICT["comparison"]


def _archive_members(data: bytes) -> list[tuple[str, tuple[int, ...], int, int, bytes]]:
    """Everything in a ZIP the lane consumes, without the container's host byte."""
    with ZipFile(io.BytesIO(data)) as archive:
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


#: The archive's container bytes are platform-independent since batch 2: every
#: lane archive is written by `gpo_studio.deterministic_zip` (members sorted,
#: fixed timestamps, ``create_system=3``, fixed attributes, STORED -- deflate
#: is not used because Windows' CPython links a different deflate than Linux).
#: So the exact hash is asserted on EVERY platform, and
#: `test_a_windows_host_builds_the_same_archive` pins the reason: forcing
#: Windows' ``create_system`` default changes nothing.


def test_the_builder_still_produces_the_banked_candidate(tmp_path: Path) -> None:
    """The certified request is reproducible: the same bytes on every platform."""
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        BUILDER["build"](tmp_path, BUILDER["candidate_policy"]())
    archive = BUILDER["ARCHIVE_NAME"]
    banked_dir = PACK / "controller-candidate"
    for name, digest in VERDICT["candidate"].items():
        if name in ("builder.stdout.txt", archive):
            continue
        assert _sha((tmp_path / name).read_bytes()) == digest, name
    assert _archive_members((tmp_path / archive).read_bytes()) == _archive_members(
        (banked_dir / archive).read_bytes()
    )
    banked = (banked_dir / "builder.stdout.txt").read_text(encoding="utf-8")
    assert _sha((tmp_path / archive).read_bytes()) == VERDICT["candidate"][archive]
    assert stdout.getvalue() == banked


def test_a_windows_host_builds_the_same_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forcing Windows' `create_system` default leaves the archive byte-identical."""
    native = tmp_path / "native"
    native.mkdir()
    with redirect_stdout(io.StringIO()):
        BUILDER["build"](native, BUILDER["candidate_policy"]())
    original = zipfile.ZipInfo.__init__

    def windows_default(self: zipfile.ZipInfo, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        self.create_system = 0

    monkeypatch.setattr(zipfile.ZipInfo, "__init__", windows_default)
    windows = tmp_path / "windows"
    windows.mkdir()
    with redirect_stdout(io.StringIO()):
        BUILDER["build"](windows, BUILDER["candidate_policy"]())
    name = BUILDER["ARCHIVE_NAME"]
    assert (native / name).read_bytes() == (windows / name).read_bytes()


def _leg_pol(leg: str) -> bytes:
    return base64.b64decode(RESULT[leg]["registry_pol_base64"], validate=True)


def test_the_write_leg_got_the_candidate_registry_pol_back_byte_for_byte() -> None:
    with ZipFile(PACK / "controller-candidate/studio-firewall-backup.zip") as archive:
        (machine,) = [
            name
            for name in archive.namelist()
            if name.casefold().endswith("/domainsysvol/gpo/machine/registry.pol")
        ]
        candidate = archive.read(machine)
    windows = _leg_pol("write_leg")
    assert windows == candidate
    recorded = VERDICT["comparison"]["write_registry_bytes"]
    assert recorded["equal"] is True
    assert recorded["windows_sha256"] == recorded["candidate_sha256"] == _sha(candidate)


def test_both_legs_decode_to_the_authored_policy_with_nothing_unrecognised() -> None:
    expected = _load(PACK / "controller-candidate/expected.json")["policy"]
    for leg in ("read_leg", "write_leg"):
        parsed = from_registry_records(parse(_leg_pol(leg)))
        assert parsed.unrecognised_records == ()
        assert parsed.issues == ()
        assert all(not rule.unknown_tokens for rule in parsed.policy.rules)
        assert FINALIZER["_policy_dict"](parsed.policy) == expected, leg


def test_the_tool_guid_observation_is_what_the_results_doc_records() -> None:
    """Recorded, not asserted, by the lane -- and the surface's limitation cites it.

    Native authoring registers the Registry CSE with the firewall tool GUID
    (`B05566AC`); Studio's unchanged exporter registers it with the
    Administrative Templates tool GUID (`D02B1F72`). GPMC's report rendered a
    Windows Firewall extension for both. Whether the Group Policy Management
    Editor shows the write-leg rules under its firewall node was not measured.
    """
    observations = VERDICT["comparison"]["extension_observations"]
    assert observations["read"]["gPCMachineExtensionNames"] == (
        f"[{REGISTRY_CSE_GUID}{FIREWALL_TOOL_GUID}]"
    )
    assert observations["write"]["gPCMachineExtensionNames"] == (
        f"[{REGISTRY_CSE_GUID}{ADMIN_TEMPLATES_TOOL_GUID}]"
    )
    assert observations["read"]["gpmc_firewall_extension_rendered"] is True
    assert observations["write"]["gpmc_firewall_extension_rendered"] is True
    for leg, side in (("read_leg", "read"), ("write_leg", "write")):
        assert (
            RESULT[leg]["ad_attributes"]["gPCMachineExtensionNames"]
            == observations[side]["gPCMachineExtensionNames"]
        )


def test_the_verdict_is_registered_live() -> None:
    registry = runpy.run_path(str(ROOT / "tests/test_committed_evidence.py"))
    assert registry["LANE_VERDICTS"][VERDICT_PATH] == "finalize_firewall_run.py"
    assert VERDICT_PATH in registry["LIVE_VERDICTS"]
