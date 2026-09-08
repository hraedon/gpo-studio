# Publication completeness — Windows results

**Current qualification (2026-09-08):** `publication-completeness-20260908013539-2644`, 21/21,
on clean frozen `b5ccbabd19b7ed661312915ca4b314dc27bbdd6e`.
[Complete successor evidence](wp1b-evidence/backup-report-20260908/publication/verification.json) and the
[reason for the targeted refresh](backup-report-fidelity.md). Earlier results
below remain records of their original revisions and scope.


Plan 034 WP-1's `publication` item. Run
`publication-completeness-20260907193221-2759` passed all 21 checks on the
clean disposable member server from source commit `362699c3437a`; the source
checkout was clean. The immutable evidence tag is
`evidence/publication-completeness-20260907193221-2759`.

**Re-run independently on 2026-09-07** at the merged commit `5037147`, as
`publication-completeness-20260907211717-2520`: 21/21 again, same comparison,
against a freshly created GPO with a different identity. That second run is not
banked — the run above remains the certification of record — but it is tagged,
and it is the reason this is described as a lane rather than a capture. Plan
034's rule is that a capture becomes a lane before it becomes a surface, and
the difference between the two is precisely whether a second person can get the
same answer without the first one present.

## What this lane asks, and why nothing else asked it

Every other lane in this plan asks a round-trip question: Windows consumes
what Studio wrote, and re-emits it, and the two are compared. That question
cannot see the defect this lane was built for, because **a round trip never
asks what a third party would have had to write.**

The publication planner does not write anything. It produces a typed account of
what an administrator would have to do to publish a GPO. So the question here
is a comparison between that account and the state Windows actually arrives at:

* every byte-bearing file in the GPO's SYSVOL tree, in both directions — a file
  Windows wrote that no step names is content publication would silently drop,
  and a step naming a file Windows never produces is a plan asserting something
  the directory does not want; and
* the `gPCMachineExtensionNames` / `gPCUserExtensionNames` attributes, without
  which every one of those files is inert.

WI-057 is the reason the second bullet exists. The planner named every file and
registered no extension, so a plan executed exactly as written produced a GPO
whose content was byte-perfect and which applied nothing — and it failed
silently, because everything a reviewer would think to check was present.

## Observed native behaviour

The candidate carries two registry sides plus one verified GPP family per side
(Services computer-side, Drives user-side). After `Import-GPO`, Windows
produced exactly five files, and the plan named exactly those five:

```
gpt.ini
Machine/Preferences/Services/Services.xml
Machine/registry.pol
User/Preferences/Drives/Drives.xml
User/registry.pol
```

Casing is not preserved — the plan says `Machine/Registry.pol` and Windows
writes `Machine/registry.pol` — so path comparison is casefolded. Nothing else
is: content differences are never folded away.

The directory object carried three extension groups per side, byte-identical to
what the planner declared before the import happened:

```
gPCMachineExtensionNames = [{35378EAC-…}{D02B1F72-…}]
                           [{00000000-0000-0000-0000-000000000000}{CC5746A9-…}]
                           [{91FBB303-…}{CC5746A9-…}]
gPCUserExtensionNames    = [{35378EAC-…}{D02B1F73-…}]
                           [{00000000-0000-0000-0000-000000000000}{2EA1A81B-…}]
                           [{5794DAFD-…}{2EA1A81B-…}]
```

The two sides differ in their registry tool half (`D02B1F72` against
`D02B1F73`), which is why the candidate uses a different GPP family per side: a
candidate whose two lists were identical could not catch a fix that computed one
side and assigned it to both.

`versionNumber` was `65537` — user half 1, machine half 1 — matching the plan's
declared `version_half: "both"`, declared before anything was imported. The GPO
carries no description and Windows produced no `GPO.cmt`, which is the negative
half of WI-058 and the only way that condition can be stated.

## How the expectation is bound

`expected.json` is built on the controller and **never travels to the guest**.
That distinction carries more weight in this lane than in a round-trip one: the
guest is the thing being measured, so an expectation it supplied would make the
verdict a comparison of Windows against itself. A test asserts the driver does
not push it and that the guest script never names it.

The verdict records a SHA-256 for every file under the candidate root, and
refuses at the door if a required one is absent (WI-025).

## Boundary

This run proves that, for one GPO shape, the publication plan's account of what
it would write matches what Windows produces — files and extension registration
both. It does **not** prove:

* that executing such a plan works. Nothing in this lane writes to SYSVOL or AD
  on Studio's behalf; the planner remains review-only and unsurfaced, and the
  operation allowlist remains empty. This lane measures the *plan*, not a
  publication.
* anything about security filtering, links, WMI filters, or the AD-side steps
  that accompany them. `Import-GPO` restores settings, not links, so those steps
  have no coverage here.
* anything about GPP families whose extension metadata has never been captured.
  The planner refuses those rather than guessing, and the candidate cannot carry
  one without tripping that refusal.
* that the file set generalises. One shape was measured. A GPO carrying security
  templates, scripts or preserved CSE content would produce a different tree,
  and the lane would have to be pointed at it.

`publication.py` therefore remains **unsurfaced**. Under the exit condition in
`docs/domain-layer-status.md` a module needs both a re-runnable lane and a
delivery surface; this supplies the first.
