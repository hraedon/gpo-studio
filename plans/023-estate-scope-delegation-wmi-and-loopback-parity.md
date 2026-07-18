# Plan 023 — Estate, scope, delegation, WMI, and loopback parity

Status: proposed (post-1.0)
Scope: match GPMC's live navigation, search, SOM/link/inheritance management,
security/delegation surfaces, WMI filters, and loopback semantics
Depends on: Plans 021 and 022
Review gate: **REVIEW AND REFINE — REQUIRED before any scope/security writes**

## WP-1 — Forest and estate discovery

- Add read-only collectors for forests, domains, sites, subnets, OUs, GPOs,
  Starter GPOs, WMI filters, trusts needed for resolution, and permissions.
- Add a read-only principal resolver in which an administrator searches within
  an explicitly selected forest/domain/container and chooses the AD object to
  reconcile. Record `objectGUID`, current `objectSid`, `sIDHistory`, object
  class, canonical names/DN, source DC, replication metadata, access gaps, and
  collection time with the result.
- Support direct lookup of a stored SID and validation of a specified AD object,
  but never silently substitute a same-named object, broaden the selected
  directory scope, or collapse identities across a trust boundary.
- Reproduce GPMC search/filter cases with paging and incomplete-access markers.
- Pin collectors to named DCs and record replication/version evidence.
- Support multiple domains/forests without merging identities or trust scopes.

## WP-2 — SOM, links, and inheritance

- Model site/domain/OU links, enabled/enforced/order state, block inheritance,
  inherited links, disabled GPO sides, and resulting precedence.
- Provide exact before/after link-order planning and enhanced warnings for
  domain root, sites, Domain Controllers OU, enforced links, and block changes.
- Implement typed create/update/remove link and block-inheritance operations
  behind distinct least-privilege capability profiles.
- Snapshot and compensate the entire affected SOM ordering, not one link.

## WP-3 — ACLs, filtering, and delegation

- Preserve complete owner/group/DACL/SACL, ACE order, deny/inherited/object ACEs,
  unknown rights, and inheritance flags.
- Separate Apply/Read filtering from GPO edit/delete/security rights and SOM
  link rights.
- Add effective-rights previews using real synthetic tokens and Windows access
  checks, plus owner/admin lockout guards.
- Produce a reconciliation review for every principal-bearing reference that
  shows the observed SID/name, the administrator-selected AD object, current
  SID versus SID-history match, object type compatibility, affected adapters,
  and unresolved/deleted/inaccessible state. Selection alone does not mutate a
  draft or live GPO.
- Require an explicit mapping decision before rewriting security filtering,
  delegation trustees, GPP group targets/members, group ILT predicates, or any
  later adapter's principal fields; adapter ownership determines whether the
  proposed mapping is editable, preserve-only, or blocked.
- Cover delegation for GPOs, domains/OUs, Group Policy Modeling, and remote
  Group Policy Results.

## WP-4 — WMI filters and loopback

- Discover and manage WMI filter identity, namespace, query groups, ownership,
  ACLs, description, references, and deletion safety.
- Parse/lint WQL without claiming runtime truth; preserve multi-query filters.
- Add loopback Merge/Replace configuration and scope/diff visualization.
- Treat WMI evaluation and group membership as evidence-dependent/unknown when
  runtime context is missing.

## Acceptance gates

- Topology/search results match GPMC in all reference estates.
- Link precedence and compensation match Windows across nested/enforced/blocked
  fixtures and concurrent native GPMC changes.
- ACL round trips preserve all ACEs and effective rights match Windows checks.
- SID/principal resolution matches Windows for selected user, group, computer,
  managed service, foreign-security-principal, SID-history, deleted, ambiguous,
  inaccessible, cross-domain, and cross-forest fixtures. No name-only or
  unselected-object mapping can pass the reconciliation gate.
- WMI CRUD/association and loopback reports match GPMC and endpoint behavior.

## REVIEW AND REFINE — REQUIRED

Complete read-only discovery and dry-run evidence first. Perform a focused
security review of privilege profiles, lockout prevention, link compensation,
multi-DC races, and blast-radius presentation. Refine write operations and Plan
030 publication profiles before granting any live scope or security rights.
