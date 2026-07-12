# Plan 010 — UI module split, target_type editor, error handling

Status: executable plan
Scope: split studio.js into ES modules, add missing target_type field to
security filter editor, improve API error feedback
Depends on: Plan 009 (principal format validation, domain validation)

## Purpose

The UI JavaScript (studio.js) is a 196-line dense single-file script. While
not yet unmanageable, it's growing with each plan. The security filter editor
dialog is also missing a `target_type` field — the API accepts it (Pydantic
model has `target_type: Literal["user", "group", "computer"]`), the model
stores it, and the PowerShell plan uses it, but the UI form doesn't expose it.
Users cannot set `target_type` through the UI; it always defaults to `"group"`.

Additionally, API errors are shown via a simple toast that disappears after
2.6 seconds. Form validation errors from the server (422 responses with
structured `issues` arrays) are not rendered inline.

## WP-1 — Split studio.js into ES modules

Goal: break studio.js into focused ES modules loaded via `<script type="module">`.

Deliverables:

- Create `static/js/api.mjs` — `api()`, `toast()`, `audit()` helpers
- Create `static/js/state.mjs` — shared state object and `$`/`$$` selectors
- Create `static/js/render.mjs` — all `render*` functions and `renderAll`
- Create `static/js/forms.mjs` — dialog open/submit handlers for GPO, setting,
  link, filter, WMI
- Create `static/js/admx.mjs` — ADMX search, detail, configure
- Create `static/js/main.mjs` — entry point: tab switching, chip filtering,
  button wiring, initial `loadList()` call
- Update `index.html` to use `<script type="module" src="/assets/js/main.mjs">`
- Update `StaticFiles` mount or add a route for `/assets/js/`
- Delete `static/studio.js`
- All existing functionality must work identically

Acceptance gates:

- The UI loads and all interactions work (create GPO, edit settings, links,
  filters, WMI, ADMX browse/configure, export, plan, GPMC backup)
- No console errors
- `uv run pytest -q` passes (API tests don't test JS, but verify no Python
  changes broke anything)

## WP-2 — Add target_type field to security filter editor

Goal: the security filter dialog should let users select target_type.

Deliverables:

- In the filter dialog HTML, add a `<select name="target_type">` with
  options: User, Group, Computer (default Group)
- In `forms.mjs`, include `target_type` in the filter form data sent to the API
- In `render.mjs`, display the target_type in the security filters table
  (add a column or append to the principal cell)
- In `openFilter()`, populate `target_type` from the existing filter when editing

Acceptance gates:

- Creating a security filter with target_type "user" works
- Editing an existing filter's target_type works
- The filters table shows the target_type

## WP-3 — Improve API error feedback

Goal: show structured validation errors inline instead of just a toast.

Deliverables:

- In `api.mjs`, when a 422 response has `error.issues`, throw an error with
  the issues attached
- In `forms.mjs`, catch errors with issues and render them below the form
  fields (or in a summary block at the top of the dialog)
- Keep the toast for non-form errors (e.g., "GPO not found")
- Clear previous error messages when a new submission is attempted

Acceptance gates:

- Submitting a setting with an invalid key shows the validation error in the
  dialog, not just a disappearing toast
- The toast still appears for non-validation errors

## Sequence

```text
WP-1 (module split)     — touches all JS + index.html
WP-2 (target_type)     — touches filter dialog HTML + forms.mjs + render.mjs
WP-3 (error handling)  — touches api.mjs + forms.mjs

Recommended: WP-1 first (creates the module structure),
then WP-2+WP-3 in the same agent (both touch forms.mjs).
```

## Deferred

- Diff viewer UI (requires Plan 011 estate import)
- File upload for backup import (v0.1 is local-only)
- Multi-GPO backup import
- Dark mode
- Responsive/mobile layout
