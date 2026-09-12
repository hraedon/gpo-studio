# Changelog

All notable changes to GPO Studio are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Current version: `1.0.0`.

## [Unreleased]

- A cross-lineage read of the fdeploy reader before it merged found four things
  worth keeping the record of, because three were self-inflicted. A `Flags`
  value of more than 4300 digits reached `int()`, which refuses that conversion
  and raises a bare `ValueError` -- a 10 KB request returned 500 from a handler
  whose own docstring says this surface is never a server fault; the digit
  pattern is bounded now. `is_marker` was "no sections", so a file of prose was
  reported as the empty marker GPMC writes, validated clean, and described in a
  report line that asserted Windows wrote it -- three untrue statements about a
  native artifact, now one honest one. `validate_fdeploy` was quadratic and its
  result unbounded: one in-cap document took 4.19s and produced a 5.4MB answer,
  and takes 0.045s for a capped one now.
  The fourth is the useful one. `format_fdeploy` returned its input whenever a
  reparse matched, so `format(parse(t)) == t` was `t == t` and both round-trip
  tests passed against a parser mutated to return no sections at all -- exactly
  the self-consistency AGENTS.md rejects, in a test whose docstring cited
  WI-064 to claim otherwise. The serializer now always rebuilds from the parsed
  document, which is why the document carries the file's preamble and each
  section's verbatim lines; the same mutation now fails twelve tests.

- WI-069: the estate repair that blocks the 22nd lane is an item now, not a
  paragraph. It had no number while three open items (WI-063, WI-064, WI-065)
  waited on the estate run it blocks, and the only account of the failure was
  one paragraph in the WI-062 batch note that four other documents pointed
  back at. `docs/plan-033/estate-clock-dns-repair.md` adds the capture plan and
  `scripts/plan-033/collect-dc-clock-dns.ps1` the read-only collector -- three
  phases, LDAP node state and replication metadata rather than resolver
  answers, the scavenging and aging settings as configured rather than as
  remembered. Neither was run: this host has no lab credential capability
  provisioned, so the transport is unavailable from it.
  It also records the step nobody took. That the DC-locator records were
  *deleted* was concluded through a resolver, and absent, tombstoned and
  present-but-unserved are one symptom from there and three different findings
  over LDAP -- the third being plausible on exactly the machine whose clock has
  just jumped. The account may still be right; the step between symptom and
  mechanism was never taken, and it is one read.

- Plan 034 WP-4 is ruled: Folder Redirection is a **read target**, with the
  writer deferred behind R12
  ([the decision](docs/scope-decision-2026-09-11-folder-redirection.md)). The
  read half is `src/gpo_studio/fdeploy.py` -- a strict UTF-16LE/BOM codec, a
  lossless parse, structural validation, review rendering and a diff keyed on
  `(folder GUID, principal)` -- reachable at
  `POST /api/folder-redirection/fdeploy`. Before this, `fdeploy1.ini` reached
  an operator as a 458-byte SHA-256 in the unmodeled-file inventory, and the
  module named `folder_redirection.py` addressed neither it nor the marker
  beside it.
  Four limits ride in the module and in every response rather than in a
  document: `Flags` is carried as the integer Windows wrote and **no bit is
  named** -- one capture is one observation of a ten-bit word (WI-066); one
  capture is also one shape, so multi-folder and multi-principal documents are
  unmeasured; twelve of the thirteen folder names are documented Windows
  constants no lane has measured, and an unrecognised GUID reports null rather
  than a guess; and there is no writer. No lane has read this artifact in either direction, so the banked
  R3 capture is doing a lane's job: the reader is tested against bytes
  hash-bound to what GPMC wrote, which is stronger than a round trip through
  our own output and weaker than a verdict.
  The parse reaches no `GPO`, so an imported backup's reports and diffs are
  unchanged -- that field lands in `model.py`, which two live verdicts bind
  (WI-068, filed against the batch that owes WI-063 through WI-065).
- WI-067: the R3 fixture's provenance called
  `{FDD39AD0-238F-46AF-ADB4-6C85480369C7}` the Folder Redirection CSE GUID. It
  keys the redirected *folder*; the CSE GUID is
  `{25537BA6-77A8-11D2-9B6C-0000F8080861}`, which is what R6's census counted
  and which appears in neither captured file. The repository already
  contradicted itself two paragraphs apart and nothing reconciled it because no
  code read the file. The constant is renamed and the provenance record carries
  a dated correction rather than a silent rewrite; the upstream envelope is
  still wrong, which is what keeps the item open.

- Plan 034 WP-3: `policy_families.py` is reachable. `POST /api/security-template/
  policy-families` renders the account, audit, user-rights and security-options
  families as a `GptTmpl.inf` -- text for reading, UTF-16LE/BOM/CRLF bytes for
  writing -- in the emission direction its member and DC lanes certified
  (21/21 each at `4e27f27`) and no further. It does not parse a template back:
  that direction reaches its oracle only through a GPMC snap-in and no lane
  certifies it. Every response carries the three limits the lane did not reach
  -- `/configure` is never invoked, one tranche of values was measured, and
  GPME editing is unmeasured -- and a member-server render omits the Kerberos
  section, which is what the lane's finalizer requires. The second layer to
  leave the unproven-draft set after `rsop.py`, and the first from Plan 025,
  whose three other modules remain in it.
  The composition lives in `api.py` because the serializers, their codec and
  the candidate builder are all in the verdicts' bound file set;
  `tests/test_policy_family_surface.py` holds it equal to the certified
  builder's in both scopes rather than letting a second composition drift.
- WI-066: R3 answered one of the four questions it was designed to answer. Its
  request authors two folders -- Documents in Basic with three non-default
  options, and Pictures in Advanced with two groups at defaults, the second
  existing expressly so the first could be read against it. Step 4 was never
  authored, so the banked capture is one folder, one principal and one
  `Flags=1021`: nine bits set against four modelled booleans, with no control.
  Nothing was misrecorded -- the result was entered against the scope-changing
  question R3 was asked, which it answers emphatically, and the binding table
  had no column for what a capture did not settle. R3's row now says. R12 is
  step 4 re-requested, plus a third folder that makes one flag bit derivable
  rather than merely constrained; it is a console session, not a lane. Checked
  while filing: R3 is the only request whose claim is narrower than its body.
- Plan 034 WP-4: the Folder Redirection scope brief
  (`docs/scope-brief-2026-09-11-folder-redirection.md`). Not a ruling -- the
  plan says this one is a decision, and the brief assembles what it needs
  without taking it. Zero estate time. It ran the offline discriminator the
  survey asked for and nobody had: an advanced policy with three group rules
  produces one registry tuple, carrying neither the group SIDs nor the four
  option flags that R3 shows Windows encoding as `Flags=1021`, so
  `to_registry_settings()` is not a Folder Redirection writer at any level of
  detail. The CSE appears in 0 of the same 26 production GPOs that ruled
  Software Installation out -- but the two costs that made *that* ruling easy
  are absent here: this capture is already taken, and `fdeploy1.ini` is a
  458-byte UTF-16LE INI whose oracle is `Backup-GPO`, not an undocumented
  binary. Recommendation: read target, write deferred rather than refused.
  `tests/test_folder_redirection_scope.py` pins the code facts and fails when a
  ruling is acted on.
