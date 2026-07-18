# Plan 021 WP-4 — Reference estates and evidence schema

> **Status:** provisional (pre-review-gate). This document is adopted now so
> that fixture generation, redaction, and signing are not blocked on the Plan
> 021 review gate. The gate **ratifies or amends** this matrix and these rules;
> it does not initiate them. See
> [`plans/021-gpmc-parity-contract-and-adapter-platform.md`](../../plans/021-gpmc-parity-contract-and-adapter-platform.md)
> WP-4 and Acceptance gates.
> **Schema version:** `1` (generalizes the 1.0 `release-evidence-report.json`
> `report_version: 2`).
> **Substrate note.** The `windows-evidence-lab` command boundary was
> live-qualified on 2026-07-18 from the Linux operator box `mvmhermes01`
> against the `ad.hraedon.com` forest (DC `mvmdc03`). All homelab identifiers
> (`hraedon`, `mvm*`, `ad.hraedon.com`, `studio.local`) are allowed per the
> identifier gate; real work-domain identifiers are not.

This document defines the mechanical gates that must hold before any fixture
pack or evidence pack produced by the parity program is signed. It is
rule-oriented and normative: where it says "MUST", "MUST NOT", or "CANNOT",
the signing step enforces it.

The 1.0 release evidence (WS2025 build 26100, domain `ad.hraedon.com`, DC
`mvmdc03`, see [`release-evidence.md`](../release-evidence.md) and
[`release-evidence-report.json`](../release-evidence-report.json)) is the
existence proof that this schema generalizes. The 1.0 report is a different
shape (`report_version: 2`) and is **not** parsed by the schema-1 loader; it is
treated conceptually as a legacy `schema_version: 0` artifact. The
`load_pack`/`parse_pack` functions in `src/gpo_studio/evidence.py` accept
`schema_version: 1` only; a schema-version-0 adapter is out of scope for the
pre-gate deliverable.

---

## 1. Provisional target matrix

Adopted now. Ratified or amended at the Plan 021 review gate. Windows 10 rows
are admitted **only** behind an explicit ESU decision recorded against the
matrix row; they are not default members.

### Matrix columns

| Column | Meaning |
|--------|---------|
| **OS** | Windows Server or Windows client marketing name. |
| **Build** | Windows build number (e.g. 26100). Drives ADMX central-store level. |
| **Role** | `DC` (domain controller), `member-server`, or `client`. |
| **GPMC availability** | `inbox` / `RSAT` / `absent`. `absent` rows carry no GPMC evidence. |
| **ADMX central-store level** | The central-store ADMX generation matched to the build (e.g. WS2025 = `Windows 11 24H2 / Server 2025 ADMX set`). |
| **Deprecation / ESU status** | `mainstream`, `esu-active`, `esu-required`, `out-of-support`. |
| **Lab ownership** | Which lab class hosts the row (see §7). `disposable lab* VM` for destructive work; `mvm*` hosts are off-limits for destructive ops; `mvmhermes01` is the Linux operator box only. |

### Provisional rows

| OS | Build | Role | GPMC | ADMX central-store level | Status | Lab ownership |
|----|-------|------|------|--------------------------|--------|---------------|
| Windows Server 2025 | 26100 | DC | inbox | WS2025 / Win11 24H2 ADMX | mainstream | `mvmdc03` (non-destructive import/report only); destructive work on `lab-dc-*` |
| Windows Server 2025 | 26100 | member-server | inbox | WS2025 / Win11 24H2 ADMX | mainstream | `lab-srv-*` disposable |
| Windows Server 2022 | 20348 | DC | inbox | WS2022 ADMX | mainstream | `lab-dc-2022-*` disposable |
| Windows Server 2022 | 20348 | member-server | inbox | WS2022 ADMX | mainstream | `lab-srv-2022-*` disposable |
| Windows Server 2019 | 17763 | DC | inbox | WS2019 ADMX | esu-active (admit only if a documented parity claim requires WS2019 DC behaviour) | `lab-dc-2019-*` disposable |
| Windows Server 2019 | 17763 | member-server | inbox | WS2019 ADMX | esu-active | `lab-srv-2019-*` disposable |
| Windows 11 24H2 | 26100 | client | RSAT | WS2025 / Win11 24H2 ADMX | mainstream | `lab-win11-*` disposable |
| Windows 11 23H2 | 22631 | client | RSAT | Win11 23H2 ADMX | mainstream | `lab-win11-23h2-*` disposable |
| Windows 10 22H2 | 19045 | client | RSAT | Win10 22H2 ADMX | **esu-required** (admitted only behind an explicit ESU decision row; no default coverage) | `lab-win10-*` disposable |

