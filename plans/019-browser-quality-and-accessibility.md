# Plan 019 — Browser quality and accessibility

Status: implemented and accepted (2026-07-16); closed 2026-07-18. The browser
test foundation, conflict and failure UX, review workflows, automated
accessibility coverage, adversarial review, and CI evidence are complete. The
hands-on NVDA acceptance journey passed against the promoted v1.0.0 candidate
(recorded in `docs/release-evidence.md`); no Plan 019 work remains open.
Scope: make the dependency-light browser application testable, resilient to
conflicts and failures, and usable with keyboard and assistive technology
Depends on: Plans 015 and 016 API contracts

## Purpose

The browser is a core product surface but currently has no automated JavaScript,
browser, or accessibility suite. Most failures collapse into short-lived toast
messages, and optimistic-concurrency conflicts require manual recovery. The 1.0
UI must make safety state and unsupported content hard to miss.

## WP-1 — Testable frontend structure

- Format and lint the ES modules with a pinned toolchain.
- Extract pure value parsing, rendering, and request-shaping helpers for unit
  tests, especially numeric values and HTML escaping.
- Add browser automation against a temporary real SQLite workspace.
- Seed only synthetic fixtures through public APIs.
- Run browser tests in CI on Chromium and smoke tests on Firefox; document the
  supported browser baseline.

## WP-2 — Concurrency and failure UX

- On 409, retain unsaved form values, fetch the current revision, and offer a
  structured compare/reapply flow. Never silently retry a destructive change.
- Map server issue paths to fields and keep an always-visible error summary.
- Make loading, empty, disabled, offline/server-down, and partial-import states
  explicit and recoverable.
- Disable export actions when validation blocks them and explain why before the
  user downloads.
- Confirm destructive membership changes, revision restore, and any future
  archive/delete action with target and impact summaries.

## WP-3 — Review-centered workflows

- Add revision-to-revision selection and render all Plan 015 diff kinds.
- Show semantic digest, artifact capabilities, unsupported/preserved content,
  and validation state together before export.
- Add a generated human-readable policy report suitable for code review or a
  change ticket, with no active content.
- Make baseline, draft, and observed identities unmistakable in three-way diff.
- Surface when an imported object is archived/read-only and require an explicit
  fork before editing.

## WP-4 — Accessibility and responsive behavior

- Give tabs correct tablist semantics, keyboard navigation, and selected state.
- Ensure every control has an accessible name, error association, focus style,
  and logical focus return after dialogs.
- Add focus trapping/initial focus for dialogs and non-toast announcements for
  persistent errors.
- Meet WCAG 2.2 AA contrast, reflow, zoom, target-size, and reduced-motion needs.
- Do not rely on color alone for diff or validation meaning.
- Run automated accessibility checks on every primary route/state and complete
  a documented keyboard/screen-reader manual pass.

## WP-5 — End-to-end release journeys

Automate these journeys with screenshots/traces on failure:

1. Create -> raw registry edit -> validate -> revision compare -> Studio export.
2. Load ADMX -> configure each supported presentation kind -> edit result.
3. Import estate -> fork -> three-way conflict -> resolve/reapply.
4. Import GPMC backup -> inspect preserved content -> edit GPP -> GPMC export.
5. Add security and WMI filters -> stale conflict -> recover.
6. Restore revision and prove history remains append-only.
7. Exercise max QWORD, Unicode, long values, server errors, and narrow viewport.

## Acceptance gates

- The primary release journeys pass in CI using the packaged application.
- Stale edits retain user input and provide an explicit reconciliation path.
- No supported action depends on a toast as its only error/status channel.
- Automated accessibility checks report no serious/critical violations and the
  manual keyboard/screen-reader checklist is complete.
- GPP, preserved content, validation blockers, and all diff kinds are visible
  and understandable in the UI.

## Implementation reflection — 2026-07-15

Implemented the browser-quality foundation with a pinned ESLint, Prettier,
Vitest, Playwright, and axe-core toolchain. CI now exercises the packaged CLI
against a temporary real SQLite workspace in Chromium, with a Firefox smoke
baseline and failure traces/screenshots.

Conflict responses expose structured revision metadata. Edit forms retain
unsaved values, map validation issues to fields, and require an explicit
load-current or review-and-reapply choice. Offline and server failures use a
persistent alert instead of relying on a toast. Export actions now open a
review boundary showing both digests, validation, preserved content, and
artifact capability before download.

Review workflows now include revision-to-revision diff selection and a
deterministic inert text policy report. GPMC import exposes inbox capability and
uses inbox-relative paths. Archived imports disable edit actions and surface an
explicit fork path. GPP gained clone, atomic reorder, per-item revision restore,
destructive confirmations, and preserved/read-only unsupported ILT rendering.

The accessibility work adds semantic keyboard tabs, labelled/focus-managed
dialogs, field-error relationships, persistent announcements, visible focus,
target sizing, forced-colors/reduced-motion handling, and narrow reflow. Axe
reports no serious or critical findings in covered primary states.

The hands-on screen-reader session documented in
`docs/browser-accessibility-checklist.md` remains release-candidate evidence;
the current environment has no screen reader installed. This is deliberately
recorded as pending rather than claiming an automated accessibility-tree
inspection as a manual pass.

## Adversarial review resolution — 2026-07-16

- Added configured-inbox tests for both relative traversal and absolute paths
  outside the inbox. Both fail with the stable `path_outside_inbox` issue.
- Replaced GPP module-level source variables with explicit shared state and
  clear that state whenever its dialog closes, preventing cancel-cycle leaks.
- Reject identical revision comparisons before either revision is loaded.
- Narrowed GPP reorder kind to a `Literal`; its runtime validation was already
  before `_mutate`, and a regression test now proves invalid kinds never enter
  the mutation transaction.
- Promoted ESLint empty-block and unused-variable rules to errors and removed
  the four legacy violations.
- Made the stale-conflict browser assertion relative to the concurrent
  revision instead of assuming revision 3.
- Rendered multi-string report values as human-readable semicolon-separated
  text instead of Python representation syntax.
- Retained archived-by-default GPMC imports intentionally. The explicit fork
  endpoint is the safety boundary for editable drafts; coverage now verifies
  an archived import forks to a draft.
- Added a Playwright browser cache to CI and expanded documentation for the
  `GPO_STUDIO_TEST_PYTHON` override.
