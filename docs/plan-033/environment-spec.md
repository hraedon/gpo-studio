# Plan 033 frozen environment specification

This document pins the Windows builds and tool versions that Plan 033 evidence
runs are qualified on. Every run records its exact environment in the
manifest's `environment` object. A run on a build that is not frozen here is
`inconclusive` unless the build is added here first.

The single source of truth for the profile is `FROZEN_ENVIRONMENT` in
`src/gpo_studio/oracle_evidence.py` (rule 7). Keep this document in sync with
it; `tests/test_oracle_evidence.py` checks that every frozen value appears here.

Status: environment frozen; requalified 2026-09-08 by the full WI-059 batch.
The validation hosts are the isolated member server, domain controller and
client, reached over PowerShell Direct, with the build-family requirements
below.

## Current qualification

The [WI-062 batch](wi062-batch.md) is current: 21 of 22 runs on frozen harness
`f5cad577` (manifest-form bound source; schema version 2). The computer
group-deny lane is pending estate repair; the batch note records the clock/DNS
failure that blocks it.

The WI-059 batch and its WI-060 successors remain the binding history for
their commits. The two successor runs (Scripts and publication) bind
`b5ccbabd19b7ed661312915ca4b314dc27bbdd6e`. The other 19 verdicts and WP-0 keep
their WI-059 source binding, `4cfa9af4b3f12104e8c592cd94df00b88e49beb5`.

**WP-0.** The current run is `live-synthetic-registry-basic-20260908003108-1493`
at `4cfa9af4b3f12104e8c592cd94df00b88e49beb5`. Canonical manifest hash:
`184477fdedb21cba4e55c7f4d3c1f606c37478551ec2ce74937d8a6f61dc98a9`. Its
[complete integrity pack](wp0-evidence/wi059-20260908/wp0/manifest.json)
includes the recipe, guest scripts, controller, transport, finalizer and shared
enforcement library. Every artifact and command stream rehashes intact, and
cleanup passed.

The historical success manifest remains at `wp0-evidence/manifest-estate.json`.
The failure-path capture `live-synthetic-registry-basic-20260803183850-2692`
remains historical evidence of a parser-valid failure. It was not re-executed
as a separate live qualification in this batch.

## Qualified environments

A lane may certify only in an environment qualified here. Each certification is
bound to the environment recorded in its own manifest, so pointing a lane at a
new target needs its own qualification run before it can carry evidence. The
manifest records `transport`, so a reviewer can tell which environment
produced a verdict.