### Lab-ownership rules

- The `mvm*` hosts (`mvmdc03`, and any other `mvm`-prefixed host) are
  long-lived homelab infrastructure. They are off-limits for destructive
  operations: no `Remove-GPO` against non-disposable GPOs, no SYSVOL writes
  outside the documented disposable-GPO pattern, no schema or DC-promo changes.
- `mvmhermes01` is the Linux operator box. It is not a Windows target. It hosts
  the operator's shell, the `windows-evidence-lab` command boundary, and the
  signing step.
- `lab-*` hosts are disposable VMs in isolated forests. Destructive work
  (delete-GPO, restore-into-existing, ACL mutation, schema-touching experiments)
  runs there. Forests are torn down and rebuilt between matrix-row families when
  cross-contamination would taint evidence.
- Per-row evidence is captured on a host that matches the row's build and role;
  a single host MUST NOT serve two different build numbers in the same evidence
  pack.

### Review-gate amendment

The Plan 021 review gate may add, drop, or reclassify rows. Rows it does not
ratify are removed before the first signed pack that claims program-level
coverage. Until then, this matrix is the operative contract for fixture
generation and evidence capture.

---

## 2. Licensing rules for corpus content

These rules MUST be defined before any fixture pack is assembled. ADMX/ADML
files are Microsoft- or vendor-copyrighted; redistributing them in the repo is
a licensing violation regardless of redaction.

### Content classifications

Every content item in a fixture pack or evidence pack carries exactly one
classification:

| Classification | Meaning | Storage |
|----------------|---------|---------|
| `in-repo` | Synthetic, author-authored content, or content whose license permits redistribution. Stored directly in the repository. | bytes in the pack directory |
| `hash-reference` | Referenced by SHA-256 with regeneration instructions. The expected default for Microsoft ADMX/ADML: **do NOT redistribute Microsoft's files**. Reference by hash plus the exact Windows build and source path to regenerate. | sha256 + regeneration metadata only; bytes never enter the repo |
| `excluded` | Not distributed at all (e.g. vendor content whose license forbids redistribution and whose hash-reference form is impractical). | pack manifest records the exclusion and its reason; no bytes, no hash |

### Per-item metadata fields

Every content item MUST carry:

```text
content_id          # stable pack-local identifier (e.g. "admx/WindowsServer2025.admx")
classification      # one of: in-repo, hash-reference, excluded
sha256              # sha256 of the canonical bytes; null only when classification == excluded
source_build        # the Windows build the bytes were sourced from (e.g. 26100); null for in-repo synthetic content
regeneration_path   # the exact path on the source host (e.g. C:\Windows\PolicyDefinitions\...)
license_note        # short human-readable note (e.g. "Microsoft copyright; hash-reference only")
```

### Signing gate

A fixture pack or evidence pack CANNOT be signed while any content item lacks
a licensing classification. The signing step (§4) computes
`licensing_complete = true` only when every content item has a non-null
`classification` and the per-classification required fields are populated:

- `in-repo` requires `sha256` and `license_note`.
- `hash-reference` requires `sha256`, `source_build`, `regeneration_path`, and
  `license_note`.
- `excluded` requires `license_note` (the reason for exclusion); `sha256`,
  `source_build`, and `regeneration_path` are null.

A pack with an item whose `classification` is absent or whose required fields
are null has `licensing_complete = false` and MUST NOT be signed.

---

## 3. Redaction contract

Windows-generated fixtures, GPMC reports, and endpoint observations MUST
contain synthetic directory names, SIDs, paths, and exports ONLY. Redaction is
verified by the identifier gate before a pack is signed.

### What must be redacted

Real identifiers MUST NOT appear in any pack content or pack metadata. This
list is exhaustive for the gate:

