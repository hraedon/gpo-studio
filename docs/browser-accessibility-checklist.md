# Browser and accessibility verification

Plan 019 treats the current stable Chromium family as the primary browser
baseline and Firefox ESR as the secondary baseline. The application remains
runtime dependency-free; the pinned Node packages are development-only test
tools.

## Automated evidence

- `npm run check` covers formatting, ESLint, Vitest request/rendering helpers,
  and GPP browser helpers.
- `npm run test:browser` starts the packaged CLI against a temporary real
  SQLite workspace. Chromium runs all journeys; Firefox runs the smoke journey.
- axe-core scans the overview, policy settings, security, revision/diff state,
  and a narrow-viewport modal. Serious and critical findings fail CI.
- Playwright retains screenshots and traces on failure.

## Keyboard pass

- [x] Skip link reaches the main workspace.
- [x] Tab list uses one tab stop; Left/Right/Home/End move focus and selection.
- [x] Dialogs place initial focus inside, trap Tab/Shift+Tab, close with Escape,
  and return focus to the opener.
- [x] Generated GPP rows expose named controls and reachable actions.
- [x] Persistent form errors receive focus and link back to invalid fields.
- [x] Export blocking, conflict reconciliation, revision restore, and destructive
  GPP actions are operable without pointer input.
- [x] At 360 CSS pixels, navigation, tables, tabs, and dialogs remain reachable
  without page-level horizontal scrolling.

## Screen-reader pass

The automated semantic pass verifies roles, names, relationships, live regions,
dialog labelling, and serious/critical axe findings. A hands-on NVDA session
against the exact release candidate supplements that automated evidence with
announcement order, browse-mode behavior, and NVDA-specific navigation.

- [x] Windows: NVDA + Chromium — announce policy navigation, tabs, validation,
  field errors, conflict choices, and export review in a sensible order.
- [x] Windows: NVDA + Firefox ESR — repeat the smoke path and dialog focus pass.
- [x] Confirm repeated announcements are not noisy and dynamic tables retain
  useful row/action context.

The automated snapshot confirms named navigation and main landmarks, a level-1
policy heading, tabs and tab panels, labelled dialogs and fields, tables with
headers and rows, and named export and row-action controls. The exact hands-on
session below followed the [NVDA validation runbook](nvda-validation-runbook.md)
and confirmed that those semantics support the complete task.

### 1.0.0rc3 acceptance session

- Date: 2026-07-18
- Tester: Paul Merritt (PLM)
- Candidate: `v1.0.0-rc.3`
- Source commit: `bae7395837de76efdf279651741c32d1457bd52d`
- Wheel: `gpo_studio-1.0.0rc3-py3-none-any.whl`
- Wheel SHA-256: `93c43610bd0fa5a2198e3e3933bfbe5aeb9f4bbc78565402619e8775b391e6ce`
- Windows: Windows 11 Pro 25H2, build 26200.8875
- NVDA: 2026.1.1 (`2026.1.1.55980`)
- Edge: 150.0.4078.65, official 64-bit build
- Firefox ESR: 140.12.0esr, 32-bit
- Supplementary Firefox release-channel run: 152.0.6, 64-bit
- Gate decision: **Pass**

The complete Edge journey and the Firefox ESR smoke journey passed without a
task-blocking or significant finding. The supplementary current Firefox run
also passed. One minor observation was accepted: landmark navigation reliably
worked in the navigation rail and elsewhere, but did not reliably produce a
useful announcement for the work pane. The work pane remains reachable and
the tester completed every core task, so this did not affect the gate result.

### Interrupted 1.0.0rc2 session

On 2026-07-18, the initial hands-on Windows/NVDA session found a blocker before
policy creation: NVDA announced each button, but activating a button had no
effect. A direct request showed `/assets/js/main.mjs` was served as
`text/plain; charset=utf-8`; the browser therefore rejected it under the
application's `nosniff` policy and no interaction handlers were installed.
The server-side MIME fix and regression coverage are included in `1.0.0rc3`.
No screen-reader checklist item is credited from the interrupted session.
