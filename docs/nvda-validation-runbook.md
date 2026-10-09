# NVDA validation runbook

The manual screen-reader acceptance gate for GPO Studio releases. It was
written for 1.0 and extended for 1.1. The automated axe, keyboard, and
accessibility-tree tests must pass first, but they do not replace a person
listening to the interface through NVDA and using NVDA's navigation model.

Run this procedure against the exact release candidate. Do not mark the manual
items in [`browser-accessibility-checklist.md`](browser-accessibility-checklist.md)
complete until a person has performed and recorded this session.

The candidate is published as a GitHub prerelease before this gate runs. That
publication does not approve the final release. It fixes the wheel and checksum
that this gate evaluates.

**What a 1.1 session covers.** The 1.0 journeys (sections 1 to 6 below) and
what 1.1 added to the interface (sections 7 to 12): sidebar navigation, dark
theme switching, the RSOP prediction, Security template and Folder
Redirection panels, and the sticky row actions of wide tables. The 1.0.0
session covered none of the 1.1 additions. For 1.1.0 this session is required
before final approval, against a `v1.1.0-rc.N` candidate.

## Scope and pass rule

Run the full journey in Microsoft Edge, then the shorter smoke journey in the
supported Firefox ESR version. The gate passes when a keyboard-only NVDA user
can understand the page structure, complete the core authoring and export
tasks, recover from validation and concurrency errors, and retain useful focus
and row context.

Classify findings as:

- **Blocker:** a core task cannot be completed; focus becomes lost or trapped;
  a critical control lacks a usable name; or an error is neither announced nor
  discoverable.
- **Significant:** the task has a workaround, but announcements, order, state,
  or context are materially confusing or inefficient.
- **Minor:** pronunciation, verbosity, or polish issue that does not obscure
  meaning or state.

How findings decide the gate:

- **Any blocker fails acceptance.** The candidate is not promoted. The fix
  ships in a new candidate, and the session is repeated on that candidate.
- **Every other significant finding needs the release owner's explicit,
  recorded disposition** before approval: fixed (in a new candidate), accepted
  for this release with a reason, or deferred to a named work item. A
  significant finding with no recorded disposition blocks approval.
- **Minor findings are recorded.** They need no disposition to approve.
- **Any change to application behavior requires a new candidate**
  (`vX.Y.Z-rc.N+1`), including a fix for a finding from this session. Rerun
  the journeys the change touches, plus the page-structure and dialog smoke
  checks, on the new candidate's wheel and record its SHA-256 afresh. The
  final release is promoted from the accepted candidate's code with no
  application-behavior change; only version, changelog and evidence files may
  differ.

## Prepare the exact candidate

Record all of the following before testing:

- GPO Studio version and source commit.
- Wheel filename and the **exact** SHA-256 of that wheel, copied from the
  `SHA256SUMS` in the candidate's release assets. For 1.1.0rc1 the wheel is
  `gpo_studio-1.1.0rc1-py3-none-any.whl`.
- Windows edition, version, and build.
- NVDA version.
- Edge and Firefox ESR versions.
- Tester and date.

Install the candidate using the [Windows quickstart](windows-quickstart.md).
Download the wheel and `SHA256SUMS` from the candidate's GitHub Release
**Assets**, not from the source-code ZIP, a CI Actions artifact or a local
build. The session is evidence only for the wheel whose hash it records.
Before installing, confirm the downloaded file is that wheel, for example:

```powershell
$Wheel = Get-Item (Join-Path $HOME "Downloads\gpo_studio-1.1.0rc1-py3-none-any.whl")
$Expected = (Select-String -Path (Join-Path $HOME "Downloads\SHA256SUMS") -SimpleMatch $Wheel.Name).Line.Split(" ")[0]
$Actual = (Get-FileHash -Algorithm SHA256 $Wheel.FullName).Hash.ToLowerInvariant()
if ($Actual -ne $Expected) { throw "Wheel hash $Actual does not match SHA256SUMS ($Expected)" }
"Wheel SHA-256: $Actual"
```

Record the printed SHA-256 in the evidence record. If it does not match, stop:
the session cannot be recorded against that candidate.

Use a new disposable workspace, for example:

