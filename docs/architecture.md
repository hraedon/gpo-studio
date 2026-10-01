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
