# WI-059: every finalizer enforces committed source bytes

**Status:** closed 2026-09-08. All 22 estate runs passed on frozen harness
`4cfa9af4b3f12104e8c592cd94df00b88e49beb5`: 21 lane verdicts plus WP-0's separate manifest.
No product capability was expanded by this batch.

**Subsequent qualification:** [WI-060](backup-report-fidelity.md) replaced
only the Scripts metadata and publication verdicts after model/digest changes;
[the WI-062 batch](wi062-batch.md) then requalified 20 of the remaining 21 on
the manifest-form harness (the group-deny lane is pending estate repair). This
batch and its hashes remain immutable; the live registry follows the WI-062
verdicts.

## Enforced behaviour

A normalized clean Git status can hide CRLF working bytes against an LF blob.
All ten supported finalization paths now call the shared raw-byte check in
`oracle_evidence.py` before grading or writing finalized output. It compares
the working file to `git show :<path>` and that index blob to `HEAD:<path>`,
using binary subprocess output. A mismatch, missing blob/file or empty source
set refuses finalization and names the affected paths. It rewrites nothing.
`--no-tag` does not bypass it. Existing candidate, artifact, cleanup, host-role
and clean-tree checks remain in place.

Every lane now binds its finalizer and the shared library, including the
legacy lanes that omitted them. Drivers retain those exact input copies.
WP-0 records both as manifest input artifacts. The operator preflight uses
the same implementation across 57 declared source files in all ten lanes:

```console
uv run python scripts/plan-033/check-bound-source-bytes.py
```

Run it before estate work; finalizers repeat the check afterwards. Thirty
subprocess cases exercise all ten finalizers with hidden CRLF drift, with and
without tagging, and with clean source controls. Refusals produce no new tag
or verdict and preserve existing output. Helper tests additionally cover
staged changes, absent files/blobs, binary bytes and multiple named failures.

## Evidence

| Experiment | Passing run | Banked verdict |
|---|---|---|
| wp0 | `live-synthetic-registry-basic-20260908003108-1493` | [evidence](wp0-evidence/wi059-20260908/wp0/manifest.json) |
| wp1b | `wp1b-writer-20260908003141-5853` | [evidence](wp1b-evidence/wi059-20260908/wp1b/verification.json) |
| wp2 | `wp2-native-import-20260908003212-8693` | [evidence](wp2-evidence/wi059-20260908/wp2/verification.json) |
| wp3-member | `wp3-security-template-20260908003235-1230` | [evidence](wp3-evidence/wi059-20260908/wp3-member/verification.json) |
| wp3-dc | `wp3-security-template-20260908003251-3920` | [evidence](wp3-evidence/wi059-20260908/wp3-dc/verification.json) |
| object-security | `object-security-20260908003317-7120` | [evidence](wp3-evidence/wi059-20260908/object-security/verification.json) |
| scripts-metadata | `scripts-r10-20260908003334-8294` | [evidence](wp1b-evidence/wi059-20260908/scripts-metadata/verification.json) |
| publication | `publication-completeness-20260908003355-4887` | [evidence](wp1b-evidence/wi059-20260908/publication/verification.json) |
| endpoint | `endpoint-observe-20260908003432-9991` | [evidence](wp6-evidence/wi059-20260908/endpoint/verification.json) |
| lsdou-precedence | `rsop-observe-20260908003647-5124` | [evidence](wp6-evidence/wi059-20260908/lsdou-precedence/verification.json) |
| disabled-block-enforced | `rsop-observe-20260908003802-3428` | [evidence](wp6-evidence/wi059-20260908/disabled-block-enforced/verification.json) |
| wmi-filtering | `rsop-observe-20260908003917-3943` | [evidence](wp6-evidence/wi059-20260908/wmi-filtering/verification.json) |
| wmi-filtering-error | `rsop-observe-20260908004032-4871` | [evidence](wp6-evidence/wi059-20260908/wmi-filtering-error/verification.json) |
| computer-security-filtering | `rsop-observe-20260908004147-8239` | [evidence](wp6-evidence/wi059-20260908/computer-security-filtering/verification.json) |
| computer-security-filtering-group-deny | `rsop-observe-20260908004329-7397` | [evidence](wp6-evidence/wi059-20260908/computer-security-filtering-group-deny/verification.json) |
| computer-security-filtering-deny-read | `rsop-observe-20260908004451-9557` | [evidence](wp6-evidence/wi059-20260908/computer-security-filtering-deny-read/verification.json) |
| loopback-merge | `rsop-user-observe-20260908004610-7393` | [evidence](wp9-evidence/wi059-20260908/loopback-merge/verification.json) |
| loopback-replace | `rsop-user-observe-20260908004751-4785` | [evidence](wp9-evidence/wi059-20260908/loopback-replace/verification.json) |
| user-side-disabled | `rsop-user-observe-20260908004932-4297` | [evidence](wp9-evidence/wi059-20260908/user-side-disabled/verification.json) |
| user-security-filtering | `rsop-user-observe-20260908005147-1436` | [evidence](wp9-evidence/wi059-20260908/user-security-filtering/verification.json) |
| user-security-filtering-deny | `rsop-user-observe-20260908005409-2134` | [evidence](wp9-evidence/wi059-20260908/user-security-filtering-deny/verification.json) |
| user-security-filtering-read-deny | `rsop-user-observe-20260908005556-1857` | [evidence](wp9-evidence/wi059-20260908/user-security-filtering-read-deny/verification.json) |


