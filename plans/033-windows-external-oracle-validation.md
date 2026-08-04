# Plan 033 — Windows external-oracle validation and parity evidence

Status: active — WP-0/WP-2 certified; WP-1A genuine GPMC canaries landed;
WP-1B automated writer lane passes all seven candidates from corrected clean
source, including Services after WI-024; daily Exec TaskV2 and OS-filter
endpoint paths are certified. The WP-3 account/audit/user-rights writer tranche
is certified, with its broader corpus and areas plus the WP-1B manual GPMC
edit/save leg remaining. Services is GPMC writer-conformance certified, not
endpoint-applied.
The remediation scenario corpus for the Plans 025–032 divergence landed
2026-07-29 (13 scenarios across gpp-services, security-template,
rsop-topology, and ilt-os, plus the machine-readable test-platform registry —
data and validator only, nothing oracle-executed; see
`docs/plan-033/remediation-corpus.md`)
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

Implementation status (2026-07-26, post-hardening):

- run-level manifest parser and canonical hash: implemented;
- conservative XML normalizer v1 and regression corpus: implemented
  (now also normalizes GPO-report `CreatedTime`/`ModifiedTime`/`ReadTime`);
- owning-boundary matrix and JSON Schema: implemented;
- frozen environment spec (`docs/plan-033/environment-spec.md`): implemented
  and enforced by the parser's `pass` gate (full environment + dirty source);
- fixture recipe schema and synthetic recipes: implemented (the parser rejects
  not-yet-supported recipe features — `delete`, links, security filters, WMI
  filters — and enforces `side`/`hive` consistency);
- two-phase Windows harness: `scripts/windows-oracle/run-evidence.ps1` captures
  genuine raw evidence (real stdout/stderr/exit codes, real environment) on the
  domain-joined host; `scripts/windows-oracle/finalize_oracle_run.py` runs where
  the git repository lives and is the single authority for source provenance,
  semantic normalization, comparison binding, and the final evidence state;
- snapshot-backed dry-run orchestrator (`oracle_harness.py`): implemented;
- live Windows lab runs against the frozen environment (2026-07-26): the
  success-path run is a certified **pass**. It carries the full integrity pack:
  the deployed harness scripts (`run-evidence.ps1`, `common.psm1`), the recipe,
  the control-plane orchestrator and the transport (`psdirect.ps1`) are hashed
  input artifacts bound to the recorded commit; every artifact and command
  stream rehashes intact via `verify_evidence_pack`; and the cleanup re-query is
  a strict `Get-GPO -All` probe with three outcomes (absent / present /
  query-error) and both streams recorded as command/artifact evidence. A
  deliberate fail-path run is parser-valid **fail** (real failed command, real
  stderr, successful strict cleanup re-query).

  **Re-certified on the disposable evidence estate, 2026-08-03**: run
  `live-synthetic-registry-basic-20260803213433-5325`, source commit `97bdaf9`,
  clean tree, manifest hash
  `76c79ba93152b59203383b1443b24b159d412bca5dd83775c33a3b8d891d4b3a`, committed
  at `docs/plan-033/wp0-evidence/manifest-estate.json` and preserved by the tag
  `evidence/live-synthetic-registry-basic-20260803213433-5325`. The July
  certification on `mvmcitest01` (commit `000f1b5`, hash
  `0751b39667c982784af7f0a221fe193a1fa7ba5d84f601c8c71147aacdfabee9`) is
  superseded: its commit is a squash-merge orphan, so its integrity pack could
  not be re-verified against anything.

Note: the previously cited hashes
`265cfadc0c692c2cbaa6e69b0306c9c6813746f0caae40352f6ba10fe950d3d0` (predates the
comparison-to-artifact binding checks),
`930d37fca9aa7a314c7d40aeb2bf3d984ac114e4581d0df43663a624db901d19` (inconclusive;
dirty source),
`91dd0232d207220d8092fddcb7096777f8bd828deca7c862372ff61da1ade990` (genuine pass
but predates the integrity pack), and
`6d4b91b229a08e99a9851b5cb894f8f587bea577937b81dbec46754c1f3e1f47` (integrity
pack but predates the strict cleanup probe, launcher binding, recorded-commit
enforcement, and verifier hardening) are all superseded by the certified pass
above.

Note (resolved 2026-08-03): the July pass was produced through a disposable
scheduled-task harness that passed the credential via `schtasks` argv —
acceptable for that isolated lab but not a model to promote. It is gone. Every
lane now runs over PowerShell Direct against the disposable evidence estate,
which carries the credential to the guest through the hypervisor and needs no
launcher, so no lane puts a password in a task registration. The forward
dependency on the agent-capability-broker Plan 008 WI-3.2 Windows
authenticated-launch boundary is therefore no longer blocking anything here;
credentials already arrive through a composed acb checkout.

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

- A dry run produces a complete evidence record and restores the lab state (verified by re-query of all created resources).
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

Implementation status (2026-07-26, genuine canaries landed):

**Genuine GPMC-authored canaries** (4 fixtures) captured on mvmcitest01
(WS2025 build 26100) via the Group Policy Management Editor GUI, then
`Backup-GPO`. GPMC recognition verified: gpreport.xml contains
ExtensionData, side versions non-zero (917518 / 524296 / 262148).
Note: Backup.xml "Unknown Extension" is Backup-GPO's generic label for
the GPP CSE — it appears for genuine captures too and does NOT indicate
non-recognition.

