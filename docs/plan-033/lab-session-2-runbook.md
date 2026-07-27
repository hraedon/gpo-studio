# Lab session 2 — WP-1A corpus completion and ADMX empirical questions

One operator session on the Windows lab. Two independent parts:

- **Part A** completes the WP-1A native-origin reader corpus (8 new GPOs +
  1 supplement to an existing GPO).
- **Part B** settles three ADMX write-path questions (WI-008, WI-011, WI-012)
  that have been answered by inference rather than evidence.

They share a host and are batched only for that reason. Either can be dropped
without invalidating the other.

Host: mvmcitest01 (WS2025 build 26100, ad.hraedon.com)
Identity: svc-da (via scheduled task or interactive RDP)
Estimated: Part A ~65 min authoring + ~15 min capture; Part B ~45 min.

Prior art: [`wp1a-gpmc-authoring-guide.md`](wp1a-gpmc-authoring-guide.md)
(the three canaries already captured — same workflow, same verification
discipline) and [`wp1a-supplementary-matrix.md`](wp1a-supplementary-matrix.md)
(where these item specifications come from, with production frequencies).

---

## Pre-registered predictions

Recorded **before** the run so the session can falsify them. A prediction that
survives is evidence; one that fails is a bug found cheaply.

| # | Prediction | Basis | If wrong |
|---|-----------|-------|----------|
| P1 | The Power Options fixture will **not** parse as typed power settings. It will land as preserved unknown content. | GPMC's "Power Plan (At least Windows 7)" emits `<GlobalPowerOptionsV2>`; `_ADAPTER_META["power_options"]` expects item tag `<PowerScheme>`, and `GlobalPowerOptionsV2` appears nowhere in `gpp_adapters.py`. | The parser handles more than expected — record which element GPMC actually emitted. |
| P2 | `FilterCollection` (nested ILT) will round-trip as an **opaque raw-XML item**, not as modelled predicates. | `parse_ilt` maps known tags via `_TAG_TO_TYPE`; unknown tags are preserved via `ET.tostring` into `IltFilter.items`. `FilterCollection` is in no tag map. | Nested ILT is modelled — verify the predicate tree matches GPMC's nesting. |
| P3 | Every other adapter in Part A (Printers, Services, Files, Folders, Shortcuts, Environment, INI) will parse as typed items. | Each has a `_ADAPTER_META` entry with matching root/item tags. | Note the divergent tag; it is the same class of defect as the `TaskV2` finding. |
| P4 | Studio can **read** all of these but can natively **emit** only Drives, Groups, and ScheduledTasks. | `_GPP_EXTENSION_PROFILES` in `export.py` is a three-family allowlist; everything else raises `unsupported_native_gpp_extension`. | — (this one is near-certain; it is stated so the writer-lane gap is explicit) |

P1 and P2 are not blockers. A fixture that proves content is *preserved* is a
legitimate WP-1A deliverable — the capability contract allows preserve-only, it
just has to be recorded as such rather than assumed typed.

---

## Part A — WP-1A corpus completion

### Ground rules

Same as the canary session, repeated because they are what make the corpus
*native-origin*:

1. Author **entirely through the Group Policy Management Editor GUI**. No
   direct SYSVOL writes, no ADSI extension edits. A hand-injected XML file is
   a diagnostic specimen, not a corpus fixture.
2. After creating each item, **close its properties dialog and reopen it** to
   confirm GPMC persisted what you entered.
3. Save the GPO, then verify recognition before capture (below).
4. Use lab identifiers only. Never a work-domain name — these fixtures are
   committed.

### Per-GPO verification (do this before moving on)

```powershell
# ExtensionData present = the authoritative GPMC-recognition check
Get-GPOReport -Name $name -ReportType XML -Domain ad.hraedon.com |
    Select-String -Pattern 'ExtensionData' -Quiet

# Side version must be non-zero on the side you authored
Get-GPO -Name $name -Domain ad.hraedon.com |
    Select-Object DisplayName, @{n='User';e={$_.User.DsaVersion}},
                               @{n='Computer';e={$_.Computer.DsaVersion}}
```

Backup.xml labelling the GPP CSE "Unknown Extension" is expected and means
nothing — it is Backup-GPO's generic label. gpreport ExtensionData plus a
non-zero side version is the real signal.

