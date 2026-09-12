"""Reader for ``fdeploy1.ini``, the artifact Folder Redirection actually ships.

Plan 034 WP-4 ruled Folder Redirection a **read target** on 2026-09-11
(:doc:`../../docs/scope-decision-2026-09-11-folder-redirection`). This module is
the read half. It does not write.

The distinction matters more here than the word "reader" usually carries.
``folder_redirection.py`` models redirection as ``User Shell Folders`` registry
policy -- what the client-side extension writes on the endpoint -- and R3
measured that the GPO carries something else entirely: a UTF-16LE INI at
``User/Documents & Settings/fdeploy1.ini``, beside an empty ``fdeploy.ini``
marker. Neither file was addressed by any code in this package. So this is not
a second way to read a thing already read; it is the first time the artifact is
read at all, and until the GPO model carries it (WI-068) the only way an
operator reaches it is the endpoint that composes this module.

**What is measured, and what is therefore not decoded.** One capture exists
(R3, GPMC on Windows Server 2025, banked at
``tests/fixtures/native-folder-redirection-gpmc/``). It shows one folder, one
principal, and ``Flags=1021``. That is a single observation of a ten-bit word
against the four booleans ``folder_redirection.py`` models, so no bit is
attributable to any option and this module attributes none: ``Flags`` is
carried as the integer Windows wrote and rendered as decimal and binary, never
as a set of named options. WI-066 owes the capture (R12) that would make the
encoding readable; ``object_security.py``'s propagation codes were wrong on all
three values until R4 measured them, and guessing a bit layout from one point
is the same mistake with more bits.

The decode boundary follows ``security_template.py``: strict about the wire
contract, tolerant about content it does not recognise, and verbatim about
everything it cannot interpret. A section or entry this module does not
understand survives the round trip rather than being dropped or guessed at.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from .model import ValidationIssue

_UTF16LE_BOM = b"\xff\xfe"
_UTF32LE_BOM = b"\xff\xfe\x00\x00"

_MAX_FDEPLOY_SIZE = 1024 * 1024
_MAX_SECTIONS = 5_000
_MAX_SECTION_ENTRIES = 5_000

#: The section listing each redirected folder and the principals it is
#: redirected for. Banked from R3; the name is Windows'.
FOLDER_REDIRECTION_SECTION = "Folder_Redirection"

#: The Folder Redirection client-side extension GUID. It is **not** the GUID
#: that appears inside ``fdeploy1.ini`` -- see :data:`KNOWN_FOLDER_NAMES`. R6's
#: census counted this GUID's registrations (0 of 26 production GPOs).
FOLDER_REDIRECTION_CSE_GUID = "{25537BA6-77A8-11D2-9B6C-0000F8080861}"

#: Documented ``KNOWNFOLDERID`` values, used for rendering only.
#:
#: The GUIDs keying a redirection are *folder* identifiers, not the CSE GUID.
#: Exactly one entry here is corroborated by a capture in this repository:
#: ``FDD39AD0`` (Documents), which R3 authored and whose ``FullPath`` ends in
#: ``\Documents``. The rest are documented Windows constants that no lane has
#: measured, so nothing in this module depends on them being right -- an
#: unrecognised GUID renders as itself, and :func:`known_folder_name` returns
#: ``None`` rather than inventing a name.
KNOWN_FOLDER_NAMES: dict[str, str] = {
    "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}": "Documents",
    "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}": "Desktop",
    "{33E28130-4E1E-4676-835A-98395C3BC3BB}": "Pictures",
    "{4BD8D571-6D19-48D3-BE97-422220080E43}": "Music",
    "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}": "Videos",
    "{374DE290-123F-4565-9164-39C4925E467B}": "Downloads",
    "{3EB685DB-65F9-4CF6-A03A-E3EF65729F3D}": "AppData (Roaming)",
    "{625B53C3-AB48-4EC1-BA1F-A1EF4146FC19}": "Start Menu",
    "{1777F761-68AD-4D8A-87BD-30B759FA33DD}": "Favorites",
    "{56784854-C6CB-462B-8169-88E350ACB882}": "Contacts",
    "{BFB9D5E0-C6A9-404C-B2B2-AE6DB6AF4968}": "Links",
    "{4C5C32FF-BB9D-43B0-B5B4-2D72E54EAAA4}": "Saved Games",
    "{7D1D3A04-DEBB-4115-95CF-2F29DA2920DA}": "Searches",
}

#: What a reader is told about ``Flags`` instead of a decoded option set.
FLAGS_ARE_UNDECODED = (
    "Flags is carried verbatim: one capture (R3) shows one value, which "
    "attributes no bit to any option. WI-066 owes the capture that would."
)

_SECTION_HEADER = re.compile(r"^\[(?P<name>.*)\]$")

#: What counts as a `Flags` value this reader will call an integer.
#:
#: Not `str.isdigit()` and not a bare `int()`: `int` accepts Python's own
#: spellings -- `1_021`, `+1021`, and every non-ASCII decimal digit -- none of
#: which Windows writes. Reading one as 1021 would report a value the file does
#: not carry, which is the whole failure mode this module is written against.
_FLAGS_VALUE = re.compile(r"^\d+$", re.ASCII)

#: ``[{folder-guid}_{principal-sid}]``. The separator is an underscore and SIDs
#: contain hyphens and digits only, so the split is unambiguous from the right.
_REDIRECTION_SECTION = re.compile(
    r"^(?P<folder>\{[0-9A-Fa-f-]{36}\})_(?P<principal>[Ss]-[0-9A-Fa-f-]+)$"
)


class FdeployError(ValueError):
    """Malformed or unsupported ``fdeploy`` content."""


@dataclass(frozen=True, slots=True)
class FdeploySection:
    """One INI section, preserved in file order with its lines verbatim."""

    name: str
    entries: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    unknown_lines: tuple[str, ...] = field(default_factory=tuple)

    def get(self, key: str) -> str | None:
        """Return the first value for *key*, matched case-insensitively."""
        folded = key.casefold()
        for name, value in self.entries:
            if name.casefold() == folded:
                return value
        return None


@dataclass(frozen=True, slots=True)
class FdeployRedirection:
    """One ``(folder, principal)`` redirection, as the file spells it.

    ``flags`` is the integer Windows wrote. It is not an option set: see
    :data:`FLAGS_ARE_UNDECODED`. ``flags_text`` keeps the spelling for the
    cases where it is not an integer at all.
    """

    folder_guid: str
    principal: str
    full_path: str = ""
    flags: int | None = None
    flags_text: str = ""
    entries: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def folder_name(self) -> str | None:
        """The documented name for this folder GUID, or None if unrecognised."""
        return known_folder_name(self.folder_guid)

    def describe_flags(self) -> str:
        """Render ``Flags`` without claiming to know what any bit means."""
        if self.flags is None:
            return f"{self.flags_text or '(absent)'} (not an integer)"
        return f"{self.flags} (binary {self.flags:b})"


@dataclass(frozen=True, slots=True)
class FdeployDocument:
    """A parsed ``fdeploy``/``fdeploy1`` file.

    ``sections`` is everything the file contained, in order. The typed views
    -- :meth:`version`, :meth:`folders`, :meth:`redirections` -- are derived
    from it and never the authority; anything they do not cover is still in
    ``sections`` and still round-trips.
    """

    sections: tuple[FdeploySection, ...] = field(default_factory=tuple)
    raw_text: str = ""
    parse_warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_marker(self) -> bool:
        """True for the empty ``fdeploy.ini`` GPMC lays down beside the policy."""
        return not self.sections

    def section(self, name: str) -> FdeploySection | None:
        """Return the first section named *name*, matched case-insensitively."""
        folded = name.casefold()
        for entry in self.sections:
            if entry.name.casefold() == folded:
                return entry
        return None

    @property
    def version(self) -> str | None:
        """The ``[version] version=`` value, verbatim (``100`` in R3)."""
        section = self.section("version")
        return section.get("version") if section is not None else None

    def folders(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Return ``(folder GUID, principals)`` from ``[Folder_Redirection]``.

        The value is a semicolon-separated, semicolon-terminated SID list;
        R3 banked one entry with one principal (``s-1-1-0``, Everyone).
        """
        section = self.section(FOLDER_REDIRECTION_SECTION)
        if section is None:
            return ()
        return tuple(
            (guid, tuple(sid for sid in value.split(";") if sid))
            for guid, value in section.entries
        )

    def redirections(self) -> tuple[FdeployRedirection, ...]:
        """Return every ``[{folder}_{principal}]`` section as a typed rule."""
        found: list[FdeployRedirection] = []
        for section in self.sections:
            match = _REDIRECTION_SECTION.match(section.name)
            if match is None:
                continue
            raw_flags = section.get("Flags") or ""
            flags = int(raw_flags) if _FLAGS_VALUE.match(raw_flags) else None
            found.append(
                FdeployRedirection(
                    folder_guid=match.group("folder"),
                    principal=match.group("principal"),
                    full_path=section.get("FullPath") or "",
                    flags=flags,
                    flags_text=raw_flags,
                    entries=section.entries,
                )
            )
        return tuple(found)


