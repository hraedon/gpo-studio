"""Golden tests for ADMX policy-level enabled/disabled value semantics (Plan 022 WP-1).

These pin what registry bytes a policy writes for its Enabled and Disabled
states — ``valueName``, ``<enabledValue>``/``<disabledValue>``, and
``<enabledList>``/``<disabledList>`` — the semantics the v1 catalogue parser
dropped. All fixtures are synthetic (author-authored, no Microsoft/vendor
content).
"""

from __future__ import annotations

import pytest

from gpo_studio.admx import (
    AdmxError,
    PolicyDefinition,
    PolicyValue,
    effective_disabled_value,
    effective_enabled_value,
    parse_admx,
)

# One synthetic ADMX exercising every policy-level value form.
_ADMX = b"""<?xml version="1.0" encoding="utf-8"?>
<policyDefinitions xmlns="http://www.microsoft.com/GroupPolicy/PolicyDefinitions">
  <policies>
    <policy name="DecimalToggle" class="Machine" key="Software\\Policies\\Synthetic"
            valueName="DecimalToggle" displayName="$(string.x)" explainText="$(string.x)"
            supportedOn="S">
      <enabledValue><decimal value="1" /></enabledValue>
      <disabledValue><decimal value="0" /></disabledValue>
    </policy>
    <policy name="DeleteOnDisable" class="Machine" key="Software\\Policies\\Synthetic"
            valueName="DeleteOnDisable" displayName="$(string.x)" explainText="$(string.x)"
            supportedOn="S">
      <enabledValue><decimal value="1" /></enabledValue>
      <disabledValue><delete /></disabledValue>
    </policy>
    <policy name="StringStates" class="Machine" key="Software\\Policies\\Synthetic"
            valueName="Mode" displayName="$(string.x)" explainText="$(string.x)"
            supportedOn="S">
      <enabledValue><string>On</string></enabledValue>
      <disabledValue><string value="Off" /></disabledValue>
    </policy>
    <policy name="QwordState" class="Machine" key="Software\\Policies\\Synthetic"
            valueName="Big" displayName="$(string.x)" explainText="$(string.x)"
            supportedOn="S">
      <enabledValue><longDecimal value="4294967296" /></enabledValue>
    </policy>
    <policy name="ListStates" class="Machine" key="Software\\Policies\\Synthetic"
            displayName="$(string.x)" explainText="$(string.x)" supportedOn="S">
      <enabledList defaultKey="Software\\Policies\\Synthetic\\On">
        <item valueName="A"><value><decimal value="1" /></value></item>
        <item key="Software\\Policies\\Synthetic\\Override" valueName="B">
          <value><string>yes</string></value>
        </item>
      </enabledList>
      <disabledList>
        <item valueName="A"><value><delete /></value></item>
      </disabledList>
    </policy>
    <policy name="NoExplicitValues" class="Machine" key="Software\\Policies\\Synthetic"
            valueName="Implicit" displayName="$(string.x)" explainText="$(string.x)"
            supportedOn="S" />
  </policies>
</policyDefinitions>"""


@pytest.fixture(scope="module")
def policies() -> dict[str, PolicyDefinition]:
    parsed, _categories, _supported = parse_admx(_ADMX)
    return {p.id: p for p in parsed}


def test_value_name_is_captured(policies: dict[str, PolicyDefinition]) -> None:
    assert policies["DecimalToggle"].value_name == "DecimalToggle"


def test_decimal_enabled_disabled(policies: dict[str, PolicyDefinition]) -> None:
    p = policies["DecimalToggle"]
    assert p.enabled_value == PolicyValue("decimal", "1", "REG_DWORD")
    assert p.disabled_value == PolicyValue("decimal", "0", "REG_DWORD")


def test_delete_on_disable(policies: dict[str, PolicyDefinition]) -> None:
    p = policies["DeleteOnDisable"]
    assert p.enabled_value == PolicyValue("decimal", "1", "REG_DWORD")
    assert p.disabled_value == PolicyValue("delete", "", None)


def test_string_states_text_and_attribute(policies: dict[str, PolicyDefinition]) -> None:
    p = policies["StringStates"]
    # <string>On</string> — value carried as element text
    assert p.enabled_value == PolicyValue("string", "On", "REG_SZ")
    # <string value="Off" /> — value carried as attribute
    assert p.disabled_value == PolicyValue("string", "Off", "REG_SZ")


