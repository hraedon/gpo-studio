# Plan 033 frozen environment specification

Status: environment frozen. Every lane is qualified on the disposable evidence
estate over PowerShell Direct (2026-08-03). A WP-0 success-path run is certified
`pass` against a clean, committed source tree, and the same harness produces a
parser-valid `fail` manifest for a deliberate failure path.
Last updated: 2026-08-03 (WP-0, WP-2 and WP-3 qualified on the estate; the SSH
transport retired)
Validation host: the disposable evidence estate, domain-joined member server,
PowerShell Direct — see **Qualified environments** below
Certified passing manifest hash (success-path run
`live-synthetic-registry-basic-20260803213433-5325`, source commit `97bdaf9`,
tag `evidence/live-synthetic-registry-basic-20260803213433-5325`):
`76c79ba93152b59203383b1443b24b159d412bca5dd83775c33a3b8d891d4b3a`

The corresponding fail-path run
(`live-synthetic-registry-basic-20260803183850-2692`) parses as `fail`, canonical
hash `68c9dfdf24fc955b19f4e8c57e6b8a61ee28c4d3fe3fb2fc30cf29552c628ebe`. It
carries a real failed command and its real stderr, so the parser is demonstrated
to tell a failed run from a missing one on this transport too.

This run carries the full integrity pack: the deployed harness scripts
(`run-evidence.ps1`, `common.psm1`), the recipe, the control-plane orchestrator
and the transport (`psdirect.ps1`) are hashed input artifacts bound to the
recorded commit; every artifact and command stream rehashes intact; and the
cleanup re-query is a strict `Get-GPO -All` probe (absent / present /
query-error) with both streams recorded as command/artifact evidence. The
verdict is committed at `docs/plan-033/wp0-evidence/manifest-estate.json`.

> **The superseded mvmcitest01 certification.** Until 2026-08-03 this line cited
> the success-path run `live-synthetic-registry-basic-20260726070916` at commit
> `000f1b5`, manifest hash
> `0751b39667c982784af7f0a221fe193a1fa7ba5d84f601c8c71147aacdfabee9`. That commit
> is a squash-merge orphan and no longer resolves (see
> `docs/evidence-binding-audit-2026-08-03.md`), so its integrity pack could not be
> re-verified: the committed tree it compared the deployed harness against was
> unreachable. It is superseded rather than repaired — the run above re-earns the
> certification on a commit that resolves and a tag that preserves it.

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

| Lane | Environment | Transport | Qualified | Certifying run (tagged `evidence/<run-id>`) |
|---|---|---|---|---|
| WP-0 | estate, domain-joined member server | `psdirect` | 2026-08-03 | `live-synthetic-registry-basic-20260803213433-5325` (`pass`) |
| WP-1B | estate, domain-joined member server | `psdirect` | 2026-08-03 | `wp1b-writer-20260803213602-6066` (7/7) |
| WP-2 | estate, domain-joined member server | `psdirect` | 2026-08-03 | `wp2-native-import-20260803230132-8090` (18/18) |
| WP-3 | estate, domain-joined member server | `psdirect` | 2026-08-03 | `wp3-security-template-20260803230220-2450` (20/20) |
| endpoint | estate, client guest (26200) | `psdirect` | 2026-08-03 | `endpoint-observe-20260803142424-3050` (`pass`, real client build) |
| WP-6B | estate, member server + client guest (26200) | `psdirect` | 2026-08-04 | `rsop-observe-20260804010341-7165` (`pass`; also `rsop-observe-20260804010551-9363` and `rsop-observe-20260804010738-5543`, identical) |
| any | `mvmcitest01` (historic shared host) | `ssh` + launcher | 2026-07-26 | **retired 2026-08-03** — `live-synthetic-registry-basic-20260726070916` (commit orphaned, see above) |

Each estate row cites a run made against the lane scripts as they ship. WP-2 and
WP-3 cite `db775b0`, which moved the candidate hashes out of `source.files`;
WP-0 and WP-1B still cite `97bdaf9` because nothing they bind changed after it,
and re-certifying a lane whose inputs are identical adds no information. The
rows were produced after an adversarial review round changed what several of these
checks mean. WP-1B's verdict now gates on the environment at all; WP-2's and
WP-3's are graded against the candidate this controller built rather than the
copy the guest returned, and each carries a `candidate_delivered_intact` check
proving the guest ran against that candidate byte for byte. Every run also owns
a private tree on the guest, so no run can select another's evidence.

Earlier rounds on this branch remain valid for the commits they name, and are
superseded here for two different reasons worth keeping distinct. The
dual-transport round (`wp1b-writer-20260803014047-4766`,
`live-synthetic-registry-basic-20260803183723-2067`,
`wp2-native-import-20260803182557-5095`,
`wp3-security-template-20260803182956-1132`) and the SSH-removal round at
`1f71fab` produced **identical results** — same checks, same 7/7 for WP-1B —
which is the evidence that removing the SSH branches changed nothing about what
the psdirect path does. Those are superseded only so that a certification binds
the code that ships.

The round at `97bdaf9` is different: it is superseded-by-strengthening. WP-2
went from 17 checks to 18 and WP-3 from 19 to 20, and WP-1B's verdict gained an
environment gate it never had. A pass under the weaker checks is not a weaker
claim about the same thing — it is a claim about less.

**The SSH transport is retired.** Every lane is qualified on the estate, so the
historic host is no longer the only way to run anything — and keeping it meant
keeping the scheduled-task launcher, which existed solely to obtain a logon
token an SSH network logon cannot provide. That launcher took the credential as
a `schtasks /RP` argument: transient, but decodable by a privileged observer on
the host for as long as the task existed. Removing it is a security improvement,
not tidying.

