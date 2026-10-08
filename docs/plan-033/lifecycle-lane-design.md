# Same-domain lifecycle lane: design

**Status (2026-10-08):** certified. `lifecycle-20261008093248-2000-c76d10eb3f2849fe`
passed at `3513052` on a clean tree: harness valid, all 30 survival cells agreeing
with `lifecycle.SCOPE_SURVIVAL`, cleanup clean. The verdict is banked at
[`wp7-evidence/lifecycle/`](wp7-evidence/lifecycle/verification.json) and the
measured table, the WMI wire shape and the clock-skew finding are in
[the results](lifecycle-results.md). The status lines further down this
document describe the lane as it was being built, and are kept as history.

Plan 034's ruling for `lifecycle.py` ([direction 2026-10-07](../direction-2026-10-07-plan-034-completion.md)):
same-domain lane over `Backup-GPO` / `Restore-GPO` / `Import-GPO` / `Copy-GPO`,
then a restore-plan surface. The lane came first; the surface is
`POST /api/lifecycle/restore-plan` (2026-10-08).

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

## Hardening after the first review (2026-10-08)

An executing review returned FAIL with nine findings. The fixes are listed here
so the next reader knows what each guard is for:

* **Ownership guard.** Before its first create, the guest generates every name
  it will use and asks the directory whether any of them exist. On a collision
  it aborts having created nothing, and its cleanup deletes nothing. There is no
  deletion by name pattern anywhere.
* **Intent recorded before each create, but ownership proven separately.**
  Each object enters the inventory before the command that creates it, so a
  create that commits and then throws is still found. Intent is not ownership,
  though: another creator can take a name between the guard and the create
  (re-review P1). Each directory object (OU, group, WMI filter) therefore
  carries a run-unique marker set by its own create call (`description`, or
  `msWMI-Parm1` for a filter), and cleanup deletes it only if the marker it
  reads back is this run's. A GPO from `New-GPO` counts as owned when the
  create returns it, because `New-GPO` cannot adopt an existing name. A GPO
  returned by `Copy-GPO` or `Import-GPO -CreateIfNeeded` does not prove
  creation, since the import can write into a GPO another creator made after
  the absence check (re-review 3). It counts as owned only if its id was absent
  from a `Get-GPO -All` snapshot taken immediately before the operation, and
  its AD `whenCreated` is not earlier than the operation's start. Both times
  are read from the domain controller's clock (RootDSE `currentTime`) and
  truncated to the whole second. Estate run 1 compared against the member's
  clock, and a DC a few seconds behind rejected all three genuine creations.
  A GPO that fails either check is recorded as foreign, never owned and never
  deleted, and the run fails. A GPO found under an intended name after a failed
  create is likewise reported and left in place. A window remains between the
  snapshot and the cmdlet's own create step. Each creating operation's target
  name therefore carries a 64-bit nonce of its own, from a fresh GUID generated
  immediately before that operation. That nonce is written nowhere (not the
  directory, not any output) before the operation's create step, and the
  result records it only afterwards. The run's own nonce cannot serve, because
  it is public from the first OU onward, and a racer could derive a target name
  from it (re-review 5).
* **The residual risk, stated plainly.** Two cases cannot be told apart from
  a genuine creation on the client side. One is a creator who can learn an
  unpublished in-process nonce, for example by observing the absence lookup
  on the DC or reading the guest's memory. The other is a GPO another party
  creates inside the window under a guessed 64-bit name. Either would pass
  both the snapshot and the DC-clock checks and be owned and deleted. Closing
  this would need a marker on the GPO, and a GPO has nowhere to carry one
  without changing something the lane measures.
* **Creating operations have no before-state.** A non-null `target_before` on
  a copy or `import_as_new` record is refused as malformed. It is never
  ignored.
* **Each WMI association is its domain plus its filter id.** Every comparison
  uses both: the snapshot's `gPCWQLFilter` and the backup bridge's
  `[domain;{id};n]` form. The domain DNS name is compared case-insensitively.
* **Snapshots must name what the run made.** `run_id` must be
  `lifecycle-<stamp>`, and every baseline, and every creating operation's
  result, must carry the display name the run generated.
