"""The fdeploy reader, measured against the only capture there is.

Plan 034 WP-4 ruled Folder Redirection a read target. Nothing in the live
verdict set binds ``fdeploy.py`` -- no lane has ever read this artifact -- so
the banked R3 capture is doing the job a bound lane would otherwise do, and
these tests are written to that standard: the reader is exercised on bytes
reconstructed from the capture and hash-checked against what Windows wrote,
not on a fixture this repository invented.

The negative tests matter as much as the positive ones. ``Flags`` is a
ten-bit word seen once, so the reader is pinned to carry it and *not* decode
it; when R12 lands and the encoding is readable, these are the tests that
have to be deliberately changed.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from gpo_studio.fdeploy import (
    FLAGS_ARE_UNDECODED,
    FOLDER_REDIRECTION_CSE_GUID,
    FdeployDocument,
    FdeployError,
    FdeploySection,
    decode_fdeploy,
    diff_fdeploy,
    encode_fdeploy,
    fdeploy_report_lines,
    format_fdeploy,
    known_folder_name,
    parse_fdeploy,
    read_fdeploy,
    validate_fdeploy,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "native-folder-redirection-gpmc"

#: Banked in the R3 provenance record; the same constants
#: ``test_native_folder_redirection_capture.py`` binds.
_POLICY_SHA = "71f1026180c4a92ed5bcca3366e5d22b80a64931f663fa079ef5d49cd2450800"
_MARKER_SHA = "5ad8f52071d25165e7e68064ab194ec27a074a3846149ed0689af23e7f7f2d00"

#: The GUID keying R3's one redirection. It is a *folder* identifier
#: (FOLDERID_Documents), not the Folder Redirection CSE GUID -- WI-067.
_DOCUMENTS = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"
_EVERYONE = "s-1-1-0"


def _native_capture_bytes(name: str) -> bytes:
    """Rebuild the native UTF-16LE bytes from a banked ASCII transcript."""
    transcript = (_FIXTURES / name).read_text(encoding="ascii")
    header, counters, content = transcript.split("\n", 2)
    assert header == "first 4 bytes: FF FE 0D 00"
    size_match = re.search(r"size: (\d+) bytes", counters)
    lf_match = re.search(r"LF count: (\d+)", counters)
    assert size_match is not None and lf_match is not None
    assert content.count("\n") == int(lf_match.group(1))
    native = b"\xff\xfe" + content.replace("\n", "\r\n").encode("utf-16-le")
    assert len(native) == int(size_match.group(1))
    return native


def _policy_bytes() -> bytes:
    data = _native_capture_bytes("fdeploy1.ini.txt")
    assert hashlib.sha256(data).hexdigest() == _POLICY_SHA
    return data


def _marker_bytes() -> bytes:
    data = _native_capture_bytes("fdeploy.ini.txt")
    assert hashlib.sha256(data).hexdigest() == _MARKER_SHA
    return data


# ---------------------------------------------------------------------------
# The codec, against the native wire contract
# ---------------------------------------------------------------------------


def test_the_native_capture_decodes_and_parses_to_its_banked_shape() -> None:
    """Three sections, four entries, version=100 -- read as structure this time.

    ``test_native_folder_redirection_capture.py`` already binds these facts
    with a hand parser written inside the test. This asserts the *product*
    reader agrees with it, which is the thing that was missing.
    """
    document = read_fdeploy(_policy_bytes())

    assert [section.name for section in document.sections] == [
        "version",
        "Folder_Redirection",
        f"{_DOCUMENTS}_{_EVERYONE}",
    ]
    assert document.version == "100"
    assert document.folders() == ((_DOCUMENTS, (_EVERYONE,)),)
    assert document.parse_warnings == ()

    (rule,) = document.redirections()
    assert rule.folder_guid == _DOCUMENTS
    assert rule.principal == _EVERYONE
    assert rule.full_path == r"\\zz-studio-fileserver\zzredir\%USERNAME%\Documents"
    assert rule.flags == 1021
    assert rule.flags_text == "1021"


def test_the_reader_round_trips_the_native_bytes_exactly() -> None:
    """Losslessness against Windows' own bytes, not against our own output.

    WI-064's lesson is that a clean round trip through a tolerant parser
    proves nothing when both halves are ours. Here the input is a capture
    hash-bound to what GPMC wrote, so the round trip is a real claim.
    """
    data = _policy_bytes()
    document = read_fdeploy(data)
    assert encode_fdeploy(format_fdeploy(document)) == data


def test_the_marker_reads_as_an_empty_document_and_survives_the_round_trip() -> None:
    data = _marker_bytes()
    document = read_fdeploy(data)
    assert document.is_marker
    assert document.sections == ()
    assert document.version is None
    assert document.redirections() == ()
    assert encode_fdeploy(format_fdeploy(document)) == data


def test_the_codec_refuses_anything_but_utf16le_with_a_bom() -> None:
    """Strict for the reason ``decode_security_template`` is strict."""
    with pytest.raises(FdeployError, match="byte-order mark"):
        decode_fdeploy(b"[version]\r\nversion=100\r\n")
    with pytest.raises(FdeployError, match="UTF-32LE"):
        decode_fdeploy(b"\xff\xfe\x00\x00[version]")
    with pytest.raises(FdeployError, match="invalid UTF-16LE"):
        decode_fdeploy(b"\xff\xfe\x41")
    with pytest.raises(FdeployError, match="exceeds"):
        decode_fdeploy(b"\xff\xfe" + b"A" * (1024 * 1024))


# ---------------------------------------------------------------------------
# What the reader deliberately does not know
# ---------------------------------------------------------------------------


def test_flags_is_carried_and_never_decoded_into_options() -> None:
    """WI-066: one observation of a ten-bit word attributes no bit to anything.

    This is the test to change when R12 lands, and changing it should be a
    deliberate act with a capture behind it -- which is why the reader has no
    ``grant_exclusive_rights``-shaped attribute for anyone to fill in.
    """
    (rule,) = read_fdeploy(_policy_bytes()).redirections()

    assert rule.describe_flags() == "1021 (binary 1111111101)"
    exported = set(dir(rule))
    assert not exported & {
        "grant_exclusive_rights",
        "move_contents",
        "remove_redirect_on_policy_removal",
        "also_redirect_subfolders",
    }
    assert "attributes no bit" in FLAGS_ARE_UNDECODED

    rendered = "\n".join(fdeploy_report_lines(read_fdeploy(_policy_bytes())))
    assert "1021 (binary 1111111101)" in rendered
    assert "exclusive" not in rendered.casefold()


def test_a_non_integer_flags_value_is_reported_rather_than_coerced() -> None:
    document = parse_fdeploy(
        f"[{_DOCUMENTS}_{_EVERYONE}]\nFlags=lots\nFullPath=\\\\fs\\share\n"
    )
    (rule,) = document.redirections()
    assert rule.flags is None
    assert rule.flags_text == "lots"
    assert "not an integer" in rule.describe_flags()
    assert "unreadable_flags" in {issue.code for issue in validate_fdeploy(document)}


def test_an_unrecognised_folder_guid_is_never_given_a_name() -> None:
    """The name table is documentation, not measurement, so it may not guess."""
    assert known_folder_name(_DOCUMENTS) == "Documents"
    assert known_folder_name(_DOCUMENTS.lower()) == "Documents"
    assert known_folder_name(_DOCUMENTS.strip("{}")) == "Documents"
    assert known_folder_name("{00000000-0000-0000-0000-000000000000}") is None

    document = parse_fdeploy(
        "[{00000000-0000-0000-0000-000000000000}_S-1-5-21-1-1-1-513]\n"
        "Flags=1\nFullPath=\\\\fs\\share\n"
    )
    rendered = "\n".join(fdeploy_report_lines(document))
    assert "{00000000-0000-0000-0000-000000000000}" in rendered


def test_the_cse_guid_is_not_the_guid_the_file_carries() -> None:
    """WI-067: the fixture's provenance called the folder GUID the CSE GUID.

    R6's census counted the CSE GUID; ``fdeploy1.ini`` keys its sections by
    folder. Conflating them is how a demand figure and an artifact key come to
    look like the same measurement when they are not.
    """
    assert FOLDER_REDIRECTION_CSE_GUID == "{25537BA6-77A8-11D2-9B6C-0000F8080861}"
    assert FOLDER_REDIRECTION_CSE_GUID != _DOCUMENTS
    # The capture names the folder GUID and never the CSE GUID.
    policy_text = _policy_bytes()[2:].decode("utf-16-le")
    assert _DOCUMENTS in policy_text
    assert FOLDER_REDIRECTION_CSE_GUID not in policy_text


# ---------------------------------------------------------------------------
# Tolerance, preservation and validation
# ---------------------------------------------------------------------------


def test_unrecognised_sections_and_lines_survive_verbatim() -> None:
    """Preserve what is not understood; a reader that drops it is lying."""
    text = (
        "[version]\nversion=100\n"
        "[Something_New]\nKey=Value\nnot a key value line\n"
    )
    document = parse_fdeploy(text)
    section = document.section("Something_New")
    assert section is not None
    assert section.entries == (("Key", "Value"),)
    assert section.unknown_lines == ("not a key value line",)
    assert any("Unparseable line" in w for w in document.parse_warnings)

    rendered = "\n".join(fdeploy_report_lines(document))
    assert "Unrecognised section [Something_New] (preserved, not interpreted):" in rendered
    assert "not a key value line" in rendered
    assert format_fdeploy(document) == text


def test_validation_reports_structure_and_judges_nothing_else() -> None:
    """A folder listed with no section, and a section listed by nobody."""
    document = parse_fdeploy(
        "[version]\nversion=100\n"
        f"[Folder_Redirection]\n{_DOCUMENTS}={_EVERYONE};S-1-5-21-1-1-1-513;\n"
        f"[{_DOCUMENTS}_{_EVERYONE}]\nFlags=1021\nFullPath=\\\\fs\\share\n"
        "[{33E28130-4E1E-4676-835A-98395C3BC3BB}_S-1-1-0]\nFlags=1021\n"
    )
    codes = {issue.code for issue in validate_fdeploy(document)}
    assert "missing_redirection_section" in codes  # the second listed SID
    assert "unlisted_redirection" in codes  # Pictures, listed by nobody
    assert "missing_full_path" in codes  # Pictures again


def test_the_native_capture_validates_clean() -> None:
    assert validate_fdeploy(read_fdeploy(_policy_bytes())) == ()
    assert validate_fdeploy(read_fdeploy(_marker_bytes())) == ()


def test_a_missing_version_section_is_a_warning_not_a_refusal() -> None:
    document = parse_fdeploy(
        f"[Folder_Redirection]\n{_DOCUMENTS}={_EVERYONE};\n"
        f"[{_DOCUMENTS}_{_EVERYONE}]\nFlags=1021\nFullPath=\\\\fs\\share\n"
    )
    assert {issue.code for issue in validate_fdeploy(document)} == {"missing_version"}


# ---------------------------------------------------------------------------
# The diff
# ---------------------------------------------------------------------------


def test_the_diff_keys_on_folder_and_principal_together() -> None:
    """Identity is the pair because the file's own section name is the pair."""
    before = read_fdeploy(_policy_bytes())
    after = parse_fdeploy(
        "[version]\nversion=100\n"
        f"[Folder_Redirection]\n{_DOCUMENTS}={_EVERYONE};S-1-5-21-1-1-1-513;\n"
        f"[{_DOCUMENTS}_{_EVERYONE}]\nFlags=1021\nFullPath=\\\\other\\share\n"
        f"[{_DOCUMENTS}_S-1-5-21-1-1-1-513]\nFlags=1021\nFullPath=\\\\fs\\eng\n"
    )

    changes = diff_fdeploy(before, after)
    assert [(c.kind, c.principal) for c in changes] == [
        ("modified", _EVERYONE),
        ("added", "S-1-5-21-1-1-1-513"),
    ]
    modified = changes[0]
    assert modified.old is not None and modified.new is not None
    assert modified.old.full_path.endswith(r"%USERNAME%\Documents")
    assert modified.new.full_path == r"\\other\share"

    # Sorted by identity, so Everyone (s-1-1-0) precedes the group SID.
    assert [c.kind for c in diff_fdeploy(after, before)] == ["modified", "removed"]
    assert diff_fdeploy(before, before) == ()


