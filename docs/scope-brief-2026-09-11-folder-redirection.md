# Scope brief, 2026-09-11 — Folder Redirection

Status: **not decided.** This is Plan 034 WP-4, which the plan states is "a
decision first" and "not a lane until someone rules". Everything a ruling needs
is now measured and assembled here; the ruling itself is a person's, in the way
`software_install`'s was.

Nothing here consumed estate time. The decisive capture was taken in September
(R3), the demand figure comes from a census already banked (R6), and the one
measurement the survey asked for and nobody had run is offline and takes a
second — it is run below.

---

## The question

From [`plans-025-032-oracle-survey.md`](plans-025-032-oracle-survey.md) §3.8 and
Plan 034 WP-4: **is Folder Redirection a write target, a read target, or out of
scope?**

It reaches this form rather than "fix the serializer" because R3 answered the
cheap discriminator the survey proposed, and the answer invalidated the module's
*scope* rather than its correctness.

## What is measured

**1. The artifact is `fdeploy1.ini`, and the module addresses neither file.**
R3 authored folder redirection through the GPMC editor on Windows Server 2025
and backed the GPO up. The capture is banked at
`tests/fixtures/native-folder-redirection-gpmc/`:

```
fdeploy.ini    20 bytes  — UTF-16LE BOM and two CRLFs. An empty marker.
fdeploy1.ini  458 bytes  — UTF-16LE BOM, the actual policy:

[version]
version=100
[Folder_Redirection]
{FDD39AD0-238F-46AF-ADB4-6C85480369C7}=s-1-1-0;
[{FDD39AD0-238F-46AF-ADB4-6C85480369C7}_s-1-1-0]
Flags=1021
FullPath=\\zz-studio-fileserver\zzredir\%USERNAME%\Documents
```

Two corrections to what the project believed before R3. The survey expected one
file named `fdeploy.ini`; there are two, and the policy is in the one nobody had
named. And `folder_redirection.py` emits `User Shell Folders` registry values —
which is what the CSE writes *on the client*, not what the GPO *carries*.

**2. The demand side is zero, by the same measure that ruled Software
Installation out.** R6's census covers the extension lists of 26 production
GPOs in a live domain. The Folder Redirection CSE
`{25537BA6-77A8-11D2-9B6C-0000F8080861}` appears in **0 of 26** — the same
figure, from the same census, as
`{C6DC5466-785A-11D2-84D0-00C04FB169F7}` (Software Installation).

The honest weight of that is what
[`scope-decision-2026-09-06`](scope-decision-2026-09-06-software-installation-and-certification.md)
already said of it: one estate, 26 GPOs. It is not evidence about the industry.
It is evidence about the estate this project can see, and it is enough to move a
priority.

**3. The module drops almost everything a real policy carries.** The survey
asked for one offline check — "call `to_registry_settings()` on an `advanced`
policy with three group rules and observe that exactly one tuple comes back" —
and called it "a code fact, not an oracle result, but it bounds what any lane
could certify". It had never been run. It is now, and it is pinned by
`tests/test_folder_redirection_scope.py`:

| Input | Output |
|---|---|
| three group rules, three different UNC targets | **one** registry tuple |
| three group SIDs | **none** appear |
| all four option flags set non-default | **none** appear |

Only the first rule's path survives. Set against R3's capture, where `Flags=1021`
encodes the option flags and the section key carries the SID, this settles the
survey's hypothesis: `to_registry_settings()` is not a Folder Redirection writer
at any level of detail. There is nothing here to fix incrementally.

## What `folder_redirection.py` currently is

537 lines. A typed model of redirection rules, path validation, and
`assess_redirection_migration` — an Intune migration-impact assessment. No
`fdeploy1.ini` parser, no writer, no file emitter. Its one output path is the
registry conversion measured above.

Per [`domain-layer-status.md`](domain-layer-status.md), none of the options
below is a deletion order. The path validation and the migration assessment are
independently coherent; what is at stake is whether the module is *progress
toward a capability* or a utility that happens to live here.

It is bound by no lane
([`bound-source-cost.md`](plan-033/bound-source-cost.md)), so every option costs
zero estate time to *start*.

## Where the Software Installation precedent applies, and where it does not

