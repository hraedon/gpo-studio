from __future__ import annotations

from pathlib import Path

import pytest

from gpo_studio.admx import (
    AdmxCatalogue,
    AdmxError,
    EnumItem,
    PolicyElement,
    build_catalogue,
    find_adml,
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
        <multiText id="MultiLine" key="Software\\Policies\\Synthetic" valueName="MultiLine" />
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


def test_multitext_element_capital_t_not_unknown() -> None:
    cat = build_catalogue(_ADMX_MINIMAL, _ADML_MINIMAL)
    elements = cat.policies[0].elements
    multi = [e for e in elements if e.id == "MultiLine"][0]
    assert multi.kind == "multitext"
    assert multi.tag_name == "multiText"


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


def test_supported_on_definitions_wrapper() -> None:
    admx = b"""<?xml version="1.0" encoding="utf-8"?>
<policyDefinitions xmlns="http://www.microsoft.com/GroupPolicy/PolicyDefinitions">
  <supportedOn>
    <definitions>
      <definition name="SUPPORTED_Windows7" displayName="$(string.SUPPORTED_Windows7)" />
      <definition name="SUPPORTED_Windows8" displayName="$(string.SUPPORTED_Windows8)" />
    </definitions>
  </supportedOn>
  <policies>
    <policy name="P" class="Machine" key="Software\\Policies\\Synthetic"
            displayName="$(string.P)" explainText="$(string.P)" supportedOn="SUPPORTED_Windows7"
            presentation="$(presentation.P)">
      <supportedOn ref="SUPPORTED_Windows7" />
      <presentation />
    </policy>
  </policies>
</policyDefinitions>"""
    adml = b"""<?xml version="1.0" encoding="utf-8"?>
<policyDefinitionResources xmlns="http://www.microsoft.com/GroupPolicy/PolicyDefinitions">
  <resources>
    <stringTable>
      <string id="SUPPORTED_Windows7">Windows 7</string>
      <string id="SUPPORTED_Windows8">Windows 8</string>
      <string id="P">Synthetic Policy</string>
    </stringTable>
  </resources>
</policyDefinitionResources>"""
    cat = build_catalogue(admx, adml)
    assert len(cat.supported_on) == 2
    names = {d.name: d.display_name for d in cat.supported_on}
    assert names["SUPPORTED_Windows7"] == "Windows 7"
    assert names["SUPPORTED_Windows8"] == "Windows 8"


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


def test_load_catalogue_skips_bad_file_loads_good(tmp_path) -> None:
    (tmp_path / "good.admx").write_bytes(_ADMX_MINIMAL)
    (tmp_path / "good.adml").write_bytes(_ADML_MINIMAL)
    (tmp_path / "bad.admx").write_bytes(b"<not valid xml")
    (tmp_path / "bad.adml").write_bytes(_ADML_MINIMAL)
    cat = load_catalogue(tmp_path)
    assert len(cat.policies) == 2
    assert cat.policies[0].display_name == "Synthetic Policy"
    assert len(cat.load_errors) == 1
    assert "bad.admx" in cat.load_errors[0]


def test_find_adml_sibling_case_insensitive(tmp_path, case_sensitive_fs) -> None:
    (tmp_path / "policy.admx").write_bytes(b"<x/>")
    (tmp_path / "Policy.adml").write_bytes(b"<x/>")
    result = find_adml(tmp_path / "policy.admx")
    assert result is not None
    assert result.name == "Policy.adml"


def test_find_adml_locale_dir_case_insensitive(tmp_path, case_sensitive_fs) -> None:
    (tmp_path / "policy.admx").write_bytes(b"<x/>")
    (tmp_path / "EN-US").mkdir()
    (tmp_path / "EN-US" / "Policy.adml").write_bytes(b"<x/>")
    result = find_adml(tmp_path / "policy.admx")
    assert result is not None
    assert result.name == "Policy.adml"


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


def _real_enum(catalogue: AdmxCatalogue, enum_id: str) -> PolicyElement:
    for policy in catalogue.policies:
        for element in policy.elements:
            if element.kind == "enum" and element.id == enum_id:
                return element
    raise AssertionError(f"enum {enum_id!r} not in catalogue")


def test_real_admx_enum_items_inside_value_are_parsed() -> None:
    """Issue #139: real ADMX wraps each item's value in ``<value>``."""
    real = Path(__file__).parent / "fixtures" / "admx-real"
    catalogue = build_catalogue(
        (real / "UserProfiles.admx").read_bytes(),
        (real / "UserProfiles.adml").read_bytes(),
    )
    for enum_id in (
        "UploadHiveMethod",
        "UploadHiveTime",
        "AllowUserInfoAccess",
        "USER_HOME_LOCATION",
        "DRIVE_LETTER",
    ):
        assert _real_enum(catalogue, enum_id).enum_items, enum_id

    method = _real_enum(catalogue, "UploadHiveMethod").enum_items
    assert [(i.value, i.registry_type) for i in method] == [
        (1, "REG_DWORD"),
        (2, "REG_DWORD"),
    ]
    # The schema's string form is element text, not a value attribute.
    drives = _real_enum(catalogue, "DRIVE_LETTER").enum_items
    assert all(i.registry_type == "REG_SZ" for i in drives)
    assert drives[-1].value == "Z:"
    assert drives[-1].display_name


def test_enum_item_value_wrapper_and_direct_child_both_parse() -> None:
    items = _enum_with_items(
        b'<item displayName="$(string.SyntheticPolicy)"><value><decimal value="3" /></value></item>'
        b'<item displayName="$(string.SyntheticPolicy)"><value><string>abc</string></value></item>'
        b'<item displayName="$(string.SyntheticPolicy)">'
        b'<value><longDecimal value="5000000000" /></value></item>'
        b'<item displayName="$(string.SyntheticPolicy)"><decimal value="4" /></item>'
    )
    assert [(i.value, i.registry_type) for i in items] == [
        (3, "REG_DWORD"),
        ("abc", "REG_SZ"),
        (5000000000, "REG_QWORD"),
        (4, "REG_DWORD"),
    ]


def _enum_with_items(items_xml: bytes) -> tuple[EnumItem, ...]:
    admx = _ADMX_MINIMAL.replace(
        b'<enum id="Mode" key="Software\\Policies\\Synthetic" valueName="Mode" />',
        b'<enum id="Mode" key="Software\\Policies\\Synthetic" valueName="Mode">'
        + items_xml
        + b"</enum>",
    )
    assert admx != _ADMX_MINIMAL
    return _real_enum(build_catalogue(admx, _ADML_MINIMAL), "Mode").enum_items


def test_enum_item_with_empty_string_value_gets_an_id_unlike_unselected() -> None:
    # The UI's unselected option is "", so an empty value cannot be the id.
    items = _enum_with_items(
        b'<item displayName="$(string.SyntheticPolicy)"><value><string /></value></item>'
        b'<item displayName="$(string.SyntheticPolicy)"><value><string>x</string></value></item>'
    )
    assert [(i.id, i.value) for i in items] == [("item-0", ""), ("x", "x")]


def test_enum_with_a_value_list_item_reports_no_items() -> None:
    # Its extra writes have no place in EnumItem; no items makes configure refuse.
    items = _enum_with_items(
        b'<item displayName="$(string.SyntheticPolicy)"><value><decimal value="1" /></value>'
        b'<valueList><item key="Software\\X" valueName="Y"><value><decimal value="2" />'
        b"</value></item></valueList></item>"
        b'<item displayName="$(string.SyntheticPolicy)"><value><decimal value="0" /></value></item>'
    )
    assert items == ()



_ITEM = b'<item displayName="$(string.SyntheticPolicy)">'


@pytest.mark.parametrize(
    "unreadable",
    [
        pytest.param(
            _ITEM + b'<value /><decimal value="1" /></item>', id="empty-value-beside-direct"
        ),
        pytest.param(
            _ITEM + b'<value><decimal value="1" /></value><decimal value="1" /></item>',
            id="value-beside-direct",
        ),
        pytest.param(
            _ITEM
            + b'<value><decimal value="1" /></value><value><decimal value="2" /></value></item>',
            id="two-values",
        ),
        pytest.param(_ITEM + b"<value /></item>", id="empty-value"),
        pytest.param(
            _ITEM + b'<value><decimal value="1" /><decimal value="2" /></value></item>',
            id="two-children-in-value",
        ),
        pytest.param(_ITEM + b'<decimal value="1" /><string>a</string></item>', id="two-direct"),
        pytest.param(
            _ITEM + b'<value><string value="a">b</string></value></item>', id="attr-and-text"
        ),
        pytest.param(
            _ITEM + b'<value><string value="a"> </string></value></item>',
            id="attr-and-whitespace-text",
        ),
    ],
)
def test_enum_with_an_unreadable_item_reports_no_items(unreadable: bytes) -> None:
    # Issue #142: each of these was dropped or read as a guess. A partial list
    # would hide the item; no items makes configure refuse the enum.
    items = _enum_with_items(
        _ITEM + b'<value><decimal value="0" /></value></item>' + unreadable
    )
    assert items == ()


# The shape of inetres.admx NeedEdgeBrowser (Windows Server 2025): a
# Both-class policy with no policy-level valueName whose enums offer "None"
# as a <delete/> item before the decimal choices. Names and key are synthetic.
DELETE_ITEM_ADMX = b"""<?xml version="1.0" encoding="utf-8"?>
<policyDefinitions revision="1.0" schemaVersion="1.0"
    xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions">
  <policyNamespaces>
    <target prefix="synthetic" namespace="Synthetic.Policies.DeleteItem" />
  </policyNamespaces>
  <resources minRequiredRevision="1.0" />
  <policies>
    <policy name="NeedEdgeBrowser" class="Both" displayName="$(string.NeedEdgeBrowser)"
        explainText="$(string.NeedEdgeBrowser)" key="Software\\Policies\\Synthetic\\DeleteItem"
        presentation="$(presentation.NeedEdgeBrowser)">
      <elements>
        <enum id="NeedEdgeBrowser" valueName="NeedEdgeBrowser">
          <item displayName="$(string.Choice_None)">
            <value>
              <delete />
            </value>
          </item>
          <item displayName="$(string.Choice_Stable)">
            <value>
              <decimal value="1" />
            </value>
          </item>
          <item displayName="$(string.Choice_Beta)">
            <value>
              <decimal value="2" />
            </value>
          </item>
          <item displayName="$(string.Choice_Legacy)">
            <value>
              <decimal value="0" />
            </value>
          </item>
        </enum>
      </elements>
    </policy>
  </policies>
</policyDefinitions>"""

DELETE_ITEM_ADML = b"""<?xml version="1.0" encoding="utf-8"?>
<policyDefinitionResources revision="1.0" schemaVersion="1.0"
    xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions">
  <resources>
    <stringTable>
      <string id="NeedEdgeBrowser">Synthetic browser choice</string>
      <string id="Choice_None">None</string>
      <string id="Choice_Stable">Stable channel</string>
      <string id="Choice_Beta">Beta channel</string>
      <string id="Choice_Legacy">Legacy engine</string>
    </stringTable>
    <presentationTable>
      <presentation id="NeedEdgeBrowser">
        <dropdownList refId="NeedEdgeBrowser">Choice</dropdownList>
      </presentation>
    </presentationTable>
  </resources>
</policyDefinitionResources>"""


def test_real_delete_item_shape_is_a_choice_that_removes_the_value() -> None:
    """Issue #142: inetres.admx NeedEdgeBrowser offers "None" as <delete/>."""
    catalogue = build_catalogue(DELETE_ITEM_ADMX, DELETE_ITEM_ADML)
    items = _real_enum(catalogue, "NeedEdgeBrowser").enum_items
    assert [(i.id, i.action, i.registry_type, i.value, i.display_name) for i in items] == [
        ("item-0", "delete", "REG_SZ", "", "None"),
        ("1", "set", "REG_DWORD", 1, "Stable channel"),
        ("2", "set", "REG_DWORD", 2, "Beta channel"),
        ("0", "set", "REG_DWORD", 0, "Legacy engine"),
    ]


@pytest.mark.parametrize(
    ("xml", "expected"),
    [
        pytest.param(b"<string> G: </string>", " G: ", id="padded-text"),
        pytest.param(b'<string value=" G: " />', " G: ", id="padded-attribute"),
        pytest.param(b"<string>\n  Z:\n</string>", "\n  Z:\n", id="multiline-text"),
    ],
)
def test_enum_string_whitespace_is_value_data(xml: bytes, expected: str) -> None:
    # ADMX strings are xs:string: surrounding whitespace is part of the value.
    items = _enum_with_items(
        _ITEM + b"<value>" + xml + b"</value></item>"
        + _ITEM + b"<value><string>other</string></value></item>"
    )
    assert [(i.id, i.value) for i in items] == [(expected, expected), ("other", "other")]


def test_enum_numeric_fallback_ids_are_canonical() -> None:
    # Issue #142: "01" writes the value 1, so its id is "1".
    items = _enum_with_items(
        _ITEM + b'<value><decimal value="01" /></value></item>'
        + _ITEM + b'<value><longDecimal value="0042" /></value></item>'
        + _ITEM + b'<value><decimal value="0" /></value></item>'
    )
    assert [(i.id, i.value, i.registry_type) for i in items] == [
        ("1", 1, "REG_DWORD"),
        ("42", 42, "REG_QWORD"),
        ("0", 0, "REG_DWORD"),
    ]


@pytest.mark.parametrize(
    ("xml", "expected"),
    [
        pytest.param(
            _ITEM + b'<value><decimal value="1" /></value></item>'
            + _ITEM + b"<value><string>1</string></value></item>"
            + _ITEM + b'<value><decimal value="2" /></value></item>',
            [("decimal:1", "REG_DWORD", 1), ("string:1", "REG_SZ", "1"), ("2", "REG_DWORD", 2)],
            id="decimal-and-string",
        ),
        pytest.param(
            _ITEM + b'<value><decimal value="1" /></value></item>'
            + _ITEM + b'<value><longDecimal value="1" /></value></item>',
            [("decimal:1", "REG_DWORD", 1), ("longDecimal:1", "REG_QWORD", 1)],
            id="dword-and-qword",
        ),
        pytest.param(
            _ITEM + b"<value><string /></value></item>"
            + _ITEM + b"<value><string>item-0</string></value></item>",
            [("string:", "REG_SZ", ""), ("string:item-0", "REG_SZ", "item-0")],
            id="empty-and-literal-placeholder",
        ),
        pytest.param(
            _ITEM + b'<value><decimal value="1" /></value></item>'
            + _ITEM + b"<value><string>1</string></value></item>"
            + _ITEM + b"<value><string>decimal:1</string></value></item>",
            [
                ("decimal:1", "REG_DWORD", 1),
                ("string:1", "REG_SZ", "1"),
                ("string:decimal:1", "REG_SZ", "decimal:1"),
            ],
            id="qualified-id-collides-again",
        ),
    ],
)
def test_type_distinct_items_with_colliding_fallback_ids_are_qualified(
    xml: bytes, expected: list[tuple[str, str, object]]
) -> None:
    # Issue #142: the registry type is part of an item's identity. Only the
    # colliding fallback ids change; a unique one stays exactly str(value).
    assert [(i.id, i.registry_type, i.value) for i in _enum_with_items(xml)] == expected


@pytest.mark.parametrize(
    "duplicate",
    [
        pytest.param(
            _ITEM + b'<value><decimal value="1" /></value></item>'
            + _ITEM + b'<value><decimal value="01" /></value></item>',
            id="same-decimal",
        ),
        pytest.param(
            _ITEM + b"<value><string>a</string></value></item>"
            + _ITEM + b"<value><string>a</string></value></item>",
            id="same-string",
        ),
        pytest.param(
            _ITEM + b"<value><delete /></value></item>"
            + _ITEM + b"<value><delete /></value></item>",
            id="two-deletes",
        ),
        pytest.param(
            b'<item id="x" displayName="$(string.SyntheticPolicy)"><decimal value="1" /></item>'
            + b'<item id="x" displayName="$(string.SyntheticPolicy)"><decimal value="2" /></item>',
            id="same-explicit-id",
        ),
    ],
)
def test_enum_with_true_duplicates_reports_no_items(duplicate: bytes) -> None:
    # Same type and value (or the same explicit id): the first match would win.
    assert _enum_with_items(duplicate) == ()


@pytest.mark.parametrize(
    "spelling", ["\u0661", "1_0", "\uff11", "-1", "4294967296"]
)
def test_enum_decimal_outside_the_schema_is_skipped_as_malformed(spelling: str) -> None:
    # int() accepts these; xs:unsignedInt does not (lexically or by range), so
    # they take Plan 005's malformed-number path.
    items = _enum_with_items(
        _ITEM + f'<value><decimal value="{spelling}" /></value></item>'.encode()
        + _ITEM + b'<value><decimal value="3" /></value></item>'
    )
    assert [(i.id, i.value) for i in items] == [("3", 3)]


@pytest.mark.parametrize(
    ("spelling", "kept"),
    [("-1", False), ("18446744073709551616", False), ("18446744073709551615", True)],
)
def test_enum_long_decimal_is_unsigned_64_bit(spelling: str, kept: bool) -> None:
    items = _enum_with_items(
        _ITEM + f'<value><longDecimal value="{spelling}" /></value></item>'.encode()
        + _ITEM + b'<value><longDecimal value="3" /></value></item>'
    )
    assert [i.value for i in items] == ([int(spelling)] if kept else []) + [3]


_EXPLICIT_ONE = b'<item id="1" displayName="$(string.SyntheticPolicy)">'


@pytest.mark.parametrize(
    "xml",
    [
        pytest.param(
            _ITEM + b'<value><decimal value="1" /></value></item>'
            + _EXPLICIT_ONE + b"<value><string>other</string></value></item>",
            id="fallback-first",
        ),
        pytest.param(
            _EXPLICIT_ONE + b"<value><string>other</string></value></item>"
            + _ITEM + b'<value><decimal value="1" /></value></item>',
            id="explicit-first",
        ),
        pytest.param(
            _ITEM + b'<value><decimal value="1" /></value></item>'
            + _ITEM + b"<value><string>1</string></value></item>"
            + b'<item id="decimal:1" displayName="$(string.SyntheticPolicy)">'
            + b"<value><string>other</string></value></item>",
            id="explicit-equals-qualified-fallback",
        ),
    ],
)
def test_explicit_id_equal_to_a_fallback_id_reports_no_items(xml: bytes) -> None:
    # Issue #142 review: on main the first item with id "1" won, so submitting
    # "1" wrote REG_DWORD 1 (fallback first) or REG_SZ "other" (explicit
    # first). Renaming the fallback would move "1" to another write; refuse.
    assert _enum_with_items(xml) == ()


def test_explicit_ids_distinct_from_every_fallback_are_kept() -> None:
    items = _enum_with_items(
        b'<item id="a" displayName="$(string.SyntheticPolicy)"><decimal value="1" /></item>'
        + _ITEM + b'<value><decimal value="2" /></value></item>'
    )
    assert [(i.id, i.value) for i in items] == [("a", 1), ("2", 2)]
