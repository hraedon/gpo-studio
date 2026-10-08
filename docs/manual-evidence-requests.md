# Manual evidence requests: operator work order

**Status:** R1–R11 fully executed 2026-09-05; evidence bound to this repository
2026-09-06. Written 2026-08-06 against `main` at `80c23b5`. **R12 is open**
([WI-066](work-items.md)).

This file is the work order an operator follows at a Windows/GPMC console. Each
numbered request (R1, R2, ...) is a self-contained errand that produces an
artifact this project cannot get from its own source. The requests are ordered
by value per minute of your time. Each one lists what it settles, the time, the
estate, what it writes, the steps, what to send back, sanitisation and cleanup.
Background on why the question matters comes last in each request.

The route a request ran by limits what its result is worth:

- **R1–R5** ran as full transactions through the console driver on the lab
  estate: capability envelope evaluated, verdict `verified`, strict-absence
  cleanup, banked as records in the windows-console-driver claim registry
  (estate windows 2–4). Only these five carry the complete transactional
  guarantee.
- **R10** ran controller-side over PSDirect in the same estate (window 5), but
  **pilot-style, with no capability envelope and no transaction id**, and its
  record says so. The byte-identical re-emission result rests on the
  comparison, not on the transaction machinery.
- **R9** did not use the console driver. Its oracle is `secedit /validate` on
  LabMS01: Windows' own parser accepted one candidate shape and rejected the
  other.
- **R6 and R8** ran read-only from the admin workstation. **R11** ran against
  the live domain with the operator's go-ahead (one unlinked GPO, removed in the
  same sitting, strict re-query zero). **R7** ran on the domain controller the
  same day via `secedit /export`, sliced on-box, with the full export deleted.
  These four are operator-run captures over WinRM, not driver transactions.

## Where each result lives

Every result is traceable to a named artifact in a named repository. A row with
no citation would be a result nobody can check.

Records under `docs/estate-window-*/records/` and rows in `docs/claim-registry.md`
are in the **`windows-console-driver`** repository (`../windows-console-driver/`).
Fixture paths are in this one.

| # | Record / claim | Fixture here | Code change | What it settled |
|---|---|---|---|---|
| R1 | `estate-window-2/records/r1-migtable.json`, `-4/r1-v1-record.json`, `-6/r1-v2-record.json` | `tests/fixtures/migration-tables/r1-studio.migtable` | `936394d` | GPMC's `.migtable` namespace and `Mapping → Type/Source/Destination` shape. `migration.py` parsed neither — a GPMC table produced an **empty table, silently**, on a live API endpoint |
| R2 | `-2/r2-record.json`, `-3/r2-psorder-window3-record.json`, `-4/r2-v2-record.json` | `tests/fixtures/native-scripts-gpmc/` | `4221432`, `38e3e9f` | `scripts.ini`/`psscripts.ini` are UTF-16LE BOM + CRLF; ordering is `[ScriptsConfig] StartExecutePSFirst`; no `[Policy]` section exists |
| R3 | `-3/r3-window3-record.json` | `tests/fixtures/native-folder-redirection-gpmc/` (D5) | `src/gpo_studio/fdeploy.py` ([the ruling](scope-decision-2026-09-11-folder-redirection.md)) | Folder Redirection writes an empty `fdeploy.ini` marker plus the real policy in **`fdeploy1.ini`** — `version=100`, `Flags=1021`. `folder_redirection.py` addresses neither file. **Scope question for Plan 034, not a patch.** **Partial against its own request (recorded 2026-09-11):** this row asked four questions and the capture answers the first cleanly. The Pictures/Advanced folder was never authored, so there is **one** folder, **one** principal (`s-1-1-0`, Everyone) and **one** `Flags` value — no multi-group representation, and no default/non-default contrast to read the flag bits against. Questions 3 and 4 are open; see R12 and WI-066. **Consumed 2026-09-11:** the scope question was ruled *read target* and `fdeploy.py` reads this capture; the writer the other three questions gate is deferred until R12. The fixture's provenance note called `{FDD39AD0-…}` the CSE GUID; it keys the folder, which is WI-067 |
| R4 | `-4/r4-v2b-record.json` (qualification artifact) | `tests/fixtures/native-security-template-gpmc/` (D4) | `d7caf44` | Propagation codes are **propagate=0, do-not-allow-replace=1, replace=2** — `object_security.py` was wrong on all three. Native `[Registry Keys]` rows are bare quoted-CSV; `key = value` appears nowhere. `security_template.py` parses **0 of 3** native rows (3 `unknown_lines`) |
| R5 | `-2/r5m-record.json`, `-2/r5u-record.json`, `-4/r5{m,u}-v2-record.json` | — (facts only) | `725d085` | `gpt.ini`/`versionNumber` is the packed field `user·65536 + machine`. `publication.py`'s flat `+1` would corrupt it on a user-side change |
| R6 | claim-registry R6 row | `tests/fixtures/live-domain-census/r06-cse-census/` (D6) | `0a6664e`, `b8fa1f4` | The two GPP XML `clsid`s in `_KNOWN_CSE_GUIDS` appear in **no extension list of any of the 26 production GPOs**. They were never CSE GUIDs |
| R7 | claim-registry R7 row | — (five-key slice only; full export deleted on-box) | `055e5f5` | `[Kerberos Policy]` key names and the mixed units — `MaxTicketAge` hours vs `MaxServiceAge` minutes. `policy_families.py`'s three unit mappings were **correct**; two keys were unmodelled |
| R8 | claim-registry R8 row | `tests/fixtures/live-domain-census/r08-gpo-anatomy/` (D6) | none needed | What a published GPO consists of. `gpo2`'s packed `Version=131082` (user 2, machine 10) is **live production corroboration of R5** from an independent directory |
| R9 | claim-registry R9 row | — (builder is `scripts/plan-033/build-object-security-candidate.py`) | `d7caf44` | `secedit /validate` **rejects** `object_security.py`'s `key = value` shape and **accepts** the native quoted-CSV shape. Windows' own parser is the oracle |
| R10 | `-5/records/r10-record.json` | reuses `native-scripts-gpmc/` | `4221432` | Windows **accepts** a Studio-written scripts bundle: `Import-GPO` consumed it, `Backup-GPO` re-emitted both INIs **byte-identical** |
| R11 | claim-registry R11 row | — (raw re-backup deleted untranscribed) | none needed | A **production** directory accepts Studio's output as the lab does: extension lists preserved verbatim, packed `versionNumber=65537`, both `registry.pol` files re-emitted byte-identical |

