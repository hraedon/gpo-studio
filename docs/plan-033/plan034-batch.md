# The Plan 034 requalification batch

**Status:** executed 2026-10-08. All 22 runs passed on frozen commit
`263f19640529d469c2a54c18b43d228db5378279` with `source.dirty=false`: WP-0's
manifest plus 21 lane verdicts, all schema version 2. That includes the
computer group-deny lane, which had not passed since the WI-059 batch
(WI-069). One verdict was stale on arrival, because a review fix changed a file
it binds after the freeze. Its lane was re-run at
`1fb3f56ac7431e0044c69c32edc4350b2ab84151`, and that successor is the live
certification. `PENDING_REQUALIFICATION` is empty.

The batch was driven by `scripts/plan-033/run-requal-batch.sh`, the first batch
not driven by hand. The driver's progress log is the source of every
`started_utc`/`completed_utc` in the [batch manifest](plan034-batch.json).

## What this batch changed

The [2026-10-07 direction](../direction-2026-10-07-plan-034-completion.md)
put every bound-file edit the programme could foresee into one batch, so the
estate paid once. Each item below expired the WI-062 verdicts that bind the
files it touched:

- **WI-063, LF renormalization** (`17448d5`). Sixteen controller-side sources
  (eight `run-*-oracle.sh`, eight `finalize_*_run.py`) had been committed with
  CRLF under `-text`, and eight runners did not parse. The controller trees are
  now declared `text eol=lf`, and every runner passes `bash -n`. This alone
  expired 19 of the 21 WI-062 verdicts.
- **WI-064 and WI-065, object security** (`6d66ce9`, then `8b1a5b4`).
  `RestrictedGroupsFamily` now stars SID principals in the `[Group Membership]`
  key, and the object-security candidate carries three rows that family builds
  (two `__Members`, one `__Memberof`, BUILTIN alias SIDs). The finalizer
  requires `group_mgmt` on import and export and compares Windows' re-export
  as principal sets. `SystemServicesFamily.validate` parses `raw_sddl` on
  demand, and the candidate builder now validates every family before it
  builds.
- **Retirements** (`8940fd6`, plus PR 91 merged in at `263f196`). The
  PowerShell publication-script branch, `artifact_store.py` and the stale
  pre-R2 `scripts.ini` writer and parser are deleted, which moved
  `publication.py`, `script_policy.py` and `export.py`. The PR 91 deletions
  (`software_install.py`, `folder_redirection.py`, most of `gpmc_interop.py`)
  touched no bound file.
- **WI-068, fdeploy on `GPO`** (`4f71cb4`). The imported `fdeploy1.ini` is
  carried on the GPO, reported and diffed. This moved `model.py`,
  `canonical.py` and `export.py`, which the publication and scripts-metadata
  verdicts bind.
- **WI-070, disabled side refused** (in `8940fd6`). A publication plan for a GPO
  with a disabled side now carries an `unsupported_side_status` refusal instead
  of silently publishing the side enabled. This moved `publication.py`. The
  lane's candidate has both sides enabled, so the run exercises the unchanged
  path, not the refusal.
- **Review-driven finalizer hardening** (`5b59091`, `7cf5092`, `7c6fb50`).
  A Sol review of the batch found that several finalizers read a
  missing field as "nothing to report" and compared empty to empty as a pass.
  The publication lane now grades the plan's own extension-list claims,
  `GPT.INI` and a real owned GUID (21 checks, up from 20). The endpoint lane
  requires every candidate row observed and answered. The RSoP, WP-1B, WP-2,
  WP-3 and Scripts finalizers refuse missing evidence and empty yardsticks.

A dry run at `127a40e` (nine lanes passed) found a driver defect (the user
principal leaking into computer-scope lanes) and the UInt32 cast. Both were
fixed (`ec09462`, `de41b90`) before the real batch, which was then re-run in
full at the final commit.

## The estate repair this batch needed

The estate ran on its **2026-09-20 clock-seeded baselines, at real time**: no
checkpoint revert to a frozen clock and no forward clock jump. This is the
condition the WI-062 batch could not reach.

