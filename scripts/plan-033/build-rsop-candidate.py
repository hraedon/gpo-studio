#!/usr/bin/env python3
"""Build the Plan 033 WP-6B RSOP candidate: a topology, and a prediction about it.

WP-6B asks one question: **is `rsop.py` right?** Not "does Studio author a
working GPO" -- WP-1B and WP-2 own that. So the topology below is authored
*natively* on the member server (`New-GPO`, `Set-GPRegistryValue`,
`New-GPLink`), and Studio's only contribution is the prediction. That
separation is deliberate: if the lane authored through Studio's writer, a
writer defect and a model defect would be indistinguishable in the evidence.

This script emits three files, and the order they are produced in is part of
the experiment:

``topology.json``   what the authoring half creates on the member server.
``prediction.json`` what ``compute_rsop()`` says will happen, computed here,
                    **before** anything is applied and committed as an input
                    artifact. A prediction produced after the observation is
                    not a prediction.
``expected.json``   run metadata: what each row isolates, and which row is the
                    control.

## Scope: computer only

Ruled 2026-08-03 (see ``docs/plan-033/rsop-oracle-design.md``). Every setting
here is HKLM and every principal is the computer account. The estate has never
had an interactive logon, so a user-scope assertion could not be observed even
if it were authored -- and an assertion that cannot be observed does not belong
in a lane that claims to have tested something. User scope is WP-9.

## The topology, and what each GPO is for

Derived from the ``lsdou-precedence`` corpus scenario, which is the only
authored rsop-topology scenario that is wholly computer-scope. Five GPOs write
to ``HKLM\\Software\\Policies\\StudioLab``, plus one control:

    GPO                     linked to        writes                  isolates
    ----------------------  ---------------  ----------------------  --------------
    Studio-RSOP-Site        the site         Precedence, SiteOnly    the S in LSDOU
    Studio-RSOP-Domain      domain root      Precedence              the D
    Studio-RSOP-Parent      parent OU        Precedence              the first O
    Studio-RSOP-ChildA      child OU (ord 1) Precedence              link order
    Studio-RSOP-ChildB      child OU (ord 2) Precedence, ChildBOnly  link order
    Studio-RSOP-Control     child OU (ord 3) Control                 DID IT RUN?

Expected winners: ``Precedence=childA`` (child OU applies last; within it,
link order 1 applies after order 2 and wins), ``SiteOnly=1`` and
``ChildBOnly=1`` (non-conflicting values from losing GPOs still apply).

## Why there is a control row

The endpoint lane learned this the expensive way. ``Control=present`` is
written by exactly one GPO, conflicts with nothing, and is filtered by
nothing. It is not a test of Studio at all -- it is the test of whether the
experiment happened. If ``Control`` is absent, the client did not process
policy, and every other row in the run means nothing; the finalizer reports
**inconclusive** rather than reading the silence as "Studio predicted wrong".
Without it, a client that failed to apply any policy produces exactly the
evidence signature of a total model failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gpo_studio.model import GPO, RegistrySetting  # noqa: E402
from gpo_studio.rsop import RsopQuery, RsopTarget, compute_rsop  # noqa: E402
from gpo_studio.som import SomLink, SomNode  # noqa: E402

# The lane writes only here. A single key under Software\Policies keeps the
# whole experiment inside the managed-policy branch, which the CSE removes when
# the GPO stops applying -- so teardown does not depend on the harness
# remembering to delete individual values.
POLICY_KEY = r"Software\Policies\StudioLab"


@dataclass(frozen=True)
class RawValue:
    """A machine value written outside the lane's own policy key.

    Only loopback needs this so far. Loopback is not a setting Studio resolves
    -- it is the switch that decides HOW the user side is resolved at all, and
    ``rsop.py`` models it as a property of the target rather than as a registry
    value. So these values are authored into the GPO and are deliberately
    ABSENT from the model query: feeding them in as settings would make
    ``UserPolicyMode`` show up as a predicted winner, and the lane would then be
    checking that Studio can echo back a number it was handed.
    """

    key: str
    value_name: str
    value: int | str
    value_type: str  # DWord | String
    why: str


@dataclass(frozen=True)
class PlannedGpo:
    """One GPO in the topology: where it links, and what it writes."""

    name: str
    guid: str
    scope: str  # site | domain | ou
    scope_key: str  # symbolic; the authoring half resolves it to a real DN
    order: int
    values: dict[str, str]
    isolates: str
    enforced: bool = False
    link_enabled: bool = True
    #: Machine values outside the policy key, excluded from the prediction.
    raw_values: tuple[RawValue, ...] = ()
    #: User-side values. Authored so the GPO genuinely carries a user side --
    #: which is what makes "the computer side still applies" a real test rather
    #: than a statement about an empty GPO. WP-6 never *asserts* on them; that
    #: assertion is WP-9's (see the user-side-disabled scenario).
    user_values: dict[str, str] = field(default_factory=dict)
    user_enabled: bool = True


@dataclass(frozen=True)
class PlannedOu:
    """One OU in the disposable tree."""

    key: str
    name: str
    parent_key: str  # "domain" for the root of the tree
    block_inheritance: bool = False


@dataclass(frozen=True)
class Scenario:
    """A corpus scenario expressed as something the lane can build and predict.

    ``scope`` decides three things at once and they must not be allowed to
    drift apart: which side of the model the prediction is emitted from, which
    hive the observation reads, and which principal the lane moves into the
    disposable tree. A scenario that predicted the user side while the lane
    moved only the computer would be comparing two different experiments.
    """

    scenario_id: str
    ous: tuple[PlannedOu, ...]
    gpos: tuple[PlannedGpo, ...]
    target_ou_key: str
    control_gpo: str
    control_value_name: str
    scope: str = "computer"  # computer | user
    #: Where the USER object is placed. Distinct from ``target_ou_key`` on
    #: purpose: loopback is only observable when the user and the computer sit
    #: in different containers with different policy linked to each, because
    #: merge and replace are both statements about preferring the COMPUTER's
    #: location over the USER's. Put them in one container and every loopback
    #: mode produces the same answer.
    user_ou_key: str = ""
    loopback_mode: str = "disabled"  # disabled | merge | replace


OU_ROOT = "StudioRsop"
OU_PARENT = "StudioRsopParent"
OU_CHILD = "StudioRsopChild"

#: A plain three-level tree, used where the scenario does not need anything
#: special of its containers.
PLAIN_OUS: tuple[PlannedOu, ...] = (
    PlannedOu(key="root", name=OU_ROOT, parent_key="domain"),
    PlannedOu(key="parent", name=OU_PARENT, parent_key="root"),
    PlannedOu(key="child", name=OU_CHILD, parent_key="parent"),
)

# Deterministic GUIDs. The authoring half creates real GPOs and reports their
# real GUIDs; these exist so the prediction is computable here, before any of
# them exist. The finalizer matches on NAME, never on these.
LSDOU_PRECEDENCE = Scenario(
    scenario_id="lsdou-precedence",
    ous=PLAIN_OUS,
    target_ou_key="child",
    control_gpo="Studio-RSOP-Control",
    control_value_name="Control",
    gpos=(
        PlannedGpo(
            name="Studio-RSOP-Site",
            guid="00000000-0000-0000-0000-00000000513e",
            scope="site",
            scope_key="site",
            order=1,
            values={"Precedence": "site", "SiteOnly": "1"},
            isolates="site scope applies first and loses conflicts; its unique value still applies",
        ),
        PlannedGpo(
            name="Studio-RSOP-Domain",
            guid="00000000-0000-0000-0000-0000000d0a11",
            scope="domain",
            scope_key="domain",
            order=1,
            values={"Precedence": "domain"},
            isolates="domain scope applies after site and before OUs",
        ),
        PlannedGpo(
            name="Studio-RSOP-Parent",
            guid="00000000-0000-0000-0000-0000000a4e17",
            scope="ou",
            scope_key="parent",
            order=1,
            values={"Precedence": "parent"},
            isolates="parent OU applies after domain and before child",
        ),
        PlannedGpo(
            name="Studio-RSOP-ChildA",
            guid="00000000-0000-0000-0000-00000000c41d",
            scope="ou",
            scope_key="child",
            order=1,
            values={"Precedence": "childA"},
            isolates="link order 1 applies last within a container and wins",
        ),
        PlannedGpo(
            name="Studio-RSOP-ChildB",
            guid="00000000-0000-0000-0000-00000000c42d",
            scope="ou",
            scope_key="child",
            order=2,
            values={"Precedence": "childB", "ChildBOnly": "1"},
            isolates="link order 2 loses the conflict; its unique value still applies",
        ),
        PlannedGpo(
            name="Studio-RSOP-Control",
            guid="00000000-0000-0000-0000-0000000c04f0",
            scope="ou",
            scope_key="child",
            order=3,
            values={"Control": "present"},
            isolates="CONTROL: unconflicted, unfiltered. Absent => the experiment did not run.",
        ),
    ),
)

#: WI-029. The computer-scope half of the corpus scenario, which was blocked in
#: full for a single HKCU assertion. That assertion was RELOCATED to the WP-9
#: `user-side-disabled` scenario rather than deleted -- a criterion that is
#: dropped instead of moved is how an unverified claim becomes an invisible one.
#:
#: Note that Studio-RSOP-UserSideOff still authors its user-side value and still
#: has its user side disabled. Removing them would have made the surviving
#: assertion ("the computer side still applies") a statement about a GPO with no
#: user side at all, which tests nothing.
DISABLED_BLOCK_ENFORCED = Scenario(
    scenario_id="disabled-block-enforced",
    ous=(
        PlannedOu(key="root", name=OU_ROOT, parent_key="domain"),
        PlannedOu(key="parent", name=OU_PARENT, parent_key="root"),
        PlannedOu(key="child", name=OU_CHILD, parent_key="parent", block_inheritance=True),
    ),
    target_ou_key="child",
    control_gpo="Studio-RSOP-Control",
    control_value_name="Control",
    gpos=(
        PlannedGpo(
            name="Studio-RSOP-DomainPlain",
            guid="00000000-0000-0000-0000-00000000d791",
            scope="domain",
            scope_key="domain",
            order=1,
            values={"Block": "domainPlain"},
            isolates="non-enforced domain link must NOT reach a child that blocks inheritance",
        ),
        PlannedGpo(
            name="Studio-RSOP-DomainEnforced",
            guid="00000000-0000-0000-0000-00000000d7e2",
            scope="domain",
            scope_key="domain",
            order=2,
            enforced=True,
            values={"Block": "domainEnforced", "EnforcedOnly": "1"},
            isolates="enforced link defeats the block AND applies last, so it wins the conflict",
        ),
        PlannedGpo(
            name="Studio-RSOP-ChildGPO",
            guid="00000000-0000-0000-0000-00000000c41e",
            scope="ou",
            scope_key="child",
            order=1,
            values={"Block": "child", "Side": "computer"},
            isolates="linked AT the blocking container, so the block does not affect it",
        ),
        PlannedGpo(
            name="Studio-RSOP-UserSideOff",
            guid="00000000-0000-0000-0000-000000005500",
            scope="ou",
            scope_key="child",
            order=2,
            values={"MachineVal": "1"},
            user_values={"UserVal": "1"},
            user_enabled=False,
            isolates="a disabled USER side must not stop the COMPUTER side applying",
        ),
        PlannedGpo(
            name="Studio-RSOP-DisabledLink",
            guid="00000000-0000-0000-0000-00000000d15a",
            scope="ou",
            scope_key="child",
            order=3,
            link_enabled=False,
            values={"DisabledLinkVal": "1"},
            isolates="a disabled link must make its GPO vanish entirely",
        ),
        PlannedGpo(
            name="Studio-RSOP-Control",
            guid="00000000-0000-0000-0000-0000000c04f0",
            scope="ou",
            scope_key="child",
            order=4,
            values={"Control": "present"},
            isolates="CONTROL: unconflicted, unfiltered. Absent => the experiment did not run.",
        ),
    ),
)

# ---------------------------------------------------------------------------
# WP-9: user scope and loopback
# ---------------------------------------------------------------------------
#
# These became runnable when the estate gained a scripted interactive logon
# (windows-evidence-lab, scripts/enable_lab_autologon.ps1). Everything below
# asserts on HKCU under the logged-on principal.

OU_USER = "StudioRsopUser"

#: Two branches under one root: the computer in one, the user in the other.
#: Loopback needs them separated, and using the same tree for the non-loopback
#: user scenario keeps one shape across the whole work package.
SPLIT_OUS: tuple[PlannedOu, ...] = (
    PlannedOu(key="root", name=OU_ROOT, parent_key="domain"),
    PlannedOu(key="parent", name=OU_PARENT, parent_key="root"),
    PlannedOu(key="child", name=OU_CHILD, parent_key="parent"),
    PlannedOu(key="user", name=OU_USER, parent_key="root"),
)

#: The corpus `user-side-disabled` scenario. One claim, and a control that makes
#: its absence mean something: a GPO whose user side is disabled contributes
#: nothing to the user scope, while its computer side still applies.
#: The user sits in the SAME container as the computer here, which is the
#: corpus scenario's own shape: its computer-side control ("MachineVal still
#: applies") is only meaningful if the GPO scopes the computer too. The split
#: tree below exists for loopback, where separation is the whole point.
USER_SIDE_DISABLED = Scenario(
    scenario_id="user-side-disabled",
    scope="user",
    ous=PLAIN_OUS,
    target_ou_key="child",
    user_ou_key="child",
    control_gpo="Studio-RSOP-UserControl",
    control_value_name="Control",
    gpos=(
        PlannedGpo(
            name="Studio-RSOP-UserSideOff",
            guid="00000000-0000-0000-0000-000000005500",
            scope="ou",
            scope_key="child",
            order=1,
            user_enabled=False,
            values={"MachineVal": "1"},
            user_values={"UserVal": "1"},
            isolates="a disabled user side must contribute NOTHING to the user scope",
        ),
        PlannedGpo(
            name="Studio-RSOP-UserOn",
            guid="00000000-0000-0000-0000-000000005501",
            scope="ou",
            scope_key="child",
            order=2,
            values={},
            user_values={"UserWinner": "userOn"},
            isolates=(
                "the same container, user side ENABLED: separates 'disabled sides work' "
                "from 'no user policy processed at all'"
            ),
        ),
        PlannedGpo(
            name="Studio-RSOP-UserControl",
            guid="00000000-0000-0000-0000-000000005502",
            scope="ou",
            scope_key="child",
            order=3,
            values={},
            user_values={"Control": "present"},
            isolates="CONTROL: unconflicted, unfiltered. Absent => the experiment did not run.",
        ),
    ),
)


def _loopback_scenario(mode: str, guid_tail: str) -> Scenario:
    """Build the merge or replace loopback scenario.

    They differ in exactly one authored byte -- ``UserPolicyMode`` -- and in
    what they expect. Writing them as one function keeps that true: a
    hand-copied pair would eventually diverge somewhere that is not the thing
    under test, and the difference between "merge lost a value" and "the two
    topologies were not the same" would be unrecoverable from the evidence.
    """
    # 1 = merge, 2 = replace.
    #
    # This was written the other way round, and Windows is what corrected it.
    # The first replace run authored UserPolicyMode=1 and event 5311 reported
    # that the pass had run with loopback MERGE -- so the lane compared nothing
    # and reported `inconclusive`, naming the mismatch.
    #
    # Worth being precise about what that avoided. Under merge the
    # user-location GPO still applies, so `UserOnly` was present; the replace
    # prediction says it must be absent. A lane without the 5311 control would
    # have read that as "rsop.py gets loopback replace wrong" -- a confident,
    # well-evidenced, entirely false finding about the model, caused by a
    # constant in the harness. It is the exact failure the control was built
    # for, and it fired on the first run that could trigger it.
    user_policy_mode = 1 if mode == "merge" else 2
    return Scenario(
        scenario_id=f"loopback-{mode}",
        scope="user",
        ous=SPLIT_OUS,
        target_ou_key="child",
        user_ou_key="user",
        loopback_mode=mode,
        control_gpo="Studio-RSOP-UserControl",
        control_value_name="Control",
        gpos=(
            PlannedGpo(
                name="Studio-RSOP-Loopback",
                guid=f"00000000-0000-0000-0000-00000000{guid_tail}",
                scope="ou",
                scope_key="child",
                order=1,
                values={"LoopbackOn": mode},
                raw_values=(
                    RawValue(
                        key=r"Software\Policies\Microsoft\Windows\System",
                        value_name="UserPolicyMode",
                        value=user_policy_mode,
                        value_type="DWord",
                        why=(
                            "'Configure user Group Policy loopback processing mode' "
                            f"= {mode}. A COMPUTER-side value that changes how the "
                            "USER side is resolved."
                        ),
                    ),
                ),
                isolates=f"turns loopback {mode} on for the client",
            ),
            PlannedGpo(
                name="Studio-RSOP-CompLocation",
                guid="00000000-0000-0000-0000-00000000c10c",
                scope="ou",
                scope_key="child",
                order=2,
                values={},
                user_values={"Loop": "computerLocation", "CompOnly": "1"},
                isolates=(
                    "user settings in the COMPUTER's container: applied at all only "
                    "because loopback is on, and under merge they win the conflict"
                ),
            ),
            PlannedGpo(
                name="Studio-RSOP-UserLocation",
                guid="00000000-0000-0000-0000-000000075e12",
                scope="ou",
                scope_key="user",
                order=1,
                values={},
                user_values={"Loop": "userLocation", "UserOnly": "1"},
                isolates=(
                    "user settings in the USER's container: kept under merge, "
                    "discarded entirely under replace"
                ),
            ),
            PlannedGpo(
                name="Studio-RSOP-UserControl",
                guid="00000000-0000-0000-0000-00000000c04f",
                scope="ou",
                scope_key="child",
                order=3,
                values={},
                user_values={"Control": "present"},
                isolates=(
                    "CONTROL: in the COMPUTER's container, so it applies under either "
                    "loopback mode. Absent => the experiment did not run."
                ),
            ),
        ),
    )


#: Under merge, user-location settings apply first and computer-location
#: settings apply second, so ``Loop=computerLocation`` and both unique values
#: survive. Under replace, the user's own container is not consulted at all:
#: ``UserOnly`` must be ABSENT, which is the assertion that distinguishes
#: replace from merge and is only readable because the control row proves the
#: run happened.
LOOPBACK_MERGE = _loopback_scenario("merge", "10e6")
LOOPBACK_REPLACE = _loopback_scenario("replace", "10e7")

SCENARIOS: dict[str, Scenario] = {
    LSDOU_PRECEDENCE.scenario_id: LSDOU_PRECEDENCE,
    DISABLED_BLOCK_ENFORCED.scenario_id: DISABLED_BLOCK_ENFORCED,
    USER_SIDE_DISABLED.scenario_id: USER_SIDE_DISABLED,
    LOOPBACK_MERGE.scenario_id: LOOPBACK_MERGE,
    LOOPBACK_REPLACE.scenario_id: LOOPBACK_REPLACE,
}


def _domain_dn(domain: str) -> str:
    return ",".join(f"DC={part}" for part in domain.split("."))


def _scope_dns(scenario: Scenario, domain: str, site_name: str) -> dict[str, str]:
    """Symbolic scope key -> DN, for the scenario's own OU tree."""
    domain_dn = _domain_dn(domain)
    dns = {
        "site": f"CN={site_name},CN=Sites,CN=Configuration,{domain_dn}",
        "domain": domain_dn,
    }
    for ou in scenario.ous:
        parent_dn = dns[ou.parent_key]
        dns[ou.key] = f"OU={ou.name},{parent_dn}"
    return dns


