"""Bounded XML parsing with structural limits enforced during construction."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

_ENTITY_MARKERS = (
    b"<!ENTITY",
    b"<\x00!\x00E\x00N\x00T\x00I\x00T\x00Y\x00",
    b"\x00<\x00!\x00E\x00N\x00T\x00I\x00T\x00Y",
    b"<\x00\x00\x00!\x00\x00\x00E\x00\x00\x00N\x00\x00\x00T\x00\x00\x00I\x00\x00\x00T\x00\x00\x00Y\x00\x00\x00",
    b"\x00\x00\x00<\x00\x00\x00!\x00\x00\x00E\x00\x00\x00N\x00\x00\x00T\x00\x00\x00I\x00\x00\x00T\x00\x00\x00Y",
)


def _has_entity_decl(data: bytes) -> bool:
    return any(marker in data for marker in _ENTITY_MARKERS)


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
) -> ET.Element:
    """Parse XML with structural limits enforced during construction.

    Checks byte size and entity declarations before parsing, then
    uses a BoundedTreeBuilder to enforce structural limits incrementally.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    if len(data) > max_size:
        raise error_class(f"XML exceeds {max_size} bytes")
    if _has_entity_decl(data):
        raise error_class("XML entity declarations are not allowed")
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
