# NVDA validation runbook

This is the manual screen-reader acceptance gate for GPO Studio 1.0. Automated
axe, keyboard, and accessibility-tree tests are prerequisites, not substitutes
for hearing the interface through NVDA and using NVDA's navigation model.

Run this procedure against the exact release candidate. Do not mark the manual
items in [`browser-accessibility-checklist.md`](browser-accessibility-checklist.md)
complete until a person has performed and recorded this session.

The candidate is intentionally published as a GitHub prerelease before this
manual gate is complete. Its publication does not approve the final release;
it creates the immutable wheel and checksum identity that this gate evaluates.

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

Any blocker fails the release gate. Record and triage significant findings
before approval; the release owner must explicitly accept any that remain.

## Prepare the exact candidate

Record all of the following before testing:

- GPO Studio version and source commit.
- Wheel filename and SHA-256 from the release `SHA256SUMS`.
- Windows edition, version, and build.
- NVDA version.
- Edge and Firefox ESR versions.
- Tester and date.

Install the candidate using the [Windows quickstart](windows-quickstart.md).
Download the wheel and `SHA256SUMS` from the candidate's GitHub Release
**Assets**, not from the source-code ZIP or a CI Actions artifact.
Use a new disposable workspace, for example:

```powershell
$Root = Join-Path $env:LOCALAPPDATA "GPO Studio"
$App = Join-Path $Root "venv\Scripts\gpo-studio.exe"
$NvdaData = Join-Path $Root "nvda-validation"
New-Item -ItemType Directory -Force $NvdaData | Out-Null
& $App run --host 127.0.0.1 --port 8765 --database (Join-Path $NvdaData "nvda-test.db")
```

Install the [current stable NVDA](https://www.nvaccess.org/download/) if the
lab image does not already have an approved version. The
[NVDA User Guide](https://download.nvaccess.org/releases/stable/documentation/en/userGuide.html)
is the keyboard-command authority. Record any reason for testing an older
version.

Use headphones or working speakers. NVDA Speech Viewer may be opened from
**NVDA menu > Tools > Speech Viewer** to help capture exact announcements, but
the tester must still listen to speech. Leave punctuation and verbosity at the
tester's normal settings and record material non-default settings.

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

Keep this workspace solely as test evidence; do not publish its artifacts.

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

## Firefox ESR: smoke journey

Repeat these checks in Firefox ESR:

1. Useful page title, landmarks, headings, and policy selection.
2. Tab-list arrow navigation and selected-state announcements.
3. **Add setting** dialog name, initial focus, labels, validation error, Escape,
   and focus return.
4. Preferences-table headers, cell navigation, and row/action context.
5. Export-review dialog name, digest labels, actions, and focus return.

The conflict journey need not be repeated in Firefox unless the Edge run found
a browser-independent issue or Firefox behavior suggests a related regression.

## Evidence record

Copy this template into the release ticket or append it below the screen-reader
section of `browser-accessibility-checklist.md`:

```text
Candidate version:
Source commit:
Wheel filename:
Wheel SHA-256:
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
Firefox smoke journey: PASS / FAIL — notes
Repeated/noisy announcements: PASS / FAIL — notes

Findings:
- ID / severity / browser / steps / expected / actual speech / workaround

Gate decision: PASS / FAIL
Release owner disposition for significant findings:
```

Speech Viewer excerpts and screenshots are useful attachments, but do not
record production identifiers or other sensitive data.

## Cleanup

Stop GPO Studio with **Ctrl+C**. After evidence has been retained, remove only
the disposable workspace:

```powershell
$NvdaData = Join-Path $env:LOCALAPPDATA "GPO Studio\nvda-validation"
Remove-Item -Recurse -Force $NvdaData
```

Do not remove the main `data` or `backups` directories.
