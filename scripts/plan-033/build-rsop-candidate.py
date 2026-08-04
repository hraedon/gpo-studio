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
    """A corpus scenario expressed as something the lane can build and predict."""

    scenario_id: str
    ous: tuple[PlannedOu, ...]
    gpos: tuple[PlannedGpo, ...]
    target_ou_key: str
    control_gpo: str
    control_value_name: str


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

SCENARIOS: dict[str, Scenario] = {
    LSDOU_PRECEDENCE.scenario_id: LSDOU_PRECEDENCE,
    DISABLED_BLOCK_ENFORCED.scenario_id: DISABLED_BLOCK_ENFORCED,
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
    scenario: Scenario, domain: str, site_name: str, computer_name: str
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
            site_name=site_name,
            domain=domain,
        ),
        som_nodes=tuple(som_nodes),
        gpos=gpos,
    )


def prediction_document(
    scenario: Scenario, domain: str, site_name: str, computer_name: str
) -> dict[str, Any]:
    """Run ``compute_rsop`` and normalize it to the shape the finalizer diffs.

    Only the COMPUTER side is emitted. WP-6 is computer scope by ruling, and a
    predicted user-side winner that the lane cannot observe would sit in the
    verdict looking like a tested claim.
    """
    result = compute_rsop(build_query(scenario, domain, site_name, computer_name))

    applied = sorted(g.gpo_name for g in result.gpo_results if g.is_applied)
    denied = sorted(
        (
            {"gpo": g.gpo_name, "reasons": sorted(g.filtering_reasons)}
            for g in result.gpo_results
            if not g.is_applied
        ),
        key=lambda row: str(row["gpo"]),
    )

    winners = sorted(
        (
            {
                "hive": setting.hive or "HKLM",
                "key": setting.key,
                "value_name": setting.value_name,
                "value": setting.effective_value,
                "winning_gpo": setting.winning_gpo_name,
                "overridden_by": sorted(setting.overridden_by),
            }
            for setting in result.computer_settings
        ),
        key=lambda row: str(row["value_name"]),
    )

    return {
        "query_id": result.query_id,
        "scenario_id": scenario.scenario_id,
        "scope": "computer",
        "target": {
            "computer_name": result.target.computer_name,
            "site_name": result.target.site_name,
            "domain": result.target.domain,
        },
        "applied_gpos": applied,
        "denied_gpos": denied,
        "winners": winners,
        "warnings": sorted(result.warnings),
    }


def topology_document(
    scenario: Scenario, domain: str, site_name: str, computer_name: str
) -> dict[str, Any]:
    """The authoring instructions, symbolic where the estate must resolve them."""
    dns = _scope_dns(scenario, domain, site_name)
    return {
        "scenario_id": scenario.scenario_id,
        "domain": domain,
        "site_name": site_name,
        "endpoint_computer": computer_name,
        "policy_key": POLICY_KEY,
        "ous": [
            {
                "name": ou.name,
                "dn": dns[ou.key],
                "parent_dn": dns[ou.parent_key],
                "block_inheritance": ou.block_inheritance,
            }
            for ou in scenario.ous
        ],
        "target_ou_dn": dns[scenario.target_ou_key],
        "gpos": [
            {
                "name": planned.name,
                "scope": planned.scope,
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
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    scenario = SCENARIOS[args.scenario]

    topology = topology_document(scenario, args.domain, args.site_name, args.computer_name)
    prediction = prediction_document(scenario, args.domain, args.site_name, args.computer_name)
    expected = {
        "scenario_id": scenario.scenario_id,
        "scope": "computer",
        "control_gpo": scenario.control_gpo,
        "control_value_name": scenario.control_value_name,
        "policy_key": POLICY_KEY,
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