**Synthetic SYSVOL-injection diagnostics** (3 fixtures, retained):
hand-injected XML collected by Backup-GPO. GPMC did not author them.
Useful as parser behavior probes and WP-2 container specimens.

**Deliverables:**
- `tests/fixtures/native-gpp-gpmc/` — genuine sanitized backups with
  semantic manifests and sanitization-record.json
- `tests/fixtures/sysvol-injection-diagnostics/` — synthetic diagnostics
- `tests/test_wp1a_genuine_fixtures.py` — 57 passed
- `tests/test_wp1a_native_fixtures.py` — 18 passed, 0 xfailed (the strict
  xfails that encoded gaps 1–3 and 5 now pass and were removed)
- `docs/plan-033/semantic-manifest-v1.schema.json` — manifest schema
- `scripts/plan-033/sanitize-gpp-fixtures.py` — recorded sanitizer
- `docs/plan-033/wp1a-gpmc-authoring-guide.md` — GPMC Editor guide

**Confirmed genuine-vs-synthetic divergences:**
- GPMC emits `<TaskV2>` (confirmed), Task schema version 1.2 (not 1.3)
- ImmediateTaskV2 action is Create only (GPMC constraint)
- Drive thisDrive/allDrives = "NOCHANGE" (not "USE"/"0")
- GPMC omits false common-option attributes (not always emitted)
- FilterRunOnce has hidden="1"; FilterOs coexists after promotion
- Group Properties emits groupSid + groupName + newName=""
- Principal uses %LogonDomain%\%LogonUser% variables, InteractiveToken

**Remaining for full WP-1A corpus:**
- 3-adapter canary (Drives, Groups, ScheduledTasks) is approved
- Full 19-adapter corpus pending additional GPMC sessions
- Every action/scope/common-option combination per plan step 2
- Unicode and XML-sensitive values
- Deliberate unknown attributes/children for preservation tests
- Mixed-CSE fixtures
- Public backup-import path integration tests
- Unknown-content import/export/preservation proof (blocked on WP-2)

**Critical gaps found (1–3 and 5 resolved; 4 mitigated, not modelled; 6 by
design):**

1. **Layout mismatch**: Native GPMC uses `{BACKUP_ID}/DomainSysvol/GPO/
   {Side}/Preferences/...`; Studio's `read_backup` + `collect_gpp_collections`
   expect `{GPO_GUID}/{Side}/Preferences/...`. This is the WP-2 gate.
   **RESOLVED**: collect_gpp_collections now discovers all GPP adapter files.
2. **File discovery**: `collect_gpp_collections` (import_export.py:96) only
   reads `Groups/Groups.xml` and `Registry/Registry.xml`. Drives, Services,
   ScheduledTasks, and all other adapter files are not discovered.
   **RESOLVED**: collect_gpp_collections now discovers all GPP adapter files.
3. **TaskV2 vs Task**: `parse_gpp_scheduled_tasks` searches for `<Task>`
   elements (gpp_adapters.py:2368) but native GPMC emits `<TaskV2>`.
   Zero scheduled task items are found in native fixtures.
   **RESOLVED**: parser now searches for both `<TaskV2>` and `<Task>` elements.
4. **Task model flattening**: `GppScheduledTask` uses scalar fields that
   cannot represent the Task Scheduler 2.0 XML (multiple triggers, actions,
   principals, settings, registration info).
   **MITIGATED, NOT RESOLVED**: a `task_xml` field preserves the native XML
   verbatim, which is sufficient for the WP-1A *reader* lane. It is not
   sufficient for the WP-1B *writer* lane: a task with multiple triggers or
   actions cannot be authored through the scalar fields. Decide before
   generating writer fixtures whether the contract is preserve-only (author
   simple tasks, round-trip complex ones opaquely) or whether Task Scheduler
   2.0 gets a real model.
5. **GppGroup vs GppLocalGroup**: `parse_gpp_collection` routes `<Group>`
   elements to `parse_gpp_groups` (producing `GppGroup`), not to the
   `local_groups` adapter. `collection.local_groups` is always empty via
   the normal parse path.
6. **FilterRunOnce promotion**: `FilterRunOnce` ILT predicates are promoted
   to `common.apply_once` and removed from the ILT filter (by design).

### WP-1B — Studio-origin writer conformance

Implementation status (2026-07-30): **the automated writer lane passes all
seven candidates from corrected clean source. WI-018 and WI-021 are fixed and
endpoint-verified. WI-024 corrected and recertified the capture-invalidated
Services delay semantics; Services is not endpoint-applied.**

The candidate set is one Studio-authored native backup per isolated family
(`registry-both` as the WP-2 control, `drives-user`, `groups-machine`,
`localusers-machine`, `scheduledtasks-machine`, `services-machine`) plus
`mixed-all`. Each is
imported into its own disposable GPO, so no adapter's result can be hidden by
another's. Certified run `wp1b-writer-20260730164352-5286`, source commit
`716f43c`, passed from a clean tree against Windows Server 2025 build 26100 /
GroupPolicy module 1.0.0.0 / en-US; the verdict is stored at
`docs/plan-033/wp1b-evidence/verification.json`.