- real domain DNS names (e.g. a work-domain `corp.example.com`)
- real SIDs
- real hostnames
- real service-account names
- real distinguished names (DNs)
- real SYSVOL paths
- real user or computer names

### Canonical substitution scheme

Real identifiers are replaced with synthetic homelab identifiers. The
homelab identifiers (`hraedon`, `mvm*`, `ad.hraedon.com`, `studio.local`) are
allowed per the identifier gate (AGENTS.md) and are the canonical replacements:

| Real kind | Canonical replacement |
|-----------|------------------------|
| Domain DNS name | `ad.hraedon.com` (forest root) or `studio.local` (synthetic default domain) |
| NetBIOS domain | `HRAENET` |
| DC hostname | `mvmdc03` (non-destructive rows) or `lab-dc-*` (disposable rows) |
| Member server | `lab-srv-*` |
| Client | `lab-win11-*` / `lab-win10-*` |
| Service account (least-priv) | `gpstudio-lab` |
| Service account (diagnosis) | `svc-da` (SID redacted) |
| User / computer principal | synthetic `SYNTHETIC\AdminGroup`-style placeholders; never real names |
| DC / forest / domain DN | `DC=ad,DC=hraedon,DC=com` or `DC=studio,DC=local` |

SIDs are either fully synthetic (e.g. `S-1-5-21-111111111-222222222-333333333-1111`)
or redacted to the literal string `SID redacted` in human-readable fields.
Native Windows well-known SIDs (`NT AUTHORITY\SYSTEM`, `BUILTIN\Administrators`,
`S-1-1-0`, etc.) are not real identifiers and need no redaction.

### Verification step

Redaction is verified by the identifier gate
(`scripts/check_committed_identifiers.py` with the
`GPO_STUDIO_FORBIDDEN_IDENTIFIERS` secret). The gate is run over every text
file in the pack before signing:

1. The pack is assembled in a staging directory.
2. `GPO_STUDIO_FORBIDDEN_IDENTIFIERS` is set to the denylist of real
   identifiers.
3. `python scripts/check_committed_identifiers.py` is run over the staging
   directory.
4. The pack is signed (§4) only when the gate is clean (exit 0).
5. A pack is unsigned (and MUST NOT be referenced as release evidence) until the
   gate passes; `redaction_verified` is set to `true` only after a clean run.

The always-on guard against tracked files under `samples/` is independent of
the secret-driven scan: both MUST pass. A binary file (UTF-16 BOM not
withstanding) is decoded via BOM detection and scanned the same way.

---

## 4. Evidence record schema

A normalized, versioned JSON schema generalizing the 1.0
`release-evidence-report.json`. This document defines `schema_version: 1`. The
loader (`src/gpo_studio/evidence.py`) accepts `schema_version: 1` only; the 1.0
report is not parseable by it and is referenced here as the conceptual
ancestor, not a supported input.

### Pack metadata

```text
schema_version       # integer; this document defines version 1
pack_id               # stable identifier (e.g. "plan-021/2026-07-18/ws2025-dc-baseline")
generated_at          # ISO 8601 UTC timestamp
source_commit         # git commit sha the pack was built from
operator              # claimed operator identity (unauthenticated in the single-operator profile)
redaction_verified    # bool; true only after the identifier gate passed over the pack
licensing_complete    # bool; true only after every content item is classified (§2)
estate                # the estate block (below)
records[]             # one record per capability exercised
content[]             # the per-item licensing manifest (§2)
```

### Estate block

```text
os                   # marketing name (e.g. "Windows Server 2025")
build                # Windows build number as a string (e.g. "26100")
role                 # DC | member-server | client
forest               # forest DNS name (synthetic only, e.g. ad.hraedon.com)
domain               # domain DNS name (synthetic only)
dc                   # DC hostname (synthetic only, e.g. mvmdc03)
gpmc_version         # GPMC version string as reported by the host
client_os            # for endpoint observations only; null for DC/member-server rows
```

### Record schema

Each entry in `records[]`:

