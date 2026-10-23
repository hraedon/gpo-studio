"""Writer-emitted identity framing, including accepted empty element identifiers."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

from gpo_studio.admx import AdmxCatalogue, parse_admx
from gpo_studio.api import app
from gpo_studio.policy_config import (
    PolicyConfiguration,
    PolicySettingIdentity,
    _write_id,
    parse_structured_setting_id,
    resolve_policy,
)
from gpo_studio.settings_browser import build_settings_browser
from gpo_studio.store import WorkspaceStore


@st.composite
def writer_identities(draw):
    alphabet = st.sampled_from("abcXYZ09:-_/?#%\\; λ雪")
    owner = draw(st.text(alphabet, min_size=0, max_size=20))
    side = draw(st.sampled_from(("computer", "user")))
    kind = draw(
        st.sampled_from(("state", "state-list", "element", "element-list", "disabled-element"))
    )
    component = "" if kind in ("state", "state-list") else draw(st.text(alphabet, max_size=20))
    ordinal = draw(st.integers(1, 100)) if kind in ("state-list", "element-list") else 0
    return PolicySettingIdentity(owner, side, kind, component, ordinal)


def encoded(identity: PolicySettingIdentity) -> str:
    prefix = f"admx2:{identity.policy_id.encode('utf-8').hex()}:{identity.side}:"
    return _write_id(prefix, identity.kind, identity.component, identity.ordinal)


@given(writer_identities(), writer_identities())
@settings(max_examples=80, derandomize=True, deadline=None)
def test_writer_codec_roundtrips_and_does_not_alias_distinct_identities(
    left: PolicySettingIdentity,
    right: PolicySettingIdentity,
) -> None:
    left_id, right_id = encoded(left), encoded(right)
    assert parse_structured_setting_id(left_id) == left
    assert parse_structured_setting_id(right_id) == right
    assert (left_id == right_id) == (left == right)


def empty_element_xml(kind: str) -> bytes:
    return f"""<policyDefinitions xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions">
      <policyNamespaces><target prefix="synthetic"
        namespace="https://synthetic.example.test/empty" /></policyNamespaces>
      <policies><policy name="SyntheticEmpty" class="Machine"
        key="Software\\Policies\\Synthetic\\EmptyElement" valueName="State">
        <elements><{kind} id="" valueName="EmptyValue" valuePrefix="Item"
          key="Software\\Policies\\Synthetic\\EmptyElement\\Values" /></elements>
      </policy></policies></policyDefinitions>""".encode()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    store = WorkspaceStore(tmp_path / "identity-codec.db")
    monkeypatch.setattr(app.state, "store", store, raising=False)
    monkeypatch.setattr(app.state, "owns_store", False, raising=False)
    monkeypatch.setattr(app.state, "template_sources", [], raising=False)
    with TestClient(app) as test_client:
        yield test_client
    store.close()


@pytest.mark.parametrize("kind,value", [("text", "synthetic value"), ("list", ["first", "second"])])
def test_accepted_empty_element_ids_roundtrip_through_writer_browser_and_api(
    kind,
    value,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definitions, _, _ = parse_admx(empty_element_xml(kind))
    assert len(definitions) == 1
    policy = definitions[0]
    assert policy.elements[0].id == ""
    catalogue = AdmxCatalogue(policies=(policy,))
    monkeypatch.setattr(app.state, "admx_catalogue", catalogue, raising=False)
    resolved = resolve_policy(policy, PolicyConfiguration("computer", {"": value}))
    identities = [parse_structured_setting_id(row.id) for row in resolved]
    expected_kind = "element" if kind == "text" else "element-list"
    assert any(
        identity is not None and identity.kind == expected_kind and identity.component == ""
        for identity in identities
    )
    browser = build_settings_browser(catalogue, resolved)
    assert browser.resolved[0].element_values == {"": value}
    assert not browser.unresolved

    created = client.post("/api/gpos", json={"name": "Synthetic empty element"})
    assert created.status_code == 201
    gpo = created.json()["gpo"]
    configured = client.post(
        "/api/admx/policies/SyntheticEmpty/configure",
        json={
            "gpo_guid": gpo["guid"],
            "expected_revision": 1,
            "state": "enabled",
            "values": {"": value},
            "side": "computer",
        },
    )
    assert configured.status_code == 200, configured.text
    configured_ids = [row["id"] for row in configured.json()["gpo"]["settings"]]
    assert configured_ids == [row.id for row in resolved]
    if kind == "text":
        disabled = client.post(
            "/api/admx/policies/SyntheticEmpty/configure",
            json={
                "gpo_guid": gpo["guid"],
                "expected_revision": 2,
                "state": "disabled",
                "values": {},
                "side": "computer",
            },
        )
        assert disabled.status_code == 200, disabled.text
        disabled_ids = [
            parse_structured_setting_id(row["id"]) for row in disabled.json()["gpo"]["settings"]
        ]
        assert any(
            identity is not None
            and identity.kind == "disabled-element"
            and identity.component == ""
            for identity in disabled_ids
        )


@pytest.mark.parametrize("side", ["computer", "user"])
@pytest.mark.parametrize(
    "kind,component,ordinal",
    [
        ("state", "", 0),
        ("state-list", "", 1),
        ("element", "", 0),
        ("element-list", "", 1),
        ("disabled-element", "", 0),
    ],
)
def test_empty_owner_is_canonical_for_every_writer_kind(side, kind, component, ordinal) -> None:
    identity = PolicySettingIdentity("", side, kind, component, ordinal)
    assert parse_structured_setting_id(encoded(identity)) == identity


@pytest.mark.parametrize(
    "setting_id",
    [
        "admx2::other:state::0",
        "admx2::computer:state:61:0",
        "admx2::computer:state::00",
        "admx2::computer:state::+0",
        "admx2::computer:state::-0",
        "admx2::computer:state-list::0",
        "admx2::computer:state-list::01",
        "admx2::computer:element::1",
        "admx2::computer:element-list::10000000000",
        "admx2::computer:unknown::0",
        "admx2::computer:element:FF:0",
        "admx2::computer:element:4A:0",
        "admx2::computer:element:6 1:0",
        "admx2:ff:computer:state::0",
    ],
)
def test_empty_owner_acceptance_keeps_malformed_and_noncanonical_frames_refused(
    setting_id: str,
) -> None:
    assert parse_structured_setting_id(setting_id) is None


@st.composite
def bounded_writer_schemas(draw):
    from gpo_studio.admx import (
        PolicyDefinition,
        PolicyElement,
        PolicyListItem,
        PolicyValue,
        PolicyValueList,
    )

    components = [
        "",
        "Plain",
        "state",
        "listitem",
        "listitem-1",
        "listitem-12",
        "disabled-element",
        "1",
        "12",
        "01",
        "-1",
        "computer",
        "computer-",
        "computer-Option",
        "Tail-computer",
        "user",
        "user-",
        "user-Option",
        "Tail-user",
        "Items",
        "Items-12",
        "Items-named",
        "disabled-element-Option",
        "組",
        "x/y",
        "x%2F",
    ]
    names = draw(st.lists(st.sampled_from(components), min_size=1, max_size=3, unique=True))
    kinds = draw(
        st.lists(st.sampled_from(("text", "list")), min_size=len(names), max_size=len(names))
    )
    elements = tuple(
        PolicyElement(
            kind=kind,
            id=name,
            registry_key=f"Software\\Policies\\Synthetic\\Closure\\Element{index}",
            registry_value_name=f"Value{index}",
            attributes=(("valuePrefix", "Item"),),
        )
        for index, (name, kind) in enumerate(zip(names, kinds, strict=True))
    )
    fixed_count = draw(st.sampled_from((0, 1, 13)))
    fixed = (
        PolicyValueList(
            tuple(
                PolicyListItem(f"Fixed{i}", PolicyValue("string", "fixed", "REG_SZ"))
                for i in range(fixed_count)
            )
        )
        if fixed_count
        else None
    )
    return PolicyDefinition(
        draw(
            st.sampled_from(
                (
                    "p",
                    "p-computer",
                    "p-user",
                    "-computer",
                    "-user",
                    "computer-p",
                    "p-computer-",
                    "p-user-",
                    "",
                )
            )
        ),
        "Both",
        r"Software\Policies\Synthetic\Closure",
        "Synthetic closure",
        "",
        "",
        namespace=draw(
            st.sampled_from(("", "Synthetic.Closure", "https://synthetic.example.test/closure"))
        ),
        value_name=draw(st.sampled_from(("", "State"))),
        elements=elements,
        enabled_list=fixed,
        disabled_list=fixed,
    )


@given(bounded_writer_schemas())
@settings(max_examples=100, derandomize=True, deadline=None)
def test_mechanical_legacy_admission_is_closed_over_actual_writer_outputs(policy) -> None:
    """Observe real writer kinds/identities, including ordinal12 and cross-state aliases.

    Disable the compatibility floor to prove that it grants no legacy safety:
    the mechanical closure alone must handle every generated writer language.
    """
    from unittest.mock import patch

    import gpo_studio.policy_config as writer

    observed = []
    write_id = writer._write_id

    def recording(prefix, kind, component="", ordinal=0):
        result = write_id(prefix, kind, component, ordinal)
        observed.append((result, (kind, component, ordinal)))
        return result

    with (
        patch.object(writer, "_structured_family_compatibility", return_value=False),
        patch.object(writer, "_write_id", recording),
    ):
        family = writer.structured_policy_ids(policy)
        seen = {}
        for side in ("computer", "user"):
            for count in (0, 1, 3, 12):
                values = {
                    element.id: (
                        [f"item{i}" for i in range(count)] if element.kind == "list" else "scalar"
                    )
                    for element in policy.elements
                }
                for state in ("enabled", "disabled", "not_configured"):
                    observed.clear()
                    rows = writer.resolve_policy(
                        policy,
                        writer.PolicyConfiguration(
                            side, values if state == "enabled" else {}, state
                        ),
                    )
                    assert len({row.id for row in rows}) == len(rows)
                    assert [row.id for row in rows] == [item[0] for item in observed]
                    if state == "enabled":
                        browser = build_settings_browser(AdmxCatalogue(policies=(policy,)), rows)
                        assert not browser.unresolved
                        decoded_values = (
                            browser.resolved[0].element_values if browser.resolved else {}
                        )
                        assert all(
                            decoded_values.get(element.id, []) == values[element.id]
                            for element in policy.elements
                        )
                    for row, (_id, identity) in zip(rows, observed, strict=True):
                        expected = (side, *identity)
                        assert seen.setdefault(row.id, expected) == expected
                        if family:
                            decoded = writer.parse_structured_setting_id(row.id)
                            assert decoded is not None
                            assert (
                                decoded.policy_id,
                                decoded.side,
                                decoded.kind,
                                decoded.component,
                                decoded.ordinal,
                            ) == (policy.qualified_id, *expected)
                        else:
                            assert not set("/?#%\\").intersection(row.id)
                            parts = writer.legacy_setting_parts(row.id)
                            assert len(parts) == 1 and parts[0][:2] == (policy.qualified_id, side)
