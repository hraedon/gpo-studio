"""The corpus must actually carry the rows the register says it will.

Opened for WI-049, whose three cells were answered by argument rather than by
measurement; all three are MEASURED as of the 2026-09-06 batch (the verdicts are
committed beside the closure), and WI-054 added the last cell the same rule
applies to -- a deny matched through a group in the CLIENT'S machine token.

These tests now do regression work, and the reason they exist is unchanged and
still worth stating: they pin that the estate corpus contains a row for each
measured region, on a scenario the lanes actually run, so a corpus edit cannot
silently drop a measurement the register says was taken. That is the failure
this project keeps having. WI-025 was minted in a design paragraph and
rediscovered a month later; a plan header said `proposed` while implemented; the
capability matrix said `failed` while supported. Every one was a document that
nothing checked.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, cast

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MODULE_PATH = _REPO_ROOT / "scripts" / "plan-033" / "build-rsop-candidate.py"

_spec = importlib.util.spec_from_file_location("build_rsop_candidate", _MODULE_PATH)
assert _spec and _spec.loader
build_rsop_candidate = importlib.util.module_from_spec(_spec)
sys.modules["build_rsop_candidate"] = build_rsop_candidate
_spec.loader.exec_module(build_rsop_candidate)

DOMAIN = "ad.example.test"
SITE = "Default-First-Site-Name"
COMPUTER = "CLIENT01"
USER = "labuser"


def _scenario(scenario_id: str) -> Any:
    return build_rsop_candidate.SCENARIOS[scenario_id]


def _predict(scenario_id: str) -> dict[str, Any]:
    scenario = _scenario(scenario_id)
    return cast(
        dict[str, Any],
        build_rsop_candidate.prediction_document(scenario, DOMAIN, SITE, COMPUTER, USER),
    )


def _row(prediction: dict[str, Any], gpo: str) -> str:
    if gpo in prediction["applied_gpos"]:
        return "applied"
    if any(row["gpo"] == gpo for row in prediction["denied_gpos"]):
        return "blocked"
    if any(row["gpo"] == gpo for row in prediction["unevaluable_gpos"]):
        return "unevaluable"
    raise AssertionError(f"{gpo} is in no bucket of the prediction")


def _winner(prediction: dict[str, Any], value_name: str) -> str:
    for row in prediction["winners"]:
        if row["value_name"] == value_name:
            return str(row["winning_gpo"])
    return ""


def test_the_read_cell_is_carried_by_a_computer_scope_scenario() -> None:
    """A user-named read deny on the computer side, and the model still applies it.

    The user has to be NAMED for this to measure anything: an empty principal
    matches nothing, so the row would author a deny against no one and the
    scenario would certify agreement on an experiment that did not happen.
    """
    prediction = _predict("computer-security-filtering-deny-read")
    assert prediction["target"]["user_name"] == USER
    assert _row(prediction, "Studio-RSOP-CompFilterDenyReadUser") == "applied"
    # At the top link order, so a wrong answer costs the WINNER rather than one
    # absent value -- which is the difference between a sharp measurement and a
    # weak one.
    assert _winner(prediction, "Filter") == "Studio-RSOP-CompFilterDenyReadUser"
    # The measured neighbour still says what it always said.
    assert _row(prediction, "Studio-RSOP-CompFilterDenyRead") == "blocked"
    # And the control that tells a broken DACL write from a working read deny.
    assert _row(prediction, "Studio-RSOP-CompFilterAllow") == "applied"


def test_the_apply_cell_and_the_group_deny_are_carried_by_a_user_scope_scenario() -> None:
    """Both remaining rows, on a scenario that already pays for the group."""
    prediction = _predict("user-security-filtering-deny")

    # The off-diagonal APPLY cell: the deny names the COMPUTER, the user side
    # applies anyway, and it wins the conflict.
    assert _row(prediction, "Studio-RSOP-FilterDenyApplyComp") == "applied"
    assert _winner(prediction, "Filter") == "Studio-RSOP-FilterDenyApplyComp"

    # The group-matched deny: blocked THROUGH the token, not by name.
    assert _row(prediction, "Studio-RSOP-FilterDenyGroup") == "blocked"
    assert prediction["user_group_memberships"] == [build_rsop_candidate.GROUP_NAME]

    # The certified rows are unchanged by the additions.
    assert _row(prediction, "Studio-RSOP-FilterDeny") == "blocked"
    assert _row(prediction, "Studio-RSOP-FilterNested") == "applied"
    assert _row(prediction, "Studio-RSOP-UserControl") == "applied"


def test_the_group_deny_is_not_matched_by_name() -> None:
    """The control for the row above, and the reason it is worth running.

    A deny that named the USER would block too, so "blocked" alone does not
    prove the group was consulted. The group is the only identity this filter
    names, so blocking can only have come through the membership.
    """
    scenario = _scenario("user-security-filtering-deny")
    row = next(
        gpo for gpo in scenario.gpos if gpo.name == "Studio-RSOP-FilterDenyGroup"
    )
    denies = [f for f in row.filters if f.kind == "deny"]
    assert [f.principal_key for f in denies] == ["group"]
    assert any(f.principal_key == "user" and f.kind == "apply" for f in row.filters)


def test_a_scenario_that_names_the_user_declares_it() -> None:
    """`names_user` must agree with the filters, not merely be set.

    The flag decides whether `--user-name` is required or refused, so a scenario
    whose rows name the user without declaring it would build a candidate whose
    deny matches nobody -- and a scenario that declares it without naming the
    user would author a principal into the topology that nothing asserts on.
    Both directions are checked, over the whole corpus, because either produces
    a run that looks like a result.
    """
    for scenario_id, scenario in build_rsop_candidate.SCENARIOS.items():
        names_user_in_a_filter = any(
            planned_filter.principal_key == "user"
            for gpo in scenario.gpos
            for planned_filter in gpo.filters
        )
        if scenario.scope == "user":
            # The user side resolves the principal regardless; `names_user` is
            # about computer-scope scenarios only.
            assert not scenario.names_user, scenario_id
            continue
        assert scenario.names_user == names_user_in_a_filter, scenario_id


def test_the_computer_group_deny_is_carried_by_a_computer_scope_scenario() -> None:
    """WI-054's row: a deny matched through a group in the CLIENT'S machine token.

    The model says BLOCKED, resolving the membership it was told about through
    `computer_group_memberships`. Whether Windows agrees about a machine token
    is what the estate run measures -- and the run only counts because the
    client was rebooted after the group was authored, which is why this
    scenario is the one that pays for the reboot.
    """
    prediction = _predict("computer-security-filtering-group-deny")

    # The membership rides on the COMPUTER side and on nothing else -- the
    # mirror image of the user-scope nesting row.
    assert prediction["computer_group_memberships"] == [build_rsop_candidate.GROUP_NAME]
    assert prediction["user_group_memberships"] == []

    # Blocked THROUGH the token, not by name: the group is the only identity
    # the deny names, exactly as in the user-scope control below.
    assert _row(prediction, "Studio-RSOP-CompFilterDenyGroup") == "blocked"
    # And the control that says the DACL write and the membership did not
    # simply block everything the run authored.
    assert _row(prediction, "Studio-RSOP-CompFilterAllow") == "applied"
    assert _winner(prediction, "Filter") == "Studio-RSOP-CompFilterAllow"


def test_the_computer_group_deny_is_not_matched_by_name() -> None:
    """The control for the row above, same reasoning as the user-scope one."""
    scenario = _scenario("computer-security-filtering-group-deny")
    row = next(
        gpo for gpo in scenario.gpos if gpo.name == "Studio-RSOP-CompFilterDenyGroup"
    )
    denies = [f for f in row.filters if f.kind == "deny"]
    assert [f.principal_key for f in denies] == ["group"]
    assert any(f.principal_key == "computer" and f.kind == "apply" for f in row.filters)


def test_every_group_scenario_declares_which_principal_joins_it() -> None:
    """`group_principal` must agree with the memberships the prediction carries.

    The flag decides which account the authoring half adds to the disposable
    group AND which side's token the observation half corroborates, so a
    scenario that needs a group without declaring the principal would author a
    member nobody told the model about -- and one that declares it without
    needing the group would pay a re-session or a reboot for nothing. Both
    directions are checked over the whole corpus.
    """
    for scenario_id, scenario in build_rsop_candidate.SCENARIOS.items():
        if not scenario.needs_group:
            assert scenario.group_principal == "user" or scenario.group_principal, (
                scenario_id
            )
            continue
        assert scenario.group_principal in ("user", "computer"), scenario_id
        prediction = cast(
            dict[str, Any],
            build_rsop_candidate.prediction_document(
                scenario, DOMAIN, SITE, COMPUTER, USER
            ),
        )
        if scenario.group_principal == "computer":
            assert prediction["computer_group_memberships"] == [
                build_rsop_candidate.GROUP_NAME
            ], scenario_id
            assert prediction["user_group_memberships"] == [], scenario_id
        else:
            assert prediction["user_group_memberships"] == [
                build_rsop_candidate.GROUP_NAME
            ], scenario_id
            assert prediction["computer_group_memberships"] == [], scenario_id
