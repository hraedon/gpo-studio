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
>
> **Pre-review spike boundary.** WP-2/WP-3 architecture spikes are welcome
> before the review gate, but they must not land bundle schema migrations,
> hash-profile definitions, or canonical/hash vectors on `main`. Those
> artifacts are exactly the contract the review gate exists to ratify; explore
> them on throwaway branches.

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

## Explicit operator outcomes

The parity program carries these operator-visible outcomes across plan
boundaries; they are not satisfied by a parser or API existing in isolation.

| Outcome | Foundation | Product delivery | Closure evidence |
|---|---|---|---|
| Browse a GPO's configured settings with useful names, state, descriptions, support requirements, source template/adapter, and raw fallback | This plan's renderer/reporter contract | Plan 022 WP-3 for Administrative Templates; Plan 028 WP-4 for the complete per-GPO settings explorer and reports | Plan 031 mixed-CSE browse/report comparison against GPMC |
| Reconcile stored SIDs/principals against administrator-specified AD objects without silently guessing identity | This plan's principal-reference and resolution model | Plan 023 WP-1/WP-3 for read-only object selection and resolution; Plan 028 WP-2 for reviewed migration mappings; Plan 030 Phase A for the isolated live resolver | Plan 031 cross-domain, SID-history, deleted-object, ambiguity, and stale-resolution evidence |

## WP-1 — Authoritative capability inventory

> **Pre-gate deliverable:** [`docs/plan-021/capability-inventory.md`](../docs/plan-021/capability-inventory.md)
> (version `0.1.0-pre-gate`). Realizes the inventory below as a versioned
> matrix; the review gate ratifies or amends it.

- Inventory GPMC lifecycle operations, forest/domain/site/OU views, links,
  inheritance, ACL/delegation, WMI filters, Starter GPOs, backup/import/copy/
  restore, migration tables, reports, Modeling, Results, and search.
- Inventory the GPMC settings/report browse surfaces, including configured-only
  views, explanation/support text, unresolved settings, and extension-owned
  descriptions.
- Inventory every principal-bearing field and every supported AD object type,
  including current SID, SID history, foreign security principals, deleted or
  inaccessible objects, and cross-domain/forest identity boundaries.
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
- Provide a common configured-setting rendering contract with semantic state,
  display/explanation/support text, source provenance, and an explicit raw or
  opaque fallback when no verified renderer owns the setting.
- Provide common principals, paths, schedules, ACLs, artifacts, common options,
  ILT, unknown fields, and version-list primitives. A principal reference must
  distinguish observed SID/name from a selected AD object's immutable
  `objectGUID`, current `objectSid`, `sIDHistory`, object class, domain/forest,
  source DC/snapshot, resolution time, and resolution state; names alone are
  never stable identity.
- Reject adapter registration with incomplete dispatch or missing evidence.

## WP-4 — Reference estates and evidence schema

> **Pre-gate deliverables:**
> [`docs/plan-021/reference-estates-and-evidence.md`](../docs/plan-021/reference-estates-and-evidence.md)
> (provisional target matrix, licensing/redaction rules, evidence-pack schema,
> negative/downgrade fixtures) and `scripts/generate_public_matrix.py` +
> `src/gpo_studio/evidence.py` (the public matrix generator that derives claims
> only from passing evidence). The review gate ratifies or amends these.

- Adopt a **provisional target matrix immediately** so fixture generation is
  not blocked on the review gate: Windows Server 2019/2022/2025 for domain
  roles and current-GA Windows 11 for clients, with Windows 10 rows admitted
  only behind an explicit ESU decision. The review gate ratifies or amends
  this matrix; it does not initiate it.
- Expand the Plan 017 lab across supported Windows versions and client roles.
- Generate one minimal GPMC-origin fixture per matrix row plus mixed-CSE GPOs.
- Define normalized GPMC report, backup, RSoP, and endpoint-observation records.
- Version and sign evidence packs; include negative and downgrade fixtures.
- Define licensing rules for corpus content before any fixture pack is
  assembled: ADMX/ADML files are Microsoft- or vendor-copyrighted, so each
  pack must be classified as storable in-repo, referenced by hash with
  regeneration instructions, or excluded from distribution entirely.
- Define the redaction contract for Windows-generated fixtures, GPMC reports,
  and endpoint observations: synthetic directory names, SIDs, paths, and
  exports only, with sanitization verified by the identifier gate before an
  evidence pack is signed.
- Add a public matrix generator that derives claims only from passing evidence.

## Acceptance gates

- Every target GPMC surface has a matrix row, owner, test oracle, and state.
- The contract can render every configured setting as verified semantic detail
  or an explicit raw/opaque entry; an unknown setting is never silently absent.
- Principal-resolution fixtures prove that only explicitly selected AD objects
  can become reconciliation targets and that ambiguous, stale, inaccessible,
  deleted, or SID-history-only matches require an explicit review outcome.
- Unknown bytes survive no-edit round trips byte-for-byte.
- Editing one adapter leaves all other adapter bytes unchanged.
- Two independent implementations reproduce canonical/hash vectors.
- The matrix cannot show `verified-rw` without Windows and endpoint evidence.
- No fixture or evidence pack is signed while any of its content lacks a
  licensing classification or identifier-gate-verified redaction.

## REVIEW AND REFINE — REQUIRED

Stop here. Review the capability inventory with Windows/GPMC operators, compare
it against the actual supported reference estates, and refine Plans 022–031.
Resolve scope disputes, OS-version policy, intentional safety divergences, and
adapter boundaries before expanding authoring breadth.