This is the part a ruling turns on, because the surface similarity is strong
enough to be misleading.

**Reason 1 carries over exactly.** Zero measured demand, same census, same
caveat, same reversibility.

**Reason 4 carries over exactly.** A module modelling something no Windows tool
emits, which under a preserve-only ruling would need a stop-counting order
rather than a deletion.

**Reason 2 does not carry over at all.** Software Installation's capture was
"the most expensive unrun capture in the survey" — an MSI inside an
egress-free estate, a destructive install, a checkpoint-restore path no lane had
exercised. **Folder Redirection's capture is already taken.** R3 cost a person
an hour in September and the bytes are committed. The endpoint half, if it were
ever wanted, "needs nothing new" — WP-9 built and certified the two-guest lane,
the `user-logged-on` checkpoint and the `HKEY_USERS\<SID>` reads on 2026-08-04.

**Reason 3 inverts.** `.aas` is generated by the Windows Installer APIs from the
package: no documented format, no oracle for correctness beyond "the endpoint
installed something". `fdeploy1.ini` is a 458-byte UTF-16LE INI with a version
stanza, a folder-to-SID map and a flags-and-path section — and this repository
already has a UTF-16LE/BOM INI codec with a strict decoder
(`security_template.py`), a lane pattern for exactly this shape, and two
surfaces built on it. The oracle is `Backup-GPO`, which the harness drives
routinely.

**So the two arguments that made Software Installation an easy "no" are absent
here.** Folder Redirection is cheap to build and unwanted by the only estate
this project can see. That is a genuinely different balance, and it is why this
document stops at a brief.

## The options

**(a) Out of scope, preserve-only.** Mirrors the Software Installation ruling:
`fdeploy1.ini` is preserved as unknown CSE content on import, nothing is
emitted, the module stops counting as progress. *Cost:* a ruling and a matrix
row. *Buys:* no new debt. *Gives up:* a family that is, uniquely among the
remaining ones, tractable.

**(b) Read target.** Parse `fdeploy1.ini` on import and show it in reports and
diffs; emit nothing. *Cost:* a parser against the R3 capture, a report surface,
and a lane that proves the parse round-trips the banked bytes — no estate time,
because the capture is committed and the comparison is offline. *Buys:* an
operator reviewing an imported GPO can see the redirection policy instead of an
opaque preserved blob. *Gives up:* authoring.

**(c) Write target.** Parse and emit, certified by a `Backup-GPO` /
`Import-GPO` lane in the shape the scripts-metadata lane already has. *Cost:*
the parser and writer, plus one estate session — which the estate owes anyway
for WI-063, WI-064 and WI-065. *Buys:* real GPMC parity for a family GPMC has.
*Gives up:* the argument that effort follows measured demand.

## Recommendation

**(b), with (c) explicitly deferred rather than refused.**

The reasoning is that (b) is the only option whose cost is *already sunk*. The
capture exists; a parser tested against it needs no estate session and no
scheduling decision, and it converts a preserved blob into something a reviewer
can read — which is the thing a workbench is for, and the thing the module's
current 537 lines conspicuously do not do.

It also makes (c) cheap later rather than expensive: a certified parser is most
of a certified writer in this codebase, as WP-1B's history shows in the other
direction. And it avoids the one outcome that would be hard to justify — ruling
a tractable family out on a demand figure of 26 GPOs, when the two costs that
justified doing exactly that for Software Installation do not apply.

Against the recommendation, honestly: (a) is defensible on the plan's own
principle that effort should follow measurement, and a reader who weights the
0-of-26 more heavily than the tractability should take (a). That is the
disagreement the ruling has to settle, and it is not one more measurement will
settle — the next estate is the only thing that would move the demand figure.

## What would change the answer

- **A second estate's census.** The demand figure is this document's weakest
  input and the only one a future measurement can move.
- **The endpoint half disagreeing with the file half.** R3 measured what the
  GPO carries. Nothing has measured what the client does with it, and WP-9's
  machinery could — cheaply, since it is built.
- **`fdeploy.ini`'s marker turning out to be load-bearing.** Twenty bytes with
  no content; whether Windows requires it alongside `fdeploy1.ini` is unmeasured,
  and a writer that omits it would be the kind of defect this project keeps
  finding by measuring rather than reasoning.
