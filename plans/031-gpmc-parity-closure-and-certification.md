# Plan 031 — GPMC parity closure and certification

Status: proposed (post-1.0)
Scope: close matrix gaps, independently validate claims, and release the first
version carrying a qualified full-GPMC-parity claim
Depends on: Plans 021–030 and Plan 032
Review gate: **FINAL INDEPENDENT REVIEW REQUIRED before parity claim**

## WP-1 — Matrix closure

- Regenerate the Plan 021 matrix from current supported Windows versions,
  installed in-box extensions, Microsoft documentation, and signed evidence.
- Resolve every row to a verified or explicitly non-RW state; no unowned gaps.
- Confirm target-version removal/deprecation and third-party preservation.
- Publish deliberate safety divergences, especially credential material and
  operations whose safe alternative is not semantically identical.

## WP-2 — Cross-feature estates

- Test realistic mixed-CSE, multi-domain/site/OU, Starter GPO, WMI, loopback,
  migration, delegation, Modeling/Results, and concurrent-admin scenarios.
- Run backup/import/copy/restore, edit, publish, replication, endpoint apply,
  removal, drift, and recovery across supported version combinations.
- Measure semantic diff false negatives/positives and opaque-content retention.

## WP-3 — Independent assurance

- Commission Windows/GPMC interoperability, publisher security, parser/fuzz,
  accessibility, privacy, least privilege, and disaster-recovery reviews.
- Red-team control-plane compromise, replay, confused deputy, malicious backup/
  ADMX/artifact, native GPMC race, privilege escalation, and partial failure.
- Resolve all silent-loss, unsafe-write, inaccurate-scope, and recovery findings.

## WP-4 — Product and operator completion

- Align UI, API, CLI, reports, runbooks, compatibility scanner, documentation,
  training, alerts, SLOs, support policy, upgrade/rollback, and evidence portal.
- Make capability state and target version visible at selection, edit, review,
  export, publish, and audit surfaces.
- Publish a reproducible reference-lab/evidence pack where licensing permits.

## Qualified parity definition

The release may claim “full GPMC parity for the published compatibility matrix”
only when it can discover, author or losslessly preserve, validate, diff, report,
backup/import/copy/restore, scope, model/observe, and—where marked RW—safely
publish every supported in-box matrix row on named target versions.

It must not claim universal parity for unknown third-party CSEs, removed legacy
features, unsafe credential storage, or unverified target versions.

## FINAL INDEPENDENT REVIEW REQUIRED

No parity claim, broad publisher enablement, or final release occurs until the
matrix, evidence, residual-risk register, intentional divergences, and all
independent review reports are approved. Any material finding reopens the
affected adapter plan and may require refining downstream gates.

## Acceptance gates

- No matrix row is implied by marketing rather than generated evidence.
- Cross-feature estates pass GPMC, endpoint, concurrency, and recovery tests.
- Unknown content survives every eligible no-edit lifecycle byte-for-byte.
- Independent reviews contain no unresolved critical/high findings.
- Operators complete restore, partial-failure, and credential-compromise drills.
