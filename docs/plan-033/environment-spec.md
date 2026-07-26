# Plan 033 frozen environment specification

Status: environment frozen and confirmed on mvmcitest01 (2026-07-26). A WP-0
success-path run has been certified `pass` against a clean, committed source
tree (commit `7eb3c14`). The harness also produces parser-valid `fail`
manifests for deliberate failure paths.
Last updated: 2026-07-26
Validation host: mvmcitest01
Certified passing manifest hash (success-path run
`live-synthetic-registry-basic-20260725231015-9640`, source commit `7eb3c14`):
`91dd0232d207220d8092fddcb7096777f8bd828deca7c862372ff61da1ade990`

Note: the previously cited hashes
`265cfadc0c692c2cbaa6e69b0306c9c6813746f0caae40352f6ba10fe950d3d0` (predates the
comparison-to-artifact binding checks) and
`930d37fca9aa7a314c7d40aeb2bf3d984ac114e4581d0df43663a624db901d19` (inconclusive;
the source tree was dirty at run time) are both superseded by the certified
pass above.

Every Plan 033 run records the exact environment in the manifest's
`environment` object. This document pins the supported builds and tool
versions. A run on an unfrozen build is `inconclusive` unless the build is
added here first.

## Supported builds

| Role | OS | Build | Notes |
|------|----|-------|-------|
| DC / server | Windows Server 2025 Standard | 26100 | Primary validation target (mvmcitest01) |
| Client | Windows 11 Enterprise | 26100 | Endpoint processing oracle (not yet tested) |

Additional builds may be added after a successful qualification run. Each
addition requires its own evidence manifest and a review update to this
document.

## Tool versions (frozen from live dry run 2026-07-26)

| Tool | Version | Source |
|------|---------|--------|
| PowerShell | 5.1.26100.32860 (Desktop edition) | Built into Windows |
| GroupPolicy module | 1.0.0.0 | `Get-Module GroupPolicy` |
| GPMC | built-in (matched to OS build) | Server Manager feature |
| LGPO.exe | 3.0 (Microsoft Security Compliance Toolkit) | SHA-256 pinned below |

## Locale

All runs use `en-US`. A run on a different locale must record the locale and
may require additional normalization review for case-folding behavior.

## LGPO.exe hash

SHA-256: `0c97f29543418b30340c4ff5d930d31e6196dd59c2cc74b6b890fa7b90c910c7`
Path on validation host: `C:\gpo-tools\LGPO_30\LGPO.exe`

The SHA-256 of `LGPO.exe` is recorded in every manifest's
`environment.lgpo_sha256` field. The hash is computed on the exact binary
used during the run. Do not substitute a different LGPO build without
updating this document.

## Domain environment

| Property | Value |
|----------|-------|
| Forest/domain | ad.hraedon.com |
| NetBIOS | HRAENET |
| DCs | MVMDC01 (192.168.1.29), MVMDC02 (192.168.1.19), MVMDC03 (192.168.1.21) |
| DC OS | Windows Server 2025 Standard |

## Freeze rules

1. A frozen build cannot be removed without a plan amendment.
2. Tool versions are recorded per run; this document records the expected
   values for the primary target.
3. The `locale` field must match this document for a run to be considered
   on-target.
4. `dirty: true` in the manifest `source` object is acceptable for
   development runs but must be `false` for certification evidence. The
   manifest parser enforces this: a `pass` capability with a dirty source is
   rejected.
5. The parser enforces the full frozen environment for a `pass`: any deviation
   in `server_build`, `powershell_edition`, `powershell_version`,
   `group_policy_module_version`, `gpmc_version`, `locale`, or `lgpo_sha256`
   from the values in this document downgrades the run to `inconclusive`.
