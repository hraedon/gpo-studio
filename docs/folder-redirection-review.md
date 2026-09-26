# Reviewing Folder Redirection files

Open **Folder Redirection** in the workspace sidebar. Choose a **Current file**,
then **Review files**. To compare two copies, also select an **Earlier file**.
The comparison runs from earlier to current.

Use the native `fdeploy1.ini` from a backup's `User/Documents & Settings`
directory. The adjacent `fdeploy.ini` is usually an empty marker; the panel
recognises the marker captured by R3. Keep the original UTF-16LE encoding,
byte-order mark and line endings. Each file must be at most 1 MiB. Renaming a
text file to `.ini` does not convert its encoding.

Selected bytes are sent to the Studio server's existing review API. The panel
does not save them into a GPO or the workspace. It offers inspection and
comparison, with no authoring or publication action. Changing a selection or
closing the panel clears the previous result; reopening clears the selections.

The result starts with the reader's evidence limits. Each file then shows its
version, parsing warnings, structural issues, redirection sections and raw
flags. Expand **Folder-to-principal map** or **Full file report** to inspect the
other retained observations. Unknown folder identifiers stay visible as GUIDs.
Raw flags are not translated into options: the capture needed to establish
their meaning is still outstanding (WI-066/R12).

The comparison matches redirection sections by folder GUID and principal and
shows added, removed and modified sections. It displays earlier/current paths
and raw flags. Additional entries within a redirection section can also cause
a modification; their values are in the full file reports. Repeated identities
compare using the last section, so the panel also displays each file's duplicate
warnings and all parsed sections.

An empty comparison means no redirection-section changes were detected.
Version, folder-map, unrelated-section and formatting changes are outside that
comparison. The panel separately reports whether the complete file bytes are
identical, so an empty comparison of different files is explicit.

This interface uses `POST /api/folder-redirection/fdeploy` to inspect each file
and `POST /api/folder-redirection/fdeploy/diff` to compare them. The reader is
tested against one native Windows capture, with no repeatable Windows lane
behind it. Adding the panel does not expand that evidence. Integration with
imported GPO reports and workspace diffs remains WI-068; see the
[read-target decision](scope-decision-2026-09-11-folder-redirection.md).
