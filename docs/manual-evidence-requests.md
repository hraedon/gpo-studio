# Manual evidence requests — work order for the operator

Status: active, written 2026-08-06 against `main` at `80c23b5`. Nothing in this
document has been executed; no estate host was contacted while writing it.

## What this is

Eleven numbered requests for a human operator. Each one is a self-contained
errand that produces an artifact settling a question this project cannot answer
from its own source. They exist because two capabilities were unlocked on
2026-08-06: **a working GUI console into `LabMS01`**, and **a live Active
Directory domain, `ad.hraedon.com`, usable as required.**

The input is
[`docs/plans-025-032-oracle-survey.md`](plans-025-032-oracle-survey.md), which
names a *cheap discriminator* per module. **That file is not on `main` yet** — it
lives on branch `docs/oracle-survey-025-032`, so the link above dangles until
that branch lands. Read it with
`git show origin/docs/oracle-survey-025-032:docs/plans-025-032-oracle-survey.md`. This document turns those
discriminators into instructions. Where the survey says "one backup and one
grep," this says which menu, which cmdlet, which file, and what to send back.

**Why it matters.** Nothing in this product has ever been validated against a
real domain. Twelve RSOP verdicts, WP-1B, WP-2 and WP-3 are all against a
synthetic three-guest estate with no egress. Requests 6, 7, 8 and 11 are the
first observations this project would hold about a production directory.

### Relationship to the existing queue

This **extends**
[`plan-033/manual-gui-evidence-queue.md`](plan-033/manual-gui-evidence-queue.md);
it does not replace it. That document stays the checkbox tracker and the
statement of the staging/cleanup discipline, and the WI-022 entry remains the
worked precedent. Two things it does not cover, which this document adds:

1. **A live production domain.** The queue's first safety rule is "use only the
   isolated lab." That rule was sufficient when the lab was the only estate. It
   is not now. The live-domain regime below is new and takes precedence for
   requests 6, 7, 8 and 11.
2. **Direction B.** The queue only ever captures what Windows writes. Requests
   9, 10 and 11 hand Windows something *Studio* wrote and ask whether it was
   accepted.

The queue's access path — "RDP to `mvmcitest01`" — is **retired** (§7 of the
survey, and `plan-033/environment-spec.md`). When the first request here is
executed, update that document's access path and add the live-domain rules.

---

## SAFETY — read before touching `ad.hraedon.com`

`ad.hraedon.com` is a **live production domain**. Everything below is written to
be non-destructive and reversible, and every live-domain request states what it
writes. The rules are absolute:

1. **Never modify, relink, rename or delete an existing GPO.** Not even to "put
   it back afterwards." If a request seems to need this, it is a defect in the
   request — stop and say so.
2. **Create GPOs unlinked.** No request here needs a link. If a future one does,
   it links only to a dedicated, empty, purpose-made OU containing no real
   objects.
3. **Never link anything to the domain root, to `Domain Controllers`, to a
   site, or to any OU containing real objects.**
4. **Never run `gpupdate`, `secedit /configure`, `Restore-GPO`, or
   `Set-GPPermission` against the live domain.** Anything that applies or
   configures policy belongs on `LabMS01`/`LabCL01`, which are disposable and
   checkpoint-backed.
5. **Prefer read-only exports.** `Get-GPOReport`, `Backup-GPO`, `Get-ADObject`
   and `secedit /export` read; they do not write to the directory or SYSVOL.
6. **Exactly one request writes to the live domain: request 11**, which creates
   one unlinked GPO and removes it in the same sitting. Requests 6, 7 and 8 are
   read-only end to end. R11 is marked as such throughout and needs a fresh
   go-ahead before it runs; skipping it costs nothing downstream.
7. **Every request ends with cleanup steps.** Run them in the same sitting.
   Confirm removal with a strict re-query, not by looking at the console.

### Naming convention

Every object this document asks you to create, on either estate:

```
zz-studio-evidence-NN-<slug>
```

`NN` is the request number, zero-padded. The `zz-` prefix sorts the objects to
the bottom of every GPMC list, so they are visually obvious and trivially
enumerable. To find everything this document ever created:

```powershell
Get-GPO -All | Where-Object { $_.DisplayName -like 'zz-studio-evidence-*' } |
  Select-Object DisplayName, Id, CreationTime
```

That command is also the cleanup verification. It should return **zero rows**
when a sitting is finished.

### Where artifacts go

