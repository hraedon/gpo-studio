# Plan 011 — Estate import and baseline forking (Milestone 1 close-out)

Status: executable plan
Scope: import gpo-lens estate exports as read-only baselines, fork baselines
into editable drafts, wire three-way diff to estate data
Depends on: Plan 009 (validation hardening), Plan 010 (UI module split)

## Purpose

GPO Studio's read-only counterpart, gpo-lens, can export an "estate" — a JSON
snapshot of all observed GPOs in a domain. Currently, GPO Studio can only
create drafts from scratch or import GPMC backups. To close Milestone 1, we
need:

1. **Estate import** — read a gpo-lens estate export JSON and store GPOs as
   read-only baselines (status `"archived"`, immutable).
2. **Baseline forking** — create a new editable draft from a baseline, copying
   all settings, links, security filters, WMI filter, and domain.
3. **Three-way diff wiring** — the three-way diff infrastructure exists
   (`diff.py:three_way_diff`) but only works with inline GPO references or
   stored GPO GUIDs. Wire it to compare a baseline, a draft forked from that
   baseline, and a newly imported estate observation.

## WP-1 — Estate import endpoint and store method

Goal: `POST /api/estate/import` accepts a gpo-lens estate export JSON and
creates read-only baseline GPOs.

Deliverables:

- New module `estate.py` with `parse_estate(data: dict) -> list[GPO]` that
  converts gpo-lens estate JSON to GPO model objects. The estate format is:
  ```json
  {
    "kind": "gpo-lens-estate",
    "domain": "corp.example.com",
    "exported_at": "2026-07-12T10:00:00Z",
    "gpos": [
      {
        "guid": "...",
        "display_name": "...",
        "description": "...",
        "domain": "...",
        "computer_enabled": true,
        "user_enabled": true,
        "settings": [...],
        "links": [...],
        "security_filters": [...],
        "wmi_filter": {...}
      }
    ]
  }
  ```
- Settings in the estate JSON use the same shape as `RegistrySetting.to_dict()`
- Links use the same shape as `GPOLink.to_dict()`
- Security filters use the same shape as `SecurityFilter.to_dict()`
- `parse_estate` validates each GPO via `validate_gpo` and raises
  `ValidationError` if any GPO has errors
- New store method `import_baseline_gpos(gpos: list[GPO], identity, reason)`
  that creates each GPO with `status="archived"` and `source_guid` set to the
  original GUID from the estate
- New API endpoint `POST /api/estate/import` that accepts the JSON body,
  calls `parse_estate`, and calls `import_baseline_gpos`
- Skip GPOs whose GUID already exists in the store (idempotent import)
- Return a summary: `{"imported": N, "skipped": M, "total": N+M}`

Acceptance gates:

- Importing a valid estate JSON creates archived GPOs
- Re-importing the same estate skips existing GPOs
- Importing an estate with invalid settings raises ValidationError
- `uv run pytest -q`, `uv run ruff check .`, `uv run mypy src` pass

## WP-2 — Baseline forking

Goal: `POST /api/gpos/{guid}/fork` creates a new editable draft from a
baseline GPO.

Deliverables:

- New store method `fork_gpo(guid, new_name, identity, reason)` that:
  1. Reads the source GPO
  2. Creates a new GPO with a fresh GUID, the given name, `status="draft"`,
     `source_guid` set to the original GUID
  3. Copies all settings (with new IDs prefixed `forked-`), links (new IDs),
     security_filters (new IDs), wmi_filter (new ID), domain, computer_enabled,
     user_enabled
  4. Does NOT copy cse_metadata (forked GPOs are registry-only drafts)
- New API endpoint `POST /api/gpos/{guid}/fork` with body `{name, actor, reason}`
- The forked GPO's settings get new IDs to avoid collisions

Acceptance gates:

- Forking a baseline creates a new draft with identical settings
- The forked GPO has a different GUID and `source_guid` pointing to the baseline
- The forked GPO's status is `"draft"`
- Existing tests pass

## WP-3 — Three-way diff endpoint for estate comparison

Goal: `POST /api/estate/diff` compares a baseline, a draft, and a newly
imported estate observation.

Deliverables:

- New API endpoint `POST /api/estate/diff` with body:
  ```json
  {
    "baseline_guid": "...",
    "draft_guid": "...",
    "observed_guid": "..."
  }
  ```
- All three GUIDs must reference existing GPOs in the store
- Calls `three_way_diff(baseline, draft, observed)` and returns the result
- This is a convenience endpoint — the existing `POST /api/diff` already
  supports inline GPO references, but this endpoint is simpler for the
  estate comparison use case

Acceptance gates:

- The endpoint returns a valid three-way diff
- Non-existent GUIDs raise NotFoundError
- Existing tests pass

## WP-4 — UI for estate import and forking

Goal: add UI controls for estate import and baseline forking.

Deliverables:

- In the rail (sidebar), add an "Import estate" button below "New GPO"
  that opens a file picker (or textarea for JSON paste, since v0.1 is
  local-only and file upload is deferred)
- Use a `<dialog>` with a `<textarea>` for pasting estate JSON
- On submit, POST to `/api/estate/import` and refresh the GPO list
- In the GPO overview panel, if `source_guid` is set and status is
  `"archived"`, show a "Fork to draft" button
- On fork, open a small dialog for the new GPO name, then POST to
  `/api/gpos/{guid}/fork`

Acceptance gates:

- Pasting valid estate JSON and submitting creates GPOs
- The fork button appears on archived GPOs
- Forking creates a new draft and selects it

## Sequence

```text
WP-1 (estate import)     — new estate.py + store method + API endpoint
WP-2 (baseline fork)     — new store method + API endpoint
WP-3 (three-way diff)    — new API endpoint (uses existing diff.py)
WP-4 (UI)                — touches JS modules + index.html

Recommended: WP-1+WP-2 in one agent (both touch store.py + api.py),
WP-3 in another agent (touches api.py only, no conflicts with WP-1+WP-2
if done sequentially), WP-4 after WP-1+WP-2+WP-3 are done.
```

## Deferred

- File upload for estate import (use textarea for now)
- Scheduled/automatic estate re-import
- Estate diff visualization in the UI (show conflicts inline)
- Baseline comparison across multiple estate snapshots
- Estate import from gpo-lens API (not just JSON file)
