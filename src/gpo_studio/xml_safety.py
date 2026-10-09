"""Bounded XML parsing with structural limits enforced during construction."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

_ENTITY_MARKERS = (
    b"<!ENTITY",
    b"<\x00!\x00E\x00N\x00T\x00I\x00T\x00Y\x00",
    b"\x00<\x00!\x00E\x00N\x00T\x00I\x00T\x00Y",
    b"<\x00\x00\x00!\x00\x00\x00E\x00\x00\x00N\x00\x00\x00T\x00\x00\x00I\x00\x00\x00T\x00\x00\x00Y\x00\x00\x00",
    b"\x00\x00\x00<\x00\x00\x00!\x00\x00\x00E\x00\x00\x00N\x00\x00\x00T\x00\x00\x00I\x00\x00\x00T\x00\x00\x00Y",
)


def xml_char_forbidden(cp: int) -> bool:
    """Whether code point *cp* is outside the XML 1.0 ``Char`` production.

    ``Char ::= #x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD] |
    [#x10000-#x10FFFF]``: the C0 controls other than TAB, LF and CR, the
    surrogate block (a lone surrogate is not a character at all, and no UTF
    encoder will write one), and U+FFFE/U+FFFF are forbidden. The other
    plane-final noncharacters (U+nFFFE/U+nFFFF above the BMP) are legal XML
    but are refused too, as before batch 2: nothing Windows writes uses them.
    """
    if cp < 0x20:
        return cp not in (0x09, 0x0A, 0x0D)
    if 0xD800 <= cp <= 0xDFFF:
        return True
    if cp in (0xFFFE, 0xFFFF):
        return True
    return cp > 0xFFFF and (cp & 0xFFFE) == 0xFFFE


def xml_text_problem(text: str, *, allow_cr: bool = False) -> str | None:
    """Why *text* cannot be written into XML and read back exactly, or ``None``.

    The ONE predicate every check uses (batch-2 review). TAB and LF are always
    allowed: ElementTree writes them as character references in attributes and
    literally in element text, and both read back exactly. CR is refused by
    default: an XML parser normalizes CR and CRLF in element text to LF, so a
    GPO name, a description or a GPP value holding one would come back changed
    after a native export and re-import. *allow_cr* is for text that never
    becomes XML (Registry.pol data, the fdeploy INI document); see
    `CR_ALLOWED_PATHS`.
    """
    for ch in text:
        cp = ord(ch)
        if xml_char_forbidden(cp):
            if 0xD800 <= cp <= 0xDFFF:
                return f"a lone surrogate U+{cp:04X}"
            return f"U+{cp:04X}, which XML 1.0 forbids"
        if cp == 0x0D and not allow_cr:
            return "a carriage return, which XML reads back as a line feed"
    return None


#: Model paths whose strings never become XML, so a carriage return in them
#: survives: Registry.pol value data (binary PReg, UTF-16) and the parsed
#: fdeploy INI document. Every other string -- GPO name and description, GPP
#: items and attributes, ILT, filters -- reaches XML element text or attribute
#: values somewhere (Backup.xml, bkupInfo.xml, manifest.xml, GPP XML), where a
#: CR would come back as LF (batch-2 review).
CR_ALLOWED_PATHS = (
    re.compile(r"settings/\d+/value(/\d+)?"),
    re.compile(r"fdeploy(/.*)?"),
)


def unwritable_text(
    data: Any, path: str = "", *, allow_cr_everywhere: bool = False
) -> list[tuple[str, str]]:
    """Every string in a plain-data tree (dicts, lists, str) XML cannot carry.

    Returns ``(path, problem)`` pairs. Dict keys are checked as well as values.
    Generic on purpose: a field added to any model is covered the day it
    lands, instead of the day someone remembers to validate it. Paths are model
    paths (``gpo.to_dict()``); a caller walking some other shape, such as a raw
    request body, passes *allow_cr_everywhere* and leaves the CR decision to
    the model check.
    """
    allow_cr = allow_cr_everywhere or any(
        pattern.fullmatch(path) for pattern in CR_ALLOWED_PATHS
    )
    found: list[tuple[str, str]] = []
    if isinstance(data, str):
        problem = xml_text_problem(data, allow_cr=allow_cr)
        if problem is not None:
            found.append((path, problem))
    elif isinstance(data, dict):
        for key, value in data.items():
            child = f"{path}/{key}" if path else str(key)
            if isinstance(key, str):
                problem = xml_text_problem(key, allow_cr=allow_cr)
                if problem is not None:
                    found.append((child, f"its key holds {problem}"))
            found.extend(
                unwritable_text(value, child, allow_cr_everywhere=allow_cr_everywhere)
            )
    elif isinstance(data, (list, tuple)):
        for index, value in enumerate(data):
            found.extend(
                unwritable_text(
                    value, f"{path}/{index}", allow_cr_everywhere=allow_cr_everywhere
                )
            )
    return found


def _has_entity_decl(data: bytes) -> bool:
    return any(marker in data for marker in _ENTITY_MARKERS)


_ENCODING_ATTR_RE = re.compile(
    r"\s+encoding\s*=\s*(['\"])[^'\"]*\1", re.IGNORECASE
)


def _normalize_xml_encoding(data: bytes) -> bytes:
    if data.startswith(b"\xff\xfe"):
        text = data.decode("utf-16-le")
    elif data.startswith(b"\xfe\xff"):
        text = data.decode("utf-16-be")
    else:
        return data
    if text.startswith("\ufeff"):
        text = text[1:]
    text = _ENCODING_ATTR_RE.sub("", text, count=1)
    return text.encode("utf-8")


class BoundedTreeBuilder(ET.TreeBuilder):
    """TreeBuilder that enforces structural limits during parsing.

    Raises ValueError (or the specified error_class) when limits are exceeded,
    preventing the full tree from being constructed in memory.
    """

    def __init__(
        self,
        *,
        max_elements: int = 100_000,
        max_depth: int = 100,
        max_text_length: int = 1_048_576,
        max_attr_length: int = 4096,
        error_class: type[Exception] = ValueError,
    ) -> None:
        super().__init__()
        self._max_elements = max_elements
        self._max_depth = max_depth
        self._max_text_length = max_text_length
        self._max_attr_length = max_attr_length
        self._error_class = error_class
        self._element_count = 0
        self._depth = 0
        # One counter per open element.  Each counter represents the current
        # logical text slot: the element's text before its first child, or the
        # most recently closed child's tail.  Expat may deliver either slot in
        # multiple data callbacks (including across ignored comments/PIs), so
        # checking individual callbacks is insufficient.
        self._text_lengths: list[int] = []

    def start(self, tag: str, attrs: dict[str, str]) -> Any:
        self._element_count += 1
        self._depth += 1
        if self._element_count > self._max_elements:
            raise self._error_class(
                f"XML element count exceeds {self._max_elements}"
            )
        if self._depth > self._max_depth:
            raise self._error_class(
                f"XML nesting depth exceeds {self._max_depth}"
            )
        for attr_val in attrs.values():
            if len(attr_val) > self._max_attr_length:
                raise self._error_class(
                    f"XML attribute length exceeds {self._max_attr_length}"
                )
        elem = super().start(tag, attrs)
        self._text_lengths.append(0)
        return elem

    def end(self, tag: str) -> Any:
        elem = super().end(tag)
        self._text_lengths.pop()
        self._depth -= 1
        if self._text_lengths:
            # Subsequent parent data belongs to this element's tail, which is
            # a new logical text slot with its own length limit.
            self._text_lengths[-1] = 0
        return elem

    def data(self, text: str) -> None:
        current_length = self._text_lengths[-1] if self._text_lengths else 0
        new_length = current_length + len(text)
        if new_length > self._max_text_length:
            raise self._error_class(
                f"XML text length exceeds {self._max_text_length}"
            )
        if self._text_lengths:
            self._text_lengths[-1] = new_length
        super().data(text)


def parse_xml_bounded(
    data: bytes | str,
    *,
    max_size: int,
    max_elements: int = 100_000,
    max_depth: int = 100,
    max_text_length: int = 1_048_576,
    max_attr_length: int = 4096,
    error_class: type[Exception] = ValueError,
    builder: BoundedTreeBuilder | None = None,
) -> ET.Element:
    """Parse XML with structural limits enforced during construction.

    Checks byte size and entity declarations before parsing, then
    uses a BoundedTreeBuilder to enforce structural limits incrementally.
    A caller that needs more from the parse (namespace declarations, say)
    passes its own *builder*, a `BoundedTreeBuilder` subclass constructed with
    the limits it wants; the ``max_*`` arguments other than *max_size* are then
    the builder's business.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    data = _normalize_xml_encoding(data)
    if len(data) > max_size:
        raise error_class(f"XML exceeds {max_size} bytes")
    if _has_entity_decl(data):
        raise error_class("XML entity declarations are not allowed")
    if builder is None:
        builder = BoundedTreeBuilder(
            max_elements=max_elements,
            max_depth=max_depth,
            max_text_length=max_text_length,
            max_attr_length=max_attr_length,
            error_class=error_class,
        )
    parser = ET.XMLParser(target=builder)
    try:
        return ET.fromstring(data, parser=parser)
    except ET.ParseError as error:
        raise error_class(f"Malformed XML: {error}") from error