- The "Security template" panel: one dialog reaching both Plan 034 WP-3
  surfaces, switched by a mode selector, with `limitations` rendered **above**
  the answer as the RSOP panel does. Thin on purpose -- families arrive as JSON
  and only `scope` is a field. The `scope` control is hidden for object
  security, which has no such distinction, and an empty validation list renders
  with WI-055's ruling beside it rather than as a bare "no issues". Seven
  browser tests including an axe scan of the open dialog, which the
  workspace-wide scan cannot reach because it runs with every dialog closed.
- `POST /api/security-template/policy-families` now declares
  `empty_sections_unmeasured` when a family renders as a bare section header.
  `UserRightsFamily` and `SecurityOptionsFamily` emit their section
  unconditionally while the object-security families omit theirs when empty;
  the two disagree, every section in the certified candidate carried entries,
  and only the non-empty behaviour is measured. Conditional on what the render
  produced, which is an exact property of the answer rather than a guess about
  the caller.
- `docs/plan-033/bound-source-cost.md`: what each file costs to edit, in lanes
  that must be re-run, generated from the live verdicts by
  `scripts/plan-033/report-bound-source-cost.py` and guarded by
  `tests/test_bound_source_cost.py`. The information was always complete and
  never readable -- spread across twenty-one packs -- so pricing a change meant
  opening all of them. `oracle_evidence.py` and `psdirect.ps1` cost the whole
  estate; `model.py`, `export.py` and `validation.py` cost two lanes each; the
  rest of `src/gpo_studio/` costs nothing, which is a statement about coverage
  rather than about quality. Linked from AGENTS.md, where the next session
  reads it before editing.
- Plan 034 WP-3: `object_security.py` is reachable. `POST /api/security-template/
  object-security` renders registry-key, file-system and service security as a
  `GptTmpl.inf`, for the three families its lane certified (18/18 at
  `f5cad577`, propagation codes 0/1/2 and startup codes 2/3/4) and in the
  emission direction only. Restricted groups are not renderable: no lane has
  read that serializer. Four limits ride on every response, including
  `acl_content_is_not_judged` -- WI-055's ruling, surfaced where a caller reads
  the answer rather than left as an empty `issues` list that looks like
  approval.
- WI-064: the restricted-groups writer emits `S-1-5-32-544__Members` where
  Windows exports `*S-1-5-32-544__Members`, starring every SID in the entry's
  value and not the one in its key. The parser strips a leading star, so Studio
  read its own output back into the model that produced it and the round trip
  stayed clean. Filed, not fixed: the module is bound by its verdict.
- WI-065: `SystemServicesFamily.validate` reports `unparseable_service_sddl`
  when `raw_sddl` is set and `security_descriptor` is `None` -- but that field
  is only populated by `from_template`, so a directly built model is called
  malformed for having gone unparsed. Validating the object-security lane's own
  candidate yields three such errors for a descriptor Windows accepted. The
  surface parses on construction as a workaround; the check itself is filed
  against the same batch.
- WI-063: eight `run-*-oracle.sh` lane runners are committed with CRLF and no
  longer parse under `bash`. Filed rather than fixed: all sixteen affected
  files are hash-bound by the WI-062 batch, so renormalizing them fails the
  live-harness binding for 19 of the 21 banked verdicts and costs an estate
  requalification, which the estate owes anyway for its 22nd lane.
  `tests/test_lane_runner_line_endings.py` holds the line until then. The
  mechanism is `-text` in `.gitattributes`, which pins committed bytes in both
  directions and so removes the worktree/index disagreement WI-059's guard
  detects; `text eol=lf` -- already used for every `src/gpo_studio/*.py` in the
  same file -- pins LF and keeps the guard.
- WI-061: revision snapshots no longer each carry a full copy of the retained
  native XML. Schema v4 stores each distinct document once
  (`retained_documents`, keyed by the SHA-256 of the decoded bytes) with
  per-snapshot references, rehydrating at every store read so no consumer of
  the API observes the encoding; a v3 workspace migrates in place, and
  deleting the last holder collects its documents. The bound
  Scripts/publication qualifications are re-earned by this batch.
- WI-062: evidence packs no longer bank byte copies of controller-side bound
  source. Verdicts record `(commit, path, sha256)` for every bound file
  (schema version 2), `harness_matches_source` covers the guest-deployed
  half, WP-0's manifest carries the orchestrator files in `source.bound`,
  and `test_committed_evidence.py` re-derives recorded digests from git at
  each verdict's own commit. Historical packs and tags are untouched. The
  requalification batch banked WP-0 plus 20 schema-version-2 verdicts at one
  frozen harness; the computer group-deny lane is pending the estate repair
  its batch note describes. See [the decision](docs/plan-033/bound-source-manifest.md)
  and [the batch](docs/plan-033/wi062-batch.md).
- WI-061 (part): `GET /api/gpos` and `GET /api/starter-gpos` no longer carry
  WI-060's retained native XML in every row -- rows report
  `has_backup_inventory` and the detail endpoint serves the snapshot. The
  workbench refetches the list on load and after every mutation. The
  per-revision copies of the same bytes remain open.
- Retained inventory paths are now deduplicated exactly rather than
  case-insensitively: `read_backup` keys its file map case-sensitively and fed
  its own output back through the validator, which could refuse a capture the
  importer had just produced. Reports also state that native names and values
  are reproduced verbatim from the source domain.
- WI-060: native backup imports retain the original XML documents and a complete
  payload file inventory. Plain-text reports expose imported native settings,
  including unmodeled Scripts commands, as an explicitly historical snapshot.
  Payload bytes still require the original backup. The regression compares
  27 Windows-produced backups; the two affected qualifications each passed
  21/21 on fresh clean-source runs. See
  [the measured scope and evidence](docs/plan-033/backup-report-fidelity.md).
- WI-059: every oracle finalizer now refuses working-tree/index/HEAD byte
  drift before writing verdicts or tags, including with `--no-tag`. All 21
  live lane verdicts and WP-0 were requalified in one frozen estate batch;
  exact captures and historical records are preserved. See
  [the completed batch](docs/plan-033/wi059-harness-batch.md).
- WI-028: measured `SearchedSOM` persistence in the client's RSoP WMI namespace
  after verified OU deletion and policy refresh. The investigation documents
  a scoped-use strategy for future SOM assertions; current lanes do not grade
  these historical rows. See [the findings](docs/plan-033/wi028-searched-som-investigation.md).
