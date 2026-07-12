from __future__ import annotations

import pytest

from gpo_studio.admx import EnumItem, PolicyDefinition, PolicyElement
from gpo_studio.model import ValidationError
from gpo_studio.policy_config import PolicyConfiguration, resolve_policy


def _policy(
    elements: tuple[PolicyElement, ...] = (),
    class_: str = "Machine",
    key: str = r"Software\Policies\Test",
) -> PolicyDefinition:
    return PolicyDefinition(
        id="TestPolicy",
        class_=class_,  # type: ignore[arg-type]
        key=key,
        display_name="Test Policy",
        explain_text="A test policy",
        supported_on="Supported_Test",
        elements=elements,
    )


def _element(
    kind: str = "boolean",
    id: str = "Enabled",
    key: str = "",
    value_name: str = "Enabled",
) -> PolicyElement:
    return PolicyElement(
        kind=kind,  # type: ignore[arg-type]
        id=id,
        registry_key=key,
        registry_value_name=value_name,
    )


def _enum_element(
    items: tuple[EnumItem, ...],
    id: str = "Mode",
    value_name: str = "Mode",
) -> PolicyElement:
    return PolicyElement(
        kind="enum",
        id=id,
        registry_value_name=value_name,
        enum_items=items,
    )


def test_boolean_policy_produces_dword() -> None:
    policy = _policy((_element("boolean", "Enabled"),))
    config = PolicyConfiguration(side="computer", values={"Enabled": True})
    settings = resolve_policy(policy, config)
    assert len(settings) == 1
    s = settings[0]
    assert s.registry_type == "REG_DWORD"
    assert s.value == 1
    assert s.side == "computer"
    assert s.hive == "HKLM"
    assert s.key == r"Software\Policies\Test"
    assert s.value_name == "Enabled"


def test_boolean_policy_false_produces_zero() -> None:
    policy = _policy((_element("boolean", "Enabled"),))
    config = PolicyConfiguration(side="computer", values={"Enabled": False})
    settings = resolve_policy(policy, config)
    assert settings[0].value == 0


def test_decimal_policy_produces_dword() -> None:
    policy = _policy((_element("decimal", "Threshold", value_name="Threshold"),))
    config = PolicyConfiguration(side="computer", values={"Threshold": 42})
    settings = resolve_policy(policy, config)
    assert settings[0].registry_type == "REG_DWORD"
    assert settings[0].value == 42


def test_text_policy_produces_sz() -> None:
    policy = _policy(
        (_element("text", "Label", value_name="Label"),),
        class_="User",
    )
    config = PolicyConfiguration(side="user", values={"Label": "hello"})
    settings = resolve_policy(policy, config)
    assert settings[0].registry_type == "REG_SZ"
    assert settings[0].value == "hello"
    assert settings[0].side == "user"
    assert settings[0].hive == "HKCU"


def test_multitext_policy_produces_multi_sz() -> None:
    policy = _policy((_element("multitext", "Multi", value_name="Multi"),))
    config = PolicyConfiguration(side="computer", values={"Multi": ["line1", "line2"]})
    settings = resolve_policy(policy, config)
    assert settings[0].registry_type == "REG_MULTI_SZ"
    assert settings[0].value == ["line1", "line2"]


def test_list_policy_produces_multi_sz() -> None:
    policy = _policy((_element("list", "Items", value_name="Items"),))
    config = PolicyConfiguration(side="computer", values={"Items": ["a", "b"]})
    settings = resolve_policy(policy, config)
    assert settings[0].registry_type == "REG_MULTI_SZ"
    assert settings[0].value == ["a", "b"]


def test_enum_policy_produces_sz() -> None:
    policy = _policy((_element("enum", "Mode", value_name="Mode"),))
    config = PolicyConfiguration(side="computer", values={"Mode": "Standard"})
    settings = resolve_policy(policy, config)
    assert settings[0].registry_type == "REG_SZ"
    assert settings[0].value == "Standard"


def test_unknown_element_raises_validation_error() -> None:
    policy = _policy((_element("unknown", "Weird", value_name="Weird"),))
    config = PolicyConfiguration(side="computer", values={"Weird": "x"})
    with pytest.raises(ValidationError) as exc_info:
        resolve_policy(policy, config)
    assert exc_info.value.issues[0].code == "unsupported_element_kind"


