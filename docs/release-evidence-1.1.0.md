# Release evidence manifest - GPO Studio 1.1.0

> **Date:** 2026-10-09 (release candidate 1.1.0rc1; draft opened 2026-10-08)
> **Source commit:** resolved by the tagged release workflow
> **Status:** release candidate; final approval pending

This is the 1.1.0 manifest, marked for the first release candidate,
`1.1.0rc1`, tagged `v1.1.0-rc.1`. It records what has been requalified, what
the release workflow now checks, and what 1.1.0 does **not** claim. Every Plan
034 row has reached its exit, and every lane was requalified at one commit by
the [release 1.1.0 batch](#requalified-evidence-the-release-110-batch).

The candidate is published as a GitHub prerelease so that its wheel and
checksum are fixed for the owner's hands-on NVDA acceptance. It is not
approved for production use or as the final 1.1.0 release. What is still open
before that approval is listed under
[Remaining before approval](#remaining-before-approval).

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

This file and the JSON report are now marked for the candidate `1.1.0rc1`.
Final approval makes the same two edits for `approved` and `1.1.0`, after the
items under [Remaining before approval](#remaining-before-approval) are
closed; the package version, changelog and `SECURITY.md` change with it.

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

## Requalified evidence: the release 1.1.0 batch

The [release 1.1.0 requalification batch](plan-033/release110-batch.md) ran on
2026-10-09 (UTC). **All 26 lanes passed** on one frozen commit,
`de9736ed3a4148b91cf2267fbe4640e260cc232f`, every one with a clean source tree
(`source.dirty=false`): WP-0's manifest plus 25 lane verdicts. That commit
carries batch 2 (WI-075), the chunked psdirect transport of WI-078 and the GPP
fixes of WI-072, WI-073 and WI-079. The release is qualified by one commit.

- The [batch manifest](plan-033/release110-batch.json) (schema 2) is the
  record: every run id, verdict path, window, exit and banked file's SHA-256.
  Every row states `test_scope_tool: false`, and no lane timed out, was
  cancelled, lost containment, failed its scope or left a process behind.
  `not_passed` and `successors` are empty.
- The batch was driven by `scripts/plan-033/run-requal-batch.sh` under its
  per-lane watchdog and banked by `scripts/plan-033/bank-requal-batch.py`,
  which checked every staged file against the controller by SHA-256. Every
  passing run has an `evidence/` tag at `de9736e`, named after its run id.
- It retired every verdict live before it: the Plan 034 batch's 21 at
  `263f196`, its object-security successor at `1fb3f56`, and the four lanes
  banked after that batch (lifecycle at `3513052`, report parity at `a1c280b`,
  the firewall at `a6e0002`, fdeploy at `df713ef`). Their packs and tags are
  unchanged and certify nothing for this release. This is the first batch to
  run the lifecycle, report-parity, firewall and fdeploy lanes.
- `PENDING_REQUALIFICATION` in `tests/test_committed_evidence.py` is empty.
- The [post-batch directory check](plan-033/release110-cleanup/directory.json),
  captured at 2026-10-09T00:51:59Z after the last lane, is clean: the computer
  and user account are restored, and no experiment OUs, GPOs, groups or WMI
  filters survived. The operator ran the committed
  [collector](plan-033/release110-cleanup/collector.ps1), whose bytes the
  manifest hashes, read-only on the lab domain controller through the frozen
  worktree's `psdirect.ps1`.

### The superseded attempt at 2f21e7c

The first attempt ran all 26 lanes at
`2f21e7cd9fcf47bdf216a68b395ce18fd5a1e580` (batch 2 alone) on 2026-10-08, and
25 passed. The report-parity lane's push of its 1.45 MB archive stalled on an
idle WinRM connection (`Copy-Item -ToSession` from the Linux controller stalls
above roughly 256 KB), and nothing timed out because neither the transport nor
the driver had a wall-clock bound. The operator killed the lane after an hour
(exit 143); a re-run of that lane alone hung the same way.

The fix, WI-078, is a chunked, verified and bounded transport plus a per-lane
watchdog in the driver. It changed `scripts/windows-oracle/psdirect.ps1`, which
every lane binds, so every verdict of the attempt stopped binding before it
could be banked. The attempt is recorded under `superseded_attempts` in the
batch manifest (commit, run ids, windows and the killed lane). Its packs are
not in the repository; its evidence tags are kept as history.

### The runs

All 26 at `de9736e`, as the batch manifest records them:

| Experiment | Passing run | Checks |
|---|---|---|
| wp0 | `live-synthetic-registry-basic-20261009001124-4850` | pass |
| wp1b | `wp1b-writer-20261009001221-5737` | pass |
| wp2 | `wp2-native-import-20261009001318-2870` | 18/18 |
| wp3-member | `wp3-security-template-20261009001358-5650` | 20/20 |
| wp3-dc | `wp3-security-template-20261009001426-5385` | 20/20 |
| object-security | `object-security-20261009001510-7162` | 20/20 |
| scripts-metadata | `scripts-r10-20261009001541-4025` | 20/20 |
| publication | `publication-completeness-20261009001615-4372` | 21/21 |
| lifecycle | `lifecycle-20261009001644-6217-f2f5047fed814807` | 73/73 |
| report-parity | `report-parity-20261009001727-3532` | 26/26 |
| firewall | `firewall-20261009001906-2614294` | 36/36 |
| fdeploy | `fd-20261009002120-4293` | 29/29 |
| endpoint | `endpoint-observe-20261009002229-7198` | pass |
| lsdou-precedence | `rsop-observe-20261009002514-3293` | pass |
| disabled-block-enforced | `rsop-observe-20261009002658-3387` | pass |
| wmi-filtering | `rsop-observe-20261009002841-9327` | pass |
| wmi-filtering-error | `rsop-observe-20261009003024-5445` | pass |
| computer-security-filtering | `rsop-observe-20261009003207-9958` | pass |
| computer-security-filtering-deny-read | `rsop-observe-20261009003349-5735` | pass |
| loopback-merge | `rsop-user-observe-20261009003535-1462` | pass |
| loopback-replace | `rsop-user-observe-20261009003744-5527` | pass |
| user-side-disabled | `rsop-user-observe-20261009003952-3546` | pass |
| user-security-filtering | `rsop-user-observe-20261009004238-6019` | pass |
| user-security-filtering-deny | `rsop-user-observe-20261009004533-3353` | pass |
| user-security-filtering-read-deny | `rsop-user-observe-20261009004749-4067` | pass |
| computer-security-filtering-group-deny | `rsop-observe-20261009005022-4207` | pass |

Verdicts bind their source files by `(commit, path, sha256)`. An edit to a
bound file between `de9736e` and the tag expires the verdicts that bind it.
`tests/test_committed_evidence.py` runs in CI, which the release requires on
the tagged commit, so a release cannot publish over an expired verdict without
first changing that test. See [`bound-source-cost.md`](plan-033/bound-source-cost.md).

The batch re-earns existing lanes. It does not by itself move any module to
`yes`.

## What the newer lanes certify

The four lanes first certified on 2026-10-08 ran in the batch for the first
time. All four ran on the estate member server (Windows Server 2025, build
26100, Windows PowerShell 5.1).

| Lane | Certifying run (at `de9736e`) | What it certifies |
|---|---|---|
| firewall | `firewall-20261009001906-2614294` (36/36) | `firewall_policy.py` for one measured tranche: the 13 rule shapes and the Domain and Private profile literals. Read leg: rules authored with `New-NetFirewallRule -PolicyStore` parse with zero unrecognised records and equal the authored policy. Write leg: `Import-GPO` of Studio's backup, now registered with the firewall tool's native GUID pair (`B05566AC`), returns Studio's Registry.pol byte for byte. [Results](plan-033/firewall-results.md) |
| lifecycle (same-domain) | `lifecycle-20261009001644-6217-f2f5047fed814807` (73/73; all 30 cells) | `lifecycle.SCOPE_SURVIVAL`: five GPMC operations by six scope dimensions, each agreeing with what Windows did, plus the five plan-identity claims and the backup bridge. One topology. [Results](plan-033/lifecycle-results.md) |
| fdeploy (read target) | `fd-20261009002120-4293` (29/29) | `fdeploy.py`'s reader for four shapes: R3's GPMC-written `fdeploy1.ini` verbatim (`Flags=1021`) and three builder-written `Flags`-only variants (1020, 1023, 3069). `Import-GPO` placed the exact bytes, `Backup-GPO` re-exported them byte for byte, and Studio's reading of Windows' own backup agreed with a fresh `Get-GPOReport` row for row (folder, principal SID, destination). [Results](plan-033/fdeploy-results.md) |
| report-parity | `report-parity-20261009001727-3532` (26/26; 30 corpus cases plus a guest-authored GPO) | `backup.py` / `report.py` over the existing import and plain-text report surfaces, for registry (`REG_SZ`/`REG_DWORD`), Drive Maps, Environment, Files, Folders, GPP Registry (the three WI-075 captures), Ini Files, Local Users and Groups, Power Options, Printers, Scheduled Tasks (including the interleaving of scheduled and immediate tasks), Services and Shortcuts, at the level of each preference item's element, name, `uid`, action and document order (not its other `Properties` attributes). No Studio defect is accepted. [Results](plan-033/report-parity-results.md) |

What these runs do **not** certify:

- **Report parity:** six Studio preference families have no capture and are
  not claimed (Regional Options, Devices, Folder Options, Data Sources,
  Network Shares, Applications). ADMX policy rendering, Scripts, links,
  security filtering and WMI filters are named exclusions. Property-level
  equivalence of preference items is not claimed: the lane compares each
  item's element, name, `uid`, action and document order as the typed model
  writes them, and the model does not type several `Properties` attributes
  (for example Drives' `thisDrive` and `allDrives`). Since WI-080 an export of
  a stored import writes those attributes, and each item's `FilterRunOnce`
  id, back as imported; `tests/test_gpp_native_preservation.py` holds that to
  the native captures offline, and no lane certifies it. The first run's two
  pinned divergences, WI-072 (Power Options' power plan dropped on write) and
  WI-073 (task interleaving lost on write), are fixed: the lane's
  `fixed_work_item_cases_agree_exactly` check required their three cases to
  agree with Windows exactly, and they did.
- **Firewall:** PolicyStore readback, not endpoint application; one build;
  values outside the tranche are refused; GPME display is unmeasured
  (WI-077). IPsec, Public Key, wired and wireless policy are out of scope for
  1.x.
- **fdeploy:** decoding `Flags`, other `Flags` values, multi-folder and
  multi-principal documents, and any writer. `Flags` decoding and the writer
  stay deferred under WI-066 until R12. Windows' option rendering per `Flags`
  is recorded as data for WI-066, not asserted.
- **Lifecycle:** one topology (one source, one target, one member server, one
  DC). A deleted GPO's restore, multi-DC replication, `Import-GPO -TargetName`
  into an existing GPO, deny ACEs and the WMI filter object are unmeasured.
  The cross-domain half is out of scope until the estate has a second domain
  or a trust.

## Plan 034 module exits

Every Plan 034 exit landed on 2026-10-08 (no exit is open). The table matches
the [capability matrix](capability-matrix.md), the
[Plan 034 status line](../plans/034-post-1.0-layer-reconciliation.md) and the
[rulings](direction-2026-10-07-plan-034-completion.md), and every lane it
cites is the release 1.1.0 batch's run at `de9736e`. The last column says what
1.1.0 may claim. It says "capability" only for a row whose lane **and**
surface both exist.

| Module | Ruling | Lane | Surface | 1.1.0 claim |
|---|---|---|---|---|
| `policy_families.py` | `yes` | certified (WP-3 member and DC, 20/20 each) | `POST /api/security-template/policy-families` | capability: emission direction only |
| `object_security.py` | `yes` | certified (`object-security-20261009001510-7162`, 20/20) | `POST /api/security-template/object-security` | capability: registry, file-system and service security; **restricted groups are lane-certified but not surfaced** |
| `security_template.py` | exits through its consumers | bound by three live verdicts | via the two endpoints above | no standalone claim; reading GPME-authored `GptTmpl.inf` is out of scope |
| `publication.py` | `yes` | publication completeness (`publication-completeness-20261009001615-4372`, 21/21) | `GET /api/gpos/{guid}/publication-plan` and the Publication preview panel (review-only) | capability: review-only preview; steps marked `measured`, `unmeasured` or `refused`; nothing writes |
| `script_policy.py` | `yes` | Scripts metadata (`scripts-r10-20261009001541-4025`, 20/20) | `POST /api/gpos/{guid}/gpmc-backup-with-scripts` (+ `/preview`) and the Scripts panel | capability: the measured shape only; unmeasured shapes refused with 422 |
| `fdeploy.py` | lane, or the writer stays deferred (WI-066) | fdeploy read lane (`fd-20261009002120-4293`, 29/29) | `POST /api/folder-redirection/fdeploy`, imported backups' report and diffs, and the Folder Redirection review panel | capability: reading the four measured shapes; `Flags` decoding and the writer stay deferred under WI-066 |
| `network_security.py` / `firewall_policy.py` | firewall: codec, lane, surface; the rest out of scope | firewall (`firewall-20261009001906-2614294`, 36/36) | `POST /api/network-security/firewall/render`, `GET /api/gpos/{guid}/firewall-policy` | capability: the measured tranche only, everything else refused; IPsec, Public Key, wired and wireless are not claimed |
| `lifecycle.py` | same-domain lane plus restore-plan surface; cross-domain out of scope | same-domain lifecycle (`lifecycle-20261009001644-6217-f2f5047fed814807`, 73/73, all 30 cells) | `POST /api/lifecycle/restore-plan` (review only) | capability: same-domain restore plans over GPOs imported from a Windows backup; cross-domain is refused and not claimed |
| `backup.py` / `report.py` | report-parity lane for modelled families | report-parity (`report-parity-20261009001727-3532`, 26/26, 30 cases) | existing backup import and plain-text report | capability: item-level report parity (element, name, `uid`, action, order) for the families listed above, not property-level; the six uncaptured families are not claimed |
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
candidate branch at `a4a3686` (Python 3.14 on a Linux development host; the
workflow uses Python 3.13), the full Python suite passed (6361 passed, 15
skipped) with total branch coverage of 89.94 percent against the 84 percent
floor, as did ruff, mypy, `check_safety.py`, `rehearse_upgrade_rollback.py`,
the frontend checks (Prettier, ESLint, 148 Vitest tests) and 67 Playwright
browser tests (Chromium, plus the Firefox smoke subset). From a clean export
of that commit, two builds with `SOURCE_DATE_EPOCH=315532800` produced
byte-identical wheels and source distributions, the gate accepted both for
`v1.1.0-rc.1`, and the installed-package smoke and the upgrade rehearsal
passed against that wheel. The tagged run rebuilds and rechecks everything;
its artifacts, not these, are the release.

## Accessibility evidence

Automated, run by the `frontend` CI job and the release `verify` job:

- the workspace axe scan (no serious or critical findings), keyboard tab
  order and dialog focus semantics;
- the dark theme, held to the same axe bar as the light theme;
- the Security template dialog's own axe scan, needed because the
  workspace-wide scan runs with every dialog closed, and a scan of a rendered
  template in a 360-pixel-wide window;
- the Folder Redirection dialog, populated, with an axe scan, at desktop width
  and in a 360-pixel-wide window;
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
after its heading, as the Scripts preview's file contents already did. The
same defect was then looked for in the other new result panels: the Security
template's validation-issue table had it in a narrow window (the new scan
failed before the fix), and it and the rendered `GptTmpl.inf` block are now
focusable named regions too. The Folder Redirection table wraps rather than
scrolls and passed unchanged.

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
- **Not certified within certified lanes:** the six uncaptured preference
  families, firewall values outside the measured tranche, fdeploy documents
  beyond the four measured shapes, and the lifecycle cases listed under
  [What the newer lanes certify](#what-the-newer-lanes-certify).
- **Deployment profile unchanged:** one operator on loopback, no
  authentication, no TLS, no hosted or multi-user mode. The `actor` on each
  revision is claimed, not authenticated.
- **1.0.0 limitations that still stand:** the PowerShell plan does not apply
  WMI filter assignments, GPP or ILT predicates. GPMC backup export covers a
  certified subset only.

## Schema and artifact identity

- Workspace schema version: 4
- Application version: 1.1.0
- Package version of this candidate: 1.1.0rc1 (tag `v1.1.0-rc.1`)
- Source commit: resolved in the release attachment by the tagged workflow
- Wheel SHA-256: resolved in the release attachment and `SHA256SUMS`
- Source distribution SHA-256: resolved in the release attachment and `SHA256SUMS`
- CycloneDX SBOM SHA-256: resolved in the release attachment and `SHA256SUMS`
- Release checksums and provenance attestations: generated for the release tag

## Remaining before approval

1. **Done (2026-10-09).** Every Plan 034 row reached its exit on 2026-10-08.
   Every lane was requalified at one commit after batch 2: the release 1.1.0
   batch, 26 of 26 at `de9736e`, banked in
   [`release110-batch.json`](plan-033/release110-batch.json) with a clean
   post-batch directory check. The verdicts cited here are that batch's. The
   tagged commit's CI must still show that they bind its source
   (`tests/test_committed_evidence.py`).
2. **Done for the candidate (2026-10-09).** `__version__` is `1.1.0rc1`, the
   JSON report and line 5 are marked for a candidate, `CHANGELOG.md` has a
   dated `1.1.0-rc.1` section, and `SECURITY.md` describes the candidate and
   the planned `1.1.x` line without making it the supported line. Final
   approval repeats this for `1.1.0`: the version, a dated `1.1.0` changelog
   section, and `SECURITY.md`'s supported-versions table and compatibility
   policy for `1.1.x`.
3. **Pending; does not block the candidate.** Decided: a release candidate
   first, then the owner's hands-on NVDA acceptance before final approval.
   The owner runs the [NVDA validation runbook](nvda-validation-runbook.md)
   against the exact `v1.1.0-rc.N` wheel from the release assets and records
   the wheel's SHA-256. A blocker fails acceptance; any other significant
   finding needs the owner's explicit disposition; a behavior change requires
   a new candidate.
4. **Pending; does not block the candidate.** Fill in the automated-evidence
   section from the tagged candidate run (run IDs of every required job, the
   coverage summary, the artifact hashes), then finalize
   `docs/release-evidence-report-1.1.0.json`. These come from the tagged run,
   so they cannot be a prerequisite for creating it.
5. Approve as described in [How this release is approved](#how-this-release-is-approved)
   (the JSON `status` and `version`, and line 5 here, in one commit), and tag
   `v1.1.0` on a commit on `main`. Final promotion should not change the
   application behavior the accepted candidate showed.
