from __future__ import annotations

import pytest

from gpo_studio.admx import (
    AdmxError,
    EnumItem,
    build_catalogue,
    load_catalogue,
    parse_adml,
    parse_admx,
)

_ADMX_MINIMAL = b"""<?xml version="1.0" encoding="utf-8"?>
<policyDefinitions xmlns="http://www.microsoft.com/GroupPolicy/PolicyDefinitions">
  <categories>
    <category name="SyntheticCategory" displayName="$(string.SyntheticCategory)">
      <parentCategory ref="ParentCat" />
    </category>
  </categories>
  <supportedOn>
    <definition name="Supported_Synthetic" displayName="$(string.Supported_Synthetic)" />
  </supportedOn>
  <policies>
    <policy name="SyntheticPolicy" class="Machine" key="Software\\Policies\\Synthetic"
            displayName="$(string.SyntheticPolicy)" explainText="$(string.SyntheticPolicy_Explain)"
            supportedOn="Supported_Synthetic" presentation="$(presentation.SyntheticPolicy)">
      <parentCategory ref="SyntheticCategory" />
      <supportedOn ref="Supported_Synthetic" />
      <elements>
        <boolean id="Enabled" key="Software\\Policies\\Synthetic" valueName="Enabled" />
        <decimal id="Threshold" key="Software\\Policies\\Synthetic" valueName="Threshold" />
        <text id="Label" key="Software\\Policies\\Synthetic" valueName="Label" />
        <enum id="Mode" key="Software\\Policies\\Synthetic" valueName="Mode" />
        <list id="AllowedList" key="Software\\Policies\\Synthetic" valueName="AllowedList" />
        <multitext id="MultiLine" key="Software\\Policies\\Synthetic" valueName="MultiLine" />
      </elements>
      <presentation>
        <checkBox id="Enabled" refId="Enabled" label="$(string.EnableLabel)" />
        <decimalTextBox id="Threshold" refId="Threshold" label="$(string.ThresholdLabel)" />
        <textBox id="Label" refId="Label" label="$(string.LabelLabel)" />
        <dropdownList id="Mode" refId="Mode" label="$(string.ModeLabel)" />
        <listBox id="AllowedList" refId="AllowedList" label="$(string.ListLabel)" />
        <multiTextBox id="MultiLine" refId="MultiLine" label="$(string.MultiLabel)" />
      </presentation>
    </policy>
    <policy name="UserPolicy" class="User" key="Software\\Policies\\UserSynthetic"
            displayName="$(string.UserPolicy)" explainText="$(string.UserPolicy_Explain)"
            supportedOn="Supported_Synthetic" presentation="$(presentation.UserPolicy)">
      <parentCategory ref="SyntheticCategory" />
      <supportedOn ref="Supported_Synthetic" />
      <elements>
        <text id="UserSetting" key="Software\\Policies\\UserSynthetic" valueName="UserSetting" />
      </elements>
      <presentation>
        <textBox id="UserSetting" refId="UserSetting" label="$(string.UserSettingLabel)" />
      </presentation>
    </policy>
  </policies>
</policyDefinitions>"""

_ADML_MINIMAL = b"""<?xml version="1.0" encoding="utf-8"?>
<policyDefinitionResources xmlns="http://www.microsoft.com/GroupPolicy/PolicyDefinitions">
  <resources>
    <stringTable>
      <string id="SyntheticCategory">Synthetic Category</string>
      <string id="Supported_Synthetic">Synthetic OS Support</string>
      <string id="SyntheticPolicy">Synthetic Policy</string>
      <string id="SyntheticPolicy_Explain">This is a synthetic policy for testing.</string>
      <string id="UserPolicy">User Policy</string>
      <string id="UserPolicy_Explain">User-side synthetic policy.</string>
      <string id="EnableLabel">Enable</string>
      <string id="ThresholdLabel">Threshold</string>
      <string id="LabelLabel">Label</string>
      <string id="ModeLabel">Mode</string>
      <string id="ListLabel">Allowed List</string>
      <string id="MultiLabel">Multi Line</string>
      <string id="UserSettingLabel">User Setting</string>
    </stringTable>
  </resources>
</policyDefinitionResources>"""


