# Release evidence manifest - GPO Studio 1.1.0

> **Date:** not set (draft opened 2026-10-08)
> **Source commit:** resolved by the tagged release workflow
> **Status:** draft; not approved for release and not a release candidate

This is the skeleton of the 1.1.0 manifest. It records what has been
requalified, what the release workflow now checks, and what 1.1.0 does **not**
claim. Every "to be resolved" item must be filled in, and every open row in
[the Plan 034 table](#plan-034-module-exits) must reach its exit, before the
release is approved.

## How this release is approved

The source of truth is the JSON report,
`docs/release-evidence-report-1.1.0.json`. Approving the release is two edits
made in one commit:

1. In the JSON report, set `"status"` to `"approved"` for the final release,
   or to `"candidate"` for a release candidate. Set `"version"` to the exact
   package version being tagged (`1.1.0`, or `1.1.0rc1` for the first
   candidate).
2. Replace line 5 of this file with the line `STATUS_LINES` in
   `scripts/check_release_manifest.py` gives for that status. It keeps the
   same prefix and ends in "approved for release" for the final release, or in
   "release candidate; final approval pending" for a candidate. (It is not
   quoted here because the gate allows that line exactly once in this file.)

Nothing else in this file needs to change.

### Writing rules for this file

The gate checks this file's bytes without rendering it, so some ordinary prose
is refused on purpose. Each refusal names its rule and suggests a rephrasing:

- **ascii:** printable ASCII and newlines only. Use `-` for dashes, straight
  quotes, `->` for arrows, spaces rather than tabs, and LF line endings.
- **no-html:** no left angle bracket anywhere, even inside backticks. Write
  `X.Y.Z` rather than a placeholder in angle brackets.
- **no-entities:** no character references (an ampersand followed by `#`, or
  by a name and a semicolon). Spell the character out instead.
- **no-code-blocks:** no code fences and no indented code, including inside
  quotes and list items (no fence after a `>` or list marker, and never four or
  more spaces after one). Use inline code, and indent list continuations by two
  or three spaces.
- **plain-links:** links only as `[text](path)` or `[text][ref]`, with a bare
  path or URL (no spaces, parentheses or title), and never touching a letter or
  digit on either side. A `[` straight after a letter is refused even in
  inline code, so write "item 0 of the list" rather than indexing syntax.
- **one-status (a single status line):** the word "status" followed by a
  colon appears only on line 5, and no other line may start with the word or
  be a heading containing it. Rephrase, for example "where X stands" or "open
  items".

## How the release gate reads this

`release.yml` runs `scripts/check_release_manifest.py` before anything else
and again in the publish job. For a tag `vX.Y.Z` or `vX.Y.Z-rc.N` it requires:

- `src/gpo_studio/__init__.py` to bind `__version__` exactly once, to exactly
  that version (`X.Y.Z` or `X.Y.ZrcN`), and Hatchling to read the same value.
  Development and other pre-release versions are refused;
- the JSON report to match a strict schema: exact keys and types, no duplicate
  keys, `version` equal to the package version, and `status` equal to
  `approved` for a final tag or `candidate` for an RC tag. `draft` never
  releases;
- this file to hold a lexical contract, checked on its bytes without rendering
  it: printable ASCII and newlines only; no raw HTML, character references,
  code fences or deeply indented code; plain links only, never spliced into a
  word; the fixed header above, with line 5 matching the JSON status; and the
  word "status" followed by a colon nowhere except line 5, with no heading or
  line elsewhere that reads as a status declaration;
- before anything is attested, the built wheel's `METADATA`, its file name
  and the sdist's `PKG-INFO` to carry the approved version.

`docs/release-evidence.md` is the **1.0.0** manifest. It cannot approve any
other version. Before this gate, the workflow grepped that file for an
approval string, so a `v1.1.0` tag would have published on 1.0.0's approval.
`tests/test_release_manifest_gate.py` holds that case closed, together with
every bypass found by four rounds of review of the earlier, rendering-based
gate.

The publish job also requires, for the exact tagged commit:

- every job in `ci.yml`, called as a reusable workflow: Linux and Windows
  test matrices (`test`, `test-windows`), frontend and browser tests, installed
  package, dependency audit, SBOM, static safety, identifier-gate fail-closed
  test, PowerShell analysis, property tests and secret scan;
- the `identifier-gate.yml` gate, with the denylist secret passed explicitly;
- the existing `verify` job, now on a full-history checkout;
- the tagged commit to be reachable from `origin/main`;
- immediately before `gh release create`, the remote tag to still peel to the
  commit the run built.

## What 1.1.0 is

The [2026-10-07 direction](direction-2026-10-07-plan-034-completion.md) makes
1.1.0 the release that carries Plan 034's result, with a deadline of
2026-10-31. The 1.0 capability contract in
[`capability-matrix.md`](capability-matrix.md) is unchanged. Post-1.0 layers
count as 1.1.0 capabilities only if they meet both halves of the exit
condition in [`domain-layer-status.md`](domain-layer-status.md): a re-runnable
evidence lane, **then** a delivery surface an operator can reach.

## Requalified evidence: the Plan 034 batch

> Lane verdicts as banked; release requalification pending. A final
> requalification of every lane at one commit follows batch 2, and this section
> will cite that run. Until then each verdict below is the one banked when its
> lane landed, not a release-level certification.

The [requalification batch](plan-033/plan034-batch.md) ran on 2026-10-08:

- 22 runs passed on frozen commit `263f19640529d469c2a54c18b43d228db5378279`
  with a clean source tree: WP-0's manifest plus 21 schema-version-2 lane
  verdicts, including the computer group-deny lane, which had not passed since
  the WI-059 batch;
- one successor: the object-security lane, re-run at
  `1fb3f56ac7431e0044c69c32edc4350b2ab84151` after a serializer review fix
  (`object-security-20261008082348-9729`, 20/20). The batch's own
  object-security verdict is retired;
- `PENDING_REQUALIFICATION` in `tests/test_committed_evidence.py` is empty;
- the [batch manifest](plan-033/plan034-batch.json) records every banked
  file's SHA-256, and the post-batch directory check confirms cleanup.

Verdicts bind their source files by `(commit, path, sha256)`. An edit to a
bound file between `1fb3f56` and the tag expires the verdicts that bind it.
`tests/test_committed_evidence.py` runs in CI, which the release now requires,
so a release cannot publish over an expired verdict without first changing
that test. See [`bound-source-cost.md`](plan-033/bound-source-cost.md).

The batch re-earns existing lanes. It does not by itself move any module to
`yes`.

## Lanes banked after the batch

Four new lanes were certified on 2026-10-08 and banked with their evidence
packs (lane verdicts as banked; release requalification pending).
`tests/test_committed_evidence.py`, which the release now requires on the
tagged commit, holds every live verdict to the source bytes it binds. All four
ran on the estate member server (Windows Server 2025, build 26100, Windows
PowerShell 5.1).

| Lane | Certifying run | Commit | What it certifies |
|---|---|---|---|
| firewall | `firewall-20261008094055-2092337` (36/36) | `a6e0002` | `firewall_policy.py` for one measured tranche: the 13 rule shapes and the Domain and Private profile literals. Read leg: rules authored with `New-NetFirewallRule -PolicyStore` parse with zero unrecognised records and equal the authored policy. Write leg: `Import-GPO` of Studio's backup returns Studio's Registry.pol byte for byte. [Results](plan-033/firewall-results.md) |
| lifecycle (same-domain) | `lifecycle-20261008093248-2000-c76d10eb3f2849fe` (all 30 cells) | `3513052` | `lifecycle.SCOPE_SURVIVAL`: five GPMC operations by six scope dimensions, each agreeing with what Windows did, plus the five plan-identity claims and the backup bridge. One topology. [Results](plan-033/lifecycle-results.md) |
| fdeploy (read target) | `fd-20261008121347-3151` (29/29) | `df713ef` | `fdeploy.py`'s reader for four shapes: R3's GPMC-written `fdeploy1.ini` verbatim (`Flags=1021`) and three builder-written `Flags`-only variants (1020, 1023, 3069). `Import-GPO` placed the exact bytes, `Backup-GPO` re-exported them byte for byte, and Studio's reading of Windows' own backup agreed with a fresh `Get-GPOReport` row for row (folder, principal SID, destination). [Results](plan-033/fdeploy-results.md) |
| report-parity | `report-parity-20261008104512-7480` (25/25, 27/27 cases) | `a1c280b` | `backup.py` / `report.py` over the existing import and plain-text report surfaces, for registry (`REG_SZ`/`REG_DWORD`), Drive Maps, Environment, Files, Folders, Ini Files, Local Users and Groups, Printers, Scheduled Tasks, Services and Shortcuts. [Results](plan-033/report-parity-results.md) |

What these runs do **not** certify:

- **Power Options** is not certified. Its only case passes on a pinned known
  divergence, WI-072 (the power plan is dropped on write). WI-073 (scheduled
  and immediate task interleaving is lost on write) is also open and accepted
  only as a pinned divergence. Both are fixed in code (fixed pending
  requalification) and are certified only when the requalification's
  report-parity run, which now requires their cases to agree exactly, banks.
  Seven Studio preference families have no
  capture and are not claimed, and ADMX policy rendering, Scripts, links,
  security filtering and WMI filters are named exclusions of the report-parity
  lane.
- **Firewall:** PolicyStore readback, not endpoint application; one build;
  values outside the tranche are refused; GPME display is unmeasured
  (WI-077). Batch 2 changed Studio to register the firewall snap-in's tool
  GUID for firewall-only policy, as native authoring does; the banked run
  predates that change.
  IPsec, Public Key, wired and wireless policy are out of scope for 1.x.
- **fdeploy:** decoding `Flags`, other `Flags` values, multi-folder and
  multi-principal documents, and any writer. `Flags` decoding and the writer
  stay deferred under WI-066 until R12. Windows' option rendering per `Flags`
  is recorded as data for WI-066, not asserted.
- **Lifecycle:** one topology (one source, one target, one member server, one
  DC). A deleted GPO's restore, multi-DC replication, `Import-GPO -TargetName`
  into an existing GPO, deny ACEs and the WMI filter object are unmeasured.
  The cross-domain half is out of scope until the estate has a second domain
  or a trust.

## Batch 2 changed bound files

Batch 2 (WI-075: GPP Registry native export, deterministic archives,
report parity for GPP Registry, and the review fixes that followed) edited
files that live lane verdicts bind: `gpp.py`, `export.py`, `publication.py`,
`xml_safety.py`, `object_security.py`, `report_parity.py`, the new
`deterministic_zip.py`, and several lane builders and finalizers. The
publication, scripts-metadata, WP-1B, WP-2, report-parity, firewall,
object-security, fdeploy and endpoint verdicts cited in this file were banked
before batch 2, so they no longer bind the shipping code, and
`tests/test_committed_evidence.py` reports them as stale until they are re-run.
The single requalification of every lane at one commit, listed under
remaining work below, covers them: it runs on the batch-2 commit and replaces
every banked verdict cited here. The report-parity corpus grows from 27 to 30
cases in that run (the three GPP Registry captures), and the WP-1B candidate
set gains a GPP Registry candidate.

## Plan 034 module exits

State as of 2026-10-08, after every Plan 034 exit landed (no exit is open),
matching the
[capability matrix](capability-matrix.md), the
[Plan 034 status line](../plans/034-post-1.0-layer-reconciliation.md) and the
[rulings](direction-2026-10-07-plan-034-completion.md). The last column says
what 1.1.0 may claim. It is filled in at the release cut and may only say
"capability" for a row whose lane **and** surface both exist.

| Module | Ruling | Lane | Surface | 1.1.0 claim |
|---|---|---|---|---|
| `policy_families.py` | `yes` | certified (WP-3 member and DC) | `POST /api/security-template/policy-families` | capability: emission direction only |
| `object_security.py` | `yes` | certified (successor at `1fb3f56`) | `POST /api/security-template/object-security` | capability: registry, file-system and service security; **restricted groups are lane-certified but not surfaced** |
| `security_template.py` | exits through its consumers | bound by three live verdicts | via the two endpoints above | no standalone claim; reading GPME-authored `GptTmpl.inf` is out of scope |
| `publication.py` | `yes` | publication completeness (21/21 at `263f196`) | `GET /api/gpos/{guid}/publication-plan` and the Publication preview panel (review-only) | capability: review-only preview; steps marked `measured`, `unmeasured` or `refused`; nothing writes |
| `script_policy.py` | `yes` | Scripts metadata (20/20 at `263f196`) | `POST /api/gpos/{guid}/gpmc-backup-with-scripts` (+ `/preview`) and the Scripts panel | capability: the measured shape only; unmeasured shapes refused with 422 |
| `fdeploy.py` | lane, or the writer stays deferred (WI-066) | fdeploy read lane (`fd-20261008121347-3151`, 29/29 at `df713ef`) | `POST /api/folder-redirection/fdeploy`, imported backups' report and diffs, and the Folder Redirection review panel | capability: reading the four measured shapes; `Flags` decoding and the writer stay deferred under WI-066 |
| `network_security.py` / `firewall_policy.py` | firewall: codec, lane, surface; the rest out of scope | firewall (`firewall-20261008094055-2092337`, 36/36 at `a6e0002`) | `POST /api/network-security/firewall/render`, `GET /api/gpos/{guid}/firewall-policy` | capability: the measured tranche only, everything else refused; IPsec, Public Key, wired and wireless are not claimed |
| `lifecycle.py` | same-domain lane plus restore-plan surface; cross-domain out of scope | same-domain lifecycle (`lifecycle-20261008093248-2000-c76d10eb3f2849fe`, 30/30 cells at `3513052`) | `POST /api/lifecycle/restore-plan` (review only) | capability: same-domain restore plans over GPOs imported from a Windows backup; cross-domain is refused and not claimed |
| `backup.py` / `report.py` | report-parity lane for modelled families | report-parity (`report-parity-20261008104512-7480`, 25/25 at `a1c280b`) | existing backup import and plain-text report | capability: report parity for the families listed above; Power Options and the uncaptured families are not claimed |
| `artifact_store.py` | deleted | n/a | n/a | not shipped |
| `software_install.py` | deleted | n/a | n/a | not shipped |
| `folder_redirection.py` | deleted (superseded by `fdeploy.py`) | n/a | n/a | not shipped |
| `gpmc_interop.py` | reduced to `InteropIssue` | n/a | n/a | no claim |
| `publisher.py`, `hosting.py` | out of scope for 1.x; code retained | n/a | n/a | not capabilities; no hosted mode |

Plan 034's review gate stands: no module leaves the plan `capture-backed`.

## Automated evidence

To be resolved from the tagged release workflow run: run IDs for every
required job, the coverage summary, and the artifact hashes below. Local
success is not release evidence.

Local pre-release observation, **not release evidence**: on 2026-10-08, on the
`release/1.1.0-prep` working tree, the frontend checks (Prettier, ESLint, 97
Vitest tests) and 50 Playwright browser tests passed on this host. The Python
suite result for that tree is in the commit that adds this file.

## Accessibility evidence

Automated, run by the `frontend` CI job and the release `verify` job:

- the workspace axe scan (no serious or critical findings), keyboard tab
  order and dialog focus semantics;
- the dark theme, held to the same axe bar as the light theme;
- the Security template dialog's own axe scan, needed because the
  workspace-wide scan runs with every dialog closed;
- the Folder Redirection dialog, populated, with an axe scan;
- the RSOP prediction dialog, scanned in the light and the dark theme in each
  state that renders its own markup: the empty prompt, a conclusive result, a
  result with warnings and a blocked GPO, an inconclusive result (the server's
  answer altered in the test, because no topology the engine accepts yields an
  unevaluable GPO), a topology refused before the request, and one refused by
  the server's validation (`tests/browser/rsop.spec.mjs`).

The RSOP scan found one serious finding, now fixed: a result table wide
enough to scroll sideways was not reachable from the keyboard (axe rule
`scrollable-region-focusable`; it showed once a blocked GPO's reasons widened
the GPO table). Each RSOP result table now sits in a focusable region named
after its heading, as the Scripts preview's file contents already did.

Gaps, stated rather than implied:

- the 1.0.0 hands-on NVDA acceptance covered the 1.0 interface only. The RSOP,
  Security template and Folder Redirection panels, the dark theme, the sticky
  row actions and the sidebar navigation have had no hands-on screen-reader
  session. One is required before 1.1.0 is approved: the owner runs the
  [NVDA validation runbook](nvda-validation-runbook.md) against the exact
  `v1.1.0-rc.N` wheel. It has not been run.

## Upgrade and rollback

- Workspace schema moves from 1 (1.0.0) to 4. Opening a 1.0.0 workspace with
  1.1.0 migrates it in place, without making a backup.
- `tests/fixtures/release-1.0.0-workspace/` holds a workspace **written by the
  1.0.0 release itself**: the `v1.0.0` tag, installed from its own lockfile,
  driven through its HTTP API by
  `scripts/generate_release_workspace_fixture.py`, with a 1.0.0-written backup
  and sidecar. Two synthetic GPOs, 19 revisions. The provenance pins the
  writer (the `v1.0.0` commit, its `uv.lock` digest and a digest of the
  installed package files), and the test re-derives all three from the tag.
- `tests/test_release_upgrade_from_1_0_0.py` shows the upgrade is lossless.
  Every stored snapshot is byte-identical after migration. Every field 1.0.0
  served is served unchanged. Fields added since 1.0.0 hold only empty
  defaults. Exported `Registry.pol` files, decoded by an independent reader
  in the test, carry exactly the records 1.0.0 exported and every setting
  1.0.0 served. A mutation that drops REG_QWORD records fails two tests. The
  workspace accepts new revisions.
- Rollback is **restoring the pre-upgrade backup**, not downgrading.
  1.0.0 refuses an upgraded workspace (`Workspace schema version 4 is newer
  than this version of GPO Studio supports (1)`, exit 3) and refuses to
  restore a backup of one. Both were observed by running the 1.0.0 release.
  The test also re-runs 1.0.0's schema guard from the tag against the current
  upgrade output. Restoring the 1.0.0-written pre-upgrade backup with 1.0.0
  served the original workspace again.
- `scripts/rehearse_upgrade_rollback.py`, run in CI and against the exact
  release wheel, now rehearses this fixture as well as the synthetic schema-0
  one.
- **Known issue in 1.0.0 (WI-074):** `workspace check` writes into the file
  it checks, so checking a backup makes it unrestorable (`Backup database
  checksum mismatch`). Observed with 1.0.0 and recorded in the fixture's
  provenance. 1.1.0's check is read-only. The runbooks verify a backup through
  a throwaway restored copy, which is safe under either release. The
  documented backup, verify, upgrade and rollback steps were executed end to
  end with the v1.0.0 wheel and a 1.1.0-labelled wheel of this tree.
- Operator procedure: [Windows quickstart](windows-quickstart.md#upgrade-to-another-release)
  and [workspace recovery](workspace-recovery.md#upgrading-and-rolling-back-across-a-schema-change).

These outputs differ from what 1.0.0 produced for the same content. None loses
data. Each is pinned by a test:

1. `Registry.pol` records within a key are ordered delete-all-values, delete,
   then set. 1.0.0 ordered them by value name alone, so a GPO with a delete
   action exports the same records in a different byte order, with a
   different hash.
2. For a GPO with preference items, the policy-semantic and review digests
   change. The canonical form gained empty entries for the preference families
   added since 1.0.0. No value changes.
3. A GPO with a GPP Registry item offers native GPMC backup export again, as
   1.0.0 did. Between WP-1B and batch 2 it was refused, because native GPP
   output is an allowlist of families whose extension metadata was captured;
   batch 2 captured GPP Registry's pair and every item shape (WI-075). A
   default-value item is still refused. GPP Registry values are now written
   in Windows' form (fixed-width hex for DWORD and QWORD, a `Values` list for
   multi-strings), so a GPP Registry file's bytes differ from 1.0.0's.
4. Every archive (GPMC backup and Studio bundle) is written with stored, not
   deflated, members in code-point order, so the same content yields the same
   bytes on every platform. The archives are larger and hash differently from
   1.0.0's; the member contents do not change.

## Not claimed by 1.1.0

- **No AD or SYSVOL write path.** The web process writes nothing to Active
  Directory or SYSVOL. Publication remains a separate human action.
- **Surfaced but not Windows-verified:** `som.py`, `delegation.py`,
  `ad_discovery.py`, `wmi_filter.py` (Plan 023) and `gpp_adapters.py`
  (Plan 024). They are reachable from the API, and no independent Windows
  oracle has checked their output. They are carried as stated limitations, not
  Plan 034 modules.
- **Landed but not surfaced:** every module in the table above whose surface
  is open at the cut, and restricted groups. An unsurfaced module is not a
  capability, whatever its lane says.
- **Out of scope for 1.x:** IPsec, Public Key, wired and wireless policy;
  cross-domain lifecycle; delivering script or executable payloads; writing
  Software Installation; a Folder Redirection writer and `Flags` decoding
  unless WI-066 is answered; `publisher.py` and `hosting.py` as capabilities.
- **Not certified within certified lanes:** Power Options report parity
  (WI-072), task interleaving on write (WI-073), the seven uncaptured
  preference families, firewall values outside the measured tranche, fdeploy
  documents beyond the four measured shapes, and the lifecycle cases listed
  under the banked lanes above.
- **Deployment profile unchanged:** one operator on loopback, no
  authentication, no TLS, no hosted or multi-user mode. The `actor` on each
  revision is claimed, not authenticated.
- **1.0.0 limitations that still stand:** the PowerShell plan does not apply
  WMI filter assignments, GPP or ILT predicates. GPMC backup export covers a
  certified subset only.

## Schema and artifact identity

- Workspace schema version: 4
- Application version: 1.1.0
- Source commit: resolved in the release attachment by the tagged workflow
- Wheel SHA-256: resolved in the release attachment and `SHA256SUMS`
- Source distribution SHA-256: resolved in the release attachment and `SHA256SUMS`
- CycloneDX SBOM SHA-256: resolved in the release attachment and `SHA256SUMS`
- Release checksums and provenance attestations: generated for the release tag

## Remaining before approval

1. Every Plan 034 row has reached its exit (done 2026-10-08). Requalify every
   lane at one commit after batch 2 and replace the banked verdicts cited here
   with that run's.
2. Bump `__version__` (to `1.1.0rc1` for a candidate, or to `1.1.0`), date
   the changelog section, and update `SECURITY.md`'s supported-versions table
   and 1.x compatibility policy for the `1.1.x` line.
3. Decide whether a release candidate and a hands-on screen-reader session are
   required. If they are, publish `v1.1.0-rc.N` with the candidate status line,
   and record the session against that exact wheel.
4. Fill in the automated-evidence section from the tagged run, then finalize
   `docs/release-evidence-report-1.1.0.json`.
5. Approve as described in [How this release is approved](#how-this-release-is-approved)
   (the JSON `status` and `version`, and line 5 here, in one commit), and tag
   `v1.1.0` on a commit on `main`.