These results are recorded as `capture-backed`, not as verification; see
[After the artifacts land](#after-the-artifacts-land).

### Raw artifacts, and where they are

The captures live outside both git worktrees, on `mvmcc03` under
`/home/itadmin/gpo-studio-evidence/inbox/`: `r01-migtable/`,
`r03-folderredir/`, `r06-cse-census/`, `r08-gpo-anatomy/`,
`r10-scripts-roundtrip/`.

R1, R3 and R10's raw output was moved there on 2026-09-06. Before that it
existed only in `windows-console-driver/runs/`, which is `.gitignore`d. That
included the two R10 screenshots that a **committed** record hash-binds, and a
hash for a file nobody holds a durable copy of cannot be checked.

R7's full `secedit` export and R11's re-backup were deleted by design; only
their semantic slices left the estate. Those two rows rest on the claim registry
alone, and that is the correct ceiling for them.

## Background

Two capabilities were unlocked on 2026-08-06: **a working GUI console into
`LabMS01`**, and **a live Active Directory domain, `ad.hraedon.com`, usable as
required.**

Before these requests, nothing in this product had been validated against a
real domain. Twelve RSOP verdicts, WP-1B, WP-2 and WP-3 all ran against a
synthetic three-guest estate with no egress. Requests 6, 7, 8 and 11 were the
first observations this project held about a production directory.

The input is
[`docs/plans-025-032-oracle-survey.md`](plans-025-032-oracle-survey.md), which
names a *cheap discriminator* per module. (When this work order was written the
survey lived only on branch `docs/oracle-survey-025-032`; it has since landed.)
This document turns each discriminator into instructions: where the survey says
"one backup and one grep", this says which menu, which cmdlet, which file and
what to send back.

### Relationship to the existing queue

This **extends**
[`plan-033/manual-gui-evidence-queue.md`](plan-033/manual-gui-evidence-queue.md)
and does not replace it. That document stays the checkbox tracker and the
statement of the staging and cleanup discipline, and its WI-022 entry remains the
worked precedent. This document adds two things it does not cover:

1. **A live production domain.** The queue's first safety rule is "use only the
   isolated lab", which was enough while the lab was the only estate. The
   live-domain rules below are new and take precedence for requests 6, 7, 8
   and 11.
2. **Direction B.** The queue only captures what Windows writes. Requests 9, 10
   and 11 hand Windows something *Studio* wrote and ask whether Windows
   accepted it.

The queue's access path, "RDP to `mvmcitest01`", is **retired** (§7 of the
survey, and `plan-033/environment-spec.md`). The work order asked that the queue's
access path be updated and the live-domain rules added when the first request
here ran; that is done (see [After the artifacts land](#after-the-artifacts-land)).

---

## SAFETY — read before touching `ad.hraedon.com`

`ad.hraedon.com` is a **live production domain**. Every request is written to be
non-destructive and reversible, and every live-domain request states what it
writes. These rules are absolute:

1. **Never modify, relink, rename or delete an existing GPO**, not even to "put
   it back afterwards". If a request seems to need this, the request is
   defective: stop and say so.
2. **Create GPOs unlinked.** No request here needs a link. If a future one does,
   link only to a dedicated, empty, purpose-made OU containing no real objects.
3. **Never link anything to the domain root, to `Domain Controllers`, to a
   site, or to any OU containing real objects.**
4. **Never run `gpupdate`, `secedit /configure`, `Restore-GPO`, or
   `Set-GPPermission` against the live domain.** Anything that applies or
   configures policy belongs on `LabMS01`/`LabCL01`, which are disposable and
   checkpoint-backed.
5. **Prefer read-only exports.** `Get-GPOReport`, `Backup-GPO`, `Get-ADObject`
   and `secedit /export` read; they do not write to the directory or SYSVOL.
6. **Exactly one request writes to the live domain: R11.** It creates one
   unlinked GPO and removes it in the same sitting. **R11 needs a fresh
   go-ahead before it runs**, and skipping it costs nothing downstream.
   Requests 6, 7 and 8 are read-only end to end.
7. **Every request ends with cleanup steps.** Run them in the same sitting.
   Confirm removal with a strict re-query, not by looking at the console.

### Naming convention

Name every object you create, on either estate:

```
zz-studio-evidence-NN-<slug>
```

`NN` is the request number, zero-padded. The `zz-` prefix sorts the objects to
the bottom of every GPMC list. To find everything this document ever created:

```powershell
Get-GPO -All | Where-Object { $_.DisplayName -like 'zz-studio-evidence-*' } |
  Select-Object DisplayName, Id, CreationTime
```

This is also the cleanup check. It must return **zero rows** when a sitting is
finished.

### Where artifacts go

**On the Windows side**, stage under `C:\gpo-studio\manual\<request-id>\`, for
example `C:\gpo-studio\manual\r02-scripts-ini\` (the existing queue's
convention).

**Coming back**, put the raw output (zipped is fine, one zip per request or one
per sitting) on `mvmcc03` under:

```
/home/itadmin/gpo-studio-evidence/inbox/<request-id>/
```

Use any transfer method. Only the destination matters, and it must be:

- **Outside the repository working tree.** Raw output has not passed identifier
  review, so it must never sit where a broad `git add` can reach it.
- **Not under `/tmp`.** `/tmp` on `mvmcc03` is tmpfs (RAM-backed), and a
  multi-megabyte backup tree there costs memory.
- **Not `<repo>/samples/`.** `scripts/check_committed_identifiers.py` fails any
  tracked file whose first path component is `samples`, which makes it a good
  last line of defence. It is *not* in this repo's `.gitignore`, so it is not a
  good first one.

Tell the agent the request IDs and the exact GPO names you used. The agent
retrieves, inspects, sanitises, curates a fixture and drives cleanup.

### Sanitisation

Read [`corpus-topology-redaction.md`](corpus-topology-redaction.md) for the
principle: a committed fixture models the *shape* of production, never the
*structure* of one estate.

**The identifier gate will not catch live-domain identifiers.** AGENTS.md
permits homelab identifiers (`hraedon` and `mvm*`), because `ad.hraedon.com`
used to be this project's validation forest (see the superseded section of
`plan-033/environment-spec.md`). So `scripts/check_committed_identifiers.py`
passes a file containing `ad.hraedon.com`, `HRAENET` and a real DC hostname. It
does **not** catch what the live domain now holds and the old validation forest
did not: a **populated production directory**, with real user account names,
group names, OU structure, service accounts, UNC paths, computer names and the
real domain SID.

So:

- **Prefer sanitisation by construction.** Several requests shape the query so
  identifiers never leave the domain, selecting only the attributes wanted
  rather than filtering afterwards. That is why requests 6 and 8 are written the
  way they are.
- **The existing sanitiser is `scripts/plan-033/sanitize-gpp-fixtures.py`**,
  with rules `replace-domain-sid`, `replace-sd-hex` and `replace-gpreport-sid`.
  It reads the real domain SID prefix from `GPO_STUDIO_REAL_SID_PREFIX` at run
  time and never commits it. It writes a `sanitization-record.json` with raw and
  sanitised hashes per file, as
  `tests/fixtures/native-gpp-gpmc/sanitization-record.json` records.
- **That sanitiser was written for the lab.** It handles domain SIDs and
  security-descriptor blobs. It does **not** know about real user, group or OU
  names, and it applies the generic SID rule only to files named
  `gpreport*.xml`. Live-domain artifacts need a manual pass on top of it.

Each request below says what its artifacts contain and what must **not** be
committed at all.

---

## Ordering

Requests are ordered by **value per minute of your time**, cheapest decisive
test first, then batched by host and session, because switching estates costs
more than any single request.

That gives three sittings. Sitting A alone settles the five questions most
likely to change the plan.

| Sitting | Requests | Where | Est. | Character |
|---|---|---|---|---|
| **A** | 1–5 | `LabMS01` (GPMC console) | ~90 min | Direction A capture. Nothing touches the live domain. |
| **B** | 6–8 | `ad.hraedon.com` | ~40 min | **Read-only.** Nothing is created, modified or deleted. |
| **C** | 9–11 | `LabMS01`, then live | ~55 min | Direction B. **Blocked** until Studio-side bundles exist (see each). |

Sittings A and B are independent and can run in either order. Sitting C cannot
start until the bundles named in requests 9–11 are generated and handed over.

**Total: approximately 185 minutes**, of which requests 1–5 are ~90.

### The eleven, at a glance

| # | Settles | Where | Dir | Min | Writes? |
|---|---|---|---|---|---|
| 1 | Is `migration.py`'s `.migtable` namespace GPMC's? A mismatch is a **silent no-op on a live API endpoint** | LabMS01 | A | 8 | local file only |
| 2 | Is a native `scripts.ini` UTF-16/BOM/CRLF, and is `[Policy]` real? **The WP-3 finding's exact shape** | LabMS01 | A | 20 | disposable GPO |
| 3 | Is Folder Redirection in `fdeploy.ini` rather than `User Shell Folders`? **Changes Plan 027's scope** | LabMS01 | A | 15 | disposable GPO |
| 4 | Which propagation code means what — **the repo contradicts itself** — plus the first native `GptTmpl.inf` we have ever parsed | LabMS01 | A | 25 | disposable GPO |
| 5 | Is `gpt.ini`'s `Version=` a packed 32-bit field that `+1` corrupts? Plus a reusable multi-CSE reference backup | LabMS01 | A | 20 | disposable GPO |
| 6 | Are `_KNOWN_CSE_GUIDS`' two GPP entries actually XML `clsid`s, not CSE GUIDs? Measured against a real GPO population | **live** | A | 15 | **no — read-only** |
| 7 | `[Kerberos Policy]` key names and units. **Unblocks a named `(b)` residual** — it cannot be measured on a member server | **live**, on a DC | A | 5 | **no — read-only** |
| 8 | What a published GPO actually consists of, versus the six steps `publication.py` plans | **live** | A | 20 | **no — read-only** |
| 9 | Does `secedit` accept `object_security.py`'s `key = value` shape, or only the native bare-CSV line? | LabMS01 | **B** | 10 | nothing |
| 10 | Does Windows *accept* a Studio-written `scripts.ini`, or silently ignore it? | LabMS01 | **B** | 25 | disposable GPO |
| 11 | Does a **production** directory accept Studio's output as the lab does? | **live** | **B** | 20 | **one unlinked GPO** |

As written (2026-08-06), requests 9–11 were blocked on Studio-side bundles (see
each request), and only R11's bundle could be produced with the code of that
day. R12 was added on 2026-09-11 and is not in this table; see
[R12](#r12--the-folder-redirection-flags-and-a-second-group-rule).

Directions:

- **Direction A — reference capture.** GPMC authors it, you export it, and we
  compare our writer against what Windows produced. Settles wire format.
- **Direction B — round-trip conformance.** *We* produce an artifact, you feed
  it to native tooling, and you return what came back. This is stronger: it
  proves Windows **accepted** our output, rather than that our output resembles
  a sample.

---

# Sitting A — `LabMS01`, GPMC console (~90 min)

All five requests use the disposable evidence estate and do not touch
`ad.hraedon.com`. Use the `zz-studio-evidence-NN-<slug>` names, leave every GPO
**unlinked**, and run R5's cleanup block at the end to remove all of them at
once.

Before you start:

```powershell
New-Item -ItemType Directory -Force -Path 'C:\gpo-studio\manual' | Out-Null
```

---

## R1 — One GPMC-authored migration table

| | |
|---|---|
| **Settles** | Whether GPMC's `.migtable` namespace and element shape match what `migration.py` parses |
| **Direction** | A |
| **Estate** | `LabMS01` |
| **Time** | 8 minutes. No GUI beyond launching one tool. |
| **Writes** | A local file only |
| **Send back** | `studio.migtable` |

### Steps

1. On `LabMS01`, open a Windows PowerShell 5.1 console **as administrator**.
2. Run `mtedit.exe` (the Migration Table Editor, installed with GPMC).
   *Unverified: if `mtedit.exe` is not on `PATH`, it is under
   `C:\Windows\System32\`. If it is absent entirely, use the COM fallback below
   and tell us.*
3. Add **four rows**, one per source type, so the file exercises more than one
   shape. Use these exact values; they are synthetic and safe to commit:

   | Source Name | Source Type | Destination Name |
   |---|---|---|
   | `LAB\zz-studio-src-group` | Global Group | `LAB\zz-studio-dst-group` |
   | `LAB\zz-studio-src-user` | User | `LAB\zz-studio-dst-user` |
   | `\\zz-studio-src\share` | UNC Path | `\\zz-studio-dst\share` |
   | `LAB\zz-studio-src-local` | Domain Local Group | *(leave as "Same as source")* |

   The editor may complain that these principals do not resolve. That is fine:
   unresolvable entries still serialise. If it refuses to save, substitute any
   real lab principal and tell us which.
4. **File → Save As** → `C:\gpo-studio\manual\r01-migtable\studio.migtable`.
   Create the directory first if the dialog will not.

**COM fallback**, if `mtedit.exe` is unavailable:

```powershell
$root = 'C:\gpo-studio\manual\r01-migtable'
New-Item -ItemType Directory -Force -Path $root | Out-Null
$gpm = New-Object -ComObject GPMgmt.GPM
$c   = $gpm.GetConstants()
$mt  = $gpm.CreateMigrationTable()
$mt.AddEntry('LAB\zz-studio-src-group', $c.EntryTypeGlobalGroup, 'LAB\zz-studio-dst-group')
$mt.AddEntry('LAB\zz-studio-src-user',  $c.EntryTypeUser,        'LAB\zz-studio-dst-user')
$mt.AddEntry('\\zz-studio-src\share',   $c.EntryTypeUNCPath,     '\\zz-studio-dst\share')
$mt.Save("$root\studio.migtable")
```

*Unverified: the `EntryType*` constant names come from the GPMC COM reference
and have not been checked on Server 2025. If one errors, run
`$c | Get-Member -MemberType Property | Where-Object Name -like 'EntryType*'`,
use the nearest name, and tell us which names exist.*

### What to return

One file: `C:\gpo-studio\manual\r01-migtable\studio.migtable`.

If easy, also paste its first three lines into your reply. The XML declaration
and root element answer the question on their own.

### Sanitisation

None. Every value is synthetic. If you substituted a real lab principal, say
which and we will replace it before committing.

### Cleanup

Delete `C:\gpo-studio\manual\r01-migtable` after transfer. No GPO was created
and nothing in the directory was touched.

### Background

`migration.py` parses `.migtable` XML in namespace
`http://www.microsoft.com/GroupPolicy/Types`, looking for `Mapping` elements
containing `Source`/`Destination` → `Identifier` → `Sid`|`Name`. **Every
`.migtable` in this repository was hand-written by this project**: the only
matching files are inline strings in `tests/test_migration.py` and
`tests/test_lifecycle.py`. No GPMC-authored migration table exists in the tree,
and nobody had checked that namespace or element shape against GPMC.

- **If GPMC's output parses with entries**, the reader is right. This is the
  only outcome in Sitting A that is merely confirmatory, and it still removes a
  live risk for eight minutes.
- **If the namespace or element shape differs**, `parse_migration_table`
  iterates `root.iter(f"{{{_GPMC_NS}}}Mapping")`, finds nothing, returns an
  **empty table without raising**, and `apply_migration` returns the GPO
  unchanged. That is a **silent no-op on a live API endpoint**:
  `migration_table_path` on the backup-import endpoint at `api.py:3129`. An
  operator would upload a migration table, get a 200, and get no migration.

R1 is first because it is the cheapest request, the most likely to fire, and the
only one that concerns *surfaced 1.0 code* rather than an unproven draft.

It runs on `LabMS01` because the question is about file format, which is a
property of GPMC, not of the directory. A table authored against the live domain
would carry real principal names and a real domain SID for no extra
information.

---

## R2 — A native `scripts.ini` and `psscripts.ini`

| | |
|---|---|
| **Settles** | Native `scripts.ini` encoding, line endings, and whether `[Policy]` exists in `psscripts.ini` |
| **Direction** | A |
| **Estate** | `LabMS01` |
| **Time** | 20 minutes |
| **Writes** | Disposable GPO `zz-studio-evidence-02-scripts` |
| **Send back** | The whole `r02-scripts-ini\` directory, plus the console output of step 9 |

### Steps

1. GPMC (`gpmc.msc`) → `Forest: ad.labdomain.dev` → Domains → `ad.labdomain.dev`
   → **Group Policy Objects** → right-click → **New**.
   Name: `zz-studio-evidence-02-scripts`. **Do not link it.**
2. Right-click it → **Edit**.
3. **Computer Configuration → Policies → Windows Settings → Scripts
   (Startup/Shutdown)**.
4. Double-click **Startup**. On the **Scripts** tab → **Add**:
   - Script Name: `zz-studio-marker.cmd`
   - Script Parameters: `/c alpha beta`

   Click **Add** again and add a second script so ordering is visible:
   - Script Name: `zz-studio-second.cmd`
   - Script Parameters: *(leave empty; an empty-parameter entry is its own
     question)*
5. Still in the Startup dialog, switch to the **PowerShell Scripts** tab →
   **Add**:
   - Script Name: `zz-studio-marker.ps1`
   - Script Parameters: `-Mode Alpha`
6. On the same **PowerShell Scripts** tab, set the dropdown **"For this GPO,
   run scripts in the following order"** to **"Run Windows PowerShell scripts
   first"**. We believe this maps to `StartExecutePSFirst`; a non-default value
   makes its encoding visible.
7. Click **OK**. Reopen the Startup dialog, confirm both tabs kept what you
   entered, then close the editor.
8. **Leave `Shutdown` empty.** Whether GPMC writes an empty `[Shutdown]`
   section, and whether it writes a comment line there as the module does, is
   one of the questions.
9. Capture:

```powershell
$root = 'C:\gpo-studio\manual\r02-scripts-ini'
New-Item -ItemType Directory -Force -Path $root, "$root\backup" | Out-Null
Backup-GPO -Name 'zz-studio-evidence-02-scripts' -Path "$root\backup" `
  -Comment 'R2 native scripts.ini capture'
Get-GPOReport -Name 'zz-studio-evidence-02-scripts' -ReportType Xml `
  -Path "$root\gpreport.xml"

# The three answers, inline. Adjust the backup GUID folder name.
$bk  = (Get-ChildItem "$root\backup" -Directory | Where-Object Name -like '{*}').FullName
$ini = Join-Path $bk 'DomainSysvol\GPO\Machine\Scripts\scripts.ini'
$psi = Join-Path $bk 'DomainSysvol\GPO\Machine\Scripts\psscripts.ini'
foreach ($p in @($ini, $psi)) {
  if (-not (Test-Path $p)) { "ABSENT: $p"; continue }
  $b = [System.IO.File]::ReadAllBytes($p)
  "$p"
  "  first 4 bytes : {0:X2} {1:X2} {2:X2} {3:X2}" -f $b[0],$b[1],$b[2],$b[3]
  "  CR count      : $(($b | Where-Object { $_ -eq 13 }).Count)"
  "  LF count      : $(($b | Where-Object { $_ -eq 10 }).Count)"
  "  size          : $($b.Length)"
}
```

Paste that console output into your reply. It answers questions 1 and 2 before
we retrieve anything.

### What to return

The **whole** directory `C:\gpo-studio\manual\r02-scripts-ini\`. Before zipping,
check these exist:

- `backup\{GUID}\DomainSysvol\GPO\Machine\Scripts\scripts.ini`
- `backup\{GUID}\DomainSysvol\GPO\Machine\Scripts\psscripts.ini`
- `backup\{GUID}\Backup.xml` (for the Scripts CSE GUID in the extension lists)
- `gpreport.xml`

GPMC will *not* have created the `Scripts\Startup\` bodies, because the named
scripts do not exist. That is intended and does not affect the answer; the INI
is the artifact.

### Sanitisation

`Backup.xml` and `gpreport.xml` carry the lab domain (`ad.labdomain.dev`,
`LAB`), the lab DC name, and a security-descriptor hex blob containing the lab
domain SID. `scripts/plan-033/sanitize-gpp-fixtures.py` handles all of that
(`replace-domain-sid`, `replace-sd-hex`). The two INI files should contain only
the synthetic script names above. Check before sending, and if GPMC substituted
a full path containing a real share, say so.

### Cleanup

Deferred to R5's block, which removes all of Sitting A's GPOs at once.

### Background

`serialize_script_policy_ini()` (`script_policy.py:412`) returns a **`str`**
built by `"\n".join(lines)`. The module has no encoding, BOM, CRLF, file path or
CSE registration, and nothing in this repository is the caller that would
supply them.

This is the exact shape of the WP-3 finding, in a module no oracle had read.
When `secedit` first looked at `security_template.py`'s output, the file was not
a valid security template: wrong encoding, missing preamble, wrong line endings.
The correction was +36 −1 lines and changed everything about the meaning. Three
observations settle whether the same is true here:

1. **Does `scripts.ini` begin `FF FE`?** If yes, it is UTF-16LE with a BOM and
   the module emits the wrong encoding for every file it writes.
2. **Are the line endings CRLF or LF?** The module joins with `"\n"`.
3. **Does `psscripts.ini` contain a `[Policy]` section?** The module appends
   one, carrying `RunLogonScriptsSync`, `RunLogoffScriptsSync`,
   `LegacyScriptsFirst` and `PowerShellOrder`. `RunLogonScriptsSync` is an
   Administrative Templates setting that lives in `Registry.pol`, not in an INI.
   The suspicion is that native `psscripts.ini` carries a **`[ScriptsConfig]`**
   section with `StartExecutePSFirst` / `EndExecutePSFirst` instead. If so,
   `[Policy]` is invented and four modelled settings have no representation on
   the wire.

Any one of the three matching the module's assumption is a real answer. All
three matching would be the surprise.

It runs on `LabMS01` because scripts have **no cmdlet authoring surface**: the
GPMC editor snap-in is the only native producer, so this needed a console. The
live domain adds nothing, because `scripts.ini` is a SYSVOL file whose format
does not depend on the directory.

---

## R3 — A GPMC-authored Folder Redirection GPO

| | |
|---|---|
| **Settles** | Which file the Folder Redirection CSE reads, how folders are keyed, how option flags and multiple group rules are encoded |
| **Direction** | A |
| **Estate** | `LabMS01` |
| **Time** | 15 minutes |
| **Writes** | Disposable GPO `zz-studio-evidence-03-folderredir` |
| **Send back** | The whole `r03-folderredir\` directory, plus the file listing and `Select-String` output |

> **Delivered partially, 2026-09-04; noticed 2026-09-11.** Step 3 (Documents,
> Basic) was captured. **Step 4 was not**: no Advanced folder and no second
> group, so no multi-rule representation and no default-encoding control to
> read step 3's non-default one against. The capture settles question 1 and
> shows question 2's key form once. Questions 3 and 4 are open, and a writer
> needs both. R12 is step 4, re-requested. Nothing in the result was wrong; the
> gap is that "four things fall out of the same file" was recorded as though
> four things had.

### Steps

1. GPMC → **Group Policy Objects** → New →
   `zz-studio-evidence-03-folderredir`. **Unlinked.** Right-click → **Edit**.
2. **User Configuration → Policies → Windows Settings → Folder Redirection.**
3. Right-click **Documents** → **Properties**.
   - **Setting:** `Basic - Redirect everyone's folder to the same location`
   - **Target folder location:** `Create a folder for each user under the root path`
   - **Root Path:** `\\zz-studio-fileserver\zzredir`
   - **Settings tab**: set these **away from their defaults**, so their
     encoding is visible:
     - **Uncheck** "Grant the user exclusive rights to Documents"
     - **Check** "Move the contents of Documents to the new location"
     - Under policy removal, select **"Redirect the folder back to the local
       userprofile location when policy is removed"**
   - **OK.** GPMC may warn that the path is not accessible, or offer to create
     it. **Accept the warning and decline creation.** The lab has no such server
     and does not need one; the INI records the path you typed.
     *Unverified: if GPMC on Server 2025 refuses to save an unreachable UNC
     path, substitute any share that resolves inside the lab and tell us
     which.*
4. Right-click **Pictures** → **Properties**.
   - **Setting:** `Advanced - Specify locations for various user groups`
   - **Add** two groups so multi-rule representation is visible. Use two groups
     that exist in the lab. `LAB\Domain Users` and `LAB\Domain Admins` are fine
     and are not sensitive.
     - `LAB\Domain Users` → root path `\\zz-studio-fileserver\zzpics-a`
     - `LAB\Domain Admins` → root path `\\zz-studio-fileserver\zzpics-b`
   - **Settings tab:** leave the defaults, so we can tell a default encoding
     from the non-default one on Documents.
   - **OK.**
5. Close the editor. Reopen both property pages to confirm GPMC persisted them.
6. Capture:

```powershell
$root = 'C:\gpo-studio\manual\r03-folderredir'
New-Item -ItemType Directory -Force -Path $root, "$root\backup" | Out-Null
Backup-GPO -Name 'zz-studio-evidence-03-folderredir' -Path "$root\backup" `
  -Comment 'R3 folder redirection capture'
Get-GPOReport -Name 'zz-studio-evidence-03-folderredir' -ReportType Xml `
  -Path "$root\gpreport.xml"

# The one-line answer.
$bk = (Get-ChildItem "$root\backup" -Directory | Where-Object Name -like '{*}').FullName
Get-ChildItem -Recurse -File "$bk\DomainSysvol" | Select-Object FullName, Length
Select-String -Path (Get-ChildItem -Recurse -File "$bk\DomainSysvol").FullName `
  -Pattern 'User Shell Folders' -SimpleMatch
```

Paste the file listing and the `Select-String` result (we expect it to be empty)
into your reply.

### What to return

The whole of `C:\gpo-studio\manual\r03-folderredir\`. The key file is whatever
appeared under `DomainSysvol\GPO\User\`. We expect
`Documents & Settings\fdeploy.ini`, but we do not know, so **return the entire
`DomainSysvol` tree**, not just the file we guessed. `Backup.xml` is needed too,
for the Folder Redirection CSE GUID.

### Sanitisation

The two group names are real lab groups (`LAB\Domain Users`,
`LAB\Domain Admins`) and appear as **real SIDs** in `fdeploy.ini` and
`gpreport.xml`. They are lab SIDs, not production ones, and
`replace-domain-sid` / `replace-gpreport-sid` handle them, but the SID-to-group
mapping must be checked before this becomes a fixture. Everything else
(`zz-studio-fileserver`, the share names) is synthetic.

### Cleanup

Deferred to R5's block.

### Background

`folder_redirection.py`'s `to_registry_settings()` emits tuples targeting
`HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders`.
The Folder Redirection CSE `{25537BA6-77A8-11D2-9B6C-0000F8080861}` is believed
to consume **`fdeploy.ini`**, under the GPO's `User\Documents & Settings\`.
Verified by grep: **`fdeploy.ini` appears nowhere in this repository** except a
single inventory row in `docs/plan-021/capability-inventory.md`; not in `src/`,
`scripts/` or any fixture.

The hypothesis is that `User Shell Folders` is what the CSE **writes on the
client**, not what the GPO **carries**. If so, `to_registry_settings()` is the
**wrong artifact** rather than a wrong serializer, and Plan 027 WP-2 changes
from "fix the serializer" to "decide whether to write one". That is a scope
answer, for the cost of one backup and one grep.

The same file answers four questions:

1. Which file the CSE actually reads.
2. How each folder is keyed.
3. How the four option flags (grant exclusive rights, move contents, remove
   redirect on policy removal, also redirect subfolders) are encoded. The
   module models all four and **emits none of them**
   (`folder_redirection.py:317–336`).
4. How multiple group rules are represented. In `advanced` mode
   `effective_path()` returns `rules[0].target_path`, so a three-group policy
   emits one path and silently discards two.

It runs on `LabMS01` for the same reason as R2: a GPMC editor snap-in with no
cmdlet surface, and a SYSVOL file format that does not depend on the directory.
Authoring it against the live domain would put real group names and a real
file-server UNC path into the artifact for no gain.

---

## R4 — Security Settings: propagation codes, and the first native `GptTmpl.inf` this project has ever read

| | |
|---|---|
| **Settles** | 4a: which propagation code means what. 4b: how much of a native `GptTmpl.inf` the parser can read |
| **Direction** | A |
| **Estate** | `LabMS01` |
| **Time** | 25 minutes. Two questions, one GPO, one backup. |
| **Writes** | Disposable GPO `zz-studio-evidence-04-secsettings` |
| **Send back** | `GptTmpl.inf` **as a file**, the backup tree, `gpreport.xml`, the `Get-Content` output, and which radio label you clicked for each key |

### Steps

1. GPMC → **Group Policy Objects** → New →
   `zz-studio-evidence-04-secsettings`. **Unlinked.** → **Edit**.
2. **Computer Configuration → Policies → Windows Settings → Security Settings →
   Registry.** Right-click → **Add Key**. Do this three times, once per radio
   option:

   | # | Key (Select Registry Key dialog) | After OK, in the security dialog, choose |
   |---|---|---|
   | 1 | `MACHINE\SOFTWARE\zzStudioAlpha` | *Configure this key then* → **Propagate inheritable permissions to all subkeys** |
   | 2 | `MACHINE\SOFTWARE\zzStudioBravo` | *Configure this key then* → **Replace existing permissions on all subkeys with inheritable permissions** |
   | 3 | `MACHINE\SOFTWARE\zzStudioCharlie` | **Do not allow permissions on this key to be replaced** |

   For keys 1 and 2, when the ACL editor opens, add **Administrators** with
   **Full Control** and leave the rest. Only the option code matters, not the
   DACL. The dialog lets you type a key path that does not exist; accept it.

   *Unverified: the radio-button wording comes from the classic Security
   Settings dialog. If Server 2025's wording differs, choose the option that is
   clearly first, second or third, and **record which label you actually
   clicked for each key**. That mapping is the answer, so the label text matters
   more than the key names.*

3. **Security Settings → File System.** Right-click → **Add File**. Path
   `C:\zzStudioData`. Add **Administrators / Full Control**, then choose
   **Replace existing permissions on all subfolders and files with inheritable
   permissions**. One entry is enough: the registry trio maps the codes, and
   this confirms files use the same vocabulary.

4. **Security Settings → Restricted Groups.** Right-click → **Add Group** →
   type `zz-studio-restricted` → OK. In *Members of this group*, **Add** →
   `LAB\Domain Admins`. OK.
   *If GPMC refuses a non-existent group name, use `Backup Operators` instead
   and tell us.*

5. **Security Settings → Account Policies → Account Lockout Policy.** Set:
   - Account lockout duration: **47** minutes
   - Account lockout threshold: **13** invalid logon attempts
   - Reset account lockout counter after: **11** minutes

   These non-default, distinguishable numbers make a unit conversion impossible
   to hide. `policy_families.py` writes `lockout_duration_minutes` straight out
   with no conversion; if Windows stores anything other than `47`, we will see
   it.

6. Close the editor. Capture:

```powershell
$root = 'C:\gpo-studio\manual\r04-secsettings'
New-Item -ItemType Directory -Force -Path $root, "$root\backup" | Out-Null
Backup-GPO -Name 'zz-studio-evidence-04-secsettings' -Path "$root\backup" `
  -Comment 'R4 security settings capture'
Get-GPOReport -Name 'zz-studio-evidence-04-secsettings' -ReportType Xml `
  -Path "$root\gpreport.xml"

$bk  = (Get-ChildItem "$root\backup" -Directory | Where-Object Name -like '{*}').FullName
$inf = Join-Path $bk 'DomainSysvol\GPO\Machine\Microsoft\Windows NT\SecEdit\GptTmpl.inf'
Copy-Item $inf "$root\GptTmpl.inf"
$b = [System.IO.File]::ReadAllBytes($inf)
"first 4 bytes : {0:X2} {1:X2} {2:X2} {3:X2}" -f $b[0],$b[1],$b[2],$b[3]
Get-Content -Path $inf   # PowerShell autodetects the BOM
```

Paste the `Get-Content` output into your reply. It is short and contains the
whole answer to 4a.

### What to return

- `C:\gpo-studio\manual\r04-secsettings\GptTmpl.inf`, **the primary artifact.**
  Send it as a file, not pasted text: the encoding is part of the evidence, and
  pasting destroys it.
- The full `backup\` tree and `gpreport.xml`.
- Your note of which radio-button label you clicked for each of
  `zzStudioAlpha` / `zzStudioBravo` / `zzStudioCharlie`.

### Sanitisation

`GptTmpl.inf` will contain `[Group Membership]` keyed by the **lab domain SID**
for `Domain Admins`, and `[Registry Keys]`/`[File Security]` SDDL strings
containing `BA` (well-known, fine) and possibly the lab domain SID.
`replace-domain-sid` handles the prefix. `gpreport.xml` needs
`replace-gpreport-sid` as usual. The registry paths, the file path and the
restricted-group name are synthetic.

### Cleanup

Deferred to R5's block.

### Background

**4a — the propagation codes.** The repository contradicted itself:

- `object_security.py:119–128` (`_propagation_from_code`) maps
  `0 → none`, `1 → propagate`, `2 → replace`.
- `tests/fixtures/scenarios/security-template/regkeys-filesecurity.json:57`
  states `0 = propagate inheritable permissions to subkeys/subfolders`,
  `1 = replace existing permissions`, `2 = do not allow permissions to be
  replaced`, and says these are spec-informed until the first capture.

Both were unverified, and they cannot both be right. This is the same class of
question as WI-024, where the answer was wrong by a factor of 1000.

The GPMC Registry/File-System editor offers exactly three mutually exclusive
radio options for each secured object. Authoring one key under each option and
reading back the integer Windows wrote maps all three codes in one capture,
**without `secedit /configure`**. That command is destructive and is not asked
for here.

**4b — the read direction.** `parse_security_template` had **never been given a
native template.** Every WP-3 run authored with Studio and read back with
`secedit`. WI-038 establishes that `[Registry Keys]`, `[File Security]` and
`[Service General Setting]` are not `key = value` on the wire. They are bare
quoted-CSV lines, which the parser cannot read, so they land in
`InfSection.unknown_lines` with `entries` empty. Feeding a native file to the
parser and counting `unknown_lines` shows how blind the reader is to the
sections `domain-layer-status.md` names as the risky ones.

`[Group Membership]` is included for the same reason: the module writes
`{sid}__Members`, and whether Windows keys it by SID or by name was unmeasured.

It runs on `LabMS01` because Security Settings authoring is a GPMC snap-in
gesture and the `GptTmpl.inf` format is domain-independent. The one part of
`security_template` that needs a real domain is `[Kerberos Policy]`, which
exports empty on a member server. That is **R7**, kept separate on purpose.

---

## R5 — A multi-CSE reference backup, and the `gpt.ini` version delta

| | |
|---|---|
| **Settles** | Whether `gpt.ini`'s `Version=` is a packed field that a flat `+1` corrupts. Also produces a reusable multi-CSE reference backup. |
| **Direction** | A |
| **Estate** | `LabMS01` |
| **Time** | 20 minutes |
| **Writes** | Disposable GPO `zz-studio-evidence-05-multicse`; its cleanup block removes all Sitting A GPOs |
| **Send back** | The three `READING` blocks (pasted), the whole `r05-multicse\` directory, the extension-name `Format-List` output, and both cleanup outputs |

### Steps

1. GPMC → **Group Policy Objects** → New → `zz-studio-evidence-05-multicse`.
   **Unlinked.** → **Edit**.
2. Add settings across **four** extensions, so the backup is a real multi-CSE
   reference:
   - **Computer Configuration → Policies → Administrative Templates → System →
     Logon** → *Always wait for the network at computer startup and logon* →
     **Enabled**. (Registry CSE, machine side.)
   - **Computer Configuration → Preferences → Windows Settings → Environment**
     → New → Environment Variable: Action `Update`, System variable,
     Name `ZZ_STUDIO_MARKER`, Value `r05`. (GPP Environment CSE.)
   - **Computer Configuration → Policies → Windows Settings → Security
     Settings → Local Policies → Audit Policy** → *Audit account logon events*
     → define, tick **Success** and **Failure**. (Security CSE.)
   - **User Configuration → Policies → Administrative Templates → Control
     Panel** → *Prohibit access to Control Panel and PC settings* →
     **Enabled**. (Registry CSE, **user** side; this one matters for step 4.)
3. Close the editor. **Read `GPT.INI`, reading #1:**

```powershell
$g   = Get-GPO -Name 'zz-studio-evidence-05-multicse'
$dom = $g.DomainName
$gpt = "\\$dom\SYSVOL\$dom\Policies\{$($g.Id)}\GPT.INI"
"READING 1"; Get-Content $gpt; $g | Select-Object @{n='UserVer';e={$_.User.DsaVersion}},
  @{n='UserSysvol';e={$_.User.SysvolVersion}},
  @{n='CompVer';e={$_.Computer.DsaVersion}},
  @{n='CompSysvol';e={$_.Computer.SysvolVersion}}
```

4. Reopen the editor and make **one user-side-only change**: *User Configuration
   → Policies → Administrative Templates → Desktop* → *Hide Network Locations
   icon on desktop* → **Enabled**. Change **nothing** on the computer side.
   Close the editor. **Reading #2**: rerun the block above with the label
   `READING 2`.
5. Reopen the editor and make **one computer-side-only change**: *Computer
   Configuration → Policies → Administrative Templates → System → Logon* →
   *Do not process the legacy run list* → **Enabled**. Close the editor.
   **Reading #3**: rerun the block with the label `READING 3`.
6. Capture:

```powershell
$root = 'C:\gpo-studio\manual\r05-multicse'
New-Item -ItemType Directory -Force -Path $root, "$root\backup" | Out-Null
Backup-GPO -Name 'zz-studio-evidence-05-multicse' -Path "$root\backup" `
  -Comment 'R5 multi-CSE reference backup'
Get-GPOReport -Name 'zz-studio-evidence-05-multicse' -ReportType Xml `
  -Path "$root\gpreport.xml"
Copy-Item $gpt "$root\GPT.INI"

# The extension lists, straight off the directory object.
$dn = (Get-ADDomain).DistinguishedName
Get-ADObject -Identity "CN={$($g.Id)},CN=Policies,CN=System,$dn" `
  -Properties gPCMachineExtensionNames, gPCUserExtensionNames, versionNumber |
  Select-Object gPCMachineExtensionNames, gPCUserExtensionNames, versionNumber |
  Format-List
```

### What to return

- The three `READING` blocks, pasted into your reply. **These are the answer**:
  three `Version=` integers and their deltas.
- The whole of `C:\gpo-studio\manual\r05-multicse\`, including the full backup
  tree. It becomes the reference corpus.
- The `Format-List` output of the extension names.

### Sanitisation

Standard lab treatment: `replace-domain-sid` and `replace-sd-hex` over the
backup, `replace-gpreport-sid` over `gpreport.xml`. `GPT.INI` contains no
identifiers. The `\\<domain>\SYSVOL\...` path in your pasted output contains the
lab domain name, which is allowed.

### Cleanup — **run this at the end of Sitting A**

```powershell
$names = @(
  'zz-studio-evidence-02-scripts',
  'zz-studio-evidence-03-folderredir',
  'zz-studio-evidence-04-secsettings',
  'zz-studio-evidence-05-multicse'
)
foreach ($n in $names) {
  try { Remove-GPO -Name $n -ErrorAction Stop; "removed: $n" }
  catch { "NOT REMOVED: $n -- $($_.Exception.Message)" }
}

# Strict absence re-query. This must return nothing.
Get-GPO -All | Where-Object { $_.DisplayName -like 'zz-studio-evidence-*' } |
  Select-Object DisplayName, Id
```

Paste the output of both blocks. If the re-query returns any row, tell us: an
incomplete cleanup makes the sitting `inconclusive` under
`plan-033/boundary-matrix.md`'s evidence-state rule.

### Background

**Half of this request is confirmatory.** The multi-CSE backup will almost
certainly show what we expect: a `Backup.xml` with populated
`MachineExtensionGuids` / `UserExtensionGuids`, a `DomainSysvol` tree and a
`bkupInfo.xml`. Its value is reuse: it becomes **the most reusable artifact in
this document**, and every future lane that needs "what a real multi-CSE backup
looks like" reads it instead of booking you again.

**The `gpt.ini` half can fire.** `publication.py:499` computes
`$expectedVersion = [int]$currentVersion + 1`. `GPT.INI`'s `Version=` is a
**packed 32-bit field**. `docs/live-publication.md` says so itself ("user
changes increment the upper 16 bits and computer changes increment the lower 16
bits"), and this repo's WP-2 finalizer already unpacks the corresponding
`Backup.xml` numbers as two 16-bit halves (`finalize_wp2_import_run.py`:
`packed_machine == (dsa << 16) | sysvol`).

If flat `+1` is wrong, a **user-side-only publication increments the computer
counter**, and clients never reprocess the user side. The GPO goes quietly stale
rather than visibly broken. Two edits and three reads of `GPT.INI` answer it.

It runs on `LabMS01` because it needs a real DC and real SYSVOL, which the lab
has, and not a production directory. On the live domain it would mean creating
a GPO and editing it twice, for an answer the lab gives identically.

---

# Sitting B — `ad.hraedon.com`, read-only (~40 min)

**Nothing in this sitting creates, modifies or deletes anything.** Every command
is a read. The only cleanup is deleting the local staging directory.

Run these from wherever you normally administer the domain: a DC or an admin
workstation with RSAT. **R7 must run on a domain controller**; R6 and R8 can run
from either.

```powershell
New-Item -ItemType Directory -Force -Path 'C:\gpo-studio\manual' | Out-Null
```

---

## R6 — Extension-list census over the existing production GPOs

| | |
|---|---|
| **Settles** | Whether `_KNOWN_CSE_GUIDS`' two GPP entries ever appear as CSE GUIDs in a real GPO population |
| **Direction** | A |
| **Estate** | `ad.hraedon.com` |
| **Time** | 15 minutes |
| **Writes** | **Nothing. Read-only.** |
| **Send back** | `extension-lists.csv`, plus the two summary lines |

### Steps

Run this exactly as written. **It selects only the two extension attributes: no
display names, DNs, GUIDs or creation times.** The identifiers never leave the
domain, so there is nothing to redact afterwards.

```powershell
$root = 'C:\gpo-studio\manual\r06-cse-census'
New-Item -ItemType Directory -Force -Path $root | Out-Null
$dn = "CN=Policies,CN=System,$((Get-ADDomain).DistinguishedName)"

Get-ADObject -SearchBase $dn -LDAPFilter '(objectClass=groupPolicyContainer)' `
  -Properties gPCMachineExtensionNames, gPCUserExtensionNames |
  ForEach-Object {
    [pscustomobject]@{
      machine = $_.gPCMachineExtensionNames
      user    = $_.gPCUserExtensionNames
    }
  } | Export-Csv -NoTypeInformation -Encoding UTF8 -Path "$root\extension-lists.csv"

# Summary you can eyeball before sending.
"total GPOs: $((Import-Csv "$root\extension-lists.csv").Count)"
Select-String -Path "$root\extension-lists.csv" -SimpleMatch `
  -Pattern '3125E937', 'A3CC7818' | Measure-Object | Select-Object Count
```

### What to return

One file: `C:\gpo-studio\manual\r06-cse-census\extension-lists.csv`.

Paste the two summary lines into your reply: the total GPO count and the match
count for the two suspect GUIDs.

### Sanitisation

**Open the CSV before sending it.** It should contain only bracketed GUID
strings and commas. A CSE extension list cannot carry a name or a SID, so if you
see anything that looks like a word, a domain or an `S-1-5-…`, stop and tell us:
the query returned more than it asked for.

The GPO **count** is a weak piece of estate structure. It is fine in a reply to
us; we will not commit the raw count in a fixture.

**Do not** add `displayName`, `distinguishedName`, `gPCFileSysPath` or
`whenCreated` to the `-Properties` list to make it easier to read. Each is a
production identifier, and none is needed.

### Cleanup

Delete `C:\gpo-studio\manual\r06-cse-census` after transfer. Nothing in the
directory was modified.

### Background

`gpmc_interop.py:40–44` defines `_KNOWN_CSE_GUIDS` as three entries. Two are
labelled "GPP Groups" `{3125E937-EB16-4b4c-9934-544FC6D24D26}` and "GPP
Registry" `{A3CC7818-8A30-4e0c-91C5-A4EA4B5A8DAB}`. **Verified: everywhere else
in this repository, those two literals are the `clsid` attribute on the root
element of a GPP XML file** (`<Groups clsid="{3125E937…}">` at `gpp.py:70`,
`gpp_adapters.py:79`, `conformance.py:598`), not client-side extension GUIDs.
Extracting every extension list from the seventeen committed GPMC-authored
backups shows **neither GUID in any of them**. Groups appears as
`[{17D89FEC-…}{79F92669-…}]`, which is also what `export.py:42` emits.

If the hypothesis holds, any GPO carrying GPP `cse_metadata` trips the
`unknown_cse_guid` **error** branch (`gpmc_interop.py:220–228`) and is reported
`is_gpmc_importable = False`, **including every GPMC-authored one.**

The same three literals are defined again at `publication.py:126–128` and are
**never read anywhere in that file**. That is the second half of the question:
`generate_publication_plan` emits no step that updates
`gPCMachineExtensionNames` / `gPCUserExtensionNames`, and those attribute names
appear **nowhere in `src/`**. A GPO whose SYSVOL contains a `Registry.pol` but
whose extension list is empty **is not processed by any client**. It is inert
rather than wrong, which offline tests cannot see.

- **If no production GPO carries either GUID in an extension list**, both
  hypotheses are confirmed against a population rather than seventeen curated
  fixtures, and `_KNOWN_CSE_GUIDS` is wrong.
- **If some do**, we have learned something unexpected and the fixture sweep was
  misleading.
- **Either way** we get a real-world CSE-GUID frequency table, the ground truth
  `_KNOWN_CSE_GUIDS` should have been built from.

**Why only the live domain can settle this.** The lab has no organically
authored GPOs. Every GPO on `LabMS01` was created by this project in the two
weeks before this was written, so asking it which CSE GUIDs appear asks our own
fixtures a second time. That is the self-consistency trap
`domain-layer-status.md` rejects. A production domain has GPOs authored by
different people, across Windows generations, for real reasons. That population
is the oracle, and no lab can manufacture it.

---

## R7 — `[Kerberos Policy]` from a real domain controller

| | |
|---|---|
| **Settles** | `[Kerberos Policy]` key names, units and omission rules |
| **Direction** | A |
| **Estate** | `ad.hraedon.com`, **on a domain controller** |
| **Time** | 5 minutes |
| **Writes** | **Nothing in the directory or SYSVOL. Read-only.** A local `.inf` file that you delete before leaving. |
| **Send back** | **The two console outputs, pasted. No files.** |

### Steps

On a **domain controller** in `ad.hraedon.com`, in an elevated PowerShell 5.1
console:

```powershell
$root = 'C:\gpo-studio\manual\r07-kerberos'
New-Item -ItemType Directory -Force -Path $root | Out-Null

secedit /export /areas SECURITYPOLICY /cfg "$root\dc-effective.inf" /quiet

# Print ONLY the section we need. Do not send the whole file (see below).
$txt = Get-Content "$root\dc-effective.inf"
$i = ($txt | Select-String -SimpleMatch '[Kerberos Policy]').LineNumber
$txt[($i-1)..($i+8)]
```

Then, so we can tell a converted number from a stored one:

```powershell
Get-ADDefaultDomainPasswordPolicy |
  Select-Object MaxTicketAge, MaxServiceAge, MaxClockSkew, MaxRenewAge
```

`Get-ADDefaultDomainPasswordPolicy` returns .NET `TimeSpan` values, so it states
in plain units what the raw integers mean. Comparing the two outputs is the
whole oracle.

### What to return

**Paste the two console outputs into your reply. Send no files.** That is about
ten lines of text.

### Sanitisation — the strictest in this document

**Never send or commit `dc-effective.inf`, under any circumstance.**
`secedit /export /areas SECURITYPOLICY` on a production DC exports the
**effective domain security policy**: `[Privilege Rights]` with the real SID of
every principal holding every privilege, `[Group Membership]` with real group
SIDs, `[System Access]` with the domain's real password and lockout posture, and
`[Registry Values]` with the real security-options configuration. That is a
security-relevant description of the production domain and is not fixture
material at any level of redaction.

The **`[Kerberos Policy]` section alone** is safe: five integer key/value pairs,
with no principals, SIDs or names. That is why the command prints a slice.

The values are the domain's real Kerberos settings. They are policy posture, not
secrets. If they are the Windows defaults we can commit them as a fixture. If
they are non-default we will use them only to *interpret* the units and commit
synthetic values instead. Tell us if you would rather we did that either way.

### Cleanup — **before you leave the console**

```powershell
Remove-Item -Force 'C:\gpo-studio\manual\r07-kerberos\dc-effective.inf'
Remove-Item -Recurse -Force 'C:\gpo-studio\manual\r07-kerberos'
```

The file is the sensitive artifact in this document. Do not let it sit on disk.

### Background

`[Kerberos Policy]` is a **named blocker** in the survey: a `(b)` residual on
both `security_template.py` and `policy_families.py`. It **cannot be measured on
`LabMS01`**: it exports empty on a member server (measured 2026-08-04), and
`platforms.json` states that the `dc-ws2025` host_id "is historical and does not
imply the lane executes on the DC." No lane has ever executed on `LabDC01`.

`policy_families.py:267–274` writes `max_ticket_age_hours`,
`max_renewal_age_days` and `max_clock_skew_minutes` as bare integers with **no
conversion**, under the key names `MaxTicketAge`, `MaxRenewAge` and
`MaxClockSkew`. Whether Windows means hours, days and minutes for those keys had
never been observed. It is the same class of question as WI-024.

One `secedit /export` on a real DC produces the section with the domain's actual
values, and settles the key names, the unit shape and the omission rules at
once.

It uses the live domain because that is the only domain controller available.
`LabDC01` exists, but no lane has run there, and running one is a larger job than
this request. `secedit /export` is read-only: it writes a local `.inf` file and
touches nothing in the directory or SYSVOL.

---

## R8 — What a real published GPO actually consists of

| | |
|---|---|
| **Settles** | Whether `publication.py`'s six step kinds cover everything Windows maintains for a published GPO |
| **Direction** | A |
| **Estate** | `ad.hraedon.com` |
| **Time** | 20 minutes |
| **Writes** | **Nothing. Read-only.** |
| **Send back** | Nine files (three per GPO) plus the `gpos_with_wmi_filter` count |

### Steps

1. **Pick three GPOs** that are, as far as you can tell, *different in kind*:
   for example one policy-heavy Administrative Templates GPO, one carrying
   Preferences, and one carrying security settings. Do not pick anything
   sensitive, and do not tell us their names.

2. For each, run this. **It reports attribute names and whether they are
   populated, never their values**, so nothing needs redacting afterwards.

```powershell
$root = 'C:\gpo-studio\manual\r08-gpo-anatomy'
New-Item -ItemType Directory -Force -Path $root | Out-Null

# Repeat for each chosen GPO. $n is just a counter: 1, 2, 3.
$n    = 1
$gpo  = Get-GPO -Name '<the GPO display name>'
$dn   = (Get-ADDomain).DistinguishedName
$obj  = Get-ADObject -Identity "CN={$($gpo.Id)},CN=Policies,CN=System,$dn" -Properties *

$obj.PropertyNames | Sort-Object | ForEach-Object {
  $v = $obj.$_
  [pscustomobject]@{
    attribute = $_
    populated = -not ([string]::IsNullOrEmpty(($v -join '')))
    kind      = if ($null -eq $v) { 'null' } else { $v.GetType().Name }
  }
} | Export-Csv -NoTypeInformation -Encoding UTF8 -Path "$root\gpo$n-attributes.csv"

# SYSVOL structure: relative paths and sizes, no contents.
$sys = "\\$($gpo.DomainName)\SYSVOL\$($gpo.DomainName)\Policies\{$($gpo.Id)}"
Get-ChildItem -Recurse -File $sys | ForEach-Object {
  [pscustomobject]@{
    relative = $_.FullName.Substring($sys.Length)
    bytes    = $_.Length
  }
} | Export-Csv -NoTypeInformation -Encoding UTF8 -Path "$root\gpo$n-sysvol.csv"

# The one file whose contents we DO want, because it is five integers.
Copy-Item "$sys\GPT.INI" "$root\gpo$n-GPT.INI"
```

3. Once for the domain, so we know what a WMI-filter association looks like
   when one exists:

```powershell
Get-ADObject -SearchBase "CN=Policies,CN=System,$dn" `
  -LDAPFilter '(&(objectClass=groupPolicyContainer)(gPCWQLFilter=*))' |
  Measure-Object | Select-Object @{n='gpos_with_wmi_filter';e={$_.Count}}
```

### What to return

- `gpo1-attributes.csv`, `gpo2-attributes.csv`, `gpo3-attributes.csv`
- `gpo1-sysvol.csv`, `gpo2-sysvol.csv`, `gpo3-sysvol.csv`
- `gpo1-GPT.INI`, `gpo2-GPT.INI`, `gpo3-GPT.INI`
- The `gpos_with_wmi_filter` count, pasted into your reply.

### Sanitisation

- **`*-attributes.csv`** contains LDAP attribute *names* and booleans, so it is
  safe by construction. **Check it before sending.** If a value column slipped
  in, that is a bug in the script, and we would rather fix it than redact.
- **`*-sysvol.csv`** contains relative paths under the GPO folder. These are
  Windows-defined structural paths (`\Machine\Registry.pol`,
  `\User\Preferences\Drives\Drives.xml`). They are safe unless a GPO carries a
  script or file whose *name* is an identifier. **Open the CSV and look.** If a
  file name identifies a person, a server or a project, delete that row before
  sending and tell us you did.
- **`*-GPT.INI`** is `[General]` + `Version=` + `displayName=`. The
  `displayName=` line, where present, is a **real GPO name**: delete it, or send
  only the `Version=` line. It is the one identifier guaranteed to be present in
  this request.
- **Do not send `Get-GPOReport` output for any production GPO.** It contains
  every setting value, every principal in the security filtering, and the full
  security descriptor, and this question does not need it.

### Cleanup

Delete `C:\gpo-studio\manual\r08-gpo-anatomy` after transfer. Nothing was
modified, and the three GPOs you read were not touched.

### Background

`publication.py`'s `generate_publication_plan` emits exactly six step kinds:
`update_gpt_ini`, `write_registry_pol`, `copy_gpp_xml`,
`update_nt_security_descriptor`, `associate_wmi_filter`, `update_gplink`. That
list is a **hypothesis about what publishing a GPO consists of**, never checked
against a GPO that Windows actually published.

**Any attribute Windows maintains that no `PublicationStep` mentions is a hole in
the plan.** One was already suspected: `gPCMachineExtensionNames` /
`gPCUserExtensionNames`, which appear nowhere in `src/` (see R6). This request
looks for the ones nobody has thought of. It is the biggest unknown in the
product, and a real domain is uniquely able to settle it.

- **If the six steps cover every non-empty attribute and every SYSVOL file
  class**, the plan is complete, which is a real result.
- **If they do not**, we get an enumerated list of what publication forgets,
  which is the input to Plan 030's scope.

It uses the live domain for the same reason as R6, one level up. A lab GPO's
attribute set is whatever *we* caused to exist. A production GPO's attribute set
is what Windows and real administrative practice caused to exist: WMI filter
associations, non-default DACLs, delegated permissions,
`gPCFunctionalityVersion`, and whatever else is there. The lab cannot answer
"what does Windows maintain", because we built the lab.

---

# Sitting C — Direction B (~55 min) — **blocked**

These three requests hand Windows something **Studio** wrote. That proves
Windows *accepted* our output, which is stronger than showing our output
resembles a sample.

**As written, none of them could run yet.** Each needed a Studio-side bundle that
did not exist on disk. Each request names its bundle, with the module, function
and script that would produce it. **Do not start Sitting C until the bundle is
in your hands.**

---

## R9 — `secedit /validate` on Studio's SDDL sections

> **2026-09-07 implementation update:** The request below is the historical
> work order. R9 already rejected the former key/value shape and accepted native
> quoted CSV, and the production formatter was corrected. The builder now emits
> one serializer-backed candidate and a typed expectation manifest for the
> repeatable [object-security lane](plan-033/object-security-results.md). That
> lane's verdict must be earned independently; the historical A/B instructions
> below do not describe the current builder.

| | |
|---|---|
| **Settles** | Whether `secedit` accepts `object_security.py`'s `key = value` shape or only the native bare quoted-CSV line |
| **Direction** | B |
| **Estate** | `LabMS01` |
| **Time** | 10 minutes |
| **Writes** | Nothing. No import, no configure, no GPO. |
| **Precondition** | **Blocked on a bundle**: `candidate.inf` and `candidate-native-shape.inf` |
| **Send back** | The two console outputs, pasted, with full error text. No files. |

### The bundle we must hand you first

**A single file**, `candidate.inf`: UTF-16LE with a BOM, CRLF line endings, an
`[Unicode]`/`[Version]` preamble, and three sections rendered from
`RegistrySecurityFamily.to_template_entries()`,
`FileSystemSecurityFamily.to_template_entries()` and
`SystemServicesFamily.to_template_entries()`. It comes with the alternative
shape, `candidate-native-shape.inf`.

How it would be produced (as planned on 2026-08-06, when it did not yet exist):
a new `scripts/plan-033/build-object-security-candidate.py`, modelled on the
existing `scripts/plan-033/build-wp3-candidate.py`. It constructs the three
families from `gpo_studio.object_security`, merges their
`to_template_entries()` dicts into `InfSection`s, renders with
`gpo_studio.security_template.format_security_template`, and writes the bytes
through `gpo_studio.security_template.encode_security_template`. That function
was added by the WP-3 correction and supplies the UTF-16LE BOM and the CRLF
endings. Roughly thirty lines.

### Steps (once you have `candidate.inf`)

```powershell
$root = 'C:\gpo-studio\manual\r09-secedit-validate'
New-Item -ItemType Directory -Force -Path $root | Out-Null
# Place candidate.inf in $root first.

secedit /validate "$root\candidate.inf"
"exit code: $LASTEXITCODE"
```

Then, whatever the result, validate the alternative shape we send alongside it:

```powershell
secedit /validate "$root\candidate-native-shape.inf"
"exit code: $LASTEXITCODE"
```

Two files, two verdicts. One passing and one failing is far more informative
than either alone.

### What to return

The two console outputs, pasted. Include the full error text if there is one;
`secedit`'s error messages are specific, and they are the interesting part.

No files come back from this request.

### Sanitisation

None. Both inputs are synthetic files we wrote, and the outputs are `secedit`'s
own error strings. If an error message quotes a host path, it is
`C:\gpo-studio\manual\...`, which is not an identifier.

### Cleanup

`Remove-Item -Recurse -Force 'C:\gpo-studio\manual\r09-secedit-validate'`.
Nothing was imported or configured, and no GPO was created.

### Background

`object_security.py`'s `to_template_entries()` emits, via
`_format_object_value`, entries of the form
`MACHINE\SOFTWARE\Path = 2,"D:PAR(A;OICI;FA;;;BA)"`. That is a `key = value`
pair, because the only serializer that could consume its output is
`format_security_template`, which writes `f"{key} = {value}"`.

The corpus's own spec-informed excerpts show the native form as a **bare quoted
CSV line with no `=`**:
`"MACHINE\SOFTWARE\StudioLab\Audit",0,"D:PAR(A;OICI;FA;;;BA)"`.

Which shape `secedit` accepts had never been measured. `secedit /validate` is a
real oracle here, not a rubber stamp: on 2026-08-04 it was measured rejecting a
malformed SDDL with a specific error.

- **If it rejects Studio's shape**, the module cannot write these three sections
  at all: a concrete correction rather than a suspicion.
- **If it accepts**, the shape is fine and WI-038's scope shrinks to the reader
  only.
- **If it accepts silently and R4's native `GptTmpl.inf` shows the other
  shape**, `secedit /validate` is not strict about this, which matters before
  anyone builds a comparator on it.

---

## R10 — Does Windows accept a Studio-written `scripts.ini`?

| | |
|---|---|
| **Settles** | Whether Windows accepts and renders a Studio-written `scripts.ini`, or silently ignores it |
| **Direction** | B |
| **Estate** | `LabMS01` |
| **Time** | 25 minutes |
| **Writes** | Disposable GPO `zz-studio-evidence-10-scripts-rt` |
| **Precondition** | **Blocked, and harder than the others.** Needs `studio-scripts-backup.zip`. **Do not schedule R10 until we tell you the bundle exists.** |
| **Send back** | `gpreport-after-import.xml`, the whole `rebackup\` tree, and what the GPMC editor showed |

### Why this is blocked harder than R9

As written (2026-08-06), **the current code could not produce the bundle.**
`export.py`'s `gpmc_backup_bundle` is the native-backup writer, and
`_native_export_files` (`export.py:420–465`) emits **only**
`Machine/registry.pol`, `User/registry.pol`, and GPP XML for four allowlisted
families (`Drives`, `Groups`, `ScheduledTasks`, `Services`). It has no path for
a Scripts file, and `_extension_guids` (`export.py:468`) has no Scripts CSE
profile to register in `Backup.xml`.

So this request first needs a change to `export.py`: a Scripts branch in
`_native_export_files` and a Scripts entry in the extension-profile table. That
is product work, not a script. **R2 must come first**, because the correct
encoding and preamble for `scripts.ini` are what that change has to be built
from. Sequence: R2 → fix `script_policy.py` and extend `export.py` → R10.

(The bundle is now built by `scripts/plan-033/build-scripts-backup-candidate.py`.)

### Steps (once you have `studio-scripts-backup.zip`)

```powershell
$root = 'C:\gpo-studio\manual\r10-scripts-import'
New-Item -ItemType Directory -Force -Path $root | Out-Null
Expand-Archive -Path "$root\studio-scripts-backup.zip" -DestinationPath "$root\in"

# The backup id is the {GUID} directory name inside the archive.
$bid = (Get-ChildItem "$root\in" -Directory | Where-Object Name -like '{*}').Name

Import-GPO -BackupId $bid -Path "$root\in" `
  -TargetName 'zz-studio-evidence-10-scripts-rt' -CreateIfNeeded

Get-GPOReport -Name 'zz-studio-evidence-10-scripts-rt' -ReportType Xml `
  -Path "$root\gpreport-after-import.xml"

New-Item -ItemType Directory -Force -Path "$root\rebackup" | Out-Null
Backup-GPO -Name 'zz-studio-evidence-10-scripts-rt' -Path "$root\rebackup" `
  -Comment 'R10 re-export after Studio import'
```

Then open GPMC, find `zz-studio-evidence-10-scripts-rt`, and look at
**Computer Configuration → Policies → Windows Settings → Scripts
(Startup/Shutdown) → Startup**. Report whether the two `.cmd` entries and the
one `.ps1` entry are there, and whether the PowerShell ordering dropdown shows
the non-default value.

This last step is a human observation and cannot be automated. It is the only
real oracle for "GPMC can edit this", which is the sub-claim
`gpmc_interop.py`'s `is_gpmc_editable` makes.

### What to return

- `gpreport-after-import.xml`
- The whole `rebackup\` tree, in particular its
  `DomainSysvol\GPO\Machine\Scripts\scripts.ini`, so we can diff what Windows
  re-emitted against what Studio wrote.
- Your description of what the GPMC editor showed.

### Sanitisation

Standard lab treatment. `Import-GPO` will have rewritten the domain and DC
references to the lab's, so `replace-domain-sid` / `replace-sd-hex` /
`replace-gpreport-sid` apply as usual. No production identifiers are involved.

### Cleanup

```powershell
Remove-GPO -Name 'zz-studio-evidence-10-scripts-rt'
Get-GPO -All | Where-Object { $_.DisplayName -like 'zz-studio-evidence-*' } |
  Select-Object DisplayName, Id
Remove-Item -Recurse -Force 'C:\gpo-studio\manual\r10-scripts-import'
```

The re-query must return nothing.

### Background

R2 tells us what Windows *writes*. R10 tells us whether Windows *accepts* what
Studio writes: import a Studio-produced GPMC backup carrying a
`Machine\Scripts\scripts.ini` and the Scripts CSE GUID, then ask GPMC to render
it and back it up again.

If `Get-GPOReport` shows the script with the authored command, parameters and
order, Windows understood the file. If it shows an empty Scripts node, the file
was ignored. That is the inert failure mode, and R2 cannot detect it on its own.

---

## R11 — Does a real production domain accept Studio's output?

> **This is the only request in this document that writes to the live domain.
> It requires a fresh go-ahead before it is run.** If you would rather not
> create anything in the live domain, skip it. Requests 6, 7 and 8 are
> read-only and carry most of Sitting B's value, and nothing downstream depends
> on R11.

| | |
|---|---|
| **Settles** | Whether a production directory imports Studio's GPMC backup as the lab does |
| **Direction** | B |
| **Estate** | `ad.hraedon.com` |
| **Time** | 20 minutes |
| **Writes** | **One unlinked GPO**, `zz-studio-evidence-11-roundtrip`, removed in the same sitting |
| **Preconditions** | A fresh go-ahead; `wp2-candidate-backup.zip` and its SHA-256 from us; rights to create a GPO in `ad.hraedon.com` (if you lack them, skip R11 rather than escalate) |
| **Send back** | The `Format-List` and `LinksTo` outputs (pasted), the whole `rebackup\` tree, and the `Get-FileHash` result |

### What it writes, precisely

**It creates exactly one object:** a new `groupPolicyContainer` named
`zz-studio-evidence-11-roundtrip`, with its SYSVOL folder, created by
`Import-GPO -CreateIfNeeded` (the supported Microsoft path).

- It is **never linked**: not to the domain root, an OU or a site. An unlinked
  GPO applies to nothing.
- It **modifies no existing object.** `Import-GPO -CreateIfNeeded` with a target
  name that does not exist creates and touches nothing else.
- It contains **two synthetic registry values** under
  `Software\Policies\GPOStudio\WP2`, which no product reads.
- It is removed by `Remove-GPO` in the same sitting, with a strict re-query.

### The bundle we must hand you first

`wp2-candidate-backup.zip`. **Unlike R9 and R10, this could be produced with the
code of 2026-08-06**, because it is the artifact the certified WP-2 lane already
builds:

```
python scripts/plan-033/build-wp2-candidate.py <output-dir>
```

It calls `gpo_studio.export.gpmc_backup_bundle` and
`gpo_studio.export.native_backup_id` on a fixed synthetic GPO
(GUID `11111111-2222-3333-4444-555555555555`, two registry values, one machine,
one user). We zip its output and send it with the recorded SHA-256, so the
artifact you import can be bound to a commit.

### Steps (once you have the bundle, and a fresh go-ahead)

```powershell
$root = 'C:\gpo-studio\manual\r11-live-roundtrip'
New-Item -ItemType Directory -Force -Path $root | Out-Null
# Place wp2-candidate-backup.zip in $root, then:
Expand-Archive -Path "$root\wp2-candidate-backup.zip" -DestinationPath "$root\in"

# Verify you received what we sent. Compare against the hash in our message.
Get-FileHash "$root\wp2-candidate-backup.zip" -Algorithm SHA256 | Select-Object Hash

$bid = (Get-ChildItem "$root\in" -Directory | Where-Object Name -like '{*}').Name
"backup id: $bid"

# THE ONE WRITE. Creates an unlinked GPO. Nothing else is modified.
Import-GPO -BackupId $bid -Path "$root\in" `
  -TargetName 'zz-studio-evidence-11-roundtrip' -CreateIfNeeded

# Prove it is unlinked. This must show no links.
$g = Get-GPO -Name 'zz-studio-evidence-11-roundtrip'
([xml](Get-GPOReport -Guid $g.Id -ReportType Xml)).GPO.LinksTo

# Read the extension lists Windows assigned.
$dn = (Get-ADDomain).DistinguishedName
Get-ADObject -Identity "CN={$($g.Id)},CN=Policies,CN=System,$dn" `
  -Properties gPCMachineExtensionNames, gPCUserExtensionNames, versionNumber,
              gPCFunctionalityVersion, flags |
  Select-Object gPCMachineExtensionNames, gPCUserExtensionNames, versionNumber,
                gPCFunctionalityVersion, flags | Format-List

# Re-export.
New-Item -ItemType Directory -Force -Path "$root\rebackup" | Out-Null
Backup-GPO -Name 'zz-studio-evidence-11-roundtrip' -Path "$root\rebackup" `
  -Comment 'R11 live re-export'
```

### What to return

- The `Format-List` output and the `LinksTo` output, pasted into your reply.
  **An empty `LinksTo` output is the safety confirmation**, so send it even
  though it is empty.
- The whole `rebackup\` tree.
- The `Get-FileHash` result, so we can bind the artifact you used.

**Do not** run `Get-GPOReport` over any other GPO while you are here.

### Sanitisation

This is the one artifact in this document that comes out of a **production
directory**. It needs the most care, and the sanitiser was not written for it:

- **`rebackup\{GUID}\Backup.xml`** carries `GPODomain` (`ad.hraedon.com`,
  allowed), `GPODomainGuid` (**the production domain GUID; must be replaced**),
  `GPODomainController` (a real DC FQDN; allowed under the homelab permit, but
  we will replace it anyway), and a `SecurityDescriptor` hex blob containing the
  **production domain SID** plus whatever the domain's default GPO DACL grants.
  `replace-domain-sid` and `replace-sd-hex` handle the last two **only if
  `GPO_STUDIO_REAL_SID_PREFIX` is set to the production prefix at sanitisation
  time.** The domain GUID has no rule and needs one.
- **`bkupInfo.xml`** carries the same domain fields plus a real `BackupTime`.
- The `Registry.pol` files are Studio's own synthetic bytes and are clean.

Because of the domain GUID and the default-DACL descriptor, assume **nothing
from this request is committable until it has had a manual pass on top of the
sanitiser.** The pasted `Format-List` output is fine to work from directly:
extension lists and integers carry no identifiers.

### Cleanup — **do not leave the console without running this**

```powershell
Remove-GPO -Name 'zz-studio-evidence-11-roundtrip'

# Strict absence re-query against the live domain. Must return nothing.
Get-GPO -All | Where-Object { $_.DisplayName -like 'zz-studio-evidence-*' } |
  Select-Object DisplayName, Id

Remove-Item -Recurse -Force 'C:\gpo-studio\manual\r11-live-roundtrip'
```

Paste the re-query output. If it returns a row, tell us immediately. An orphaned
unlinked GPO in a production domain is harmless but untidy, and we would rather
chase it now than find it in six months.

### Background

WP-2 is certified 18/18: a Studio-generated GPMC backup imports into the lab,
reports correctly and re-exports. That certification is bound to a single-DC,
two-week-old, purpose-built forest with no organic content.

A real domain differs in ways that could matter and that the lab cannot
simulate: multiple domain controllers, DFS-R SYSVOL replication, an existing
Policies container with real ACL inheritance, a real default GPO DACL, and
whatever schema extensions and delegations have accumulated. The lab will not
do, because the lab is what was already tested; only a production directory can
show whether the difference matters.

- **If it imports cleanly and the resulting GPC carries the expected
  `gPCMachineExtensionNames`**, this is the first observation this project holds
  of a real domain accepting its output. That is a stronger claim than WP-2, and
  it would let `export.py`'s native-backup path stop being "certified against a
  synthetic estate".
- **If it fails**, we learn what the lab does not model, which is worth more
  than another green lab run.

---

## R12 — The Folder Redirection flags, and a second group rule

**Open.** Opened 2026-09-11 as [WI-066](work-items.md).

| | |
|---|---|
| **Settles** | How the four Folder Redirection option flags are encoded (one bit identified, the rest constrained), and how multiple group rules are represented |
| **Direction** | A |
| **Estate** | `LabMS01` |
| **Time** | 15 minutes |
| **Writes** | Disposable GPO: R3's `zz-studio-evidence-03-folderredir` if it still exists, otherwise `zz-studio-evidence-12-folderredir` (unlinked) |
| **Send back** | As R3: the backup, `gpreport.xml`, and the recursive `DomainSysvol` listing |

### Steps

Reuse R3's GPO if it still exists. Otherwise create
`zz-studio-evidence-12-folderredir`, **unlinked**, and first repeat R3 step 3 so
the folders sit in one file.

1. **User Configuration → Policies → Windows Settings → Folder Redirection.**
2. Right-click **Pictures** → **Properties**.
   - **Setting:** `Advanced - Specify locations for various user groups`
   - **Add** two groups that exist in the lab. `LAB\Domain Users` and
     `LAB\Domain Admins` are fine and not sensitive:
     - `LAB\Domain Users` → root path `\\zz-studio-fileserver\zzpics-a`
     - `LAB\Domain Admins` → root path `\\zz-studio-fileserver\zzpics-b`
   - **Settings tab: leave every option at its default.** This is the control.
     Documents carries three non-default options and Pictures carries none, so
     the two `Flags` values differ by exactly those three.
3. Right-click **Videos** → **Properties**.
   - **Setting:** `Basic`, root path `\\zz-studio-fileserver\zzvid`
   - **Settings tab:** change **exactly one** option from its default:
     **uncheck** "Grant the user exclusive rights to Videos". Leave the other
     three alone.
   - Pictures gives the default word and Videos gives default-minus-one-option,
     so their difference is that option's bit. Two folders would only bound the
     answer; three identify one bit outright.
4. Close the editor, reopen all three property pages to confirm GPMC persisted
   them, then capture exactly as R3 step 6 does: `Backup-GPO`, `Get-GPOReport`
   and the recursive `DomainSysvol` listing.

Cleanup is not spelled out for R12. Follow the general rule (remove the GPO in
the same sitting and run the strict re-query from
[Naming convention](#naming-convention)).

### What comes back

One `fdeploy1.ini` with three folder GUIDs, four `[{GUID}_{sid}]` sections
(Pictures contributes two, one per group), and three distinct `Flags` values.
That settles question 4 outright and gives question 3 one identified bit plus a
three-point constraint on the rest.

**It does not settle the whole flag word.** Nine bits are set and four options
are modelled. The rest are presumably fixed or mean something the module does
not model, and identifying them would need a fourth and fifth folder. The
expected output is "one option's bit identified, three more constrained, the
rest unexplained". That is enough to write the four options Studio models, and
to know that Studio copies the rest rather than composing them.

### Background

This is **step 4 of R3, re-requested**, plus the flags control R3's design
implied and its delivery lost. R3 captured Documents in Basic mode: one folder,
one principal (`s-1-1-0`), one `Flags=1021`. The Advanced folder was never
authored.

Two questions are still open, and both block a writer:

1. **How the four option flags are encoded.** `Flags=1021` is `0b1111111101`:
   nine bits set against four modelled booleans. One sample of a bitfield
   attributes no bit to any option. R3 asked for a second folder *at defaults*
   so the non-default one could be read against it, and that control is what
   went missing.
2. **How multiple group rules are represented.** Only one principal was
   captured, so nothing shows whether a second group adds a `[{GUID}_{sid}]`
   section, extends the `[Folder_Redirection]` value, or does something else.

A writer built without these would infer a bit layout from one point.
`object_security.py`'s propagation codes were wrong on all three values until R4
measured them; this is the same kind of guess.

---

## Where this document is uncertain

These are also stated inline. Each costs one question to resolve.

1. **File transfer from the Windows hosts to `mvmcc03`** is not specified,
   because we do not know what you use. Any method is fine.
2. **Whether the `LabMS01` console can reach a file share** for returning
   artifacts, or whether they have to come out through the hypervisor. The
   estate has no egress by design.
3. **`mtedit.exe` availability** on Server 2025 (R1). The COM fallback exists,
   but its `EntryType*` constant names come from the GPMC COM reference and are
   not verified on this build.
4. **Whether GPMC will save a Folder Redirection target pointing at an
   unreachable UNC path** in an isolated lab (R3). We think it warns and saves;
   if it refuses, substitute a reachable share.
5. **The exact wording of the three registry-security radio buttons** on Server
   2025 (R4). The label-to-integer mapping is the answer, so record the label
   you clicked, not the one we guessed.
6. **Which host you administer `ad.hraedon.com` from**, and whether R7 can run
   on a DC (it must).
7. **Whether the live domain's SYSVOL is DFS-R or FRS.** It changes nothing in
   these instructions, but put it in one line of your reply: it changes how we
   read R11's result if the import behaves oddly.
8. **Whether you hold rights to create a GPO in `ad.hraedon.com`** (R11 only;
   requests 6, 7 and 8 need only read access). If not, skip R11 rather than
   escalate.

## After the artifacts land

The agent's side. **Executed 2026-09-06.** Each step's status is stated
explicitly, because five of the eleven requests sat for a month with their
captures unbanked while this document's header said they were complete.

1. ~~Retrieve from `/home/itadmin/gpo-studio-evidence/inbox/<request-id>/`.~~
   **Done.** R6 and R8 came from `mvmcc03`. R1–R5, R9 and R10 came from the
   windows-console-driver records, where the transactional route had already
   banked them.
2. ~~Inspect raw, **outside the repository**, and record raw SHA-256 per file.~~
   **Done.** Hashes are in each fixture's `provenance.json`.
3. **Sanitisation: not needed as feared.** R6 and R8 were sanitised *by
   construction* at capture time, as designed. The census CSV holds
   extension-list GUIDs; the attribute CSVs hold names and `True`/`False` but
   never values; the SYSVOL CSVs hold relative paths and byte counts. Nothing
   that reached `mvmcc03` needed redacting. **The hazard was real, and the
   mitigation was query shape, not the sanitiser.**
4. ~~Curate into a fixture under `tests/fixtures/`, hash-bound.~~ **Done** for
   R1, R2, R3, R4, R6 and R8. R5, R7, R9, R10 and R11 have no committable
   artifact: their results are facts, recorded in the claim registry.
5. ~~Update the manual queue.~~ **Done.** The access path is retired, and the
   queue now points at the binding table instead of restating it.
6. ~~Record each answered question against the module it bears on.~~ **Done**:
   [`plans-025-032-oracle-survey.md`](plans-025-032-oracle-survey.md) §5.0.

A capture is certified **against the capture**, not against the authoring
gesture. None of these became a re-runnable lane. That is the cost of the manual
queue, and it is why the capability matrix records these results as
`capture-backed` rather than as verification. See
[`capability-matrix.md`](capability-matrix.md#the-capture-backed-value-and-what-it-denies).

## What the closing loop got wrong, kept as the lesson

Steps 4–6 above were written in August and executed on 2026-09-06, a month after
this document's header began saying "fully executed". For that month, the
sentence *"See `plans-025-032-oracle-survey.md` §5 for the module-by-module
record"* pointed at a table of predictions with no results, and the survey's
opening line still read "Nothing was executed against Windows."

The work-order route was not at fault: windows-console-driver banked every lab
result in claim-registry rows and transaction records on the day it ran. The
failure was that **no document in this repository cited them**, so from inside
gpo-studio the evidence looked missing. R6 and R8 were the real gap: captured,
transferred, then referenced by nothing in either repository until someone
listed the inbox.

This is the sixth instance of a defect AGENTS.md records five earlier times: the
registry that gates work disagreed with the registry that records reality. It is
now checked mechanically. `tests/test_evidence_registry_consistency.py` fails the
build for an uncited `capture-backed` cell, a dangling fixture citation, or a
module promoted to Windows-verified `yes`.
