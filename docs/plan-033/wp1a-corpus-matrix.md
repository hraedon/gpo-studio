# WP-1A Corpus Coverage Matrix

Status per adapter. ✓ = genuine GPMC capture exists, — = not applicable,
✗ = needs GPMC authoring session.

## Drive Maps (Drives/Drives.xml)

| Dimension | Covered | Gap |
|-----------|---------|-----|
| User scope | ✓ (4 items) | — |
| Computer scope | — | Drive Maps is user-scope only (no computer configuration in GPMC) |
| Action: Create | ✓ (H: Unicode) | — |
| Action: Update | ✓ (M:) | — |
| Action: Replace | ✓ (P:) | — |
| Action: Delete | ✓ (X:) | — |
| All common options | ✓ (across items) | — |
| ILT expression | ✓ (FilterOs + FilterRunOnce) | — |
| Unicode path | ✓ (H: `\\filesrv\shäre-ünïcode`) | — |
| XML-sensitive chars | ✓ (H: label `Ünïcödé <"&> label`) | — |
| Empty/default values | ✓ (X: item 3) | — |

## Local Groups (Groups/Groups.xml)

| Dimension | Covered | Gap |
|-----------|---------|-----|
| Computer scope | ✓ (3 items) | — |
| User scope | ✓ (1 item) | — |
| Action: Create | ✓ (user-scope dev-team) | — |
| Action: Update | ✓ (Administrators) | — |
| Action: Replace | ✓ (Power Users) | — |
| Action: Delete | ✓ (test-delete-group) | — |
| Member ADD | ✓ | — |
| Member REMOVE | ✓ (userAction="REMOVE") | — |
| deleteAllUsers/Groups | ✓ (Power Users) | — |
| All common options | Partial | apply_once not exercised on groups |
| ILT expression | ✓ (FilterGroup + FilterRunOnce on user-scope) | — |
| Unicode member name | ✓ (HRAENET\ünïcode-test-group) | — |

## Scheduled Tasks (ScheduledTasks/ScheduledTasks.xml)

| Dimension | Covered | Gap |
|-----------|---------|-----|
| Computer scope | ✓ (3 items) | — |
| User scope | ✓ (2 items) | — |
| TaskV2 Create | ✓ (user-scope "Create Task") | — |
| TaskV2 Update | ✓ (GpoStudio-Cleanup) | — |
| TaskV2 Replace | ✓ ("Replace Test", multiple triggers) | — |
| TaskV2 Delete | ✓ ("Delete Test", SendEmail action) | — |
| ImmediateTaskV2 Create | ✓ (GpoStudio-Init) | — |
| Multiple triggers | ✓ (weekly + monthly on "Replace Test") | — |
| Weekly/Monthly trigger | ✓ | — |
| SessionStateChangeTrigger | ✓ (RemoteConnect) | — |
| ShowMessage action | ✓ | — |
| SendEmail action | ✓ | — |
| All common options | Partial | apply_once, disabled not exercised |
| ILT expression | ✓ (FilterDomain on user-scope) | — |
| Unicode in command/args | ✓ (user-scope "Create Task") | — |
| Mixed-CSE GPO | ✓ (User/Drives + Machine/Groups + Machine/SchedTasks) | — |

## Cross-cutting

| Dimension | Status |
|-----------|--------|
| Mixed-CSE fixture (one GPO, multiple adapters) | ✓ |
| Unicode and XML-sensitive values | ✓ |
| Deliberate unknown attrs/children | Partial (native unknowns captured; no artificial injection) |
| Unknown-content preservation (import→export) | Partial (no-edit verbatim; edited preserves unknown_attrs/children/task_xml/unknown_props_children) |
| Public backup-import path integration | ✓ (read_backup + collect_gpp_collections; full API endpoint test pending WP-1B) |
