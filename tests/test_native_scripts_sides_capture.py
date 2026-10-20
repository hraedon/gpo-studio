"""Plan 035 WP-1: the GPME capture of a user logon and a machine shutdown script.

Ground truth is ``fixtures/native-scripts-sides-gpme/`` (provenance in that
directory): windows-console-driver estate window 19, record ``w19-r2``,
GPME on Windows Server 2025, 2026-10-20. It answers WI-071's open question --
the user-side Scripts extension pair -- and banks the ``scripts.ini`` shapes
for the Shutdown and Logon triggers that R2 (Startup only) never measured.

What these tests hold:

- the transcripts reconstruct to the exact captured bytes (hash-bound);
- Studio's ``scripts.ini`` writer reproduces both captures byte for byte, so
  the per-trigger file shape needs no change;
- the machine-side Scripts pair Studio registers matches the capture;
- the USER-side pair does not: GPME registered tool GUID ``{40B66650-...}``
  where ``export.py`` writes the machine side's ``{40B6664F-...}``. That
  test is ``xfail(strict=True)``: it asserts the measured truth, fails
  today because ``export.py`` is bound by live lane evidence and changes
  only in a requalification batch (Plan 035, WI-071), and will XPASS --
  failing the suite -- the moment that batch lands, so the marker cannot
  outlive the fix.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from gpo_studio.export import gpmc_backup_bundle, native_backup_id
from gpo_studio.model import GPO
from gpo_studio.script_policy import ScriptEntry, ScriptPolicy

_FIXTURES = Path(__file__).parent / "fixtures" / "native-scripts-sides-gpme"
_PROVENANCE = json.loads((_FIXTURES / "provenance.json").read_text(encoding="utf-8"))
_BKP = "{http://www.microsoft.com/GroupPolicy/GPOOperations}"
_MEASURED = _PROVENANCE["measured_extension_lists"]


def _native_bytes(name: str) -> bytes:
    transcript = (_FIXTURES / name).read_text(encoding="ascii")
    header, counters, content = transcript.split("\n", 2)
    assert header == "first 4 bytes: FF FE 0D 00"
    size = int(re.search(r"size: (\d+) bytes", counters).group(1))  # type: ignore[union-attr]
    native = b"\xff\xfe" + content.replace("\n", "\r\n").encode("utf-16-le")
    assert len(native) == size
    return native


def _gpo() -> GPO:
    return GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Synthetic logon and shutdown scripts",
        domain="synthetic.test",
    )


def _captured_policies() -> dict[str, ScriptPolicy]:
    """The two entries the window-19 transaction authored, as Studio models them."""
    return {
        "computer": ScriptPolicy(
            shutdown=(
                ScriptEntry(
                    script_id="m0",
                    artifact_id="m",
                    original_name="zz-studio-shutdown.cmd",
                    parameters="/c shutdown-alpha",
                    script_type="shutdown",
                ),
            ),
        ),
        "user": ScriptPolicy(
            logon=(
                ScriptEntry(
                    script_id="u0",
                    artifact_id="u",
                    original_name="zz-studio-logon.cmd",
                    parameters="/c logon-beta",
                    script_type="logon",
                ),
            ),
        ),
    }


def _bundle() -> tuple[zipfile.ZipFile, str]:
    gpo = _gpo()
    archive = zipfile.ZipFile(io.BytesIO(gpmc_backup_bundle(gpo, scripts=_captured_policies())))
    return archive, native_backup_id(gpo)


def _core_lists() -> tuple[str, str]:
    archive, backup_id = _bundle()
    with archive:
        root = ET.fromstring(archive.read(f"{backup_id}/Backup.xml"))
    core = root.find(f".//{_BKP}GroupPolicyCoreSettings")
    assert core is not None
    machine = core.find(f"{_BKP}MachineExtensionGuids")
    user = core.find(f"{_BKP}UserExtensionGuids")
    assert machine is not None and user is not None
    return machine.text or "", user.text or ""


@pytest.mark.parametrize("name", ["machine-scripts.ini.txt", "user-scripts.ini.txt"])
def test_transcript_reconstructs_the_captured_bytes(name: str) -> None:
    native = _native_bytes(name)
    record = _PROVENANCE["files"][name]
    assert len(native) == record["measured_total_bytes"]
    assert native.count(b"\r\x00") == record["measured_cr_count"]
    assert native.count(b"\n\x00") == record["measured_lf_count"]
    assert hashlib.sha256(native).hexdigest() == record["raw_sha256"]


def test_gpme_writes_only_the_populated_trigger_section() -> None:
    machine = _native_bytes("machine-scripts.ini.txt").decode("utf-16-le")
    user = _native_bytes("user-scripts.ini.txt").decode("utf-16-le")
    assert machine.removeprefix("﻿").split("\r\n") == [
        "",
        "[Shutdown]",
        "0CmdLine=zz-studio-shutdown.cmd",
        "0Parameters=/c shutdown-alpha",
        "",
    ]
    assert user.removeprefix("﻿").split("\r\n") == [
        "",
        "[Logon]",
        "0CmdLine=zz-studio-logon.cmd",
        "0Parameters=/c logon-beta",
        "",
    ]


@pytest.mark.parametrize(
    ("relative", "fixture"),
    [
        ("Machine/Scripts/scripts.ini", "machine-scripts.ini.txt"),
        ("User/Scripts/scripts.ini", "user-scripts.ini.txt"),
    ],
)
def test_studio_writes_the_captured_scripts_ini_byte_for_byte(relative: str, fixture: str) -> None:
    archive, backup_id = _bundle()
    with archive:
        data = archive.read(f"{backup_id}/DomainSysvol/GPO/{relative}")
    assert data == _native_bytes(fixture)


def test_no_psscripts_ini_for_legacy_only_sides() -> None:
    archive, backup_id = _bundle()
    with archive:
        names = archive.namelist()
    assert not any(name.endswith("psscripts.ini") for name in names), names
    assert "psscripts.ini" in _PROVENANCE["not_written"]


def test_machine_side_pair_matches_the_capture() -> None:
    machine, _user = _core_lists()
    assert machine == _MEASURED["gPCMachineExtensionNames"]


def test_the_two_sides_carry_different_tool_guids() -> None:
    """The measurement itself, pinned: same CSE, different tool half."""
    machine = _MEASURED["gPCMachineExtensionNames"]
    user = _MEASURED["gPCUserExtensionNames"]
    assert machine[:39] == user[:39] == "[{42B5FAAE-6536-11D2-AE5A-0000F87571E3}"
    assert "{40B6664F-4972-11D1-A7CA-0000F87571E3}" in machine
    assert "{40B66650-4972-11D1-A7CA-0000F87571E3}" in user


@pytest.mark.xfail(
    strict=True,
    reason=(
        "WI-071: export.py writes the machine-side Scripts tool GUID {40B6664F-...} on the "
        "user side; window 19 measured {40B66650-...}. export.py is bound by live lane "
        "evidence, so the fix rides the Plan 035 requalification batch."
    ),
)
def test_user_side_pair_matches_the_capture() -> None:
    _machine, user = _core_lists()
    assert user == _MEASURED["gPCUserExtensionNames"]
