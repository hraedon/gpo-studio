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

from gpo_studio.model import (  # noqa: E402
    GPO,
    RegistrySetting,
    SecurityFilter,
    WmiFilter,
)
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
class WmiIntent:
    """A WMI filter the estate authors, and what it should evaluate to.

    ``expect_true`` is the lane's control dimension. A filter written to be
    TRUE must let its GPO apply; if it does not, the filter was authored wrong
    and the run says nothing about the model. Only once that holds does the
    FALSE row mean anything.
    """

    name: str
    query: str
    #: ``None`` means the filter cannot be evaluated at all -- a query naming a
    #: class that does not exist. The model is then told NOTHING about it, which
    #: leaves it unknown, which is the honest input: nobody has an answer to
    #: supply because there is no answer.
    expect_true: bool | None
    why: str


@dataclass(frozen=True)
class PlannedFilter:
    """One security-filtering intent on a GPO.

    ``kind`` maps to what the estate authors and to what the model is told, and
    the two are NOT the same set -- which is the point of the deny row:

    ``apply``   Read + Apply Group Policy for the principal. Authored with
                Set-GPPermission; expressed to the model as an ``apply``
                SecurityFilter.
    ``read``    Read only, no Apply. The principal can see the GPO and does not
                receive it. Expressed as a ``read`` filter, which the resolver
                correctly treats as not-applying.
    ``deny``    An explicit DENY ace on the Apply Group Policy control right,
                written straight onto the groupPolicyContainer's DACL, and
                expressed to the model as a filter with ``deny=True``. Studio
                could not express this at all until WI-033 was fixed, and the
                run that demonstrated the gap is
                ``rsop-user-observe-20260804065525-9254``.
    ``deny-read``
                An explicit DENY ace on GENERIC READ, beside an intact
                Read + Apply allow. Applying a GPO requires BOTH rights, so
                denying the read is a second, independent way to keep a GPO off
                a target -- and it is the one no scenario has ever exercised
                (WI-040). Expressed to the model as ``permission="read"`` with
                ``deny=True``, which `_gpo_filter_status` currently ignores
                entirely: it inspects only ``permission == "apply"`` rows, in
                both its deny branch and its allow branch.
    """

    principal_key: str  # "user" | "computer" | "group" | "authenticated-users"
    kind: str  # apply | read | deny | deny-read


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
    #: Security filtering. Empty means the GPO keeps the default
    #: Authenticated Users = Read + Apply, which is what makes an unfiltered
    #: control row a control.
    filters: tuple[PlannedFilter, ...] = ()
    #: A WMI filter to author and link. The WQL is written for the estate's
    #: client and is expected to evaluate to the stated truth value there.
    #: **Not given to the model**: `rsop.py` cannot evaluate WQL, and handing
    #: it the query would only let it record that a filter exists -- which it
    #: already does, as a warning.
    wmi_filter: WmiIntent | None = None
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
    #: The lane creates a disposable group and puts the principal in it, so
    #: nesting can be tested without touching any pre-existing group.
    needs_group: bool = False
    #: Set when the scenario is EXPECTED to disagree with Windows, with the
    #: reason. Declared in the candidate so a divergence cannot be explained
    #: after the fact -- and so the finalizer can tell a predicted capability
    #: gap from a surprise.
    expect_finding: str = ""


#: The disposable group used for the nesting case. Symbolic here; the estate
#: stamps it per run. The model is told the principal is a member, and the
#: observation half proves that membership independently from the principal's
#: actual token -- otherwise the prediction rests on an input the estate does
#: not corroborate.
GROUP_NAME = "StudioRsopGroup"

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

