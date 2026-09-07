# Scripts metadata interoperability lane

Plan 034 WP-1 turns R10's import/rebackup observation into a repeatable lane.
Implementation and qualification are in progress; no new bound verdict has
been earned yet.

The unchanged `build-scripts-backup-candidate.py` uses the production export
path to emit a deterministic GPMC backup. It contains two legacy startup
entries, one PowerShell startup entry, and `StartExecutePSFirst=true`. The ZIP
contains the two INIs, backup metadata, and manifest. It contains no `.cmd` or
`.ps1` executable payloads. This experiment concerns metadata interoperability,
not script delivery, execution, or endpoint policy application.

## Native report observation before qualification

A preliminary, unlinked disposable GPO was imported and backed up on LabMS01
on 2026-09-07 to inspect Windows' XML representation. Its owned GUID was
removed and a subsequent `Get-GPO -All` query confirmed absence. This probe
informed the parser; it is not a certification of the new lane.

The report stores scripts under the computer-side Scripts extension, using
the `http://www.microsoft.com/GroupPolicy/Settings/Scripts` namespace. Each
`Script` has `Command`, `Type`, `Order`, and `RunOrder` fields. `Parameters`
is omitted for the parameterless command. The XML lists the command scripts
first with order values 1 and 2, then the PowerShell script with order 0. All
three carry `RunPSFirst`. XML traversal order therefore must not be confused
with the recorded execution order.

The lane must match these fields and identities exactly, require an unlinked
owned GPO, compare both rebackup INIs to the original archive bytes, check the
Scripts CSE pair and file references, retain raw artifacts, and confirm cleanup.
Its verdict binds both source and transported bytes. Duplicate or substituted
metadata, unrelated XML text, incomplete evidence, and failed cleanup must
prevent a pass. GPMC GUI editability remains a separate capture-backed claim.
