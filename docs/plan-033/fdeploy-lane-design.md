# fdeploy lane design

Status: **built, not yet run on the estate** (2026-10-08). The lane has no
verdict, so nothing here is Windows-verified. Its claims about what
`Import-GPO`, `Backup-GPO` and `Get-GPOReport` do with these bytes are
hypotheses until the first run.

This is the exit the [2026-10-07 direction](../direction-2026-10-07-plan-034-completion.md)
set for `fdeploy.py`: "banked R3 bytes go through `Import-GPO`, then
`Backup-GPO`/`Get-GPOReport`, and the result is compared with the parse." R12
(the console-driver authoring capture WI-066 owes) is a separate piece of work
and is not attempted here.

## The question

For each case: **does Studio's reader say about an `fdeploy1.ini` what Windows
says about it, once Windows has imported it, reported it and backed it up?**

"What the reader says" is limited to what `fdeploy.py` actually claims:

- the folder GUID and the principal SID that key each `[{folder}_{principal}]`
  section;
- the `FullPath` value;
- the `Flags` integer, carried verbatim and never decoded (WI-066);
- the section and entry structure, through `validate_fdeploy` (no findings) and
  the parse warnings (none);
- the encoding facts: UTF-16LE with a BOM and CRLF line endings, checked
  strictly by `decode_fdeploy`.

"What Windows says" comes from two sources: the `FolderRedirectionSettings`
extension in a fresh `Get-GPOReport -ReportType Xml` (`Folder/Id`,
`Location/SecurityGroup/SID`, `Location/DestinationPath`), and the bytes
`Backup-GPO` re-exports.

## What the 2026-10-08 probe established first

A one-off capture on LabMS01 (`gpo-studio-evidence/inbox/fdeploy-flags-20261008/`,
not banked in this repository) wrote R3's two files straight into the SYSVOL of
15 disposable GPOs, with only `Flags` changed. Each GPO carried the measured
extension pair `[{25537BA6-77A8-11D2-9B6C-0000F8080861}{88E729D6-BDC1-11D1-BD2A-00C04FB9603F}]`.
The probe then reported every GPO and backed one up. The lane's design rests on
four of its results:

1. **`Backup-GPO` copies these bytes verbatim.** The probe's native backup of
   the 1021 GPO holds `fdeploy1.ini` and `fdeploy.ini` with R3's exact SHA-256.
   So the lane **demands byte identity** for the re-export rather than parse
   equality. If `Import-GPO` turns out to rewrite the file, the lane fails, and
   `byte_differences` records the encoding facts, the reader-level changes and
   a line diff. Relaxing to parse equality would then be a decision someone
   takes, not something the finalizer does automatically.
2. **The report engine does not read `Flags` as a flat bit field.** Clearing or
   setting one bit can change several rendered options at once: 1017 sets
   `GrantExclusiveRights`, `FollowParent` and `DoNotCare` together, and
   changes `PolicyRemovalBehavior`. So nothing about options is asserted.
3. **`DestinationPath` is not `FullPath` for every `Flags` value.** For 765
   and 2045, Windows renders the folder with an empty `DestinationPath` while
   the file carries a `FullPath`. For 0, 509, 893, 957 and 989 it renders no
   folder at all, only `FRSettingRead failed with -2147467259`. The
   reader/report agreement this lane checks therefore holds only for the
   `Flags` values it runs. It is not a claim about the format.
4. **The backup skeleton.** The probe's `Backup.xml` shows what Windows writes
   for this extension: a `Folder Redirection` `GroupPolicyExtension` whose
   wildcard entry carries `bkp:ReEvaluateFunction="FRValidateSettings"`, the
   path in `FilePaths`, and a generic `{F15C46CD-…}` directory entry for
   `Documents & Settings`.

| Flags | Report rendering (relative to 1021) | In the lane |
|---:|---|---|
| 1021 | base: `MoveContents=true`, the other booleans false, `RestoreContents`, `GP`, `PrimaryComputerPolicyDisabled` | yes (R3 verbatim) |
| 1020 | `MoveContents=false` | yes |
| 1023 | `FollowParent=true` | yes |
| 3069 | `RedirectToLocal=true` | yes |
| 1017 | `GrantExclusiveRights`, `FollowParent`, `DoNotCare` true; `LeaveContents` | no (renders path) |
| 1013 | `GrantExclusiveRights`, `FollowParent` true; `LeaveContents` | no (renders path) |
| 1005 | `DoNotCare=true`; `LeaveContents` | no (renders path) |
| 5117 | same as 1017 | no (renders path) |
| 765 | `DoNotCare=true`; **empty `DestinationPath`** | no |
| 2045 | `DoNotCare`, `RedirectToLocal` true; `LeaveContents`; **empty `DestinationPath`** | no |
| 0, 509, 893, 957, 989 | **no folder rendered**; `FRSettingRead failed` | no |