- Plan 034: the unsurfaced policy-family serializers now feed a repeatable
  Windows WP-3 lane. Native validation found and corrected `AuditDSAccess`
  and rejected two speculative Kerberos fields, which were removed. Final
  member/DC runs each pass 21 checks with retained raw evidence and source
  bindings. The lane records DC role and retains failure evidence. See
  [the results](docs/plan-033/wp3-policy-family-results.md).
- Plan 034 object-security serializer lane passed 19/19 checks on the clean
  member server, with exact six registry/file and three service rows retained
  in the evidence pack. ACL application and content suitability remain outside
  scope under WI-055. See [the results](docs/plan-033/object-security-results.md).
- Plan 034 Scripts metadata lane passed 21/21 checks for Import-GPO,
  Get-GPOReport, and Backup-GPO rebackup. Script payload execution remains
  outside scope.
- Artifact-store scope is now explicit: EICAR marker and secret heuristics are
  local checks, executable publication requires a future verified signer path,
  duplicate arrivals append provenance without replacing the canonical row,
  and publication eligibility remains read-only. See
  [the scope ruling](docs/plan-033/artifact-store-scope.md).
- Plain-text GPO reports now count all 21 typed preference families per scope,
  including drives, services, scheduled tasks, and immediate tasks. Native
  backup fixtures cover these counts; this is not full Get-GPOReport parity.
- Publication planning now marks preserved CSE metadata/files as unsupported
  for SYSVOL targets and fails validation, instead of silently omitting them.
  The regression uses the qualified native Scripts rebackup. Generated scripts
  remain review-only and refuse all unverified operations.
- Windows frontend formatting checks now accept checkout line endings without
  reformatting the source. NetSecurity availability and isolated firewall
  GPO authoring/readback were measured; the network model remains unverified.
- RSOP prediction answers the two sides separately (WI-032). Each GPO row
  carries `computer_status` and `user_status`, and a result answers
  `computer_applied_gpos` / `user_applied_gpos`. Promoting the WP-9 lane's
  applied-set comparison from advisory to gated found a real over-report on its
  first run — the model reported a GPO applied to a side it carried nothing
  for, which Windows omits — corrected and re-certified across thirteen RSOP
  runs.
- `slow_link`, `safe_mode`, `simulate_slow_link` and `simulate_safe_mode` are
  removed from the RSOP model and API (WI-036). They were accepted and never
  read; the request models now refuse unknown keys, so a caller sending one
  gets a 422 rather than a prediction that silently ignored it.
- `object_security.validate()` deliberately does not judge ACL content, now
  recorded as a ruling rather than left as silence (WI-055), and
  `certification.py` is deleted as superseded (WI-056). The RSOP surface's
  `limitations` array is consequently empty: all three limitations it carried
  have been closed by fixing what they disclosed.
- Plan 034: publication-plan completeness now has a repeatable Windows lane,
  which passed 21/21 on the clean member server. It compares the plan's own
  account of what it would write against the SYSVOL tree and extension-list
  attributes Windows produces from the same content — the comparison that found
  WI-057 and the only one that could, since a round trip never asks what a
  third party would have had to write. The lane measures the plan, not a
  publication: nothing writes to SYSVOL or AD, and the operation allowlist
  stays empty. See
  [the results](docs/plan-033/publication-completeness-results.md).
- Publication plans now register the client-side extensions their SYSVOL
  content requires, and publish a GPO's comment. Both gaps were measured
  against Windows rather than reasoned (WI-057, WI-058): without the extension
  lists a plan produced a GPO whose files were all correct and which applied
  nothing. The values come from the exporter's measured vocabulary rather than
  being restated, and content that cannot be honestly registered — an
  unverified GPP family, or a SYSVOL-only target that cannot reach a directory
  attribute — is refused rather than guessed. The planner remains unsurfaced
  and review-only, and the fix itself is not yet Windows-verified.

> Post-1.0 development has added considerably more to `src/` than it has added
> to the operator-facing product. Entries below distinguish **surfaced**
> capabilities (reachable from the API or browser application) from **domain
> layers** (implemented and unit-tested, reachable from neither). No post-1.0
> capability is Windows-verified except where an explicit Plan 033 workpackage
> is cited.
>
> As of 2026-07-29 the unsurfaced domain layers are further classified as
> **unproven drafts, not assets awaiting wiring** — a claim about correctness,
> not only reach. Every layer an external oracle has examined has needed
> correction. See [`docs/domain-layer-status.md`](docs/domain-layer-status.md).

### Added

- Plan 023: scope-of-management, delegation, WMI-filter, and loopback support
  — **surfaced**. `som.py`, `delegation.py`, `wmi_filter.py`, and
  `ad_discovery.py` back new API endpoints for GPO links, loopback validation
  and description, AD discovery script generation/ingest
  (`/api/discovery/*`), and effective-rights evaluation. Discovery generates
  PowerShell and parses its JSON output; it performs no network I/O of its
  own, preserving the offline-first charter.
- Plan 024: full GPP adapter coverage — **surfaced**. `gpp_adapters.py`
  extends the 1.0 Groups/Registry slice across the in-box preference families
  through `gpp.py`, `canonical.py`, and `import_export.py`.
- Plan 029: RSOP prediction — **certified in twelve measured regions, then
  surfaced** (WI-030, 2026-08-06). `POST /api/rsop/compute` predicts the
  effective policy for a computer/user pair over a topology supplied in the
  request body; `POST /api/rsop/compare` computes two and reports where the
  effective settings differ. A thin browser panel ("RSOP prediction" in the
  rail) covers `compute` only: the target as form fields, the topology as JSON,
  no builder — the workspace holds draft policies rather than an estate to build
  a topology from.
  The twelve certifying scenarios ran against a real Windows 11 26200 client
  and cover LSDOU ordering, link order, inheritance and its blocking,
  enforcement, disabled links and sides, security filtering with denies on both
  Apply and Read, user scope, and loopback merge and replace; they are
  enumerated in `docs/capability-matrix.md`, which also states what is **not**
  certified — including WI-049's two filter cells, which the surface exposes and
  which reasoning rather than measurement settled. Two limitations are announced in every response rather than left
  in the docs: `gpo_status_is_not_per_side` (WI-032 — the applied-GPO status
  collapses to "applied on at least one side" and cannot answer the two sides
  separately) and, when the caller sets one of the fields,
  `slow_link_and_safe_mode_are_not_evaluated` (WI-036). This is the first
  post-1.0 layer to complete both halves of the exit condition in
  `docs/domain-layer-status.md`.
