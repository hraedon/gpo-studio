from __future__ import annotations

import pytest

from gpo_studio.admx import EnumItem, PolicyDefinition, PolicyElement
from gpo_studio.model import ValidationError
from gpo_studio.numeric import coerce_dword_qword, is_canonical_decimal
from gpo_studio.policy_config import PolicyConfiguration, resolve_policy


def test_is_canonical_decimal_accepts_zero() -> None:
    assert is_canonical_decimal("0")


def test_is_canonical_decimal_accepts_single_digit() -> None:
    assert is_canonical_decimal("1")


def test_is_canonical_decimal_accepts_dword_max() -> None:
    assert is_canonical_decimal("4294967295")


def test_is_canonical_decimal_accepts_qword_max() -> None:
    assert is_canonical_decimal("18446744073709551615")


def test_is_canonical_decimal_rejects_signed_negative() -> None:
    assert not is_canonical_decimal("-1")


def test_is_canonical_decimal_rejects_plus_sign() -> None:
    assert not is_canonical_decimal("+1")


def test_is_canonical_decimal_rejects_fractional() -> None:
    assert not is_canonical_decimal("1.0")


def test_is_canonical_decimal_rejects_exponent() -> None:
    assert not is_canonical_decimal("1e3")


def test_is_canonical_decimal_rejects_leading_whitespace() -> None:
    assert not is_canonical_decimal(" 1")


def test_is_canonical_decimal_rejects_hex() -> None:
    assert not is_canonical_decimal("0x10")


def test_is_canonical_decimal_rejects_double_zero() -> None:
    assert not is_canonical_decimal("00")


def test_is_canonical_decimal_rejects_leading_zero() -> None:
    assert not is_canonical_decimal("01")


def test_is_canonical_decimal_rejects_empty() -> None:
    assert not is_canonical_decimal("")


def test_is_canonical_decimal_rejects_whitespace_only() -> None:
    assert not is_canonical_decimal("  ")


def test_coerce_dword_string_basic() -> None:
    assert coerce_dword_qword("42", "REG_DWORD") == 42


def test_coerce_dword_string_zero() -> None:
    assert coerce_dword_qword("0", "REG_DWORD") == 0


def test_coerce_dword_string_max() -> None:
    assert coerce_dword_qword("4294967295", "REG_DWORD") == 4294967295


def test_coerce_dword_int_basic() -> None:
    assert coerce_dword_qword(42, "REG_DWORD") == 42


def test_coerce_dword_int_zero() -> None:
    assert coerce_dword_qword(0, "REG_DWORD") == 0


def test_coerce_dword_int_max() -> None:
    assert coerce_dword_qword(4294967295, "REG_DWORD") == 4294967295


def test_coerce_dword_string_overflow() -> None:
    with pytest.raises(ValueError):
        coerce_dword_qword("4294967296", "REG_DWORD")


def test_coerce_dword_int_overflow() -> None:
    with pytest.raises(ValueError):
        coerce_dword_qword(4294967296, "REG_DWORD")


def test_coerce_dword_rejects_signed() -> None:
    with pytest.raises(ValueError):
        coerce_dword_qword("-1", "REG_DWORD")


def test_coerce_dword_rejects_plus_sign() -> None:
    with pytest.raises(ValueError):
        coerce_dword_qword("+1", "REG_DWORD")


def test_coerce_dword_rejects_fractional() -> None:
    with pytest.raises(ValueError):
        coerce_dword_qword("1.0", "REG_DWORD")


def test_coerce_dword_rejects_exponent() -> None:
    with pytest.raises(ValueError):
        coerce_dword_qword("1e3", "REG_DWORD")


def test_coerce_dword_rejects_whitespace() -> None:
    with pytest.raises(ValueError):
        coerce_dword_qword(" 1", "REG_DWORD")


def test_coerce_dword_rejects_hex() -> None:
    with pytest.raises(ValueError):
        coerce_dword_qword("0x10", "REG_DWORD")


def test_coerce_dword_rejects_leading_zeros() -> None:
    with pytest.raises(ValueError):
        coerce_dword_qword("00", "REG_DWORD")


def test_coerce_dword_rejects_leading_zero() -> None:
    with pytest.raises(ValueError):
        coerce_dword_qword("01", "REG_DWORD")


def test_coerce_dword_rejects_empty() -> None:
    with pytest.raises(ValueError):
        coerce_dword_qword("", "REG_DWORD")


def test_coerce_dword_rejects_whitespace_only() -> None:
    with pytest.raises(ValueError):
        coerce_dword_qword("  ", "REG_DWORD")


def test_coerce_dword_rejects_bool() -> None:
    with pytest.raises(ValueError):
        coerce_dword_qword(True, "REG_DWORD")


