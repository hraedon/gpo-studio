# Plan 021 — REVIEW AND REFINE gate: decision record (2026-07-19)

Status: **ratified** (operator ruling recorded). This document is the outcome of
the `REVIEW AND REFINE — REQUIRED` gate in
[`plans/021-gpmc-parity-contract-and-adapter-platform.md`](../../plans/021-gpmc-parity-contract-and-adapter-platform.md).
Until this record existed, Plans 022–031 were provisional pre-inventory drafts
and no contract-shaped artifact (bundle schemas, hash/signature profiles,
canonical vectors) could land on `main`. This record ratifies the inventory and
evidence contract and unblocks Plan 022 implementation.

The operator ruled on all five open gate questions. Decisions 1–3 are direct
operator rulings. Decisions 4–5 were explicitly delegated to agent judgment
("open to ideas" / "defer to my judgment"); the designs below are recorded as
the ratified direction and remain open to revision on review of this record.

---

## Decision 1 — OS-version policy: drop Windows 10

**Ruling:** The Windows matrix is defined as **"the OS versions gpo-studio
supports."** On that framing, **Windows 10 is dropped** from the reference
matrix (no Win10/ESU rows).

**Matrix (ratified):**

| Estate | In matrix | Notes |
|---|---|---|
| Windows Server 2025 | yes | primary DC reference (already `verified-ro` at 1.0) |
| Windows Server 2022 | yes | supported DC |
| Windows Server 2019 | yes | supported DC (oldest in-support server) |
| Windows 11 (client) | yes | client-side CSE application |
| Windows 10 | **no** | dropped; out of the supported set |

