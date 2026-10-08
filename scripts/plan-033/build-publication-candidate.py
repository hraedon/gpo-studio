#!/usr/bin/env python3
"""Build the Plan 034 publication-completeness candidate.

The question this lane asks is not "can Windows read what Studio wrote?" -- the
Scripts and WP-3 lanes already answer that -- but **"does Studio's publication
plan account for everything Windows actually needs?"** A plan that names every
file and omits an attribute produces a GPO whose content is byte-perfect and
which applies nothing, which is the shape WI-057 was filed for and which no
round-trip test can see, because a round trip never asks what a *third* party
would have had to write.

So this builder emits two artifacts:

* ``studio-publication-backup.zip`` -- a native GMPC backup, through the real
  ``gpo_studio.export.gpmc_backup_bundle`` path, for Windows to import; and
* ``expected.json`` -- the *plan's own claim* about what publishing this GPO
  would write: its SYSVOL paths, its extension-list values, and which half of
  the packed GPT.INI version it moves.

Every value in ``expected.json`` is read off the plan's steps. The extension
lists in particular come from the plan's ``update_extension_lists`` steps and
NOT from ``extension_registration(gpo)``: the backup Windows imports is built
by the export path, which registers the extensions on its own, so an
expectation derived from the same vocabulary would match Windows even for a
plan that omitted both registration steps -- certifying the WI-057 omission
this lane exists to catch. A plan with no step for a side claims an empty list
for it, and Windows' populated attribute then fails the comparison.

``expected.json`` is built here, on the controller, and hash-bound by the
finalizer (WI-025). That matters more than usual for this lane: the guest is
the thing being measured, so an expectation the guest could supply would be a
comparison of Windows against itself.

The GPO deliberately carries **no description**. `GPO.cmt` exists in SYSVOL
only when a GPO has a comment -- measured with a control run on 2026-09-07 --
so an undescribed GPO is the case where the plan must name no comment file and
Windows must produce none. That is the negative half of WI-058, and it is
cheaper to assert here than to build a second candidate for.

Only the four GPP families whose native extension metadata has been captured
(``_GPP_EXTENSION_PROFILES``) can appear: the export path refuses the rest
rather than guessing a pair, and a candidate that tripped that refusal would be
testing the refusal instead of the plan. Drives is user-side and Services
computer-side, as they are in practice, which also makes the machine and user
extension lists differ -- a lane whose two sides were identical could not catch
a fix that copied one to the other.

Invocation (deterministic: same inputs -> byte-identical outputs):

    python scripts/plan-033/build-publication-candidate.py <output-dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from gpo_studio.export import gpmc_backup_bundle, native_backup_id
from gpo_studio.gpp import GppCollection, GppDrive, GppService
from gpo_studio.model import GPO, RegistrySetting, ValidationError
from gpo_studio.publication import (
    PublicationPlan,
    generate_publication_plan,
    planned_sysvol_paths,
    validate_publication_plan,
)

ARCHIVE_NAME = "studio-publication-backup.zip"
EXPECTATION_NAME = "expected.json"

#: Fixed synthetic identity, per the R9/WP-2/R10 builder conventions: fixed
#: GUID, zz-studio-evidence-* display name, synthetic.test domain, and no
#: estate identifier anywhere.
_GPO = GPO(
    guid="31415926-5358-9793-2384-626433832795",
    name="zz-studio-evidence-publication-completeness",
    domain="synthetic.test",
    # No description: see the module docstring. This is the control half of
    # WI-058, not an oversight.
    settings=(
        RegistrySetting(
            id="m1",
            side="computer",
            hive="HKLM",
            key=r"SOFTWARE\Policies\SyntheticApp",
            value_name="MachineFlag",
            registry_type="REG_DWORD",
            value=1,
        ),
        RegistrySetting(
            id="u1",
            side="user",
            hive="HKCU",
            key=r"SOFTWARE\Policies\SyntheticApp",
            value_name="UserFlag",
            registry_type="REG_SZ",
            value="on",
        ),
    ),
    gpp_collections=(
        GppCollection(
            scope="computer",
            services=(
                GppService(service_name="SyntheticSvc", startup_type="automatic"),
            ),
        ),
        GppCollection(
            scope="user",
            drives=(
                GppDrive(letter="S", path=r"\\synthetic.test\share", label="Synthetic"),
            ),
        ),
    ),
)


#: The directory attribute each side's extension list lives in, keyed the way
#: ``expected.json`` names them.
_EXTENSION_ATTRIBUTES = {
    "gPCMachineExtensionNames": "machine_extension_names",
    "gPCUserExtensionNames": "user_extension_names",
}


def _planned_extension_lists(plan: PublicationPlan) -> dict[str, str]:
    """The extension-list values *plan* says it would set, by expectation key.

    Read from the ``update_extension_lists`` steps' typed fields, never from
    the GPO. A side with no step is the plan claiming that attribute stays
    empty, which is exactly what the lane must be able to catch.
    """
    planned = {key: "" for key in _EXTENSION_ATTRIBUTES.values()}
    seen: set[str] = set()
    for step in plan.steps:
        if step.operation != "update_extension_lists":
            continue
        attribute = step.directory_attribute
        if attribute is None or attribute not in _EXTENSION_ATTRIBUTES:
            raise ValueError(f"step {step.step_id!r} names no extension-list attribute")
        if not step.directory_value:
            raise ValueError(f"step {step.step_id!r} sets {attribute} to nothing")
        if attribute in seen:
            raise ValueError(f"plan sets {attribute} twice")
        seen.add(attribute)
        planned[_EXTENSION_ATTRIBUTES[attribute]] = step.directory_value
    return planned


def _expectation(gpo: GPO, plan: PublicationPlan) -> dict[str, object]:
    halves = {
        step.version_half
        for step in plan.steps
        if step.operation == "update_gpt_ini" and step.version_half is not None
    }
    if len(halves) != 1:
        raise ValueError("plan must move exactly one GPT.INI version half set")
    return {
        "schema_version": 1,
        "gpo_id": "{" + gpo.guid.upper() + "}",
        "gpo_name": gpo.name,
        "domain": gpo.domain,
        "backup_id": native_backup_id(gpo),
        "plan_payload_digest": plan.payload_digest,
        "sysvol_paths": list(planned_sysvol_paths(plan)),
        **_planned_extension_lists(plan),
        "version_half": halves.pop(),
        # Asserted as an absence, which is the only way to state it: an
        # undescribed GPO must produce no comment file.
        "expects_gpo_cmt": any(
            step.operation == "write_gpo_comment" for step in plan.steps
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)

    plan = generate_publication_plan(_GPO, target="both")
    # Every refusal the planner can emit surfaces as a validation error, so the
    # builder asks the validator rather than keeping its own list of refusal
    # operations that a new refusal could slip past.
    refusals = [issue for issue in validate_publication_plan(plan) if issue.level == "error"]
    if refusals:
        for issue in refusals:
            print(f"plan refuses publication: {issue.check}: {issue.message}", file=sys.stderr)
        return 2

    try:
        bundle = gpmc_backup_bundle(_GPO)
    except ValidationError as exc:
        for issue in exc.issues:
            print(f"[{issue.severity}] {issue.code}: {issue.message}", file=sys.stderr)
        return 2

    (out / ARCHIVE_NAME).write_bytes(bundle)
    expectation = _expectation(_GPO, plan)
    (out / EXPECTATION_NAME).write_bytes(
        json.dumps(expectation, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )

    print(f"{ARCHIVE_NAME} sha256={hashlib.sha256(bundle).hexdigest()}")
    print(
        f"{EXPECTATION_NAME} sha256="
        f"{hashlib.sha256((out / EXPECTATION_NAME).read_bytes()).hexdigest()}"
    )
    print(json.dumps(expectation, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
