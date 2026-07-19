"""ADMX/ADML namespace robustness (Plan 022 WP-1, lesson from gpo-lens).

Real Windows and vendor ADMX files declare the namespace
``http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions``, not the
``http://www.microsoft.com/GroupPolicy/PolicyDefinitions`` URI used in the
MS-GPREG schema text and in this project's early synthetic fixtures. The parser
originally pinned the latter and silently parsed ZERO policies from real
central-store files. gpo-lens (tested against real SYSVOL exports) uses the
``2006/07`` namespace; the lesson brought over here is to match by local name in
ANY namespace. These tests would have caught the original gap.
"""

from __future__ import annotations

import pytest

from gpo_studio.admx import PolicyValue, build_catalogue, parse_admx

# The real-world namespace used by shipped Windows/vendor ADMX files.
_REAL_NS = "http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions"
# The MS-GPREG-schema / early-fixture namespace.
_DOC_NS = "http://www.microsoft.com/GroupPolicy/PolicyDefinitions"


def _admx(ns: str) -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<policyDefinitions xmlns="{ns}" revision="1.0" schemaVersion="1.0">
  <categories><category name="Cat" displayName="$(string.Cat)" /></categories>
  <policies>
    <policy name="P" class="Machine" key="Software\\Policies\\Synthetic" valueName="V"
            displayName="$(string.P)" explainText="$(string.E)" supportedOn="S">
      <enabledValue><decimal value="1" /></enabledValue>
      <disabledValue><delete /></disabledValue>
    </policy>
  </policies>
</policyDefinitions>""".encode()


def _adml(ns: str) -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<policyDefinitionResources xmlns="{ns}">
  <resources><stringTable>
    <string id="P">Synthetic Policy</string>
    <string id="E">Explain.</string>
    <string id="Cat">Category</string>
  </stringTable></resources>
</policyDefinitionResources>""".encode()


@pytest.mark.parametrize("ns", [_REAL_NS, _DOC_NS], ids=["real-2006-07", "ms-gpreg-doc"])
def test_admx_policies_parse_regardless_of_namespace(ns: str) -> None:
    policies, _categories, _supported = parse_admx(_admx(ns))
    assert len(policies) == 1
    p = policies[0]
    assert p.value_name == "V"
    assert p.enabled_value == PolicyValue("decimal", "1", "REG_DWORD")
    assert p.disabled_value == PolicyValue("delete", "", None)


@pytest.mark.parametrize("ns", [_REAL_NS, _DOC_NS], ids=["real-2006-07", "ms-gpreg-doc"])
def test_adml_display_names_resolve_regardless_of_namespace(ns: str) -> None:
    catalogue = build_catalogue(_admx(ns), _adml(ns))
    assert catalogue.policies[0].display_name == "Synthetic Policy"
    assert catalogue.categories[0].display_name == "Category"


def test_no_default_namespace_still_parses() -> None:
    # A namespace-less document (some tooling strips xmlns) must not break the
    # local-name matching either.
    doc = b"""<?xml version="1.0" encoding="utf-8"?>
<policyDefinitions>
  <policies>
    <policy name="P" class="Machine" key="Software\\Policies\\Synthetic" valueName="V"
            displayName="$(string.P)" explainText="$(string.E)" supportedOn="S">
      <enabledValue><decimal value="1" /></enabledValue>
    </policy>
  </policies>
</policyDefinitions>"""
    policies, _categories, _supported = parse_admx(doc)
    assert len(policies) == 1
    assert policies[0].enabled_value == PolicyValue("decimal", "1", "REG_DWORD")
