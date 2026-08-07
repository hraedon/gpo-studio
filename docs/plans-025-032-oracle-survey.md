# Oracle survey — the Plans 025–032 domain layers

Written 2026-08-06 against `main` at `b421996`, read-only. Nothing was executed
against Windows and no estate host was contacted.

## What this document is

Ruling 1 of
[`direction-2026-08-06-reconciliation-and-lab-handover.md`](direction-2026-08-06-reconciliation-and-lab-handover.md)
makes reconciling the blind-built post-1.0 layers **the programme** rather than a
prerequisite to one, and says how the remaining set gets scoped: not by auditing
it, but by a survey that answers, per module, **what oracle would settle it and
does the estate have that oracle today** — splitting the set into lanes runnable
now, lanes needing estate capability that does not exist yet, and lanes whose
oracle is a person. This is that survey. Quoting the ruling: *"That survey is the
input to the plan, and the plan is not Plan 033."*

### What it deliberately does not do

**It does not assess whether any module is correct.** §4 of
[`domain-layer-status.md`](domain-layer-status.md) forbids exactly that: reading
a domain layer against the specification and pronouncing it correct is the
internally-consistent-round-trip trap one level up, and the only thing that
settles these questions is native tooling. This survey assesses
**measurability** — what observation would discriminate the module against
Windows, and whether that observation can be made today.

Consequently, every place below where something on the wire looks suspicious is
recorded as **a hypothesis for a lane to test**, marked unverified, never as a
finding. Some of those hypotheses are strong — several are corroborated by
material already committed to this repository, and where that is so it is said
explicitly and the corroboration is named. None of them is a defect report. A
hypothesis that survives contact with a native producer is how a lane gets
scoped; a hypothesis dressed up as a finding is the audit this ruling rejects.

It also does not schedule anything. The running order in §5 is a recommendation
with reasoning attached, not a plan.

### Provenance of the estate claims

Every claim about what the estate can do is sourced from
`docs/plan-033/environment-spec.md`, `docs/plan-033/boundary-matrix.md`,
`tests/fixtures/scenarios/platforms.json`, `docs/plan-033/endpoint-lane-design.md`,
`docs/plan-033/wp3-expansion-design.md` and
`docs/plan-033/manual-gui-evidence-queue.md`. Where those documents do not answer
a question, this survey says so rather than guessing, and §9 collects what
remains uncertain.

Two questions the survey could not answer from the repository were put to the
operator while it was being written, and both came back answered. They are
recorded where they bear rather than left open, but they are stated here because
they change several classifications below:

- **A GUI console into LabMS01 is available.** Native reference capture is
  therefore possible for every CSE in this set that has no cmdlet surface. See
  the next subsection for exactly what that does and does not buy.
- **Plan 032's hosted control plane is not a build-it-or-not question.** The
  operator's decision is narrower and is recorded at §3.14.

### Capture versus lane: what the LabMS01 console does and does not buy

This distinction runs through the whole survey and is worth stating once,
plainly, rather than repeating it fourteen times.

**What it buys.** Security Settings' SDDL sections, Scripts, Folder Redirection,
Software Installation, Public Key Policies and wired/wireless are all authored by
GPMC editor snap-ins with **no cmdlet surface**. Before the console was
confirmed, there was no path to a native reference for any of them — the question
"what does Windows actually write here" had no answer available at any price.
Now it does. Those modules move from *no path at all* to
**manual-GUI-evidence-queue items**, and the repo already has the mechanism:
`docs/plan-033/manual-gui-evidence-queue.md`, whose WI-022 entry is the worked
precedent — a person drives the snap-in, `Backup-GPO` and `Get-GPOReport` stage
output under `C:\gpo-studio\manual`, the agent retrieves, inspects, sanitizes and
curates it into a committed fixture, and the disposable GPO is cleaned up under a
strict absence re-query. Route these captures there rather than inventing a
mechanism.

**What it does not buy.** *A human driving a GPMC snap-in is not a lane the
harness can re-run.* Anything settled this way is certified **against a capture**,
and the capture — not the authoring gesture — is the artifact that has to be
hash-bound, exactly as WI-022's fixture is. That is the difference between
"runnable now" and **"runnable now, once, by a person."** It has three practical
consequences a plan must budget for:

1. **Re-certification is not free.** A certified lane can be re-run against a new
   build family; a capture cannot. Re-establishing the reference means booking
   the operator again.
2. **The capture must earn its trip.** WI-022 is the model: one sitting settled
   five separate questions because they were enumerated in advance. Enumerate
   before booking.
3. **It settles the *read* direction, not the *write* direction.** A capture
   tells you what Windows writes. Proving that what *Studio* writes is accepted
   still needs `Import-GPO`, `secedit /validate` or an endpoint apply — all of
   which the harness can drive automatically once the reference exists.

So the modules below that depend on a capture are classified **(a) runnable now,
qualified: first step is a manual capture.** They are not blocked. But the
"automatable native authoring path" gap in §7 is still real and still the
dominant one — it is a gap in *automation*, not in *access*.

One procedural note: the direction document this survey answers **is not on
`main`** at the time of writing. It exists only in an uncommitted worktree. This
survey is scoped against that copy; if it changes before it lands, re-read
Ruling 1 against this document.

---

## 1. Reconciled module attribution

Attribution was determined from three sources that agree, and checked rather
than assumed:

1. **each plan's own `Status:` line**, which names its landed modules;
2. **`docs/capability-matrix.md`**, "Post-1.0 domain layers — landed but not
   surfaced", which is the repo's own plan→module index;
3. **the import graph**, by grep across `src/`, `scripts/` and `tests/`, which
   corroborates the grouping and additionally establishes who consumes each
   module.

### First tier — the Plans 025–032 set

| Plan | Modules | `src` lines |
|---|---|---|
| 025 | `security_template.py`, `object_security.py`, `network_security.py`, `policy_families.py` | 641 + 734 + 566 + 672 = 2,613 |
| 026 | `script_policy.py`, `artifact_store.py` | 672 + 684 = 1,356 |
| 027 | `software_install.py`, `folder_redirection.py` | 487 + 537 = 1,024 |
| 028 | `lifecycle.py`, `gpmc_interop.py` | 496 + 361 = 857 |
| 029 | `rsop.py` — **excluded, the worked example** | 831 |
| 030 | `publication.py`, `publisher.py` | 757 + 820 = 1,577 |
| 031 | `certification.py` | 684 |
| 032 | `hosting.py` | 687 |

**Fifteen modules, 9,629 lines. Fourteen are surveyed here**; `rsop.py` is
excluded because Ruling 1 makes it the worked example that establishes the shape
every other layer follows, and it is already further along than any of the rest.
The fourteen surveyed modules are 8,798 lines.

### On the direction document's "fifteen modules, ~9.6k lines"

**That figure is exactly right and needs no correction.** Fifteen is the module
count for Plans 025–032 *including* `rsop.py`, and 9,629 lines is the sum. The
same ruling then says "the remaining fourteen," which is the same set with
`rsop.py` removed. Both numbers in the direction document are internally
consistent and both are confirmed against `wc -l` and the capability matrix.

This is worth stating plainly because the task that commissioned this survey
suspected the figure might need correcting. It does not. What *was* wrong was a
provisional six-module reading of Plans 025–027 that never appeared in the
direction document — see below.

### Two attribution disagreements, resolved

**`policy_families.py` and `artifact_store.py` belong in scope; a six-module
reading of Plans 025–027 was wrong.** The capability matrix has listed all four
of Plan 025's modules and both of Plan 026's since it was written, and Plan 025's
`Status:` line names `policy_families.py` explicitly. `policy_families.py` is the
typed layer *over* `security_template.py` and imports directly from it;
`artifact_store.py` is the content-addressed store `script_policy.py` previews
against. Both are in. Plans 025–027 contribute **eight** modules, not six.

**`backup.py`, `migration.py` and `report.py` are not Plan 028 landings.**
Verified: Plan 028's `Status:` line names only `lifecycle.py` and
`gpmc_interop.py`, and explicitly adds that "native GPMC backup emission itself
(in `export.py`) *is* surfaced and carries Plan 033 WP-2 Windows evidence; this
plan's lifecycle/migration/report layer does not." The three modules predate Plan
028 — `backup.py` is named by Plans 002/003/004/007/012/013/014, `migration.py`
by Plans 012 and 013, `report.py` by none (it is foundation work) — and all three
are reachable from `api.py`: `backup.py` at `api.py:48`, `report.py` at
`api.py:123`, and `migration.py` by a function-scoped import at `api.py:3130`
serving the `migration_table_path` parameter on the backup-import endpoint.

They are therefore **surfaced 1.0 code**, not unproven drafts in the
`domain-layer-status.md` sense, and they are not part of the fifteen. But Plan
028's *scope* — WP-1 backup preservation, WP-2 migration tables, WP-4 reports —
is written about exactly these three, and its acceptance gates bind them. They
are surveyed in a separate second tier at §4, with the distinction that changes
their urgency stated there.

### Second tier — surfaced code inside Plan 028's gates

| Module | Attributed to | `src` lines | Reached from `api.py` |
|---|---|---|---|
| `backup.py` | 002 / 003 / 007 / 014 | 768 | yes, module-level import |
| `migration.py` | 012 / 013 WP-2–3 | 135 | yes, function-scoped import |
| `report.py` | foundation | 100 | yes, module-level import |

**Seventeen modules are surveyed in total: fourteen first-tier, three
second-tier.**

### Consumers, checked rather than assumed

Outside their own test modules, of the fourteen:

- `security_template.py` — consumed by `scripts/windows-oracle/finalize_wp3_run.py`
  and `scripts/plan-033/build-wp3-candidate.py`. **It is the only one of the
  fourteen that any oracle has ever read.**
- `artifact_store.py` — imported by `publication.py` (itself unsurfaced) and by
  `script_policy.py`, which resolves artifact bodies through it.
- `gpmc_interop.py` — imported by `publication.py`, itself unsurfaced.
- `object_security.py` and `script_policy.py` — imported only by other unsurfaced
  modules in the same set.
- `policy_families.py`, `network_security.py`, `software_install.py`,
  `folder_redirection.py`, `lifecycle.py`, `publication.py`, `publisher.py`,
  `certification.py`, `hosting.py` — **zero** consumers outside tests.

**None of the eight Plans 025–027 test modules references a native GPMC capture.**
Checked by grep for `fixtures`/`native`/`gpmc`/`Backup` across all eight test
files: the only hits are the string `SeBackupPrivilege` and a comment. Every
expectation in every test is a Studio-authored string. That is the "Studio could
read what Studio wrote" condition `domain-layer-status.md` describes, present in
all eight.

---

## 2. The worked example, and the bar every proposed oracle is held to

`rsop.py` is not surveyed, but its evidence lane is the shape every
recommendation below copies. From `docs/plan-033/rsop-oracle-design.md`,
`wp6b-results.md` and `wp9-results.md`:

1. **Probe the estate before designing against it.** WP-6 discovered by
   measurement that its own registry's stated oracle
   (`Get-GPResultantSetOfPolicy`) did not exist on the client, and that
   `gpresult /x` exits 0 while writing no file. The design changed as a result.
   Do this first for every lane below.
2. **Author a disposable topology with native tooling**, not with Studio.
3. **Commit Studio's prediction as a hash-bound input artifact before applying
   anything**, so it cannot be retrofitted to the observation.
4. **Assert on the artifact, never on the exit code** of a native executable.
5. **Three outcomes, never collapsed**: prediction matches / prediction wrong (a
   finding about Studio) / experiment did not run (inconclusive) — plus a
   **native control row** whose answer is decided by a mechanism Studio does not
   model, so "Studio was wrong" can be told apart from "nothing happened."
6. **Expect the lane to rewrite what it touches.** WP-6/WP-9 produced WI-031
   through WI-043, each demonstrated against a real client first, then fixed,
   then re-certified.

The reusable machinery is `scripts/plan-033/build-*-candidate.py`,
`scripts/windows-oracle/run-*-author.ps1` / `run-*-observe.ps1`, `psdirect.ps1`,
`finalize_*_run.py`, and `src/gpo_studio/oracle_evidence.py` with its manifest
schema, `FROZEN_ENVIRONMENT` binding and four-state evidence vocabulary. **A
proposed oracle below is credible only if it can be built out of that**, and each
entry says so when it cannot.

---

## 3. The fourteen, module by module

Each entry carries: what the module claims to produce on the wire; what oracle
would settle it; whether the estate has that oracle today; the classification —
**(a)** runnable now, **(b)** blocked on estate capability, **(c)** the oracle is
a person; the cheap discriminator; and rough lane cost.

### 3.1 `security_template.py` — Plan 025