# ---------------------------------------------------------------------------
# WP-9: user-side security filtering
# ---------------------------------------------------------------------------
#
# The corpus `security-filtering` scenario, split in two. The split is the
# design: the first three cases are things Studio's model can be TOLD about,
# and the deny case is one it cannot. Running them together would produce a
# single verdict in which a genuine capability gap and three working
# behaviours are indistinguishable.
#
# MS16-072 shapes every row. Since that update user GPOs are retrieved in the
# COMPUTER's security context, so a GPO the computer cannot read does not apply
# to the user however the user is filtered. Every filtered GPO below therefore
# keeps Authenticated Users at READ and only moves Apply -- which is also the
# supported way to security-filter, and the reason the `read` row is a real
# case rather than a contrivance.


def _filtering_gpos(include_deny: bool) -> tuple[PlannedGpo, ...]:
    """The filtering rows, with the deny row optional.

    Every row writes a UNIQUELY named value as well as the shared conflicting
    one. That is not decoration: this lane gates on winners and treats the
    applied-GPO set as advisory (WI-032), so "did this GPO apply?" has to be
    answerable from a value, not from a GPO list.
    """
    # With the deny row present it takes link order 1, so it is the row Studio
    # predicts will WIN the conflict. That placement is deliberate: at any
    # lower precedence the only divergence would be its unique value going
    # missing, and the sharper failure -- the model naming a winning value that
    # never arrives on the machine -- would go undemonstrated. Everything else
    # keeps its relative order.
    offset = 1 if include_deny else 0
    rows = [
        PlannedGpo(
            name="Studio-RSOP-FilterAllow",
            guid="00000000-0000-0000-0000-00000000fa11",
            scope="ou",
            scope_key="child",
            order=1 + offset,
            values={},
            user_values={"Filter": "allow", "AllowOnly": "1"},
            filters=(
                PlannedFilter(principal_key="authenticated-users", kind="read"),
                PlannedFilter(principal_key="user", kind="apply"),
            ),
            isolates="Read + Apply for the principal: applies, and wins the conflict at order 1",
        ),
        PlannedGpo(
            name="Studio-RSOP-FilterReadOnly",
            guid="00000000-0000-0000-0000-00000000fa12",
            scope="ou",
            scope_key="child",
            order=2 + offset,
            values={},
            user_values={"Filter": "readOnly", "ReadOnlyOnly": "1"},
            filters=(
                PlannedFilter(principal_key="authenticated-users", kind="read"),
                PlannedFilter(principal_key="user", kind="read"),
            ),
            isolates=(
                "Read WITHOUT Apply: the principal can see the GPO and must not receive it. "
                "ReadOnlyOnly must be ABSENT"
            ),
        ),
        PlannedGpo(
            name="Studio-RSOP-FilterNested",
            guid="00000000-0000-0000-0000-00000000fa14",
            scope="ou",
            scope_key="child",
            order=3 + offset,
            values={},
            user_values={"Filter": "nested", "NestedOnly": "1"},
            filters=(
                PlannedFilter(principal_key="authenticated-users", kind="read"),
                PlannedFilter(principal_key="group", kind="apply"),
            ),
            isolates=(
                "Apply granted to a GROUP the principal belongs to: applies through the "
                "token. NestedOnly must be present"
            ),
        ),
        PlannedGpo(
            name="Studio-RSOP-UserControl",
            guid="00000000-0000-0000-0000-00000000fa15",
            scope="ou",
            scope_key="child",
            order=4 + offset,
            values={},
            user_values={"Control": "present"},
            isolates=(
                "CONTROL: default Authenticated Users Read+Apply, unconflicted. Absent => "
                "the experiment did not run, and every absence above means nothing."
            ),
        ),
    ]
    if include_deny:
        rows.insert(
            0,
            PlannedGpo(
                name="Studio-RSOP-FilterDeny",
                guid="00000000-0000-0000-0000-00000000fa13",
                scope="ou",
                scope_key="child",
                order=1,
                values={},
                user_values={"Filter": "deny", "DenyOnly": "1"},
                filters=(
                    PlannedFilter(principal_key="authenticated-users", kind="read"),
                    PlannedFilter(principal_key="user", kind="apply"),
                    PlannedFilter(principal_key="user", kind="deny"),
                ),
                isolates=(
                    "An explicit deny on Apply dominates the allow, so "
                    "Windows will not apply this GPO. WI-033 gave SecurityFilter its "
                    "polarity and the model now says so too; before that fix the model "
                    "was told only about the allow, predicted that it applied, and -- at "
                    "link order 1 -- that it WON the conflict. Certified as an ordinary "
                    "agreement by rsop-user-observe-20260804065525-9254."
                ),
            ),
        )
    return tuple(rows)


