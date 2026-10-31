# Architecture and trust boundaries

## Design: offline drafts, separate publication

GPO Studio keeps three concerns apart, so that no long-running web service
needs to hold Domain Admin credentials:

1. **Authoring** is local and unprivileged. Drafts are ordinary structured
   data.
2. **Review** works on immutable revisions and deterministic artifacts.
3. **Publication** is a replaceable adapter. It is not part of the web process.

The v0.1 adapter is an exported PowerShell plan. A future enterprise adapter
should be a short-lived Windows worker that uses delegated rights, signed
inputs, approval tokens and an allow-listed command vocabulary. It must not run
arbitrary shell.

## Components

```text
┌─────────────────┐       JSON/HTTP       ┌──────────────────────────┐
│ Browser editor  │ ────────────────────▶ │ FastAPI delivery layer   │
└─────────────────┘                       └────────────┬─────────────┘
                                                     │
                                      ┌──────────────▼──────────────┐
                                      │ Deterministic domain core   │
                                      │ validation / PReg / export │
                                      └──────────────┬──────────────┘
                                                     │
                                      ┌──────────────▼──────────────┐
                                      │ SQLite current snapshots + │
                                      │ immutable revision journal │
                                      └──────────────┬──────────────┘
                                                     │ explicit export
                                      ┌──────────────▼──────────────┐
                                      │ ZIP + Registry.pol + plan  │
                                      └─────────────────────────────┘
```

The domain core does not import FastAPI. A CLI, desktop shell or automation API
could replace the web layer without changing policy serialization.

## Mutation contract

Every mutation carries:

- `expected_revision`: compare-and-swap protection against lost updates.
- `actor`: the local operator identity as claimed by the caller. In v0.1,
  authentication is left to the deployment.
- `reason`: a required, human-readable audit note.

On success the store writes a complete immutable snapshot as revision `N+1`.
Restore never rewrites history; it copies an old snapshot into a new revision.

A multi-user deployment must take `actor` from trusted authentication
middleware, not from request JSON. The roadmap lists this as a gate.

### ADMX catalogue context

Catalogue-backed policy reads expose an opaque `catalogue_revision` token;
configured-settings reads also identify the `gpo_revision` they rendered.