```text
capability          # capability row identifier (e.g. "raw_registry_policy")
cse_guid            # nullable; CSE GUID when the record is CSE-scoped
side                # computer | user | both
action              # the operation exercised (e.g. "set", "delete", "import", "backup")
outcome             # pass | fail | skip | expected_failure
classification      # verified-rw | verified-ro | preserve-only | intentional-deny | not-present-on-target | unknown
evidence_kind       # windows-side | endpoint  (which side of the parity claim this record observes)
tool                # the Windows tool used (e.g. "Set-GPRegistryValue", "Import-GPO", "Backup-GPO")
ms_doc              # URL to the authoritative Microsoft documentation page
evidence_hash       # sha256 of the captured artifact (Registry.pol, backup.xml, gpreport.xml, ...); required for every record
notes               # free-form short notes; mismatch causes go here
```

### Promotion rule

A record with `outcome != pass` CANNOT promote a capability to `verified-rw`.
The public matrix generator (§6) enforces this mechanically: a `verified-rw`
public claim for a (capability, estate) requires **both** at least one passing
`windows-side` record **and** at least one passing `endpoint` record.
Windows-side-only passing evidence yields `verified-ro`; endpoint-only yields
`unknown` (the Windows side was not observed). `expected_failure`, `fail`,
`skip`, and `not-present-on-target` records do not promote a verified claim;
`expected_failure` records are collected into a separate expected-failures
section. Explicit policy classifications (`preserve-only`, `intentional-deny`,
`not-present-on-target`) are taken as-is from any record asserting them.

### Signing model

A pack is **signable** only when `redaction_verified && licensing_complete` are
both `true`. Signing is the act of:

1. Serializing the pack to canonical JSON (keys sorted recursively, UTF-8, no
   trailing whitespace, LF newlines) — implemented by
   `canonical_pack_bytes`/`canonical_pack_hash` in `src/gpo_studio/evidence.py`.
2. Computing a pack-level SHA-256 over the canonical bytes (`--hash` CLI).
3. Recording that SHA-256 in a pinned manifest mirroring
   `docs/.evidence-report-sha256`, and verifying each consumed pack against it
   (`--verify <sha256>` CLI for a single pack).

The pinned manifest is the intended trust root for downstream consumers (the
public matrix generator, release evidence, and review). A pack whose hash is
not in the pinned manifest is unsigned and MUST NOT be treated as release
evidence. The pre-gate generator enforces the signable gate (`redaction_verified
&& licensing_complete`) mechanically and refuses unsigned packs by default; the
full pinned-manifest lookup (consume only packs whose hash appears in a
supplied manifest file) is a WP-4 follow-up tracked against the review gate,
not part of this pre-gate deliverable.

### Minimal example pack

The example below uses synthetic identifiers only. It is illustrative, not a
real captured pack.

```json
{
  "schema_version": 1,
  "pack_id": "plan-021/2026-07-18/ws2025-dc-baseline",
  "generated_at": "2026-07-18T14:05:00Z",
  "source_commit": "0000000000000000000000000000000000000000",
  "operator": "gpstudio-lab",
  "redaction_verified": true,
  "licensing_complete": true,
  "estate": {
    "os": "Windows Server 2025",
    "build": "26100",
    "role": "DC",
    "forest": "ad.hraedon.com",
    "domain": "ad.hraedon.com",
    "dc": "mvmdc03",
    "gpmc_version": "10.0.26100",
    "client_os": null
  },
  "records": [
    {
      "capability": "raw_registry_policy",
      "cse_guid": null,
      "side": "computer",
      "action": "set",
      "outcome": "pass",
      "classification": "verified-rw",
      "evidence_kind": "windows-side",
      "tool": "Set-GPRegistryValue",
      "ms_doc": "https://learn.microsoft.com/powershell/module/grouppolicy/set-gpregistryvalue",
      "evidence_hash": "ebb3123cdd01d229b974db5060790169bd5b9ecea8d1b426adffd427648dd6e3",
      "notes": "REG_DWORD set and verified in Get-GPOReport XML."
    },
    {
      "capability": "gpmc_backup_import",
      "cse_guid": null,
      "side": "both",
      "action": "import",
      "outcome": "expected_failure",
      "classification": "intentional-deny",
      "evidence_kind": "windows-side",
      "tool": "Import-GPO",
      "ms_doc": "https://learn.microsoft.com/powershell/module/grouppolicy/import-gpo",
      "evidence_hash": "f1d2d2f924e98d4b7e7f9a01a3b8c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
      "notes": "Import-GPO rejects legacy manifest.xml/bkupInfo.xml format; requires Backup.xml v2.0. Downgrade fixture, not a regression."
    }
  ],
  "content": [
    {
      "content_id": "fixture/synthetic_all_registry_types.regpol",
      "classification": "in-repo",
      "sha256": "ebb3123cdd01d229b974db5060790169bd5b9ecea8d1b426adffd427648dd6e3",
      "source_build": null,
      "regeneration_path": null,
      "license_note": "Synthetic author-authored content; redistributable."
    },
    {
      "content_id": "admx/WindowsServer2025.admx",
      "classification": "hash-reference",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "source_build": "26100",
      "regeneration_path": "C:\\Windows\\PolicyDefinitions\\WindowsServer2025.admx",
      "license_note": "Microsoft copyright; hash-reference only, do not redistribute."
    },
    {
      "content_id": "vendor/ThirdParty.admx",
      "classification": "excluded",
      "sha256": null,
      "source_build": null,
      "regeneration_path": null,
      "license_note": "Vendor license forbids redistribution and hash-reference is impractical; excluded."
    }
  ]
}
```

