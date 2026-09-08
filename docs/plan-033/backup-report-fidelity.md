# Backup and report inventory fidelity

Status: complete for the bounded WI-060 tranche of Plan 034 WP-2 (2026-09-08).
Implemented on the public import/report paths; all 27 native capture replay
cases pass. Both affected live qualifications were replaced on clean source.

## What the native captures exposed

Native `Backup.xml` declarations were discarded after reading GPO identity and
side options. An unmodeled Scripts extension consequently appeared as
`machine/unknown`, with only a file count. The native Scripts report identifies
three startup commands, but none appeared in Studio's report.

The proposed comparison of backup-handler count against `cse_metadata` count
was also the wrong discriminator. Windows includes the Registry backup handler
even when it has no payload and uses an Unknown Extension handler to copy some
GPP files. Handler declarations, extension registration pairs, file inventories
and native setting observations are different sets. Equality of their counts
would be a false requirement.

## Delivered behaviour

Native imports retain `Backup.xml` and optional `gpreport.xml` as exact bytes,
encoded as base64 in `GPO.backup_inventory`. The snapshot inventories every
captured file below `DomainSysvol/GPO` by relative path, size and SHA-256,
including root content such as `GPO.cmt` and `Adm` templates. It survives SQLite
reopen, immutable revisions, forks and serialization in a Studio manifest.

The existing plain-text report now appends the imported registration strings,
backup handlers and their references, file metadata, and native per-side
extension observations. Unknown XML elements and attributes remain visible.
The appendix explicitly describes a historical source snapshot: later edits
do not update it. The current modeled settings remain in the main report.

Native XML bytes are retained; other payload bytes are **not**. The report says
to keep the original backup and labels unmodeled CSE files as metadata only.
Neither the snapshot nor its native commands authorize payload execution or
make an unmodeled family exportable. Existing publication refusals remain.

The review digest covers source provenance. The policy-semantic digest excludes
it, and GPOs without an inventory keep their existing review digests. XML and
file-list parsing enforce structural, size and path limits; mismatched backup
and report GPO identities are refused. Retained XML also rejects `cpassword`.

## Independent comparison and scope

`tests/test_backup_report_inventory.py` sends Windows-produced backups through
the public import and report endpoints, then independently reads the original
XML and file bytes to compare every captured path/hash/size, registration,
handler GUID, extension leaf value and attribute value. It also checks forks,
reopened workspaces, bundle manifests, missing reports, source-only digest
changes, extra root files and unfamiliar native fields.

The initial corpus has 26 native backups: 16 existing GPMC preference captures
and ten native backups/rebackups from the immutable WI-059 batch. It includes
registry on both sides, Scripts, Drives, local users/groups, environment,
files/folders, INI, power, printers, scheduled/immediate tasks, Services,
shortcuts and item targeting. Studio-produced input candidates are excluded
from the comparison corpus. The fresh Scripts rebackup from the affected-lane
requalification brings the regression corpus to 27 backups.

This establishes observation retention and report inventory for those captures.
It does not establish typed editing, full GPMC report equivalence, endpoint
processing or family-wide Windows qualification. Security ACL interpretation,
Software Installation, Folder Redirection and the legacy IE extension remain
outside this measured corpus. Plan 034's broader family investigation remains
open.

## Affected Windows qualifications

Only the Scripts metadata and publication-completeness finalizers bind the
changed `model.py` and `canonical.py`. Both replacement runs passed 21/21 on
LabMS01, member-server role 3, using clean frozen revision
`b5ccbabd19b7ed661312915ca4b314dc27bbdd6e`.

| Lane | Run | Evidence |
|---|---|---|
| Scripts metadata | `scripts-r10-20260908013518-2476` | [complete pack](wp1b-evidence/backup-report-20260908/scripts-metadata/verification.json) |
| Publication completeness | `publication-completeness-20260908013539-2644` | [complete pack](wp1b-evidence/backup-report-20260908/publication/verification.json) |

The [batch manifest](backup-report-batch.json) hashes every banked artifact,
including native results, command output, source snapshots and controller
candidates. Both native results confirm removal of their owned GPOs and
restored state. Each run has an `evidence/<run-id>` tag. The live registry,
platform notes and environment table name these successors; the original
WI-059 packs, hashes and tags remain unchanged.

The other 19 live verdicts and WP-0 still match their existing bound source
bytes. Scripts remains metadata/rebackup qualification, and publication
completeness measures the plan against native import output; neither run
executes script payloads or a Studio publication.

Local verification on Windows/Python 3.13: 3,904 tests passed, 38 skipped;
branch coverage 89.48%, all per-module floors satisfied. Ruff, strict mypy,
the static safety gate and the raw-source-byte preflight passed. Remote CI
remains the check for Linux and both supported Python versions.

## Follow-ups filed

Reviewing this tranche opened [WI-061](../work-items.md#wi-061--retained-native-xml-is-copied-into-every-revision-snapshot)
(the retained bytes were serialized into every GPO list row, and are still
stored per revision) and [WI-062](../work-items.md#wi-062--evidence-packs-duplicate-source-bytes-that-are-already-bound-to-head)
(packs bank source bytes that `assert_bound_source_bytes` already binds to
HEAD). The response half of WI-061 is fixed; both remainders touch modules the
two runs above bind, so they land with an estate batch, not on their own.