**Estate qualification run (2026-08-03).** The lane was re-pointed onto the
disposable evidence lab over PowerShell Direct and re-run: certified run
`wp1b-writer-20260803014047-4766`, source commit `b3aba79`, clean tree,
`transport: psdirect`, all **seven candidates pass**, verdict at
`docs/plan-033/wp1b-evidence/verification-estate.json` and source tree preserved
at tag `evidence/wp1b-writer-20260803014047-4766`.

WP-1B was chosen as the estate's qualification lane because its candidates
already passed on the historic host, so the re-run changes exactly one variable:
same inputs, same expected results, new environment. The environment matched the
frozen profile on every gated field without amendment (build family 26100,
PowerShell 5.1.26100, GroupPolicy 1.0.0.0, en-US), so this qualifies the estate
rather than redefining what qualification means. The lane ran with **no
scheduled-task launcher** -- see the transport note in `environment-spec.md`.

Two transport defects were found and fixed by this run, both invisible to any
test that does not move real evidence: `Compress-Archive` cannot encode the
timestamps that `Copy-Item -FromSession` invents for a copied directory, and it
silently skips hidden files -- which is every `manifest.xml` and `bkupInfo.xml`
in a `Backup-GPO` tree. The second reported success while dropping 14 of 146
files, and surfaced as seven candidates failing on a missing manifest. The pull
now counts what it delivers against what the guest packed.

Results:

- **pass** — `registry-both`, `drives-user`, `groups-machine`,
  `localusers-machine`, `scheduledtasks-machine`, `services-machine`, and
  `mixed-all`. Each cleared `Import-GPO -WhatIf` without creating the target,
  real `Import-GPO`, GPMC report semantic equality, `Backup-GPO` re-export
  semantic equality, native shape comparison, and strict cleanup re-query.
  GPMC declared `ServiceSettings` for both the isolated Services candidate and
  the mixed candidate.

The earlier seven-candidate run (`wp1b-writer-20260730151953-6878`, source
`b4b9049`) remains in git history and is superseded because WI-024 invalidated
its Services delay semantics. The prior six-candidate certified run
(`wp1b-writer-20260727200636-4528`, source `8d9872e`) is likewise superseded.
The initial run (`wp1b-writer-20260727143434-7491`, source `83fe9b8`) remains in
git history as the evidence that exposed WP-1B-1: its two scheduled-task
candidates failed only the native-shape gate. The current verdict supersedes it
for branch-tip certification rather than rewriting that historical result.

Two methodological points came out of building this lane and both are load
bearing for how the remaining work packages should be read:

1. **`Import-GPO` copies GPP files to SYSVOL byte-for-byte.** A
   Studio → import → `Backup-GPO` → Studio round trip therefore proves only
   that the payload survived, not that GPMC or the CSE understood it. The
   decisive check is `summary_from_gpmc_report`, which parses GPMC's *own*
   report through Studio's GPP parser so that agreement is between two
   independent readers of one policy. Registry stays covered separately by the
   harness's `Get-GPRegistryValue` readback, since GPMC renders registry policy
   in Administrative Templates form.
2. **A round trip cannot detect a synthetic shape at all.** GPMC echoes back
   attributes it does not act on. Shape conformance is therefore checked
   against the captured native corpus directly
   (`writer_conformance.native_shape_findings`), not inferred from a round
   trip.

#### Finding WP-1B-1 — RESOLVED: non-native Task Scheduler 2.0 shape

The initial scheduled-task serializer wrote the Task Scheduler **1.0** scalar
attribute set (`program`, `arguments`, `startIn`, `triggerType`, `triggerTime`,
`triggerDays`) onto a **`TaskV2`** element, and embeds a `<Task>` payload only
when `task_xml` is populated — which the authoring path never set. Genuine
GPMC `TaskV2` items captured in `tests/fixtures/native-gpp-gpmc` are the exact
inverse: `Properties` carries only `action`/`name`/`runAs`/`logonType`, and the
actions and triggers live in an embedded `<Task version="1.2">` payload.

GPMC's report echoes Studio's scalar attributes back unchanged, so the item
survives import, report, and re-export intact. That is precisely the
"synthetic format that Windows silently ignores" case step 6 forbids, and it is
invisible to every round-trip check.

Phase 1 proved the Scheduled Tasks CSE does **not** honour that scalar form.
Phase 3 then proved the corrected writer creates a task with the expected
action. The writer now synthesizes an embedded payload, supplies GPMC's
scope-specific identity default, and emits an ISO 8601 `StartBoundary`.

This resolves the defect for the measured daily Exec path; it does not promote
the whole Scheduled Tasks family. Multiple triggers, `ImmediateTaskV2`,
non-Exec actions, and the `at_logon`/`at_startup` scalar forms remain outside
the measured authoring surface.

#### Step 5 endpoint evidence — EXECUTED 2026-07-27

Run `endpoint-20260727163558` on mvmcitest01 (WS2025 build 26100). A disposable
child OU containing only the target machine, a disposable GPO linked to it, a
verified policy refresh, then direct observation of created scheduled tasks.
Lab confirmed clean afterwards on all three DCs. Verdict at
`docs/plan-033/wp1b-evidence/endpoint-result.json`.

