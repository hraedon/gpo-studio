# Plan 033 frozen environment specification

Status: environment frozen and confirmed on mvmcitest01 (2026-07-26). A WP-0
success-path run has been certified `pass` against a clean, committed source
tree (commit `000f1b5`). The harness also produces parser-valid `fail`
manifests for deliberate failure paths.
Last updated: 2026-08-03 (disposable evidence estate qualified over PowerShell
Direct; WP-1B re-pointed)
Validation host: mvmcitest01 (historic, SSH); the disposable evidence estate
(PowerShell Direct) as of 2026-08-03 — see **Qualified environments** below
Certified passing manifest hash (success-path run
`live-synthetic-registry-basic-20260726070916`, source commit `000f1b5`):
`0751b39667c982784af7f0a221fe193a1fa7ba5d84f601c8c71147aacdfabee9`

This run carries the full integrity pack: the deployed harness scripts
(`run-evidence.ps1`, `common.psm1`, `remote-run.ps1`), the recipe, and the
control-plane orchestrator are hashed input artifacts bound to the recorded
commit; every artifact and command stream rehashes intact; and the cleanup
re-query is a strict `Get-GPO -All` probe (absent / present / query-error) with
both streams recorded as command/artifact evidence.

Note: the previously cited hashes
`265cfadc0c692c2cbaa6e69b0306c9c6813746f0caae40352f6ba10fe950d3d0` (predates the
comparison-to-artifact binding checks),
`930d37fca9aa7a314c7d40aeb2bf3d984ac114e4581d0df43663a624db901d19` (inconclusive;
dirty source),
`91dd0232d207220d8092fddcb7096777f8bd828deca7c862372ff61da1ade990` (genuine pass
but predates the integrity pack), and
`6d4b91b229a08e99a9851b5cb894f8f587bea577937b81dbec46754c1f3e1f47` (integrity
pack but predates the strict cleanup probe, launcher binding, recorded-commit
enforcement, and verifier hardening) are all superseded by the certified pass
above.

Every Plan 033 run records the exact environment in the manifest's
`environment` object. This document pins the supported builds and tool
versions. A run on an unfrozen build is `inconclusive` unless the build is
added here first.

## Qualified environments

A lane may only certify in an environment qualified here. Re-pointing a lane is
not a variable change: every certification is bound to the environment recorded
in its own manifest, so a new target needs its own qualification run before it
can carry evidence. The manifest records `transport`, so a reviewer can tell
which of these produced a given verdict.

| Environment | Transport | Qualified | Qualifying run |
|---|---|---|---|
| `mvmcitest01` (historic shared host) | `ssh` + scheduled-task launcher | 2026-07-26 | `live-synthetic-registry-basic-20260726070916` (WP-0) |
| Disposable evidence estate, domain-joined member server | `psdirect` | 2026-08-03 | `wp1b-writer-20260803014047-4766` (WP-1B, 7/7 pass, tag `evidence/wp1b-writer-20260803014047-4766`) |

**Why WP-1B qualified the estate.** Its seven candidates already passed on the
historic host, so re-running them changes exactly one variable — same inputs,
same expected results, new environment. The estate matched the frozen profile on
every gated field without amendment (server build family 26100, PowerShell
5.1.26100, GroupPolicy module 1.0.0.0, en-US), so the run qualifies the estate
rather than redefining qualification. Nothing below changed to accommodate it.

**The estate lane runs no scheduled task.** The launcher exists only to obtain a
delegable logon token, which an SSH network logon cannot provide. PowerShell
Direct carries the credential to the guest through the hypervisor and the
resulting logon authenticates outward — measured directly (`New-GPO`,
`Backup-GPO`, SYSVOL enumeration, `Remove-GPO`), and then exercised by a full
seven-candidate lane that imports, reports, re-exports and removes a GPO per
candidate. This also removes the `schtasks /RP` password argument the lane
scripts flag as decodable by a privileged observer, so the estate path is
strictly safer than the one it replaces, not merely different.

The estate's guests have **no network** at all; the transport reaches them
through the hypervisor. That is what makes the estate cheap to isolate, and it
is why `ssh` cannot be used there.

## Supported builds

| Role | OS | Build | Notes |
|------|----|-------|-------|
| DC / server | Windows Server 2025 Standard | 26100 family | Primary validation target |
| Client | Windows 11 Enterprise (25H2) | 26200 family | Endpoint processing oracle (not yet tested) |

Builds are qualified by **family**, not by exact servicing revision. A run on
`26100.4652` and a run on `26100.5011` are both on-target for the 26100 family;
the exact revision is recorded in every manifest's `environment` object, so a
suspected servicing regression stays diagnosable after the fact.