def test_a_flags_change_alone_is_a_modification() -> None:
    """Even though no bit is decoded, a changed word is a changed policy."""
    before = parse_fdeploy(
        f"[{_DOCUMENTS}_{_EVERYONE}]\nFlags=1021\nFullPath=\\\\fs\\share\n"
    )
    after = parse_fdeploy(
        f"[{_DOCUMENTS}_{_EVERYONE}]\nFlags=1005\nFullPath=\\\\fs\\share\n"
    )
    (change,) = diff_fdeploy(before, after)
    assert change.kind == "modified"
    assert change.new is not None and change.new.flags == 1005


def test_flags_is_not_read_through_python_s_own_integer_spellings() -> None:
    """`int()` accepts more than Windows writes, and each one is a wrong answer.

    `1_021` is a Python literal, `+1021` is a Python sign, and `١٠٢١` is a
    non-ASCII decimal `int()` reads as 1021. None is a value `fdeploy1.ini`
    carries, and reporting any of them as 1021 would mean the reader stated a
    number the file does not contain.
    """
    # A leading space is not one of them: the parser strips surrounding
    # whitespace from every value, as an INI reader should, so `Flags= 1021`
    # is the same value written with a space and is read as 1021.
    for spelling in ("1_021", "+1021", "١٠٢١"):
        document = parse_fdeploy(
            f"[{_DOCUMENTS}_{_EVERYONE}]\nFlags={spelling}\nFullPath=\\\\fs\\share\n"
        )
        (rule,) = document.redirections()
        assert rule.flags is None, spelling
        assert rule.flags_text == spelling


def test_a_document_assembled_in_memory_serializes_without_inventing_anything() -> None:
    """The reconstruction path, which no file ever takes.

    It exists so a hand-built document is not silently unserializable, not so
    one can be authored: a section with no `Flags` comes back with no `Flags`,
    rather than a default nobody measured.
    """
    document = FdeployDocument(
        sections=(
            FdeploySection(name="version", entries=(("version", "100"),)),
            FdeploySection(
                name=f"{_DOCUMENTS}_{_EVERYONE}",
                entries=(("FullPath", r"\\fs\share"),),
                unknown_lines=("a line nothing parsed",),
            ),
        )
    )
    text = format_fdeploy(document)
    assert text == (
        "[version]\r\nversion=100\r\n"
        f"[{_DOCUMENTS}_{_EVERYONE}]\r\n"
        "FullPath=\\\\fs\\share\r\n"
        "a line nothing parsed\r\n"
    )
    assert "Flags" not in text
    (rule,) = parse_fdeploy(text).redirections()
    assert rule.flags is None and rule.flags_text == ""
