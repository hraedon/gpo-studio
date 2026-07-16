"""Bounded property tests for release-critical parsers and codecs."""

from __future__ import annotations

import json
from contextlib import suppress

from hypothesis import given, settings
from hypothesis import strategies as st

from gpo_studio.canonical import canonical_json
from gpo_studio.gpp import GppError, parse_gpp_groups, parse_gpp_registry
from gpo_studio.registry_pol import PolRecord, RegistryPolError, parse, serialize

_FUZZ = settings(max_examples=200, deadline=None, derandomize=True, database=None)
_TEXT = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs",),
        blacklist_characters=("\x00", ";"),
    ),
    min_size=1,
    max_size=32,
)


@st.composite
def _pol_record(draw: st.DrawFn) -> PolRecord:
    registry_type = draw(
        st.sampled_from(
            [
                "REG_SZ",
                "REG_EXPAND_SZ",
                "REG_BINARY",
                "REG_DWORD",
                "REG_MULTI_SZ",
                "REG_QWORD",
            ]
        )
    )
    if registry_type in {"REG_SZ", "REG_EXPAND_SZ"}:
        value: str | int | list[str] = draw(_TEXT)
    elif registry_type == "REG_BINARY":
        value = draw(st.binary(max_size=64)).hex().upper()
    elif registry_type == "REG_DWORD":
        value = draw(st.integers(min_value=0, max_value=0xFFFFFFFF))
    elif registry_type == "REG_QWORD":
        value = draw(st.integers(min_value=0, max_value=0xFFFFFFFFFFFFFFFF))
    else:
        value = draw(st.lists(_TEXT, max_size=12))
    return PolRecord(
        key=draw(_TEXT),
        value_name=draw(_TEXT),
        registry_type=registry_type,
        value=value,
    )


@_FUZZ
@given(_pol_record())
def test_registry_pol_round_trip_property(record: PolRecord) -> None:
    assert parse(serialize([record])) == [record]


@_FUZZ
@given(st.binary(max_size=4096))
def test_registry_pol_arbitrary_bytes_fail_with_domain_error(data: bytes) -> None:
    with suppress(RegistryPolError):
        parse(data)


@_FUZZ
@given(st.binary(max_size=4096))
def test_gpp_arbitrary_bytes_fail_with_domain_error(data: bytes) -> None:
    for parser in (parse_gpp_groups, parse_gpp_registry):
        with suppress(GppError):
            parser(data)


_JSON = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(min_value=-(2**80), max_value=2**80)
    | st.floats(allow_nan=False, allow_infinity=False, width=64)
    | st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=64),
    lambda children: st.lists(children, max_size=8)
    | st.dictionaries(
        st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=24),
        children,
        max_size=8,
    ),
    max_leaves=30,
)


@_FUZZ
@given(_JSON)
def test_canonical_json_is_deterministic_and_parseable(value: object) -> None:
    first = canonical_json(value)
    second = canonical_json(value)
    assert first == second
    assert json.loads(first) == value