def test_longdecimal_maps_to_qword(policies: dict[str, PolicyDefinition]) -> None:
    p = policies["QwordState"]
    assert p.enabled_value == PolicyValue("longDecimal", "4294967296", "REG_QWORD")
    assert p.disabled_value is None


def test_enabled_list_items_and_key_override(policies: dict[str, PolicyDefinition]) -> None:
    lst = policies["ListStates"].enabled_list
    assert lst is not None
    assert lst.default_key == "Software\\Policies\\Synthetic\\On"
    assert len(lst.items) == 2
    first, second = lst.items
    assert first.value_name == "A"
    assert first.key == ""  # inherits defaultKey
    assert first.value == PolicyValue("decimal", "1", "REG_DWORD")
    assert second.value_name == "B"
    assert second.key == "Software\\Policies\\Synthetic\\Override"  # item override
    assert second.value == PolicyValue("string", "yes", "REG_SZ")


def test_disabled_list_delete(policies: dict[str, PolicyDefinition]) -> None:
    lst = policies["ListStates"].disabled_list
    assert lst is not None
    assert lst.default_key == ""
    assert len(lst.items) == 1
    assert lst.items[0].value == PolicyValue("delete", "", None)


def test_no_explicit_values_are_none_not_defaulted(
    policies: dict[str, PolicyDefinition],
) -> None:
    # The parser is faithful to source bytes: absent enabled/disabled values are
    # None, not an inferred default. Default application is a consumer concern.
    p = policies["NoExplicitValues"]
    assert p.value_name == "Implicit"
    assert p.enabled_value is None
    assert p.disabled_value is None
    assert p.enabled_list is None
    assert p.disabled_list is None


# --- resolver: the single place the implicit default is applied ---------------


def test_effective_values_return_explicit_when_present(
    policies: dict[str, PolicyDefinition],
) -> None:
    p = policies["DecimalToggle"]
    assert effective_enabled_value(p) == PolicyValue("decimal", "1", "REG_DWORD")
    assert effective_disabled_value(p) == PolicyValue("decimal", "0", "REG_DWORD")


def test_effective_values_synthesize_implicit_default(
    policies: dict[str, PolicyDefinition],
) -> None:
    # valueName present, no explicit enabled/disabled: resolver applies the
    # implicit default (Enabled=REG_DWORD 1, Disabled=delete) in ONE place.
    p = policies["NoExplicitValues"]
    assert effective_enabled_value(p) == PolicyValue("decimal", "1", "REG_DWORD")
    assert effective_disabled_value(p) == PolicyValue("delete", "", None)


def test_effective_values_none_without_value_name(
    policies: dict[str, PolicyDefinition],
) -> None:
    # ListStates writes only through enabled/disabled lists (no policy valueName),
    # so there is no policy-level state value to synthesize.
    p = policies["ListStates"]
    assert p.value_name == ""
    assert effective_enabled_value(p) is None
    assert effective_disabled_value(p) is None


# --- parse-time validation of numeric values ---------------------------------


def _admx_with_enabled(value_xml: str) -> bytes:
    return (
        b"""<?xml version="1.0" encoding="utf-8"?>
<policyDefinitions xmlns="http://www.microsoft.com/GroupPolicy/PolicyDefinitions">
  <policies>
    <policy name="P" class="Machine" key="Software\\Policies\\Synthetic"
            valueName="V" displayName="$(string.x)" explainText="$(string.x)"
            supportedOn="S">
      <enabledValue>"""
        + value_xml.encode("ascii")
        + b"""</enabledValue>
    </policy>
  </policies>
</policyDefinitions>"""
    )


def test_non_integer_decimal_is_rejected() -> None:
    with pytest.raises(AdmxError, match="not an integer"):
        parse_admx(_admx_with_enabled('<decimal value="abc" />'))


def test_out_of_range_decimal_is_rejected() -> None:
    with pytest.raises(AdmxError, match="out of range"):
        parse_admx(_admx_with_enabled('<decimal value="4294967296" />'))  # 2**32


def test_out_of_range_longdecimal_is_rejected() -> None:
    with pytest.raises(AdmxError, match="out of range"):
        parse_admx(_admx_with_enabled('<longDecimal value="18446744073709551616" />'))  # 2**64


def test_max_uint_values_are_accepted() -> None:
    # Boundary values at the top of each range parse cleanly.
    parsed, _c, _s = parse_admx(_admx_with_enabled('<decimal value="4294967295" />'))
    assert parsed[0].enabled_value == PolicyValue("decimal", "4294967295", "REG_DWORD")
