# Scripts metadata lane (R10)

Plan 034 WP-1 extends Plan 033 WP-1B with a Windows-verified R10 Scripts
metadata lane on a clean member
server. Run `scripts-r10-20260907081826-5183` passed all 21 checks from source
commit `f6b06af1bfcc4150b7bdea35b6ecdd660c1d0bc5`; the source checkout was clean.
The immutable evidence tag is `evidence/scripts-r10-20260907081826-5183`.
The [verification record](wp1b-evidence/scripts-metadata/verification.json)
contains the authoritative verdict, source/artifact hashes, environment, and
raw-artifact index.

## Re-run

From a clean checkout with the qualified lab credentials configured, run:

```text
bash scripts/windows-oracle/run-scripts-backup-oracle.sh
```

The runner requires `GPO_STUDIO_LAB_HOST`, `GPO_STUDIO_LAB_GUEST`,
`HYPERV_CONTROL_USERNAME`, and `GUEST_BOOTSTRAP_USERNAME`, plus their matching
`HYPERV_CONTROL_PASSWORD` and `GUEST_BOOTSTRAP_PASSWORD` environment variables
for the existing `psdirect` transport. It builds the
serializer-backed candidate, transfers the ZIP with `psdirect`, creates one
explicit disposable GPO, imports it with `Import-GPO`, obtains an XML
`Get-GPOReport`, re-backs it up with `Backup-GPO`, pulls the run directory, and
finalizes the hash-bound comparison. The PowerShell stage removes its owned
GPO in `finally` and verifies that no owned ID or target name remains.

## Observed native behavior

The candidate contained the two authored machine-side INI files. Windows
reported exactly three startup metadata rows: the command marker with
`/c alpha beta` at order 1, the second command with no parameters at order 2,
and the PowerShell marker with `-Mode Alpha` at order 0. The report exposed
`Command`, optional `Parameters`, `Type=Startup`, `Order`, and
`RunOrder=RunPSFirst` for these rows. The rebackup preserved the two INI byte
streams and the machine Scripts extension pair. Windows also emitted its
four standard script-file wildcard references and normalized the PowerShell
filename to `PSscripts.ini`; these are compared as native layout facts rather
than discarded.

The raw evidence pack is under
[`wp1b-evidence/scripts-metadata`](wp1b-evidence/scripts-metadata/):
`candidate.zip`, `report.xml`, `result.json`, `verification.json`, the input
and rebackup trees, command streams, and builder stdout. The 14 bound source
files are omitted from this bank; recover their exact bytes
with `git show f6b06af1bfcc4150b7bdea35b6ecdd660c1d0bc5:<repository-path>` and
verify its SHA-256 against `verification.json`.

## Boundary

This run proves Scripts metadata import, report exposure, and Backup-GPO
round-trip fidelity for the measured three-entry shape. It does not prove
script payload execution, endpoint processing, application ordering, user
side scripts, GUI editability, or broader CSE behavior. The R2 native captures
remain retained as the original wire-shape anchors; the R10 lab-runbook
anchor supplies the repeatable Windows verdict. The candidate contained no
executable payload files; Windows accepted and re-backed up its metadata in
their absence. Payload delivery and execution remain untested.