Configured-settings `state` is read-only evidence, separate from configure/bulk
write targets. For policies declaring a scalar `valueName` marker, `enabledList` or
`disabledList`, the reader compares complete normalized policy-level patterns,
including effective default scalar writes. Exactly one nonempty matching pattern
identifies Enabled or Disabled. Both/neither matches, unsupported patterns and missing positive state
evidence produce `indeterminate` with a separate `state_reason`; the read filter
accepts that observation. Policy-ownership ambiguity remains separate. The
ownership helper intentionally retains its catalogue parameter for call compatibility, while ownership framing
does not use current catalogue addresses to infer a historical producer. Policies
with no attributed records are absent from this configured-only view: empty
patterns cannot prove historical Enabled, Disabled or Not configured intent.
Child-only policies (no policy-level writes) are observed from their element rows
(#147): a value write is only written by Enabled, and a delete carrying a disable's
editor ID only by Disabled. A complete set of element deletes that no enabled choice
writes is Disabled. Where every element is an enum with a `<delete/>` item
(inetres.admx `NeedEdgeBrowser`), an imported disable is byte-identical to enabling
it with that item chosen everywhere, so without Studio's editor IDs it is
`indeterminate` (`disabled_matches_enabled_deletes`); partial or mixed sets are
`indeterminate` (`no_complete_pattern`). A legacy `disabled-element-X` suffix is
identity only when no element ID can also spell it. Only when no row is bound to
an element does the older heuristic remain, and an unclaimed possible historical
policy-level record still exposes uncertainty. Each single-value element reads
exactly one row: several rows bound to it, or a delete an enabled policy cannot
write, are listed as unresolved instead of decoded (#146). Actual stored state
frames outside current pattern addresses cannot prove a current state, regardless
of today's identity family; unclaimed rows remain visible as unresolved evidence.
Structured rows whose stored side or hive disagrees with their editor identity
remain visible as unresolved records and cannot supply decoded child values.
A side move keeps state writes observable at their actual native addresses;
a row with a hive inconsistent with its side cannot establish a current state.
Reads do not repair the identity or change reconfiguration ownership.
Decoded child values remain available. Observations do not alter export
eligibility, write records, or introduce an Indeterminate mutation target.
Explicit Enable/Disable/Not configured actions retain their existing validation,
actor/reason and GPO/catalogue version checks, including for Indeterminate rows.
These synthetic read-contract checks are not additional Windows qualification.
The browser sends the reviewed token as `expected_catalogue_revision` on policy
configuration and every bulk batch, alongside the GPO's expected revision.
Changing a source or restarting the process rotates the catalogue token.
A stale token returns HTTP 409 with `error.code = "catalogue_conflict"` before
any workspace mutation. It requires reviewing the current policy rather than
reapplying a GPO revision conflict. Legacy API clients may omit the catalogue
precondition for compatibility; callers that need reviewed mappings must send it.

The local process publishes the catalogue and its sources as one immutable
snapshot. A shared reentrant lock covers activation and policy resolution through
the workspace commit, in catalogue-then-store lock order. File ingestion runs
outside that lock; a failed load or merge preserves the previous snapshot.
This is a single-process authoring contract. Persistent catalogue configuration
and coordination between hosted workers remain part of the shared-service work.

## Collection observations and removed policies

The server's ordinary policy listing includes Starter GPOs. A successful Starter
create or delete updates that membership and refreshes the current open Starter
library. Completion does not reopen a closed library or select another policy.
Deleting the selected policy clears its workspace view; unrelated selections,
pending navigation and editors retain their ownership.

The browser records each confirmed deletion with a per-GUID monotonic watermark.
These numbers last for the page lifetime so older UI continuations remain retired
even after newer list reads finish. They are ordering metadata, without persistent
browser storage or cached policy content. A fresh request issued after deletion
may admit a genuinely recreated GUID. Existing authoritative-list removal and
post-dispatch upsert observations continue to apply.

API delivery registers response-object provenance in a WeakMap using the dispatch
watermark, with no early summary, selection, editor or error updates. Owned summary
and payload consumers check that provenance before publishing. For a GUID with a client-observed deletion, copies of a GPO must retain
explicit provenance rather than acquiring its current generation by accident.
An untagged object for a never-deleted GUID remains admissible at watermark zero;
this is a client-observed-deletion contract, not a blanket ban on object copies. Dialog opening views and chosen ADMX targets separately retain their opening
policy generations: a new Save request cannot retarget an old draft to a recreated
GUID/revision. Related unsaved fields remain available, and the operator must
close and reopen the editor to review an existing target. Conflict and bulk paths
use the same deletion ordering checks alongside their revision/catalogue guards.

## Reviewed artifact downloads

The first-party export review captures the policy GUID, revision and review-model
SHA-256. The existing `export.zip`, `plan.ps1` and `gpmc-backup` GET routes accept
paired `expected_revision` and `expected_review_hash` query parameters. A mismatch
returns JSON `409 export_review_conflict`; an incomplete or malformed pair returns
422. Callers that omit both parameters retain the legacy latest-state behavior.
These are read preconditions and do not create workspace revisions.

Each request reads one immutable policy object for the preconditions, validation
and serialization. A concurrent edit after that read can still produce the
reviewed artifact, never newer content under the old review. The review-model hash
also distinguishes replacement policies with changed review content even when
they reuse a GUID and integer revision. It retains the existing hash definition;
metadata excluded from that definition does not become part of the review.
Current validation and artifact capability refusals remain in force.

Successful responses use `Cache-Control: no-store` and expose
`X-GPO-Studio-Guid`, `X-GPO-Studio-Revision`, `X-GPO-Studio-Review-Hash` and
`X-GPO-Studio-Artifact-SHA256`. The browser verifies the first three against the
captured review, then downloads those response bytes through a local Blob URL.
It also checks the artifact digest when Web Crypto is available; supported HTTP
origins without Web Crypto still enforce the revision/content-review binding.
This is freshness and response consistency checking, not independent artifact
qualification or protection against an untrusted server.

Pending requests and inline failures belong to one opening of the review. Cancel
and Close remain usable; late success, errors and finalizers cannot download or
change a reopened review. A stale review must be closed and the policy reloaded
and reviewed before downloading changed content.

## Registry policy fidelity

`registry_pol.py` implements PReg version 1:

- header `PReg` and little-endian version `1`;
- UTF-16LE bracketed records;
- DWORD type and data-size fields;
- standard numeric, string, binary and multi-string encodings;
- the conventional `**del.<name>` value-deletion marker.

Records are sorted by `(key, value name)`, so equivalent drafts produce
byte-identical policy files. ZIP entries have fixed timestamps and order. This
determinism is what makes review hashes and later signatures possible.

## What the design does not claim

- A staged link records intent. It does not prove that the target exists or
  that the operator may modify it.
- A `Registry.pol` file is not a complete GPMC backup. The bundle is a GPO
  Studio publication artifact and is labelled as one.
- GPO-side enablement and link order do not simulate per-object RSoP.
- WMI filtering, security filtering and loopback depend on evaluation context.
  When implemented, they should be shown with caveats.