#: The representable cases. A pass here means Studio resolves Apply, Read
#: without Apply, and group nesting the way Windows does.
USER_SECURITY_FILTERING = Scenario(
    scenario_id="user-security-filtering",
    scope="user",
    ous=PLAIN_OUS,
    target_ou_key="child",
    user_ou_key="child",
    control_gpo="Studio-RSOP-UserControl",
    control_value_name="Control",
    needs_group=True,
    gpos=_filtering_gpos(include_deny=False),
)

#: The deny case. It WAS a declared divergence -- Studio predicted `Filter=deny`
#: and `DenyOnly=1` while Windows resolved `Filter=allow` and never wrote
#: `DenyOnly`, certified as an expected-finding on 2026-08-04. WI-033 fixed the
#: model, so the declaration is gone and this scenario is now an ordinary
#: agreement: a deny ACE keeps its GPO off the machine, and Studio says so.
#:
#: The row stays at link order 1 for the same reason it was placed there: at any
#: lower precedence the only thing at stake would be its unique value, and the
#: sharper property -- that the model does not name a winning value the machine
#: never receives -- would go untested.
USER_SECURITY_FILTERING_DENY = Scenario(
    scenario_id="user-security-filtering-deny",
    scope="user",
    ous=PLAIN_OUS,
    target_ou_key="child",
    user_ou_key="child",
    control_gpo="Studio-RSOP-UserControl",
    control_value_name="Control",
    needs_group=True,
    gpos=_filtering_gpos(include_deny=True),
)

# ---------------------------------------------------------------------------
# WP-6B extension: WMI filtering
# ---------------------------------------------------------------------------
#
# `_gpo_filter_status` records a WMI filter as the warning `wmi_filter_unknown`
# and applies the GPO anyway. Measured directly: a GPO whose filter can never
# be true is predicted to apply, and its settings are predicted to win.
#
# That WAS a declared divergence, certified as an expected-finding on
# 2026-08-04. WI-035 then gave the model a way to be TOLD how a filter
# evaluated -- not a WQL engine, which is the CSE's job, but an answer a caller
# already has -- so the scenario is now an ordinary agreement.
#
# THE TRUE ROW IS THE CONTROL, and it is not optional. A WMI filter is authored
# as a raw directory object with a length-prefixed query format that is easy to
# get subtly wrong; a malformed filter fails closed, and its GPO not applying
# looks exactly like the FALSE row working. If the true-filtered GPO does not
# apply, the filter authoring is broken and the run is a lane failure rather
# than a finding.