- Plans 025–028, 030–032: domain layers — **not surfaced**. Security settings
  (`security_template.py`, `object_security.py`, `network_security.py`,
  `policy_families.py`), script and managed-artifact policy
  (`script_policy.py`, `artifact_store.py`), software installation and folder
  redirection (`software_install.py`, `folder_redirection.py`), GPMC lifecycle
  and interop (`lifecycle.py`, `gpmc_interop.py`), controlled publication
  (`publication.py`, `publisher.py`), certification (`certification.py`), and
  hosted control plane (`hosting.py`) are implemented and unit-tested, but are
  reachable from no API endpoint, UI module, or export path. They are not
  operator capabilities and are excluded from the 1.0 contract; see
  `docs/capability-matrix.md`. The publication modules are pure and emit no
  writes — the web process still never writes to AD or SYSVOL — and
  `hosting.py` does not make a hosted mode available.
- Plan 033 WP-0: Windows external-oracle evidence contract, owning-boundary
  matrix, frozen environment spec, conservative XML normalizer v1, fixture
  recipe schema, and the two-phase harness — `run-evidence.ps1` captures
  genuine raw evidence on the domain-joined host and
  `finalize_oracle_run.py` is the single authority for source provenance,
  normalization, and comparison binding. Certified pass on a clean tree with a
  full integrity pack: harness scripts, recipe, and orchestrator are hashed
  input artifacts bound to the recorded commit, every artifact rehashes
  intact, and cleanup is confirmed by an independent LDAPS re-query.
- Plan 033 WP-1A: native-origin GPMC corpus, authoring guide, and genuine
  GPMC-authored canary fixtures.
- Plan 033 remediation scenario corpus — **validation infrastructure, not a
  capability**. Thirteen provenance-graded scenarios across four families
  (gpp-services for WI-022, security-template areas for Plan 025/WP-3,
  rsop-topology for Plan 029/WP-6, ilt-os for WI-023) under
  `tests/fixtures/scenarios/`, a machine-readable test-platform registry
  (`platforms.json`) extending `docs/plan-033/environment-spec.md`, and the
  `remediation_corpus.py` loader that enforces referential integrity,
  readiness honesty (no `ready` claim on an unqualified platform), and
  sha256-pinned native-capture anchors. Executable WI-022 characterization
  probes pin today's parse/writer divergence and flip when the fix lands.
  The corpus records expected Windows behavior for the Plans 025–032
  remediation program; nothing in it is oracle-executed yet and no
  capability claim changes.
- WI-022 corrects the GPP Services typed model and wire shape against the
  genuine GPMC capture: native recovery names and omission semantics,
  `thirdFailure`, exact delay preservation, and captured extension metadata.
  It also covers the complete MS-GPPREF startup, service-action, and failure-
  action vocabularies plus the protocol-defined restart/program fields, GPMC
  report comparison, and a new WP-1B Services candidate. Its first Windows
  Server 2025 writer run passed, but the later manual capture invalidated the
  delay semantics; WI-024 corrected and recertified the candidate in clean-
  source run `wp1b-writer-20260730164352-5286`. Services is not endpoint-
  applied or authorable through the browser/API.
- WI-024 uses a dedicated GPMC Services recovery capture to correct the
  remaining wire assumptions: GPMC emits `RUNCMD`/`REBOOT`, millisecond
  restart-service and restart-computer delays, boolean `append=1`, and omits
  `serviceAction` for No change. It also confirms `program`, `args`,
  `restartMessage`, `accountName`, and `interact`. The corrected isolated and
  mixed Services candidates pass the full WP-1B Windows writer lane.
- WI-023 surfaces the modern `FilterOs` family-token limitation as a preflight
  warning and in Studio bundle manifests: `WINTHRESHOLDSRV` cannot distinguish
  Server 2016/2019/2022/2025, and `WINTHRESHOLD` cannot distinguish Windows 10
  from Windows 11. Imported OS criteria are shown read-only in the browser and
  survive edits instead of being silently dropped; build-specific targeting
  directs operators to WMI or registry predicates.

- Plan 033 WP-2: deterministic native GPMC backup emission with distinct
  backup/GPO identities, v2 `Backup.xml`, native `DomainSysvol/GPO` paths,
  verified Registry and GPP extension profiles, and a Windows Server 2025
  `Import-GPO`/re-backup/cleanup oracle lane. Native GPP output is now a strict
  allowlist: Drive Maps, Local Users and Groups, Scheduled Tasks, and Services
  are emitted; GPP Registry and uncaptured families must use the Studio bundle
  until their native extension metadata is independently verified. Services
  extension metadata is capture-backed and its Studio-origin candidate passes
  `Import-GPO`, GPMC report comparison, and `Backup-GPO` semantic comparison.
- Plan 021 WP-1: authoritative GPMC capability inventory
  (`docs/plan-021/capability-inventory.md`) — a versioned, pre-gate matrix of
  GPMC lifecycle/scope/report surfaces, principal-bearing fields, every in-box
  CSE and editor by GUID/side/storage/OS/deprecation/management-API, the GPP
  item/action/option space, and the complete ILT predicate AST. Each row is
  classified (`verified-rw` … `unknown`) and linked to Microsoft documentation
  and lab evidence. No row is `verified-rw` without Windows and endpoint
  evidence.
- Plan 021 WP-4: reference estates and evidence schema
  (`docs/plan-021/reference-estates-and-evidence.md`) — the provisional Windows
  target matrix (WS2019/2022/2025 + Win11; Win10 behind an ESU decision), the
  ADMX/ADML licensing classification rules, the redaction contract enforced by
  the identifier gate, the versioned evidence-pack JSON schema, and the
  negative/downgrade fixture requirements.
- Plan 021 WP-4: public matrix generator (`scripts/generate_public_matrix.py`
  and `src/gpo_studio/evidence.py`) — loads versioned evidence packs, refuses
  to derive claims from packs whose redaction or licensing gates are
  unsatisfied, and derives a public capability matrix containing only claims
  backed by passing evidence. A `verified-rw` claim requires both a passing
  Windows-side and a passing endpoint record.

### Changed

- WI-049 (corpus half): the Plan 033 RSOP corpus now carries a row for each of
  the three filtering regions the model answers by reasoning rather than by
  measurement — a read deny naming the user resolved on the computer side, an
  Apply deny naming the computer resolved on the user side, and a deny that
  matches through a group rather than by name. They are filter edits on two
  scenarios the lanes already run, not a session of their own, and each takes
  the top link order so a wrong answer costs the predicted *winner* rather than
  one absent value.

  **Measured on the estate 2026-09-06, and all three agreed with the model** —
  `rsop-observe-20260906184434-8187` and
  `rsop-user-observe-20260906185345-9222`. A user-named read deny left the GPO
  applying on the computer side, a computer-named Apply deny left it applying on
  the user side, and a group-matched deny blocked. The API's
  `answer_rests_on_a_reasoned_cell` limitation is **removed** with them: it
  existed only while those cells were unmeasured, and a payload calling a
  measured answer reasoned is the same defect as a matrix that says `failed`
  while supported. **Operator-visible**: a caller who was reading that code will
  stop seeing it. What remains unmeasured is a deny matched through a
  *computer's* group, which now has its own item (WI-054) rather than a
  paragraph inside a closed one.
