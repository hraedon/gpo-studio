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
    owner = draw(st.text(alphabet, min_size=1, max_size=20))
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
