# Plan 033 — Windows external-oracle validation and parity evidence

Status: active — WP-0 implementation in progress
Scope: prove Studio import, authoring, prediction, and export claims against
supported Microsoft tooling without allowing internally consistent round trips
to substitute for interoperability evidence
Depends on: Plans 023–031 correctness fixes
Review gate: **REVIEW AND REFINE — REQUIRED before any new capability is
promoted to Windows-verified or live-RW**

## Why this plan exists

Studio's Python round-trip tests prove that Studio can read its own output.
They do not prove that GPMC, Windows client-side extensions, `secedit`,
`gpresult`, `Import-GPO`, or `LGPO.exe` assign the same meaning to that output.

The validation program therefore uses independent Windows tooling as the
oracle, compares normalized semantics rather than raw formatting, and records
which system owns each piece of state:

- a GPO backup owns per-GPO policy content;
- AD owns GPO security descriptors and WMI-filter associations;
- site/domain/OU objects own links and block-inheritance state;
- the target computer and user tokens determine security filtering;
- endpoint processing is the final oracle for applied behavior.

No phase may pass by documenting an unexplained discrepancy as a known
limitation. A limitation is an acceptable result only when the affected
capability is explicitly downgraded to read-only, preserve-only, preview, or
unsupported in the capability matrix.

## Preconditions

Before starting external validation, resolve the known correctness defects:

- replace synthetic or incorrect GPP paths, roots, element names, CLSIDs,
  common-option placement, and action mappings with MS-GPPREF-compatible
  representations;
- encode Local Users and Groups in `Groups/Groups.xml` and scheduled/immediate
  task variants in the Microsoft-defined Scheduled Tasks structure;
- correct loopback merge precedence so computer-location user settings win
  conflicts;
- evaluate Apply Group Policy as a control-access right (`CR`) and replace
  synthetic delegation-right mappings with real AD access masks/schema GUIDs;
- make publication target selection truthful, remove comment-only operations
  from executable claims, and prove or withdraw idempotency claims.

The lab must be disposable or snapshot-backed, isolated from production,
reachable only with lab credentials, and have a tested cleanup/restore path.
Use least-privileged identities for normal runs and a separately audited
privileged identity only where the operation requires it.

## WP-0 — Evidence contract, boundary matrix, and harness

Implementation status (2026-07-26):

- run-level manifest parser and canonical hash: implemented;
- conservative XML normalizer v1 and regression corpus: implemented;
- owning-boundary matrix and JSON Schema: implemented;
- frozen environment spec (`docs/plan-033/environment-spec.md`): implemented;
- fixture recipe schema and synthetic recipes: implemented;
- Windows setup/collect/cleanup harness (`scripts/windows-oracle/`): implemented;
- snapshot-backed dry-run orchestrator (`oracle_harness.py`): implemented;
- live Windows lab dry run against frozen environment: **passed** 2026-07-26
  (run `live-dry-run-20260725192622-3233`, canonical hash
  `265cfadc0c692c2cbaa6e69b0306c9c6813746f0caae40352f6ba10fe950d3d0`).

### Work

1. Freeze the supported Windows Server/client builds, PowerShell edition,
   GroupPolicy module version, GPMC version, locale, and `LGPO.exe` hash.
2. Add an evidence manifest schema containing:
   - source commit and dirty-tree state;
   - fixture ID and generation recipe;
   - Windows/tool versions;
   - input/output SHA-256 hashes;
   - normalized semantic comparison;
   - command exit codes and relevant event IDs;
   - cleanup result;
   - capability-matrix row and resulting evidence state.
3. Define a versioned normalizer for generated UIDs, timestamps, backup IDs,
   attribute order, insignificant whitespace, case-only path differences, and
   Microsoft-added default attributes. Normalization must never discard typed
   values, actions, filters, extension GUIDs, unknown elements, or file paths.
4. Publish a boundary matrix for each tested field:
   - GPO backup content;
   - GPO AD object/security descriptor;
   - WMI filter object and association;
   - SOM link/block-inheritance state;
   - endpoint resultant state.
5. Build `setup`, `collect`, and `cleanup` scripts that use unique disposable
   names, fail on partial cleanup, and can be rerun safely.
6. Keep raw lab artifacts in the controlled evidence store. Commit only
   synthetic/sanitized fixtures and hashes that pass the identifier gate.

### Acceptance

- A dry run produces a complete evidence record and restores the lab snapshot.
- Every later assertion has exactly one named oracle and one owning boundary.
- Any unsupported normalization difference fails loudly.

