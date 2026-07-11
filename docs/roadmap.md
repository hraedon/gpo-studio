# GPMC compatibility roadmap

“Matches all GPMC features” spans multiple storage formats, CSEs, directory
objects, delegation semantics, backup formats, and runtime policy evaluation.
This roadmap makes the parity claim measurable instead of treating it as one
large checkbox.

For the larger end state beyond compatibility—policy-as-code, controlled
publication, promotion rings, estate-scale convergence, multi-forest operation,
an adapter ecosystem, and independent evidence—see
[`Plan 001: maximalist platform`](../plans/001-maximalist-platform.md).

| GPMC capability | v0.1 | Planned implementation |
|---|---:|---|
| GPO inventory/create/rename/description | Draft | Import and reconcile IDs with a publication target |
| Computer/User side status | Draft + plan | Verify against lab Windows publication worker |
| Administrative Templates registry policy | Core primitives | ADMX/ADML catalogue, typed presentation controls, explain text |
| Raw registry preferences | Yes | Add GPP XML actions and item-level targeting |
| Link enabled/enforced/order | Draft + plan | Live target discovery and compare-before-publish |
| Revision/audit/rollback | Yes (workspace) | Signed approvals and published-state correlation |
| Backup/import/restore | Studio bundle export | Parse and emit complete GPMC backup directory + manifest |
| Security filtering/delegation | No | SDDL editor with effective-rights preview and lockout guards |
| WMI filters | No | Directory-backed filter catalogue and syntax linting |
| GPP Drive/Files/Folders/Groups/Tasks | No | Typed editors per CSE, never accepting embedded passwords |
| Scripts/software installation/folder redirection | No | Artifact store, checksum/signing, and dedicated editors |
| Starter GPOs | No | Template catalogue and inheritance metadata |
| Migration tables/cross-domain copy | No | SID/path mapping rules with dry-run report |
| Modeling/results reports | No | Consume gpo-lens topology and clearly bounded RSoP views |
| HTML/XML reports | Manifest only | Human diff and policy documentation report |
| Live create/update/delete | Intentionally absent | Isolated privileged Windows worker after security gates |
| Intune migration planning | No | Per-GPO readiness plus cohort-based intent, assignment, and cutover planning |

## Milestone 1 — usable policy editor

- ADMX/ADML ingestion and searchable category tree.
- Presentation-element widgets: checkbox, decimal, text, enum, list, and
  multi-text mapped to registry values.
- Import from a gpo-lens estate export into read-only baselines, then fork a GPO
  into an editable draft.
- Semantic diff between baseline, draft, and latest observed estate.
- Complete GPMC backup parser/writer validated by round-trip in a Windows lab.

## Milestone 2 — scope and preferences

- Security descriptor and delegation model with canonical SDDL.
- WMI filter objects and link assignment.
- GPP XML framework plus typed editors for Groups, Services, Scheduled Tasks,
  Files, Folders, Environment, Registry, Drives, Printers, and Shortcuts.
- Item-level targeting expression builder preserving unknown XML extensions.
- Explicit bans and detectors for legacy `cpassword` material.

## Milestone 3 — controlled publication

Publication is allowed only after all gates exist:

1. OIDC/Windows authentication; actor derived from the session, not the body.
2. Role separation for author, reviewer, and publisher.
3. Signed immutable artifact and two-person approval.
4. Short-lived, delegated worker credentials; never Domain Admin.
5. Allow-listed typed operations with no arbitrary PowerShell input.
6. Compare-and-swap against current AD and SYSVOL versions.
7. Backup before mutation, saga-style compensation, and clear partial-failure
   recovery.
8. Event log/SIEM output with artifact digest and resulting GPO versions.
9. Windows lab integration suite for every supported CSE.

The complete worker protocol, concurrency/rollback model, privilege profiles,
and rollout gates are specified in
[`live-publication.md`](live-publication.md), with adversarial analysis in
[`publisher-threat-model.md`](publisher-threat-model.md). Managed publication
must not begin until its Phase 0 and Phase 1 gates are satisfied.

## Milestone 4 — forest-scale operations

- Multi-domain target registry and migration tables.
- Reusable policy components, templates, promotion environments, and rings.
- Bulk linting, owners, expiry, exception workflow, and change windows.
- gpo-lens analysis embedded as the read-only verification plane: conflicts,
  topology, dangerous configuration, baseline drift, and post-publish checks.

At that point the goal is no longer a browser clone of MMC. It is a safer
policy-as-change system that retains GPMC interoperability while adding review,
determinism, provenance, and automation ergonomics.
