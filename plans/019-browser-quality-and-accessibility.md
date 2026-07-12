# Plan 019 — Browser quality and accessibility

Status: proposed
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

