# Plan 002 — Foundation hardening

Status: executable plan  
Scope: strengthen the information and trust foundations of the offline
workbench without weakening the v0.1 safety boundary  
Depends on: existing v0.1 editor, `docs/architecture.md`, `plans/001-maximalist-platform.md` §14

## Purpose

The maximalist plan (001) defines the "Immediate next tranche" in §14. This plan
turns those ten items into concrete work packages with acceptance criteria.
Items requiring a Windows lab (§14.5, §14.6) are documented as deferred with
their prerequisite; the remaining items are code-achievable and land here.

## Work packages

### WP-1 — Canonical model and semantic identities (§14.1, WS-A)

Goal: give every GPO, setting, and link a stable semantic identity independent
of serialization order, and produce deterministic content hashes.

Deliverables:

- `canonical.py` — RFC 8785 JSON Canonicalization Scheme implementation.
- `canonical.py` — semantic hash (SHA-256 over canonical JSON of a normalized
  representation) for GPO, RegistrySetting, and GPOLink.
- Bundle v2: `manifest.json` gains `schema_version: 2`, `semantic_sha256`,
  and `canonical_model` fields.
- Deterministic test vectors for canonicalization and hashing.

Acceptance gates:

- Equivalent input ordering produces identical canonical bytes and hashes.
- Non-semantic changes (whitespace, key order) do not change the hash.
- Semantic changes (value, type, key, side) always change the hash.
- Existing v0.1 bundle tests still pass (backward-compatible schema bump).

### WP-2 — ADMX/ADML catalogue parser (§14.3, WS-C)

Goal: parse Administrative Template definitions into a searchable model without
editing them yet.

Deliverables:

- `admx.py` — parse ADMX XML: policy definitions, categories, supported-on
  definitions, presentation elements, and namespace declarations.
- `admx.py` — parse ADML XML: display names, explain text, and presentation
  string tables.
- Model types: `PolicyDefinition`, `Category`, `SupportedOnDefinition`,
  `PresentationElement`.
- Synthetic ADMX/ADML fixtures covering all presentation element types.
- Tests for round-trip parsing and unknown-element preservation.

Acceptance gates:

- A representative synthetic ADMX corpus parses without silent loss.
- Unknown ADMX elements are preserved as opaque records, not discarded.
- Locale changes affect display text only, never semantic identity.
- Namespace declarations are captured so policy IDs are unambiguous.

### WP-3 — Semantic diff (§14.4, WS-A/WS-F)

Goal: compute deterministic, setting-aware diffs between GPO snapshots.

Deliverables:

- `diff.py` — two-way diff (added, removed, changed settings and links).
- `diff.py` — three-way diff (baseline, draft, observed) with conflict
  detection (both sides changed the same setting differently).
- Diff results use typed operation records, not free-form dictionaries.
- Tests covering add, remove, modify, no-op, and three-way conflict.

Acceptance gates:

- Identical inputs produce an empty diff.
- Reordering settings does not produce false adds/removes.
- Value-only changes are reported as modifications, not add+remove.
- Three-way conflicts are detected when both draft and observed change the
  same semantic identity to different values.

### WP-4 — GPMC backup preservation reader (§14.2, WS-B)

Goal: read a GPMC backup directory and preserve its content, including unknown
CSE data, without claiming edit support for unsupported extensions.

Deliverables:

- `backup.py` — parse `manifest.xml` and `bkupInfo.xml` from a GPMC backup.
- `backup.py` — enumerate CSE extensions and their file references.
- `backup.py` — preserve unsupported CSE content as opaque byte blobs with
  content hashes.
- Synthetic GPMC backup fixtures (registry-only and mixed-CSE).
- Tests for manifest parsing, CSE enumeration, and byte preservation.

Acceptance gates:

- Registry-only synthetic backups parse completely.
- Unknown CSE content is preserved byte-for-byte.
- Missing or malformed manifest files fail safely with a clear error.
- No unsupported CSE is silently normalized or dropped.

### WP-5 — Publisher payload canonicalization (§14.8, WS-H)

Goal: establish the canonicalization and hashing foundation for signed
publisher artifacts, with test vectors, but no executable publisher.

