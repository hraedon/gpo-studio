# The post-1.0 domain layers are unproven drafts

Status: operator ruling, 2026-07-29. This is the canonical statement; the
`Status:` lines of Plans 025–032 and the post-1.0 section of
`capability-matrix.md` point here rather than restating it.

## The ruling

The domain layers landed by Plans 025–032 — roughly 13k lines under `src/` that
no delivery surface reaches — are **unproven drafts to be revised by evidence
lanes, not assets awaiting wiring.**

This is a stronger claim than the one already recorded elsewhere. The existing
rule says a landed domain layer *is not a capability*, which is an argument
about **reach**: no operator can get to it. The ruling here is an argument about
**correctness**: there is no reason to believe the wire behaviour is right, and
two of the two layers examined against real Windows tooling turned out to be
wrong.

## Why: what happened when evidence arrived

Not a theoretical concern. Every time an external oracle has read this code, it
has found the code disagreeing with Windows.

**WP-3, `security_template.py`.** The module had landed with tests and was
"implemented". The first time `secedit` was asked to read its output, the file
was not valid MS-GPSB on the wire at all: wrong encoding, missing the required
preamble, wrong line endings. The correction was small in lines (+36 −1) and
total in meaning — before it, nothing the module emitted was a security template
as far as Windows was concerned. Its own tests had passed throughout, because
they only ever asked whether Studio could read what Studio wrote.

**WP-1B, the GPP writers.** The writer-conformance lane produced **+547 −58
lines of correction across four modules** (`gpp_adapters`, `ilt`, `gpp`,
`policy_config`), on top of a 509-line conformance harness. Those four modules
are not unsurfaced drafts — they are **surfaced code that shipped in 1.0**. If
evidence rewrites that much of the code operators already use, the untested,
unsurfaced layers are not in better shape.

**The remediation corpus.** Thirteen provenance-graded scenarios now record
expected Windows behaviour for this remediation program. **Nine are blocked**
on platform qualification, and the corpus already contradicts two committed
documents on day one. The gap between what is written down and what Windows does
is measured, not speculative.

### How strong this evidence actually is

Stated precisely, because a ruling *about* unproven claims should not overstate
its own. **The direct evidence for the unsurfaced set is a single case.**
`security_template.py` is the only unsurfaced domain layer an external oracle
has ever read, and it was wrong. Everything beyond that is inference.

The WP-1B result is inference by analogy, and the analogy runs in the
favourable direction: those four modules had a delivery surface, real
operators, a 1.0 release behind them, and far more scrutiny than any unsurfaced
layer has received — and evidence still rewrote +547 lines of them. Code that
has had *less* attention is not the code more likely to be correct.

So the honest form of the ruling is not "these layers have been proven wrong."
It is: **nothing here has been shown right; one has been shown wrong; and the
one comparable body of code that did get examined needed substantial
correction.** That is more than enough to stop counting them as progress. It is
not enough to say what specifically is broken in any layer no oracle has read —
which is precisely why each needs its own evidence lane rather than an audit.

## What this means operationally

1. **A landed domain layer is not progress toward the product.** Do not count
   it in roadmaps, release notes, or status summaries as if it were. Post-1.0
   work has added far more to `src/` than to anything an operator can use, and
   the roadmap must stop crediting that as advancement.
2. **Expect the evidence lane to rewrite what it touches.** The serialization
   in these modules is a hypothesis about Windows. Budget lanes on that basis:
   the writing is the cheap part, the evidence is the constraint.
3. **The salvage value is structure, not behaviour.** The type models, the
   boundaries, and the test scaffolding are worth keeping. The wire format,
   attribute names, units, and omission rules are exactly what has been wrong
   each time, and should be treated as unverified until an oracle says
   otherwise.
4. **Do not audit them cold.** Reading a domain layer against the specification
   and pronouncing it correct is the internally-consistent-round-trip trap that
   Plan 033 exists to reject, one level up. The only thing that settles these
   questions is native tooling.

## What this does not mean

- **Not a deletion order.** Nothing is being removed. These layers are the
  starting point their evidence lanes will revise.
- **Not a moratorium.** Writing a domain layer ahead of its evidence is a
  legitimate way to work in this project. The error is not writing them; it is
  *counting* them as done.
- **Not a reversal of any capability claim.** None of these modules ever
  appeared in the 1.0 capability matrix. Nothing shipped changes.

## How a layer stops being a draft

Both of these, and in this order:

1. an evidence lane certifies its behaviour against native Windows tooling on a
   qualified platform, producing an evidence manifest; and
2. it is wired to a delivery surface an operator can reach.

Only then does it enter the capability matrix proper. The matrix's post-1.0
section tracks the interim state and is explicitly outside the 1.0 contract.