Additional *families* may be added after a successful qualification run. Each
addition requires its own evidence manifest and a review update to this
document. A new family is a re-freeze, not a widening: record it here first.

**Client re-freeze (2026-07-29).** The client row previously read
`Windows 11 Enterprise 26100` (24H2). It was never tested — no evidence was ever
produced against a client — so no certification depended on it. Available lab
media is Windows 11 25H2 (build 26200 family), so the client is re-frozen to
26200 before the WP-6 endpoint lane produces its first evidence. Nothing is
invalidated by this change, precisely because the endpoint lane had never run.

## Tool versions (frozen from live dry run 2026-07-26)

| Tool | Qualified on | Source |
|------|--------------|--------|
| PowerShell | 5.1.26100 family, Desktop edition | Built into Windows |
| GroupPolicy module | 1.0.0.0 (exact) | `Get-Module GroupPolicy` |
| GPMC | built-in (matched to OS build) | Server Manager feature |
| LGPO.exe | **recorded, not qualified** — see below | Microsoft Security Compliance Toolkit |

## Locale

All runs use `en-US`. A run on a different locale must record the locale and
may require additional normalization review for case-folding behavior.

## LGPO.exe hash — recorded, not qualified

SHA-256: `0c97f29543418b30340c4ff5d930d31e6196dd59c2cc74b6b890fa7b90c910c7`
Path on validation host: `C:\gpo-tools\LGPO_30\LGPO.exe`

The SHA-256 of `LGPO.exe` is recorded in every manifest's
`environment.lgpo_sha256` field as provenance.

**It does not gate a `pass` (changed 2026-07-29).** No lane in
`scripts/windows-oracle/` ever executes `LGPO.exe` — it is only hashed
(`run-evidence.ps1` and `run-wp3-security-template.ps1` both call a
file-hashing helper on the path and nothing else). Qualifying on it meant a run
could be downgraded to `inconclusive` over a binary that had not influenced a
single byte of the evidence it was gating. If a lane is ever written that
genuinely invokes LGPO, restore the qualification check in
`frozen_environment_violations()` at the same time.

## Domain environment

> **Superseded when the disposable lab estate lands.** The values below describe
> validation against the live `ad.hraedon.com` forest from the shared host
> `mvmcitest01`. That host is shared with another project, which is why WP-3
> forbids `secedit /configure` and why the endpoint lane has never run. The
> replacement is a disposable three-VM estate (`ad.labdomain.dev`) on the
> dedicated Hyper-V host; this section and the validation-host line at the top
> of this document must be re-frozen against it, with a fresh qualification run,
> before any lane is re-pointed. Do not re-point a certified lane without that
> re-freeze — every existing certification is bound to the environment recorded
> in its own manifest.

| Property | Value |
|----------|-------|
| Forest/domain | ad.hraedon.com |
| NetBIOS | HRAENET |
| DCs | MVMDC01 (192.168.1.29), MVMDC02 (192.168.1.19), MVMDC03 (192.168.1.21) |
| DC OS | Windows Server 2025 Standard |

## Freeze rules

1. A frozen build family cannot be removed without a plan amendment.
2. Tool versions are recorded per run; this document records the qualified
   values for the primary target.
3. The `locale` field must match this document for a run to be considered
   on-target.
4. `dirty: true` in the manifest `source` object is acceptable for
   development runs but must be `false` for certification evidence. The
   manifest parser enforces this: a `pass` capability with a dirty source is
   rejected.
5. The parser enforces the qualification profile for a `pass`. Deviation in
   any of the following downgrades the run to `inconclusive`:
   - `server_build` — **build family**, parsed from the trailing build number;
   - `client_build` — **build family**, or the literal `not-tested` sentinel
     for a lane that never touches a client;
   - `powershell_version` — **version family** (prefix match, so a servicing
     revision is on-target but PowerShell 7 is not);
   - `powershell_edition`, `group_policy_module_version`, `gpmc_version`,
     `locale` — exact match.

   `lgpo_sha256` is recorded but not qualified (see above).

6. **A lane that applies policy to a client must assert a real
   `client_build` in its own finalizer.** The `not-tested` sentinel is
   on-target at the parser level because the parser cannot tell which lane
   produced a manifest; it is the lane's job not to claim endpoint evidence it
   did not gather.
7. The single source of truth for the profile is `FROZEN_ENVIRONMENT` in
   `src/gpo_studio/oracle_evidence.py`. This document mirrors it; keep the two
   in sync.
