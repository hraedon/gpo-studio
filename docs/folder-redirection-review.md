# Reviewing Folder Redirection files

The Folder Redirection panel reads a native `fdeploy` file, or compares an
earlier copy with a current one. It only inspects and compares. It does not
author or publish policy.

## Review or compare files

1. Open **Folder Redirection** in the workspace sidebar.
2. Choose a **Current file**. To compare, also choose an **Earlier file**.
   The comparison runs from earlier to current.
3. Select **Review files**.

Changing a selection or closing the panel clears the previous result.
Reopening the panel clears the selections.

## Which file to use

- Use the native `fdeploy1.ini` from a backup's `User/Documents & Settings`
  directory. The `fdeploy.ini` next to it is usually an empty marker; the panel
  recognises the marker captured by R3.
- Keep the original UTF-16LE encoding, byte-order mark and line endings.
  Renaming a text file to `.ini` does not convert its encoding.
- Each file must be 1 MiB or smaller.

The panel sends the selected bytes to the Studio server's existing review API.
It does not save them into a GPO or the workspace.

## Reading the result

The result starts with the reader's evidence limits. Then, for each file, it
shows the version, parsing warnings, structural issues, redirection sections
and raw flags. Expand **Folder-to-principal map** or **Full file report** to
see the rest of what the reader kept.

- Unknown folder identifiers are shown as GUIDs.
- Raw flags are not translated into options. The capture needed to establish
  what they mean is still outstanding (WI-066/R12).

## Reading a comparison

The comparison matches redirection sections by folder GUID and principal, and
lists added, removed and modified sections with their earlier and current
paths and raw flags.

- A section can also show as modified because of other entries inside it. Their
  values are in the full file reports.
- If an identity appears more than once, the comparison uses the last section.
  The panel therefore also shows each file's duplicate warnings and all parsed
  sections.
- An empty comparison means no redirection-section changes were found. Changes
  to the version, folder map, unrelated sections or formatting are outside the
  comparison. The panel reports separately whether the complete file bytes are
  identical, so you can tell when two different files produced an empty
  comparison.

## Evidence and limits

The panel calls `POST /api/folder-redirection/fdeploy` to inspect each file and
`POST /api/folder-redirection/fdeploy/diff` to compare them. The reader is
tested against one native Windows capture (R3), and since 2026-10-08 a
repeatable Windows lane backs it: the fdeploy lane's run
`fd-20261008121347-3151` showed Windows keeping the file's bytes through
`Import-GPO` and `Backup-GPO`, and the reader agreeing with `Get-GPOReport` on
folder, principal and destination, for R3's bytes and three `Flags`-only
variants. It did not decode `Flags` or measure multi-folder or multi-principal
files; see [the lane results](plan-033/fdeploy-results.md). The panel does not
add to that evidence. Imported backups carry the parsed file on the GPO, and
its policy report and GPO diffs render it (WI-068, closed 2026-10-08); see the
[read-target decision](scope-decision-2026-09-11-folder-redirection.md).
