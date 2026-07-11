# Architecture and trust boundaries

## Decision: offline drafts first

A GPMC-like editor naturally tempts a design where a long-running web service
holds Domain Admin credentials. That is an unnecessarily large blast radius.
GPO Studio instead separates three concerns:

1. **Authoring** is unprivileged and local. Drafts are ordinary structured data.
2. **Review** operates on immutable revisions and deterministic artifacts.
3. **Publication** is a replaceable adapter that is absent from the web process.

The v0.1 adapter is an exported PowerShell plan. A future enterprise adapter
should be a short-lived Windows worker using delegated rights, signed inputs,
approval tokens, and an allow-listed command vocabulary—not arbitrary shell.

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

The core does not import FastAPI. The web layer may be replaced by a CLI,
desktop shell, or automation API without changing policy serialization.

## Mutation contract

Every mutation includes:

- `expected_revision`: compare-and-swap protection against lost updates;
- `actor`: the claimed local operator identity (authentication is deployment
  scope in v0.1);
- `reason`: a required human-readable audit note.

On success the store creates a complete immutable snapshot at revision `N+1`.
Restore never rewrites history: it copies an old snapshot into a new revision.

For a multi-user deployment, actor must come from trusted authentication
middleware rather than request JSON. That is intentionally listed as a gate in
the roadmap.

## Registry policy fidelity

`registry_pol.py` implements PReg version 1:

- header `PReg` and little-endian version `1`;
- UTF-16LE bracketed records;
- type and data-size DWORD fields;
- standard numeric, string, binary, and multi-string encodings;
- conventional `**del.<name>` value deletion marker.

Serialization sorts by `(key, value name)`, making equivalent drafts produce
byte-for-byte identical policy files. The ZIP also has fixed entry timestamps
and ordering. Determinism enables review hashes and signatures later.

## Deliberate non-claims

- A staged link is intent, not proof that the target exists or that the
  operator may modify it.
- A Registry.pol file is not a complete GPMC backup. The bundle is a GPO Studio
  publication artifact and is labeled as such.
- GPO-side enablement and link ordering do not simulate per-object RSoP.
- WMI/security filtering and loopback require evaluation context; they should
  be displayed with caveats when implemented.

