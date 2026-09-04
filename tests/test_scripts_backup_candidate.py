"""The R10 candidate builder emits exactly the bundle the Windows probe needs.

R10 (``docs/manual-evidence-requests.md``) is gated on
``studio-scripts-backup.zip`` existing: the operator imports it into the lab,
asks GPMC to render the Scripts node, and re-backs-up the result. These tests
pin the artifact ``build-scripts-backup-candidate.py`` hands over:

* ``scripts.ini`` / ``psscripts.ini`` byte-identical to the banked R2 WS2025
  GPMC captures (the same contract ``test_native_scripts_export.py`` pins for
  the exporter);
* the measured Scripts CSE GUID pair registered in ``Backup.xml``, with every
  claimed payload present and the trigger directories referenced;
* run-to-run determinism (the operator can bind the artifact to a SHA-256);
* a round-trip through the Studio's own import machinery (``backup.py`` +
  ``import_export.py``). The work order's real oracle is Windows; this one is
  free.

The builder module is loaded by path because ``scripts/`` is not a package
(same approach as ``test_rsop_candidate_partition.py``).
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from gpo_studio.backup import read_backup, read_cse_content
from gpo_studio.export import native_backup_refusal
from gpo_studio.import_export import collect_cse_metadata, extract_side_settings

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MODULE_PATH = _REPO_ROOT / "scripts" / "plan-033" / "build-scripts-backup-candidate.py"
_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "native-scripts-gpmc"

_spec = importlib.util.spec_from_file_location("build_scripts_backup_candidate", _MODULE_PATH)
assert _spec and _spec.loader
build_scripts_backup_candidate = importlib.util.module_from_spec(_spec)
# Registered before execution because the module imports dataclasses-backed
# models whose annotations resolve through `sys.modules[cls.__module__]`.
sys.modules["build_scripts_backup_candidate"] = build_scripts_backup_candidate
_spec.loader.exec_module(build_scripts_backup_candidate)

_BACKUP_NS = "http://www.microsoft.com/GroupPolicy/GPOOperations"
_BKP = f"{{{_BACKUP_NS}}}"
# Measured on the wire (claim-registry R2 re-run): the pair GPMC registered in
# gPCMachineExtensionNames when authoring script entries.
_SCRIPTS_PAIR = (
    "[{42B5FAAE-6536-11D2-AE5A-0000F87571E3}{40B6664F-4972-11D1-A7CA-0000F87571E3}]"
)


def _bundle_bytes() -> bytes:
    return build_scripts_backup_candidate.build_bundle()


def _backup_id_of(archive: zipfile.ZipFile) -> str:
    return next(
        name.split("/")[0] for name in archive.namelist() if name.endswith("/Backup.xml")
    )


def _read(archive: zipfile.ZipFile, backup_id: str, relative: str) -> bytes:
    return archive.read(f"{backup_id}/DomainSysvol/GPO/{relative}")


def _native_capture_bytes(name: str) -> bytes:
    """Rebuild the measured native bytes from a banked ASCII transcript.

    Same reconstruction ``test_native_scripts_export.py`` uses: the transcripts
    carry a measurement header, then the decoded content verbatim with CRLF
    transcribed as LF; rebuilding and cross-checking the banked byte count
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


def test_scripts_ini_matches_banked_capture_byte_for_byte() -> None:
    with zipfile.ZipFile(io.BytesIO(_bundle_bytes())) as archive:
        backup_id = _backup_id_of(archive)
        data = _read(archive, backup_id, "Machine/Scripts/scripts.ini")
    assert data == _native_capture_bytes("scripts.ini.txt")
    _assert_native_encoding(data)
    # The capture's content, line for line, after the leading blank line —
    # including the empty 1Parameters= for the parameterless second script.
    assert data.decode("utf-16-le").split("\r\n")[1:] == [
        "[Startup]",
        "0CmdLine=zz-studio-marker.cmd",
        "0Parameters=/c alpha beta",
        "1CmdLine=zz-studio-second.cmd",
        "1Parameters=",
        "",
    ]


def test_psscripts_ini_matches_banked_capture_byte_for_byte() -> None:
    with zipfile.ZipFile(io.BytesIO(_bundle_bytes())) as archive:
        backup_id = _backup_id_of(archive)
        data = _read(archive, backup_id, "Machine/Scripts/psscripts.ini")
    assert data == _native_capture_bytes("psscripts.ini.txt")
    _assert_native_encoding(data)
    # The non-default ordering R10 asks the operator to check, in the measured
    # shape: [ScriptsConfig] before the trigger section, no [Policy] anywhere.
    lines = data.decode("utf-16-le").split("\r\n")
    assert lines[1:5] == [
        "[ScriptsConfig]",
        "StartExecutePSFirst=true",
        "[Startup]",
        "0CmdLine=zz-studio-marker.ps1",
    ]
    assert "[Policy]" not in data.decode("utf-16-le")