Six items, each varying one thing against a control:

| | task shape | ILT filter | result | |
|---|---|---|---|---|
| A | Studio scalar | none | **absent** | WI-018 |
| B | genuine GPMC | none | present | control |
| C | genuine GPMC | Studio `FilterOS`, excluding | absent | WI-021 |
| D | genuine GPMC | genuine `FilterOs`, excluding | absent | control |
| E | genuine GPMC | Studio `FilterOS`, matching | **absent** | WI-021 discriminator |
| F | genuine GPMC | genuine `FilterOs`, matching | present | control |

**WI-018 confirmed.** A Studio-authored scheduled task is *inert*. B proves GPP
tasks work on this host in the same CSE pass, so A's absence is attributable to
the emitted shape.

**WI-021 confirmed, with the impact direction inverted.** The plan's own
prediction was over-application — an unrecognised filter being ignored so the
item applies everywhere. E vs F disproves it: F shows a correctly-shaped
*matching* filter does apply, while E, the same logical predicate in Studio's
shape, does not. Studio's OS filter fails closed in both polarities, so the item
applies **nowhere**.

Two methodological points worth carrying forward:

1. **Controls are not optional here.** The first attempt at this experiment
   returned all six tasks absent *including the control*, and was discarded as
   inconclusive rather than reported as confirming WI-018. Root cause was
   replication — the GPO and link were written to the PDC while the client read
   policy from a third DC. The harness now forces replication and polls
   `gpresult` until the client itself reports the GPO applied, refusing to
   sample otherwise. Without B, that run would have produced a false positive.
2. **An excluding filter alone is not discriminating.** "Task absent" is equally
   consistent with "the filter was honoured" and "the CSE could not parse it and
   failed closed". Only the negated pair (E/F) separates them.

Both defects share a failure signature: **silent no-op with success reported at
every layer.** The item imports cleanly, GPMC renders it as a typed item, it
survives `Backup-GPO` round trips, and the CSE logs event 5016 "Completed"
without error — while doing nothing. No layer surfaces anything an operator
could notice.

#### Step 5 phase 2 — the corrected OS filter, measured (2026-07-28)

Run `endpoint-20260728020216`, same harness, same disposable-OU scoping, lab
confirmed clean afterwards on all three DCs. Verdict at
`docs/plan-033/wp1b-evidence/endpoint-result-phase2.json`.

Phase 1's design could not test the WI-021 fix: once Studio emits a genuine
`<FilterOs>`, the "Studio vs native" pairs that made phase 1 discriminating
collapse into the same bytes. The question changed from *is the shape wrong?*
to *does the corrected filter get evaluated?* — and an always-present result
would be as damning as always-absent, since it would mean the filter is
ignored. Only a split result demonstrates evaluation.

| | filter | result | expected |
|---|---|---|---|
| A | none | present | present |
| B | Studio `FilterOs`, **matches** (`WINTHRESHOLDSRV`) | **present** | present |
| C | Studio `FilterOs`, **excludes** (`XP`) | **absent** | absent |
| D | Studio `FilterOs`, negated (`NOT XP`) | **present** | present |
| E | hand-written native `FilterOs`, excludes | absent | absent |
| F | Studio scalar `TaskV2`, no filter | absent | absent |

**Six for six.** B/C/D split on polarity against an otherwise identical item,
which is what shows the CSE genuinely evaluates the filter rather than ignoring
it or failing closed. Compare phase 1, where the Studio filter produced an
absent task in *both* polarities.

**WI-021 is therefore closed on measurement, not inference.** The earlier note
that "the new shape applies correctly is inferred from byte-equivalence, not
measured" no longer holds.

> Record-keeping note: WI-021's work-item body still carries that superseded
> caveat. `agent-notes` refuses to amend an item in a terminal state
> (`cannot amend terminal state 'done'`), and reopening a closed item to edit a
> note is worse hygiene than leaving the authoritative record here. **This
> section and `endpoint-result-phase2.json` supersede the work item's closing
> paragraph.** The same constraint applies to the WI-011 corpus facts recorded
> in `lab-session-2-runbook.md`.

Row F is a deliberate regression pin: WI-018 is still open, and Studio's scalar
`TaskV2` remains inert in the same run that proves the OS filter works — so the
two defects are confirmed independent rather than one masking the other.

#### Step 5 phase 3 — WI-018 fixed and verified (2026-07-28)

Run `endpoint-20260728024058`, verdict at
`docs/plan-033/wp1b-evidence/endpoint-result-phase3.json`. Lab clean on all
three DCs afterwards.

| | varies | result |
|---|---|---|
| A | no filter (control) | present |
| B | Studio `FilterOs`, matches | present |
| C | Studio `FilterOs`, excludes | absent |
| D | Studio `FilterOs`, negated | present |
| E | native `FilterOs`, excludes (control) | absent |
| **F** | **scalar-authored `TaskV2`** | **present** |
| G | F + explicit `runAs` | present |
| H | F + explicit ISO boundary | present |
| I | valid `runAs` + **bare-time** boundary | **absent** |

**WI-018 is fixed.** Row F — the row that has been absent since phase 1 — now
creates a task with the correct action.

Getting there took two rounds of bisection, because the CSE reports success and
logs nothing in every failing case. The first fix (synthesize an embedded
`<Task>` payload) was necessary but not sufficient, and there was no signal
saying so beyond the task's absence.

