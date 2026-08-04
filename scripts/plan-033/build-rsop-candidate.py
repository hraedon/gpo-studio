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
from dataclasses import dataclass
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

OU_ROOT = "StudioRsop"
OU_PARENT = "StudioRsopParent"
OU_CHILD = "StudioRsopChild"


@dataclass(frozen=True)
class PlannedGpo:
    """One GPO in the topology, with where it links and what it writes."""

    name: str
    guid: str
    scope: str  # site | domain | ou
    scope_key: str  # symbolic; the authoring half resolves it to a real DN
    order: int
    values: dict[str, str]
    isolates: str
    enforced: bool = False


# Deterministic GUIDs. The authoring half creates real GPOs and reports their
# real GUIDs; these exist so the prediction is computable here, before any of
# them exist. The finalizer matches on NAME, never on these.
PLAN: tuple[PlannedGpo, ...] = (
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
)

CONTROL_GPO = "Studio-RSOP-Control"
CONTROL_VALUE_NAME = "Control"


def _domain_dn(domain: str) -> str:
    return ",".join(f"DC={part}" for part in domain.split("."))


def _scope_dns(domain: str, site_name: str) -> dict[str, str]:
    domain_dn = _domain_dn(domain)
    root = f"OU={OU_ROOT},{domain_dn}"
    parent = f"OU={OU_PARENT},{root}"
    child = f"OU={OU_CHILD},{parent}"
    return {
        "site": f"CN={site_name},CN=Sites,CN=Configuration,{domain_dn}",
        "domain": domain_dn,
        "root": root,
        "parent": parent,
        "child": child,
    }


def _settings(gpo: PlannedGpo) -> tuple[RegistrySetting, ...]:
    return tuple(
        RegistrySetting(
            id=f"{gpo.name}:{value_name}",
            side="computer",
            hive="HKLM",
            key=POLICY_KEY,
            value_name=value_name,
            registry_type="REG_SZ",
            value=value,
        )
        for value_name, value in sorted(gpo.values.items())
    )


def build_query(domain: str, site_name: str, computer_name: str) -> RsopQuery:
    """Build the RSOP query for the planned topology.

    The SOM tree mirrors exactly what the authoring half will create. If the
    two ever drift, the prediction describes a topology that does not exist --
    which is why the authoring half reports the DNs it actually created and the
    finalizer checks them against this.
    """
    dns = _scope_dns(domain, site_name)

    gpos = tuple(
        GPO(
            guid=planned.guid,
            name=planned.name,
            computer_enabled=True,
            user_enabled=True,
            settings=_settings(planned),
            domain=domain,
        )
        for planned in PLAN
    )

    def links_for(scope_key: str) -> tuple[SomLink, ...]:
        return tuple(
            SomLink(
                gpo_guid=planned.guid,
                scope=planned.scope,  # type: ignore[arg-type]
                scope_dn=dns[planned.scope_key],
                enabled=True,
                enforced=planned.enforced,
                order=planned.order,
            )
            for planned in PLAN
            if planned.scope_key == scope_key
        )

    som_nodes = (
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
        SomNode(
            dn=dns["root"],
            name=OU_ROOT,
            scope="ou",
            parent_dn=dns["domain"],
            links=(),
        ),
        SomNode(
            dn=dns["parent"],
            name=OU_PARENT,
            scope="ou",
            parent_dn=dns["root"],
            links=links_for("parent"),
        ),
        SomNode(
            dn=dns["child"],
            name=OU_CHILD,
            scope="ou",
            parent_dn=dns["parent"],
            links=links_for("child"),
        ),
    )

    return RsopQuery(
        query_id="wp6b-lsdou-precedence",
        mode="planning",
        target=RsopTarget(
            computer_name=computer_name,
            # WI-026, and the first thing this lane found -- before it ran.
            #
            # This is the CONTAINER DN, not the computer's own DN. Passing
            # CN=<computer>,<container> -- which is what a real directory
            # returns and what RsopTarget.computer_dn reads like it wants --
            # makes compute_precedence resolve nothing and compute_rsop return
            # an empty result: no applied GPOs, no winners. Windows applies
            # five GPOs to this machine.
            #
            # So this line is an adapter choice, and the design doc's open
            # question 3 warned that adapter choices are themselves part of
            # what is being tested. Recording it here rather than burying it:
            # the lane's prediction is what Studio says *when handed the input
            # shape it actually requires*. The lane is not testing WI-026, and
            # a lane that silently fed the model an object DN would predict
            # "nothing applies", observe five applied GPOs, and report a
            # spectacular model failure that was really a caller error.
            computer_dn=dns["child"],
            site_name=site_name,
            domain=domain,
        ),
        som_nodes=som_nodes,
        gpos=gpos,
    )


def prediction_document(domain: str, site_name: str, computer_name: str) -> dict[str, Any]:
    """Run ``compute_rsop`` and normalize it to the shape the finalizer diffs.

    Normalizing here rather than in the finalizer is deliberate: the adapter
    between Studio's shape and the Rsop schema is itself part of what is being
    tested (open question 3 in the design doc), so it must be reviewable as a
    committed artifact rather than applied invisibly at comparison time.
    """
    result = compute_rsop(build_query(domain, site_name, computer_name))

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


def topology_document(domain: str, site_name: str, computer_name: str) -> dict[str, Any]:
    """The authoring instructions, symbolic where the estate must resolve them."""
    dns = _scope_dns(domain, site_name)
    return {
        "domain": domain,
        "site_name": site_name,
        "endpoint_computer": computer_name,
        "policy_key": POLICY_KEY,
        "ous": [
            {"name": OU_ROOT, "dn": dns["root"], "parent_dn": _domain_dn(domain)},
            {"name": OU_PARENT, "dn": dns["parent"], "parent_dn": dns["root"]},
            {"name": OU_CHILD, "dn": dns["child"], "parent_dn": dns["parent"]},
        ],
        "target_ou_dn": dns["child"],
        "gpos": [
            {
                "name": planned.name,
                "scope": planned.scope,
                "scope_dn": dns[planned.scope_key],
                "order": planned.order,
                "enforced": planned.enforced,
                "values": [
                    {"value_name": name, "value": value, "type": "String"}
                    for name, value in sorted(planned.values.items())
                ],
            }
            for planned in PLAN
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--domain",
        required=True,
        help="AD DNS domain of the estate, e.g. ad.labdomain.dev",
    )
    parser.add_argument(
        "--site-name",
        default="Default-First-Site-Name",
        help="AD site the endpoint is in; the site-scope GPO links here",
    )
    parser.add_argument(
        "--computer-name",
        required=True,
        help="The endpoint computer's sAMAccountName without the trailing $",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    topology = topology_document(args.domain, args.site_name, args.computer_name)
    prediction = prediction_document(args.domain, args.site_name, args.computer_name)
    expected = {
        "scenario_id": "lsdou-precedence",
        "scope": "computer",
        "control_gpo": CONTROL_GPO,
        "control_value_name": CONTROL_VALUE_NAME,
        "policy_key": POLICY_KEY,
        "rows": [
            {"gpo": planned.name, "isolates": planned.isolates} for planned in PLAN
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
    print(f"built rsop candidate ({len(PLAN)} GPOs) in {args.output_dir}")
    print(f"  predicted winners: {winners}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
