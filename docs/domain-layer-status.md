# The post-1.0 domain layers are unproven drafts

Status: operator ruling, 2026-07-29. This is the canonical statement. The
`Status:` lines of Plans 025–032 and the post-1.0 section of
`capability-matrix.md` link here instead of restating it.

## The ruling

Plans 025–032 landed domain layers: roughly 13k lines under `src/` that no
delivery surface reaches. Treat them as **unproven drafts that evidence lanes
will revise**, not as finished assets waiting to be wired up.

Two rules apply to these layers:

- **Reach.** A landed domain layer is not a capability, because no operator can
  get to it. This rule predates the ruling.
- **Correctness.** There is no reason to believe a layer's wire behaviour is
  right. This is what the ruling adds. When it was made, both layers that had
  been examined against real Windows tooling had turned out to be wrong.

Layers leave the set only by the route in
[How a layer stops being a draft](#how-a-layer-stops-being-a-draft). The
capability matrix records which have done so.

## The evidence

Every time an external oracle has read this code, the code has disagreed with
Windows.

- **WP-3, `security_template.py`.** The module had landed with passing tests and
  was called "implemented". The first time `secedit` read its output, the file
  was not valid MS-GPSB on the wire: wrong encoding, missing the required
  preamble, wrong line endings. The fix was small (+36 −1), but until it landed
  nothing the module emitted was a security template as far as Windows was
  concerned. Its tests passed throughout, because they only checked that Studio
  could read what Studio wrote.
- **WP-1B, the GPP writers.** The writer-conformance lane produced **+547 −58
  lines of correction across four modules** (`gpp_adapters`, `ilt`, `gpp`,
  `policy_config`), on top of a 509-line conformance harness. These four modules
  are surfaced code that shipped in 1.0. If evidence rewrote that much of the
  code operators already use, the unsurfaced, untested layers are not in better
  shape.
- **The remediation corpus.** Thirteen provenance-graded scenarios record
  expected Windows behaviour for this remediation program. At the time of the
  ruling, nine were blocked on platform qualification, and the corpus
  contradicted two committed documents on its first day.

### How strong the evidence is

**The direct evidence for the unsurfaced set is one case.** At the time of the
ruling, `security_template.py` was the only unsurfaced domain layer an external
oracle had read, and it was wrong. Everything beyond that is inference.

The WP-1B result is an analogy, and it runs in the ruling's favour. Those four
modules had a delivery surface, real operators, a 1.0 release and far more
scrutiny than any unsurfaced layer, and evidence still rewrote +547 lines of
them. Code that has had less attention is not more likely to be correct.

So the ruling does not say these layers have been proven wrong. It says:
nothing here has been shown right, one layer has been shown wrong, and the one
comparable body of code that was examined needed substantial correction. That
is enough to stop counting them as progress. It is not enough to say what is
broken in any layer no oracle has read, which is why each layer needs its own
evidence lane rather than an audit.

## What to do

1. **Don't count a landed domain layer as progress toward the product**, in
   roadmaps, release notes or status summaries. Post-1.0 work has added far more
   to `src/` than to anything an operator can use, and the roadmap must not
   credit that as advancement.
2. **Expect an evidence lane to rewrite what it touches.** The serialization in
   these modules is a hypothesis about Windows. Budget lanes on that basis:
   writing the code is cheap, and the evidence is the constraint.
3. **Keep the structure, not the behaviour.** The type models, boundaries and
   test scaffolding are worth keeping. Treat the wire format, attribute names,
   units and omission rules as unverified until an oracle confirms them. Those
   are what have been wrong each time.
4. **Don't audit them cold.** Reading a domain layer against the specification
   and declaring it correct is the internally consistent round-trip trap that
   Plan 033 exists to reject, one level up. Only native tooling settles these
   questions.

## What the ruling does not mean

- **No deletion.** Nothing is being removed. These layers are the starting point
  their evidence lanes will revise.
- **No moratorium.** Writing a domain layer ahead of its evidence is a
  legitimate way to work in this project. The error is counting it as done.
- **No change to any capability claim.** None of these modules appeared in the
  1.0 capability matrix, so nothing shipped changes.

## How a layer stops being a draft

Both steps, in this order:

1. an evidence lane certifies its behaviour against native Windows tooling on a
   qualified platform and produces an evidence manifest; then
2. it is wired to a delivery surface an operator can reach.

Only then does it enter the capability matrix proper. Until then, the matrix's
post-1.0 section tracks it, outside the 1.0 contract.