One repair came first. The client's autologon account failed to log on: the
DC's domain-joined checkpoint predated the client's current autologon password,
and the 2026-09-25 DC restore (the [DNS deletion
experiment](dns-deletion-experiment-20260925.md)) had rolled the password back.
LabCL01's client checkpoints and LabDC01's domain-joined checkpoint were
re-minted together on 2026-10-08 between 00:36 and 00:38 MST, before the batch
launched. The defect is in how the lab mints baselines (a DC baseline older
than the client's must break autologon), so it belongs to the lab tooling, not
to this repository.

Because the estate ran at real time, guest-stamped and controller-stamped
records are comparable again. Every run id's UTC stamp falls inside the
driver's window for that lane, and the post-batch directory check is
timestamped after the last lane. `tests/test_plan034_batch.py` asserts both.
WI-062 could assert neither.

## WI-069: which debt was paid

WI-069 asked that its closure say which of two debts was paid: the mechanism
of the DC-locator DNS deletion identified, or the lane unblocked around it.
**The lane was unblocked around it.** The group-deny lane, which reboots the
client mid-run, passed (`rsop-observe-20261008081929-9918`) on baselines
re-minted at real time, with no forward clock jump. The mechanism behind the
deletion on the 2026-09-05-generation baseline was **never identified**. The
2026-09-25 experiment did not reproduce it on the newer generation, and the
three-phase collector never observed the original failure.

## The object-security successor

After the freeze, DeepSeek's batch review (finding B1) showed that the WI-064
fix starred **every** `[Group Membership]` principal, so a native name-keyed
row (`Power Users__Members = Administrator`) would be written back as
`*Power Users` / `*Administrator`. `8b1a5b4` stars a principal only when it is
a SID, with a regression test built from the native shape. That commit changed
`src/gpo_studio/object_security.py`, which only the object-security lane binds,
so the batch's object-security verdict (`object-security-20261008074755-6711`)
binds serializer bytes that no longer ship. The registry retires it.

The lane was re-run at `1fb3f56` (which also contains an unbound `store.py`
change, review finding N1) with the same driver and a clean tree:
`object-security-20261008082348-9729`, 20/20 checks. Windows accepted all
three `RestrictedGroupsFamily` rows and re-exported them exactly, including the
`__Memberof` row the candidate predicted but no earlier run had measured.

The candidate's rows are all SID-keyed, so the run certifies the starred-SID
path. The unstarred-name path that `8b1a5b4` added is bound by this verdict
(through the `object_security.py` hash) and pinned by a unit test, but no lane
candidate exercises it.

The successor ran at 08:23Z, after the post-batch directory check (08:22:44Z).
The object-security lane creates no directory objects, and its own
`result.json` records its database cleanup.

## Evidence

| Experiment | Passing run | Commit |
|---|---|---|
| wp0 | `live-synthetic-registry-basic-20261008074346-9312` | `263f196` |
| wp1b | `wp1b-writer-20261008074501-2113` | `263f196` |
| wp2 | `wp2-native-import-20261008074557-7463` | `263f196` |
| wp3-member | `wp3-security-template-20261008074639-3419` | `263f196` |
| wp3-dc | `wp3-security-template-20261008074709-2998` | `263f196` |
| object-security | `object-security-20261008074755-6711` (retired; superseded) | `263f196` |
| scripts-metadata | `scripts-r10-20261008074828-8492` | `263f196` |
| publication | `publication-completeness-20261008074904-1047` | `263f196` |
| endpoint | `endpoint-observe-20261008075004-5187` | `263f196` |
| lsdou-precedence | `rsop-observe-20261008075254-6590` | `263f196` |
| disabled-block-enforced | `rsop-observe-20261008075447-5315` | `263f196` |
| wmi-filtering | `rsop-observe-20261008075636-2267` | `263f196` |
| wmi-filtering-error | `rsop-observe-20261008075825-8227` | `263f196` |
| computer-security-filtering | `rsop-observe-20261008080014-9153` | `263f196` |
| computer-security-filtering-deny-read | `rsop-observe-20261008080205-6689` | `263f196` |
| loopback-merge | `rsop-user-observe-20261008080357-2733` | `263f196` |
| loopback-replace | `rsop-user-observe-20261008080612-1816` | `263f196` |
| user-side-disabled | `rsop-user-observe-20261008080829-2986` | `263f196` |
| user-security-filtering | `rsop-user-observe-20261008081133-5663` | `263f196` |
| user-security-filtering-deny | `rsop-user-observe-20261008081431-2742` | `263f196` |
| user-security-filtering-read-deny | `rsop-user-observe-20261008081652-5582` | `263f196` |
| computer-security-filtering-group-deny | `rsop-observe-20261008081929-9918` | `263f196` |
| object-security (successor) | `object-security-20261008082348-9729` | `1fb3f56` |

Packs live under `<family>-evidence/plan034-20261008/<name>/`. The successor
lives under `wp3-evidence/plan034-rerun-20261008/object-security/`, which keeps
the lane's name, following the WI-060 successor layout. Each pack holds the
controller's local run directory verbatim (checked file by file against the
controller), plus the two things WI-062's packs also carry:

- `controller-candidate/`, the builder's output, which the verdict's candidate
  hashes bind;
- `controller.log`, the driver's per-lane log.

No pack holds a controller-side bound source copy. The RSoP verdicts were
written as `rsop-verdict.json` / `rsop-user-verdict.json` and are banked as
`verification.json`, as in WI-062. The manifest records each original name.

The [batch manifest](plan034-batch.json) records every banked file's SHA-256,
with the successor under `successors` (its own `commit` and `replaces`). Every
passing run has an `evidence/<run-id>` tag.

The [post-batch directory check](plan034-cleanup/directory.json), captured at
2026-10-08T08:22:44Z after the last lane in wall order, confirms that the
computer and the user account were restored and that no experiment OUs, GPOs,
groups or WMI filters survived. Its
[collector](plan034-cleanup/collector.ps1) is WI-062's, extended to the
`zz-studio-*` names.

The 20 WI-062 verdicts these replace, the WI-059 group-deny verdict that was
pending, and the batch's own superseded object-security verdict are retired in
`tests/test_committed_evidence.py` with their packs and tags unchanged.