WMI_FILTERING = Scenario(
    scenario_id="wmi-filtering",
    ous=PLAIN_OUS,
    target_ou_key="child",
    control_gpo="Studio-RSOP-Control",
    control_value_name="Control",
    gpos=(
        PlannedGpo(
            name="Studio-RSOP-WmiFalse",
            guid="00000000-0000-0000-0000-000000001f00",
            scope="ou",
            scope_key="child",
            order=1,
            values={"Wmi": "false", "WmiFalseOnly": "1"},
            wmi_filter=WmiIntent(
                name="StudioRsopNeverTrue",
                # A build number no Windows has. Deliberately a query the
                # client can EVALUATE and answer no to, rather than one it
                # cannot parse -- an unparseable filter fails for a different
                # reason and would prove something else.
                query="SELECT * FROM Win32_OperatingSystem WHERE BuildNumber = '99999'",
                expect_true=False,
                why=(
                    "EXPECTED DIVERGENCE: Windows will not apply this GPO. Studio "
                    "predicts it does and, at link order 1, that it wins the conflict."
                ),
            ),
            isolates="a WMI filter that evaluates FALSE must keep its GPO off the machine",
        ),
        PlannedGpo(
            name="Studio-RSOP-WmiTrue",
            guid="00000000-0000-0000-0000-000000001f01",
            scope="ou",
            scope_key="child",
            order=2,
            values={"Wmi": "true", "WmiTrueOnly": "1"},
            wmi_filter=WmiIntent(
                name="StudioRsopAlwaysTrue",
                query="SELECT * FROM Win32_OperatingSystem WHERE BuildNumber >= '1'",
                expect_true=True,
                why=(
                    "CONTROL: proves the filter authoring works. If this GPO does not "
                    "apply, the false row says nothing."
                ),
            ),
            isolates="CONTROL: a WMI filter that evaluates TRUE must let its GPO apply",
        ),
        PlannedGpo(
            name="Studio-RSOP-Control",
            guid="00000000-0000-0000-0000-000000001f02",
            scope="ou",
            scope_key="child",
            order=3,
            values={"Control": "present"},
            isolates="CONTROL: no WMI filter at all. Absent => the experiment did not run.",
        ),
    ),
)

# ---------------------------------------------------------------------------
# WP-6 item 5: the same filtering, against the COMPUTER account
# ---------------------------------------------------------------------------
#
# WP-9 certified filtering for the user. The plan's topology item 5 asks for it
# "as they apply to the **computer** account", which is a different principal
# and a different token, so the user result does not carry over.
#
# The nesting row is deliberately absent. A computer's group membership lives
# in its machine token, minted at boot, so a group created by the run would not
# be in it -- the same trap the user lane hit, and there it cost a re-session
# restart to fix. Allow, read-without-apply and deny need no group at all and
# cover the rest of the item; nesting for the computer is left as its own
# question rather than smuggled in half-tested.

COMPUTER_SECURITY_FILTERING = Scenario(
    scenario_id="computer-security-filtering",
    ous=PLAIN_OUS,
    target_ou_key="child",
    control_gpo="Studio-RSOP-Control",
    control_value_name="Control",
    gpos=(
        PlannedGpo(
            name="Studio-RSOP-CompFilterDeny",
            guid="00000000-0000-0000-0000-00000000cf01",
            scope="ou",
            scope_key="child",
            order=1,
            values={"Filter": "deny", "DenyOnly": "1"},
            filters=(
                PlannedFilter(principal_key="authenticated-users", kind="read"),
                PlannedFilter(principal_key="computer", kind="apply"),
                PlannedFilter(principal_key="computer", kind="deny"),
            ),
            isolates=(
                "an explicit deny on Apply keeps the GPO off the COMPUTER, and at link "
                "order 1 the model must not name Filter=deny as the winner"
            ),
        ),
        PlannedGpo(
            name="Studio-RSOP-CompFilterAllow",
            guid="00000000-0000-0000-0000-00000000cf02",
            scope="ou",
            scope_key="child",
            order=2,
            values={"Filter": "allow", "AllowOnly": "1"},
            filters=(
                PlannedFilter(principal_key="authenticated-users", kind="read"),
                PlannedFilter(principal_key="computer", kind="apply"),
            ),
            isolates="Read + Apply for the computer: applies, and wins once the deny row is out",
        ),
        PlannedGpo(
            name="Studio-RSOP-CompFilterReadOnly",
            guid="00000000-0000-0000-0000-00000000cf03",
            scope="ou",
            scope_key="child",
            order=3,
            values={"Filter": "readOnly", "ReadOnlyOnly": "1"},
            filters=(
                PlannedFilter(principal_key="authenticated-users", kind="read"),
                PlannedFilter(principal_key="computer", kind="read"),
            ),
            isolates="Read WITHOUT Apply: ReadOnlyOnly must be ABSENT",
        ),
        PlannedGpo(
            name="Studio-RSOP-Control",
            guid="00000000-0000-0000-0000-00000000cf04",
            scope="ou",
            scope_key="child",
            order=4,
            values={"Control": "present"},
            isolates="CONTROL: default filtering, unconflicted. Absent => nothing applied.",
        ),
    ),
)

