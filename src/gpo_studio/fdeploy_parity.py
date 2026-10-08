"""What the fdeploy lane compares: Studio's reader against Windows' own reading.

Plan 034's 2026-10-07 ruling gave ``fdeploy.py`` a lane: banked R3 bytes go
through ``Import-GPO``, then ``Backup-GPO`` and ``Get-GPOReport`` are compared
with the parse. This module is the comparison half, kept out of every bound
file so the lane costs no existing verdict.

It reads two Windows artifacts and one Studio artifact:

* the ``FolderRedirectionSettings`` extension in a ``Get-GPOReport -ReportType
  Xml`` document -- each ``Folder``'s ``Id``, every ``Location``'s
  ``DestinationPath`` and ``SecurityGroup``, and the option elements Windows
  renders beside them;
* the report's ``Identifier``/``Name``, so a lane can prove the report is of
  the GPO it owned;
* what :mod:`gpo_studio.fdeploy` claims about a document: folder GUID,
  principal, ``FullPath`` and the raw ``Flags`` integer.

**The option elements are recorded, never compared with anything Studio
says.** ``fdeploy.py`` deliberately decodes no ``Flags`` bit (WI-066), and the
2026-10-08 probe showed the report engine does not read ``Flags`` as a flat
bit field, so there is no Studio claim to hold them against. They are data for
WI-066, read here so a lane can bank them.

**Nor is ``DestinationPath`` the same thing as ``FullPath`` for every
``Flags`` value.** The same probe rendered an empty ``DestinationPath`` for
``Flags`` 765 and 2045 while the file carried a ``FullPath``. The agreement
:func:`reader_report_differences` checks is therefore a claim about the
``Flags`` values a lane runs, not about the file format.

This module composes no fdeploy bytes and calls neither half of the writer
codec (``tests/test_folder_redirection_scope.py`` holds that line).
"""

from __future__ import annotations

import difflib
import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .fdeploy import FdeployDocument, FdeployError, diff_fdeploy, read_fdeploy
from .xml_safety import parse_xml_bounded

SETTINGS_NS = "http://www.microsoft.com/GroupPolicy/Settings"
TYPES_NS = "http://www.microsoft.com/GroupPolicy/Types"
FOLDER_REDIRECTION_NS = "http://www.microsoft.com/GroupPolicy/Settings/FolderRedirection"
_XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"

#: The ``xsi:type`` local name Windows gives the extension (2026-10-08 probe).
FOLDER_REDIRECTION_TYPE = "FolderRedirectionSettings"
#: The ``ExtensionData/Name`` Windows gives it, also where it reports errors.
FOLDER_REDIRECTION_EXTENSION_NAME = "Folder Redirection"

_MAX_REPORT_BYTES = 16 * 1024 * 1024
_MAX_DIFF_LINES = 200


class FdeployParityError(ValueError):
    """A report this module cannot read as a GPMC settings report."""


@dataclass(frozen=True, slots=True)
class ReportIdentity:
    """Which GPO a report describes. GUID unbraced and casefolded."""

    guid: str
    domain: str
    name: str


@dataclass(frozen=True, slots=True)
class ReportRedirection:
    """One ``Folder``/``Location`` pair as Windows' report renders it.

    ``options`` is every child of ``Folder`` other than ``Id`` and
    ``Location``, as ``(element, text)`` in document order. Recorded only.
    """

    folder_id: str
    principal_sid: str
    principal_name: str
    destination_path: str
    options: tuple[tuple[str, str], ...] = ()

    def key(self) -> tuple[str, str, str]:
        return (self.folder_id, self.principal_sid, self.destination_path)


@dataclass(frozen=True, slots=True)
class FolderRedirectionRendering:
    """The Folder Redirection part of one report.

    ``extension_count`` counts ``FolderRedirectionSettings`` extensions on the
    user side; ``errors`` holds every ``Error/Details`` text under an
    ``ExtensionData`` named Folder Redirection (what Windows printed for
    ``Flags=0``: "FRSettingRead failed"); ``computer_side_extensions`` counts
    any that appeared under ``Computer``, which no capture has shown.
    """

    extension_count: int
    errors: tuple[str, ...]
    redirections: tuple[ReportRedirection, ...]
    computer_side_extensions: int = 0

    def keys(self) -> list[tuple[str, str, str]]:
        return sorted(r.key() for r in self.redirections)

    def to_json(self) -> dict[str, object]:
        return {
            "extension_count": self.extension_count,
            "computer_side_extensions": self.computer_side_extensions,
            "errors": list(self.errors),
            "redirections": [
                {
                    "folder_id": r.folder_id,
                    "principal_sid": r.principal_sid,
                    "principal_name": r.principal_name,
                    "destination_path": r.destination_path,
                    "options": dict(r.options),
                }
                for r in self.redirections
            ],
        }