### The eight GPOs

Specifications are condensed from the supplementary matrix; consult it for the
attribute-level patterns each item is meant to exercise.

#### 1. `WI01A-Printers-GPMC` — User Config → Preferences → Control Panel Settings → Printers → New → Shared Printer (~10 min)

Highest production frequency (17 instances) and the only source of OU-targeted ILT.

| # | Action | Printer path | Default | ILT |
|---|--------|-------------|---------|-----|
| 1 | Update | `\\printsv\Lab-Color` | Yes | FilterOrgUnit: `OU=Lab-WS,OU=Workstations,DC=ad,DC=hraedon,DC=com` |
| 2 | Update | `\\printsv\Lab-Mono` | No | FilterOrgUnit `OU=Lab-WS1,…` **AND** (OR-chain: `OU=Lab-WS2,…`, `OU=Lab-WS3,…`) |
| 3 | Delete | `\\printsv\Old-Printer` | — | none |

Exercises `default="1"/"0"`, and multi-OU `bool="OR"` chaining. The printer
share need not exist — GPMC stores the path without resolving it.

#### 2. `WI01A-Services-GPMC` — Computer Config → Preferences → Control Panel Settings → Services (~10 min)

| # | Service | Startup | Action | Timeout | Recovery 1/2/3 | Reset delay | Restart delay |
|---|---------|---------|--------|---------|----------------|-------------|---------------|
| 1 | WinRM | No change | Start | 30 | Restart/Restart/Restart | 0 | 60000 |
| 2 | Spooler | Automatic | Restart | 60 | Restart/Restart/None | 86400 | 30000 |
| 3 | W32Time | No change | Stop | 30 | None/None/None | 0 | 0 |

Exercises `startupType="NOCHANGE"` vs explicit, all three service actions, and
the failure-recovery attributes. **Do not** apply this GPO to anything — item 3
stops W32Time, and time is load-bearing in this domain.

#### 3. `WI01A-Power-GPMC` — Computer Config → … → Power Options → New → Power Plan (At least Windows 7) (~10 min)

| # | Action | Plan | Default | Settings | ILT |
|---|--------|------|---------|----------|-----|
| 1 | Update | High Performance (clone) | Yes | displayOff=0, sleepAfter=0, hibernate=0, lidClose=SLEEP | FilterCollection: NOT(OS=XP) AND NOT(OS=2K3) AND NOT(OS=2K3R2) |

**This is the P1/P2 fixture.** Expect unknown-content preservation, not typed
parsing. Capture it anyway and record what element GPMC emitted — that
observation is the deliverable.

#### 4. `WI01A-Files-GPMC` — User Config (~5 min)

| # | Action | fromPath | targetPath | Attrs |
|---|--------|----------|------------|-------|
| 1 | Update | `\\filesrv\app\vendor\*.*` | `%APPDATA%\Vendor App\` | none |
| 2 | Create | `\\filesrv\source\cönfig.xml` | `%TEMP%\Ünïcödé <"&>.xml` | RO, Archive, Hidden, Suppress |
| 3 | Delete | (any) | `%USERPROFILE%\Desktop\old.txt` | — |

Item 2 carries the Unicode + XML-entity payload. Type it exactly.

#### 5. `WI01A-Folders-GPMC` — User Config (~5 min)

| # | Action | Path | Attrs |
|---|--------|------|-------|
| 1 | Update | `%APPDATA%\Vendor App\` | none |
| 2 | Create | `%USERPROFILE%\Pröjects <"&>` | RO, Archive, Hidden |

Production data contains literal `&quot;` entities inside attribute values; if
GPMC lets you enter a quoted path, do it — that is the edge case.

#### 6. `WI01A-Shortcuts-GPMC` — User Config (~10 min)

| # | Action | Name | Target | Shortcut path | ILT |
|---|--------|------|--------|---------------|-----|
| 1 | Replace | `Lab Manager` | `C:\Program Files\LabTools\manager.exe` (args `--config lab`, start in `C:\Program Files\LabTools`, icon `…\icon.ico, 0`) | `%CommonDesktopDir%\Lab Tools\Manager` | FilterGroup `HRAENET\Lab-WS-Group` |
| 2 | Create | `Ünïcödé App` | `notepad.exe` (args `/A`) | `%USERPROFILE%\Desktop\Ü <"&>.lnk` | none |
| 3 | Delete | `Old App` | — | — | — |

