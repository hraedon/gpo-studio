# fdeploy lane results

**Current qualification (2026-10-09):** `fd-20261009002120-4293`, 29/29, on
clean frozen `de9736ed3a4148b91cf2267fbe4640e260cc232f`
([evidence](wp4-evidence/release110-20261009/fdeploy/verification.json),
[release 1.1.0 batch](release110-batch.md)), tagged
`evidence/fd-20261009002120-4293`. The run below
stopped binding when WI-078 changed `psdirect.ps1`; its pack and tag are kept.

**Previous qualification (2026-10-08):** `fd-20261008121347-3151`, 29/29, on
clean commit `df713ef6eb86152e3e1e5ecf1e55f21dd5c64540`
([evidence](wp4-evidence/fdeploy/verification.json)), tagged
`evidence/fd-20261008121347-3151`. It replaced, at the same path, the lane's
first certification, `fd-20261008102559-9746` at `6b76fad` (see
[History](#history)). The candidate is byte-identical to that run's; only the
harness files that build and deliver it changed.

The lane, and the reasoning behind each assertion, are in
[the lane design](fdeploy-lane-design.md). This page records what the
certifying run showed and what it did not.

## What ran

One run on the estate member server, LabMS01: Windows Server 2025 Standard,
build 26100, Windows PowerShell 5.1, domain role 3, frozen profile, `psdirect`
transport. Each case got its own disposable GPO under a run-unique name. None
was linked, and each was removed by its owned GUID and confirmed absent by a
checked enumeration. The run found no foreign residue.

There were four cases. Each case is a GPMC-format backup carrying `fdeploy1.ini`
and the empty `fdeploy.ini` marker under `User/Documents & Settings`:

| Case | `Flags` | Bytes |
|---|---:|---|
| `r3-flags-1021` | 1021 | R3's `fdeploy1.ini` and `fdeploy.ini`, byte for byte (written by GPMC, 2026-09-04) |
| `r3-flags-1020` | 1020 | R3's, with only the four `Flags` digits replaced by the builder |
| `r3-flags-1023` | 1023 | as above |
| `r3-flags-3069` | 3069 | as above |

Each case carries one folder (Documents, `{FDD39AD0-…}`), one principal
(Everyone, `s-1-1-0`) and one `FullPath`
(`\\zz-studio-fileserver\zzredir\%USERNAME%\Documents`).

For each case the guest ran `Import-GPO`, read SYSVOL back through
`gPCFileSysPath`, took a fresh `Get-GPOReport -ReportType Xml` and re-exported
the GPO with `Backup-GPO`. The controller then graded these results against
an expectation that never reached the guest. The bound builder rebuilt that
expectation byte for byte before it was trusted.

## What is certified

For these four shapes, **Studio's reader says what Windows says**:

- **The bytes survive Windows.** After `Import-GPO`, the GPO's SYSVOL holds
  exactly the two files with the candidate's lengths and SHA-256s. The
  `Backup-GPO` re-export of `fdeploy1.ini` (458 bytes) and `fdeploy.ini`
  (20 bytes) is **byte-identical** to the candidate. Both the pulled backup
  directory and the guest's base64 show this. UTF-16LE with a BOM, CRLF only,
  the five-space preamble and the SID's lower case all came back unchanged.
  `FRValidateSettings`, the backup's re-evaluation hook, rewrote nothing
  without a migration table.
- **The reader agrees with the report, row for row.** `read_backup` over
  Windows' own backup yields one GPO with the owned GUID and a `GPO.fdeploy`
  that has no structural finding and no parse warning, and whose native digest
  is Windows' `fdeploy1.ini`. Its claims (folder GUID, principal SID,
  `FullPath`, and `Flags` carried verbatim) agree with the
  `FolderRedirectionSettings` rows of Windows' fresh report on
  `(folder id, principal SID, destination path)`. Each report has exactly one
  such extension, on the user side, with no error and nothing on the computer
  side.
- **The report is of the owned GPO.** The fresh report's identifier GUID and
  name, and the re-export's manifest, `bkupInfo.xml` and `gpreport.xml`, all
  name the GPO the run created. The backup's own report renders the same as
  the fresh one, options included.

The verdict hashes every candidate file and raw artifact. It binds 17 files by
commit, path and SHA-256: the lane files, the R3 transcripts, `fdeploy.py`,
`fdeploy_parity.py`, and the `read_backup` chain (`backup.py`,
`backup_inventory.py`, `model.py`, `gpp.py`, `xml_safety.py`, `safe_io.py`).
`tests/test_fdeploy_lane_evidence.py` re-runs the shipping finalizer over the
banked bytes and gets the same verdict back. It rebuilds the candidate byte
for byte, and re-derives each claim above from the raw Windows artifacts
rather than from the verdict.

## What is not certified

- **`Flags` decoding.** `fdeploy.py` carries `Flags` as the integer GPMC wrote
  and names no bit. This lane asserts nothing about the options. The 2026-10-08
  probe showed that the report engine does not read the word as independent
  bits: one bit can flip several options at once, and some values render no
  folder at all. See [the design](fdeploy-lane-design.md#what-the-2026-10-08-probe-established-first).
- **Other `Flags` values.** The agreement holds for 1021, 1020, 1023 and 3069
  only. The lane excluded the values whose report rendering breaks the
  reader/report comparison: no folder for 0, 509, 893, 957 and 989, and an
  empty `DestinationPath` for 765 and 2045.
- **Multi-folder and multi-principal documents.** Every case has one folder
  and one principal. Documents redirecting several folders, or one folder per
  group, are unmeasured. The parity module renders every `Location` as a row,
  so a later case can add them without a code change.
- **The writer.** Nothing here writes Folder Redirection policy, and nothing
  certifies one. What GPMC *writes* for each checkbox is still R12's question,
  so the writer stays deferred under
  [WI-066](../work-items.md#wi-066--r3-answered-one-of-the-four-questions-it-was-designed-to-answer).
- **GPMC origin of the variants.** Only `r3-flags-1021` is bytes Windows
  wrote. The other three add Windows' reading of each value through
  `Import-GPO`, not GPMC's authoring of it.
- **Endpoint behaviour.** Nothing was linked, and no client processed the
  policy.
- **SYSVOL replication and other builds.** One DC and one build
  (WS2025 26100) were measured.

## Option rendering per `Flags` (recorded data, for WI-066)

The finalizer records Windows' rendering of each case's `Folder` options
without asserting it. All four match the 2026-10-08 probe
(`matches_probe_20261008` is true for each case). Six elements were the same
in every case: `ApplyToDownLevel=false`, `ConfigurationControl=GP`,
`DoNotCare=false`, `GrantExclusiveRights=false`,
`PolicyRemovalBehavior=RestoreContents` and
`PrimaryComputerEvaluation=PrimaryComputerPolicyDisabled`. The three that
varied:

| Flags | `MoveContents` | `FollowParent` | `RedirectToLocal` |
|---:|---|---|---|
| 1021 | true | false | false |
| 1020 | false | false | false |
| 1023 | true | true | false |
| 3069 | true | false | true |

This is Windows' *interpretation* of four values, read by the report engine.
It is not a decoding: four observations of a ten-bit word do not establish
which bit means what, and the probe already showed that the bits interact.
`tests/test_fdeploy_lane_evidence.py` pins the table to the raw reports.

`gPCUserExtensionNames` after every import was
`[{25537BA6-77A8-11D2-9B6C-0000F8080861}{88E729D6-BDC1-11D1-BD2A-00C04FB9603F}]`,
the pair the probe measured.

## History

- **Exploratory pass at `379e59b` (2026-10-08), 28/28.** This was the first
  estate run of the lane, and its measurements match this one. A cross-lineage
  review (GPT Sol) then failed the harness on four findings: name-based
  cleanup, absence inferred from exceptions, an unparsed `bkupInfo.xml`, and
  an unidentified backup report. A second review failed the first fix round,
  because it still let an exception decide absence. The fixes changed the
  guest, the builder and the finalizer, which that run binds, so it binds
  superseded source. It was never banked or registered, and it certifies
  nothing. See [Review hardening](fdeploy-lane-design.md#review-hardening-2026-10-08).
- **Review of `6b76fad`.** GPT Sol passed the hardened lane ("fit to bank")
  before the certifying run. Two earlier rounds had failed it. The verdict
  gained one check, `no_foreign_residue`, between the two runs.
- **First certification, `fd-20261008102559-9746` at `6b76fad` (2026-10-08),
  29/29.** Banked and live until PR #98's Windows CI showed that its candidate
  did not rebuild on a Windows checkout. The builder sorted `Path` objects,
  which compare case-insensitively on Windows, so the archive's member order
  (`bkupInfo.xml` against `DomainSysvol/`) depended on the controller's OS:
  the defect report-parity's builder had first. The builder now sorts on
  ordinal path components (`8cb1bc2`), with Windows-ordered regression tests.
  On Linux the rebuilt candidate stayed byte-identical, but the builder is
  bound, so that verdict stopped binding the shipping tree. Its pack is in
  git history and its tag, `evidence/fd-20261008102559-9746`, is preserved.
- **Two refusals at `8cb1bc2`: "run root exists".** The re-run refused twice
  before touching a GPO. Since `258c195` the driver had set
  `GUEST_ROOT="C:\gpo-studio\fd\$STAMP"`; inside double quotes bash reads
  `\$` as a literal dollar, so every run's guest root was the constant
  `C:\gpo-studio\fd$STAMP`. The `6b76fad` run created it, and every later run
  collided with it; the driver's PREPARE guard refused, as designed. The
  driver now writes `fd\\$STAMP` (`df713ef`), and a test evaluates the
  driver's path lines in bash and requires a fresh root per run. The stale
  guest directory was removed. Neither refusal produced a verdict.
- **Current certification, `fd-20261008121347-3151` at `df713ef`.** 29/29 on
  a clean tree, with the same candidate bytes as `6b76fad`'s run.

## Re-run

From a clean checkout on the controller, with a qualified Hyper-V host:

```bash
GPO_STUDIO_LAB_HOST=<hyper-v host> \
GPO_STUDIO_LAB_GUEST=<LabMS01 VM name> \
HYPERV_CONTROL_USERNAME=<host account> \
GUEST_BOOTSTRAP_USERNAME=<guest account> \
acb exec cred:lab-hyperv-control cred:lab-guest-bootstrap -- \
  bash scripts/windows-oracle/run-fdeploy-oracle.sh
```

Any edit to a bound file expires this verdict. See
[the bound-source cost table](bound-source-cost.md).
