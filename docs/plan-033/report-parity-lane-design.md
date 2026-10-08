# Report-parity lane design

Status: **Windows-verified** (2026-10-08). The certifying run
`report-parity-20261008093047-3377` passed 25/25 checks over all 27 corpus
cases plus the guest-authored case, on LabMS01 at clean commit `1a31feb`.
See [the results](report-parity-results.md). The rest of this note was written
before that run and is kept as the design. Where it calls something a
hypothesis about a fresh report, the run has since measured it for the
families the corpus exercises, and only for those.

This is the exit the [2026-10-07 direction](../direction-2026-10-07-plan-034-completion.md)
set for `backup.py` and `report.py`: "a report-parity lane against a fresh
`Get-GPOReport -ReportType Xml`, preceded by an offline differ", reaching `yes`
for the families Studio models. It closes Plan 034 WP-2 item 3 once it has a
passing verdict, which it has had since `report-parity-20261008093047-3377`.

## The question

For one GPMC backup: **does Studio's import inventory the same settings as
Windows' own fresh report of a GPO that backup was imported into?**

"The same settings" is defined by one inventory shape, built two ways in
`src/gpo_studio/report_parity.py`:

- **Windows** (`windows_inventory`): the report's settings grouped by side
  (Computer/User) and by `Extension/@xsi:type` (`RegistrySettings`,
  `DriveMapSettings`, `LugsSettings`, `ScheduledTasksSettings` and so on).
  Registry entries are identified by key path, value name and the value as
  rendered (`String:...`, `Number:...`), compared exactly — no trimming. The
  only text dropped is the indentation between child elements of a multi-part
  value, which no captured report contains. Preference items are identified by
  element, `name`, `uid` and `Properties/@action`, in document order.
- **Studio** (`studio_inventory`): registry entries from `GPO.settings`, and
  preference items from `serialize_gpp` over each collection **with the
  retained source bytes removed**. An unedited import exports its source bytes
  verbatim, so comparing those with a report generated from the same bytes would
  compare Windows with itself. Removing them measures the typed model, which is
  what Studio writes after any edit.

`compare` checks each (side, family) for the same items in the same order.
Preference items are processed in document order, so order counts as meaning.
A difference is a named divergence: `missing_in_studio`, `extra_in_studio`,
`order`, or `admx_policy_rendering`.

The plain-text policy report (`report.py`) lists the same inventory under
"Settings inventory (Windows report families)", so a reviewer reads exactly
what the lane compares.

## What the offline differ found

Run over every Windows-produced backup in the corpus (the 16 WI-01A GPMC
captures and the 11 WI-059 native rebackups), through the public
`POST /api/backups/import` endpoint, against each backup's own capture-time
`gpreport.xml`:

| Finding | Disposition |
|---|---|
| Studio named preference items differently from GPME: a drive `M` for `M:`; files, folders and printers by full path instead of the leaf; shortcuts `""` instead of the shortcut path's leaf | **Fixed** in `gpp_adapters.py` (unbound). All 16 GPMC captures now match in every modeled family |
| Power Options' `GlobalPowerOptionsV2` is retained in the model, but `serialize_gpp` never re-emits adapter root unknowns | **WI-072**. The fix is in `gpp.py`, which two lanes bind |
| `TaskV2` and `ImmediateTaskV2` lose their interleaving, because the model holds them in two lists | **WI-073**. The fix is in the bound model. The allowance accepts only the stable partition (scheduled tasks first, then immediate, each in its original order); any other reordering stays unexplained |
| Two WI-059 rebackups name a drive `P`. An older Studio writer wrote that, and Windows keeps names verbatim | Named: `legacy-studio-drive-name`. Matched only as a complete `P`/`P:` pair of otherwise identical items |
| Scripts appear in the report but are not typed settings in Studio | Named exclusion: `scripts-not-modeled` |

Every known divergence is pinned per backup in
`tests/test_report_parity.py::EXPECTED_KNOWN`. A fix that removes one, or a
regression that adds one, fails until the pin and the lane move together
(WI-048). A divergence that matches no entry is unexplained, and an unexplained
divergence fails both the test and the candidate builder.

## The lane

New files only. It edits no shared bound file.

| File | Role |
|---|---|
| `scripts/plan-033/build-report-parity-candidate.py` | Packages each corpus backup into `report-parity-cases.zip`, and writes `expected.json` with Studio's inventory per case, the capture-time report's inventory, the expected known divergences and the guest-authored spec. Deterministic |
| `scripts/windows-oracle/run-report-parity.ps1` | Guest, LabMS01, Windows PowerShell 5.1. For each case: `New-GPO` (disposable, unlinked), `Import-GPO`, fresh `Get-GPOReport -ReportType Xml`, `Remove-GPO`, then a strict absence re-query by ID and by name. Then one guest-authored case |
| `scripts/windows-oracle/run-report-parity-oracle.sh` | Controller driver. Prints `LOCAL_RUN_DIR=` and `CANDIDATE_DIR=` |
| `scripts/windows-oracle/finalize_report_parity_run.py` | Grades the run, binds source as (commit, path, sha256), writes `verification.json` and tags on a pass |

### What a passing verdict asserts

Per imported case:

- Studio's expected inventory against Windows' fresh report gives no
  unexplained divergence, and exactly the known divergences the offline compare
  named for that case.
- The fresh report's inventory equals the capture-time report's. Windows
  regenerates the same settings from the imported bytes.