@dataclass(frozen=True, slots=True)
class ReaderClaim:
    """What ``fdeploy.py`` says one redirection section carries."""

    folder_guid: str
    principal: str
    full_path: str
    flags: int | None
    flags_text: str

    def key(self) -> tuple[str, str, str]:
        return (self.folder_guid, self.principal, self.full_path)

    def to_json(self) -> dict[str, object]:
        return {
            "folder_guid": self.folder_guid,
            "principal": self.principal,
            "full_path": self.full_path,
            "flags": self.flags,
            "flags_text": self.flags_text,
        }


def _parse(report_xml: bytes) -> ET.Element:
    root = parse_xml_bounded(
        report_xml, max_size=_MAX_REPORT_BYTES, error_class=FdeployParityError
    )
    if root.tag != f"{{{SETTINGS_NS}}}GPO":
        raise FdeployParityError("not a GPMC settings report")
    return root


def _text(element: ET.Element | None) -> str:
    return (element.text or "").strip() if element is not None else ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def report_identity(report_xml: bytes) -> ReportIdentity:
    """Read the GPO a ``Get-GPOReport`` document says it describes."""
    root = _parse(report_xml)
    ident = root.find(f"{{{SETTINGS_NS}}}Identifier")
    guid = ident.find(f"{{{TYPES_NS}}}Identifier") if ident is not None else None
    domain = ident.find(f"{{{TYPES_NS}}}Domain") if ident is not None else None
    if not _text(guid):
        raise FdeployParityError("report names no GPO identifier")
    return ReportIdentity(
        guid=_text(guid).strip("{}").casefold(),
        domain=_text(domain),
        name=_text(root.find(f"{{{SETTINGS_NS}}}Name")),
    )


def _is_folder_redirection(extension: ET.Element) -> bool:
    return _local(extension.get(_XSI_TYPE, "")).rsplit(":", 1)[-1] == FOLDER_REDIRECTION_TYPE


def _side_extensions(root: ET.Element, side: str) -> tuple[list[ET.Element], list[str]]:
    extensions: list[ET.Element] = []
    errors: list[str] = []
    side_elem = root.find(f"{{{SETTINGS_NS}}}{side}")
    if side_elem is None:
        return extensions, errors
    for data in side_elem.findall(f"{{{SETTINGS_NS}}}ExtensionData"):
        named = _text(data.find(f"{{{SETTINGS_NS}}}Name")) == FOLDER_REDIRECTION_EXTENSION_NAME
        for extension in data.findall(f"{{{SETTINGS_NS}}}Extension"):
            if _is_folder_redirection(extension):
                extensions.append(extension)
        if named:
            for error in data.findall(f"{{{SETTINGS_NS}}}Error"):
                details = _text(error.find(f"{{{SETTINGS_NS}}}Details"))
                errors.append(details or "(error with no details)")
    return extensions, errors


def folder_redirection_rendering(report_xml: bytes) -> FolderRedirectionRendering:
    """Read every redirection Windows' report renders, with its options.

    Text is kept exactly as Windows printed it, apart from the surrounding
    whitespace ``ElementTree`` reads between elements. A ``Folder`` with no
    ``Location`` renders as one row with empty path and principal, so a
    missing location is visible rather than silently dropped.
    """
    root = _parse(report_xml)
    extensions, errors = _side_extensions(root, "User")
    computer_extensions, computer_errors = _side_extensions(root, "Computer")
    fr = FOLDER_REDIRECTION_NS
    rows: list[ReportRedirection] = []
    for extension in extensions:
        for folder in extension.findall(f"{{{fr}}}Folder"):
            folder_id = _text(folder.find(f"{{{fr}}}Id"))
            options = tuple(
                (_local(child.tag), (child.text or "").strip())
                for child in folder
                if child.tag not in {f"{{{fr}}}Id", f"{{{fr}}}Location"}
            )
            locations: list[ET.Element | None] = list(folder.findall(f"{{{fr}}}Location"))
            for location in locations or [None]:
                group = location.find(f"{{{fr}}}SecurityGroup") if location is not None else None
                rows.append(
                    ReportRedirection(
                        folder_id=folder_id,
                        principal_sid=_text(
                            group.find(f"{{{TYPES_NS}}}SID") if group is not None else None
                        ),
                        principal_name=_text(
                            group.find(f"{{{TYPES_NS}}}Name") if group is not None else None
                        ),
                        destination_path=_text(
                            location.find(f"{{{fr}}}DestinationPath")
                            if location is not None
                            else None
                        ),
                        options=options,
                    )
                )
    return FolderRedirectionRendering(
        extension_count=len(extensions),
        errors=tuple(errors + [f"computer side: {e}" for e in computer_errors]),
        redirections=tuple(rows),
        computer_side_extensions=len(computer_extensions),
    )