* **Creation evidence is parsed completely.** The guest's `creation_evidence`
  must match `in_snapshot=…;when_created_utc=…;dc_start_utc=…` exactly, with
  nothing extra. It must show `in_snapshot=False` and `whenCreated >= dc_start`
  (re-review 5).
* **Single-valued backup elements stay single.** A second
  `GroupPolicyCoreSettings`, `WMIFilter` or `WMIFilterName` in `Backup.xml` is
  refused, not resolved by whichever comes first or last. Which one Windows
  would honour is unmeasured.
* **No coercion.** The finalizer type-checks every value it grades: GUID syntax,
  SIDs, `SID|level|denied` permission entries, and booleans. A `null` is a
  harness error and never becomes the string `"None"`. A `gPCWQLFilter` that is
  present but unparseable is a harness error, never "no filter".
* **One perturbation snapshot, on the source.** The restore is graded only if
  its `before` state is the very snapshot whose perturbation was verified, and
  that snapshot's GPO id is the source's.
* **Cleanup proof.** The residual lists every surviving object under a name
  this run used, its own or not. A survivor that is not provably ours is
  marked "left in place, ownership unproven", so the post-run state is never
  reported clean while a run-named object exists. Estate run 1 reported an
  empty residual with three such GPOs still present. The proof needs exactly
  the five named residual categories. It also needs a creation inventory equal, DN for DN, to the
  objects the run's stamp generates: the OUs, the two groups under the run's
  OU, and the two `msWMI-Som` objects in `CN=SOM,CN=WMIPolicy,CN=System`.
  Every GPO entry must be owned, carry its generated name, and have the id the
  guest read back. The control must be an untouched `New-GPO` (Authenticated Users holds
  Apply), so that "defaulted" means something.
* **Bridge evidence: the measured shape.** Estate run 1 showed what
  `Backup.xml` really carries:
  `MSFT_SomFilter.ID="{id}",Domain="DOMAIN"`, a WMI object path with the DNS
  domain in upper case, plus a sibling `WMIFilterName`. It is not the
  directory attribute's `[domain;{id};0]` form. The bridge accepts only that
  measured shape, with the exact id, the run's domain (compared ignoring case)
  and the source filter's exact name. The shapes guessed before the run are no
  longer accepted. The sanitized measured value is
  `tests/fixtures/lifecycle/backup-wmifilter-ws2025.json`.
* **The `-CreateIfNeeded` precondition.** The creating plans set
  `requires_target_absent`. When the caller supplies the domain's current
  names, only that list decides, and the backup's historical name only draws a
  warning. Without the list, the backup's own name is refused by default. The guest measures
  absence immediately before each creating operation, and the finalizer requires
  that measurement.
* **An independent control.** The end-to-end control uses the frozen-spec
  environment and a clean repository of the bound bytes, and it must exit 0.
  The guest-script probes (`tests/test_lifecycle_guest_probes.py`) run the
  script under `pwsh` against stand-in cmdlets.

## Exploratory estate run 1 (2026-10-08, commit `7a9671d`)

This run is not a verdict, but it is the first contact with Windows, on
LabMS01 (WS2025 build 26100, PowerShell 5.1.26100):

* `restore_in_place` and `import_into_existing` ran, and **all 12 of their
  cells matched the predictions**. Restore kept the settings, GUID, DACL, WMI
  association and description, and left the current links in place
  (`replaced`). Import into the existing GPO kept the settings and the
  description, and the target's own GUID, DACL, WMI association and links
  stayed (`replaced`). The description cell for `import_into_existing` was
  among the least certain predictions; it held.
* The three creating operations ran but were rejected by the clock-skew defect
  described above, so their 18 cells are ungraded.
* The backup bridge failed on the then-unknown `Backup.xml` WMI shape, which
  is now measured and handled.

Exploratory run 2 at `a3f24d1` passed: the harness was valid, all 30
predictions agreed, and cleanup was clean. It is not banked, because the lane
is re-run and banked from `main` after the Plan 034 batch.

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