**What changes:** Plan 021 §"OS-version policy" note (Win10 "only behind an
explicit ESU decision") is resolved to **excluded**. The WP-1 inventory's
`present` / `not-present-on-target` columns are evaluated against WS2019–2025 +
Win11 only. `reference-estates-and-evidence.md` reference estates carry no Win10
row.

---

## Decision 2 — First evidence corpus: falsifiable read/write loop first

**Ruling:** Base the first CSE evidence set on the gpo-lens analysis corpus plus
agent suggestions.

**Principle (ratified):** the first evidence corpus is the set of CSEs where
**gpo-studio authors the content AND gpo-lens independently verifies it**, so
every `verified-rw` claim is falsifiable by an independent read plane rather than
a self-attested smoke run. The full 52-CSE inventory in
[`capability-inventory.md`](./capability-inventory.md) stays the north-star
catalogue; this decision only sequences which rows gather live evidence first.

**First corpus (Tranche A — closed read/write loop exists today):**

| CSE | CSE GUID | Studio authors | gpo-lens verifies | Target |
|---|---|---|---|---|
| Registry / Administrative Templates | `{35378EAC-683F-11D2-A89A-00C04FBBCFA2}` | yes (`Registry.pol`) | yes (Registry CSE) | promote `verified-ro` → `verified-rw` |
| GPP Registry | Registry.xml (GPP) | yes | yes (reads `Registry.xml`) | `verified-rw` |
| GPP Local Users and Groups | Groups.xml (GPP) | yes | yes (reads `Groups.xml`) | `verified-rw` |
| **cpassword negative fixture** | GPP (any) | detects + refuses | detects | `intentional-deny` (proves the footgun detector fires on both planes) |

**Tranche B (add as studio's authoring surface / gpo-lens coverage grows):**
Security Settings (`{827D319E-…}`, `GptTmpl.inf`), Scripts (`{42B5FAAE-…}`),
Folder Redirection (`{25537BA6-…}`). These are in gpo-lens's corpus but not yet
in studio's authoring surface; they enter the evidence corpus when the authoring
side lands, not before.

**Rationale:** Tranche A is the intersection of "gpo-studio can author it today"
and "gpo-lens can read it today," so it is the only set for which a `verified-rw`
claim can be *proven* rather than *asserted*. cpassword is included first because
it is the correctness keystone for Decision 3.

**Grounding in a real environment (hard redaction invariant):** the corpus is
grounded against a **real, operator-held production reference estate** captured
in gpo-lens — used privately to decide *which CSEs and GPO structures actually
occur in a production domain*, so the corpus reflects reality rather than a toy.
That reference estate is **never committed to this repository, and its
identifiers never enter any gpo-studio fixture, evidence pack, or document.**
The committed corpus is **synthetic derivations only**: every fixture and every
evidence pack is `redaction_verified` and passes the identifier gate before it
lands. The real estate grounds *what to model*; the redaction gate guarantees
*what is committed* carries no production identifiers. (This is the same
discipline that governs the whole evidence architecture — synthetic-only,
redaction-gated — applied to corpus selection.)

The invariant extends beyond identifiers to **structure** (cross-lineage review,
2026-07-19): CSE presence/absence and ordering can fingerprint a production
topology even when identifiers are scrubbed. The committed corpus therefore
models **representative** production patterns, not a 1:1 structural clone of the
reference estate. Practical risk is low (the reference estate is not sensitive
per the operator), but the invariant is named on topology, not only identifiers.
Tracked as **WI-007**.

---

## Decision 3 — Intentional safety divergence: correctness first, the footgun test

**Ruling:** A divergence from GPMC behavior is **acceptable when it prevents a
footgun** — specifically, when there is **no legitimate reason a real operator
would use the diverging feature.** Correctness comes before bug-for-bug parity.

**Ratified test for every divergence row:** a divergence is admissible iff the
GPMC behavior it refuses has **no legitimate operational use**. If a real
operator could have a genuine reason to use the feature, studio must not silently
diverge — it preserves and surfaces instead.

**Immediate application:**

- **cpassword** — GPMC does *not* reject the `cpassword` attribute; gpo-studio
  refuses it at every boundary (`intentional-deny`). Ratified as admissible:
  `cpassword` is a published, unfixable credential-exposure footgun (MS14-025)
  with no legitimate use. The matrix records the divergence explicitly.
- The evidence-pack schema already encodes this: `intentional-deny` /
  `preserve-only` / `not-present-on-target` are operator-policy classifications
  taken as-is (see `evidence.py` `_POLICY_CLASSIFICATIONS`). Each such row must
  carry a `notes` field naming the footgun and, where applicable, the MS
  advisory/CVE, so the divergence is auditable rather than opaque.

**Boundary:** divergences that merely change *format* or *ergonomics* without a
footgun rationale are **not** admissible under this rule — those must preserve
GPMC behavior. Preserve-don't-refuse remains the default for anything with a
legitimate use (e.g. unknown/OR ILT predicates are preserved read-only, not
dropped).

---

## Decision 4 — ADMX/vendor pack licensing: link-or-require, and generalize to arbitrary packs

**Ruling (delegated → agent design, ratified):** Do not redistribute
Microsoft-copyrighted or vendor ADMX content. Prefer **linking / requiring** the
pack over vendoring it, and **generalize the mechanism** so gpo-studio can import
and render *arbitrary* ADMX packs, not a hardcoded set.

**Ratified licensing rule (maps onto the existing `ContentClassification`):**

| Content | Classification | In repo? | How it is referenced |
|---|---|---|---|
| Author-authored synthetic fixtures | `in-repo` | yes | committed, `sha256` recorded |
| Microsoft / OS-shipped ADMX (e.g. `PolicyDefinitions`) | `hash-reference` | no | `sha256` + `source_build` + `regeneration_path` (the on-box path to reconstitute), never redistributed |
| Vendor ADMX with redistribution-forbidding license and impractical hashing | `excluded` | no | `license_note` records why; not stored, not hashed |

This is already enforced by `evidence.py` `_parse_content_item`
(`hash-reference` requires `sha256` + `source_build` + `regeneration_path`;
every item requires a `license_note`). The gate ratifies these three classes as
the complete licensing taxonomy — no fourth "vendored copyrighted content" class
exists.

**Product direction (feeds the roadmap, not this gate's contract):**

- **Production default — ingest/reconcile against SYSVOL, no manual step.** In a
  live deployment gpo-studio **reads the domain's ADMX central store from SYSVOL**
  (`\\<domain>\SYSVOL\<domain>\Policies\PolicyDefinitions`) and reconciles its
  catalogue against it, so the operator does not hand-import packs. This is a
  **read-only** ingestion — consistent with the charter's "the web process never
  writes to AD/SYSVOL"; it composes with the same read boundary gpo-lens uses.
- **Manual import — always offered, for pre-authoring.** The operator can still
  point studio at a `PolicyDefinitions`-shaped tree (`.admx` + language `.adml`)
  and import it explicitly. This path exists so **changes can be authored before
  the ADMX files are present** (e.g. drafting policy against a pack not yet on
  SYSVOL), and as the fallback when no central store is reachable.
- Under both paths studio **renders** the catalogue and **never redistributes**
  the bytes; ingested MS/vendor ADMX is `hash-reference` or `excluded` per the
  licensing rule above.

**Accepted tradeoff (operator ruling, 2026-07-19):** SYSVOL ingest is a
*read-only* operation but still a **trust-boundary expansion** — it needs domain
credentials and network reachability, and a poisoned central store is an
ingestion path the charter's write ban does not cover. The operator accepts this
as a small, bounded cost for a significantly simpler ingest story (no manual
step), with the maximal plan expected to expand these limits anyway. The
read-trust model (treat ingested ADMX/settings as untrusted input; no-retention
of transient copyrighted bytes) and **mechanical** license classification at
import (detect MS/vendor ADMX; refuse a mislabeled `in-repo`) are the required
mitigations, tracked as **WI-006** — not left to operator process.

Tracked as a Plan 022+ workstream (ADMX SYSVOL ingest + generalized manual
import); the licensing rule above is the invariant that workstream must satisfy.

---

## Decision 5 — Release-evidence enforcement: provenance signature, not a pinned hash manifest

**Ruling (delegated → agent judgment, ratified):** Adopt a release-eligibility
mechanism **friendlier than hash-scoped enforcement.** Do not gate release
evidence on a hand-curated manifest of allowed pack hashes.

**Ratified mechanism — signable + valid provenance signature:**

A pack is **release-eligible** iff:

1. it is `signable` — `redaction_verified AND licensing_complete` (already
   enforced; the identifier gate covers redaction, the licensing taxonomy above
   covers licensing); **and**
2. it carries a **valid detached provenance signature** over its
   `canonical_pack_hash`, produced by **cairn** (the suite's cryptographic
   provenance instrument) — the operator's ratified choice of signer. cairn
   already attests agent actions offline; signing the pack's canonical hash
   makes the evidence pack's provenance verifiable with the same infrastructure
   the 1.0 release and the suite already use.

The generator verifies *that a valid signature is present*, not *that a hash
appears in a curated allowlist*. This is friendlier (a pack is signed once, at
gather time, by whoever gathered it — no separate manifest to maintain and keep
in sync) and stronger (cryptographic, tamper-evident provenance rather than a
mutable list). It composes with the existing byte-deterministic
`canonical_pack_bytes` and the acb→cairn attestation path that already records
who ran the gather.

**Signature is eligibility, not verification — two orthogonal gates, both
required** (clarified after cross-lineage review, 2026-07-19). A cairn signature
attests *who gathered* a pack and that it is unmodified; it does **not** attest
that the pack's `verified-rw` rows passed the read/write loop. Verification is a
separate gate: the classification derivation still requires a passing
`windows-side` **and** a passing `endpoint` record per (capability, estate)
before any row reaches `verified-rw` (Decision 2's falsifiable loop). The
signature makes a pack *release-eligible*; the evidence content is what makes a
*claim* true. A signed pack full of unverified rows yields no `verified-rw`
claims. Trust-anchor custody and offline trust bootstrap for the cairn signer
(the verification key must not ship with the pack producer, or the gate becomes
self-attesting) are tracked as **WI-005**.

**Consequences:**

- The doc's "hash-pinned manifest" language and the never-implemented
  `--pinned-manifest` CLI argument are **withdrawn**. `reference-estates-and-evidence.md`
  is updated to describe signature-based eligibility instead.
- Implementation (signature verification in the generator + a cairn signing step
  in the gather path) is a **Plan 022+ contract-shaped workstream**, now unblocked
  by this ratification. It uses **cairn** (Decision 5), not a new scheme.
- **`schema_version: 0` legacy adapter — stays out of scope.** The 1.0
  `release-evidence-report.json` is a conceptual ancestor only; `load_pack`
  rejects `schema_version != 1` with a clear error, and no adapter will be built.
  Anyone with 1.0 evidence re-gathers under schema 1. Ratified as-is.

---

## Contract state after this gate

- Plans 022–031 are no longer provisional pre-inventory drafts; Plan 022
  (Administrative Templates + starter GPO parity) may begin implementation.
- The evidence-pack schema (`evidence.py`, `schema_version: 1`) is ratified as
  the contract. Bundle schema / signature-profile / canonical-vector work may now
  land on `main` in service of the decisions above.
- Cross-cutting invariant unchanged: **no AI in the truth path; every
  `verified-rw` claim is backed by independent verification** (Decision 2's
  read/write loop), and **synthetic-only fixtures** with identifier-gate-verified
  redaction.

## Open follow-ups created by this gate

1. **Plan 022** — Administrative Templates + starter GPO parity, sequenced first;
   promote the Registry CSE `verified-ro → verified-rw` using Tranche A.
2. **ADMX ingest generalization** (Decision 4) — import/render arbitrary
   operator-supplied ADMX packs under the `hash-reference` / `excluded` rule.
3. **Signature-based release eligibility** (Decision 5) — generator verifies a
   valid detached **cairn** signature over `canonical_pack_hash`; withdraw
   pinned-manifest language.
4. **WP-4 evidence gathering unblocked** — acb WI-010 (per-capability
   `env_prefix`) resolves the composed lab-credential checkout; verified live on
   the operator box 2026-07-19. Tranche A is the first gather target.
