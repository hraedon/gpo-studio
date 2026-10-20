# The release 1.1.0 re-freeze batch

**Status:** executed 2026-10-20 (UTC). **All 26 lanes passed** at the re-freeze
commit `5ffa026516830139f8607ef5706f2bf8f4e002d5` ("Re-freeze 1.1.0", the
commit 1.1.0 ships), every one with `source.dirty=false`: WP-0's manifest plus
25 lane verdicts. `pending_requalification` in the verdict registry is empty.
Every live verdict records the shipping tree.

This is step 2 of the 1.1.0 approval order (Plan 036, the D-9 addendum; see
[How this release is approved](../release-evidence-1.1.0.md#how-this-release-is-approved)),
run a second time: every lane live at the freeze commit F, re-run at F as one
batch, then the post-batch directory check. It is banked in commit B, F's
child.

## Why there is a second batch

The first freeze commit, `765b304`, was requalified by the
[release 1.1.0 final batch](release110-final-batch.md) (26 of 26 on
2026-10-19), banked in `3cec82b` and approved in `d19f846`, and the tag
`v1.1.0` was pushed on `d19f846`. The release workflow run on that tag (run
39361251167) published nothing: its `verify` job was cancelled at its 25-minute
limit on every attempt. Workflows are frozen from F to the tag, so raising the
limit needed a new freeze commit, `5ffa026`, the child of `d19f846`. It
changes the workflow timeouts, the release gate and its tests, the
`CHANGELOG.md` date and one entry, and the release records, and no application
code. The release gate holds every live verdict to the freeze commit's batch,
so every lane was run again at `5ffa026`.

The batch was driven by `scripts/plan-033/run-requal-batch.sh` on the second
dev box (the controller), from a clean clone of F, under the driver's per-lane
watchdog and containment layer. It ran from 07:26:28Z to 08:10:20Z, about 44
minutes, and ended `BATCH_DONE rc=0`. No lane timed out, was cancelled, lost
containment, failed its scope, failed to start or left a process behind
(`timed_out`, `cancelled`, `containment_lost`, `scope_failed`, `exec_failed`,
`processes_killed` in every row of the [batch manifest](release110-refreeze-batch.json),
a schema 3 manifest whose every row states `test_scope_tool: false`). The
driver's progress log is the source of every window, budget and exit status.
The packs were staged and the manifest written by
`scripts/plan-033/bank-requal-batch.py` (`stage`, then `manifest --cleanup`),
and `retarget` pointed `tests/fixtures/scenarios/platforms.json` at the new
runs. The tests read this batch from the release evidence report's `freeze`
(`tests/current_qualification.py`), so banking it changed no test.

## What this batch requalifies

Every verdict that was the current qualification at F: the first freeze's
batch at `765b304`, its 25 lane verdicts and WP-0's manifest. All are retired
with their packs and tags unchanged; they certify nothing for the release.
That batch had already retired every release candidate verdict.

The lanes measured what they measured at the first freeze, with the same
checks, and every check count and every pack's file count is unchanged: the
firewall's write leg again carried the native `B05566AC` registration, report
parity again compared 30 Windows-produced backups plus the guest-authored GPO
with the WI-072 and WI-073 cases agreeing exactly, and the lifecycle lane's 30
survival cells again agreed with `lifecycle.SCOPE_SURVIVAL`.

## Evidence

| Experiment | Passing run | Checks | Duration | Commit |
|---|---|---|---|---|
| wp0 | `live-synthetic-registry-basic-20261020072705-9695` | pass | 1m17s | `5ffa026` |
| wp1b | `wp1b-writer-20261020072808-8446` | pass | 0m46s | `5ffa026` |
| wp2 | `wp2-native-import-20261020072858-5525` | 18/18 | 0m43s | `5ffa026` |
| wp3-member | `wp3-security-template-20261020072938-4029` | 20/20 | 0m36s | `5ffa026` |
| wp3-dc | `wp3-security-template-20261020072911-9635` | 20/20 | 0m37s | `5ffa026` |
| object-security | `object-security-20261020073052-3290` | 20/20 | 0m38s | `5ffa026` |
| scripts-metadata | `scripts-r10-20261020073126-4216` | 20/20 | 0m36s | `5ffa026` |
| publication | `publication-completeness-20261020073202-7563` | 21/21 | 0m36s | `5ffa026` |
| lifecycle | `lifecycle-20261020073233-9178-40aaabfb07a44f6d` | 73/73 | 0m34s | `5ffa026` |
| report-parity | `report-parity-20261020073320-2127` | 26/26 | 2m02s | `5ffa026` |
| firewall | `firewall-20261020073459-2222842` | 36/36 | 2m03s | `5ffa026` |
| fdeploy | `fd-20261020073719-5157` | 29/29 | 0m45s | `5ffa026` |
| endpoint | `endpoint-observe-20261020073835-9781` | pass | 2m54s | `5ffa026` |
| lsdou-precedence | `rsop-observe-20261020074127-4290` | pass | 1m55s | `5ffa026` |
| disabled-block-enforced | `rsop-observe-20261020074322-5597` | pass | 2m02s | `5ffa026` |
| wmi-filtering | `rsop-observe-20261020074546-9797` | pass | 2m20s | `5ffa026` |
| wmi-filtering-error | `rsop-observe-20261020074753-3255` | pass | 1m59s | `5ffa026` |
| computer-security-filtering | `rsop-observe-20261020074945-9317` | pass | 1m48s | `5ffa026` |
| computer-security-filtering-deny-read | `rsop-observe-20261020075135-6601` | pass | 1m50s | `5ffa026` |
| loopback-merge | `rsop-user-observe-20261020075328-1230` | pass | 2m15s | `5ffa026` |
| loopback-replace | `rsop-user-observe-20261020075545-2837` | pass | 2m17s | `5ffa026` |
| user-side-disabled | `rsop-user-observe-20261020075802-7706` | pass | 2m16s | `5ffa026` |
| user-security-filtering | `rsop-user-observe-20261020080055-6849` | pass | 3m04s | `5ffa026` |
| user-security-filtering-deny | `rsop-user-observe-20261020080359-8373` | pass | 3m02s | `5ffa026` |
| user-security-filtering-read-deny | `rsop-user-observe-20261020080623-7133` | pass | 2m16s | `5ffa026` |
| computer-security-filtering-group-deny | `rsop-observe-20261020080904-2605` | pass | 2m24s | `5ffa026` |

Packs live under `<family>-evidence/release110-refreeze-20261020/<name>/`.
Each holds the controller's local run directory verbatim, plus
`controller-candidate/` (the builder's output, which the verdict's candidate
hashes bind; WP-0 has none) and `controller.log` (the driver's per-lane log):
the file set of the earlier batches. Every file was checked against the
controller by SHA-256 when it was staged, and the manifest records each one's
hash. No pack holds a controller-side bound source copy. RSoP verdicts written
as `rsop-verdict.json` / `rsop-user-verdict.json` are banked as
`verification.json`; the manifest records each original name.

Every passing run has an annotated `evidence/<run-id>` tag, created by its
finalizer in the controller's clone and peeling to `5ffa026`. They are pushed
with the batch.

## Run-id stamps

A run id's UTC stamp has one-second precision, so it is compared with the
driver's window for its lane at that precision, the window's start truncated
to the second. So compared, every stamp falls inside its lane's window except
one. The firewall's, `firewall-20261020073459-2222842`, which its runner stamps
on the controller with `date +%Y%m%d%H%M%S`, names 07:34:59, the second in
which its lane started (07:34:59.020Z); it reads 20 ms before the window's
exact start only because the stamp is truncated to the second. The exception,
as in the first freeze's batch, is `wp3-dc`'s stamp,
`wp3-security-template-20261020072911-9635`: it is 42 seconds before that lane
started (07:29:53Z) and falls inside the `wp2` window two lanes earlier. The
WP-3 guest script stamps the id with the guest's own clock (`Get-Date` in
`run-wp3-security-template.ps1`), and that lane runs on the domain controller,
so the DC's clock ran at least 42 seconds behind the controller's (39 seconds
in the first freeze's batch the day before). The pack is the DC's run, not a
mix-up: its environment records `LABDC01`, domain role 5, and the driver
recorded its run directory at 07:29:53Z, the lane's start. No lane's checks
depend on the guest clock, and Kerberos tolerates far larger skew. Nothing in
this repository measured the DC's clock directly during this batch.

## Post-batch directory check

The [post-batch directory check](release110-refreeze-cleanup/directory.json),
captured at 2026-10-20T08:11:30Z (the DC's clock) after the last lane, is
clean: the computer and the user account are restored, and no experiment OUs,
GPOs, groups or WMI filters survived. Its
[collector](release110-refreeze-cleanup/collector.ps1) is byte-identical to the
release 1.1.0 batch's (`release110-cleanup/collector.ps1`) and the first
freeze's batch's. The operator ran it read-only on LabDC01 with
`scripts/plan-033/run-post-batch-collector.sh` (exit 0); its standard error
held only the script's one status line and is not banked. The manifest hashes
both files.

## What this does not prove

The final-tag release gate (`scripts/check_release_manifest.py`) checks that
the tagged tree is F plus this batch's evidence and the release records, and
that the evidence is internally consistent. It does not authenticate the
estate runs. That rests on the controller's transcripts: each pack's
`controller.log`, banked verbatim and hashed by the manifest, and the batch
directory on the controller (`progress.jsonl`, `logs/` and the driver's
output), and on the review of the banking commit.