#: WI-040: the deny nobody has measured. Applying a GPO takes Read AND Apply
#: Group Policy, so a deny on READ keeps a GPO off a target with the Apply
#: allow left completely intact -- a second, independent gate that
#: `_gpo_filter_status` cannot see, because every branch it has inspects only
#: `permission == "apply"` rows.
#:
#: THE COMPUTER SCOPE, AND NOT BY PREFERENCE. MS16-072 is why: since that
#: update a USER's GPOs are retrieved in the COMPUTER's security context, so
#: denying the USER read would be evaluated against a principal that is not the
#: one doing the reading, and a null result would be uninterpretable -- it could
#: mean Windows ignores read denies, or it could mean the computer read the GPO
#: on the user's behalf exactly as designed. Here the filtered principal and the
#: reading principal are the same account and the experiment says one thing.
#: (`_filtering_gpos` keeps Authenticated Users' Read for the same reason, which
#: is also why THIS row denies the computer specifically rather than removing a
#: grant.)
#:
#: A DECLARED DIVERGENCE, built the way WP-6B's were: the model is left
#: untouched and Windows arbitrates. That ordering is not ceremony. WP-6B's
#: disabled-link case is the counter-example -- a predicted "defect" that turned
#: out to be correct behaviour, and would have been "fixed" into a real one had
#: the code been changed first.
#:
#: The prediction is that Windows BLOCKS. If it does, the model is wrong in the
#: WI-033 direction -- promising an operator settings that never arrive -- and
#: `test_a_deny_on_read_does_not_block_apply` is asserting a falsehood.
COMPUTER_SECURITY_FILTERING_DENY_READ = Scenario(
    scenario_id="computer-security-filtering-deny-read",
    ous=PLAIN_OUS,
    target_ou_key="child",
    control_gpo="Studio-RSOP-Control",
    control_value_name="Control",
    gpos=(
        PlannedGpo(
            name="Studio-RSOP-CompFilterDenyRead",
            guid="00000000-0000-0000-0000-00000000cf05",
            scope="ou",
            scope_key="child",
            order=1,
            values={"Filter": "denyRead", "DenyReadOnly": "1"},
            filters=(
                PlannedFilter(principal_key="authenticated-users", kind="read"),
                PlannedFilter(principal_key="computer", kind="apply"),
                PlannedFilter(principal_key="computer", kind="deny-read"),
            ),
            isolates=(
                "EXPECTED DIVERGENCE (WI-040): a deny on READ, with the Apply allow "
                "intact. The model never sees the deny, so it predicts this GPO applies "
                "and -- at link order 1 -- that Filter=denyRead WINS. DenyReadOnly is "
                "the sharp assertion: Studio predicts it present, Windows is expected to "
                "leave it ABSENT."
            ),
        ),
        PlannedGpo(
            name="Studio-RSOP-CompFilterAllow",
            guid="00000000-0000-0000-0000-00000000cf06",
            scope="ou",
            scope_key="child",
            order=2,
            values={"Filter": "allow", "AllowOnly": "1"},
            filters=(
                PlannedFilter(principal_key="authenticated-users", kind="read"),
                PlannedFilter(principal_key="computer", kind="apply"),
            ),
            isolates=(
                "CONTROL, and the one that makes the deny-read row mean something: the "
                "SAME Read+Apply grant, differing only in the absence of the read deny. "
                "If this GPO does not apply either, the run measured a broken DACL write "
                "rather than a read deny"
            ),
        ),
        PlannedGpo(
            name="Studio-RSOP-Control",
            guid="00000000-0000-0000-0000-00000000cf07",
            scope="ou",
            scope_key="child",
            order=3,
            values={"Control": "present"},
            isolates="CONTROL: default filtering, unconflicted. Absent => nothing applied.",
        ),
    ),
)

