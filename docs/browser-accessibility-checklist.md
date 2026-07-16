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
dialog labelling, and serious/critical axe findings. A hands-on screen-reader
session still requires an environment with NVDA, JAWS, VoiceOver, or Orca;
none is installed in the current Linux validation environment.

- [ ] Windows: NVDA + Chromium — announce policy navigation, tabs, validation,
  field errors, conflict choices, and export review in a sensible order.
- [ ] Windows: NVDA + Firefox ESR — repeat the smoke path and dialog focus pass.
- [ ] Confirm repeated announcements are not noisy and dynamic tables retain
  useful row/action context.

Record the screen reader, browser versions, tester, date, and findings here when
that final hands-on gate is run. Until then, Plan 019's manual screen-reader
acceptance gate remains explicitly open.