- The report lists at least one setting. Two empty inventories are not evidence.
- The disposable GPO had no links, was removed, and was then confirmed absent by
  ID and by name. Each name is registered before `New-GPO`, removal retries with
  backoff, and a final sweep removes anything still holding a registered name;
  nothing unregistered is ever touched.

For the guest-authored case: `Set-GPRegistryValue` writes two values (String and
DWord) on each side under `Software\Policies\GPOStudio\ReportParity`, a key no
ADMX template describes. The GPO is backed up with `Backup-GPO` and reported:

- Windows' report lists exactly the four authored values. The controller holds
  the spec independently of the guest script, and a test holds the two copies
  equal.
- Studio's import of Windows' backup equals Windows' fresh report, with **no**
  divergence, known or otherwise. Windows wrote everything in this case, so no
  legacy or exclusion applies.
- The backup's own `gpreport.xml` equals the fresh report.

Every fresh report must name the run's own GPO — identifier GUID and name
exactly, domain ignoring case — and every owned id must be a unique GUID whose
GPO name starts with `zz-studio-rp-<run_id>-`, so a replayed capture-time
report cannot pass as fresh. The authored steps (`values_set`,
`backup_succeeded`, a backup id equal to the manifest's) must all succeed, and
the driver hands the guest's exit status to the finalizer
(`--guest-status`): a failed guest can neither pass nor tag.

Whole-run checks follow the publication lane: exact result schema, every case
run once with the candidate's backup and GPO IDs, member-server role, frozen
environment, raw command output for every step, fresh reports delivered intact
(guest SHA-256 equal to the pulled file), deployed script identical to source,
candidate delivered intact, clean source tree. Before the expectation is
trusted, the finalizer **re-runs the bound builder** and requires a
byte-identical `expected.json` and archive, so every expectation — the
required 27-case list, both inventories per case, the known divergences and
the authored spec — comes from bound source. A forged, stale or short
candidate fails.

`expected.json` never reaches the guest, and a test checks that the driver never
pushes it.

### Named exclusions

- **ADMX-resolved `<Policy>` rendering.** When an ADMX template describes a
  registry value, the report renders it as a `<Policy>` instead of a
  `RegistrySetting`. Studio models raw values only. A side with `<Policy>`
  elements yields an `admx-policy-rendering` divergence. The corpus has none,
  and the authored case uses a non-ADMX key on purpose.
- **Links, security filtering and WMI filters.** These belong to the lifecycle
  lane. The disposable GPOs are unlinked, and the inventory does not read
  scope.
- **Scripts.** Scripts are kept as source metadata, not typed settings. The
  Scripts metadata lane measures them.
- **Preference `Properties` attributes beyond `action`.** The inventory
  identifies items. It does not assert property-level equivalence. The adapters
  drop several unmodeled `Properties` attributes (for example Drives'
  `thisDrive`/`allDrives`). That is outside this lane's claim, and a later lane
  could extend the inventory to cover it.
- **Report metadata.** Times, security descriptor, owner and the `Identifier`
  block are not compared.

### Unmeasured, said out loud

- Registry types other than `REG_SZ` and `REG_DWORD`, and deletion entries
  (`**del.`/`**delvals.`), have no corpus case. Studio renders them as
  `REG_TYPE:value` and as the `Registry.pol` reserved names, which cannot match
  a Windows rendering by accident. If a future case contains one, it surfaces as
  a divergence until a capture says what Windows prints.
- Studio families no capture has shown (Regional Options, Devices, Folder
  Options, Data Sources, Network Shares, Applications, GPP Registry) are reported
  under `unobserved:<path>` instead of a guessed report type.
- `Groups.xml` interleaving `Group` and `User` items has the same shape as
  WI-073, but no capture shows it.
- The registry order claim rests on the corpus: in the `wp0` rebackup the report
  lists values in `Registry.pol` order, which is not alphabetical. The authored
  case re-measures it on a file Windows wrote.

### Import readiness and the one transformation

The builder checks each case offline: a native single-backup manifest with GPO
GUID and backup ID, and an `{ID}` directory holding `Backup.xml` (core ID equal
to the manifest's), `bkupInfo.xml`, `gpreport.xml` and `DomainSysvol/GPO`. A
case that fails is excluded and the reason is recorded in `expected.json` and
the verdict. All 27 currently pass.

The WI-01A fixtures were sanitized, and their `Backup.xml` security descriptor
became an 8-byte placeholder, which is not a complete descriptor. `Import-GPO`
imports settings, not security, but nobody has measured whether it parses that
field. Spending an estate run to find out would be wasteful. The candidate copy
therefore carries Studio's domain-neutral descriptor, which `Import-GPO` already
accepted in the publication and WP-2 lanes, and records
`restore-importable-security-descriptor` for each case it touched. The
descriptor is outside every inventory compared here.

## Running it

From the controller that holds the `cred:lab-*` capabilities, on a clean
checkout of the commit under test:

```bash
GPO_STUDIO_LAB_HOST=<hyper-v host> \
GPO_STUDIO_LAB_GUEST=<LabMS01 VM name> \
HYPERV_CONTROL_USERNAME=<host account> \
GUEST_BOOTSTRAP_USERNAME=<guest account> \
bash scripts/windows-oracle/run-report-parity-oracle.sh
```

The driver prints `LOCAL_RUN_DIR=` and `CANDIDATE_DIR=`. A passing run tags
`evidence/<run-id>`. Bank the run directory and the candidate as the other lanes
do.
