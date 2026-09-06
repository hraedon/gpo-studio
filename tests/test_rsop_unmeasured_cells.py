"""WI-049: the corpus must actually carry the cells the register says it will.

Three regions of `_gpo_filter_status` are answered by argument rather than by
measurement, all in the over-promising direction -- the model says a GPO applies
where it previously said it was blocked:

* a READ deny naming the USER, resolved on the computer side;
* an APPLY deny naming the COMPUTER, resolved on the user side;
* any deny that matches THROUGH A GROUP rather than by name, which is
  unit-tested in both directions and measured in neither.

`TestTheUnmeasuredCellsArePinned` in `test_rsop.py` pins the *answers* so a
silent flip is visible. This file pins something different and easier to lose:
that the estate corpus contains a row for each, on a scenario the lanes already
run, so the next batch measures them without anybody remembering to.

That is the failure this project keeps having. WI-025 was minted in a design
paragraph and rediscovered a month later; a plan header said `proposed` while
implemented; the capability matrix said `failed` while supported. Every one was
a document that nothing checked. The register states a closing condition for
WI-049 -- these are the parts of it a test can hold.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, cast

from gpo_studio.rsop import query_reaches_a_reasoned_cell

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


def test_every_scenario_that_reaches_a_reasoned_cell_says_so_in_its_prediction() -> None:
    """The prediction records the model's own disclosure, so a verdict carries it.

    `query_reaches_a_reasoned_cell` is what the API uses to tell a caller its
    answer rests on an unmeasured region. Writing the same answer into the
    prediction means the committed verdict states it in the run's own words --
    which is what makes a run citable when the item is closed, rather than a
    scenario name somebody has to recognise.
    """
    reaching = set()
    for scenario_id, scenario in build_rsop_candidate.SCENARIOS.items():
        user = USER if (scenario.scope == "user" or scenario.names_user) else ""
        query = build_rsop_candidate.build_query(scenario, DOMAIN, SITE, COMPUTER, user)
        expected = query_reaches_a_reasoned_cell(query)
        prediction = cast(
            dict[str, Any],
            build_rsop_candidate.prediction_document(
                scenario, DOMAIN, SITE, COMPUTER, user
            ),
        )
        assert prediction["reaches_reasoned_cell"] is expected, scenario_id
        if expected:
            reaching.add(scenario_id)

    # Not a tautology: the corpus must contain both kinds, or the field records
    # a constant and the disclosure means nothing.
    #
    # `user-security-filtering-read-deny` is in the set and is not one of the
    # two rows this item adds. Its row A denies READ to the user, and the
    # predicate is a property of the QUERY rather than of one side: the same
    # topology answered on the computer side reaches the reasoned cell, even
    # though the user side's answer is measured (row A of
    # rsop-user-observe-20260806165543-8004). Flagging it is honest -- an
    # exception carved out per scenario would be the model deciding which of its
    # own answers to disclose.
    assert reaching == {
        "computer-security-filtering-deny-read",
        "user-security-filtering-deny",
        "user-security-filtering-read-deny",
    }, sorted(reaching)
    assert len(reaching) < len(build_rsop_candidate.SCENARIOS)