**On the Windows side**, stage under `C:\gpo-studio\manual\<request-id>\`, e.g.
`C:\gpo-studio\manual\r02-scripts-ini\`. Same convention as the existing queue.

**Coming back**, place the raw output — zipped is fine, one zip per request or
one per sitting — under:

```
/home/itadmin/gpo-studio-evidence/inbox/<request-id>/
```

on `mvmcc03`. Any transfer method you already use is fine; the requirement is
only about the destination.

Three constraints on that destination, and they are the point of it:

- **Outside the repository working tree.** Raw output has not passed identifier
  review. It must never sit inside a git worktree where a broad `git add` can
  reach it.
- **Not under `/tmp`.** `/tmp` on `mvmcc03` is tmpfs — RAM-backed — and a
  multi-megabyte backup tree there costs memory.
- **Not `<repo>/samples/`.** That directory is mechanically guarded
  (`scripts/check_committed_identifiers.py` fails any tracked file whose first
  path component is `samples`), which makes it a good last line of defence — but
  it is *not* in this repo's `.gitignore`, so it is not a good first one.

Tell the agent the request IDs and the exact GPO names used. The agent
retrieves, inspects, sanitises, curates a fixture, and drives cleanup.

### Sanitisation — the part that is easy to get wrong

Read [`corpus-topology-redaction.md`](corpus-topology-redaction.md) for the
principle: a committed fixture models the *shape* of production, never the
*structure* of one estate.

**The specific hazard here is that the identifier gate will not save you.**
AGENTS.md allows homelab identifiers — `hraedon` and `mvm*` are on the permit
list, because `ad.hraedon.com` used to be this project's validation forest (see
the superseded section of `plan-033/environment-spec.md`). So
`scripts/check_committed_identifiers.py` will pass a file containing
`ad.hraedon.com`, `HRAENET`, and a real DC hostname. What it will **not** catch,
and what the live domain now contains that the old validation forest did not, is
a **populated production directory**: real user account names, real group names,
real OU structure, real service accounts, real UNC paths, real computer names,
and the real domain SID.

Consequently:

- **Prefer sanitisation by construction.** Several requests below shape the
  query so identifiers never leave the domain in the first place — selecting
  only the attributes wanted rather than filtering afterwards. That is always
  better than redacting later, and it is why requests 6 and 8 are written the
  way they are.
- **The existing sanitiser is
  `scripts/plan-033/sanitize-gpp-fixtures.py`**, with rules
  `replace-domain-sid`, `replace-sd-hex`, `replace-gpreport-sid`. It takes the
  real domain SID prefix from `GPO_STUDIO_REAL_SID_PREFIX` at run time and never
  commits it. It produces a `sanitization-record.json` with raw and sanitised
  hashes per file, exactly as
  `tests/fixtures/native-gpp-gpmc/sanitization-record.json` records.
- **That sanitiser was written for the lab.** It handles domain SIDs and
  security-descriptor blobs. It does **not** know about real user names, group
  names or OU names, and it only applies the generic SID rule to files named
  `gpreport*.xml`. Live-domain artifacts need a manual pass on top of it.

Per-request notes below say what each artifact will contain and, where relevant,
what must **not** be committed at all.

---

## Ordering, and why

Primary ordering is **value per minute of your time** — cheapest decisive test
first. Secondary ordering batches requests that share a host and a session,
because switching estates costs more than any individual request.

The result is three sittings. If you only get through the first, you will have
settled the five questions most likely to change the plan.

| Sitting | Requests | Where | Est. | Character |
|---|---|---|---|---|
| **A** | 1–5 | `LabMS01` (GPMC console) | ~90 min | Direction A capture. Nothing touches the live domain. |
| **B** | 6–8 | `ad.hraedon.com` | ~40 min | **Read-only.** Nothing is created, modified or deleted. |
| **C** | 9–11 | `LabMS01`, then live | ~55 min | Direction B. **Blocked** until Studio-side bundles exist (see each). |

Sittings A and B are independent and can be taken in either order. Sitting C
cannot start until the bundles named in requests 9–11 are generated and handed
over.

**Total: approximately 185 minutes.** Requests 1–5 are ~90 of those.

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

Requests 9–11 are blocked on Studio-side bundles (§ each). Of those, only R11's
bundle can be produced with today's code.

Each request is labelled:

- **Direction A — reference capture.** GPMC authors it, you export it, we
  compare our writer against what Windows produced. Settles wire format.
- **Direction B — round-trip conformance.** *We* produce an artifact, you feed
  it to native tooling, you return what came back. Stronger, because it proves
  Windows **accepted** our output rather than that our output resembles a
  sample.

---

# Sitting A — `LabMS01`, GPMC console (~90 min)

All five requests use the disposable evidence estate. Nothing here touches
`ad.hraedon.com`. Use the `zz-studio-evidence-NN-<slug>` convention throughout,
leave every GPO **unlinked**, and run request 5's cleanup block at the end to
remove all of them at once.

Before you start:

```powershell
New-Item -ItemType Directory -Force -Path 'C:\gpo-studio\manual' | Out-Null
```

---

## R1 — One GPMC-authored migration table

**Direction A.** Estate: **`LabMS01`.** Estimated: **8 minutes.** Needs no GUI
beyond launching one tool.

### The question

`migration.py` parses `.migtable` XML in namespace
`http://www.microsoft.com/GroupPolicy/Types`, looking for `Mapping` elements
containing `Source`/`Destination` → `Identifier` → `Sid`|`Name`. **Every
`.migtable` in this repository was hand-written by this project** — the only
files matching are inline strings in `tests/test_migration.py` and
`tests/test_lifecycle.py`. No GPMC-authored migration table exists anywhere in
the tree. Nobody has ever checked whether that namespace or that element shape
is what GPMC writes.

- **If GPMC's output parses with entries** — the reader is right, and this is
  the one outcome in Sitting A that is merely confirmatory. It still costs eight
  minutes and it removes a live risk, so it is worth taking.
- **If the namespace or element shape differs** —
  `parse_migration_table` iterates `root.iter(f"{{{_GPMC_NS}}}Mapping")`, finds
  nothing, returns an **empty table without raising**, and `apply_migration`
  then returns the GPO unchanged. That is a **silent no-op on a live API
  endpoint**: `migration_table_path` on the backup-import endpoint at
  `api.py:3129`. An operator would upload a migration table, get a 200, and get
  no migration.

This is first because it is the cheapest request here, it is the most likely to
fire, and it is the only one that concerns *surfaced 1.0 code* rather than an
unproven draft.

### Why `LabMS01` and not the live domain

The question is about **file format**, and the format is a property of GPMC, not
of the directory. A table authored against the live domain would carry real
principal names and a real domain SID for no additional information. Use the
lab.

### Steps

