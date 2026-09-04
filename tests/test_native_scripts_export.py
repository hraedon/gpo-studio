"""Byte-level contract for Studio-written scripts.ini / psscripts.ini.

The contract is the banked R2 measurement (Windows Server 2025 GPMC,
2026-09-03), not a reading of the docs: UTF-16LE with BOM, CRLF on every line
including the last, a leading blank line, split-style entries with empty
``NParameters=`` keys retained, and psscripts.ini ordering as
``[ScriptsConfig] StartExecutePSFirst=`` — no ``[Policy]`` section anywhere.
Ground-truth captures live in fixtures/native-scripts-gpmc/ (provenance in
that directory's provenance.json).
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

import pytest

from gpo_studio.export import gpmc_backup_bundle, native_backup_id, native_backup_refusal
from gpo_studio.model import GPO, RegistrySetting, ValidationError
from gpo_studio.script_policy import (
    PowerShellScriptEntry,
    ScriptEntry,
    ScriptPolicy,
)

_BACKUP_NS = "http://www.microsoft.com/GroupPolicy/GPOOperations"
_BKP = f"{{{_BACKUP_NS}}}"
_FIXTURES = Path(__file__).parent / "fixtures" / "native-scripts-gpmc"
_REGISTRY_PAIR = (
    "[{35378EAC-683F-11D2-A89A-00C04FBBCFA2}{D02B1F72-3407-48AE-BA88-E8213C6761F1}]"
)
# Measured on the wire: the pair GPMC registered in gPCMachineExtensionNames
# on the transaction that first authored script entries (claim-registry R2).
_SCRIPTS_PAIR = "[{42B5FAAE-6536-11D2-AE5A-0000F87571E3}{40B6664F-4972-11D1-A7CA-0000F87571E3}]"


def _r2_legacy_policy() -> ScriptPolicy:
    """The legacy script policy behind the banked scripts.ini capture."""
    return ScriptPolicy(
        startup=(
            ScriptEntry(
                script_id="s0",
                artifact_id="a0",
                original_name="zz-studio-marker.cmd",
                parameters="/c alpha beta",
            ),
            ScriptEntry(
                script_id="s1",
                artifact_id="a1",
                original_name="zz-studio-second.cmd",
            ),
        ),
    )


def _r2_powershell_policy(order: Any = "not_configured") -> ScriptPolicy:
    """The PowerShell policy behind the banked psscripts.ini capture."""
    return ScriptPolicy(
        powershell_startup=(
            PowerShellScriptEntry(
                script_id="p0",
                artifact_id="a0",
                original_name="zz-studio-marker.ps1",
                parameters="-Mode Alpha",
            ),
        ),
        powershell_order=order,
    )


def _gpo(*, with_registry: bool = True) -> GPO:
    extra: tuple[RegistrySetting, ...] = ()
    if with_registry:
        extra = (
            RegistrySetting(
                id="machine",
                side="computer",
                hive="HKLM",
                key=r"Software\Policies\Synthetic",
                value_name="MachineValue",
                registry_type="REG_DWORD",
                value=42,
            ),
        )
    return GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Synthetic native scripts",
        domain="synthetic.test",
        settings=extra,
    )


def _open(gpo: GPO, scripts: dict[str, ScriptPolicy] | None = None) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(gpmc_backup_bundle(gpo, scripts=scripts)))


def _backup_id_of(archive: zipfile.ZipFile) -> str:
    return next(
        name.split("/")[0] for name in archive.namelist() if name.endswith("/Backup.xml")
    )


def _read(archive: zipfile.ZipFile, backup_id: str, relative: str) -> bytes:
    return archive.read(f"{backup_id}/DomainSysvol/GPO/{relative}")


def _native_capture_bytes(name: str) -> bytes:
    """Rebuild the native UTF-16LE bytes from a banked ASCII transcript.

    The transcripts carry a two-line measurement header, then the decoded
    content verbatim except CRLF was transcribed as LF. Rebuilding the native
    bytes and cross-checking the length against the header's banked byte count
    proves both the transcript and the reconstruction.
    """
    transcript = (_FIXTURES / name).read_text(encoding="ascii")
    header, counters, content = transcript.split("\n", 2)
    assert header == "first 4 bytes: FF FE 0D 00"
    size = int(re.search(r"size: (\d+) bytes", counters).group(1))
    lf_count = int(re.search(r"LF count: (\d+)", counters).group(1))
    assert content.count("\n") == lf_count
    native = b"\xff\xfe" + content.replace("\n", "\r\n").encode("utf-16-le")
    assert len(native) == size, (len(native), size)
    return native


def _native_ini_shape(sections: list[tuple[str, list[tuple[str, str]]]]) -> bytes:
    parts = ["\r\n"]
    for name, pairs in sections:
        parts.append(f"[{name}]\r\n")
        parts.extend(f"{key}={value}\r\n" for key, value in pairs)
    return b"\xff\xfe" + "".join(parts).encode("utf-16-le")


def _assert_native_encoding(data: bytes) -> None:
    """BOM present, decodes as utf-16-le, CRLF-only, measured framing."""
    assert data.startswith(b"\xff\xfe")
    text = data.decode("utf-16-le")
    # Decoding as utf-16-le (not utf-16) keeps the BOM as a leading U+FEFF.
    assert text.startswith("\ufeff\r\n"), "measured leading blank line missing"
    text = text.removeprefix("\ufeff")
    assert text.endswith("\r\n")
    for index, char in enumerate(text):
        if char == "\n":
            assert text[index - 1] == "\r", "bare LF found"
    assert "; no scripts configured" not in text


def test_scripts_ini_matches_banked_capture_byte_for_byte() -> None:
    gpo = _gpo()
    with _open(gpo, {"computer": _r2_legacy_policy()}) as archive:
        backup_id = _backup_id_of(archive)
        data = _read(archive, backup_id, "Machine/Scripts/scripts.ini")
    assert data == _native_capture_bytes("scripts.ini.txt")
    _assert_native_encoding(data)
    # The capture's content, line for line, after the leading blank line.
    assert data.decode("utf-16-le").split("\r\n")[1:] == [
        "[Startup]",
        "0CmdLine=zz-studio-marker.cmd",
        "0Parameters=/c alpha beta",
        "1CmdLine=zz-studio-second.cmd",
        "1Parameters=",
        "",
    ]


def test_psscripts_ini_matches_banked_capture_byte_for_byte() -> None:
    gpo = _gpo()
    policy = _r2_powershell_policy(order="run_windows_powershell_scripts_first")
    with _open(gpo, {"computer": policy}) as archive:
        backup_id = _backup_id_of(archive)
        data = _read(archive, backup_id, "Machine/Scripts/psscripts.ini")
    assert data == _native_capture_bytes("psscripts.ini.txt")
    _assert_native_encoding(data)
    lines = data.decode("utf-16-le").split("\r\n")
    assert lines[1] == "[ScriptsConfig]"
    assert lines[2] == "StartExecutePSFirst=true"
    assert lines[3] == "[Startup]"


def test_psscripts_ini_without_ordering_has_no_scriptsconfig_section() -> None:
    # The r2-record engine re-run captured 140 bytes / 4 CRLF pairs with no
    # [ScriptsConfig]: the section appears only when the ordering was set.
    gpo = _gpo()
    with _open(gpo, {"computer": _r2_powershell_policy()}) as archive:
        backup_id = _backup_id_of(archive)
        data = _read(archive, backup_id, "Machine/Scripts/psscripts.ini")
    assert b"ScriptsConfig" not in data
    assert data == _native_ini_shape(
        [
            (
                "Startup",
                [
                    ("0CmdLine", "zz-studio-marker.ps1"),
                    ("0Parameters", "-Mode Alpha"),
                ],
            )
        ]
    )
    assert len(data) == 140


def test_psscripts_ini_run_last_writes_false() -> None:
    gpo = _gpo()
    policy = _r2_powershell_policy(order="run_windows_powershell_scripts_last")
    with _open(gpo, {"computer": policy}) as archive:
        backup_id = _backup_id_of(archive)
        data = _read(archive, backup_id, "Machine/Scripts/psscripts.ini")
    assert "StartExecutePSFirst=false\r\n" in data.decode("utf-16-le")


def test_no_policy_section_anywhere() -> None:
    # Measured: RunLogonScriptsSync / RunLogoffScriptsSync / LegacyScriptsFirst
    # / PowerShellOrder appear nowhere on the wire.
    gpo = _gpo()
    policy = _r2_powershell_policy(order="run_windows_powershell_scripts_first")
    with _open(gpo, {"computer": policy}) as archive:
        backup_id = _backup_id_of(archive)
        data = _read(archive, backup_id, "Machine/Scripts/psscripts.ini")
    assert "[Policy]" not in data.decode("utf-16-le")
    assert b"RunLogonScriptsSync" not in data
    assert b"RunLogoffScriptsSync" not in data
    assert b"LegacyScriptsFirst" not in data
    assert b"PowerShellOrder" not in data


def test_legacy_and_powershell_files_coexist_like_the_capture() -> None:
    gpo = _gpo(with_registry=False)
    legacy = _r2_legacy_policy()
    powershell = _r2_powershell_policy("run_windows_powershell_scripts_first")
    scripts = {
        "computer": ScriptPolicy(
            startup=legacy.startup,
            powershell_startup=powershell.powershell_startup,
            powershell_order="run_windows_powershell_scripts_first",
        ),
    }
    with _open(gpo, scripts) as archive:
        backup_id = _backup_id_of(archive)
        names = {
            name.removeprefix(f"{backup_id}/DomainSysvol/GPO/")
            for name in archive.namelist()
            if "/DomainSysvol/GPO/" in name
        }
    assert names == {
        "Machine/Scripts/scripts.ini",
        "Machine/Scripts/psscripts.ini",
    }


def test_backup_xml_registers_measured_scripts_extension_pair() -> None:
    gpo = _gpo()
    backup_id = native_backup_id(gpo)
    with _open(gpo, {"computer": _r2_legacy_policy()}) as archive:
        root = ET.fromstring(archive.read(f"{backup_id}/Backup.xml"))
    core = root.find(f".//{_BKP}GroupPolicyCoreSettings")
    assert core is not None
    machine = core.find(f"{_BKP}MachineExtensionGuids")
    user = core.find(f"{_BKP}UserExtensionGuids")
    assert machine is not None and machine.text is not None
    assert user is not None
    # Registry group first (this GPO carries registry settings), then the
    # measured Scripts pair. The user side carries nothing, so its element is
    # empty (no text after an XML round-trip).
    assert machine.text == _REGISTRY_PAIR + _SCRIPTS_PAIR
    assert (user.text or "") == ""


def test_scripts_only_side_registers_scripts_pair_alone() -> None:
    gpo = _gpo(with_registry=False)
    backup_id = native_backup_id(gpo)
    with _open(gpo, {"computer": _r2_legacy_policy()}) as archive:
        root = ET.fromstring(archive.read(f"{backup_id}/Backup.xml"))
    core = root.find(f".//{_BKP}GroupPolicyCoreSettings")
    assert core is not None
    machine = core.find(f"{_BKP}MachineExtensionGuids")
    assert machine is not None and machine.text is not None
    assert machine.text == _SCRIPTS_PAIR


def test_backup_xml_scripts_extension_references_files_and_trigger_dirs() -> None:
    gpo = _gpo(with_registry=False)
    with _open(gpo, {"computer": _r2_legacy_policy()}) as archive:
        backup_id = _backup_id_of(archive)
        root = ET.fromstring(archive.read(f"{backup_id}/Backup.xml"))
        payloads = {
            name.removeprefix(f"{backup_id}/")
            for name in archive.namelist()
            if "/DomainSysvol/GPO/" in name
        }
    extension = root.find(
        f".//{_BKP}GroupPolicyExtension[@{_BKP}ID="
        "'{42B5FAAE-6536-11D2-AE5A-0000F87571E3}']"
    )
    assert extension is not None
    file_locations = {
        elem.attrib[f"{_BKP}Location"].replace("\\", "/")
        for elem in extension.findall(f"{_BKP}FSObjectFile")
    }
    # Every FSObjectFile the Scripts extension claims exists in the bundle.
    assert file_locations == {
        payload for payload in payloads if "/Scripts/" in payload
    }
    dir_locations = {
        elem.attrib[f"{_BKP}Location"]
        for elem in extension.findall(f"{_BKP}FSObjectDir")
    }
    # Measured SYSVOL tree: the trigger subdirectories sit alongside the INIs.
    assert "DomainSysvol\\GPO\\Machine\\Scripts\\Startup" in dir_locations
    assert "DomainSysvol\\GPO\\Machine\\Scripts\\Shutdown" in dir_locations


def test_user_side_scripts_land_under_user() -> None:
    gpo = _gpo(with_registry=False)
    policy = ScriptPolicy(
        logon=(ScriptEntry(script_id="u0", artifact_id="u", original_name="logon.cmd"),),
    )
    backup_id = native_backup_id(gpo)
    with _open(gpo, {"user": policy}) as archive:
        data = _read(archive, backup_id, "User/Scripts/scripts.ini")
        root = ET.fromstring(archive.read(f"{backup_id}/Backup.xml"))
    assert data == _native_ini_shape(
        [("Logon", [("0CmdLine", "logon.cmd"), ("0Parameters", "")])]
    )
    core = root.find(f".//{_BKP}GroupPolicyCoreSettings")
    assert core is not None
    user = core.find(f"{_BKP}UserExtensionGuids")
    assert user is not None and user.text is not None
    assert user.text == _SCRIPTS_PAIR


def test_bundle_without_scripts_has_no_scripts_artifacts() -> None:
    gpo = _gpo()
    with _open(gpo) as archive:
        names = archive.namelist()
        backup_xml = archive.read(f"{_backup_id_of(archive)}/Backup.xml").decode("utf-8")
    assert not any("/Scripts/" in name for name in names)
    assert "42B5FAAE" not in backup_xml


def test_empty_script_policy_contributes_nothing() -> None:
    gpo = _gpo()
    with _open(gpo, {"computer": ScriptPolicy()}) as archive:
        names = archive.namelist()
    assert not any("/Scripts/" in name for name in names)


def test_bundle_with_scripts_is_deterministic() -> None:
    gpo = _gpo()
    scripts = {"computer": _r2_legacy_policy()}
    assert gpmc_backup_bundle(gpo, scripts=scripts) == gpmc_backup_bundle(
        gpo, scripts=scripts
    )


@pytest.mark.parametrize(
    "policy",
    [
        ScriptPolicy(run_logon_scripts_sync=True),
        ScriptPolicy(
            startup=(
                ScriptEntry(
                    script_id="s0",
                    artifact_id="a",
                    original_name="a.cmd",
                    execution="asynchronous",
                ),
            ),
        ),
        ScriptPolicy(
            powershell_startup=(
                PowerShellScriptEntry(
                    script_id="p0",
                    artifact_id="a",
                    original_name="a.ps1",
                    no_profile=True,
                ),
            ),
        ),
        ScriptPolicy(
            powershell_startup=(
                PowerShellScriptEntry(
                    script_id="p0",
                    artifact_id="a",
                    original_name="a.ps1",
                    non_interactive=False,
                ),
            ),
        ),
        ScriptPolicy(
            powershell_startup=(
                PowerShellScriptEntry(
                    script_id="p0",
                    artifact_id="a",
                    original_name="a.ps1",
                    execution="asynchronous",
                ),
            ),
        ),
    ],
    ids=[
        "logon-sync-flag",
        "async-legacy-entry",
        "no-profile-entry",
        "interactive-entry",
        "async-powershell-entry",
    ],
)
def test_inexpressible_script_states_are_refused_not_approximated(
    policy: ScriptPolicy,
) -> None:
    gpo = _gpo()
    with pytest.raises(ValidationError) as raised:
        gpmc_backup_bundle(gpo, scripts={"computer": policy})
    issue = raised.value.issues[0]
    assert issue.code == "inexpressible_native_script_state"
    refusal = native_backup_refusal(gpo, scripts={"computer": policy})
    assert refusal is not None
    assert refusal.code == issue.code
