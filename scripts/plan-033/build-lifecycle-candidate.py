#!/usr/bin/env python3
"""Build the Plan 034 lifecycle (same-domain scope-survival) expectation.

The lane asks one question per cell of ``gpo_studio.lifecycle.SCOPE_SURVIVAL``:
**after this GPMC operation, does this piece of a GPO's scope survive the way
Studio predicts?** Five operations (``Restore-GPO``, ``Import-GPO`` into an
existing GPO, ``Import-GPO -CreateIfNeeded``, ``Copy-GPO`` with and without
``-CopyAcl``) by six dimensions (settings, GUID, security filtering, WMI filter
association, links, description).

Unlike the publication lane there is nothing for Windows to import from
Studio: the guest authors its source GPO natively, so a Studio writer defect
cannot masquerade as a lifecycle finding. The only candidate artifact is
``expected.json`` -- the survival table plus, per operation, the restore
plan's own claims (cmdlet, whose GUID the result carries, the warnings it
raises) -- built here on the controller from the bound ``lifecycle.py`` and
hash-bound by the finalizer (WI-025). It never travels to the guest: the guest
is the thing being measured.

The plans are generated against a fixed synthetic manifest that, like the
lane's source GPO, links a WMI filter -- otherwise the plan would not raise the
"filter existence not checked" warning the lane's source makes relevant.

Invocation (deterministic: same inputs -> byte-identical output):

    python scripts/plan-033/build-lifecycle-candidate.py <output-dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import assert_never

from gpo_studio.lifecycle import (
    SCOPE_DIMENSIONS,
    SCOPE_SURVIVAL,
    WINDOWS_OPERATIONS,
    BackupFileEntry,
    BackupManifest,
    WindowsOperation,
    generate_restore_plan,
)

EXPECTATION_NAME = "expected.json"

#: The order the guest runs the operations in. Copies first, while the source
#: is exactly as backed up (Copy-GPO reads the live GPO, not the backup);
#: Restore-GPO last, because it is preceded by perturbing the source.
OPERATION_ORDER: tuple[WindowsOperation, ...] = (
    "copy",
    "copy_with_acl",
    "import_as_new",
    "import_into_existing",
    "restore_in_place",
)

#: Fixed synthetic identity, per the builder conventions: no estate
#: identifier anywhere.
_MANIFEST = BackupManifest(
    backup_id="{27182818-2845-9045-2353-602874713526}",
    gpo_guid="{31415926-5358-9793-2384-626433832795}",
    gpo_display_name="zz-studio-evidence-lifecycle-source",
    domain="synthetic.test",
    created_at="2026-10-07T00:00:00",
    has_wmi_filter=True,
    wmi_filter_reference="[synthetic.test;{16180339-8874-9894-8482-045868343656};0]",
    files=(BackupFileEntry("Machine/registry.pol", "0" * 64, 1),),
)

_EXISTING_TARGET = "{14142135-6237-3095-0488-016887242096}"


def _plan_claims(operation: WindowsOperation) -> dict[str, object]:
    match operation:
        case "restore_in_place":
            plan = generate_restore_plan(_MANIFEST, operation)
        case "import_into_existing":
            plan = generate_restore_plan(_MANIFEST, operation, target_gpo_guid=_EXISTING_TARGET)
        case "import_as_new" | "copy" | "copy_with_acl":
            plan = generate_restore_plan(_MANIFEST, operation, target_name="zz-target")
        case _:
            assert_never(operation)
    return {
        "cmdlet": plan.cmdlet,
        "target_identity": plan.target_identity,
        "plan_names_target_guid": bool(plan.target_gpo_guid),
        "survival": {p.dimension: p.survival for p in plan.scope},
        "warnings": list(plan.warnings),
    }


def expectation() -> dict[str, object]:
    """The whole expectation, recomputable by the finalizer from bound source."""
    if set(OPERATION_ORDER) != set(WINDOWS_OPERATIONS):
        raise ValueError("operation order does not cover every Windows operation")
    operations = {op: _plan_claims(op) for op in OPERATION_ORDER}
    for op, claims in operations.items():
        # The plan carries the table; a plan that disagreed with the table
        # would make the lane grade two answers at once.
        if claims["survival"] != dict(SCOPE_SURVIVAL[op]):
            raise ValueError(f"{op}: plan scope disagrees with SCOPE_SURVIVAL")
    return {
        "schema_version": 1,
        "lane": "lifecycle-same-domain",
        "predictions_are_hypotheses": True,
        "dimensions": list(SCOPE_DIMENSIONS),
        "operation_order": list(OPERATION_ORDER),
        "operations": operations,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    data = json.dumps(expectation(), indent=2, sort_keys=True).encode("utf-8") + b"\n"
    (out / EXPECTATION_NAME).write_bytes(data)
    print(f"{EXPECTATION_NAME} sha256={hashlib.sha256(data).hexdigest()}")
    print(data.decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
