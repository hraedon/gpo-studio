#!/usr/bin/env python3
"""Build the Plan 033 WP-1B writer-conformance candidate set.

Each candidate is one Studio-authored native backup container imported into its
own disposable GPO on the Windows oracle.  Families are isolated so that one
adapter's failure cannot be masked by success elsewhere (WP-1B acceptance), and
a mixed candidate exercises them together in a single GPO.

Every candidate carries an ``expected.json`` holding:

* the native backup ID and source GPO ID (checked by the Windows harness before
  it imports anything);
* the registry settings the harness reads back through ``Get-GPRegistryValue``;
* the authoritative writer summary, which the local finalizer compares against
  the summary derived from the Windows ``Backup-GPO`` re-export.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from gpo_studio.export import gpmc_backup_bundle, native_backup_id
from gpo_studio.gpp import GppCollection, GppGroup, GppGroupMember
from gpo_studio.gpp_adapters import GppDrive, GppLocalUser, GppScheduledTask
from gpo_studio.model import GPO, RegistrySetting
from gpo_studio.writer_conformance import native_shape_findings, summary_from_gpo

# Fixed GUIDs keep candidates byte-reproducible so a rerun is comparable to the
# recorded evidence.  The namespace is synthetic and never resolves in AD.
_GUID_PREFIX = "9b1de5c0-0000-4000-8000-0000000000"

MACHINE_KEY = r"Software\Policies\GPOStudio\WP1B"
USER_KEY = r"Software\Policies\GPOStudio\WP1B"

DRIVE = GppDrive(
    letter="P",
    path="\\\\gpostudio-wp1b.invalid\\share\\conformance",
    label="WP1B Conformance Share",
    persistent=True,
    use_letter=True,
    action="update",
    id="{9B1DE5C0-0000-4000-8000-0000000000D1}",
)
GROUP = GppGroup(
    name="GPOStudio-WP1B-Group",
    action="update",
    description="Plan 033 WP-1B writer conformance",
    members=(
        GppGroupMember(
            name="GPOSTUDIO\\wp1b-member",
            sid="S-1-5-21-1111111111-2222222222-3333333333-1101",
            action="add",
        ),
    ),
    id="{9B1DE5C0-0000-4000-8000-0000000000A1}",
)
LOCAL_USER = GppLocalUser(
    user_name="wp1b-localuser",
    full_name="WP1B Conformance User",
    description="Plan 033 WP-1B writer conformance",
    account_disabled=True,
    password_never_expires=True,
    action="update",
    id="{9B1DE5C0-0000-4000-8000-0000000000A2}",
)
SCHEDULED_TASK = GppScheduledTask(
    name="GPOStudio WP1B Conformance Task",
    program="C:\\Windows\\System32\\cmd.exe",
    arguments="/c exit 0",
    start_in="C:\\Windows\\System32",
    enabled=True,
    trigger_type="daily",
    trigger_time="03:15:00",
    action="update",
    id="{9B1DE5C0-0000-4000-8000-0000000000A3}",
)
MACHINE_SETTING = RegistrySetting(
    id="wp1b-machine",
    side="computer",
    hive="HKLM",
    key=MACHINE_KEY,
    value_name="MachineValue",
    registry_type="REG_DWORD",
    value=1101,
)
USER_SETTING = RegistrySetting(
    id="wp1b-user",
    side="user",
    hive="HKCU",
    key=USER_KEY,
    value_name="UserValue",
    registry_type="REG_SZ",
    value="wp1b-conformance",
)


def _gpo(
    candidate_id: str,
    suffix: str,
    *,
    settings: tuple[RegistrySetting, ...] = (),
    machine: GppCollection | None = None,
    user: GppCollection | None = None,
) -> GPO:
    collections = tuple(item for item in (machine, user) if item is not None)
    return GPO(
        guid=f"{_GUID_PREFIX}{suffix}",
        name=f"GPOStudio WP1B {candidate_id}",
        domain="synthetic.test",
        settings=settings,
        gpp_collections=collections,
    )


def _registry_both() -> GPO:
    """Control candidate: the WP-2-certified shape, rerun in the WP-1B lane."""
    return _gpo("registry-both", "01", settings=(MACHINE_SETTING, USER_SETTING))


def _drives_user() -> GPO:
    return _gpo("drives-user", "02", user=GppCollection(scope="user", drives=(DRIVE,)))


def _groups_machine() -> GPO:
    return _gpo("groups-machine", "03", machine=GppCollection(scope="computer", groups=(GROUP,)))


def _local_users_machine() -> GPO:
    """Local Users shares ``Groups/Groups.xml`` with Groups per MS-GPPREF.

    Isolating it proves the shared-file merge is not what makes either kind
    readable to GPMC.
    """
    return _gpo(
        "localusers-machine",
        "04",
        machine=GppCollection(scope="computer", local_users=(LOCAL_USER,)),
    )


def _scheduled_tasks_machine() -> GPO:
    return _gpo(
        "scheduledtasks-machine",
        "05",
        machine=GppCollection(scope="computer", scheduled_tasks=(SCHEDULED_TASK,)),
    )


def _mixed_all() -> GPO:
    """Every natively supported family in one GPO, both sides populated."""
    return _gpo(
        "mixed-all",
        "06",
        settings=(MACHINE_SETTING, USER_SETTING),
        machine=GppCollection(
            scope="computer",
            groups=(GROUP,),
            local_users=(LOCAL_USER,),
            scheduled_tasks=(SCHEDULED_TASK,),
        ),
        user=GppCollection(scope="user", drives=(DRIVE,)),
    )


CANDIDATES: tuple[tuple[str, str, Callable[[], GPO]], ...] = (
    ("registry-both", "registry", _registry_both),
    ("drives-user", "drives", _drives_user),
    ("groups-machine", "groups", _groups_machine),
    ("localusers-machine", "local_users", _local_users_machine),
    ("scheduledtasks-machine", "scheduled_tasks", _scheduled_tasks_machine),
    ("mixed-all", "mixed", _mixed_all),
)


def _expected(gpo: GPO) -> dict[str, object]:
    return {
        "backup_id": native_backup_id(gpo),
        "source_gpo_id": "{" + gpo.guid.upper() + "}",
        "gpo_name": gpo.name,
        "settings": [
            {
                "side": setting.side,
                "hive": setting.hive,
                "key": setting.key,
                "value_name": setting.value_name,
                "registry_type": setting.registry_type,
                "value": setting.value,
            }
            for setting in gpo.settings
        ],
        "summary": summary_from_gpo(gpo),
        "native_shape_findings": list(native_shape_findings(gpo)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)

    index: list[dict[str, str]] = []
    for candidate_id, family, factory in CANDIDATES:
        gpo = factory()
        candidate_dir = args.output_dir / candidate_id
        candidate_dir.mkdir()
        (candidate_dir / "candidate.zip").write_bytes(gpmc_backup_bundle(gpo))
        (candidate_dir / "expected.json").write_text(
            json.dumps(_expected(gpo), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        index.append({"id": candidate_id, "family": family})

    (args.output_dir / "candidates.json").write_text(
        json.dumps({"candidates": index}, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"built {len(index)} WP-1B candidates in {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