Certifications produced on that host are **not retracted** — retiring a
transport does not retract a certification, which is the point of binding
verdicts to commits. But no new run can be produced there without restoring the
transport and re-qualifying, and an evidence pack from it can no longer be
re-verified in this tree: its harness-input record binds a launcher the tree no
longer contains. `build_harness_inputs` says so explicitly rather than
defaulting, so an old pack reports an anachronism instead of tampering.

Each lane needed its own qualifying run rather than inheriting WP-1B's. The
estate is one environment, but a lane is qualified by evidence that *that lane*
behaves there, and the ports were not uniform: WP-0's integrity pack binds a
different file set per transport, and WP-2 and WP-3 were not checking their
environment at all before this round.

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
| Client | Windows 11 Enterprise (25H2) | 26200 family | Endpoint processing oracle; **qualified 2026-08-03** by `endpoint-observe-20260803142424-3050` |

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

**Client qualification (2026-08-03).** The client is no longer untested. Run
`endpoint-observe-20260803142424-3050` applied real policy to the estate client
guest and observed CSE evidence, passing with no lane problems and no control
problems, on a genuine 26200 build. Two limits were measured at the same time,
and they constrain every lane built on this host rather than being defects to
fix:

- **The `GroupPolicy` module is absent**, so `Get-GPResultantSetOfPolicy` is not
  available. RSAT is a Feature-on-Demand whose source is on the internet, which
  an estate with no egress cannot reach. That is the isolation invariant
  working. Client-side RSOP capture is `gpresult.exe` only.
- **The estate has never had an interactive logon**, and PowerShell Direct does
  not provide one. `gpresult /x` without `/scope:computer` therefore exits **0**,
  writes **no file**, and reports that the invoking account has no RSoP data.
  User-scope resolution is WP-9 work; WP-6 is computer scope only.

## Tool versions (frozen from live dry run 2026-07-26)

| Tool | Qualified on | Source |
|------|--------------|--------|
| PowerShell | 5.1.26100 family, Desktop edition | Built into Windows |
| GroupPolicy module | 1.0.0.0 (exact) | `Get-Module GroupPolicy` — **server only**, absent on the client |
| GPMC | built-in (matched to OS build) | Server Manager feature |
| secedit | rides the server OS build family (26100) | Built into Windows; exercised 20/20 by `wp3-security-template-20260803230220-2450` |
| gpresult.exe | rides the client OS build family (26200) | Built into Windows; qualified with the client 2026-08-03 |
| LGPO.exe | **recorded, not qualified** — see below | Microsoft Security Compliance Toolkit |

`secedit` and `gpresult.exe` carry no independent version pin: they ship with
the OS and are qualified by the build family of the host they run on. Recording
that explicitly is the point — an inbox tool with no pin looks identical to an
inbox tool nobody thought about.

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

**Ruling 2026-08-03: LGPO.exe is approved on the evidence estate.** The estate
has no egress and cannot fetch the binary, so WP-5 pushes it in over `psdirect`
rather than being narrowed to its domain-GPO leg. The guests are disposable and
checkpoint-backed, and the isolation invariant governs *egress*, not what is
deliberately placed inside. The path recorded above (`C:\gpo-tools\LGPO_30\`)
is the retired shared host's; WP-5 records the estate path when it stages the
binary. Three conditions bind that work:

1. Verify the pushed binary against a **pinned** SHA-256 on the guest. The
   transfer is the trust boundary; hashing whatever arrived and recording it is
   provenance, not verification.
2. Stage it **after checkpoint restore**, so no golden checkpoint carries it and
   the estate stays reproducible from clean media.
3. Restore the qualification check only when the lane genuinely executes the
   binary. The ruling makes LGPO permissible, not qualified — qualification is
   earned by execution, which is the whole reason the 2026-07-29 de-gating
   exists.

## Domain environment

**Superseded 2026-08-03: the disposable estate is the domain environment.** The
re-freeze the note below demanded has happened — every lane is qualified on the
estate with its own run, and the SSH transport to the shared host is retired.

| Property | Value |
|----------|-------|
| Forest/domain | ad.labdomain.dev |
| NetBIOS | LAB |
| Guests | LabDC01 (domain controller), LabMS01 (member server), LabCL01 (client) |
| DC OS | Windows Server 2025 Standard, 26100 family |
| Client OS | Windows 11 Enterprise 25H2, 26200 family |
| Networking | none — the guests have no network at all; the transport reaches them through the hypervisor |

Lanes needing GPMC execute on **LabMS01** and reach the DC for AD and SYSVOL.
`LabCL01` is the endpoint oracle. The guests are checkpoint-backed and
disposable, which is what makes destructive operations (`secedit /configure`,
policy application, staging LGPO.exe) permissible here and not on the retired
shared host.

> **The retired shared host.** Until 2026-08-03 this section described
> validation against the live `ad.hraedon.com` forest from `mvmcitest01`, a
> host shared with another project — which is why WP-3 forbade
> `secedit /configure` there and why the endpoint lane had never run. Those
> constraints belonged to that host, not to Plan 033. Certifications produced
> against it are **not retracted**: each is bound to the environment recorded in
> its own manifest. But no new run can be produced there without restoring the
> transport and re-qualifying.
>
> | Property | Value |
> |----------|-------|
> | Forest/domain | ad.hraedon.com |
> | NetBIOS | HRAENET |
> | DCs | MVMDC01 (192.168.1.29), MVMDC02 (192.168.1.19), MVMDC03 (192.168.1.21) |

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