---

## 5. Negative and downgrade fixtures

Every matrix row family (DC, member-server, client) MUST ship at least one
negative fixture and one downgrade fixture. These fixtures are not
release-evidence promotions; they are falsifiability probes that prove the
importer/exporter rejects what it must reject and preserves what it must
preserve.

### Negative fixtures

A **negative fixture** is a fixture that MUST be rejected by a correct importer
or exporter. The expected outcome is `expected_failure` (the tool rejects it)
or, for content that is structurally detected, `intentional-deny` at the
Studio boundary.

Required negative fixtures (one per matrix row family):

- **`cpassword-bearing GPP`** — a `Groups.xml` (or any GPP XML) carrying a
  `cpassword` attribute. GPO Studio structurally rejects `cpassword` at every
  boundary (import, Studio bundle export, GPMC backup export); this fixture
  proves the detector fires. The expected Studio outcome is `intentional-deny`;
  the expected GPMC/tool outcome is `expected_failure` only if the tool itself
  rejects the attribute (GPMC does not; that divergence is recorded).
- Additional negative fixtures per row family: malformed PReg (truncated,
  bad hive prefix, missing null terminator), unknown CSE content (preserved,
  not edited), and corrupt backup (partial `manifest.xml`).

### Downgrade fixtures

A **downgrade fixture** is an older-format backup that a newer Windows build
no longer accepts, or that Studio must preserve losslessly even though it
cannot re-emit it. The expected outcome is `expected_failure` (the tool
rejects the older format) combined with `preserve-only` at the Studio
boundary.

Required downgrade fixture (one per matrix row family):

- **Legacy `manifest.xml` / `bkupInfo.xml` backup** — the Studio-emitted
  GPMC backup format that `Import-GPO` on WS2025 (build 26100) rejects. The
  1.0 release evidence established the root cause: `Import-GPO` searches for a
  backup instance by ID inside `Backup.xml` (v2.0
  `GroupPolicyBackupScheme`); the legacy Studio format carries no
  `Backup.xml`, and the directory structure (`{GPO_GUID}/Machine/Registry.pol`
  vs. native `{BACKUP_ID}/DomainSysvol/GPO/Machine/registry.pol`) does not
  match. This fixture records the exact `Import-GPO` error
  (`System.ArgumentException`; inner `0x80070002`) and proves the format
  divergence is known and tracked, not a regression.

### Per-row-family requirement

At minimum, each matrix row family (DC / member-server / client) carries:

1. One negative fixture: `cpassword-bearing GPP` (the canonical safety probe).
2. One downgrade fixture: legacy `manifest.xml`/`bkupInfo.xml` backup rejected
   by the row's native `Import-GPO`.

A row family that cannot produce both MUST be marked `esu-required` or
`out-of-support` in the matrix and is excluded from `verified-rw` promotion.

---

## 6. Public matrix generator contract

This section describes the contract for `scripts/generate_public_matrix.py`.
Another workstream implements the script; this document does not.

### Inputs

The generator reads signed evidence packs only. A pack is signed when:

- its canonical-JSON SHA-256 is recorded in the pinned manifest (mirroring
  `docs/.evidence-report-sha256`); AND