This reads Windows' *interpretation* of the word. What GPMC *writes* for each
checkbox is still R12's question, so WI-066 stays open.

## The cases

| Case | Bytes | Origin |
|---|---|---|
| `r3-flags-1021` | R3's `fdeploy1.ini` and `fdeploy.ini`, byte for byte | GPMC, 2026-09-04 |
| `r3-flags-1020` | R3's, with the four ASCII digits of `Flags` replaced | this builder (the probe's construction) |
| `r3-flags-1023` | as above | this builder |
| `r3-flags-3069` | as above | this builder |

The builder rebuilds R3's bytes from the committed transcripts and refuses to
build unless they match the provenance record's `raw_sha256`, size and CR/LF
counts. The three variant cases change only the digits, so their files are the
same length as R3's. Windows did not write those three files. What they add is
Windows' reading of each value through `Import-GPO`, and Studio's reading of
Windows' re-export of them.

Each case is its own GPMC backup: `manifest.xml`, `{ID}/Backup.xml`,
`{ID}/bkupInfo.xml`, and `DomainSysvol/GPO/User/Documents & Settings/` holding
both files. Identity is synthetic: `synthetic.test`, uuid5 GUIDs, the
`UNKNOWN` controller, and display names `zz-studio-fdeploy-<case>`. The
security descriptor is Studio's domain-neutral one, which `Import-GPO`
accepted in the publication and WP-2 lanes. The candidate carries no estate
identifier.

## The lane

New files only. The lane edits no shared bound file, so it expires no verdict.

| File | Role |
|---|---|
| `src/gpo_studio/fdeploy_parity.py` | Reads the report's `FolderRedirectionSettings` (rows plus recorded options) and identity, extracts the reader's claims, compares the two, and records byte differences. Unbound before this lane; composes no fdeploy bytes |
| `scripts/plan-033/build-fdeploy-candidate.py` | `fdeploy-cases.zip` plus `expected.json`. Deterministic. Refuses a case Studio would not read as provenance says |
| `scripts/windows-oracle/run-fdeploy-lane.ps1` | Guest (LabMS01, Windows PowerShell 5.1). Runs once per case; see below |
| `scripts/windows-oracle/run-fdeploy-oracle.sh` | Controller driver. Prints `LOCAL_RUN_DIR=` and `CANDIDATE_DIR=`, and passes the guest's exit status to the finalizer |
| `scripts/windows-oracle/finalize_fdeploy_run.py` | Grades the run, binds source as `(commit, path, sha256)`, writes `verification.json`, and tags on a pass |
| `tests/test_fdeploy_lane.py` | Parity unit tests, candidate tests, a simulated run graded by the real finalizer (control plus one mutation per check), driver and guest structure, and the guest run against mocked cmdlets |

### Guest, per case

The steps run in this order:

1. Register a run-unique name `zz-studio-fd-<run_id>-<n>`, after a collision
   check and **before** `New-GPO`.
2. Create the disposable GPO with `New-GPO`. It is never linked.
3. `Import-GPO` the case's backup into it.
4. Read `gPCUserExtensionNames` from AD, and list the files (length and
   SHA-256) under `User\Documents & Settings` in the GPO's SYSVOL, found
   through `gPCFileSysPath`.
5. Take a fresh `Get-GPOReport -ReportType Xml` and count its links.
6. Run `Backup-GPO`. Read the re-exported `fdeploy1.ini` and `fdeploy.ini`
   into `result.json` as base64 plus SHA-256. The whole backup directory is
   also pulled.
7. Remove the GPO by ID and by registered name, with retries and backoff, then
   strictly re-query by ID and by name to confirm it is gone. A final sweep
   removes anything that still holds a registered name. Nothing unregistered is
   ever touched.

### What a passing verdict asserts

Per case (each is `every_case_<name>` in the verdict):

- `fresh_report_delivered_intact`: the pulled report's SHA-256 equals the hash
  the guest recorded.
- `fresh_report_identifies_owned_gpo`: the report's identifier GUID equals the
  owned GPO's and its name equals the registered target. The domain is
  compared ignoring case. A replayed or foreign report fails.
- `rebackup_is_of_owned_gpo`: the re-export's manifest names the owned GPO,
  and its backup ID is the one the guest reported.
- `rebackup_bytes_delivered_intact`: the guest's base64 decodes to bytes whose
  SHA-256 and length match what the guest recorded, and equal the pulled file.
- `imported_sysvol_holds_candidate_bytes`: after `Import-GPO`, SYSVOL holds
  exactly the two files, with the candidate's lengths and hashes.