**Two independent causes, each isolated by varying one field:**

1. **Empty `runAs`.** Row G differed from F only in carrying an identity, and
   only G appeared. The writer now substitutes GPMC's scope defaults —
   `NT AUTHORITY\System` for computer, `%LogonDomain%\%LogonUser%` for user —
   both read from the native corpus rather than chosen by Studio.
2. **A bare-time `StartBoundary`.** Task Scheduler 1.0 stores a time of day;
   2.0 requires ISO 8601. Row I carries the pre-fix bare time *with* a valid
   identity and is still absent, so this was independently fatal.

Row I exists because the `runAs` bisect made the boundary fix look incidental —
H showed the 1970 date was fine, so the normalization had not been shown to
matter. Claiming it as a fix without measuring would have been asserting
something unmeasured. It turned out to be load-bearing.

**The StartBoundary defect is the sharpest illustration of why this lane
exists: it was introduced by *my own fix for WI-018*, passed all 2742 unit
tests, and was invisible to every round trip** — Studio's parser happily accepts
a bare time because Studio's writer produced it. Only Windows disagreed.

#### Remaining WP-1B work

- Step 4's "open and edit the item in GPMC, save" leg is **not** covered. The
  automated lane captures GPMC's report of the imported GPO, which proves GPMC
  parses the payload, but a GUI edit-and-save is what would rewrite the GPP XML
  through GPMC's own writer. That remains a manual gate.
- The daily Exec TaskV2 path has endpoint evidence. The broader Scheduled Tasks
  surface remains unpromoted until its additional variants have isolated
  writer and endpoint cases.
- Step 6 negative cases are covered for blocked-at-export families
  (`unsupported_native_gpp_extension`, `cpassword_detected` in
  `tests/test_conformance.py`) and now for synthetic shape, but not for
  intentionally divergent authored values.

#### Original work items

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

Implementation status (2026-07-27): **certified for Windows Server 2025
build 26100**. Studio now emits a distinct deterministic backup ID, native v2
`Backup.xml`, nested `bkupInfo.xml`, and the
`{BACKUP_ID}/DomainSysvol/GPO/...` layout. A fully Studio-generated,
registry-both-sides candidate passed `Import-GPO -WhatIf`, actual
`Import-GPO -CreateIfNeeded`, GroupPolicy registry readback, native
`Backup-GPO`, side-version reconciliation, and strict cleanup. The certified
clean-tree run was `wp2-native-import-20260726235913-9111`; every check in
the local finalizer passed, with `dirty: false`.

> **Evidence binding broken (found 2026-08-03) — re-certification queued.**
> This run cannot be verified from the repository. Its recorded source commit
> `c8b4fa8ed37a86ee…` is not a valid object here, and neither is `5e0a6df` from
> the superseded dirty-tree run: both were orphaned by squash-merge before the
> issue #22 remedy landed, and unlike the four commits rescued as `evidence/*`
> tags, WP-2's were already unreachable by the time that remedy ran, so they
> cannot be retro-tagged. WP-2 also committed **no evidence manifest** — it has
> no `docs/plan-033/wp2-evidence/` counterpart to `wp1b-evidence/` and
> `wp3-evidence/`, so its bindings survive only as the hashes quoted below.
>
> Consequence, stated plainly: the `harness_matches_source` claim below is not
> independently checkable, because the committed tree it compared against is
> gone. The WP-2 result is a **prose record, not a verifiable certification**.
> The implementation it describes is on `main` at `96f3aec`; that commit is
> real, but it is not evidence that the run happened against it.
>
> The capability-matrix rows for GPMC backup import/export continue to rest on
> this record pending re-certification on the lab estate, where the run will
> produce a committed manifest and an `evidence/` tag like every other lane.

Native GPP emission is deliberately limited to the extension profiles backed
by genuine GPMC captures: Drive Maps, Local Users and Groups, and Scheduled
Tasks. Other GPP families fail export with
`unsupported_native_gpp_extension` rather than guessing Windows metadata.
Security filtering, WMI association, and SOM links remain separate adapter
boundaries and are not encoded into the native backup manifest.

Before WP-2, `gpmc_backup_bundle()` emitted a deterministic Studio archive,
not a native Windows Server 2025 `Import-GPO` backup. The historical evidence
records that it lacked `Backup.xml`, used the GPO GUID where native backups use
a distinct backup ID, and used a non-native directory layout.

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

### Evidence and implementation

- Writer: `src/gpo_studio/export.py`
- Structural/native-fixture contract: `tests/test_native_backup.py`
- Candidate builder: `scripts/plan-033/build-wp2-candidate.py`
- Windows import/re-backup harness:
  `scripts/windows-oracle/run-wp2-import.ps1`
- Credential-bound orchestrator: `scripts/windows-oracle/run-wp2-oracle.sh`
- Hashing and semantic/version finalizer:
  `scripts/windows-oracle/finalize_wp2_import_run.py`

The 2026-07-27 clean-tree pass established:

1. The exact generated backup ID was enumerated without transplanted native
   metadata.
2. `-WhatIf` succeeded and an independent query proved that it created no GPO.
3. Actual import succeeded, and machine/user values matched through
   `Get-GPRegistryValue`.