def known_folder_name(guid: str) -> str | None:
    """Return the documented folder name for *guid*, or None.

    Case- and brace-insensitive. Returns None rather than a guess for a GUID
    that is not in :data:`KNOWN_FOLDER_NAMES`; callers render the GUID itself.
    """
    key = "{" + guid.strip().strip("{}").upper() + "}"
    return KNOWN_FOLDER_NAMES.get(key)


# ---------------------------------------------------------------------------
# Byte codec and parser
# ---------------------------------------------------------------------------


def decode_fdeploy(data: bytes) -> str:
    """Decode a native ``fdeploy`` file's bytes to text.

    R3 measured the wire encoding as UTF-16LE with a byte-order mark and CRLF
    line endings throughout, for both the marker and the policy file. Decode
    that contract strictly rather than sniffing, for the reason
    ``decode_security_template`` gives for the sibling artifact: a tolerant
    codec lets an internally consistent round trip hide an invalid artifact.
    """
    if len(data) > _MAX_FDEPLOY_SIZE:
        raise FdeployError(f"fdeploy exceeds {_MAX_FDEPLOY_SIZE} bytes")
    if data.startswith(_UTF32LE_BOM):
        raise FdeployError("fdeploy must not use UTF-32LE encoding")
    if not data.startswith(_UTF16LE_BOM):
        raise FdeployError("fdeploy must be UTF-16LE with a byte-order mark")
    try:
        return data[len(_UTF16LE_BOM) :].decode("utf-16-le")
    except UnicodeDecodeError as error:
        raise FdeployError("fdeploy contains invalid UTF-16LE text") from error