def reader_claims(document: FdeployDocument) -> tuple[ReaderClaim, ...]:
    """Everything ``fdeploy.py`` claims about each redirection, nothing more."""
    return tuple(
        ReaderClaim(
            folder_guid=rule.folder_guid,
            principal=rule.principal,
            full_path=rule.full_path,
            flags=rule.flags,
            flags_text=rule.flags_text,
        )
        for rule in document.redirections()
    )


def reader_report_differences(
    claims: tuple[ReaderClaim, ...], rendering: FolderRedirectionRendering
) -> list[str]:
    """Where Studio's reading and Windows' rendering disagree; empty if they agree.

    Compared exactly -- no case folding, no trimming beyond what both parsers
    already do -- as the multiset of ``(folder, principal, path)``. A report
    that renders nothing, renders an error, or renders on the computer side is
    a disagreement, not an empty agreement.
    """
    problems: list[str] = []
    if not claims:
        problems.append("Studio's reader claims no redirection")
    if rendering.extension_count != 1:
        problems.append(
            f"report renders {rendering.extension_count} FolderRedirectionSettings "
            "extension(s) on the user side, not exactly one"
        )
    if rendering.computer_side_extensions:
        problems.append("report renders Folder Redirection on the computer side")
    problems.extend(f"report error: {error}" for error in rendering.errors)
    studio = sorted(claim.key() for claim in claims)
    windows = rendering.keys()
    for key in studio:
        if studio.count(key) != windows.count(key):
            problems.append(f"Studio reads {key}; Windows renders it {windows.count(key)} time(s)")
    for key in windows:
        if key not in studio:
            problems.append(f"Windows renders {key}; Studio does not read it")
    return sorted(set(problems))


def encoding_facts(data: bytes) -> dict[str, object]:
    """The wire facts R3's provenance records, measured on *data*."""
    facts: dict[str, object] = {
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bom": data[:2].hex(),
    }
    try:
        text = data[2:].decode("utf-16-le") if data.startswith(b"\xff\xfe") else None
    except UnicodeDecodeError:
        text = None
    if text is None:
        facts.update(utf16le_bom=False, cr_count=None, lf_count=None, crlf_only=False)
        return facts
    cr, lf, crlf = text.count("\r"), text.count("\n"), text.count("\r\n")
    facts.update(utf16le_bom=True, cr_count=cr, lf_count=lf, crlf_only=cr == lf == crlf)
    return facts


def byte_differences(candidate: bytes, windows: bytes) -> dict[str, object]:
    """Record every way Windows' copy of a file differs from the candidate's.

    For a lane that asserts byte identity this is the evidence a failure
    leaves behind: the encoding facts of both, whether Windows' copy still
    reads, the reader-level changes ``diff_fdeploy`` sees, and a line diff of
    the decoded text (bounded).
    """
    record: dict[str, object] = {
        "identical": candidate == windows,
        "candidate": encoding_facts(candidate),
        "windows": encoding_facts(windows),
    }
    if candidate == windows:
        return record
    try:
        ours = read_fdeploy(candidate)
        theirs = read_fdeploy(windows)
    except FdeployError as error:
        record["windows_reads"] = False
        record["read_error"] = str(error)
        return record
    record["windows_reads"] = True
    record["reader_changes"] = [
        {
            "kind": change.kind,
            "folder_guid": change.folder_guid,
            "principal": change.principal,
            "old": ReaderClaim(
                change.old.folder_guid, change.old.principal, change.old.full_path,
                change.old.flags, change.old.flags_text,
            ).to_json() if change.old else None,
            "new": ReaderClaim(
                change.new.folder_guid, change.new.principal, change.new.full_path,
                change.new.flags, change.new.flags_text,
            ).to_json() if change.new else None,
        }
        for change in diff_fdeploy(ours, theirs)
    ]
    lines = list(
        difflib.unified_diff(
            ours.raw_text.splitlines(keepends=True),
            theirs.raw_text.splitlines(keepends=True),
            "candidate", "windows", lineterm="",
        )
    )
    record["line_diff"] = [repr(line) for line in lines[:_MAX_DIFF_LINES]]
    record["line_diff_truncated"] = len(lines) > _MAX_DIFF_LINES
    return record
