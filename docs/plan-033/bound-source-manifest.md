# Bound source by manifest, not by copy

**Decision date:** 2026-09-10. **Work items:** [WI-062](../work-items.md#wi-062--evidence-packs-duplicate-source-bytes-that-are-already-bound-to-head)
(and, riding the same batch, [WI-061](../work-items.md#wi-061--retained-native-xml-is-copied-into-every-revision-snapshot)).
**Status:** policy change landed; the 2026-09-08 verdicts were parked as
pending requalification and are re-earned by the estate batch this change
forces.

## What changed

Every lane pack used to bank a byte copy of every bound source module — the
finalizer, `oracle_evidence.py`, the driver, the candidate builder, the
transport, and for the Scripts/publication lanes the bound product modules
(`model.py`, `export.py`, ...) as well. Twenty-seven copies of
`oracle_evidence.py` accumulated under `docs/` (16MB against 3.6MB of `src/`),
and every requalification added another full set.

Since WI-059, `assert_bound_source_bytes` refuses to finalize unless the
working tree, index and HEAD agree byte for byte for exactly those paths, and
since the same batch every verdict records each bound file's SHA-256 beside
the run's commit. Within this repository the copies were therefore provably
identical to what git already holds at the recorded commit, and a divergent
copy was detectable by re-hashing `git show <commit>:<path>` against the
record — the copy added weight without adding a check.

New packs record the controller-side half as **(commit, path, sha256)**:

- lane verdicts carry `source.paths` (every bound file's repository path) and
  `source.banked_copies` (the names whose bytes the pack holds), at
  `schema_version: 2`; `harness_matches_source` now covers only the
  guest-deployed half, because the controller half has no pack copy to
  rehash — its guarantee is the raw-byte check plus the recorded digest;
- the WP-0 manifest carries the four orchestrator files in
  `source.bound` instead of as `input` artifacts; `build_harness_inputs`
  verifies them against the deploy-time record, the source tree that
  executed, and git at the commit.

The guest-deployed half is unchanged: those files ran on Windows, the pack's
retrieved copy is evidence of what the guest executed, and it stays banked
and hash-verified.

Nothing was deleted. Historical packs, their tags and their byte copies are
immutable; the saving is in what future packs add.

## The trade-off, stated plainly

A pack can no longer be verified **standalone, outside this repository**.
Before, a reviewer with only the pack could rehash each banked copy against
the verdict's own record. Now, verifying the controller half's digests
requires reading `git show <commit>:<path>` from a clone — the pack plus the
repository, not the pack alone.

The case for keeping copies would be decisive if packs needed standalone
verification: if a pack had to prove itself somewhere the repository cannot
be fetched, or to a party who must not be trusted to read the repository it
names. No current or planned consumer of these packs does. They are review
artifacts inside the repository that produced them, banked beside the code
they bind, and the repository itself is the unit of custody (issue #22's tag
preservation exists for exactly that reason). If that ever changes, the
finalizers can restore copies for the affected lanes — the tables that name
the files were never removed.

## What still binds

- `assert_bound_source_bytes` runs before every finalization, unchanged, over
  the same declared path set (`check-bound-source-bytes.py` preflight
  included). A dirty or CRLF-drifted tree still refuses.
- Each verdict's `source.files` still names every bound file with its
  SHA-256, and `test_committed_evidence.py` still fails if a live verdict's
  recorded hash drifts from the shipping tree.
- New for schema version 2:
  `test_a_manifest_form_verdict_resolves_against_its_commit` re-derives every
  recorded digest from `git show` at the verdict's own commit and refuses a
  pack that still banks a controller-side copy.

## What it does not buy

The blobs stay in history: `.git` does not shrink, and the existing packs are
not made smaller by this decision. The saving is only in what future batches
add — which is why this is a finalizer policy change rather than a deletion
sweep, and why it belongs to a batch that requalifies the estate anyway.