def encode_fdeploy(text: str) -> bytes:
    """Encode ``fdeploy`` text as UTF-16LE/BOM with CRLF endings.

    This is the codec's other half, used to prove the reader lossless against
    the banked native bytes. It is **not** an authoring path: composing a
    document Windows has never written requires the ``Flags`` encoding R12
    owes, which is why Plan 034 WP-4 ruled read and deferred write.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    encoded = _UTF16LE_BOM + normalized.replace("\n", "\r\n").encode("utf-16-le")
    if len(encoded) > _MAX_FDEPLOY_SIZE:
        raise FdeployError(f"fdeploy exceeds {_MAX_FDEPLOY_SIZE} bytes")
    return encoded


def parse_fdeploy(text: str) -> FdeployDocument:
    """Parse ``fdeploy`` text into a :class:`FdeployDocument`.

    Preserves section order, entry order and every line that is neither, so
    ``format_fdeploy(parse_fdeploy(t)) == t``. Blank and whitespace-only lines
    outside any section are dropped by the parse and restored by the raw-text
    round trip -- both native files open with exactly such a preamble (an empty
    line, then five spaces), which is content GPMC wrote rather than transcript
    framing.
    """
    if len(text.encode("utf-8")) > _MAX_FDEPLOY_SIZE:
        raise FdeployError(f"fdeploy exceeds {_MAX_FDEPLOY_SIZE} bytes")

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")

    sections: list[FdeploySection] = []
    warnings: list[str] = []
    current: str | None = None
    entries: list[tuple[str, str]] = []
    unknown: list[str] = []

    def flush() -> None:
        nonlocal current, entries, unknown
        if current is not None:
            sections.append(
                FdeploySection(
                    name=current,
                    entries=tuple(entries),
                    unknown_lines=tuple(unknown),
                )
            )
            current = None
            entries = []
            unknown = []

    for line in normalized.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        header = _SECTION_HEADER.match(stripped)
        if header is not None:
            flush()
            if len(sections) >= _MAX_SECTIONS:
                raise FdeployError(f"section count exceeds {_MAX_SECTIONS}")
            name = header.group("name").strip()
            if not name:
                warnings.append("Encountered section header with empty name")
            current = name
            continue
        if current is None:
            warnings.append(f"Line outside any section: {stripped}")
            continue
        if "=" in stripped:
            if len(entries) >= _MAX_SECTION_ENTRIES:
                raise FdeployError(
                    f"entry count in section '{current}' "
                    f"exceeds {_MAX_SECTION_ENTRIES}"
                )
            key, _, value = stripped.partition("=")
            entries.append((key.strip(), value.strip()))
        else:
            unknown.append(stripped)
            warnings.append(f"Unparseable line in section '{current}': {stripped}")

    flush()

    return FdeployDocument(
        sections=tuple(sections),
        raw_text=text,
        parse_warnings=tuple(warnings),
    )


def format_fdeploy(document: FdeployDocument) -> str:
    """Serialize a :class:`FdeployDocument` back to ``fdeploy`` text.

    Returns ``raw_text`` when it still re-parses to the same sections, which is
    the lossless path the banked R3 capture exercises, and the path every
    document read from bytes takes. Reconstruction is the fallback for a
    document assembled in memory: it reproduces only what was read -- it
    invents no ``Flags`` value, because nothing here knows how to -- and it
    emits each section's unrecognised lines after its entries rather than where
    they stood, because a document that was never parsed from text has no
    original order to preserve. A document that *was* parsed never reaches
    this path, so nothing round-tripped from a file is reordered by it.
    """
    if document.raw_text:
        reparsed = parse_fdeploy(document.raw_text)
        if reparsed.sections == document.sections:
            return document.raw_text

    lines: list[str] = []
    for section in document.sections:
        lines.append(f"[{section.name}]")
        lines.extend(f"{key}={value}" for key, value in section.entries)
        lines.extend(section.unknown_lines)
    return "".join(f"{line}\r\n" for line in lines)


def read_fdeploy(data: bytes) -> FdeployDocument:
    """Decode and parse native ``fdeploy`` bytes in one step."""
    return parse_fdeploy(decode_fdeploy(data))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_fdeploy(document: FdeployDocument) -> tuple[ValidationIssue, ...]:
    """Report what a parsed document says that does not hang together.

    Every check here is structural -- a folder listed with no section, a
    section carrying no path. None of them judges ``Flags``, and none judges
    whether Windows would accept the file: no lane has read this artifact in
    the write direction, so an opinion about acceptance would be a guess
    wearing a severity.
    """
    issues: list[ValidationIssue] = []

    if document.is_marker:
        return ()

    if document.version is None:
        issues.append(
            ValidationIssue(
                severity="warning",
                code="missing_version",
                message="no [version] section; R3's capture carries version=100",
                path="fdeploy.version",
            )
        )

    listed: dict[str, tuple[str, ...]] = {
        guid.casefold(): principals for guid, principals in document.folders()
    }
    seen: set[str] = set()

    for rule in document.redirections():
        key = f"{rule.folder_guid.casefold()}_{rule.principal.casefold()}"
        base = f"fdeploy.{rule.folder_guid}_{rule.principal}"
        if key in seen:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="duplicate_redirection",
                    message=f"duplicate section for {rule.folder_guid}_{rule.principal}",
                    path=base,
                )
            )
        seen.add(key)

        principals = listed.get(rule.folder_guid.casefold())
        if principals is None:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="unlisted_redirection",
                    message=(
                        f"{rule.folder_guid} has a redirection section but is not "
                        f"listed in [{FOLDER_REDIRECTION_SECTION}]"
                    ),
                    path=base,
                )
            )
        elif not any(
            sid.casefold() == rule.principal.casefold() for sid in principals
        ):
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="unlisted_principal",
                    message=(
                        f"{rule.principal} has a section for {rule.folder_guid} but is "
                        f"not among its listed principals"
                    ),
                    path=base,
                )
            )

        if not rule.full_path:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="missing_full_path",
                    message="redirection section carries no FullPath",
                    path=f"{base}.FullPath",
                )
            )
        if not rule.flags_text:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="missing_flags",
                    message="redirection section carries no Flags",
                    path=f"{base}.Flags",
                )
            )
        elif rule.flags is None:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="unreadable_flags",
                    message=f"Flags is not an integer: {rule.flags_text!r}",
                    path=f"{base}.Flags",
                )
            )

    for guid, principals in document.folders():
        for sid in principals:
            if f"{guid.casefold()}_{sid.casefold()}" not in seen:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="missing_redirection_section",
                        message=(
                            f"[{FOLDER_REDIRECTION_SECTION}] lists {sid} for {guid} "
                            "with no matching section"
                        ),
                        path=f"fdeploy.{guid}",
                    )
                )

    return tuple(issues)


# ---------------------------------------------------------------------------
# Review surfaces
# ---------------------------------------------------------------------------


def fdeploy_report_lines(document: FdeployDocument) -> Iterator[str]:
    """Render a parsed document as review text.

    This is the whole point of the read ruling: the artifact reaches an
    operator today as a 458-byte hash in the unmodeled-file inventory, which
    says nothing a reviewer can act on.
    """
    if document.is_marker:
        yield "Empty marker file; GPMC writes one beside the policy. No sections."
        return

    yield f"Version: {document.version or '(absent)'}"
    yield f"Flags: {FLAGS_ARE_UNDECODED}"

    rules = document.redirections()
    if not rules:
        yield "No redirection sections."
    for rule in rules:
        name = rule.folder_name
        label = f"{name} {rule.folder_guid}" if name else rule.folder_guid
        yield f"{label} for {rule.principal}:"
        yield f"  FullPath: {rule.full_path or '(absent)'}"
        yield f"  Flags: {rule.describe_flags()}"
        for key, value in rule.entries:
            if key.casefold() not in {"flags", "fullpath"}:
                yield f"  {key}: {value}"

    unclaimed = tuple(
        section
        for section in document.sections
        if section.name.casefold() not in {"version", FOLDER_REDIRECTION_SECTION.casefold()}
        and _REDIRECTION_SECTION.match(section.name) is None
    )
    for section in unclaimed:
        yield f"Unrecognised section [{section.name}] (preserved, not interpreted):"
        for key, value in section.entries:
            yield f"  {key}: {value}"
        for line in section.unknown_lines:
            yield f"  {line}"

    for warning in document.parse_warnings:
        yield f"Parse warning: {warning}"


@dataclass(frozen=True, slots=True)
class FdeployChange:
    """One ``(folder, principal)`` redirection that differs between documents."""

    kind: str
    folder_guid: str
    principal: str
    old: FdeployRedirection | None = None
    new: FdeployRedirection | None = None


def _redirections_equal(old: FdeployRedirection, new: FdeployRedirection) -> bool:
    return (
        old.full_path == new.full_path
        and old.flags_text == new.flags_text
        and old.entries == new.entries
    )


def diff_fdeploy(
    old: FdeployDocument, new: FdeployDocument
) -> tuple[FdeployChange, ...]:
    """Compare two parsed documents by ``(folder GUID, principal)``.

    Identity is the pair because that is the file's own key -- a section is
    named ``[{folder}_{principal}]``, so a folder redirected for two groups is
    two rows, and R12 will show whether a folder can appear with more than one.
    Ordering is by identity, not file order, so the result is stable.
    """
    old_map = {(r.folder_guid.casefold(), r.principal.casefold()): r for r in old.redirections()}
    new_map = {(r.folder_guid.casefold(), r.principal.casefold()): r for r in new.redirections()}

    changes: list[FdeployChange] = []
    for key, rule in new_map.items():
        previous = old_map.get(key)
        if previous is None:
            changes.append(
                FdeployChange("added", rule.folder_guid, rule.principal, None, rule)
            )
        elif not _redirections_equal(previous, rule):
            changes.append(
                FdeployChange("modified", rule.folder_guid, rule.principal, previous, rule)
            )
    for key, rule in old_map.items():
        if key not in new_map:
            changes.append(
                FdeployChange("removed", rule.folder_guid, rule.principal, rule, None)
            )

    return tuple(
        sorted(changes, key=lambda c: (c.folder_guid.casefold(), c.principal.casefold()))
    )
