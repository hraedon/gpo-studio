# Same-domain lifecycle lane: design

**Status (2026-10-07):** built offline, **never run**. No verdict exists and none
is registered. `lifecycle.py`'s survival table is a set of predictions until this
lane has run on the estate and the table has been corrected to what it saw.

Plan 034's ruling for `lifecycle.py` ([direction 2026-10-07](../direction-2026-10-07-plan-034-completion.md)):
same-domain lane over `Backup-GPO` / `Restore-GPO` / `Import-GPO` / `Copy-GPO`,
then a restore-plan surface. The surface is not built yet; the lane comes first.

## What it asserts

One check per cell of `gpo_studio.lifecycle.SCOPE_SURVIVAL`: five operations
(`restore_in_place` = `Restore-GPO`, `import_into_existing` = `Import-GPO
-TargetGuid`, `import_as_new` = `Import-GPO -CreateIfNeeded`, `copy` and
`copy_with_acl` = `Copy-GPO` without and with `-CopyAcl`), each crossed with six
dimensions (settings, GUID, security filtering (the DACL), WMI filter
association, links, description). That makes 30 cells. Each cell is classified
from what the guest reads back:

| Outcome | Meaning |
|---|---|
| `kept` | the target carries the source's value as authored and backed up |
| `replaced` | the target carries the value it held just before the operation |
| `lost` | the target carries no value |
| `defaulted` | a Windows-assigned GUID, or the DACL of an untouched `New-GPO` control from the same run |
| `unclassified` | none of these, which is itself a finding |

There are also two kinds of claim beyond the table:

* **Plan target identity**, five checks. `generate_restore_plan` says whose GUID
  the result carries: the source, the existing target, or one Windows assigns.
* **The backup bridge**, one check. `manifest_from_backup(read_backup(...))` on
  the real `Backup-GPO` tree pulled back from the guest must name the source GPO
  and the backup ID, keep those two distinct, and see the WMI link. The verdict
  records the populated `Backup.xml` `WMIFilter` text, which no banked backup has
  carried before. The bridge keeps that text verbatim until this run shows its
  shape.

The verdict keeps two groups apart. **Lane checks** decide whether the run is a
valid measurement. They cover the environment, whether authoring landed as
specified, whether every dimension is distinguishable (the target and the
perturbed source differ from the source everywhere, and the source DACL differs
from the default), whether cleanup is proven by re-query, and whether the bound
source and expectation are intact. **Claim checks** are the 36 described above.
`harness_valid` combined with `predictions_agree: false` is the expected first
result. Record the mismatches, correct `_predict` in `lifecycle.py` (the
expectation follows automatically), and re-run.

**Least certain predictions** (stated in `_predict`'s docstring): the description
after both imports, which depends on whether `Import-GPO` writes `GPO.cmt` and
whether `Get-GPO` reads the description from it; the WMI association after a
same-domain `Copy-GPO` (both forms); and the DACL after `Import-GPO -CreateIfNeeded`.

## How it is built

New files only. No shared bound file is edited.

* `scripts/plan-033/build-lifecycle-candidate.py` writes `expected.json` on the
  controller. The finalizer re-derives it from the bound builder and
  `lifecycle.py`, so a hand-edited expectation fails
  `expectation_reproduces_from_bound_source`.
* `scripts/windows-oracle/run-lifecycle.ps1` runs on the member server
  (PowerShell 5.1). It authors natively, using patterns copied from
  `run-rsop-author.ps1`, so a Studio writer defect cannot appear as a lifecycle
  finding. Copies run first while the source is pristine. `Restore-GPO` runs last,
  after the source has been perturbed in every dimension to a *different,
  non-empty* value, so that `kept`, `replaced` and `lost` are all
  distinguishable. Everything links to empty disposable OUs, so nothing applies
  anywhere.
* `scripts/windows-oracle/run-lifecycle-oracle.sh` drives the run and
  `finalize_lifecycle_run.py` grades it. The lane binds `lifecycle.py` and
  `backup.py`. After the first verdict, editing either one expires this lane.

Run it from a clean checkout on the controller:

```bash
ACB_VAULT_ENV=~/.claude/evidence-lab.env \
  acb exec cred:lab-hyperv-control cred:lab-guest-bootstrap -- \
    env GPO_STUDIO_LAB_HOST=<hyper-v host> GPO_STUDIO_LAB_GUEST=<member server> \
      bash scripts/windows-oracle/run-lifecycle-oracle.sh
```

## What it cannot assert

* **Cross-domain anything.** That covers migration tables, principal
  translation, and WMI links whose filter does not exist in the target. This
  half of `lifecycle.py` is **out of scope by ruling (2026-10-07)** until the
  estate has a second domain or a trust. `generate_restore_plan` refuses
  cross-domain plans.
* **Restore of a deleted GPO.** The lane restores a GPO that still exists, so
  `links: replaced` means "Restore-GPO leaves current links alone". It does not
  mean "Restore-GPO brings deleted links back".
* **The WMI filter object itself.** The lane measures only the association.
  Both filters exist throughout the run.
* **Deny ACEs and raw DACL semantics.** The comparison uses
  `Get-GPPermission -All` as SID, level and denied. That summary collapses deny
  ACEs (measured in the RSOP lane, 2026-08-04). The lane authors no deny ACE, and
  it keeps the SDDL as evidence without grading it.
* **The `-TargetName` form of an import into an existing GPO**, multi-DC
  replication (every call is pinned to the PDC emulator), and any client-side
  effect. Nothing here is applied to a machine.
* **The removed six-state workflow.** It was never a Windows question. The
  module docstring records why it was deleted.