#: WP-6 topology item 6's third case, and the one nobody has measured: a WMI
#: filter that cannot be EVALUATED, as distinct from one that evaluates false.
#:
#: THIS SCENARIO CARRIES NO DECLARATION, deliberately. The deny and false-filter
#: scenarios declared their divergence because the answer was known from the
#: code; here it is not known what Windows does with an unevaluatable filter,
#: and declaring a guess would turn the run into a test of the guess. The lane
#: exists to find out.
#:
#: What the model will say is known: nothing supplies a result for a filter that
#: cannot be evaluated, so it stays unknown, the GPO applies, and the result
#: carries `wmi_filter_unknown`. Whether Windows agrees is the experiment.
WMI_FILTERING_ERROR = Scenario(
    scenario_id="wmi-filtering-error",
    ous=PLAIN_OUS,
    target_ou_key="child",
    control_gpo="Studio-RSOP-Control",
    control_value_name="Control",
    gpos=(
        PlannedGpo(
            name="Studio-RSOP-WmiError",
            guid="00000000-0000-0000-0000-000000001e01",
            scope="ou",
            scope_key="child",
            order=1,
            values={"Wmi": "error", "WmiErrorOnly": "1"},
            wmi_filter=WmiIntent(
                name="StudioRsopUnevaluatable",
                # A class that does not exist. The WQL is well-formed, so this
                # is an evaluation failure rather than a parse failure -- those
                # are different questions and only one of them is item 6's.
                query="SELECT * FROM Win32_NoSuchClassStudioLab",
                expect_true=None,
                why=(
                    "UNKNOWN OUTCOME: the query is valid WQL naming a class that does not "
                    "exist, so it can be neither true nor false. What Windows does with it "
                    "is what this scenario is for."
                ),
            ),
            isolates="a WMI filter that cannot be evaluated -- outcome unmeasured",
        ),
        PlannedGpo(
            name="Studio-RSOP-WmiTrue",
            guid="00000000-0000-0000-0000-000000001e02",
            scope="ou",
            scope_key="child",
            order=2,
            values={"Wmi": "true", "WmiTrueOnly": "1"},
            wmi_filter=WmiIntent(
                name="StudioRsopAlwaysTrue",
                query="SELECT * FROM Win32_OperatingSystem WHERE BuildNumber >= '1'",
                expect_true=True,
                why=(
                    "CONTROL: proves filter authoring works in this run, so the error row's "
                    "outcome cannot be blamed on a malformed filter."
                ),
            ),
            isolates="CONTROL: a WMI filter that evaluates TRUE must let its GPO apply",
        ),
        PlannedGpo(
            name="Studio-RSOP-Control",
            guid="00000000-0000-0000-0000-000000001e03",
            scope="ou",
            scope_key="child",
            order=3,
            values={"Control": "present"},
            isolates="CONTROL: no WMI filter at all. Absent => the experiment did not run.",
        ),
    ),
)

