# The release 1.2.0 batch

**Status:** executed 2026-11-02 (UTC). **All 26 lanes passed** at the freeze
commit `f9715048fafa209069d9a5c2f78ae4a36d3a49e6` ("Re-freeze 1.2.0", the commit 1.2.0 ships), every one with
`source.dirty=false`: WP-0's manifest plus 25 lane verdicts.
`pending_requalification` in the verdict registry is empty. Every live
verdict records the shipping tree.

This is step 2 of the 1.2.0 approval order (Plan 036, the D-9 addendum; see
[How this release is approved](../release-evidence-1.2.0.md#how-this-release-is-approved)):
every lane live at the freeze commit F, re-run at F as one batch, then the
post-batch directory check. It is banked in commit B, F's child.

## Why every lane ran

Before F the tree was qualified by the
[release 1.1.0 re-freeze batch](release110-refreeze-batch.md), 26 of 26 at
`5ffa026` on 2026-10-20, the batch 1.1.0 shipped on. Changes merged after
1.1.0 had expired its 25 lane verdicts, which were pending requalification:
all 25 bind `scripts/windows-oracle/psdirect.ps1`, which the realm fix (#157)
changed, and several also bind other files changed since
(`src/gpo_studio/export.py`, `gpp.py`, `registry_pol.py`, `validation.py`,
`publication.py` and `safe_io.py`). The owner authorized releasing 1.2.0 by a
full requalification at one freeze commit on 2026-10-29, and the release gate
holds every live verdict to the freeze commit's batch.

The batch was driven by `scripts/plan-033/run-requal-batch.sh` on the second
dev box (the controller), from a clean clone of F, under the driver's per-lane
watchdog and containment layer. It ran from 14:06:10Z to 14:48:39Z, about 42
minutes, and ended `BATCH_DONE rc=0`. No lane timed out, was cancelled, lost
containment, failed its scope, failed to start or left a process behind
(`timed_out`, `cancelled`, `containment_lost`, `scope_failed`, `exec_failed`,
`processes_killed` in every row of the [batch manifest](release120-batch.json),
a schema 3 manifest whose every row states `test_scope_tool: false`). The
driver's progress log is the source of every window, budget and exit status.
The packs were staged and the manifest written by
`scripts/plan-033/bank-requal-batch.py` (`stage`, then `manifest --cleanup`),
and `retarget` pointed `tests/fixtures/scenarios/platforms.json` at the new
runs. The tests read this batch from the release evidence report's `freeze`
(`tests/current_qualification.py`), so banking it changed no test.

## The first freeze's batch

This is the second batch for 1.2.0. The first freeze commit,
`a4306361e4829449ced64d173978dcb532d2814c`, was requalified 26 of 26 earlier
the same day (12:15:08Z to 13:02:10Z, from a separate clean clone and batch
directory on the controller). That batch was never banked: building its
banking commit showed that a test the 1.2.0 preparation added,
`test_a_predecessor_report_that_is_not_its_release_fails`, holds only while
the release report's `freeze` is null, and the release gate freezes every test
at F. The re-freeze commit `f971504`, the first freeze's child, fixes that test
and the release records and changes no application code. The first batch
certifies nothing; its evidence tags were never pushed, and nothing from its
controller clone or batch directory is banked here.

## What this batch requalifies

Every verdict that was the current qualification at F: the release 1.1.0
re-freeze batch's 25 lane verdicts, all pending requalification, and WP-0's
manifest. The 25 are retired and WP-0's manifest replaced, with their packs
and tags unchanged; they certify nothing for this release.

The lanes measured what they measured for 1.1.0, with the same checks, and
every check count and every pack's file count is unchanged (1572 files): the
firewall's write leg again carried the native `B05566AC` registration, report
parity again compared 30 Windows-produced backups plus the guest-authored GPO
with the WI-072 and WI-073 cases agreeing exactly, and the lifecycle lane's 30
survival cells again agreed with `lifecycle.SCOPE_SURVIVAL`.

## Evidence

| Experiment | Passing run | Checks | Duration | Commit |
|---|---|---|---|---|
| wp0 | `live-synthetic-registry-basic-20261102140640-6683` | pass | 1m04s | `f971504` |
| wp1b | `wp1b-writer-20261102140736-4397` | pass | 0m46s | `f971504` |
| wp2 | `wp2-native-import-20261102140826-7467` | 18/18 | 0m43s | `f971504` |
| wp3-member | `wp3-security-template-20261102140906-1647` | 20/20 | 0m36s | `f971504` |
| wp3-dc | `wp3-security-template-20261102140836-7161` | 20/20 | 0m37s | `f971504` |
| object-security | `object-security-20261102141020-2204` | 20/20 | 0m35s | `f971504` |
| scripts-metadata | `scripts-r10-20261102141051-8240` | 20/20 | 0m34s | `f971504` |
| publication | `publication-completeness-20261102141125-6887` | 21/21 | 0m35s | `f971504` |
| lifecycle | `lifecycle-20261102141155-6172-1237168f99c149d5` | 73/73 | 0m33s | `f971504` |
| report-parity | `report-parity-20261102141241-1684` | 26/26 | 2m01s | `f971504` |
| firewall | `firewall-20261102141420-2589712` | 36/36 | 1m59s | `f971504` |
| fdeploy | `fd-20261102141636-5061` | 29/29 | 0m45s | `f971504` |
| endpoint | `endpoint-observe-20261102141748-2070` | pass | 2m49s | `f971504` |
| lsdou-precedence | `rsop-observe-20261102142041-3460` | pass | 1m57s | `f971504` |
| disabled-block-enforced | `rsop-observe-20261102142234-5360` | pass | 1m46s | `f971504` |
| wmi-filtering | `rsop-observe-20261102142419-3472` | pass | 1m45s | `f971504` |
| wmi-filtering-error | `rsop-observe-20261102142605-7801` | pass | 1m44s | `f971504` |
| computer-security-filtering | `rsop-observe-20261102142752-1196` | pass | 1m46s | `f971504` |
| computer-security-filtering-deny-read | `rsop-observe-20261102142938-5258` | pass | 1m47s | `f971504` |
| loopback-merge | `rsop-user-observe-20261102143130-9149` | pass | 2m14s | `f971504` |
| loopback-replace | `rsop-user-observe-20261102143344-2571` | pass | 2m13s | `f971504` |
| user-side-disabled | `rsop-user-observe-20261102143556-1486` | pass | 2m12s | `f971504` |
| user-security-filtering | `rsop-user-observe-20261102143845-8266` | pass | 3m02s | `f971504` |
| user-security-filtering-deny | `rsop-user-observe-20261102144155-5489` | pass | 3m11s | `f971504` |
| user-security-filtering-read-deny | `rsop-user-observe-20261102144427-7670` | pass | 2m26s | `f971504` |
| computer-security-filtering-group-deny | `rsop-observe-20261102144719-3861` | pass | 2m35s | `f971504` |

Packs live under `<family>-evidence/release120-20261102/<name>/`. Each holds
the controller's local run directory verbatim, plus `controller-candidate/`
(the builder's output, which the verdict's candidate hashes bind; WP-0 has
none) and `controller.log` (the driver's per-lane log): the file set of the
earlier batches. Every file was checked against the controller by SHA-256 when
it was staged, and the manifest records each one's hash. No pack holds a
controller-side bound source copy. RSoP verdicts written as
`rsop-verdict.json` / `rsop-user-verdict.json` are banked as
`verification.json`; the manifest records each original name.

Every passing run has an annotated `evidence/<run-id>` tag, created by its
finalizer in the controller's clone and peeling to `f971504`. They are pushed
with the batch.

## Run-id stamps

A run id's UTC stamp has one-second precision, so it is compared with the
driver's window for its lane at that precision, the window's start truncated
to the second. So compared, every stamp falls inside its lane's window except
one. The exception, as in the 1.1.0 batches, is `wp3-dc`'s stamp,
`wp3-security-template-20261102140836-7161`: it is 45 seconds before that lane
started (14:09:21Z) and falls inside the `wp2` window two lanes earlier. The
WP-3 guest script stamps the id with the guest's own clock (`Get-Date` in
`run-wp3-security-template.ps1`), and that lane runs on the domain controller,
so the DC's clock ran at least 45 seconds behind the controller's (44 seconds
in the unbanked first-freeze batch, 42 in the 1.1.0 re-freeze batch). The pack
is the DC's run, not a mix-up: its environment records `LABDC01`, domain role
5, and the driver recorded its run directory (`wp3-oracle-run-20261102140922-...`)
at 14:09:22Z, the lane's start.
No lane's checks depend on the guest clock. Nothing in this repository
measured the DC's clock directly during this batch.

Two other timing observations. First, on the controller's own clock, three
times a lane's recorded start reads earlier than the previous lane's recorded
end, although the driver runs lanes one after the other and reads both times
with `date -u`: `loopback-replace` started 0.25 seconds before
`loopback-merge` ended (14:32:59Z), `user-security-filtering` 0.73 seconds
before `user-side-disabled` ended (14:37:24Z), and
`computer-security-filtering-group-deny` 0.32 seconds before
`user-security-filtering-read-deny` ended (14:46:04Z). The unbanked first
batch showed the same once (0.27 seconds). So the controller's wall clock read
earlier between those readings; the controller runs `chrony`, and no record
here says whether it stepped the clock. Every lane passed, and no check reads
the controller's clock. Second, the post-batch check's capture time
(14:47:47Z, the DC's clock, below) reads about 52 seconds before the driver's
`BATCH_DONE` (14:48:39Z, the controller's clock), but the controller wrote the
check's `directory.json` at 14:48:54Z by its own clock, about 15 seconds after
`BATCH_DONE`. The check ran after the batch; the difference is the DC's clock
running behind, as the `wp3-dc` stamp shows.

## Post-batch directory check

The [post-batch directory check](release120-cleanup/directory.json), captured
at 2026-11-02T14:47:47Z (the DC's clock) after the last lane, is clean: the
computer and the user account are restored, and no experiment OUs, GPOs,
groups or WMI filters survived. Its
[collector](release120-cleanup/collector.ps1) is byte-identical to the release
1.1.0 re-freeze batch's (`release110-refreeze-cleanup/collector.ps1`). The
operator ran it read-only on LabDC01 (exit 0). The manifest hashes both files.

## What this does not prove

The final-tag release gate (`scripts/check_release_manifest.py`) checks that
the tagged tree is F plus this batch's evidence and the release records, and
that the evidence is internally consistent. It does not authenticate the
estate runs. That rests on the controller's transcripts: each pack's
`controller.log`, banked verbatim and hashed by the manifest, and the batch
directory on the controller (`progress.jsonl`, `logs/` and the driver's
output), and on the review of the banking commit.