4. Windows `Backup-GPO` reproduced both Registry.pol files byte-for-byte.
5. Machine and user AD/SYSVOL versions were non-zero and synchronized; packed
   re-backup versions matched the independent `Get-GPO` state.
6. Removal and strict GUID re-query confirmed restored state.

Evidence bindings for the certified clean-tree run:

- Studio candidate ZIP:
  `9af16e44aab4babbcf253419febf51756eeca9d2d9b5b5b11829916f4ef581ea`
- Windows result JSON:
  `899337bce139634ac9e35017c0a36ca51bfdfac6d6010f90bb5aeced2a9707a5`
- Executed Windows harness:
  `24f3a7174c0276a7c65f16f294667c6ccfb427bd18c3c25eba67a6982bf7505e`
- Final verification JSON:
  `51f9bf99709d4929e627af8d4abc66a1183e511498b54e321ecd94b8c634b330`

The run records source commit `c8b4fa8ed37a86eefc1c6886ed54e09619c55cb2`
with `dirty: false`. At the time it ran, the `harness_matches_source` check
compared the remotely deployed scripts (retrieved post-run via scp) — not
source-tree copies — against the committed tree, which is the property that
made this the release-certification pass.

That check can no longer be re-verified: `c8b4fa8` is not a valid object in
this repository (see the note under **WP-2** above), so the tree it compared
against is unavailable. The hashes listed here are retained as the historical
record; they are not independently checkable, and none of them binds to a
reachable commit. Treat this section as superseded once the queued estate
re-certification lands.

The prior dirty-tree run (`wp2-native-import-20260726212733-5804`, source
commit `5e0a6df`, `dirty: true`) is superseded. Its evidence hashes
(`6436304a…` result JSON, `b16f4c27…` verification JSON) were sufficient for
implementation closure but not for release certification. An intermediate
clean-tree run (`wp2-native-import-20260726224125-1346`, source commit
`90c1ef6`) is also superseded: its `harness_matches_source` check compared
source-tree copies rather than remotely deployed files, so it could not
detect deployment drift. That provenance defect was corrected before the
certified run above.

## WP-3 — Security-template conformance

**Expansion scoped 2026-08-04:** `docs/plan-033/wp3-expansion-design.md`. The
certified tranche covers three of the module's eleven sections, all of them
plain key/value or principal-list shapes. The six untouched sections split by
risk: `Kerberos Policy`, `Registry Values` and `Group Membership` need no new
comparison machinery, while `Registry Keys`, `File Security` and
`Service General Setting` carry SDDL, which Windows canonicalises -- so an exact
comparison would report a Microsoft normalisation as a Studio defect. Two cheap
measurements are named there that settle the question before any row is written.

Implementation status (2026-07-28): the byte codec and first Studio-origin
writer tranche are certified against the frozen Windows Server 2025 oracle.
`security_template.py` now emits and strictly decodes the MS-GPSB UTF-16LE/BOM
wire format, recognizes the required `[Unicode]` preamble, and normalizes line
endings to CRLF. The synthetic account-policy, event-audit, and user-rights
candidate passed `secedit /validate`, import with `/overwrite` into a fresh
temporary database, and export with no semantic differences. The harness
invoked only `validate`, `import`, and `export`; it never invoked `configure`.
Cleanup verified that neither the `.sdb` nor its ESENT `.jfm` companion
remained.

The clean-tree certified run is
`wp3-security-template-20260727220623-7682`, bound to source commit
`fdb46004c2f838f5b5eb6a693ebdf7f99d4ee71a`. Evidence is recorded in
`docs/plan-033/wp3-evidence/verification.json`; its SHA-256 is
`7400d1679fc665ca9f405ed3af7b55967b6cbdf47b0ce57a9c8960ac1ea46339`.
Candidate and Windows-export hashes are respectively
`27bea20d6141bf063918bfe939291b602d5b3a01916f732057d81acc9699ac83`
and
`bef55726c9af7b41807ccfe6ed27792d90b256a11553b9a4d4adc4e008fa564a`.

This certifies only the bounded account/audit/user-rights writer tranche. The
native GPMC/secedit corpus and the `group_mgmt`, `regkeys`, `filestore`, and
`services` areas remain open, so WP-3 as a whole is not complete and Plan 025
is not promoted to surfaced or live-RW.

The comparison is intentionally asymmetric. Studio's candidate must contain
the exact authored section/key set in its semantic manifest, so an unexpected
privilege or policy fails certification. The Windows export comparison is a
subset check because `secedit` can add defaults; every authored value must
survive, while additional Windows-owned defaults are retained as evidence
rather than attributed to Studio.

Evidence-policy follow-ups from the PR 19 review:

