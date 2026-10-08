# Object-security serialization lane

**Current qualification (2026-10-08):** `object-security-20261008082348-9729`,
20/20, on clean `1fb3f56ac7431e0044c69c32edc4350b2ab84151`
([evidence](wp3-evidence/plan034-rerun-20261008/object-security/verification.json)).
It is the successor to the [Plan 034 batch](plan034-batch.md)'s run, after
`8b1a5b4` changed the serializer. Since that batch the candidate also carries
three `[Group Membership]` rows built by `RestrictedGroupsFamily`, which
Windows re-exported exactly (WI-064). The results below are the lane's first
qualification and remain records of their original revision and scope.

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
first tranche.

**Surfaced 2026-09-11** at `POST /api/security-template/object-security`
(Plan 034 WP-3), in the emission direction and for these three families only.
WI-055 was the stated gate and it closed on 2026-09-07 as a ruling rather than
a fix: ACL content is deliberately unjudged, and the surface carries that as
`acl_content_is_not_judged` in every response instead of leaving a clean
`issues` list to be read as approval.

Two defects were found while scoping the surface, both filed rather than fixed
because this module is bound by the verdict above and correcting it costs a
re-run: the restricted-groups writer emits a bare SID where Windows emits a
star-SID (WI-064 — and that family is therefore not surfaced at all), and
`SystemServicesFamily.validate` reports "could not be parsed" for a descriptor
nothing tried to parse, which is why validating this lane's own candidate
yields three errors for an SDDL Windows accepted (WI-065). Neither was
reachable by the lane: the first has no rows in the candidate, and the second
is on a path the candidate builder never calls.

**Requalification batch, 2026-10-07** (branch `batch/wi064-wi065`, not yet
run). Both defects are fixed in code, and the lane is extended so that the
next run measures the fix. The candidate gains three `[Group Membership]` rows
built by `RestrictedGroupsFamily`. The guest imports and exports the
`group_mgmt` area. The finalizer requires starred keys and members and
compares Windows' re-export as principal sets. The services in the candidate
carry parsed descriptors, and the builder refuses any candidate the product
reports issues for. The verdict above no longer binds the shipping files on
that branch. WI-064 and WI-065 stay open until a run re-earns it, and until
then restricted groups stay off the surface.