def _settings(gpo: PlannedGpo) -> tuple[RegistrySetting, ...]:
    computer = tuple(
        RegistrySetting(
            id=f"{gpo.name}:computer:{value_name}",
            side="computer",
            hive="HKLM",
            key=POLICY_KEY,
            value_name=value_name,
            registry_type="REG_SZ",
            value=value,
        )
        for value_name, value in sorted(gpo.values.items())
    )
    user = tuple(
        RegistrySetting(
            id=f"{gpo.name}:user:{value_name}",
            side="user",
            hive="HKCU",
            key=POLICY_KEY,
            value_name=value_name,
            registry_type="REG_SZ",
            value=value,
        )
        for value_name, value in sorted(gpo.user_values.items())
    )
    return computer + user


def build_query(
    scenario: Scenario,
    domain: str,
    site_name: str,
    computer_name: str,
    user_name: str = "",
) -> RsopQuery:
    """Build the RSOP query for the planned topology.

    The SOM tree mirrors exactly what the authoring half will create. If the two
    ever drift, the prediction describes a topology that does not exist -- which
    is why the authoring half copies its input into its own work dir and the
    finalizer compares the two byte for byte.
    """
    dns = _scope_dns(scenario, domain, site_name)

    gpos = tuple(
        GPO(
            guid=planned.guid,
            name=planned.name,
            computer_enabled=True,
            user_enabled=planned.user_enabled,
            settings=_settings(planned),
            domain=domain,
        )
        for planned in scenario.gpos
    )

    def links_for(scope_key: str) -> tuple[SomLink, ...]:
        return tuple(
            SomLink(
                gpo_guid=planned.guid,
                scope=planned.scope,  # type: ignore[arg-type]
                scope_dn=dns[planned.scope_key],
                enabled=planned.link_enabled,
                enforced=planned.enforced,
                order=planned.order,
            )
            for planned in scenario.gpos
            if planned.scope_key == scope_key
        )

    som_nodes: list[SomNode] = [
        SomNode(
            dn=dns["site"],
            name=site_name,
            scope="site",
            parent_dn="",
            links=links_for("site"),
        ),
        SomNode(
            dn=dns["domain"],
            name=domain,
            scope="domain",
            parent_dn="",
            links=links_for("domain"),
        ),
    ]
    for ou in scenario.ous:
        som_nodes.append(
            SomNode(
                dn=dns[ou.key],
                name=ou.name,
                scope="ou",
                parent_dn=dns[ou.parent_key],
                block_inheritance=ou.block_inheritance,
                links=links_for(ou.key),
            )
        )

    return RsopQuery(
        query_id=f"wp6b-{scenario.scenario_id}",
        mode="planning",
        target=RsopTarget(
            computer_name=computer_name,
            # The computer's own object DN -- the shape a real directory returns,
            # and the shape an operator-facing caller would supply.
            #
            # WI-026 was the first thing this lane found, before it ran once:
            # this exact value used to make compute_precedence resolve nothing
            # and compute_rsop return an empty result -- no applied GPOs, no
            # winners -- for a machine Windows applies six GPOs to. The lane
            # therefore ran its first certification against the CONTAINER DN,
            # recorded as an adapter choice, because a lane that fed the model an
            # object DN would have predicted "nothing applies", observed six
            # applied GPOs, and reported a spectacular model failure that was
            # really a caller error.
            #
            # WI-026 is now fixed (an unresolved DN walks up to its nearest
            # ancestor in the SOM tree), so the lane feeds the honest input and
            # the oracle checks the fix rather than working around it.
            computer_dn=f"CN={computer_name},{dns[scenario.target_ou_key]}",
            # The user half, present only for user-scope scenarios. Same object-DN
            # shape as the computer, resolved by the same WI-026 ancestor walk.
            user_name=user_name,
            user_dn=(
                f"CN={user_name},{dns[scenario.user_ou_key]}"
                if scenario.scope == "user" and user_name
                else ""
            ),
            # Loopback is a property of the target here, and a machine registry
            # value on the estate. The lane authors the value and tells the model
            # the mode; nothing derives one from the other, so a lane that failed
            # to make loopback take effect cannot quietly become a model that was
            # asked to predict the wrong thing. Windows' own event 5311 is what
            # settles which mode actually ran.
            loopback_mode=scenario.loopback_mode,  # type: ignore[arg-type]
            site_name=site_name,
            domain=domain,
        ),
        som_nodes=tuple(som_nodes),
        gpos=gpos,
    )


