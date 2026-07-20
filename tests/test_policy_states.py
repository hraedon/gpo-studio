"""Enabled / Disabled / Not Configured policy state authoring.

Plan 022 WP-1: "Model Enabled, Disabled, and Not Configured explicitly."

Before this, ``resolve_policy`` could only express "Enabled with these element
values": there was no state at all, and the policy-level value resolver
(``effective_enabled_value``/``effective_disabled_value``, landed with the ADMX
value semantics) had no production consumer — a policy's own registry value was
never written, only its child elements were.

The three states are NOT "has settings / has no settings". Disabled is an active
decision that writes its own value (commonly a 0, or a ``**del.``); Not
Configured must leave the policy out of Registry.pol entirely so a
lower-precedence GPO can still apply.
"""

from __future__ import annotations

import pytest

from gpo_studio.admx import (
    PolicyDefinition,
    PolicyElement,
    PolicyListItem,
    PolicyValue,
    PolicyValueList,
)
from gpo_studio.model import ValidationError
from gpo_studio.policy_config import (
    PolicyConfiguration,
    policy_setting_prefix,
    resolve_policy,
)

KEY = r"Software\Policies\Synthetic\Feature"


def _policy(
    *,
    value_name: str = "FeatureState",
    enabled_value: PolicyValue | None = None,
    disabled_value: PolicyValue | None = None,
    enabled_list: PolicyValueList | None = None,
    disabled_list: PolicyValueList | None = None,
    elements: tuple[PolicyElement, ...] = (),
) -> PolicyDefinition:
    return PolicyDefinition(
        id="SyntheticFeature",
        class_="Machine",
        key=KEY,
        display_name="Synthetic feature",
        explain_text="",
        supported_on="",
        namespace="Synthetic.Policies.VendorA",
        value_name=value_name,
        enabled_value=enabled_value,
        disabled_value=disabled_value,
        enabled_list=enabled_list,
        disabled_list=disabled_list,
        elements=elements,
    )


def _config(state: str, **values: object) -> PolicyConfiguration:
    return PolicyConfiguration(
        side="computer",
        values=dict(values),  # type: ignore[arg-type]
        state=state,  # type: ignore[arg-type]
    )


# --- explicit enabled/disabled values ---------------------------------------


def test_enabled_writes_the_policy_level_value() -> None:
    policy = _policy(
        enabled_value=PolicyValue("decimal", "1", "REG_DWORD"),
        disabled_value=PolicyValue("decimal", "0", "REG_DWORD"),
    )
    settings = resolve_policy(policy, _config("enabled"))
    assert len(settings) == 1
    assert (settings[0].key, settings[0].value_name) == (KEY, "FeatureState")
    assert (settings[0].registry_type, settings[0].value) == ("REG_DWORD", 1)
    assert settings[0].action == "set"


def test_disabled_writes_the_disabled_value_not_nothing() -> None:
    # The regression this guards: Disabled is not "no settings". It writes 0 here.
    policy = _policy(
        enabled_value=PolicyValue("decimal", "1", "REG_DWORD"),
        disabled_value=PolicyValue("decimal", "0", "REG_DWORD"),
    )
    settings = resolve_policy(policy, _config("disabled"))
    assert len(settings) == 1
    assert (settings[0].registry_type, settings[0].value) == ("REG_DWORD", 0)
    assert settings[0].action == "set"


def test_not_configured_writes_nothing() -> None:
    policy = _policy(
        enabled_value=PolicyValue("decimal", "1", "REG_DWORD"),
        disabled_value=PolicyValue("decimal", "0", "REG_DWORD"),
    )
    assert resolve_policy(policy, _config("not_configured")) == []


# --- implicit defaults, via the resolver that previously had no consumer -----


def test_implicit_enabled_default_is_applied() -> None:
    # valueName with no explicit enabledValue: Enabled writes REG_DWORD 1.
    settings = resolve_policy(_policy(), _config("enabled"))
    assert (settings[0].registry_type, settings[0].value) == ("REG_DWORD", 1)


def test_implicit_disabled_default_deletes_the_value() -> None:
    # ... and Disabled DELETES the value rather than writing a 0.
    settings = resolve_policy(_policy(), _config("disabled"))
    assert settings[0].action == "delete"
    assert settings[0].value_name == "FeatureState"


def test_delete_value_becomes_a_delete_action() -> None:
    policy = _policy(disabled_value=PolicyValue("delete", "", None))
    settings = resolve_policy(policy, _config("disabled"))
    assert settings[0].action == "delete"


def test_policy_without_value_name_writes_no_state_value() -> None:
    # A policy that expresses itself only through elements has no own value.
    policy = _policy(value_name="")
    assert resolve_policy(policy, _config("disabled")) == []


def test_string_state_value_round_trips_as_sz() -> None:
    policy = _policy(enabled_value=PolicyValue("string", "on", "REG_SZ"))
    settings = resolve_policy(policy, _config("enabled"))
    assert (settings[0].registry_type, settings[0].value) == ("REG_SZ", "on")


