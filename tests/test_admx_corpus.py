"""Representative ADMX/ADML corpus parses without silent loss (Plan 022 WP-1).

The fixtures under ``tests/fixtures/corpus/`` model general production
Group Policy patterns with synthetic namespaces, registry paths, and policy
combinations. This module verifies that the whole corpus loads and that no
policy, category, namespace, element, or presentation control is silently lost.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gpo_studio.admx import (
    AdmxCatalogue,
    PolicyDefinition,
    PolicyValue,
    build_catalogue,
    load_catalogue,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "corpus"


@pytest.fixture(scope="module")
def catalogue() -> AdmxCatalogue:
    return load_catalogue(_FIXTURES)


def _policy(catalogue: AdmxCatalogue, name: str) -> PolicyDefinition:
    matches = [p for p in catalogue.policies if p.id == name]
    assert len(matches) == 1, f"expected one {name!r}, found {len(matches)}"
    return matches[0]


# --- corpus-wide lossless parsing ------------------------------------------


def test_corpus_loads_expected_counts(catalogue: AdmxCatalogue) -> None:
    assert len(catalogue.policies) == 20
    assert len(catalogue.categories) == 15
    assert len(catalogue.supported_on) == 7
    assert len(catalogue.target_namespaces) == 5
    assert len(catalogue.used_namespaces) == 7


def test_all_target_namespaces_are_synthetic(catalogue: AdmxCatalogue) -> None:
    namespaces = {d.namespace for d in catalogue.target_namespaces}
    assert namespaces == {
        "Synthetic.Policies.SecurityBaseline",
        "Synthetic.Policies.WindowsUpdate",
        "Synthetic.Policies.Defender",
        "Synthetic.Policies.UserConfig",
        "Synthetic.Policies.Network",
    }


def test_all_using_namespaces_are_recorded(catalogue: AdmxCatalogue) -> None:
    namespaces = {d.namespace for d in catalogue.used_namespaces}
    assert namespaces == {
        "Microsoft.Policies.Windows",
        "Synthetic.Policies.SecurityBaseline",
    }


def test_no_unresolved_display_names(catalogue: AdmxCatalogue) -> None:
    for policy in catalogue.policies:
        assert not policy.display_name.startswith("$("), policy.id
        assert not policy.explain_text.startswith("$("), policy.id


def test_no_unresolved_category_display_names(catalogue: AdmxCatalogue) -> None:
    for category in catalogue.categories:
        assert not category.display_name.startswith("$("), category.id


def test_no_unresolved_supported_on_display_names(catalogue: AdmxCatalogue) -> None:
    for definition in catalogue.supported_on:
        assert not definition.display_name.startswith("$("), definition.name


def test_policies_with_elements_have_bound_presentations(
    catalogue: AdmxCatalogue,
) -> None:
    for policy in catalogue.policies:
        if not policy.elements:
            continue
        element_ids = {e.id for e in policy.elements}
        presentation_ref_ids = {p.ref_id for p in policy.presentation}
        assert element_ids == presentation_ref_ids, (
            f"{policy.id}: elements {element_ids} != presentation refs {presentation_ref_ids}"
        )


def test_policies_without_elements_have_empty_presentations(
    catalogue: AdmxCatalogue,
) -> None:
    for policy in catalogue.policies:
        if policy.elements:
            continue
        assert policy.presentation == (), policy.id


# --- representative pattern coverage ---------------------------------------


def test_security_baseline_account_lockout(catalogue: AdmxCatalogue) -> None:
    policy = _policy(catalogue, "AccountLockoutDuration")
    assert policy.class_ == "Machine"
    assert policy.parent_category == "AccountPolicies"
    elem = policy.elements[0]
    assert elem.kind == "decimal"
    assert elem.id == "LockoutDuration"
    assert dict(elem.attributes)["minValue"] == "0"
    assert dict(elem.attributes)["maxValue"] == "999"


def test_audit_policy_uses_implicit_default(catalogue: AdmxCatalogue) -> None:
    policy = _policy(catalogue, "AuditProcessTracking")
    assert policy.class_ == "Machine"
    assert policy.value_name == "AuditProcessTracking"
    assert policy.enabled_value is None
    assert policy.disabled_value is None
    assert policy.elements == ()


def test_enabled_list_disabled_list_parsed(catalogue: AdmxCatalogue) -> None:
    policy = _policy(catalogue, "SecurityOptionsList")
    assert policy.enabled_list is not None
    assert policy.disabled_list is not None
    assert policy.enabled_list.default_key == r"Software\Policies\TestLab\SecurityBaseline\Options"
    enabled = policy.enabled_list.items
    assert len(enabled) == 2
    assert enabled[0].value_name == "OptionA"
    assert enabled[0].value == PolicyValue("decimal", "1", "REG_DWORD")
    assert enabled[1].key == r"Software\Policies\TestLab\SecurityBaseline\Options\Extra"
    assert enabled[1].value_name == "OptionB"
    assert enabled[1].value == PolicyValue("string", "enabled", "REG_SZ")
    assert policy.disabled_list.items[0].value == PolicyValue("delete", "", None)


def test_delete_value_in_disabled_state(catalogue: AdmxCatalogue) -> None:
    policy = _policy(catalogue, "WUDeleteOnDisable")
    assert policy.enabled_value == PolicyValue("decimal", "1", "REG_DWORD")
    assert policy.disabled_value == PolicyValue("delete", "", None)


def test_long_decimal_enabled_disabled_values(catalogue: AdmxCatalogue) -> None:
    policy = _policy(catalogue, "NetworkBandwidthLimit")
    assert policy.enabled_value == PolicyValue("longDecimal", "4294967296", "REG_QWORD")
    assert policy.disabled_value == PolicyValue("longDecimal", "0", "REG_QWORD")


def test_explicit_value_list_attributes_preserved(catalogue: AdmxCatalogue) -> None:
    policy = _policy(catalogue, "UpdateAllowList")
    elem = policy.elements[0]
    assert elem.kind == "list"
    attrs = dict(elem.attributes)
    assert attrs["explicitValue"] == "true"


def test_additive_list_with_value_prefix(catalogue: AdmxCatalogue) -> None:
    policy = _policy(catalogue, "DNSPreferredServers")
    elem = policy.elements[0]
    assert elem.kind == "list"
    attrs = dict(elem.attributes)
    assert attrs["valuePrefix"] == "Server"
    assert attrs["additive"] == "true"


def test_class_both_policies(catalogue: AdmxCatalogue) -> None:
    both = {p.id for p in catalogue.policies if p.class_ == "Both"}
    assert both == {"BaselineBothClass", "NetworkBothClass"}


def test_user_policies_are_user_class(catalogue: AdmxCatalogue) -> None:
    user_ids = {"SetWallpaper", "StartMenuLayout", "TaskbarLock", "ScreenSaverTimeout"}
    for pid in user_ids:
        assert _policy(catalogue, pid).class_ == "User"


def test_enum_dropdown_items_resolved(catalogue: AdmxCatalogue) -> None:
    policy = _policy(catalogue, "DeferFeatureUpdates")
    elem = policy.elements[0]
    assert elem.kind == "enum"
    assert len(elem.enum_items) == 3
    assert elem.enum_items[0].value == 0
    assert elem.enum_items[1].value == 1
    assert elem.enum_items[2].value == 2
    names = [i.display_name for i in elem.enum_items]
    assert names == ["Do not defer", "Defer short term", "Defer long term"]


def test_multitext_element_parsed(catalogue: AdmxCatalogue) -> None:
    policy = _policy(catalogue, "DefenderExclusions")
    elem = policy.elements[0]
    assert elem.kind == "multitext"
    assert elem.id == "ExclusionPaths"
    pres = policy.presentation[0]
    assert pres.kind == "multitext"
    assert pres.ref_id == "ExclusionPaths"


def test_text_element_expandable_attribute_preserved(catalogue: AdmxCatalogue) -> None:
    policy = _policy(catalogue, "SetWallpaper")
    elem = policy.elements[0]
    assert elem.kind == "text"
    attrs = dict(elem.attributes)
    assert attrs["expandable"] == "true"
    assert attrs["maxLength"] == "260"
    assert attrs["required"] == "true"


def test_category_ancestry_within_file(catalogue: AdmxCatalogue) -> None:
    cats = {c.id: c for c in catalogue.categories}
    assert cats["AccountPolicies"].parent_id == "SecurityBaselineRoot"
    assert cats["AuditPolicies"].parent_id == "SecurityBaselineRoot"
    assert cats["UpdateBehavior"].parent_id == "WindowsUpdateRoot"
    assert cats["DeliveryOptimization"].parent_id == "WindowsUpdateRoot"
    assert cats["Antivirus"].parent_id == "DefenderRoot"
    assert cats["FirewallIntegration"].parent_id == "DefenderRoot"
    assert cats["DesktopSettings"].parent_id == "UserConfigRoot"
    assert cats["StartMenu"].parent_id == "UserConfigRoot"
    assert cats["TCPIPSettings"].parent_id == "NetworkRoot"
    assert cats["FirewallSettings"].parent_id == "NetworkRoot"


def test_multi_element_policy_has_all_control_kinds(catalogue: AdmxCatalogue) -> None:
    policy = _policy(catalogue, "NetworkBothClass")
    kinds = {e.kind for e in policy.elements}
    assert kinds == {"boolean", "decimal", "text"}
    pres_kinds = {p.kind for p in policy.presentation}
    assert pres_kinds == {"checkbox", "decimal", "text"}


def test_corpus_files_are_pairwise_complete() -> None:
    admx_files = sorted(_FIXTURES.glob("*.admx"))
    assert len(admx_files) == 5
    for admx_path in admx_files:
        adml_path = admx_path.with_suffix(".adml")
        assert adml_path.exists(), f"missing ADML for {admx_path.name}"
        cat = build_catalogue(admx_path.read_bytes(), adml_path.read_bytes())
        assert cat.policies, f"{admx_path.name} parsed zero policies"
        assert cat.target_namespaces, f"{admx_path.name} missing target namespace"
