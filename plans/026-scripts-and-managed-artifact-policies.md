# Plan 026 — Scripts and managed-artifact policies

Status: proposed (post-1.0)
Scope: startup/shutdown/logon/logoff and PowerShell script policy with a secure,
content-addressed artifact lifecycle
Depends on: Plans 021 and 025 security classification
Review gate: **REVIEW AND REFINE — REQUIRED before executable publication**

## WP-1 — Artifact store and provenance

- Content-address every script and companion file; record original name, type,
  size, signer, scan result, source, owner, expiry, and licensing metadata.
- Enforce immutable versions, signatures, malware/secret scanning, size/type
  limits, retention, quarantine, and secure deletion.
- Never fetch mutable URLs during publication; bind exact bytes into approval.

## WP-2 — Script policy model/editor

- Support startup, shutdown, logon, and logoff script ordering, parameters,
  Computer/User scope, synchronous/asynchronous behavior, and PowerShell script
  ordering/options represented by supported Windows policy.
- Preserve unknown script types and metadata.
- Quote/render parameters without exposing an arbitrary publisher command path.
- Preview execution identity, trigger, ordering, dependencies, and risk.

## WP-3 — GPMC and client interoperability

- Verify editor/report and backup/import/copy/restore for supported script types.
- Apply to isolated clients and capture event logs, exit behavior, timeout,
  network dependency, reboot/logon implications, and removal behavior.
- Test signed/unsigned execution policies without weakening endpoint controls.

## WP-4 — Typed publication

- Publisher accepts only signed artifact references and typed script metadata;
  it never accepts free-form PowerShell or shell text as an operation.
- Stage atomically where possible, read back hashes, and compensate metadata and
  bytes on failure.
- Require enhanced approval and canary endpoint evidence.

## Acceptance gates

- Exact approved bytes are the bytes stored, published, and observed.
- GPMC/client round trips preserve order, parameters, and scope.
- Secret/malware/signature failures are fail-closed and auditable.
- Crash injection cannot leave untracked or mutable executable content.

## REVIEW AND REFINE — REQUIRED

Stop after read-only import/editor/export and lab client execution. Perform
threat modeling, red-team review, code-signing/scan policy review, and operational
recovery exercises. Refine Plan 027's package handling and Plan 030's publisher
protocol before executable publication is allowed.