def test_missing_element_value_raises() -> None:
    policy = _policy((_element("boolean", "Enabled"),))
    config = PolicyConfiguration(side="computer", values={})
    with pytest.raises(ValidationError) as exc_info:
        resolve_policy(policy, config)
    assert exc_info.value.issues[0].code == "missing_element_value"


def test_decimal_rejects_bool() -> None:
    policy = _policy((_element("decimal", "Threshold", value_name="Threshold"),))
    config = PolicyConfiguration(side="computer", values={"Threshold": True})
    with pytest.raises(ValidationError) as exc_info:
        resolve_policy(policy, config)
    assert exc_info.value.issues[0].code == "type_mismatch"


def test_text_rejects_int() -> None:
    policy = _policy(
        (_element("text", "Label", value_name="Label"),),
        class_="Both",
    )
    config = PolicyConfiguration(side="computer", values={"Label": 123})
    with pytest.raises(ValidationError) as exc_info:
        resolve_policy(policy, config)
    assert exc_info.value.issues[0].code == "type_mismatch"


def test_side_mismatch_machine_policy() -> None:
    policy = _policy((_element("boolean", "Enabled"),), class_="Machine")
    config = PolicyConfiguration(side="user", values={"Enabled": True})
    with pytest.raises(ValidationError) as exc_info:
        resolve_policy(policy, config)
    assert exc_info.value.issues[0].code == "side_mismatch"


def test_side_mismatch_user_policy() -> None:
    policy = _policy((_element("text", "Label", value_name="Label"),), class_="User")
    config = PolicyConfiguration(side="computer", values={"Label": "x"})
    with pytest.raises(ValidationError) as exc_info:
        resolve_policy(policy, config)
    assert exc_info.value.issues[0].code == "side_mismatch"


def test_both_class_allows_either_side() -> None:
    policy = _policy((_element("boolean", "Enabled"),), class_="Both")
    config_c = PolicyConfiguration(side="computer", values={"Enabled": True})
    settings_c = resolve_policy(policy, config_c)
    assert settings_c[0].hive == "HKLM"
    config_u = PolicyConfiguration(side="user", values={"Enabled": True})
    settings_u = resolve_policy(policy, config_u)
    assert settings_u[0].hive == "HKCU"


def test_element_registry_key_overrides_policy_key() -> None:
    policy = _policy(
        (_element("boolean", "Enabled", key=r"Software\Overrides", value_name="Enabled"),),
    )
    config = PolicyConfiguration(side="computer", values={"Enabled": True})
    settings = resolve_policy(policy, config)
    assert settings[0].key == r"Software\Overrides"


def test_multiple_elements() -> None:
    policy = _policy((
        _element("boolean", "Enabled", value_name="Enabled"),
        _element("decimal", "Threshold", value_name="Threshold"),
        _element("text", "Label", value_name="Label"),
    ))
    config = PolicyConfiguration(
        side="computer",
        values={"Enabled": True, "Threshold": 100, "Label": "production"},
    )
    settings = resolve_policy(policy, config)
    assert len(settings) == 3
    by_name = {s.value_name: s for s in settings}
    assert by_name["Enabled"].value == 1
    assert by_name["Threshold"].value == 100
    assert by_name["Label"].value == "production"


def test_setting_id_is_deterministic() -> None:
    policy = _policy((_element("boolean", "Enabled", value_name="Enabled"),))
    config = PolicyConfiguration(side="computer", values={"Enabled": True})
    settings = resolve_policy(policy, config)
    assert settings[0].id == "admx-TestPolicy-Enabled-computer"


def test_empty_elements_returns_empty_list() -> None:
    policy = _policy(())
    config = PolicyConfiguration(side="computer", values={})
    assert resolve_policy(policy, config) == []


def test_decimal_rejects_negative() -> None:
    policy = _policy((_element("decimal", "Threshold", value_name="Threshold"),))
    config = PolicyConfiguration(side="computer", values={"Threshold": -1})
    with pytest.raises(ValidationError) as exc_info:
        resolve_policy(policy, config)
    assert exc_info.value.issues[0].code == "value_range"