def prediction_document(
    scenario: Scenario,
    domain: str,
    site_name: str,
    computer_name: str,
    user_name: str = "",
) -> dict[str, Any]:
    """Run ``compute_rsop`` and normalize it to the shape the finalizer diffs.

    ONE SIDE IS EMITTED, the one the scenario is about. A computer-scope
    scenario emits ``computer_settings`` and a user-scope scenario emits
    ``user_settings``; the other side is dropped rather than carried along,
    because a predicted winner the lane does not observe would sit in the
    verdict looking exactly like a tested claim.

    ``applied_gpos`` IS NOT A PER-SIDE SET, and the verdict says so. The model
    reports one ``is_applied`` per GPO, meaning "applied on at least one side",
    so on a user-scope scenario whose GPOs also scope the computer the two are
    not the same question. The finalizer therefore gates on the winners --
    which is what the corpus scenarios actually assert -- and records the
    applied sets as observation rather than as a check. See WI-032.
    """
    result = compute_rsop(
        build_query(scenario, domain, site_name, computer_name, user_name)
    )

    applied = sorted(g.gpo_name for g in result.gpo_results if g.is_applied)
    denied = sorted(
        (
            {"gpo": g.gpo_name, "reasons": sorted(g.filtering_reasons)}
            for g in result.gpo_results
            if not g.is_applied
        ),
        key=lambda row: str(row["gpo"]),
    )

    resolved = (
        result.user_settings if scenario.scope == "user" else result.computer_settings
    )
    default_hive = "HKCU" if scenario.scope == "user" else "HKLM"
    winners = sorted(
        (
            {
                "hive": setting.hive or default_hive,
                "key": setting.key,
                "value_name": setting.value_name,
                "value": setting.effective_value,
                "winning_gpo": setting.winning_gpo_name,
                "overridden_by": sorted(setting.overridden_by),
            }
            for setting in resolved
        ),
        key=lambda row: str(row["value_name"]),
    )

    return {
        "query_id": result.query_id,
        "scenario_id": scenario.scenario_id,
        "scope": scenario.scope,
        "loopback_mode": scenario.loopback_mode,
        "target": {
            "computer_name": result.target.computer_name,
            "user_name": result.target.user_name,
            "site_name": result.target.site_name,
            "domain": result.target.domain,
        },
        "applied_gpos": applied,
        "denied_gpos": denied,
        "winners": winners,
        "warnings": sorted(result.warnings),
    }