- `redaction_verified == true`; AND
- `licensing_complete == true`.

### Hard refusals

The generator MUST refuse to process a pack when:

- `redaction_verified` is `false` (the identifier gate has not signed off).
- `licensing_complete` is `false` (a content item lacks a classification).
- The pack's hash is not in the pinned manifest (it is unsigned).

The generator MUST refuse to emit `verified-rw` for a capability unless at
least one record in a signed pack has `outcome == pass` for that capability
on the target row (§4 promotion rule). `expected_failure`, `fail`, `skip`,
and `not-present-on-target` produce the corresponding lower public claim
(`expected_failure`, `failed`, `pending`, `not-present-on-target`).

### Output

A public capability matrix showing ONLY claims backed by passing evidence.
The output mirrors the 1.0
[`capability-matrix.md`](../capability-matrix.md) Win-lab column legend
(`verified`, `expected_failure`, `not_validated`, `failed`, `pending`) and
adds the `classification` field from the evidence record.

### Philosophy

Local success alone is never release evidence. This mirrors the 1.0 release
evidence philosophy: a passing CI run, a passing local lab run, or a
placeholder observation never promotes a capability-matrix row. Only signed
evidence packs do, and only when their records carry `outcome == pass`.

---

## 7. Lab ownership, GPMC operators, disposable forests, least-privilege identities

### Roles

| Role | Identity | Privilege | Use |
|------|----------|-----------|-----|
| Routine GPMC operator | `gpstudio-lab` | Member of `Group Policy Creator Owners` and `Remote Management Users`; **NOT** Domain Admin | Routine fixture capture: `New-GPO`, `Set-GPRegistryValue`, `Backup-GPO`, `Get-GPOReport`. |
| Diagnostic operator | `svc-da` | Domain Admin | Diagnosis that requires Domain Admin (e.g. `Import-GPO` ACL capture, SYSVOL tree comparison, `Backup-GPO` against existing-GPO edge cases). Requires a documented justification per session. |
| Linux operator | operator on `mvmhermes01` | Unprivileged Linux account | Runs the `windows-evidence-lab` command boundary, assembles packs, runs the identifier gate, signs packs. |

### Least-privilege model

The 1.0 lab validated this model: the initial session used `gpstudio-lab`
(Group Policy Creator Owners + Remote Management Users, not Domain Admin) and
the follow-up diagnosis session used `svc-da` (Domain Admin) only because the
least-privileged account had been removed per cleanup policy. The ACL evidence
in [`release-evidence-report.json`](../release-evidence-report.json) records
both the delegated-permission subset and the recursive SYSVOL Modify
limitation inherent to the delegated model.

Routine work MUST run as `gpstudio-lab`. Domain Admin (`svc-da`) is reserved
for diagnosis that needs it, with a documented justification recorded in the
pack's `notes` field for every record captured under that identity. A pack
that contains records captured under Domain Admin MUST record that fact in
pack-level metadata; it does not invalidate the pack, but it is auditable.

### Disposable forest rules

- Destructive operations (delete-GPO, restore-into-existing, ACL mutation,
  schema-touching experiments) run on disposable `lab-*` forests, never on the
  `ad.hraedon.com` production-equivalent forest.
- `mvm*` hosts (including `mvmdc03`) are off-limits for destructive ops; only
  non-destructive import/report capture and the documented disposable-GPO
  pattern run there.
- Each disposable forest is single-tenant: one matrix row family per forest at
  a time. Forests are torn down and rebuilt between row families when
  cross-contamination would taint evidence (e.g. WS2022 ADMX residue in a
  WS2025 DC forest).
- Every GPO created in a disposable forest uses a unique random name and is
  cleaned up in `finally` paths, mirroring the 1.0 lab discipline.

### Operator-box boundary

The `windows-evidence-lab` command boundary is the only sanctioned path from
the Linux operator box (`mvmhermes01`) to the Windows targets. It carries the
PowerShell remoting credentials, the denylist secret
(`GPO_STUDIO_FORBIDDEN_IDENTIFIERS`), and the signing step. It was
live-qualified on 2026-07-18 against `ad.hraedon.com` / `mvmdc03`. A pack
captured through any other path is unsigned by definition.
