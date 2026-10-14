"""Plan 036 WP-0: the live-closure tooling (WI-083, a 1.1.0 final blocker).

Four parts, as `plans/036-change-cost.md` ("The 1.1.0 final blocker: WP-0")
defines them; this package is parts 1 to 3 and the release-only closure gate's
machinery. Part 4 (requalifying the lanes D-9 selects on the estate) is an
operator step at the freeze and is not code.

- `closure`   the static import closure of each lane's builder and finalizer;
- `scan`      the positive classification of every resolved call target, on
              mypy's semantic analysis, with the completeness assertion;
- `tracer`    the runtime line tracer (standard library only; launched as a
              script, never imported by the lanes);
- `changed`   D-9's changed lines, the release-metadata exemption and the
              Boolean lane selection with its convergence rule;
- `replay`    the two-clone builder replay and the re-grade over banked packs;
- `projection` the canonical claim projection with relational provenance;
- `record`    the successor record and the pin, and the gate's re-derivation.

Nothing here is imported by any lane's builder, finalizer or runner, and no
lane's dependency set contains any file of this package: adding it costs no
lane (the plan's "Order", step 1). The package lives beside the harness it
measures because the plan places WP-0's tooling under `scripts/plan-033/`.
"""