## WP-1 — Native GPMC corpus and GPP adapter conformance

This work package has separate reader and writer lanes. Do not inject Studio
XML into an ad hoc directory and call it a GPMC backup.

### WP-1A — Native-origin reader corpus

1. In GPMC, create minimal native fixtures for each supported GPP item kind.
   Keep each adapter isolated for diagnosis, then add mixed-CSE fixtures.
2. Cover every applicable scope and variant:
   - computer and user scope where supported;
   - every action;
   - empty/default and non-default values;
   - all common options;
   - one item-level-targeting expression;
   - Unicode and XML-sensitive values;
   - unknown attributes/children for preservation tests.
3. Save with GPMC and capture a native `Backup-GPO` tree.
4. Import through Studio's public backup-import path, not `parse_estate`
   (`parse_estate` is for `gpo-lens-estate` JSON).
5. Compare the normalized Studio model field-by-field against an explicit
   expected semantic manifest.
6. Export without editing and prove opaque/unknown content is preserved
   according to the capability contract.

### WP-1B — Studio-origin writer conformance

1. First implement and pass WP-2's native backup-format gate.
2. Generate one Studio item per isolated fixture and a mixed fixture per CSE.
3. Import each generated native backup with `Import-GPO -BackupId ...` into a
   fresh disposable target GPO.
4. Open and edit the item in GPMC. Save, run `Backup-GPO`, and compare:
   - source Studio semantic model;
   - GPMC report/model after import;
   - Studio model after re-import.
5. Where safe and applicable, link the disposable GPO to a disposable OU,
   run `gpupdate`, and verify client-side processing through direct state and
   GroupPolicy operational event logs.
6. Test unsupported or intentionally divergent features as explicit negative
   cases. They must be blocked or preserved, never emitted under a synthetic
   format that Windows silently ignores.

### Acceptance

- Every matrix row has native-origin read evidence and Studio-origin
  `Import-GPO` evidence on every claimed target version.
- GPMC shows the correct item kind and typed values after import and save.
- Endpoint evidence exists for executable/high-impact adapters.
- Differences are limited to the documented normalizer.
- One adapter's failure cannot be hidden by success elsewhere in a 19-item GPO.

## WP-2 — Native GPMC backup-format gate

The current `gpmc_backup_bundle()` output is a deterministic Studio archive,
not a native Windows Server 2025 `Import-GPO` backup. The existing evidence
records that it lacks `Backup.xml`, uses the GPO GUID where native backups use
a distinct backup ID, and uses a non-native directory layout.

### Work

1. Capture native `Backup-GPO` trees for the supported Windows versions.
2. Implement or explicitly decline a native backup writer:
   - native `Backup.xml` schema and backup ID;
   - `{BACKUP_ID}/DomainSysvol/GPO/...` layout;
   - correct case-insensitive Windows paths;
   - required metadata and extension information;
   - distinct backup ID and GPO ID semantics.
3. Validate enumeration by `Import-GPO -WhatIf`.
4. Import into a fresh target with `-CreateIfNeeded`, then run `Backup-GPO`.
5. Compare policy content semantically and verify computer/user version state.
6. If native backup emission is declined, rename the current artifact so it
   cannot be mistaken for an importable GPMC backup and route Studio-origin
   validation through a separately verified publication adapter.

### Acceptance

- `Import-GPO` discovers the exact backup ID without a transplanted native
  manifest or hand-edited directory.
- Actual import succeeds on every claimed target version.
- Backup ID, GPO ID, side versions, extension metadata, and content paths are
  correct.
- Otherwise the capability remains explicitly non-native/preview.

## WP-3 — Security-template conformance

### Corpus

Use both:

- native `GptTmpl.inf` files captured from GPMC-authored GPO backups; and
- `secedit /export` outputs covering `securitypolicy`, `group_mgmt`,
  `user_rights`, `regkeys`, `filestore`, and `services`.

Include UTF-16LE/BOM input, continuation lines, comments, empty assignments,
`*SID` privilege principals, unknown sections, service security, registry/file
security, and Unicode.

### Work

1. Test byte decoding separately from `parse_security_template(str)`.
2. Parse each fixture and compare typed fields plus opaque content against an
   expected semantic manifest.
3. Format, run `secedit /validate`, and import into a fresh temporary security
   database with `secedit /import /overwrite`.
4. Export that database and compare normalized semantics.
5. Use `secedit /analyze` only to compare a database with system state; do not
   treat it as a syntax validator or require zero policy differences.
