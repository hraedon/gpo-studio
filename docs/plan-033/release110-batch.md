# The release 1.1.0 requalification batch

**Status:** executed 2026-10-09 (UTC). **All 26 lanes passed** on frozen commit
`de9736ed3a4148b91cf2267fbe4640e260cc232f` (batch 2, the chunked psdirect transport of WI-078, and the GPP
fixes of WI-072, WI-073 and WI-079), every one with `source.dirty=false`:
WP-0's manifest plus 25 lane verdicts. `PENDING_REQUALIFICATION` is empty.
1.1.0 is qualified by one commit.

The batch was driven by `scripts/plan-033/run-requal-batch.sh` on the second
dev box, under the driver's per-lane watchdog and containment layer: no lane
timed out, was cancelled, lost containment or left a process behind
(`timed_out`, `cancelled`, `containment_lost`, `scope_failed`,
`processes_killed` in every row of the [batch manifest](release110-batch.json),
a schema 2 manifest whose every row states `test_scope_tool: false`). The
driver's progress log is the source of every window, budget and exit status.
The packs were staged and the manifest written by
`scripts/plan-033/bank-requal-batch.py`, the Plan 034 banking procedure made
into a tool; run ids, commit and pack paths are read from the manifest by
`tests/test_release110_batch.py` and the lane evidence tests, so a later batch
re-points them by replacing the manifest.

## The superseded attempt at `2f21e7c`

The first attempt ran all 26 lanes at `2f21e7c` (batch 2 alone) on
2026-10-08. 25 passed. The report-parity lane's `psdirect.ps1` push of its
1.45 MB STORED archive stalled on an idle WinRM connection, and nothing timed
out, because neither the transport nor the driver had a wall-clock bound. The
operator killed the lane after an hour (exit 143); a re-run of the lane alone
hung the same way. The cause was `Copy-Item -ToSession` from the Linux
controller stalling for any file above roughly 256 KB.

The fix (WI-078: a chunked, verified and bounded transport, and a per-lane
watchdog in the driver) changed `scripts/windows-oracle/psdirect.ps1`, which
every lane binds. Every verdict of the attempt stopped binding before it could
be banked, so it was superseded: its packs are not in the repository, and the
manifest records it under `superseded_attempts` (commit, every run id and
evidence tag, every window, and the killed lane). Its 25 `evidence/<run-id>`
tags at `2f21e7c` are kept as history.

## What this batch requalifies

Every verdict live before it: the Plan 034 batch's 21, its object-security
successor at `1fb3f56`, and the four lanes banked after that batch (lifecycle
at `3513052`, report parity at `a1c280b`, the firewall at `a6e0002`, fdeploy at
`df713ef`). `psdirect.ps1` alone expired all 25; batch 2 and the GPP fixes
moved more besides. All 25 are retired with their packs and tags unchanged.
This is the first batch to run the lifecycle, report-parity, firewall and
fdeploy lanes.

Lane facts worth reading the verdicts for:

- **WP-1B** carries eight candidates: batch 2's `gppregistry-both` and the GPP
  Registry items in `mixed-all` imported on Windows and re-exported as written
  (WI-075).
- **Report parity** compared 30 Windows-produced backups (batch 2 added the
  three GPP Registry captures) plus a guest-authored GPO, and accepted no
  Studio defect: the WI-072 Power Options case and both WI-073 scheduled-task
  cases agreed with Windows exactly, which the lane's
  `fixed_work_item_cases_agree_exactly` check requires. Its 1.45 MB candidate,
  the archive that hung the first attempt, was delivered by the chunked
  transport.
- **Firewall**: the write leg imported Studio's export registered
  `[{35378EAC-…}{B05566AC-…}]`, the native pair; the lane's first run carried
  `{D02B1F72-…}`. GPME display remains unmeasured (WI-077).
- **Lifecycle**: all 30 survival cells agreed again with
  `lifecycle.SCOPE_SURVIVAL`.

## Evidence