**Produces.** The MS-GPSB security template `GptTmpl.inf`, which lives in SYSVOL
at `{GUID}\Machine\Microsoft\Windows NT\SecEdit\GptTmpl.inf` and is consumed by
the Security Settings CSE `{827D319E-6EAC-11D2-A4EA-00C04F79F83A}`. The wire
contract it asserts is narrow and explicit: **UTF-16LE with a BOM**, refusing
UTF-32LE and refusing a missing BOM; **CRLF** on emit; INI sections;
`Key = Value` entries; `;` comments and trailing-`\` continuations preserved;
eleven recognised section names. It reproduces `raw_text` verbatim when nothing
changed. It does not write SYSVOL, does not emit the `[Unicode]`/`[Version]`
preamble itself, and does not touch `gPCMachineExtensionNames`.

Three of the eleven sections (`Registry Keys`, `File Security`,
`Service General Setting`) are not `key = value` on the wire — they are bare
quoted-CSV lines. The parser cannot read that shape and puts them in
`InfSection.unknown_lines` with `entries` empty. This is already WI-038 and is a
code fact, not a survey claim.

**Oracle.** The one that already exists, and this project's best precedent:
`secedit /validate <candidate.inf>`, then `/import` into a fresh `.sdb`, then
`/export`, decoded with `decode_security_template` and compared per (section,
key) with a section-specific comparator — principal **sets** for
`Privilege Rights`, name/SID equivalence with RID reduction for
`Group Membership`. The direction that has **never** been run is the read one: no
native GPMC-authored `GptTmpl.inf` has ever been fed to
`parse_security_template`. Every WP-3 run to date authored with Studio and read
back with `secedit`. For the three SDDL-bearing sections the round trip is not
the whole oracle even in principle — `secedit /export` re-keys them by ordinal
index (measured 2026-08-04) — so a lane needs an entry-shape comparator plus a
canonical-SDDL control row.

**Estate.** **Yes, for most of it.** `security-template-secedit` is a defined
lane; LabMS01 (`member-ws2025-disposable`) is `frozen`, qualified by
`wp3-security-template-20260803230220-2450`; `secedit` is `frozen`.
`System Access`, `Event Audit`, `Privilege Rights`, `Registry Values` and
`Group Membership` are certified. Two things the estate does not have today:
`Kerberos Policy` **cannot be measured on LabMS01** — it exports empty on a
member server (measured 2026-08-04) and `platforms.json` states that the
`dc-ws2025` host_id "is historical and does not imply the lane executes on the
DC"; and **`secedit /configure` has never been invoked by any lane**, so the
entire WP-3 record is a database round trip and nothing tests that a template
Studio wrote changes a machine. `environment-spec.md` explicitly permits
`/configure` here, so that is unexercised capability, not absent capability.

**Classification: (a) runnable now**, with two named carve-outs. `Kerberos
Policy` is **(b)**, blocked on a lane that executes on LabDC01. The three SDDL
sections are gated by the open **WI-038 product decision** — parse them, or
declare them preserve-only — before a comparator for them is worth building. The
read direction needs a native reference, which is a **manual capture** under the
LabMS01 console rather than a lane step.

**Cheap discriminator.** **Read a native template for the first time.** On
LabMS01, produce a GPO carrying a restricted group and a registry-key ACL —
authoring the restricted group and the registry-key ACL is a GPMC snap-in
gesture, so this is a manual-queue capture — then `Backup-GPO`, retrieve
`GptTmpl.inf`, and run it through `decode_security_template` +
`parse_security_template`. Count `parse_warnings` and `unknown_lines`.

*Hypothesis, unverified:* every `[Registry Keys]`, `[File Security]` and
`[Service General Setting]` line lands in `unknown_lines`, and `[Group
Membership]` arrives keyed by `*SID__Members` rather than by name. If so, the
reader is blind to exactly the sections `domain-layer-status.md` names as the
risky ones. A zero-cost pre-check needing no estate: run
`parse_security_template` over the `inf_excerpt` strings already committed in
`tests/fixtures/scenarios/security-template/regkeys-filesecurity.json` and
`services-area.json` and count `unknown_lines`. That is a code check, not an
oracle — those excerpts are `spec-informed` — but it costs nothing and says
whether the estate trip is worth booking.

**Cost.** Small for the read-direction discriminator. Medium for a
genuinely-parsed SDDL tranche: three new `secedit` areas on both the import and
export calls, an entry-shape comparator and a canonical-SDDL control row — all
already scoped in `wp3-expansion-design.md`, which is why it is medium rather
than large.

### 3.2 `object_security.py` — Plan 025

**Produces.** Not a file — **INF section entries**, as
`dict[section, dict[key, value]]` from each family's `to_template_entries()`,
intended to be merged into a `GptTmpl.inf`. Four families:
`[Group Membership]` keyed `{sid}__Members` / `{sid}__Memberof` with comma-joined
`*{sid}` values; `[Service General Setting]` as `{service} = {code},"{SDDL}"`
with 2/3/4 = automatic/manual/disabled; `[Registry Keys]` and `[File Security]`
as `{path} = {code},"{SDDL}"`. The only serializer in the tree that could consume
these dicts is `format_security_template`, which writes `f"{key} = {value}"`.

**Hypotheses for a lane, unverified.**

- *Shape.* The corpus's own spec-informed excerpts show the native form as a bare
  quoted-CSV line with **no `=`**:
  `"MACHINE\SOFTWARE\StudioLab\Audit",0,"D:PAR(A;OICI;FA;;;BA)"`. The module
  emits `MACHINE\SOFTWARE\StudioLab\Audit = 0,"D:PAR(…)"`. Different shapes;
  which one `secedit` accepts has never been measured.
- ***The repository contradicts itself on the propagation codes.***
  `_propagation_from_code` (`object_security.py:119–128`) maps
  `0 → none, 1 → propagate, 2 → replace`. The committed corpus scenario
  `tests/fixtures/scenarios/security-template/regkeys-filesecurity.json:57`
  states "0 = propagate inheritable permissions to subkeys/subfolders,
  1 = replace existing permissions, 2 = do not allow permissions to be replaced.
  These meanings are spec-informed until the first capture." **Both are
  unverified and they cannot both be right.** Verified as a contradiction; which
  side is correct is not. This is a unit/vocabulary question of exactly the kind
  WI-024 settled for GPP recovery delays, where the answer was wrong by 1000x.
- *Reading is structurally dead today.* `from_template` reads `section.entries`,
  which WI-038 establishes is always empty for these three sections, so
  `RegistrySecurityFamily.from_template()` on any real `GptTmpl.inf` returns an
  empty family. Code fact, not oracle finding.

**Oracle.** Two oracles for two different questions, and conflating them is the
trap. **Shape and round-trip:** the WP-3 lane extended — build a candidate whose
three SDDL sections come from `to_template_entries()`, `secedit /validate`,
import with `/areas regkeys filestore services group_mgmt`, export with the same
areas, parse back through `from_template` and compare **typed objects** rather
than strings so `secedit`'s ordinal re-keying and path lower-casing are not
reported as defects; carry a canonical-SDDL control row
(`D:PAR(A;CI;KA;;;BA)(A;CI;KR;;;BU)` came back byte-for-byte on 2026-08-04).
**Meaning of the propagation codes:** the round trip cannot answer this — it
preserves a code without saying what the code does. The oracle is
`secedit /configure` on a disposable guest plus `Get-Acl` on the target registry
key and file **before and after**.

**Estate.** **Yes for both, and neither has been exercised.** Adding
`/areas regkeys filestore services` is a two-line change to
`run-wp3-security-template.ps1`, which already carries a comment naming exactly
those three areas. `secedit /configure` is *permitted* by `environment-spec.md`
— "the guests are checkpoint-backed and disposable, which is what makes
destructive operations … permissible here" — but no lane has ever run it, so the
harness has no configure step, no `Get-Acl` observer and no checkpoint-restore
discipline. The corpus already carries `services-area`, `regkeys-filesecurity`
and `group-membership` at readiness `ready` with committed expected wire text.
The scenarios exist; the lane rows do not.

**Classification: (a) runnable now.** Both oracles are inbox tools on a frozen
host and both are fully automatable — `secedit` needs no GUI. What is missing is
harness, not capability. The *scope* of what to build should wait on the WI-038
decision, which is a person's call recorded elsewhere and not this module's
blocker. A GPMC-authored native reference, if one is wanted for comparison, is
now available as a manual capture, but neither oracle below requires it.

**Cheap discriminator.** **One `secedit /validate` call.** Take a single
`RegistrySecurityFamily.to_template_entries()` output, render it as
`format_security_template` would, wrap it in the `[Unicode]`/`[Version]`
preamble, and validate it. *Hypothesis, unverified:* it is rejected or silently
ignored, because the native form is a bare quoted-CSV line. `secedit /validate`
was measured on 2026-08-04 to genuinely reject a malformed SDDL with a specific
error, so it is a real oracle here and not a rubber stamp. Second discriminator,
for the meaning question: author `"C:\StudioLab\Data",1,"D:PAR(A;OICI;FA;;;BA)"`,
`secedit /configure`, `Get-Acl`. Whether the existing DACL survived tells you in
one observation whether the module or the corpus has the codes right.

**Cost.** Small for the shape discriminator. Medium for a full round-trip
tranche. Medium-to-large if the `/configure` + `Get-Acl` meaning lane is built,
because it would be the first destructive lane in the repo and needs
checkpoint-restore in the harness rather than only in the runbook.

### 3.3 `network_security.py` — Plan 025

**Produces. Nothing on the wire**, and this is the load-bearing fact about the
module. No serializer, no parser, no `to_template_entries`, no file emitter, no
registry shape. 566 lines of frozen dataclasses (`FirewallRule`,
`FirewallPolicy`, `IpsecRule`, `IpsecPolicy`, `CertificateTrustEntry`,
`PublicKeyPolicy`, `NetworkPolicy`), per-object `validate()`, and
`assess_network_security()` folding issues into a `low`/`medium`/`high`/`critical`
label. Its own docstring concedes these families "use dedicated Group Policy
Client-Side Extension (CSE) formats" and then implements none of them.

Per `docs/plan-021/capability-inventory.md`, five different artifacts sit behind
this one module: Windows Firewall with Advanced Security is registry-backed
policy; legacy IPsec is `{E437BC1C-AA7D-11D2-A382-00C04F991E27}` with its own
policy store; Wireless is `{0ACDD40C-75AC-47AB-BAA0-BF6DE7E7FE63}` with Wi-Fi
profile XML; Wired is `{B587E2B1-4D59-4E7E-AED9-22B9DF11D053}`; EFS recovery is
`{B1BE8D72-6EAC-11D2-A4EA-00C04F79F83A}` inside a `GptTmpl.inf` section. So the
measurable question is not "is the serialization right" — there is none to be
wrong — but **"is the model expressive enough, and are its vocabularies
Windows'?"**

**Oracle.** Author natively, read what was written, compare field for field.
Firewall: `New-NetFirewallRule -PolicyStore "LAB\<GPO>"` with distinctive values
on every field the model carries, then `Backup-GPO`, plus
`Get-NetFirewallRule -PolicyStore … | Get-NetFirewallPortFilter` for structured
readback. IPsec: `New-NetIPsecRule -PolicyStore …` plus
`Get-NetIPsecQuickModeCryptoSet` for the `IpsecEncryption` vocabulary. Public Key
Policies, wired and wireless have **no cmdlet surface** — a GPMC-authored backup,
read for its actual artifacts, is the only oracle.

*Hypothesis, unverified:* a firewall rule authored into a GPO surfaces in the
backup as a single pipe-delimited string under
`SOFTWARE\Policies\Microsoft\WindowsFirewall\FirewallRules` in `Registry.pol`
(`v2.31|Action=Allow|Dir=In|Protocol=6|LPort=80|Name=…|`) rather than as a
structured record — in which case `FirewallRule`'s real job is to be a
parser/emitter for that string, and the discriminating question is whether its
field set and enum spellings correspond to the token names in it.

**Estate. Yes by capture; unknown by cmdlet.** The GUI console makes a native
reference reachable for every family here, so the module is measurable. What
could **not** be determined from the repository is whether any of it is
*automatable* — and that is worth stating carefully rather than assuming, because
it is the difference between a lane and a booking. Verified by grep: there is **no mention of
`NetSecurity`, `New-NetFirewallRule`, `Get-NetFirewallRule`, `PolicyStore` or
`netsh advfirewall` anywhere in `src/`, `scripts/`, `docs/` or `tests/`** — zero
hits. `platforms.json` has no tool row and no lane touches these boundaries. The
endpoint-lane measurement table recorded `GroupPolicy`, `ActiveDirectory`,
`gpupdate`, `gpresult` and RSAT presence on both guests, and not NetSecurity. The
expectation is that NetSecurity is inbox on Server 2025 and therefore present,
but that is precisely the assumption `rsop-oracle-design.md` exists to forbid.
Separately: Public Key Policies, wired and wireless have no command-line
authoring path at all and need the GPMC editor — which the LabMS01 console now
makes reachable as a manual capture.

**Classification: (a) runnable now, qualified — and the qualification is about
automation, not access.** This module was the survey's one **(b)** until the
LabMS01 console was confirmed. It is no longer blocked: every family here now has
*some* path to a native reference, and a reference is exactly what this module's
measurable question needs, because there is no serializer to round-trip and the
question is "is the model expressive enough, and are its vocabularies Windows'?"

The qualification has two parts, and they are different in kind:

- **PKI, wired and wireless are capture-only.** No cmdlet exists, so they are
  manual-queue items, certified against a hash-bound capture, re-established only
  by booking the operator again.
- **Firewall and IPsec may be automatable, and nobody has checked.** If
  NetSecurity is present on LabMS01, they get a re-runnable authoring lane on the
  WP-1B pattern. If it is absent, they fall back to capture like the other three.
  **That is a difference in lane economics, not in whether the work can be done**
  — which is why the module is (a) either way, and why the probe below is still
  worth running first.

**Cheap discriminator.** Two commands, the first nearly free.
(1) `Get-Module -ListAvailable NetSecurity` and
`Get-NetFirewallRule -PolicyStore "LAB\<disposable GPO>"` on LabMS01. This no
longer decides whether the module is measurable — it decides whether the firewall
and IPsec halves get a *re-runnable* lane or a one-shot capture, which is the
difference between a small tranche and a manual-queue booking. (2) Then, by
whichever path the probe selects: create one rule with every field distinctively
set, `Backup-GPO`, and look at how it is stored. One backup settles whether
`FirewallRule` models the artifact or models the GPMC dialog.

**Cost.** Small for the probe. Medium for a firewall/IPsec vocabulary lane if the
probe is positive — the WP-1B pattern with a different authoring cmdlet. Large if
PKI, wired and wireless stay in scope: three more CSEs, three more artifacts,
three manual GUI captures that must be enumerated before they are booked, and
certificate material an isolated estate has to be given rather than able to
fetch.

### 3.4 `policy_families.py` — Plan 025

**Produces.** Typed families over `GptTmpl.inf` sections, each with
`to_template_entries() -> dict[section, dict[key, value]]`. Every key name is
concrete and checkable: `[System Access]` — `MinimumPasswordAge`,
`MaximumPasswordAge`, `MinimumPasswordLength`, `PasswordComplexity`,
`PasswordHistorySize`, `ClearTextPassword`, `LockoutBadCount`,
`LockoutDuration`, `ResetLockoutCount`; `[Kerberos Policy]` — `MaxTicketAge`,
`MaxRenewAge`, `MaxClockSkew`, `EnforceLogonRestrictions`,
`EnforceUserLogonRestrictions`; `[Event Audit]` — nine `Audit*` keys valued
`0|1|2|3`; `[Privilege Rights]` — `{privilege} = {principals joined by ","}`;
`[Registry Values]` passed through opaquely.

**Hypotheses for a lane, unverified.**

- *Units.* The Python fields are `lockout_duration_minutes`,
  `lockout_window_minutes`, `max_ticket_age_hours`, `max_renewal_age_days`,
  `max_clock_skew_minutes`, and `to_template_entries` writes those integers out
  with **no conversion** (verified at `policy_families.py:206–216, 267–274`).
  Whether `LockoutDuration` is minutes, `MaxTicketAge` hours and `MaxRenewAge`
  days on the wire has never been measured.
- *Omission rules.* `PasswordPolicy.to_template_entries()` **always emits all six
  keys**, including its defaults (`MaximumPasswordAge=42`,
  `MinimumPasswordLength=0`). In a security template an absent key and a key set
  to a default are different policy states. Whether the module can express "not
  configured" at all is a question a lane answers immediately.
- *Principal syntax.* `UserRightsFamily.to_template_entries()` joins principals
  with `","` and does nothing about the `*` SID prefix, while
  `_is_admin_principal` in the same file strips a leading `*` — so the module
  reads `*S-1-…` and writes whatever it was handed.

**Oracle.** The **existing, already-certified WP-3 lane**, with exactly one thing
changed: the candidate is built from `policy_families.to_template_entries()`
instead of from hand-written `InfSection`s.

**This is the finding that matters most in the whole survey, and it is verified.**
`scripts/plan-033/build-wp3-candidate.py` imports only from
`gpo_studio.security_template`, contains **zero** references to `policy_families`
or `to_template_entries`, and hand-writes every `InfSection` including `[Unicode]`,
`[Version]`, `[System Access]`, `[Event Audit]` and `[Privilege Rights]`.
**WP-3's green 20/20 therefore certifies `security_template.py`'s codec and says
nothing whatsoever about `policy_families.py`.** Building the candidate from
`to_template_entries()` instead aims a currently-green, already-certified lane at
unproven code, and tests units, key names and omission rules at once.

Use deliberately non-default, mutually distinguishable values so a unit error
cannot hide: `LockoutDuration = 47`, `ResetLockoutCount = 11`,
`MaximumPasswordAge = 43`, `MaxTicketAge = 13`, `MaxClockSkew = 7`. Agreement is
the export carrying those exact pairs; divergence is a renamed key, a converted
number, or a key that vanished. For the *meaning* of the units as opposed to
their round-trip survival, the oracle is `secedit /configure` on a disposable
guest followed by `net accounts`, which prints the lockout duration and
observation window in minutes and would settle it in one line of output.

**Estate. Yes**, for `[System Access]`, `[Event Audit]`, `[Privilege Rights]` and
`[Registry Values]`: LabMS01 frozen, `secedit` frozen, lane green, finalizer
written, comparator already generalised for principal sets. **No** for
`[Kerberos Policy]`, which exports empty on a member server and needs a
DC-hosted lane that does not exist.

**Classification: (a) runnable now.** The `[Kerberos Policy]` family alone is
**(b)**.

**Cheap discriminator.** **Point the existing green lane at the untested code.**
Change `build-wp3-candidate.py` so the `[System Access]` and `[Event Audit]`
blocks come from `AccountPolicyFamily(...).to_template_entries()` and
`AuditPolicyFamily(...).to_template_entries()` with the values above, and rerun
`run-wp3-oracle.sh` unchanged. **No new host, no new tool, no new comparator, no
new finalizer.** If `policy_families` disagrees with Windows on a key name, a
unit or an omission rule, that run turns red on the first attempt.

**Cost. Small.** One candidate-builder change, one expected-side change, one lane
run on an already-qualified host with an already-certified harness. The only new
thinking is choosing values distinctive enough that a unit error cannot survive.

### 3.5 `script_policy.py` — Plan 026

**Produces.** Two Windows INI files under the GPO in SYSVOL —
`{GUID}\Machine\Scripts\scripts.ini` and `…\psscripts.ini`, plus the `User\`
equivalents — consumed by the Scripts CSE
`{42B5FAAE-6536-11D2-AE5A-0000F87571E3}`, with bodies under `Scripts\Startup\`,
`Shutdown\`, `Logon\`, `Logoff\`. `serialize_script_policy_ini()` returns a
**`str`** (verified at `script_policy.py:412–414`), joined with `"\n"`. It emits
`[Startup]`, `[Shutdown]`, `[Logon]`, `[Logoff]` with `{n}CmdLine` and
`{n}Parameters` keys, and for the PowerShell variant additionally `{n}NoProfile`,
`{n}NonInteractive` and `{n}ExecutionMode`. An empty section gets a literal
`; no scripts configured` comment. The PowerShell variant appends a `[Policy]`
section carrying `RunLogonScriptsSync`, `RunLogoffScriptsSync`,
`LegacyScriptsFirst` and `PowerShellOrder`.

There is no encoding, no BOM, no CRLF, no file path and no CSE-GUID registration
anywhere in the module — the caller supplies all of it, and **nothing in the
repository is that caller.**

**Hypotheses for a lane, unverified.**

- *Encoding and line endings.* The module returns text joined with `"\n"` and no
  BOM. If native `scripts.ini` is UTF-16LE/BOM with CRLF — as `GptTmpl.inf`
  turned out to be — then nothing this module emits is a `scripts.ini` as far as
  Windows is concerned. **This is the WP-3 finding's exact shape, in a module no
  oracle has ever read.**
- *The `[Policy]` section.* `RunLogonScriptsSync` is an Administrative Templates
  setting that lives in `Registry.pol`, not an INI key, and native
  `psscripts.ini` is believed to carry PowerShell ordering in a `[ScriptsConfig]`
  section with `StartExecutePSFirst` / `EndExecutePSFirst` rather than a
  `PowerShellOrder` string. If that holds, `[Policy]` is invented and four
  settings have no representation.
- *The comment line.* Whether native GPMC writes empty sections at all, and
  whether the CSE tolerates a comment there, is unmeasured.

**Oracle.** Two legs answering different questions, which must not be collapsed.
**Authoring-format leg — the WP-1B pattern, fully automatable:** build a
Studio-authored native v2 GPMC backup carrying `Machine\Scripts\scripts.ini`, a
`.cmd` body under `Machine\Scripts\Startup\`, and the Scripts CSE GUID in
`Backup.xml`'s extension lists; `Import-GPO -CreateIfNeeded` on LabMS01; then
`Get-GPOReport -ReportType Xml` and assert GPMC renders the script with the
authored command, parameters and order; then `Backup-GPO` and compare the
re-exported `scripts.ini` after normalising line endings. **Endpoint leg — the
two-guest lane, which already exists:** link to the disposable OU holding
LabCL01, `gpupdate`, restart for a startup script (or use a logon script under
WP-9's `user-logged-on` checkpoint), and observe a marker the script writes into
HKLM plus GroupPolicy operational events for the Scripts CSE. Assert on the
artifact, never on the exit code.

**Estate. Yes, both legs, and neither needs anything new.** LabMS01 has
`Import-GPO`, `Get-GPOReport` and `Backup-GPO` — the machinery WP-1B and WP-2 are
certified on. The two-guest endpoint lane is built and certified
(`endpoint-observe-20260803142424-3050`), the client is a real 26200 build, and
WP-9 established a reproducible logged-on console session. What is missing is a
candidate builder, a finalizer, and a Scripts-CSE-GUID registration path in
Studio's `Backup.xml` writer — all harness, all in-repo. Scripts have no cmdlet
authoring surface, so the GPMC-authored *reference* `scripts.ini` is a
**manual-queue capture** under the LabMS01 console rather than a lane step; both
oracle legs above are automatable once that reference exists.

**Classification: (a) runnable now**, qualified: the reference capture is a
one-shot operator booking, everything downstream of it re-runs.

**Cheap discriminator.** **Hex-dump the first four bytes of a native
`scripts.ini`.** Get one native Scripts GPO onto LabMS01, `Backup-GPO`, retrieve
`DomainSysvol/GPO/Machine/Scripts/scripts.ini`, and check three things: does it
begin `FF FE`; are line endings CRLF; is there a `[Policy]` section. Three
questions, one file, one command. If (1) or (2) holds, the correction is small in
lines and total in meaning, exactly as it was for `security_template.py`
(+36 −1).

**Cost.** Small for the discriminator. Medium for the full writer-conformance plus
endpoint tranche. The endpoint half is cheap because the lane already exists and
only needs a new candidate.

### 3.6 `artifact_store.py` — Plan 026

**Produces. Nothing on the Windows wire.** A local SQLite database with an
`artifacts` table keyed by SHA-256 content hash and an `artifact_provenance`
audit trail; an extension allow-list and deny-list; an EICAR-literal malware
check; six regex secret detectors; a
`pending → scanned → approved/rejected/quarantined/deleted` lifecycle; and
`check_publication_safety`, which re-verifies status, scan result, secrets,
expiry, signature-presence-for-`.exe`/`.dll`, and content hash. The only thing it
ever puts near SYSVOL is the artifact **bytes, unmodified**. Its entire wire claim
is therefore Plan 026's first acceptance gate: *the exact approved bytes are the
bytes stored, published, and observed.*

**Oracle.** For the byte-identity claim: stage an approved artifact into a GPO's
`Scripts\Startup\` on LabMS01 over `psdirect`, `Get-FileHash` on the guest, and
compare against `metadata.content_hash`. Any Scripts lane gets that for free as a
side effect. For **everything else the module does there is no Windows oracle and
there cannot be one.** Windows has no opinion about whether `AKIA…` is a secret,
whether `.hta` should be forbidden while `.exe` is permitted, or whether a signed
executable should be publishable. Those are policy choices, and asking `secedit`
or the CSE about them is a category error.

**Estate.** Byte-identity leg: yes, trivially. The rest: no oracle exists to have.

**Classification: (c) the oracle is a person.** The judgement, as concretely as
the survey can put it:

- **Is the extension policy the intended one?** `.exe` and `.dll` are in
  `ALLOWED_EXTENSIONS` and `check_publication_safety` publishes a signed one —
  but `signer` is a free-text metadata field that nothing in the module ever
  verifies, while Plan 026 WP-4 requires "only signed artifact references."
- **Is `_detect_malware` honestly labelled?** It is a single EICAR literal.
  Calling that "malware scanning" in a module whose docstring says artifacts "are
  scanned for malware signatures … before it can be approved" is a review
  decision, not a measurement.
- **Is content-hash-as-primary-key the intended dedupe semantics?**
  `store_artifact` returns the *existing* row when the hash matches, so two files
  with identical bytes and different names collapse to whichever arrived first,
  and no provenance entry records the second arrival.
- **Should `check_publication_safety` be able to revoke an approval?** It
  re-scans for secrets at publication time, so an approved artifact can become
  unpublishable without any state change or audit entry.

**Cheap discriminator.** Not a Windows test — a one-line demonstration put in
front of the person who has to decide. Store two byte-identical files with
different `original_name`s and show that the second call returns the first's
metadata unchanged, with no new provenance row. Whether that is correct
content-addressing or a provenance gap is the judgement to settle **before** any
lane is scoped, because it decides whether "the exact approved bytes are the
bytes published" is even the right gate.

**Cost. Small** — this is a review, not a lane. The only lane-shaped work is a
single byte-identity assertion a Scripts lane already produces.

### 3.7 `software_install.py` — Plan 027

**Produces. Nothing on the wire**, and unusually so even for this set. 487 lines
with no serializer, no parser, no file emitter and no registry shape:
`MsiPackage`, `SoftwareCategory`, `CategoryTree`, `UpgradeRule`,
`DeploymentStatus`, `InstallationScript`, a GUID regex, a validation pass, and a
state machine over `pending|deployed|upgrading|removing|removed|failed|orphaned`.
That state machine models a **workspace** lifecycle, not a Windows one; no
Windows tool emits those states.

What the Software Installation CSE `{C6DC5466-785A-11D2-84D0-00C04FB169F7}`
actually consumes, per `docs/plan-021/capability-inventory.md`, is `.aas`
Application Advertisement Scripts under `Machine\Applications\{GUID}.aas` plus
`packageRegistration` objects in AD under the GPO's Class Store. The `.aas` file
is a binary advertisement script the GPMC editor generates from the MSI via the
Windows Installer APIs. Studio emits neither, and there is no documented format
for it to emit.

**Oracle.** The first oracle is a **feasibility read, not a conformance write**,
and it decides whether further work is worth doing. On LabMS01, deploy one MSI
through the GPMC Software Installation node, `Backup-GPO`, and inspect the
backup: does `DomainSysvol/GPO/Machine/Applications/*.aas` appear at all; is the
AD-side `packageRegistration` carried by the backup or is it external AD state
like GPO DACLs and WMI-filter associations (which `boundary-matrix.md` puts under
`gpo-ad-object-security` and `wmi-filter-object-association`, explicitly excluded
from `gpo-backup-content`); does `Import-GPO` into a fresh GPO reconstruct a
working deployment? That answers the question everything else depends on: **is
Software Installation writable by anything other than the GPMC editor?**

*Hypothesis, unverified:* the AD-side package objects are not carried by
`Backup-GPO`/`Import-GPO`, in which case Studio's only honest states for this
family are read-only or preserve-only and the deployment model has no wire to be
right or wrong about.

If the feasibility read says writing is possible, the second oracle is the
endpoint: link to LabCL01, `gpupdate`, restart, and observe the product installed
plus `AppMgmt` operational events.

**Estate. Yes for the feasibility read, with two conditions.** Authoring the
reference deployment needs the **GPMC editor GUI** on LabMS01, which the console
provides — so this is a manual-queue booking, not a blocker. The two conditions
are real: an **MSI has to exist inside an isolated estate with no egress** (the
LGPO ruling of 2026-08-03 is the precedent — push over `psdirect`
post-checkpoint-restore, verify against a pinned SHA-256 on the guest), and
**installing software on LabCL01 is destructive** and needs checkpoint restore,
which is permitted but has never been exercised by any lane.

**Classification: (c) the oracle is a person.** The judgement is a scope
decision, not a Windows fact: **should GPO Studio write Software Installation at
all?** The module cannot produce the CSE's artifact, and that artifact is
generated by Windows Installer from the package rather than authored by hand. The
person chooses between preserve-only, read-only (parse a native backup for review
and diff), or committing to `.aas` generation. The feasibility capture is the
*input* to that decision; it does not substitute for it. That capture is now
bookable — the console removed its only access blocker — so the decision is no
longer waiting on the estate.

**Cheap discriminator.** **One GPMC-authored MSI deployment, one `Backup-GPO`,
one directory listing.** If `Machine\Applications\*.aas` is absent from the
backup — or present but not reconstructed by `Import-GPO` — then Software
Installation is not a writer target under this project's contract. That is a
scope answer, not a defect list, which is exactly why it is worth taking before
anything is built.

**Cost. Medium.** The capture is small, but it is a manual GUI item plus an MSI
staged into an isolated estate under hash-pin discipline, and the decision it
feeds is the expensive part. An actual conformance lane, if the decision goes
that way, would be **large**.

### 3.8 `folder_redirection.py` — Plan 027

**Produces.** `to_registry_settings()` returns tuples of
`(HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders,
<value name>, <path>)` for nine of the thirteen modelled folders; `contacts`,
`links`, `saved_games` and `searches` map to `None` and emit nothing. The module
also models `grant_exclusive_rights`, `move_contents`,
`remove_redirect_on_policy_removal`, `also_redirect_subfolders`, and per-group
`RedirectionRule`s — **none of which appear in the output** (verified at
`folder_redirection.py:317–336`). In `advanced` mode `effective_path()` returns
`rules[0].target_path`, so a policy with three group rules emits one path and
silently discards two.

The Folder Redirection CSE `{25537BA6-77A8-11D2-9B6C-0000F8080861}` consumes
`fdeploy.ini` under the GPO's `User\Documents & Settings\`. **Verified by grep:
nothing in this repository mentions `fdeploy.ini` outside that one inventory row
in `docs/plan-021/capability-inventory.md`** — not in `src/`, not in `scripts/`,
not in the corpus.

*Hypothesis, unverified:* `User Shell Folders` is what the CSE **writes on the
client**, not what the GPO **carries**, so `to_registry_settings()` describes the
effect rather than the input. If so this is not a wrong serialization — it is the
wrong artifact, which is a scope question rather than a bug.

**Oracle.** Author folder redirection for two folders through the GPMC Folder
Redirection node on LabMS01 — one Basic, one Advanced with two different groups,
with exclusive-rights and move-contents both **non-default** so their encoding is
visible — then `Backup-GPO` and read
`DomainSysvol/GPO/User/Documents & Settings/fdeploy.ini`. That single artifact
settles four questions at once: which file the CSE reads; how each folder is
keyed; how the four option flags are encoded; and how multiple group rules are
represented. Then the endpoint leg on LabCL01 under WP-9's `user-logged-on`
checkpoint: apply, then read the resulting `User Shell Folders` values from
`HKEY_USERS\<SID>` — **not** `HKCU`, which WP-9 measured returns the harness
account's hive and reports every value absent — plus the FolderRedirection
operational event log.

**Estate.** **The endpoint half, fully — it needs nothing new.** The two-guest
lane, the `user-logged-on` checkpoint, `HKEY_USERS\<SID>` reads and GroupPolicy
operational-event capture are exactly what WP-9 built and certified on
2026-08-04. **The capture half needs GPMC editor authoring**, which has no cmdlet
surface and therefore needs the LabMS01 GUI console — the same capability that
serves `software_install.py` and the PKI/wireless half of `network_security.py`,
and which is available.

**Classification: (a) runnable now** — the decisive measurement is one
`Backup-GPO` and the endpoint leg is already built. Qualified: the first step is
a manual-queue capture, which costs a person an hour. This module was the one
whose classification was written contingently on the console existing; the answer
came back yes, so it is (a) without the caveat.

**Cheap discriminator.** **`Backup-GPO` a GPMC-authored redirection GPO and grep
the result for `User Shell Folders`.** One backup, one grep. *Hypothesis,
unverified:* there is no such entry — the whole configuration lives in
`fdeploy.ini`. If that holds, `to_registry_settings()` is not a Folder
Redirection writer at any level of detail, and the four option flags plus the
advanced-mode group rules have no representation in the module's output at all.
That reframes Plan 027 WP-2 from "fix the serializer" to "decide whether to write
one," which is a much more useful thing to learn for one command. A zero-cost
companion, offline: call `to_registry_settings()` on an `advanced` policy with
three group rules and observe that exactly one tuple comes back. That is a code
fact, not an oracle result, but it bounds what any lane could certify.

**Cost.** Small for the discriminator plus the offline check. Medium for a full
endpoint lane. Potentially **large** if the discriminator's likely outcome holds
and the module has to be rebuilt around `fdeploy.ini`.

### 3.9 `gpmc_interop.py` — Plan 028

**Produces.** Nothing on the wire. It produces a **prediction about Windows**:
`check_gpmc_interop` returns a `GpmcInteropReport` with two booleans —
`is_gpmc_importable` and `is_gpmc_editable` — plus typed `InteropIssue`s.
`check_backup_importable` makes the converse prediction about a backup manifest.
Because the output is a claim *about* a native tool's behaviour rather than bytes
a native tool reads, it is unusually directly falsifiable: every
`is_gpmc_importable == False` is a testable assertion that `Import-GPO` will fail.

**Oracle.** `Import-GPO` and the GPMC editor, as a **two-sided confusion matrix**,
not a one-sided smoke test. *False negatives* (predicted unimportable, Windows
imports it): take the GPMC-authored backups already committed at
`tests/fixtures/native-gpp-gpmc/*` and `tests/fixtures/sysvol-injection-diagnostics/*`,
load each into Studio via `import_export.py`, run `check_gpmc_interop`, record
the prediction, then on LabMS01 `Import-GPO` against a fresh GPO followed by
`Get-GPOReport -ReportType Xml` and a `Backup-GPO` re-export. *False positives*
(predicted importable, `Import-GPO` throws): author boundary-case GPOs in Studio,
export via `export.py` (the WP-2-evidenced path), and import; assert on the
artifact, not on `$LASTEXITCODE`. **`is_gpmc_editable`** is the harder half: it is
a claim about the GPMC MMC editor, which no cmdlet observes. The nearest
automatable proxy is the import → report → re-backup round trip with GUIDs,
`BackupTime`, `GPODomainController` and version numbers normalised — and that is
**a proxy which must be labelled as one**, because it does not observe the editor.

Boundary owner: `gpo-backup-content` for the import half. `is_gpmc_editable` does
not map cleanly onto any of the five boundaries, which is itself a signal that
the claim as written may not be certifiable.

**Estate. Yes, for the import half.** LabMS01 is frozen and qualified, has the
`GroupPolicy` module 1.0.0.0 and GPMC built in, and the WP-2 lane
(`run-wp2-import.ps1`, `wp2-native-import-20260803230132-8090`, 18/18) already
performs exactly this import → readback → `Backup-GPO` cycle with version-number
and settings comparison. **For `is_gpmc_editable` as literally stated, yes but
only by hand** — observing the editor needs an interactive GUI session on
LabMS01, which the console provides. That makes the claim observable; it does not
make it a lane, because a person opening a snap-in and reporting what it renders
cannot be re-run by the harness.

**Classification: (a) runnable now** for the import predicate, which is the
load-bearing half and is fully automatable. The `is_gpmc_editable` sub-claim is
**(a), capture-only** — no longer blocked, but certifiable only against a
one-shot manual observation. The survey's suggestion is unchanged and is now
sharper for it: either build the round-trip proxy and rename the field to say
what it measures, or drop the claim. Binding a re-runnable certification to a
field whose only honest oracle is a person looking at a dialog is the worst of
the three options.

**Cheap discriminator.** **Run `check_gpmc_interop` over the GPMC-authored backup
fixtures already in the repository. It costs nothing and needs no estate.**

The hypothesis it tests, and the corroboration here is unusually strong:
`_KNOWN_CSE_GUIDS` (`gpmc_interop.py:40–44`) contains
`{3125E937-EB16-4b4c-9934-544FC6D24D26}` labelled "GPP Groups" and
`{A3CC7818-8A30-4e0c-91C5-A4EA4B5A8DAB}` labelled "GPP Registry". **Verified:
those two literals appear everywhere else in this repository as the `clsid`
attribute on the root element of a GPP XML file** — `<Groups clsid="{3125E937…}">`
at `gpp.py:70`, `gpp_adapters.py:79`, `conformance.py:598` and throughout
`tests/test_gpp.py` — not as client-side-extension GUIDs. Extracting every
`MachineExtensionGuids` / `UserExtensionGuids` CDATA block from all seventeen
committed GPMC-authored backups under `tests/fixtures/native-gpp-gpmc/` shows
**neither GUID in any extension list**; Groups appears as
`[{17D89FEC-5C44-4972-B12D-241CAEF74509}{79F92669-4224-476C-9C5C-6EFB4D87DF4A}]`,
which is also what `export.py:42` — the surfaced, WP-2-evidenced writer — emits.

The one place `{3125E937-…}` *does* appear in an extension list is
`tests/fixtures/sysvol-injection-diagnostics/diag-groups/…/Backup.xml`, which is
a **Studio-authored diagnostic fixture, not a GPMC-authored capture**. That is
self-consistency evidence of exactly the kind this programme exists to reject,
and it should not be read as corroboration of the code.

*If the hypothesis holds — and it is still unverified* — then any GPO carrying
GPP `cse_metadata` trips the `unknown_cse_guid` **error** branch
(`gpmc_interop.py:220–228`) and is reported `is_gpmc_importable = False`,
including every GPMC-authored one. The lane settles it in two steps: offline
against committed fixtures (minutes), then `Import-GPO` on the estate to prove
Windows disagrees.

Second, near-free discriminator: `check_gpmc_interop` reports
`is_gpmc_editable = False` whenever *any* warning exists, including every
`studio_validation:*` warning folded in at lines 232–244. Whether an unrelated
Studio validation warning should be able to assert that GPMC cannot edit a GPO is
a question the fixture sweep answers immediately.

**Cost. Small.** The WP-2 lane already does the cycle and the candidate-builder
pattern exists; this is new candidates plus a prediction artifact committed before
the run. The GUI half, if pursued, is medium and needs an estate capability first.

### 3.10 `lifecycle.py` — Plan 028

**Produces.** No wire output. Four in-memory things: `BackupManifest` /
`BackupIndex`, a Studio-invented description of a GPMC backup; `RestorePlan` — a
mode (`overwrite` | `new_gpo` | `import_to_draft`), a target and a `conflicts`
tuple; a lifecycle state machine
`draft → ready → approved → published → archived → deleted`; and a second,
separate `MigrationTable` type described in its own docstring as a
"backup/restore planning view", explicitly distinct from `migration.py`'s parser.

One structural fact determines its measurability: **nothing constructs a
`BackupManifest` from a real backup.** `lifecycle.py` imports only `_GUID_RE`
from `backup.py`; the only caller of `BackupManifest(...)` anywhere in the tree
is `tests/test_lifecycle.py`. There is no bridge from `bkupInfo.xml` /
`Backup.xml` to this type, so the module cannot today be pointed at anything
Windows produced.

**Oracle.** Three separable questions, and only two have a Windows oracle.
*Do the three `RestoreMode` values correspond to real GPMC operations, and is the
surrounding-state loss they imply the loss Windows actually inflicts?* On
LabMS01, author a GPO with links, security filtering, a WMI-filter association
and a non-default ACL; `Backup-GPO`; then run *two* separate restores —
`Restore-GPO` (original identity) and `Import-GPO -TargetName <new>
-CreateIfNeeded` (new identity). After each, read back with `Get-GPO`,
`Get-GPInheritance` on the linked SOMs, `Get-GPPermission -All`, and the
`gPCWQLFilter` / `msWMI-Som` association by `[adsisearcher]`. The observable:
**which of links, ACL, WMI association and GUID survive each operation.** Plan
028 WP-1 asserts links and WMI need an explicit external scope snapshot because
restore does not bring them back; that assertion is measurable and has never been
measured here. Boundary owners: `gpo-backup-content`, `gpo-ad-object-security`,
`som-link-block-inheritance`, `wmi-filter-object-association` — one assertion
each, per boundary-matrix rule 5. *Is the conflict set complete and are its
members real?* Same lane. *Is the state machine right?* No Windows oracle exists;
`draft → ready → approved → published` is a product process, and its oracle is an
operator.

**Estate. Yes for same-domain restore/import semantics** — LabMS01 + LabDC01
support `Backup-GPO`/`Restore-GPO`/`Import-GPO`, and WP-2 has exercised the
import leg end to end against real AD and SYSVOL. **No for the cross-domain
half.** `RestorePlan` exists to model restoring into a *different* domain, and
`MigrationTable` exists for principal translation across domains. The estate is a
single forest (`ad.labdomain.dev`, one DC LabDC01) with no second domain and no
trust. Plan 028 WP-2's cross-domain import/copy gates cannot be run at all today.

**Classification: (a) runnable now** for same-domain restore/import semantics,
which is the part that could be wrong about Windows and is cheaply measurable.
Two named residuals: the cross-domain half is **(b)**, blocked on a second
domain or forest trust; the lifecycle state machine is **(c)**, an operator
decision about process, and should not be dressed up as a Windows question.

**Cheap discriminator.** `generate_restore_plan` emits the conflict
`"WMI filter {name!r} not found in target domain"` on the basis of
`manifest.has_wmi_filter` alone (`lifecycle.py:272–276`) — it never consults a
target domain, because it has no way to. So restoring into a domain that *does*
contain the filter would still report a conflict. One measurement: back up a GPO
with a WMI filter association, restore it into the same domain where the filter
demonstrably exists, and compare Windows' behaviour with the plan's conflict
list. **Unverified hypothesis**, needing one backup and one restore.

Second-cheapest, structural and free: try to build a `BackupManifest` from the
committed native fixture `bkupInfo.xml` under
`tests/fixtures/native-gpp-gpmc/`. That file carries `GPOGuid`, `GPODomain`,
`GPODomainGuid`, `GPODomainController`, `BackupTime`, `ID`, `Comment` and
`GPODisplayName` — note that `ID` (the backup id) and `GPOGuid` are *different*
GUIDs, that `BackupTime` is a naive local-looking timestamp rather than an ISO
timestamp with an offset, and that there is no per-file hash list anywhere in the
format. Whether `BackupManifest`'s fields can be populated at all, and what has
to be invented, is a fifteen-minute answer that says how much of this type
survives contact.

**Cost. Medium.** The restore/scope-survival lane needs a richer authored
topology than WP-2's and four boundary assertions rather than one, but every
piece of harness it needs already exists. The cross-domain half is **large** and
gated on new estate capability.

### 3.11 `publication.py` — Plan 030

**Produces.** Two outputs, and it matters to keep them apart because only one
exists today.

- **A `PublicationPlan`** — an ordered tuple of typed `PublicationStep`s
  (`update_gpt_ini`, `write_registry_pol`, `copy_gpp_xml`,
  `update_nt_security_descriptor`, `associate_wmi_filter`, `update_gplink`), a
  parallel `rollback_plan`, a `risk_level`, and per-step SHA-256 `artifact_ids`
  over content it serialises in passing. This is the live output.
- **A PowerShell script** — `generate_publication_script`. **This branch is dead
  today**: `_WINDOWS_VERIFIED_OPERATIONS` is `frozenset()` (`publication.py:138`,
  verified), so every step is unverified and the function always returns the
  fail-closed refusal script. The mutating branch has never been reachable. It
  would `Set-Content` on `$env:SystemRoot\SYSVOL\domain\Policies\{guid}\gpt.ini`
  with `-Encoding ASCII`, `Copy-Item` a staged `Registry.pol` and GPP XML into
  place; the three AD operations emit a `Write-Warning` and increment
  `$incompleteSteps`.

So the wire, when there is one, is SYSVOL file writes plus a `gpt.ini` version
edit, done by direct filesystem mutation rather than through the GroupPolicy
module.

**Oracle.** *Question 1 — is the plan complete?* This needs no script and no
decision, and is the right first lane. On LabMS01, perform the equivalent
publication by native means (`New-GPO`, `Import-GPO` from an `export.py`-generated
backup, `Set-GPPermission`, `Set-ADObject` for `gPLink`, WMI-filter association),
`Backup-GPO` the result, and diff the resulting `Backup.xml`
`GroupPolicyCoreSettings` and SYSVOL tree against **the set of mutations the plan
names**. Any attribute Windows changed that no `PublicationStep` mentions is a
hole in the plan. Then apply to LabCL01 and confirm the CSEs actually run.
Boundary owners: `gpo-backup-content`, `gpo-ad-object-security`,
`som-link-block-inheritance`, `endpoint-resultant-state`. *Question 2 — if a step
were promoted, would its script produce something Windows processes?* Studio
writes → build the SYSVOL tree the plan describes → `Import-GPO` →
`Get-GPOReport` → `gpupdate` on LabCL01 → observe CSE evidence. That is the WP-2
lane plus the endpoint lane, composed.

**Estate. Yes.** Everything above is LabMS01 + LabCL01 + PowerShell Direct, and
both halves already exist as certified lanes. Nothing new is needed.

**Classification: (a) runnable now** for plan completeness, which needs no prior
decision. There is a genuine **(c)** question stacked on top of it that should be
settled *before* anyone builds a script lane: `docs/live-publication.md` states,
as committed design, "do not directly edit a live `Registry.pol`, `gpt.ini`, GPC
attribute, or GPP XML file over LDAP/SMB" and "use the Windows GroupPolicy module
and GPMC interfaces for production writes." The dead script branch does precisely
what that forbids. Certifying its wire behaviour would be certifying a mechanism
the architecture already ruled out. This survey flags the divergence; it does not
adjudicate it. See §8.1.

**Cheap discriminator.** Two, both very cheap, both unverified hypotheses.

1. **The plan emits no step that updates `gPCMachineExtensionNames` /
   `gPCUserExtensionNames`.** Verified structurally: `generate_publication_plan`
   emits the six operations listed above and nothing else; the three CSE-GUID
   constants defined at `publication.py:126–128` for exactly this purpose are
   **never read anywhere in the file**, and `gPCMachineExtensionNames` /
   `gPCUserExtensionNames` appear **nowhere in `src/`** at all. A GPO whose SYSVOL
   contains a `Registry.pol` but whose extension list is empty is not processed by
   any client. The measurement: publish by the plan's step list, then read
   `gPCMachineExtensionNames` and `gpupdate` on LabCL01. Sub-hypothesis, free to
   check: two of those three constants are the GPP XML `clsid` values, **the same
   suspected confusion as §3.9** — one measurement informs two modules.
2. **`update_gpt_ini` treats `Version=` as a flat integer** —
   `$expectedVersion = [int]$currentVersion + 1` (`publication.py:499`).
   `gpt.ini`'s `Version` is a packed 32-bit field; `docs/live-publication.md`
   itself says "User changes increment the upper 16 bits and computer changes
   increment the lower 16 bits", and this repo's own WP-2 finalizer already
   unpacks the corresponding `Backup.xml` version numbers as two 16-bit halves
   (`finalize_wp2_import_run.py`: `packed_machine == (dsa << 16) | sysvol`). If
   the hypothesis holds, a user-side-only publication increments the computer
   counter and clients never reprocess the user side. One measurement: author a
   user-side change through GPMC, read `gpt.ini` before and after, compare the
   delta against `+1`.

Also worth one line in the lane: the script hard-codes
`$env:SystemRoot\SYSVOL\domain\Policies\…` while declaring
`#Requires -Modules GroupPolicy, ActiveDirectory` and calling `Get-GPO` — it
assumes it runs on a domain controller. Whether that is intended belongs to the
(c) decision, not to a lane.

**Cost. Small to medium.** The plan-completeness lane composes two
already-certified lanes and needs a comparison normaliser rather than new
transport. The script lane is medium and should not be started until the (c)
question is answered.

### 3.12 `publisher.py` — Plan 030

**Produces.** No wire output. A **gating decision**: `evaluate_publication(plan,
profile, approval)` runs six gates — capability, approval, scope, blast radius,
time, interop, RSOP — and returns a `PublisherDecision` with `approved` true only
if every gate passes, plus `PublisherProfile`, `ApprovalRequest` (multi-approver
counting, expiry, self-approval refusal in `approve_request`) and an append-only
`PublicationAuditTrail`. Its observable output is a boolean and a list of gate
results.

**Oracle. Not Windows.** Nothing here is a claim about GPO wire behaviour. The
claim it makes is: *this gate set stops the threats enumerated in
`docs/publisher-threat-model.md`.* The oracle is the review Plan 030 Phase A
itself demands — "External threat-model and privilege review before any write
grant" — and Plan 031 WP-3's publisher-security and red-team commissions.

There is exactly one measurable sub-question hiding inside it, worth extracting
rather than folding into the review: **does `_dn_within_any_scope`'s
string-suffix nesting agree with real AD containment?** Oracle: `[adsisearcher]`
on LabMS01 over an OU tree containing DNs with escaped commas
(`OU=Sales\, EMEA,…`), differing case, and cross-domain DNs; compare Studio's
verdict against AD's actual parent chain. Boundary owner:
`som-link-block-inheritance`. That is class (a) and small.

**Estate.** For the DN sub-question, **yes** — `[adsisearcher]` is measured
available and LabDC01 can hold an awkward OU tree. For the module as a whole,
**the oracle is not an estate capability at all.** No lab produces it. It
requires a security reviewer with the threat model in hand. Cross-lineage
adversarial review inside this project is available but is not the same thing as
the external review Plan 030 Phase A specifies, and the survey should not let one
stand in for the other.

**Classification: (c) the oracle is a person.** The judgement: *does this gate
set, as implemented, satisfy the required controls in
`publisher-threat-model.md`, and which rows does it not address?* That is a
security review against a named artifact, not an open-ended opinion — which is
what makes it schedulable.

**Cheap discriminator.** **Take the threat model's "Required controls" column and
check each row against a named gate.** It is a table with eighteen rows
(`publisher-threat-model.md:29–46`, verified), and one pass over it surfaced two
rows with no corresponding mechanism.

**Both were recorded here as unverified hypotheses. Both have since been
reproduced by execution** in the Plan 030/032 shape assessment run alongside this
survey. They are stated here as what they now are — demonstrated behaviour of the
current code — while noting that *what to do about them* is still the security
review's call, not this survey's.

- *"Artifact swapped after approval → Canonical digest in every approval
  signature; publisher recomputes it."* **Control absent.** `ApprovalRequest`
  binds to `plan_id` only, and `plan_id` is `f"plan-{uuid.uuid4().hex[:12]}"`
  (`publication.py:141–142`) — a random identifier with no relationship to plan
  content. No digest of plan content exists anywhere; step artifacts *are*
  content-hashed, but those digests are never checked against an approval, and
  the most dangerous plan content — the `gPLink` target DN, carried as free text
  in `step.detail` — has no artifact and therefore no hash at all. Reproduced:
  approve a benign plan linked to `OU=Servers`, then `dataclasses.replace` the
  steps to relink to `OU=Domain Controllers` keeping `plan_id`, and **the full
  seven-gate pipeline returns `approved: True` with no blocking gates**.
  `_blast_radius_gate` did not catch it either, because `plan.risk_level` is a
  stored field computed from the original GPO and is never re-derived.
- *"Stolen author session self-approves → `author != approver`."* **Control
  exists but the gates bypass it.** `approve_request` does raise on
  `approver == requested_by`, and that is tested. But `_approval_gate` never
  compares `approved_by`/`approvers` against `requested_by`, `ApprovalRequest.
  validate()` does not either, and `run_publisher_gates` takes **no principal
  parameter at all**. Reproduced: construct an `ApprovalRequest` with
  `requested_by="alice"`, `approvers=("alice",)`, `state="approved"` — the shape
  any persistence layer would rehydrate — and validation reports no issues while
  the pipeline approves. **The prohibition is enforced on the *transition*, never
  on the *state*, so any path that reconstitutes an approval from storage skips
  it.** Corroborating: all four of `_approval_gate`'s content-binding refusal
  branches (`publisher.py:472, 483, 491, 499`) are uncovered by the test suite.

Third, verified structurally here and confirmed there: `_rsop_gate` fails only on
`risk_level == "critical"` (`publisher.py:641–649`), but `_assess_risk` returns
only `low`/`medium`/`high` (`publication.py:186–191`) — so as reached from
`generate_publication_plan`, that gate cannot fail.

Fourth, which this survey raised only as "worth asking about" and the assessment
then demonstrated: `profiles_for_actor` matches `p.profile_id == actor`, and
`PublisherProfile` has no principal field, so **there is no way to express "alice
holds profile p1"** — `effective_capabilities('alice')` returns nothing while
`effective_capabilities('p1')` returns seven capabilities. A missing field rather
than a broken design, but it is why the capability gate cannot currently be
evaluated against a real principal.

**None of this changes the classification.** These are inputs the reviewer should
have in hand, not a substitute for the review: the question remains which of the
eighteen rows the gate set is *meant* to address and at what strength, and that
is a person's call.

**Cost. Small as a review** — one reviewer, one sitting, against an existing
eighteen-row table — and **small** for the DN-containment measurement. Large only
if the review finds the model needs restructuring, which is the outcome
`domain-layer-status.md` tells us to budget for.

### 3.13 `certification.py` — Plan 031

**Produces.** No wire output. A **certification verdict**:
`evaluate_certification(suite, results)` maps `ConformanceResult`s onto a
`CertificationLevel` of `none` | `basic` | `standard` | `full`, plus
`ParityEvidence` / `EvidencePortfolio` types. `default_certification_suite()`
supplies the case list across eight `ConformanceCategory` values.

**Oracle. Not Windows, and this is the crux.** `certification.py` is a *second*
evidence vocabulary sitting next to one this project has already built, used and
certified: `src/gpo_studio/oracle_evidence.py`, the
`windows-oracle-manifest-v1.schema.json` contract, `boundary-matrix.md`'s five
boundary owners, and the four-state verdict
`pass` / `fail` / `unsupported` / `inconclusive`. Every Plan 033 certification
runs through that path.

The oracle is therefore an operator judgement: *should this module exist, and if
so, what is its relationship to `oracle_evidence.py`?* The two disagree in ways
that are not cosmetic, and all three are verified as code facts:

- `ConformanceResult` is `passed: bool` plus `skipped: bool`
  (`certification.py:83–91`). There is **no `inconclusive` and no
  `unsupported`**, and the string `boundary_owner` does not appear in the file at
  all. The boundary matrix's evidence-state rule exists precisely because
  "`unsupported` is an explicit capability downgrade, not a waived failure" and
  "`inconclusive` means the run cannot support a claim." A two-state model cannot
  express either, and `evaluate_certification` counts a skipped case as
  not-passed — collapsing "we could not measure" into "it failed", the exact
  conflation WP-6B's three-outcome rule forbids.
- Rule 5 of the boundary matrix requires one assertion per boundary and forbids a
  content comparison standing in for AD or endpoint evidence.
  `ConformanceCategory` is a feature taxonomy, not a state boundary.
- `ParityEvidence.evidence_type` admits `"round_trip"` as a first-class evidence
  type, and `_case`'s default `oracle` parameter is the literal string
  `"round_trip"` (`certification.py:399`), which the default suite then carries on
  fourteen cases including required ones.

*Unverified, and the discriminator is written to confirm rather than assume it:*
that `evaluate_certification` will return `certification_level="full"` on a
portfolio in which no external oracle ever ran. That is not a bug report; it is
the reason the classification is (c). A module whose default suite treats
Studio-reads-what-Studio-wrote as certifying evidence is a policy artifact, and
policy is settled by the operator.

**Estate. Not applicable** — no estate capability is involved. What already
exists is the comparison material: eleven-plus certified RSOP verdicts under
`docs/plan-033/wp6-evidence/` and `wp9-evidence/`, plus WP-0/1B/2/3 manifests, so
any decision can be tested against real verdicts rather than in the abstract.

**Classification: (c) the oracle is a person.** The judgement: *is
`certification.py` the parity certification framework, or is
`oracle_evidence.py` — and if the former, what must it adopt from the boundary
matrix and the four-state vocabulary before a `CertificationResult` may be
published?* A defensible third answer is "delete it and let Plan 031 consume Plan
033's manifests directly." Plan 031's own status line already concedes the
premise is unmet: "a certification model is not a certification."

**Cheap discriminator.** **Try to express one real, already-certified Plan 033
verdict in `certification.py`'s types, and see what is lost.** Take
`docs/plan-033/wp6-evidence/verdict-rsop-observe-20260805045139-3731.json` — the
run that *demonstrated* the deny-on-READ gap before WI-040 fixed it — and the
WI-043 `unevaluable` case, and attempt to encode both as `ConformanceResult` +
`ParityEvidence`. It is an afternoon at a keyboard, needs no estate, and produces
a concrete list of what the type system cannot say. Expectation, **unverified**:
`unevaluable`/`inconclusive` and `boundary_owner` are the first two casualties.

**Cost. Small** to produce the decision input. The consequences of the decision
could be large, but that is a rewrite, not a lane.

### 3.14 `hosting.py` — Plan 032

**Produces.** No wire output, and — unlike every other module here — **no output
that any running system consumes.** Pure functions over frozen dataclasses:
`HostedConfig.validate()` (fail-closed startup rules: loopback bind, non-empty
TLS paths, non-SQLite `database_url`, non-empty `trusted_proxy_addresses`,
`admin_group`, `session_secret_path`, minimum session age),
`DeploymentConfig.validate()`, `AuthenticatedIdentity`, `SessionConfig`,
`check_authorization()` (deny-by-default role/operation/scope matrix),
`can_self_approve()`, and `AuditEvent` / `filter_audit_events`.

**Verified, and it determines everything below: `api.py` contains zero references
to `hosting`, `HostedConfig` or `check_authorization`; nothing in `src/` or
`scripts/` constructs a `HostedConfig`; and no module anywhere parses `Forwarded`
or `X-Forwarded-*` headers.** `trusted_proxy_addresses` is validated non-empty
and then read by nothing. The module is a specification written in Python, not a
component.

#### The operator's ruling, and why it changes the question

An earlier draft of this survey framed Plan 032 as parked pending a decision on
whether the hosted control plane is wanted at all. **That framing is wrong and
the operator has said so.** Verbatim:

> *"if the shape just needs hardening keep it; if it will need what amounts to a
> redesign anyway, discard. This won't be deployed anywhere until we (you, me,
> sol) declare it fit for purpose, so we don't need to make it absolutely
> compliant"*

So the question is not *whether the hosted control plane is being built*. It is
**whether the landed code is a foundation worth hardening or a thing to
discard**, and the standard is explicitly not production-readiness and not full
compliance with Plan 032's acceptance gates. The standard is:

> **Would an implementer starting from this code get there faster than from a
> blank file?**

That reframing matters for this survey specifically, because it means the
oracle-and-estate analysis below is **not the decision input.** Everything in it
— the pen test, the real startup, the installer matrix, the external review — is
about compliance with gates that the operator has just said are not the bar yet.
Those remain the right oracles *eventually*, and they are recorded here so the
eventual scope is known. They are not what settles the question in front of us.

**A separate shape assessment against exactly that standard was run alongside
this survey, and its verdict is HARDEN.** Its reasoning is summarised here
because it settles the disposition; it covers `hosting.py`, `publisher.py` and
`publication.py` together, so it also bears on §3.11 and §3.12.

> All three modules are a sound foundation whose defects are **additive, not
> structural** — the expensive judgement (the role/operation matrix, the gate
> decomposition, the identity seam these plug into) is already made and pinned by
> 153 tests.

Four points from it are worth carrying into any plan built on this survey:

1. **Authorization is a seam that exists and is simply never called.**
   `check_authorization` is the right signature with the right semantics —
   union-over-grants, deny by default, not first-grant-wins, which is a thing
   implementers get wrong. "Nothing calls it" is a *wiring* fact, not a shape
   fact; the design has an obvious place to put authorization and there is no
   unpicking to do.
2. **The identity gate has a place to be enforced, and it is not in
   `hosting.py`.** `api.py`'s `_identity(actor)` is the sole construction point
   (38 call sites) and `store.py`'s `_resolve_actor` the sole consumption point,
   so "in hosted mode, `body.actor` is ignored" is a change to one function — or
   zero call sites if `_identity` becomes a FastAPI dependency. The one real
   coherence defect is that `AuthenticatedIdentity` does **not** satisfy the
   `Identity` protocol `identity.py` already defines (verified by execution:
   `isinstance(...) = False`, no `.actor`, no `.is_trusted`). That is a fifteen-
   line adapter, not a redesign.
3. **The gate pipeline's defects are "gates read declared fields instead of
   derived facts" — a bug class, not a shape.** Fixing it needs a content digest
   on the plan and one extra parameter on the gate functions; `canonical.py`
   already supplies the digest primitive.
4. **Plan 032 is about 5% implemented — 0 of 8 required outputs, 0 of 9
   acceptance gates enforced — and volume is not the criterion the operator
   set.** The 5% that exists is design judgement rather than plumbing, and the
   missing 95% is plumbing the shape accommodates unmodified.

**The one scoped deletion** the assessment recommends: `HostedConfig`'s inert
policy flags (`csrf_enabled`, `hsts_enabled`, `rate_limit_per_minute`) and
`AuthenticatedIdentity`'s dead `groups` / `is_admin` fields. They name controls
no code implements or reads, and unlike the rest of the module they carry no
design value.

**The one place the skipped-review ordering violation genuinely bites**, named
precisely: WP-1's ADR was supposed to choose between IIS+HttpPlatformHandler, a
Windows Service behind IIS/ARR, and a non-Windows host behind an OIDC proxy — and
**`HostedConfig` has silently made that choice**, putting `tls_certificate_path`
and `tls_key_path` in the *application's* config while also mandating a loopback
bind. The app holds a certificate it will never present. That gives a clean seam
inside the module: **the authorization half is topology-independent and should be
kept unconditionally; the config half should be frozen until the ADR exists.**
Which topology is intended is an open operator question — see §8.7.

One finding from the assessment that is not about Plan 032 at all and is worth
acting on independently: `scripts/check_coverage.py` enforces a total floor, and
`hosting.py` sits at **99% branch coverage** precisely because a module of pure
validation dataclasses with no I/O is trivially coverable. **The code furthest
from anything an operator can reach is the code that most flatters the headline
number, and deleting it would lower the product's reported coverage.** That is
`domain-layer-status.md`'s argument made concrete. The coverage floors should
exclude unsurfaced layers, so that surfacing a layer is what earns it a floor.

#### The eventual oracles, for scope rather than for the decision

Not Windows tooling in any form — no GPO, no SYSVOL, no CSE. Plan 032's own
acceptance gates name what would settle it, and each names a different oracle:

- *"Client-controlled identity/forwarding headers never affect actor identity,
  including through direct listener access"* — a **penetration test against a
  running deployment**. It cannot be evaluated against this module because the
  module contains no header handling.
- *"Hosted mode cannot start anonymously, on SQLite, with an untrusted public
  host/proxy, or without TLS"* — a **real startup on a real host**, IIS
  terminating TLS, an authenticated principal propagating, PostgreSQL behind it.
  `HostedConfig.validate()` returning errors is not the same claim as a service
  refusing to start.
- *"Install, repeat install, upgrade, interrupted migration, rollback, repair,
  certificate rotation, backup/restore, and uninstall pass on the supported
  Windows matrix"* — an **installer matrix on real Windows Server builds**.
- *"Independent review has no unresolved critical/high findings"* — an **external
  architecture/security/operations review**, which Plan 032's own "REVIEW AND
  REFINE — REQUIRED" section says must happen *before implementation*.

**Estate. No, and not partially.** The estate is three guests with **no network
at all** — `environment-spec.md`: "the guests have no network; the transport
reaches them through the hypervisor." That is the isolation invariant, and it is
precisely what a reverse-proxy trust boundary, an OIDC issuer, a TLS certificate
chain and a spoofed-header test all require. There is no IIS, no PostgreSQL, no
identity provider, no certificate authority. Qualifying such an environment would
be a new environment under `environment-spec.md`'s freeze rules — its own
qualification run, its own row — not an extension of this one. But naming the
missing capability would be misleading as the headline, because **the more
binding constraint is upstream: there is nothing to deploy.** The gates are about
a service that does not exist in code. Under the operator's ruling that is not a
reason to park the module — it is the observation the harden-or-discard judgement
has to weigh.

**Classification: (c) the oracle is a person — and that person has now ruled.**
The judgement was *is this code a foundation worth hardening, or does it need
what amounts to a redesign anyway?*, and the answer is **harden**. It was never
(b): no estate capability was missing, because no estate capability was the bar —
the assessment read code and reached a verdict without touching Windows.

What follows from "harden" for *this* survey is narrow, and it is worth being
explicit so the module is not accidentally promoted. Hardening work is
**self-consistency work**. Per `domain-layer-status.md` §4 and AGENTS.md, none of
it makes this layer proven; identity propagation and spoof resistance are lab
items and the plan says so. So `hosting.py` stays exactly where it is in the
capability matrix, and the follow-on evidence work becomes **(b)** against a
hosted substrate the qualified estate does not have and should not acquire
without its own freeze. That substrate is downstream of the hardening, not a
blocker on it, and it is still the largest single item in this survey.

**Cheap discriminator, and it is the survey's contribution to the assessment.**
**Ask what a `HostedConfig` currently prevents.** The measurement is one grep and
it is already done, above: nothing constructs a `HostedConfig`, nothing calls
`check_authorization`, and no code path reads a forwarded header. So **the
strongest safety claim in Plan 032's acceptance gates — that client-controlled
identity headers can never affect actor identity — is not merely unverified, it
is unstated in code.**

The assessment corroborated it independently — `rg` over `src/` for
`x-forwarded|forwarded|REMOTE_USER|LOGON_USER|proxy_headers|root_path` returns
exactly one hit, a prose word in `hosting.py`'s own docstring — and then read it
the favourable way: **there is nothing to unpick because there is nothing there.**
The deny-by-default authorization matrix and the fail-closed startup rules are
the parts an implementer would otherwise derive from scratch; the missing header
handling is *additive* rather than a contradiction of the shape, and it has a
single existing enforcement point to be added at. That reasoning is what turned
the fact into a "harden" rather than a "discard."

**Cost. Small** to produce the decision input, and it is produced. Everything
after a "harden" verdict is **large** — an ADR, a prototype of two hosting
topologies, a new qualified environment, an installer matrix and an external
review — and remains the largest single item here, which is why it should not be
sequenced against the GPMC-parity work regardless of the verdict.

---

## 4. Second tier — surfaced code inside Plan 028's gates

`backup.py`, `migration.py` and `report.py` are **surfaced 1.0 code reachable
from `api.py`**, attributed to Plans 002/003/007/012/013/014 rather than to Plan
028. They are outside the `domain-layer-status.md` set and outside the fifteen.

**The distinction changes their urgency, and it changes it upward.** A defect in
an unproven draft is *latent*: no operator can reach it, so it costs nothing
until the layer is surfaced. A defect in these three is *live* — an operator can
exercise it through the API today. `domain-layer-status.md` is silent on
surfaced-but-Windows-unverified code except through the capability matrix, which
calls such modules "live authoring surfaces whose output no independent Windows
oracle has yet checked." That is a stronger reason to measure them, not a weaker
one. They are separated here because they are a different *kind* of thing, not
because they are lower priority.

The operator should decide whether they fold into the same programme. This survey
recommends that they do — one of them yields what may be the cheapest and most
likely-to-fire discriminator in the whole exercise.

Worth recording alongside: **`export.py`** does the actual native GPMC backup
emission, *is* surfaced, and *does* carry WP-2 Windows evidence. Several first-
tier modules are best understood as "the parts of Plan 028's scope that
`export.py` did not already earn evidence for."

### 4.1 `migration.py` — GPMC migration table parsing

**Produces:** a reader only. `parse_migration_table(path)` parses a `.migtable`
XML file into `MigrationTable(entries, domain)`, reading namespace
`http://www.microsoft.com/GroupPolicy/Types` (`migration.py:13`) and the element
path `Mapping / Source|Destination / Identifier / Sid|Name`. `apply_migration`
rewrites **security-filter principals and SIDs only**. There is **no writer**, so
Studio cannot produce a table for `Import-GPO -MigrationTable`. Surfaced at
`api.py:3130` on the backup-import endpoint.

**Oracle:** GPMC writing a migration table, then Studio reading it. Author one on
LabMS01 via the GPMC COM API — `$gpm = New-Object -ComObject GPMgmt.GPM;
$mt = $gpm.CreateMigrationTable(); …; $mt.Save($path)` — which needs no GUI, and
additionally populate one from a real backup with
`GPMMigrationTable.AddFromBackup()`. Feed the bytes to `parse_migration_table`.
Divergence = a parse error, or a parse that yields zero entries. Boundary owner:
`gpo-backup-content`.

**Estate: yes.** LabMS01 has GPMC and the GroupPolicy module; the COM object is
scriptable over PowerShell Direct.

**Classification: (a) runnable now.**

**Cheap discriminator — possibly the cheapest and most likely-to-fire measurement
in the survey.** Produce **one** GPMC-authored `.migtable` and parse it.

*Hypothesis, unverified:* the namespace and element shape in the code are
Studio's own invention. Verified as far as the repository can go: **every
`.migtable` in the tree is hand-written by this project** in the
`…/GroupPolicy/Types` namespace with `<Source><Identifier><Sid>` children — the
only files matching are `tests/test_migration.py` and `tests/test_lifecycle.py`,
both of which build the XML as inline strings; **no GPMC-authored migration table
exists anywhere in the repository.** The suspected real format is a
`MigrationTable` root in the `…/GPOOperations/MigrationTable` namespace with
`<Mapping><Type/><Source/><Destination/></Mapping>` where `Source` and
`Destination` are plain text rather than `Identifier/Sid` sub-elements. **That
recollection is not verified against Microsoft documentation and is the reason
this measurement is ranked first.**

What *is* verified is the failure mode if the hypothesis holds:
`parse_migration_table` iterates `root.iter(f"{{{_GPMC_NS}}}Mapping")`, so a
namespace mismatch yields zero matches, returns an empty `MigrationTable`
**without raising**, and `apply_migration` then returns the GPO unchanged because
`not table.entries`. **That is a silent no-op on a live API endpoint.** One file
settles it. This is the WP-3 `secedit` situation exactly: the first time a native
producer's output meets the parser.

Second, free: `apply_migration` touches only `security_filters`, while Plan 028
WP-2 requires migration across "security filters, ACL trustees, GPP group
targets/members, group ILT predicates, and all later principal-bearing adapters."

**Cost: small.** Minutes of estate time; the harness pattern exists.

### 4.2 `backup.py` — GPMC backup reader

**Produces:** a reader. Parses `bkupInfo.xml` and `Backup.xml` from a GPMC backup
directory, enumerates CSE extensions and their file references, and preserves
unsupported CSE content as opaque blobs with hashes. Surfaced via `api.py:48` and
`import_export.py`.

**Oracle:** `Backup-GPO` output across the CSE matrix, not just the families
already fixtured. Author GPOs on LabMS01 covering security settings, scripts,
software installation, folder redirection and IE maintenance; `Backup-GPO`;
parse; assert that every `GroupPolicyExtension` element and every
`FSObjectFile`/`FSObjectDir` path is accounted for as known or explicitly opaque.
Boundary owner: `gpo-backup-content`.

**Estate: yes.** This is the WP-2 lane's existing shape.

**Classification: (a) runnable now.**

**Cheap discriminator:** count. For each committed native fixture and each newly
authored family, compare the number of `GroupPolicyExtension` elements in
`Backup.xml` against the number of `cse_metadata` entries Studio produced, and
the extension-list GUID pairs in `MachineExtensionGuids` / `UserExtensionGuids`
against what Studio records. Plan 028's acceptance gate is "reports account for
every byte-bearing adapter or mark it opaque"; a count mismatch is a silent-loss
result and needs no semantic comparison.

**Cost: medium** — the authoring breadth is the cost, not the harness.

### 4.3 `report.py` — inert-text review report

**Produces:** a deterministic plain-text report (`policy_report(gpo)`) with fixed
sections: identity, two canonical SHA-256 digests, validation issues, registry
settings, links, security filters, WMI filter, GPP counts, preserved extension
content. No XML, no HTML, no wire format. Not a `Get-GPOReport` equivalent and
does not claim to be. Surfaced at `api.py:123`.

**Oracle:** for Plan 028 WP-4 and Plan 031's "no setting may disappear for lack
of a renderer" gate, `Get-GPOReport -ReportType Xml`. Import a GPMC-authored GPO
into Studio, generate both, and compare the **setting inventory** — counts and
identities, not prose. A setting present in the Windows report and absent from
Studio's is a divergence.

**Estate: yes** — `Get-GPOReport` is already exercised by the WP-1B and WP-0 lanes.

**Classification: (a) runnable now.** Report *wording* quality is (c) and is not
worth a lane.

**Cheap discriminator:** the GPP section renders as
`f"{collection.scope}: {len(collection.groups)} group item(s), {len(collection.registry)} registry item(s)"`
— two families only, by count. Every other GPP adapter Plan 024 landed (Drives,
ScheduledTasks, Services and the rest of `ADAPTER_KEYS`) is invisible in the
report. **Unverified as a Windows question**, but comparing against
`Get-GPOReport` for a GPO carrying a Drives or Services item answers it in one
run.

**Cost: small** — a report differ over an existing lane's artifacts.

---

## 5. Summary

| Module | Plan | Class | Cheap discriminator | Cost |
|---|---|---|---|---|
| `security_template.py` | 025 | **(a)** (Kerberos → b; SDDL sections gated by the WI-038 decision; read direction needs a manual capture) | Feed a **native** GPMC-authored `GptTmpl.inf` to `parse_security_template` and count `unknown_lines` — the read direction has never been run | S / M |
| `object_security.py` | 025 | **(a)** | `secedit /validate` one emitted `[Registry Keys]` line — the module writes `path = 2,"SDDL"`, the corpus says the wire is a bare quoted-CSV line | S / M / M–L |
| `network_security.py` | 025 | **(a)** capture-backed for PKI/wired/wireless; firewall/IPsec automatable *if* NetSecurity is present | `Get-Module -ListAvailable NetSecurity` + `Get-NetFirewallRule -PolicyStore` on LabMS01 — decides re-runnable lane vs one-shot capture | S / M / L |
| `policy_families.py` | 025 | **(a)** (Kerberos family → b) | Build the WP-3 candidate from `to_template_entries()` with distinctive values and rerun the certified lane unchanged | **S** |
| `script_policy.py` | 026 | **(a)** (reference capture is a manual booking; both oracle legs automate after it) | Hex-dump a native `scripts.ini`: BOM? CRLF? `[Policy]` section? — the module returns a `str` joined with `\n` | S / M |
| `artifact_store.py` | 026 | **(c)** — extension policy, unverified `signer`, EICAR-only "malware scanning", hash-as-key dedupe | Store two byte-identical files with different names; show the second returns the first's metadata with no provenance row | S (review) |
| `software_install.py` | 027 | **(c)** — should Studio write Software Installation at all, given it cannot produce `.aas` | GPMC-deploy one MSI, `Backup-GPO`, list the backup: is `Machine\Applications\*.aas` there, and does `Import-GPO` reconstruct it? | M / L |
| `folder_redirection.py` | 027 | **(a)** (capture is a manual-queue item; endpoint leg already built) | `Backup-GPO` a GPMC-authored redirection GPO and grep for `User Shell Folders` — expect `fdeploy.ini` instead | S / M / L |
| `gpmc_interop.py` | 028 | **(a)** (`is_gpmc_editable` capture-only — rename it or drop it) | Run `check_gpmc_interop` over the committed GPMC-authored fixtures; `_KNOWN_CSE_GUIDS` may hold GPP XML `clsid` values, not extension GUIDs | S |
| `lifecycle.py` | 028 | **(a)** (cross-domain → b; state machine → c) | Restore a GPO whose WMI filter exists in the target domain and see whether Windows agrees with the unconditional conflict; separately, try to build a `BackupManifest` from a real `bkupInfo.xml` | M / L |
| `publication.py` | 030 | **(a)** for plan completeness (script branch gated on a **(c)** architecture decision) | No `gPCMachineExtensionNames` step and the three CSE-GUID constants are never read; `update_gpt_ini` does `+1` on a packed 32-bit field | S–M |
| `publisher.py` | 030 | **(c)** — security review against `publisher-threat-model.md` | Two rows now **reproduced by execution**: approval binds a random `plan_id` not a content digest, and self-approval is refused on the transition but not on the state | S (review) |
| `certification.py` | 031 | **(c)** — operator decision vs `oracle_evidence.py` | Encode one certified WP-6/WI-043 verdict in its types and list what is lost; the default suite's `oracle` is literally `"round_trip"` | S |
| `hosting.py` | 032 | **(c)** — **ruled: HARDEN.** Defects additive, not structural; config half frozen until the WP-1 ADR exists | Nothing constructs a `HostedConfig`, nothing reads a forwarded header — the headline safety gate is unstated in code | S now / L after |
| `migration.py` | 013, surfaced | **(a)** | Produce **one** GPMC-authored `.migtable` via `GPMgmt.GPM` COM and parse it; suspected wrong namespace and element shape, failing silently to an empty table | S |
| `backup.py` | 002/003/007/014, surfaced | **(a)** | Count `GroupPolicyExtension` elements and extension-list GUID pairs in `Backup.xml` against Studio's `cse_metadata`, across CSE families not yet fixtured | M |
| `report.py` | foundation, surfaced | **(a)** | Compare the setting inventory against `Get-GPOReport -ReportType Xml`; the GPP section renders only Groups and Registry counts | S |
| `rsop.py` | 029 | — | **Excluded** — the worked example (§2) | — |

**Classification split.**

- **The fourteen first-tier modules: (a) 9, (b) 0, (c) 5.**
- **The three second-tier modules: (a) 3.**
- **All seventeen: (a) 12, (b) 0, (c) 5.**

**No module is primarily blocked on estate capability.** That is a stronger
result than the survey expected when it started, and it turns on one answer:
`network_security.py` was the single **(b)**, and confirming the LabMS01 GUI
console moved it to (a). Two things follow, and neither should be over-read.

First, **(b) survives as a named residual on several modules** —
`security_template` and `policy_families` (`Kerberos Policy`, needing a lane that
executes on LabDC01), `lifecycle` (the cross-domain half, needing a second domain
or forest trust), and `hosting` (after a "harden" verdict). The estate can start
all of this work; it cannot finish several pieces of it.

Second, and more important for planning: **"(a) runnable now" is doing two
different amounts of work in that count.** Five of the nine — `security_template`
(read direction), `network_security` (PKI/wired/wireless, and possibly all of
it), `script_policy`, `folder_redirection`, and `gpmc_interop`'s
`is_gpmc_editable` — reach their oracle through a **manual capture** that a
person books once and that no harness can re-run. The remaining four —
`object_security`, `policy_families`, `lifecycle`, `publication` — are
automatable end to end. A plan that treats those two groups as interchangeable
will under-budget the first. The running order below front-loads the second
group for exactly that reason.

---

## 6. Recommended running order

Two principles shape it. **Cheapest-and-most-likely-to-fire first**, because a
survey's job is to point at the measurement that changes the plan. And
**automatable before capture-backed**, because a manual capture costs an operator
booking that cannot be re-run, so it should be enumerated and batched rather than
taken one module at a time — items 4, 5 and part of 6 below are all manual-queue
captures and are candidates for a **single sitting** on the WI-022 model, which
settled five questions in one visit because they were listed in advance.

**1. `policy_families.py` through the existing WP-3 lane.** It is the only module
in the set where a currently green, already-certified lane can be pointed at
unproven code **by editing one file** — no new host, tool, comparator or
finalizer. It tests three of the four categories `domain-layer-status.md` names
as the ones that have been wrong every time (units, key names, omission rules) on
a lane whose result is already trusted. If it passes, that is a real
certification; if it fails, it fails cheaply and specifically.

**2. `migration.py` — one GPMC-authored `.migtable`.** Minutes of estate time,
and it is *surfaced* code on a live API endpoint. The suspected failure mode is
silent: a parser that returns an empty table rather than raising. Same shape as
the WP-3 `secedit` finding — a parser that has only ever read its own project's
fixtures, meeting a native producer for the first time.

**3. `gpmc_interop.py` — the offline fixture sweep, then `Import-GPO`.** The
offline half costs nothing and needs no estate at all, and the hypothesis is
checkable against ground truth already committed here. If it fires it immediately
implicates `publication.py`'s CSE-GUID constants too, so one measurement informs
two modules.

**4. `script_policy.py`'s encoding and preamble check.** One `Backup-GPO` and a
hex dump. This is the WP-3 finding's exact shape — encoding, line endings,
preamble — in a module no external oracle has ever read, and the module returns a
`str` joined with `"\n"` with no BOM anywhere in it. **Manual capture** — Scripts
has no cmdlet authoring surface.

**5. `folder_redirection.py`'s "is there an `fdeploy.ini`" capture.** One backup
and one grep, and the answer changes Plan 027's *scope* rather than fixing a line.
Learning that a module addresses the wrong artifact entirely is worth far more
than learning a value name is misspelled, and it costs the same. **Manual
capture** — and it should be booked in the same sitting as item 4, because both
are one GPMC gesture followed by one `Backup-GPO`.

**6. `network_security.py`'s NetSecurity probe.** Two commands, fully
automatable, and worth running before any capture is booked: it decides whether
the firewall and IPsec halves get a *re-runnable* lane or join the manual queue,
which changes what the sitting in items 4–5 should cover. If they join the queue,
add them to it; the PKI/wired/wireless captures belong there in any case.

**7. `publication.py` — plan completeness against a native publication.** It
composes two already-certified lanes and needs no new transport, and the
missing-extension-list hypothesis is the kind of omission that makes a GPO inert
rather than wrong — the failure mode offline tests structurally cannot see. **Run
the plan-completeness lane; do not build the script lane until the
`live-publication.md` divergence is adjudicated.**

Then a second wave: `object_security.py`'s shape probe — deliberately after the
others, because it is entangled with the open **WI-038** decision and the
measurement is most useful when the person taking that decision has it in hand;
`lifecycle.py`'s restore/scope-survival lane (medium, four boundary assertions,
real value); `backup.py`'s CSE-family breadth (medium, mostly authoring effort);
and `security_template.py`'s native read, which belongs in the same manual
sitting as items 4–5 if that sitting has not already happened.

**The five (c) items should be scheduled as decisions, not as lanes, and should
not sit in the same queue as the measurements.** `hosting.py` is the worked
example of why: its decision was taken by reading code, needed no estate time at
all, and came back **harden** with a concrete work list — none of which any lane
could have produced. `certification.py` is the same shape and needs about an
afternoon of preparation to produce its decision input. Putting either in the
lane queue would let them consume estate time they do not need and cannot use.

Note that the harden ruling puts follow-on work into the queue —
`AuthenticatedIdentity` reconciled to the `Identity` protocol, the identity gate
stated in code with its negative test, `DeploymentConfig.validate()` called at
startup, a plan content digest bound to approvals — but **that work is
self-consistency work and must not be claimed as evidence.** Per
`domain-layer-status.md` §4, none of it makes any of these layers proven.

---

## 7. Estate-capability gaps, in order of how much they block

**1. There is no automatable native *authoring* path for most of these CSEs.**
This is the dominant gap, and it is a gap in **automation, not in access.**

WP-1B, WP-2 and WP-3 all work because `Import-GPO`, `Get-GPOReport`,
`Backup-GPO` and `secedit` are command-line tools PowerShell Direct can drive.
The families in Plans 025–027 are not like that. Security Settings' SDDL
sections, Scripts, Folder Redirection, Software Installation, Public Key Policies
and wired/wireless are authored by **GPMC editor snap-ins with no cmdlet
surface**. The estate's guests have **no network at all** — the transport reaches
them through the hypervisor — so the only GUI path is a console session, which
the harness cannot drive.

**The console exists**, so this no longer blocks anything: it converts "capture a
native reference" from a lane step into a **manual-queue item** for
`security_template` (read direction), `object_security` (native reference, though
its own oracles do not need one), `network_security` (PKI/wired/wireless, and
possibly firewall/IPsec too), `script_policy`, `software_install` and
`folder_redirection`, plus `gpmc_interop`'s `is_gpmc_editable`. What it costs is
re-runnability. Each such certification is bound to a hash-bound capture rather
than to a lane, so it cannot be re-established against a new build family without
booking the operator again, and it cannot be regenerated from source. Budget it
that way, enumerate questions before booking, and batch captures into single
sittings on the WI-022 model.

Note that the one recorded precedent (`manual-gui-evidence-queue.md`, WI-022) was
performed by **RDP to `mvmcitest01`, which is retired**, against a forest that no
longer carries the lanes. The *procedure* transfers; the access path in that
document does not, and the queue should be updated to describe the current
console when the next capture is booked.

**2. No second domain or forest trust.** Blocks Plan 028 WP-2 outright —
cross-domain import/copy, migration-table application across domains, SID-history
and foreign-security-principal cases — and the cross-domain half of
`lifecycle.py`'s `RestorePlan`. The estate is one forest, one DC. With the
console confirmed, **this is now the only hard blocker in the survey**: it is the
one gap where no amount of operator time produces the evidence, because the
capability itself is absent rather than un-automated.

**3. `secedit /configure` has never been invoked by any lane.** The whole WP-3
record is a database round trip; no security-template *application* evidence
exists. `environment-spec.md` explicitly permits `/configure` on these disposable,
checkpoint-backed guests, so this is missing harness — including a
checkpoint-restore discipline the harness does not yet have — not a missing
capability. It blocks the `object_security` propagation-code *meaning* question,
which the round trip cannot answer even in principle, and the unit-*meaning* half
of `policy_families`.

**4. No lane has ever executed on LabDC01.** `platforms.json` says so explicitly.
This blocks `Kerberos Policy` in both `security_template` and `policy_families`
— it exports empty on a member server, measured 2026-08-04 — and would block
anything domain-wide.

**5. No hosted substrate** — IIS, PostgreSQL, identity provider, TLS, and network
reachability at all. Relevant to Plan 032 only, and only *after* a "harden"
verdict; the shape judgement itself needs no estate. It should be qualified as
its own environment if that verdict lands, and it is worth saying plainly that
the estate's no-network invariant is *deliberate*: this is a reason to build a
different environment, not to weaken this one.

**6. NetSecurity module presence on LabMS01 is unrecorded.** Not known to be
missing — known to be unmeasured, which under `rsop-oracle-design.md`'s rule is
the same thing until probed. One command settles it.

---

## 8. Questions for the operator

These are the decisions this survey cannot take. Two questions it originally
carried have been answered during writing and are recorded first, briefly, so
that a reader arriving at this section does not re-ask them.

### Answered while this survey was being written

- **Is there a working GUI console into `LabMS01`? — Yes.** This was the survey's
  highest-leverage unknown: it gated native reference capture for six modules and
  one sub-claim. The consequences are worked through in "Capture versus lane" at
  the top of this document, in the per-module entries, and in §7 gap 1. In short:
  nothing in Plans 025–027 is blocked on access any more, `network_security.py`
  moved from (b) to (a), and the residual cost is that capture-backed
  certifications are not re-runnable.
- **Is the hosted control plane (Plan 032) being built at all? — Wrong question,
  and the right one is now answered: HARDEN.** The operator's decision was
  narrower than the survey first framed it — *harden or discard*, judged by
  whether an implementer would get there faster from this code than from a blank
  file, and explicitly not by production-readiness or compliance with Plan 032's
  gates. A shape assessment against that standard returned **harden**: the
  defects are additive, not structural. §3.14 carries the reasoning, the one
  scoped deletion, and the one place where the skipped review genuinely bites.
  A residue of that decision is still open and appears below as §8.7.

### 8.1 Does `publication.py`'s PowerShell script branch survive?

`docs/live-publication.md` states, as committed design, "do not directly edit a
live `Registry.pol`, `gpt.ini`, GPC attribute, or GPP XML file over LDAP/SMB" and
"use the Windows GroupPolicy module and GPMC interfaces for production writes."
The module's script branch — currently unreachable, because
`_WINDOWS_VERIFIED_OPERATIONS` is empty — does precisely what that forbids.
Certifying its wire behaviour would be certifying a mechanism the architecture
already ruled out. **Decide whether the branch survives before any lane is scoped
against it.** The plan-completeness lane (§3.11) needs no such decision and can
proceed regardless.

### 8.2 Is `certification.py` the parity framework, or is `oracle_evidence.py`?

Two evidence vocabularies now exist side by side, and they disagree on
`inconclusive`/`unsupported`, on boundary ownership, and on whether a round trip
counts as certifying evidence. A defensible answer is "delete `certification.py`
and let Plan 031 consume Plan 033's manifests directly." This is (c) by
classification, but it is listed here because it decides whether Plan 031 has a
subject at all.

### 8.3 Should GPO Studio write Software Installation at all?

The module cannot produce the CSE's artifact, and that artifact (`.aas`) is
generated by Windows Installer from the package rather than authored by hand. The
choice is between preserve-only, read-only, or committing to `.aas` generation.
The feasibility capture in §3.7 is the input to this decision, not a substitute
for it, and it is now bookable.

### 8.4 WI-038: parse the three SDDL-bearing sections, or preserve-only?

Already an open work item, restated here because it **gates the scope of the
`object_security` lane** rather than being a consequence of it. Building an
entry-shape comparator for `[Registry Keys]`, `[File Security]` and
`[Service General Setting]` is medium-cost work that should not start before the
decision. The `secedit /validate` shape probe in §3.2 is cheap and useful input
to it.

### 8.5 Do the three second-tier modules fold into the same programme?

`backup.py`, `migration.py` and `report.py` are surfaced 1.0 code inside Plan
028's acceptance gates but outside the fifteen. This survey recommends including
them — a defect in surfaced code is live rather than latent, and `migration.py`
may be the cheapest measurement available — but the scope call is the operator's.

### 8.6 How much manual-capture time is available, and in how many sittings?

Not a yes/no, but a resource question that changes how the plan is written. Six
modules plus one sub-claim now reach their oracle through a GPMC console capture
(§7, gap 1). Each is cheap in isolation and none is re-runnable. WI-022 is the
precedent for doing this well — one sitting settled five enumerated questions —
and the running order in §6 batches items 4, 5, part of 6, and
`security_template`'s native read into as few sittings as possible on that basis.
**If operator time is the scarce resource, say so and the plan will enumerate
harder before booking; if sittings are cheap, the captures can be taken one
module at a time and the plan gets simpler.** Either answer is workable; guessing
wrong is what costs.

### 8.7 Which of Plan 032 WP-1's three hosting topologies is the target?

The residue of the harden ruling, and the only question the shape assessment
could not close by reading code. WP-1's skipped ADR was to choose between
**IIS + HttpPlatformHandler**, **a Windows Service behind IIS/ARR**, and **a
non-Windows host behind an OIDC reverse proxy** — and `HostedConfig` has already
made the choice implicitly by carrying `tls_certificate_path` and
`tls_key_path` in the application's own config while mandating a loopback bind.

The consequence is bounded and worth stating so it is not over-weighted: if the
target is either IIS topology, the current field list is roughly right and needs
only the TLS-ownership question resolved. If it is a non-Windows host behind an
OIDC proxy, then `tls_certificate_path`, `tls_key_path` and
`trusted_proxy_addresses` all belong to the edge and should leave the
application's config entirely, at which point `HostedConfig` is mostly rewritten.
**That is a roughly sixty-line question affecting one dataclass. It does not
touch the authorization model, the identity seam, or either publication module**
— which is why it did not move the harden verdict, and why the recommended
sequence is to write the ADR first and freeze only the config half meanwhile.

---

## 9. Where this survey is uncertain

Stated plainly, because a survey *about* unproven claims should not overstate its
own.

- **The `.migtable` namespace and element shape (§4.1) is a recollection of the
  real format, not something verified against Microsoft documentation.** It is
  the reason that measurement is ranked second rather than dismissed. What *is*
  verified is that no GPMC-authored migration table exists in this repository and
  that a namespace mismatch fails silently.
- **Whether `{3125E937-…}` and `{A3CC7818-…}` are *only* GPP XML `clsid` values
  was not confirmed against Microsoft documentation.** The repository's own
  evidence points one way — seventeen GPMC-authored fixtures, none carrying
  either GUID in an extension list, and `export.py` using `{17D89FEC-…}` for
  Groups — but the fixture sweep is what settles it.
- **`certification.py` returning `full` on round-trip-only evidence follows from
  reading the level logic and the default suite's `oracle` fields; it was not
  executed.** The discriminator in §3.13 is written to confirm it rather than to
  assume it.
- **Whether `network_security.py`'s firewall and IPsec halves are automatable is
  unknown**, and rests on the absence of any NetSecurity reference in the
  repository rather than on a measurement of the host. That uncertainty no longer
  reaches the classification — the console makes the module measurable either way
  — but it does reach the lane economics.
- **Two things this survey recorded as hypotheses are no longer hypotheses.** The
  Plan 030/032 shape assessment reproduced both `publisher.py` threat-model rows
  by execution (§3.12) and demonstrated the `profiles_for_actor` conflation.
  Those are restated as demonstrated behaviour, and flagged as such. Everything
  else in this document that is labelled a hypothesis still is one.
- **The direction document this survey answers is not committed on `main`.** It
  was read from an uncommitted worktree.
