# Plan 025 — Security Settings extension parity

Status: reconciled 2026-10-08 — **every module is a capability or ruled
out**, so the plan has left the unsurfaced domain-layer set. Surfaced and
lane-backed: `policy_families.py` and `object_security.py` (2026-09-11, below)
and the firewall, through `firewall_policy.py`, at
`POST /api/network-security/firewall/render` and
`GET /api/gpos/{guid}/firewall-policy` (2026-10-08). The firewall lane's
certification is `firewall-20261008094055-2092337` (36/36, `a6e0002`; see
[the results](../docs/plan-033/firewall-results.md)). `security_template.py`
exits through its consumers. `network_security.py`'s firewall half is now
explicit re-exports of that codec (WI-076); its IPsec, Public Key, wired and
wireless half is **out of scope for 1.x** (ruling 2026-10-07), retained and
reachable from nothing. The `Windows-verified` claim in this plan's scope is
met only for the measured tranches: endpoint application, GPME editing (WI-077)
and the broader native corpus remain unmeasured.

**Two of the four left, 2026-09-11.** `policy_families.py` is reachable at
`POST /api/security-template/policy-families` and `object_security.py` at
`POST /api/security-template/object-security` (Plan 034 WP-3), each in the
emission direction its lanes certified and no further: both render families as
INF and neither parses one back: no cmdlet oracle reads a GPME-authored
`GptTmpl.inf`, although the lanes do parse the INF Windows exports through
`secedit /export` (`finalize_wp3_run.py`). They are the first modules of this plan to satisfy both halves of the
exit condition in the order
[`domain-layer-status.md`](../docs/domain-layer-status.md) requires — lane
first, then surface. Under the 2026-10-07 ruling `security_template.py` exits through those two
consumers, which left `network_security.py` as the one module still
unsurfaced until its firewall half reached the surface above on 2026-10-08.

**Rulings of 2026-10-07** ([Plan 034 completion](../docs/direction-2026-10-07-plan-034-completion.md)).
`security_template.py` **exits through its consumers**: three live verdicts
bind it (`wp3-member`, `wp3-dc`, `object-security`) and both endpoints above
emit through it, and reading a GPME-authored `GptTmpl.inf` is out of
scope until a Security Settings import surface is proposed.
`network_security.py`: IPsec, Public Key, wired and wireless policy are **out
of scope for 1.x**; the firewall half was pending a lane, which reached its
verdict on 2026-10-08, before the 2026-10-24 cutoff.

Surfacing `object_security.py` also found two defects a certified lane could
not: WI-064 (the restricted-groups writer emits a bare SID where Windows
exports a star-SID, so that family was deliberately left off the surface) and WI-065
(`validate` calls an unparsed descriptor unparseable, so this lane's own
candidate fails its own validator). Both are filed against the batch that
re-runs the estate, because this module is bound by its verdict.

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
