# The release 1.1.0 final batch

**Status:** executed 2026-10-19 (UTC). **All 26 lanes passed** at the freeze
commit `765b30405f59e12f05c518ae16ddcc8437be5cd3` ("Freeze 1.1.0", the commit
1.1.0 ships), every one with `source.dirty=false`: WP-0's manifest plus 25 lane
verdicts. `pending_requalification` in the verdict registry is empty. Every
live verdict records the shipping tree.

This is step 2 of the 1.1.0 approval order (Plan 036, the D-9 addendum; see
[How this release is approved](../release-evidence-1.1.0.md#how-this-release-is-approved)):
every lane live at the freeze commit F, re-run at F as one batch, then the
post-batch directory check. It is banked in commit B, F's child.

The batch was driven by `scripts/plan-033/run-requal-batch.sh` on the second
dev box (the controller), from a clean clone of F, under the driver's per-lane
watchdog and containment layer. It ran from 15:50:37Z to 16:38:02Z, about 47
minutes, and ended `BATCH_DONE rc=0`. No lane timed out, was cancelled, lost
containment, failed its scope, failed to start or left a process behind
(`timed_out`, `cancelled`, `containment_lost`, `scope_failed`, `exec_failed`,
`processes_killed` in every row of the [batch manifest](release110-final-batch.json),
a schema 3 manifest whose every row states `test_scope_tool: false`). The
driver's progress log is the source of every window, budget and exit status.
The packs were staged and the manifest written by
`scripts/plan-033/bank-requal-batch.py` (`stage`, then `manifest --cleanup`),
and `retarget` pointed `tests/fixtures/scenarios/platforms.json` at the new
runs. The tests read this batch from the release evidence report's `freeze`
(`tests/current_qualification.py`), so banking it changed no test.

## What this batch requalifies

Every verdict that was the current qualification at F: the 25 lane verdicts
pending requalification since 2026-10-17, when the whole-value validator sweep
changed files every lane binds, and WP-0's manifest. Those were the
[release 1.1.0 batch](release110-batch.md)'s runs at `de9736e`, with the six
its [successor batch](release110-successors-batch.md) re-ran at `9940561` in
place of theirs. All are retired with their packs and tags unchanged; they
certify nothing for the release.

The lanes measured what they measured for the release candidate, with the same
checks, and every check count is unchanged: the firewall's write leg again
carried the native `B05566AC` registration, report parity again compared 30
Windows-produced backups plus the guest-authored GPO with the WI-072 and WI-073
cases agreeing exactly, and the lifecycle lane's 30 survival cells again agreed
with `lifecycle.SCOPE_SURVIVAL`.

## Evidence

| Experiment | Passing run | Checks | Duration | Commit |
|---|---|---|---|---|
| wp0 | `live-synthetic-registry-basic-20261019155134-6415` | pass | 2m11s | `765b304` |
| wp1b | `wp1b-writer-20261019155315-9046` | pass | 1m04s | `765b304` |
| wp2 | `wp2-native-import-20261019155422-5328` | 18/18 | 0m51s | `765b304` |
| wp3-member | `wp3-security-template-20261019155513-8477` | 20/20 | 0m45s | `765b304` |
| wp3-dc | `wp3-security-template-20261019155452-1648` | 20/20 | 0m42s | `765b304` |
| object-security | `object-security-20261019155637-6032` | 20/20 | 0m40s | `765b304` |
| scripts-metadata | `scripts-r10-20261019155712-3835` | 20/20 | 0m41s | `765b304` |
| publication | `publication-completeness-20261019155758-4530` | 21/21 | 0m50s | `765b304` |
| lifecycle | `lifecycle-20261019155839-7141-cbc99747b80b4645` | 73/73 | 0m40s | `765b304` |
| report-parity | `report-parity-20261019155932-2646` | 26/26 | 2m13s | `765b304` |
| firewall | `firewall-20261019160121-1083478` | 36/36 | 2m22s | `765b304` |
| fdeploy | `fd-20261019160401-3653` | 29/29 | 0m48s | `765b304` |
| endpoint | `endpoint-observe-20261019160520-5888` | pass | 3m39s | `765b304` |
| lsdou-precedence | `rsop-observe-20261019160859-9265` | pass | 2m18s | `765b304` |
| disabled-block-enforced | `rsop-observe-20261019161122-7582` | pass | 2m06s | `765b304` |
| wmi-filtering | `rsop-observe-20261019161318-7026` | pass | 1m51s | `765b304` |
| wmi-filtering-error | `rsop-observe-20261019161509-1243` | pass | 1m50s | `765b304` |
| computer-security-filtering | `rsop-observe-20261019161659-3576` | pass | 1m50s | `765b304` |
| computer-security-filtering-deny-read | `rsop-observe-20261019161851-1960` | pass | 1m52s | `765b304` |
| loopback-merge | `rsop-user-observe-20261019162047-3911` | pass | 2m20s | `765b304` |
| loopback-replace | `rsop-user-observe-20261019162306-2809` | pass | 2m17s | `765b304` |
| user-side-disabled | `rsop-user-observe-20261019162524-3630` | pass | 2m17s | `765b304` |
| user-security-filtering | `rsop-user-observe-20261019162824-9670` | pass | 3m06s | `765b304` |
| user-security-filtering-deny | `rsop-user-observe-20261019163131-5611` | pass | 3m06s | `765b304` |
| user-security-filtering-read-deny | `rsop-user-observe-20261019163359-6458` | pass | 2m20s | `765b304` |
| computer-security-filtering-group-deny | `rsop-observe-20261019163644-2281` | pass | 2m31s | `765b304` |

Packs live under `<family>-evidence/release110-final-20261019/<name>/`. Each
holds the controller's local run directory verbatim, plus
`controller-candidate/` (the builder's output, which the verdict's candidate
hashes bind; WP-0 has none) and `controller.log` (the driver's per-lane log):
the file set of the earlier batches. Every file was checked against the
controller by SHA-256 when it was staged, and the manifest records each one's
hash. No pack holds a controller-side bound source copy. RSoP verdicts written
as `rsop-verdict.json` / `rsop-user-verdict.json` are banked as
`verification.json`; the manifest records each original name.

Every passing run has an annotated `evidence/<run-id>` tag, created by its
finalizer in the controller's clone and peeling to `765b304`. They are pushed
with the batch.

## Run-id stamps

Every run id's UTC stamp falls inside the driver's window for its lane except
one: `wp3-dc`'s, `wp3-security-template-20261019155452-1648`, is 39 seconds
before that lane started (15:55:31Z) and falls inside the preceding
`wp3-member` window. The WP-3 guest script stamps the id with the guest's own
clock (`Get-Date` in `run-wp3-security-template.ps1`), and that lane runs on
the domain controller, so the DC's clock ran at least 39 seconds behind the
controller's. The pack is the DC's run, not a mix-up: its environment records
`LABDC01`, domain role 5, and the driver recorded its run directory at
15:55:31Z, the lane's start. No lane's checks depend on the guest clock, and
Kerberos tolerates far larger skew. The release 1.1.0 batch's DC stamp fell
inside its window. Nothing in this repository measured the DC's clock directly
during this batch.

## Post-batch directory check

The [post-batch directory check](release110-final-cleanup/directory.json),
captured at 2026-10-19T16:38:09Z (the DC's clock) after the last lane, is
clean: the computer and the user account are restored, and no experiment OUs,
GPOs, groups or WMI filters survived. Its
[collector](release110-final-cleanup/collector.ps1) is byte-identical to the
release 1.1.0 batch's (`release110-cleanup/collector.ps1`). The operator ran
it read-only on LabDC01 with `scripts/plan-033/run-post-batch-collector.sh`
(exit 0), where the two release candidate batches ran it through `psdirect.ps1`
by hand; its standard error held
only the script's one status line and is not banked. The manifest hashes both
files.

## What this does not prove

The final-tag release gate (`scripts/check_release_manifest.py`) checks that
the tagged tree is F plus this batch's evidence and the release records, and
that the evidence is internally consistent. It does not authenticate the
estate runs. That rests on the controller's transcripts: each pack's
`controller.log`, banked verbatim and hashed by the manifest, and the batch
directory on the controller (`progress.jsonl`, `logs/` and the driver's
output), and on the review of the banking commit.
