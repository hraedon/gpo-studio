"""Real-shaped ADMX/ADML parsing: namespaces, ADML presentations, identity.

Plan 022 WP-1. The existing ``tests/fixtures/admx/`` files carry an inline
``<presentation>`` child inside ``<policy>``, which is not valid ADMX and does
not occur in shipped Windows or vendor templates. Real files put the controls in
the ADML ``<presentationTable>`` and reference them by
``presentation="$(presentation.X)"``. Parsing that reference is what makes the
authoring surface non-empty against a real central store — the same class of
fixture-shaped gap as the namespace bug fixed earlier in WP-1.

These tests run against ``tests/fixtures/admx_real_shape/``, whose files are
deliberately shaped like the real thing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gpo_studio.admx import (
    AdmxCatalogue,
    AmbiguousPolicyError,
    PolicyDefinition,
    build_catalogue,
    find_policy,
    load_catalogue,
    parse_adml_presentations,
)

FIXTURES = Path(__file__).parent / "fixtures" / "admx_real_shape"


@pytest.fixture(scope="module")
def catalogue() -> AdmxCatalogue:
    return load_catalogue(FIXTURES)


@pytest.fixture(scope="module")
def vendora() -> AdmxCatalogue:
    return build_catalogue(
        (FIXTURES / "vendora.admx").read_bytes(),
        (FIXTURES / "vendora.adml").read_bytes(),
    )


def _policy(catalogue: AdmxCatalogue, qualified_id: str) -> PolicyDefinition:
    policy = find_policy(catalogue, qualified_id)
    assert policy is not None, f"{qualified_id} not found"
    return policy


# --- namespaces -------------------------------------------------------------


def test_target_namespace_is_parsed(vendora: AdmxCatalogue) -> None:
    assert [(d.prefix, d.namespace) for d in vendora.target_namespaces] == [
        ("vendora", "Synthetic.Policies.VendorA")
    ]


def test_using_namespace_is_parsed(vendora: AdmxCatalogue) -> None:
    assert [(d.prefix, d.namespace) for d in vendora.used_namespaces] == [
        ("windows", "Microsoft.Policies.Windows")
    ]


def test_policies_carry_their_target_namespace(vendora: AdmxCatalogue) -> None:
    policy = vendora.policies[0]
    assert policy.namespace == "Synthetic.Policies.VendorA"
    assert policy.qualified_id == "Synthetic.Policies.VendorA:SharedPolicyName"


def test_qualified_id_falls_back_to_bare_name_without_namespace() -> None:
    # tests/fixtures/admx/ declares no <policyNamespaces>; those policies keep
    # their bare name as identity rather than gaining an empty ":" prefix.
    legacy = load_catalogue(Path(__file__).parent / "fixtures" / "admx")
    assert legacy.policies, "legacy fixture catalogue should not be empty"
    for policy in legacy.policies:
        assert policy.namespace == ""
        assert policy.qualified_id == policy.id


# --- ADML presentation table ------------------------------------------------


def test_presentation_table_parses_standalone() -> None:
    presentations = parse_adml_presentations((FIXTURES / "vendora.adml").read_bytes())
    assert set(presentations) == {"SharedPolicyName"}


def test_presentation_resolves_from_adml_not_inline(vendora: AdmxCatalogue) -> None:
    # The regression this whole module exists for: the ADMX has no inline
    # <presentation>, so a parser that only looks there yields zero controls.
    policy = vendora.policies[0]
    assert policy.presentation_ref == "SharedPolicyName"
    assert [p.kind for p in policy.presentation] == [
        "checkbox",
        "decimal",
        "text",
        "list",
    ]


def test_presentation_controls_bind_to_elements_by_ref_id(
    vendora: AdmxCatalogue,
) -> None:
    policy = vendora.policies[0]
    element_ids = {e.id for e in policy.elements}
    assert {p.ref_id for p in policy.presentation} == element_ids


def test_presentation_label_from_element_text(vendora: AdmxCatalogue) -> None:
    labels = {p.ref_id: p.label for p in vendora.policies[0].presentation}
    assert labels["FeatureEnabled"] == "Enable the synthetic feature"
    assert labels["RetryCount"] == "Retry count"


def test_presentation_label_from_label_child(vendora: AdmxCatalogue) -> None:
    # textBox/listBox wrap the label in a <label> child rather than element text.
    labels = {p.ref_id: p.label for p in vendora.policies[0].presentation}
    assert labels["ServerName"] == "Server name"
    assert labels["AllowedHosts"] == "Allowed hosts"


def test_presentation_id_defaults_to_ref_id(vendora: AdmxCatalogue) -> None:
    # ADML controls carry no id attribute; refId is the identity.
    for control in vendora.policies[0].presentation:
        assert control.id == control.ref_id


def test_empty_presentation_yields_no_controls(catalogue: AdmxCatalogue) -> None:
    policy = _policy(catalogue, "Synthetic.Policies.VendorB:VendorBOnlyPolicy")
    assert policy.presentation == ()
    assert policy.presentation_ref == "VendorBOnlyPolicy"


# --- lossless element attributes --------------------------------------------


def test_element_attributes_are_preserved_for_typed_kinds(
    vendora: AdmxCatalogue,
) -> None:
    # These attributes have no typed field on PolicyElement yet; dropping them
    # would silently lose authoring semantics (WP-1: no silent loss).
    elements = {e.id: dict(e.attributes) for e in vendora.policies[0].elements}
    assert elements["RetryCount"]["minValue"] == "1"
    assert elements["RetryCount"]["maxValue"] == "99"
    assert elements["ServerName"]["expandable"] == "true"
    assert elements["ServerName"]["maxLength"] == "255"
    assert elements["AllowedHosts"]["valuePrefix"] == "Host"
    assert elements["AllowedHosts"]["additive"] == "true"


def test_element_tag_name_is_recorded(vendora: AdmxCatalogue) -> None:
    tags = {e.id: e.tag_name for e in vendora.policies[0].elements}
    assert tags == {
        "FeatureEnabled": "boolean",
        "RetryCount": "decimal",
        "ServerName": "text",
        "AllowedHosts": "list",
    }


# --- cross-namespace identity -----------------------------------------------


def test_colliding_names_are_distinct_policies(catalogue: AdmxCatalogue) -> None:
    shared = [p for p in catalogue.policies if p.id == "SharedPolicyName"]
    assert len(shared) == 2
    assert {p.qualified_id for p in shared} == {
        "Synthetic.Policies.VendorA:SharedPolicyName",
        "Synthetic.Policies.VendorB:SharedPolicyName",
    }


def test_qualified_lookup_selects_the_right_policy(catalogue: AdmxCatalogue) -> None:
    vendor_a = _policy(catalogue, "Synthetic.Policies.VendorA:SharedPolicyName")
    vendor_b = _policy(catalogue, "Synthetic.Policies.VendorB:SharedPolicyName")
    assert vendor_a.key == "Software\\Policies\\SyntheticVendorA\\Feature"
    assert vendor_b.key == "Software\\Policies\\SyntheticVendorB\\Feature"
    assert vendor_a.class_ == "Machine"
    assert vendor_b.class_ == "User"


def test_ambiguous_bare_name_raises_rather_than_guessing(
    catalogue: AdmxCatalogue,
) -> None:
    # Before qualified identity, this silently returned whichever file loaded
    # first — configuring the wrong vendor's registry key.
    with pytest.raises(AmbiguousPolicyError) as excinfo:
        find_policy(catalogue, "SharedPolicyName")
    assert excinfo.value.candidates == (
        "Synthetic.Policies.VendorA:SharedPolicyName",
        "Synthetic.Policies.VendorB:SharedPolicyName",
    )


def test_unambiguous_bare_name_still_resolves(catalogue: AdmxCatalogue) -> None:
    policy = find_policy(catalogue, "VendorBOnlyPolicy")
    assert policy is not None
    assert policy.qualified_id == "Synthetic.Policies.VendorB:VendorBOnlyPolicy"


def test_unknown_policy_returns_none(catalogue: AdmxCatalogue) -> None:
    assert find_policy(catalogue, "NoSuchPolicy") is None


def test_load_catalogue_merges_namespaces_from_all_files(
    catalogue: AdmxCatalogue,
) -> None:
    assert {d.namespace for d in catalogue.target_namespaces} == {
        "Synthetic.Policies.VendorA",
        "Synthetic.Policies.VendorB",
    }