Exercises `targetType="FILESYSTEM"`, empty-but-present `pidl`/`window`, and the
`removePolicy` + `userContext` combination.

#### 7. `WI01A-EnvVars-GPMC` — User Config → … → Environment (~5 min)

Four items, no production reference — completeness coverage. Vary across them:
one Create, one Update, one Replace, one Delete; at least one `User` and one
`System` variable if the GUI offers both; one value containing `%` expansion
and one containing Unicode.

#### 8. `WI01A-IniFiles-GPMC` — User Config → … → Ini Files (~5 min)

Three items: Create, Update, Delete. Vary section/property/value, and include
one value with a space and one with Unicode.

#### 9. Supplement to the existing `WI01A-SchedTasks-GPMC` (~3 min)

Add one **ImmediateTaskV2** item, `Create`, name `Defender-Activation`, run as
`NT AUTHORITY\System`, with a UNC command path. Production shows Task schema
**1.3** where the WS2025 canary produced **1.2** — the point is to hold both
versions in the corpus. If GPMC emits 1.2 again, record that: it would mean the
version is authoring-tool dependent in a way the matrix's note gets wrong.

### Capture and transfer

```powershell
$BackupRoot = 'C:\Temp\gpp-gpmc-native-3'
New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null

$names = @(
    'WI01A-Printers-GPMC','WI01A-Services-GPMC','WI01A-Power-GPMC',
    'WI01A-Files-GPMC','WI01A-Folders-GPMC','WI01A-Shortcuts-GPMC',
    'WI01A-EnvVars-GPMC','WI01A-IniFiles-GPMC','WI01A-SchedTasks-GPMC'
)

foreach ($name in $names) {
    $outDir = Join-Path $BackupRoot $name
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    Backup-GPO -Name $name -Path $outDir -Domain ad.hraedon.com
    Get-GPOReport -Name $name -ReportType XML `
        -Path (Join-Path $outDir 'gpreport-verify.xml') -Domain ad.hraedon.com
}
```

`WI01A-SchedTasks-GPMC` is re-captured because it changed. Transfer the whole
tree; sanitization and fixture generation happen on the dev box through the
recorded sanitizer, not by hand.

---

## Part B — ADMX empirical questions

Three questions the ADMX write path currently answers by inference. All three
are the same shape: *what registry bytes does Windows actually write?*

### Why read `Registry.pol` rather than the hive

Configure through the **Local** Group Policy Editor (`gpedit.msc`) and read the
local GPO's PReg file directly:

```
C:\Windows\System32\GroupPolicy\Machine\Registry.pol   (Computer Config)
C:\Windows\System32\GroupPolicy\User\Registry.pol      (User Config)
```

That file *is* the layer Studio emits, so it answers the authoring question
with no CSE interpretation in between. Read the hive as well (after
`gpupdate /force`) only for WI-008's Disabled case, where delete semantics
(`**del.`) manifest as an absence rather than a value.

Copy each `Registry.pol` back rather than transcribing bytes by hand — Studio's
own parser can read them, and hand-transcription is where this kind of evidence
usually goes wrong.

### Finding candidate policies

Run against the central store (or `C:\Windows\PolicyDefinitions`) to find
policies with the shapes each question needs:

```powershell
$admx = Get-ChildItem C:\Windows\PolicyDefinitions -Filter *.admx

# WI-008 candidates: valueName present, no enabledValue/disabledValue children
foreach ($f in $admx) {
    Select-Xml -Path $f.FullName -XPath '//*[local-name()="policy"]' |
        Where-Object {
            $_.Node.valueName -and
            -not $_.Node.SelectSingleNode('*[local-name()="enabledValue"]') -and
            -not $_.Node.SelectSingleNode('*[local-name()="disabledValue"]')
        } | Select-Object @{n='File';e={$f.Name}},
                          @{n='Policy';e={$_.Node.name}},
                          @{n='Key';e={$_.Node.key}},
                          @{n='ValueName';e={$_.Node.valueName}}
}

