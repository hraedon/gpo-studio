# Plan 025 — Security Settings extension parity

Status: implemented (domain layer) — **not surfaced**. `security_template.py`,
`object_security.py`, `network_security.py`, and `policy_families.py` are
landed and unit-tested but are reachable from no API endpoint, UI module, or
export path; their only consumers are their own test modules. Plan 033 now has
a certified Studio-origin `secedit` writer tranche for account policy, event
audit, and user rights, but platform wiring and the broader native corpus and
security areas remain open. The `Windows-verified` claim in this plan's scope
is **not** met.

**Unproven draft, not an asset** (operator ruling 2026-07-29): the wire
behaviour of this layer is a hypothesis about Windows until an evidence lane
certifies it, and every layer examined so far has needed correction. See
[`docs/domain-layer-status.md`](../docs/domain-layer-status.md).

Scope: typed, lossless, Windows-verified support for supported in-box Security
Settings families
Depends on: Plans 021 and 023 ACL/principal foundations
Review gate: **REVIEW AND REFINE — REQUIRED between every security family**

## WP-1 — Security template foundation

- Parse/preserve INF, registry security templates, extension lists, versioning,
  and report representations without flattening unknown sections.
- Model local/domain applicability, merge behavior, principal resolution,
  privilege constants, ACL propagation, and target-version support.
- Add baseline import/export and a semantic comparison oracle using Windows.

## WP-2 — Account and local policy families

- Account/password/lockout and Kerberos policy with domain-role constraints.
- Audit policy and advanced audit policy, including conflict detection.
- User rights assignment and security options with exact principal semantics.
- Make domain-controller/domain-wide blast radius explicit and enhanced-approved.

## WP-3 — Groups, services, and object security

- Restricted Groups, System Services, Registry security, and File System
  security with complete ACL/inheritance semantics.
- Preview affected principals/objects and refuse unresolved trustees or paths.
- Verify merge/removal and client-side ACL application/rollback behavior.

## WP-4 — Network and public-key families

- Windows Defender Firewall with Advanced Security and connection security/IPsec.
- Public Key Policies, auto-enrollment, EFS, trusted roots, and certificate
  settings without importing private keys or secrets.
- Wired/wireless, Network List Manager, and other supported network/security
  extensions identified by Plan 021.
- Treat obsolete/removed families as preserve-only by target version.

## WP-5 — Safety and evidence

- Per-family least-privilege publisher operations, preconditions, backup,
  compensation, endpoint validation, and emergency runbooks.
- Dedicated deny rules for lockout, firewall isolation, trust-root replacement,
  audit disablement, broad rights, and protected filesystem/registry targets.
- Test DC, member server, and workstation behavior separately.

## Acceptance gates

- Every claimed family round-trips GPMC backup/editor/report and applies on the
  appropriate reference client role.
- Unknown INF/extension data is preserved and blocks lossy replacement.
- ACL/principal results match Windows, including deny and inheritance.
- High-risk failures stop safely and have demonstrated recovery procedures.

## REVIEW AND REFINE — REQUIRED

Each WP-2/3/4 family is a separate stop/go tranche. Review Windows normalization,
blast radius, client behavior, compensation, and privilege requirements before
starting the next family or enabling publication. An external Windows security
review is required before any Security Settings adapter reaches live RW status.
