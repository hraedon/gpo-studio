# Plan 021 — GPMC parity contract and adapter platform

Status: proposed (post-1.0)
Scope: define a falsifiable meaning of GPMC parity and establish the lossless
model, extension inventory, and adapter lifecycle required by every later plan
Depends on: Plan 020
Review gate: **REVIEW AND REFINE — REQUIRED before Plan 022 implementation**

> **Program status.** Plan 021 is the head of the post-1.0 parity program.
> Plans 022–031 are **provisional pre-inventory drafts**, not settled execution
> plans: their work-package breakdowns encode expected shape, but the
> authoritative capability inventory (WP-1) and reference-estate evidence (WP-4)
> below will revise them. Do not treat a 022–031 work package as a contract
> until it has passed the review gate at the end of this plan. This is the same
> discipline that reframed Plan 001 from an execution plan into a charter.

## Purpose

GPMC combines lifecycle/scope/reporting features with the separate Group Policy
Object Editor extension snap-ins that own individual setting families. “Parity”
must therefore be a versioned compatibility matrix, not one checkbox.

For this program, parity means semantic coverage of the supported in-box GPMC
and GPO Editor capabilities on named Windows Server/client versions. It does not
mean pixel-identical MMC UI, synthesis of unknown third-party extensions, or
support for removed legacy components. Unknown and legacy content must be
losslessly preserved, visibly classified, and blocked from lossy publication.
The permanent `cpassword` ban remains an intentional safety divergence.

## WP-1 — Authoritative capability inventory

- Inventory GPMC lifecycle operations, forest/domain/site/OU views, links,
  inheritance, ACL/delegation, WMI filters, Starter GPOs, backup/import/copy/
  restore, migration tables, reports, Modeling, Results, and search.
- Inventory every in-box editor and client-side extension by side, CSE/tool
  GUID, storage format, OS availability, deprecation state, and management API.
- Record preference item types, common options, actions, and complete ILT AST.
- Classify each row as `verified-rw`, `verified-ro`, `preserve-only`,
  `intentional-deny`, `not-present-on-target`, or `unknown`.
- Link every row to Microsoft documentation and lab evidence.

## WP-2 — Three-layer lossless model

- Store original bytes, normalized semantics, and editable intent separately.
- Add content-addressed artifacts, CSE manifests, dependencies, source version,
  and target compatibility to the Studio bundle.
- Add hash profiles for settings, scope, security, artifacts, deployment, and
  complete preserved state.
- Define edit isolation: editing one adapter cannot rewrite untouched adapters.
- Refuse serialization when preserved content would be lost or reordered.
- Add bundle/schema migrations with explicit loss reports.

## WP-3 — Adapter SDK and lifecycle

- Define typed parser, serializer, validator, canonicalizer, diff, renderer,
  reporter, publisher, verifier, and compensation interfaces.
- Require adapter manifests with GUIDs, sides, Windows versions, risk class,
  dependencies, privileges, artifact handling, and evidence status.
- Isolate untrusted/third-party parsers and renderers out of process.
- Provide common principals, paths, schedules, ACLs, artifacts, common options,
  ILT, unknown fields, and version-list primitives.
- Reject adapter registration with incomplete dispatch or missing evidence.

## WP-4 — Reference estates and evidence schema

- Expand the Plan 017 lab across supported Windows versions and client roles.
- Generate one minimal GPMC-origin fixture per matrix row plus mixed-CSE GPOs.
- Define normalized GPMC report, backup, RSoP, and endpoint-observation records.
- Version and sign evidence packs; include negative and downgrade fixtures.
- Add a public matrix generator that derives claims only from passing evidence.

## Acceptance gates

- Every target GPMC surface has a matrix row, owner, test oracle, and state.
- Unknown bytes survive no-edit round trips byte-for-byte.
- Editing one adapter leaves all other adapter bytes unchanged.
- Two independent implementations reproduce canonical/hash vectors.
- The matrix cannot show `verified-rw` without Windows and endpoint evidence.

## REVIEW AND REFINE — REQUIRED

Stop here. Review the capability inventory with Windows/GPMC operators, compare
it against the actual supported reference estates, and refine Plans 022–031.
Resolve scope disputes, OS-version policy, intentional safety divergences, and
adapter boundaries before expanding authoring breadth.