Deliverables:

- `payload.py` — publisher job model matching the schema in
  `docs/live-publication.md`.
- `payload.py` — canonical payload computation: strip `approval` block,
  canonicalize with RFC 8785 (via `canonical.py`), SHA-256 digest.
- `payload.py` — signature structure: key ID, algorithm, approver subject,
  and signature value.
- `payload.py` — `verify_payload_digest()` checks that the approval's
  `payload_sha256` matches the canonical payload digest. Actual Ed25519
  cryptographic verification is deferred to the publisher worker, which
  will have key material.
- Tests for canonicalization, digest stability, and digest verification.

Acceptance gates:

- The canonical payload excludes `approval` and is self-consistent.
- Equivalent payloads with different JSON key ordering produce identical
  digests.
- QWORD values are represented as strings in the wire contract.
- Digest verification rejects tampered payloads (mismatched digest).
- Digest verification returns `False` when no approval is present.

### WP-6 — Identity abstraction (§14.7, WS-H)

Goal: prepare the identity interface for trusted authentication without
enabling managed writes.

Deliverables:

- `identity.py` — `Identity` protocol with `actor`, `is_trusted`, and
  `source` attributes.
- `identity.py` — `ClaimedIdentity` implementation for v0.1 local mode
  (honestly labels itself as untrusted).
- Tests for identity creation, labeling, and protocol conformance.

Deferred to a follow-up:

- Wiring `WorkspaceStore` and API to use `Identity` instead of raw actor
  strings. The module is complete and tested; integration into the store
  and API is a separate change that touches the mutation contract and
  requires careful backward-compatibility testing.

Acceptance gates:

- v0.1 behavior is unchanged: the API still accepts actor from request body.
- `ClaimedIdentity.is_trusted` returns `False` and `source` returns
  `"request-body"`.
- The `Identity` protocol is `@runtime_checkable` and `ClaimedIdentity`
  satisfies it.
- Future trusted identity implementations can be dropped in without changing
  the protocol contract.

### WP-7 — Malicious input and fuzz corpus (§14.9, WS-O)

Goal: harden existing parsers against malformed, oversized, and adversarial
input.

Deliverables:

- Malformed Registry.pol corpus: truncated, cyclic, oversized, duplicate-key,
  and Unicode edge cases.
- ZIP export traversal tests: path traversal entry names, oversized entries,
  and nested archive attempts.
- ADMX/ADML malformed XML corpus: unclosed tags, namespace collisions, deeply
  nested elements, and entity expansion.
- All corpus entries must fail safely (raise, not crash or produce garbage).

Acceptance gates:

- Every malformed input produces a typed error, never a crash or silent
  garbage output.
- Oversized values are rejected before serialization.
- ZIP entries with traversal paths (`../`, absolute, device names) are
  rejected or sanitized.
- No parser accepts cyclic or deeply nested structures without a depth limit.

### WP-8 — Windows interoperability lab and plan validation (§14.5, §14.6)

Status: deferred — requires Windows lab infrastructure.

This work package is documented here for completeness. It requires ephemeral
Windows Server/DC infrastructure and cannot be implemented as pure code. Its
prerequisite is a provisioned Windows lab environment.

When the lab is available:

- Validate `apply.ps1` output against supported Windows Server versions.
- Validate Registry.pol round-trip through GPMC.
- Record compatibility evidence in a signed evidence pack.

## Deferred from §14

- §14.5 (Windows interop lab) and §14.6 (plan-only validation against lab):
  deferred to WP-8 above.
- §14.10 (ship improved Offline Workbench release): this plan produces the
  code; the release packaging is a separate step after integration and review.

## Sequence

```text
WP-1 (canonical model) ──┬──▶ WP-3 (semantic diff)
                         └──▶ WP-5 (publisher payload)

WP-2 (ADMX parser)      ── independent
WP-4 (GPMC backup)      ── independent
WP-6 (identity)         ── independent
WP-7 (malicious input)  ── independent
```

WP-1 is the foundation. WP-3 and WP-5 depend on its canonicalization
interface but can be developed in parallel once the interface is defined.
