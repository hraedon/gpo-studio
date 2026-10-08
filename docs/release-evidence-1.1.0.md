# Release evidence manifest — GPO Studio 1.1.0

> **Date:** not set (draft opened 2026-10-08)
> **Source commit:** resolved by the tagged release workflow
> **Status:** DRAFT — not approved for release and not a release candidate

This is the skeleton of the 1.1.0 manifest. It records what has been
requalified, what the release workflow now checks, and what 1.1.0 does **not**
claim. Every "to be resolved" item must be filled in, and every open row in
[the Plan 034 table](#plan-034-module-exits) must reach its exit, before the
status line changes.

## How this manifest gates the release

`release.yml` runs `scripts/check_release_manifest.py` before anything else
and again in the publish job. For a tag `vX.Y.Z` or `vX.Y.Z-rc.N` it requires:

- `src/gpo_studio/__init__.py` to declare exactly that version
  (`X.Y.Z` or `X.Y.ZrcN`; development and other pre-release versions are
  refused);
- this file, `docs/release-evidence-X.Y.Z.md`, to begin with the title above
  and to contain `- Application version: X.Y.Z`;
- exactly one status line in this file. For an `-rc.N` tag it must read
  `release candidate; final approval pending`. For a final tag it must read
  `approved for release`. The draft line above satisfies neither, so no tag
  can publish from this file as it stands;
- `docs/release-evidence-report-X.Y.Z.json` to declare `"release_version":
  "X.Y.Z"`.

`docs/release-evidence.md` is the **1.0.0** manifest. It can no longer
approve any other version. Before this gate, the workflow grepped that file for
an approval string, so a `v1.1.0` tag would have published on 1.0.0's approval.
`tests/test_release_manifest_gate.py` holds that case closed.

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

The manifest is parsed as CommonMark. Exactly one status mention (the word and a colon) may
exist anywhere in it, in any spelling, and it must be a plain paragraph in the
top-level blockquote at the head of the file. A status in a code block (including
one nested in a quote), list, nested quote or heading fails, and so does any raw
HTML. `__version__` must be bound exactly once, and Hatchling must read the same
value. Before anything is attested, the built wheel's `METADATA` and the
sdist's `PKG-INFO` must carry the approved version.

## What 1.1.0 is

The [2026-10-07 direction](direction-2026-10-07-plan-034-completion.md) makes
1.1.0 the release that carries Plan 034's result, with a deadline of
2026-10-31. The 1.0 capability contract in
[`capability-matrix.md`](capability-matrix.md) is unchanged. Post-1.0 layers
count as 1.1.0 capabilities only if they meet both halves of the exit
condition in [`domain-layer-status.md`](domain-layer-status.md): a re-runnable
evidence lane, **then** a delivery surface an operator can reach.

## Requalified evidence: the Plan 034 batch

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

## Plan 034 module exits

State as of 2026-10-08, after the Scripts and publication surfaces merged, from
the [Plan 034 status line](../plans/034-post-1.0-layer-reconciliation.md) and the
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
| `fdeploy.py` | lane, or the writer stays deferred (WI-066) | **open** | read surface exists (`POST /api/folder-redirection/fdeploy`, browser panel); no lane | to be resolved; no writer either way |
| `network_security.py` | firewall: codec, lane, surface; the rest out of scope | firewall lane **open** (ruled out if no verdict by about 2026-10-24) | none | to be resolved; IPsec, Public Key, wired and wireless are not claimed |
| `lifecycle.py` | same-domain lane plus restore-plan surface; cross-domain out of scope | **open** | none | to be resolved; cross-domain is not claimed |
| `backup.py` / `report.py` | report-parity lane for modelled families | **open** | none | to be resolved |
| `artifact_store.py` | deleted | — | — | not shipped |
| `software_install.py` | deleted | — | — | not shipped |
| `folder_redirection.py` | deleted (superseded by `fdeploy.py`) | — | — | not shipped |
| `gpmc_interop.py` | reduced to `InteropIssue` | — | — | no claim |
| `publisher.py`, `hosting.py` | out of scope for 1.x; code retained | — | — | not capabilities; no hosted mode |

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
- the Folder Redirection dialog, populated, with an axe scan.

Gaps, stated rather than implied:

- the RSOP prediction panel has browser tests but no axe scan of its open
  dialog;
- the 1.0.0 hands-on NVDA acceptance covered the 1.0 interface only. The RSOP,
  Security template and Folder Redirection panels, the dark theme and the
  sticky row actions have had no hands-on screen-reader session. Whether 1.1.0
  requires one before approval is an operator decision that has **not** been
  made. It has not been run.

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

Three outputs differ from what 1.0.0 produced for the same content. None loses
data. Each is pinned by a test:

1. `Registry.pol` records within a key are ordered delete-all-values, delete,
   then set. 1.0.0 ordered them by value name alone, so a GPO with a delete
   action exports the same records in a different byte order, with a
   different hash.
2. For a GPO with preference items, the policy-semantic and review digests
   change. The canonical form gained empty entries for the preference families
   added since 1.0.0. No value changes.
3. A GPO with a GPP Registry item no longer offers native GPMC backup export.
   Native GPP output is an allowlist of families whose extension metadata was
   captured. The Studio bundle still carries GPP Registry.

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
  Software Installation; a Folder Redirection writer unless WI-066 is answered;
  `publisher.py` and `hosting.py` as capabilities.
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

1. Every open row above reaches its exit (`yes` or a recorded ruling), with
   its plan status line, capability matrix entry and changelog entry in the
   same change.
2. Bump `__version__` (to `1.1.0rc1` for a candidate, or to `1.1.0`), date
   the changelog section, and update `SECURITY.md`'s supported-versions table
   and 1.x compatibility policy for the `1.1.x` line.
3. Decide whether a release candidate and a hands-on screen-reader session are
   required. If they are, publish `v1.1.0-rc.N` with the candidate status line,
   and record the session against that exact wheel.
4. Fill in the automated-evidence section from the tagged run, then finalize
   `docs/release-evidence-report-1.1.0.json`.
5. Change the status line to `approved for release`, and tag `v1.1.0` on a
   commit on `main`.