1. On `LabMS01`, open a Windows PowerShell 5.1 console **as administrator**.
2. Run `mtedit.exe`. This is the Migration Table Editor, installed with GPMC.
   *(Flagged uncertainty: if `mtedit.exe` is not on `PATH`, it is under
   `C:\Windows\System32\`. If it is absent entirely, use the COM fallback
   below and say so.)*
3. Add **four rows**, one per source type, so the file exercises more than one
   shape. Use these exact values — they are synthetic and safe to commit:

   | Source Name | Source Type | Destination Name |
   |---|---|---|
   | `LAB\zz-studio-src-group` | Global Group | `LAB\zz-studio-dst-group` |
   | `LAB\zz-studio-src-user` | User | `LAB\zz-studio-dst-user` |
   | `\\zz-studio-src\share` | UNC Path | `\\zz-studio-dst\share` |
   | `LAB\zz-studio-src-local` | Domain Local Group | *(leave as "Same as source")* |

   The editor may complain that these principals do not resolve. **That is
   fine** — it is a text file, and unresolvable entries still serialise. If it
   refuses to save, substitute any real lab principal and tell us which.
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

*(Flagged uncertainty: the exact `EntryType*` constant names are from the GPMC
COM reference and have not been verified on Server 2025. If one errors, run
`$c | Get-Member -MemberType Property | Where-Object Name -like 'EntryType*'`
and use the nearest name — then tell us which names actually exist.)*

### What to return

One file: `C:\gpo-studio\manual\r01-migtable\studio.migtable`.

Also paste the first three lines of it into your reply if that is easy — the XML
declaration and root element answer the question on their own.

### Sanitisation

**None needed.** Every value above is synthetic. If you had to substitute a real
lab principal, say which and we will replace it before committing.

### Cleanup

Delete `C:\gpo-studio\manual\r01-migtable` after transfer. No GPO was created and
nothing in the directory was touched.

---

## R2 — A native `scripts.ini` and `psscripts.ini`

**Direction A.** Estate: **`LabMS01`.** Estimated: **20 minutes.**

### The question

`serialize_script_policy_ini()` (`script_policy.py:412`) returns a **`str`**,
built by `"\n".join(lines)`. There is no encoding, no BOM, no CRLF, no file path
and no CSE registration anywhere in the module — and nothing in this repository
is the caller that would supply them.

**This is the exact shape of the WP-3 finding, in a module no oracle has ever
read.** When `secedit` first looked at `security_template.py`'s output, the file
was not a valid security template at all: wrong encoding, missing preamble,
wrong line endings. The correction was +36 −1 lines and total in meaning. Three
observations settle whether the same thing is true here.

1. **Does `scripts.ini` begin `FF FE`?** If yes, it is UTF-16LE with a BOM and
   the module emits the wrong encoding for every file it will ever write.
2. **Are the line endings CRLF or LF?** The module joins with `"\n"`.
3. **Does `psscripts.ini` contain a `[Policy]` section?** The module appends
   one, carrying `RunLogonScriptsSync`, `RunLogoffScriptsSync`,
   `LegacyScriptsFirst` and `PowerShellOrder`. `RunLogonScriptsSync` is an
   Administrative Templates setting that lives in `Registry.pol`, not in an INI.
   The suspicion is that native `psscripts.ini` carries a **`[ScriptsConfig]`**
   section with `StartExecutePSFirst` / `EndExecutePSFirst` instead — in which
   case `[Policy]` is invented and four modelled settings have no
   representation on the wire.

Any of the three coming back "as the module assumed" is a real answer. All three
coming back that way would be the surprise.

### Why `LabMS01`

Scripts have **no cmdlet authoring surface** — the GPMC editor snap-in is the
only native producer, which is precisely why this needs a console and could not
be done before today. The live domain adds nothing: `scripts.ini` is a SYSVOL
file whose format does not depend on the directory.

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
   Click **Add** again and add a second one so ordering is visible:
   - Script Name: `zz-studio-second.cmd`
   - Script Parameters: *(leave empty — an empty-parameter entry is its own
     question)*
5. Still in the Startup dialog, switch to the **PowerShell Scripts** tab →
   **Add**:
   - Script Name: `zz-studio-marker.ps1`
   - Script Parameters: `-Mode Alpha`
6. On that same **PowerShell Scripts** tab, set the dropdown **"For this GPO,
   run scripts in the following order"** to **"Run Windows PowerShell scripts
   first"**. This is the setting we believe maps to `StartExecutePSFirst`; a
   non-default value makes its encoding visible.
7. Click **OK**. Reopen the Startup dialog and confirm both tabs kept what you
   entered, then close the editor.
8. **Leave `Shutdown` empty.** Whether GPMC writes an empty `[Shutdown]`
   section at all — and whether it writes a comment line there, as the module
   does — is one of the questions.
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

Paste that console output into your reply — it answers questions 1 and 2 before
we retrieve anything.

### What to return

The **whole backup directory** `C:\gpo-studio\manual\r02-scripts-ini\`. All of
it. Specifically we need, and it is worth checking they exist before you zip:

- `backup\{GUID}\DomainSysvol\GPO\Machine\Scripts\scripts.ini`
- `backup\{GUID}\DomainSysvol\GPO\Machine\Scripts\psscripts.ini`
- `backup\{GUID}\Backup.xml` — for the Scripts CSE GUID in the extension lists
- `gpreport.xml`

**Note:** GPMC will *not* have created the `Scripts\Startup\` bodies, because
you named scripts that do not exist. That is intentional and does not affect the
answer — the INI is the artifact.

### Sanitisation

`Backup.xml` and `gpreport.xml` carry the lab domain (`ad.labdomain.dev`, `LAB`),
the lab DC name, and a security-descriptor hex blob containing the lab domain
SID. All of that is handled by `scripts/plan-033/sanitize-gpp-fixtures.py`
(`replace-domain-sid`, `replace-sd-hex`). The two INI files should contain only
the synthetic script names above — check before sending, and if GPMC substituted
a full path containing a real share, say so.

### Cleanup

Deferred to R5's block, which removes all of Sitting A's GPOs at once.

---

## R3 — A GPMC-authored Folder Redirection GPO

**Direction A.** Estate: **`LabMS01`.** Estimated: **15 minutes.**

### The question

`folder_redirection.py`'s `to_registry_settings()` emits tuples targeting
`HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders`.
The Folder Redirection CSE `{25537BA6-77A8-11D2-9B6C-0000F8080861}` is believed
to consume **`fdeploy.ini`**, under the GPO's `User\Documents & Settings\`.
Verified by grep: **`fdeploy.ini` appears nowhere in this repository** outside a
single inventory row in `docs/plan-021/capability-inventory.md` — not in `src/`,
not in `scripts/`, not in any fixture.

The hypothesis is that `User Shell Folders` is what the CSE **writes on the
client**, not what the GPO **carries**. If that holds, `to_registry_settings()`
is not a wrong serializer — it is the **wrong artifact**, and Plan 027 WP-2
changes from "fix the serializer" to "decide whether to write one." That is a
scope answer, and it costs one backup and one grep.

Four things fall out of the same file:

1. Which file the CSE actually reads.
2. How each folder is keyed.
3. How the four option flags — grant exclusive rights, move contents, remove
   redirect on policy removal, also redirect subfolders — are encoded. The
   module models all four and **emits none of them**
   (`folder_redirection.py:317–336`).
4. How multiple group rules are represented. In `advanced` mode
   `effective_path()` returns `rules[0].target_path`, so a three-group policy
   emits one path and silently discards two.

### Why `LabMS01`

Same reason as R2 — a GPMC editor snap-in with no cmdlet surface, and a SYSVOL
file format that does not depend on the directory. Authoring folder redirection
against a live domain would put real group names and a real file-server UNC path
into the artifact for no gain.

### Steps

1. GPMC → **Group Policy Objects** → New →
   `zz-studio-evidence-03-folderredir`. **Unlinked.** Right-click → **Edit**.
2. **User Configuration → Policies → Windows Settings → Folder Redirection.**
3. Right-click **Documents** → **Properties**.
   - **Setting:** `Basic - Redirect everyone's folder to the same location`
   - **Target folder location:** `Create a folder for each user under the root path`
   - **Root Path:** `\\zz-studio-fileserver\zzredir`
   - **Settings tab** — set both of these **away from their defaults**, so their
     encoding is visible:
     - **Uncheck** "Grant the user exclusive rights to Documents"
     - **Check** "Move the contents of Documents to the new location"
     - Under policy removal, select **"Redirect the folder back to the local
       userprofile location when policy is removed"**
   - **OK.** GPMC may warn that the path is not accessible, or ask to create it.
     **Accept the warning / decline creation.** The lab has no such server and
     does not need one — the INI is a text file and records the path you typed.
     *(Flagged uncertainty: if GPMC hard-refuses to save an unreachable UNC
     path on Server 2025, substitute any share that does resolve inside the
     lab and tell us which.)*
4. Right-click **Pictures** → **Properties**.
   - **Setting:** `Advanced - Specify locations for various user groups`
   - **Add** two groups so multi-rule representation is visible. Use two groups
     that exist in the lab — `LAB\Domain Users` and `LAB\Domain Admins` are
     fine, and are not sensitive.
     - `LAB\Domain Users` → root path `\\zz-studio-fileserver\zzpics-a`
     - `LAB\Domain Admins` → root path `\\zz-studio-fileserver\zzpics-b`
   - **Settings tab:** leave defaults here, so we can tell a default encoding
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

Paste that file listing and the `Select-String` result (which we expect to be
empty) into your reply.

### What to return

The whole of `C:\gpo-studio\manual\r03-folderredir\`. The load-bearing file is
whatever appeared under `DomainSysvol\GPO\User\` — we expect
`Documents & Settings\fdeploy.ini`, but the point is that we do not know, so
**return the entire `DomainSysvol` tree** rather than the file we guessed at.
`Backup.xml` matters too, for the Folder Redirection CSE GUID.

### Sanitisation

The two group names are real lab groups (`LAB\Domain Users`,
`LAB\Domain Admins`) and will appear as **real SIDs** in `fdeploy.ini` and in
`gpreport.xml`. Those are lab SIDs, not production ones, and
`replace-domain-sid` / `replace-gpreport-sid` handle them — but the mapping from
SID to group name must be checked before this becomes a fixture. Everything else
(`zz-studio-fileserver`, the share names) is synthetic.

### Cleanup

Deferred to R5's block.

---

## R4 — Security Settings: propagation codes, and the first native `GptTmpl.inf` this project has ever read

**Direction A.** Estate: **`LabMS01`.** Estimated: **25 minutes.** Two questions,
one GPO, one backup.

### Question 4a — the propagation codes

**The repository contradicts itself, and one observation settles it.**

- `object_security.py:119–128` (`_propagation_from_code`) maps
  `0 → none`, `1 → propagate`, `2 → replace`.
- `tests/fixtures/scenarios/security-template/regkeys-filesecurity.json:57`
  states `0 = propagate inheritable permissions to subkeys/subfolders`,
  `1 = replace existing permissions`, `2 = do not allow permissions to be
  replaced`, and says these are spec-informed until the first capture.

Both are unverified. **They cannot both be right.** This is the same class of
question as WI-024, where the answer turned out to be wrong by a factor of 1000.

The GPMC Registry/File-System editor offers exactly three mutually-exclusive
radio options for each secured object. Authoring one key under each option and
reading back the integer Windows wrote maps all three codes in a single capture
— **without needing `secedit /configure`**, which would be destructive and is
therefore explicitly not asked for here.

### Question 4b — the read direction

`parse_security_template` has **never been given a native template.** Every WP-3
run authored with Studio and read back with `secedit`. Separately, WI-038
establishes that `[Registry Keys]`, `[File Security]` and
`[Service General Setting]` are not `key = value` on the wire — they are bare
quoted-CSV lines, which the parser cannot read, so they land in
`InfSection.unknown_lines` with `entries` empty. Feeding a native file to the
parser and counting `unknown_lines` says how blind the reader is to exactly the
sections `domain-layer-status.md` names as the risky ones.

`[Group Membership]` is included below for the same reason: the module writes
`{sid}__Members`, and whether Windows keys it by SID or by name is unmeasured.

### Why `LabMS01`

`secedit`-area authoring is a GPMC snap-in gesture, and the resulting
`GptTmpl.inf` format is domain-independent. The one part of `security_template`
that genuinely needs a real domain is `[Kerberos Policy]`, which exports empty on
a member server — that is **request 7**, and it is deliberately separate.

### Steps

1. GPMC → **Group Policy Objects** → New →
   `zz-studio-evidence-04-secsettings`. **Unlinked.** → **Edit**.
2. **Computer Configuration → Policies → Windows Settings → Security Settings →
   Registry.** Right-click → **Add Key**. Three times, one per radio option:

   | # | Key (Select Registry Key dialog) | After OK, in the security dialog, choose |
   |---|---|---|
   | 1 | `MACHINE\SOFTWARE\zzStudioAlpha` | *Configure this key then* → **Propagate inheritable permissions to all subkeys** |
   | 2 | `MACHINE\SOFTWARE\zzStudioBravo` | *Configure this key then* → **Replace existing permissions on all subkeys with inheritable permissions** |
   | 3 | `MACHINE\SOFTWARE\zzStudioCharlie` | **Do not allow permissions on this key to be replaced** |

   For keys 1 and 2, when the ACL editor opens, add **Administrators** with
   **Full Control** and leave the rest — the DACL contents do not matter, only
   the option code does. The dialog will let you type a key path that does not
   exist; accept it.

   *(Flagged uncertainty: the exact wording of those three radio buttons is from
   the classic Security Settings dialog. If Server 2025's wording differs,
   choose the option that is clearly the first/second/third and **record which
   label you actually clicked for each key** — that mapping is the answer, so
   the label text matters more than the key names.)*

3. **Security Settings → File System.** Right-click → **Add File**. Path
   `C:\zzStudioData`. Add **Administrators / Full Control**, then choose
   **Replace existing permissions on all subfolders and files with inheritable
   permissions**. One entry is enough here — the registry trio already maps the
   codes; this one confirms the same vocabulary is used for files.

4. **Security Settings → Restricted Groups.** Right-click → **Add Group** →
   type `zz-studio-restricted` → OK. In *Members of this group*, **Add** →
   `LAB\Domain Admins`. OK.
   *(If GPMC refuses a non-existent group name, use `Backup Operators` instead
   and say so.)*

5. **Security Settings → Account Policies → Account Lockout Policy.** Set:
   - Account lockout duration: **47** minutes
   - Account lockout threshold: **13** invalid logon attempts
   - Reset account lockout counter after: **11** minutes

   These deliberately non-default, mutually distinguishable numbers make a unit
   conversion impossible to hide. `policy_families.py` writes
   `lockout_duration_minutes` straight out with no conversion; if Windows stores
   something other than `47` we will see it immediately.

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

Paste the `Get-Content` output into your reply. It is short, and it contains the
whole answer to 4a.

### What to return

- `C:\gpo-studio\manual\r04-secsettings\GptTmpl.inf` — **the primary artifact.**
  Send it as a file, not as pasted text; the encoding is part of the evidence and
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
restricted-group name are all synthetic.

### Cleanup

Deferred to R5's block.

---

## R5 — A multi-CSE reference backup, and the `gpt.ini` version delta

**Direction A.** Estate: **`LabMS01`.** Estimated: **20 minutes.**

### Honesty first

**Half of this request is confirmatory and should be read that way.** The
multi-CSE backup will almost certainly show Windows doing what we expect: a
`Backup.xml` with populated `MachineExtensionGuids` / `UserExtensionGuids`, a
`DomainSysvol` tree, a `bkupInfo.xml`. Its value is not as a discriminator — it
is that it becomes **the single most reusable artifact in this document**. Every
future lane that needs "what does a real multi-CSE backup look like" reads it
instead of booking you again. That is worth twenty minutes even though it is
unlikely to surprise anyone.

The **`gpt.ini` half is not confirmatory** and is the part that can fire.

### The question — `gpt.ini` version packing

`publication.py:499` computes `$expectedVersion = [int]$currentVersion + 1`.
`GPT.INI`'s `Version=` is a **packed 32-bit field**: `docs/live-publication.md`
says so itself — "user changes increment the upper 16 bits and computer changes
increment the lower 16 bits" — and this repo's own WP-2 finalizer already unpacks
the corresponding `Backup.xml` numbers as two 16-bit halves
(`finalize_wp2_import_run.py`: `packed_machine == (dsa << 16) | sysvol`).

If flat `+1` is wrong, a **user-side-only publication increments the computer
counter**, and clients never reprocess the user side. That is the failure mode
that makes a GPO quietly stale rather than visibly broken.

Two edits, three reads of `GPT.INI`, and the deltas answer it.

### Why `LabMS01`

It needs a real DC and real SYSVOL, which the lab has. It does **not** need a
production directory. Doing this on the live domain would mean creating a GPO
there and editing it twice, for an answer the lab gives identically.

### Steps

1. GPMC → **Group Policy Objects** → New → `zz-studio-evidence-05-multicse`.
   **Unlinked.** → **Edit**.
2. Give it settings across **four** extensions, so the backup is a genuine
   multi-CSE reference:
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
     **Enabled**. (Registry CSE, **user** side — this one matters for step 4.)
3. Close the editor. **Read `GPT.INI` — reading #1:**

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
   Close the editor. **Reading #2** — rerun the block above with the label
   `READING 2`.
5. Reopen the editor and make **one computer-side-only change**: *Computer
   Configuration → Policies → Administrative Templates → System → Logon* →
   *Do not process the legacy run list* → **Enabled**. Close the editor.
   **Reading #3** — rerun the block with the label `READING 3`.
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

- The three `READING` blocks, pasted into your reply. **These are the actual
  answer** — three `Version=` integers and their deltas.
- The whole of `C:\gpo-studio\manual\r05-multicse\`, including the full backup
  tree. This becomes the reference corpus.
- The `Format-List` output of the extension names.

### Sanitisation

Standard lab treatment: `replace-domain-sid` and `replace-sd-hex` over the
backup, `replace-gpreport-sid` over `gpreport.xml`. `GPT.INI` itself contains no
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

Paste the output of both blocks. If the re-query returns any row, say so — an
incomplete cleanup makes the sitting `inconclusive` under
`plan-033/boundary-matrix.md`'s evidence-state rule, and we would rather know.

---

# Sitting B — `ad.hraedon.com`, read-only (~40 min)

**Nothing in this sitting creates, modifies or deletes anything.** Every command
is a read. There is no cleanup to do beyond deleting the local staging directory,
because nothing was changed.

Run these from wherever you normally administer the domain — a DC or an admin
workstation with RSAT. *(Flagged uncertainty: request 7 must run **on a domain
controller**; the other two do not care.)*

```powershell
New-Item -ItemType Directory -Force -Path 'C:\gpo-studio\manual' | Out-Null
```

---

## R6 — Extension-list census over the existing production GPOs

**Direction A.** Estate: **`ad.hraedon.com`.** Estimated: **15 minutes.**
**Read-only.**

### The question

`gpmc_interop.py:40–44` defines `_KNOWN_CSE_GUIDS` as three entries, two of which
are labelled "GPP Groups" `{3125E937-EB16-4b4c-9934-544FC6D24D26}` and "GPP
Registry" `{A3CC7818-8A30-4e0c-91C5-A4EA4B5A8DAB}`. **Verified: those two
literals appear everywhere else in this repository as the `clsid` attribute on
the root element of a GPP XML file** — `<Groups clsid="{3125E937…}">` at
`gpp.py:70`, `gpp_adapters.py:79`, `conformance.py:598` — not as client-side
extension GUIDs. Extracting every extension list from the seventeen committed
GPMC-authored backups shows **neither GUID in any of them**; Groups appears as
`[{17D89FEC-…}{79F92669-…}]`, which is also what `export.py:42` emits.

If the hypothesis holds, any GPO carrying GPP `cse_metadata` trips the
`unknown_cse_guid` **error** branch (`gpmc_interop.py:220–228`) and is reported
`is_gpmc_importable = False` — **including every GPMC-authored one.**

The same three literals are defined again at `publication.py:126–128` and are
**never read anywhere in that file**, which is the second half of the question:
`generate_publication_plan` emits no step that updates
`gPCMachineExtensionNames` / `gPCUserExtensionNames`, and those attribute names
appear **nowhere in `src/`** at all. A GPO whose SYSVOL contains a `Registry.pol`
but whose extension list is empty **is not processed by any client** — it is
inert rather than wrong, which is the failure mode offline tests structurally
cannot see.

- **If no production GPO carries either GUID in an extension list** — both
  hypotheses are confirmed against a population rather than against seventeen
  fixtures this project curated, and `_KNOWN_CSE_GUIDS` is wrong.
- **If some do** — we have learned something genuinely unexpected and the
  fixture sweep was misleading.
- **Either way** we get a real-world CSE-GUID frequency table, which is the
  ground truth `_KNOWN_CSE_GUIDS` should have been built from.

### Why the live domain, and why it is uniquely able to settle this

The lab has no organically-authored GPOs. Every GPO on `LabMS01` was created by
this project, for this project, in the last two weeks — asking it what CSE GUIDs
"really" appear is asking our own fixtures a second time, which is exactly the
self-consistency trap `domain-layer-status.md` exists to reject. A production
domain has GPOs authored by different people, through different Windows
generations, for real reasons. **That population is the oracle, and no lab can
manufacture it.**

### Steps

Run this exactly as written. **It deliberately selects only the two extension
attributes — no display names, no DNs, no GUIDs, no creation times.** That is
sanitisation by construction: the identifiers never leave the domain, so there is
nothing to redact afterwards.

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

Plus the two summary lines pasted into your reply — the total GPO count and the
match count for the two suspect GUIDs.

### Sanitisation

**Open the CSV before sending it.** It should contain nothing but bracketed GUID
strings and commas. A CSE extension list is structurally incapable of carrying a
name or a SID, so if you see anything that looks like a word, a domain, or an
`S-1-5-…`, stop and tell us — that would mean the query returned more than we
asked for.

The GPO **count** is itself a weak piece of estate structure. It is fine in a
reply to us; we will not commit the raw count in a fixture.

**Do not** add `displayName`, `distinguishedName`, `gPCFileSysPath`, or
`whenCreated` to that `-Properties` list "to make it easier to read." Every one
of them is a production identifier, and none is needed.

### Cleanup

Delete `C:\gpo-studio\manual\r06-cse-census` after transfer. Nothing in the
directory was modified.

---

## R7 — `[Kerberos Policy]` from a real domain controller

**Direction A.** Estate: **`ad.hraedon.com`, on a domain controller.**
Estimated: **5 minutes.** **Read-only.**

### The question

`[Kerberos Policy]` is a **named blocker** in the survey — a `(b)` residual on
both `security_template.py` and `policy_families.py`. It **cannot be measured on
`LabMS01`**: it exports empty on a member server (measured 2026-08-04), and
`platforms.json` states that the `dc-ws2025` host_id "is historical and does not
imply the lane executes on the DC." No lane has ever executed on `LabDC01`.

`policy_families.py:267–274` writes `max_ticket_age_hours`,
`max_renewal_age_days` and `max_clock_skew_minutes` out as bare integers with
**no conversion**, under the key names `MaxTicketAge`, `MaxRenewAge`,
`MaxClockSkew`. Whether Windows means hours, days and minutes for those three
keys has never been observed. It is the same class of question as WI-024.

One `secedit /export` on a real DC produces the section, with the domain's actual
values, and settles the key names, the unit shape and the omission rules at once.

### Why the live domain

Because it is the only domain controller available. `LabDC01` exists but no lane
has ever run there, and running one is a larger piece of work than this request.
`secedit /export` is read-only — it writes a local `.inf` file and touches
nothing in the directory or SYSVOL.

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

Also, so we can tell a converted number from a stored one:

```powershell
Get-ADDefaultDomainPasswordPolicy |
  Select-Object MaxTicketAge, MaxServiceAge, MaxClockSkew, MaxRenewAge
```

`Get-ADDefaultDomainPasswordPolicy` returns .NET `TimeSpan` values, so it says in
plain units what the raw integers mean. Comparing the two is the whole oracle.

### What to return

**Paste the two console outputs into your reply. Send no files.**

That is roughly ten lines of text, and it is everything we need.

### Sanitisation — the strictest in this document

**`dc-effective.inf` must NOT be sent, and must NOT be committed under any
circumstance.** `secedit /export /areas SECURITYPOLICY` on a production DC
exports the **effective domain security policy**: `[Privilege Rights]` with the
real SID of every principal holding every privilege, `[Group Membership]` with
real group SIDs, `[System Access]` with the domain's real password and lockout
posture, and `[Registry Values]` with the real security-options configuration.
That is a security-relevant description of the production domain. It is not
fixture material at any level of redaction.

The **`[Kerberos Policy]` section alone** is safe: five key/value pairs of
integers, no principals, no SIDs, no names. That is why the command above prints
a slice rather than the file.

The values themselves are the domain's real Kerberos settings. They are policy
posture, not secrets, and if they turn out to be the Windows defaults we can
commit them as a fixture. If they are non-default we will use them to *interpret*
the units and commit synthetic values instead — tell us if you would rather we
did that either way.

### Cleanup

```powershell
Remove-Item -Force 'C:\gpo-studio\manual\r07-kerberos\dc-effective.inf'
Remove-Item -Recurse -Force 'C:\gpo-studio\manual\r07-kerberos'
```

**Do this before you leave the console.** The file is the sensitive artifact in
this document; do not let it sit on disk.

---

## R8 — What a real published GPO actually consists of

**Direction A.** Estate: **`ad.hraedon.com`.** Estimated: **20 minutes.**
**Read-only.**

### The question

`publication.py`'s `generate_publication_plan` emits exactly six step kinds:
`update_gpt_ini`, `write_registry_pol`, `copy_gpp_xml`,
`update_nt_security_descriptor`, `associate_wmi_filter`, `update_gplink`. That
list is a **hypothesis about what publishing a GPO consists of**, and it has
never been checked against a GPO that was actually published by Windows.

**Any attribute Windows maintains that no `PublicationStep` mentions is a hole in
the plan.** We already suspect one — `gPCMachineExtensionNames` /
`gPCUserExtensionNames`, which appear nowhere in `src/` (see R6). The purpose of
this request is to find the ones we have not thought of.

This is the biggest unknown in the product, and it is what a real domain is
uniquely able to settle.

- **If the six steps cover every non-empty attribute and every SYSVOL file
  class** — the plan is complete and that is a genuine, non-trivial result.
- **If they do not** — we get an enumerated list of what publication forgets,
  which is the input to Plan 030's scope.

### Why the live domain

Same argument as R6, one level up. A lab GPO's attribute set is whatever *we*
caused to exist. A production GPO's attribute set is what Windows and real
administrative practice caused to exist — WMI filter associations, non-default
DACLs, delegated permissions, `gPCFunctionalityVersion`, whatever else is there.
We are asking "what does Windows maintain," and the lab cannot answer that
because we built the lab.

### Steps

1. **Pick three GPOs** that are, as far as you can tell, *different in kind* —
   for example one policy-heavy Administrative Templates GPO, one carrying
   Preferences, and one carrying security settings. Do not pick anything
   sensitive; do not tell us their names.

2. For each, run this. **It reports attribute names and whether they are
   populated — never their values.** Sanitisation by construction again.

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

3. Additionally, once for the domain, so we know what a WMI-filter association
   looks like when one exists:

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

- **`*-attributes.csv`** contains LDAP attribute *names* and booleans. Safe by
  construction. **Check it before sending** — if any value column slipped in,
  that is a bug in the script above and we would rather fix it than redact.
- **`*-sysvol.csv`** contains relative paths under the GPO folder. These are
  Windows-defined structural paths (`\Machine\Registry.pol`,
  `\User\Preferences\Drives\Drives.xml`). They are safe unless a GPO carries a
  script or file whose *name* is an identifier — **open the CSV and look**; if
  you see a file name that identifies a person, a server or a project, delete
  that row before sending and tell us you did.
- **`*-GPT.INI`** is `[General]` + `Version=` + `displayName=`. The
  `displayName=` line, where present, is a **real GPO name** — delete it, or
  send only the `Version=` line. That is the one identifier in this request that
  is guaranteed to be present.
- **Do not send `Get-GPOReport` output for any production GPO.** It contains
  every setting value, every principal in the security filtering, and the full
  security descriptor. It is not needed for this question.

### Cleanup

Delete `C:\gpo-studio\manual\r08-gpo-anatomy` after transfer. Nothing was
modified; the three GPOs you read were not touched.

---

# Sitting C — Direction B (~55 min) — **blocked**

These three requests hand Windows something **Studio** wrote. That is the
stronger oracle: it proves Windows *accepted* our output, not merely that our
output resembles a sample.

**None of them can be run yet.** Each needs a Studio-side bundle that does not
exist on disk. The bundle is named per request, with the module, function and
script that would produce it, so it can be generated and attached. **Do not
start Sitting C until the bundle is in your hands.**

---

## R9 — `secedit /validate` on Studio's SDDL sections

**Direction B.** Estate: **`LabMS01`.** Estimated: **10 minutes.**
**Blocked on a bundle.**

### The question

`object_security.py`'s `to_template_entries()` emits, via
`_format_object_value`, entries of the form
`MACHINE\SOFTWARE\Path = 2,"D:PAR(A;OICI;FA;;;BA)"` — a `key = value` pair,
because the only serializer that could consume its output is
`format_security_template`, which writes `f"{key} = {value}"`.

The corpus's own spec-informed excerpts show the native form as a **bare quoted
CSV line with no `=`**:
`"MACHINE\SOFTWARE\StudioLab\Audit",0,"D:PAR(A;OICI;FA;;;BA)"`.

Different shapes. Which one `secedit` accepts has never been measured.
`secedit /validate` is a real oracle here and not a rubber stamp — it was
measured on 2026-08-04 to reject a malformed SDDL with a specific error.

- **If it rejects Studio's shape** — the module cannot write these three sections
  at all, and that is a concrete correction rather than a suspicion.
- **If it accepts** — the shape is fine and WI-038's scope shrinks to the reader
  only.
- **If it accepts silently and R4's native `GptTmpl.inf` shows the other shape** —
  then `secedit /validate` is not strict about this, which is itself worth
  knowing before anyone builds a comparator on top of it.

### The bundle we must hand you first

**A single file**, `candidate.inf`: UTF-16LE with a BOM, CRLF line endings, an
`[Unicode]`/`[Version]` preamble, and three sections rendered from
`RegistrySecurityFamily.to_template_entries()`,
`FileSystemSecurityFamily.to_template_entries()` and
`SystemServicesFamily.to_template_entries()`.

How it would be produced: a new
`scripts/plan-033/build-object-security-candidate.py`, modelled directly on the
existing `scripts/plan-033/build-wp3-candidate.py` — construct the three families
from `gpo_studio.object_security`, merge their `to_template_entries()` dicts into
`InfSection`s, render with
`gpo_studio.security_template.format_security_template`, and write the bytes
through `gpo_studio.security_template.encode_security_template`, which is the
function the WP-3 correction added and which supplies the UTF-16LE BOM and the
CRLF endings. Roughly thirty lines. **It does not exist yet.**

### Steps (once you have `candidate.inf`)

```powershell
$root = 'C:\gpo-studio\manual\r09-secedit-validate'
New-Item -ItemType Directory -Force -Path $root | Out-Null
# Place candidate.inf in $root first.

secedit /validate "$root\candidate.inf"
"exit code: $LASTEXITCODE"
```

Then, regardless of the result, try the alternative shape we will send alongside
it as `candidate-native-shape.inf`:

```powershell
secedit /validate "$root\candidate-native-shape.inf"
"exit code: $LASTEXITCODE"
```

Two files, two verdicts. The pair is the answer — one passing and one failing is
far more informative than either alone.

### What to return

The two console outputs, pasted. Include the full error text if there is one;
`secedit`'s error messages are specific and are the interesting part.

Nothing else. No files come back from this request.

### Sanitisation

None. Both inputs are synthetic files we wrote; the outputs are `secedit`'s own
error strings. If an error message quotes a path from the host, that path is
`C:\gpo-studio\manual\...` and is not an identifier.

### Cleanup

`Remove-Item -Recurse -Force 'C:\gpo-studio\manual\r09-secedit-validate'`.
Nothing was imported, nothing was configured, no GPO was created.

---

## R10 — Does Windows accept a Studio-written `scripts.ini`?

**Direction B.** Estate: **`LabMS01`.** Estimated: **25 minutes.**
**Blocked, and harder than the others — read this before scheduling it.**

### The question

R2 tells us what Windows *writes*. This tells us whether Windows *accepts* what
Studio writes: import a Studio-produced GPMC backup carrying a
`Machine\Scripts\scripts.ini` and the Scripts CSE GUID, then ask GPMC to render
it and to back it up again.

If `Get-GPOReport` shows the script with the authored command, parameters and
order, Windows understood the file. If it shows an empty Scripts node, the file
was ignored — which is the inert failure mode, and it is exactly what R2 cannot
detect on its own.

### Why this is blocked harder than R9

**The bundle cannot be produced by the current code.** `export.py`'s
`gpmc_backup_bundle` is the native-backup writer, and `_native_export_files`
(`export.py:420–465`) emits **only** `Machine/registry.pol`, `User/registry.pol`,
and GPP XML for four allowlisted families (`Drives`, `Groups`, `ScheduledTasks`,
`Services`). It has no path for a Scripts file, and `_extension_guids`
(`export.py:468`) has no Scripts CSE profile to register in `Backup.xml`.

So this request needs, first, a change to `export.py` — a Scripts branch in
`_native_export_files` and a Scripts entry in the extension-profile table —
which is product work, not a script. **R2 must come first anyway**, because the
correct encoding and preamble for `scripts.ini` are precisely what that change
has to be built from. Sequencing: R2 → fix `script_policy.py` and extend
`export.py` → then R10.

Flagging that plainly rather than writing an instruction that cannot be
followed: **do not schedule R10 until we tell you the bundle exists.**

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
(Startup/Shutdown) → Startup**. Say whether the two `.cmd` entries and the one
`.ps1` entry are there, and whether the PowerShell ordering dropdown shows the
non-default value.

That last step is a human observation and cannot be automated — it is the only
honest oracle for "GPMC can edit this," which is the sub-claim
`gpmc_interop.py`'s `is_gpmc_editable` makes.

### What to return

- `gpreport-after-import.xml`
- The whole `rebackup\` tree — specifically its
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

---

## R11 — Does a real production domain accept Studio's output?

**Direction B.** Estate: **`ad.hraedon.com`.** Estimated: **20 minutes.**
**This is the only request in this document that writes to the live domain.**
**Requires a fresh go-ahead before it is run.**

### The question

WP-2 is certified 18/18: a Studio-generated GPMC backup imports into the lab,
reports correctly, and re-exports. That certification is bound to a
single-DC, two-week-old, purpose-built forest with no organic content.

**Does the same artifact import into a production directory?** A real domain
differs in ways that could matter and that the lab cannot simulate: multiple
domain controllers, DFS-R SYSVOL replication, an existing Policies container with
real ACL inheritance, a real default GPO DACL, and whatever schema extensions and
delegations have accumulated.

- **If it imports cleanly and the resulting GPC carries the expected
  `gPCMachineExtensionNames`** — this becomes the first observation this project
  holds about a real domain accepting its output. That is a meaningfully stronger
  claim than WP-2, and it is what would let `export.py`'s native-backup path stop
  being "certified against a synthetic estate."
- **If it fails** — we learn what the lab does not model, which is worth
  considerably more than another green lab run.

### What it writes, precisely, and why the lab will not do

**It creates exactly one object:** a new `groupPolicyContainer` named
`zz-studio-evidence-11-roundtrip`, with its SYSVOL folder. Created by
`Import-GPO -CreateIfNeeded`, which is the supported Microsoft path.

- It is **never linked**. Not to the domain root, not to an OU, not to a site.
  An unlinked GPO applies to nothing and is inert.
- It **modifies no existing object.** `Import-GPO -CreateIfNeeded` with a target
  name that does not exist creates; it does not touch anything else.
- It contains **two synthetic registry values** under
  `Software\Policies\GPOStudio\WP2`, which no product reads.
- It is removed by `Remove-GPO` in the same sitting, with a strict re-query.

The lab will not do because the lab is exactly the thing already tested. The
question *is* "does the difference between a synthetic forest and a production
directory matter," and only a production directory can answer it.

**If you would rather not create anything in the live domain, skip this request.**
Requests 6, 7 and 8 are all read-only and carry most of Sitting B's value.
Nothing downstream depends on R11.

### The bundle we must hand you first

`wp2-candidate-backup.zip`. **This one can be produced today with existing code**
— unlike R9 and R10 — because it is exactly the artifact the certified WP-2 lane
already builds:

```
python scripts/plan-033/build-wp2-candidate.py <output-dir>
```

which calls `gpo_studio.export.gpmc_backup_bundle` and
`gpo_studio.export.native_backup_id` on a fixed synthetic GPO
(GUID `11111111-2222-3333-4444-555555555555`, two registry values, one machine,
one user). We will zip its output and send it with the recorded SHA-256, so the
artifact you imported can be bound to a commit.

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
  **The `LinksTo` output being empty is the safety confirmation** — send it even
  though it is boring.
- The whole `rebackup\` tree.
- The `Get-FileHash` result, so we can bind the artifact you used.

**Do not** run `Get-GPOReport` over any other GPO while you are here.

### Sanitisation

This is the one artifact in this document that comes out of a **production
directory**, so it needs the most care and the sanitiser was not written for it:

- **`rebackup\{GUID}\Backup.xml`** carries `GPODomain` (`ad.hraedon.com` —
  allowed), `GPODomainGuid` (**the production domain GUID — must be replaced**),
  `GPODomainController` (a real DC FQDN — allowed under the homelab permit, but
  we will replace it anyway), and a `SecurityDescriptor` hex blob containing the
  **production domain SID** plus whatever the domain's default GPO DACL grants.
  `replace-domain-sid` and `replace-sd-hex` handle the last two **only if
  `GPO_STUDIO_REAL_SID_PREFIX` is set to the production prefix at sanitisation
  time.** The domain GUID has no rule and needs one.
- **`bkupInfo.xml`** carries the same domain fields plus a real `BackupTime`.
- The `Registry.pol` files are Studio's own synthetic bytes and are clean.

Because of the domain GUID and the default-DACL descriptor, treat the assumption
as: **nothing from this request is committable until it has had a manual pass on
top of the sanitiser.** The pasted `Format-List` output is fine to work from
directly — extension lists and integers carry no identifiers.

### Cleanup — **do not leave the console without running this**

```powershell
Remove-GPO -Name 'zz-studio-evidence-11-roundtrip'

# Strict absence re-query against the live domain. Must return nothing.
Get-GPO -All | Where-Object { $_.DisplayName -like 'zz-studio-evidence-*' } |
  Select-Object DisplayName, Id

Remove-Item -Recurse -Force 'C:\gpo-studio\manual\r11-live-roundtrip'
```

Paste the re-query output. If it returns a row, say so immediately — an orphaned
unlinked GPO in a production domain is harmless but untidy, and we would rather
chase it now than find it in six months.

---

## Where this document is uncertain

Stated inline above as well, collected here so nothing is buried. Each of these
costs one question to resolve; guessing would have cost a sitting.

1. **File transfer from the Windows hosts to `mvmcc03`** is not specified,
   because we do not know what you use. Any method is fine.
2. **Whether the `LabMS01` console can reach a file share** for returning
   artifacts, or whether they have to come out through the hypervisor. The estate
   has no egress by design.
3. **`mtedit.exe` availability** on Server 2025 (R1). The COM fallback exists,
   and the `EntryType*` constant names in it are from the GPMC COM reference and
   are not verified on this build.
4. **Whether GPMC will save a Folder Redirection target pointing at an
   unreachable UNC path** in an isolated lab (R3). We think it warns and saves;
   if it refuses, substitute a reachable share.
5. **The exact wording of the three registry-security radio buttons** on Server
   2025 (R4). The mapping from label to integer is the answer, so record the
   label you clicked rather than the one we guessed at.
6. **Which host you administer `ad.hraedon.com` from**, and whether request 7
   can be run on a DC (it must be).
7. **Whether the live domain's SYSVOL is DFS-R or FRS.** It affects nothing in
   these instructions, but it is worth one line in your reply — it changes how we
   read R11's result if the import behaves oddly.
8. **Whether you hold rights to create a GPO in `ad.hraedon.com`** (R11 only —
   requests 6, 7 and 8 need only read access). If not, R11 is skipped rather
   than escalated.

## After the artifacts land

The agent's side, recorded so the loop is closed rather than implied:

1. Retrieve from `/home/itadmin/gpo-studio-evidence/inbox/<request-id>/`.
2. Inspect raw, **outside the repository**, and record raw SHA-256 per file.
3. Sanitise with `scripts/plan-033/sanitize-gpp-fixtures.py` plus a manual pass
   for anything the live domain contributed, producing a
   `sanitization-record.json` in the shape
   `tests/fixtures/native-gpp-gpmc/sanitization-record.json` already uses.
4. Curate into a fixture under `tests/fixtures/`, hash-bound, and run the
   identifier gate over the tree **before** the commit, not after.
5. Update
   [`plan-033/manual-gui-evidence-queue.md`](plan-033/manual-gui-evidence-queue.md)
   with the result and the questions it settled — and, on the first live-domain
   request, replace that document's retired `mvmcitest01` access path and add the
   live-domain safety rules from the top of this document.
6. Record each answered question against the module it bears on, per
   [`plans-025-032-oracle-survey.md`](plans-025-032-oracle-survey.md) §5.

A capture is certified **against the capture**, not against the authoring
gesture. Nothing here becomes a re-runnable lane — that is the cost of the manual
queue, and it is why these requests were enumerated before you were booked rather
than discovered one at a time.
