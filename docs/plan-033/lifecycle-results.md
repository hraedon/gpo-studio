# Same-domain lifecycle — Windows results

**Certification (2026-10-08):** `lifecycle-20261008093248-2000-c76d10eb3f2849fe`,
passed, on clean commit `35130528d89761ed1e6990001d086241e5655025`
([evidence](wp7-evidence/lifecycle/verification.json), tag
`evidence/lifecycle-20261008093248-2000-c76d10eb3f2849fe`). The harness was
valid, all 30 survival cells agreed with `lifecycle.SCOPE_SURVIVAL`, the five
plan-identity claims and the backup bridge held, and cleanup was proven empty
by re-query. It ran on LabMS01: Windows Server 2025 Standard build 26100,
domain role 3 (member server), PowerShell 5.1.26100.33438 Desktop, GroupPolicy
module 1.0.0.0, every call pinned to the PDC emulator of `ad.labdomain.dev`.

The commit was reviewed by GPT Sol over six rounds; the last returned PASS
("fit to bank"), with only the client-side residual described
[below](#the-residual-that-cannot-be-closed-from-the-client) left open.

Plan 033 WP-7 is the lifecycle work package, so the pack lives under
`wp7-evidence/`. How the lane works and what each guard is for:
[the lane design](lifecycle-lane-design.md).

## What was measured

Five GPMC operations, each crossed with six dimensions, read back from the
directory after each operation:

| Operation | Cmdlet |
|---|---|
| `restore_in_place` | `Restore-GPO` into the GPO the backup was taken from |
| `import_into_existing` | `Import-GPO -TargetGuid` into a GPO that already existed |
| `import_as_new` | `Import-GPO -TargetName -CreateIfNeeded`, target absent |
| `copy` | `Copy-GPO` |
| `copy_with_acl` | `Copy-GPO -CopyAcl` |

The source GPO was authored natively with a registry setting, a description,
a DACL granting Apply to a run-owned group (not Authenticated Users), a WMI
filter and a link to an empty OU. The pre-existing target differed from it in
every dimension, and before `Restore-GPO` the source itself was changed in
every dimension to a third, non-empty value. That is what makes `kept`,
`replaced` and `lost` distinguishable. An untouched `New-GPO` from the same
run supplies the `defaulted` DACL.

## The 30 cells, as measured

`kept`: the target carries the source's value as backed up. `replaced`: it
carries the value it held just before the operation. `lost`: it carries
none. `defaulted`: a Windows-assigned GUID, or the control's default DACL.

| Operation | settings | GPO GUID | security filtering (DACL) | WMI association | links | description |
|---|---|---|---|---|---|---|
| `restore_in_place` | kept | kept | kept | kept | replaced | kept |
| `import_into_existing` | kept | replaced | replaced | replaced | replaced | kept |
| `import_as_new` | kept | defaulted | defaulted | lost | lost | kept |
| `copy` | kept | defaulted | defaulted | kept | lost | kept |
| `copy_with_acl` | kept | defaulted | kept | kept | lost | kept |

Every cell matched the prediction in `lifecycle.py`, so the table needed no
correction. That includes the three the lane design named least certain:

* **The description after both imports is `kept`.** After `Import-GPO`,
  `Get-GPO` reports the backup's description, which the backup carries in
  `GPO.cmt`. The cell is graded on `Get-GPO`'s description. The guest also
  recorded a `GPO.cmt` in every target's SYSVOL folder afterwards, but that is
  kept as evidence, not graded, and the pre-existing target already had one.
* **A same-domain `Copy-GPO` keeps the WMI association**, with and without
  `-CopyAcl`.
* **`Import-GPO -CreateIfNeeded` gives the new GPO the `New-GPO` default
  DACL**, not the backup's.

Two further readings follow from the table and are easy to get wrong:

* `restore_in_place` leaves the links as they were just before the restore
  (`replaced`). The lane restored a GPO that still existed, so this does not
  show what happens to the links of a deleted GPO.
* `import_as_new` loses the WMI association that both copies keep. A GPO
  rebuilt from a backup by `-CreateIfNeeded` has no WMI filter until one is
  linked again.

`tests/test_lifecycle_verdict.py` re-grades the committed `result.json` with
the shipping finalizer and holds `SCOPE_SURVIVAL` equal to the observed table.

## The `Backup.xml` WMI wire shape

`Backup-GPO` does not write the directory attribute's form. The source GPO's
`gPCWQLFilter` read:

```
[AD.LABDOMAIN.DEV;{39b2ba78-17af-4954-95c1-7b21773c44e0};0]
```

and the same association in the backup's `Backup.xml`, under
`GroupPolicyCoreSettings`, read:

```
<WMIFilter><![CDATA[MSFT_SomFilter.ID="{39b2ba78-17af-4954-95c1-7b21773c44e0}",Domain="AD.LABDOMAIN.DEV"]]></WMIFilter>
<WMIFilterName><![CDATA[zz-studio-lifecycle-20261008093248-2000-c76d10eb3f2849fe-src-wmi]]></WMIFilterName>
```

That is a WMI object path: the filter id in lower case and braces, the DNS
domain in upper case, and a sibling `WMIFilterName` carrying the filter's
`msWMI-Name`. No banked backup had carried a populated `WMIFilter` before.
Exploratory run 1 measured the shape first (below), and the sanitized value is
`tests/fixtures/lifecycle/backup-wmifilter-ws2025.json`. `lifecycle.py`
accepts only this shape (`parse_wmi_filter_reference`) and refuses a second
`GroupPolicyCoreSettings`, `WMIFilter` or `WMIFilterName` instead of choosing
between them. In the certifying run, `manifest_from_backup(read_backup(...))`
read the real tree and named the source filter by id, domain and name, and not
the target's.

## The clock-skew finding

A GPO returned by `Copy-GPO` or `Import-GPO -CreateIfNeeded` does not prove the
run created it, so the guest owns one only if its id was absent from a
snapshot taken just before the operation and its AD creation time is not
earlier than the operation's start.

In exploratory run 1 that start time came from the member server's clock, and
the creation time from the directory. The DC's clock was a few seconds behind
the member's: all three genuine creations reported a `CreationTime` of
08:40:56, earlier than the operation start the member had recorded, so the
guard rejected them as foreign GPOs. They were left in place, as the guard
intends, and removed by hand at 08:42Z.

Both times are now read from the domain controller: the start from RootDSE
`currentTime` and the creation from `whenCreated`, each truncated to the whole
second. In the certifying run the evidence for the three creations was:

```
copy           in_snapshot=False;when_created_utc=2026-10-08T09:32:46Z;dc_start_utc=2026-10-08T09:32:46Z
copy_with_acl  in_snapshot=False;when_created_utc=2026-10-08T09:32:46Z;dc_start_utc=2026-10-08T09:32:46Z
import_as_new  in_snapshot=False;when_created_utc=2026-10-08T09:32:47Z;dc_start_utc=2026-10-08T09:32:47Z
```

Each GPO was created within the same second the operation started, so a
comparison at second resolution against any other clock would be decided by
that clock's offset. The general point: a creation-time guard is only as good
as the agreement between the clocks it compares, so it compares one clock with
itself.

## The residual that cannot be closed from the client

Two cases look the same as a genuine creation from the client side:

* a creator who learns the unpublished per-operation nonce in the target name,
  for example by watching the absence lookup on the DC or reading the guest's
  memory; and
* a GPO another party creates inside the window between the snapshot and the
  cmdlet's own create step, under a guessed 64-bit name.

Either would pass the snapshot check and the DC-clock check, so it would be
treated as owned and deleted at cleanup. Closing this would need a marker on
the GPO, and a GPO has nowhere to carry one without changing something the
lane measures. The design records it in the same terms. It bears on the
lane's cleanup in a disposable lab domain. It does not bear on any survival
cell.

## Run history

These runs are history. Neither is a verdict, and neither is banked.

* **Exploratory run 1**, `lifecycle-20261008084058-7356`, commit `7a9671d`.
  This was the first contact with Windows. `restore_in_place` and
  `import_into_existing` ran, and all 12 of their cells matched the
  predictions. The three creating operations ran but were rejected by the
  creation guard, which is the clock-skew finding above, so their 18 cells were
  never graded. The backup bridge failed on the then-unknown `Backup.xml` WMI
  shape. This run is where the measured shape comes from. It reported an empty
  cleanup residual while the three rejected GPOs still existed, which is why
  the residual now lists every run-named survivor, owned or not.
* **Exploratory run 2**, commit `a3f24d1`, after the bridge and guard fixes.
  It passed: harness valid, all 30 predictions agreeing, cleanup clean. It was
  not banked because its commit predates the Plan 034 batch merge and was
  superseded. The lane was re-run from the merged line instead.

Both commits come from before a rebase. In this clone they are reachable only
from a local `backup/lifecycle-pre-rebase-*` branch, so neither is cited as
evidence.

## Boundary

This verdict shows, for one topology, which parts of a GPO's scope survive
each same-domain GPMC operation. It does **not** show:

* **Anything cross-domain.** That covers migration tables, principal
  translation, and WMI filters missing from the target. That half is out of
  scope by ruling (2026-10-07), and `generate_restore_plan` refuses it.
* **That the table generalises.** One source shape and one target were
  measured, on one member server, against one DC. Multi-DC replication, the
  `-TargetName` form of an import into an existing GPO, and restoring a
  deleted GPO were not exercised.
* **The WMI filter object itself.** Only the association was measured. Both
  filters existed throughout the run.
* **Deny ACEs or raw DACL semantics.** The DACL is compared as
  `Get-GPPermission -All` rows (SID, level, denied). That summary collapses
  deny ACEs, and the lane authored none.
* **Any client-side effect.** Everything linked to empty OUs, so nothing
  applied to a machine.
* **That Studio performs any of this.** Studio executes nothing. The lane
  measured Windows cmdlets run natively on the guest.

The restore-plan surface (`POST /api/lifecycle/restore-plan`) carries these
limits in every response.