def test_backup_xml_registers_measured_scripts_pair_and_references_payload() -> None:
    with zipfile.ZipFile(io.BytesIO(_bundle_bytes())) as archive:
        backup_id = _backup_id_of(archive)
        root = ET.fromstring(archive.read(f"{backup_id}/Backup.xml"))
        payloads = {
            name.removeprefix(f"{backup_id}/")
            for name in archive.namelist()
            if "/DomainSysvol/GPO/" in name
        }
    core = root.find(f".//{_BKP}GroupPolicyCoreSettings")
    assert core is not None
    machine = core.find(f"{_BKP}MachineExtensionGuids")
    user = core.find(f"{_BKP}UserExtensionGuids")
    assert machine is not None and machine.text is not None
    # Scripts-only candidate: the measured Scripts pair alone, no registry or
    # GPP groups — exactly the extension list the R10 import probe exists to
    # exercise.
    assert machine.text == _SCRIPTS_PAIR
    assert user is not None and (user.text or "") == ""
    extension = root.find(
        f".//{_BKP}GroupPolicyExtension[@{_BKP}ID="
        "'{42B5FAAE-6536-11D2-AE5A-0000F87571E3}']"
    )
    assert extension is not None
    file_locations = {
        elem.attrib[f"{_BKP}Location"].replace("\\", "/")
        for elem in extension.findall(f"{_BKP}FSObjectFile")
    }
    # Every payload the Scripts extension claims is in the archive, and vice
    # versa: the operator's Expand-Archive yields exactly what Backup.xml cites.
    assert file_locations == {p for p in payloads if "/Scripts/" in p}
    dir_locations = {
        elem.attrib[f"{_BKP}Location"] for elem in extension.findall(f"{_BKP}FSObjectDir")
    }
    # Measured SYSVOL tree: trigger subdirectories alongside the INIs.
    assert "DomainSysvol\\GPO\\Machine\\Scripts\\Startup" in dir_locations
    assert "DomainSysvol\\GPO\\Machine\\Scripts\\Shutdown" in dir_locations


def test_candidate_state_carries_no_native_refusal() -> None:
    # The builder's fixed input sits entirely inside the measured encoding; if
    # that ever stops being true, gpmc_backup_bundle refuses and the builder
    # must fail loudly rather than ship an approximated bundle.
    assert (
        native_backup_refusal(
            build_scripts_backup_candidate._GPO,
            scripts=build_scripts_backup_candidate._SCRIPTS,
        )
        is None
    )


def test_builder_runs_are_byte_identical_and_print_manifest(tmp_path: Path) -> None:
    outputs: list[Path] = []
    for run in ("run-1", "run-2"):
        out_dir = tmp_path / run
        result = subprocess.run(
            [sys.executable, str(_MODULE_PATH), str(out_dir)],
            capture_output=True,
            text=True,
            check=True,
        )
        archive_path = out_dir / build_scripts_backup_candidate.ARCHIVE_NAME
        assert archive_path.name == "studio-scripts-backup.zip"
        outputs.append(archive_path)
        # The printed manifest is part of the operator contract.
        assert "Machine/Scripts/scripts.ini" in result.stdout
        assert "Machine/Scripts/psscripts.ini" in result.stdout
        assert "archive sha256:" in result.stdout
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    # And the subprocess output equals the in-process build, so the byte
    # assertions above describe the artifact the operator actually receives.
    assert outputs[0].read_bytes() == _bundle_bytes()


def test_bundle_round_trips_through_studio_import_path(tmp_path: Path) -> None:
    data = _bundle_bytes()
    backup_dir = tmp_path / "in"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        archive.extractall(backup_dir)

    backup = read_backup(backup_dir)
    assert len(backup.gpos) == 1
    gpo = backup.gpos[0]
    assert gpo.guid == "22222222-3333-4444-5555-666666666666"
    assert gpo.display_name == "zz-studio-evidence-10-scripts"
    assert gpo.content_root is not None

    # Scripts-only candidate: the import path finds no registry payload, and
    # the Scripts CSE files are preserved as CSE metadata with content hashes.
    assert extract_side_settings(gpo.content_root, "computer") == []
    metadata = collect_cse_metadata(gpo)
    scripts_meta = [
        entry
        for entry in metadata
        if any("scripts" in f.relative_path.casefold() for f in entry.files)
    ]
    assert len(scripts_meta) == 1
    carried = {f.relative_path.replace("\\", "/") for f in scripts_meta[0].files}
    assert carried == {"Scripts/scripts.ini", "Scripts/psscripts.ini"}
    for relative, capture in (
        ("Scripts/scripts.ini", "scripts.ini.txt"),
        ("Scripts/psscripts.ini", "psscripts.ini.txt"),
    ):
        member = next(
            f
            for f in scripts_meta[0].files
            if f.relative_path.replace("\\", "/") == relative
        )
        expected = _native_capture_bytes(capture)
        assert member.size == len(expected)
        assert member.content_hash == hashlib.sha256(expected).hexdigest()
        # read_cse_content hands back the raw authored bytes.
        assert (
            read_cse_content(backup_dir, gpo.guid, "machine", scripts_meta[0].guid, relative)
            == expected
        )