6. Do not run `secedit /configure` on a shared host. Any endpoint-application
   test requires a disposable snapshot and a generated rollback template.

### Acceptance

- `secedit /validate` and `/import` succeed for every emitted supported
  template.
- All typed settings and unknown/preserve-only content survive the eligible
  round trip.
- No test depends only on Studio parse-format-parse equality.

## WP-4 — Discovery execution and independent reconciliation

### Work

1. Parse every generated PowerShell script with the Windows PowerShell 5.1 AST
   parser before execution.
2. Execute forest, domain, site/subnet, OU, GPO, and principal-search scripts
   with a read-only lab identity.
3. Capture JSON without shell re-encoding where possible; separately test
   UTF-8 BOM, UTF-16LE, non-ASCII data, and PowerShell 5.1/7 encoding behavior.
4. Compare against independent oracles:
   - `Get-ADForest`, `Get-ADDomain`, `Get-ADReplicationSite`,
     `Get-ADOrganizationalUnit`, and `Get-GPO -All` where available;
   - direct LDAP queries with separately maintained commands;
   - known SID suffixes only as secondary checks.
5. Exercise pagination beyond 1,000 objects, multi-link `gPLink`, disabled and
   enforced links, block inheritance, escaped LDAP characters, empty results,
   inaccessible objects, and non-ASCII names.
6. Run principal search with injection-shaped input and prove it changes only
   the literal search term.
7. Feed the captured output through the public Studio import API and verify
   stored/retrieved equality.

### Acceptance

- Every script parses and executes without unexpected stderr.
- Counts, identities, parent relationships, link fields, and source metadata
  match the independent oracle.
- Truncation, access denial, and partial discovery are explicit model states,
  never silent success.

## WP-5 — Registry.pol Windows-client conformance

### Work

1. Generate separate `Machine/Registry.pol` and `User/Registry.pol` files.
   The hive is implied by side; never mix HKLM and HKCU records in one file.
2. Cover all claimed types, empty/default values, boundary integers, Unicode,
   delete-value/delete-key operations, and duplicate/conflicting records.
3. Run Studio parse/serialize properties, but treat them only as preflight.
4. On a snapshot-backed endpoint:
   - import the machine file with `LGPO.exe /m`;
   - import the user file through the corresponding LGPO user-policy path;
   - run policy refresh;
   - verify exact registry type/data through direct registry APIs;
   - inspect GroupPolicy operational events.
5. Separately place the files in a native GPO through the verified WP-2 or
   publication path, link to a disposable OU, and verify domain-policy
   processing. LGPO acceptance alone does not prove GPMC/SYSVOL integration.
6. Re-run the same operation and prove the declared idempotency behavior.
7. Restore the snapshot and prove cleanup.

### Acceptance

- Windows accepts both side-specific files without parse errors.
- Every claimed type/action produces the expected endpoint state.
- Domain GPO processing and LGPO processing both pass.
- Cleanup restores the pre-test state.

## WP-6 — Controlled RSOP and effective-rights oracle

Do not use whatever 3–5 GPOs happen to exist. Create a deterministic topology
with intentional conflicts so that every expected winner is known.

### Topology

Create disposable site/domain/parent-OU/child-OU scopes and synthetic lab
principals. Cover:

1. basic LSDOU and same-container link order;
2. disabled link and disabled GPO side;
3. block inheritance;
4. enforced links above a block;
5. Apply+Read allow, missing Apply, explicit deny, and group nesting;
6. WMI true, false, and evaluation-error outcomes;
7. loopback disabled, merge, and replace;
8. slow-link/safe-mode behavior for every capability Studio claims to model.

Each applicable scenario must contain conflicting user or computer registry
values so the winning setting—not merely the applied GPO set—is observable.

### Work

1. Collect computer-account token groups independently from the logged-on
   user's groups. `whoami /groups` from an interactive session is not the
   computer security token; use LDAP `tokenGroups` or an audited SYSTEM-context
   collection for the computer.
2. Collect user token groups separately, including nested and deny-only cases
   where representable.
3. Run `gpupdate /force`, collect `gpresult /x` for computer and user scopes,
   `Get-GPResultantSetOfPolicy`, direct winning registry values, and relevant
   GroupPolicy operational events.
4. Run `compute_rsop()` from the exact captured SOM, GPO, WMI, and token inputs.
5. Compare:
   - applied and denied GPO sets;
   - link/application precedence;
   - filtering reason where Windows exposes it;
   - winning GPO and effective value for every conflict;
   - loopback source and winner.