- The browser application supports a dark colour theme — **surfaced**. Every
  colour in `studio.css` now flows through a design token, and a
  `data-theme` attribute on `<html>` selects the palette: `Auto` follows the
  operating system, with an explicit Dark/Light override persisted per
  browser. The bootstrap (`static/js/theme.js`) is a classic head script so
  the resolved theme lands before first paint under the `script-src 'self'`
  CSP. The dark palette is held to the same automated accessibility bar as
  the light one by a browser test that runs the axe scan under it. A choice
  made in one tab reaches the application's other tabs through a `storage`
  listener rather than leaving them on a stale palette until reload, and
  engines that ship only the deprecated `MediaQueryList.addListener`
  (Safari 13 and earlier) still follow the system while the mode is `Auto`.
- Wide policy tables keep their row actions reachable: the actions cell is
  sticky against the right edge of the scrolling table card, so Edit /
  Comment / Delete no longer disappear behind a horizontal scroll nobody is
  told about. Placeholders are styled distinctly from values — and now at a
  contrast ratio that clears WCAG AA against the light canvas, which the
  first colour did not — and secondary buttons gained a quiet hover state.
  On narrow viewports the rail's footer is shown rather than hidden, so the
  workspace status it carries is not desktop-only.
- Plan 033: lane verdicts now check what they claim to check. An adversarial
  review round (three reviewers, hazard-scoped, one cross-lineage) found that
  WP-2 and WP-3 graded themselves against the copy of `expected.json` the guest
  returned rather than the candidate the controller built — which also made two
  of WP-2's named checks structurally unfalsifiable — and that WP-1B, the lane
  that qualified the estate, had no environment gate at all. Both lanes now take
  `--candidate-root` and carry a `candidate_delivered_intact` check; WP-1B gates
  on `FROZEN_ENVIRONMENT` like the others. Every run now owns a private
  directory tree on the guest, so concurrent runs on one guest can no longer
  select each other's evidence; `cleanup_succeeded` means the GPO was removed or
  an independent enumeration shows nothing by that name; orphaned GPOs are
  reaped and the reap is recorded; and two fail-open defaults
  (`native_shape_findings` absent, `check_git=False`) are closed.
- Plan 033: **every evidence lane now runs against the disposable evidence
  estate over PowerShell Direct, and the SSH transport is retired.** WP-0, WP-2
  and WP-3 were ported alongside WP-1B and the endpoint lane, each with its own
  qualification run on the estate (recorded in the Qualified environments table
  in `docs/plan-033/environment-spec.md`, with committed verdicts and evidence
  tags). WP-0's certification, whose commit had been orphaned by a squash merge,
  is re-earned on a commit that resolves. Lane finalizers now check the recorded
  environment against `FROZEN_ENVIRONMENT` rather than private copies of the
  profile; WP-2 had not been checking its environment at all, and WP-3's copy
  had drifted into pinning an exact PowerShell servicing revision and gating on
  an LGPO hash the 2026-07-29 re-freeze had already removed.
- Plan 022 closed — REVIEW AND REFINE gate passed 2026-07-25
  (`docs/plan-022/gate-decision-2026-07-25.md`), with ADMX parser fixes and
  code hardening.
- Adopted `ruff` 0.16 and its expanded default rule set.
- Documentation: corrected the recorded status of Plans 021 and 023–032, which
  claimed `proposed (post-1.0)` while their implementations were already
  committed. Each now records whether its domain layer is surfaced and whether
  it carries Windows evidence. The capability matrix and README gained an
  explicit inventory of landed-but-unreachable modules, so the matrix again
  matches what `src/` contains.
- Documentation: closed out pre-release status language in `SECURITY.md` and
  Plans 017/019/020, wrote the 1.0.x support/compatibility/deprecation
  policy, and refined Plan 021 with a provisional target matrix, corpus
  licensing/redaction rules, and a pre-review spike boundary.
- Risk-based coverage floors now include `src/gpo_studio/evidence.py` (90%).
- The static safety gate now scopes forbidden-import categories to the **web
  process** — the modules transitively reachable from `api.py` — which is what
  the charter actually constrains, rather than to the `src/` directory. A
  category exemption may be granted to lab or release tooling that never runs
  in a request path, and `scripts/check_safety.py` fails if an exempt module
  ever becomes reachable from `api.py`, so an exemption cannot silently widen
  into a charter breach.

### Removed

- `scripts/windows-oracle/remote-run.ps1`, the scheduled-task launcher, and
  every lane's SSH branch. The launcher existed only to obtain a logon token an
  SSH non-interactive session cannot provide, and it took the credential as a
  `schtasks /RP` argument — transient, but decodable by a privileged observer on
  the host for as long as the task existed. PowerShell Direct carries the
  credential through the hypervisor and needs no launcher, so removing it is a
  security improvement rather than cleanup. Certifications produced on the
  retired transport are not retracted, but their evidence packs can no longer be
  re-verified in this tree, and `build_harness_inputs` reports that explicitly
  instead of defaulting to a file set that no longer exists.

### Fixed

- WI-037: a lane's staging step removed every directory under the guest's
  output root, so the next run deleted exactly the evidence a human needed to
  explain why the last one failed. The three shared-root drivers now retain the
  newest five run directories and sweep the guest's `scripts` directory, which
  staging owns. Preserving run directories makes the "newest output directory"
  fallback unsafe in a new way — it would pull the *previous* run's observation
  and the finalizer would grade it as this one's — so the fallback now requires
  an observation-bearing directory created since a guest-side clock reading
  taken immediately before the observation, and refuses anything but exactly one
  match. The endpoint lane's `verify` phase was writing to a fixed path for the
  same reason and is per-invocation now. Lab tooling; no operator-facing change.
  **Closed**: fourteen runs re-certified the affected lanes on 2026-09-06, and
  the retention was confirmed on the guests rather than inferred — the previous
  run's observation survived where the old staging would have deleted it. The
  first run also found a defect in the fix itself: `$(verify_endpoint)` ran the
  phase in a subshell, so its idempotency flag never reached the driver's shell
  and the EXIT trap repeated the whole post-teardown verification.
- WI-025 (code half): the WP-1B and endpoint lane verdicts named the candidate
  artifacts they were graded against and hashed none of them, asserting a
  comparison nobody could re-check. Both finalizers now record SHA-256 for every
  file under `--candidate-root`, and refuse a run whose candidate root is
  missing a required artifact rather than recording a shorter block that still
  looks complete. WP-6B's implementation is the model. **Closed** by
  `wp1b-writer-20260906183513-1195` (7/7, fifteen candidate hashes) and
  `endpoint-observe-20260906185837-7523`, both committed with their blocks
  populated.