# WI-011/012 candidates: policies containing <list> elements
foreach ($f in $admx) {
    Select-Xml -Path $f.FullName -XPath '//*[local-name()="list"]' |
        Select-Object @{n='File';e={$f.Name}},
                      @{n='ValuePrefix';e={$_.Node.valuePrefix}},
                      @{n='ExplicitValue';e={$_.Node.explicitValue}},
                      @{n='Additive';e={$_.Node.additive}}
}
```

### WI-008 — the implicit default

**Question:** for a policy with `valueName` and no explicit enabled/disabled
values, does Enabled write `DWORD 1` and does Disabled *delete* the value?
Studio hard-codes exactly that in its single resolver, and the behaviour is
undocumented at the ADMX level.

1. Pick one WI-008 candidate. Record file, policy name, key, valueName.
2. Set it **Enabled** → save → copy `Registry.pol` → record the value type and data.
3. Set it **Disabled** → save → copy `Registry.pol` → record what appears
   (expect a `**del.` marker rather than a value).
4. `gpupdate /force`, then read the hive key to confirm the value is absent.
5. Set it **Not Configured** → save → confirm the entry leaves the file entirely.

Repeat on a second candidate from a different ADMX file. One sample proves the
policy; two make it a rule.

### WI-011 — list value naming

**Question:** how are list items named under `valuePrefix` / `additive`? The
schema reference documents these as literally "TBD", and Studio's current
behaviour comes from worked examples.

Pick candidates covering, ideally: `valuePrefix` present, `valuePrefix` absent
or empty, and `additive="true"`. For each:

1. Enable the policy and add **three** list entries with distinguishable values.
2. Save, copy `Registry.pol`, and record the exact value **names** written.

Three entries, not one — the question is the numbering/naming sequence, which a
single entry cannot reveal. Studio emits N×`REG_SZ` (one value per item); the
shape is settled, only the naming is open.

### WI-012 — `explicitValue="true"`

**Question:** what name/data pairing does Windows write when the list takes an
explicit name *and* value? Studio currently **refuses** this with an error
rather than guessing, which is the right default but leaves the feature
unavailable.

1. Find a policy with `explicitValue="true"` (from the query above).
2. Enable it and add two entries, each with a distinct name and value.
3. Save, copy `Registry.pol`, and record how name and data map onto the
   registry value name and its data.

If no in-box policy has `explicitValue="true"`, say so and stop — that is a
finding too, and it downgrades WI-012 from "unimplemented" to "unreachable with
in-box ADMX", which changes its priority.

### Cleanup

Reset every policy touched to **Not Configured**, save, and confirm the local
`Registry.pol` files are back to their pre-session state (or absent). These are
local-GPO edits on a lab host, but leaving policy behind makes the next
session's readings ambiguous.

---

## Post-session checklist

- [ ] Nine backup trees transferred, each with `gpreport-verify.xml`.
- [ ] Each GPO showed ExtensionData and a non-zero side version **before** capture.
- [ ] P1 recorded: which element did GPMC emit for the power plan?
- [ ] P2 recorded: how did the nested ILT appear in the XML?
- [ ] P3 recorded: any adapter whose emitted tag differs from `_ADAPTER_META`.
- [ ] WI-008: `Registry.pol` for Enabled / Disabled / Not Configured, two candidates.
- [ ] WI-011: `Registry.pol` for each list variant, three entries each.
- [ ] WI-012: either name/data evidence, or a recorded finding that no in-box
      policy offers `explicitValue`.
- [ ] Local GPO policies reset to Not Configured.
- [ ] No work-domain identifier typed into any GPO, path, or value.

## What happens next with the output

1. Sanitize and generate fixtures with semantic manifests (recorded sanitizer,
   not by hand).
2. Extend the WP-1A corpus matrix with the new adapter rows and their real
   coverage.
3. Feed the verified CSE/tool GUID pairs from these captures into
   `_GPP_EXTENSION_PROFILES` so native emission grows **from evidence** — this
   is what unblocks WP-1B beyond three adapters.
4. Settle the WI-008/011/012 answers into the ADMX resolver, replacing inferred
   behaviour with cited evidence.