6. Validate delegation/effective-rights results against `Get-Acl`,
   `Get-GPPermission -All`, and real object-specific `CR`, `RP`, `WP`, `WD`,
   `WO`, `SD`, and `CC` ACEs.

### Acceptance

- Applied/denied sets and every observable winning value match Windows.
- Loopback merge proves computer-location user settings win conflicts.
- Real Apply Group Policy `CR` ACEs are recognized.
- There are zero unexplained discrepancies in supported behavior.
- An unresolved discrepancy blocks predictive/publication gating or
  downgrades the affected capability.

## WP-7 — Boundary-correct lifecycle round trip

### Per-GPO content lane

1. Author a native GPO containing every supported per-GPO CSE family.
2. `Backup-GPO`.
3. Import through `read_backup` and the public backup-import API.
4. Re-export through the verified native writer or publication adapter.
5. `Import-GPO` into a fresh target, then `Backup-GPO` again.
6. Compare normalized per-GPO semantics and unknown-content preservation.

The backup importer must discover all supported GPP paths; it currently scans
only `Groups/Groups.xml` and `Registry/Registry.xml`, so expanding
`parse_gpp_collection()` alone is insufficient.

### External-state lane

Capture, model, apply, and verify separately:

- GPO DACL/security filtering;
- WMI filter object plus GPO association;
- site/domain/OU links, order, enabled/enforced flags;
- block inheritance;
- principal reconciliation/migration mappings.

Do not fail a per-GPO backup for omitting external SOM state, and do not claim
that a backup round trip preserved state that was actually reconstructed by a
different adapter.

### Acceptance

- Every field in the boundary matrix is verified through its owning adapter.
- Eligible unknown content survives a no-edit lifecycle byte-for-byte.
- Edited supported content is semantically equal after native re-backup.
- External-state writes are stale-checked, audited, and rollback-tested.

## WP-8 — Integrated certification, negative cases, and repeatability

### Work

1. Run a mixed-CSE estate through discovery, import, edit, review, native
   export/publication, endpoint processing, RSOP, backup, restore, and cleanup.
2. Exercise failure injection:
   - malformed/oversized XML and INF;
   - wrong CLSID or missing extension registration;
   - corrupt/missing backup metadata;
   - stale AD/SYSVOL version;
   - replication delay and partial write;
   - unavailable DC/share;
   - unauthorized principal and expired approval;
   - cpassword/secret and executable-artifact denial.
3. Prove rollback from each partial-failure boundary.
4. Repeat the full suite from a clean snapshot on every claimed Windows
   version and again from the same snapshot to expose nondeterminism.
5. Have an independent reviewer reconcile the evidence manifest with the
   capability matrix.

### Acceptance

- Two consecutive clean-snapshot runs produce identical normalized outcomes.
- Cleanup and rollback pass in every success and injected-failure case.
- No capability is marked verified without its native-origin, Studio-origin,
  and endpoint evidence where applicable.
- Independent review has no unresolved critical/high findings.

## Sequencing

```text
WP-0 evidence contract
 ├─ WP-3 security templates
 ├─ WP-4 discovery
 └─ WP-2 native backup gate
      ├─ WP-1 GPP writer conformance
      ├─ WP-5 Registry.pol domain lane
      └─ WP-7 lifecycle round trip

WP-4 discovery + verified policy application
 └─ WP-6 controlled RSOP/effective rights

WP-1 through WP-7
 └─ WP-8 integrated certification
```

WP-1A native-origin reader fixtures can start after WP-0 and run in parallel
with WP-2. WP-1B cannot start until the native backup/publication boundary is
verified. WP-3 and the LGPO-only portion of WP-5 can also run in parallel.

## Planning guidance

Do not estimate this as six sessions. A defensible first-target validation is
roughly 8–12 focused lab sessions after correctness fixes, plus implementation
time for defects found. Multi-version qualification and independent review are
additional work. Each session should close one evidence tranche and leave the
lab clean rather than maximizing the number of features touched.

## Promotion rule

The evidence state for a capability is one of:

- `unit-verified`
- `native-origin-read`
- `windows-imported`
- `endpoint-applied`
- `predictive-verified`
- `preserve-only`
- `preview`
- `unsupported`

Only evidence produced by this plan can promote a new Plans 023–031 capability
beyond `unit-verified`. Marketing/UI/API language must display the lowest state
across every required boundary for that capability.
