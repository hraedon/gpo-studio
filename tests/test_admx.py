from __future__ import annotations

import pytest

from gpo_studio.admx import (
    AdmxError,
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


def test_load_catalogue_from_directory(tmp_path) -> None:
    (tmp_path / "test.admx").write_bytes(_ADMX_MINIMAL)
    (tmp_path / "test.adml").write_bytes(_ADML_MINIMAL)
    cat = load_catalogue(tmp_path)
    assert len(cat.policies) == 2
    assert cat.policies[0].display_name == "Synthetic Policy"
    assert len(cat.categories) == 1
    assert len(cat.supported_on) == 1


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