def test_long_decimal_state_value_is_qword() -> None:
    policy = _policy(enabled_value=PolicyValue("longDecimal", "5", "REG_QWORD"))
    settings = resolve_policy(policy, _config("enabled"))
    assert (settings[0].registry_type, settings[0].value) == ("REG_QWORD", 5)


# --- enabled/disabled value LISTS -------------------------------------------


def _value_list(*names: str, key: str = "") -> PolicyValueList:
    return PolicyValueList(
        items=tuple(
            PolicyListItem(value_name=n, value=PolicyValue("decimal", "1", "REG_DWORD"))
            for n in names
        ),
        default_key=key,
    )


def test_enabled_list_items_are_written() -> None:
    policy = _policy(enabled_list=_value_list("Alpha", "Beta"))
    settings = resolve_policy(policy, _config("enabled"))
    written = {(s.value_name, s.value) for s in settings}
    assert ("Alpha", 1) in written
    assert ("Beta", 1) in written


def test_disabled_list_is_used_for_the_disabled_state() -> None:
    policy = _policy(
        value_name="",
        enabled_list=_value_list("Alpha"),
        disabled_list=_value_list("Omega"),
    )
    settings = resolve_policy(policy, _config("disabled"))
    assert [s.value_name for s in settings] == ["Omega"]


def test_list_item_default_key_overrides_the_policy_key() -> None:
    other = r"Software\Policies\Synthetic\Other"
    policy = _policy(value_name="", enabled_list=_value_list("Alpha", key=other))
    settings = resolve_policy(policy, _config("enabled"))
    assert settings[0].key == other


def test_list_item_own_key_wins_over_default_key() -> None:
    item_key = r"Software\Policies\Synthetic\Item"
    policy = _policy(
        value_name="",
        enabled_list=PolicyValueList(
            items=(
                PolicyListItem(
                    value_name="Alpha",
                    value=PolicyValue("decimal", "1", "REG_DWORD"),
                    key=item_key,
                ),
            ),
            default_key=r"Software\Policies\Synthetic\Default",
        ),
    )
    settings = resolve_policy(policy, _config("enabled"))
    assert settings[0].key == item_key


# --- element values are refused where they cannot be written ----------------


def _boolean_element() -> PolicyElement:
    return PolicyElement(
        kind="boolean", id="Sub", registry_value_name="Sub", tag_name="boolean"
    )


def test_enabled_writes_both_the_state_value_and_elements() -> None:
    policy = _policy(elements=(_boolean_element(),))
    settings = resolve_policy(policy, _config("enabled", Sub=True))
    names = {s.value_name for s in settings}
    assert names == {"FeatureState", "Sub"}


@pytest.mark.parametrize("state", ["disabled", "not_configured"])
def test_element_values_are_refused_when_they_cannot_be_written(state: str) -> None:
    # GPMC greys out the options panel in these states. Silently dropping the
    # supplied value would let an operator believe it took effect.
    policy = _policy(elements=(_boolean_element(),))
    with pytest.raises(ValidationError) as excinfo:
        resolve_policy(policy, _config(state, Sub=True))
    assert excinfo.value.issues[0].code == "element_values_in_non_enabled_state"


@pytest.mark.parametrize("state", ["disabled", "not_configured"])
def test_non_enabled_states_need_no_element_values(state: str) -> None:
    policy = _policy(elements=(_boolean_element(),))
    resolve_policy(policy, _config(state))  # must not raise missing_element_value


def test_enabled_still_requires_every_element_value() -> None:
    policy = _policy(elements=(_boolean_element(),))
    with pytest.raises(ValidationError) as excinfo:
        resolve_policy(policy, _config("enabled"))
    assert excinfo.value.issues[0].code == "missing_element_value"


# --- setting identity -------------------------------------------------------


def test_every_setting_shares_the_policy_prefix() -> None:
    # Not Configured removal works by prefix, so this is load-bearing.
    policy = _policy(
        enabled_list=_value_list("Alpha"), elements=(_boolean_element(),)
    )
    settings = resolve_policy(policy, _config("enabled", Sub=True))
    prefix = policy_setting_prefix(policy, "computer")
    assert len(settings) == 3
    assert all(s.id.startswith(prefix) for s in settings)


def test_setting_ids_within_a_policy_are_unique() -> None:
    policy = _policy(
        enabled_list=_value_list("Alpha", "Beta"), elements=(_boolean_element(),)
    )
    settings = resolve_policy(policy, _config("enabled", Sub=True))
    assert len({s.id for s in settings}) == len(settings)


def test_prefix_is_namespace_qualified_and_side_scoped() -> None:
    policy = _policy()
    assert (
        policy_setting_prefix(policy, "computer")
        == "admx-Synthetic.Policies.VendorA:SyntheticFeature-computer-"
    )
    assert policy_setting_prefix(policy, "user") != policy_setting_prefix(
        policy, "computer"
    )
