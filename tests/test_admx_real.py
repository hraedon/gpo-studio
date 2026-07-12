from __future__ import annotations

from pathlib import Path

from gpo_studio.admx import (
    AdmxCatalogue,
    PolicyDefinition,
    build_catalogue,
    load_catalogue,
)

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "admx"


def _load(name: str) -> AdmxCatalogue:
    admx = (_FIXTURE_DIR / f"{name}.admx").read_bytes()
    adml = (_FIXTURE_DIR / f"{name}.adml").read_bytes()
    return build_catalogue(admx, adml)


def _get_policy(cat: AdmxCatalogue, name: str) -> PolicyDefinition:
    return [p for p in cat.policies if p.id == name][0]


def test_parse_windowssettings_admx() -> None:
    cat = _load("windowssettings")
    assert len(cat.policies) == 2
    assert len(cat.categories) == 2
    assert len(cat.supported_on) == 2


def test_parse_securitypolicies_admx() -> None:
    cat = _load("securitypolicies")
    assert len(cat.policies) == 3
    assert len(cat.categories) == 1
    assert len(cat.supported_on) == 1


def test_parse_networksettings_admx() -> None:
    cat = _load("networksettings")
    assert len(cat.policies) == 2
    assert len(cat.categories) == 2
    assert len(cat.supported_on) == 1


def test_enum_items_decimal_values_are_int() -> None:
    cat = _load("securitypolicies")
    policy = _get_policy(cat, "SyntheticPasswordPolicy")
    enum_elem = [e for e in policy.elements if e.id == "PasswordMode"][0]
    decimal_items = [i for i in enum_elem.enum_items if i.registry_type == "REG_DWORD"]
    assert len(decimal_items) == 3
    assert all(isinstance(i.value, int) for i in decimal_items)
    assert decimal_items[0].value == 0
    assert decimal_items[1].value == 1
    assert decimal_items[2].value == 2


def test_enum_items_string_values_are_str() -> None:
    cat = _load("securitypolicies")
    policy = _get_policy(cat, "SyntheticPasswordPolicy")
    enum_elem = [e for e in policy.elements if e.id == "PasswordMode"][0]
    string_items = [i for i in enum_elem.enum_items if i.registry_type == "REG_SZ"]
    assert len(string_items) == 1
    assert isinstance(string_items[0].value, str)
    assert string_items[0].value == "custom"


def test_enum_items_display_names_resolved() -> None:
    cat = _load("securitypolicies")
    policy = _get_policy(cat, "SyntheticPasswordPolicy")
    enum_elem = [e for e in policy.elements if e.id == "PasswordMode"][0]
    names = [i.display_name for i in enum_elem.enum_items]
    assert names == [
        "Basic Password Mode",
        "Standard Password Mode",
        "Strict Password Mode",
        "Custom Password Mode",
    ]


def test_enum_items_count_and_registry_types() -> None:
    cat = _load("securitypolicies")
    policy = _get_policy(cat, "SyntheticPasswordPolicy")
    enum_elem = [e for e in policy.elements if e.id == "PasswordMode"][0]
    assert len(enum_elem.enum_items) == 4
    reg_types = {i.registry_type for i in enum_elem.enum_items}
    assert reg_types == {"REG_DWORD", "REG_SZ"}


def test_display_names_resolved_from_adml() -> None:
    cat = _load("windowssettings")
    audit = _get_policy(cat, "SyntheticAuditPolicy")
    assert audit.display_name == "Synthetic Audit Policy"
    assert not audit.display_name.startswith("$(")
    threshold = _get_policy(cat, "SyntheticThresholdPolicy")
    assert threshold.display_name == "Synthetic Threshold Policy"
    assert not threshold.display_name.startswith("$(")


def test_explain_text_resolved_from_adml() -> None:
    cat = _load("windowssettings")
    audit = _get_policy(cat, "SyntheticAuditPolicy")
    assert audit.explain_text == "Enables synthetic auditing for the synthetic subsystem."
    assert not audit.explain_text.startswith("$(")


def test_display_names_resolved_all_fixtures() -> None:
    for name in ("windowssettings", "securitypolicies", "networksettings"):
        cat = _load(name)
        for policy in cat.policies:
            assert not policy.display_name.startswith("$(")
            assert not policy.explain_text.startswith("$(")


def test_policy_classes_windowssettings() -> None:
    cat = _load("windowssettings")
    assert _get_policy(cat, "SyntheticAuditPolicy").class_ == "Both"
    assert _get_policy(cat, "SyntheticThresholdPolicy").class_ == "Machine"