def topology_document(
    scenario: Scenario,
    domain: str,
    site_name: str,
    computer_name: str,
    user_name: str = "",
) -> dict[str, Any]:
    """The authoring instructions, symbolic where the estate must resolve them.

    Each OU names its parent SYMBOLICALLY as well as by DN. The authoring half
    used to treat the OU list as a chain, each entry parented to the one before
    it, which is true of the computer-scope tree and false of the split tree
    loopback needs -- there the user branch hangs off the root beside the
    computer branch. A chain would have built the right OUs in the wrong shape
    and produced a topology the prediction does not describe.
    """
    dns = _scope_dns(scenario, domain, site_name)
    return {
        "scenario_id": scenario.scenario_id,
        "scope": scenario.scope,
        "loopback_mode": scenario.loopback_mode,
        "domain": domain,
        "site_name": site_name,
        "endpoint_computer": computer_name,
        "endpoint_user": user_name,
        "policy_key": POLICY_KEY,
        "ous": [
            {
                "key": ou.key,
                "name": ou.name,
                "dn": dns[ou.key],
                "parent_key": ou.parent_key,
                "parent_dn": dns[ou.parent_key],
                "block_inheritance": ou.block_inheritance,
            }
            for ou in scenario.ous
        ],
        "target_ou_dn": dns[scenario.target_ou_key],
        "target_ou_key": scenario.target_ou_key,
        "user_ou_dn": dns[scenario.user_ou_key] if scenario.user_ou_key else "",
        "user_ou_key": scenario.user_ou_key,
        "gpos": [
            {
                "name": planned.name,
                "scope": planned.scope,
                "scope_key": planned.scope_key,
                "scope_dn": dns[planned.scope_key],
                "order": planned.order,
                "enforced": planned.enforced,
                "link_enabled": planned.link_enabled,
                "user_enabled": planned.user_enabled,
                "values": [
                    {"value_name": name, "value": value, "type": "String"}
                    for name, value in sorted(planned.values.items())
                ],
                "user_values": [
                    {"value_name": name, "value": value, "type": "String"}
                    for name, value in sorted(planned.user_values.items())
                ],
                # Written to their own key, with their own type, and absent from
                # the prediction on purpose (see RawValue).
                "raw_values": [
                    {
                        "key": raw.key,
                        "value_name": raw.value_name,
                        "value": raw.value,
                        "type": raw.value_type,
                        "why": raw.why,
                    }
                    for raw in planned.raw_values
                ],
            }
            for planned in scenario.gpos
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--scenario",
        default="lsdou-precedence",
        choices=sorted(SCENARIOS),
        help="which corpus scenario to build a topology and prediction for",
    )
    parser.add_argument(
        "--domain",
        required=True,
        help="AD DNS domain of the estate, e.g. ad.labdomain.dev",
    )
    parser.add_argument(
        "--site-name",
        default="Default-First-Site-Name",
        help="AD site the endpoint is in; a site-scope GPO links here",
    )
    parser.add_argument(
        "--computer-name",
        required=True,
        help="The endpoint computer's sAMAccountName without the trailing $",
    )
    parser.add_argument(
        "--user-name",
        default="",
        help=(
            "The interactively logged-on principal's sAMAccountName. Required "
            "for user-scope scenarios and refused for computer-scope ones."
        ),
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    scenario = SCENARIOS[args.scenario]

    # A user-scope scenario with no principal would silently predict an empty
    # user side -- no applied GPOs and no winners -- which is also what a
    # correct model produces for a user nothing applies to. Two very different
    # situations with one evidence signature is the shape this project keeps
    # having to design out, so it is refused at the door instead.
    if scenario.scope == "user" and not args.user_name:
        parser.error(f"--user-name is required for the user-scope scenario {scenario.scenario_id}")
    if scenario.scope != "user" and args.user_name:
        parser.error(
            f"--user-name was given for {scenario.scenario_id}, which is a "
            "computer-scope scenario; it would be authored into the topology "
            "and asserted on by nothing"
        )

    topology = topology_document(
        scenario, args.domain, args.site_name, args.computer_name, args.user_name
    )
    prediction = prediction_document(
        scenario, args.domain, args.site_name, args.computer_name, args.user_name
    )
    expected = {
        "scenario_id": scenario.scenario_id,
        "scope": scenario.scope,
        "loopback_mode": scenario.loopback_mode,
        "control_gpo": scenario.control_gpo,
        "control_value_name": scenario.control_value_name,
        "policy_key": POLICY_KEY,
        "endpoint_user": args.user_name,
        "rows": [
            {"gpo": planned.name, "isolates": planned.isolates} for planned in scenario.gpos
        ],
    }

    for name, document in (
        ("topology.json", topology),
        ("prediction.json", prediction),
        ("expected.json", expected),
    ):
        (args.output_dir / name).write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    winners = ", ".join(
        f"{row['value_name']}={row['value']}({row['winning_gpo']})" for row in prediction["winners"]
    )
    denied = ", ".join(
        f"{row['gpo']}({'/'.join(row['reasons'])})" for row in prediction["denied_gpos"]
    )
    print(f"built rsop candidate for {scenario.scenario_id} "
          f"({len(scenario.gpos)} GPOs) in {args.output_dir}")
    print(f"  predicted winners: {winners}")
    if denied:
        print(f"  predicted denied:  {denied}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
