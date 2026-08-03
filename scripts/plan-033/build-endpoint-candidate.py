#!/usr/bin/env python3
"""Build the Plan 033 WP-1B step-5 endpoint candidate.

PHASE 3 (2026-07-28) — does the CORRECTED TaskV2 writer create a task?

Row F is the WI-018 test and its expectation has FLIPPED. In phase 2 it was a
regression pin: a scalar-authored TaskV2 had to stay absent, because the writer
still emitted the inert shape. The writer now synthesizes an embedded <Task>
payload, so the same row must now produce a task. Rows A-E are unchanged and
act as controls -- if they still behave, a change in F is attributable to the
writer fix and nothing else.

PHASE 2 — does the CORRECTED FilterOs emitter actually apply?

Phase 1 proved the two defects were real: Studio's scalar TaskV2 created no
task (WI-018), and Studio's synthetic <FilterOS osType="..."> made an item
apply nowhere in either polarity (WI-021). Its task set is preserved in git
history; the certified verdict is at
docs/plan-033/wp1b-evidence/endpoint-result.json.

WI-021 is now fixed, and phase 1's design cannot test the fix: Studio and GPMC
now emit byte-equivalent <FilterOs> elements, so the "Studio vs native" pairs
that made phase 1 discriminating have collapsed into the same thing. The
question has changed from "is Studio's filter shape wrong?" to "does Studio's
corrected filter get EVALUATED?", which needs a different contrast.

An always-present result would be as damning as always-absent: it would mean
the filter is ignored rather than honoured. So the set below pairs a matching
filter against an excluding one on an otherwise identical item. Only a
split result — matching applies, excluding does not — shows genuine
evaluation.

PHASE 4 (2026-08-03) — the endpoint moves to a CLIENT, and the product code
moves with it.

Phase 3 ran on a Windows Server 2025 host that was both the authoring machine
and the endpoint. The evidence estate cannot be that, so the lane is now
two-guest (docs/plan-033/endpoint-lane-design.md) and the endpoint is the
client. Two consequences for this file:

* the matching product code becomes ``WINTHRESHOLD``. A client does not report
  ``WINTHRESHOLDSRV``, so carrying the server code across would have produced a
  filter that legitimately did not match and reported it as a Studio defect;
* ``WINTHRESHOLD`` is itself only *inferred* to cover Windows 11 — GPMC offers
  no Windows 11 entry at all — so rows J and K make that inference falsifiable
  instead of load-bearing. J is the native control for B, and K asserts that
  the server code does NOT match a client.

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
from gpo_studio.ilt import IltFilter, IltOsCriteria, IltPredicate
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

# The product code that MATCHES the endpoint. This is a parameter, not a
# constant, and the reason is the whole difference between the old lane and the
# new one.
#
# Phase 2 ran on a Windows Server 2025 host, where WINTHRESHOLDSRV matches. The
# endpoint is now the CLIENT -- it carries the frozen client build family, and
# only a lane that applies policy to a client may assert a real client_build.
# Windows clients report WINTHRESHOLD, not WINTHRESHOLDSRV. Carrying the server
# code across the port would have turned the WI-021 discriminator into a filter
# that legitimately does not match, and the run would have reported "Studio's
# matching filter did not apply" -- a fabricated defect, indistinguishable in
# the evidence from a real one.
#
# WINTHRESHOLD is also not a safe assumption. The vocabulary capture
# (WI01A-OS-ILT) recorded WINTHRESHOLD against Windows 10 and observed that
# GPMC offers NO Windows 11 entry at all, from which the corpus matrix INFERS
# that WINTHRESHOLD covers Windows 11 too. The manual-evidence queue still
# carries that as an open item wanting endpoint proof. The estate's client is
# Windows 11, so this run can settle it -- which is precisely why the row set
# below pairs every Studio filter with a hand-written native one. If the
# vocabulary guess is wrong, the NATIVE matching row is absent too, and the
# finalizer reports inconclusive-on-vocabulary rather than blaming Studio.
CLIENT_MATCH_OS_VERSION = "WINTHRESHOLD"
SERVER_MATCH_OS_VERSION = "WINTHRESHOLDSRV"


def _native_os_filter(version: str) -> str:
    """Hand-written genuine GPMC FilterOs matching ``version``.

    not="0" means "the OS IS this product". Edition, type and sp stay at NE
    ("Any") so the match turns only on the product code -- the same reduction
    the Studio-side rows make, so the pair differs in authoring shape and
    nothing else.
    """
    return (
        f'<FilterOs bool="AND" not="0" class="NT" version="{version}" '
        'type="NE" edition="NE" sp="NE"/>'
    )


# Studio's OS predicate, post-WI-021. Serializes to a genuine
# <FilterOs class= version= type= edition= sp=> element.
STUDIO_EXCLUDING_FILTER = IltFilter(
    items=(
        IltPredicate(
            type="os", os_criteria=IltOsCriteria(os_class="NT", version="XP")
        ),
    )
)


def _studio_os_filter(version: str) -> IltFilter:
    """Studio's OS predicate for one product code, everything else "Any"."""
    return IltFilter(
        items=(
            IltPredicate(
                type="os",
                os_criteria=IltOsCriteria(os_class="NT", version=version),
            ),
        )
    )

