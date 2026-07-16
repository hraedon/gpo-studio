# Plan 017 — Windows and GPMC interoperability program

Status: implemented and accepted (2026-07-16). The compatibility corpus,
normalization/import conformance, generated-plan validator, adversarial review,
and CI evidence are complete. Per-capability Windows-lab claims remain pending
until their sanitized release-candidate evidence is attached to the capability
matrix; this evidence gate is operational release work, not open Plan 017 code.
Scope: replace format plausibility with reproducible evidence from real Windows
and Group Policy tooling
Depends on: Plans 015 and 016

## Purpose

The Python round-trip suite is strong evidence that GPO Studio can read what it
writes. It is not proof that GPMC, the GroupPolicy PowerShell module, or Windows
CSEs accept the generated artifacts. A robust 1.0 needs an external oracle.

All lab identifiers and captured fixtures must remain synthetic and pass the
repository identifier gate.

## WP-1 — Compatibility corpus and expected semantics

- Build minimal synthetic GPOs in supported Windows/GPMC versions for every
  registry type, delete operation, side status, link shape, security-filter
  type, WMI shape, GPP Groups action, GPP Registry action, and ILT predicate.
- Record tool/OS build, locale, creation steps, and expected semantic model.
- Keep redistributable sanitized fixtures in the repository; store artifacts
  that cannot be redistributed in an access-controlled lab fixture store with
  hashes and generation scripts.
- Include negative fixtures: malformed PReg, unknown CSEs, cpassword, complex
  unsupported ILT, migration tables, and partial/corrupt backups.
- Define a versioned normalization layer for timestamps, generated GUIDs,
  ordering, and domain-specific identifiers before comparisons.

## WP-2 — Import conformance

- Import corpus backups through both core functions and the public API.
- Compare the normalized Studio model to expected semantics field by field.
- Re-export and verify supported content; unsupported content must remain
  visibly preserved/read-only or cause an explicit export block.
- Exercise non-English ADML, Unicode names/data, empty/default values, and
  Windows path/case behavior.
- Test migrations across workspace schema and bundle schema versions.

## WP-3 — Export conformance in a disposable domain

- Provision a disposable, isolated Windows domain lab with least-privileged
  test credentials and no route to production environments.
- Restore Studio-generated GPMC backups with supported Microsoft tooling.
- Use `Get-GPOReport`, direct backup, and controlled client policy application
  to compare restored semantics with the source model.
- Verify Registry.pol on both sides and Groups/Registry preference processing.
- Verify security filtering, link order/status, side enablement, and WMI filter
  behavior separately; do not count comments in a plan as publication.
- Always create unique disposable GPOs and clean them up in `finally` paths.

## WP-4 — PowerShell plan verification

- Parse generated plans before execution and reject unexpected command or AST
  shapes in tests.
- Execute plans twice in the disposable lab and prove idempotency.
- Test create-new and existing-GUID/name collision behavior.
- Prove stale security filters are removed only when intended and that default
  principals are not removed accidentally.
- Confirm DWORD/QWORD/Binary/MultiString values and quote/comment injection
  cases with actual GroupPolicy cmdlets.
- Either implement real WMI and GPP application in the plan or state clearly
  that the plan is partial and direct users to the verified GPMC artifact.

## WP-5 — Automated evidence and triage

- Run platform-independent conformance in normal CI.
- Run disposable-domain tests on a manual/nightly self-hosted Windows runner;
  never expose its credentials to forked pull requests.
- Upload sanitized reports containing artifact hashes, normalized diffs, OS
  version, and cleanup result.
- Quarantine flaky infrastructure separately from product failures and require
  a passing lab run for a release candidate.
- Document fixture refresh and incident procedures.

## Acceptance gates

- Every claimed 1.0 capability has at least one GPMC-origin import fixture and
  one Studio-origin artifact accepted by supported Windows tooling.
- Import/export semantic comparisons are clean after documented normalization.
- The PowerShell plan is idempotent for every command it claims to apply.
- A Windows lab release report is attached to the 1.0 release candidate.
- Unknown/unsupported CSE and ILT content is never silently lost.
- Lab credentials, identifiers, and artifacts cannot enter normal logs or git.

## Implementation reflection — 2026-07-16

Plan 017 replaced informal format confidence with a versioned, synthetic
compatibility corpus and explicit normalization rules. The corpus covers all
registry types, deletes, side state, link and security shapes, WMI, GPP Groups
and Registry actions on both scopes, all supported ILT predicates, Unicode,
unknown content, malformed inputs, `cpassword`, migration tables, and partial
or corrupt backups. Core and public-API import paths compare normalized
semantics and exercise deterministic re-export.

Generated PowerShell plans now pass through a closed allowlist validator that
checks required structure, assignment ordering, command shapes, pipes,
semicolons, backticks, dangerous aliases, and case-insensitive cmdlet spelling.
Three adversarial review rounds fixed real bypasses involving multiline quoted
strings, case-insensitive PowerShell names and aliases, and user-scope GPP
coverage. The committed identifier gate prevents lab identifiers and artifacts
from entering normal history.

The completed implementation landed in `6f6f675`, `eeb6d83`, `9189470`, and
`40d2e16`; cleanup followed in `e350745`. Current `main` CI passes on Python
3.13 and 3.14 together with the identifier gate.

Completion of this plan does not silently promote individual capability-matrix
rows to Windows-verified. Native GPMC/CSE reports and hashes stay outside git
until sanitized, and each row remains `pending` until its release-candidate lab
evidence is attached. This keeps the external oracle falsifiable while
separating the finished conformance implementation from release operations.
