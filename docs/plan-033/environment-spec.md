# Plan 033 frozen environment specification

Status: frozen — WP-0 live dry run validated 2026-07-26
Last updated: 2026-07-26
Validation host: mvmcitest01
Canonical manifest hash: 265cfadc0c692c2cbaa6e69b0306c9c6813746f0caae40352f6ba10fe950d3d0

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
   development runs but must be `false` for certification evidence.
