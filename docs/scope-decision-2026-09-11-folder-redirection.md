# Scope decision, 2026-09-11 — Folder Redirection

Status: **decided.** Plan 034 WP-4 asked whether Folder Redirection is a write
target, a read target, or out of scope.

**The ruling: read target. The writer is deferred behind R12, and deferred is
not declined.**

This records a decision, not an argument for one. The argument is
[`scope-brief-2026-09-11-folder-redirection.md`](scope-brief-2026-09-11-folder-redirection.md),
which assembled everything a ruling needed and stopped there, as
`software_install`'s brief did. The brief's own recommendation was "build it,
read first"; that is what was ruled.

---

## What was decided

1. **`fdeploy1.ini` is read.** `src/gpo_studio/fdeploy.py` decodes the native
   UTF-16LE/BOM/CRLF bytes strictly, parses sections and entries losslessly,
   validates the structure, renders a review report, and diffs two documents by
   `(folder GUID, principal)`. It is reachable at
   `POST /api/folder-redirection/fdeploy`.
2. **Nothing writes it.** The codec's encode half exists to prove the reader
   lossless against R3's bytes and is called by nothing else;
   `tests/test_folder_redirection_scope.py::test_the_writer_half_is_still_deferred_behind_r12`
   fails if that changes.
3. **`Flags` is carried, not decoded.** No bit is named anywhere in the module,
   the report, or the endpoint's response. WI-066 owes the capture (R12) that
   would make the word readable.
4. **`folder_redirection.py` is untouched.** Its 537 lines — the typed model,
   the path validation, `assess_redirection_migration` — keep doing what they
   did. The ruling says the module was never a Folder Redirection writer; it
   does not say the code in it is wrong, and
   [`domain-layer-status.md`](domain-layer-status.md) is explicit that none of
   WP-4's options was a deletion order.

## Why read, when the demand figure is zero

R6's census found the Folder Redirection CSE in 0 of 26 production GPOs — the
same figure, from the same census, that helped rule Software Installation out
on [2026-09-06](scope-decision-2026-09-06-software-installation-and-certification.md).
Ruling the other way on the same number needs a reason, and the brief gives it:
of the four arguments that made the Software Installation ruling easy, two carry
over and two do not.

- **Carry over.** Zero measured demand in the one estate this project can see;
  a module modelling something no Windows tool emits.
- **Do not.** Software Installation's capture was the most expensive unrun one
  in the survey — this one was already taken, in September, and cost nothing to
  use. `.aas` has no documented format and no oracle; `fdeploy1.ini` is a
  458-byte INI and the oracle is `Backup-GPO`.

So the two costs that made "no" cheap are both absent, and the demand figure
alone does not carry enough weight to refuse a family this tractable. That is
the brief's argument and it is adopted here rather than restated further.

The honest weight of the 0-of-26 stands unchanged: one estate, 26 GPOs, not
evidence about the industry. A reader who weights it above tractability would
rule (a), and the brief says so. That disagreement is not one more measurement
will settle — only a second estate's census would move it.

## Why write is deferred, and what the deferral is waiting for

Not cost. WI-066 is the reason, and it was found by asking why the brief's
first draft deferred writing at all: the brief's stated reason was that reading
was already paid for and writing needed a session, which is true and is the
weaker argument. **The real constraint is that the capture cannot support a
writer.**

R3 was designed to author two folders — Documents in Basic with three options
set away from default, and Pictures in Advanced with two groups and options left
at default, the second existing expressly so a default encoding could be told
from a non-default one. Step 4 was never authored. What is banked is one folder,
one principal, one `Flags=1021`: `0b1111111101`, nine bits set across at least
ten positions, against the four booleans the module models.

That attributes no bit to any option. `object_security.py`'s propagation codes
were wrong on all three values until R4 measured them, and a `Flags` writer
built on one observation is the same guess with more bits.

**R12 closes it**: step 4 re-requested, plus a third folder differing from the
default one by exactly one option — two folders bound the flag word, three
identify a bit outright. It is a GPMC console session on `LabMS01`, roughly a
person-hour, the same class of work R3 was. It needs no lane and no harness
change, so it does not queue behind the batch that owes WI-063, WI-064 and
WI-065. The writer's *certification* can ride that batch once the writer exists.

## What this ruling does not claim

- **No lane has read this artifact.** `fdeploy.py` is bound by nothing, which
  means no Windows verdict measures it — a statement about coverage, in the
  sense [`bound-source-cost.md`](plan-033/bound-source-cost.md) insists on. The
  banked R3 capture is doing the job a lane would otherwise do: the reader is
  tested against bytes hash-bound to what GPMC wrote, not against a fixture
  this repository invented. That is stronger than a round-trip test and weaker
  than a lane.
- **One capture is one shape.** Multi-folder and multi-principal documents are
  unmeasured. The parser handles them because the file format's own section
  naming implies them, not because anything watched Windows write one.
- **The endpoint is not an import path.** The GPO model does not carry a parsed
  redirection, so an imported backup still shows `fdeploy1.ini` as a hash in
  the unmodeled-file inventory. Wiring it into `GPO`, `policy_report` and
  `diff_gpos` edits `model.py`, which two live verdicts bind — WI-068, filed
  against the batch that owes the others.
- **Nothing here is a statement about the endpoint.** R3 measured what the GPO
  carries. What the client-side extension does with it is unmeasured, and WP-9's
  machinery could measure it cheaply since it is already built.

## What would change this ruling

- **A second estate's census** moving the demand figure. It is the weakest
  input and the only one a future measurement can move.
- **R12 showing the flag word is not a bit field** — an enumeration, say — which
  would make the writer cheaper than assumed rather than more expensive.
- **`fdeploy.ini`'s marker turning out to be load-bearing.** Twenty bytes with
  no content; whether Windows requires it alongside `fdeploy1.ini` is
  unmeasured, and a writer that omitted it would be exactly the defect this
  project keeps finding by measuring rather than reasoning.
