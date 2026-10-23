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