def test_policy_classes_securitypolicies() -> None:
    cat = _load("securitypolicies")
    assert _get_policy(cat, "SyntheticPasswordPolicy").class_ == "Machine"
    assert _get_policy(cat, "SyntheticUserTextPolicy").class_ == "User"
    assert _get_policy(cat, "SyntheticBlockListPolicy").class_ == "Both"


def test_policy_classes_networksettings() -> None:
    cat = _load("networksettings")
    assert _get_policy(cat, "SyntheticQwordPolicy").class_ == "Machine"
    assert _get_policy(cat, "SyntheticMultiElementPolicy").class_ == "Both"


def test_categories_parent_child_hierarchy_windowssettings() -> None:
    cat = _load("windowssettings")
    assert len(cat.categories) == 2
    parent = [c for c in cat.categories if c.id == "SyntheticWindowsSettings"][0]
    child = [c for c in cat.categories if c.id == "SyntheticWindowsSettingsChild"][0]
    assert parent.parent_id == "SyntheticTopLevel"
    assert child.parent_id == "SyntheticWindowsSettings"
    assert parent.display_name == "Synthetic Windows Settings"
    assert child.display_name == "Synthetic Windows Settings (Advanced)"


def test_categories_parent_child_hierarchy_networksettings() -> None:
    cat = _load("networksettings")
    assert len(cat.categories) == 2
    parent = [c for c in cat.categories if c.id == "SyntheticNetwork"][0]
    child = [c for c in cat.categories if c.id == "SyntheticNetworkAdvanced"][0]
    assert parent.parent_id == "SyntheticTopLevel"
    assert child.parent_id == "SyntheticNetwork"


def test_supported_on_definitions_parsed() -> None:
    cat = _load("windowssettings")
    assert len(cat.supported_on) == 2
    def0 = cat.supported_on[0]
    def1 = cat.supported_on[1]
    assert def0.name == "Supported_SyntheticOS_v1"
    assert def0.display_name == "Synthetic OS Version 1"
    assert def1.name == "Supported_SyntheticOS_v2"
    assert def1.display_name == "Synthetic OS Version 2"


def test_supported_on_definitions_resolved_display_names() -> None:
    cat = _load("securitypolicies")
    assert len(cat.supported_on) == 1
    assert cat.supported_on[0].name == "Supported_SyntheticSecure_v1"
    assert cat.supported_on[0].display_name == "Synthetic Secure OS v1"
    assert not cat.supported_on[0].display_name.startswith("$(")


def test_load_catalogue_loads_all_files() -> None:
    cat = load_catalogue(_FIXTURE_DIR)
    assert len(cat.policies) == 7
    assert len(cat.categories) == 5
    assert len(cat.supported_on) == 4
    policy_ids = {p.id for p in cat.policies}
    expected = {
        "SyntheticAuditPolicy",
        "SyntheticThresholdPolicy",
        "SyntheticPasswordPolicy",
        "SyntheticUserTextPolicy",
        "SyntheticBlockListPolicy",
        "SyntheticQwordPolicy",
        "SyntheticMultiElementPolicy",
    }
    assert policy_ids == expected


def test_long_decimal_enum_produces_reg_qword() -> None:
    cat = _load("networksettings")
    policy = _get_policy(cat, "SyntheticQwordPolicy")
    enum_elem = [e for e in policy.elements if e.id == "QwordMode"][0]
    assert len(enum_elem.enum_items) == 2
    assert all(i.registry_type == "REG_QWORD" for i in enum_elem.enum_items)
    assert all(isinstance(i.value, int) for i in enum_elem.enum_items)
    assert enum_elem.enum_items[0].value == 0
    assert enum_elem.enum_items[1].value == 4294967296


def test_long_decimal_enum_display_names_resolved() -> None:
    cat = _load("networksettings")
    policy = _get_policy(cat, "SyntheticQwordPolicy")
    enum_elem = [e for e in policy.elements if e.id == "QwordMode"][0]
    names = [i.display_name for i in enum_elem.enum_items]
    assert names == ["Standard Throttle", "Large Throttle"]


def test_multiple_elements_in_single_policy() -> None:
    cat = _load("networksettings")
    policy = _get_policy(cat, "SyntheticMultiElementPolicy")
    assert len(policy.elements) == 3
    kinds = {e.kind for e in policy.elements}
    assert kinds == {"boolean", "decimal", "text"}
    bool_elem = [e for e in policy.elements if e.kind == "boolean"][0]
    assert bool_elem.id == "NetworkEnabled"
    assert bool_elem.registry_value_name == "Enabled"
    dec_elem = [e for e in policy.elements if e.kind == "decimal"][0]
    assert dec_elem.id == "NetworkTimeout"
    assert dec_elem.registry_value_name == "Timeout"
    text_elem = [e for e in policy.elements if e.kind == "text"][0]
    assert text_elem.id == "NetworkGateway"
    assert text_elem.registry_value_name == "Gateway"