- ~~define a WP-3 environment qualification profile that matches the OS and
  PowerShell build family without failing on every servicing revision, records
  the exact revision, and does not gate on tools such as LGPO that this harness
  never invokes; document the explicit re-freeze step for a new build~~ —
  **done 2026-07-29** (PR #20, `f2a2f69`): builds qualify on OS/PowerShell family with
  the exact revision recorded per manifest, LGPO is recorded provenance rather
  than a gate, and the re-freeze rule for new families is documented in
  `environment-spec.md`;
- replace the hard-coded shared-host target and scheduled-task password
  transport with a dedicated disposable-host requirement before expanding
  this lane — **still open**, now scoped to the planned disposable
  `ad.labdomain.dev` three-VM estate named in `environment-spec.md`; the
  remediation corpus's security-template scenarios stay `blocked` on it.

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

**LGPO ruling, 2026-08-03: `LGPO.exe` is approved on the evidence estate, and
WP-5 keeps both legs.** The estate cannot fetch the binary, so WP-5 pushes it in
over `psdirect`. The lab guests are disposable, checkpoint-backed and isolated;
the isolation invariant is about egress, not about what is deliberately placed
inside. Three conditions attach:

- Verify the pushed binary against a pinned SHA-256 **on the guest**. Recording
  the hash of whatever arrived is provenance theatre, not verification.
- Stage it after checkpoint restore, so no golden checkpoint carries it and the
  estate stays reproducible from clean media.
- Restore `lgpo` to qualified in `platforms.json` only when this lane genuinely
  executes it. The 2026-07-29 de-gating exists because a `pass` was once gated
  on a binary no lane ran; qualification is earned by execution.

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

## WP-6 — Controlled RSOP and effective-rights oracle (computer scope)

**Status 2026-08-04: partially delivered.** WP-6A reconciled the platform
registry; WP-6B built the lane and certified `lsdou-precedence` over three
identical passes — the first external check `rsop.py` has ever had. It covers
LSDOU ordering, same-container link order and non-conflicting inheritance, and
nothing else: items 2, 5 and 6 of the topology below are untested, their corpus
scenarios being blocked or user-scope. Findings and the answered open questions
are in `docs/plan-033/wp6b-results.md`; WI-026 is open.

Do not use whatever 3–5 GPOs happen to exist. Create a deterministic topology
with intentional conflicts so that every expected winner is known.

**Scope ruling, 2026-08-03: WP-6 is computer-scope-only.** The user half needs an
interactive logon the estate has never had, and PowerShell Direct does not
provide one. Rather than stall the lane, user scope and loopback move to WP-9,
which is a committed follow-up and not an optional one. Nothing below may be read
as certifying user-side resolution, and no capability WP-6 certifies may be
recorded in `docs/capability-matrix.md` without the words *computer scope*.
See `docs/plan-033/rsop-oracle-design.md`, which measured the estate first.

**Status 2026-08-04 — partially executed. Item by item, because "WP-6 ran" is
not the same as "WP-6 is done":**

| # | topology requirement | state |
|---|---|---|
| 1 | LSDOU and same-container link order | **certified** (`lsdou-precedence`) |
| 2 | disabled link and disabled GPO side | **certified** (`disabled-block-enforced`) |
| 3 | block inheritance | **certified** (same) |
| 4 | enforced links above a block | **certified** (same — and it found WI-031) |
| 5 | Apply+Read, missing Apply, explicit deny, group nesting | certified for the user account (WP-9) **and for the computer account** (`computer-security-filtering`, 2026-08-04). **Group nesting is user-side only** — a computer's membership lives in its machine token, minted at boot |
| 6 | WMI true, false, and evaluation-error | **all three certified** — true/false by `wmi-filtering` (which found WI-035), evaluation-error by `wmi-filtering-error` (which found WI-039, the only undeclared finding this lane has produced) |
| 7 | slow-link / safe-mode | **not covered.** The fields are accepted and never read (WI-036), and the estate cannot classify a link as slow — capping the vNIC does not work, measured |

Work item 8 (validating effective rights against `Get-Acl`, `Get-GPPermission
-All` and real `CR`/`RP` ACEs) is **not** covered either. The lane reads the raw
DACL to verify *its own authoring* — which is how WI-033 was authored correctly
— but it does not compare Studio's effective-rights model against those ACEs,
and those are different claims.

So the certified region is LSDOU, link order, block inheritance, enforcement,
disabled links and sides, filtering on both principals, and WMI true/false.
What remains: **group nesting for the computer** (its token is minted at boot,
so a run-created group is not in it), item 7 entirely, and item 8.

### Topology

Create disposable site/domain/parent-OU/child-OU scopes and synthetic lab
principals. Cover:

1. basic LSDOU and same-container link order;
2. disabled link and disabled GPO side;
3. block inheritance;
4. enforced links above a block;
5. Apply+Read allow, missing Apply, explicit deny, and group nesting, as they
   apply to the **computer** account;
6. WMI true, false, and evaluation-error outcomes;
7. slow-link/safe-mode behavior for every capability Studio claims to model on
   the computer side.

Loopback (disabled, merge, replace) is deliberately absent — it is WP-9.

Each applicable scenario must contain conflicting **computer** registry values
so the winning setting—not merely the applied GPO set—is observable.

### Work

1. Collect computer-account token groups independently from the logged-on
   user's groups. `whoami /groups` from an interactive session is not the
   computer security token; use LDAP `tokenGroups` or an audited SYSTEM-context
   collection for the computer.
2. Run `gpupdate /force`, then collect `gpresult /x … /f /scope:computer`,
   direct winning registry values, and relevant GroupPolicy operational events.
   `Get-GPResultantSetOfPolicy` is **not available on the qualified client** —
   the `GroupPolicy` module is absent and RSAT is a Feature-on-Demand the
   isolated estate cannot fetch. Build on `gpresult.exe` alone.
3. Assert on artifacts, never on exit codes. `gpresult.exe` exits 0 while
   writing no file at all; a lane that trusts the exit code parses a missing or
   stale document and certifies it. Require the file to exist, to parse, and its
   `ComputerResults` to name the GPO the run applied.
4. Run `compute_rsop()` from the exact captured SOM, GPO, WMI, and token inputs.
5. Compare:
   - applied and denied GPO sets;
   - link/application precedence;
   - filtering reason where Windows exposes it;
   - winning GPO and effective value for every conflict.
6. Compute the prediction *before* applying anything, and commit it as an input
   artifact, so the prediction cannot be retrofitted to the observation.
7. Include at least one control row whose winning GPO is decided by a mechanism
   Studio does not model. Without it, "Studio predicted wrong" is
   indistinguishable in the evidence from "nothing applied" — the same
   vocabulary-control lesson the endpoint lane had to learn.
8. Validate delegation/effective-rights results against `Get-Acl`,
   `Get-GPPermission -All`, and real object-specific `CR`, `RP`, `WP`, `WD`,
   `WO`, `SD`, and `CC` ACEs.

### Acceptance

- Applied/denied sets and every observable computer-side winning value match
  Windows.
- Real Apply Group Policy `CR` ACEs are recognized.
- There are zero unexplained discrepancies in supported computer-scope behavior.
- Three outcomes stay distinct in the evidence and are never collapsed:
  prediction matched; prediction wrong (a finding about Studio); experiment did
  not run (inconclusive).
- Every capability this WP certifies is recorded in the capability matrix as
  **computer scope only**, with user-side and loopback shown as unverified
  pending WP-9.
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

## WP-9 — User-scope RSOP and loopback

**Status: executed 2026-08-04, acceptance partially met.** Three scenarios
certified `pass` against the estate's client
(`docs/plan-033/wp9-results.md`): `user-side-disabled`, `loopback-merge` and
`loopback-replace`. The interactive session is established from a checkpoint by
script, as work item 1 required. Outstanding against the acceptance criteria
below: user-side **security filtering** and separate **token-group collection**
(work items 2 and 3's filtering half) are not covered, so the capability matrix
keeps a coverage qualifier even though the scope qualifier is gone. A second
run from the checkpoint reproducing a result has been demonstrated for the lane
as a whole, not yet as a scripted acceptance step.

Created 2026-08-03 when WP-6 was ruled computer-scope-only. This work package
exists so that decision is a deferral with a name attached rather than a quiet
reduction in what the project claims to have validated. Until it runs,
`rsop.py`'s user-side and loopback behavior is an unverified claim and the
capability matrix must say so.

The blocker was never policy — an interactive logon on the disposable estate is
approved. The blocker is that the estate has never had one, and PowerShell
Direct does not provide one.

### Work

1. Establish a reproducible interactive session on `LabCL01`: script an
   autologon for a synthetic lab principal and take a dedicated checkpoint, so
   the logged-on state is restorable rather than hand-made. A hand-made logon
   makes the lane unrepeatable, which fails WP-8's repeatability requirement.
2. Collect user token groups separately from the computer token, including
   nested and deny-only cases where representable. Do not reuse the computer
   token collection; conflating the two is the specific error WP-6 work item 1
   exists to prevent.
3. Author user-side conflicts: link order, security filtering on user
   principals, and conflicting HKCU values so the winning setting is observable
   rather than only the applied GPO set.
4. Capture `gpresult /x … /f /scope:user` under that session, with the same
   artifact-based assertions WP-6 uses — the exit code proves nothing, and the
   silent-empty-file failure is *how this trap first appeared*, on a
   user-scope invocation.
5. Cover loopback disabled, merge, and replace.
6. Diff against `compute_rsop()` predictions committed before application.
7. If `Get-GPResultantSetOfPolicy -User` from `LabMS01` proves reachable across
   the private switch, use it as a second independent oracle. Treat it as a
   bonus: it is untested, and this project has already been burned once by
   designing a lane against an unmeasured transport.

### Acceptance

- User-side applied/denied sets and every observable winning HKCU value match
  Windows.
- Loopback merge proves computer-location user settings win conflicts.
- Loopback replace proves user-location settings are discarded entirely.
- The interactive session is established from a checkpoint by script, and a
  second run from that checkpoint reproduces the result.
- The capability matrix is updated to drop the *computer scope only* qualifier
  from every capability this WP verifies — and only those.

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
 └─ WP-6 controlled RSOP/effective rights (computer scope)
      └─ WP-9 user scope + loopback

WP-1 through WP-7
 └─ WP-8 integrated certification
```

WP-1A native-origin reader fixtures can start after WP-0 and run in parallel
with WP-2. WP-1B cannot start until the native backup/publication boundary is
verified. WP-3 and the LGPO-only portion of WP-5 can also run in parallel.

WP-9 does not gate WP-8. WP-8 certifies what WP-1 through WP-7 verified, and
computer-scope RSOP is what WP-6 verified — so an integrated certification can
complete while user-scope RSOP remains outstanding, *provided* the matrix
carries the scope qualifier. If that qualifier is ever dropped without WP-9
having run, WP-8's certification becomes an overclaim.

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