| Experiment | Passing run | Checks | Commit |
|---|---|---|---|
| wp0 | `live-synthetic-registry-basic-20261009001124-4850` | pass | `de9736e` |
| wp1b | `wp1b-writer-20261009001221-5737` | pass | `de9736e` |
| wp2 | `wp2-native-import-20261009001318-2870` | 18/18 | `de9736e` |
| wp3-member | `wp3-security-template-20261009001358-5650` | 20/20 | `de9736e` |
| wp3-dc | `wp3-security-template-20261009001426-5385` | 20/20 | `de9736e` |
| object-security | `object-security-20261009001510-7162` | 20/20 | `de9736e` |
| scripts-metadata | `scripts-r10-20261009001541-4025` | 20/20 | `de9736e` |
| publication | `publication-completeness-20261009001615-4372` | 21/21 | `de9736e` |
| lifecycle | `lifecycle-20261009001644-6217-f2f5047fed814807` | 73/73 | `de9736e` |
| report-parity | `report-parity-20261009001727-3532` | 26/26 | `de9736e` |
| firewall | `firewall-20261009001906-2614294` | 36/36 | `de9736e` |
| fdeploy | `fd-20261009002120-4293` | 29/29 | `de9736e` |
| endpoint | `endpoint-observe-20261009002229-7198` | pass | `de9736e` |
| lsdou-precedence | `rsop-observe-20261009002514-3293` | pass | `de9736e` |
| disabled-block-enforced | `rsop-observe-20261009002658-3387` | pass | `de9736e` |
| wmi-filtering | `rsop-observe-20261009002841-9327` | pass | `de9736e` |
| wmi-filtering-error | `rsop-observe-20261009003024-5445` | pass | `de9736e` |
| computer-security-filtering | `rsop-observe-20261009003207-9958` | pass | `de9736e` |
| computer-security-filtering-deny-read | `rsop-observe-20261009003349-5735` | pass | `de9736e` |
| loopback-merge | `rsop-user-observe-20261009003535-1462` | pass | `de9736e` |
| loopback-replace | `rsop-user-observe-20261009003744-5527` | pass | `de9736e` |
| user-side-disabled | `rsop-user-observe-20261009003952-3546` | pass | `de9736e` |
| user-security-filtering | `rsop-user-observe-20261009004238-6019` | pass | `de9736e` |
| user-security-filtering-deny | `rsop-user-observe-20261009004533-3353` | pass | `de9736e` |
| user-security-filtering-read-deny | `rsop-user-observe-20261009004749-4067` | pass | `de9736e` |
| computer-security-filtering-group-deny | `rsop-observe-20261009005022-4207` | pass | `de9736e` |

Packs live under `<family>-evidence/release110-20261009/<name>/`, including the
four lanes that had their own paths before. Each holds the controller's local
run directory verbatim, plus `controller-candidate/` (the builder's output,
which the verdict's candidate hashes bind) and `controller.log` (the driver's
per-lane log): the Plan 034 file set. Every file was checked against the
controller by SHA-256 when it was staged. No pack holds a controller-side
bound source copy. RSoP verdicts written as `rsop-verdict.json` /
`rsop-user-verdict.json` are banked as `verification.json`; the manifest
records each original name. Every passing run has an `evidence/<run-id>` tag
at `de9736e`.

Every run id's UTC stamp falls inside the driver's window for that lane: the
estate ran at real time.

The [post-batch directory check](release110-cleanup/directory.json), captured
at 2026-10-09T00:51:59Z after the last lane, is clean: the computer and the
user account are restored, and no experiment OUs, GPOs, groups or WMI filters
survived. Its [collector](release110-cleanup/collector.ps1) is the Plan 034
collector extended to the newer lanes: lifecycle groups (`zzlc-*`) and WMI
filters (`zz-studio*`), and firewall GPOs (`StudioFwLane*`); the existing
`zz-studio*` OU and GPO patterns already covered the lifecycle, fdeploy and
report-parity objects. The operator ran the committed `collector.ps1` (the same
bytes the manifest hashes) read-only on LabDC01 through the frozen worktree's
`psdirect.ps1` (exit 0, empty stderr), not through
`scripts/plan-033/run-post-batch-collector.sh`, which was written for the
purpose and has not yet been used on the estate.
