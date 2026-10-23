"""Schema-family selection is per resolved policy group, not per row/element pair."""

from __future__ import annotations

from dataclasses import replace

import pytest

import gpo_studio.settings_browser as browser
from gpo_studio.admx import AdmxCatalogue, PolicyDefinition, PolicyElement
from gpo_studio.policy_config import PolicyConfiguration, resolve_policy


def synthetic_policy(size: int, *, structured: bool = False) -> PolicyDefinition:
    return PolicyDefinition(
        id="SyntheticCost",
        class_="Both",
        key=r"Software\Policies\Synthetic\BrowserCost",
        namespace="https://synthetic.example.test/cost" if structured else "Synthetic.Cost",
        display_name="Synthetic browser cost",
        explain_text="",
        supported_on="",
        value_name="PolicyState",
        elements=tuple(
            PolicyElement(kind="text", id=f"Element{i}", registry_value_name=f"Value{i}")
            for i in range(size)
        ),
    )


@pytest.mark.parametrize("size", [1, 12, 40])
@pytest.mark.parametrize("structured", [False, True], ids=["legacy", "structured"])
def test_schema_family_is_computed_once_per_resolved_policy_side_group(
    size: int,
    structured: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = synthetic_policy(size, structured=structured)
    values = {f"Element{i}": f"value{i}" for i in range(size)}
    settings = [
        setting
        for side in ("computer", "user")
        for setting in resolve_policy(policy, PolicyConfiguration(side, values))
    ]
    unused = replace(policy, id="Unused", key=policy.key + r"\Unused")
    catalogue = AdmxCatalogue(policies=(policy, unused))
    choose_family = browser.structured_policy_ids
    calls: list[str] = []

    def counted(candidate: PolicyDefinition) -> bool:
        calls.append(candidate.qualified_id)
        return choose_family(candidate)

    monkeypatch.setattr(browser, "structured_policy_ids", counted)
    result = browser.build_settings_browser(catalogue, settings)
    assert len(result.resolved) == 2
    assert not result.unresolved
    assert calls == [policy.qualified_id, policy.qualified_id]
    assert {row.side for row in result.resolved} == {"computer", "user"}
    assert all(row.state == "enabled" and row.element_values == values for row in result.resolved)