| Lane | Environment | Transport | Qualified | Certifying run (tagged `evidence/<run-id>`) |
|---|---|---|---|---|
| wp0 | estate, domain-joined member server (role 3) | `psdirect` | 2026-09-08 | `live-synthetic-registry-basic-20260908003108-1493` (`pass`) |
| wp1b | estate, domain-joined member server (role 3) | `psdirect` | 2026-09-08 | `wp1b-writer-20260908003141-5853` (`pass`) |
| wp2 | estate, domain-joined member server (role 3) | `psdirect` | 2026-09-08 | `wp2-native-import-20260908003212-8693` (`pass`) |
| wp3-member | estate, domain-joined member server (role 3) | `psdirect` | 2026-09-08 | `wp3-security-template-20260908003235-1230` (`pass`) |
| wp3-dc | estate, domain controller (role 5) | `psdirect` | 2026-09-08 | `wp3-security-template-20260908003251-3920` (`pass`) |
| object-security | estate, domain-joined member server (role 3) | `psdirect` | 2026-09-08 | `object-security-20260908003317-7120` (`pass`) |
| scripts-metadata | estate, domain-joined member server (role 3) | `psdirect` | 2026-09-08 | `scripts-r10-20260908013518-2476` (`pass`) |
| publication | estate, domain-joined member server (role 3) | `psdirect` | 2026-09-08 | `publication-completeness-20260908013539-2644` (`pass`) |
| endpoint | estate, member server + client (26200) | `psdirect` | 2026-09-08 | `endpoint-observe-20260908003432-9991` (`pass`) |
| lsdou-precedence | estate, member server + client (26200) | `psdirect` | 2026-09-08 | `rsop-observe-20260908003647-5124` (`pass`) |
| disabled-block-enforced | estate, member server + client (26200) | `psdirect` | 2026-09-08 | `rsop-observe-20260908003802-3428` (`pass`) |
| wmi-filtering | estate, member server + client (26200) | `psdirect` | 2026-09-08 | `rsop-observe-20260908003917-3943` (`pass`) |
| wmi-filtering-error | estate, member server + client (26200) | `psdirect` | 2026-09-08 | `rsop-observe-20260908004032-4871` (`pass`) |
| computer-security-filtering | estate, member server + client (26200) | `psdirect` | 2026-09-08 | `rsop-observe-20260908004147-8239` (`pass`) |
| computer-security-filtering-group-deny | estate, member server + client (26200) | `psdirect` | 2026-09-08 | `rsop-observe-20260908004329-7397` (`pass`) |
| computer-security-filtering-deny-read | estate, member server + client (26200) | `psdirect` | 2026-09-08 | `rsop-observe-20260908004451-9557` (`pass`) |
| loopback-merge | estate, member server + client (26200) | `psdirect` | 2026-09-08 | `rsop-user-observe-20260908004610-7393` (`pass`) |
| loopback-replace | estate, member server + client (26200) | `psdirect` | 2026-09-08 | `rsop-user-observe-20260908004751-4785` (`pass`) |
| user-side-disabled | estate, member server + client (26200) | `psdirect` | 2026-09-08 | `rsop-user-observe-20260908004932-4297` (`pass`) |
| user-security-filtering | estate, member server + client (26200) | `psdirect` | 2026-09-08 | `rsop-user-observe-20260908005147-1436` (`pass`) |
| user-security-filtering-deny | estate, member server + client (26200) | `psdirect` | 2026-09-08 | `rsop-user-observe-20260908005409-2134` (`pass`) |
| user-security-filtering-read-deny | estate, member server + client (26200) | `psdirect` | 2026-09-08 | `rsop-user-observe-20260908005556-1857` (`pass`) |

Scripts and publication bind the WI-060 revision
(`b5ccbabd19b7ed661312915ca4b314dc27bbdd6e`); every other row binds
`4cfa9af4b3f12104e8c592cd94df00b88e49beb5`. The shared byte guard and finalizer
inputs are part of the recorded evidence.

Each lane needed its own qualifying run rather than inheriting WP-1B's. The
estate is one environment, but a lane is qualified by evidence that that lane
behaves there. The lanes also differed: WP-0's integrity pack binds a different
file set per transport, and WP-2 and WP-3 did not check their environment at
all before the estate round.