The [batch manifest](wi059-batch.json) records every banked file's SHA-256.
Each pack retains the native result, command outputs, delivered input copies,
controller candidate where applicable, and controller transcript. A renamed
banked verdict retains its exact bytes and its original filename is recorded
in the batch manifest. All runs passed with `source.dirty=false` at the same
revision; every passing run has an `evidence/<run-id>` tag.

The independent [post-batch directory check](wi059-cleanup/directory.json)
confirms both accounts were restored and no experiment OUs, GPOs, groups or
WMI filters survived. Its collector and raw output are hash-bound in the
batch manifest; the capture follows the last completed lane.

The 21 previous live verdicts are explicitly retired in the registry, with
their original bytes and tags preserved. Their historical source-file schemas
are frozen as literal expectations. Older native packs that previously read
missing inputs from the *current* source tree now contain the original input
bytes recovered from their exact recorded commits; every recovered digest was
checked against the unchanged historical verdict before banking.

The environment qualification table, platform registry and capability matrix
record this batch and its subsequent WI-060 successors. Host scope is retained: member-server role 3 and
DC role 5 have separate WP-3 records; only the DC candidate contains Kerberos.
The endpoint and RSoP runs use the real client and verify their teardown.
Scripts remains metadata qualification, object security remains temporary
database serialization, and publication remains a plan comparison.

## Validation

Before the estate session, 3,764 tests passed and 38 skipped, with only the 21
old live-binding checks excluded pending replacement evidence. Coverage was
89.44%, above all floors; Ruff, strict source typing, the static safety gate,
and shell syntax checks passed.

After banking, the full suite passed **3,851 tests, with 38 skips and no
exclusions**, in 98.02 seconds on Windows/Python 3.13. All current live
bindings, WP-0 artifact integrity and source bindings, the 741 banked file
hashes, final cleanup, and lane registry citations passed. Four existing
SQLite resource warnings remain. Ruff, strict typing, static safety and the
57-file source preflight passed. All 88 tracked PowerShell files parsed and
passed PSScriptAnalyzer 1.25.0. Remote CI is recorded in the change's review.

The local identifier denylist was unavailable. Captured data was reviewed as
lab-only and scanned against the supplied credential values before banking;
the existing configured GitHub identifier gate remains the publication check.

The [WI-028 investigation](wi028-searched-som-investigation.md) is independent:
none of these lanes grades `SearchedSOM`. Its scoped-use guidance remains a
prerequisite for any future SOM precedence lane.