SCENARIOS: dict[str, Scenario] = {
    LSDOU_PRECEDENCE.scenario_id: LSDOU_PRECEDENCE,
    DISABLED_BLOCK_ENFORCED.scenario_id: DISABLED_BLOCK_ENFORCED,
    USER_SIDE_DISABLED.scenario_id: USER_SIDE_DISABLED,
    LOOPBACK_MERGE.scenario_id: LOOPBACK_MERGE,
    LOOPBACK_REPLACE.scenario_id: LOOPBACK_REPLACE,
    USER_SECURITY_FILTERING.scenario_id: USER_SECURITY_FILTERING,
    USER_SECURITY_FILTERING_DENY.scenario_id: USER_SECURITY_FILTERING_DENY,
    WMI_FILTERING.scenario_id: WMI_FILTERING,
    COMPUTER_SECURITY_FILTERING.scenario_id: COMPUTER_SECURITY_FILTERING,
    COMPUTER_SECURITY_FILTERING_DENY_READ.scenario_id: (COMPUTER_SECURITY_FILTERING_DENY_READ),
    WMI_FILTERING_ERROR.scenario_id: WMI_FILTERING_ERROR,
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

    def filters_for(planned: PlannedGpo) -> tuple[SecurityFilter, ...]:
        """Translate authored filtering into what the model is told.

        Deny rows used to be DROPPED here, because ``SecurityFilter`` had no
        polarity and inventing one -- silently turning a deny into the absence
        of an allow -- would have made the model look correct about a case it
        could not express. The lane demonstrated the consequence against a real
        client (WI-033) and `SecurityFilter.deny` now exists, so the deny is
        passed through like any other filter and the scenario expects an
        ordinary agreement.
        """
        expressible: list[SecurityFilter] = []
        for index, planned_filter in enumerate(planned.filters):
            principal = {
                "user": user_name,
                "computer": computer_name,
                "group": GROUP_NAME,
                "authenticated-users": "Authenticated Users",
            }[planned_filter.principal_key]
            expressible.append(
                SecurityFilter(
                    id=f"{planned.name}:{index}",
                    principal=principal,
                    permission=(
                        "read" if planned_filter.kind in ("read", "deny-read") else "apply"
                    ),
                    deny=planned_filter.kind in ("deny", "deny-read"),
                )
            )
        return tuple(expressible)

    def wmi_for(planned: PlannedGpo) -> WmiFilter | None:
        if planned.wmi_filter is None:
            return None
        return WmiFilter(
            id=f"{planned.name}:wmi",
            name=planned.wmi_filter.name,
            query=planned.wmi_filter.query,
        )

    gpos = tuple(
        GPO(
            guid=planned.guid,
            name=planned.name,
            computer_enabled=True,
            user_enabled=planned.user_enabled,
            settings=_settings(planned),
            security_filters=filters_for(planned),
            wmi_filter=wmi_for(planned),
            domain=domain,
        )
        for planned in scenario.gpos
    )

    # The scenario states how each filter evaluates on this client, and the
    # model is told. That is the same division loopback uses: the lane authors
    # the condition AND declares it, and Windows independently confirms the
    # condition really held -- the true-filtered GPO applying is what makes the
    # false one's absence mean anything. Studio evaluates no WQL here and is
    # not being asked to.
    # ``None`` in the scenario means the filter cannot be evaluated on this
    # target, which is a FACT about it and not an absence of information -- so
    # it is supplied to the model as `"unevaluatable"` rather than withheld.
    # Withholding it would say "nobody looked", and somebody did (WI-039).
    wmi_results = tuple(
        (
            f"{planned.name}:wmi",
            "unevaluatable" if planned.wmi_filter.expect_true is None
            else planned.wmi_filter.expect_true,
        )
        for planned in scenario.gpos
        if planned.wmi_filter is not None
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
            # The nesting case, stated as an input rather than discovered. The
            # observation half collects the principal's real token groups two
            # independent ways and the finalizer refuses the run if the group
            # is not in them -- so this is a claim the estate has to
            # corroborate, not one the prediction gets for free.
            group_memberships=(GROUP_NAME,) if scenario.needs_group else (),
            site_name=site_name,
            domain=domain,
        ),
        som_nodes=tuple(som_nodes),
        gpos=gpos,
        wmi_filter_results=wmi_results,
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
        "expect_finding": scenario.expect_finding,
        "group_memberships": list(result.target.group_memberships),
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
        "group_name": GROUP_NAME if scenario.needs_group else "",
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
                "filters": [
                    {"principal": planned_filter.principal_key, "kind": planned_filter.kind}
                    for planned_filter in planned.filters
                ],
                "wmi_filter": (
                    {
                        "name": planned.wmi_filter.name,
                        "query": planned.wmi_filter.query,
                        "expect_true": planned.wmi_filter.expect_true,
                        "why": planned.wmi_filter.why,
                    }
                    if planned.wmi_filter
                    else None
                ),
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
        "group_name": GROUP_NAME if scenario.needs_group else "",
        "expect_finding": scenario.expect_finding,
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