- `rebackup_bytes_equal_candidate`: Windows' re-exported `fdeploy1.ini` and
  `fdeploy.ini` are byte-identical to the candidate's. Every difference is
  recorded either way.
- `studio_reads_windows_rebackup`: `read_backup` over Windows' own backup
  yields one GPO with the owned GUID and a `GPO.fdeploy` whose claims equal the
  provenance values (folder `{FDD39AD0-…}`, principal `s-1-1-0`, the
  `zz-studio-fileserver` `FullPath`, and the case's `Flags` and spelling). It
  also has no structural finding and no parse warning, and its native digest
  equals Windows' bytes.
- `windows_report_renders_expected_redirections`: there is exactly one
  `FolderRedirectionSettings` extension on the user side, with no error and
  nothing on the computer side. Its `(Id, SID, DestinationPath)` rows equal the
  provenance rows exactly, with no case folding.
- `studio_reader_agrees_with_windows_report`: Studio's reading of Windows'
  backup and Windows' fresh rendering agree row for row. An empty rendering or
  an error never counts as agreement.
- `rebackup_report_matches_fresh_report`: the backup's `gpreport.xml`
  rendering, options included, equals the fresh one.

Whole-run checks follow the report-parity lane:

- The guest exited with status zero.
- The result schema is exact, and the candidate carries exactly the four
  required cases.
- Every case ran exactly once, with the candidate's backup and source IDs.
- Every owned GPO ID is a unique GUID under this run's prefix.
- Every GPO was unlinked, removed, and confirmed absent, and the cleanup scan
  is clean.
- No harness error was reported.
- The host is a member server, and its environment matches the frozen spec.
- Raw stdout and stderr exist for every import, report and backup.
- The deployed script is identical to source, the candidate was delivered
  intact, and the source tree is clean.

Before the expectation is trusted, the finalizer **re-runs the bound
builder** and requires a byte-identical `expected.json` and archive.
`expected.json` never reaches the guest. A check that is missing from the
verdict fails it (`checks_complete`). A case that cannot be read fails all of
that case's checks and names the error.

### Recorded, not asserted

- Windows' option rendering for every case, and whether it matches the probe
  (`matches_probe_20261008`). This is data for WI-066.
- `gPCUserExtensionNames` after `Import-GPO`.
- The encoding facts of Windows' bytes, and `byte_differences` for both files.

### Unmeasured, said out loud

- **Whether `Import-GPO` keeps the bytes.** The probe never imported anything.
  The Folder Redirection backup entry names `FRValidateSettings` as a
  re-evaluation hook, and nobody has measured what it does without a migration
  table. If it rewrites the file (normalizing the SID's case, say, or dropping
  the five-space preamble), the lane fails on the first run and records exactly
  how.
- **Whether `Import-GPO` accepts this skeleton at all.** The skeleton follows
  Windows' own `Backup.xml` with synthetic identity. The empty
  `SecurityGroups` element and the domain-neutral descriptor are what Studio's
  accepted exports carry.
- **SYSVOL replication.** SYSVOL is read through `gPCFileSysPath`
  immediately after the import, as the publication lane does. That works on a
  single-DC estate. With more than one DC, the read could reach an unreplicated
  replica.
- **One folder and one principal.** Multi-folder and multi-principal documents
  are still unmeasured. The parity module renders every `Location` as a row, so
  a later case can add them without a code change.
- **The variant cases are not GPMC output.** Only `r3-flags-1021` is bytes
  Windows wrote.

## What a pass would and would not mean

A pass certifies that, for the four `Flags` values run, Windows imports R3's
bytes unchanged, re-exports them unchanged, and reports the folder, principal
and path Studio's reader claims. It would bind `fdeploy.py`, `backup.py` and
the parity module by hash. It would not decode `Flags`, certify any writer, or
say anything about what the client-side extension does on an endpoint. The
direction's exit is "`yes`, or the writer stays deferred under WI-066", and a
pass delivers the reader's half of that exit only.

## Running it

From the controller that holds the `cred:lab-*` capabilities, on a clean
checkout of the commit under test:

```bash
GPO_STUDIO_LAB_HOST=<hyper-v host> \
GPO_STUDIO_LAB_GUEST=<LabMS01 VM name> \
HYPERV_CONTROL_USERNAME=<host account> \
GUEST_BOOTSTRAP_USERNAME=<guest account> \
bash scripts/windows-oracle/run-fdeploy-oracle.sh
```

The credentials come from `acb exec cred:lab-hyperv-control
cred:lab-guest-bootstrap -- ...`, as for the other lanes. A passing run tags
`evidence/<run-id>`. Bank the run directory and the candidate as the other
lanes do.