```powershell
$Root = Join-Path $env:LOCALAPPDATA "GPO Studio"
$App = Join-Path $Root "venv\Scripts\gpo-studio.exe"
$NvdaData = Join-Path $Root "nvda-validation"
New-Item -ItemType Directory -Force $NvdaData | Out-Null
& $App run --host 127.0.0.1 --port 8765 --database (Join-Path $NvdaData "nvda-test.db")
```

Install the [current stable NVDA](https://www.nvaccess.org/download/) if the
lab image does not already have an approved version. If you test an older
version, record why. The
[NVDA User Guide](https://download.nvaccess.org/releases/stable/documentation/en/userGuide.html)
is the authority on keyboard commands.

Use headphones or working speakers. You may open **NVDA menu > Tools > Speech
Viewer** to capture exact announcements, but you must still listen to the
speech. Leave punctuation and verbosity at your normal settings, and record any
material non-default settings.

## NVDA command reminder

The **NVDA key** is normally Insert or Caps Lock, depending on configuration.

| Action | Key |
|---|---|
| Stop speech | Ctrl |
| Toggle browse/focus mode | NVDA+Space |
| Input Help on/off | NVDA+1 |
| Next/previous heading | H / Shift+H |
| Next/previous landmark | D / Shift+D |
| Next/previous form field | F / Shift+F |
| Next/previous button | B / Shift+B |
| Next/previous table | T / Shift+T |
| Elements List | NVDA+F7 |
| Move within a table | Ctrl+Alt+Arrow keys |

If a command behaves differently in the approved NVDA version, follow that
version's User Guide and record the deviation.

## Create the disposable policy

1. Start NVDA, then Edge. Go to <http://127.0.0.1:8765>.
2. Use **New GPO** and create `NVDA Synthetic Policy` with a synthetic domain,
   the tester's initials in the description, and `Manual NVDA validation` as
   the change reason.
3. Avoid real domain names, SIDs, paths, or production policy data.

Use this workspace only as test evidence. Do not publish its artifacts.

## Edge: full journey

For every step, note whether speech conveyed the control's name, role, value or
state when applicable, and whether focus landed where expected.

### 1. Page structure and policy selection

1. Reload the page and confirm NVDA announces a useful document title.
2. Press **D** through landmarks and **H** through headings. Confirm the policy
   navigation and main workspace are understandable without visual inspection.
3. Open **NVDA+F7**. Confirm headings, links, and form fields have meaningful,
   non-duplicated names.
4. Select `NVDA Synthetic Policy`. Confirm its name, draft state, and revision
   are available in a sensible order.

### 2. Tabs and keyboard state

1. Tab to **Overview** in the section tab list.
2. Use Right Arrow, Left Arrow, Home, and End. Confirm NVDA announces each tab,
   selected state, and its position or equivalent context.
3. Confirm one Tab press leaves the tab list for the active panel rather than
   visiting every inactive tab.

### 3. Dialog focus, labels, validation, and return

1. On **Policy settings**, activate **Add setting**.
2. Confirm the `Add policy setting` dialog name is announced and initial focus
   lands on **Configuration**.
3. Tab and Shift+Tab through the dialog. Confirm all fields and buttons are
   named, required state is conveyed, and focus remains inside the open dialog.
4. Enter this synthetic data, but first set Value to `not-a-number`:

   - Registry key: `Software\Policies\NvdaSynthetic`
   - Value name: `Enabled`
   - Type: `REG_DWORD`

5. Activate **Save setting**. Confirm the validation error is announced, focus
   moves to the persistent error summary, the message identifies Value, and
   the linked message returns focus to that field.
6. Correct Value to `1` and save. Confirm the dialog closes, the new revision
   is discoverable, and the setting row has enough context to understand its
   value and actions.
7. Open **Add setting** again, press Escape, and confirm focus returns to the
   **Add setting** button.

### 4. Table and action context

1. On **Preferences**, activate **Add group**, enter `Administrators` as the
   group name and `S-1-5-32-544` as the synthetic SID, then save it.
2. Press **T** to reach its table, then use Ctrl+Alt+Arrow keys to move among
   cells and headers.
3. Navigate to Edit, Clone, Restore, and Delete actions. Confirm each action is
   associated with the correct row; an announcement of only `Edit` or `Delete`
   without item context is a finding.
4. Confirm repeated live announcements are not noisy enough to interrupt or
   obscure the task.

### 5. Concurrency conflict and recovery

1. In the first Edge window, open **Security**, activate **Add filter**, enter
   the synthetic SID `S-1-5-32-544`, and leave the dialog open.
2. Open a second Edge window at the same URL. Select the same policy, activate
   **Edit** in the **Policy details** card, change the description, provide a
   change reason, and save. This creates a newer revision.
3. Return to the first window and activate **Save filter**.
4. Confirm NVDA announces the `Review changes before reapplying` dialog and
   makes `Unsaved fields retained` and the available choices discoverable.
5. Activate **Review and reapply**. Confirm focus returns to the filter dialog,
   the SID remains present, and saving again succeeds.

### 6. Export review

1. Activate **Export bundle**.
2. Confirm NVDA announces the `Review export readiness` dialog, validation
   status, policy semantic SHA-256, review-model SHA-256, and action choices in
   a sensible order.
3. Confirm Escape returns focus to **Export bundle**.
4. Reopen the dialog and activate **Download export**. Confirm the action is
   operable and the browser reports the download without trapping focus.

## Edge: journeys added in 1.1

Run these after the 1.0 journeys, in the same Edge window and workspace. The
synthetic inputs below use the reserved `example.test` domain; do not replace
them with real names, paths or SIDs.

### 7. Sidebar navigation

1. Press **D** to the sidebar. Confirm its controls are discoverable in order:
   **New GPO**, **Import estate**, **Import GPMC backup**, **Starter GPOs**,
   **RSOP prediction**, **Security template**, **Folder Redirection**,
   **Scripts**, the **Filter policies** search, the **Group Policy objects**
   navigation list, and the colour-theme button in the sidebar footer.
2. Tab through the sidebar. Confirm each button's name is announced once, in
   that order, and that focus never jumps into the main workspace and back.
3. For each of **RSOP prediction**, **Security template**, **Folder
   Redirection** and **Scripts**: activate it, confirm NVDA announces the
   dialog's name and focus lands inside the dialog, press Escape, and confirm
   focus returns to the sidebar button that opened it.
4. Type part of `NVDA Synthetic Policy` into **Filter policies**. Confirm the
   list is reachable afterwards and that NVDA conveys which policies remain
   (an empty or stale list with no announcement is a finding).

### 8. Dark theme switching

1. Tab to the colour-theme button in the sidebar footer. Confirm NVDA
   announces its name and current mode, for example "Colour theme: automatic.
   Activate to change."
2. Activate it three times. Confirm each press announces the new mode (dark,
   light, automatic, in that order), focus stays on the button, and nothing
   else on the page is read out or moved.
3. Leave it on **Dark**. Reload the page and confirm the button still
   announces dark.
4. In the dark theme, repeat section 3 steps 1 to 5 (the **Add setting**
   dialog, its validation error and its focus return). Confirm the error
   summary and field messages are announced exactly as in the light theme.
   If you also check visually, a control or message that cannot be seen in
   the dark theme is a finding, even if NVDA reads it.
5. Return the button to **Automatic** before the remaining journeys, or note
   which theme they ran in.

### 9. RSOP prediction

1. Activate **RSOP prediction** in the sidebar. Confirm NVDA announces
   `Predict effective policy`, and that the explanatory note (what the
   prediction does and does not model) can be read in browse mode.
2. Tab through the form. Confirm every field is named: Computer name,
   Computer DN, User name, User DN, Domain (required), Loopback, Computer
   group memberships, User group memberships, Query id and Topology, and that
   the help text under the membership and topology fields is announced or
   reachable.
3. Enter Computer name `CL01`, Computer DN
   `CN=CL01,OU=Servers,DC=example,DC=test`, and Domain `example.test`.
   Replace the Topology text with:

   ```json
   {"nodes": [
     {"dn": "OU=Servers,DC=example,DC=test", "name": "Servers", "scope": "ou",
      "parent_dn": "DC=example,DC=test",
      "links": [{"gpo_guid": "22222222-3333-4444-5555-666666666666", "scope": "ou",
                 "scope_dn": "OU=Servers,DC=example,DC=test", "order": 1}]},
     {"dn": "DC=example,DC=test", "name": "example", "scope": "domain",
      "links": [{"gpo_guid": "11111111-2222-3333-4444-555555555555", "scope": "domain",
                 "scope_dn": "DC=example,DC=test", "order": 1}]}],
    "gpos": [
     {"guid": "11111111-2222-3333-4444-555555555555", "name": "Domain Baseline",
      "settings": [{"id": "s-a", "side": "computer", "hive": "HKLM",
                    "key": "Software\\Policies\\NvdaSynthetic", "value_name": "Val",
                    "registry_type": "REG_SZ", "value": "domain"}]},
     {"guid": "22222222-3333-4444-5555-666666666666", "name": "Servers Override",
      "settings": [{"id": "s-b", "side": "computer", "hive": "HKLM",
                    "key": "Software\\Policies\\NvdaSynthetic", "value_name": "Val",
                    "registry_type": "REG_SZ", "value": "ou"}]}]}
   ```

4. Activate **Compute**. Confirm the result is discoverable without visual
   inspection: the **Warnings from this computation** note (this topology
   links a GPO at the domain root), headings **Computer settings**, **User
   settings** and **GPOs** (H), and the two tables (T). With Ctrl+Alt+Arrow
   keys, confirm the winning value `ou` is read with its winning GPO
   `Servers Override` and, under Overrode, the domain GPO's GUID
   (`11111111-...`). Confirm the User side says no value is predicted to
   apply rather than staying silent.
5. Tab through the results. Each result table sits in a scrollable region
   named after its heading (for example "GPOs"). Confirm the region's name is
   announced when it takes focus, and that the arrow keys scroll it when it is
   wider than the dialog.
6. Replace the Topology text with `{ not json` and activate **Compute**.
   Confirm the error summary is announced, takes focus, and says the topology
   is not valid JSON.
7. Restore the topology from step 3 but change the first setting's
   `registry_type` to `REG_DWORD` (its value `domain` is not a number), and
   activate **Compute**. Confirm the server's refusal is announced the same
   way and identifies the problem.
8. Press Escape and confirm focus returns to **RSOP prediction**.

### 10. Security template

1. Activate **Security template**. Confirm NVDA announces
   `Render a security template`, and that the note saying the template was
   validated by `secedit` but never applied can be read.
2. Confirm the **Families** select announces both options, and **Scope**
   announces Member server and Domain controller.
3. Replace the Families text with `{"audit": {"logon_events": "success"}}` and
   activate **Render**. Confirm the limitations ("What this answer does not
   say") are reached **before** the template, that **Validation** and
   **GptTmpl.inf** are headings, and that the rendered template is a named
   region ("GptTmpl.inf contents") whose lines can be read line by line.
4. Change **Families** to Object security. Confirm **Scope** disappears and is
   not announced or reachable afterwards, rather than lingering as a hidden
   control.
5. With Object security selected, enter `{ not json` and activate **Render**.
   Confirm the error summary is announced and focused. Then enter
   `{"audit": {}}` and confirm the refusal names the unrecognised key and the
   keys this mode accepts.
6. Press Escape and confirm focus returns to **Security template**.

### 11. Folder Redirection

This panel reads a native `fdeploy1.ini`. Create a synthetic one first, in
UTF-16LE with a byte-order mark as Windows writes it:

```powershell
$Guid = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"
$Lines = @("", "[version]", "version=100", "[Folder_Redirection]",
  "$Guid=s-1-1-0;", "[${Guid}_s-1-1-0]", "Flags=1021",
  "FullPath=\\fileserver.example.test\redirected\%USERNAME%\Documents", "")
$Ini = Join-Path $NvdaData "fdeploy1.ini"
[IO.File]::WriteAllText($Ini, ($Lines -join "`r`n"), [Text.Encoding]::Unicode)
```

1. Activate **Folder Redirection**. Confirm NVDA announces
   `Review native policy files`, and that **Current file** announces it is
   required and has the file help text.
2. Choose the synthetic `fdeploy1.ini` as **Current file** and activate
   **Review files**. Confirm the status message is announced when the review
   completes, without moving focus unexpectedly.
3. Confirm the result is navigable: the document heading, the limitations
   (the Flags number is shown as stored, not decoded), and the
   **Redirection sections** table, where Ctrl+Alt+Arrow keys associate
   Documents, the principal, the full path and `1021`.
4. Expand **Full file report** and confirm it can be read.
5. Choose an empty text file, or any non-UTF-16 file, as **Current file** and
   review it. Confirm the error is announced and names the file.
6. Press Escape and confirm focus returns to **Folder Redirection**.

### 12. Sticky row actions on a wide table

1. Select `NVDA Synthetic Policy` and open **Policy settings**. Add a setting
   whose path and value are long enough to make the table scroll sideways,
   for example key
   `Software\Policies\NvdaSynthetic\AVeryLongSubkeyNameForWidthTesting\AndAnotherOne`,
   value name `LongValue`, type `REG_SZ`, and a value of about 120
   characters. (Zooming the page to 200 percent has the same effect.)
2. Tab to the new row's actions. Confirm **Edit**, **Comment** and the remove
   button are reached in order, that the remove button is named ("Remove
   setting") rather than read as a bare symbol, that it is possible to tell
   which row each action acts on (as in section 4 step 3), and that the
   focused action stays visible at the right edge instead of scrolling out of
   view.
3. Use Ctrl+Alt+Arrow keys along the row. Confirm the actions cell is read as
   part of the same row as the path and value.
4. Activate **Edit**, then press Escape. Confirm focus returns to that row's
   **Edit** button.

## Firefox ESR: smoke journey

Repeat these checks in Firefox ESR:

1. Useful page title, landmarks, headings, and policy selection.
2. Tab-list arrow navigation and selected-state announcements.
3. **Add setting** dialog name, initial focus, labels, validation error, Escape,
   and focus return.
4. Preferences-table headers, cell navigation, and row/action context.
5. Export-review dialog name, digest labels, actions, and focus return.
6. Sidebar order and focus return from the four panel buttons (section 7
   steps 1 and 3).
7. Colour-theme button announcements across all three modes (section 8
   steps 1 and 2).
8. RSOP prediction: compute the section 9 topology, reach the result headings
   and tables, and hear the invalid-JSON error (section 9 steps 3, 4 and 6).
9. Security template: render, reach the limitations before the template, and
   confirm Scope disappears for Object security (section 10 steps 3 and 4).
10. Folder Redirection: review the synthetic file and navigate the
    redirection table (section 11 steps 2 and 3).

Repeat the conflict journey in Firefox only if the Edge run found a
browser-independent issue or Firefox behavior suggests a related regression.

## Evidence record

Copy this template into the release ticket or append it below the screen-reader
section of `browser-accessibility-checklist.md`:

```text
Candidate version:
Release tag:
Source commit:
Wheel filename:
Wheel SHA-256 (exact, from the release assets' SHA256SUMS):
Downloaded wheel hash checked against SHA256SUMS: YES / NO
Windows edition/version/build:
NVDA version:
Edge version:
Firefox ESR version:
Tester:
Date:
Material NVDA setting deviations:

Edge page structure: PASS / FAIL — notes and exact speech if relevant
Edge tabs: PASS / FAIL — notes
Edge dialog/focus/validation: PASS / FAIL — notes
Edge table/action context: PASS / FAIL — notes
Edge conflict recovery: PASS / FAIL — notes
Edge export review: PASS / FAIL — notes
Edge sidebar navigation (1.1): PASS / FAIL — notes
Edge dark theme switching (1.1): PASS / FAIL — notes
Edge RSOP prediction (1.1): PASS / FAIL — notes
Edge Security template (1.1): PASS / FAIL — notes
Edge Folder Redirection (1.1): PASS / FAIL — notes
Edge sticky row actions (1.1): PASS / FAIL — notes
Firefox smoke journey: PASS / FAIL — notes
Repeated/noisy announcements: PASS / FAIL — notes

Findings:
- ID / severity / browser / steps / expected / actual speech / workaround

Blockers: none / list (any blocker fails acceptance)
Gate decision: PASS / FAIL
Release owner disposition for each significant finding
(fixed in candidate rc.N / accepted for this release, with reason / deferred to WI-NNN):
Behavior changed since this candidate? NO / YES (a change requires a new candidate and a repeat on its wheel)
```

Attach Speech Viewer excerpts and screenshots where useful. Do not record
production identifiers or other sensitive data.

## Cleanup

Stop GPO Studio with **Ctrl+C**. Once the evidence is saved, remove only the
disposable workspace:

```powershell
$NvdaData = Join-Path $env:LOCALAPPDATA "GPO Studio\nvda-validation"
Remove-Item -Recurse -Force $NvdaData
```

Do not remove the main `data` or `backups` directories.
