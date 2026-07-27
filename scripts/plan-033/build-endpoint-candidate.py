#!/usr/bin/env python3
"""Build the Plan 033 WP-1B step-5 endpoint candidate (WI-018 + WI-021).

Every row varies exactly one thing against a control, because an absent
scheduled task is otherwise ambiguous between "the shape was ignored" and
"GPP tasks do not work on this host at all".

    task             task shape      ILT filter                  isolates
    ---------------  --------------  --------------------------  --------------
    A-studio-shape   Studio scalar   none                        WI-018
    B-native-shape   genuine GPMC    none                        WI-018 control
    C-studio-filter  genuine GPMC    Studio FilterOS, excluding  WI-021
    D-native-filter  genuine GPMC    genuine FilterOs, excluding WI-021 control
    E-studio-match   genuine GPMC    Studio FilterOS, matching   WI-021 discrim.
    F-native-match   genuine GPMC    genuine FilterOs, matching  WI-021 discrim.

C-F deliberately use the *genuine* task shape so the only variable is the
filter; A turned out to be inert (WI-018 confirmed), which would otherwise have
made every filter row untestable.

E and F exist because the first run's excluding-only design was not
discriminating. An excluding filter that produces an absent task cannot
distinguish "the CSE honoured it" from "the CSE could not parse it and failed
closed". The negated pair settles it: if E is absent while F is present, the
Studio filter fails closed regardless of polarity, which means a
Studio-authored OS filter makes the item apply NOWHERE.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gpo_studio.export import gpmc_backup_bundle, native_backup_id
from gpo_studio.gpp import GppCollection
from gpo_studio.gpp_adapters import GppScheduledTask
from gpo_studio.ilt import IltFilter, IltPredicate
from gpo_studio.model import GPO

# Harmless, fast, leaves nothing behind.
PROGRAM = "C:\\Windows\\System32\\cmd.exe"
ARGUMENTS = "/c exit 0"

# Lifted from the shape of genuine GPMC TaskV2 captures in
# tests/fixtures/native-gpp-gpmc: actions and triggers live in an embedded
# <Task> payload, never in scalar Properties attributes.
NATIVE_TASK_XML = (
    '<Task version="1.2">'
    "<RegistrationInfo><Author>GPOStudio-Endpoint</Author></RegistrationInfo>"
    '<Principals><Principal id="Author">'
    "<UserId>NT AUTHORITY\\System</UserId><RunLevel>HighestAvailable</RunLevel>"
    "</Principal></Principals>"
    "<Settings><Enabled>true</Enabled><AllowStartOnDemand>true</AllowStartOnDemand>"
    "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy></Settings>"
    "<Triggers><CalendarTrigger>"
    "<StartBoundary>2026-01-01T03:00:00</StartBoundary><Enabled>true</Enabled>"
    "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
    "</CalendarTrigger></Triggers>"
    f'<Actions Context="Author"><Exec><Command>{PROGRAM}</Command>'
    f"<Arguments>{ARGUMENTS}</Arguments></Exec></Actions>"
    "</Task>"
)

# Genuine GPMC OS-filter shape: five independent attributes, element FilterOs.
# not="0" + version="XP" means "the OS IS Windows XP" -> false on the target.
NATIVE_EXCLUDING_FILTER = (
    '<FilterOs bool="AND" not="0" class="NT" version="XP" type="NE" edition="NE" sp="NE"/>'
)

# Studio's own OS predicate. Serializes to <FilterOS osType="XP" .../> -- an
# element name and attribute set with zero precedent in genuine GPMC output.
STUDIO_EXCLUDING_FILTER = IltFilter(items=(IltPredicate(type="os", value="XP"),))

# Same predicate, negated: "the OS is NOT Windows XP" -> TRUE on the target.
# This is the discriminator. An excluding filter alone cannot distinguish "the
# CSE honoured it" from "the CSE could not parse it and failed closed", because
# both produce an absent task. If the negated form is ALSO absent, the filter
# fails closed regardless of polarity -- meaning a Studio-authored OS filter
# makes the item apply nowhere.
STUDIO_MATCHING_FILTER = IltFilter(items=(IltPredicate(type="os", value="XP", negate=True),))
NATIVE_MATCHING_FILTER = (
    '<FilterOs bool="AND" not="1" class="NT" version="XP" type="NE" edition="NE" sp="NE"/>'
)


def _task(name: str, *, native_shape: bool, ilt: IltFilter | None) -> GppScheduledTask:
    """One scheduled task.

    ``native_shape`` clears the Task Scheduler 1.0 scalar properties and supplies
    an embedded ``<Task>`` payload instead, which is what genuine GPMC writes.
    Studio's serializer still emits the scalar attributes (empty), so this is
    "as native as Studio can express" rather than byte-identical to GPMC -- the
    payload, which is what the CSE acts on, is the genuine shape.
    """
    if native_shape:
        return GppScheduledTask(
            name=name,
            action="replace",
            element_variant="TaskV2",
            task_xml=NATIVE_TASK_XML,
            trigger_type="daily",
            ilt_filter=ilt,
        )
    return GppScheduledTask(
        name=name,
        action="replace",
        element_variant="TaskV2",
        program=PROGRAM,
        arguments=ARGUMENTS,
        start_in="C:\\Windows\\System32",
        enabled=True,
        trigger_type="daily",
        trigger_time="03:00:00",
        ilt_filter=ilt,
    )


TASKS = (
    ("GPOStudio-EP-A-studio-shape", False, None, "WI-018", "absent"),
    ("GPOStudio-EP-B-native-shape", True, None, "WI-018 control", "present"),
    (
        "GPOStudio-EP-C-studio-filter",
        True,
        STUDIO_EXCLUDING_FILTER,
        "WI-021",
        "present (means filter ignored)",
    ),
    (
        "GPOStudio-EP-D-native-filter",
        True,
        IltFilter(items=(NATIVE_EXCLUDING_FILTER,)),
        "WI-021 control",
        "absent",
    ),
    (
        "GPOStudio-EP-E-studio-match",
        True,
        STUDIO_MATCHING_FILTER,
        "WI-021 discriminator",
        "absent (means filter fails closed, so OS targeting never applies)",
    ),
    (
        "GPOStudio-EP-F-native-match",
        True,
        IltFilter(items=(NATIVE_MATCHING_FILTER,)),
        "WI-021 discriminator control",
        "present",
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)

    gpo = GPO(
        guid="9b1de5c0-0000-4000-8000-0000000000e1",
        name="GPOStudio WP1B endpoint",
        domain="synthetic.test",
        gpp_collections=(
            GppCollection(
                scope="computer",
                scheduled_tasks=tuple(
                    _task(name, native_shape=native, ilt=ilt) for name, native, ilt, _, _ in TASKS
                ),
            ),
        ),
    )

    expected = {
        "backup_id": native_backup_id(gpo),
        "source_gpo_id": "{" + gpo.guid.upper() + "}",
        "tasks": [
            {"name": name, "isolates": isolates, "expected_if_defects_real": expectation}
            for name, _, _, isolates, expectation in TASKS
        ],
    }
    (args.output_dir / "candidate.zip").write_bytes(gpmc_backup_bundle(gpo))
    (args.output_dir / "expected.json").write_text(
        json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"built endpoint candidate ({len(TASKS)} tasks) in {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