# Same predicate negated: "the OS is NOT Windows XP" -> TRUE on the target.
# Kept from phase 1 because it exercises the negation path independently of the
# version-match path.
STUDIO_NEGATED_FILTER = IltFilter(
    items=(
        IltPredicate(
            type="os",
            negate=True,
            os_criteria=IltOsCriteria(os_class="NT", version="XP"),
        ),
    )
)


def _scalar_task(name: str, **overrides: object) -> GppScheduledTask:
    """A scalar-authored TaskV2 with one field varied, for bisecting row F.

    Row F (scalar authoring, defaults) produced no task even after the payload
    and StartBoundary fixes, and the CSE logs nothing. These vary exactly one
    field each so the cause is attributable rather than guessed.
    """
    base: dict[str, object] = dict(
        name=name,
        action="replace",
        element_variant="TaskV2",
        program=PROGRAM,
        arguments=ARGUMENTS,
        start_in="C:\\Windows\\System32",
        enabled=True,
        trigger_type="daily",
        trigger_time="03:00:00",
    )
    base.update(overrides)
    return GppScheduledTask(**base)  # type: ignore[arg-type]


def _bare_time_task(name: str) -> GppScheduledTask:
    """Row I: the pre-fix boundary form, with a valid identity.

    The runAs bisect showed row F failed on identity, not on the boundary, so
    the StartBoundary normalization has not actually been shown to matter. This
    supplies an explicit task_xml carrying the bare time the writer used to
    emit, with runAs correct, to find out whether that alone is fatal. Claiming
    a fix without this would be claiming something unmeasured.
    """
    payload = (
        '<Task version="1.2">'
        "<RegistrationInfo><Author>GPO Studio</Author></RegistrationInfo>"
        '<Principals><Principal id="Author">'
        "<UserId>NT AUTHORITY\\System</UserId><RunLevel>LeastPrivilege</RunLevel>"
        "</Principal></Principals>"
        "<Settings><Enabled>true</Enabled><AllowStartOnDemand>true</AllowStartOnDemand>"
        "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy></Settings>"
        "<Triggers><CalendarTrigger><StartBoundary>03:00:00</StartBoundary>"
        "<Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval>"
        "</ScheduleByDay></CalendarTrigger></Triggers>"
        f'<Actions Context="Author"><Exec><Command>{PROGRAM}</Command>'
        f"<Arguments>{ARGUMENTS}</Arguments></Exec></Actions>"
        "</Task>"
    )
    return GppScheduledTask(
        name=name,
        action="replace",
        element_variant="TaskV2",
        run_as="NT AUTHORITY\\System",
        task_xml=payload,
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
    ("GPOStudio-EP2-A-nofilter", True, None, "control: task applies at all", "present"),
    (
        "GPOStudio-EP2-B-os-match",
        True,
        _studio_os_filter(CLIENT_MATCH_OS_VERSION),
        "WI-021 fix: matching filter must APPLY",
        "present",
    ),
    (
        "GPOStudio-EP2-J-native-os-match",
        True,
        IltFilter(items=(_native_os_filter(CLIENT_MATCH_OS_VERSION),)),
        "vocabulary control for B: does WINTHRESHOLD match a Windows 11 client at all?",
        "present",
    ),
    (
        "GPOStudio-EP2-K-os-server-code",
        True,
        _studio_os_filter(SERVER_MATCH_OS_VERSION),
        "the server product code must NOT match a client (negative half of the collision proof)",
        "absent",
    ),
    (
        "GPOStudio-EP2-C-os-exclude",
        True,
        STUDIO_EXCLUDING_FILTER,
        "WI-021 fix: excluding filter must NOT apply",
        "absent",
    ),
    (
        "GPOStudio-EP2-D-os-negated",
        True,
        STUDIO_NEGATED_FILTER,
        "WI-021 fix: negation path",
        "present",
    ),
    (
        "GPOStudio-EP2-E-native-control",
        True,
        IltFilter(items=(NATIVE_EXCLUDING_FILTER,)),
        "control: hand-written native excluding filter",
        "absent",
    ),
    (
        "GPOStudio-EP2-F-scalar-shape",
        False,
        None,
        "WI-018 fix: scalar-authored TaskV2 must now CREATE a task",
        "present",
    ),
)

