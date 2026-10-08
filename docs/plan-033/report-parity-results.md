# Report parity — Windows results

**Certifying run (2026-10-08):** `report-parity-20261008104512-7480`, 25/25
checks, 27/27 corpus cases plus the guest-authored case, on LabMS01 (Windows
Server 2025 Standard, build 26100, role 3, Windows PowerShell 5.1.26100,
GroupPolicy 1.0.0.0) at clean commit
`a1c280b8a1ec31b03397437dc2e6d947022b4857`
([evidence](wp2-evidence/report-parity/verification.json)). The evidence tag
is `evidence/report-parity-20261008104512-7480`. It supersedes the
`1a31feb` pass, which is described under [History](#history) below.

This is Plan 034 WP-2's `backup.py` and `report.py` exit, as the
[2026-10-07 ruling](../direction-2026-10-07-plan-034-completion.md) set it: a
report-parity lane against a fresh `Get-GPOReport -ReportType Xml`, preceded by
an offline differ. It closes WP-2 items 2 and 3. The lane's design, including
how the expectation is built and bound, is in
[the design note](report-parity-lane-design.md).

## What it certifies

For each Windows-produced backup in the corpus, Windows imported the backup
into a fresh, unlinked GPO and reported it with `Get-GPOReport -ReportType
Xml`. The controller held Studio's inventory of the same backup, which never
reached the guest. The finalizer compared the two by side and report family:

- registry entries by key path, value name and rendered value, exactly;
- preference items by element, `name`, `uid` and `Properties/@action`, in
  document order.

Studio's side is its **typed model** with the retained source bytes removed.
An unedited import exports its source bytes verbatim, so comparing those would
compare Windows with itself. Removing them measures what Studio writes after
any edit.

Every case passed four checks: no unexplained divergence, exactly the known
divergences the controller predicted for that case, a fresh report equal to the
backup's capture-time report (Windows regenerates the same settings from the
imported bytes), and at least one setting listed (two empty inventories are not
evidence).

One GPO was authored on the guest with `Set-GPRegistryValue`: a String and a
DWord value on each side under `Software\Policies\GPOStudio\ReportParity`, a
key no ADMX template describes. It was backed up with `Backup-GPO`. Windows'
report listed exactly the four authored values, Studio's import of Windows'
backup equalled the fresh report with no divergence at all, known or
otherwise, and the backup's own `gpreport.xml` equalled the fresh report.
Windows wrote everything in this case, so no exclusion could apply.

The whole-run checks follow the publication lane. Every fresh report names the
run's own GPO by GUID and name. Every owned GPO was unlinked, removed and then
confirmed absent by ID and by name. The fresh reports arrived intact (guest
SHA-256 equal to the pulled file), and so did the candidate. The deployed
runner matched source. The frozen environment held, the tree was clean, and the
guest exited 0. Before trusting the expectation, the finalizer re-ran the bound
candidate builder and got a byte-identical `expected.json` and archive.

The plain-text report (`GET /api/gpos/{guid}/report.txt`, `report.py`) prints
this same inventory under "Settings inventory (Windows report families)",
through `report_parity.studio_inventory`. That function is bound by the
verdict. `report.py`'s formatting around it is not bound.

## The 27 cases

`C` is the computer side and `U` the user side. Each count is the number of
items in Windows' fresh report. Studio's count is shown only where it differs
or where the family carries a known divergence.

