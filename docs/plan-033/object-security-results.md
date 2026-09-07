# Object-security serialization lane

Plan 034 WP-1 extends the R4/R9 observations into a repeatable, non-applying
`secedit` lane. A clean member-server run passed all 19 checks on 2026-09-07
(`object-security-20260907075319-7408`, source
`1b81fed31460915443f944d90cb2b88eaefb2a99`). The retained
[verification artifact](wp3-evidence/object-security/verification.json) is
the authoritative verdict and raw-artifact index.

## Experiment and acceptance

The candidate is emitted by the real `object_security` families and the
security-template formatter. It contains three Registry Keys rows, three File
Security rows, and three Service General Setting rows. Registry/file rows
exercise propagation codes 0 (propagate), 1 (do not allow replacement), and 2
(replace); services exercise startup codes 2 (automatic), 3 (manual), and 4
(disabled). All targets are synthetic and each section uses an explicit SDDL
control.

The guest validates the candidate, imports it into a fresh temporary database,
exports the same areas, and removes the database in a `finally` block. The
controller retains the candidate, expected rows, native export, command streams,
environment, operation record, and exact harness/source hashes. It must reject
missing or extra rows, duplicate targets/ordinals, mismatched codes or SDDL,
unexpected operations, asymmetric import/export areas, and failed cleanup.

Before the run, the prediction is that Windows accepts the native quoted CSV
rows, preserves their codes and already-canonical descriptors, and exports
ordinal-keyed rows with case-normalized targets. Target case and CSV whitespace
are comparison rules; SDDL changes are findings, not automatically normalized
away. A failed run is retained and its cause explained before recertification.
The passing raw pack includes the candidate, expected rows, exported template,
command streams and logs, environment, and source hashes.

## Re-running and recovering evidence

From a clean checkout with the qualified lab credentials configured, rerun with
`bash scripts/windows-oracle/run-object-security-oracle.sh`. The runner requires
`GPO_STUDIO_LAB_HOST`, `GPO_STUDIO_LAB_GUEST`, `HYPERV_CONTROL_USERNAME`, and
`GUEST_BOOTSTRAP_USERNAME`; it stages the candidate through `psdirect`, runs
`secedit /validate`, `/import`, and `/export`, then removes the temporary
database. The recorded run rechecked cleanup with zero residual database files,
and both candidate and Windows export comparisons reported no differing rows.

The evidence is tagged
`evidence/object-security-20260907075319-7408`. The verification record binds
source hashes to commit `1b81fed31460915443f944d90cb2b88eaefb2a99`; when a raw
pack omits a source file, recover the exact bound bytes with
`git show 1b81fed31460915443f944d90cb2b88eaefb2a99:<repository-path>` and verify
the resulting SHA-256 against `verification.json`.

## Scope

This proves template serialization and temporary-database round trips. It does
not prove applied permissions, inheritance on an endpoint, GPMC editability,
or that an ACL is suitable for deployment. The lane never runs `secedit
/configure`. No GPO is created or linked and no registry/file/service ACL is
changed.

Empty versus absent service descriptors, environment-variable file paths,
noncanonical SDDL, and broader descriptor combinations remain outside this
first tranche. WI-055 remains a gate before an object-security delivery surface:
the current validators do not judge permissive ACL contents. The module remains
unsurfaced even after a successful format verdict.
