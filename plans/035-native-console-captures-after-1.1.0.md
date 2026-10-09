# Plan 035: Native console captures after 1.1.0

Status: **proposed** (2026-10-09). Nothing here blocks 1.1.0. The three work
items it covers each need evidence that only the Group Policy consoles produce,
and the grounding they unlock edits bound files, so it belongs to the batch
after the release.

## Why this plan exists

1.1.0 closed every Plan 034 module and re-earned all 26 lane verdicts at one
commit ([batch note](../docs/plan-033/release110-batch.md)). Three work items
stay open because a cmdlet cannot author what they need to measure:

- [WI-071](../docs/work-items.md#wi-071--the-scripts-metadata-lane-measures-one-side-and-one-trigger):
  the Scripts lane measures computer-side startup scripts only. The user-side
  Scripts extension pair, and the `scripts.ini` shapes for logon and shutdown,
  are unmeasured, and a round trip cannot detect a wrong extension pair.
- [WI-066](../docs/work-items.md#wi-066--r3-answered-one-of-the-four-questions-it-was-designed-to-answer):
  the folder-redirection capture (R3) answered one of its four questions. The
  option-flag encoding and the multi-group representation need the capture R12
  specifies ([manual evidence requests](../docs/manual-evidence-requests.md)).
- [WI-077](../docs/work-items.md#wi-077--the-firewall-export-registers-the-administrative-templates-tool-guid):
  whether the Group Policy Management Editor shows and edits the firewall rules
  of a Studio-imported GPO, which registers the Administrative Templates tool
  GUID rather than the firewall snap-in's.

`windows-console-driver` (WCD) drives these consoles with transactionally
verified actuation and is already qualified for GPMC Scripts startup entries
and Basic folder redirection. None of its current capabilities covers these
captures by argument alone: their run-sheets and envelopes name the
computer-side Startup node, and Basic mode with one folder. Its observers
already read both scripts sides, every `scripts.ini` section, both extension
lists and the fdeploy file, so the work is mostly new run-sheets and envelopes.

## Work packages

### WP-1: user logon and machine shutdown scripts (WI-071), medium

A new WCD capability (a new identifier, so the qualified startup capability
keeps its row) that authors one `.cmd` logon script on the user side and one
`.cmd` shutdown script on the machine side through GPME.

- The envelope requires both entries and `{42B5FAAE-...}` on the user extension
  list, and does **not** assert a user-side tool GUID: that value is the
  measurement. Version relations are `>`, not `+1`, until GPME's per-dialog
  bump is measured. Forbid checks cover the user list, not only the machine
  list.
- `scripts.ini` is not byte-hashed by the observers, so the run-sheet ends with
  a guest-side backup collected before any revert, to bank byte-exact fixtures
  as R2 did.
- Plan one discovery pass for the Logon and Shutdown node and dialog names on
  this estate.

The outcome decides whether `export.py`'s user-side Scripts pair changes.

### WP-2: GPME view of an imported firewall GPO (WI-077), small to medium

An observe-only transaction: import Studio's firewall backup into a disposable
GPO, open it in GPME, navigate to Windows Defender Firewall with Advanced
Security, dump the UIA tree and screenshot the Inbound Rules and one rule's
Properties sheet, then cancel and close.

- The envelope is post equals pre (versions and the `Registry.pol` hash
  unchanged), which satisfies the post-state rule.
- This is surface evidence reviewed by a person, not a certified claim. It can
  show controls enabled; proving an edit persists needs a mutating capability.
- Needs a guest-side import step and a way to place the bundle in the guest.

If GPME handles the imported rules with `D02B1F72`, `export.py` stays as it is.
Otherwise WP-1's and WP-2's export changes go into one batch that reruns the
publication, scripts-metadata and firewall lanes.

### WP-3: folder redirection, Advanced with groups (WI-066), medium to large

R12's capture: Documents unchanged, Pictures in Advanced with two group rules
and options at default, and Videos in Basic with exactly one option changed (the
control that pins one flag bit), then the three property pages reopened.

- Leave every `Flags` value unasserted (they are the measurement); assert the
  structure, such as Pictures appearing as two sections with different SIDs.
- The main risk is the group field: if it forces the standard object picker,
  that is new surface work. Run a recon pass before writing the capability.
- The fdeploy writer stays deferred until this capture lands.

## Operating the captures

All three are disposable-GPO transactions on the same checkpointed member and
can share one estate window: canary, bring-up, exclusive console, the run, a
revert, then bring-up and canary again. Collect byte artifacts before the
revert. Bank each record in WCD's estate-window notes and qualification ledger,
then in gpo-studio as a fixture with provenance, as R2 and R3 were.

Recommended order: WP-1 and WP-2 in one window (both decide `export.py`), WP-3
after its recon pass.

## Exit

Each work package exits when its capture is banked and its work item's closing
condition is met, or when a recorded ruling takes it out of scope. Grounding
that edits bound files runs as one requalification batch through
`scripts/plan-033/run-requal-batch.sh`.
