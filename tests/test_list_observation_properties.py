"""Independent fixed-address maps, not a writer/reader round-trip oracle."""

from __future__ import annotations

from dataclasses import replace

from hypothesis import given, settings
from hypothesis import strategies as st

from gpo_studio.admx import (
    AdmxCatalogue,
    PolicyDefinition,
    PolicyListItem,
    PolicyValue,
    PolicyValueList,
)
from gpo_studio.model import RegistrySetting
from gpo_studio.settings_browser import build_settings_browser

native_maps = st.dictionaries(
    st.sampled_from(["Alpha", "Beta", "Gamma"]), st.integers(0, 3), min_size=1
)


def value_list(values: dict[str, int]) -> PolicyValueList:
    return PolicyValueList(
        tuple(
            PolicyListItem(name, PolicyValue("decimal", str(value), "REG_DWORD"))
            for name, value in values.items()
        )
    )


@given(enabled=native_maps, disabled=native_maps, actual=native_maps)
@settings(max_examples=60, deadline=None)
def test_exact_nonempty_native_maps_determine_state_without_order_bias(
    enabled: dict[str, int], disabled: dict[str, int], actual: dict[str, int]
) -> None:
    p = replace(
        PolicyDefinition(
            id="SyntheticProperty",
            class_="Machine",
            key=r"Software\Policies\Synthetic\ObservedProperty",
            display_name="Synthetic observed property",
            explain_text="",
            supported_on="",
            namespace="Synthetic.Property",
        ),
        enabled_list=value_list(enabled),
        disabled_list=value_list(disabled),
    )
    # Only addresses declared by either state are evidence in this group.
    projection = {key: value for key, value in actual.items() if key in enabled or key in disabled}
    matches_enabled, matches_disabled = projection == enabled, projection == disabled
    expected = (
        "indeterminate"
        if matches_enabled == matches_disabled
        else "enabled"
        if matches_enabled
        else "disabled"
    )
    rows = [
        RegistrySetting(f"synthetic-{name}", "computer", "HKLM", p.key, name, "REG_DWORD", value)
        for name, value in actual.items()
    ]
    result = build_settings_browser(AdmxCatalogue(policies=(p,)), rows)
    if not projection:
        assert not result.resolved
        return
    assert result.resolved[0].state == expected
    swapped = replace(p, enabled_list=p.disabled_list, disabled_list=p.enabled_list)
    reversed_rows = [
        replace(
            row,
            key=row.key.swapcase(),
            value_name=row.value_name.swapcase(),
            comment="Synthetic ignored comment",
        )
        for row in reversed(rows)
    ]
    swapped_state = (
        build_settings_browser(AdmxCatalogue(policies=(swapped,)), reversed_rows).resolved[0].state
    )
    assert (
        swapped_state
        == {"enabled": "disabled", "disabled": "enabled", "indeterminate": "indeterminate"}[
            expected
        ]
    )