| Dir | Case | Families compared (items) | Accepted divergence |
|---|---|---|---|
| c01 | `native-WI01A-DriveMaps-GPMC` | U Drive Maps 4 | none |
| c02 | `native-WI01A-EnvVars-GPMC` | U Environment 10 | none |
| c03 | `native-WI01A-Files-GPMC` | U Files 3 | none |
| c04 | `native-WI01A-Folders-GPMC` | U Folders 2 | none |
| c05 | `native-WI01A-IniFiles-GPMC` | U Ini Files 4 | none |
| c06 | `native-WI01A-LocalGroups-GPMC` | C Local Users and Groups 3; U Local Users and Groups 1 | none |
| c07 | `native-WI01A-MixedCSE-GPMC` | C Local Users and Groups 1; C Scheduled Tasks 1; U Drive Maps 1 | none |
| c08 | `native-WI01A-NestedILT-GPMC` | U Drive Maps 1 | none |
| c09 | `native-WI01A-OS-ILT` | U Drive Maps 1 | none |
| c10 | `native-WI01A-Power-GPMC` | U Power Options 1 (Studio 0) | `adapter-root-unknowns-dropped` (WI-072) |
| c11 | `native-WI01A-Printers-GPMC` | U Printers 5 | none |
| c12 | `native-WI01A-SchedTasks-GPMC` | C Scheduled Tasks 3 (Studio 3, order differs); U Scheduled Tasks 2 | `scheduled-task-order` (WI-073) |
| c13 | `native-WI01A-SchedTasksFull-GPMC` | C Scheduled Tasks 4 (Studio 4, order differs); U Scheduled Tasks 2 | `scheduled-task-order` (WI-073) |
| c14 | `native-WI01A-Services-GPMC` | C Services 3 | none |
| c15 | `native-WI01A-ServicesRecovery-GPMC` | C Services 2 | none |
| c16 | `native-WI01A-Shortcuts-GPMC` | U Shortcuts 3 | none |
| c17 | `evidence-wi059-20260908-wp0-backup` | C Registry 2; U Registry 1 | none |
| c18 | `evidence-wi059-20260908-scripts-metadata-rebackup` | C Scripts 3 (Studio 0) | `scripts-not-modeled` |
| c19 | `evidence-wi059-20260908-wp1b-drives-user-rebackup` | U Drive Maps 1 (Studio 1, name `P` against `P:`) | `legacy-studio-drive-name` |
| c20 | `evidence-wi059-20260908-wp1b-groups-machine-rebackup` | C Local Users and Groups 1 | none |
| c21 | `evidence-wi059-20260908-wp1b-localusers-machine-rebackup` | C Local Users and Groups 1 | none |
| c22 | `evidence-wi059-20260908-wp1b-mixed-all-rebackup` | C Local Users and Groups 2; C Registry 1; C Scheduled Tasks 1; C Services 1; U Drive Maps 1 (Studio 1, `P` against `P:`); U Registry 1 | `legacy-studio-drive-name` |
| c23 | `evidence-wi059-20260908-wp1b-registry-both-rebackup` | C Registry 1; U Registry 1 | none |
| c24 | `evidence-wi059-20260908-wp1b-scheduledtasks-machine-rebackup` | C Scheduled Tasks 1 | none |
| c25 | `evidence-wi059-20260908-wp1b-services-machine-rebackup` | C Services 1 | none |
| c26 | `evidence-wi059-20260908-wp2-rebackup` | C Registry 1; U Registry 1 | none |
| c27 | `evidence-backup-report-20260908-scripts-metadata-rebackup` | C Scripts 3 (Studio 0) | `scripts-not-modeled` |

c01–c16 are the 16 WI-01A GPMC captures. c17–c27 are the 11 Windows rebackups
from the WI-059 and WI-060 batches. Twenty cases matched Windows in every
family with no divergence. The other seven carry only the named divergences
shown, and `tests/test_report_parity_evidence.py` pins that split.

## Families covered, and the ones that are not

The `yes` in Plan 034 is scoped to **the families Studio models that this
corpus exercises**: registry policy (`REG_SZ` and `REG_DWORD`), Drive Maps,
Environment, Files, Folders, Ini Files, Local Users and Groups, Printers,
Scheduled Tasks (membership, not interleaving order; see WI-073), Services and
Shortcuts.

Not covered, and not claimed:

- **Power Options.** The corpus's only Power Options case (c10) carries a
  `GlobalPowerOptionsV2` plan that Studio retains on import and drops on write
  (WI-072), so Studio's inventory for it is empty and the case passes only on
  that named divergence. No case contains an XP-era `PowerScheme` either. The
  family is not certified until WI-072 is fixed and a lane re-runs it.

- **Studio families with no capture:** Regional Options, Devices, Folder
  Options, Data Sources, Network Shares, Applications and GPP Registry. Their
  inventory is reported under `unobserved:<path>` instead of a guessed report
  type, and no case contains one.
- **Other registry types:** types other than `REG_SZ` and `REG_DWORD`, and
  deletion entries (`**del.`/`**delvals.`). No case contains one. If a future
  case does, it shows up as a divergence until a capture records what Windows
  prints.
- **Interleaved `Groups.xml`:** this has WI-073's shape, but no capture shows
  it.

## Named exclusions

The verdict records four, and every case is graded with them:

- **`admx-policy-rendering`.** When an ADMX template describes a registry
  value, the report renders it as `<Policy>`. Studio models raw values. The
  corpus has no such case, and the authored case uses a non-ADMX key on
  purpose.
- **`scripts-not-modeled`.** Scripts are kept as source metadata, not typed
  settings. The Scripts metadata lane measures them. c18 and c27 show it.
