# The release 1.1.0 successor batch

**Status:** executed 2026-10-09 (UTC). **All six lanes passed** on frozen
commit `99405618105edaa4b408be92047f83b6a24dd217` (`fix/gpp-attribute-preservation`, WI-080, WI-081 and WI-082),
every one with `source.dirty=false`. Their verdicts replace the six
[release 1.1.0 batch](release110-batch.md) verdicts that bind the files the
fix changed, which are retired. The other 19 lane verdicts and WP-0 of that
batch, at `de9736e`, still bind the shipping tree unchanged.
`PENDING_REQUALIFICATION` is empty.

## Why these six

The fix changed `gpp.py`, `gpp_adapters.py`, `canonical.py`, `report_parity.py`
and `backup_inventory.py`. Of the 25 live verdicts, exactly six bind any of
them, and no other bound file moved:

- WP-1B binds `gpp.py` and `gpp_adapters.py`;
- scripts-metadata, publication and the firewall bind `gpp.py` and
  `canonical.py`;
- report parity binds `gpp.py`, `gpp_adapters.py`, `report_parity.py` and
  `backup_inventory.py`;
- fdeploy binds `gpp.py` and `backup_inventory.py`.

`tests/test_release110_successors_batch.py` checks that none of the 19 verdicts
left live binds one of the five, and the freshness gate in
`tests/test_committed_evidence.py` checks that every one of them still hashes
to the tree.

## The run

The batch was driven by `scripts/plan-033/run-requal-batch.sh` with the six
lane names, under its per-lane watchdog and containment layer: no lane timed
out, was cancelled, lost containment, failed its scope, failed to execute
(`exec_failed: false` on every row, so the manifest is schema 3) or left a
process behind. The packs were staged and the [manifest](release110-successors-batch.json)
written by `scripts/plan-033/bank-requal-batch.py`, which checked every banked
file against the controller by SHA-256. Each pack has the same file layout as
the run it replaces.

| Experiment | Successor run | Checks | Replaces |
|---|---|---|---|
| wp1b | `wp1b-writer-20261009080228-1849` | pass | `wp1b-writer-20261009001221-5737` |
| scripts-metadata | `scripts-r10-20261009080318-5148` | 20/20 | `scripts-r10-20261009001541-4025` |
| publication | `publication-completeness-20261009080352-5754` | 21/21 | `publication-completeness-20261009001615-4372` |
| report-parity | `report-parity-20261009080432-2383` | 26/26 | `report-parity-20261009001727-3532` |
| firewall | `firewall-20261009080610-2829523` | 36/36 | `firewall-20261009001906-2614294` |
| fdeploy | `fd-20261009080824-4244` | 29/29 | `fd-20261009002120-4293` |

Packs live under `<family>-evidence/release110-rerun-20261009/<name>/`. Every
run has an `evidence/<run-id>` tag at `9940561`. Report parity again compared 30
corpus backups plus the guest-authored GPO and accepted no Studio defect; the
firewall write leg again carried the native `B05566AC` registration.

## Post-batch directory check

The [check](release110-rerun-cleanup/directory.json), captured at
2026-10-09T08:08:58Z after the last lane, is clean: the computer and the user
account are restored, and no experiment OUs, GPOs, groups or WMI filters
survived. The operator ran the same [collector](release110-rerun-cleanup/collector.ps1)
as the release 1.1.0 batch (byte-identical to `release110-cleanup/collector.ps1`)
read-only on LabDC01 through the frozen worktree's `psdirect.ps1`.

## Closed by this batch

WI-080, WI-081 and WI-082: each closes when the requalification runs of the
lanes binding the changed files bank on them, and these six are those runs.
