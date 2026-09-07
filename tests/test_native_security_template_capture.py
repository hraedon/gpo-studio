"""Byte-level contract for the first native GPMC-authored GptTmpl.inf capture.

The contract is the banked R4 measurement (Windows Server 2025 GPMC,
2026-09-05, windows-console-driver r4-v2b-record.json), not a reading of the
docs: UTF-16LE with BOM, CRLF on every line including the last, three sections
in the order ``[Unicode]``, ``[Version]``, ``[Registry Keys]``, and every
``[Registry Keys]`` entry a bare three-field quoted-CSV row. The original file
was not retained; the ASCII transcript and provenance live in
fixtures/native-security-template-gpmc/.
"""

from __future__ import annotations

import re
from pathlib import Path

from gpo_studio.security_template import (
    decode_security_template,
    format_security_template,
    parse_security_template,
    validate_security_template,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "native-security-template-gpmc"

# Banked in the r4-v2b-record.json envelope delta (gpttmpl.encoding.*):
# bom=utf16le, cr_count=9, lf_count=9, crlf_only=true.
_BANKED_CR_COUNT = 9
_BANKED_LF_COUNT = 9

#: The committed number for the first native template this project ever
#: parsed: every ``[Registry Keys]`` row is a bare quoted-CSV line the parser
#: cannot read (WI-038, preserve-only). Three authored keys, three unknown
#: lines, ``entries`` empty. Changing this number is a parser change, not a
#: fixture change.
_FIRST_NATIVE_UNKNOWN_LINES = 3

_SDDL = (
    "D:PAR(A;CI;KA;;;BA)(A;CIIO;KA;;;CO)(A;CI;KA;;;SY)"
    "(A;CI;KR;;;BU)(A;CI;KR;;;S-1-15-2-1)"
)
# Banked verbatim (gpttmpl.registry_keys.N.raw). The file order is the REVERSE
# of the authoring order (Charlie, Bravo, Alpha) -- measured 2026-09-03 in the
# R4 observer, windows-console-driver gpo_observers/gpttmpl_inf.py.
_BANKED_ROWS = (
    rf'"MACHINE\SOFTWARE\zzStudioCharlie",1,"{_SDDL}"',
    rf'"MACHINE\SOFTWARE\zzStudioBravo",2,"{_SDDL}"',
    rf'"MACHINE\SOFTWARE\zzStudioAlpha",0,"{_SDDL}"',
)


def _native_capture_bytes(name: str) -> bytes:
    """Rebuild the native UTF-16LE bytes from a banked ASCII transcript.

    The transcript carries a two-line measurement header, then the decoded
    content verbatim except that each CRLF was transcribed as LF. Rebuilding
    the native bytes and cross-checking the length against the header's banked
    byte count proves both the transcript and the reconstruction.
    """
    transcript = (_FIXTURES / name).read_text(encoding="ascii")
    header, counters, content = transcript.split("\n", 2)
    assert header == "first 4 bytes: FF FE 5B 00"
    size = int(re.search(r"size: (\d+) bytes", counters).group(1))
    lf_count = int(re.search(r"LF count: (\d+)", counters).group(1))
    assert content.count("\n") == lf_count
    native = b"\xff\xfe" + content.replace("\n", "\r\n").encode("utf-16-le")
    assert len(native) == size, (len(native), size)
    return native


def test_gpttmpl_reconstruction_reproduces_banked_wire_facts() -> None:
    data = _native_capture_bytes("gpttmpl.inf.txt")
    # gpttmpl.encoding.bom == "utf16le": BOM then the '[' of "[Unicode]"
    # (unlike the scripts.ini captures, there is no leading blank line).
    assert data.startswith(b"\xff\xfe[")
    assert data.count(b"\r") == _BANKED_CR_COUNT
    assert data.count(b"\n") == _BANKED_LF_COUNT
    # gpttmpl.encoding.crlf_only: every LF is preceded by a CR, and the last
    # line carries a terminator too.
    text = data[2:].decode("utf-16-le")
    assert text.count("\r\n") == _BANKED_LF_COUNT
    assert text.endswith("\r\n")
    # No blank lines anywhere: 3 section headers + 1 + 2 + 3 banked content
    # lines == 9 terminated lines.
    assert len(text.split("\r\n")) == _BANKED_LF_COUNT + 1


def test_first_native_gpttmpl_parses_with_committed_unknown_lines() -> None:
    data = _native_capture_bytes("gpttmpl.inf.txt")
    text = decode_security_template(data)  # BOM required, then stripped
    template = parse_security_template(text)

    # Banked section order and per-section line counts (gpttmpl.sections.*).
    assert [s.name for s in template.sections] == [
        "Unicode",
        "Version",
        "Registry Keys",
    ]
    assert len(template.sections[0].entries) == 1
    assert len(template.sections[1].entries) == 2

    # The key=value sections parsed as entries (quotes retained in values).
    assert template.get_value("Unicode", "Unicode") == "yes"
    assert template.get_value("Version", "signature") == '"$CHICAGO$"'
    assert template.get_value("Version", "Revision") == "1"

    # [Registry Keys] is preserve-only (WI-038): entries empty, every row in
    # unknown_lines verbatim, in file order.
    registry = template.get_section("Registry Keys")
    assert registry is not None
    assert registry.entries == ()
    assert registry.unknown_lines == _BANKED_ROWS
    assert (
        sum(len(s.unknown_lines) for s in template.sections)
        == _FIRST_NATIVE_UNKNOWN_LINES
    )
    assert len(template.parse_warnings) == _FIRST_NATIVE_UNKNOWN_LINES

    # Not lost: preserve-only round-trips the text identically.
    assert format_security_template(template) == text
    assert template.raw_text == text

    # And validation says so, rather than staying silent (WI-038).
    issues = validate_security_template(template)
    assert any(
        issue.code == "unparsed_entries" and "3 line(s)" in issue.message
        for issue in issues
    )
