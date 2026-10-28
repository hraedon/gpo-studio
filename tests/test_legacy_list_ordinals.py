"""Unsupported raw ordinal spellings stay visible without rewriting valid producers."""

from __future__ import annotations

import subprocess
import sys
import types
from collections.abc import Iterator
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

import gpo_studio.api as api
from gpo_studio.admx import AdmxCatalogue, PolicyDefinition, parse_admx
from gpo_studio.policy_config import PolicyConfiguration, resolve_policy
from gpo_studio.settings_browser import _list_sort_key, build_settings_browser
from gpo_studio.store import WorkspaceStore

PARENT = "d2ea4fcf36f975ce1d73f42ef89bfe1c5453cab3"
OLD = "eec9cc0ba8fbf72ae74f43e8f1ca44a61f2ee3e6"
KEY = r"Software\Policies\Synthetic\LegacyOrdinal"


def historical_module(commit: str, filename: str, name: str) -> types.ModuleType:
    source = subprocess.check_output(
        ["git", "show", f"{commit}:src/gpo_studio/{filename}.py"],
        cwd=Path(__file__).resolve().parents[1],
    )
    module = types.ModuleType("gpo_studio." + name)
    module.__package__ = "gpo_studio"
    sys.modules[module.__name__] = module
    exec(compile(source, f"{commit}:{filename}.py", "exec"), module.__dict__)
    return module


@pytest.fixture(scope="module")
def historical() -> Iterator[tuple[Any, Any]]:
    old = historical_module(OLD, "policy_config", "_ordinal_old_writer")
    parent = historical_module(PARENT, "settings_browser", "_ordinal_parent_reader")
    yield old, parent
    del sys.modules[old.__name__]
    del sys.modules[parent.__name__]


def schema(
    *,
    lists: tuple[str, ...] = ("Entries",),
    scalars: tuple[str, ...] = (),
    structured: bool = False,
) -> PolicyDefinition:
    elements = [
        f'<list id="{name}" key="{KEY}\\List{index}" valuePrefix="Item{index}_"/>'
        for index, name in enumerate(lists)
    ] + [
        f'<text id="{name}" key="{KEY}\\Scalars" valueName="Scalar{index}"/>'
        for index, name in enumerate(scalars)
    ]
    namespace = "https://synthetic.example.test/ordinals" if structured else "Synthetic.Ordinals"
    xml = f'''<policyDefinitions><policyNamespaces>
      <target prefix="synthetic" namespace="{namespace}"/></policyNamespaces>
      <policies><policy name="Probe" class="Both" key="{KEY}">
        <elements>{"".join(elements)}</elements>
      </policy></policies></policyDefinitions>'''.encode()
    return parse_admx(xml)[0][0]


def malformed_row(policy: PolicyDefinition, ordinal: str):
    # A raw workspace substitution, deliberately NOT a historical writer output.
    (row,) = resolve_policy(policy, PolicyConfiguration("computer", {"Entries": ["raw item"]}))
    return replace(row, id=f"admx-{policy.qualified_id}-computer-Entries-{ordinal}")


