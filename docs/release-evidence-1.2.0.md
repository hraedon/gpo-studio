# Release evidence manifest - GPO Studio 1.2.0

> **Date:** draft opened 2026-10-31 for the 1.2.0 preparation; the banking commit B sets the release day
> **Source commit:** resolved by the tagged release workflow
> **Status:** release candidate; final approval pending

This is the manifest for the 1.2.0 minor release, to be tagged `v1.2.0`. It
records how 1.2.0 is approved, which runs qualify the tree before the freeze
commit, what 1.2.0 changes, and what it does **not** claim. It is the
preparation's draft: the freeze commit F has not been made, and no lane has
been run for 1.2.0. Line 5 and the JSON report say where the approval stands;
what remains before it is listed under
[Remaining before approval](#remaining-before-approval).

On 2026-10-29 the owner authorized releasing this work by a full
requalification: every lane is re-run at one freeze commit, as for 1.1.0. It
ships as 1.2.0, not as a `1.1.x` patch (owner decision, 2026-11-01), because
it adds features and changes behaviour and `SECURITY.md` limits a `1.1.x` patch
to fixes. On the same day the owner waived the hands-on
NVDA acceptance for 1.2.0 (see
[Accessibility evidence](#accessibility-evidence)). The sequence, and the release gate that holds the tag
to it, are the ones 1.1.0 was released through (Plan 036, the D-9 addendum),
under [How this release is approved](#how-this-release-is-approved).

## How this release is approved

The source of truth is the JSON report,
`docs/release-evidence-report-1.2.0.json`.

**The final release** is approved in five steps, each its own commit or
action, in this order:

1. **Freeze commit F.** Every product change for 1.2.0 is merged, and the
   preparation listed below is in. F itself replaces `YYYY-MM-DD` in the
   `CHANGELOG.md` heading for 1.2.0 with F's date. `SECURITY.md`'s
   supported-versions text for the `1.2.x` line is an owner decision; it ships
   in the distributions, so whatever is decided lands before F or in F, and
   nothing else changes in F. F is a commit on `main`. After F, only the batch's evidence may be added
   and only the release records changed.
2. **The full batch at F.** All 26 lanes live at F (WP-0's manifest and the 25
   lane verdicts pending requalification) run on the estate from a clean clone
   at F, as one batch, with `scripts/plan-033/run-requal-batch.sh` (about 44
   minutes on the release 1.1.0 re-freeze batch's durations), followed by the
   post-batch directory check (`scripts/plan-033/run-post-batch-collector.sh`
   with the committed collector, writing the batch's own cleanup directory).
3. **Bank, commit B, whose parent is F.** `scripts/plan-033/bank-requal-batch.py`
   stages the packs (`stage`) and writes the batch manifest (`manifest`, which
   refuses to run without `--cleanup` naming the post-batch directory check,
   because the gate requires one). The finalizers have already created each
   passing run's annotated `evidence/` tag, named after its run id, at F; they
   are pushed with the batch. Then, in this order: `retarget` points
   `platforms.json` at the new runs (it swaps the runs that are live or
   pending, so it runs before the registry changes); the verdict registry,
   `docs/plan-033/verdict-registry.json`, registers the new verdicts with the
   finalizer each lane had at F, moves every verdict pending requalification
   to `retired_verdicts`, empties `pending_requalification` and points
   `wp0_manifest` at the new WP-0 manifest; the records that cite runs are
   edited by hand (this file, its Date line set to the release day included,
   the JSON report's evidence, the environment spec, the capability matrix,
   the lane results documents), and `report-bound-source-cost.py --write`
   regenerates the cost table; and the JSON report's `freeze` is set to F's
   full commit and the batch manifest's path. Nothing else changes: no test
   (they read the current batch from `freeze`), no code, and neither
   `README.md` nor `CHANGELOG.md`, which ship in the distributions and so are
   final at F. Records-only commits may follow B, before A; each must change a
   record (an empty commit is refused). B and every commit after it until A
   keep both release records a candidate's: the report's `candidate`, this
   file's line 5 the candidate line, and an item still marked pending in each
   remaining list. Approval is A's alone.
4. **Approve, commit A, the last commit.** Records only, and only the
   approval: in the JSON report, set `"status"` to `"approved"` (its
   `"version"` is already `1.2.0`) and close every item of
   `remaining_before_approval`; in this file, replace line 5 with the line
   `STATUS_LINES` in `scripts/check_release_manifest.py` gives for `approved`
   (same prefix, ending in "approved for release"; it is not quoted here
   because the gate allows that line exactly once in this file) and close
   every item under [Remaining before approval](#remaining-before-approval).
   Closed means done; for the owner's NVDA acceptance, waived for 1.2.0 and
   recorded as not run (already recorded in the preparation); for the
   automated evidence, recorded by the tagged run after A (item 4 of that
   section says exactly what it records). Both lists stay. A may change
   nothing else in either record, so everything else the approval relies on is
   recorded in B or a records-only commit before A. What the gate checks is narrower than "closed": it refuses an approved
   release that still marks any item as pending, and an approval that empties
   or removes either list (the report's list must be non-empty, the section
   must keep at least one list item). That each item is truly closed is for
   the approval's reviewers.
5. **Tag `v1.2.0` on A**, on `main`, and push it.

The release gate holds the tag to this: for a final tag it refuses unless the
history from F to the tag is exactly B, any records-only commits and A, the
tagged tree differs from F only in the batch's banked files and the release
records, and every live verdict is the batch's run of its lane, recorded at F,
with its evidence tag at F (see
[How the release gate reads this](#how-the-release-gate-reads-this)).

**What the gate proves, and what it does not.** It proves that the tagged tree
is F plus internally consistent banked evidence that records a run of every
lane at F, reached in this order. It does not prove that the Windows estate
produced that evidence: every byte it reads is committed after F, and a careful
forgery with its tags would pass. That rests on the batch controller's
transcripts, which are each pack's `controller.log` (banked verbatim, hashed
by the batch manifest) and the batch directory on the controller (`progress.jsonl`
and `logs/`), and on the two-reviewer review of the banking commit. Within that
claim, the code that ships is the code at F, and the tag never claims approval
before the batch is banked.

**Preparation that had to land before F**, because the gate refuses any later
change to it. The machinery 1.1.0 added is in place and needs no edit for a new
release: `.gitattributes` keeps any batch's packs and post-batch check
byte-exact by pattern; the verdict tables are data
(`docs/plan-033/verdict-registry.json`); `pyproject.toml` keeps every path that
may change after F out of the sdist; `src/gpo_studio/api.py` cites the lane and
this document at the version's tag, `v1.2.0`, never a run; and `README.md`
names no run. What 1.2.0's preparation changed:

- `__version__` is `1.2.0`, and so is the JSON report's `version`. For 1.1.0
  the freeze commit made this change; for 1.2.0 the preparation did, so F
  changes only the changelog's date;
- `CHANGELOG.md` has the 1.2.0 section, its heading's date left for F;
- the tests read the current batch generically (`tests/current_qualification.py`):
  from B on, the batch the JSON report's `freeze` names; before that, the
  freeze batch of the latest approved release before the package's version,
  read from that release's report (for 1.2.0, the release 1.1.0 re-freeze
  batch), with any run the registry has made live since in place of the one
  it replaced. A later release needs no edit there;
- the owner's waiver of the hands-on NVDA acceptance for 1.2.0 is recorded
  here, in the JSON report's `accessibility` and remaining items, and in the
  NVDA validation runbook, so neither B nor A has to change it;
- the tests that check the committed release records against the gate read
  the package's version, not 1.1.0's
  (`tests/test_release_manifest_gate.py`, `tests/test_record_release_identity.py`).

**A release candidate** is approved with two edits in one commit: in the JSON
report, `"status"` set to `"candidate"` and `"version"` to the exact candidate
version, and line 5 of this file replaced with the candidate line from
`STATUS_LINES`. A candidate's report may leave `freeze` as `null`; candidate
tags are not held to a freeze commit. No candidate is planned for 1.2.0, and
with `__version__` at `1.2.0` the gate accepts only the tag `v1.2.0`.

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

The JSON report also has `freeze` (report schema 3): `null`, or exactly a full
40-character commit (`commit`) and the path of a batch manifest under
`docs/plan-033/` (`batch_manifest`). An approved report must have one. For a
final tag `vX.Y.Z`, and never for an RC tag, every run of the gate also
requires, on a full-history checkout:

- F, the report's `freeze` commit, to be in the clone (a shallow clone is
  refused) and a strict ancestor of the tagged commit; and the checkout to have
  no uncommitted change to a tracked file;
- the history from F to the tagged commit to be linear: the banking commit B,
  F's child, adds the batch (its packs, manifest and note, post-batch directory
  check) and modifies only release records; any later commit modifies at least
  one release record and nothing else (an empty commit is refused); the tagged
  commit is the approval commit A, the only commit whose report is approved,
  and it changes only the report's status, version and remaining items and
  this file's line 5 and remaining section, keeping both remaining lists
  non-empty with no item still marked pending. Every commit before A keeps both
  records a candidate's (the report's `candidate`, this file's line 5 the
  candidate line, an item still pending in each remaining list), so A approves
  both at once. A refusal names the commit;
- the tagged tree to differ from F only in those files. The release records
  are a closed list (`FREEZE_RECORDS` in the gate: the capability matrix, the
  work-item register, this file and the JSON report, the environment spec, the
  cost table, the lane results documents, `platforms.json` and the verdict
  registry), every one excluded from the distributions. Everything else,
  including `src/`, `scripts/`, `pyproject.toml`, `uv.lock`, `.github/`,
  `.gitattributes`, `README.md`, `CHANGELOG.md` and every test, must be
  byte-identical to F. Every changed path is a regular file, no pack banks an
  importable source or extension module or a `.pth` file (archives such as the
  builder's `candidate.zip` are evidence, and no pack directory is ever on the
  import path), and every changed record is UTF-8 with no byte-order mark or
  coding declaration;
- the verdict registry to keep F's entries unchanged, add exactly the batch's
  verdicts, each with the finalizer its lane had at F (the finalizer is code
  the tests run), retire every verdict pending requalification at F and only
  verdicts registered at F, and owe nothing;
- the batch manifest to record one passing run per lane at F, with nothing
  not passed and no successors, each pack's files hashing to what it banked;
- every live verdict, and the WP-0 manifest, to be that batch's run of its
  lane, recorded at F from a clean tree and passing, and the batch to cover
  every lane live or pending at F; and each run's annotated `evidence/` tag to
  peel to F. A refusal names the lane.

`tests/test_release_freeze_gate.py` builds that history in temporary git
repositories and holds each refusal.

`docs/release-evidence.md` is the **1.0.0** manifest. It cannot approve any
other version. `tests/test_release_manifest_gate.py` holds that case closed,
together with every bypass found by four rounds of review of the earlier,
rendering-based gate.

The publish job also requires, for the exact tagged commit:

- every job in `ci.yml`, called as a reusable workflow: Linux and Windows
  test matrices (`test`, `test-windows`), frontend and browser tests, installed
  package, dependency audit, SBOM, static safety, identifier-gate fail-closed
  test, PowerShell analysis, property tests and secret scan;
- the `identifier-gate.yml` gate, with the denylist secret passed explicitly;
- the `verify` job, on a full-history checkout (the `release-identity` and
  `publish` jobs check out full history too, which the final-tag freeze check
  needs);
- the tagged commit to be reachable from `origin/main`;
- immediately before `gh release create`, the remote tag to still name the
  commit the run built (as the commit it peels to, or as the annotated tag
  object, whichever `GITHUB_SHA` carries).

## What 1.2.0 is

A minor release, the first of a `1.2.x` line. It carries the work merged
since 1.1.0; `CHANGELOG.md`'s 1.2.0 section lists each change, with the
behaviour changes marked. It is a minor release, not a `1.1.x` patch, because
it adds features and changes behaviour and `SECURITY.md`'s compatibility
policy limits a patch to fixes (owner decision, 2026-11-01). In outline: ADMX
enum elements from real Windows templates load their items, and stored rows
the settings browser cannot match are flagged rather than shown as valid; the
editing dialogs ask before Esc or the close button discards a change; clear
errors for malformed input; the ADMX template store reads each file once,
follows no links, is bounded and runs one ingest at a time, with the security
fixes listed in the changelog; the native backup export writes the measured
user-side Scripts tool GUID; registry-only recovery previews every row; and
the local Windows CI tooling is hardened. It also adds the ADMX folder dialog
and the `gpo-studio run --admx-dir` startup option.

The workspace schema is still 4. The 1.0 capability contract in
[`capability-matrix.md`](capability-matrix.md) is unchanged, and no Plan 034
module's ruling changes: 1.2.0 adds no capability and moves no module to
`yes`. What [1.1.0 is](release-evidence-1.1.0.md#what-110-is) still describes
the capability set.

## Requalified evidence

Pending: the batch at F. B replaces this section with the batch's record, as
1.1.0's manifest records its re-freeze batch: the batch manifest and note, the
window, every run at F with its checks, the post-batch directory check, the
run-id stamps, and what the batch retired.

### The qualification before the freeze

Until B, the current qualification is the
[release 1.1.0 re-freeze batch](plan-033/release110-refreeze-batch.md), all 26
lanes at the 1.1.0 freeze commit,
`5ffa026516830139f8607ef5706f2bf8f4e002d5`, on 2026-10-20. 25 of its 26
verdicts are pending requalification in `docs/plan-033/verdict-registry.json`,
each because a change merged after 1.1.0 edited a file it binds:
`scripts/windows-oracle/psdirect.ps1` (the realm fix, #157), which all 25 lane
verdicts bind; `src/gpo_studio/export.py` (the measured user-side Scripts tool
GUID), which six bind (endpoint, firewall, publication, scripts-metadata, WP-1B
and WP-2); and `src/gpo_studio/safe_io.py` (non-blocking opens), which fdeploy
binds. WP-0's manifest binds by commit, not by file hashes, and stays live
until the batch at F replaces it. Nothing certified by these runs is claimed
for 1.2.0; the batch at F must re-earn every lane, and the gate requires it to
retire every pending verdict.

| Experiment | Run at `5ffa026` | Checks | Before F |
|---|---|---|---|
| wp0 | `live-synthetic-registry-basic-20261020072705-9695` | pass | live |
| wp1b | `wp1b-writer-20261020072808-8446` | pass | pending requalification |
| wp2 | `wp2-native-import-20261020072858-5525` | 18/18 | pending requalification |
| wp3-member | `wp3-security-template-20261020072938-4029` | 20/20 | pending requalification |
| wp3-dc | `wp3-security-template-20261020072911-9635` | 20/20 | pending requalification |
| object-security | `object-security-20261020073052-3290` | 20/20 | pending requalification |
| scripts-metadata | `scripts-r10-20261020073126-4216` | 20/20 | pending requalification |
| publication | `publication-completeness-20261020073202-7563` | 21/21 | pending requalification |
| lifecycle | `lifecycle-20261020073233-9178-40aaabfb07a44f6d` | 73/73 | pending requalification |
| report-parity | `report-parity-20261020073320-2127` | 26/26 | pending requalification |
| firewall | `firewall-20261020073459-2222842` | 36/36 | pending requalification |
| fdeploy | `fd-20261020073719-5157` | 29/29 | pending requalification |
| endpoint | `endpoint-observe-20261020073835-9781` | pass | pending requalification |
| lsdou-precedence | `rsop-observe-20261020074127-4290` | pass | pending requalification |
| disabled-block-enforced | `rsop-observe-20261020074322-5597` | pass | pending requalification |
| wmi-filtering | `rsop-observe-20261020074546-9797` | pass | pending requalification |
| wmi-filtering-error | `rsop-observe-20261020074753-3255` | pass | pending requalification |
| computer-security-filtering | `rsop-observe-20261020074945-9317` | pass | pending requalification |
| computer-security-filtering-deny-read | `rsop-observe-20261020075135-6601` | pass | pending requalification |
| loopback-merge | `rsop-user-observe-20261020075328-1230` | pass | pending requalification |
| loopback-replace | `rsop-user-observe-20261020075545-2837` | pass | pending requalification |
| user-side-disabled | `rsop-user-observe-20261020075802-7706` | pass | pending requalification |
| user-security-filtering | `rsop-user-observe-20261020080055-6849` | pass | pending requalification |
| user-security-filtering-deny | `rsop-user-observe-20261020080359-8373` | pass | pending requalification |
| user-security-filtering-read-deny | `rsop-user-observe-20261020080623-7133` | pass | pending requalification |
| computer-security-filtering-group-deny | `rsop-observe-20261020080904-2605` | pass | pending requalification |

Verdicts bind their source files by `(commit, path, sha256)`. An edit to a
bound file between F and the tag would expire the verdicts that bind it, and
the release gate refuses any change after F outside the batch and the release
records. `tests/test_committed_evidence.py` runs in CI, which the release
requires on the tagged commit. See [`bound-source-cost.md`](plan-033/bound-source-cost.md).

## What the lanes certify

1.2.0 claims what 1.1.0 claimed for each lane, re-earned at F, and nothing
more. Each lane's certifying run for 1.2.0 is its run in the batch at F, which
B records under [Requalified evidence](#requalified-evidence); the scope of
each, and what each does not certify, are unchanged from
[what the newer lanes certify](release-evidence-1.1.0.md#what-the-newer-lanes-certify)
in the 1.1.0 manifest and the lane results documents:
[firewall](plan-033/firewall-results.md),
[lifecycle](plan-033/lifecycle-results.md),
[fdeploy](plan-033/fdeploy-results.md) and
[report parity](plan-033/report-parity-results.md).

What these runs do **not** certify, in short (the 1.1.0 manifest has each in
full):

- **Report parity:** six Studio preference families have no capture and are
  not claimed (Regional Options, Devices, Folder Options, Data Sources,
  Network Shares, Applications). ADMX policy rendering, Scripts, links,
  security filtering and WMI filters are named exclusions. Property-level
  equivalence of preference items is not claimed.
- **Firewall:** PolicyStore readback, not endpoint application; one build;
  values outside the tranche are refused; GPME display is unmeasured
  (WI-077). IPsec, Public Key, wired and wireless policy are out of scope for
  1.x.
- **fdeploy:** decoding `Flags`, other `Flags` values, multi-folder and
  multi-principal documents, and any writer (deferred under WI-066).
- **Lifecycle:** one topology (one source, one target, one member server, one
  DC). A deleted GPO's restore, multi-DC replication, `Import-GPO -TargetName`
  into an existing GPO, deny ACEs and the WMI filter object are unmeasured.
  The cross-domain half is out of scope until the estate has a second domain
  or a trust.
- **Scripts:** the measured computer startup shape only. User-side scripts
  stay refused at the API (`scripts_user_side_unmeasured`) until WI-071's
  logon and shutdown lane banks a verdict, although the native backup export
  now writes the measured user-side Scripts tool GUID.

## Plan 034 module exits

Every Plan 034 exit landed on 2026-10-08, and 1.2.0 changes none of them. The
table matches the [capability matrix](capability-matrix.md), the
[Plan 034 status line](../plans/034-post-1.0-layer-reconciliation.md) and the
[rulings](direction-2026-10-07-plan-034-completion.md). Every lane it cites is
certified for 1.2.0 by its run in the batch at F. The last column says what
1.2.0 may claim. It says "capability" only for a row whose lane **and**
surface both exist.

| Module | Ruling | Lane | Surface | 1.2.0 claim |
|---|---|---|---|---|
| `policy_families.py` | `yes` | WP-3 member and DC, 20/20 each at 1.1.0 | `POST /api/security-template/policy-families` | capability: emission direction only |
| `object_security.py` | `yes` | object security, 20/20 at 1.1.0 | `POST /api/security-template/object-security` | capability: registry, file-system and service security; **restricted groups are lane-certified but not surfaced** |
| `security_template.py` | exits through its consumers | bound by three lane verdicts | via the two endpoints above | no standalone claim; reading GPME-authored `GptTmpl.inf` is out of scope |
| `publication.py` | `yes` | publication completeness, 21/21 at 1.1.0 | `GET /api/gpos/{guid}/publication-plan` and the Publication preview panel (review-only) | capability: review-only preview; steps marked `measured`, `unmeasured` or `refused`; nothing writes |
| `script_policy.py` | `yes` | Scripts metadata, 20/20 at 1.1.0 | `POST /api/gpos/{guid}/gpmc-backup-with-scripts` (+ `/preview`) and the Scripts panel | capability: the measured shape only; unmeasured shapes refused with 422 |
| `fdeploy.py` | lane, or the writer stays deferred (WI-066) | fdeploy read lane, 29/29 at 1.1.0 | `POST /api/folder-redirection/fdeploy`, imported backups' report and diffs, and the Folder Redirection review panel | capability: reading the four measured shapes; `Flags` decoding and the writer stay deferred under WI-066 |
| `network_security.py` / `firewall_policy.py` | firewall: codec, lane, surface; the rest out of scope | firewall, 36/36 at 1.1.0 | `POST /api/network-security/firewall/render`, `GET /api/gpos/{guid}/firewall-policy` | capability: the measured tranche only, everything else refused; IPsec, Public Key, wired and wireless are not claimed |
| `lifecycle.py` | same-domain lane plus restore-plan surface; cross-domain out of scope | same-domain lifecycle, 73/73 (all 30 cells) at 1.1.0 | `POST /api/lifecycle/restore-plan` (review only) | capability: same-domain restore plans over GPOs imported from a Windows backup; cross-domain is refused and not claimed |
| `backup.py` / `report.py` | report-parity lane for modelled families | report parity, 26/26 (30 cases) at 1.1.0 | existing backup import and plain-text report | capability: item-level report parity (element, name, `uid`, action, order) for the families listed in the 1.1.0 manifest, not property-level; the six uncaptured families are not claimed |
| `artifact_store.py` | deleted | n/a | n/a | not shipped |
| `software_install.py` | deleted | n/a | n/a | not shipped |
| `folder_redirection.py` | deleted (superseded by `fdeploy.py`) | n/a | n/a | not shipped |
| `gpmc_interop.py` | reduced to `InteropIssue` | n/a | n/a | no claim |
| `publisher.py`, `hosting.py` | out of scope for 1.x; code retained | n/a | n/a | not capabilities; no hosted mode |

The check counts are 1.1.0's; B replaces each with the batch's. Plan 034's
review gate stands: no module leaves the plan `capture-backed`.

## Automated evidence

To be resolved from the tagged release workflow run: when it publishes,
`scripts/record_release_identity.py` replaces this paragraph in the published
copy with that run's URL and attempt, the statement that the jobs gating
publication passed in it, that its `verify` job checked coverage against the
floor (the total stays in that job's log), and the artifact hashes. Local
success is not release evidence.

Local pre-release observation, **not release evidence**: on 2026-11-01, on the
preparation branch at `49d02f3` (Python 3.14 on a Linux development host; CI
runs Python 3.13 and 3.14), the full Python suite passed (14267 passed, 160
skipped) with total branch coverage of 92.46 percent against the 84 percent
floor and every per-module floor met (`scripts/check_coverage.py`), as did
ruff and `mypy src`. No distribution was built for this observation. The
tagged run rebuilds and rechecks everything; its artifacts, not these, are the
release.

## Accessibility evidence

Automated, run by the `frontend` CI job and the release `verify` job, as for
1.1.0: the workspace axe scan (no serious or critical findings), keyboard tab
order and dialog focus semantics; the dark theme, held to the same axe bar as
the light theme; and the open-dialog axe scans of the Security template,
Folder Redirection and RSOP prediction dialogs (see the
[1.1.0 manifest](release-evidence-1.1.0.md#accessibility-evidence) for each).

Gaps, stated rather than implied:

- No hands-on screen-reader session has covered the interface added since
  1.0: the 1.0.0 NVDA acceptance covered the 1.0 interface only, and the
  owner waived it for 1.1.0. 1.2.0 changes the editing dialogs (the prompt
  before Esc or the close button discards a change), adds the ADMX folder
  dialog and changes how unresolved enum rows read in the settings browser.
  **The owner's hands-on NVDA acceptance for 1.2.0 was not run.** The owner
  waived it for 1.2.0 on 2026-11-01: the dialogs changed, but no NVDA pass
  was performed. It was not passed, and nothing here claims a screen-reader
  result for the editing dialogs, the ADMX folder dialog or any other part of
  the interface added since 1.0. The
  [NVDA validation runbook](nvda-validation-runbook.md) is unchanged for a
  later release.

## Upgrade and rollback

- Workspace schema stays 4, as in 1.1.0. Opening a 1.1.0 workspace with 1.2.0
  migrates nothing.
- Moving from 1.0.0 is as for 1.1.0: the in-place migration from schema 1 to
  4, without a backup, held by `tests/test_release_upgrade_from_1_0_0.py` and
  rehearsed by `scripts/rehearse_upgrade_rollback.py` (see the
  [1.1.0 manifest](release-evidence-1.1.0.md#upgrade-and-rollback)).
- Rollback is **restoring the pre-upgrade backup**. Running 1.1.0 against a
  workspace 1.2.0 has written is not claimed: nothing here tested it.
- Some stored data reads differently: an enum row saved before the #139 fix
  is listed as unresolved instead of shown as valid, a policy whose stored
  rows share an editor ID can no longer be edited, and a setting written by
  1.1.0 or earlier whose policy or element ID contains `-user-` or
  `-computer-` is refused on reconfigure. Nothing is rewritten; the changelog
  says how to recover each.
- Operator procedure: [Windows quickstart](windows-quickstart.md#upgrade-to-another-release)
  and [workspace recovery](workspace-recovery.md#upgrading-and-rolling-back-across-a-schema-change).

## Not claimed by 1.2.0

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
  beyond the four measured shapes, user-side scripts, and the lifecycle cases
  listed under [What the lanes certify](#what-the-lanes-certify).
- **Template store limits:** hard links and bind mounts inside a template
  folder are not detected (the changelog's Security section says why); keep
  the template root writable only by trusted administrators.
- **Deployment profile unchanged:** one operator on loopback, no
  authentication, no TLS, no hosted or multi-user mode. The `actor` on each
  revision is claimed, not authenticated.
- **1.0.0 limitations that still stand:** the PowerShell plan does not apply
  WMI filter assignments, GPP or ILT predicates. GPMC backup export covers a
  certified subset only.

## Schema and artifact identity

- Workspace schema version: 4
- Application version: 1.2.0
- Package version: 1.2.0 (no release candidate)
- Source commit: resolved in the release attachment by the tagged workflow
- Wheel SHA-256: resolved in the release attachment and `SHA256SUMS`
- Source distribution SHA-256: resolved in the release attachment and `SHA256SUMS`
- CycloneDX SBOM SHA-256: resolved in the release attachment and `SHA256SUMS`
- Release checksums and provenance attestations: generated for the release tag

## Remaining before approval

1. **Pending.** Every lane live at F (26: WP-0's manifest and the 25 lane
   verdicts pending requalification) requalified at the freeze commit F in one
   batch, banked in commit B whose parent is F, evidence and records only, with
   the JSON report's `freeze` set to F and the batch manifest, and a clean
   post-batch directory check. The tagged commit's CI must still show that the
   verdicts bind its source (`tests/test_committed_evidence.py`).
2. **Pending.** The freeze commit F: every product change for 1.2.0 merged,
   `__version__` and the JSON report's `version` `1.2.0` (done in the
   preparation; the report stays a candidate until A), and the `CHANGELOG.md`
   1.2.0 heading dated by F. `SECURITY.md` describes the `1.1.x` line; its
   supported-versions text for `1.2.x` is an owner decision, landed before F or
   in F.
3. **Waived; not run (owner decision, 2026-11-01).** The owner's hands-on
   NVDA acceptance for 1.2.0: the dialogs changed, but no NVDA pass was
   performed. It is recorded as not run, never as passed, here, under
   [Accessibility evidence](#accessibility-evidence) and in the JSON report.
4. **Recorded by the tagged run after A, not a precondition.** The automated
   evidence comes from the release workflow run on the tag, so it cannot exist
   before A. When that run publishes, `scripts/record_release_identity.py`
   writes into the published `release-evidence.md` (replacing the Automated
   evidence placeholder) and `release-evidence-report.json`: the run's URL and
   attempt, the workflow ref, the tag and source commit; that the jobs gating
   publication (`release-identity`, `ci`, `identifier-gate`, `verify`) passed
   in that run, since `publish` runs only after they succeed; that `verify`
   checked branch coverage against the floor; and the wheel, sdist and SBOM
   hashes. The job IDs and the tagged run's measured coverage total are in that
   workflow run itself (its jobs and the `verify` log), not in the assets. It
   refuses to publish if any of that is missing or the placeholder is not
   resolved. The approval commit A closes this item by saying so.
5. **Pending.** Approved in the order under
   [How this release is approved](#how-this-release-is-approved): the full
   batch of all 26 lanes at F, the banking commit B (parent F, evidence and
   records only, the report's `freeze` set to F and that batch) and a
   records-only approval commit A; the tag `v1.2.0` on A, on `main`. The
   release gate refuses the tag unless the history from F is B, records-only
   commits and A, the tree differs from F only in the batch and the release
   records, and every live verdict is the batch's run recorded at F with its
   evidence tag (it does not authenticate the estate runs; see How this
   release is approved).
