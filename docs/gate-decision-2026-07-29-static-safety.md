# Static safety gate — scope change: decision record (2026-07-29)

Status: **ratified** (operator ruling recorded).

This record covers the 2026-07-27 change to `scripts/check_safety.py`, made in
commit `1edfca9` while fixing the first CI exposure of post-1.0 work. The change
was flagged for operator ratification at the time because the gate enforces the
load-bearing charter claim that **the web process never writes to AD or
SYSVOL** — the single safety property the whole offline-first architecture rests
on. It was left running on agent judgment alone, recorded only in a commit
message, until this record.

The operator ratified the change on 2026-07-29.

---

## What changed

The gate's docstring has always said it verifies that **the web process**
contains no shell execution, AD/SMB/SYSVOL dependencies, unsafe XML parsing, or
publication code. It did not enforce that. It scanned every file under
`src/gpo_studio/` and treated the whole package as the web process.

The two diverged the moment Plan 033 landed `oracle_evidence.py`, which shells
out to `git` (`rev-parse`, `status`, `show`) to bind harness inputs to a source
commit for evidence-pack integrity. That module is driven exclusively by
`scripts/windows-oracle/`; it is reachable from no request path. Under the old
gate it was nonetheless a violation, and CI failed.

The change makes the gate compute the web process for real:

- `WEB_PROCESS_ENTRYPOINT = "api"` — the FastAPI delivery layer;
- `_web_process_modules()` walks in-package imports transitively from that
  entrypoint and returns the reachable set;
- `CATEGORY_EXEMPTIONS` maps a forbidden-import category to the modules excused
  from it — currently `{"Shell execution": {"oracle_evidence"}}`;
- `_check_exempt_unreachable()` **fails the build if any exempt module is
  reachable from the entrypoint**.

## Why this is stronger, not weaker

The obvious reading — "an exemption list was added to a safety gate" — is the
wrong one, and it is worth stating precisely why.

An exemption here is not a permanent waiver. It is a claim about topology: *this
module does not run in the web process*. `_check_exempt_unreachable()` makes the
claim self-enforcing. The instant anyone imports `oracle_evidence` into
`api.py`, or into anything `api.py` reaches, the exemption stops applying and
the build fails with an explicit message. The exemption cannot silently widen
into a charter breach; it can only collapse loudly.

Against the old gate this is a net gain in coverage:

| | old gate | new gate |
|---|---|---|
| Forbidden import in a web-process module | caught | caught |
| Forbidden import in tooling | caught (false positive) | exempt, by explicit topology claim |
| **Exempt tooling imported into the web process** | **not modelled** | **fails closed** |
| Web process boundary | asserted in a docstring | computed from the import graph |

The old gate could not have caught the third row at all, because it had no
notion of the web process to violate.

The residual risk is dynamic import — `importlib` or a deferred local import
would evade a static reachability walk. That is unchanged from the old gate,
which was equally static, and no such import exists in `src/`. It is recorded
here as a known limit rather than papered over.

## What ratification added

Reviewing for this record surfaced that the fail-closed behaviour had **no
committed test**. It was demonstrated ad hoc when the change was written and the
proof was never landed, so the property that makes the change safe was itself
unprotected — precisely the "self-consistency is not evidence" failure mode
AGENTS.md warns about, one layer up.

`tests/test_safety_gate.py` now pins it (6 tests):

- the committed tree passes its own gate;
- every exempt module is currently unreachable — the premise behind the
  exemption, checked against the real import graph rather than assumed;
- **an exempt module imported into `api.py` fails the gate** (the negative test);
- reachability is transitive — an exempt module pulled in via an intermediate
  module fails too;
- an exemption is scoped to its category — `oracle_evidence` may shell out to
  `git`, but may **not** import an AD/SMB client;
- shell execution is otherwise refused, which is the baseline the exemption
  carves out of.

The two negative tests were verified non-vacuous: with `_check_exempt_unreachable`
neutered to simulate the pre-change behaviour, both fail; with it restored, both
pass.

## Ruling

**Ratified as committed, with the regression tests above added.** The gate keeps
the reachability-scoped design. `CATEGORY_EXEMPTIONS` stays a deliberately
narrow map with a written justification per entry.

Standing conditions on future exemptions:

1. Every new exemption records *why* the module is outside the web process, in
   the comment block above the map — not just that it is.
2. An exemption is never the fix for a module that genuinely belongs in the web
   process. If a request path needs the behaviour, the answer is the publication
   adapter boundary, not an entry here.
3. `test_exempt_module_is_currently_unreachable` must never be relaxed. If it
   starts failing, the charter has been breached and the import is the bug.

## Open follow-ups

1. **Dynamic-import blind spot** — static reachability cannot see `importlib` or
   deferred local imports. Not currently exploitable (none exist in `src/`).
   Revisit if the codebase adopts dynamic loading in the delivery layer.
2. **Entrypoint is single** — `WEB_PROCESS_ENTRYPOINT` assumes `api.py` is the
   only delivery surface. If a second process is ever served (Plan 032's hosted
   control plane is the likely candidate), the gate needs a set of entrypoints
   and this record needs revisiting before that lands.
