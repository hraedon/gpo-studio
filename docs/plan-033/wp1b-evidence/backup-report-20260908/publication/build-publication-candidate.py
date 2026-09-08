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

from gpo_studio.export import (
    ExtensionRegistration,
    extension_registration,
    gpmc_backup_bundle,
    native_backup_id,
)
from gpo_studio.gpp import GppCollection, GppDrive, GppService
from gpo_studio.model import GPO, RegistrySetting, ValidationError
from gpo_studio.publication import (
    PublicationPlan,
    generate_publication_plan,
    planned_sysvol_paths,
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


def _expectation(
    gpo: GPO, plan: PublicationPlan, registration: ExtensionRegistration
) -> dict[str, object]:
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
        "machine_extension_names": registration.machine,
        "user_extension_names": registration.user,
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
    registration = extension_registration(_GPO)
    if registration.unverified_families:
        print(
            "candidate carries GPP families with no captured extension metadata: "
            + ", ".join(registration.unverified_families),
            file=sys.stderr,
        )
        return 2
    refusals = [
        step
        for step in plan.steps
        if step.operation
        in {
            "unsupported_cse_content",
            "unsupported_extension_registration",
            "extension_lists_unreachable",
        }
    ]
    if refusals:
        for step in refusals:
            print(f"plan refuses publication: {step.detail}", file=sys.stderr)
        return 2

    try:
        bundle = gpmc_backup_bundle(_GPO)
    except ValidationError as exc:
        for issue in exc.issues:
            print(f"[{issue.severity}] {issue.code}: {issue.message}", file=sys.stderr)
        return 2

    (out / ARCHIVE_NAME).write_bytes(bundle)
    expectation = _expectation(_GPO, plan, registration)
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