- **Links, security filtering and WMI filters (the lifecycle lane's scope).**
  The disposable GPOs are unlinked, and the inventory does not read scope.
- **Preference `Properties` attributes beyond the action.** The lane checks
  item identity, `uid` and action. It does not check property-level
  equivalence. The adapters drop several unmodeled `Properties` attributes
  (for example Drives' `thisDrive`/`allDrives`), and this lane makes no claim
  about them.

`legacy-studio-drive-name` is not an exclusion of a Studio behaviour. An older
Studio writer named a drive `P` where GPME writes `P:`, and Windows keeps names
verbatim, so two WI-059 rebackups still carry it. The current adapters name it
`P:`. The allowance matches only a complete `P`/`P:` pair of otherwise
identical items.

The WI-01A captures were sanitized, and their `Backup.xml` security
descriptor became an 8-byte placeholder. The candidate copy carries Studio's
domain-neutral descriptor instead, and records
`restore-importable-security-descriptor` for each of the 16 cases it touched.
The descriptor is outside every inventory compared here.

## Divergences still open

Two Studio defects are accepted by this verdict **only** as pinned known
divergences on the cases that show them. They are not fixed, and the lane is
not evidence that they are harmless.

- **[WI-072](../work-items.md#wi-072--serialize_gpp-drops-adapter-root-content-the-model-retained)**
  (c10). Import retains Power Options' `GlobalPowerOptionsV2` item, but
  `serialize_gpp` never re-emits adapter root unknowns. After any edit, Studio
  writes the file without the power plan.
- **[WI-073](../work-items.md#wi-073--scheduled-and-immediate-tasks-lose-their-interleaving-when-the-model-is-written)**
  (c12, c13). The model holds `TaskV2` and `ImmediateTaskV2` items in two
  lists, so a written model puts scheduled tasks first, then immediate ones.
  The allowance accepts only that stable partition. Any other reordering stays
  unexplained.

Both fixes need `gpp.py`, which this verdict and the publication and
scripts-metadata verdicts bind. A fix turns its pin into full equality, and it
has to re-run all three lanes (WI-048). `tests/test_report_parity.py` pins the
offline side. `tests/test_report_parity_evidence.py` pins the banked verdict:
exactly WI-072 on c10 and WI-073 on c12 and c13.

## How the pack is banked

The pack is in the manifest form (schema version 2, see
[the policy](bound-source-manifest.md)). It holds the controller's local run
directory verbatim, plus `controller-candidate/` (the builder's output, which
the verdict's `candidate` hashes) and `controller.log`. The guest-deployed
runner is banked under `deployed/`. The controller half (finalizer, builder,
driver, transport, `report_parity.py`, `backup.py`, `backup_inventory.py`,
`import_export.py`, `gpp.py`, `gpp_adapters.py`, `registry_pol.py`,
`model.py`, `xml_safety.py`, `oracle_evidence.py`) is bound by
`(commit, path, sha256)`. The pack is `-text -whitespace` in `.gitattributes`
because its reports are UTF-16LE with CRLF line endings and the verdict's
hashes cover those bytes.

`tests/test_committed_evidence.py` registers it in `LANE_VERDICTS`. That
covers key and path agreement with the finalizer, every digest resolving at
`a1c280b`, no controller-side copy in the pack, and the shipping tree still
binding. `tests/test_report_parity_evidence.py` rehashes every artifact,
requires the bound builder to rebuild the candidate byte for byte, and
re-derives every case's comparison from the banked fresh reports with the
finalizer's own grading functions. The lane's cost in re-runs is in
[the cost table](bound-source-cost.md).

## History

The lane has three estate runs. Only the last is banked in the tree. The
earlier pack is in git history, and its evidence tag is preserved.

1. **The first run, at `2470ea9`, lost every imported case to MAX_PATH.**
   Windows PowerShell 5.1's `Expand-Archive` is bound by MAX_PATH (260
   characters). The run extracted under a root of about 110 characters, with
   55-character case directories and deep GPP paths, which left the case input
   empty. The authored case ran clean, but no imported case ran. The fix at
   `1a31feb` shortened the guest's run root and gave each case a short
   directory (`c01`–`c27`, mapped by `cases/index.tsv`). The builder now refuses
   any archive member whose guest path would exceed 200 characters, and the
   guest checks the case index before any case runs. That run was not a pass
   and was not banked.
2. **`report-parity-20261008093047-3377` passed** 25/25 at `1a31feb` (clean
   tree), with the same comparison as the current run. GPT Sol reviewed the
   lane twice, the second time including the MAX_PATH fix, and passed it both
   times. It was banked first, and its tag
   `evidence/report-parity-20261008093047-3377` is preserved. It stopped
   binding when PR #94's Windows CI showed the candidate builder was not
   platform-independent. The builder sorted `Path` objects, and `WindowsPath`
   compares case-insensitively, so the archive's member order (and so its
   hash) depended on the controller's OS. The finalizer's byte-identical
   rebuild check therefore failed on a Windows checkout of an unchanged tree.
   `a1c280b` sorts on ordinal path components instead. In the same commit, the
   guest runner's by-ID lookup in `Find-Owned` records its failure instead of
   swallowing it (PSScriptAnalyzer `PSAvoidUsingEmptyCatchBlock`). Both files
   are bound by the verdict, so the lane was re-run.
3. **`report-parity-20261008104512-7480` passed** 25/25 at `a1c280b`. Its
   candidate is byte-identical to the second run's (the Linux order was
   unchanged), and its comparison is identical case for case. Only the two
   bound files above differ between the two verdicts.

## Boundary

The run proves that, for the 27 corpus backups and the one authored GPO,
Studio's typed import inventories the same settings, in the same order, as
Windows' own fresh report, apart from the four named exclusions and the two
pinned defects. It does **not** prove:

- property-level equivalence of preference items (see the exclusions);
- anything about the seven Studio families with no capture, or about registry
  types and deletion entries no case contains;
- anything about scope (links, security filtering, WMI filters);
- that `report.py`'s text rendering is correct beyond the inventory it prints.
