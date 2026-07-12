# Plan 016 — GPP end-to-end authoring

Status: proposed
Scope: turn the Plan 014 GPP/ILT codec foundation into a complete, reviewable
Groups and Registry authoring workflow
Depends on: Plan 015 canonical, validation, and numeric contracts

## Purpose

GPP Groups, Registry, and ILT currently survive storage and backup round trips,
but there are no GPP mutation endpoints or browser editors. A feature is not a
1.0 capability until a user can import, inspect, edit, validate, diff, restore,
and export it without editing JSON or SQLite directly.

## WP-1 — Stable domain and API contracts

- Give each GPP item and nested value/member a stable non-semantic editor ID.
- Add typed Pydantic request/response models for Groups, group members,
  Registry items/values, and each ILT predicate.
- Add optimistic-concurrency CRUD endpoints under
  `/api/gpos/{guid}/preferences/...`.
- Perform collection-level duplicate checks after every mutation.
- Keep each user action to one immutable GPO revision with actor and reason.
- Add OpenAPI examples for every supported action and value type.
- Reject unsupported GPP kinds explicitly rather than coercing or dropping.

## WP-2 — Fidelity and unknown-content policy

- Validate Groups.xml and Registry.xml output against synthetic samples created
  by GPMC on supported Windows versions.
- Determine which XML attributes/elements are semantically required, optional,
  generated, or safe to canonicalize.
- Preserve unknown XML attributes and ILT predicates losslessly when an item is
  not edited. If lossless preservation is not implemented, mark the item
  read-only and block export after a potentially lossy edit.
- Preserve item ordering where Group Policy Preferences evaluates it.
- Make GUID/UID generation deterministic only where Windows permits it;
  otherwise persist generated identifiers as model data.
- Expand the `cpassword` detector to namespace-qualified and malformed-input
  cases and make its refusal visible in API and UI error messages.

## WP-3 — Groups and Registry browser editors

- Add a Preferences panel separated by Computer/User scope and CSE kind.
- Groups editor: action, local group name/SID, description, remove-all flags,
  ordered member additions/removals, and clear destructive-action warnings.
- Registry editor: action, key, ordered values, type-aware editors, delete
  behavior, and exact DWORD/QWORD handling from Plan 015.
- Display imported-but-unknown fields and whether an edit would be lossy.
- Provide clone, reorder, delete, and restore-from-revision actions.
- Keep actor/reason visible and require a meaningful reason for destructive
  or broad membership changes.

## WP-4 — ILT expression editor

- Support OU, group, registry, IP range, environment, and WMI query predicates.
- Support negation and the exact boolean/grouping semantics the codec can
  preserve. If 1.0 remains AND-only, label that limitation and render more
  complex imported expressions as read-only instead of flattening them.
- Show a plain-language preview beside the serialized structure.
- Validate as the user types but preserve server validation as authoritative.
- Include ILT changes and conflicts in diff and revision comparison views.

## WP-5 — End-to-end tests

- API tests for create/edit/delete, stale revision, invalid nested data, restore,
  and error-path stability.
- Browser tests for both scopes, every supported registry type, member actions,
  all ILT predicates, cancel behavior, stale writes, and validation rendering.
- Import -> inspect -> edit -> diff -> restore -> export round trips for Groups
  and Registry, including max QWORD and non-ASCII text.
- Determinism tests across insertion orders and process restarts.

## Acceptance gates

- All supported GPP/ILT content is authorable without raw JSON/XML.
- Every successful mutation creates exactly one revision; stale mutations fail.
- Import/export round trips retain all supported semantics and explicitly block
  unsupported/lossy cases.
- Diff, semantic hash, validation, manifest, revision restore, Studio export,
  and GPMC export all include GPP/ILT.
- No GPP output path can emit `cpassword`.
- Browser automation covers the complete GPP Groups and Registry happy paths
  and their destructive/error paths.

## Deferred

Services, Scheduled Tasks, Files, Folders, Environment, Drives, Printers,
Shortcuts, Local Users, and arbitrary executable/script payloads remain
post-1.0. Add each later as its own vertical slice using this plan's gates.