@pytest.mark.parametrize("ordinal", ["²", "9" * 5000], ids=["superscript", "oversized"])
def test_parent_failure_and_api_recovery_preserve_all_raw_data(
    historical: tuple[Any, Any], ordinal: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _old, parent = historical
    policy = schema()
    row = malformed_row(policy, ordinal)
    catalogue = AdmxCatalogue(policies=(policy,))
    with pytest.raises(ValueError):
        parent.build_settings_browser(catalogue, (row,))
    with closing(WorkspaceStore(tmp_path / "ordinals.db")) as store:
        gpo = store.create_gpo(
            "Synthetic unsupported raw ordinal",
            settings=(row,),
            identity="ordinal-test",
            reason="Seed malformed raw workspace ID",
        )
        monkeypatch.setattr(api.app.state, "store", store, raising=False)
        monkeypatch.setattr(api.app.state, "owns_store", False, raising=False)
        monkeypatch.setattr(api.app.state, "admx_catalogue", catalogue, raising=False)
        monkeypatch.setattr(api.app.state, "template_sources", [], raising=False)
        original = store.get_gpo(gpo.guid)
        history = tuple(store.revisions(gpo.guid))
        with TestClient(
            api.app, base_url="http://127.0.0.1", raise_server_exceptions=False
        ) as client:
            before = tuple(store._connection.iterdump())
            digit_limit = sys.get_int_max_str_digits()
            native_before = client.get(f"/api/gpos/{gpo.guid}/gpmc-backup")
            assert native_before.status_code == 200
            with monkeypatch.context() as parent_patch:
                parent_patch.setattr(api, "build_settings_browser", parent.build_settings_browser)
                assert client.get(f"/api/gpos/{gpo.guid}/configured-settings").status_code == 500
                assert tuple(store._connection.iterdump()) == before
            for query in ("", "?side=computer", "?q=Probe", "?state=enabled"):
                response = client.get(f"/api/gpos/{gpo.guid}/configured-settings{query}")
                assert response.status_code == 200, response.text
                data = response.json()
                assert data["resolved"][0]["raw_setting_count"] == 1
                if ordinal == "²":
                    assert data["resolved"][0]["element_values"] == {}
                    assert len(data["unresolved"]) == 1
                    assert data["unresolved"][0]["id"] == row.id
                    assert data["unresolved"][0]["reason"] == "unsupported legacy list ordinal"
                else:
                    assert data["resolved"][0]["element_values"] == {"Entries": ["raw item"]}
                    assert not data["unresolved"]
                assert tuple(store._connection.iterdump()) == before
            assert client.get(f"/api/gpos/{gpo.guid}").status_code == 200
            assert client.get(f"/api/gpos/{gpo.guid}/revisions/1").status_code == 200
            # Read-only editor uncertainty is not a core/native export ban.
            native_after = client.get(f"/api/gpos/{gpo.guid}/gpmc-backup")
            assert native_after.status_code == 200 and native_after.content == native_before.content
            assert sys.get_int_max_str_digits() == digit_limit
            assert tuple(store._connection.iterdump()) == before
        assert store.get_gpo(gpo.guid) == original
        assert tuple(store.revisions(gpo.guid)) == history


@pytest.mark.parametrize("ordinal", ["²", "١²", "１２²", "²2"])
def test_unorderable_digit_like_ordinal_is_unresolved_beside_exact_valid_items(
    ordinal: str,
) -> None:
    policy = schema()
    good = resolve_policy(policy, PolicyConfiguration("computer", {"Entries": ["one", "two"]}))
    bad = malformed_row(policy, ordinal)
    rows = (good[1], bad, good[0])
    result = build_settings_browser(AdmxCatalogue(policies=(policy,)), rows)
    assert result.resolved[0].element_values == {"Entries": ["one", "two"]}
    assert result.resolved[0].raw_settings == rows
    assert [(item.setting, item.reason) for item in result.unresolved] == [
        (bad, "unsupported legacy list ordinal")
    ]
    assert _list_sort_key("Entries-" + ordinal, "Entries") is None


@pytest.mark.parametrize("producer", ["old", "current"])
@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("side", ["computer", "user"])
@pytest.mark.parametrize(
    "name",
    [
        "Entries",
        "²",
        "١",
        "0",
        "01",
        "Entries-²",
        "Entries-0",
        "Entries-01",
        "Entries-" + "9" * 4000,
    ],
    ids=[
        "plain",
        "superscript",
        "Arabic",
        "zero",
        "leading-zero",
        "suffix-Unicode",
        "suffix-zero",
        "suffix-leading-zero",
        "huge-name",
    ],
)
def test_parsed_source_and_real_producers_preserve_numeric_order_and_names(
    historical: tuple[Any, Any], producer: str, structured: bool, side: str, name: str
) -> None:
    old, _parent = historical
    policy = schema(lists=(name,), structured=structured)
    values = {name: [f"item{index}" for index in range(1, 13)]}
    rows = (
        old.resolve_policy(policy, old.PolicyConfiguration(side, values))
        if producer == "old"
        else resolve_policy(policy, PolicyConfiguration(side, values))
    )
    shuffled = tuple(reversed(rows))
    result = build_settings_browser(AdmxCatalogue(policies=(policy,)), shuffled)
    assert result.resolved[0].element_values == values
    assert result.resolved[0].raw_settings == shuffled
    assert not result.unresolved


@pytest.mark.parametrize("suffix", ["²", "١", "0", "01"])
@pytest.mark.parametrize("producer", ["old", "current"])
def test_noncanonical_digits_in_exact_scalar_ids_still_bind_to_real_source(
    historical: tuple[Any, Any], suffix: str, producer: str
) -> None:
    old, _parent = historical
    scalar = "Entries-" + suffix
    policy = schema(scalars=(scalar,))
    values = {"Entries": [], scalar: "scalar value"}
    rows = (
        old.resolve_policy(policy, old.PolicyConfiguration("computer", values))
        if producer == "old"
        else resolve_policy(policy, PolicyConfiguration("computer", values))
    )
    result = build_settings_browser(AdmxCatalogue(policies=(policy,)), rows)
    assert result.resolved[0].element_values == {scalar: "scalar value"}
    assert not result.unresolved


@pytest.mark.parametrize("ordinal", ["10000000000", "9" * 4000], ids=["eleven-digits", "huge"])
@pytest.mark.parametrize("sibling_kind", ["scalar", "list"])
def test_unbounded_canonical_sibling_roles_keep_parent_ambiguity(
    historical: tuple[Any, Any], ordinal: str, sibling_kind: str
) -> None:
    old, parent = historical
    sibling = "Entries-" + ordinal
    policy = schema(
        lists=("Entries", sibling) if sibling_kind == "list" else ("Entries",),
        scalars=(sibling,) if sibling_kind == "scalar" else (),
    )
    values: dict[str, Any] = {
        "Entries": [],
        sibling: ["sibling"] if sibling_kind == "list" else "sibling",
    }
    rows = old.resolve_policy(policy, old.PolicyConfiguration("computer", values))
    row = rows[0]
    if sibling_kind == "list":
        # A raw bare component also has a possible canonical Entries-N parse.
        row = replace(row, id=f"admx-{policy.qualified_id}-computer-{sibling}")
    catalogue = AdmxCatalogue(policies=(policy,))
    result = build_settings_browser(catalogue, (row,))
    before = parent.build_settings_browser(catalogue, (row,))
    assert result.resolved[0].element_values == before.resolved[0].element_values
    assert [(item.setting, item.reason) for item in result.unresolved] == [
        (item.setting, item.reason) for item in before.unresolved
    ]
    if sibling_kind == "list":
        assert result.unresolved and not result.resolved[0].element_values
    # Exact structured identities do carry the source distinction and remain usable.
    current = resolve_policy(policy, PolicyConfiguration("computer", values))
    current_result = build_settings_browser(AdmxCatalogue(policies=(policy,)), current)
    assert current_result.resolved[0].element_values == {sibling: values[sibling]}
    assert not current_result.unresolved


def test_supported_canonical_sibling_collision_keeps_existing_ambiguity(
    historical: tuple[Any, Any],
) -> None:
    old, parent = historical
    policy = schema(scalars=("Entries-12",))
    rows = old.resolve_policy(
        policy,
        old.PolicyConfiguration(
            "computer", {"Entries": [str(i) for i in range(12)], "Entries-12": "scalar"}
        ),
    )
    catalogue = AdmxCatalogue(policies=(policy,))
    before = parent.build_settings_browser(catalogue, rows)
    result = build_settings_browser(catalogue, rows)
    assert result.resolved[0].element_values == before.resolved[0].element_values
    assert [(item.setting, item.reason) for item in result.unresolved] == [
        (item.setting, item.reason) for item in before.unresolved
    ]
    assert result.unresolved


@given(
    st.lists(
        st.one_of(
            st.text(alphabet=st.characters(categories=("Nd",)), min_size=1, max_size=40),
            st.sampled_from(["", "-1", "+1", "x", "1-2", "²", "١²"]),
        ),
        min_size=1,
        max_size=12,
        unique=True,
    )
)
@settings(max_examples=100, derandomize=True, deadline=None)
def test_bounded_order_matches_parent_int_for_every_successful_decimal_spelling(
    ordinals: list[str],
) -> None:
    policy = schema()
    rows = tuple(
        replace(
            malformed_row(policy, ordinal), value=f"value{index}", value_name=f"Item0_{index + 1}"
        )
        for index, ordinal in enumerate(ordinals)
    )
    expected = []
    rejected = set()
    for index, ordinal in enumerate(ordinals):
        try:
            key = int(ordinal) if ordinal.isdigit() else 0
        except ValueError:
            rejected.add(rows[index].id)
        else:
            expected.append((key, f"value{index}"))
    expected.sort(key=lambda item: item[0])
    result = build_settings_browser(AdmxCatalogue(policies=(policy,)), rows)
    assert result.resolved[0].element_values.get("Entries", []) == [
        value for _key, value in expected
    ]
    assert {item.setting.id for item in result.unresolved} == rejected
    assert all(item.reason == "unsupported legacy list ordinal" for item in result.unresolved)
    assert len(expected) + len(rejected) == len(rows)


@pytest.mark.parametrize("name", ["Entries-²", "Entries-0", "Entries-01"])
@pytest.mark.parametrize("producer", ["old", "current"])
def test_nested_source_list_names_are_not_mistaken_for_malformed_parent_ordinals(
    historical: tuple[Any, Any], name: str, producer: str
) -> None:
    old, _parent = historical
    policy = schema(lists=("Entries", name))
    values = {"Entries": ["parent"], name: ["child-one", "child-two"]}
    rows = (
        old.resolve_policy(policy, old.PolicyConfiguration("computer", values))
        if producer == "old"
        else resolve_policy(policy, PolicyConfiguration("computer", values))
    )
    result = build_settings_browser(AdmxCatalogue(policies=(policy,)), tuple(reversed(rows)))
    assert result.resolved[0].element_values == values
    assert not result.unresolved


def test_unbounded_legacy_and_bounded_structured_ordinals_share_exact_numeric_order(
    historical: tuple[Any, Any],
) -> None:
    old, _parent = historical
    policy = schema(structured=True)
    legacy = old.resolve_policy(
        policy, old.PolicyConfiguration("computer", {"Entries": ["legacy1", "unused"]})
    )[0]
    structured = resolve_policy(
        policy, PolicyConfiguration("computer", {"Entries": ["unused", "unused", "structured3"]})
    )[-1]
    rows = (
        replace(legacy, id=legacy.id.rsplit("-", 1)[0] + "-" + "9" * 5000, value="legacy5000"),
        replace(legacy, id=legacy.id.rsplit("-", 1)[0] + "-12", value="legacy12"),
        structured,
        replace(legacy, id=legacy.id.rsplit("-", 1)[0] + "-10000000000", value="legacy11digits"),
        legacy,
    )
    result = build_settings_browser(AdmxCatalogue(policies=(policy,)), rows)
    assert result.resolved[0].element_values == {
        "Entries": ["legacy1", "structured3", "legacy12", "legacy11digits", "legacy5000"]
    }
    assert result.resolved[0].raw_settings == rows
    assert not result.unresolved
    assert _list_sort_key("Entries-" + "9" * 5000, "Entries") == (5000, "9" * 5000)


@pytest.mark.parametrize(
    "ordinals",
    [
        ["01", "0", "١", "００", "12", "٢"],
        ["", "-1", "+1", "x", "1-2", "0", "01"],
    ],
)
def test_explicit_previously_successful_legacy_spellings_keep_parent_order(
    historical: tuple[Any, Any],
    ordinals: list[str],
) -> None:
    _old, parent = historical
    policy = schema()
    rows = tuple(
        replace(malformed_row(policy, ordinal), value=f"item{index}")
        for index, ordinal in enumerate(ordinals)
    )
    catalogue = AdmxCatalogue(policies=(policy,))
    before = parent.build_settings_browser(catalogue, rows)
    result = build_settings_browser(catalogue, rows)
    assert result.resolved[0].element_values == before.resolved[0].element_values
    assert not result.unresolved


def test_huge_unicode_decimal_and_zero_padded_ordinals_keep_numeric_order() -> None:
    policy = schema()
    ordinals = ["٠" * 5000 + "١٢", "13", "0" * 5000 + "2", "９" * 5000, "12"]
    values = ["Arabic12", "ascii13", "padded2", "fullwidth-huge", "ascii12"]
    rows = tuple(
        replace(malformed_row(policy, ordinal), value=value)
        for ordinal, value in zip(ordinals, values, strict=True)
    )
    result = build_settings_browser(AdmxCatalogue(policies=(policy,)), rows)
    assert result.resolved[0].element_values == {
        "Entries": ["padded2", "Arabic12", "ascii12", "ascii13", "fullwidth-huge"]
    }
    assert not result.unresolved