#: Bisect rows for row F. Each varies ONE field from F so that whichever
#: appears identifies the cause; the CSE logs nothing, so this is the only way
#: to attribute it.
BISECT: tuple[tuple[str, dict[str, object], str], ...] = (
    (
        "GPOStudio-EP2-G-runas-system",
        {"run_as": "NT AUTHORITY\\System"},
        "hypothesis: empty runAs leaves the CSE no identity to register under",
    ),
    (
        "GPOStudio-EP2-H-future-boundary",
        {"trigger_time": "2026-01-01T03:00:00"},
        "hypothesis: a 1970 StartBoundary is rejected as too old (DISPROVED: absent)",
    ),
    (
        "GPOStudio-EP2-I-bare-time",
        {"run_as": "NT AUTHORITY\\System", "trigger_time": "RAW-BARE-TIME"},
        "was the StartBoundary fix load-bearing, or only schema hygiene?",
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
                scheduled_tasks=(
                    tuple(
                        _task(name, native_shape=native, ilt=ilt)
                        for name, native, ilt, _, _ in TASKS
                    )
                    + tuple(
                        _bare_time_task(name)
                        if fields.get("trigger_time") == "RAW-BARE-TIME"
                        else _scalar_task(name, **fields)
                        for name, fields, _ in BISECT
                    )
                ),
            ),
        ),
    )

    expected = {
        "backup_id": native_backup_id(gpo),
        "source_gpo_id": "{" + gpo.guid.upper() + "}",
        # The finalizer needs these to interpret the filter rows: which product
        # code was supposed to match, and which was supposed not to. Recording
        # them here rather than duplicating the constants keeps the candidate
        # and its verdict describing the same experiment.
        "endpoint_role": "client",
        "match_os_version": CLIENT_MATCH_OS_VERSION,
        "non_match_os_version": SERVER_MATCH_OS_VERSION,
        "vocabulary_control_task": "GPOStudio-EP2-J-native-os-match",
        "tasks": [
            {"name": name, "isolates": isolates, "expected_if_defects_real": expectation}
            for name, _, _, isolates, expectation in TASKS
        ]
        + [
            {"name": name, "isolates": hypothesis, "expected_if_defects_real": "present"}
            for name, _, hypothesis in BISECT
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