def test_coerce_qword_string_max() -> None:
    assert coerce_dword_qword("18446744073709551615", "REG_QWORD") == 18446744073709551615


def test_coerce_qword_int_max() -> None:
    assert coerce_dword_qword(18446744073709551615, "REG_QWORD") == 18446744073709551615


def test_coerce_qword_string_precision_boundary() -> None:
    assert coerce_dword_qword("9007199254740993", "REG_QWORD") == 9007199254740993


def test_coerce_qword_int_precision_boundary() -> None:
    assert coerce_dword_qword(9007199254740993, "REG_QWORD") == 9007199254740993


def test_coerce_qword_string_overflow() -> None:
    with pytest.raises(ValueError):
        coerce_dword_qword("18446744073709551616", "REG_QWORD")


def test_coerce_qword_int_overflow() -> None:
    with pytest.raises(ValueError):
        coerce_dword_qword(18446744073709551616, "REG_QWORD")


def test_coerce_rejects_unknown_registry_type() -> None:
    with pytest.raises(ValueError):
        coerce_dword_qword("42", "REG_SZ")


def _decimal_policy() -> PolicyDefinition:
    return PolicyDefinition(
        id="NumericPolicy",
        class_="Machine",
        key=r"Software\Policies\Numeric",
        display_name="Numeric",
        explain_text="",
        supported_on="",
        elements=(
            PolicyElement(
                kind="decimal",
                id="Threshold",
                registry_value_name="Threshold",
            ),
        ),
    )


def test_admx_decimal_string_value() -> None:
    config = PolicyConfiguration(side="computer", values={"Threshold": "42"})
    settings = resolve_policy(_decimal_policy(), config)
    assert settings[0].value == 42
    assert settings[0].registry_type == "REG_DWORD"


def test_admx_decimal_string_max_dword() -> None:
    config = PolicyConfiguration(side="computer", values={"Threshold": "4294967295"})
    settings = resolve_policy(_decimal_policy(), config)
    assert settings[0].value == 4294967295


def test_admx_decimal_string_overflow_rejected() -> None:
    config = PolicyConfiguration(side="computer", values={"Threshold": "4294967296"})
    with pytest.raises(ValidationError) as exc_info:
        resolve_policy(_decimal_policy(), config)
    assert exc_info.value.issues[0].code == "invalid_numeric_value"


def test_admx_decimal_string_signed_rejected() -> None:
    config = PolicyConfiguration(side="computer", values={"Threshold": "-1"})
    with pytest.raises(ValidationError):
        resolve_policy(_decimal_policy(), config)


def test_admx_decimal_int_still_range_checked() -> None:
    config = PolicyConfiguration(side="computer", values={"Threshold": 2**32})
    with pytest.raises(ValidationError) as exc_info:
        resolve_policy(_decimal_policy(), config)
    assert exc_info.value.issues[0].code == "value_range"


def _enum_policy(items: tuple[EnumItem, ...]) -> PolicyDefinition:
    return PolicyDefinition(
        id="EnumPolicy",
        class_="Machine",
        key=r"Software\Policies\Enum",
        display_name="Enum",
        explain_text="",
        supported_on="",
        elements=(
            PolicyElement(
                kind="enum",
                id="Mode",
                registry_value_name="Mode",
                enum_items=items,
            ),
        ),
    )


def test_admx_enum_qword_max() -> None:
    items = (
        EnumItem(
            id="max",
            display_name="Max",
            value=18446744073709551615,
            registry_type="REG_QWORD",
        ),
    )
    config = PolicyConfiguration(side="computer", values={"Mode": "max"})
    settings = resolve_policy(_enum_policy(items), config)
    assert settings[0].registry_type == "REG_QWORD"
    assert settings[0].value == 18446744073709551615


def test_admx_enum_qword_overflow_rejected() -> None:
    items = (
        EnumItem(
            id="overflow",
            display_name="Overflow",
            value=18446744073709551616,
            registry_type="REG_QWORD",
        ),
    )
    config = PolicyConfiguration(side="computer", values={"Mode": "overflow"})
    with pytest.raises(ValidationError) as exc_info:
        resolve_policy(_enum_policy(items), config)
    assert exc_info.value.issues[0].code == "value_range"


def test_admx_enum_dword_overflow_rejected() -> None:
    items = (
        EnumItem(
            id="overflow",
            display_name="Overflow",
            value=2**32,
            registry_type="REG_DWORD",
        ),
    )
    config = PolicyConfiguration(side="computer", values={"Mode": "overflow"})
    with pytest.raises(ValidationError) as exc_info:
        resolve_policy(_enum_policy(items), config)
    assert exc_info.value.issues[0].code == "value_range"