def test_multiple_elements_blocklist_policy() -> None:
    cat = _load("securitypolicies")
    policy = _get_policy(cat, "SyntheticBlockListPolicy")
    assert len(policy.elements) == 2
    kinds = {e.kind for e in policy.elements}
    assert kinds == {"list", "multitext"}


def test_presentation_elements_windowssettings() -> None:
    cat = _load("windowssettings")
    audit = _get_policy(cat, "SyntheticAuditPolicy")
    assert len(audit.presentation) == 1
    checkbox = audit.presentation[0]
    assert checkbox.kind == "checkbox"
    assert checkbox.id == "AuditEnabled"
    assert checkbox.ref_id == "AuditEnabled"
    assert checkbox.label == "Enable Synthetic Auditing"
    threshold = _get_policy(cat, "SyntheticThresholdPolicy")
    assert len(threshold.presentation) == 1
    dec = threshold.presentation[0]
    assert dec.kind == "decimal"
    assert dec.label == "Threshold Value"


def test_presentation_elements_securitypolicies() -> None:
    cat = _load("securitypolicies")
    password = _get_policy(cat, "SyntheticPasswordPolicy")
    assert len(password.presentation) == 1
    dropdown = password.presentation[0]
    assert dropdown.kind == "dropdownlist"
    assert dropdown.id == "PasswordMode"
    assert dropdown.label == "Password Complexity Mode"
    blocklist = _get_policy(cat, "SyntheticBlockListPolicy")
    assert len(blocklist.presentation) == 2
    pres_kinds = {p.kind for p in blocklist.presentation}
    assert pres_kinds == {"list", "multitext"}


def test_presentation_elements_networksettings() -> None:
    cat = _load("networksettings")
    multi = _get_policy(cat, "SyntheticMultiElementPolicy")
    assert len(multi.presentation) == 4
    kinds = {p.kind for p in multi.presentation}
    assert kinds == {"checkbox", "decimal", "text", "dropdownlist"}


def test_parent_category_set_on_policies() -> None:
    cat = _load("windowssettings")
    audit = _get_policy(cat, "SyntheticAuditPolicy")
    assert audit.parent_category == "SyntheticWindowsSettingsChild"
    threshold = _get_policy(cat, "SyntheticThresholdPolicy")
    assert threshold.parent_category == "SyntheticWindowsSettings"


def test_policy_supported_on_reference() -> None:
    cat = _load("windowssettings")
    audit = _get_policy(cat, "SyntheticAuditPolicy")
    assert audit.supported_on == "Supported_SyntheticOS_v2"
    threshold = _get_policy(cat, "SyntheticThresholdPolicy")
    assert threshold.supported_on == "Supported_SyntheticOS_v1"


def test_using_directive_does_not_break_parsing() -> None:
    cat = _load("networksettings")
    assert len(cat.policies) == 2
    assert len(cat.categories) == 2
    assert len(cat.supported_on) == 1


def test_registry_key_and_value_name_correct() -> None:
    cat = _load("windowssettings")
    audit = _get_policy(cat, "SyntheticAuditPolicy")
    bool_elem = audit.elements[0]
    assert bool_elem.registry_key == r"Software\Policies\Synthetic\Audit"
    assert bool_elem.registry_value_name == "Enabled"


def test_policy_key_attribute_correct() -> None:
    cat = _load("networksettings")
    qword = _get_policy(cat, "SyntheticQwordPolicy")
    assert qword.key == r"Software\Policies\Synthetic\Network\Qword"
    multi = _get_policy(cat, "SyntheticMultiElementPolicy")
    assert multi.key == r"Software\Policies\Synthetic\Network\Multi"


def test_decimal_element_with_min_max_constraints_parsed() -> None:
    cat = _load("windowssettings")
    threshold = _get_policy(cat, "SyntheticThresholdPolicy")
    dec_elem = [e for e in threshold.elements if e.kind == "decimal"][0]
    assert dec_elem.id == "Threshold"
    assert dec_elem.registry_value_name == "Threshold"
    assert dec_elem.registry_key == r"Software\Policies\Synthetic\Threshold"


def test_user_text_policy_element() -> None:
    cat = _load("securitypolicies")
    user_policy = _get_policy(cat, "SyntheticUserTextPolicy")
    assert len(user_policy.elements) == 1
    text_elem = user_policy.elements[0]
    assert text_elem.kind == "text"
    assert text_elem.id == "UserBanner"
    assert text_elem.registry_value_name == "Banner"