- WI-053: the endpoint lane's only committed certification escaped every
  evidence gate since 2026-08-03 because its filename matched neither prefix
  the coverage guard globs for — WI-037 changed two files it binds and every
  RSOP verdict went red while it stayed silent. The re-certification is
  promoted under a covered name and mapped in `LANE_VERDICTS`, and the guard is
  widened so every JSON in an evidence directory is either verdict-named or
  named with a reason in `NON_VERDICT_EVIDENCE_FILES` — a verdict can no longer
  escape by being named unusually. A control fails if the pattern ever stops
  matching the endpoint certification again. Lab tooling; no operator-facing
  change.
- WI-051: the publisher's separation-of-duties control existed only on the
  path that constructs an approval through `approve_request` — the gates took
  no principal at all, `decided_by` was hardcoded empty, and a
  directly-constructed self-approved request (the shape persistence produces
  when it rehydrates state) passed with zero validation issues. The gates now
  take a required `actor`, populate `decided_by` from it, and run a
  `separation_of_duties_gate` that re-derives the requester/approver
  comparison through `hosting.can_self_approve` and refuses a self-approved
  request, a publishing actor who approved it, or a missing principal.
  `ApprovalRequest.validate()` carries the same check structurally, and
  `_approval_gate`'s four previously untested refusal branches are tested.
  Domain layer; no operator-facing change.
- WI-052: `profiles_for_actor` matched an actor against a profile *id* —
  `effective_capabilities("p1")` returned profile `p1`'s capabilities for
  nobody, while a real principal got nothing. `PublisherProfile` now carries a
  `principals` field and `profiles_for_actor` resolves against it; a profile
  granted to nobody matches nobody, with a validation warning so the
  configuration is visible. Domain layer; no operator-facing change.
- WI-054: the corpus's nesting rows all put the disposable group in the USER's
  token, so the model's answer about a membership in a CLIENT'S machine token
  was unit-tested and estate-untouched while the API accepted that input from
  callers. A new computer-scope scenario authors an APPLY deny whose only
  identity is a group the client's computer account joins; the lane reboots the
  client so the machine token carries it (a machine token is minted at boot,
  and there is no lighter refresh); the observation half corroborates the
  membership from the machine token and from the directory independently; and
  the computer finalizer gained the user lane's token gate. Measured the same
  day: the model said blocked, Windows agreed
  (`rsop-observe-20260906221638-4687`), and the twelve other runs from the
  same tree re-certified the lanes the change retired. The first run found
  that the reboot makes boot-time policy processing a second applier, which
  the observe half now records as `boot_applied_values` instead of mis-reading
  as unattributable residue. The dead `reaches_reasoned_cell` disclosure left
  in the builder by WI-049's closure went out in the same change. Lab tooling;
  no operator-facing change.

- The topbar action links (`Policy report`, `Review PowerShell`,
  `GPMC backup`) rendered in default link blue: `a.button` never received an
  ink colour, only real `<button>` elements did. Long GPO names also wrapped
  the topbar inside its fixed 114px height, folding the action labels onto
  two lines; the bar now grows instead and action labels never wrap.
- WI-044: a GPO carrying a **deny** security filter advertised its PowerShell
  plan and Studio export bundle as available and then refused both downloads
  with HTTP 422. WI-041's refusal is correct and is unchanged; what was missing
  was that `artifact_capabilities` derived availability from `validate_gpo`,
  which has no deny rule. The condition now lives once, in
  `export.plan_refusal()`, which both the export path and the capability
  payload consult — so the operator is told up front, with the reason, instead
  of discovering it by pressing a button. **Surfaced** (API + browser
  application).
- WI-046: the same defect as WI-044, one capability entry along — a GPO
  carrying a **GPP Registry** preference advertised `gpmc_export` as available
  and then refused the native backup with `unsupported_native_gpp_extension`.
  Native backup covers four GPP families and `Registry` is not one of them,
  while neither `validate_gpo` nor the preserved-content count could see it.
  `export.native_backup_refusal()` now derives the advertisement by running the
  refusing code rather than restating its conditions. **Surfaced** (API +
  browser application).
- WI-045: a committed lane verdict binds its harness files by SHA-256, and no
  test checked that those hashes still matched the tree. The RSOP verdicts had
  twice been re-run for exactly this reason, both times caught by a person
  rather than by CI. Live certifications are now hash-checked against the
  working tree, with deliberately retained history enumerated in
  `RETIRED_VERDICTS` and a control that fails if a retired verdict still
  matches — so the exemption list cannot be used to silence the check. Lab
  tooling; no operator-facing change.
- `jsonschema` was declared only as a uv dependency group, so `uv sync`
  installed it locally while CI's `pip install -e '.[dev]'` did not, and the
  Plan 033 WP-1A fixture tests failed on CI with `ModuleNotFoundError`. It is
  now declared in the `dev` extra and the duplicate group was removed, leaving
  one source of truth for development dependencies.
- `oracle_evidence.py` imports `subprocess` to shell out to `git` for evidence
  provenance, which violated the static safety gate's blanket ban and broke
  the `static-safety` CI job. The module is lab tooling and is unreachable
  from the web process; it is now covered by a reachability-enforced exemption
  rather than by weakening the ban.

## [1.0.0] - 2026-07-18

### Changed

- Promoted `1.0.0rc3` after the complete Windows NVDA acceptance journey
  passed in Edge and the Firefox ESR smoke journey passed. This promotion
  contains no application-behavior changes from the tested candidate.

## [1.0.0-rc.3] - 2026-07-18

### Fixed

- JavaScript modules are now served as `text/javascript` on Windows regardless
  of the machine's MIME registry. Previously, a `text/plain` association made
  browsers reject every module under the application's `nosniff` policy,
  leaving all browser controls inert.
- Added a GPO Studio favicon.

## [1.0.0-rc.2] - 2026-07-18

### Fixed

- CycloneDX release SBOMs now receive a deterministic UUID serial number before
  GitHub attestation. The reproducible generator intentionally omitted this
  optional field, but GitHub's attestation parser requires it. The workflow now
  uses the current pinned `actions/attest` interface directly.

## [1.0.0-rc.1] - 2026-07-18

### Added

#### Capability contract and canonical model (Plan 015)

- Capability contract with explicit states: `supported`, `preview`,
  `preserved`, `blocked`, and `out of scope`. Replaces the stale roadmap
  table. Per-action fidelity documented for authoring, import, export,
  PowerShell plan, diff, and Windows-lab verification.
- Split semantic hashes: `policy_semantic_sha256` covers every field that
  changes effective policy or publication intent; `review_model_sha256`
  additionally covers review-relevant annotations and preserved CSE
  metadata.