def test_decimal_rejects_overflow() -> None:
    policy = _policy((_element("decimal", "Threshold", value_name="Threshold"),))
    config = PolicyConfiguration(side="computer", values={"Threshold": 2**32})
    with pytest.raises(ValidationError) as exc_info:
        resolve_policy(policy, config)
    assert exc_info.value.issues[0].code == "value_range"


def test_both_class_ids_include_side() -> None:
    policy = _policy((_element("boolean", "Enabled", value_name="Enabled"),), class_="Both")
    config_c = PolicyConfiguration(side="computer", values={"Enabled": True})
    config_u = PolicyConfiguration(side="user", values={"Enabled": True})
    settings_c = resolve_policy(policy, config_c)
    settings_u = resolve_policy(policy, config_u)
    assert settings_c[0].id != settings_u[0].id
    assert settings_c[0].id == "admx-TestPolicy-Enabled-computer"
    assert settings_u[0].id == "admx-TestPolicy-Enabled-user"


def test_enum_decimal_items_produce_dword() -> None:
    items = (
        EnumItem(id="standard", display_name="Standard", value=0, registry_type="REG_DWORD"),
        EnumItem(id="advanced", display_name="Advanced", value=1, registry_type="REG_DWORD"),
    )
    policy = _policy((_enum_element(items),))
    config = PolicyConfiguration(side="computer", values={"Mode": "advanced"})
    settings = resolve_policy(policy, config)
    assert settings[0].registry_type == "REG_DWORD"
    assert settings[0].value == 1


def test_enum_string_items_produce_sz() -> None:
    items = (
        EnumItem(id="plain", display_name="Plain", value="plaintext", registry_type="REG_SZ"),
        EnumItem(id="ssl", display_name="SSL", value="tls", registry_type="REG_SZ"),
    )
    policy = _policy((_enum_element(items),))
    config = PolicyConfiguration(side="computer", values={"Mode": "ssl"})
    settings = resolve_policy(policy, config)
    assert settings[0].registry_type == "REG_SZ"
    assert settings[0].value == "tls"


def test_enum_mixed_items_produce_correct_types() -> None:
    items = (
        EnumItem(id="off", display_name="Off", value=0, registry_type="REG_DWORD"),
        EnumItem(id="custom", display_name="Custom", value="custom-path", registry_type="REG_SZ"),
    )
    policy = _policy((_enum_element(items),))
    config_dword = PolicyConfiguration(side="computer", values={"Mode": "off"})
    settings_dword = resolve_policy(policy, config_dword)
    assert settings_dword[0].registry_type == "REG_DWORD"
    assert settings_dword[0].value == 0
    config_sz = PolicyConfiguration(side="computer", values={"Mode": "custom"})
    settings_sz = resolve_policy(policy, config_sz)
    assert settings_sz[0].registry_type == "REG_SZ"
    assert settings_sz[0].value == "custom-path"


def test_enum_long_decimal_items_produce_qword() -> None:
    items = (
        EnumItem(
            id="huge",
            display_name="Huge",
            value=5000000000,
            registry_type="REG_QWORD",
        ),
    )
    policy = _policy((_enum_element(items),))
    config = PolicyConfiguration(side="computer", values={"Mode": "huge"})
    settings = resolve_policy(policy, config)
    assert settings[0].registry_type == "REG_QWORD"
    assert settings[0].value == 5000000000


def test_enum_unknown_value_raises() -> None:
    items = (
        EnumItem(id="standard", display_name="Standard", value=0, registry_type="REG_DWORD"),
    )
    policy = _policy((_enum_element(items),))
    config = PolicyConfiguration(side="computer", values={"Mode": "bogus"})
    with pytest.raises(ValidationError) as exc_info:
        resolve_policy(policy, config)
    assert exc_info.value.issues[0].code == "invalid_enum_value"
    assert "bogus" in exc_info.value.issues[0].message
    assert "Mode" in exc_info.value.issues[0].message


def test_enum_empty_items_falls_back_to_sz() -> None:
    policy = _policy((_enum_element(()),))
    config = PolicyConfiguration(side="computer", values={"Mode": "Standard"})
    settings = resolve_policy(policy, config)
    assert settings[0].registry_type == "REG_SZ"
    assert settings[0].value == "Standard"