Earlier qualification records are listed under [History](#history), with their
original scope and commit.

## Supported builds

| Role | OS | Build | Notes |
|------|----|-------|-------|
| DC / server | Windows Server 2025 Standard | 26100 family | Primary validation target |
| Client | Windows 11 Enterprise (25H2) | 26200 family | Endpoint processing oracle; **requalified 2026-09-08** by `endpoint-observe-20260908003432-9991` |

Builds are qualified by **family**, not by exact servicing revision. A run on
`26100.4652` and a run on `26100.5011` are both on-target for the 26100 family.
Every manifest's `environment` object records the exact revision, so a
suspected servicing regression stays diagnosable.

A new family can be added after a successful qualification run, with its own
evidence manifest and a review update to this document. A new family is a
re-freeze: record it here first.

**Client constraints** (measured 2026-08-03, see
[History](#client-re-freeze-and-first-qualification)):

- **The `GroupPolicy` module is absent**, so `Get-GPResultantSetOfPolicy` is not
  available. RSAT is a Feature-on-Demand whose source is on the internet, and
  the estate has no egress, as the isolation invariant requires. Client-side
  RSOP capture is `gpresult.exe` only.
- PowerShell Direct does not provide an interactive logon. Without one,
  `gpresult /x` without `/scope:computer` exits **0**, writes **no file**, and
  reports that the invoking account has no RSoP data. WP-9 established and
  verified interactive sessions; the current batch requalifies all six user
  scenarios through explicit `/scope:user /user` capture. WP-6 remains computer
  scope only.

## Tool versions (frozen from live dry run 2026-07-26)

| Tool | Qualified on | Source |
|------|--------------|--------|
| PowerShell | 5.1.26100 family, Desktop edition | Built into Windows |
| GroupPolicy module | 1.0.0.0 (exact) | `Get-Module GroupPolicy` — **server only**, absent on the client |
| GPMC | built-in (matched to OS build) | Server Manager feature |
| secedit | rides the server OS build family (26100) | Built into Windows; 21/21 on member `wp3-security-template-20260908003235-1230` and DC `wp3-security-template-20260908003251-3920` |
| gpresult.exe | rides the client OS build family (26200) | Built into Windows; requalified with the client 2026-09-08 |
| LGPO.exe | **recorded, not qualified** — see below | Microsoft Security Compliance Toolkit |

`secedit` and `gpresult.exe` have no independent version pin. They ship with
the OS and are qualified by the build family of the host they run on. The
table records this explicitly so an unpinned inbox tool is not mistaken for one
nobody checked.

## Locale

All runs use `en-US`. A run on a different locale must record the locale and
may need additional normalization review for case-folding behavior.

## LGPO.exe hash — recorded, not qualified

SHA-256: `0c97f29543418b30340c4ff5d930d31e6196dd59c2cc74b6b890fa7b90c910c7`
Path on validation host: `C:\gpo-tools\LGPO_30\LGPO.exe`

Every manifest records the SHA-256 of `LGPO.exe` in
`environment.lgpo_sha256` as provenance.

**It does not gate a `pass` (changed 2026-07-29).** No lane in
`scripts/windows-oracle/` executes `LGPO.exe`. `run-evidence.ps1` and
`run-wp3-security-template.ps1` only call a file-hashing helper on the path.
Gating on it could downgrade a run to `inconclusive` over a binary that did not
influence any of the evidence. If a lane is ever written that invokes LGPO,
restore the qualification check in `frozen_environment_violations()` at the
same time.

**Ruling 2026-08-03: LGPO.exe is approved on the evidence estate.** The estate
has no egress and cannot fetch the binary, so WP-5 pushes it in over
`psdirect` rather than narrowing WP-5 to its domain-GPO leg. The guests are
disposable and checkpoint-backed. The isolation invariant governs egress, not
what is deliberately placed inside. The path recorded above
(`C:\gpo-tools\LGPO_30\`) is the retired shared host's; WP-5 records the estate
path when it stages the binary. Three conditions bind that work:

1. Verify the pushed binary against a **pinned** SHA-256 on the guest. The
   transfer is the trust boundary. Hashing whatever arrived and recording it is
   provenance, not verification.
2. Stage it **after checkpoint restore**, so no golden checkpoint carries it and
   the estate stays reproducible from clean media.
3. Restore the qualification check only when a lane executes the binary. The
   ruling makes LGPO permissible, not qualified; qualification is earned by
   execution.

## Domain environment

The disposable estate is the domain environment (since 2026-08-03). Every lane
is qualified on it with its own run.

| Property | Value |
|----------|-------|
| Forest/domain | ad.labdomain.dev |
| NetBIOS | LAB |
| Guests | LabDC01 (domain controller), LabMS01 (member server), LabCL01 (client) |
| DC OS | Windows Server 2025 Standard, 26100 family |
| Client OS | Windows 11 Enterprise 25H2, 26200 family |
| Networking | none — the guests have no network at all; the transport reaches them through the hypervisor |

- Lanes that need GPMC run on **LabMS01** and reach the DC for AD and SYSVOL.
- **LabCL01** is the endpoint oracle.
- The guests have no network; PowerShell Direct reaches them through the
  hypervisor. This makes the estate cheap to isolate, and it is why `ssh`
  cannot be used there.
- The guests are checkpoint-backed and disposable. That is what makes
  destructive operations (`secedit /configure`, policy application, staging
  LGPO.exe) permissible here and not on the retired shared host.

**PowerShell Direct needs no scheduled task.** The old launcher existed only to
obtain a delegable logon token, which an SSH network logon cannot provide.
PowerShell Direct carries the credential to the guest through the hypervisor,
and the resulting logon authenticates outward. This was measured directly
(`New-GPO`, `Backup-GPO`, SYSVOL enumeration, `Remove-GPO`) and then exercised
by a full seven-candidate lane that imports, reports, re-exports and removes a
GPO per candidate. It also removes the `schtasks /RP` password argument, which
a privileged observer on the host could decode.

### The retired shared host

Until 2026-08-03, validation ran against the live `ad.hraedon.com` forest from
`mvmcitest01`, a host shared with another project. That is why WP-3 forbade
`secedit /configure` there and why the endpoint lane had never run. Those
constraints belonged to that host, not to Plan 033.

| Property | Value |
|----------|-------|
| Forest/domain | ad.hraedon.com |
| NetBIOS | HRAENET |
| DCs | MVMDC01 (192.168.1.29), MVMDC02 (192.168.1.19), MVMDC03 (192.168.1.21) |

**The SSH transport is retired.** Keeping it meant keeping the scheduled-task
launcher, which took the credential as a `schtasks /RP` argument: transient,
but decodable by a privileged observer on the host for as long as the task
existed. Removing it is a security improvement.

Certifications produced on that host are **not retracted**: each is bound to
the environment recorded in its own manifest. But no new run can be produced
there without restoring the transport and re-qualifying. An evidence pack from
that host can no longer be re-verified in this tree, because its harness-input
record binds a launcher the tree no longer contains. `build_harness_inputs`
reports this explicitly instead of defaulting, so an old pack reports an
anachronism rather than tampering.

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
   `client_build` in its own finalizer.** The parser accepts the `not-tested`
   sentinel because it cannot tell which lane produced a manifest. It is the
   lane's job not to claim endpoint evidence it did not gather.
7. The single source of truth for the profile is `FROZEN_ENVIRONMENT` in
   `src/gpo_studio/oracle_evidence.py`. This document mirrors it; keep the two
   in sync.

## History

These dated notes record earlier qualifications. Each remains valid for the
commit it names; the [Qualified environments](#qualified-environments) table
records the current bindings.

### 2026-09-07 extensions on the existing frozen profile

- **Publication completeness.** Run
  `publication-completeness-20260907193221-2759` passed 21/21 on LabMS01
  (role 3) at `362699c` on a clean tree. It measures a publication *plan*
  against the state Windows reaches after importing the same content. It
  executes no publication. See
  [the results](publication-completeness-results.md).
- **Scripts metadata re-certification.** Run
  `scripts-r10-20260907182809-4583` passed 21/21 on LabMS01 (role 3) at
  `f8a2bbd` on a clean tree. It re-earned the binding after `export.py` gained
  `extension_registration` (WI-057). The lane and its assertions did not
  change; a bound source file did, and
  `test_a_live_verdict_still_binds_the_harness_that_ships` forces a re-run in
  that case. The superseded run `scripts-r10-20260907081826-5183` (21/21 at
  `f6b06af`) remains valid for the commit it names.
- **Scripts metadata extension.** Run `scripts-r10-20260907081826-5183` passed
  21/21 on LabMS01 (role 3) from a clean isolated checkout at `f6b06af`. The
  unlinked target GPO was removed and its absence rechecked. This qualifies
  metadata import/report/rebackup only; no script payload was delivered or
  executed. See [the bound result](scripts-metadata-results.md).
- **Object security.** Run `object-security-20260907075319-7408` passed 19/19
  on LabMS01 (role 3) under the same build-family profile. The separate
  `object-security-secedit` lane qualifies registry/file/service serialization
  through a temporary database, with no `/configure`. See
  [the bound result](object-security-results.md).
- **WP-3 policy families.** A directly executed DC qualification,
  `wp3-security-template-20260907071106-1024`, and a fresh member-server
  qualification, `wp3-security-template-20260907071149-3752`. Both pass 21/21
  under the 26100/PowerShell 5.1 build-family profile, with observed roles 5
  and 3 respectively. The DC run adds Kerberos. No lane invokes `/configure`.
  The platform registry records this distinction. See
  [the results and raw evidence](wp3-policy-family-results.md).

### Superseded rounds

The dual-transport round (`wp1b-writer-20260803014047-4766`,
`live-synthetic-registry-basic-20260803183723-2067`,
`wp2-native-import-20260803182557-5095`,
`wp3-security-template-20260803182956-1132`) and the SSH-removal round at
`1f71fab` produced identical results: the same checks, and the same 7/7 for
WP-1B. That shows removing the SSH branches did not change what the psdirect
path does. They are superseded only so that a certification binds the code that
ships.

The round at `97bdaf9` was superseded by stronger checks. WP-2 went from 17
checks to 18, WP-3 from 19 to 20, and WP-1B's verdict gained an environment gate
it did not have. A pass under the weaker checks is a claim about less, not a
weaker claim about the same thing.

**Why WP-1B qualified the estate.** Its seven candidates had already passed on
the historic host, so re-running them changed one variable: same inputs, same
expected results, new environment. The estate matched the frozen profile on
every gated field without amendment (server build family 26100, PowerShell
5.1.26100, GroupPolicy module 1.0.0.0, en-US). The run qualified the estate
without redefining qualification.

### The superseded mvmcitest01 WP-0 certification

Until 2026-08-03 the WP-0 line cited the success-path run
`live-synthetic-registry-basic-20260726070916` at commit `000f1b5`, manifest
hash `0751b39667c982784af7f0a221fe193a1fa7ba5d84f601c8c71147aacdfabee9`. That
commit is a squash-merge orphan and no longer resolves (see
`docs/evidence-binding-audit-2026-08-03.md`). Its integrity pack could not be
re-verified, because the committed tree it compared the deployed harness
against was unreachable. It is superseded rather than repaired: the current run
re-earns the certification on a commit that resolves and a tag that preserves
it.

These earlier WP-0 hashes are also superseded by the certified pass:

- `265cfadc0c692c2cbaa6e69b0306c9c6813746f0caae40352f6ba10fe950d3d0`: predates
  the comparison-to-artifact binding checks.
- `930d37fca9aa7a314c7d40aeb2bf3d984ac114e4581d0df43663a624db901d19`:
  inconclusive; dirty source.
- `91dd0232d207220d8092fddcb7096777f8bd828deca7c862372ff61da1ade990`: a real
  pass, but predates the integrity pack.
- `6d4b91b229a08e99a9851b5cb894f8f587bea577937b81dbec46754c1f3e1f47`: has the
  integrity pack, but predates the strict cleanup probe, launcher binding,
  recorded-commit enforcement and verifier hardening.

### Client re-freeze and first qualification

**Re-freeze, 2026-07-29.** The client row previously read
`Windows 11 Enterprise 26100` (24H2). No evidence had ever been produced
against a client, so no certification depended on it. The available lab media
is Windows 11 25H2 (build 26200 family), so the client was re-frozen to 26200
before the WP-6 endpoint lane produced its first evidence. Nothing was
invalidated, because the endpoint lane had never run.

**First qualification, 2026-08-03.** Run `endpoint-observe-20260803142424-3050`
applied real policy to the estate client guest and observed CSE evidence. It
passed with no lane problems and no control problems, on a real 26200 build.
Its measurements established the client constraints listed under
[Supported builds](#supported-builds). At that time the estate had no
interactive logon at all; WP-9 added one later.