- Exhaustive validation with `typing.assert_never()` dispatch on closed
  variant sets, so adding a new enum or kind fails the type check at every
  unhandled site.
- Complete two-way and three-way diff for GPP collections, ILT predicates,
  CSE metadata, side enablement, domain, and GPO-level metadata. Stable
  identities for GPP elements; link conflict detection in three-way diff.
- Golden vectors for canonical digests so other implementations can
  reproduce them.
- Stable issue codes and field paths returned by validation for browser
  field mapping.
- `ready` transition guarded: a GPO cannot enter it with validation errors,
  unknown CSE content, unresolved conflicts, or unsupported preview content.

#### GPP end-to-end authoring (Plan 016)

- GPP Groups and Registry authoring API with optimistic-concurrency CRUD
  endpoints under `/api/gpos/{guid}/preferences/...`.
- Browser editors for GPP Groups (action, members, remove-all flags,
  description) and GPP Registry (action, key, typed values) with inline
  sub-editors.
- ILT predicate editor supporting six types: `ou`, `group`, `registry`,
  `ip_range`, `environment`, `wmi_query`, with negation and AND combination.
- Plain-language ILT preview beside the serialized structure.
- Unknown XML attributes, unknown child elements, and unknown ILT predicate
  types preserved losslessly through import/export round-trips.
- Typed Pydantic request/response models and OpenAPI examples for every
  supported GPP action and value type.

#### Windows and GPMC interoperability (Plan 017)

- Versioned synthetic compatibility corpus covering all registry types,
  deletes, side state, link and security shapes, WMI, GPP Groups and
  Registry actions, all ILT predicates, Unicode, unknown content, malformed
  inputs, cpassword, migration tables, and partial or corrupt backups.
- Import conformance tests comparing normalized Studio model to expected
  semantics field by field.
- PowerShell plan validator with closed allowlist checking structure,
  assignment ordering, command shapes, pipes, semicolons, backticks,
  dangerous aliases, and case-insensitive cmdlet spelling.
- Three adversarial review rounds fixing real bypasses in multiline quoted
  strings, case-insensitive PowerShell names and aliases, and user-scope
  GPP coverage.
- WP-5 Windows lab validation on Windows Server 2025: all 12
  conformance-corpus fixtures exercised through their PowerShell plans on a
  domain controller (all six registry types, deletes, side enablement,
  idempotency, `Backup-GPO`). Sanitized, hash-pinned evidence report
  (`docs/release-evidence-report.json` +
  `scripts/generate_evidence_report.py`), capability-matrix Win-lab column
  promotions, and a root-cause diagnosis of the `Import-GPO` `Backup.xml`
  v2.0 incompatibility recorded as a known issue.

#### Workspace and runtime hardening (Plan 018)

- Versioned workspace schema with forward-only, transactional migrations,
  preflight checks, and backup before any destructive migration. Unknown
  newer schemas refused with an actionable error.
- CLI commands: `workspace check` (quick and full integrity), `workspace
  backup` (SQLite online backup API with WAL checkpoint), and `workspace
  restore` (crash-safe with rollback, retains old database).
- Atomic metadata sidecar recording schema version, application version,
  GPO count, revision count, and source/backup SHA-256 digests.
- Startup quick-check with health degradation and `/api/workspace/integrity`
  endpoint.
- Bounded untrusted input: total bytes, file count, directory depth, XML
  element count, text/attribute length, GPO count, PReg record count,
  `REG_MULTI_SZ` item count, and per-file size limits on every import path.
- Race-resistant file handling on POSIX (openat) and Windows (NtOpenFile
  with RootDirectory walk and identity verification).
- Loopback binding enforced by default. Non-loopback bind requires
  `GPO_STUDIO_UNSAFE_BIND=1`.
- Host header and mutation Origin validation to reduce DNS-rebinding abuse.
- Content-Security-Policy, `X-Content-Type-Options`, conservative referrer
  policy, and cache controls on API and artifact responses.
- Structured local logs with request ID, operation, GPO GUID, revision,
  outcome, and duration. Policy values, SIDs, paths, and request bodies
  are never logged.
- Numeric resource limits: `REG_DWORD` [0, 2^32-1], `REG_QWORD`
  [0, 2^64-1], `REG_MULTI_SZ` max 10,000 items, PReg max 100,000 records,
  backup max 100 GPOs, migration table max 10 MiB, per-file max 50 MiB,
  total backup max 500 MiB, max 10,000 filesystem entries, max depth 100,
  request body max 10 MiB.

#### Browser quality and accessibility (Plan 019)

- Browser test foundation: pinned ESLint, Prettier, Vitest, Playwright,
  and axe-core toolchain. CI exercises the packaged CLI against a temporary
  real SQLite workspace in Chromium, with a Firefox smoke baseline and
  failure traces/screenshots.
- Concurrency conflict recovery UX: 409 responses retain unsaved form
  values, fetch the current revision, and offer a structured compare/reapply
  flow. No destructive change is silently retried.
- Persistent error alerts for offline, server-down, and partial-import
  states instead of relying on transient toast messages.
- Export review boundary showing both semantic digests, validation state,
  preserved content, and artifact capability before download.
- Revision-to-revision diff selection rendering all Plan 015 diff kinds.
- Deterministic inert text policy report suitable for code review or change
  tickets, with no active content.
- Archived import detection: edit actions disabled, explicit fork path
  required before editing.
- GPP clone, atomic reorder, per-item revision restore, and destructive
  confirmation dialogs.
- Accessibility: semantic keyboard tabs, labelled and focus-managed
  dialogs, field-error relationships, persistent announcements, visible
  focus, target sizing, forced-colors and reduced-motion handling, narrow
  reflow. Automated axe checks report no serious or critical violations in
  covered primary states.
- Seven end-to-end release journeys automated in CI: raw registry
  author-review-export, ADMX configuration, estate fork and three-way
  conflict, GPMC backup import and GPP edit, security and WMI filter stale
  conflict, revision restore, and edge cases (max QWORD, Unicode, long
  values, server errors, narrow viewport).

#### Release engineering and 1.0 gates (Plan 020)

- Single version source: `pyproject.toml` reads `__version__` dynamically from
  `src/gpo_studio/__init__.py` via hatchling. Package metadata and `__version__`
  are verified consistent in CI.
- Installed-package smoke test: CI builds wheel, installs in clean Python 3.13
  venv, verifies CLI entry point, API functionality (create GPO, add settings,
  export bundle, generate plan), static UI, workspace integrity check, sdist
  excludes, and sdist required files.
- GitHub Actions pinned by immutable commit SHA with least job permissions on
  all CI jobs.
- Dependency vulnerability scanning via `pip-audit` in CI.
- SBOM generation (CycloneDX) for the shipped wheel, uploaded as artifact.
- Static safety checks (`scripts/check_safety.py`): AST-based scan for
  forbidden imports (ldap, smb, win32, subprocess, shlex), unsafe XML parsing
  (ET.fromstring/ET.parse without bounded wrapper), and publication code in the
  web process.
