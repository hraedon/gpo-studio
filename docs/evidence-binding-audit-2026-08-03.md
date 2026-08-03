# Evidence-binding audit — 2026-08-03

Every commit SHA cited in `docs/` and `plans/` was resolved against the
repository. Four citations do not resolve. All four are squash-merge orphans
predating the issue #22 remedy, and none of them can be retro-tagged, because
the commits were already unreachable when that remedy ran.

## Method

Extracted every hex token adjacent to the word "commit" from `docs/**/*.md` and
`plans/**/*.md`, then tested each with `git cat-file -t`. Two apparent hits were
false positives: a run-id timestamp (`20260726070916`) and an all-zero
placeholder in `release-evidence.md`.

## Findings

| Citation | Where | What it binds |
|---|---|---|
| `c8b4fa8ed37a86ee…` | `plans/033-…md` (WP-2) | Certified clean-tree native-backup run `wp2-native-import-20260726235913-9111` |
| `5e0a6df` | `plans/033-…md` (WP-2) | Superseded dirty-tree run `wp2-native-import-20260726212733-5804` |
| `000f1b5` | `docs/plan-033/environment-spec.md` | WP-0 success-path certification `live-synthetic-registry-basic-20260726070916` |
| `1edfca9` | `docs/gate-decision-2026-07-29-static-safety.md` | The `check_safety.py` scope change the operator ratified |

## What this does and does not mean

It does **not** mean the runs did not happen or that the results were
misreported. The manifests, hashes, and outcomes are recorded as written.

It means those results are **no longer independently checkable from the
repository**. Every one of these runs asserts some form of "the harness that
executed matched the committed source tree" — and the committed tree each one
compared against is unreachable, so the assertion cannot be re-derived. That is
precisely the property the integrity pack exists to provide.

The asymmetry worth noting: WP-1B and WP-3 survived this because they commit
evidence manifests under `docs/plan-033/wp1b-evidence/` and `wp3-evidence/` and
carry `evidence/*` tags. WP-0 and WP-2 committed neither. **A tag or a committed
manifest is what makes a certification durable; a SHA in prose is not.**

`1edfca9` is a different case — it binds a ratification record to the change it
ratifies, not a run to a tree. The change itself is on `main` and is covered by
`tests/test_safety_gate.py`, so the safety property is protected by a test
rather than by the citation. The dangling SHA costs traceability, not assurance.

## Disposition

- **WP-2** — queued for re-certification on the lab estate, alongside the WP-1B
  re-point. Will produce a committed manifest and an `evidence/` tag.
- **WP-0** — `environment-spec.md` is being re-frozen for the estate anyway;
  the re-freeze supersedes this binding rather than repairing it.
- **`1edfca9`** — annotated in place. No re-run is meaningful.

## Preventing recurrence

`finalize_oracle_run.py` already auto-tags passing runs (issue #22). The gap it
does not close is a lane that certifies without committing a manifest at all,
which is how WP-0 and WP-2 became unverifiable. Any lane claiming certification
should commit its manifest under `docs/plan-033/<wp>-evidence/`, the way WP-1B
and WP-3 do.