def test_parse_simple_admx() -> None:
    policies, categories, supported_on = parse_admx(_ADMX_MINIMAL)
    assert len(policies) == 2
    assert policies[0].id == "SyntheticPolicy"
    assert policies[0].class_ == "Machine"
    assert policies[0].key == r"Software\Policies\Synthetic"
    assert policies[1].id == "UserPolicy"
    assert policies[1].class_ == "User"


def test_parse_adml_string_table() -> None:
    strings = parse_adml(_ADML_MINIMAL)
    assert strings["SyntheticPolicy"] == "Synthetic Policy"
    assert strings["SyntheticPolicy_Explain"] == "This is a synthetic policy for testing."


def test_build_catalogue_resolves_display_names() -> None:
    cat = build_catalogue(_ADMX_MINIMAL, _ADML_MINIMAL)
    assert len(cat.policies) == 2
    assert cat.policies[0].display_name == "Synthetic Policy"
    assert cat.policies[0].explain_text == "This is a synthetic policy for testing."
    assert cat.policies[1].display_name == "User Policy"


def test_parse_all_presentation_element_types() -> None:
    cat = build_catalogue(_ADMX_MINIMAL, _ADML_MINIMAL)
    pres = cat.policies[0].presentation
    kinds = {p.kind for p in pres}
    assert kinds == {"checkbox", "decimal", "text", "dropdownlist", "list", "multitext"}


def test_parse_all_policy_element_types() -> None:
    cat = build_catalogue(_ADMX_MINIMAL, _ADML_MINIMAL)
    elements = cat.policies[0].elements
    kinds = {e.kind for e in elements}
    assert kinds == {"boolean", "decimal", "text", "enum", "list", "multitext"}


def test_unknown_element_preserved() -> None:
    admx_with_unknown = _ADMX_MINIMAL.replace(
        b"</elements>",
        b'<customElement id="Unknown" key="Software\\Policies\\Synthetic" /></elements>',
    )
    policies, _, _ = parse_admx(admx_with_unknown)
    assert len(policies[0].elements) == 7
    unknown = [e for e in policies[0].elements if e.id == "customElement"][0]
    assert unknown.kind == "unknown"
    assert unknown.tag_name == "customElement"
    assert unknown.attributes == (("id", "Unknown"), ("key", "Software\\Policies\\Synthetic"))


def test_malformed_xml_raises_admx_error() -> None:
    with pytest.raises(AdmxError, match="Malformed XML"):
        parse_admx(b"<not valid xml")
    with pytest.raises(AdmxError, match="Malformed XML"):
        parse_adml(b"<not valid xml")


def test_machine_and_user_classes() -> None:
    policies, _, _ = parse_admx(_ADMX_MINIMAL)
    assert policies[0].class_ == "Machine"
    assert policies[1].class_ == "User"


def test_categories_with_parent() -> None:
    cat = build_catalogue(_ADMX_MINIMAL, _ADML_MINIMAL)
    assert len(cat.categories) == 1
    assert cat.categories[0].id == "SyntheticCategory"
    assert cat.categories[0].parent_id == "ParentCat"
    assert cat.categories[0].display_name == "Synthetic Category"


def test_supported_on_definitions() -> None:
    cat = build_catalogue(_ADMX_MINIMAL, _ADML_MINIMAL)
    assert len(cat.supported_on) == 1
    assert cat.supported_on[0].name == "Supported_Synthetic"
    assert cat.supported_on[0].display_name == "Synthetic OS Support"


def test_invalid_class_raises_admx_error() -> None:
    admx_bad_class = _ADMX_MINIMAL.replace(b'class="User"', b'class="Invalid"', 1)
    with pytest.raises(AdmxError, match="Unsupported policy class"):
        parse_admx(admx_bad_class)


