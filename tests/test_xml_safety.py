from __future__ import annotations

import pytest

from gpo_studio.xml_safety import parse_xml_bounded


@pytest.mark.parametrize(
    "xml",
    [
        b"<root>12345<!-- split -->67890</root>",
        b"<root><child/>12345<!-- split -->67890</root>",
    ],
    ids=["element-text", "child-tail"],
)
def test_text_limit_cannot_be_split_across_parser_callbacks(xml: bytes) -> None:
    with pytest.raises(ValueError, match="XML text length exceeds 6"):
        parse_xml_bounded(xml, max_size=1024, max_text_length=6)


def test_text_and_each_child_tail_have_independent_limits() -> None:
    root = parse_xml_bounded(
        b"<root>123456<first/>123456<second/>123456</root>",
        max_size=1024,
        max_text_length=6,
    )

    assert root.text == "123456"
    assert [child.tail for child in root] == ["123456", "123456"]
