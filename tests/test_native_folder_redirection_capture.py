"""Wire contract for the first native GPMC-authored fdeploy capture (R3).

The contract is the banked R3 measurement (Windows Server 2025 GPMC,
2026-09-04, windows-console-driver r3-window3-record.json, transaction
150db7fe): GPMC lays down an empty UTF-16LE marker ``fdeploy.ini`` beside the
semantic policy ``fdeploy1.ini`` -- three sections, four entries,
``version=100``, ``Flags=1021``, and a ``FullPath`` pointing at the authored
target. Both raws are preserved (hash-bound) in windows-console-driver
``docs/estate-window-3/captures/``; the transcripts here reconstruct those
exact bytes.

``folder_redirection.py`` is deliberately NOT exercised: R3's finding is that
the module models redirection as User Shell Folders registry policy and may
address the wrong artifact entirely. Whether it should read or write fdeploy
artifacts is a scope decision for Plan 034, not this fixture's business.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_FIXTURES = Path(__file__).parent / "fixtures" / "native-folder-redirection-gpmc"

# Banked in the r3-window3-record.json envelope (fdeploy*.encoding.*):
# bom=utf16le, crlf_only=true; marker cr=lf=2, policy cr=lf=9.
_BANKED = {
    # transcript -> (raw sha-256, size, cr_count, lf_count)
    "fdeploy.ini.txt": (
        "5ad8f52071d25165e7e68064ab194ec27a074a3846149ed0689af23e7f7f2d00",
        20,
        2,
        2,
    ),
    "fdeploy1.ini.txt": (
        "71f1026180c4a92ed5bcca3366e5d22b80a64931f663fa079ef5d49cd2450800",
        458,
        9,
        9,
    ),
}

#: The Folder Redirection CSE GUID -- a Windows constant, the only GUID the
#: semantic file names.
_FR_CSE = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"


def _native_capture_bytes(name: str) -> bytes:
    """Rebuild the native UTF-16LE bytes from a banked ASCII transcript."""
    transcript = (_FIXTURES / name).read_text(encoding="ascii")
    header, counters, content = transcript.split("\n", 2)
    # Both natives begin BOM + CRLF: the marker IS that preamble alone.
    assert header == "first 4 bytes: FF FE 0D 00"
    size = int(re.search(r"size: (\d+) bytes", counters).group(1))
    lf_count = int(re.search(r"LF count: (\d+)", counters).group(1))
    assert content.count("\n") == lf_count
    native = b"\xff\xfe" + content.replace("\n", "\r\n").encode("utf-16-le")
    assert len(native) == size, (len(native), size)
    return native


def _parse_inf(text: str) -> tuple[list[str], dict[str, tuple[str, str]]]:
    """Hand-parse the banked INI shape: sections and key=value entries.

    Blank and whitespace-only lines are skipped (both natives carry a
    two-line preamble of exactly that); no other line shapes exist in the
    banked files.
    """
    sections: list[str] = []
    entries: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("[") and line.endswith("]"):
            sections.append(line[1:-1])
        else:
            key, _, value = line.partition("=")
            entries.setdefault(key, (value, sections[-1]))
    return sections, entries


def test_marker_reconstruction_is_hash_bound_and_empty() -> None:
    data = _native_capture_bytes("fdeploy.ini.txt")
    sha, size, cr, lf = _BANKED["fdeploy.ini.txt"]
    # The transcript reconstructs the preserved raw byte-for-byte.
    assert hashlib.sha256(data).hexdigest() == sha
    assert len(data) == size
    assert data.count(b"\r") == cr
    assert data.count(b"\n") == lf
    # fdeploy_marker.section_count == 0, entry_count == 0: the marker is
    # only the shared two-line preamble (empty line, five spaces).
    text = data[2:].decode("utf-16-le")
    sections, entries = _parse_inf(text)
    assert sections == []
    assert entries == {}
    assert text == "\r\n     \r\n"


def test_policy_reconstruction_is_hash_bound_with_banked_shape() -> None:
    data = _native_capture_bytes("fdeploy1.ini.txt")
    sha, size, cr, lf = _BANKED["fdeploy1.ini.txt"]
    assert hashlib.sha256(data).hexdigest() == sha
    assert len(data) == size
    assert data.count(b"\r") == cr
    assert data.count(b"\n") == lf
    text = data[2:].decode("utf-16-le")
    assert text.count("\r\n") == lf  # crlf_only: every LF rides a CR

    sections, entries = _parse_inf(text)

    # fdeploy.section_names.* and sections.N.line_count, in file order.
    assert sections == ["version", "Folder_Redirection", f"{_FR_CSE}_s-1-1-0"]
    # fdeploy.entry_count == 4, with every key/value banked verbatim.
    assert len(entries) == 4
    assert entries["version"] == ("100", "version")
    assert entries[_FR_CSE] == ("s-1-1-0;", "Folder_Redirection")
    assert entries["Flags"] == ("1021", f"{_FR_CSE}_s-1-1-0")
    assert entries["FullPath"] == (
        "\\\\zz-studio-fileserver\\zzredir\\%USERNAME%\\Documents",
        f"{_FR_CSE}_s-1-1-0",
    )