def test_entity_declaration_rejected() -> None:
    with pytest.raises(AdmxError, match="entity"):
        parse_admx(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><policyDefinitions xmlns="http://www.microsoft.com/GroupPolicy/PolicyDefinitions"/>')


def test_admx_element_count_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gpo_studio.admx._MAX_ELEMENT_COUNT", 2)

    with pytest.raises(AdmxError, match="XML element count exceeds 2"):
        parse_admx(b"<policyDefinitions><categories><category/></categories></policyDefinitions>")


def test_admx_attribute_length_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gpo_studio.admx._MAX_ATTR_LENGTH", 6)

    with pytest.raises(AdmxError, match="XML attribute length exceeds 6"):
        parse_admx(b'<policyDefinitions version="1234567"/>')


def test_adml_text_length_is_bounded_across_comments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("gpo_studio.admx._MAX_TEXT_LENGTH", 6)

    with pytest.raises(AdmxError, match="XML text length exceeds 6"):
        parse_adml(b"<resources>12345<!-- split -->67890</resources>")


def test_load_catalogue_from_directory(tmp_path) -> None:
    (tmp_path / "test.admx").write_bytes(_ADMX_MINIMAL)
    (tmp_path / "test.adml").write_bytes(_ADML_MINIMAL)
    cat = load_catalogue(tmp_path)
    assert len(cat.policies) == 2
    assert cat.policies[0].display_name == "Synthetic Policy"
    assert len(cat.categories) == 1
    assert len(cat.supported_on) == 1


# A minimal real-namespace ADMX + locale-varying ADML, for locale-selection tests.
_ADMX_ONE = b"""<?xml version="1.0" encoding="utf-8"?>
<policyDefinitions xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions">
  <policies>
    <policy name="P" class="Machine" key="Software\\Policies\\Synthetic" valueName="V"
            displayName="$(string.P)" explainText="$(string.P)" supportedOn="S" />
  </policies>
</policyDefinitions>"""


def _adml_one(display: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<policyDefinitionResources '
        'xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions">'
        f"<resources><stringTable><string id=\"P\">{display}</string>"
        "</stringTable></resources></policyDefinitionResources>"
    ).encode()


def test_load_catalogue_prefers_en_us_locale(tmp_path) -> None:
    (tmp_path / "x.admx").write_bytes(_ADMX_ONE)
    (tmp_path / "en-US").mkdir()
    (tmp_path / "de-DE").mkdir()
    (tmp_path / "en-US" / "x.adml").write_bytes(_adml_one("English Name"))
    (tmp_path / "de-DE" / "x.adml").write_bytes(_adml_one("German Name"))
    cat = load_catalogue(tmp_path)
    assert cat.policies[0].display_name == "English Name"


def test_load_catalogue_locale_fallback_is_deterministic(tmp_path) -> None:
    # No en-US present: the sorted-first locale (de-DE before fr-FR) is chosen
    # deterministically, not whatever iterdir() happens to yield first.
    (tmp_path / "x.admx").write_bytes(_ADMX_ONE)
    (tmp_path / "fr-FR").mkdir()
    (tmp_path / "de-DE").mkdir()
    (tmp_path / "fr-FR" / "x.adml").write_bytes(_adml_one("French Name"))
    (tmp_path / "de-DE" / "x.adml").write_bytes(_adml_one("German Name"))
    cat = load_catalogue(tmp_path)
    assert cat.policies[0].display_name == "German Name"


def test_load_catalogue_empty_dir(tmp_path) -> None:
    cat = load_catalogue(tmp_path)
    assert cat.policies == ()
    assert cat.categories == ()
    assert cat.supported_on == ()


def test_load_catalogue_nonexistent_dir(tmp_path) -> None:
    cat = load_catalogue(tmp_path / "nonexistent")
    assert cat.policies == ()
    assert cat.categories == ()
    assert cat.supported_on == ()


_ADMX_ENUM = b"""<?xml version="1.0" encoding="utf-8"?>
<policyDefinitions xmlns="http://www.microsoft.com/GroupPolicy/PolicyDefinitions">
  <categories>
    <category name="EnumCat" displayName="$(string.EnumCat)">
      <parentCategory ref="ParentCat" />
    </category>
  </categories>
  <supportedOn>
    <definition name="Supported_Synthetic" displayName="$(string.Supported_Synthetic)" />
  </supportedOn>
  <policies>
    <policy name="EnumPolicy" class="Machine" key="Software\\Policies\\Test"
            displayName="$(string.EnumPolicy)" explainText="$(string.EnumPolicy_Explain)"
            supportedOn="Supported_Synthetic" presentation="$(presentation.EnumPolicy)">
      <parentCategory ref="EnumCat" />
      <supportedOn ref="Supported_Synthetic" />
      <elements>
        <enum id="Mode" key="Software\\Policies\\Test" valueName="Mode">
          <item displayName="$(string.ModeStandard)">
            <decimal value="0" />
          </item>
          <item displayName="$(string.ModeAdvanced)">
            <decimal value="1" />
          </item>
          <item displayName="$(string.ModeCustom)">
            <string value="custom" />
          </item>
        </enum>
      </elements>
      <presentation />
    </policy>
    <policy name="EmptyEnumPolicy" class="Machine" key="Software\\Policies\\Test2"
            displayName="$(string.EmptyEnumPolicy)" explainText="$(string.EmptyEnumPolicy_Explain)"
            supportedOn="Supported_Synthetic" presentation="$(presentation.EmptyEnumPolicy)">
      <parentCategory ref="EnumCat" />
      <supportedOn ref="Supported_Synthetic" />
      <elements>
        <enum id="EmptyMode" key="Software\\Policies\\Test2" valueName="EmptyMode" />
      </elements>
      <presentation />
    </policy>
    <policy name="UnknownEnumPolicy" class="Machine" key="Software\\Policies\\Test3"
            displayName="$(string.UnknownEnumPolicy)"
            explainText="$(string.UnknownEnumPolicy_Explain)"
            supportedOn="Supported_Synthetic" presentation="$(presentation.UnknownEnumPolicy)">
      <parentCategory ref="EnumCat" />
      <supportedOn ref="Supported_Synthetic" />
      <elements>
        <enum id="UnknownMode" key="Software\\Policies\\Test3" valueName="UnknownMode">
          <item displayName="$(string.UnknownItem1)">
            <customValue value="42" />
          </item>
          <item displayName="$(string.UnknownItem2)">
            <decimal value="7" />
          </item>
        </enum>
      </elements>
      <presentation />
    </policy>
    <policy name="LongDecimalEnumPolicy" class="Machine" key="Software\\Policies\\Test4"
            displayName="$(string.LongDecimalEnumPolicy)"
            explainText="$(string.LongDecimalEnumPolicy_Explain)"
            supportedOn="Supported_Synthetic" presentation="$(presentation.LongDecimalEnumPolicy)">
      <parentCategory ref="EnumCat" />
      <supportedOn ref="Supported_Synthetic" />
      <elements>
        <enum id="QwordMode" key="Software\\Policies\\Test4" valueName="QwordMode">
          <item displayName="$(string.QwordStandard)">
            <longDecimal value="0" />
          </item>
          <item displayName="$(string.QwordBig)">
            <longDecimal value="4294967296" />
          </item>
        </enum>
      </elements>
      <presentation />
    </policy>
  </policies>
</policyDefinitions>"""

_AML_ENUM = b"""<?xml version="1.0" encoding="utf-8"?>
<policyDefinitionResources xmlns="http://www.microsoft.com/GroupPolicy/PolicyDefinitions">
  <resources>
    <stringTable>
      <string id="EnumCat">Enum Category</string>
      <string id="Supported_Synthetic">Synthetic OS Support</string>
      <string id="EnumPolicy">Enum Policy</string>
      <string id="EnumPolicy_Explain">Policy with enum items.</string>
      <string id="EmptyEnumPolicy">Empty Enum Policy</string>
      <string id="EmptyEnumPolicy_Explain">Policy with empty enum.</string>
      <string id="UnknownEnumPolicy">Unknown Enum Policy</string>
      <string id="UnknownEnumPolicy_Explain">Policy with unknown enum item types.</string>
      <string id="LongDecimalEnumPolicy">Long Decimal Enum Policy</string>
      <string id="LongDecimalEnumPolicy_Explain">Policy with longDecimal enum items.</string>
      <string id="ModeStandard">Standard Mode</string>
      <string id="ModeAdvanced">Advanced Mode</string>
      <string id="ModeCustom">Custom Mode</string>
      <string id="UnknownItem1">Unknown Item 1</string>
      <string id="UnknownItem2">Unknown Item 2</string>
      <string id="QwordStandard">Standard Qword</string>
      <string id="QwordBig">Big Qword</string>
    </stringTable>
  </resources>
</policyDefinitionResources>"""


def _get_enum_element(cat, policy_name: str, enum_id: str):
    policy = [p for p in cat.policies if p.id == policy_name][0]
    return [e for e in policy.elements if e.id == enum_id][0]


def test_enum_decimal_items_produce_dword() -> None:
    cat = build_catalogue(_ADMX_ENUM, _AML_ENUM)
    elem = _get_enum_element(cat, "EnumPolicy", "Mode")
    decimal_items = [i for i in elem.enum_items if i.registry_type == "REG_DWORD"]
    assert len(decimal_items) == 2
    assert decimal_items[0].value == 0
    assert decimal_items[1].value == 1
    assert all(isinstance(i.value, int) for i in decimal_items)
    assert decimal_items[0].id == "0"
    assert decimal_items[1].id == "1"


def test_enum_string_items_produce_reg_sz() -> None:
    cat = build_catalogue(_ADMX_ENUM, _AML_ENUM)
    elem = _get_enum_element(cat, "EnumPolicy", "Mode")
    string_items = [i for i in elem.enum_items if i.registry_type == "REG_SZ"]
    assert len(string_items) == 1
    assert string_items[0].value == "custom"
    assert isinstance(string_items[0].value, str)
    assert string_items[0].id == "custom"


def test_enum_mixed_decimal_and_string_items() -> None:
    cat = build_catalogue(_ADMX_ENUM, _AML_ENUM)
    elem = _get_enum_element(cat, "EnumPolicy", "Mode")
    assert len(elem.enum_items) == 3
    types = {i.registry_type for i in elem.enum_items}
    assert types == {"REG_DWORD", "REG_SZ"}
    values = [i.value for i in elem.enum_items]
    assert 0 in values
    assert 1 in values
    assert "custom" in values


def test_enum_with_no_items_produces_empty_tuple() -> None:
    cat = build_catalogue(_ADMX_ENUM, _AML_ENUM)
    elem = _get_enum_element(cat, "EmptyEnumPolicy", "EmptyMode")
    assert elem.enum_items == ()


def test_enum_skips_unknown_value_type_items() -> None:
    cat = build_catalogue(_ADMX_ENUM, _AML_ENUM)
    elem = _get_enum_element(cat, "UnknownEnumPolicy", "UnknownMode")
    assert len(elem.enum_items) == 1
    assert elem.enum_items[0].registry_type == "REG_DWORD"
    assert elem.enum_items[0].value == 7


def test_enum_long_decimal_items_produce_qword() -> None:
    cat = build_catalogue(_ADMX_ENUM, _AML_ENUM)
    elem = _get_enum_element(cat, "LongDecimalEnumPolicy", "QwordMode")
    assert len(elem.enum_items) == 2
    assert all(i.registry_type == "REG_QWORD" for i in elem.enum_items)
    assert elem.enum_items[0].value == 0
    assert elem.enum_items[1].value == 4294967296
    assert all(isinstance(i.value, int) for i in elem.enum_items)


def test_enum_item_display_names_resolved_from_adml() -> None:
    cat = build_catalogue(_ADMX_ENUM, _AML_ENUM)
    elem = _get_enum_element(cat, "EnumPolicy", "Mode")
    names = [i.display_name for i in elem.enum_items]
    assert names == ["Standard Mode", "Advanced Mode", "Custom Mode"]


def test_enum_item_type_annotation() -> None:
    cat = build_catalogue(_ADMX_ENUM, _AML_ENUM)
    elem = _get_enum_element(cat, "EnumPolicy", "Mode")
    assert all(isinstance(i, EnumItem) for i in elem.enum_items)


def test_enum_malformed_decimal_value_skipped() -> None:
    admx = b"""<?xml version="1.0" encoding="utf-8"?>
<policyDefinitions xmlns="http://www.microsoft.com/GroupPolicy/PolicyDefinitions">
  <policies>
    <policy name="MalformedEnum" class="Machine" key="Software\\Policies\\Test"
            displayName="Malformed" explainText="Test" supportedOn="S"
            presentation="$(presentation.MalformedEnum)">
      <elements>
        <enum id="BadMode" key="Software\\Policies\\Test" valueName="BadMode">
          <item displayName="Bad">
            <decimal value="not-a-number" />
          </item>
          <item displayName="Good">
            <decimal value="42" />
          </item>
        </enum>
      </elements>
      <presentation />
    </policy>
  </policies>
</policyDefinitions>"""
    policies, _, _ = parse_admx(admx)
    elem = policies[0].elements[0]
    assert len(elem.enum_items) == 1
    assert elem.enum_items[0].value == 42