- Identifier gate fail-closed behavior tested (`tests/test_identifier_gate.py`).
- `SECURITY.md` security policy with supported versions, vulnerability
  reporting, threat boundaries, deployment model, and known considerations.
- `CHANGELOG.md` (this file).
- `CONTRIBUTING.md` with the complete local gate, fixture-safety rules, and
  change expectations.
- `docs/installation.md` covering installation, configuration, data location,
  privacy, troubleshooting, Windows-lab compatibility, and a five-minute
  guided workflow.
- `docs/release-evidence.md` release evidence manifest with test summaries,
  Windows lab report, and known issues.
- A limited Windows smoke run exercised generated-plan GPO creation, DWORD and
  string registry commands, and side status. It found the empty-comment defect
  below, but does not satisfy the per-capability evidence matrix; all rows
  remain pending.
- Risk-based branch-coverage floors, bounded parser/codec properties, production
  dependency vulnerability and license checks, history secret scanning,
  reproducible builds, sdist installation, and an upgrade/rollback rehearsal.
- Tag-triggered release workflow producing checksums, CycloneDX SBOM, GitHub
  provenance/SBOM attestations, and exact-artifact installation tests. The
  sanitized Windows lab evidence report is attached to each release and
  covered by the checksums and attestation.
- Separate fail-closed publication gates for prerelease candidates and the
  final release: an RC can be published as immutable test material while the
  final tag still requires an explicitly approved evidence manifest. Attached
  evidence resolves the tagged commit and wheel, sdist, and SBOM hashes.
- `docs/windows-quickstart.md`: single-operator Windows installation guide
  requiring no Git, `uv`, IIS, or service installation.
- `docs/nvda-validation-runbook.md`: scripted manual NVDA screen-reader
  session for the open Plan 019 acceptance gate.
- Plan 032 (hardened hosted control plane) authored as the executable plan
  for Plan 001 Phase 3, sequenced before Plan 030 controlled publication.

### Changed

- DWORD and QWORD request values are represented as validated decimal strings
  at the JSON boundary, then converted to Python integers after range
  checking. Prevents browser `Number` precision loss for QWORD values
  above 2^53-1. Same contract applied to ADMX decimal and enum elements.
- Mutation validation centralized so API and store callers cannot diverge.
- GPP Registry model uses a one-value-per-item invariant matching the
  MS-GPPREF one-element-per-item model. Each `<Registry>` element maps to
  exactly one domain object with one value, one UID, one ILT filter, and
  one set of element metadata.
- Unknown CSE content is inventoried (file path, SHA-256 hash, size) and
  included in `review_model_sha256`, but GPMC backup export is blocked when
  unknown CSE content is present because the bytes cannot be faithfully
  reproduced.
- Identifier gate promoted to a required CI check with tested fail-closed
  behavior.
- Capability documentation now matches executable endpoints and export
  behavior. The roadmap is superseded by the capability matrix.
- PowerShell plan accuracy documented: registry values, links, security
  filtering, and side status are actionable; WMI filter assignment and GPP
  content are not applied by the plan and are included in GPMC backup
  export only.

### Security

- `cpassword` attributes structurally detected and rejected at every
  boundary (GPMC backup import, Studio bundle export, GPMC backup export,
  authoring). Detector covers namespace-qualified variants (e.g.
  `x:cpassword`) and mixed-case forms.
- Non-loopback binding refused without `GPO_STUDIO_UNSAFE_BIND=1`
  acknowledgment. The CLI exits with an explanatory error.
- XML entity declarations rejected rather than expanded (billion-laughs
  protection) on all XML parsers.
- Symlink rejection and path-traversal guards enforced on all archive and
  inbox import paths.
- Request body size streaming enforced with a 10 MiB ceiling.
- Logs and error bodies sanitized: policy values, SIDs, paths, and request
  bodies are never logged. Error messages do not leak absolute filesystem
  paths or SQL.
- PowerShell plan validated through a closed allowlist before execution,
  rejecting unexpected command shapes, aliases, backticks, and
  case-insensitive cmdlet variants.
- Claimed actor identity is untrusted in 1.0 and must never be treated as
  authenticated audit identity.

### Fixed

- GPP and ILT collection changes now update `policy_semantic_sha256`.
  Previously, modifying a GPP collection did not change the semantic hash,
  making it unusable as a review or approval boundary.
- Link and GPP concurrent edits produce three-way conflicts rather than
  silent merges.
- Max QWORD (2^64-1) round-trips from browser-shaped JSON without precision
  loss. Previously, browser `Number` serialization truncated values above
  2^53-1.
- Stale revision mutations fail with HTTP 409 instead of silently
  overwriting. Optimistic concurrency (`expected_revision`) enforced
  consistently.
- Legacy snapshot loading and metadata precedence corrected for pre-018
  workspace imports.
- Multi-string (`REG_MULTI_SZ`) browser rendering and newline splitting
  fixed; report values rendered as human-readable semicolon-separated text
  instead of Python representation syntax.
- GPP reorder conflict detection in two-way and three-way diff.
- GPP collection validated before persisting imported GPO content.
- Root-level XML attributes and unknown root children on `<Groups>` and
  `<RegistrySettings>` preserved through import/export round-trips.
- ILT predicate interleaving order of typed and unknown predicates
  preserved through round-trips.
- `cpassword` namespace-qualified bypass closed after adversarial review.
- GPP reorder kind narrowed to a `Literal` with regression test proving
  invalid kinds never enter the mutation transaction.
- Identical revision comparisons rejected before either revision is loaded.
- GPP module shared state cleared on dialog close, preventing cancel-cycle
  leaks.
- Generated PowerShell plan emitted `New-GPO -Comment ''` when GPO had no
  description, causing a validation error (New-GPO requires non-empty
  comment). Now emits `-Comment 'Created by GPO Studio'` as fallback. Found
  during a limited Windows smoke run.
- Generated PowerShell plan emitted `REG_BINARY` values as a bare
  `[byte[]](...)` cast, which fails parameter binding under Windows
  PowerShell 5.1. Now parenthesized (`([byte[]](...))`), with
  `([byte[]]@())` for empty values. Found during the Plan 017 WP-5 lab
  validation.
- `Registry.pol` serialization omitted the UTF-16LE null terminator on key
  and value-name strings that Windows includes. Serializer now emits the
  terminator; parser strips it and remains compatible with unterminated
  legacy files. Found during the Plan 017 WP-5 lab validation.
- GPMC backup `Registry.pol` included the `HKLM\`/`HKCU\` hive prefix in
  key paths; Windows infers the hive from the `Machine`/`User` directory
  and a prefixed key produces incorrect paths on import. Found during the
  Plan 017 WP-5 lab validation.
